#!/usr/bin/env python3
"""Read-only zero-execution Hybrid RAG search over collected-tools notes."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
import json
import os
from pathlib import Path
import sys
from typing import Any

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from skill_runtime import (  # noqa: E402
    ACI_MAX_BYTES,
    RagDoc,
    SelfDisclosingArgumentParser,
    VaultIsolationError,
    compact_json_dumps,
    hybrid_search,
    is_safe_local_dir,
    load_markdown_vault_corpus,
    redact_text,
)

REFERENCE = "skills/save-tool/SKILL.md"
DEFAULT_COLLECTED_TOOLS_DIR = Path(
    os.environ.get("COLLECTED_TOOLS_DIR", str(Path.home() / "Development" / "Personal" / "collected-tools"))
).expanduser()
_FORBIDDEN_DESIGN_PARTS = frozenset({"design-studio", "design-vault"})


class _ToolVaultArgumentParser(SelfDisclosingArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(f"{self.prog}: {redact_text(message)}\n")
        sys.stderr.write("Try `python3 skills/save-tool/scripts/tool_vault_search.py --help`.\n")
        sys.stderr.write(f"Reference: {REFERENCE}\n")
        raise SystemExit(2)


def _assert_collected_tools_dir(vault_dir: Path) -> Path:
    raw = Path(vault_dir).expanduser()
    resolved = raw.resolve(strict=False)
    for part in (*raw.parts, *resolved.parts):
        if part.casefold() in _FORBIDDEN_DESIGN_PARTS:
            raise VaultIsolationError(
                f"vault isolation violation: save-tool cannot query design vault path {redact_text(str(vault_dir))!r}"
            )
    return resolved


def _load_collected_tools_corpus(vault_dir: Path, *, max_docs: int = 240) -> list[RagDoc]:
    safe_dir = _assert_collected_tools_dir(vault_dir)
    if not is_safe_local_dir(safe_dir):
        return []
    docs: list[RagDoc] = []
    seen_ids: set[str] = set()
    for top in sorted(p for p in safe_dir.iterdir() if not p.is_symlink() and p.is_file() and not p.name.startswith(".")):
        if top.suffix.lower() not in {".md", ".json"}:
            continue
        for item in load_markdown_vault_corpus(safe_dir, corpus_scope="collected-tools", max_docs=max_docs):
            if "/" not in str(item.metadata.get("rel_path", "")):
                if item.doc_id not in seen_ids:
                    seen_ids.add(item.doc_id)
                    docs.append(item)
        break
    repos_dir = safe_dir / "repos"
    if is_safe_local_dir(repos_dir):
        for repo in sorted(p for p in repos_dir.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")):
            for doc in load_markdown_vault_corpus(repo, corpus_scope="collected-tools", max_docs=4):
                rel = f"repos/{repo.name}/{doc.metadata.get('rel_path', '')}".rstrip("/")
                did = f"collected-tools:{rel}"
                if did in seen_ids or len(docs) >= max_docs:
                    continue
                seen_ids.add(did)
                tags = tuple(dict.fromkeys([repo.name, *doc.tags]))
                docs.append(
                    RagDoc(
                        did,
                        "collected-tools",
                        doc.title,
                        doc.summary,
                        doc.body,
                        tags,
                        doc.paths,
                        doc.updated_at,
                        {**doc.metadata, "rel_path": rel, "repo_slug": repo.name},
                    )
                )
    if len(docs) < max_docs:
        for item in load_markdown_vault_corpus(safe_dir, corpus_scope="collected-tools", max_docs=max_docs):
            if item.doc_id not in seen_ids and len(docs) < max_docs:
                seen_ids.add(item.doc_id)
                docs.append(item)
    return docs


def search_tool_vault(
    *,
    query: str,
    vault_dir: Path = DEFAULT_COLLECTED_TOOLS_DIR,
    top_k: int = 5,
    use_jev: bool = False,
    corpus_scope: str = "collected-tools",
    allowed_scopes: Iterable[str] | None = None,
    max_docs: int = 240,
    jev_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    clean_query = str(query or "").strip()
    if not clean_query:
        return {
            "ok": False,
            "status": "invalid_query",
            "error": "--query must be a non-empty string",
            "corpus_scope": "collected-tools",
        }
    if int(top_k) <= 0:
        return {
            "ok": False,
            "status": "invalid_top_k",
            "error": "--top-k must be a positive integer",
            "corpus_scope": "collected-tools",
        }
    norm_scope = str(corpus_scope or "").strip().lower()
    if norm_scope != "collected-tools":
        raise VaultIsolationError(
            f"vault isolation violation: save-tool requires corpus_scope='collected-tools', got {corpus_scope!r}"
        )
    eff_scopes = frozenset({"collected-tools"}) if allowed_scopes is None else frozenset(
        str(s).strip().lower() for s in allowed_scopes if str(s).strip()
    )
    if not eff_scopes or eff_scopes != frozenset({"collected-tools"}):
        raise VaultIsolationError(
            f"vault isolation violation: cross-vault scopes forbidden in tool_vault_search: {sorted(eff_scopes)}"
        )
    docs = _load_collected_tools_corpus(vault_dir, max_docs=max_docs)
    payload = hybrid_search(
        clean_query,
        docs,
        corpus_scope="collected-tools",
        allowed_scopes=eff_scopes,
        top_k=int(top_k),
        use_jev=bool(use_jev),
        jev_fn=jev_fn,
    )
    payload["zero_execution"] = True
    while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > ACI_MAX_BYTES and payload.get("hits"):
        payload["hits"].pop()
        payload["hit_count"] = len(payload["hits"])
        payload["truncated"] = True
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = _ToolVaultArgumentParser(description="Read-only Hybrid RAG search over collected-tools (zero execution)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_search = sub.add_parser("search", help="Search collected tool notes and reference repos without executing code")
    p_search.add_argument("--query", required=True, help="Tool name, capability, or domain query")
    p_search.add_argument("--top-k", dest="top_k", type=int, default=5, help="Maximum hits to return")
    p_search.add_argument("--use-jev", dest="use_jev", action="store_true", help="Enable optional Jev Layer-1 reranking")
    p_search.add_argument("--vault-dir", "--vault", dest="vault_dir", type=Path, default=DEFAULT_COLLECTED_TOOLS_DIR)
    p_search.add_argument("--scope", dest="corpus_scope", default="collected-tools", help="Corpus scope (strictly collected-tools)")
    p_search.add_argument("--json", action="store_true", help="Emit bounded JSON output (<=12KB)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "search":
        try:
            res = search_tool_vault(
                query=args.query,
                vault_dir=args.vault_dir,
                top_k=args.top_k,
                use_jev=args.use_jev,
                corpus_scope=args.corpus_scope,
            )
        except VaultIsolationError as exc:
            err = {
                "ok": False,
                "status": "vault_isolation_error",
                "error": redact_text(str(exc)),
                "corpus_scope": "collected-tools",
            }
            print(compact_json_dumps(err, max_bytes=ACI_MAX_BYTES, preserve_keys=("ok", "status", "error", "corpus_scope")))
            return 1
        out = json.dumps(res, ensure_ascii=False, indent=None if args.json else 2)
        if len(out.encode("utf-8")) > ACI_MAX_BYTES:
            out = compact_json_dumps(res, max_bytes=ACI_MAX_BYTES, preserve_keys=("ok", "corpus_scope", "query", "hit_count", "hits"))
        print(out)
        return 0 if res.get("ok") else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
