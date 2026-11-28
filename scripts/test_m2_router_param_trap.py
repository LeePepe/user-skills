from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = ROOT / "skills/personal-m2-assistant/scripts/m2-router.py"


def load_router():
    spec = importlib.util.spec_from_file_location("personal_m2_router_param_trap", ROUTER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class M2RouterParamTrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.router = load_router()

    def test_rich_instruction_survives_plan_and_stdout_apply(self):
        instruction = (
            "Update skills/my-designer/SKILL.md, preserve the native preview boundary, "
            "and validate acceptance item 1 before reporting the result."
        )
        plan = self.router.build_plan(
            "fix it",
            [],
            targets_json={"targets": [{"topic": "my-designer", "instruction": instruction}]},
            transport="stdout",
        )
        self.assertEqual(plan["targets"][0]["sub_prompt"], instruction)
        result = self.router.apply_plan(plan, approve=True, transport="stdout")
        self.assertEqual(
            result["receipts"][0]["prompt"],
            f"{instruction}\n\n<!-- herdr-origin:v1 kind=human_routed sender=m3 topic=skills-design -->",
        )

    def test_short_sub_prompt_and_rich_instruction_are_merged(self):
        instruction = "Inspect skills/personal-m2-assistant/scripts/m2_router_core/plan_builder.py and update acceptance item 1."
        plan = self.router.build_plan(
            "fix it",
            [],
            targets_json={"targets": [{
                "topic": "my-designer",
                "sub_prompt": "Update the route",
                "instruction": instruction,
            }]},
            transport="stdout",
        )
        prompt = plan["targets"][0]["sub_prompt"]
        self.assertIn("Update the route", prompt)
        self.assertIn(instruction, prompt)
        self.assertEqual(prompt.count(instruction), 1)

    def test_short_contextless_prompt_warns_and_strict_mode_rejects(self):
        plan = self.router.build_plan(
            "fix it",
            [],
            targets_json={"targets": [{"topic": "my-designer", "sub_prompt": "fix it"}]},
            transport="stdout",
        )
        self.assertEqual(plan["targets"][0]["prompt_completeness_warning"]["reason"], "under_specified_sub_prompt")
        self.assertTrue(plan["prompt_warnings"])
        self.assertIn("## ⚠️ Handoff Context Completeness Warnings", plan["preview_markdown"])
        with self.assertRaisesRegex(self.router.RouterError, "strict prompt completeness guard"):
            self.router.build_plan(
                "fix it",
                [],
                targets_json={"targets": [{"topic": "my-designer", "sub_prompt": "fix it"}]},
                transport="stdout",
                strict_prompts=True,
            )

    def test_phase0_discussion_is_exempt_from_prompt_warning(self):
        plan = self.router.build_plan(
            "先聊聊有哪些方案，不改代码",
            [],
            targets_json={"targets": [{"topic": "my-designer", "sub_prompt": "先聊聊有哪些方案，不改代码"}]},
            transport="stdout",
            strict_prompts=True,
        )
        self.assertTrue(plan["phase0_discussion_only"])
        self.assertEqual(plan["prompt_warnings"], [])

    def test_domain_aliases_map_to_authoritative_topics(self):
        cases = [
            ("domain_key", "my-designer", "skills-design", "skills-design", "user-skills"),
            ("space_name", "my-designer", "skills-design", "skills-design", "user-skills"),
            ("topic", "China bugs 修复", "sapphire-china-bugs", "China bugs 修复", "bing-growth-sapphire-app"),
            ("domain_key", "agent-retro", "skills-retro-story", "skills-retro-story", "user-skills"),
        ]
        for field, value, topic_id, space, target_repo in cases:
            with self.subTest(field=field, value=value):
                plan = self.router.build_plan(
                    "route work",
                    [],
                    targets_json={"targets": [{field: value, "instruction": "Inspect skills/agent-retro/SKILL.md and report acceptance item 1."}]},
                    transport="stdout",
                )
                target = plan["targets"][0]
                self.assertEqual(target["topic_id"], topic_id)
                self.assertEqual(target["space"], space)
                self.assertEqual(target["target_repo"], target_repo)
                self.assertEqual(target["domain_status"], "MAPPED_DOMAIN")


if __name__ == "__main__":
    unittest.main()
