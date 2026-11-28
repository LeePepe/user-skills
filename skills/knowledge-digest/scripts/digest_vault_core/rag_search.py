"""Hybrid RAG semantic search over knowledge-vault with strict scope isolation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from .common import *

_FORBIDDEN_DESIGN_VAULT_PARTS = frozenset({"design-studio", "design-vault"})


def _assert_knowledge_vault_dir(vault_dir: Path) -> Path:
    raw = Path(vault_dir).expanduser()
    resolved = raw.resolve(strict=False)
    for part in (*raw.parts, *resolved.parts):
        if part.casefold() in _FORBIDDEN_DESIGN_VAULT_PARTS:
            raise VaultIsolationError(
                f"vault isolation violation: knowledge-digest cannot query design vault path {redact_text(str(vault_dir))!r}"
            )
    return resolved


def search_knowledge_vault(
    *,
    query: str,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    top_k: int = 5,
    use_jev: bool = False,
    corpus_scope: str = "knowledge-vault",
    allowed_scopes: Iterable[str] | None = None,
    max_docs: int = 500,
    jev_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    clean_query = str(query or "").strip()
    if not clean_query:
        return {
            "ok": False,
            "status": "invalid_query",
            "error": "--query must be a non-empty string",
            "corpus_scope": "knowledge-vault",
        }
    if int(top_k) <= 0:
        return {
            "ok": False,
            "status": "invalid_top_k",
            "error": "--top-k must be a positive integer",
            "corpus_scope": "knowledge-vault",
        }
    norm_scope = str(corpus_scope or "").strip().lower()
    if norm_scope != "knowledge-vault":
        raise VaultIsolationError(
            f"vault isolation violation: knowledge-digest search requires corpus_scope='knowledge-vault', got {corpus_scope!r}"
        )
    eff_scopes = frozenset({"knowledge-vault"}) if allowed_scopes is None else frozenset(
        str(s).strip().lower() for s in allowed_scopes if str(s).strip()
    )
    if not eff_scopes or eff_scopes != frozenset({"knowledge-vault"}):
        raise VaultIsolationError(
            f"vault isolation violation: cross-vault scopes forbidden in knowledge-digest search: {sorted(eff_scopes)}"
        )
    safe_dir = _assert_knowledge_vault_dir(vault_dir)
    docs = [
        doc
        for doc in load_markdown_vault_corpus(safe_dir, corpus_scope="knowledge-vault", max_docs=max_docs)
        if doc.metadata.get("rel_path") != "topic-ledger.json"
    ]
    return hybrid_search(
        clean_query,
        docs,
        corpus_scope="knowledge-vault",
        allowed_scopes=eff_scopes,
        top_k=int(top_k),
        use_jev=bool(use_jev),
        jev_fn=jev_fn,
    )
