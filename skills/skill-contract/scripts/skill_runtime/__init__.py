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

__all__ = [
    "SelfDisclosingArgumentParser", "SkillVersionInfo", "atomic_write_json",
    "compact_json_dumps", "get_cached", "is_safe_local_dir", "is_safe_local_file",
    "is_valid_skill_slug", "put_cached", "read_copilot_sessions", "redact_payload",
    "redact_text", "resolve_skill_version", "safe_read_text", "stat_watermark_cache", "verify_commit_exists",
]
