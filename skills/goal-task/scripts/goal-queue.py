#!/usr/bin/env python3
"""Atomic file-locked Goal task queue manager for Batch Mode (Posture 1) and Hot-Append (Posture 3).

Synchronizes `<handoff-dir>/queue.json` and `<handoff-dir>/ledger.md` under an
exclusive `fcntl.flock` lock on `<handoff-dir>/.queue.lock` so Domain Bots
(`Joy`, `Atlas`, `flower`, etc.) and `TL` can safely enqueue and drain tasks
concurrently without launching duplicate TLs or colliding on owned paths.
"""

from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import fnmatch
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterator, Sequence


VALID_STATUSES: tuple[str, ...] = (
    "planned",
    "ready",
    "in_progress",
    "in_review",
    "changes_requested",
    "done",
    "blocked",
    "cancelled",
)
ACTIVE_STATUSES: frozenset[str] = frozenset({"in_progress", "in_review", "changes_requested"})
PENDING_STATUSES: frozenset[str] = frozenset({"planned", "ready"})
TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "cancelled"})
EVENT_ID_RE: re.Pattern[str] = re.compile(r"\bid=E(\d+)\b")
GLOB_META_RE: re.Pattern[str] = re.compile(r"[*?\[\]]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sanitize_field(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").replace("|", "/").strip()


def _ensure_safe_dir(raw_dir: str | Path) -> Path:
    """Validate handoff directory against path traversal and symlinks."""
    raw_path = Path(raw_dir).expanduser()
    if ".." in raw_path.parts:
        raise ValueError(f"Refusing path traversal in handoff directory: {raw_dir}")

    current = raw_path
    checked: list[Path] = []
    while True:
        checked.append(current)
        if current.exists() or current.is_symlink():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    for candidate in checked:
        if candidate.is_symlink():
            raise ValueError(f"Refusing symlinked path in handoff directory hierarchy: {candidate}")

    raw_path.mkdir(parents=True, exist_ok=True)
    if raw_path.is_symlink():
        raise ValueError(f"Refusing symlinked handoff directory: {raw_path}")

    resolved = raw_path.resolve()
    for child_name in (".queue.lock", "queue.json", "ledger.md"):
        child = resolved / child_name
        if child.is_symlink():
            raise ValueError(f"Refusing symlinked queue artifact: {child}")

    return resolved


def _atomic_write_text(directory: Path, filename: str, content: str) -> None:
    target = directory / filename
    if target.is_symlink():
        raise ValueError(f"Refusing to write to symlink: {target}")
    tmp_path = directory / f".{filename}.tmp.{os.getpid()}"
    if tmp_path.is_symlink():
        raise ValueError(f"Refusing symlinked temporary file: {tmp_path}")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


@contextlib.contextmanager
def _queue_lock(handoff_dir: Path) -> Iterator[None]:
    lock_path = handoff_dir / ".queue.lock"
    if lock_path.is_symlink():
        raise ValueError(f"Refusing symlinked lock file: {lock_path}")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(lock_path), flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _normalize_list(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.split(",")
    else:
        raw_items = []
        for item in value:
            raw_items.extend(str(item).split(","))
    result: list[str] = []
    for item in raw_items:
        cleaned = item.strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _normalize_path_pattern(pattern: str) -> str:
    cleaned = pattern.strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.strip("/")
    return cleaned or "*"


def _static_prefix_segments(pattern: str) -> tuple[list[str], bool]:
    """Return path segments before the first glob metacharacter and whether globbing exists."""
    norm = _normalize_path_pattern(pattern)
    if norm in ("*", "**"):
        return ([], True)
    segments = [seg for seg in norm.split("/") if seg]
    static: list[str] = []
    has_glob = False
    for seg in segments:
        if GLOB_META_RE.search(seg):
            has_glob = True
            break
        static.append(seg)
    return (static, has_glob)


def _single_path_overlap(pat_a: str, pat_b: str) -> bool:
    norm_a = _normalize_path_pattern(pat_a)
    norm_b = _normalize_path_pattern(pat_b)
    if norm_a in ("*", "**") or norm_b in ("*", "**"):
        return True
    if norm_a == norm_b:
        return True
    if fnmatch.fnmatchcase(norm_a, norm_b) or fnmatch.fnmatchcase(norm_b, norm_a):
        return True

    static_a, glob_a = _static_prefix_segments(norm_a)
    static_b, glob_b = _static_prefix_segments(norm_b)

    if not glob_a and not glob_b:
        min_len = min(len(static_a), len(static_b))
        return static_a[:min_len] == static_b[:min_len]

    if not static_a or not static_b:
        if (not static_a and norm_a.startswith("**/")) or (not static_b and norm_b.startswith("**/")):
            tail_a = norm_a.removeprefix("**/")
            tail_b = norm_b.removeprefix("**/")
            return (
                fnmatch.fnmatchcase(norm_b.split("/")[-1], tail_a)
                or fnmatch.fnmatchcase(norm_a.split("/")[-1], tail_b)
                or fnmatch.fnmatchcase(norm_b, tail_a)
                or fnmatch.fnmatchcase(norm_a, tail_b)
            )
        return not static_a or not static_b

    min_static = min(len(static_a), len(static_b))
    if static_a[:min_static] != static_b[:min_static]:
        return False

    if len(static_a) < len(static_b) and glob_a:
        suffix_a = "/".join(norm_a.split("/")[len(static_a) :])
        rem_b = "/".join(norm_b.split("/")[len(static_a) :])
        if suffix_a in ("*", "**") or suffix_a.startswith("**/"):
            return True
        return fnmatch.fnmatchcase(rem_b, suffix_a)

    if len(static_b) < len(static_a) and glob_b:
        suffix_b = "/".join(norm_b.split("/")[len(static_b) :])
        rem_a = "/".join(norm_a.split("/")[len(static_b) :])
        if suffix_b in ("*", "**") or suffix_b.startswith("**/"):
            return True
        return fnmatch.fnmatchcase(rem_a, suffix_b)

    if len(static_a) == len(static_b) and glob_a and glob_b:
        rem_a = "/".join(norm_a.split("/")[len(static_a) :])
        rem_b = "/".join(norm_b.split("/")[len(static_b) :])
        if rem_a in ("*", "**") or rem_b in ("*", "**"):
            return True
        return fnmatch.fnmatchcase(rem_a, rem_b) or fnmatch.fnmatchcase(rem_b, rem_a)

    return True


def _paths_overlap(paths_a: Sequence[str], paths_b: Sequence[str]) -> bool:
    eff_a = [_normalize_path_pattern(p) for p in paths_a if p.strip()] or ["*"]
    eff_b = [_normalize_path_pattern(p) for p in paths_b if p.strip()] or ["*"]
    for pat_a in eff_a:
        for pat_b in eff_b:
            if _single_path_overlap(pat_a, pat_b):
                return True
    return False


def _tasks_conflict(task_a: dict[str, Any], task_b: dict[str, Any]) -> bool:
    repo_a = str(task_a.get("repo") or "").strip()
    repo_b = str(task_b.get("repo") or "").strip()
    if repo_a != repo_b:
        return False
    paths_a = task_a.get("paths") or []
    paths_b = task_b.get("paths") or []
    return _paths_overlap(paths_a, paths_b)


def _load_queue(handoff_dir: Path) -> dict[str, Any]:
    queue_path = handoff_dir / "queue.json"
    if not queue_path.exists():
        return {"goal": handoff_dir.name, "version": 1, "tasks": []}
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError(f"Corrupt queue.json in {handoff_dir}")
    return data


def _save_queue(handoff_dir: Path, queue_data: dict[str, Any]) -> None:
    serialized = json.dumps(queue_data, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(handoff_dir, "queue.json", serialized)


def _default_ledger_skeleton(goal_name: str) -> str:
    return (
        f"# Goal: {goal_name}\n"
        "- 目标: TBD\n"
        "- 验收: TBD\n"
        "- Owner: tianpei\n"
        "- 协调者 bot: joy\n"
        f"- TL: {goal_name}-goal-tl-r1 / 模型 gpt-6.1-sol\n"
        "- 机器: MacBook\n"
        "- 合同版本: goal-task@HEAD\n\n"
        "## Tasks\n"
        "| ID | 依赖 | 标题 | 负责 | 状态 | PR/head |\n"
        "|---|---|---|---|---|---|\n\n"
        "## Events\n"
    )


def _render_tasks_table(tasks: Sequence[dict[str, Any]]) -> str:
    lines = [
        "## Tasks",
        "| ID | 依赖 | 标题 | 负责 | 状态 | PR/head |",
        "|---|---|---|---|---|---|",
    ]
    for task in tasks:
        task_id = _sanitize_field(str(task.get("task_id", "")))
        deps_list = [str(d) for d in (task.get("depends_on") or []) if str(d).strip()]
        deps = _sanitize_field(", ".join(deps_list)) if deps_list else "—"
        title = _sanitize_field(str(task.get("title", task_id)))
        owner = _sanitize_field(str(task.get("owner") or "—"))
        status = _sanitize_field(str(task.get("status", "planned")))
        pr = _sanitize_field(str(task.get("pr") or "—"))
        lines.append(f"| {task_id} | {deps} | {title} | {owner} | {status} | {pr} |")
    return "\n".join(lines) + "\n"


def _next_event_number(ledger_text: str) -> int:
    max_id = 0
    for match in EVENT_ID_RE.finditer(ledger_text):
        max_id = max(max_id, int(match.group(1)))
    return max_id + 1


def _sync_ledger(
    handoff_dir: Path,
    tasks: Sequence[dict[str, Any]],
    new_events: Sequence[dict[str, str]],
) -> None:
    ledger_path = handoff_dir / "ledger.md"
    if ledger_path.exists():
        raw_text = ledger_path.read_text(encoding="utf-8")
    else:
        raw_text = _default_ledger_skeleton(handoff_dir.name)

    tasks_table_block = _render_tasks_table(tasks)

    if "## Tasks" in raw_text and "## Events" in raw_text:
        before_tasks, rest = raw_text.split("## Tasks", 1)
        _, events_part = rest.split("## Events", 1)
        rebuilt = f"{before_tasks.rstrip()}\n\n{tasks_table_block}\n## Events\n{events_part.lstrip()}"
    elif "## Events" in raw_text:
        before_events, events_part = raw_text.split("## Events", 1)
        rebuilt = f"{before_events.rstrip()}\n\n{tasks_table_block}\n## Events\n{events_part.lstrip()}"
    else:
        rebuilt = f"{raw_text.rstrip()}\n\n{tasks_table_block}\n## Events\n"

    next_num = _next_event_number(rebuilt)
    event_lines: list[str] = []
    ts = _now_iso()
    for evt in new_events:
        evt_id = f"E{next_num}"
        next_num += 1
        parts = [
            f"- {ts}",
            f"EVT={_sanitize_field(evt['evt'])}",
            f"id={evt_id}",
        ]
        if evt.get("task"):
            parts.append(f"task={_sanitize_field(evt['task'])}")
        if evt.get("ref"):
            parts.append(f"ref={_sanitize_field(evt['ref'])}")
        if evt.get("note"):
            parts.append(f"note={_sanitize_field(evt['note'])}")
        event_lines.append(" | ".join(parts))

    if event_lines:
        if not rebuilt.endswith("\n"):
            rebuilt += "\n"
        rebuilt += "\n".join(event_lines) + "\n"

    _atomic_write_text(handoff_dir, "ledger.md", rebuilt)


def _refresh_ready_statuses(tasks: list[dict[str, Any]]) -> None:
    status_by_id = {str(t["task_id"]): str(t.get("status", "planned")) for t in tasks}
    for task in tasks:
        current_status = str(task.get("status", "planned"))
        if current_status not in PENDING_STATUSES:
            continue
        deps = [str(d) for d in (task.get("depends_on") or []) if str(d).strip()]
        if all(status_by_id.get(dep) == "done" for dep in deps):
            task["status"] = "ready"
        else:
            task["status"] = "planned"


def _queue_metrics(tasks: Sequence[dict[str, Any]]) -> tuple[int, int, bool]:
    in_progress_count = sum(1 for t in tasks if str(t.get("status")) in ACTIVE_STATUSES)
    pending_count = sum(1 for t in tasks if str(t.get("status")) in PENDING_STATUSES)
    blocked_count = sum(1 for t in tasks if str(t.get("status")) == "blocked")
    drained = (in_progress_count + pending_count + blocked_count) == 0
    return (in_progress_count, pending_count, drained)


def _parse_batch_payload(batch_arg: str) -> list[dict[str, Any]]:
    stripped = batch_arg.strip()
    if stripped.startswith(("[", "{")):
        raw = json.loads(stripped)
    else:
        batch_path = Path(stripped).expanduser()
        if ".." in batch_path.parts or batch_path.is_symlink():
            raise ValueError(f"Refusing unsafe batch JSON path: {batch_arg}")
        raw = json.loads(batch_path.read_text(encoding="utf-8"))

    if isinstance(raw, dict):
        items = raw.get("tasks") if isinstance(raw.get("tasks"), list) else [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("--batch-json must decode to a JSON list or object")

    validated: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each batch task entry must be a JSON object")
        validated.append(item)
    return validated


def cmd_enqueue(args: argparse.Namespace) -> dict[str, Any]:
    handoff_dir = _ensure_safe_dir(args.handoff_dir)
    raw_specs: list[dict[str, Any]] = []
    if args.batch_json:
        raw_specs.extend(_parse_batch_payload(args.batch_json))
    if args.task_id:
        raw_specs.append(
            {
                "task_id": args.task_id,
                "title": args.title or args.task_id,
                "repo": args.repo or "",
                "paths": _normalize_list(args.paths),
                "depends_on": _normalize_list(args.depends_on),
                "brief": args.brief or "",
                "owner": args.owner or "",
            }
        )
    if not raw_specs:
        raise ValueError("enqueue requires either --task-id or --batch-json")

    with _queue_lock(handoff_dir):
        queue_data = _load_queue(handoff_dir)
        tasks: list[dict[str, Any]] = queue_data["tasks"]
        existing_ids = {str(t["task_id"]) for t in tasks}
        has_active_tasks = any(str(t.get("status")) in ACTIVE_STATUSES for t in tasks)

        enqueued_tasks: list[dict[str, Any]] = []
        auto_dependencies: dict[str, list[str]] = {}
        new_events: list[dict[str, str]] = []

        for spec in raw_specs:
            task_id = str(spec.get("task_id") or spec.get("id") or "").strip()
            if not task_id:
                raise ValueError("Task entry is missing required 'task_id'")
            if task_id in existing_ids:
                raise ValueError(f"Task '{task_id}' already exists in queue")

            title = str(spec.get("title") or task_id).strip()
            repo = str(spec.get("repo") or "").strip()
            paths = _normalize_list(spec.get("paths"))
            explicit_deps = _normalize_list(spec.get("depends_on"))
            brief = str(spec.get("brief") or f"briefs/{task_id}.md").strip()
            owner = str(spec.get("owner") or "—").strip()

            candidate: dict[str, Any] = {
                "task_id": task_id,
                "title": title,
                "repo": repo,
                "paths": paths,
                "depends_on": list(explicit_deps),
                "brief": brief,
                "owner": owner,
                "status": "planned",
                "pr": "—",
            }

            auto_deps_for_task: list[str] = []
            for earlier in tasks:
                earlier_status = str(earlier.get("status", "planned"))
                if earlier_status in TERMINAL_STATUSES:
                    continue
                earlier_id = str(earlier["task_id"])
                if _tasks_conflict(earlier, candidate):
                    if earlier_id not in candidate["depends_on"]:
                        candidate["depends_on"].append(earlier_id)
                    if earlier_id not in auto_deps_for_task:
                        auto_deps_for_task.append(earlier_id)

            tasks.append(candidate)
            existing_ids.add(task_id)
            auto_dependencies[task_id] = auto_deps_for_task
            enqueued_tasks.append(candidate)

        _refresh_ready_statuses(tasks)

        evt_name = "HOT_APPEND" if has_active_tasks else "READY"
        for item in enqueued_tasks:
            t_id = str(item["task_id"])
            auto_deps = auto_dependencies.get(t_id, [])
            dep_note = (
                f"auto_depends_on={','.join(auto_deps)}"
                if auto_deps
                else "parallel_wave_ready"
            )
            new_events.append(
                {
                    "evt": evt_name,
                    "task": t_id,
                    "ref": str(item.get("brief") or ""),
                    "note": f"repo={item.get('repo') or 'default'}; {dep_note}",
                }
            )

        _save_queue(handoff_dir, queue_data)
        _sync_ledger(handoff_dir, tasks, new_events)
        in_progress_count, pending_count, drained = _queue_metrics(tasks)

    return {
        "ok": True,
        "hot_append": has_active_tasks,
        "enqueued": enqueued_tasks,
        "enqueued_ids": [str(t["task_id"]) for t in enqueued_tasks],
        "auto_dependencies": auto_dependencies,
        "in_progress_count": in_progress_count,
        "pending_count": pending_count,
        "drained": drained,
    }


def cmd_next_wave(args: argparse.Namespace) -> dict[str, Any]:
    handoff_dir = _ensure_safe_dir(args.handoff_dir)
    with _queue_lock(handoff_dir):
        queue_data = _load_queue(handoff_dir)
        tasks: list[dict[str, Any]] = queue_data["tasks"]
        _refresh_ready_statuses(tasks)

        status_by_id = {str(t["task_id"]): str(t.get("status", "planned")) for t in tasks}
        active_tasks = [t for t in tasks if str(t.get("status")) in ACTIVE_STATUSES]

        selected_wave: list[dict[str, Any]] = []
        for task in tasks:
            if str(task.get("status")) not in PENDING_STATUSES:
                continue
            deps = [str(d) for d in (task.get("depends_on") or []) if str(d).strip()]
            if not all(status_by_id.get(dep) == "done" for dep in deps):
                continue
            if any(_tasks_conflict(active, task) for active in active_tasks):
                continue
            if any(_tasks_conflict(chosen, task) for chosen in selected_wave):
                continue
            selected_wave.append(task)

        new_events: list[dict[str, str]] = []
        if args.mark_in_progress and selected_wave:
            for task in selected_wave:
                task["status"] = "in_progress"
                new_events.append(
                    {
                        "evt": "DISPATCHED",
                        "task": str(task["task_id"]),
                        "ref": str(task.get("brief") or ""),
                        "note": f"wave dispatch ({task.get('repo') or 'default'})",
                    }
                )
            _save_queue(handoff_dir, queue_data)
            _sync_ledger(handoff_dir, tasks, new_events)
        else:
            _save_queue(handoff_dir, queue_data)

        in_progress_count, pending_count, drained = _queue_metrics(tasks)
        wave_payload = [
            {
                "task_id": str(t["task_id"]),
                "title": str(t.get("title") or t["task_id"]),
                "repo": str(t.get("repo") or ""),
                "paths": list(t.get("paths") or []),
                "depends_on": list(t.get("depends_on") or []),
                "brief": str(t.get("brief") or ""),
                "status": str(t.get("status") or "ready"),
            }
            for t in selected_wave
        ]

    return {
        "ok": True,
        "wave": wave_payload,
        "in_progress_count": in_progress_count,
        "pending_count": pending_count,
        "drained": drained,
    }


def cmd_set_status(args: argparse.Namespace) -> dict[str, Any]:
    handoff_dir = _ensure_safe_dir(args.handoff_dir)
    if args.status not in VALID_STATUSES:
        raise ValueError(f"Unsupported status: {args.status}")

    with _queue_lock(handoff_dir):
        queue_data = _load_queue(handoff_dir)
        tasks: list[dict[str, Any]] = queue_data["tasks"]
        target_task: dict[str, Any] | None = None
        for task in tasks:
            if str(task.get("task_id")) == args.task_id:
                target_task = task
                break

        if target_task is None:
            raise ValueError(f"Task '{args.task_id}' not found in {handoff_dir / 'queue.json'}")

        target_task["status"] = args.status
        if args.pr is not None:
            target_task["pr"] = args.pr
        if args.note is not None:
            target_task["note"] = args.note

        _refresh_ready_statuses(tasks)

        status_to_evt = {
            "done": "DONE",
            "blocked": "TASK_BLOCKED",
            "in_progress": "DISPATCHED",
            "in_review": "DISPATCHED",
            "changes_requested": "REVIEW_FAIL",
            "ready": "READY",
            "planned": "READY",
            "cancelled": "RESOLVED",
        }
        evt_type = status_to_evt.get(args.status, "READY")
        evt_entry: dict[str, str] = {
            "evt": evt_type,
            "task": args.task_id,
            "ref": str(args.pr or target_task.get("pr") or target_task.get("brief") or ""),
            "note": str(args.note or f"status={args.status}"),
        }

        _save_queue(handoff_dir, queue_data)
        _sync_ledger(handoff_dir, tasks, [evt_entry])
        in_progress_count, pending_count, drained = _queue_metrics(tasks)

    return {
        "ok": True,
        "task_id": args.task_id,
        "status": args.status,
        "in_progress_count": in_progress_count,
        "pending_count": pending_count,
        "drained": drained,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="goal-queue.py",
        description="Atomic file-locked Goal queue manager for Batch Mode and Hot-Append Mode.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    enqueue_parser = subparsers.add_parser(
        "enqueue",
        help="Enqueue single or batch tasks with automatic conflict dependency detection.",
    )
    enqueue_parser.add_argument("--handoff-dir", required=True, help="Path to handoff/<goal> directory.")
    enqueue_parser.add_argument("--task-id", default="", help="Unique Task ID (e.g. TK1).")
    enqueue_parser.add_argument("--title", default="", help="Human-readable task title.")
    enqueue_parser.add_argument("--repo", default="", help="Target repository name or relative slug.")
    enqueue_parser.add_argument(
        "--paths",
        default="",
        help="Comma-separated owned paths or globs within the target repository.",
    )
    enqueue_parser.add_argument(
        "--depends-on",
        default="",
        help="Comma-separated explicit prerequisite Task IDs.",
    )
    enqueue_parser.add_argument("--brief", default="", help="Relative path to task brief.")
    enqueue_parser.add_argument("--owner", default="", help="Assigned writer agent name.")
    enqueue_parser.add_argument(
        "--batch-json",
        default="",
        help="JSON array/object string or path to JSON file for Posture 1 batch intake.",
    )

    wave_parser = subparsers.add_parser(
        "next-wave",
        help="Compute the next conflict-free parallel wave of ready tasks.",
    )
    wave_parser.add_argument("--handoff-dir", required=True, help="Path to handoff/<goal> directory.")
    wave_parser.add_argument(
        "--mark-in-progress",
        action="store_true",
        help="Atomically mark selected wave tasks as in_progress and log EVT=DISPATCHED.",
    )

    status_parser = subparsers.add_parser(
        "set-status",
        help="Atomically update task status in queue.json and ledger.md.",
    )
    status_parser.add_argument("--handoff-dir", required=True, help="Path to handoff/<goal> directory.")
    status_parser.add_argument("--task-id", required=True, help="Task ID to update.")
    status_parser.add_argument(
        "--status",
        required=True,
        choices=VALID_STATUSES,
        help="New task status.",
    )
    status_parser.add_argument("--pr", default=None, help="PR URL, number, or head SHA.")
    status_parser.add_argument("--note", default=None, help="Short status transition note.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.subcommand == "enqueue":
            payload = cmd_enqueue(args)
        elif args.subcommand == "next-wave":
            payload = cmd_next_wave(args)
        elif args.subcommand == "set-status":
            payload = cmd_set_status(args)
        else:
            raise ValueError(f"Unknown subcommand: {args.subcommand}")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False) + "\n")
        return 2

    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
