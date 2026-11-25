from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from skill_runtime import (
    SelfDisclosingArgumentParser,
    atomic_write_json,
    compact_json_dumps,
    redact_payload,
    safe_read_text,
)

SCRIPT_DIR = Path(__file__).resolve().parents[1]
_HANDOFF_ROOT = Path.home() / "Development/handoff/project-root-tracker"
HANDOFF_DIR = _HANDOFF_ROOT if (_HANDOFF_ROOT / "roots-seed.json").is_file() else SCRIPT_DIR
DEFAULT_SUMMARY = Path.home() / "Development/handoff/bing-cn-1030-bugs-summary.md"
DEFAULT_DEVSYNC = Path.home() / "Development/handoff/devsync1030"
DEFAULT_WORKTREES = Path.home() / "Development/worktrees"
DEFAULT_SUPERVISOR = Path.home() / "Development/job skills/my-pr-supervisor/config/my-prs.json"
DEFAULT_LATEST_DISCOVERY = Path.home() / "Development/handoff/my-pr-supervisor/latest-discovery.json"
DEFAULT_EVAL_ACTIONS = Path.home() / "Development/job skills/my-pr-supervisor/config/eval-actions.jsonl"
DEFAULT_AIDASH_EVAL_ACTIONS = Path.home() / "Library/Application Support/AIDash/jobs/my-pr-supervisor/eval-actions.jsonl"
DEFAULT_SEED = HANDOFF_DIR / "roots-seed.json"
DEFAULT_TOPIC_MAPS = (
    SCRIPT_DIR.parents[1] / "personal-m2-assistant/references/m2-domain-map.json",
    Path.home() / ".agents/skills/personal-m2-assistant/references/m2-domain-map.json",
    Path.home() / "Development/user-skills/skills/personal-m2-assistant/references/m2-domain-map.json",
)
TOPIC_TO_ROOT = {
    "sapphire-china-bugs": "china-release",
    "sapphire-notification": "sapphire-notification",
    "sapphire-smartbanner": "sapphire-smartbanner",
    "sapphire-discovery-kumo": "sapphire-discovery-kumo",
}
WORKSTREAMS = ("PR & PoP", "Bugs & Devsync", "Repo & Skill Sinks")

DEFAULT_STATE_DIR = HANDOFF_DIR / "state"
DEFAULT_SNAPSHOT = DEFAULT_STATE_DIR / "root-tree-snapshot.json"
DEFAULT_CACHE = DEFAULT_STATE_DIR / "roots-cache.json"
DEFAULT_DASHBOARD = HANDOFF_DIR / "dashboard/index.html"
DEFAULT_TEAMS_TIMELINE = DEFAULT_STATE_DIR / "teams-china-release-timeline.json"
TIMELINE_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
MANIFEST_RE = re.compile(r"<!--\s*ROOT-MANIFEST:v1\s+({.*?})\s*-->", re.DOTALL)
ROOT_META_RE = re.compile(r"<!--\s*ROOT-META:v1\s+({.*?})\s*-->", re.DOTALL)
BUG_ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*\[AB#(\d+)\]\([^)]*\)\s*\|\s*\*\*(.*?)\*\*\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$"
)
PR_RE = re.compile(r"(?:pull/|PR\s*[#=])\s*(\d+)", re.IGNORECASE)

SIGNALS = (
    "PoP Ready",
    "Auto-Healing S1/S2/S3",
    "P0 Blocking Decision",
    "Bugs",
    "Skill/Repo Sink >= 0.70",
)
LANDING_OPTIONS = (
    "LAND_REPO_TESTING_SKILL",
    "LAND_PR_SNAPSHOT_TEST",
    "LAND_PR_AUTO_REVIEW_EVAL",
)


class OneWayBoundaryError(RuntimeError):
    """Raised when code attempts to write tracking data to a non-Linear source."""


class LinearSyncError(RuntimeError):
    """Raised for an expected Linear API failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default


def _read_json_from_text(text: str, default: Any = None) -> Any:
    try:
        value = json.loads(text)
    except (ValueError, TypeError, json.JSONDecodeError):
        return default
    return value if isinstance(value, Mapping) else default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(f"{key} {_text(value_part)}" for key, value_part in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(item) for item in value)
    return str(value or "")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _iso_date(value: Any) -> str:
    match = TIMELINE_DATE_RE.search(str(value or ""))
    return match.group(1) if match else ""


def _first_value(data: Mapping[str, Any], keys: Sequence[str], default: Any = "") -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def _timeline_items(raw: Any) -> list[Any]:
    if isinstance(raw, Mapping):
        return [{"name": key, **(value if isinstance(value, Mapping) else {"date": value})} for key, value in raw.items()]
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return [raw] if raw not in (None, "") else []


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "confirmed", "y"}
    return bool(value)


def _normalize_timeline_item(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        name = _first_value(item, ("name", "title", "milestone", "stage", "event"), "Checkpoint")
        summary = _first_value(item, ("summary", "description", "context", "notes"), "")
        return {
            "name": str(name),
            "date": _iso_date(_first_value(item, ("date", "target_date", "targetDate", "release_date", "when"), "")),
            "status": str(_first_value(item, ("status", "state"), "")),
            "owner": str(_first_value(item, ("owner", "dri", "responsible", "lead"), "")),
            "summary": str(summary),
            "deep_link": str(_first_value(item, ("deep_link", "deepLink", "url", "link"), "")),
        }
    return {"name": str(item), "date": "", "status": "", "owner": "", "summary": "", "deep_link": ""}


def _normalize_context_item(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return {
            "summary": str(_first_value(item, ("summary", "text", "context", "note", "description"), "")),
            "owner": str(_first_value(item, ("owner", "dri", "responsible"), "")),
            "date": _iso_date(_first_value(item, ("date", "updated_at", "updatedAt"), "")),
            "deep_link": str(_first_value(item, ("deep_link", "deepLink", "url", "link"), "")),
            "conversation": str(_first_value(item, ("conversation", "conversation_id", "conversationId", "thread"), "")),
        }
    return {"summary": str(item), "owner": "", "date": "", "deep_link": "", "conversation": ""}


def _normalize_deep_link(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return {
            "title": str(_first_value(item, ("title", "name", "label"), "Teams conversation")),
            "url": str(_first_value(item, ("url", "deep_link", "deepLink", "link"), "")),
            "conversation_id": str(_first_value(item, ("conversation_id", "conversationId", "thread", "id"), "")),
        }
    return {"title": "Teams conversation", "url": str(item), "conversation_id": ""}


def _persist_timeline_confirmation(path: Path, timeline: Mapping[str, Any]) -> None:
    raw = _read_json(path, {})
    if not isinstance(raw, Mapping):
        raw = {}
    payload = dict(raw)
    payload["root_id"] = timeline.get("root_id", "china-release")
    payload["confirmed"] = True
    payload["owner_confirmed"] = True
    if timeline.get("target_date"):
        payload["target_date"] = timeline["target_date"]
    _write_json(path, payload)


def load_teams_timeline(
    path: Path = DEFAULT_TEAMS_TIMELINE,
    *,
    confirm: bool | None = None,
    target_date: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Load flexible info-teams output into a stable, previewable timeline schema."""
    requested = Path(path).expanduser()
    source = requested if requested.is_file() else DEFAULT_TEAMS_TIMELINE
    data = _read_json(source, {}) if source.is_file() else {}
    if not isinstance(data, Mapping):
        data = {}
    nested = data.get("teams_timeline") or data.get("timeline_context")
    if isinstance(nested, Mapping):
        data = {**data, **nested}
    milestone_raw = _first_value(data, ("milestones", "timeline", "nodes", "events", "stages"), [])
    milestones = [_normalize_timeline_item(item) for item in _timeline_items(milestone_raw)]
    context_raw = _first_value(data, ("key_context", "context", "highlights", "decisions", "notes"), [])
    contexts = [_normalize_context_item(item) for item in _timeline_items(context_raw)]
    links_raw = _first_value(data, ("deep_links", "links", "conversations", "sources"), [])
    links = [_normalize_deep_link(item) for item in _timeline_items(links_raw)]
    for item in milestones:
        if item["deep_link"] and not any(link["url"] == item["deep_link"] for link in links):
            links.append({"title": item["name"], "url": item["deep_link"], "conversation_id": ""})
    for item in contexts:
        if item["deep_link"] and not any(link["url"] == item["deep_link"] for link in links):
            links.append({"title": item["summary"][:80] or "Teams context", "url": item["deep_link"], "conversation_id": item["conversation"]})
    release_value = _first_value(data, ("release",), "")
    release_map = release_value if isinstance(release_value, Mapping) else {}
    release_date = _iso_date(_first_value(data, ("target_date", "targetDate", "release_date", "releaseDate"), ""))
    release_date = release_date or _iso_date(_first_value(release_map, ("target_date", "targetDate", "date", "release_date"), ""))
    if not release_date:
        release_date = next((item["date"] for item in milestones if "release" in item["name"].casefold() and item["date"]), "")
    version_value = _first_value(data, ("version", "release_version", "releaseVersion"), "")
    version_value = version_value or _first_value(release_map, ("version", "name", "release_version"), "") or (release_value if not isinstance(release_value, Mapping) else "")
    timeline = {
        "schema_version": 1,
        "root_id": str(_first_value(data, ("root_id", "rootId"), "china-release")),
        "version": str(version_value),
        "target_date": _iso_date(target_date) if target_date else release_date,
        "confirmed": _as_bool(_first_value(data, ("confirmed", "owner_confirmed", "ownerConfirmed"), False)),
        "milestones": milestones,
        "key_context": contexts,
        "deep_links": links,
        "source": str(source),
    }
    if confirm is True:
        timeline["confirmed"] = True
        if persist and requested.is_file():
            _persist_timeline_confirmation(requested, timeline)
    if timeline["confirmed"] and persist and requested.is_file() and target_date:
        _persist_timeline_confirmation(requested, timeline)
    if persist and not DEFAULT_TEAMS_TIMELINE.is_file() and (source.is_file() or requested == DEFAULT_TEAMS_TIMELINE):
        _write_json(DEFAULT_TEAMS_TIMELINE, timeline)
    return timeline


def _timeline_is_confirmed(timeline: Mapping[str, Any] | None, force: bool = False) -> bool:
    return bool(force or (isinstance(timeline, Mapping) and timeline.get("confirmed") is True))


def _timeline_milestone_summary(timeline: Mapping[str, Any]) -> str:
    parts = []
    for item in timeline.get("milestones") or []:
        if not isinstance(item, Mapping):
            continue
        label = str(item.get("name") or "Checkpoint")
        date = str(item.get("date") or "TBD")
        parts.append(f"{label} {date}")
    return " · ".join(parts) or "No milestones supplied"


def _timeline_context_summary(timeline: Mapping[str, Any]) -> str:
    contexts = [str(item.get("summary") or "").strip() for item in timeline.get("key_context") or [] if isinstance(item, Mapping)]
    context = next((item for item in contexts if item), "No key context supplied")
    links = [item for item in timeline.get("deep_links") or [] if isinstance(item, Mapping) and item.get("url")]
    link_text = " · ".join(_md_link(item.get("title") or "Teams", item.get("url")) for item in links[:3])
    return f"{context[:240]}" + (f" · {link_text}" if link_text else "")


def _timeline_description(timeline: Mapping[str, Any]) -> str:
    lines = [f"Release/version: {timeline.get('version') or '—'}", f"Milestones: {_timeline_milestone_summary(timeline)}", f"Context: {_timeline_context_summary(timeline)}"]
    return "\n".join(lines)


def load_roots(path: Path = DEFAULT_SEED) -> list[dict[str, Any]]:
    data = _read_json(path, {})
    roots = data.get("roots", data) if isinstance(data, Mapping) else data
    if not isinstance(roots, list):
        raise ValueError(f"roots seed must contain a roots list: {path}")
    return [dict(root) for root in roots if isinstance(root, Mapping) and root.get("root_id")]


def manifest_block(root: Mapping[str, Any]) -> str:
    manifest = {
        "root_id": root.get("root_id"),
        "name": root.get("name"),
        "repos": list(root.get("repos", [])),
        "branch_globs": list(root.get("branch_globs", [])),
        "handoff_files": list(root.get("handoff_files", [])),
        "bug_list_paths": list(root.get("bug_list_paths", root.get("handoff_files", []))),
        "integration_branches": list(root.get("integration_branches", [])),
        "keywords": list(root.get("keywords", [])),
    }
    return "<!-- ROOT-MANIFEST:v1 " + json.dumps(manifest, ensure_ascii=False, sort_keys=True) + " -->"


def _unescape_linear_json(raw: str) -> str:
    return re.sub(r"\\([\[\]*_~`])", r"\1", raw or "")


def parse_manifest(description: str) -> dict[str, Any] | None:
    match = MANIFEST_RE.search(description or "")
    if not match:
        return None
    try:
        value = json.loads(_unescape_linear_json(match.group(1)))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def parse_root_meta(description: str) -> dict[str, Any] | None:
    match = ROOT_META_RE.search(description or "")
    if not match:
        return None
    try:
        value = json.loads(_unescape_linear_json(match.group(1)))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None

__all__ = [name for name in globals() if not name.startswith("__")] + [
    "_HANDOFF_ROOT",
    "_read_json",
    "_read_json_from_text",
    "_write_json",
    "_text",
    "_slug",
    "_iso_date",
    "_first_value",
    "_timeline_items",
    "_as_bool",
    "_normalize_timeline_item",
    "_normalize_context_item",
    "_normalize_deep_link",
    "_persist_timeline_confirmation",
    "_timeline_is_confirmed",
    "_timeline_milestone_summary",
    "_timeline_context_summary",
    "_timeline_description",
    "_unescape_linear_json",
]
