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
import tempfile
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from skill_runtime import (SelfDisclosingArgumentParser, atomic_write_json, compact_json_dumps, is_safe_local_dir, redact_payload, is_safe_local_file, is_valid_skill_slug, redact_text, safe_read_text)  # noqa: E402
REFERENCE = "skills/m2-dispatcher/references/pr-dag-auto-heal.md"
_TERMINAL = {"done", "cancelled"}
_EVENT_RE = re.compile(r"\|\s*EVT=([^|\s]+)")
TRACKED_TASK_FIELDS = {"feedback_ids", "acceptance_mapping", "acceptance_evidence", "superseded_by", "cancellation_authorization", "replacement_of"}; _ID_RE = re.compile(r"(?:^|\|\s*)id=([A-Za-z]+\d+)(?:\s*\||$)"); SCRIPT_REPO_ROOT = SCRIPT_DIR.parents[2].resolve()
class _M2PreflightArgumentParser(SelfDisclosingArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(f"{self.prog}: {redact_text(message)}\n")
        sys.stderr.write("Try `python3 skills/m2-dispatcher/scripts/m2_preflight.py --help`.\n")
        sys.stderr.write(f"Reference: {REFERENCE}\n")
        raise SystemExit(2)
def _fail(message: str) -> int:
    sys.stderr.write(f"m2_preflight: {redact_text(message)}\nReference: {REFERENCE}\n")
    return 1
def _event_id(line: str) -> str:
    match = re.search(r"\|\s*id=([A-Za-z]+\d+)\b", line); return match.group(1) if match else ""
def _byte_prefix(text: str, limit: int = 3900) -> str: return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
def _emit(payload: dict, as_json: bool) -> None:
    safe_payload = redact_payload(payload)
    if as_json:
        text = json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":"))
        print(text if len(text.encode("utf-8")) <= 3900 else compact_json_dumps(safe_payload, max_bytes=3900, preserve_keys=("ok", "goal", "old_goal", "new_goal", "has_delta", "truncated", "remaining_events", "tasks", "new_events", "cancelled_tasks", "reference")))
        return
    lines = [f"ok={safe_payload.get('ok', False)}"]
    for key in ("goal", "old_goal", "new_goal", "has_delta", "truncated", "tasks", "cancelled_tasks", "new_events"):
        if key in safe_payload:
            lines.append(f"{key}={safe_payload[key]}")
    print(_byte_prefix("\n".join(lines)))
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
    lines, total_lines = text.splitlines(), len(text.splitlines())
    event_rows = [(i, n, line) for i, line in enumerate(lines) if (n := len([x for x in lines[:i] if _EVENT_RE.search(x)])) >= 0 and _EVENT_RE.search(line)]
    events = [row[2] for row in event_rows]
    watermark_path = handoff / ".m2-ledger-watermark.json"
    watermark = _load_watermark(watermark_path)
    explicit = args.since_line is not None
    if explicit:
        start_line = min(max(0, args.since_line), total_lines)
        candidates = [row for row in event_rows if row[0] >= start_line]
        ordered = candidates[-max(1, int(args.tail)) :]
    else:
        start_line = min(max(0, int(watermark.get("last_line", 0) or 0)), total_lines)
        event_start = int(watermark.get("last_event_idx", 0) or 0)
        previous_id = str(watermark.get("last_event_id", "") or "")
        if previous_id:
            matches = [i for i, line in enumerate(events) if _event_id(line) == previous_id]
            event_start = matches[-1] + 1 if matches else 0
        event_start = min(max(0, event_start), len(events))
        ordered = event_rows[event_start : event_start + max(1, int(args.tail))]
    tasks = [{key: record[key] for key in ("task_id", "status", "owner", "pr")} for record in _task_records(lines)]
    candidates = ordered if explicit else event_rows[event_start:]
    tail = max(1, int(args.tail))
    chosen: list[tuple[int, int, str]] = []
    for count in range(min(tail, len(candidates)), 0, -1):
        batch = candidates[-count:] if explicit else candidates[:count]
        for task_count in range(min(len(tasks), 40), -1, -1):
            for char_limit in ((600,) if count > 1 else (600, 300, 150, 80, 40, 20)):
                batch_events = [row[2][:char_limit] for row in batch]
                payload = {"ok": True, "goal": handoff.name, "since_line": start_line,
                           "next_since_line": batch[-1][0] + 1, "total_lines": total_lines,
                           "has_delta": True, "truncated": len(batch) < len(candidates),
                           "remaining_events": len(candidates) - len(batch), "tasks": tasks[:task_count],
                           "new_events": batch_events, "watermark_file": redact_text(str(watermark_path))}
                if len(json.dumps(redact_payload(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 3800:
                    chosen = batch
                    break
            if chosen:
                break
        if chosen:
            break
    if chosen:
        payload["new_events"] = [row[2][:char_limit] for row in chosen]
        payload["has_delta"] = True
        payload["truncated"] = len(chosen) < len(candidates)
        payload["remaining_events"] = len(candidates) - len(chosen)
    else:
        last_line = start_line
        payload = {"ok": True, "goal": handoff.name, "since_line": start_line, "next_since_line": last_line,
                   "total_lines": total_lines, "has_delta": False, "truncated": False,
                   "remaining_events": 0, "tasks": tasks[:40], "new_events": [],
                   "watermark_file": redact_text(str(watermark_path))}
    if not args.no_save and chosen:
        last_event = chosen[-1]
        next_watermark = {"last_line": last_event[0] + 1, "last_event_idx": last_event[1] + 1,
                          "last_event_id": _event_id(last_event[2]),
                          "sha256_prefix": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]}
        try:
            atomic_write_json(watermark_path, next_watermark)
        except OSError as exc:
            return _fail(f"cannot save ledger watermark: {exc}")
    _emit(payload, args.json)
    return 0
def _next_event_id(lines: list[str]) -> int:
    ids = [int(value[1:]) for value in (_event_id(line) for line in lines) if value.startswith("E") and value[1:].isdigit()]
    return max(ids, default=-1) + 1
def _load_queue_data(path: Path) -> dict | None:
    if not os.path.lexists(path):
        return None
    if not is_safe_local_file(path):
        raise ValueError("unsafe queue.json")
    try:
        value = json.loads(safe_read_text(path, default="") or "")
    except (json.JSONDecodeError, TypeError, UnicodeError) as exc:
        raise ValueError("corrupt queue.json") from exc
    if not isinstance(value, dict) or not isinstance(value.get("tasks"), list) or any(not isinstance(task, dict) for task in value["tasks"]):
        raise ValueError("corrupt queue.json")
    return value
def _feedback_mappings(queue: dict, tasks: list[dict]) -> dict:
    tracked = queue.get("feedback_manifest") is not None or any(str(t.get("status", "")).casefold() not in _TERMINAL and TRACKED_TASK_FIELDS.intersection(t) for t in tasks)
    if not tracked: return {}
    if queue.get("feedback_manifest") is None: raise ValueError("malformed tracked queue")
    try:
        sys.path.insert(0, str(SCRIPT_REPO_ROOT / "skills/team-lead/scripts")); from feedback_guard import validate_feedback_manifest
        return validate_feedback_manifest(queue["feedback_manifest"], tasks)
    except (ImportError, ValueError, TypeError, KeyError) as exc: raise ValueError(f"malformed tracked queue: {exc}") from exc
def _atomic_write_text(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle: handle.write(text); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try: os.unlink(temporary)
        except OSError: pass
        raise
def _supersede_goal(args: argparse.Namespace) -> int:
    old = Path(args.old_handoff).expanduser()
    if not is_valid_skill_slug(args.new_goal):
        return _fail("--new-goal must be a lowercase skill slug")
    if old.name == args.new_goal:
        return _fail("--new-goal must differ from the old handoff name")
    ledger_path, queue_path = old / "ledger.md", old / "queue.json"
    if not is_safe_local_dir(old) or not is_safe_local_file(ledger_path):
        return _fail(f"old handoff or ledger.md is missing/unreadable: {old}")
    text = safe_read_text(ledger_path)
    if text is None:
        return _fail(f"cannot read ledger.md: {ledger_path}")
    try:
        queue = _load_queue_data(queue_path)
        mappings = _feedback_mappings(queue, queue["tasks"]) if queue is not None else {}
    except ValueError as exc:
        return _fail(str(exc))
    lines, records = text.splitlines(), _task_records(text.splitlines())
    events_heading = next((i for i, line in enumerate(lines) if line.strip().casefold() == "## events"), len(lines))
    tasks_heading = next((i for i, line in enumerate(lines) if line.strip().casefold() == "## tasks"), events_heading)
    ledger_tasks = [record["task_id"] for record in records if record["line_index"] < events_heading and record["status"].strip().casefold() not in _TERMINAL]
    queue_tasks = [str(task.get("task_id", "")) for task in (queue or {}).get("tasks", []) if str(task.get("task_id", "")) and str(task.get("status", "")).casefold() not in _TERMINAL]
    cancelled = list(dict.fromkeys(ledger_tasks + [task_id for task_id in queue_tasks if task_id not in ledger_tasks]))
    has_superseded = any("EVT=GOAL_SUPERSEDED" in line and f"superseded_by={args.new_goal}" in line for line in lines[events_heading:])
    has_done = any("EVT=GOAL_DONE" in line and f"superseded_by={args.new_goal}" in line for line in lines[events_heading:])
    ledger_reconciled = not ledger_tasks and not any(re.match(r"(?i)^\s*[-*]?\s*(?:goal\s+)?status\s*:\s*(?:in_progress|active|planning)\b", line) for line in lines[:tasks_heading])
    queue_reconciled = queue is None or (queue.get("status") == "cancelled" and queue.get("superseded_by_goal") == args.new_goal and not queue_tasks)
    existing = has_superseded and has_done and ledger_reconciled and queue_reconciled
    missing_events = []
    if not has_superseded:
        missing_events.append("EVT=GOAL_SUPERSEDED")
    if not has_done:
        missing_events.append("EVT=GOAL_DONE")
    payload = {"ok": True, "applied": bool(args.apply), "old_goal": old.name, "new_goal": args.new_goal,
               "cancelled_tasks": cancelled, "events_appended": missing_events, "already_superseded": existing}
    if args.apply:
        try:
            if queue is not None and not queue_reconciled:
                auth = f"owner:supersede-goal:{args.new_goal}"
                for task in queue["tasks"]:
                    if str(task.get("status", "")).casefold() in _TERMINAL:
                        continue
                    task["status"], task["note"] = "cancelled", f"superseded_by_goal={args.new_goal}"
                    if mappings:
                        task["cancellation_authorization"] = auth
                        task["acceptance_evidence"] = [{"feedback_id": item["feedback_id"], "acceptance_id": aid, "status": "cancelled", "evidence": [f"Goal superseded by {args.new_goal}"], "authorization": auth} for item in mappings[str(task.get("task_id"))].get("acceptance_mapping", []) for aid in item.get("acceptance_ids", [])]
                queue["status"], queue["superseded_by_goal"] = "cancelled", args.new_goal
                atomic_write_json(queue_path, queue)
            rewritten = list(lines)
            for record in records:
                if record["line_index"] >= events_heading or record["status"].strip().casefold() in _TERMINAL:
                    continue
                parts = rewritten[record["line_index"]].split("|")
                index = record["status_index"] + 1 if rewritten[record["line_index"]].lstrip().startswith("|") else record["status_index"]
                if index < len(parts):
                    parts[index] = " cancelled "
                    rewritten[record["line_index"]] = "|".join(parts)
            for index in range(tasks_heading):
                rewritten[index] = re.sub(r"(?i)^(\s*[-*]?\s*(?:goal\s+)?status\s*:\s*)(?:in_progress|active|planning)\b", r"\1cancelled", rewritten[index])
            timestamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
            reason = str(args.reason).replace(" | ", "; ").replace("\n", " ").strip() or "superseded by M2 pivot"
            next_id = _next_event_id(rewritten)
            if not has_superseded:
                rewritten.append(f"- {timestamp} | EVT=GOAL_SUPERSEDED | id=E{next_id} | note=superseded_by={args.new_goal}; cancelled_tasks={len(cancelled)}; reason={reason}")
            if not has_done:
                rewritten.append(f"- {timestamp} | EVT=GOAL_DONE | id=E{next_id + (0 if has_superseded else 1)} | note=outcome=superseded; superseded_by={args.new_goal}")
            if rewritten != lines or missing_events:
                _atomic_write_text(ledger_path, "\n".join(rewritten) + "\n")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            return _fail(f"cannot apply goal supersession: {exc}")
    _emit(payload, args.json)
    return 0
def _inside(path: Path, root: Path) -> bool:
    try: return path != root and root in path.parents
    except (OSError, ValueError): return False
def _safe_allowlist_root(root: Path, zone: str) -> bool:
    if zone == "tmp": return True
    try:
        parents = [root.parent] if zone == "handoff" else [root.parent, root.parent.parent]
        if root.is_symlink(): target = root.resolve(strict=False)
        if root.is_symlink() and (target == SCRIPT_REPO_ROOT or SCRIPT_REPO_ROOT in target.parents): return False
        return not any(parent.is_symlink() for parent in parents)
    except OSError: return False
def _write_zone(raw: str) -> str | None:
    if ".." in Path(raw.replace("\\", "/")).parts:
        return None
    expanded = Path(raw).expanduser()
    candidate = expanded if expanded.is_absolute() else Path.cwd() / expanded
    roots = [(Path.cwd() / "handoff", "handoff"), (Path.home() / "Development" / "handoff", "handoff"),
             (Path("/tmp"), "tmp"), (Path("/private/tmp"), "tmp"),
             (Path.cwd() / ".copilot" / "session-state", "session-state"),
             (Path.home() / ".copilot" / "session-state", "session-state"),
             (Path("/var/folders"), "tmp"), (Path("/private/var/folders"), "tmp")]
    for root, zone in roots:
        if zone == "tmp" and not expanded.is_absolute(): continue
        if not _inside(candidate, root) or not _safe_allowlist_root(root, zone):
            continue
        try:
            canonical = root.resolve(strict=False)
            resolved = candidate.resolve(strict=False)
        except OSError:
            continue
        if _inside(resolved, canonical):
            return zone
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
