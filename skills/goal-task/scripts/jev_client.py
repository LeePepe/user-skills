#!/usr/bin/env python3
"""Small fail-open client for the Falcon/Raven Jev decision endpoint."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any


AUTH_ENV_VARS = (
    "FALCON_SOURCE_KEY",
    "FALCON_API_KEY",
    "JEV_API_KEY",
    "RAVEN_API_KEY",
    "RAVEN_INTERNAL_API_KEY",
)
AUTH_KEY_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "bearer_token",
    "client_key",
    "key",
    "local_gateway_client_key",
    "source_key",
    "token",
}


def _native_questions(questions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Translate legacy option lists to Raven's native Choice criteria map."""
    formatted: dict[str, dict[str, Any]] = {}
    for name, question in questions.items():
        native = dict(question)
        if native.get("type") == "choice" and "criteria" not in native:
            choices = native.pop("options", native.pop("candidates", None))
            if isinstance(choices, list) and choices:
                native["instructions"] = native.get("instructions") or native.pop(
                    "prompt", "Choose one option."
                )
                native["criteria"] = {str(choice): str(choice) for choice in choices}
        formatted[name] = native
    return formatted


def _read_local_auth_values(directory: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not directory.is_dir():
        return values
    for path in directory.rglob("*"):
        if not path.is_file() or path.name.endswith((".lock", ".bak")):
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            if path.name in {"local-gateway-client-key", "api-key", "api_key", "token"}:
                values[path.name] = text
            continue
        stack: list[Any] = [parsed]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for key, value in item.items():
                    normalized = str(key).lower().replace("-", "_")
                    if normalized in AUTH_KEY_NAMES and isinstance(value, str) and value.strip():
                        values[normalized] = value.strip()
                    else:
                        stack.append(value)
            elif isinstance(item, list):
                stack.extend(item)
    return values


def _read_skill_source_key(directory: Path, agent_role: str) -> str | None:
    path = directory / "source-keys.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(agent_role)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _resolve_auth_token(
    base_url: str, source_key: str | None = None, *, agent_role: str = "goal-task"
) -> tuple[str | None, str | None]:
    """Resolve local Jev credentials without embedding secrets in the repository."""
    host_port = base_url.lower()
    is_falcon = ":19823" in host_port or "falcon" in host_port
    env = os.environ
    if is_falcon:
        token = next((env.get(name) for name in ("FALCON_API_KEY", "JEV_API_KEY") if env.get(name)), None)
        resolved_source_key = source_key or env.get("FALCON_SOURCE_KEY")
        falcon_dir = Path.home() / ".config" / "falcon"
        values = _read_local_auth_values(falcon_dir)
        token = token or values.get("api_key") or values.get("apikey") or values.get("token")
        resolved_source_key = resolved_source_key or values.get("source_key")
        resolved_source_key = resolved_source_key or _read_skill_source_key(falcon_dir, agent_role)
        token = token or resolved_source_key
        return token, resolved_source_key

    token = next(
        (env.get(name) for name in ("RAVEN_API_KEY", "RAVEN_INTERNAL_API_KEY", "JEV_API_KEY") if env.get(name)),
        None,
    )
    values = _read_local_auth_values(Path.home() / ".config" / "raven")
    token = token or values.get("api_key") or values.get("apikey") or values.get("local_gateway_client_key")
    token = token or values.get("client_key") or values.get("token")
    if token is None:
        magpie_values = _read_local_auth_values(Path.home() / ".config" / "magpie")
        token = magpie_values.get("api_key") or magpie_values.get("client_key") or magpie_values.get("token")
    return token, None


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    path = Path(value).expanduser()
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def _unittest_active() -> bool:
    if "unittest" in sys.modules:
        return True
    try:
        parent = subprocess.run(
            ["ps", "-p", str(os.getppid()), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return "unittest" in parent.stdout


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
    if _unittest_active() and os.environ.get("GTT_JEV_LIVE") != "1":
        return None
    payload = {"model": model, "state": state, "questions": _native_questions(questions)}
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
            token, resolved_source_key = _resolve_auth_token(
                base, source_key, agent_role=agent_role
            )
            if not token:
                continue
            request_headers = dict(headers)
            request_headers["Authorization"] = f"Bearer {token}"
            if resolved_source_key:
                request_headers["X-Falcon-Source-Key"] = resolved_source_key
            request = urllib.request.Request(
                base.rstrip("/") + "/v1/systemone",
                data=json.dumps(payload).encode("utf-8"),
                headers=request_headers,
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
