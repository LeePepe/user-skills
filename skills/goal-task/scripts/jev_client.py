#!/usr/bin/env python3
"""Small fail-open client for the Falcon/Raven Jev decision endpoint."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    path = Path(value).expanduser()
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def jev_decide(
    state: str | dict[str, Any] | list[Any],
    questions: dict[str, dict[str, Any]],
    *,
    source_key: str | None = None,
    agent_role: str = "goal-task",
    goal_id: str = "",
    model: str = "microsoft-decision-1",
    timeout_sec: float = 1.5,
    mock_response: Any = None,
) -> dict[str, Any] | None:
    try:
        candidate = mock_response if mock_response is not None else os.environ.get("GTT_JEV_MOCK_JSON")
        if candidate:
            result = _json_value(candidate)
            return result if isinstance(result, dict) else None
    except (OSError, ValueError, TypeError):
        return None
    payload = {"model": model, "state": state, "questions": questions}
    if source_key:
        payload["source_key"] = source_key
    headers = {
        "Content-Type": "application/json",
        "X-Falcon-Agent": agent_role,
        "X-Falcon-Run-ID": goal_id,
        "Authorization": "******",
    }
    bases = (
        os.environ.get("FALCON_BASE_URL", "http://127.0.0.1:19823"),
        os.environ.get("RAVEN_BASE_URL", "http://127.0.0.1:7024"),
    )
    for base in bases:
        try:
            request = urllib.request.Request(
                base.rstrip("/") + "/v1/systemone",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                result = json.loads(response.read().decode("utf-8"))
            return result if isinstance(result, dict) else None
        except (OSError, ValueError, TypeError):
            continue
    return None


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--mock-jev-json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = jev_decide(args.state, _json_value(args.questions), mock_response=args.mock_jev_json)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
