#!/usr/bin/env python3
"""Design-vault Hybrid RAG wrapper for `design-studio` via `skill_runtime.hybrid_rag`."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from skill_runtime.hybrid_rag import (  # noqa: E402
    ACI_MAX_BYTES,
    ALLOWED_CORPUS_SCOPES,
    VaultIsolationError,
    hybrid_search,
    load_design_vault_corpus,
)

DESIGN_CORPUS_SCOPE = "design-vault"
_FORBIDDEN_VAULT_TOKENS = frozenset({"knowledge-vault", "knowledge-digest", "collected-tools", "handoff"})


def _validate_design_vault_target(vault_dir: Path, corpus_scope: str) -> Path:
    scope = str(corpus_scope or "").strip().lower()
    if scope not in {"design-vault", "design-studio"}:
        raise VaultIsolationError(
            f"design-studio strictly enforces corpus_scope='design-vault' (rejected {corpus_scope!r})"
        )
    allowed = ALLOWED_CORPUS_SCOPES.get(scope, frozenset())
    if "knowledge-vault" in allowed:
        raise VaultIsolationError("knowledge-vault must never be permitted in design-studio")

    raw_parts = {p.casefold() for p in Path(vault_dir).expanduser().parts}
    if raw_parts & _FORBIDDEN_VAULT_TOKENS:
        raise VaultIsolationError(
            f"cross-vault path rejected for design-studio: {vault_dir} (knowledge-vault isolation)"
        )
    resolved = Path(vault_dir).expanduser().resolve()
    resolved_parts = {p.casefold() for p in resolved.parts}
    if resolved_parts & _FORBIDDEN_VAULT_TOKENS:
        raise VaultIsolationError(
            f"cross-vault resolved path rejected for design-studio: {resolved} (knowledge-vault isolation)"
        )
    return resolved


def _index_vault_cards(vault_dir: Path, platform: str | None) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    if not vault_dir.is_dir():
        return index
    want = platform.strip().lower() if platform else None
    for path in sorted(p for p in vault_dir.glob("*.json") if not p.is_symlink()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        fplat = str(payload.get("platform") or path.stem).lower()
        for kind in ("inspirations", "tools", "cards"):
            entries = payload.get(kind, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                plats = [str(p).lower() for p in (*(entry.get("platforms_v2") or []), *(entry.get("platforms") or []), fplat) if p]
                if want and want not in plats and not any(want in p for p in plats):
                    continue
                cid = str(entry.get("id") or entry.get("name") or path.stem)
                index[(fplat, cid)] = entry
    return index


def _enrich_hit(hit: dict[str, Any], card_index: dict[tuple[str, str], dict[str, Any]], dim_prefixes: dict[str, str]) -> dict[str, Any]:
    meta = dict(hit.get("metadata") or {})
    fplat = str(meta.get("platform") or "")
    cid = str(meta.get("id") or hit.get("doc_id", "").split(":")[-1])
    raw_card = card_index.get((fplat, cid), {})

    primary_dim = str(raw_card.get("primary_dimension") or meta.get("primary_dimension") or "")
    secondary_dims = list(raw_card.get("secondary_dimensions") or [])
    sub_cat = str(raw_card.get("sub_category") or "")
    prefix = str(raw_card.get("prefix") or dim_prefixes.get(primary_dim) or (cid.split("-", 1)[0] if "-" in cid else ""))
    hex_palette = list(meta.get("hex_palette") or [])
    measured_palette = raw_card.get("measured_palette")
    if isinstance(measured_palette, list):
        for swatch in measured_palette:
            if isinstance(swatch, dict) and swatch.get("hex") and str(swatch["hex"]).upper() not in hex_palette:
                hex_palette.append(str(swatch["hex"]).upper())
    else:
        measured_palette = [{"hex": hx} for hx in hex_palette[:8]] if hex_palette else []

    taxonomy_8d = {
        "prefix": prefix,
        "primary_dimension": primary_dim,
        "secondary_dimensions": secondary_dims[:4],
        "sub_category": sub_cat,
        "platforms": list(raw_card.get("platforms_v2") or raw_card.get("platforms") or ([fplat] if fplat else [])),
    }
    motion = {
        "motion_tier": str(raw_card.get("motion_tier") or ""),
        "category": str(raw_card.get("category") or ""),
        "state_machine": str(raw_card.get("state_machine") or "")[:180],
    }
    meta.update({
        "id": cid,
        "taxonomy_8d": taxonomy_8d,
        "motion": motion,
        "measured_palette": measured_palette[:8] if isinstance(measured_palette, list) else [],
        "hex_palette": hex_palette[:12],
    })
    enriched = dict(hit)
    enriched["id"] = cid
    enriched["taxonomy_8d"] = taxonomy_8d
    enriched["motion"] = motion
    enriched["measured_palette"] = meta["measured_palette"]
    enriched["hex_palette"] = meta["hex_palette"]
    enriched["metadata"] = meta
    return enriched


def _enforce_aci_budget(payload: dict[str, Any], max_bytes: int = ACI_MAX_BYTES) -> dict[str, Any]:
    while len(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")) > max_bytes:
        hits = payload.get("hits") or []
        if not hits:
            payload["query"] = str(payload.get("query") or "")[:128]
            payload["truncated"] = True
            break
        slimmed = False
        for row in hits:
            if row.get("snippet") and len(str(row["snippet"])) > 96:
                row["snippet"] = str(row["snippet"])[:93] + "..."
                slimmed = True
            if isinstance(row.get("tags"), list) and len(row["tags"]) > 6:
                row["tags"] = row["tags"][:6]
                slimmed = True
        if slimmed and len(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")) <= max_bytes:
            break
        hits.pop()
        payload["matches"] = hits
        payload["hit_count"] = len(hits)
        payload["truncated"] = True
    return payload


def run_design_vault_rag(
    query: str,
    *,
    vault_dir: Path,
    dim_prefixes: dict[str, str],
    platform: str | None = None,
    dimension: str | None = None,
    top_k: int = 5,
    use_jev: bool = False,
    corpus_scope: str = DESIGN_CORPUS_SCOPE,
    jev_fn: Any = None,
) -> dict[str, Any]:
    q_clean = str(query or "").strip()
    if not q_clean:
        raise ValueError("--rag-query must be non-empty")
    norm_plat = None if platform in (None, "", "shared") else str(platform).strip().lower()
    resolved_vault = _validate_design_vault_target(vault_dir, corpus_scope)
    docs = load_design_vault_corpus(resolved_vault, platform=norm_plat)
    if dimension:
        want_dim = dimension.strip().lower()
        docs = [
            d for d in docs
            if want_dim == str(d.metadata.get("primary_dimension") or "").lower()
            or want_dim in {t.lower() for t in d.tags}
        ]
    bounded_k = max(1, min(int(top_k), 25))
    rag_res = hybrid_search(
        q_clean,
        docs,
        corpus_scope=corpus_scope,
        allowed_scopes=("design-vault", "design-studio"),
        top_k=bounded_k,
        use_jev=bool(use_jev),
        jev_fn=jev_fn,
    )
    card_index = _index_vault_cards(resolved_vault, norm_plat)
    enriched_hits = [_enrich_hit(h, card_index, dim_prefixes) for h in rag_res.get("hits", [])]
    payload: dict[str, Any] = {
        "schema": "design-vault-rag/v1",
        "ok": True,
        "corpus_scope": DESIGN_CORPUS_SCOPE,
        "allowed_scopes": rag_res.get("allowed_scopes", ["design-studio", "design-vault"]),
        "query": rag_res.get("query", q_clean),
        "platform_filter": norm_plat,
        "dimension_filter": dimension,
        "top_k": bounded_k,
        "backend": rag_res.get("backend", "fts5"),
        "jev_reranked": bool(rag_res.get("jev_reranked", False)),
        "total_docs": int(rag_res.get("total_docs", len(docs))),
        "hit_count": len(enriched_hits),
        "hits": enriched_hits,
        "matches": enriched_hits,
    }
    return _enforce_aci_budget(payload, ACI_MAX_BYTES)


def build_stats_payload(
    *,
    catalog: dict[str, Any],
    taxonomy: dict[str, Any],
    eval_doc: dict[str, Any],
    vault_dir: Path,
    platform_filter: str | None,
    dim_prefixes: dict[str, str],
    load_fn: Any,
) -> dict[str, Any]:
    vault_files = {
        "desktop": vault_dir / "desktop-macos.json",
        "mobile": vault_dir / "mobile-ios.json",
        "web": vault_dir / "web.json",
    }
    target_platforms = [platform_filter] if platform_filter in {"desktop", "mobile", "web"} else ["desktop", "mobile", "web"]
    per_platform: dict[str, int] = {}
    unique_inspirations: dict[str, dict[str, Any]] = {}
    dim_counts = {prefix: 0 for prefix in ("CL", "VS", "CP", "DV", "GM", "MI", "LN", "TC")}

    for plat in ("desktop", "mobile", "web"):
        vpath = vault_files[plat]
        items = load_fn(vpath).get("inspirations", []) if vpath.is_file() else []
        per_platform[plat] = len(items)
        if plat in target_platforms:
            for item in items:
                iid = str(item.get("id", ""))
                if iid and iid not in unique_inspirations:
                    unique_inspirations[iid] = item

    for iid, item in unique_inspirations.items():
        pdim = str(item.get("primary_dimension", ""))
        prefix = dim_prefixes.get(pdim) or (iid.split("-", 1)[0] if "-" in iid else "")
        if prefix in dim_counts:
            dim_counts[prefix] += 1

    expected_archetypes = ("VS-005", "VS-006", "VS-007", "VS-008")
    present_archetypes = sorted(aid for aid in expected_archetypes if aid in unique_inspirations)
    tools = catalog.get("tools", [])
    patterns = catalog.get("patterns", [])
    return {
        "schema": "design-studio-stats/v1",
        "ok": True,
        "advisory_mode": "soft_exit_0",
        "platform_filter": platform_filter,
        "catalog": {
            "tools_count": len(tools),
            "patterns_count": len(patterns),
            "ip_count": sum(1 for item in patterns if str(item.get("id", "")).startswith("IP-")),
            "mp_count": sum(1 for item in patterns if str(item.get("id", "")).startswith("MP-")),
        },
        "vault": {
            "desktop": per_platform["desktop"],
            "mobile": per_platform["mobile"],
            "web": per_platform["web"],
            "total_inspirations": len(unique_inspirations),
            "taxonomy_dimensions": len(taxonomy.get("dimensions", [])),
            "dimensions": dim_counts,
        },
        "creative_spectrum_2_1_1": {
            "archetypes_present": present_archetypes,
            "quota_formula": "2_persona_deepening + 1_cross_domain + 1_wildcard",
            "vs_005_to_008_ready": len(present_archetypes) == 4,
        },
        "eval_cases_count": len(eval_doc.get("active_cases", [])),
        "drawers": ["toolchain-20-matrix.md", "remotion-video.md", "eval-cases.json"],
        "references": [
            "skills/design-studio/references/toolchain-20-matrix.md",
            "skills/design-studio/references/remotion-video.md",
            "skills/design-studio/references/eval-cases.json",
        ],
    }


class _RagParser(__import__("argparse").ArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(
            f"design_rag.py error: {message}\n"
            "  Fix: run `python3 skills/design-studio/scripts/design_rag.py --self-check` or use `design_catalog.py --rag-query <query>`.\n"
            "  References: skills/design-studio/references/toolchain-20-matrix.md · skills/design-studio/references/eval-cases.json\n"
        )
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    ap = _RagParser(description="Design-vault Hybrid RAG helper.")
    ap.add_argument("--self-check", action="store_true", help="Run fast design-vault RAG self-check")
    ap.add_argument("--json", action="store_true", help="Emit JSON output")
    ap.parse_args(sys.argv[1:] if argv is None else argv)
    vault_dir = SCRIPTS_DIR / "data" / "vault"
    res = run_design_vault_rag("palette", vault_dir=vault_dir, dim_prefixes={}, top_k=2)
    print(json.dumps({"ok": bool(res.get("ok")), "module": "design_rag", "hit_count": res.get("hit_count", 0)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
