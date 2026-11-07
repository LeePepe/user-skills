#!/usr/bin/env python3
"""Deterministic cleanup for merged Git worktrees and branches."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote
from urllib.request import urlopen


NOISE_NAMES = {".DS_Store", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__", ".build"}
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
GITHUB_SLUG = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"


def _run(command: Sequence[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=check, capture_output=True, text=True)


def resolve_repo(raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    if "/" in raw and not raw.startswith("/"):
        candidate = Path("~/Development").expanduser() / raw.rsplit("/", 1)[-1]
        if candidate.is_dir():
            return candidate.resolve()
    raise ValueError(f"Repository directory not found: {raw}")


def _json_values(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _json_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_values(child)


def has_active_herdr_pane(worktree: Path, *, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> bool:
    """Return true when Herdr reports an active pane rooted in worktree."""
    try:
        result = runner(["herdr", "agent", "list", "--json"], check=False, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return False
    output = result.stdout or ""
    try:
        records = list(_json_values(json.loads(output)))
    except (ValueError, TypeError):
        records = []
    target = worktree.expanduser().resolve()
    for record in records:
        status = str(record.get("status") or record.get("state") or "").lower()
        cwd = record.get("cwd") or record.get("worktree") or record.get("directory")
        if status in {"active", "working", "idle", "blocked", "running"} and cwd:
            try:
                path = Path(str(cwd)).expanduser().resolve()
            except OSError:
                continue
            if path == target or target in path.parents:
                return True
    # Older Herdr versions emit a human-readable list. Only treat an exact
    # worktree path as active; an unavailable/ambiguous listing stays safe.
    return any(
        str(target) in line and any(word in line.lower() for word in ("active", "working", "idle", "blocked", "running"))
        for line in output.splitlines()
    )


def _github_slug(repo: Path | str, *, runner: Runner = _run) -> str | None:
    value = str(repo)
    if re.fullmatch(GITHUB_SLUG, value) and not Path(value).expanduser().exists():
        return value
    try:
        result = runner(["git", "-C", value, "remote", "get-url", "origin"])
    except (OSError, subprocess.SubprocessError):
        return None
    remote = result.stdout.strip()
    match = re.search(r"github\.com[/:]([^/:\s]+/[^/\s]+?)(?:\.git)?$", remote)
    return match.group(1) if match else None


def _rest_json(url: str) -> Any:
    with urlopen(url) as response:
        return json.load(response)


def _pr_state(repo: Path, pr: str, *, runner: Runner = _run) -> str | None:
    value = str(pr)
    slug = _github_slug(repo, runner=runner)
    try:
        result = runner(["gh", "pr", "view", value, "--repo", slug or str(repo), "--json", "state,mergedAt"])
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        if not slug:
            return None
        try:
            payload = _rest_json(f"https://api.github.com/repos/{slug}/pulls/{quote(value, safe='')}")
        except (OSError, ValueError, json.JSONDecodeError):
            return None
    if payload.get("mergedAt") or str(payload.get("state", "")).upper() == "MERGED":
        return "MERGED"
    if payload.get("merged_at") or str(payload.get("state", "")).upper() == "MERGED":
        return "MERGED"
    return str(payload.get("state") or "").upper() or None


def is_merged(repo: Path, *, branch: str | None = None, pr: str | None = None, runner: Runner = _run) -> bool:
    """Require positive merge evidence; never infer MERGED from a missing query."""
    if pr:
        return _pr_state(repo, pr, runner=runner) == "MERGED"
    if branch:
        slug = _github_slug(repo, runner=runner)
        try:
            result = runner([
                "gh", "pr", "list", "--repo", slug or str(repo), "--state", "all",
                "--head", branch, "--limit", "20", "--json", "number,state,mergedAt",
            ])
            rows = json.loads(result.stdout)
            if isinstance(rows, list):
                if any(str(row.get("state", "")).upper() == "OPEN" for row in rows):
                    return False
                if any(row.get("mergedAt") or str(row.get("state", "")).upper() == "MERGED" for row in rows):
                    return True
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            if slug:
                try:
                    owner, _ = slug.split("/", 1)
                    rows = _rest_json(
                        f"https://api.github.com/repos/{slug}/pulls?state=all&head="
                        f"{quote(owner + ':' + branch, safe=':')}&per_page=20"
                    )
                    if isinstance(rows, list):
                        if any(str(row.get("state", "")).upper() == "OPEN" for row in rows):
                            return False
                        if any(row.get("merged_at") or str(row.get("state", "")).upper() == "MERGED" for row in rows):
                            return True
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
        # A local ancestor is useful for non-GitHub repositories, but is not
        # allowed to override an explicit OPEN/CLOSED PR result.
        try:
            result = runner(["git", "-C", str(repo), "merge-base", "--is-ancestor", branch, "HEAD"], check=False)
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return False


def _worktrees(repo: Path, *, runner: Runner = _run) -> list[dict[str, str]]:
    result = runner(["git", "-C", str(repo), "worktree", "list", "--porcelain"])
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines() + [""]:
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key in {"worktree", "HEAD", "branch"}:
            current[key] = value.removeprefix("refs/heads/")
    return records


def clean_noise(worktree: Path) -> int:
    removed = 0
    if not worktree.is_dir():
        return removed
    for path in sorted(worktree.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.name not in NOISE_NAMES:
            continue
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _dirty(worktree: Path, *, runner: Runner = _run) -> bool:
    result = runner(["git", "-C", str(worktree), "status", "--porcelain"], check=True)
    return bool(result.stdout.strip())


def _remote_exists(repo: Path, branch: str, *, runner: Runner = _run) -> bool:
    result = runner(["git", "-C", str(repo), "ls-remote", "--exit-code", "--heads", "origin", branch], check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


def task_clean(
    *,
    repo: Path,
    worktree: Path | None = None,
    branch: str | None = None,
    pr: str | None = None,
    dry_run: bool = False,
    allow_unmerged: bool = False,
    runner: Runner = _run,
    herdr_check: Callable[[Path], bool] = has_active_herdr_pane,
) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "repo": str(repo), "dry_run": dry_run, "removed": [], "skipped": None}
    if worktree:
        worktree = worktree.expanduser().resolve()
        if worktree == repo.resolve():
            result["skipped"] = "primary_worktree"
            return result
        if herdr_check(worktree):
            result["skipped"] = "active_herdr_pane"
            return result
    if not allow_unmerged and not is_merged(repo, branch=branch, pr=pr, runner=runner):
        result["skipped"] = "unmerged_pr"
        return result
    if worktree and worktree.is_dir():
        clean_noise(worktree)
        if _dirty(worktree, runner=runner):
            result["skipped"] = "dirty_worktree"
            return result
    companions: list[Path] = []
    if worktree:
        companion = Path(f"{worktree}-data")
        clean_noise(companion)
        if companion.is_dir() and not _dirty(companion, runner=runner):
            companions.append(companion)
    if not dry_run:
        for target in [worktree, *companions]:
            if target and target.exists():
                _run(["git", "-C", str(repo), "worktree", "remove", str(target)])
                result["removed"].append(str(target))
        _run(["git", "-C", str(repo), "worktree", "prune"])
    elif worktree:
        result["removed"].extend(str(target) for target in [worktree, *companions] if target.exists())

    remaining = _worktrees(repo, runner=runner)
    if branch and not any(item.get("branch") == branch for item in remaining):
        if not dry_run:
            _run(["git", "-C", str(repo), "branch", "-D", branch], check=False)
        result["removed"].append(f"local:{branch}")
    if branch and _remote_exists(repo, branch, runner=runner):
        if not dry_run:
            _run(["git", "-C", str(repo), "push", "origin", "--delete", branch], check=False)
        result["removed"].append(f"remote:{branch}")
    return result


def _pr_rows(repo: str, *, runner: Runner = _run) -> list[dict[str, Any]]:
    slug = _github_slug(repo, runner=runner) or repo
    try:
        result = runner(["gh", "pr", "list", "--repo", slug, "--state", "all", "--limit", "500",
                         "--json", "number,state,headRefName,mergedAt,closedAt"])
        payload = json.loads(result.stdout)
        return payload if isinstance(payload, list) else []
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        if not slug or not re.fullmatch(GITHUB_SLUG, slug):
            return []
        try:
            payload = _rest_json(f"https://api.github.com/repos/{slug}/pulls?state=all&per_page=100")
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        rows = []
        for item in payload if isinstance(payload, list) else []:
            head = item.get("head") or {}
            rows.append({
                "number": item.get("number"),
                "state": str(item.get("state") or "").upper(),
                "headRefName": head.get("ref"),
                "mergedAt": item.get("merged_at"),
                "closedAt": item.get("closed_at"),
            })
        return rows


def _remote_branches(remote: str, *, runner: Runner = _run) -> set[str]:
    try:
        result = runner(["gh", "api", f"repos/{remote}/branches", "--paginate", "--jq", ".[].name"])
        if result.returncode == 0:
            return {line.strip() for line in result.stdout.splitlines() if line.strip()}
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        result = runner(["git", "ls-remote", "--heads", f"https://github.com/{remote}.git"], check=False)
    except (OSError, subprocess.SubprocessError):
        return set()
    branches = set()
    for line in result.stdout.splitlines():
        _, _, ref = line.partition("\trefs/heads/")
        if ref:
            branches.add(ref.strip())
    return branches


def sweep(args: argparse.Namespace) -> dict[str, Any]:
    dev_root = Path(args.dev_root).expanduser()
    worktrees_root = Path(args.worktrees_root).expanduser()
    report_dir = Path(args.report_dir).expanduser()
    report_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    selected = None if args.repos in {"", "all"} else {item.strip() for item in args.repos.split(",")}
    for repo in sorted(dev_root.iterdir()) if dev_root.is_dir() else []:
        if not (repo / ".git").exists() or (selected and repo.name not in selected and str(repo) not in selected):
            continue
        worktree_items = _worktrees(repo)
        checked_out = {item.get("branch") for item in worktree_items if item.get("branch")}
        for item in worktree_items:
            path = Path(item["worktree"])
            if path == repo or not path.exists():
                continue
            branch = item.get("branch")
            merged = is_merged(repo, branch=branch)
            age = time.time() - max(path.stat().st_mtime, (path / ".git").stat().st_mtime if (path / ".git").exists() else 0)
            reason = "merged" if merged else ("dirty" if _dirty(path) else "no_pr")
            entry = {"kind": "worktree", "repo": str(repo), "worktree": str(path), "branch": branch, "merged": merged, "reason": reason}
            if has_active_herdr_pane(path):
                entry["reason"] = "active_herdr_pane"
            elif age < args.min_age_hours * 3600:
                entry["reason"] = "too_recent"
            elif merged and not _dirty(path) and args.apply_tier1:
                entry["gc_result"] = task_clean(repo=repo, worktree=path, branch=branch, dry_run=False)
            rows.append(entry)
        branch_result = _run(["git", "-C", str(repo), "branch", "--format=%(refname:short)"])
        for branch in (line.strip() for line in branch_result.stdout.splitlines()):
            if not branch or branch in {"main", "master"} or branch in checked_out:
                continue
            merged = is_merged(repo, branch=branch)
            if merged:
                entry = {"kind": "local_branch", "repo": str(repo), "branch": branch,
                         "merged": True, "reason": "merged"}
                if args.apply_tier1:
                    completed = _run(["git", "-C", str(repo), "branch", "-D", branch], check=False)
                    entry["gc_result"] = {"ok": completed.returncode == 0, "branch": branch}
                rows.append(entry)
                continue
            pr_rows = [pr for pr in _pr_rows(str(repo)) if pr.get("headRefName") == branch]
            if any(str(pr.get("state", "")).upper() == "OPEN" for pr in pr_rows):
                continue
            reason = "closed" if any(str(pr.get("state", "")).upper() == "CLOSED" for pr in pr_rows) else "no_pr"
            rows.append({"kind": "local_branch", "repo": str(repo), "branch": branch,
                         "merged": False, "reason": reason})
    for remote in filter(None, (item.strip() for item in args.remote_repos.split(","))):
        pr_by_branch: dict[str, list[dict[str, Any]]] = {}
        for pr in _pr_rows(remote):
            branch = str(pr.get("headRefName") or "")
            if branch:
                pr_by_branch.setdefault(branch, []).append(pr)
        for branch in sorted(_remote_branches(remote)):
            if branch in {"main", "master"}:
                continue
            prs = pr_by_branch.get(branch, [])
            if any(str(pr.get("state", "")).upper() == "OPEN" for pr in prs):
                continue
            merged = any(str(pr.get("state", "")).upper() == "MERGED" and pr.get("mergedAt") for pr in prs)
            entry = {
                "kind": "remote_branch", "repo": remote, "branch": branch,
                "merged": merged, "reason": "merged" if merged else (
                    "closed" if any(str(pr.get("state", "")).upper() == "CLOSED" for pr in prs) else "no_pr"
                ),
            }
            if merged and args.apply_tier1:
                completed = _run(
                    ["gh", "api", "-X", "DELETE", f"repos/{remote}/git/refs/heads/{branch}"],
                    check=False,
                )
                entry["gc_result"] = {"ok": completed.returncode == 0, "branch": branch}
            rows.append(entry)
    tier2 = [row for row in rows if row.get("reason") != "merged" and "gc_result" not in row]
    payload = {"ok": True, "tier1": [row for row in rows if row.get("reason") == "merged"], "tier2": tier2, "count": len(rows)}
    (report_dir / "latest-sweep.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (report_dir / "latest-tier2.md").write_text(
        "# Git GC Tier 2\n\n" + "\n".join(f"- `{row.get('reason')}` {row.get('worktree') or row.get('branch')}" for row in tier2) + "\n",
        encoding="utf-8",
    )
    return payload


def approve_tier2(args: argparse.Namespace) -> dict[str, Any]:
    report = json.loads(Path(args.report_file).read_text(encoding="utf-8"))
    cleaned = []
    for row in report.get("tier2", []):
        reason = row.get("reason")
        if reason == "dirty" or (reason == "closed" and not args.include_closed_pr) or (
            reason == "no_pr" and not args.include_no_pr
        ) or (
            (reason == "companion_data" or str(row.get("worktree", "")).endswith("-data"))
            and not args.include_companion_data
        ):
            continue
        if row.get("kind") == "worktree" and row.get("worktree"):
            cleaned.append(task_clean(
                repo=Path(row["repo"]), worktree=Path(row["worktree"]), branch=row.get("branch"),
                dry_run=args.dry_run, allow_unmerged=True,
            ))
        elif row.get("kind") == "local_branch" and row.get("branch"):
            command = ["git", "-C", row["repo"], "branch", "-D", row["branch"]]
            if args.dry_run:
                cleaned.append({"ok": True, "dry_run": True, "branch": row["branch"]})
            else:
                completed = _run(command, check=False)
                cleaned.append({"ok": completed.returncode == 0, "branch": row["branch"]})
        elif row.get("kind") == "remote_branch" and row.get("branch"):
            command = ["gh", "api", "-X", "DELETE", f"repos/{row['repo']}/git/refs/heads/{row['branch']}"]
            if args.dry_run:
                cleaned.append({"ok": True, "dry_run": True, "branch": row["branch"]})
            else:
                completed = _run(command, check=False)
                cleaned.append({"ok": completed.returncode == 0, "branch": row["branch"]})
    return {"ok": True, "cleaned": cleaned, "dry_run": args.dry_run}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    task = sub.add_parser("task-clean")
    task.add_argument("--repo", required=True)
    task.add_argument("--worktree")
    task.add_argument("--branch")
    task.add_argument("--pr")
    task.add_argument("--handoff-dir")
    task.add_argument("--task-id")
    task.add_argument("--dry-run", action="store_true")
    sweep_parser = sub.add_parser("sweep")
    sweep_parser.add_argument("--dev-root", default="~/Development")
    sweep_parser.add_argument("--worktrees-root", default="~/Development/worktrees")
    sweep_parser.add_argument("--repos", default="all")
    sweep_parser.add_argument("--remote-repos", default="")
    sweep_parser.add_argument("--apply-tier1", action="store_true")
    sweep_parser.add_argument("--report-dir", default="~/Development/handoff/_watch/gc")
    sweep_parser.add_argument("--min-age-hours", type=int, default=24)
    approve = sub.add_parser("approve-tier2")
    approve.add_argument("--report-file", required=True)
    approve.add_argument("--include-closed-pr", action="store_true")
    approve.add_argument("--include-no-pr", action="store_true")
    approve.add_argument("--include-companion-data", action="store_true")
    approve.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "task-clean":
            payload = task_clean(repo=resolve_repo(args.repo), worktree=Path(args.worktree) if args.worktree else None,
                                 branch=args.branch, pr=args.pr, dry_run=args.dry_run)
        elif args.command == "sweep":
            payload = sweep(args)
        else:
            payload = approve_tier2(args)
        print(json.dumps(payload, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
