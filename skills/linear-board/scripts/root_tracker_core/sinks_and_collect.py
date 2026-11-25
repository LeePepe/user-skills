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


def _canonical_repo(value: Any) -> str:
    repo = str(value or "").casefold().strip().rstrip("/")
    if repo.startswith("san/"):
        repo = repo[4:]
    return repo


def _shared_repositories(roots: Sequence[Mapping[str, Any]]) -> set[str]:
    counts: Counter[str] = Counter()
    for root in roots:
        for repo in root.get("repos", []):
            counts[_canonical_repo(repo)] += 1
    return {repo for repo, count in counts.items() if count > 1}


def _root_match_score(
    item: Mapping[str, Any],
    root: Mapping[str, Any],
    shared_repositories: set[str] | None = None,
) -> int:
    haystack = _text(item).casefold()
    normalized_haystack = re.sub(r"[^a-z0-9]+", "", haystack)
    root_id = str(root.get("root_id") or "").casefold()
    repository = str(item.get("repository") or item.get("repo") or "")
    repository_match = _match_value(repository, root.get("repos", []))
    contextual_match = _has_china_context(item)
    if repository_match and root_id == "china-release" and not contextual_match:
        repository_match = False
    context_score = 0
    if _match_value(str(item.get("branch") or ""), root.get("branch_globs", [])):
        context_score += 5
    if _match_value(str(item.get("integration_branch") or ""), root.get("integration_branches", [])):
        context_score += 5
    if _match_value(str(item.get("source") or ""), root.get("handoff_files", []) + root.get("bug_list_paths", [])):
        context_score += 4
    keywords = root.get("keywords", [])
    if root_id == "china-release":
        keywords = [keyword for keyword in keywords if _has_china_context(keyword)]
    context_score += sum(
        1
        for keyword in keywords
        if str(keyword).casefold() in haystack
        or re.sub(r"[^a-z0-9]+", "", str(keyword).casefold()) in normalized_haystack
    )
    if repository_match and shared_repositories and _canonical_repo(repository) in shared_repositories and context_score == 0:
        return 0
    return (1 if repository_match else 0) + context_score


def deterministic_root_match(
    item: Mapping[str, Any],
    root: Mapping[str, Any],
    shared_repositories: set[str] | None = None,
) -> bool:
    return _root_match_score(item, root, shared_repositories) > 0


def _load_jev_function():
    candidates = [
        Path.home() / "Development/user-skills/skills/team-lead/scripts/jev_client.py",
        SCRIPT_DIR.parents[1] / "job skills/team-lead/scripts/jev_client.py",
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
    shared_repositories = _shared_repositories(roots)
    topic_id = _topic_for_item(item)
    mapped_root_id = TOPIC_TO_ROOT.get(topic_id)
    if mapped_root_id:
        mapped_root = next((root for root in roots if root.get("root_id") == mapped_root_id), None)
        context_match = _has_china_context(item)
        item_id = str(item.get("id") or item.get("itemId") or item.get("decision_id") or "").strip()
        is_china_signal = (
            str(item.get("type") or "") in {"bug", "devsync", "worktree"}
            or item_id in CANONICAL_TOPIC_BY_ID
            or context_match
        )
        mapped_score = _root_match_score(item, mapped_root, shared_repositories) if mapped_root else 0
        if mapped_root and (item_id in CANONICAL_TOPIC_BY_ID or mapped_score > 0) and (mapped_root_id != "china-release" or is_china_signal):
            return {"topic_id": topic_id, "root_id": mapped_root_id, "method": "m2-domain-map", "confidence": 1.0, "match_score": 100}
    deterministic = sorted(
        ((_root_match_score(item, root, shared_repositories), root) for root in roots),
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
                    candidate = next(root for root in roots if root["root_id"] == root_id)
                    if _root_match_score(item, candidate, shared_repositories) > 0:
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
                    "head_sha": item.get("head_sha"),
                    "decision_id": decision.get("decision_id"),
                    "url": decision.get("url") or item.get("url"),
                    "signal": "P0 Blocking Decision",
                    "pending_decisions": [{
                        "kind": "owner_decision",
                        "status": decision.get("status"),
                        "decision_id": decision.get("decision_id"),
                        "linear_identifier": decision.get("linear_identifier"),
                        "url": decision.get("url"),
                    }],
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
