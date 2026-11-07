#!/usr/bin/env python3
"""R02 deterministic skill selection with an optional Jev truth table."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from jev_client import jev_decide  # noqa: E402


def _json_value(value: str) -> Any:
    path = Path(value).expanduser()
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else json.loads(value)


def fallback_skills(title: str, paths: list[str], brief: str) -> list[str]:
    text = " ".join([title, brief, *paths])
    lower = text.lower()
    if any("chart" in path.lower() or "chart" in lower for path in paths):
        return ["guide-swiftui-charts", "my-designer"]
    if any(token in text for token in ("@MainActor", "actor", "Sendable", "async")):
        return ["guide-swift-concurrency", "swift-concurrency"]
    if any(token in text for token in ("@Model", "SwiftData", "ModelContext")):
        return ["guide-swiftdata", "swiftdata"]
    if any(path.endswith(".swift") for path in paths) and any(
        token in lower for token in ("ui", "view", "swiftui", "layout")
    ):
        return ["swiftui", "my-designer"]
    return []


def route(title: str, paths: list[str], brief: str, mock: Any, max_skills: int) -> dict[str, Any]:
    candidates = {
        "guide-swiftui-charts": "SwiftUI Charts and data visualization",
        "swift-concurrency": "Swift 6 actors, Sendable, and async boundaries",
        "guide-swiftdata": "SwiftData schemas and persistence",
        "motion-studio": "Web or Remotion animation and video rendering",
        "swiftui": "SwiftUI interface and layout",
        "my-designer": "UI visual design and interaction",
    }
    state = {"task_title": title, "owned_paths": paths, "brief": brief, "candidate_skills": candidates}
    questions = {
        "primary_skill": {"type": "choice", "candidates": list(candidates)},
        "needs_specialist_skill": {"type": "noul"},
        **{f"companion_{name}": {"type": "noul"} for name in candidates},
    }
    decision = jev_decide(state, questions, mock_response=mock)
    if decision is None:
        return {"selected_skills": fallback_skills(title, paths, brief), "decision_source": "deterministic_fallback"}
    specialist = decision.get("needs_specialist_skill", {})
    specialist_score = float(specialist.get("noul", 0.0)) if isinstance(specialist, dict) else 0.0
    primary = decision.get("primary_skill", {})
    choice = primary.get("choice") if isinstance(primary, dict) else None
    confidence = float(primary.get("confidence", 0.0)) if isinstance(primary, dict) else 0.0
    if specialist_score < 0.30:
        selected: list[str] = []
    elif specialist_score >= 0.70 and confidence >= 0.40 and choice:
        selected = [str(choice)]
        companions = []
        for name, value in decision.items():
            if not isinstance(value, dict):
                continue
            if float(value.get("noul", 0.0)) > 0.75:
                if name.startswith("companion_"):
                    companions.append(name.removeprefix("companion_"))
                elif "concurr" in name:
                    companions.append("swift-concurrency")
                elif "design" in name:
                    companions.append("my-designer")
        selected.extend(companion for companion in companions if companion not in selected)
        selected = selected[:max_skills]
    else:
        selected = fallback_skills(title, paths, brief)
        return {"selected_skills": selected[:max_skills], "decision_source": "deterministic_fallback"}
    return {"selected_skills": selected[:max_skills], "decision_source": "jev_truth_table"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--paths", required=True)
    parser.add_argument("--brief-text", default="")
    parser.add_argument("--mock-jev-json")
    parser.add_argument("--max-skills", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    paths = [item for item in args.paths.replace(",", " ").split() if item]
    mock = _json_value(args.mock_jev_json) if args.mock_jev_json else None
    result = route(args.title, paths, args.brief_text, mock, max(0, args.max_skills))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
