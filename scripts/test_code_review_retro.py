from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVAL_CASES = ROOT / "skills/code-review/references/eval-cases.json"
PLUGIN = ROOT / "skills/code-review/.claude-plugin/plugin.json"


class CodeReviewRetroTests(unittest.TestCase):
    def test_eval_cases_keep_counter_case_ratio_above_threshold(self) -> None:
        document = json.loads(EVAL_CASES.read_text(encoding="utf-8"))
        cases = document["active_cases"]
        counter_cases = [
            case
            for case in cases
            if any(str(case.get(key) or "").strip() for key in ("bad_snippet", "forbidden_regex", "negative_cmd"))
            or case.get("expect_fail_exit") is not None
            or case.get("expect_exit") not in (None, 0)
            or case.get("should_trigger") is False
        ]
        self.assertGreaterEqual(len(counter_cases) / len(cases), 0.25)
        self.assertEqual(next(case["id"] for case in cases if case["id"] == "EV-006"), "EV-006")
        self.assertEqual(next(case["id"] for case in cases if case["id"] == "EV-007"), "EV-007")

    def test_plugin_version_is_evolution_watermark(self) -> None:
        plugin = json.loads(PLUGIN.read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], "1.1.1")


if __name__ == "__main__":
    unittest.main()
