#!/usr/bin/env python3
"""Deterministic M2 lifecycle, ledger, and write-boundary preflight locks."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from skill_runtime import (  # noqa: E402
    SelfDisclosingArgumentParser,
    atomic_write_json,
    compact_json_dumps,
    is_safe_local_dir,
    is_safe_local_file,
    is_valid_skill_slug,
    redact_text,
    safe_read_text,
)

REFERENCE = "skills/m2-dispatcher/references/pr-dag-auto-heal.md"
_TERMINAL = {"done", "cancelled"}
_EVENT_RE = re.compile(r"\|\s*EVT=([^|\s]+)")
_ID_RE = re.compile(r"\bid=E(\d+)\b")
_ZOMBIE_RE = re.compile(r"\b(?:in_progress|active|planning)\b")


class _M2PreflightArgumentParser(SelfDisclosingArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(f"{self.prog}: {redact_text(message)}\n")
        sys.stderr.write("Try `python3 skills/m2-dispatcher/scripts/m2_preflight.py --help`.\n")
        sys.stderr.write(f"Reference: {REFERENCE}\n")
        raise SystemExit(2)


def _fail(message: str) -> int:
    sys.stderr.write(f"m2_preflight: {redact_text(message)}\nReference: {REFERENCE}\n")
    return 1


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(compact_json_dumps(payload, max_bytes=3900, preserve_keys=("ok", "error", "reference", "goal", "old_goal", "new_goal")))
        return
    lines = [f"ok={payload.get('ok', False)}"]
    for key in ("goal", "old_goal", "new_goal", "has_delta", "cancelled_tasks", "new_events"):
        if key in payload:
            value = payload[key]
            lines.append(f"{key}={value}")
    print("\n".join(lines)[:3900])


def _split_row(line: str) -> list[str] | None:
    if not line.lstrip().startswith("|"):
        return None
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if not cells or all(set(cell) <= {"-", ":", " "} for cell in cells):
        return None
    return cells


def _task_records(lines: list[str]) -> list[dict]:
    records: list[dict] = []
    for header_index, header in enumerate(lines):
        cells = _split_row(header)
        if not cells:
            continue
        normalized = [re.sub(r"[_\s-]+", " ", cell.casefold()) for cell in cells]
        status_index = next((i for i, value in enumerate(normalized) if value in {"status", "状态"}), None)
        if status_index is None:
            continue
        id_index = next((i for i, value in enumerate(normalized) if value in {"id", "task id", "task"}), None)
        if id_index is None:
            continue
        owner_index = next((i for i, value in enumerate(normalized) if value in {"owner", "负责"}), None)
        pr_index = next((i for i, value in enumerate(normalized) if value.startswith("pr")), None)
        for row_index in range(header_index + 1, len(lines)):
            row = _split_row(lines[row_index])
            if row is None:
                if records and row_index > header_index + 1:
                    break
                continue
            if len(row) <= max(id_index, status_index):
                continue
            task_id = row[id_index]
            if not task_id or task_id.casefold() in {"id", "task", "---"}:
                continue
            records.append({
                "task_id": task_id,
                "status": row[status_index],
                "owner": row[owner_index] if owner_index is not None and owner_index < len(row) else "—",
                "pr": row[pr_index] if pr_index is not None and pr_index < len(row) else "—",
                "line_index": row_index,
                "status_index": status_index,
            })
        if records:
            break
    return records


def _event_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if _EVENT_RE.search(line)]


def _load_watermark(path: Path) -> dict:
    if not is_safe_local_file(path):
        return {}
    try:
        value = json.loads(safe_read_text(path, default="{}") or "{}")
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _ledger_delta(args: argparse.Namespace) -> int:
    handoff = Path(args.handoff_dir).expanduser()
    ledger_path = handoff / "ledger.md"
    if not is_safe_local_dir(handoff) or not is_safe_local_file(ledger_path):
        return _fail(f"handoff directory or ledger.md is missing/unreadable: {handoff}")
    text = safe_read_text(ledger_path)
    if text is None:
        return _fail(f"cannot read ledger.md: {ledger_path}")
    lines = text.splitlines()
    total_lines = len(lines)
    events = _event_lines(lines)
    watermark_path = handoff / ".m2-ledger-watermark.json"
    watermark = _load_watermark(watermark_path)
    explicit = args.since_line is not None
    if explicit:
        start_line = min(max(0, args.since_line), total_lines)
        selected = [line for line in lines[start_line:] if _EVENT_RE.search(line)]
    else:
        start_line = min(max(0, int(watermark.get("last_line", 0) or 0)), total_lines)
        previous_id = str(watermark.get("last_event_id", "") or "")
        event_start = int(watermark.get("last_event_idx", 0) or 0)
        if previous_id:
            positions = [i for i, line in enumerate(events) if previous_id in line]
            event_start = positions[-1] + 1 if positions else 0
        if event_start > len(events):
            event_start = 0
        selected = events[event_start:]
    tail = max(1, int(args.tail))
    selected = selected[-tail:]
    payload = {
        "ok": True,
        "goal": handoff.name,
        "since_line": start_line,
        "next_since_line": total_lines,
        "total_lines": total_lines,
        "has_delta": bool(selected),
        "tasks": [{key: record[key] for key in ("task_id", "status", "owner", "pr")} for record in _task_records(lines)],
        "new_events": selected,
        "watermark_file": redact_text(str(watermark_path)),
    }
    if not args.no_save:
        last_id_match = _ID_RE.search(events[-1]) if events else None
        next_watermark = {
            "last_line": total_lines,
            "last_event_idx": len(events),
            "last_event_id": f"E{last_id_match.group(1)}" if last_id_match else "",
            "sha256_prefix": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        }
        try:
            atomic_write_json(watermark_path, next_watermark)
        except OSError as exc:
            return _fail(f"cannot save ledger watermark: {exc}")
    _emit(payload, args.json)
    return 0


def _next_event_id(lines: list[str]) -> int:
    ids = [int(match.group(1)) for match in (_ID_RE.search(line) for line in lines) if match]
    return max(ids, default=-1) + 1


def _queue_tasks(queue_path: Path) -> list[dict]:
    if not is_safe_local_file(queue_path):
        return []
    try:
        value = json.loads(safe_read_text(queue_path, default="{}") or "{}")
        tasks = value.get("tasks", []) if isinstance(value, dict) else []
        return tasks if isinstance(tasks, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _supersede_goal(args: argparse.Namespace) -> int:
    old = Path(args.old_handoff).expanduser()
    if not is_valid_skill_slug(args.new_goal):
        return _fail("--new-goal must be a lowercase skill slug")
    if old.name == args.new_goal:
        return _fail("--new-goal must differ from the old handoff name")
    ledger_path = old / "ledger.md"
    if not is_safe_local_dir(old) or not is_safe_local_file(ledger_path):
        return _fail(f"old handoff or ledger.md is missing/unreadable: {old}")
    text = safe_read_text(ledger_path)
    if text is None:
        return _fail(f"cannot read ledger.md: {ledger_path}")
    lines = text.splitlines()
    existing = f"EVT=GOAL_SUPERSEDED" in text and f"superseded_by={args.new_goal}" in text
    records = _task_records(lines)
    queue_path = old / "queue.json"
    queue_tasks = _queue_tasks(queue_path)
    cancelled = [record["task_id"] for record in records if record["status"].strip().casefold() not in _TERMINAL]
    for task in queue_tasks:
        task_id = str(task.get("task_id", ""))
        if task_id and str(task.get("status", "")).casefold() not in _TERMINAL and task_id not in cancelled:
            cancelled.append(task_id)
    payload = {"ok": True, "applied": bool(args.apply), "old_goal": old.name, "new_goal": args.new_goal,
               "cancelled_tasks": cancelled, "events_appended": [] if existing else ["EVT=GOAL_SUPERSEDED", "EVT=GOAL_DONE"],
               "already_superseded": existing}
    if args.apply and not existing:
        for record in records:
            if record["status"].strip().casefold() in _TERMINAL:
                continue
            parts = lines[record["line_index"]].split("|")
            cell_index = record["status_index"] + 1 if lines[record["line_index"]].lstrip().startswith("|") else record["status_index"]
            if cell_index < len(parts):
                parts[cell_index] = " cancelled "
                lines[record["line_index"]] = "|".join(parts)
        rewritten = []
        for line in lines:
            line = re.sub(r"(?i)(\bstatus\s*:\s*)(?:in_progress|active|planning)\b", r"\1done (superseded_by=" + args.new_goal + ")", line)
            if "EVT=" not in line:
                line = _ZOMBIE_RE.sub("cancelled", line)
            rewritten.append(line)
        timestamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
        reason = str(args.reason).replace(" | ", "; ").replace("\n", " ").strip() or "superseded by M2 pivot"
        event_id = _next_event_id(rewritten)
        rewritten.append(f"- {timestamp} | EVT=GOAL_SUPERSEDED | id=E{event_id} | note=superseded_by={args.new_goal}; cancelled_tasks={len(cancelled)}; reason={reason}")
        rewritten.append(f"- {timestamp} | EVT=GOAL_DONE | id=E{event_id + 1} | note=outcome=superseded; superseded_by={args.new_goal}")
        ledger_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        if queue_tasks and is_safe_local_file(queue_path):
            try:
                queue = json.loads(safe_read_text(queue_path, default="{}") or "{}")
                for task in queue.get("tasks", []):
                    if str(task.get("status", "")).casefold() not in _TERMINAL:
                        task["status"] = "cancelled"
                        task["superseded_by"] = args.new_goal
                atomic_write_json(queue_path, queue)
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                return _fail(f"cannot update queue.json: {exc}")
    _emit(payload, args.json)
    return 0


def _inside(path: Path, root: Path) -> bool:
    try:
        return path == root or root in path.parents
    except (OSError, ValueError):
        return False


def _write_zone(raw: str) -> str | None:
    if ".." in Path(raw.replace("\\", "/")).parts:
        return None
    expanded = Path(raw).expanduser()
    if expanded.is_absolute():
        candidate = expanded
    else:
        candidate = Path.cwd() / expanded
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    roots = [(Path.cwd() / "handoff", "handoff"), (Path.home() / "Development" / "handoff", "handoff"),
             (Path("/tmp"), "tmp"), (Path("/private/tmp"), "tmp"),
             (Path.cwd() / ".copilot" / "session-state", "session-state"),
             (Path.home() / ".copilot" / "session-state", "session-state"),
             (Path("/var/folders"), "tmp"), (Path("/private/var/folders"), "tmp")]
    for root, zone in roots:
        try:
            if _inside(resolved, root.resolve(strict=False)):
                return zone
        except OSError:
            continue
    return None


def _check_write_path(args: argparse.Namespace) -> int:
    zone = _write_zone(args.path)
    if zone:
        _emit({"ok": True, "allowed": True, "path": redact_text(args.path), "matched_zone": zone}, args.json)
        return 0
    message = "M2 write-boundary violation: M2 may only write handoff/**, /tmp/**, or .copilot/session-state/**; dispatch M1 TL/Writer for repo code/test/config edits."
    if args.json:
        _emit({"ok": False, "allowed": False, "error": message, "reference": REFERENCE}, True)
    sys.stderr.write(f"m2_preflight: {message}\nReference: {REFERENCE}\n")
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = _M2PreflightArgumentParser(prog="m2_preflight.py")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_M2PreflightArgumentParser)
    ledger = sub.add_parser("ledger-delta")
    ledger.add_argument("--handoff-dir", required=True)
    ledger.add_argument("--since-line", type=int)
    ledger.add_argument("--tail", type=int, default=20)
    ledger.add_argument("--no-save", action="store_true")
    ledger.add_argument("--json", action="store_true")
    supersede = sub.add_parser("supersede-goal")
    supersede.add_argument("--old-handoff", required=True)
    supersede.add_argument("--new-goal", required=True)
    supersede.add_argument("--reason", default="superseded by M2 pivot")
    supersede.add_argument("--apply", action="store_true")
    supersede.add_argument("--json", action="store_true")
    write = sub.add_parser("check-write-path")
    write.add_argument("--path", required=True)
    write.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "ledger-delta":
        return _ledger_delta(args)
    if args.command == "supersede-goal":
        if args.apply:
            old = Path(args.old_handoff).expanduser()
            if not old.is_dir() or not (old / "ledger.md").is_file():
                return _supersede_goal(args)
            lock_path = old / ".queue.lock"
            with open(lock_path, "a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                result = _supersede_goal(args)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return result
        return _supersede_goal(args)
    return _check_write_path(args)


if __name__ == "__main__":
    raise SystemExit(main())
