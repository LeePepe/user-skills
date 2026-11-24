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


FAILED_CONCLUSIONS = {"failure", "timed_out", "action_required"}
GREEN_CHECKS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
FAILED_CHECKS = {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "ERROR"}
CANCELLED_CHECKS = {"CANCELLED"}
PENDING_CHECKS = {"PENDING", "IN_PROGRESS", "QUEUED", "REQUESTED", "WAITING", "EXPECTED", "PROGRESS"}
PR_URL_RE = re.compile(r"https?://github\.com/([^/\s]+/[^/\s]+?)/pull/(\d+)(?:[/?#].*)?$", re.IGNORECASE)
GITHUB_REMOTE_RE = re.compile(r"github\.com[/:]([^/:\s]+/[^/\s]+?)(?:\.git)?$", re.IGNORECASE)
SUBCOMMAND_ALIASES: dict[str, str] = {
    "workflow": "workflow",
    "pr": "pr",
    "checks": "checks",
    "pr-checks": "checks",
    "inspect-pr": "checks",
}


class _SelfDisclosingCIMonitorParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(
            f"[ci-monitor] CLI error: {message}\n"
            "  Fix: run `python3 skills/team-lead/scripts/ci-monitor.py {pr,workflow,checks} --help`\n"
            "  Reference: skills/team-lead/references/playbook.md | skills/team-lead/references/eval-cases.json\n"
        )
        raise SystemExit(2)


def _fail_self_disclosing(message: str) -> int:
    sys.stderr.write(
        f"[ci-monitor] error: {message}\n"
        "  Fix: supply `--pr <num|url> --repo <owner/repo>` or run inside a git worktree with a GitHub `origin`\n"
        "  Reference: skills/team-lead/references/playbook.md | skills/team-lead/references/eval-cases.json\n"
    )
    print(json.dumps({"status": "ERROR", "error": message}))
    return 2


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
        "gc_result",
        "execution_layer",
        "cancelled_checks",
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
    if not getattr(args, "receipt", None):
        args.receipt = _default_receipt_path(args)
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
            if conclusion == "success":
                status = "WORKFLOW_SUCCESS"
            elif conclusion == "cancelled":
                status = "WORKFLOW_CANCELLED"
            else:
                status = "WORKFLOW_SKIPPED"
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
            return result, 0 if status in {"WORKFLOW_SUCCESS", "WORKFLOW_CANCELLED"} else 1
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


def _check_timestamp(check: dict[str, Any]) -> str:
    return str(
        check.get("completedAt")
        or check.get("updatedAt")
        or check.get("startedAt")
        or check.get("createdAt")
        or ""
    )


def _effective_check_rollup(checks: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the latest named check while preserving unnamed rollup entries."""
    named: dict[str, tuple[int, str, dict[str, Any]]] = {}
    unnamed: list[dict[str, Any]] = []
    for index, check in enumerate(checks):
        name = str(check.get("name") or check.get("context") or "").strip()
        if not name:
            unnamed.append(check)
            continue
        timestamp = _check_timestamp(check)
        previous = named.get(name)
        if previous is None or (timestamp and timestamp >= previous[1]) or (not timestamp and index >= previous[0]):
            named[name] = (index, timestamp, check)
    selected = [(index, check) for index, _, check in named.values()]
    selected.extend((len(checks) + index, check) for index, check in enumerate(unnamed))
    return [check for _, check in sorted(selected, key=lambda item: item[0])]


def _parse_pr_spec(raw: str | int | None) -> tuple[int | None, str | None]:
    if raw is None:
        return None, None
    if isinstance(raw, int):
        return raw, None
    text = str(raw).strip()
    if not text:
        return None, None
    match = PR_URL_RE.match(text)
    if match:
        return int(match.group(2)), match.group(1)
    cleaned = text.lstrip("#")
    if re.fullmatch(r"\d+", cleaned):
        return int(cleaned), None
    raise ValueError(f"invalid pull request identifier: {raw!r} (expected PR number or GitHub pull URL)")


def _infer_repo_from_git(worktree: str | None, *, run: Callable[[Sequence[str]], str] = run_command) -> str | None:
    target = str(Path(worktree).expanduser()) if worktree else os.getcwd()
    try:
        remote = run(["git", "-C", target, "remote", "get-url", "origin"]).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    match = GITHUB_REMOTE_RE.search(remote)
    return match.group(1) if match else None


def _default_receipt_path(args: argparse.Namespace) -> str:
    explicit = getattr(args, "receipt", None)
    if explicit:
        return str(explicit)
    handoff_dir = getattr(args, "handoff_dir", None)
    tmp_root = Path(tempfile.gettempdir())
    if args.command == "workflow":
        wf_slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(getattr(args, "workflow", "workflow") or "workflow")).strip("-") or "workflow"
        if handoff_dir:
            return str(Path(handoff_dir).expanduser() / "receipts" / f"ci-monitor-workflow-{wf_slug}.md")
        return str(tmp_root / f"ci-monitor-workflow-{wf_slug}.md")
    pr_num = getattr(args, "pr", "unknown")
    if handoff_dir:
        return str(Path(handoff_dir).expanduser() / "receipts" / f"ci-monitor-pr-{pr_num}.md")
    return str(tmp_root / f"ci-monitor-pr-{pr_num}.md")


def _resolve_pr_and_repo(
    args: argparse.Namespace,
    *,
    run: Callable[[Sequence[str]], str] = run_command,
    require_pr: bool = True,
) -> None:
    pr_opt = getattr(args, "pr", None)
    pr_pos = getattr(args, "pr_positional", None)
    chosen_raw = pr_opt if pr_opt not in (None, "") else pr_pos
    pr_num: int | None = None
    url_repo: str | None = None
    if chosen_raw not in (None, ""):
        pr_num, url_repo = _parse_pr_spec(chosen_raw)
        if pr_opt not in (None, "") and pr_pos not in (None, ""):
            pos_num, pos_repo = _parse_pr_spec(pr_pos)
            if pos_num != pr_num:
                raise ValueError(f"conflicting PR numbers supplied: positional {pos_num} vs --pr {pr_num}")
            url_repo = url_repo or pos_repo
    if require_pr and pr_num is None:
        raise ValueError("pull request number or URL is required (positional <pr> or --pr <pr>)")
    if pr_num is not None:
        args.pr = pr_num

    repo = str(getattr(args, "repo", "") or "").strip()
    if not repo and url_repo:
        repo = url_repo
    if not repo:
        repo = _infer_repo_from_git(getattr(args, "worktree", None), run=run) or ""
    if not repo:
        raise ValueError("--repo <owner>/<repo> is required when repository cannot be inferred from PR URL or git origin")
    args.repo = repo


def _resolve_merge_method(args: argparse.Namespace) -> str:
    auto_merge_val = getattr(args, "auto_merge", None)
    if isinstance(auto_merge_val, str) and auto_merge_val in {"squash", "merge", "rebase"}:
        return auto_merge_val
    if getattr(args, "squash", False):
        return "squash"
    method = str(getattr(args, "merge_method", None) or "squash").lower()
    return method if method in {"squash", "merge", "rebase"} else "squash"


def _should_enable_auto_merge(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "enable_auto", False)
        or getattr(args, "auto_merge", None) is not None
    )


def _best_effort_enable_auto_merge(
    args: argparse.Namespace,
    *,
    head_oid: str | None = None,
    run: Callable[[Sequence[str]], str] = run_command,
) -> None:
    if not _should_enable_auto_merge(args):
        return
    method = _resolve_merge_method(args)
    cmd = ["gh", "pr", "merge", str(args.pr), "--repo", str(args.repo), "--auto", f"--{method}"]
    match_sha = str(getattr(args, "head_sha", "") or head_oid or "").strip()
    if match_sha:
        cmd.extend(["--match-head-commit", match_sha])
    if getattr(args, "delete_branch", False):
        cmd.append("--delete-branch")
    try:
        if run is run_command:
            subprocess.run(cmd, check=False, capture_output=True, text=True)
        else:
            run(cmd)
    except Exception:  # noqa: BLE001
        pass


def inspect_pr_checks(
    args: argparse.Namespace,
    *,
    run: Callable[[Sequence[str]], str] = run_command,
) -> tuple[dict[str, Any], int]:
    _resolve_pr_and_repo(args, run=run, require_pr=True)
    view = _json_command(
        run,
        [
            "gh",
            "pr",
            "view",
            str(args.pr),
            "--repo",
            str(args.repo),
            "--json",
            "number,state,mergeStateStatus,statusCheckRollup,url",
        ],
    )
    if not isinstance(view, dict):
        view = {}
    checks = view.get("statusCheckRollup")
    if not isinstance(checks, list):
        checks = []
    payload = {
        "ok": True,
        "gate_tier": "soft_advisory_exit_0",
        "pr": view.get("number", args.pr),
        "repo": args.repo,
        "state": view.get("state"),
        "merge_state_status": view.get("mergeStateStatus"),
        "url": view.get("url"),
        "checks": checks,
    }
    return payload, 0


def monitor_pr(
    args: argparse.Namespace,
    *,
    run: Callable[[Sequence[str]], str] = run_command,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], int]:
    if not getattr(args, "receipt", None):
        args.receipt = _default_receipt_path(args)
    started = now()
    auto_merge_attempted = False
    while True:
        view = _json_command(
            run,
            ["gh", "pr", "view", str(args.pr), "--repo", args.repo,
             "--json", "number,state,mergedAt,mergeCommit,mergeStateStatus,headRefOid,headRefName,statusCheckRollup,url"],
        )
        head = view.get("headRefOid")
        if args.head_sha and head != args.head_sha:
            result = {"status": "PR_HEAD_DRIFT", "pr": args.pr, "receipt": args.receipt}
            _receipt(args.receipt, {**result, "repo": args.repo, "execution_layer": "Layer-0 deterministic script (0 LLM tokens)"})
            return result, 1
        if view.get("state") == "MERGED" or view.get("mergedAt"):
            merge_commit = view.get("mergeCommit") or {}
            gc_result = _maybe_gc(args, view)
            fields = {
                "status": "PR_MERGED", "conclusion": "success", "pr": args.pr, "repo": args.repo,
                "merge_commit_sha": merge_commit.get("oid", "n/a") if isinstance(merge_commit, dict) else merge_commit,
                "url": view.get("url", "n/a"), "execution_layer": "Layer-0 deterministic script (0 LLM tokens)",
                "gc_result": gc_result,
            }
            _receipt(args.receipt, fields)
            _notify(args.notify_agent, fields["status"], str(args.pr), "success", args.receipt)
            return {**fields, "receipt": args.receipt}, 0
        if not auto_merge_attempted:
            auto_merge_attempted = True
            _best_effort_enable_auto_merge(args, head_oid=str(head or ""), run=run)
        if view.get("mergeStateStatus") == "DIRTY":
            fields = {"status": "PR_CONFLICT", "conclusion": "failure", "pr": args.pr, "repo": args.repo,
                      "url": view.get("url", "n/a"), "execution_layer": "Layer-0 deterministic script (0 LLM tokens)"}
            _receipt(args.receipt, fields)
            return {**fields, "receipt": args.receipt}, 1
        raw_checks = view.get("statusCheckRollup") or []
        checks = _effective_check_rollup([check for check in raw_checks if isinstance(check, dict)])
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
        pending = [check for check in checks if _check_state(check) in PENDING_CHECKS]
        cancelled = [check for check in checks if _check_state(check) in CANCELLED_CHECKS]
        if args.exit_on_green and checks and all(state in GREEN_CHECKS for state in states):
            fields = {"status": "PR_CHECKS_GREEN", "conclusion": "success", "pr": args.pr, "repo": args.repo,
                      "url": view.get("url", "n/a"), "execution_layer": "Layer-0 deterministic script (0 LLM tokens)"}
            _receipt(args.receipt, fields, checks=states)
            return {**fields, "receipt": args.receipt}, 0
        elapsed = now() - started
        if cancelled and not pending and (args.exit_on_green or elapsed >= args.timeout):
            cancelled_names = [str(check.get("name") or check.get("context") or "unnamed") for check in cancelled]
            required = any(bool(check.get("isRequired")) for check in cancelled)
            fields = {"status": "PR_CHECK_CANCELLED", "conclusion": "cancelled", "pr": args.pr, "repo": args.repo,
                      "url": view.get("url", "n/a"), "cancelled_checks": cancelled_names,
                      "execution_layer": "Layer-0 deterministic script (0 LLM tokens)"}
            _receipt(args.receipt, fields, checks=states)
            return {**fields, "receipt": args.receipt}, 1 if required else 0
        if elapsed >= args.timeout:
            fields = {"status": "WORKFLOW_TIMEOUT", "conclusion": "pending", "pr": args.pr, "repo": args.repo,
                      "url": view.get("url", "n/a"), "execution_layer": "Layer-0 deterministic script (0 LLM tokens)"}
            _receipt(args.receipt, fields, checks=states)
            return {**fields, "receipt": args.receipt}, 1
        sleep(args.interval)


def _maybe_gc(args: argparse.Namespace, view: dict[str, Any]) -> dict[str, Any] | None:
    if getattr(args, "no_cleanup_worktree", False) or os.environ.get("GTT_WORKTREE_GC") == "0":
        return None
    worktree_arg = getattr(args, "worktree", None)
    branch_arg = getattr(args, "branch", None)
    if not (worktree_arg or branch_arg or os.environ.get("GTT_WORKTREE_GC") != "0"):
        return None
    script = Path(__file__).with_name("git-gc.py")
    command = [sys.executable, str(script), "task-clean", "--repo", args.repo, "--pr", str(args.pr)]
    worktree = worktree_arg
    branch = branch_arg or view.get("headRefName")
    if worktree:
        command.extend(["--worktree", worktree])
    if branch:
        command.extend(["--branch", str(branch)])
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        try:
            payload = json.loads(completed.stdout)
        except (ValueError, json.JSONDecodeError):
            payload = {"ok": False, "error": completed.stderr.strip() or "invalid gc output"}
        if completed.returncode:
            payload.setdefault("ok", False)
        return payload
    except OSError as error:
        return {"ok": False, "error": str(error)}


def _normalize_argv(argv: Sequence[str] | None) -> list[str]:
    tokens = list(sys.argv[1:] if argv is None else argv)
    if not tokens:
        return tokens
    value_flags = {
        "--repo",
        "--workflow",
        "--run-id",
        "--ref",
        "--marker-tag",
        "--interval",
        "--timeout",
        "--max-wait",
        "--receipt",
        "--receipt-out",
        "--out",
        "--notify-agent",
        "--pr",
        "--head-sha",
        "--worktree",
        "--branch",
        "--handoff-dir",
        "--handoff",
        "--dir",
        "--goal",
        "--goal-id",
        "--task",
        "--task-id",
        "--merge-method",
    }
    subcmd_index: int | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in {"-h", "--help"}:
            return tokens
        if tok in SUBCOMMAND_ALIASES:
            subcmd_index = i
            break
        if tok == "--auto-merge":
            if i + 1 < len(tokens) and tokens[i + 1] in {"squash", "merge", "rebase"}:
                i += 2
            else:
                i += 1
            continue
        if tok.startswith("-"):
            if "=" not in tok and tok in value_flags and i + 1 < len(tokens):
                i += 2
            else:
                i += 1
        else:
            break
    if subcmd_index is not None:
        canonical = SUBCOMMAND_ALIASES[tokens[subcmd_index]]
        return [canonical, *tokens[:subcmd_index], *tokens[subcmd_index + 1 :]]
    return tokens


def _add_common_compat_args(sub: argparse.ArgumentParser, *, default_timeout: float | None = None) -> None:
    sub.add_argument("--json", action="store_true", default=True)
    sub.add_argument("--handoff-dir", "--handoff", "--dir", dest="handoff_dir", default=None)
    sub.add_argument("--goal", "--goal-id", dest="goal", default=None)
    sub.add_argument("--task", "--task-id", dest="task", default=None)
    sub.add_argument("--no-cleanup-worktree", action="store_true", default=False)
    if default_timeout is not None:
        sub.add_argument("--timeout", "--max-wait", dest="timeout", type=float, default=default_timeout)


def _parser() -> argparse.ArgumentParser:
    parser = _SelfDisclosingCIMonitorParser(prog="ci-monitor.py", description=__doc__)
    parser.add_argument("--json", action="store_true", default=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    workflow = subparsers.add_parser("workflow", help="monitor a GitHub Actions workflow")
    workflow.add_argument("--repo", default="")
    workflow.add_argument("--workflow", default="testflight.yml")
    workflow.add_argument("--run-id")
    workflow.add_argument("--trigger", action="store_true")
    workflow.add_argument("--force", action="store_true")
    workflow.add_argument("--ref", default="main")
    workflow.add_argument("--marker-tag", default="testflight/last-released")
    workflow.add_argument("--interval", type=float, default=30)
    workflow.add_argument("--receipt", "--receipt-out", "--out", dest="receipt", default=None)
    workflow.add_argument("--notify-agent")
    workflow.add_argument("--worktree", default=None)
    _add_common_compat_args(workflow, default_timeout=2400)

    pr = subparsers.add_parser("pr", help="monitor a pull request")
    pr.add_argument("pr_positional", nargs="?", default=None, help="PR number or GitHub PR URL")
    pr.add_argument("--repo", default="")
    pr.add_argument("--pr", dest="pr", default=None)
    pr.add_argument("--head-sha")
    pr.add_argument("--worktree")
    pr.add_argument("--branch")
    pr.add_argument("--interval", type=float, default=20)
    pr.add_argument("--receipt", "--receipt-out", "--out", dest="receipt", default=None)
    pr.add_argument("--notify-agent")
    pr.add_argument("--exit-on-green", action="store_true")
    pr.add_argument("--enable-auto", "--enable-auto-merge", "--merge", dest="enable_auto", action="store_true", default=False)
    pr.add_argument("--auto-merge", dest="auto_merge", nargs="?", const="auto", default=None)
    pr.add_argument("--merge-method", dest="merge_method", choices=("squash", "merge", "rebase"), default="squash")
    pr.add_argument("--squash", action="store_true", default=False)
    pr.add_argument("--delete-branch", action="store_true", default=False)
    _add_common_compat_args(pr, default_timeout=1800)

    checks = subparsers.add_parser(
        "checks",
        aliases=["pr-checks", "inspect-pr"],
        help="collect objective PR check status facts (Layer-3 soft advisory exit 0)",
    )
    checks.add_argument("pr_positional", nargs="?", default=None, help="PR number or GitHub PR URL")
    checks.add_argument("--pr", dest="pr", default=None)
    checks.add_argument("--repo", default="")
    checks.add_argument("--worktree", default=None)
    _add_common_compat_args(checks, default_timeout=60)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    normalized = _normalize_argv(argv)
    args = _parser().parse_args(normalized)
    canonical_cmd = SUBCOMMAND_ALIASES.get(args.command, args.command)
    args.command = canonical_cmd
    try:
        if canonical_cmd == "checks":
            result, code = inspect_pr_checks(args)
        elif canonical_cmd == "workflow":
            if not str(getattr(args, "repo", "") or "").strip():
                inferred = _infer_repo_from_git(getattr(args, "worktree", None))
                if not inferred:
                    return _fail_self_disclosing(
                        "--repo <owner>/<repo> is required when repository cannot be inferred from git origin"
                    )
                args.repo = inferred
            if not getattr(args, "receipt", None):
                args.receipt = _default_receipt_path(args)
            result, code = monitor_workflow(args)
        else:
            _resolve_pr_and_repo(args, require_pr=True)
            if not getattr(args, "receipt", None):
                args.receipt = _default_receipt_path(args)
            result, code = monitor_pr(args)
    except ValueError as error:
        return _fail_self_disclosing(str(error))
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        sys.stderr.write(
            f"[ci-monitor] runtime error: {error}\n"
            "  Fix: verify `gh auth status`, repository slug, and PR/run existence\n"
            "  Reference: skills/team-lead/references/playbook.md | skills/team-lead/references/eval-cases.json\n"
        )
        print(json.dumps({"status": "ERROR", "error": str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
