#!/usr/bin/env python3
"""R03 lossless keep-or-drop compaction for tool output blocks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(SCRIPT_DIR))
from jev_client import jev_decide  # noqa: E402


def _json_value(value: str) -> Any:
    path = Path(value).expanduser()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else json.loads(value)


def deterministic_scores(text: str, later_text: str) -> tuple[float, float]:
    anchor = bool(re.search(r"\b[\w./-]+:\d+\b|\b[0-9a-f]{7,40}\b|\b(?:FAIL|Traceback)\b", text))
    superseded = bool(later_text and ("pass" in later_text.lower() or "passed" in later_text.lower()))
    return (1.0 if anchor else 0.0, 1.0 if superseded else 0.0)


def compact(blocks: list[dict[str, Any]], archive_dir: Path, keep_recent: int, mock: Any = None) -> list[dict[str, Any]]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    result = []
    for index, block in enumerate(blocks):
        block_copy = dict(block)
        text = str(block.get("text", ""))
        if index >= max(0, len(blocks) - keep_recent):
            block_copy["action"] = "keep_recent"
            result.append(block_copy)
            continue
        later = "\n".join(str(item.get("text", "")) for item in blocks[index + 1:])
        block_id = str(block.get("id", "unknown"))
        block_mock = mock.get(block_id) if isinstance(mock, dict) and block_id in mock else mock
        decision = jev_decide(
            text,
            {
                "q_contains_active_anchor": {"type": "noul"},
                "q_superseded_by_later_edit": {"type": "noul"},
            },
            mock_response=block_mock,
        )
        if decision:
            anchor = float(decision.get("q_contains_active_anchor", {}).get("noul", 0.0))
            superseded = float(decision.get("q_superseded_by_later_edit", {}).get("noul", 0.0))
        else:
            anchor, superseded = deterministic_scores(text, later)
        if anchor >= 0.35 and superseded < 0.75:
            block_copy["action"] = "keep"
        else:
            turn = block.get("turn", 0)
            name = f"turn-{turn}-{block_id}.log"
            (archive_dir / name).write_text(text, encoding="utf-8")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            block_copy["text"] = (
                f"[COMPACTED tool_call_id={block_id} lines={len(text.splitlines())} "
                f"sha256={digest} archived_at=archive/{name}]"
            )
            block_copy["action"] = "drop"
        result.append(block_copy)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--archive-dir", required=True)
    parser.add_argument("--keep-recent", type=int, default=3)
    parser.add_argument("--mock-jev-json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    mock = _json_value(args.mock_jev_json) if args.mock_jev_json else None
    result = compact(_json_value(args.input_json), Path(args.archive_dir).expanduser(), args.keep_recent, mock)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
