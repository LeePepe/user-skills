#!/usr/bin/env python3
"""Cross-handoff 4-Element Narrative Hybrid RAG for story-craft."""
from __future__ import annotations

import json, os, re, sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from skill_runtime.cli_contract import SelfDisclosingArgumentParser, redact_payload, redact_text  # noqa: E402
from skill_runtime.hybrid_rag import ACI_MAX_BYTES, RagDoc, _safe_root, hybrid_search, load_handoff_corpus  # noqa: E402

DEFAULT_HANDOFF_ROOT = Path.home() / "Development" / "handoff"
PROMPT_SLOTS_PATH = _SCRIPT_DIR.parent / "references" / "prompt-slots.json"
CORPUS_SCOPE = "retro-story"
_ISO_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}(?:T[0-9:+-]+)?\b")
_SHA_RE = re.compile(r"(?:\b(?:merge_commit_sha|commit_sha|merge_sha|sha|head|commit)\s*[=:]\s*`?|@)([0-9a-f]{7,40})\b", re.I)
_PR_RE = re.compile(r"(?:https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+|\bPR\s*#?\d+|\B#\d{2,6}\b)")
_DELTA_RE = re.compile(r"(\b\d+(?:\.\d+)?\s*(?:%|ms|s|kb|mb|lines?|tests?|shards?|x)\b|\b\d+/\d+\b|->|→|\bbefore\b|\bafter\b|<=|>=)", re.I)
_PIVOT_RE = re.compile(r"(\bH[1-5]\b|pivot|counter[- ]intuitive|root[_ -]?cause|instead of|whereas|why=|ruling|review_fail|blocked|fallback|直觉|原本|根因|排除|回退|碰壁)", re.I)
_TAKEAWAY_RE = re.compile(r"(physical[_ -]?lock|lock|guard|gate|contract|symlink|valve|invariant|mandate|rule|checklist|audit|verify|门禁|物理锁|护栏|契约|规则)", re.I)
_EXT_SECRET_RE = re.compile(
    r"(?i)(?:\bsk-(?:proj|ant|live)-[A-Za-z0-9_-]{8,}|\bgithub_pat_[A-Za-z0-9_]{8,}|\bAKIA[0-9A-Z]{16}\b|"
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b|\b(?:api[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|token|secret|cookie|password|passwd|authorization)\b(?:\\?['\"])?\s*[:=]\s*(?:\\?['\"])?"
    r"(?:Bearer\s+)?[A-Za-z0-9._~+/=-]{6,})"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_ABS_POSIX_RE = re.compile(r"(?:(?<![A-Za-z0-9_.-])|(?<=\\[nrt]))/(?:Users|home|private/var|var/root|var/folders|Volumes)(?:/[^\s`\"')\]>\\]+)?")


def _scrub(text: str, max_chars: int = 240, *, keep_lines: bool = False) -> tuple[str, list[str]]:
    raw = str(text or "").replace("\\n", "\n").replace("\\r", "\n").replace("\\t", " ")
    kinds = [k for k, ok in (("secret", bool(_EXT_SECRET_RE.search(raw)) or any(t in raw for t in ("ghp_", "github_pat_", "lin_api_", "Bearer ", "sk-"))), ("absolute_path", bool(_ABS_POSIX_RE.search(raw))), ("email", bool(_EMAIL_RE.search(raw)))) if ok]
    cleaned = _ABS_POSIX_RE.sub("<redacted-path>", _EMAIL_RE.sub("<redacted-email>", _EXT_SECRET_RE.sub("<redacted>", redact_text(raw))))
    cleaned = "\n".join(" ".join(ln.split()) for ln in cleaned.splitlines()) if keep_lines else " ".join(cleaned.split())
    return (cleaned[: max(16, max_chars - 3)].rstrip() + "..." if len(cleaned) > max_chars else cleaned), kinds


class StoryRagArgumentParser(SelfDisclosingArgumentParser):
    """Bound parser errors to three scrubbed lines ending with Reference:."""

    def error(self, message: str) -> None:
        safe_msg, _ = _scrub(message, 180)
        sys.stderr.write(f"story_rag.py: {safe_msg}\nUsage: story_rag.py [search] --topic <query> [--top-k 5] [--handoff-root <dir>] --json\nReference: skills/story-craft/SKILL.md\n")
        raise SystemExit(2)


def _load_prompt_slots() -> dict[str, Any]:
    try:
        if PROMPT_SLOTS_PATH.is_file() and not PROMPT_SLOTS_PATH.is_symlink():
            return data if isinstance(data := json.loads(PROMPT_SLOTS_PATH.read_text(encoding="utf-8")), dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return {}


def _sanitize_doc(doc: RagDoc) -> tuple[RagDoc, list[str]]:
    kinds: set[str] = set()

    def s(val: str, cap: int, *, lines: bool = False) -> str:
        out, k = _scrub(val, cap, keep_lines=lines)
        kinds.update(k)
        return out

    meta = dict(doc.metadata)
    meta["goal_id"] = s(str(meta.get("goal_id") or doc.doc_id.removeprefix("handoff:")), 96)
    meta["owned_paths"] = [s(str(p), 96) for p in (meta.get("owned_paths") or [])[:10]]
    clean_id = f"handoff:{meta['goal_id']}" if doc.doc_id.startswith("handoff:") else s(doc.doc_id, 96)
    return (
        RagDoc(
            clean_id, doc.corpus_scope, s(doc.title, 180), s(doc.summary, 240), s(doc.body, 16_000, lines=True),
            tuple(s(t, 64) for t in doc.tags[:12]), tuple(s(Path(p).name if "/" in p else p, 80) for p in doc.paths[:4]),
            doc.updated_at, meta,
        ),
        sorted(kinds),
    )


def _pick_lines(lines: Sequence[str], pattern: re.Pattern[str], exclude: set[str], limit: int = 2) -> list[str]:
    picked: list[str] = []
    for raw in lines:
        s = re.sub(r"^\*{1,2}([^*]+)\*{1,2}:\s*", r"\1: ", raw.strip().lstrip("-*0123456789. ").strip())
        if len(s) >= 12 and not s.startswith(("#", "|---", "```")) and s not in exclude and pattern.search(s) and s not in picked:
            picked.append(s)
            if len(picked) >= limit:
                break
    return picked


def extract_four_elements(doc: RagDoc, hit: Mapping[str, Any], doc_redactions: Sequence[str] = ()) -> dict[str, Any]:
    lines = [ln.strip() for ln in f"{doc.summary}\n{doc.body}".splitlines() if ln.strip()]
    redacted_kinds: set[str] = set(doc_redactions)

    def clean(val: str, cap: int = 200) -> str:
        out, kinds = _scrub(val, cap)
        redacted_kinds.update(kinds)
        return out

    goal_id = clean(str(doc.metadata.get("goal_id") or doc.doc_id.removeprefix("handoff:")), 96)
    dates = sorted({d[:10] for d in _ISO_DATE_RE.findall(doc.body)})
    window = f"{dates[0]}..{dates[-1]}" if len(dates) >= 2 else (dates[0] if dates else "unknown")
    prs = list(dict.fromkeys(clean(m.group(0), 96) for m in _PR_RE.finditer(doc.body)))[:3]
    shas = list(dict.fromkeys(m.group(1)[:12] for m in _SHA_RE.finditer(doc.body) if not m.group(1).isdigit()))[:3]
    fallback_snip = clean(str(hit.get("snippet") or doc.summary or doc.title), 200)
    owned = [clean(p, 80) for p in (doc.metadata.get("owned_paths") or [])[:4]]
    used: set[str] = set()
    pivots = _pick_lines(lines, _PIVOT_RE, used); used.update(pivots)
    deltas = _pick_lines(lines, _DELTA_RE, used); used.update(deltas)
    takeaways = _pick_lines(lines, _TAKEAWAY_RE, used)
    return {
        "goal_id": goal_id, "doc_id": clean(doc.doc_id, 96), "title": clean(doc.title, 140), "score": hit.get("score", 0.0),
        "provenance": {"source_goal": goal_id, "evidence_window": window, "refs": (prs + shas)[:4], "evidence_pointers": list(doc.paths)},
        "pivot": clean(" ; ".join(pivots), 220) if pivots else f"Mechanism shift in {goal_id}: {fallback_snip}",
        "delta": clean(" ; ".join(deltas), 220) if deltas else f"Verified artifacts={len(doc.paths)}, events={len(doc.metadata.get('events') or [])}",
        "takeaway": clean(" ; ".join(takeaways), 220) if takeaways else (f"Enforce {', '.join(owned)} contract gates" if owned else fallback_snip),
        "desensitized": True, "redactions_applied": sorted(redacted_kinds),
    }


def _bounded_json(payload: Mapping[str, Any], *, indent: int | None = None) -> str:
    text = json.dumps(redact_payload(dict(payload)), ensure_ascii=False, indent=indent)
    if len((text + "\n").encode("utf-8")) <= ACI_MAX_BYTES:
        return text
    fb = dict(payload)
    if "error" in fb:
        fb["error"] = _scrub(str(fb["error"]), 200)[0]
    elif "matches" in fb:
        fb["matches"], fb["hit_count"], fb["template_categories"] = [], 0, list(fb.get("template_categories") or [])[:5]
        fb["narrative_chain"] = {k: [] for k in ("provenance", "pivot", "delta", "takeaway")}
        if isinstance(fb.get("desensitization"), Mapping):
            fb["desensitization"] = {**dict(fb["desensitization"]), "privacy_redlines": _scrub(str(fb["desensitization"].get("privacy_redlines") or ""), 200)[0]}
    fb["truncated"] = True
    compact = json.dumps(redact_payload(fb), ensure_ascii=False)
    return compact if len((compact + "\n").encode("utf-8")) <= ACI_MAX_BYTES else json.dumps({"ok": bool(payload.get("ok")), "skill": "story-craft", "truncated": True, "reference": "skills/story-craft/SKILL.md"}, ensure_ascii=False)


def run_story_rag(
    query: str, *, handoff_root: Path = DEFAULT_HANDOFF_ROOT, top_k: int = 5, use_jev: bool = False, jev_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    clean_q, q_redactions = _scrub(query, 256)
    if not clean_q or re.fullmatch(r"(?:<redacted(?:-[a-z]+)?>\s*)+", clean_q):
        raise ValueError("query/topic must contain searchable non-secret terms")
    if top_k < 1 or top_k > 20:
        raise ValueError("top-k must be between 1 and 20")
    safe_dir = _safe_root(Path(handoff_root), allow_external_volume=True)
    if safe_dir is None or not os.access(safe_dir, os.R_OK | os.X_OK):
        bad_root, _ = _scrub(str(handoff_root), 140)
        raise FileNotFoundError(f"handoff root not found or unsafe: {bad_root}")

    sanitized_pairs = [_sanitize_doc(d) for d in load_handoff_corpus(safe_dir)]
    docs, doc_by_id = [p[0] for p in sanitized_pairs], {d.doc_id: (d, k) for d, k in sanitized_pairs}
    rag = hybrid_search(clean_q, docs, corpus_scope=CORPUS_SCOPE, top_k=top_k, use_jev=use_jev, jev_fn=jev_fn)
    slots = _load_prompt_slots()
    redline_text, _ = _scrub(str((slots.get("global") or {}).get("privacy_redlines") or "Secrets and private paths removed."), 240)
    chains = [extract_four_elements(doc_by_id[h["doc_id"]][0], h, doc_by_id[h["doc_id"]][1]) for h in rag["hits"] if h.get("doc_id") in doc_by_id]
    payload: dict[str, Any] = redact_payload({
        "ok": True, "skill": "story-craft", "corpus_scope": CORPUS_SCOPE, "query": clean_q,
        "backend": rag["backend"], "jev_reranked": rag["jev_reranked"], "total_docs": rag["total_docs"],
        "hit_count": len(chains), "prompt_slots_loaded": bool(slots),
        "template_categories": [_scrub(str(c), 48)[0] for c in sorted((slots.get("categories") or {}).keys())[:8]],
        "desensitization": {
            "passed": True, "privacy_redlines": redline_text,
            "redactions_applied": sorted({*q_redactions, *(k for c in chains for k in c["redactions_applied"])}),
        },
        "narrative_chain": {k: [c[k] for c in chains] for k in ("provenance", "pivot", "delta", "takeaway")}, "matches": chains,
    })
    while len((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")) > ACI_MAX_BYTES and payload["matches"]:
        payload["matches"].pop()
        for key in ("provenance", "pivot", "delta", "takeaway"):
            payload["narrative_chain"][key] = payload["narrative_chain"][key][: len(payload["matches"])]
        payload["hit_count"], payload["truncated"] = len(payload["matches"]), True
    return payload


def build_parser() -> StoryRagArgumentParser:
    parser = StoryRagArgumentParser(prog="story_rag.py", description="Cross-handoff 4-Element Narrative Hybrid RAG.")
    parser.add_argument("command", nargs="?", choices=("search",), help="Optional 'search' subcommand.")
    parser.add_argument("--topic", "--query", dest="query", default="", help="Narrative topic or query string.")
    parser.add_argument("--top-k", type=int, default=5, help="Maximum handoffs to return (1..20).")
    parser.add_argument("--use-jev", action="store_true", help="Enable optional Layer-1 Jev reranking.")
    parser.add_argument("--handoff-root", default=os.environ.get("STORY_CRAFT_HANDOFF_ROOT", str(DEFAULT_HANDOFF_ROOT)), help="Handoff directory root.")
    parser.add_argument("--json", action="store_true", help="Emit bounded JSON output (<=12KB).")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not str(args.query or "").strip():
        parser.error("one of --topic or --query is required")
    try:
        payload = run_story_rag(args.query, handoff_root=Path(args.handoff_root), top_k=args.top_k, use_jev=args.use_jev)
    except (ValueError, OSError) as exc:
        safe_err, _ = _scrub(str(exc), 180)
        err = {"ok": False, "skill": "story-craft", "error": safe_err, "reference": "skills/story-craft/SKILL.md"}
        sys.stderr.write(f"story_rag.py: {safe_err}\nReference: skills/story-craft/SKILL.md\n")
        if args.json:
            print(_bounded_json(err))
        return 2
    if args.json:
        print(_bounded_json(payload, indent=2))
    else:
        lines = [f"[story-craft] query={payload['query']!r} hits={payload['hit_count']}/{payload['total_docs']} ({payload['backend']})"]
        lines.extend(f"- {m['goal_id']}: Pivot={m['pivot']} | Delta={m['delta']} | Takeaway={m['takeaway']}" for m in payload["matches"])
        raw_txt = "\n".join(lines)
        print(raw_txt if len((raw_txt + "\n").encode("utf-8")) <= ACI_MAX_BYTES else raw_txt.encode("utf-8")[: ACI_MAX_BYTES - 4].decode("utf-8", errors="ignore") + "...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
