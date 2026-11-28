"""Shared, dependency-free runtime primitives for skill scripts."""

from .cli_contract import (
    SelfDisclosingArgumentParser,
    compact_json_dumps,
    is_valid_skill_slug,
    redact_payload,
    redact_text,
)
from .copilot_trace import read_copilot_sessions
from .fs_guard import (
    atomic_write_json,
    get_cached,
    is_safe_local_dir,
    is_safe_local_file,
    put_cached,
    safe_read_text,
    stat_watermark_cache,
)
from .git_version import SkillVersionInfo, resolve_skill_version, verify_commit_exists
from .hybrid_rag import (
    ACI_MAX_BYTES,
    ALLOWED_CORPUS_SCOPES,
    RagDoc,
    RagHit,
    VaultIsolationError,
    format_rag_context_hint,
    hybrid_search,
    load_design_vault_corpus,
    load_handoff_corpus,
    load_markdown_vault_corpus,
    tokenize_cjk_and_latin,
)

__all__ = [
    "ACI_MAX_BYTES", "ALLOWED_CORPUS_SCOPES", "RagDoc", "RagHit",
    "SelfDisclosingArgumentParser", "SkillVersionInfo", "VaultIsolationError",
    "atomic_write_json", "compact_json_dumps", "format_rag_context_hint",
    "get_cached", "hybrid_search", "is_safe_local_dir", "is_safe_local_file",
    "is_valid_skill_slug", "load_design_vault_corpus", "load_handoff_corpus",
    "load_markdown_vault_corpus", "put_cached", "read_copilot_sessions",
    "redact_payload", "redact_text", "resolve_skill_version", "safe_read_text",
    "stat_watermark_cache", "tokenize_cjk_and_latin", "verify_commit_exists",
]
