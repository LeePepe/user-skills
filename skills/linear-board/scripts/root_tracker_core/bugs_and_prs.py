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

from .shared import *

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


NON_REQUIRED_CHECK_PREFIXES = (
    "[backend][voicesearch] gh-code-ci",
)


def _has_only_non_required_check_failures(pr: Mapping[str, Any]) -> bool:
    if pr.get("non_required_ci_only") is True:
        return True
    rollup = pr.get("statusCheckRollup")
    if isinstance(rollup, Mapping):
        rollup = rollup.get("contexts", rollup.get("nodes", []))
        if isinstance(rollup, Mapping):
            rollup = rollup.get("nodes", [])
    if not isinstance(rollup, list) or not rollup:
        return False
    failed_conclusions = {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "CANCELLED"}
    relevant_failures = 0
    for check in rollup:
        if not isinstance(check, Mapping):
            continue
        if check.get("required", check.get("isRequired", True)) is False:
            continue
        name = str(check.get("name") or check.get("context") or "").strip().casefold()
        if any(name.startswith(prefix) for prefix in NON_REQUIRED_CHECK_PREFIXES):
            continue
        conclusion = str(check.get("conclusion") or "").upper()
        state = str(check.get("state") or "").upper()
        if conclusion in failed_conclusions or state in {"FAILURE", "ERROR"}:
            relevant_failures += 1
    return relevant_failures == 0


def _pr_signal(
    pr: Mapping[str, Any],
    pending: Sequence[Mapping[str, Any]],
    *,
    open_keys: set[str] | None = None,
    closed_pr_numbers: set[int] | None = None,
    on_hold_pr_numbers: set[int] | None = None,
) -> tuple[str, str]:
    number = pr.get("pr_number") or pr.get("number")
    num_int: int | None = None
    try:
        if number is not None:
            num_int = int(number)
    except (TypeError, ValueError):
        num_int = None
    repo = str(pr.get("repository") or pr.get("repo") or "san/bing-growth-sapphire-app")
    key = str(pr.get("key") or (f"{repo}#{number}" if number else ""))
    state_str = str(pr.get("state") or "").upper()
    merged_at = pr.get("mergedAt") or pr.get("merged_at")
    has_merged_at = "mergedAt" in pr or "merged_at" in pr
    if num_int is not None and closed_pr_numbers and num_int in closed_pr_numbers:
        return "CLOSED", "PR was closed without merging."
    if state_str == "CLOSED" and not (pr.get("merged") is True or bool(merged_at)):
        return "CLOSED", "PR was closed without merging."
    if has_merged_at and not merged_at and state_str not in {"OPEN", ""}:
        return "CLOSED", "PR has no mergedAt timestamp."
    if state_str == "MERGED" or pr.get("merged") is True or bool(merged_at):
        return "MERGED", "PR is already merged."
    if open_keys and repo.endswith("bing-growth-sapphire-app") and key and key not in open_keys:
        return "MERGED", "PR is no longer open in live discovery (merged)."
    if (
        (num_int is not None and on_hold_pr_numbers and num_int in on_hold_pr_numbers)
        or pr.get("on_hold") is True
        or "on hold" in str(pr.get("status") or "").casefold()
    ):
        return "ON_HOLD", "PR is explicitly on hold pending owner/PM decision on release exemption or unblocking."
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
    if _has_only_non_required_check_failures(pr):
        live_signals = [s for s in live_signals if "s3" not in s and "fail" not in s and "check" not in s]
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
    closed_pr_numbers = {
        int(bug["pr_number"])
        for bug in bugs
        if isinstance(bug, Mapping) and bug.get("pr_number") is not None and bug.get("pr_closed")
    }
    on_hold_pr_numbers = {
        int(bug["pr_number"])
        for bug in bugs
        if isinstance(bug, Mapping)
        and bug.get("pr_number") is not None
        and "ON HOLD" in str(bug.get("raw_status") or bug.get("status") or "").upper()
    }
    result = []
    for pr in records:
        if not pr.get("enabled", True):
            continue
        pr_number = pr.get("pr_number") or pr.get("number")
        signal, rationale = _pr_signal(
            pr,
            pending,
            open_keys=open_keys if open_keys else None,
            closed_pr_numbers=closed_pr_numbers,
            on_hold_pr_numbers=on_hold_pr_numbers,
        )
        if signal == "CLOSED":
            continue
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
                "unresolved_count": pr.get("unresolved_count", pr.get("unresolvedCount", 0)),
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
