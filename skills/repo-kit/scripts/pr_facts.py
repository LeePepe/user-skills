#!/usr/bin/env python3
"""Read-only GitHub pull-request facts used by repo-kit."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

REFERENCE_DOC = "skills/repo-kit/references/repo-bound-skills-and-gates.md"
PR_FACTS_FIELDS = (
    "number,title,state,isDraft,mergeStateStatus,mergedAt,mergeCommit,"
    "headRefName,headRefOid,statusCheckRollup,url"
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _git_remote_url(repo_dir: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo_dir), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    cfg = _read_text(repo_dir / ".git" / "config")
    match = re.search(r'(?ms)^\[remote "origin"\]\s*\n(?:\s*[^\n]*\n)*?\s*url\s*=\s*(\S+)', cfg)
    return match.group(1).strip() if match else ""


def _github_remote_slug(remote_url: str) -> str:
    match = re.search(r"github\.com[/:]([^/ :]+/[^/]+?)(?:\.git)?$", remote_url.strip())
    return match.group(1) if match else ""


def _json_size(payload: Mapping[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _json_str_cap(value: Any, max_json_bytes: int, fallback: str = "") -> str:
    text = str(value or "") or fallback
    if len(json.dumps(text, ensure_ascii=False).encode("utf-8")) <= max_json_bytes:
        return text
    return text[: max(1, max_json_bytes // 2)]


def _pr_url_parts(url: str) -> tuple[str, int] | None:
    match = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)(?:[/?#]|$)", url.strip())
    if not match:
        return None
    return match.group(1).removesuffix(".git"), int(match.group(2))


def _resolve_pr_repo(repo_arg: str | None, url_slug: str = "") -> str:
    if repo_arg:
        candidate = Path(repo_arg).expanduser()
        if candidate.is_dir():
            return _github_remote_slug(_git_remote_url(candidate))
        if re.fullmatch(r"[^/\s]+/[^/\s]+", repo_arg):
            return repo_arg.removesuffix(".git")
        return ""
    return url_slug or _github_remote_slug(_git_remote_url(Path.cwd()))


def _check_name(check: Mapping[str, Any]) -> str:
    for key in ("name", "context", "workflowName", "appName"):
        if check.get(key):
            return _json_str_cap(check[key], 160, "unnamed-check")
    return "unnamed-check"


def _check_bucket(check: Mapping[str, Any]) -> str:
    values = {str(check.get(key) or "").upper() for key in ("conclusion", "state", "status")}
    if values & {"SUCCESS", "SUCCESSFUL", "PASSED", "PASS", "NEUTRAL", "SKIPPED"}:
        return "passed"
    if values & {"FAILURE", "FAILED", "FAIL", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
        return "failed"
    return "pending"


def _error(code: str, message: str) -> dict[str, Any]:
    return {
        "schema": "repo-kit/pr-facts/v1",
        "ok": False,
        "gate_tier": "soft_advisory_exit_0",
        "error": {"code": code, "message": _json_str_cap(message, 320)},
        "reference": REFERENCE_DOC,
    }


def build_pr_facts_report(
    *, pr: str | int | None = None, url: str | None = None, repo: str | None = None, strict: bool = False
) -> tuple[dict[str, Any], int, str | None]:
    url_parts = _pr_url_parts(url) if url else None
    if url and url_parts is None:
        return _error("invalid_url", "--url must be a GitHub pull request URL"), 2, "Invalid --url."
    if pr is not None and url:
        return _error("ambiguous_selector", "provide --pr/positional PR number or --url, not both"), 2, "Choose one PR selector."
    if url_parts:
        url_slug, number = url_parts
        selector = str(number)
    elif pr is not None:
        if not re.fullmatch(r"[1-9]\d*", str(pr)):
            return _error("invalid_pr", "PR number must be a positive integer"), 2, "Use --pr <positive-number>."
        number = int(str(pr))
        selector = str(number)
        url_slug = ""
    else:
        return _error("missing_selector", "provide --pr <N>, a positional PR number, or --url <pr_url>"), 2, "Provide a PR selector."

    repo_slug = _resolve_pr_repo(repo, url_slug if url_parts else "")
    if not repo_slug and not url_parts:
        return _error("missing_repo", "could not infer owner/repo from the local git remote"), 2, "Pass --repo owner/repo or a local git directory."
    command = ["gh", "pr", "view", selector]
    if repo_slug:
        command.extend(["--repo", repo_slug])
    command.extend(["--json", PR_FACTS_FIELDS])
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return _error("gh_unavailable", str(exc)), 1, "gh could not query the pull request."
    if proc.returncode != 0:
        message = proc.stderr.strip() or f"gh pr view exited {proc.returncode}"
        return _error("gh_query_failed", message), 1, "gh query failed or is offline."
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return _error("invalid_gh_json", str(exc)), 1, "gh returned invalid JSON."
    if not isinstance(raw, Mapping):
        return _error("invalid_gh_payload", "gh returned a non-object JSON payload"), 1, "gh returned an unexpected payload."

    counts = {"total": 0, "passed": 0, "failed": 0, "pending": 0}
    failed_checks: list[str] = []
    rollup = raw.get("statusCheckRollup") or []
    if isinstance(rollup, list):
        for check in rollup:
            if not isinstance(check, Mapping):
                continue
            bucket = _check_bucket(check)
            counts["total"] += 1
            counts[bucket] += 1
            if bucket == "failed" and _check_name(check) not in failed_checks:
                failed_checks.append(_check_name(check))
    payload: dict[str, Any] = {
        "schema": "repo-kit/pr-facts/v1",
        "ok": True,
        "gate_tier": "soft_advisory_exit_0",
        "number": raw.get("number", number),
        "title": _json_str_cap(raw.get("title"), 600),
        "state": raw.get("state"),
        "isDraft": raw.get("isDraft"),
        "mergeStateStatus": raw.get("mergeStateStatus"),
        "mergedAt": raw.get("mergedAt"),
        "mergeCommit": raw.get("mergeCommit"),
        "headRefName": raw.get("headRefName"),
        "headRefOid": raw.get("headRefOid"),
        "url": raw.get("url") or url,
        "check_counts": counts,
        "failed_checks": failed_checks[:100],
        "reference": REFERENCE_DOC,
    }
    while _json_size(payload) > 20_000 and payload["failed_checks"]:
        payload["failed_checks"].pop()
    if counts["failed"]:
        payload["advisory"] = "One or more checks are failing; facts only, no subjective gate was applied."
    if strict and counts["failed"]:
        return payload, 1, "PR has failing checks (--strict)."
    return payload, 0, None
