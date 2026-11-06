#!/usr/bin/env python3
"""Layer-0 GitHub Actions and pull-request monitor."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Sequence


FAILED_CONCLUSIONS = {"failure", "timed_out", "action_required", "cancelled"}
GREEN_CHECKS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
FAILED_CHECKS = {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "CANCELLED", "ERROR"}


def run_command(command: Sequence[str]) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout


def _json_command(run: Callable[[Sequence[str]], str], command: Sequence[str]) -> dict[str, Any] | list[Any]:
    return json.loads(run(command))


def _atomic_write(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
        handle.write(content)
        temporary = handle.name
    os.replace(temporary, target)


def _job_id(job: dict[str, Any]) -> str:
    return str(job.get("databaseId") or job.get("id") or "")


def _job_failure(jobs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for job in jobs:
        if str(job.get("conclusion") or "").lower() in FAILED_CONCLUSIONS:
            return job
    return None


def _job_log(
    repo: str,
    job: dict[str, Any],
    run: Callable[[Sequence[str]], str],
    lines: int,
) -> str:
    job_id = _job_id(job)
    if not job_id:
        return "Unable to identify failed job."
    try:
        output = run(["gh", "api", f"repos/{repo}/actions/jobs/{job_id}/logs"])
    except (OSError, subprocess.CalledProcessError) as error:
        return f"Unable to fetch job log: {error}"
    return "\n".join(output.splitlines()[-lines:])


def _extract_build_number(logs: str) -> str:
    patterns = (
        r"Latest upload .*? build (\d+)",
        r"build_number\s*[:=]\s*[\"']?(\d+)",
        r"CFBundleVersion[^0-9]*(\d+)",
        r"(?:fastlane|build).*?number[^0-9]*(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, logs, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return "n/a"


def _marker_sha(repo: str, marker_tag: str, run: Callable[[Sequence[str]], str]) -> str:
    try:
        output = run(["git", "ls-remote", f"https://github.com/{repo}.git", f"refs/tags/{marker_tag}"])
    except (OSError, subprocess.CalledProcessError):
        return "n/a"
    fields = output.strip().split()
    return fields[0] if fields else "n/a"


def _receipt(
    path: str,
    fields: dict[str, Any],
    *,
    failed_log: str = "",
    checks: Sequence[str] = (),
) -> None:
    lines = ["# R10 CI Monitor Receipt", ""]
    for key in (
        "status",
        "conclusion",
        "run_id",
        "pr",
        "repo",
        "workflow",
        "candidate_sha",
        "build_number",
        "last_released_tag_sha",
        "merge_commit_sha",
        "url",
        "execution_layer",
    ):
        if key in fields:
            lines.append(f"- `{key}`: {fields[key]}")
    if checks:
        lines.extend(["", "## Checks", "", *[f"- {check}" for check in checks]])
    if failed_log:
        lines.extend(["", "## Failed job log excerpt", "", "```text", failed_log, "```"])
    lines.append("")
    _atomic_write(path, "\n".join(lines))


def _notify(agent: str | None, status: str, identifier: str, conclusion: str, receipt: str) -> None:
    if not agent:
        return
    try:
        subprocess.run(
            ["herdr", "agent", "prompt", agent, f"{status} run={identifier} conclusion={conclusion} receipt={receipt}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def monitor_workflow(
    args: argparse.Namespace,
    *,
    run: Callable[[Sequence[str]], str] = run_command,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], int]:
    run_id = args.run_id
    if args.trigger and not run_id:
        trigger = ["gh", "workflow", "run", args.workflow, "--repo", args.repo, "--ref", args.ref]
        if args.force:
            trigger.extend(["-f", "force=true"])
        run(trigger)
        if args.interval:
            sleep(min(args.interval, 2))
        listing = _json_command(
            run,
            ["gh", "run", "list", "--repo", args.repo, "--workflow", args.workflow,
             "--branch", args.ref, "--limit", "5", "--json", "databaseId,headSha,status,createdAt"],
        )
        candidates = listing if isinstance(listing, list) else []
        if not candidates:
            raise RuntimeError("workflow trigger succeeded but no workflow run was found")
        run_id = str(candidates[0]["databaseId"])
    if not run_id:
        raise ValueError("--run-id is required unless --trigger is supplied")

    started = now()
    last_view: dict[str, Any] = {}
    while True:
        last_view = _json_command(
            run,
            ["gh", "run", "view", str(run_id), "--repo", args.repo,
             "--json", "status,conclusion,jobs,headSha,url"],
        )
        jobs = last_view.get("jobs") or []
        failed = _job_failure(jobs)
        if failed:
            log = _job_log(args.repo, failed, run, 100)
            fields = {
                "status": "WORKFLOW_FAILED", "conclusion": last_view.get("conclusion") or failed.get("conclusion"),
                "run_id": run_id, "repo": args.repo, "workflow": args.workflow,
                "candidate_sha": last_view.get("headSha", "n/a"), "url": last_view.get("url", "n/a"),
                "execution_layer": "Layer-0 deterministic script (0 LLM tokens)",
            }
            _receipt(args.receipt, fields, failed_log=log)
            result = {"status": fields["status"], "run_id": run_id, "conclusion": fields["conclusion"],
                      "candidate_sha": fields["candidate_sha"], "last_released_tag_sha": "n/a", "receipt": args.receipt}
            _notify(args.notify_agent, result["status"], str(run_id), str(result["conclusion"]), args.receipt)
            return result, 1
        if last_view.get("status") == "completed":
            conclusion = str(last_view.get("conclusion") or "skipped")
            status = "WORKFLOW_SUCCESS" if conclusion == "success" else "WORKFLOW_SKIPPED"
            logs = "\n".join(_job_log(args.repo, job, run, 100) for job in jobs if _job_id(job))
            fields = {
                "status": status, "conclusion": conclusion, "run_id": run_id, "repo": args.repo,
                "workflow": args.workflow, "candidate_sha": last_view.get("headSha", "n/a"),
                "build_number": _extract_build_number(logs), "last_released_tag_sha": _marker_sha(args.repo, args.marker_tag, run),
                "url": last_view.get("url", "n/a"),
                "execution_layer": "Layer-0 deterministic script (0 LLM tokens)",
            }
            _receipt(args.receipt, fields)
            result = {"status": status, "run_id": run_id, "conclusion": conclusion,
                      "candidate_sha": fields["candidate_sha"], "last_released_tag_sha": fields["last_released_tag_sha"],
                      "receipt": args.receipt}
            _notify(args.notify_agent, status, str(run_id), conclusion, args.receipt)
            return result, 0 if status == "WORKFLOW_SUCCESS" else 1
        if now() - started >= args.timeout:
            fields = {
                "status": "WORKFLOW_TIMEOUT", "conclusion": last_view.get("conclusion") or "pending",
                "run_id": run_id, "repo": args.repo, "workflow": args.workflow,
                "candidate_sha": last_view.get("headSha", "n/a"), "url": last_view.get("url", "n/a"),
                "execution_layer": "Layer-0 deterministic script (0 LLM tokens)",
            }
            _receipt(args.receipt, fields)
            result = {"status": fields["status"], "run_id": run_id, "conclusion": fields["conclusion"],
                      "candidate_sha": fields["candidate_sha"], "last_released_tag_sha": "n/a", "receipt": args.receipt}
            _notify(args.notify_agent, result["status"], str(run_id), str(result["conclusion"]), args.receipt)
            return result, 1
        sleep(args.interval)


def _check_state(check: dict[str, Any]) -> str:
    return str(check.get("conclusion") or check.get("state") or "").upper()


def monitor_pr(
    args: argparse.Namespace,
    *,
    run: Callable[[Sequence[str]], str] = run_command,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], int]:
    started = now()
    while True:
        view = _json_command(
            run,
            ["gh", "pr", "view", str(args.pr), "--repo", args.repo,
             "--json", "number,state,mergedAt,mergeCommit,mergeStateStatus,headRefOid,statusCheckRollup,url"],
        )
        head = view.get("headRefOid")
        if args.head_sha and head != args.head_sha:
            result = {"status": "PR_HEAD_DRIFT", "pr": args.pr, "receipt": args.receipt}
            _receipt(args.receipt, {**result, "repo": args.repo, "execution_layer": "Layer-0 deterministic script (0 LLM tokens)"})
            return result, 1
        if view.get("state") == "MERGED" or view.get("mergedAt"):
            merge_commit = view.get("mergeCommit") or {}
            fields = {
                "status": "PR_MERGED", "conclusion": "success", "pr": args.pr, "repo": args.repo,
                "merge_commit_sha": merge_commit.get("oid", "n/a") if isinstance(merge_commit, dict) else merge_commit,
                "url": view.get("url", "n/a"), "execution_layer": "Layer-0 deterministic script (0 LLM tokens)",
            }
            _receipt(args.receipt, fields)
            _notify(args.notify_agent, fields["status"], str(args.pr), "success", args.receipt)
            return {**fields, "receipt": args.receipt}, 0
        if view.get("mergeStateStatus") == "DIRTY":
            fields = {"status": "PR_CONFLICT", "conclusion": "failure", "pr": args.pr, "repo": args.repo,
                      "url": view.get("url", "n/a"), "execution_layer": "Layer-0 deterministic script (0 LLM tokens)"}
            _receipt(args.receipt, fields)
            return {**fields, "receipt": args.receipt}, 1
        checks = view.get("statusCheckRollup") or []
        failed = next((check for check in checks if _check_state(check) in FAILED_CHECKS), None)
        if failed:
            details = str(failed.get("detailsUrl") or "")
            match = re.search(r"/job/(\d+)", details)
            job = {"id": match.group(1)} if match else {}
            log = _job_log(args.repo, job, run, 80) if match else "Unable to identify failed job."
            fields = {"status": "PR_CHECK_FAILED", "conclusion": _check_state(failed), "pr": args.pr, "repo": args.repo,
                      "url": view.get("url", "n/a"), "execution_layer": "Layer-0 deterministic script (0 LLM tokens)"}
            _receipt(args.receipt, fields, failed_log=log)
            return {**fields, "receipt": args.receipt}, 1
        states = [_check_state(check) for check in checks]
        if args.exit_on_green and checks and all(state in GREEN_CHECKS for state in states):
            fields = {"status": "PR_CHECKS_GREEN", "conclusion": "success", "pr": args.pr, "repo": args.repo,
                      "url": view.get("url", "n/a"), "execution_layer": "Layer-0 deterministic script (0 LLM tokens)"}
            _receipt(args.receipt, fields, checks=states)
            return {**fields, "receipt": args.receipt}, 0
        if now() - started >= args.timeout:
            fields = {"status": "WORKFLOW_TIMEOUT", "conclusion": "pending", "pr": args.pr, "repo": args.repo,
                      "url": view.get("url", "n/a"), "execution_layer": "Layer-0 deterministic script (0 LLM tokens)"}
            _receipt(args.receipt, fields, checks=states)
            return {**fields, "receipt": args.receipt}, 1
        sleep(args.interval)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    workflow = subparsers.add_parser("workflow", help="monitor a GitHub Actions workflow")
    workflow.add_argument("--repo", required=True)
    workflow.add_argument("--workflow", default="testflight.yml")
    workflow.add_argument("--run-id")
    workflow.add_argument("--trigger", action="store_true")
    workflow.add_argument("--force", action="store_true")
    workflow.add_argument("--ref", default="main")
    workflow.add_argument("--marker-tag", default="testflight/last-released")
    workflow.add_argument("--interval", type=float, default=30)
    workflow.add_argument("--timeout", type=float, default=2400)
    workflow.add_argument("--receipt", required=True)
    workflow.add_argument("--notify-agent")
    pr = subparsers.add_parser("pr", help="monitor a pull request")
    pr.add_argument("--repo", required=True)
    pr.add_argument("--pr", required=True, type=int)
    pr.add_argument("--head-sha")
    pr.add_argument("--interval", type=float, default=20)
    pr.add_argument("--timeout", type=float, default=1800)
    pr.add_argument("--receipt", required=True)
    pr.add_argument("--notify-agent")
    pr.add_argument("--exit-on-green", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result, code = monitor_workflow(args) if args.command == "workflow" else monitor_pr(args)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "ERROR", "error": str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
