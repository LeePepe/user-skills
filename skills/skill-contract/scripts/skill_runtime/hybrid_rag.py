"""Shared stdlib Hybrid RAG runtime (BM25 + CJK bigrams + optional Jev reranking)."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
import hashlib, importlib.util, json, math, os, re, sqlite3, tempfile
from pathlib import Path
from typing import Any

from .cli_contract import redact_payload, redact_text

ACI_MAX_BYTES, RECALL_LIMIT = 12_000, 12
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._/#+-]*|[\u4e00-\u9fff]+", re.IGNORECASE)
_HEX_RE, _FM_KV_RE = re.compile(r"#[0-9A-Fa-f]{6}\b"), re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")

ALLOWED_CORPUS_SCOPES: dict[str, frozenset[str]] = {
    "personal-m2-assistant": frozenset({"handoff", "personal-m2-assistant"}), "handoff": frozenset({"handoff"}),
    "knowledge-digest": frozenset({"knowledge-vault", "collected-tools"}), "knowledge-vault": frozenset({"knowledge-vault"}),
    "save-tool": frozenset({"collected-tools", "knowledge-vault"}), "collected-tools": frozenset({"collected-tools"}),
    "design-studio": frozenset({"design-studio", "design-vault"}), "design-vault": frozenset({"design-studio", "design-vault"}),
    "story-craft": frozenset({"story-craft", "handoff", "retro-story"}), "retro-story": frozenset({"story-craft", "handoff", "retro-story"}),
}


class VaultIsolationError(ValueError):
    """Raised when a caller queries or loads documents across isolated vault scopes."""


@dataclass(frozen=True)
class RagDoc:
    doc_id: str; corpus_scope: str; title: str; summary: str = ""; body: str = ""
    tags: tuple[str, ...] = (); paths: tuple[str, ...] = (); updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RagHit:
    doc_id: str; corpus_scope: str; score: float; bm25_score: float; jev_score: float | None
    title: str; snippet: str; path: str = ""; tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


def _norm_scope(scope: str) -> str:
    return str(scope or "").strip().lower()


def _resolve_allowed_scopes(corpus_scope: str, allowed_scopes: Iterable[str] | None) -> frozenset[str]:
    if not (caller := _norm_scope(corpus_scope)):
        raise VaultIsolationError("corpus_scope must be non-empty")
    policy = ALLOWED_CORPUS_SCOPES.get(caller, frozenset({caller}))
    if allowed_scopes is None:
        return policy
    req = frozenset(_norm_scope(s) for s in allowed_scopes if _norm_scope(s))
    if not req or (req - policy):
        raise VaultIsolationError(f"vault isolation violation for {caller!r}: {sorted(req - policy) if req else 'empty'}")
    return req


def tokenize_cjk_and_latin(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall((text or "").lower()):
        if "\u4e00" <= raw[0] <= "\u9fff":
            tokens.extend([raw] if len(raw) == 1 else [raw[i : i + 2] for i in range(len(raw) - 1)])
            if 1 < len(raw) <= 4:
                tokens.append(raw)
        else:
            tokens.append(raw)
            for seg in re.split(r"[/#]+", raw):
                if len(stem := (seg.rsplit(".", 1)[0] if "." in seg else seg)) >= 2 and stem not in tokens: tokens.append(stem)
            tokens.extend(p for p in re.split(r"[./_#+-]+", raw) if len(p) >= 2 and p != raw)
    return tokens


def _python_bm25(query_tokens: Sequence[str], docs: Sequence[RagDoc]) -> dict[int, float]:
    if not (q_terms := tuple(dict.fromkeys(query_tokens))) or not docs:
        return {}
    doc_toks, df = [], {}
    for doc in docs:
        toks = tokenize_cjk_and_latin(doc.title) * 3 + tokenize_cjk_and_latin(" ".join((*doc.tags, *doc.paths))) * 2 + tokenize_cjk_and_latin(f"{doc.summary}\n{doc.body}")
        doc_toks.append(toks)
        for t in set(toks).intersection(q_terms):
            df[t] = df.get(t, 0) + 1
    avgdl = max(1.0, sum(len(t) for t in doc_toks) / len(doc_toks))
    scores: dict[int, float] = {}
    for idx, toks in enumerate(doc_toks):
        tf_map: dict[str, int] = {}
        for t in toks:
            if t in df:
                tf_map[t] = tf_map.get(t, 0) + 1
        if (sc := sum(math.log(1.0 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5)) * ((tf * 2.5) / (tf + 1.5 * (0.25 + 0.75 * len(toks) / avgdl))) for t, tf in tf_map.items())) > 0.0:
            scores[idx] = sc
    return scores


def _fts5_bm25(query_tokens: Sequence[str], docs: Sequence[RagDoc]) -> dict[int, float] | None:
    if not (q_terms := [t for t in dict.fromkeys(query_tokens) if t][:24]) or not docs:
        return {}
    digest = hashlib.sha256("".join(f"{d.doc_id}|{d.corpus_scope}|{d.updated_at}|{len(d.body)}|{d.title}\n" for d in docs).encode("utf-8")).hexdigest()[:20]
    db_target = ":memory:"
    try:
        cache_root = Path(tempfile.gettempdir()) / "skill-runtime-hybrid-rag"
        cache_root.mkdir(parents=True, exist_ok=True)
        db_target = str(cache_root / f"fts-{digest}.sqlite3")
    except OSError:
        pass
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_target, timeout=1.0)
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rag_fts'").fetchone() is not None
        if exists and (conn.execute("SELECT COUNT(*) FROM rag_fts").fetchone() or (0,))[0] != len(docs):
            conn.execute("DROP TABLE rag_fts"); exists = False
        if not exists:
            conn.execute("CREATE VIRTUAL TABLE rag_fts USING fts5(doc_idx UNINDEXED, title, tags, body)")
            rows = [(i, " ".join(tokenize_cjk_and_latin(d.title)), " ".join(tokenize_cjk_and_latin(" ".join((*d.tags, *d.paths)))), " ".join(tokenize_cjk_and_latin(f"{d.summary}\n{d.body}"))) for i, d in enumerate(docs)]
            conn.executemany("INSERT INTO rag_fts(doc_idx, title, tags, body) VALUES (?, ?, ?, ?)", rows); conn.commit()
        expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in q_terms)
        return {int(idx): max(1e-6, -float(raw)) for idx, raw in conn.execute("SELECT CAST(doc_idx AS INTEGER), bm25(rag_fts, 4.0, 2.5, 1.0) FROM rag_fts WHERE rag_fts MATCH ?", (expr,)).fetchall()}
    except sqlite3.Error:
        return None
    finally:
        if conn is not None: conn.close()


def _snippet(doc: RagDoc, q_tokens: Sequence[str], max_chars: int) -> str:
    if not (base := " ".join((doc.summary or doc.body or doc.title).split())):
        return ""
    low, span = base.lower(), max(32, max_chars)
    start = max(0, next((low.find(t) for t in q_tokens if len(t) >= 2 and low.find(t) >= 0), 0) - span // 4)
    out = ("..." if start > 0 else "") + base[start : start + span].strip()
    return redact_text((out.rstrip(".") + "..." if start + span < len(base) else out)[:span])


def _default_jev_fn(state: dict[str, Any], questions: dict[str, dict[str, Any]], *, agent_role: str) -> dict[str, Any] | None:
    if not (jev_path := Path(__file__).resolve().parents[3] / "team-lead" / "scripts" / "jev_client.py").is_file():
        return None
    try:
        if (spec := importlib.util.spec_from_file_location("skill_runtime_jev_client", jev_path)) is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.jev_decide(state, questions, agent_role=agent_role, timeout_sec=1.5, falcon_only=True)
    except Exception:
        return None


def _jev_rerank(query: str, recalled: list[tuple[RagDoc, float, str]], *, corpus_scope: str, jev_fn: Callable[..., Any] | None) -> tuple[list[tuple[RagDoc, float, float | None, str]], bool]:
    fn = jev_fn or _default_jev_fn
    cands = {f"cand_{i}": {"doc_id": d.doc_id, "title": d.title, "snippet": s, "tags": list(d.tags[:6])} for i, (d, _, s) in enumerate(recalled)}
    questions: dict[str, dict[str, Any]] = {}
    for i in range(len(recalled)):
        questions[f"cand_{i}_direct"] = {"type": "noul", "instructions": f"Does cand_{i} directly address the query?"}
        questions[f"cand_{i}_tier"] = {"type": "score", "instructions": f"Rate relevance of cand_{i} to the query.", "criteria": ["Unrelated", "Tangential", "Supporting", "Direct match"]}
    try:
        try: resp = fn({"query": query, "candidates": cands}, questions, agent_role=corpus_scope)
        except TypeError: resp = fn({"query": query, "candidates": cands}, questions)
    except Exception:
        resp = None
    answers = resp.get("answers", resp) if isinstance(resp, Mapping) else None
    if not isinstance(answers, Mapping) or not answers:
        return [(d, b, None, s) for d, b, s in recalled], False
    max_b = max((b for _, b, _ in recalled), default=1.0) or 1.0
    scored, used = [], False
    for i, (doc, bm25, snip) in enumerate(recalled):
        da, ta = answers.get(f"cand_{i}_direct"), answers.get(f"cand_{i}_tier")
        pd = float(da.get("noul", da.get("probability", 0.0))) if isinstance(da, Mapping) else None
        st = float(ta.get("score", 0.0)) if isinstance(ta, Mapping) else None
        if pd is None and st is None:
            scored.append((doc, bm25, None, snip, bm25 / max_b))
        else:
            used, nt = True, max(0.0, min(1.0, (st or 0.0) / 3.0))
            jv = round(0.65 * (pd if pd is not None else nt) + 0.35 * nt, 6)
            scored.append((doc, bm25, jv, snip, round(0.7 * jv + 0.3 * (bm25 / max_b), 6)))
    if not used:
        return [(d, b, None, s) for d, b, s in recalled], False
    scored.sort(key=lambda r: (-r[4], -r[1], r[0].doc_id))
    return [(d, b, j, s) for d, b, j, s, _ in scored], True


def hybrid_search(query: str, docs: Sequence[RagDoc], *, corpus_scope: str, allowed_scopes: Iterable[str] | None = None, top_k: int = 5, use_jev: bool = False, jev_fn: Callable[..., Any] | None = None, max_snippet_chars: int = 240) -> dict[str, Any]:
    permitted, scoped = _resolve_allowed_scopes(corpus_scope, allowed_scopes), []
    for doc in docs:
        if (ds := _norm_scope(doc.corpus_scope)) not in permitted:
            raise VaultIsolationError(f"vault isolation violation: doc {doc.doc_id!r} scope {ds!r} outside {sorted(permitted)}")
        scoped.append(doc)
    q_tokens = tokenize_cjk_and_latin(query)
    q_set, fts = set(q_tokens), _fts5_bm25(q_tokens, scoped)
    raw = (fts if fts is not None else _python_bm25(q_tokens, scoped)) or (_python_bm25(q_tokens, scoped) if q_tokens else {})
    ranked = sorted(((idx, round(val * (1.0 + 0.35 * len(q_set & set(tokenize_cjk_and_latin(f"{scoped[idx].title} {' '.join(scoped[idx].tags)} {' '.join(scoped[idx].paths)} {scoped[idx].summary}"))) / max(1, len(q_set))), 6)) for idx, val in raw.items()), key=lambda x: (-x[1], scoped[x[0]].doc_id))
    recalled = [(scoped[i], b, _snippet(scoped[i], q_tokens, max_snippet_chars)) for i, b in ranked[: max(max(1, top_k), RECALL_LIMIT)]]
    rows, jev_used = _jev_rerank(query, recalled, corpus_scope=_norm_scope(corpus_scope), jev_fn=jev_fn) if (use_jev and recalled) else ([(d, b, None, s) for d, b, s in recalled], False)
    hits = [asdict(RagHit(d.doc_id, _norm_scope(d.corpus_scope), round(jv if jv is not None else bv, 6), round(bv, 6), jv, redact_text(d.title), sn, redact_text(d.paths[0]) if d.paths else "", tuple(d.tags[:12]), redact_payload(dict(d.metadata)))) for d, bv, jv, sn in rows[: max(0, int(top_k))]]
    payload: dict[str, Any] = {"ok": True, "corpus_scope": _norm_scope(corpus_scope), "allowed_scopes": sorted(permitted), "query": redact_text(query), "backend": "fts5" if fts is not None else "python_bm25", "jev_reranked": jev_used, "total_docs": len(scoped), "hit_count": len(hits), "hits": hits}
    while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > ACI_MAX_BYTES and payload["hits"]:
        for row in payload["hits"]:
            row["metadata"], row["snippet"] = {}, str(row.get("snippet", ""))[:120]
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > ACI_MAX_BYTES:
            payload["hits"].pop()
            payload["hit_count"], payload["truncated"] = len(payload["hits"]), True
    return payload


def format_rag_context_hint(hits: Sequence[RagHit | Mapping[str, Any]], *, max_chars: int = 420) -> str:
    if max_chars <= 0 or not hits:
        return ""
    budget, parts = min(max_chars, ACI_MAX_BYTES), []
    for item in hits:
        row = asdict(item) if isinstance(item, RagHit) else dict(item)
        did, title, snip = str(row.get("doc_id") or "").strip(), str(row.get("title") or "").strip(), " ".join(str(row.get("snippet") or "").split())
        entry = f"[{did}] {title}: {snip}" if snip else f"[{did}] {title or did}"
        if len(" | ".join([*parts, entry])) > budget:
            if (rem := budget - (len(" | ".join(parts)) + (3 if parts else 0))) > 16:
                parts.append(entry[: max(1, rem - 3)].rstrip() + "...")
            break
        parts.append(entry)
    return redact_text(" | ".join(parts)[:budget])


def _read_bounded(path: Path, max_bytes: int = 256 * 1024) -> str:
    try:
        if getattr(os.lstat(path), "st_flags", 0) & getattr(os, "SF_DATALESS", 0x40000000): return ""
        with open(path, "rb") as fh: return fh.read(max_bytes).decode("utf-8", errors="replace")
    except OSError: return ""


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not (s := text.lstrip()).startswith("---") or len(parts := s.split("---", 2)) < 3:
        return {}, text
    meta, lkey = {}, None
    for ln in parts[1].splitlines():
        if re.match(r"^\s*-\s+", ln) and lkey:
            meta.setdefault(lkey, []).append(re.sub(r"^\s*-\s+", "", ln).strip().strip("\"'"))
        elif m := _FM_KV_RE.match(ln.strip()):
            k, v = m.group(1), m.group(2).strip()
            lkey = k if not v else None
            meta[k] = [] if not v else ([x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()] if (v.startswith("[") and v.endswith("]")) else v.strip("\"'"))
    return meta, parts[2].strip()


def load_handoff_corpus(handoff_root: Path, *, max_docs: int = 120) -> list[RagDoc]:
    if not (root := Path(handoff_root).expanduser()).is_dir():
        return []
    dirs = sorted(((p.stat().st_mtime, p) for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))), key=lambda x: (-x[0], x[1].name))
    docs: list[RagDoc] = []
    for mtime, gdir in dirs[: max(0, max_docs)]:
        sp, lp, rdir = gdir / "spec.md", gdir / "ledger.md", gdir / "receipts"
        st, lt = (_read_bounded(sp) if sp.is_file() else ""), (_read_bounded(lp) if lp.is_file() else "")
        rfiles = sorted(rdir.glob("*.md"))[:4] if rdir.is_dir() else []
        if not (combined := "\n".join(x for x in (st, lt, *(_read_bounded(rf, 65536) for rf in rfiles)) if x)).strip():
            continue
        title = next((ln.lstrip("# ").strip() for ln in combined.splitlines() if ln.startswith("# ")), gdir.name)
        owned, evts = re.findall(r"`([A-Za-z0-9_./*-]+(?:/[A-Za-z0-9_./*-]+)+)`", st), re.findall(r"EVT=([A-Z0-9_]+)", lt)
        summary = next((ln.strip("-* ").strip() for ln in (st or lt).splitlines() if ln.strip() and not ln.startswith("#")), title)[:240]
        paths = tuple(str(p) for p in (sp, lp, *rfiles) if p.is_file())
        docs.append(RagDoc(f"handoff:{gdir.name}", "handoff", title, summary, combined[:16_000], tuple(dict.fromkeys([gdir.name, *evts[-4:], *owned[:8]])), paths, str(int(mtime)), {"goal_id": gdir.name, "events": evts[-6:], "owned_paths": owned[:10]}))
    return docs


def load_markdown_vault_corpus(root_dir: Path, *, corpus_scope: str, max_docs: int = 200) -> list[RagDoc]:
    if (scope := _norm_scope(corpus_scope)) in {"design-studio", "design-vault"}:
        raise VaultIsolationError("use load_design_vault_corpus for design-studio scope")
    if not (root := Path(root_dir).expanduser()).is_dir():
        return []
    files = sorted(((p.stat().st_mtime, p) for p in root.rglob("*") if not p.is_symlink() and p.is_file() and p.suffix.lower() in {".md", ".json"} and not any(pt.startswith(".") or pt in {"__pycache__", "node_modules"} for pt in p.parts)), key=lambda x: (-x[0], str(x[1])))
    docs: list[RagDoc] = []
    for mtime, path in files[: max(0, max_docs)]:
        if not (raw := _read_bounded(path)).strip():
            continue
        rel = str(path.relative_to(root))
        if path.suffix.lower() == ".json":
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            title = str(obj.get("title") or obj.get("name") or path.stem) if isinstance(obj, Mapping) else path.stem
            summary = str(obj.get("summary") or obj.get("description") or "")[:240] if isinstance(obj, Mapping) else ""
            rtags = obj.get("tags", []) if isinstance(obj, Mapping) else []
            tags, body, meta = (tuple(str(x) for x in rtags) if isinstance(rtags, list) else ()), json.dumps(obj, ensure_ascii=False)[:12_000], {"rel_path": rel}
        else:
            fm, body_md = _parse_frontmatter(raw)
            title = str(fm.get("title") or next((ln.lstrip("# ").strip() for ln in body_md.splitlines() if ln.startswith("# ")), "") or path.stem)
            summary = str(fm.get("insight") or fm.get("summary") or fm.get("description") or next((ln.lstrip("> ").strip() for ln in body_md.splitlines() if ln.strip() and not ln.startswith(("#", "|", "---"))), title))[:240]
            ft = fm.get("tags", [])
            tags, body, meta = tuple(str(t) for t in (ft if isinstance(ft, list) else [ft]) if t), body_md[:12_000], {"rel_path": rel, "category": fm.get("category", "")}
        docs.append(RagDoc(f"{scope}:{rel}", scope, title, summary, body, tuple(dict.fromkeys([path.stem, *tags])), (str(path),), str(int(mtime)), meta))
    return docs


def load_design_vault_corpus(vault_dir: Path, *, platform: str | None = None) -> list[RagDoc]:
    if not (root := Path(vault_dir).expanduser()).is_dir():
        return []
    want, docs, seen = (platform.strip().lower() if platform else None), [], set()
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(_read_bounded(path, 2 * 1024 * 1024))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        fplat = str(payload.get("platform") or path.stem).lower()
        for kind in ("inspirations", "tools", "cards"):
            for entry in payload.get(kind, []) if isinstance(payload.get(kind), list) else []:
                if not isinstance(entry, Mapping):
                    continue
                plats = [str(p).lower() for p in (*(entry.get("platforms_v2") or []), *(entry.get("platforms") or []), fplat) if p]
                if want and want not in plats and not any(want in p for p in plats):
                    continue
                cid = str(entry.get("id") or entry.get("name") or path.stem)
                if (dkey := (f"{fplat}:{cid}" if want is None else cid)) in seen:
                    continue
                seen.add(dkey)
                hexes = tuple(dict.fromkeys(h.upper() for h in _HEX_RE.findall(json.dumps(entry, ensure_ascii=False))))
                tags = tuple(dict.fromkeys(str(x) for x in (cid, entry.get("category"), entry.get("sub_category"), entry.get("primary_dimension"), *(entry.get("secondary_dimensions") or []), *(entry.get("surface_modes") or []), *(entry.get("aliases") or []), *plats, *hexes) if x))
                title = str(entry.get("name") or entry.get("title") or cid)
                summary = str(entry.get("canonical_prompt") or entry.get("platform_rationale") or entry.get("token_integration") or entry.get("source_prompt_zh") or title)[:240]
                body = "\n".join(str(entry.get(f) or "") for f in ("source_prompt_zh", "canonical_prompt", "platform_rationale", "desktop_macos_implementation", "mobile_ios_implementation", "web_implementation", "native_guardrails", "token_guardrails") if entry.get(f))
                docs.append(RagDoc(f"design:{fplat}:{cid}", "design-studio", title, summary, f"{body}\n{' '.join(hexes)}".strip(), tags, (str(path),), str(entry.get("last_used_at") or ""), {"id": cid, "kind": kind, "platform": fplat, "primary_dimension": entry.get("primary_dimension", ""), "hex_palette": list(hexes)}))
    return docs
