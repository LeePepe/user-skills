"""Lossless, format-budgeted task snapshots and ledger event continuation."""
from __future__ import annotations

from collections import Counter
import json
import re

from skill_runtime import redact_payload, redact_text

OUTPUT_BUDGET = 3850
_TEXT_KEYS = (
    "ok", "goal", "old_goal", "new_goal", "has_delta", "truncated",
    "tasks_truncated", "total_tasks", "omitted_tasks", "task_counts",
    "event_clipped", "remaining_events", "task_offset", "next_task_offset",
    "next_since_line", "event_char_offset", "current_event_id",
    "tasks", "cancelled_tasks", "new_events",
)


def _event_id(line: str) -> str:
    match = re.search(r"\|\s*id=([A-Za-z]+\d+)\b", line)
    return match.group(1) if match else ""


def _render_output(payload: dict, as_json: bool) -> str:
    safe = redact_payload(payload)
    if as_json:
        return json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
    return "\n".join(
        f"{key}={json.dumps(safe[key], ensure_ascii=False, separators=(',', ':'))}"
        for key in _TEXT_KEYS if key in safe
    )


def build_delta(base: dict, tasks: list[dict], candidates: list[tuple[int, int, str]], *,
                task_offset: int, char_offset: int, tail: int, as_json: bool,
                previous_id: str) -> tuple[dict, dict | None]:
    tasks = [{**task, "status": re.sub(r"[\s-]+", "_", task["status"].strip().casefold())}
             for task in tasks]
    counts = dict(Counter(task["status"] for task in tasks))
    tasks.sort(key=lambda task: task["status"] in {"done", "cancelled"})
    task_offset = min(max(0, task_offset), len(tasks))
    available = tasks[task_offset:]

    def payload_for(count: int, chunks: list[str], completed: int, offset: int = 0) -> dict:
        clipped = offset > 0
        next_line = (candidates[completed][0] if clipped else candidates[completed - 1][0] + 1
                     if completed else base["since_line"])
        return {
            **base, "has_delta": bool(chunks), "next_since_line": next_line,
            "truncated": completed < len(candidates) or count < len(tasks),
            "remaining_events": len(candidates) - completed,
            "tasks_truncated": count < len(tasks), "total_tasks": len(tasks),
            "omitted_tasks": len(tasks) - count, "task_counts": counts,
            "task_offset": task_offset,
            "next_task_offset": task_offset + count if task_offset + count < len(tasks) else None,
            "tasks": available[:count], "new_events": chunks, "event_clipped": clipped,
            "event_char_offset": offset,
            "current_event_id": _event_id(candidates[completed][2]) if clipped else "",
        }

    def fits(payload: dict) -> bool:
        return len(_render_output(payload, as_json).encode("utf-8")) <= OUTPUT_BUDGET

    count = min(8, len(available))
    payload = payload_for(count, [], 0)
    if not fits(payload):
        raise ValueError("task snapshot exceeds output budget; shorten task ID/status/owner/PR fields")
    # Reserve room for events without ever sacrificing the minimum task snapshot.
    for size in range(count + 1, len(available) + 1):
        if candidates and len(json.dumps(available[:size], ensure_ascii=False).encode("utf-8")) > 1800:
            break
        trial = payload_for(size, [], 0)
        if not fits(trial):
            break
        count, payload = size, trial

    chunks: list[str] = []
    cursor = None
    for index, row in enumerate(candidates[:tail]):
        # Redact the whole event before slicing so a continuation cannot leak a secret suffix.
        full = redact_text(row[2])
        start = char_offset if index == 0 else 0
        remainder = full[start:]
        trial = payload_for(count, chunks + [remainder], index + 1)
        if fits(trial):
            chunks.append(remainder)
            payload = trial
            cursor = {"last_line": row[0] + 1, "last_event_idx": row[1] + 1,
                      "last_event_id": _event_id(row[2]), "current_event_id": "", "event_char_offset": 0}
            continue
        if chunks:
            break
        low, high = 0, len(remainder) - 1
        while low < high:
            middle = (low + high + 1) // 2
            if fits(payload_for(count, [remainder[:middle]], 0, start + middle)):
                low = middle
            else:
                high = middle - 1
        if low == 0:
            raise ValueError("task snapshot leaves no room for event content; shorten task fields")
        payload = payload_for(count, [remainder[:low]], 0, start + low)
        cursor = {"last_line": row[0], "last_event_idx": row[1], "last_event_id": previous_id,
                  "current_event_id": _event_id(row[2]), "event_char_offset": start + low}
        break
    return payload, cursor
