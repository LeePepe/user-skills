"""Small CLI contracts shared by progressive skill scripts."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REFERENCE = "skills/skill-contract/references/valves-and-metabolism.md"
_HOME_PATH = re.compile(r"/" + "Users/" + r"[^/\\s]+")
_SECRET = re.compile(r"(?i)\b(?:ghp_[A-Za-z0-9_\-]+|lin_api_[A-Za-z0-9_\-]+|sk-[A-Za-z0-9_\-]+)\b")
_BEARER = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+\-/]+=*")


class SelfDisclosingArgumentParser(argparse.ArgumentParser):
    """Bound parser errors to three actionable lines."""

    def error(self, message: str) -> None:
        sys.stderr.write(f"{self.prog}: {message}\n")
        sys.stderr.write("Try --help or an unscoped command (audit/eval default to --all).\n")
        sys.stderr.write(f"Reference: {REFERENCE}\n")
        raise SystemExit(2)


def redact_text(value: str) -> str:
    value = _HOME_PATH.sub("~", str(value))
    value = _SECRET.sub("<redacted>", value)
    return _BEARER.sub(r"\1<redacted>", value)


def redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_payload(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_payload(item) for key, item in value.items()}
    return value


def _encoded(value: Any) -> str:
    return json.dumps(redact_payload(value), ensure_ascii=False, separators=(",", ":"))


def compact_json_dumps(payload: Any, *, max_bytes: int = 15_500, summary: bool = False, preserve_keys: tuple[str, ...] | list[str] = ()) -> str:
    """Serialize redacted JSON without exceeding the caller's byte budget."""
    if max_bytes < 64:
        raise ValueError("max_bytes must be at least 64")
    text = _encoded(payload)
    if not summary and len(text.encode("utf-8")) <= max_bytes:
        return text
    if isinstance(payload, dict):
        keep = {key: redact_payload(payload[key]) for key in preserve_keys if key in payload}
        compact = {"summary": True, **keep}
        text = _encoded(compact)
        if len(text.encode("utf-8")) <= max_bytes:
            return text
        for key in list(compact):
            if key not in preserve_keys and key != "summary":
                compact.pop(key, None)
        text = _encoded(compact)
        if len(text.encode("utf-8")) <= max_bytes:
            return text
    marker = {"summary": True, "truncated": True}
    for key in preserve_keys:
        if isinstance(payload, dict) and key in payload:
            marker[key] = redact_payload(payload[key])
    text = _encoded(marker)
    while len(text.encode("utf-8")) > max_bytes and len(marker) > 2:
        marker.pop(next(iter(k for k in marker if k not in {"summary", "truncated"})))
        text = _encoded(marker)
    return text


def is_valid_skill_slug(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", str(name)))
