"""Shared, dependency-free runtime primitives for skill scripts."""

from .cli_contract import (
    HERDR_ORIGIN_COMMENT_RE,
    HERDR_ORIGIN_KINDS,
    HERDR_ORIGIN_ROLES,
    HERDR_ORIGIN_SCHEMA,
    HERDR_SESSION_ENV_VARS,
    SelfDisclosingArgumentParser,
    append_herdr_origin_tag,
    compact_json_dumps,
    extract_dispatched_instruction,
    format_herdr_origin_tag,
    is_valid_skill_slug,
    normalize_herdr_agent_role,
    parse_herdr_origin_tag,
    redact_payload,
    redact_text,
    split_routed_prompt_sections,
    strip_herdr_origin_comments,
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
    "ACI_MAX_BYTES", "ALLOWED_CORPUS_SCOPES", "HERDR_ORIGIN_COMMENT_RE",
    "HERDR_ORIGIN_KINDS", "HERDR_ORIGIN_ROLES", "HERDR_ORIGIN_SCHEMA",
    "HERDR_SESSION_ENV_VARS", "RagDoc", "RagHit", "SelfDisclosingArgumentParser",
    "SkillVersionInfo", "VaultIsolationError",
    "append_herdr_origin_tag", "atomic_write_json", "compact_json_dumps",
    "extract_dispatched_instruction", "format_herdr_origin_tag", "format_rag_context_hint",
    "get_cached", "hybrid_search", "is_safe_local_dir", "is_safe_local_file",
    "is_valid_skill_slug", "load_design_vault_corpus", "load_handoff_corpus",
    "normalize_herdr_agent_role", "parse_herdr_origin_tag",
    "load_markdown_vault_corpus", "put_cached", "read_copilot_sessions",
    "redact_payload", "redact_text", "resolve_skill_version", "safe_read_text",
    "split_routed_prompt_sections", "stat_watermark_cache", "strip_herdr_origin_comments",
    "tokenize_cjk_and_latin", "verify_commit_exists",
]
