#!/usr/bin/env python3
"""Project Root Tracker: offline-first collection, one-way Linear sync, and HTML war-room."""

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


SCRIPT_DIR = Path(__file__).resolve().parent
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
    Path.home() / ".agents/skills/personal-m2-assistant/references/m2-domain-map.json",
    Path.home() / "Development/user-skills/skills/personal-m2-assistant/references/m2-domain-map.json",
    Path.home() / "Development/worktrees/project-root-tracker-tk1/skills/personal-m2-assistant/references/m2-domain-map.json",
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


def parse_manifest(description: str) -> dict[str, Any] | None:
    match = MANIFEST_RE.search(description or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def parse_bug_summary(path: Path = DEFAULT_SUMMARY) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    ado_snap = (_read_json(DEFAULT_STATE_DIR / "ado-bugs-snapshot.json", {}) or {}).get("items") or {}
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = BUG_ROW_RE.match(line)
        if not match:
            continue
        row, bug_number, status, pr_cell, title, resolution = match.groups()
        pr_match = re.search(r"#(\d+)", pr_cell)
        bug_id = f"AB#{bug_number}"
        ado = ado_snap.get(bug_id) if isinstance(ado_snap, Mapping) else None
        ado = ado if isinstance(ado, Mapping) else {}
        raw_status = status.strip()
        ado_state = str(ado.get("ado_state") or "")
        ado_reason = str(ado.get("ado_reason") or "")
        ship_blocker = bool(ado.get("ship_blocker", False))
        owner_domain = "" if pr_match else pr_cell.strip()
        effective_status = raw_status
        if ado_state in {"Resolved", "Closed"} and raw_status == "BLOCKING":
            effective_status = f"RESOLVED ({ado_reason or ado_state})"
        elif raw_status == "BLOCKING" and not ship_blocker and ado:
            effective_status = f"EXTERNAL ({owner_domain or '外部依赖'})"
        rows.append(
            {
                "id": bug_id,
                "type": "bug",
                "summary_row": int(row),
                "status": effective_status,
                "raw_status": raw_status,
                "ado_state": ado_state,
                "ado_reason": ado_reason,
                "ship_blocker": ship_blocker,
                "assigned_to": str(ado.get("assigned_to") or ""),
                "owner_domain": owner_domain,
                "pr_number": int(pr_match.group(1)) if pr_match else None,
                "pr_closed": "closed" in pr_cell.casefold(),
                "title": title.strip(),
                "resolution": resolution.strip(),
                "source": str(path),
                "signal": "Bugs",
            }
        )
    return rows


def parse_devsync(path: Path = DEFAULT_DEVSYNC) -> list[dict[str, Any]]:
    manifest_path = path / "manifest.json" if path.is_dir() else path
    data = _read_json(manifest_path, {})
    entries = data.get("entries", []) if isinstance(data, Mapping) else []
    if not isinstance(entries, list):
        return []
    result = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        result.append(
            {
                "id": str(entry.get("bug_id") or entry.get("id") or ""),
                "type": "devsync",
                "status": entry.get("workflow_status") or entry.get("disposition"),
                "pr_number": entry.get("pr"),
                "title": entry.get("title") or entry.get("bug_id"),
                "branch": entry.get("branch"),
                "integration_commits": entry.get("integration_commits", []),
                "disposition": entry.get("disposition"),
                "source": str(manifest_path),
                "signal": "Bugs",
            }
        )
    return [item for item in result if item["id"]]


def discover_worktrees(path: Path = DEFAULT_WORKTREES) -> list[dict[str, Any]]:
    if not path.is_dir():
        return []
    result = []
    for worktree in sorted(path.glob("ab114*-tk1")):
        match = re.match(r"ab(\d+)-tk1$", worktree.name, re.IGNORECASE)
        if not match:
            continue
        status = ""
        head = ""
        try:
            status = subprocess.run(
                ["git", "-C", str(worktree), "status", "--short"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout.strip()
            head = subprocess.run(
                ["git", "-C", str(worktree), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        result.append(
            {
                "id": f"worktree:ab{match.group(1)}",
                "type": "worktree",
                "bug_id": f"AB#{match.group(1)}",
                "title": worktree.name,
                "path": str(worktree),
                "head": head,
                "dirty": bool(status),
                "status": "DIRTY" if status else "CLEAN",
                "source": str(path),
                "signal": "Bugs",
            }
        )
    return result


def _load_supervisor(path: Path) -> dict[str, Any]:
    data = _read_json(path, {})
    return data if isinstance(data, dict) else {}


def _pr_url(item: Mapping[str, Any]) -> str:
    url = item.get("pr_url") or item.get("url")
    if url:
        return str(url)
    host = item.get("hostname") or "msft.ghe.com"
    repo = item.get("repository") or item.get("repo") or "san/bing-growth-sapphire-app"
    number = item.get("pr_number")
    return f"https://{host}/{repo}/pull/{number}" if number is not None else ""


def _is_china_pr(pr: Mapping[str, Any], bugs: Sequence[Mapping[str, Any]]) -> bool:
    number = pr.get("pr_number")
    numbers = {bug.get("pr_number") for bug in bugs if bug.get("pr_number") is not None}
    if number in numbers:
        return True
    haystack = _text(pr).casefold()
    return "bing cn" in haystack or "bingchina" in haystack or "ab114" in haystack or "china" in haystack


def _pr_signal(
    pr: Mapping[str, Any],
    pending: Sequence[Mapping[str, Any]],
    *,
    open_keys: set[str] | None = None,
) -> tuple[str, str]:
    number = pr.get("pr_number") or pr.get("number")
    repo = str(pr.get("repository") or pr.get("repo") or "san/bing-growth-sapphire-app")
    key = str(pr.get("key") or (f"{repo}#{number}" if number else ""))
    state_str = str(pr.get("state") or "").upper()
    if state_str == "MERGED" or pr.get("merged") is True:
        return "MERGED", "PR is already merged."
    if open_keys and repo.endswith("bing-growth-sapphire-app") and key and key not in open_keys:
        return "MERGED", "PR is no longer open in live discovery (merged)."
    active_pending = [
        item for item in pending
        if (item.get("pr_number") or item.get("number")) == number
        and str(item.get("status") or "").casefold() == "awaiting_owner"
    ]
    if any(str(item.get("kind") or "") == "owner_action" for item in active_pending):
        return "WAIT_POP_OR_MERGE", "PoP/Merge gate is green and requires Owner action."
    if any(str(item.get("kind") or "") == "owner_decision" for item in active_pending):
        return "P0_BLOCKING_DECISION", "Owner decision is required before source or integration action."
    live_signals = [str(s).casefold() for s in (pr.get("signals") or []) if s]
    if any("s1" in s or "conflict" in s for s in live_signals):
        return "AUTO_HEAL_S1_CONFLICT", "S1 conflict is queued before later auto-heal stages."
    if any("s2" in s or "comment" in s for s in live_signals):
        return "AUTO_HEAL_S2_UNRESOLVED_COMMENTS", "S2 unresolved review comments are queued after conflict checks."
    if any("s3" in s or "fail" in s or "check" in s for s in live_signals):
        return "AUTO_HEAL_S3_CI_FAILURE", "S3 CI failure is queued after conflicts and review comments."
    if "signals" in pr and isinstance(pr.get("signals"), list) and not live_signals:
        return "OBSERVE", "Open PR is clean in live discovery."
    haystack = _text(pr).casefold()
    pending_text = " ".join(_text(item) for item in active_pending).casefold()
    combined = f"{haystack} {pending_text}"
    if any(token in combined for token in ("proof of presence", "pop / merge", "manual merge gate", "click merge")):
        return "WAIT_POP_OR_MERGE", "PoP/Merge gate is green and requires Owner action."
    if any(token in combined for token in ("by design", "remote config", "blocking decision", "awaiting_owner", "owner_decision")):
        return "P0_BLOCKING_DECISION", "Owner decision is required before source or integration action."
    if any(token in combined for token in ("conflict", "merge conflict", "conflicting")):
        return "AUTO_HEAL_S1_CONFLICT", "S1 conflict is queued before later auto-heal stages."
    if active_pending or any(token in combined for token in ("unresolved", "review thread", "review comment")):
        return "AUTO_HEAL_S2_UNRESOLVED_COMMENTS", "S2 unresolved review comments are queued after conflict checks."
    if any(token in combined for token in ("ci failure", "failing ci", "checks failed", "build failed")):
        return "AUTO_HEAL_S3_CI_FAILURE", "S3 CI failure is queued after conflicts and review comments."
    return "OBSERVE", "No actionable supervisor signal was found in the local snapshot."


def _load_topic_map() -> list[dict[str, Any]]:
    for path in DEFAULT_TOPIC_MAPS:
        data = _read_json(path, {})
        if isinstance(data, Mapping) and isinstance(data.get("groups"), list):
            return [
                dict(topic)
                for group in data["groups"] if isinstance(group, Mapping)
                for topic in group.get("topics", []) if isinstance(topic, Mapping) and topic.get("topic_id")
            ]
    return []


CANONICAL_TOPIC_BY_ID = {
    "POP580-0d776631": "sapphire-smartbanner",
    "EVAL-RULE-WARM-LAUNCH-SOURCE": "sapphire-smartbanner",
    "EVAL-RULE-SNAPSHOT-REF-PNG": "sapphire-notification",
    "EVAL-RULE-A11Y-TOGGLE-ROW": "sapphire-notification",
    "EVAL-RULE-FLAG-LIFETIME-CACHE": "sapphire-notification",
    "EVAL-RULE-L10N-BUNDLE-KEY": "sapphire-notification",
    "EVAL-RULE-PBXPROJ-TESTS": "sapphire-china-bugs",
    "EVAL-RULE-VIEW-SNAPSHOT-TEST": "sapphire-china-bugs",
    "EVAL-RULE-SWIFTYJSON-DOC-PATH": "sapphire-china-bugs",
}


def _topic_for_item(item: Mapping[str, Any]) -> str | None:
    explicit = str(item.get("topic_id") or item.get("topicId") or "").strip()
    item_id = str(item.get("id") or item.get("itemId") or item.get("decision_id") or "").strip()
    if item_id in CANONICAL_TOPIC_BY_ID:
        return CANONICAL_TOPIC_BY_ID[item_id]
    if item.get("type") in {"bug", "devsync", "worktree"} or str(item.get("id") or "").startswith("AB#") or str(item.get("bug_id") or "").startswith("AB#"):
        return "sapphire-china-bugs"
    topics = _load_topic_map()
    valid = {str(topic.get("topic_id")) for topic in topics}
    if explicit and (not valid or explicit in valid):
        return explicit
    haystack = _text(item).casefold()
    scores = []
    for topic in topics:
        terms = [topic.get("topic_id"), topic.get("title")]
        terms += list(topic.get("keywords") or []) + list(topic.get("aliases") or [])
        score = sum(1 for term in terms if str(term or "").casefold() in haystack)
        if score:
            scores.append((score, str(topic["topic_id"])))
    if scores:
        highest = max(score for score, _ in scores)
        winners = sorted(topic_id for score, topic_id in scores if score == highest)
        if len(winners) == 1:
            return winners[0]
    return None


def _merge_discovery(config: Mapping[str, Any], discovery: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(config)
    if not isinstance(discovery, Mapping):
        return merged
    watched = [dict(item) for item in (merged.get("watched_prs") or []) if isinstance(item, Mapping)]
    by_key = {str(item.get("key") or f"{item.get('repository', 'san/bing-growth-sapphire-app')}#{item.get('pr_number')}"): item for item in watched}
    open_keys: set[str] = set()
    overview_by_num: dict[str, dict[str, Any]] = {}
    for item in discovery.get("overview", []) or []:
        if not isinstance(item, Mapping):
            continue
        candidate = dict(item)
        num = candidate.get("pr_number") or candidate.get("number")
        if num is not None:
            overview_by_num[str(num)] = candidate
        key = str(candidate.get("key") or f"{candidate.get('repository', 'san/bing-growth-sapphire-app')}#{num}")
        if not key:
            continue
        open_keys.add(key)
        if key in by_key:
            by_key[key].update(candidate)
        else:
            watched.append(candidate)
            by_key[key] = candidate
    pending: dict[str, Any] = dict(merged.get("pending_decisions") or {})
    discovered_pending = discovery.get("awaiting_owner_decisions") or discovery.get("pending") or []
    if isinstance(discovered_pending, Mapping):
        discovered_pending = discovered_pending.values()
    for item in discovered_pending:
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("key") or item.get("decision_id") or item.get("id") or "")
        if key and key not in pending:
            pending[key] = dict(item)
    for k, raw_dec in list(pending.items()):
        if not isinstance(raw_dec, Mapping):
            continue
        dec = dict(raw_dec)
        pr_num = dec.get("pr_number") or dec.get("number")
        live_pr = overview_by_num.get(str(pr_num)) if pr_num is not None else None
        if live_pr is not None and str(dec.get("status") or "").casefold() in {"awaiting_owner", "owner_decided"}:
            state = str(live_pr.get("state") or live_pr.get("status") or "").upper()
            live_head = str(live_pr.get("headRefOid") or "")
            if state in {"MERGED", "CLOSED"}:
                dec["status"] = "completed"
                dec["reconciled_by"] = "pr_merged"
            elif (
                live_pr.get("unresolvedCount") == 0
                and not (live_pr.get("signals") or [])
                and live_head
                and live_head != str(dec.get("head_sha") or "")
            ):
                dec["status"] = "completed"
                dec["reconciled_by"] = f"cross_session_head:{live_head[:9]}"
        pending[str(k)] = dec
    merged["watched_prs"] = watched
    merged["pending_decisions"] = pending
    if open_keys:
        merged["_open_keys"] = sorted(open_keys)
    return merged


def collect_prs(config: Mapping[str, Any], bugs: Sequence[Mapping[str, Any]], discovery: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    config = _merge_discovery(config, discovery)
    open_keys = set(config.get("_open_keys") or [])
    pending = list(config.get("pending_decisions", {}).values()) if isinstance(config.get("pending_decisions"), Mapping) else []
    watched = config.get("watched_prs", []) if isinstance(config.get("watched_prs"), list) else []
    records: list[dict[str, Any]] = [dict(pr) for pr in watched if isinstance(pr, Mapping) and pr.get("enabled", True)]
    known_numbers = {pr.get("pr_number") or pr.get("number") for pr in records}
    for decision in pending:
        if not isinstance(decision, Mapping):
            continue
        number = decision.get("pr_number") or decision.get("number")
        if not number or number in known_numbers:
            continue
        key = str(decision.get("key") or "")
        repo = key.split("#", 1)[0] if "#" in key else "san/bing-growth-sapphire-app"
        is_completed = str(decision.get("status") or "").casefold() == "completed"
        if not is_completed and decision.get("status") != "awaiting_owner":
            continue
        records.append({
            "key": f"{repo}#{number}",
            "pr_number": number,
            "repository": repo,
            "repo": repo,
            "title": decision.get("pr_title") or f"PR #{number}",
            "pr_url": decision.get("pr_url") or decision.get("url"),
            "state": "MERGED" if is_completed else "OPEN",
            "enabled": True,
            "pending_only": True,
        })
        known_numbers.add(number)
    result = []
    for pr in records:
        if not pr.get("enabled", True):
            continue
        pr_number = pr.get("pr_number") or pr.get("number")
        signal, rationale = _pr_signal(pr, pending, open_keys=open_keys if open_keys else None)
        pending_for_pr = [item for item in pending if (item.get("pr_number") or item.get("number")) == pr_number]
        result.append(
            {
                "id": f"PR#{pr_number}",
                "type": "pr",
                "pr_number": pr_number,
                "title": pr.get("title") or f"PR #{pr.get('pr_number')}",
                "branch": pr.get("branch"),
                "repository": pr.get("repository") or pr.get("repo"),
                "worktree": pr.get("worktree"),
                "url": _pr_url(pr),
                "signal": signal,
                "classification": "AUTO_HEAL_S1_S2_S3" if signal.startswith("AUTO_HEAL_") else signal,
                "auto_heal_stage": signal if signal.startswith("AUTO_HEAL_") else None,
                "rationale": rationale,
                "state": pr.get("state"),
                "isDraft": bool(pr.get("isDraft", pr.get("is_draft", pr.get("draft", False)))),
                "head_sha": pr.get("head_sha") or pr.get("headRefOid"),
                "signals": list(pr.get("signals") or []),
                "match_text": _semantic_text(pr),
                "pending_decisions": [
                    {
                        "decision_id": item.get("decision_id"),
                        "pr_number": item.get("pr_number") or item.get("number") or pr_number,
                        "kind": item.get("kind"),
                        "linear_identifier": item.get("linear_decision_issue_identifier"),
                        "status": item.get("status"),
                        "question": item.get("question") or item.get("body"),
                        "url": item.get("linear_decision_issue_url"),
                        "match_text": _semantic_text(item),
                    }
                    for item in pending_for_pr
                ],
                "action_command": f"my_pr_supervisor --pr {pr_number} --signal {signal}",
                "source": str(DEFAULT_SUPERVISOR),
            }
        )
    link_companion_prs(
        result,
        pending=[decision for pr in result for decision in pr.get("pending_decisions", [])],
    )
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


def _canonical_eval_rules() -> list[dict[str, Any]]:
    script = Path.home() / "Development/job skills/my-pr-supervisor/scripts/my_pr_supervisor.py"
    try:
        spec = importlib.util.spec_from_file_location("project_root_tracker_supervisor", script)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return [dict(item) for item in getattr(module, "CANONICAL_EVAL_RULES", ()) if isinstance(item, Mapping)]
    except (OSError, ImportError, AttributeError, TypeError, ValueError):
        pass
    return []


def collect_eval_sinks(
    eval_path: Path = DEFAULT_EVAL_ACTIONS,
    config: Mapping[str, Any] | None = None,
    extra_eval_paths: Sequence[Path] = (DEFAULT_AIDASH_EVAL_ACTIONS,),
    canonical_rules: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for path in (eval_path, *extra_eval_paths):
        for item in _read_jsonl(Path(path)):
            item_id = str(item.get("itemId") or item.get("item_id") or item.get("id") or "")
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            items.append(item)
    for item in (canonical_rules if canonical_rules is not None else _canonical_eval_rules()):
        item_id = str(item.get("itemId") or item.get("item_id") or item.get("id") or "")
        if item_id and item_id in seen_ids:
            continue
        if item_id:
            seen_ids.add(item_id)
        items.append(dict(item))
    if not items and config:
        pending = config.get("pending_decisions", {})
        candidates = pending.values() if isinstance(pending, Mapping) else []
        for candidate in candidates:
            body = _text(candidate).casefold()
            if any(marker in body for marker in ("snapshot", "render", "visual", "scaledtofit", "test")):
                items.append(
                    {
                        "itemId": f"PR#{candidate.get('pr_number')}:offline",
                        "pr_number": candidate.get("pr_number"),
                        "title": candidate.get("pr_title") or f"PR #{candidate.get('pr_number')}",
                        "score": 0.96,
                        "worthExtracting": True,
                        "verdict": "HIGH_VALUE_EVAL_OR_TEST_GAP",
                        "rationale": "Offline deterministic fallback found a reusable rendering/test signal.",
                        "source": "my-pr-supervisor pending_decisions",
                    }
                )
    result = []
    for item in items:
        score = _score(item)
        if score < 0.70 or item.get("worthExtracting") is False:
            continue
        assessment = item.get("jevAssessment") if isinstance(item.get("jevAssessment"), Mapping) else {}
        source = item.get("source") if isinstance(item.get("source"), Mapping) else {}
        raw_options = item.get("landing_options") or item.get("landingOptions") or []
        option_ids = [option.get("optionId") for option in raw_options if isinstance(option, Mapping) and option.get("optionId")]
        landing_options = [option_id for option_id in LANDING_OPTIONS if option_id in set(option_ids) or not option_ids]
        if len(landing_options) != 3:
            landing_options = list(LANDING_OPTIONS)
        result.append(
            {
                "id": str(item.get("itemId") or item.get("item_id") or item.get("id") or f"eval:{item.get('pr_number', len(result))}"),
                "type": "eval_sink",
                "pr_number": item.get("pr_number") or source.get("pr_number") or _pr_number_from_text(source.get("entityId")),
                "title": item.get("title") or item.get("verdict") or "Reusable PR evaluation signal",
                "match_text": _semantic_text(item),
                "linear_identifier": item.get("linear_identifier") or item.get("linearIdentifier"),
                "score": score,
                "verdict": item.get("verdict") or assessment.get("verdict") or "EVAL_WORTHY",
                "rationale": item.get("rationale") or item.get("reason") or assessment.get("rationale") or "Score meets the extraction threshold.",
                "landing_options": landing_options,
                "commands": {
                    "record_feedback": f"pr_eval_extractor.py record-feedback --item-id {item.get('itemId') or item.get('item_id') or 'eval-item'} --action approve --option-id LAND_PR_AUTO_REVIEW_EVAL",
                    "dispatch": "team-lead dispatch --sink eval",
                },
                "url": item.get("url") or source.get("url") or (_item_pr_url({"pr_number": item.get("pr_number") or source.get("pr_number")})),
                "source": str(eval_path),
                "signal": "Skill/Repo Sink >= 0.70",
            }
        )
    return result


def _pr_number_from_text(value: Any) -> int | None:
    match = re.search(r"(?:PR[# ]|#)(\d+)", str(value or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


_MATCH_FIELDS = (
    "title", "pr_title", "summary", "description", "descriptionMd", "rationale",
    "question", "body", "path", "file", "files", "paths", "branch", "source",
)
_COMPANION_KEYWORDS = (
    "snapshot", "snapshots", "screenshot", "visual", "rendersnapshot",
    "validatesnapshots", "sasnackbar", "aspect ratio",
)
_COMPANION_PATH_RE = re.compile(
    r"[A-Za-z0-9_./+\\-]+\\.(?:swift|m|mm|h|md|py|json|jsonl|pbxproj)",
    re.IGNORECASE,
)
_COMPANION_WORK_ITEM_RE = re.compile(r"\\bAB#?\\s*(\\d{6,})\\b", re.IGNORECASE)


def _semantic_text(item: Mapping[str, Any]) -> str:
    values = [item.get(field) for field in _MATCH_FIELDS if field in item]
    source = item.get("source")
    if isinstance(source, Mapping):
        values.extend(source.get(field) for field in ("entityId", "label", "url"))
    return _text(values)


def _companion_tokens(item: Mapping[str, Any]) -> tuple[set[str], set[str], set[str]]:
    text = str(item.get("match_text") or _semantic_text(item)).casefold()
    work_items = {f"ab#{value}" for value in _COMPANION_WORK_ITEM_RE.findall(text)}
    paths = {Path(value).name.casefold() for value in _COMPANION_PATH_RE.findall(text)}
    keywords = {keyword for keyword in _COMPANION_KEYWORDS if keyword in text}
    return work_items, paths, keywords


def _companion_reason(left: Mapping[str, Any], right: Mapping[str, Any]) -> str | None:
    left_work, left_paths, left_keywords = _companion_tokens(left)
    right_work, right_paths, right_keywords = _companion_tokens(right)
    if left_work & right_work:
        return f"work_item:{sorted(left_work & right_work)[0]}"
    if left_paths & right_paths:
        return f"path:{sorted(left_paths & right_paths)[0]}"
    shared_keywords = left_keywords & right_keywords
    if shared_keywords:
        return f"keyword:{sorted(shared_keywords)[0]}"
    return None


def _companion_record(pr: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "pr_number": pr.get("pr_number") or pr.get("number"),
        "title": pr.get("title") or pr.get("pr_title"),
        "url": _item_pr_url(pr),
        "state": pr.get("state"),
        "linear_identifiers": [
            value for value in (
                pr.get("linear_identifier"),
                pr.get("linear_decision_issue_identifier"),
            ) if value
        ],
        "match_reason": reason,
    }


def link_companion_prs(
    prs: Sequence[Mapping[str, Any]],
    sinks: Sequence[Mapping[str, Any]] = (),
    pending: Sequence[Mapping[str, Any]] = (),
) -> None:
    """Link newly discovered PRs to decisions and eval sinks without PR constants."""
    candidates = [item for item in prs if isinstance(item, Mapping)]
    for source in [*sinks, *pending]:
        if not isinstance(source, dict):
            continue
        source_number = source.get("pr_number") or source.get("number")
        matches = []
        for candidate in candidates:
            candidate_number = candidate.get("pr_number") or candidate.get("number")
            if source_number and candidate_number and str(source_number) == str(candidate_number):
                continue
            reason = _companion_reason(source, candidate)
            if reason:
                matches.append(_companion_record(candidate, reason))
        if matches:
            source["companion_prs"] = matches


def _score(item: Mapping[str, Any]) -> float:
    for key in ("score", "noul", "confidence", "value"):
        value = item.get(key)
        if isinstance(value, Mapping):
            value = value.get("score", value.get("value"))
        if value is None and isinstance(item.get("jevAssessment"), Mapping):
            value = item["jevAssessment"].get(key)
        try:
            score = float(value)
            if score > 1 and score <= 100:
                score /= 100
            return max(0.0, min(1.0, score))
        except (TypeError, ValueError):
            continue
    if str(item.get("id") or "").startswith("EVAL-RULE-"):
        return 0.96
    return 0.0


def _match_value(value: str, patterns: Iterable[str]) -> bool:
    value = value.casefold()
    for pattern in patterns:
        pattern = str(pattern).casefold()
        if fnmatch.fnmatch(value, pattern) or pattern in value:
            return True
    return False


CHINA_RELEASE_CONTEXT = ("china", "bingcn", "bingchina", "ab114", "devsync", "34.4")


def _has_china_context(value: Any) -> bool:
    text = _text(value).casefold()
    normalized = re.sub(r"[^a-z0-9]+", "", text)
    return any(pattern in text or pattern in normalized for pattern in CHINA_RELEASE_CONTEXT)


def _root_match_score(item: Mapping[str, Any], root: Mapping[str, Any]) -> int:
    haystack = _text(item).casefold()
    normalized_haystack = re.sub(r"[^a-z0-9]+", "", haystack)
    root_id = str(root.get("root_id") or "").casefold()
    repository_match = _match_value(str(item.get("repository") or item.get("repo") or ""), root.get("repos", []))
    contextual_match = _has_china_context(item)
    if repository_match and root_id == "china-release" and not contextual_match:
        repository_match = False
    score = 1 if repository_match else 0
    if _match_value(str(item.get("branch") or ""), root.get("branch_globs", [])):
        score += 5
    if _match_value(str(item.get("integration_branch") or ""), root.get("integration_branches", [])):
        score += 5
    if _match_value(str(item.get("source") or ""), root.get("handoff_files", []) + root.get("bug_list_paths", [])):
        score += 4
    keywords = root.get("keywords", [])
    if root_id == "china-release":
        keywords = [keyword for keyword in keywords if _has_china_context(keyword)]
    score += sum(
        1
        for keyword in keywords
        if str(keyword).casefold() in haystack
        or re.sub(r"[^a-z0-9]+", "", str(keyword).casefold()) in normalized_haystack
    )
    return score


def deterministic_root_match(item: Mapping[str, Any], root: Mapping[str, Any]) -> bool:
    return _root_match_score(item, root) > 0


def _load_jev_function():
    candidates = [
        Path(__file__).resolve().parents[2] / "worktrees/project-root-tracker-tk1/skills/team-lead/scripts/jev_client.py",
        Path.home() / "Development/user-skills/skills/team-lead/scripts/jev_client.py",
        Path(__file__).resolve().parents[2] / "job skills/team-lead/scripts/jev_client.py",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("project_root_tracker_jev", path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                function = getattr(module, "jev_decide", None)
                if callable(function):
                    return function
        except (OSError, ImportError, AttributeError, TypeError):
            continue
    return None


def classify_jev(item: Mapping[str, Any], roots: Sequence[Mapping[str, Any]], jev_function=None) -> dict[str, Any]:
    topic_id = _topic_for_item(item)
    mapped_root_id = TOPIC_TO_ROOT.get(topic_id)
    if mapped_root_id:
        mapped_root = next((root for root in roots if root.get("root_id") == mapped_root_id), None)
        context_match = _has_china_context(item)
        is_china_signal = str(item.get("type") or "") in {"bug", "devsync", "worktree"} or context_match
        if mapped_root and (mapped_root_id != "china-release" or is_china_signal):
            return {"topic_id": topic_id, "root_id": mapped_root_id, "method": "m2-domain-map", "confidence": 1.0, "match_score": 100}
    deterministic = sorted(
        ((_root_match_score(item, root), root) for root in roots),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if deterministic and deterministic[0][0] > 0:
        score, root = deterministic[0]
        return {"topic_id": topic_id, "root_id": root["root_id"], "method": "deterministic", "confidence": 1.0, "match_score": score}
    function = jev_function or _load_jev_function()
    if function is not None:
        questions = {
            "root_id": {"type": "choice", "options": [root["root_id"] for root in roots] + ["NEW_ROOT_CANDIDATE"]},
            "confidence": {"type": "noul", "instructions": "Return a confidence score between 0 and 1."},
        }
        try:
            response = function(dict(item), questions, goal_id="project-root-tracker")
            if isinstance(response, Mapping):
                root_id = response.get("root_id") or response.get("classification")
                if isinstance(response.get("answers"), Mapping):
                    root_id = root_id or response["answers"].get("root_id")
                if root_id in {root["root_id"] for root in roots}:
                    return {"topic_id": topic_id, "root_id": root_id, "method": "jev", "confidence": _score(response) or 0.75}
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
    lowered = _text(item).casefold()
    scores = []
    for root in roots:
        keywords = root.get("keywords", [])
        if str(root.get("root_id") or "").casefold() == "china-release":
            keywords = [keyword for keyword in keywords if _has_china_context(keyword)]
        score = sum(1 for keyword in keywords if str(keyword).casefold() in lowered)
        score += sum(1 for branch in root.get("branch_globs", []) if str(branch).casefold().replace("*", "") in lowered)
        scores.append((score, root))
    scores.sort(key=lambda pair: pair[0], reverse=True)
    if scores and scores[0][0] > 0:
        return {"topic_id": topic_id, "root_id": scores[0][1]["root_id"], "method": "fallback-keyword", "confidence": min(0.69, 0.35 + scores[0][0] * 0.1)}
    return {"topic_id": topic_id, "root_id": "NEW_ROOT_CANDIDATE", "method": "fallback-keyword", "confidence": 0.0}


def _new_workstreams() -> dict[str, dict[str, Any]]:
    return {name: {"items": [], "count": 0} for name in WORKSTREAMS}


def collect(
    *,
    roots_path: Path = DEFAULT_SEED,
    summary_path: Path = DEFAULT_SUMMARY,
    devsync_path: Path = DEFAULT_DEVSYNC,
    worktrees_path: Path = DEFAULT_WORKTREES,
    supervisor_path: Path = DEFAULT_SUPERVISOR,
    eval_path: Path = DEFAULT_EVAL_ACTIONS,
    latest_discovery_path: Path = DEFAULT_LATEST_DISCOVERY,
    aidash_eval_path: Path = DEFAULT_AIDASH_EVAL_ACTIONS,
    canonical_rules: Sequence[Mapping[str, Any]] | None = None,
    teams_timeline_path: Path = DEFAULT_TEAMS_TIMELINE,
    confirm_teams_timeline: bool = False,
) -> dict[str, Any]:
    roots = load_roots(roots_path)
    teams_timeline = load_teams_timeline(teams_timeline_path, confirm=confirm_teams_timeline)
    bugs = parse_bug_summary(summary_path)
    devsync = parse_devsync(devsync_path)
    worktrees = discover_worktrees(worktrees_path)
    supervisor = _load_supervisor(supervisor_path)
    latest_discovery = _read_json(latest_discovery_path, {})
    if not isinstance(latest_discovery, Mapping):
        latest_discovery = {}
    prs = collect_prs(supervisor, bugs, latest_discovery)
    sinks = collect_eval_sinks(eval_path, supervisor, (aidash_eval_path,), canonical_rules=canonical_rules)
    link_companion_prs(prs, sinks)
    tree_roots = [
        {
            "root_id": root["root_id"],
            "name": root.get("name", root["root_id"]),
            "manifest": root,
            "manifest_block": manifest_block(root),
            "workstreams": _new_workstreams(),
            "summary": {},
            **({"teams_timeline": teams_timeline} if root["root_id"] == teams_timeline.get("root_id", "china-release") else {}),
        }
        for root in roots
    ]
    by_id = {root["root_id"]: root for root in tree_roots}
    unassigned: list[dict[str, Any]] = []

    def attach(item: dict[str, Any], stream: str) -> None:
        classification = classify_jev(item, roots)
        item = dict(item)
        item["root_classification"] = classification
        target = by_id.get(classification["root_id"])
        if target is None:
            unassigned.append(item)
            return
        existing = target["workstreams"][stream]["items"]
        identity = (str(item.get("type") or ""), str(item.get("id") or ""))
        if not any((str(value.get("type") or ""), str(value.get("id") or "")) == identity for value in existing):
            existing.append(item)

    for item in bugs:
        attach(item, "Bugs & Devsync")
    for item in devsync:
        attach(item, "Bugs & Devsync")
    for item in worktrees:
        attach(item, "Bugs & Devsync")
    for item in prs:
        attach(item, "PR & PoP")
        for decision in item.get("pending_decisions", []):
            if item.get("signal") == "P0_BLOCKING_DECISION":
                attach({
                    "id": decision.get("linear_identifier") or f"decision:{item.get('id')}",
                    "type": "decision",
                    "title": decision.get("question") or "Owner decision required",
                    "pr_number": item.get("pr_number"),
                    "repository": item.get("repository"),
                    "branch": item.get("branch"),
                    "url": decision.get("url") or item.get("url"),
                    "signal": "P0 Blocking Decision",
                    "source": str(supervisor_path),
                }, "PR & PoP")
    for item in sinks:
        attach(item, "Repo & Skill Sinks")

    for root in tree_roots:
        for stream in root["workstreams"].values():
            stream["count"] = len(stream["items"])
            stream["source_counts"] = dict(Counter(item.get("type", "unknown") for item in stream["items"]))
        root["summary"] = {name: value["count"] for name, value in root["workstreams"].items()}
        root["summary"]["source_counts"] = {
            name: stream["source_counts"] for name, stream in root["workstreams"].items()
        }
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "goal": "project-root-tracker",
        "spec_sha": "prt-spec-v1-20261113",
        "roots": tree_roots,
        "unassigned": unassigned,
        "signals": list(SIGNALS),
        "sources": {
            "summary": str(summary_path),
            "devsync": str(devsync_path),
            "worktrees": str(worktrees_path),
            "supervisor": str(supervisor_path),
            "latest_discovery": str(latest_discovery_path),
            "eval_actions": [str(eval_path), str(aidash_eval_path)],
        },
    }


def _load_linear_token() -> str:
    env_val = os.environ.get("LINEAR_API_KEY", "").strip()
    if env_val:
        return env_val
    for raw_path in (
        os.environ.get("GTT_LINEAR_CREDENTIALS_FILE", "~/.config/linear/credentials.json"),
        "~/.clip/linear/credentials.json",
    ):
        path = Path(raw_path).expanduser()
        try:
            if path.is_symlink():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, Mapping):
                val = str(data.get("headerValue") or data.get("key") or "").strip()
                if val:
                    return val
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    key_re = re.compile(r"^\s*(?:export\s+)?LINEAR_API_KEY\s*=\s*(['\"]?)([^'\"#\s]+)\1\s*$", re.MULTILINE)
    for rc in ("~/.zshenv", "~/.zshrc"):
        try:
            match = key_re.search(Path(rc).expanduser().read_text(encoding="utf-8"))
            if match:
                return match.group(2).strip()
        except OSError:
            continue
    return ""


def _linear_query(token: str, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        os.environ.get("LINEAR_API_URL", "https://api.linear.app/graphql"),
        data=body,
        headers={"Content-Type": "application/json", "Authorization": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise LinearSyncError(str(exc)) from exc
    if result.get("errors"):
        raise LinearSyncError(str(result["errors"]))
    return result.get("data", {})


def build_root_markdown(
    root: Mapping[str, Any],
    snapshot_root: Mapping[str, Any] | None = None,
    confirm_teams_timeline: bool = False,
) -> str:
    """Render the Root issue as a compact executive summary; details live in streams."""
    name = str(root.get("name") or root.get("root_id") or "Root")
    description = " ".join(str(root.get("description") or "").split())
    lines = [description] if description else []
    if isinstance(snapshot_root, Mapping):
        workstreams = snapshot_root.get("workstreams") or {}
        pr_items = [item for item in (workstreams.get("PR & PoP") or {}).get("items", []) if item.get("type") == "pr"]
        decision_items = [item for item in (workstreams.get("PR & PoP") or {}).get("items", []) if item.get("type") == "decision"]
        def signal_count(predicate):
            return sum(1 for item in pr_items if predicate(item))
        merged = signal_count(lambda item: str(item.get("signal") or "").upper() == "MERGED" or str(item.get("state") or "").upper() == "MERGED")
        pop = signal_count(lambda item: str(item.get("signal") or "") == "WAIT_POP_OR_MERGE")
        auto = signal_count(lambda item: "AUTO_HEAL" in str(item.get("classification") or item.get("signal") or "") or str(item.get("signal") or "").startswith("S"))
        waiting = signal_count(lambda item: str(item.get("signal") or "") == "OBSERVE")
        bugs = [item for item in (workstreams.get("Bugs & Devsync") or {}).get("items", []) if item.get("type") == "bug"]
        def bug_bucket(item):
            st = str(item.get("status") or "").casefold()
            if "remote config" in st:
                return "Remote Config"
            if "by design" in st or "not fixing" in st or "cannot reproduce" in st:
                return "By Design & Not Fixing"
            if "external" in st:
                return "External Non-Blocker"
            if "blocking" in st or "on hold" in st:
                return "Blocking"
            return "Fixed" if "fixed" in st or "resolved" in st else "Blocking"
        bug_counts = Counter(bug_bucket(item) for item in bugs)
        ship_blocker_open = [
            item for item in bugs
            if item.get("ship_blocker") and str(item.get("ado_state") or "") not in {"Resolved", "Closed"}
            and bug_bucket(item) in {"Blocking", "Remote Config"}
        ]
        sinks = len((workstreams.get("Repo & Skill Sinks") or {}).get("items", []))
        failing = [str(item.get("pr_number")) for item in pr_items if "S3" in str(item.get("signal") or "") or "CI_FAILURE" in str(item.get("classification") or "")]
        blockers = [f"PoP PR #{item.get('pr_number')}" for item in pr_items if item.get("signal") == "WAIT_POP_OR_MERGE"]
        blockers += [str(item.get("id") or "P0 decision") for item in decision_items]
        timeline = snapshot_root.get("teams_timeline") or root.get("teams_timeline")
        failing_links = ", ".join(_md_link(f"#{pr}", f"https://msft.ghe.com/san/bing-growth-sapphire-app/pull/{pr}") for pr in failing) if failing else "None"
        waiting_prs = [str(item.get("pr_number")) for item in pr_items if str(item.get("signal") or "") == "OBSERVE" and item.get("pr_number")]
        waiting_links = ", ".join(_md_link(f"#{pr}", f"https://msft.ghe.com/san/bing-growth-sapphire-app/pull/{pr}") for pr in waiting_prs[:6]) if waiting_prs else "None"
        has_tl = _timeline_is_confirmed(timeline, confirm_teams_timeline) and isinstance(timeline, Mapping)
        ver = str(timeline.get("version") or "CN App").strip() if has_tl else name
        tdate = str(timeline.get("target_date") or "TBD").strip() if has_tl else "TBD"
        int_branch = ", ".join(f"`{b}`" for b in (root.get("integration_branches") or [])[:1]) or "`main`"
        chk_pop_review = "x" if (pop == 0 and len(decision_items) == 0 and waiting == 0) else " "
        chk_bugs = "x" if (bug_counts["Blocking"] == 0 and len(ship_blocker_open) == 0) else " "
        chk_ci = "x" if len(failing) == 0 else " "
        sb_count = len(ship_blocker_open) or bug_counts["Blocking"]
        ext_count = bug_counts["External Non-Blocker"]
        lines += [
            "",
            f"> **版本**：`{ver}` · **🎯 Target Release**：**`{tdate}`** · **集成分支**：{int_branch}",
            "",
            "## 🧭 当前推进水位 & 下一步行动清单 (Where We Are & Next Todo)",
            "",
            f"- **📍 AI 当前水位判断**：当前处于 **`G2 代码封板与收口（进行中）`**（目标发版日 `{tdate}`）。客户端代码侧已合入 `{merged}` 个 PR、在途 `{waiting + len(failing)}` 个 PR；**非 PR 可修项共 `{sb_count + ext_count}` 条（其中 `{sb_count}` 条带 Ship Blocker 需推远端配置/服务端，`{ext_count}` 条缺外部资源/PM 信息或属 H5 团队）**。",
            "",
            "### 👤 下一步需你出手 / 推动 (Owner Next Actions)",
            f"- [{chk_pop_review}] **[PR 侧：催审 & PoP 合入]** 推动 Reviewer 审批评论已清零的 `{waiting}` 个在途 PR（{waiting_links}），Approve 后完成 PoP 合入（详见子单 `PR & PoP`）",
            f"- [{chk_bugs}] **[非 PR 侧：推进 5 条 Ship Blocker 配置/服务端 + 10 条缺信息/外部依赖]** ① 在 `ManageApps/start-china` 启用标签设置 (`11453356`)、AppStarter (`11440561`)、Money (`11440609`) 配置；② 确认 Evie 禁用 AI 搜索 (`11440530`)；③ 找 PM/设计补齐 `aka.ms` 新链接 (`11453157`)、FRE 透明贴图 (`11440359`) 及合规/H5 裁决（详见子单 `Bugs & Devsync`）",
        ]
        if has_tl:
            ms_list = [m for m in (timeline.get("milestones") or []) if isinstance(m, Mapping)]
            qa_ms = next((m for m in ms_list if "qa" in str(m.get("name") or "").casefold() and str(m.get("status") or "").casefold() != "completed"), None)
            rel_ms = next((m for m in reversed(ms_list) if str(m.get("status") or "").casefold() != "completed"), None)
            if qa_ms:
                qlink = f" ([Teams]({qa_ms.get('deep_link')}))" if qa_ms.get("deep_link") else ""
                lines.append(f"- [ ] **[QA 二轮验收跟进 ({qa_ms.get('date', '11-18')})]** {qa_ms.get('summary') or qa_ms.get('name')}{qlink}")
            if rel_ms and rel_ms is not qa_ms:
                rlink = f" ([Teams]({rel_ms.get('deep_link')}))" if rel_ms.get("deep_link") else ""
                lines.append(f"- [ ] **[上架提审准备 ({rel_ms.get('date', tdate)})]** {rel_ms.get('summary') or rel_ms.get('name')}{rlink}")
        lines += [
            "",
            "### 🤖 AI / 后台正在自动推进 (Agent Auto-Pilot)",
            f"- [{chk_ci}] **[CI 红灯自愈]** 自动修复/重跑 `{len(failing)}` 个 CI 失败的 PR（{failing_links}）",
            f"- [ ] **[集成分支与规则沉淀]** 随 PR 合入自动同步 {int_branch} 台账，并推进 `{sinks}` 项高价值规则沉淀（详见子单 `Repo & Skill Sinks`）",
        ]
        if has_tl:
            ms_list = [m for m in (timeline.get("milestones") or []) if isinstance(m, Mapping)]
            done_ms = [m for m in ms_list if str(m.get("status") or "").casefold() == "completed"]
            active_ms = [m for m in ms_list if str(m.get("status") or "").casefold() in {"in_progress", "active"}]
            planned_ms = [m for m in ms_list if str(m.get("status") or "").casefold() not in {"completed", "in_progress", "active"}]
            lines += [
                "",
                "## 🏁 发版闯关阶段 Checklist (G1–G4 Release Stage Gates)",
                "",
                f"### Gate 1：基线迁仓、证书与首轮提测（`{len(done_ms)}/{len(done_ms)} ✅ 已通关`）",
            ]
            for ms in done_ms:
                mdate = str(ms.get("date") or "")[5:] if len(str(ms.get("date") or "")) >= 10 else str(ms.get("date") or "")
                dlink = f" ([Teams]({ms.get('deep_link')}))" if ms.get("deep_link") else ""
                lines.append(f"- [x] `{mdate}` **{ms.get('name')}** — {ms.get('summary') or ''}{dlink}")
            lines.append("")
            lines.append("### Gate 2：代码封板与核心 Bug 收口（`🔄 当前主战场`）")
            for ms in active_ms:
                mdate = str(ms.get("date") or "")[5:] if len(str(ms.get("date") or "")) >= 10 else str(ms.get("date") or "")
                dlink = f" ([Teams]({ms.get('deep_link')}))" if ms.get("deep_link") else ""
                lines.append(f"- [ ] **`{mdate}` {ms.get('name')}** — {ms.get('summary') or ''}{dlink}")
            lines.append(f"- [{'x' if merged > 0 else ' '}] 已完成首批 `{merged}` 个修复 PR 合入与评论清零")
            lines.append(f"- [{chk_ci}] 清零剩余 `{len(failing)}` 个 CI 红灯 PR（{failing_links}）并合入 `{waiting}` 个待审 PR")
            lines.append(f"- [{chk_bugs}] 收口剩余 `{bug_counts['Blocking']}` 条 Blocking Bugs（当前已修 `{bug_counts['Fixed']}` · 远端配置 `{bug_counts['Remote Config']}` · 不修 `{bug_counts['By Design & Not Fixing']}`）")
            if planned_ms:
                lines.append("")
                lines.append(f"### Gate 3 & 4：QA 二轮回归与正式提审上线（`🎯 目标日 {tdate}`）")
                for ms in planned_ms:
                    mdate = str(ms.get("date") or "")[5:] if len(str(ms.get("date") or "")) >= 10 else str(ms.get("date") or "")
                    dlink = f" ([Teams]({ms.get('deep_link')}))" if ms.get("deep_link") else ""
                    lines.append(f"- [ ] `{mdate}` **{ms.get('name')}** — {ms.get('summary') or ''}{dlink}")
            ctx_items = [c for c in (timeline.get("key_context") or []) if isinstance(c, Mapping)]
            if ctx_items:
                lines += ["", "### 📌 Teams 关键决策备忘 (Key Context)"]
                for ctx in ctx_items[:4]:
                    cdate = str(ctx.get("date") or "")[5:] if len(str(ctx.get("date") or "")) >= 10 else str(ctx.get("date") or "")
                    cowner = str(ctx.get("owner") or "").split("/")[0].strip()
                    csum = str(ctx.get("summary") or ctx.get("text") or "").strip()
                    curl = ctx.get("deep_link") or ctx.get("url")
                    clink = f" ([Teams]({curl}))" if curl else ""
                    prefix = f"**`{cdate}` ({cowner})**" if cdate and cowner else f"**`{cdate or cowner}`**"
                    lines.append(f"- {prefix}：{csum}{clink}")
            top_links = [
                _md_link(d.get("title") or "Teams thread", d.get("url") or d.get("deep_link"))
                for d in (timeline.get("deep_links") or [])
                if isinstance(d, Mapping) and (d.get("url") or d.get("deep_link"))
            ]
            if top_links:
                lines.append(f"- **🔗 核心讨论深链**：{' · '.join(top_links[:4])}")
            lines.append("<!-- Release Timeline & Teams Context: Milestones / Context -->")
        lines += [
            "",
            "## 📊 Milestone Readiness Scoreboard",
            "",
            "| 维度 | 当前进度 | 当前阻塞 / 待办 |",
            "| --- | --- | --- |",
            f"| **🔀 PR Funnel** | PoP Ready `{pop}` · P0 Decisions `{len(decision_items)}` · Auto-Healing `{auto}` · Waiting Review `{waiting}` · Merged `{merged}` | Failing CI PRs: {failing_links} |",
            f"| **🐛 Bug Buckets** | Fixed `{bug_counts['Fixed']}` · Remote Config `{bug_counts['Remote Config']}` · By Design & Not Fixing `{bug_counts['By Design & Not Fixing']}` · Blocking `{bug_counts['Blocking']}` | Blocking bugs: **{bug_counts['Blocking']}** |",
            f"| **🧬 Skill Sinks (score ≥ 0.70)** | `{sinks}` high-value items | Active PoP/Decisions: {', '.join(blockers) if blockers else 'None'} |",
            "",
            "<!-- Current Milestone Blockers: Details: see the three sub-issues -->",
        ]
    lines.append(manifest_block(root))
    return "\n".join(line for line in lines if line is not None).strip()


def _workstream_title(root_name: str, stream: str) -> str:
    suffixes = {
        "PR & PoP": "PR 监督、PoP 待合入与 S1/S2/S3 自愈清单",
        "Bugs & Devsync": "非 PR 可修卡点、远端配置与缺失信息清单",
        "Repo & Skill Sinks": "评测提取 (≥0.70) 与规则沉淀候选 Link",
    }
    return f"[{root_name} · {stream}] {suffixes[stream]}"


def _md_link(label: Any, url: Any) -> str:
    text = str(label or "").replace("|", "\\|").replace("\n", " ")
    return f"[{text}]({url})" if url else f"`{text}`"


def _item_pr_url(item: Mapping[str, Any]) -> str:
    url = item.get("url") or item.get("pr_url")
    if url:
        return str(url)
    number = item.get("pr_number")
    return f"https://msft.ghe.com/san/bing-growth-sapphire-app/pull/{number}" if number else ""


def _workstream_markdown(root: Mapping[str, Any], stream: str, items: Sequence[Mapping[str, Any]]) -> str:
    rows = [dict(item) for item in items if isinstance(item, Mapping)]
    lines: list[str] = []
    if stream == "PR & PoP":
        pops = [
            item for item in rows
            if item.get("signal") != "MERGED"
            and (
                item.get("signal") == "WAIT_POP_OR_MERGE"
                or any(
                    entry.get("kind") == "owner_action"
                    and str(entry.get("status") or "").casefold() == "awaiting_owner"
                    for entry in item.get("pending_decisions", [])
                    if isinstance(entry, Mapping)
                )
            )
        ]
        decisions = [item for item in rows if item.get("type") == "decision"]
        if not decisions:
            decisions = [item for item in rows if item.get("signal") == "P0_BLOCKING_DECISION"]
        lines.extend([
            "<!-- PRT-T3-ACTIVE-CALLOUT:v1 -->",
            f"> Active PoP/Merge: **{len(pops)}** · P0 Decisions: **{len(decisions)}**",
            "",
            "## 1. 🟢 待你点 PoP / Merge（直接点 PR 号去 GHE 合入）",
            "",
        ])
        action_pr_nums: set[str] = set()
        for item in pops:
            pending = [entry for entry in item.get("pending_decisions", []) if isinstance(entry, Mapping) and entry.get("kind") == "owner_action"]
            label = (pending[0].get("linear_identifier") if pending else None) or item.get("id") or item.get("pr_number")
            url = pending[0].get("url") if pending else _item_pr_url(item)
            pr_num = str(item.get("pr_number") or "")
            if pr_num:
                action_pr_nums.add(pr_num)
            pr_link = _md_link(f"PR #{pr_num}", _item_pr_url(item)) if pr_num else _md_link(label, url)
            lin_link = _md_link(label, url) if url else f"`{label}`"
            title_short = str(item.get("title") or "").strip()
            lines.append(f"- {pr_link} ({lin_link}) — {title_short} (`{item.get('head_sha') or '—'}`)")
        if not pops:
            lines.append("- — No active PoP item")
        lines.extend(["", "## 2. 🚨 待你拍板 Blocking 决策（点 TIA 单号回复 `A` 或 `B`）", ""])
        for item in decisions:
            pending = [entry for entry in item.get("pending_decisions", []) if isinstance(entry, Mapping) and entry.get("kind") == "owner_decision"]
            label = (pending[0].get("linear_identifier") if pending else None) or item.get("id") or item.get("pr_number")
            url = pending[0].get("url") if pending else item.get("url")
            pr_num = str(item.get("pr_number") or "")
            if pr_num:
                action_pr_nums.add(pr_num)
            summary_text = str(item.get("title") or item.get("rationale") or "Owner decision required").replace("|", r"\|")
            pr_comment_link = _md_link(f"PR #{pr_num}", _item_pr_url(item)) if pr_num else ""
            prefix = f"{_md_link(label, url or _item_pr_url(item))} · {pr_comment_link}" if pr_comment_link else _md_link(label, url or _item_pr_url(item))
            lines.append(f"- **{prefix}** — {summary_text}")
        if not decisions:
            lines.append("- — No active decision")
        auto_heal_lines: list[str] = []
        waiting_review_lines: list[str] = []
        merged_links: list[str] = []
        seen_prs: set[str] = set(action_pr_nums)
        for item in rows:
            if item.get("type") not in {"pr", "decision"}:
                continue
            pr_num = str(item.get("pr_number") or item.get("id") or "")
            if not pr_num or pr_num in seen_prs:
                continue
            seen_prs.add(pr_num)
            sig = str(item.get("signal") or item.get("classification") or "OBSERVE")
            link = _md_link(f"PR #{pr_num}", _item_pr_url(item))
            title_short = str(item.get("title") or "").strip()
            draft_badge = " `DRAFT`" if item.get("isDraft") else ""
            reconciled_decs = [
                d.get("linear_identifier")
                for d in (item.get("pending_decisions") or [])
                if isinstance(d, Mapping) and d.get("linear_identifier")
            ]
            dec_badge = f" *(已修完 Resolve: {', '.join(reconciled_decs)})*" if reconciled_decs else ""
            if sig == "MERGED" or str(item.get("state") or "").upper() == "MERGED":
                merged_links.append(_md_link(f"#{pr_num}", _item_pr_url(item)))
            elif "AUTO_HEAL" in sig or sig.startswith("S"):
                auto_heal_lines.append(f"  - {link} — {title_short}{draft_badge} (`{sig}`)")
            else:
                waiting_review_lines.append(f"  - {link} — {title_short}{draft_badge}{dec_badge} (`等 Review Approve / CI`)")
        lines.extend([
            "",
            "## 3. 🤖 在途 Open PR 监督（无需你操作，Approve 后自动进第 1 栏 PoP）",
            "",
            f"- **🔧 自动修评论/冲突/CI 中 ({len(auto_heal_lines)})**：",
            *(auto_heal_lines or ["  - —"]),
            f"- **⏳ 评论已清零，等 Reviewer Approve / CI ({len(waiting_review_lines)})**：",
            *(waiting_review_lines or ["  - —"]),
            "",
            f"## 4. ✅ 已合入 Merged ({len(merged_links)})",
            "",
            f"- {' · '.join(merged_links) if merged_links else '—'}",
        ])
    elif stream == "Bugs & Devsync":
        bug_rows = [item for item in rows if item.get("type") == "bug"]
        if not bug_rows:
            bug_rows = [item for item in rows if str(item.get("id") or "").startswith("AB#")]
        seen_bug_ids: set[str] = set()
        unique_bugs: list[dict[str, Any]] = []
        for item in bug_rows:
            bid = str(item.get("id") or "")
            if bid and bid in seen_bug_ids:
                continue
            if bid:
                seen_bug_ids.add(bid)
            unique_bugs.append(item)

        def _fmt_non_pr_row(b: Mapping[str, Any]) -> str:
            bid = str(b.get("id") or "")
            num = bid.removeprefix("AB#")
            link = _md_link(bid, f"https://msasg.visualstudio.com/SAN%20SA/_workitems/edit/{num}") if bid.startswith("AB#") else f"`{bid}`"
            sb = " 🚨`Ship Blocker`" if b.get("ship_blocker") else ""
            owner_dom = str(b.get("owner_domain") or b.get("raw_status") or "外部/配置").replace("`", "").replace("|", r"\|")
            title_txt = str(b.get("title") or "").replace("|", r"\|")
            res_txt = str(b.get("resolution") or "—").replace("|", r"\|")
            return f"| {link}{sb} | `{owner_dom}` | {title_txt} | {res_txt} |"

        c1_remote_config = []
        c2_missing_info_or_policy = []
        c3_external_web_h5 = []
        pr_tracked_count = 0
        ado_closed_count = 0

        for b in unique_bugs:
            ado_st = str(b.get("ado_state") or "")
            st = str(b.get("status") or "")
            raw_st = str(b.get("raw_status") or st)
            dom = str(b.get("owner_domain") or "")
            res = str(b.get("resolution") or "")
            if ado_st in {"Resolved", "Closed"} or st.startswith("RESOLVED"):
                ado_closed_count += 1
            elif b.get("pr_number") and not b.get("pr_closed") and "ON HOLD" not in raw_st:
                pr_tracked_count += 1
            elif "REMOTE CONFIG" in raw_st or "配置" in dom or "AASA" in dom:
                c1_remote_config.append(b)
            elif any(k in f"{dom} {res} {raw_st}" for k in ("短链", "Lottie", "贴图", "架构特性缺失", "合规策略", "ON HOLD")):
                c2_missing_info_or_policy.append(b)
            else:
                c3_external_web_h5.append(b)

        non_pr_total = len(c1_remote_config) + len(c2_missing_info_or_policy) + len(c3_external_web_h5)
        lines.extend([
            f"> **本单仅聚焦「非客户端 PR 可直接修复 / 缺少外部信息或配置」的 `{non_pr_total}` 条卡点**（其余已由 PR 追踪的 `{pr_tracked_count}` 条见子单 `PR & PoP`，已在 ADO 关单的 `{ado_closed_count}` 条不再重复列出）。",
            "",
            f"## 1. ⚙️ 需改远端配置 / 服务端开关 (`start-china` / `ManageApps` / `AASA`，共 {len(c1_remote_config)} 条)",
            "",
            "| Bug ID | 卡点类型 | 现象 | 为什么非 PR 可修 & 下一步怎么推 |",
            "| --- | --- | --- | --- |",
            *(_fmt_non_pr_row(b) for b in c1_remote_config),
            *(["| — | — | — | None |"] if not c1_remote_config else []),
            "",
            f"## 2. ❓ 缺少外部信息 / 资产 / 需 PM・合规拍板 (共 {len(c2_missing_info_or_policy)} 条)",
            "",
            "| Bug ID | 缺失项 / 状态 | 现象 | 缺什么信息 & 下一步找谁 |",
            "| --- | --- | --- | --- |",
            *(_fmt_non_pr_row(b) for b in c2_missing_info_or_policy),
            *(["| — | — | — | None |"] if not c2_missing_info_or_policy else []),
            "",
            f"## 3. 🌐 纯外部网页 / 远端 H5 团队职责 (非客户端代码，共 {len(c3_external_web_h5)} 条)",
            "",
            "| Bug ID | 外部责任方 | 现象 | 根因证据 & 责任团队 |",
            "| --- | --- | --- | --- |",
            *(_fmt_non_pr_row(b) for b in c3_external_web_h5),
            *(["| — | — | — | None |"] if not c3_external_web_h5 else []),
        ])
    else:
        lines.extend(["## Eval Sinks (score ≥ 0.70)", "", "| Sink | Score | PR | Landing options |", "| --- | ---: | --- | --- |"])
        for item in rows:
            score = item.get("score")
            score_text = f"{float(score):.2f}" if isinstance(score, (int, float)) else "—"
            sink_id = str(item.get("id") or "sink")
            companions = item.get("companion_prs") or []
            companion_links = []
            for companion in companions:
                number = companion.get("pr_number") or "?"
                label = f"PR #{number}"
                identifiers = companion.get("linear_identifiers") or []
                if identifiers:
                    label += f" ({', '.join(str(value) for value in identifiers)})"
                companion_links.append(_md_link(label, companion.get("url")))
            if companion_links:
                merged = all(str(companion.get("state") or "").upper() == "MERGED" for companion in companions)
                marker = "✅ **跨会话落地**" if merged else "🔗 **跨会话关联**"
                landed_note = f" {marker}: " + " · ".join(companion_links)
            else:
                landed_note = ""
            lines.append(f"| `{sink_id}` | `{score_text}` | {_md_link(item.get('pr_number') or '—', _item_pr_url(item) if item.get('pr_number') else '')} | {', '.join(f'`{option}`' for option in item.get('landing_options') or LANDING_OPTIONS)}{landed_note} |")
        if not rows:
            lines.append("| — | — | — | No current eval sink |")
    return "\n".join(lines).strip()


def sync_linear(
    roots: Sequence[Mapping[str, Any]],
    *,
    cache_path: Path = DEFAULT_CACHE,
    dry_run: bool = False,
    offline: bool = False,
    client=None,
    child_items: Sequence[Mapping[str, Any]] = (),
    snapshot_roots: Mapping[str, Mapping[str, Any]] | None = None,
    teams_timeline_path: Path = DEFAULT_TEAMS_TIMELINE,
    confirm_teams_timeline: bool = False,
) -> dict[str, Any]:
    """Upsert Linear roots and exactly three workstream children; never write sources."""
    desired = []
    fallback_timeline = load_teams_timeline(teams_timeline_path, confirm=confirm_teams_timeline)
    for root in roots:
        snap_root = (snapshot_roots or {}).get(str(root["root_id"])) or {}
        if str(root.get("root_id")) == str(fallback_timeline.get("root_id", "china-release")) and not snap_root.get("teams_timeline"):
            snap_root = {**snap_root, "teams_timeline": fallback_timeline}
        streams = snap_root.get("workstreams") if isinstance(snap_root, Mapping) else {}
        streams = streams if isinstance(streams, Mapping) else {}
        desired.append({
            "root_id": root["root_id"],
            "name": root.get("name", root["root_id"]),
            "label": "root-tracker",
            "team": "TIA",
            "teams_timeline": snap_root.get("teams_timeline"),
            "description": build_root_markdown(root, snap_root),
            "workstreams": [
                {
                    "stream": stream,
                    "title": _workstream_title(str(root.get("name", root["root_id"])), stream),
                    "description": _workstream_markdown(root, stream, (streams.get(stream) or {}).get("items") or []),
                    "items": [dict(item) for item in (streams.get(stream) or {}).get("items", []) if isinstance(item, Mapping)],
                }
                for stream in WORKSTREAMS
            ],
        })
    if dry_run or offline or client is None:
        result = {"mode": "dry-run" if dry_run else "offline", "writes": [], "roots": desired}
        if not dry_run:
            _write_json(cache_path, {"schema_version": 1, "updated_at": utc_now(), "source": "offline", "roots": desired})
        return result
    if getattr(client, "boundary", None) != "linear":
        raise OneWayBoundaryError("Project Root Tracker may write only to Linear")
    for record in desired:
        parent = client.upsert_root(record)
        record["linear"] = parent
        timeline = record.get("teams_timeline")
        if str(record.get("root_id")) == "china-release" and _timeline_is_confirmed(timeline) and hasattr(client, "upsert_project_milestone"):
            record["project_milestone"] = client.upsert_project_milestone("china-release", str(record.get("name") or "China Release"), timeline)
        for workstream in record["workstreams"]:
            workstream["linear"] = client.upsert_workstream(parent, record, workstream)
        if hasattr(client, "enforce_tia83"):
            client.enforce_tia83(roots)
        if hasattr(client, "ensure_related"):
            for workstream in record["workstreams"]:
                if workstream["stream"] == "PR & PoP":
                    client.ensure_related(workstream["linear"], record["root_id"])
    _write_json(cache_path, {"schema_version": 1, "updated_at": utc_now(), "source": "linear", "roots": desired})
    return {"mode": "linear", "writes": len(desired), "roots": desired}


class LinearClient:
    boundary = "linear"
    PROJECT_BY_ROOT = {
        "china-release": "Work Business",
        "sapphire-notification": "Work Business",
        "sapphire-smartbanner": "Work Business",
    }
    ROOT_LABEL_ID = "04dc1e1a-6803-4590-acd3-4e54b25bb4ae"
    IN_PROGRESS_ID = "d7d9992e-4736-4af0-b5ab-dce211b48bc8"

    def __init__(self, token: str, team_key: str = "TIA") -> None:
        self.token = token
        self.team_key = team_key
        self.team_id: str | None = None
        self.projects: dict[str, dict[str, Any]] | None = None

    def _mutate(self, mutation: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        return _linear_query(self.token, mutation, variables)

    def _team(self) -> str:
        if self.team_id:
            return self.team_id
        data = _linear_query(self.token, "query($key:String!){team(id:$key){id}}", {"key": self.team_key})
        team = data.get("team") or {}
        if not team.get("id"):
            raise LinearSyncError(f"Linear team not found: {self.team_key}")
        self.team_id = team["id"]
        return self.team_id

    def _projects(self) -> dict[str, dict[str, Any]]:
        if self.projects is not None:
            return self.projects
        query = "query($team:String!){team(id:$team){projects{nodes{id name}}}}"
        nodes = _linear_query(self.token, query, {"team": self.team_key}).get("team", {}).get("projects", {}).get("nodes", [])
        self.projects = {str(node.get("name")): node for node in nodes if node.get("id") and node.get("name")}
        return self.projects

    def _project_and_milestone(self, root_id: str, root_name: str) -> tuple[str | None, str | None]:
        project_name = self.PROJECT_BY_ROOT.get(root_id, "Work Business")
        project = self._projects().get(project_name)
        if not project:
            return None, None
        query = "query($id:String!){project(id:$id){projectMilestones{nodes{id name}}}}"
        nodes = _linear_query(self.token, query, {"id": project["id"]}).get("project", {}).get("projectMilestones", {}).get("nodes", [])
        milestone = next((node for node in nodes if str(node.get("name", "")).casefold() == root_name.casefold()), None)
        return str(project["id"]), str(milestone["id"]) if milestone else None

    def upsert_project_milestone(self, root_id: str, root_name: str, timeline: Mapping[str, Any]) -> dict[str, Any]:
        """Update only the confirmed Teams timeline fields on the matching milestone."""
        _, milestone_id = self._project_and_milestone(root_id, root_name)
        if not milestone_id:
            raise LinearSyncError(f"Linear milestone not found: {root_name}")
        input_data = {"description": _timeline_description(timeline)}
        if timeline.get("target_date"):
            input_data["targetDate"] = str(timeline["target_date"])
        mutation = """mutation($id:String!,$input:ProjectMilestoneUpdateInput!){projectMilestoneUpdate(id:$id,input:$input){success projectMilestone{id name targetDate description}}}"""
        result = self._mutate(mutation, {"id": milestone_id, "input": input_data})
        return (result.get("projectMilestoneUpdate") or {}).get("projectMilestone") or result.get("projectMilestoneUpdate", {})

    def _find_issue(self, title: str, parent_id: str | None = None) -> dict[str, Any] | None:
        query = """query($teamId:ID!,$title:String!){issues(filter:{team:{id:{eq:$teamId}},title:{eq:$title}},first:50){nodes{id identifier url description parent{ id }}}}"""
        try:
            nodes = _linear_query(self.token, query, {"teamId": self._team(), "title": title}).get("issues", {}).get("nodes", [])
        except LinearSyncError:
            return None
        for node in nodes:
            if parent_id is None or (node.get("parent") or {}).get("id") == parent_id:
                return node
        return None

    @staticmethod
    def _preserve_manual_checks(old_desc: str, new_desc: str) -> str:
        checked_prefixes = set()
        for line in (old_desc or "").splitlines():
            m = re.match(r"^\s*[-*]\s+\[[xX]\]\s+(\*\*\[[^\]]+\]\*\*|\*\*`[^`]+`[^*]*\*\*|`[^`]+`\s+\*\*[^*]+\*\*)", line)
            if m:
                checked_prefixes.add(m.group(1))
        if not checked_prefixes:
            return new_desc
        out_lines = []
        for line in new_desc.splitlines():
            if line.lstrip().startswith("- [ ] "):
                for prefix in checked_prefixes:
                    if prefix in line:
                        line = line.replace("- [ ] ", "- [x] ", 1)
                        break
            out_lines.append(line)
        return "\n".join(out_lines)

    def upsert_root(self, record: Mapping[str, Any]) -> dict[str, Any]:
        title = f"[Root] {record['name']}"
        project_id, milestone_id = self._project_and_milestone(str(record.get("root_id", "")), str(record["name"]))
        existing = self._find_issue(title)
        desc = str(record["description"])
        if existing and existing.get("description"):
            desc = self._preserve_manual_checks(str(existing["description"]), desc)
        input_data = {
            "description": desc,
            "labelIds": [self.ROOT_LABEL_ID],
            "stateId": self.IN_PROGRESS_ID,
            "priority": 2,
        }
        if project_id:
            input_data["projectId"] = project_id
        if milestone_id:
            input_data["projectMilestoneId"] = milestone_id
        if existing and existing.get("id"):
            query = """mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id identifier url}}}"""
            result = self._mutate(query, {"id": existing["id"], "input": input_data})
            return (result.get("issueUpdate") or {}).get("issue") or result.get("issueUpdate", {})
        create_input = {"teamId": self._team(), "title": title, **input_data}
        query = """mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id identifier url}}}"""
        result = self._mutate(query, {"input": create_input})
        return (result.get("issueCreate") or {}).get("issue") or result.get("issueCreate", {})

    def _completed_linear_ids_from_supervisor(self) -> set[str]:
        sup = _merge_discovery(_read_json(DEFAULT_SUPERVISOR, {}), _read_json(DEFAULT_LATEST_DISCOVERY, {}))
        pending = sup.get("pending_decisions") if isinstance(sup, Mapping) else {}
        if not isinstance(pending, Mapping):
            return set()
        return {
            str(item.get("linear_decision_issue_identifier"))
            for item in pending.values()
            if isinstance(item, Mapping)
            and str(item.get("status") or "").casefold() == "completed"
            and item.get("linear_decision_issue_identifier")
        }

    def active_tia83_for_root(self, root_id: str, roots: Sequence[Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
        roots = list(roots or load_roots())
        completed_ids = self._completed_linear_ids_from_supervisor()
        parent = self._tia83()
        active = []
        for child in ((parent.get("children") or {}).get("nodes") or []):
            title = str(child.get("title") or "")
            if not (title.startswith("[PR Action]") or title.startswith("[PR Decision]")):
                continue
            if str((child.get("state") or {}).get("type") or "").casefold() == "completed":
                continue
            if str(child.get("identifier") or "") in completed_ids:
                continue
            if classify_jev(child, roots).get("root_id") == root_id:
                active.append(dict(child))
        return active

    def upsert_workstream(self, parent: Mapping[str, Any], record: Mapping[str, Any], workstream: Mapping[str, Any]) -> dict[str, Any]:
        title = str(workstream["title"])
        project_id, milestone_id = self._project_and_milestone(str(record.get("root_id", "")), str(record.get("name", "")))
        description = str(workstream.get("description") or "")
        if workstream.get("stream") == "PR & PoP":
            live_children = self.active_tia83_for_root(str(record.get("root_id", "")))
            pop_children = [
                c for c in live_children
                if str(c.get("title") or "").startswith("[PR Action]")
                and str((c.get("state") or {}).get("name") or "").casefold() not in {"done", "canceled", "duplicate"}
            ]
            dec_children = [
                c for c in live_children
                if str(c.get("title") or "").startswith("[PR Decision]")
                and str((c.get("state") or {}).get("name") or "").casefold() not in {"done", "canceled", "duplicate"}
            ]
            description = re.sub(
                r"> Active PoP/Merge: \*\*\d+\*\* · P0 Decisions: \*\*\d+\*\*",
                f"> Active PoP/Merge: **{len(pop_children)}** · P0 Decisions: **{len(dec_children)}**",
                description,
                count=1,
            )
            if pop_children:
                pop_lines = []
                for child in pop_children:
                    identifier = child.get("identifier") or child.get("id")
                    lin_url = f"https://linear.app/tianpeili/issue/{identifier}" if identifier else ""
                    title_text = str(child.get("title") or "")
                    pr_match = re.search(r"#(\d+)", title_text)
                    pr_num = pr_match.group(1) if pr_match else ""
                    pr_url = f"https://msft.ghe.com/san/bing-growth-sapphire-app/pull/{pr_num}" if pr_num else ""
                    sha_match = re.search(r"\(([0-9a-f]{7,40})\)", title_text)
                    sha = sha_match.group(1) if sha_match else "—"
                    pr_part = _md_link(f"PR #{pr_num}", pr_url) if pr_num else f"`{title_text}`"
                    pop_lines.append(f"- **{pr_part}** · {_md_link(identifier, lin_url)} — Complete PoP / Merge (`{sha}`)")
                description = description.replace("- — No active PoP item", "\n".join(pop_lines), 1)
        input_data: dict[str, Any] = {
            "description": description,
            "parentId": parent.get("id"),
        }
        if project_id:
            input_data["projectId"] = project_id
        if milestone_id:
            input_data["projectMilestoneId"] = milestone_id
        existing = self._find_issue(title, parent.get("id"))
        if existing and existing.get("id"):
            query = """mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id identifier url}}}"""
            result = self._mutate(query, {"id": existing["id"], "input": input_data})
            return (result.get("issueUpdate") or {}).get("issue") or result.get("issueUpdate", {})
        create_input = {"teamId": self._team(), "title": title, **input_data}
        query = """mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id identifier url}}}"""
        result = self._mutate(query, {"input": create_input})
        return (result.get("issueCreate") or {}).get("issue") or result.get("issueCreate", {})

    def _tia83(self) -> dict[str, Any]:
        query = """query($id:String!){issue(id:$id){id children(first:100){nodes{id identifier title description labelIds labels{nodes{id name}} state{id type name} priority project{id name} projectMilestone{id name}}} team{id states{nodes{id type name}} labels(first:100){nodes{id name}} projects(first:50){nodes{id name projectMilestones(first:50){nodes{id name}}}}}}}"""
        return _linear_query(self.token, query, {"id": "TIA-83"}).get("issue") or {}

    def enforce_tia83(self, roots: Sequence[Mapping[str, Any]]) -> list[str]:
        parent = self._tia83()
        completed_ids = self._completed_linear_ids_from_supervisor()
        team = parent.get("team") or {}
        states = ((team.get("states") or {}).get("nodes") or [])
        labels = ((team.get("labels") or {}).get("nodes") or [])
        todo = next((state for state in states if str(state.get("name") or "").casefold() == "todo"), None)
        todo_id = (todo or {}).get("id") or "1a23b243-a6a0-4973-bf05-e6b39c13f883"
        done = next((state for state in states if str(state.get("name") or "").casefold() == "done"), None)
        done_id = (done or {}).get("id") or "4ec80720-d769-4133-925e-02dbf1845567"
        needs = next((label for label in labels if str(label.get("name") or "").casefold() == "needs decision"), None)
        needs_id = (needs or {}).get("id") or "f8b8718c-1500-4c0d-b5cc-bd9a97d9a6d5"
        projects = ((team.get("projects") or {}).get("nodes") or [])
        work_project = next((project for project in projects if str(project.get("name") or "").casefold() == "work business"), None)
        changed: list[str] = []
        for child in ((parent.get("children") or {}).get("nodes") or []):
            title = str(child.get("title") or "")
            if not (title.startswith("[PR Action]") or title.startswith("[PR Decision]") or "awaiting_owner" in str(child.get("description") or "")):
                continue
            classification = classify_jev(child, roots)
            root_id = classification.get("root_id")
            root = next((value for value in roots if value.get("root_id") == root_id), None)
            if root is None:
                continue
            root_name = str(root.get("name") or root_id)
            project = work_project or child.get("project") or {}
            milestones = ((project.get("projectMilestones") or {}).get("nodes") or [])
            milestone = next((value for value in milestones if str(value.get("name") or "").casefold() == root_name.casefold()), None)
            if milestone is None and root_id == "china-release":
                milestone = next((value for value in milestones if str(value.get("name") or "").casefold() == "sapphire china bugs"), None)
            updates: dict[str, Any] = {}
            is_completed = str(child.get("identifier") or "") in completed_ids or str((child.get("state") or {}).get("type") or "").casefold() == "completed"
            target_state_id = done_id if is_completed else todo_id
            if (child.get("state") or {}).get("id") != target_state_id:
                updates["stateId"] = target_state_id
            if not is_completed and child.get("priority") != 1:
                updates["priority"] = 1
            existing_ids = set(child.get("labelIds") or [])
            existing_names = {str(label.get("name") or "").casefold() for label in ((child.get("labels") or {}).get("nodes") or [])}
            if not is_completed and needs_id not in existing_ids and "needs decision" not in existing_names:
                updates["labelIds"] = sorted(existing_ids | {needs_id})
            project_id = project.get("id")
            if project_id and (child.get("project") or {}).get("id") != project_id:
                updates["projectId"] = project_id
            milestone_id = (milestone or {}).get("id")
            if milestone_id and (child.get("projectMilestone") or {}).get("id") != milestone_id:
                updates["projectMilestoneId"] = milestone_id
            if not updates:
                continue
            mutation = """mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id identifier}}}"""
            self._mutate(mutation, {"id": child["id"], "input": updates})
            changed.append(str(child.get("identifier") or child.get("id")))
        return changed

    def ensure_related(self, workstream: Mapping[str, Any], root_id: str) -> list[str]:
        workstream_id = workstream.get("id")
        if not workstream_id:
            return []
        parent = self._tia83()
        roots = load_roots()
        related: list[str] = []
        for child in ((parent.get("children") or {}).get("nodes") or []):
            title = str(child.get("title") or "")
            if not (title.startswith("[PR Action]") or title.startswith("[PR Decision]")):
                continue
            classification = classify_jev(child, roots)
            if classification.get("root_id") != root_id or (child.get("state") or {}).get("type") == "completed":
                continue
            mutation = """mutation($input:IssueRelationCreateInput!){issueRelationCreate(input:$input){success issueRelation{id}}}"""
            try:
                self._mutate(mutation, {"input": {"issueId": workstream_id, "relatedIssueId": child.get("id"), "type": "related"}})
            except LinearSyncError:
                continue
            related.append(str(child.get("identifier") or child.get("id")))
        return related

    def upsert_child(self, parent: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
        title = item.get("title", item.get("id", "tracking item"))
        existing = self._find_issue(str(title), parent.get("id"))
        if existing and existing.get("id"):
            query = """mutation($id:ID!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id identifier url}}}"""
            result = self._mutate(query, {"id": existing["id"], "input": {"description": json.dumps(dict(item), ensure_ascii=False)}})
            return result.get("issueUpdate", {})
        query = """mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id identifier url}}}"""
        result = self._mutate(query, {"input": {"teamId": self._team(), "title": title, "description": json.dumps(dict(item), ensure_ascii=False), "parentId": parent.get("id")}})
        return result.get("issueCreate", {})


def render_html(snapshot: Mapping[str, Any], output: Path = DEFAULT_DASHBOARD) -> Path:
    data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    root_cards = []
    timeline_cards = []
    for root in snapshot.get("roots", []):
        total = sum(value for value in root.get("summary", {}).values() if isinstance(value, int))
        root_cards.append(f'<button class="root-card" data-root="{_escape(str(root.get("root_id")))}"><strong>{_escape(str(root.get("name")))}</strong><span>{total} signals</span></button>')
        timeline = root.get("teams_timeline") or {}
        if timeline:
            links = " · ".join(f'<a target="_blank" rel="noreferrer" href="{_escape(str(link.get("url")))}">{_escape(str(link.get("title") or "Teams"))}</a>' for link in timeline.get("deep_links", []) if isinstance(link, Mapping) and link.get("url"))
            timeline_cards.append(f'<article class="column"><h2>Release Timeline &amp; Teams Context</h2><p><strong>{_escape(str(timeline.get("version") or "China Release"))}</strong> · target { _escape(str(timeline.get("target_date") or "TBD")) }</p><p>{_escape(_timeline_milestone_summary(timeline))}</p><p>{_escape(_timeline_context_summary(timeline))}</p><p>{links or "No Teams deep links"}</p><span class="badge">{"Confirmed" if timeline.get("confirmed") else "Preview only"}</span></article>')
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Project Root Tracker</title>
<style>
:root{{--bg:#f4f7fb;--card:#fff;--ink:#172033;--muted:#60708a;--accent:#0b67d1;--danger:#b42318;--line:#dbe3ef}}[data-theme=dark]{{--bg:#111827;--card:#1f2937;--ink:#eef2ff;--muted:#a8b3c7;--accent:#80b8ff;--danger:#ff8b82;--line:#374151}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif}}main{{max-width:1500px;margin:auto;padding:24px}}header{{display:flex;justify-content:space-between;align-items:center;gap:12px}}h1{{margin:.2em 0}}button,.pill{{border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);padding:10px 12px;cursor:pointer}}.root-picker,.filters{{display:flex;gap:10px;overflow:auto;padding:12px 0}}.root-card{{min-width:190px;text-align:left;display:grid;gap:5px}}.root-card.active{{outline:2px solid var(--accent)}}.root-card span,.muted{{color:var(--muted)}}.filters button.active{{background:var(--accent);color:white}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}}.column{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;min-height:180px}}.column h2{{font-size:15px;margin-top:0}}.item{{border-top:1px solid var(--line);padding:11px 0;cursor:pointer}}.item:first-of-type{{border-top:0}}.badge{{font-size:11px;border-radius:20px;padding:3px 7px;background:#e6eefb;color:#124c93;display:inline-block;margin:2px 3px 2px 0}}.danger{{color:var(--danger)}}#details{{margin-top:18px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;min-height:80px}}a{{color:var(--accent)}}pre{{white-space:pre-wrap;background:var(--bg);padding:10px;border-radius:8px}}@media(max-width:1000px){{.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:650px){{main{{padding:14px}}.grid{{grid-template-columns:1fr}}}}
</style></head><body><main><header><div><div class="muted">Project Root Tracker · offline-first</div><h1>China Release War-Room</h1></div><button id="theme">Toggle theme</button></header>
<section class="root-picker">{''.join(root_cards)}</section><section class="grid" aria-label="Release Timeline">{''.join(timeline_cards)}</section><section class="filters"><button data-filter="all" class="active">All signals</button>{''.join(f'<button data-filter="{_escape(signal)}">{_escape(signal)}</button>' for signal in SIGNALS)}</section>
<section id="tree" class="grid"></section><section id="details"><span class="muted">Select a signal to inspect details, jump to its PR/Linear issue, or copy its action command.</span></section><section class="column" style="margin-top:18px"><h2>Operator command palette</h2><p class="muted">Safe, owner-mediated commands: <code>record-feedback</code> · <code>my_pr_supervisor</code> · <code>devsync revert</code></p></section></main>
<script>const SNAPSHOT={data};const state={{root:SNAPSHOT.roots[0]?.root_id||'',filter:'all'}};const tree=document.querySelector('#tree'),details=document.querySelector('#details');
function esc(v){{return String(v??'').replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[c]))}}
function matches(item){{return state.filter==='all'||item.signal===state.filter||(state.filter==='Auto-Healing S1/S2/S3'&&item.classification==='AUTO_HEAL_S1_S2_S3')||(state.filter==='Bugs'&&item.type==='bug')||(state.filter==='P0 Blocking Decision'&&item.signal==='P0 Blocking Decision')}}
function render(){{const root=SNAPSHOT.roots.find(r=>r.root_id===state.root)||SNAPSHOT.roots[0];state.root=root?.root_id||'';document.querySelectorAll('.root-card').forEach(b=>b.classList.toggle('active',b.dataset.root===state.root));tree.innerHTML='';for(const name of {json.dumps(WORKSTREAMS)}){{const stream=root?.workstreams?.[name]||{{items:[]}};const col=document.createElement('article');col.className='column';col.innerHTML=`<h2>${{esc(name)}} <span class="muted">(${{stream.items.length}})</span></h2>`;for(const item of stream.items.filter(matches)){{const el=document.createElement('div');el.className='item';el.innerHTML=`<strong>${{esc(item.title||item.id)}}</strong><div>${{esc(item.signal||item.status||'')}} </div><span class="muted">${{esc(item.rationale||item.resolution||item.path||'')}}</span>`;el.onclick=()=>show(item);col.appendChild(el)}}tree.appendChild(col)}}}}
function show(item){{details.innerHTML=`<h2>${{esc(item.title||item.id)}}</h2><p>${{esc(item.rationale||item.resolution||'')}}</p><p>${{item.url?`<a target="_blank" rel="noreferrer" href="${{esc(item.url)}}">Open PR / Linear issue</a>`:''}} ${{item.score!=null?`<span class="badge">score ${{Number(item.score).toFixed(2)}}</span>`:''}}</p><pre>${{esc(item.action_command||item.commands?.record_feedback||item.path||'No action command')}}</pre><button id="copy">Copy action command</button>`;document.querySelector('#copy')?.addEventListener('click',()=>navigator.clipboard?.writeText(item.action_command||item.commands?.record_feedback||''))}}
document.querySelectorAll('.root-card').forEach(b=>b.onclick=()=>{{state.root=b.dataset.root;render()}});document.querySelectorAll('.filters button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('.filters button').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.filter=b.dataset.filter;render()}});document.querySelector('#theme').onclick=()=>document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'':'dark';render();</script></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _timeline_preview(timeline: Mapping[str, Any], root: Mapping[str, Any] | None = None, snapshot_root: Mapping[str, Any] | None = None) -> dict[str, Any]:
    root = dict(root or {"root_id": "china-release", "name": "China Release"})
    snapshot_root = dict(snapshot_root or {"teams_timeline": timeline, "workstreams": _new_workstreams()})
    proposed = build_root_markdown(root, {**snapshot_root, "teams_timeline": {**timeline, "confirmed": True}})
    return {
        "confirmed": bool(timeline.get("confirmed")),
        "version": timeline.get("version"),
        "milestones": timeline.get("milestones", []),
        "targetDate": timeline.get("target_date") or None,
        "description": _timeline_description(timeline),
        "root_l0_preview": proposed,
        "deep_links": timeline.get("deep_links", []),
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)

    def add_timeline_args(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--teams-timeline", type=Path, default=DEFAULT_TEAMS_TIMELINE)
        command_parser.add_argument("--confirm-teams-timeline", action="store_true")

    collect_parser = subs.add_parser("collect")
    collect_parser.add_argument("--roots", type=Path, default=DEFAULT_SEED)
    collect_parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    collect_parser.add_argument("--devsync", type=Path, default=DEFAULT_DEVSYNC)
    collect_parser.add_argument("--worktrees", type=Path, default=DEFAULT_WORKTREES)
    collect_parser.add_argument("--supervisor", type=Path, default=DEFAULT_SUPERVISOR)
    collect_parser.add_argument("--eval-actions", type=Path, default=DEFAULT_EVAL_ACTIONS)
    collect_parser.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT)
    collect_parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    add_timeline_args(collect_parser)
    for name in ("sync-linear", "sync-roots"):
        sync = subs.add_parser(name)
        add_timeline_args(sync)
        sync.add_argument("--roots", type=Path, default=DEFAULT_SEED)
        sync.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
        sync.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
        sync.add_argument("--dry-run", action="store_true")
        sync.add_argument("--offline", action="store_true")
        sync.add_argument("--include-children", action="store_true")
        sync.add_argument("--token")
    classify = subs.add_parser("classify-jev")
    classify.add_argument("--item-json", required=True)
    classify.add_argument("--roots", type=Path, default=DEFAULT_SEED)
    render = subs.add_parser("render-html")
    render.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    render.add_argument("--output", type=Path, default=DEFAULT_DASHBOARD)
    refresh = subs.add_parser("refresh")
    refresh.add_argument("--roots", type=Path, default=DEFAULT_SEED)
    refresh.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    refresh.add_argument("--devsync", type=Path, default=DEFAULT_DEVSYNC)
    refresh.add_argument("--worktrees", type=Path, default=DEFAULT_WORKTREES)
    refresh.add_argument("--supervisor", type=Path, default=DEFAULT_SUPERVISOR)
    refresh.add_argument("--eval-actions", type=Path, default=DEFAULT_EVAL_ACTIONS)
    refresh.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    refresh.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    refresh.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    refresh.add_argument("--pull-remote", action="store_true")
    refresh.add_argument("--offline", action="store_true")
    refresh.add_argument("--dry-run", action="store_true")
    refresh.add_argument("--token")
    add_timeline_args(refresh)
    timeline_sync = subs.add_parser("sync-teams-timeline")
    timeline_sync.add_argument("--teams-timeline", type=Path, default=DEFAULT_TEAMS_TIMELINE)
    timeline_sync.add_argument("--preview", action="store_true")
    timeline_sync.add_argument("--confirm", action="store_true")
    timeline_sync.add_argument("--target-date")
    timeline_sync.add_argument("--dry-run", action="store_true")
    timeline_sync.add_argument("--offline", action="store_true")
    timeline_sync.add_argument("--token")
    timeline_sync.add_argument("--roots", type=Path, default=DEFAULT_SEED)
    timeline_sync.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    timeline_sync.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()
    if args.command == "refresh":
        if args.pull_remote and not args.offline and not args.dry_run:
            sup_script = Path.home() / "Development/job skills/my-pr-supervisor/scripts/my_pr_supervisor.py"
            if sup_script.is_file():
                subprocess.run([sys.executable, str(sup_script), "sync-linear-decisions", "--json"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run([sys.executable, str(sup_script), "discover", "--json"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        snapshot = collect(roots_path=args.roots, summary_path=args.summary, devsync_path=args.devsync, worktrees_path=args.worktrees, supervisor_path=args.supervisor, eval_path=args.eval_actions, teams_timeline_path=args.teams_timeline, confirm_teams_timeline=args.confirm_teams_timeline)
        _write_json(args.snapshot, snapshot)
        render_html(snapshot, args.dashboard)
        roots = load_roots(args.roots)
        snapshot_roots = {str(r.get("root_id")): r for r in snapshot.get("roots", []) if isinstance(r, Mapping)}
        token = args.token or _load_linear_token()
        client = LinearClient(token) if token and not args.offline and not args.dry_run else None
        result = sync_linear(roots, cache_path=args.cache, dry_run=args.dry_run, offline=args.offline or not token, client=client, snapshot_roots=snapshot_roots, teams_timeline_path=args.teams_timeline, confirm_teams_timeline=args.confirm_teams_timeline)
        print(json.dumps({"refreshed": True, "snapshot": str(args.snapshot), "dashboard": str(args.dashboard), "linear_mode": result.get("mode"), "roots_synced": len(result.get("roots", []))}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "collect":
        snapshot = collect(roots_path=args.roots, summary_path=args.summary, devsync_path=args.devsync, worktrees_path=args.worktrees, supervisor_path=args.supervisor, eval_path=args.eval_actions, teams_timeline_path=args.teams_timeline, confirm_teams_timeline=args.confirm_teams_timeline)
        _write_json(args.output, snapshot)
        render_html(snapshot, args.dashboard)
        return 0
    if args.command in {"sync-linear", "sync-roots"}:
        roots = load_roots(args.roots)
        timeline = load_teams_timeline(args.teams_timeline, confirm=args.confirm_teams_timeline)
        snapshot = _read_json(args.snapshot, {})
        snapshot_roots = {}
        child_items = []
        for snapshot_root in snapshot.get("roots", []) if isinstance(snapshot, Mapping) else []:
            if not isinstance(snapshot_root, Mapping):
                continue
            snapshot_roots[str(snapshot_root.get("root_id"))] = snapshot_root
            if args.include_children:
                for stream in (snapshot_root.get("workstreams") or {}).values():
                    for item in (stream.get("items") or []):
                        if isinstance(item, Mapping):
                            child = dict(item)
                            child["root_id"] = snapshot_root.get("root_id")
                            child_items.append(child)
        if "china-release" not in snapshot_roots:
            snapshot_roots["china-release"] = {"root_id": "china-release", "teams_timeline": timeline, "workstreams": _new_workstreams()}
        else:
            snapshot_roots["china-release"].setdefault("teams_timeline", timeline)
        token = args.token or _load_linear_token()
        client = LinearClient(token) if token and not args.offline and not args.dry_run else None
        result = sync_linear(roots, cache_path=args.cache, dry_run=args.dry_run, offline=args.offline or not token, client=client, child_items=child_items, snapshot_roots=snapshot_roots, teams_timeline_path=args.teams_timeline, confirm_teams_timeline=args.confirm_teams_timeline)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "sync-teams-timeline":
        timeline = load_teams_timeline(args.teams_timeline, confirm=args.confirm, target_date=args.target_date, persist=not args.dry_run)
        roots = load_roots(args.roots)
        root = next((item for item in roots if item.get("root_id") == timeline.get("root_id")), {"root_id": "china-release", "name": "China Release"})
        snapshot = _read_json(args.snapshot, {})
        snapshot_root = next((item for item in snapshot.get("roots", []) if isinstance(item, Mapping) and item.get("root_id") == timeline.get("root_id")), None) if isinstance(snapshot, Mapping) else None
        if args.preview or not timeline.get("confirmed") or args.dry_run or args.offline:
            print(json.dumps(_timeline_preview(timeline, root, snapshot_root), ensure_ascii=False, indent=2))
            if args.preview or not timeline.get("confirmed") or args.dry_run or args.offline:
                return 0
        token = args.token or _load_linear_token()
        client = LinearClient(token) if token else None
        snapshot_roots = {str(item.get("root_id")): item for item in snapshot.get("roots", []) if isinstance(item, Mapping)} if isinstance(snapshot, Mapping) else {}
        snapshot_roots[str(timeline.get("root_id"))] = {**(snapshot_roots.get(str(timeline.get("root_id"))) or {}), "teams_timeline": timeline}
        result = sync_linear(roots, cache_path=args.cache, client=client, offline=not token, snapshot_roots=snapshot_roots, teams_timeline_path=args.teams_timeline)
        print(json.dumps({"synced": True, "timeline": timeline, "linear_mode": result.get("mode"), "writes": result.get("writes", 0)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "classify-jev":
        item = _read_json(Path(args.item_json), {}) if Path(args.item_json).is_file() else json.loads(args.item_json)
        print(json.dumps(classify_jev(item, load_roots(args.roots)), ensure_ascii=False, indent=2))
        return 0
    if args.command == "render-html":
        snapshot = _read_json(args.snapshot, {})
        render_html(snapshot, args.output)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
