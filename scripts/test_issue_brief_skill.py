#!/usr/bin/env python3
"""Unit tests for the `issue-brief` skill (`verify_issue_format.py`) and producer integrations."""

from __future__ import annotations

from datetime import date
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, rel_path: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ISSUE_BRIEF = _load_module("verify_issue_format_test", "skills/issue-brief/scripts/verify_issue_format.py")
AUTOPILOT = _load_module("autopilot_dual_zone_test", "skills/retro-loop/scripts/multica_skills_autopilot.py")
DIGEST = _load_module("digest_dual_zone_test", "skills/knowledge-digest/scripts/digest_vault.py")
DAILY_BRIEF = _load_module("daily_brief_dual_zone_test", "skills/linear-board/scripts/daily-brief.py")


class IssueBriefSkillTests(unittest.TestCase):
    def test_render_and_verify_dual_zone_roundtrip(self) -> None:
        md = ISSUE_BRIEF.render_dual_zone_issue(
            tldr="今日巡检发现 2 个高频技能存在重复打回风险，勾选对应项后即可自动下沉脚本检查。",
            items=[
                {"what": "为 `team-lead` 收敛常见漏检", "why": "将高频返工规则固化为确定性脚本检查"},
                {"what": "为 `knowledge-digest` 补齐防重断言", "why": "避免旧状态阻塞每日新选题推荐"},
            ],
            cursor="skills-roi-2026-11-19",
            extra_comments=["<!-- multica-skills-autopilot:skills-roi-2026-11-19 -->"],
            ai_context_md="## Telemetry\n\n| Metric | Value |\n|---|---|\n| `S10` | `0.0199` |\n| `EV-001` | `pass` |",
            ai_payload={"topic_key": "team-lead", "sample_size": 1420},
        )
        report = ISSUE_BRIEF.verify_dual_zone_issue(md)
        self.assertTrue(report["ok"], report)
        self.assertLessEqual(report["human_zone_lines"], 15)
        self.assertEqual(report["checkbox_count"], 2)
        self.assertEqual(report["cursor"], "skills-roi-2026-11-19")
        self.assertIn(ISSUE_BRIEF.CANONICAL_DETAILS_OPEN, md)
        self.assertIn('"sample_size": 1420', md)

    def test_verify_catches_human_zone_line_budget_and_noise_violations(self) -> None:
        noisy_lines = [
            "<!-- multica-cursor:bad-case -->",
            "",
            "**一句话结论**：S10 与 M2 指标告警，关联提交 a1b2c3d4 与 1a895cfa-3ed5-45f5-aaf1-a24b10b502fc。",
            "",
            "| Skill | Score |",
            "|---|---|",
            "| team-lead | EV-001 / DC-002 / H3 / R10 / V2 |",
            "",
            "```bash",
            "python3 skills/retro-loop/scripts/multica_skills_autopilot.py --dry-run",
            "```",
            *[f"- [ ] {idx}. 额外人类区噪音行 {idx}" for idx in range(1, 12)],
            "",
            ISSUE_BRIEF.CANONICAL_DETAILS_OPEN,
            "context",
            "</details>",
        ]
        report = ISSUE_BRIEF.verify_dual_zone_issue("\n".join(noisy_lines))
        self.assertFalse(report["ok"])
        codes = {v["code"] for v in report["violations"]}
        self.assertIn("human-zone-line-budget", codes)
        self.assertIn("human-zone-internal-code", codes)
        self.assertIn("human-zone-uuid", codes)
        self.assertIn("human-zone-fenced-code", codes)
        self.assertIn("human-zone-cli-command", codes)
        self.assertIn("human-zone-markdown-table", codes)

    def test_verify_catches_commit_sha_missing_cursor_and_unbalanced_details(self) -> None:
        bad_md = (
            "没有结论前缀，直接泄露 9d8cd4c9f012 提交号。\n\n"
            "<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>\n"
            "only open tag without closing tag\n"
        )
        report = ISSUE_BRIEF.verify_dual_zone_issue(bad_md)
        self.assertFalse(report["ok"])
        codes = {v["code"] for v in report["violations"]}
        self.assertIn("missing-cursor", codes)
        self.assertIn("missing-tldr", codes)
        self.assertIn("missing-checkable-items", codes)
        self.assertIn("unbalanced-details", codes)
        self.assertIn("human-zone-commit-sha", codes)

    def test_cli_render_and_verify_exit_codes(self) -> None:
        script = ROOT / "skills" / "issue-brief" / "scripts" / "verify_issue_format.py"
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = Path(tmp) / "input.json"
            md_path = Path(tmp) / "rendered.md"
            payload_path.write_text(
                json.dumps(
                    {
                        "tldr": "今日新增 2 个待确认事项，勾选后即可由后台自动执行。",
                        "items": [
                            {"what": "清理过期分支工作树", "why": "释放本地磁盘并消除无意义巡检"},
                            {"what": "补齐技能契约回归用例", "why": "防止相同格式缺陷再次上线"},
                        ],
                        "cursor": "cli-test-2026-11-19",
                        "ai_context_md": "- `cmd`: `./scripts/verify`",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            render_proc = subprocess.run(
                [sys.executable, str(script), "render", "--input", str(payload_path), "--output", str(md_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(render_proc.returncode, 0, render_proc.stderr)
            verify_proc = subprocess.run(
                [sys.executable, str(script), "verify", str(md_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(verify_proc.returncode, 0, verify_proc.stdout)
            parsed = json.loads(verify_proc.stdout)
            self.assertTrue(parsed["ok"])

            bad_proc = subprocess.run(
                [sys.executable, str(script), "verify", "-"],
                input="Plain unformatted body without details or cursor\n",
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_proc.returncode, 2)
            bad_parsed = json.loads(bad_proc.stdout)
            self.assertFalse(bad_parsed["ok"])

    def test_multica_skills_autopilot_renders_dual_zone_and_supports_update_issue_id(self) -> None:
        raw = {
            "ok": True,
            "wall_clock_date": "2026-11-18",
            "window_cst": "7d",
            "source_as_of": "2026-11-18T22:52:43+08:00",
            "sample_size": 31751,
            "coverage_status": "healthy",
            "S1_pin_drift": {"outdated_pin_count": 1, "all_up_to_date": False},
            "S3_skill_health": {"total_skills": 30, "audit_pass_count": 30, "audit_pass_rate": 1.0},
            "S2_gate_catch": {"eval_effect": {"interceptions_7d": 10, "escapes_7d": 2}},
            "S4_usage_and_r1": {"active_skills_count": 15, "idle_skills_30d": [], "empty_load_rate": 0.02},
            "S6_load_vs_script": [],
            "S7_disclosure_funnel": [],
            "S9_escape_attribution": [{"skill": "team-lead", "impl_bug_escapes": 4, "spec_gap_escapes": 6}],
            "S10_skill_roi_ranking": [
                {
                    "skill": "team-lead",
                    "diagnostic_status": "high_repair_loop",
                    "roi_score": 0.0199,
                    "r1_pass_rate": 0.667,
                    "loads_30d": 100,
                    "script_execs": 80,
                    "unpracticed_rate": 0.2,
                    "impl_bug_escapes": 4,
                    "spec_gap_escapes": 6,
                    "diagnosis": "[IMPL_BUG] 漏网 4 次；[SPEC_GAP] 盲区 6 次",
                    "needs_optimization": True,
                }
            ],
            "S12_tool_context_burn": [],
            "recommendations": [],
        }
        snap = AUTOPILOT.collect_skills_roi_snapshot(diagnostics_fn=lambda **_kw: raw)
        report = ISSUE_BRIEF.verify_dual_zone_issue(snap["markdown_brief"])
        self.assertTrue(report["ok"], report)
        self.assertLessEqual(report["human_zone_lines"], 15)

        calls: list[list[str]] = []

        def fake_runner(args: list[str], *, stdin_text: str | None = None, **_kw):
            calls.append(args)
            self.assertIsNotNone(stdin_text)
            self.assertTrue(ISSUE_BRIEF.verify_dual_zone_issue(stdin_text or "")["ok"])
            return subprocess.CompletedProcess(args, 0, json.dumps({"id": "tia-1-id", "identifier": "TIA-1"}), "")

        res = AUTOPILOT.file_actionable_issue(
            snap,
            update_issue_id="TIA-1",
            runner=fake_runner,
        )
        self.assertEqual(res["action"], "updated_existing")
        self.assertEqual(calls[0][:3], ["issue", "update", "TIA-1"])

    def test_digest_vault_dual_zone_digest_issue_passes_verifier(self) -> None:
        topics = [
            {
                "topic_key": "swift-concurrency-isolation",
                "title": "Swift 并发隔离如何消除跨线程状态竞争？",
                "question": "Swift 并发隔离如何消除跨线程状态竞争？",
                "source_id": "dimillian-skills",
                "source_uri": "https://example.test/swift",
                "why_now": "近期客户端异步任务频繁涉及隔离边界校验",
            },
            {
                "topic_key": "spring-interrupt-velocity",
                "title": "手势中途打断时如何保持弹簧动画速度连续？",
                "question": "手势中途打断时如何保持弹簧动画速度连续？",
                "source_id": "emil-skills",
                "source_uri": "https://example.test/motion",
                "why_now": "交互卡片切换需要零顿挫跟手过渡",
            },
        ]
        for t in topics:
            t["learning_brief"] = DIGEST._learning_brief(t)
        md = DIGEST._daily_digest_markdown("2026-11-19", topics, dual_zone=True)
        report = ISSUE_BRIEF.verify_dual_zone_issue(md)
        self.assertTrue(report["ok"], report)
        self.assertLessEqual(report["human_zone_lines"], 15)
        self.assertIn("<!-- KD-DAILY-START:2026-11-19 -->", md)
        self.assertIn("<!-- KD-DAILY-END:2026-11-19 -->", md)
        self.assertEqual(DIGEST._daily_description_keys(md), {"swift-concurrency-isolation", "spring-interrupt-velocity"})

    def test_daily_brief_dual_zone_flag_and_verifier(self) -> None:
        sample_issues = [
            {
                "id": "TIA-10",
                "identifier": "TIA-10",
                "title": "确认新版色板方案",
                "url": "https://linear.app/tianpeili/issue/TIA-10",
                "description": "",
                "state": {"name": "Todo", "type": "unstarted"},
                "priority": 1,
                "project": {"name": "AIDash"},
                "team": {"id": "tia", "key": "TIA", "name": "Personal"},
                "parent": None,
                "labels": {"nodes": [{"name": "Needs Decision"}]},
            }
        ]
        plan_a_md, todos = DAILY_BRIEF.render_brief(date(2026, 11, 19), sample_issues, [], [], [])
        self.assertEqual(DAILY_BRIEF.validate_markdown(plan_a_md), [])

        dual_md = DAILY_BRIEF.render_dual_zone_brief(
            date(2026, 11, 19),
            plan_a_md,
            todos,
            rollup_counts={"linear": 0, "commits": 2, "prs": 1, "events": 0},
        )
        report = ISSUE_BRIEF.verify_dual_zone_issue(dual_md)
        self.assertTrue(report["ok"], report)
        self.assertLessEqual(report["human_zone_lines"], 15)
        self.assertIn("<!-- GTT-LINEAR-DEDUP:daily-brief-2026-11-19 -->", dual_md)

        with tempfile.TemporaryDirectory() as tmp:
            md_file = Path(tmp) / "dual-brief.md"
            md_file.write_text(dual_md, encoding="utf-8")
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                rc = DAILY_BRIEF.main(["--dual-zone", "--verify-markdown", str(md_file)])
            self.assertEqual(rc, 0)
            self.assertIn("Markdown verification passed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
