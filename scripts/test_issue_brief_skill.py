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
            *[f"- [ ] {idx}. 额外人类区噪音行 {idx} — 具体原因 #{idx}" for idx in range(1, 42)],
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

        forbid_table_report = ISSUE_BRIEF.verify_dual_zone_issue(
            "\n".join(noisy_lines),
            allow_compact_table=False,
        )
        self.assertIn("human-zone-markdown-table", {v["code"] for v in forbid_table_report["violations"]})

    def test_verify_allows_rich_tia15_tables_and_rejects_duplicate_boilerplate_items(self) -> None:
        tia15_rich = (
            "<!-- multica-cursor:loop-financial-rebalance-pulse-2026-11-20 -->\n\n"
            "**核心结论**：当前组合**科技进攻仓位占比 83.39%（¥86,358.24，较目标 35% 超配 +48.39%）**，且 **8 天后（11月28日）有 3 笔 C 类份额满 30 天免赎回费解锁**。\n\n"
            "### 1. 全天候五象限仓位偏离表（实盘净值）\n"
            "| 象限 | 当前市值 (CNY) | 实际占比 | 目标占比 | 偏离度 |\n"
            "|---|---:|---:|---:|---:|\n"
            "| **科技进攻** | ¥86,358.24 | **83.39%** | 35.0% | **+48.39%** |\n"
            "| **宽基指数** | ¥17,198.75 | **16.61%** | 30.0% | -13.39% |\n\n"
            "### 2. 近期即将满期 0 赎回费解锁批次（FIFO 精确份额）\n"
            "| 基金代码 | 确认日期 | 剩余份额 | **0 费率解锁日** | 距离解锁 |\n"
            "|---|---|---:|---|---:|\n"
            "| **017811** | 2026-10-29 | **800.00 份** (≈¥1,969) | **2026-11-28** | **8 天** |\n\n"
            "### 3. 待你拍板的执行项\n"
            "- [ ] **1. 停投超配科技定投** — 将月度 ¥10,000 定投预算从科技进攻切换至黄金（15%）+ 红利低波（10%）\n"
            "- [ ] **2. 11月28日解锁批次预约提醒** — `017811`（800份）满 30 天免赎回费后评估调仓至防守象限\n\n"
            f"{ISSUE_BRIEF.CANONICAL_DETAILS_OPEN}\n"
            "- `source`: `Financial ledger`\n"
            "</details>\n"
        )
        rich_report = ISSUE_BRIEF.verify_dual_zone_issue(tia15_rich)
        self.assertTrue(rich_report["ok"], rich_report)
        self.assertEqual(rich_report["max_human_lines"], 45)
        self.assertLessEqual(rich_report["human_zone_lines"], 45)

        tia5_bad = (
            "<!-- multica-cursor:spec-code-drift-patrol-bad -->\n\n"
            "**一句话结论**：检测到漂移。\n\n"
            "- [ ] 1. 同步规格文档（VitalStride） — Specification references a path absent from the main snapshot\n"
            "- [ ] 2. 同步规格文档（VitalStride） — Specification references a path absent from the main snapshot\n\n"
            f"{ISSUE_BRIEF.CANONICAL_DETAILS_OPEN}\nctx\n</details>\n"
        )
        tia5_res = ISSUE_BRIEF.verify_dual_zone_issue(tia5_bad)
        self.assertFalse(tia5_res["ok"])
        self.assertIn("duplicate_boilerplate_items", {v["code"] for v in tia5_res["violations"]})

        tia14_bad = (
            "<!-- multica-cursor:skills-roi-bad -->\n\n"
            "**一句话结论**：检测到 3 个技能需优化。\n\n"
            "- [ ] 1. 收敛技能 team-lead 的触发与回归门禁 — 降低返工率与无效上下文消耗\n"
            "- [ ] 2. 收敛技能 howto 的触发与回归门禁 — 降低返工率与无效上下文消耗\n"
            "- [ ] 3. 收敛技能 update-swiftui-apis 的触发与回归门禁 — 降低返工率与无效上下文消耗\n\n"
            f"{ISSUE_BRIEF.CANONICAL_DETAILS_OPEN}\nctx\n</details>\n"
        )
        tia14_res = ISSUE_BRIEF.verify_dual_zone_issue(tia14_bad)
        self.assertFalse(tia14_res["ok"])
        self.assertIn("duplicate_boilerplate_items", {v["code"] for v in tia14_res["violations"]})

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
                "topic_key": "bama-agent-cli-story-verification",
                "title": "BAMA 如何把页面清单转为免 GUI 可验收用户故事？",
                "question": "BAMA 如何把页面清单转为免 GUI 可验收用户故事？",
                "source_id": "bama",
                "source_uri": "https://example.test/bama",
                "why_now": "自动化工作流与多角色验收门禁需要确定性故事切片",
                "track": "workflow",
            },
            {
                "topic_key": "swift-concurrency-isolation",
                "title": "Swift 并发隔离如何消除跨线程状态竞争？",
                "question": "Swift 并发隔离如何消除跨线程状态竞争？",
                "source_id": "dimillian-skills",
                "source_uri": "https://example.test/swift",
                "why_now": "近期客户端异步任务频繁涉及隔离边界校验",
                "track": "personal",
            },
            {
                "topic_key": "spring-interrupt-velocity",
                "title": "手势中途打断时如何保持弹簧动画速度连续？",
                "question": "手势中途打断时如何保持弹簧动画速度连续？",
                "source_id": "emil-skills",
                "source_uri": "https://example.test/motion",
                "why_now": "交互卡片切换需要零顿挫跟手过渡",
                "track": "personal",
            },
        ]
        for t in topics:
            t["learning_brief"] = DIGEST._learning_brief(t)
        md = DIGEST._daily_digest_markdown("2026-11-19", topics, dual_zone=True)
        report = ISSUE_BRIEF.verify_dual_zone_issue(md)
        self.assertTrue(report["ok"], report)
        self.assertLessEqual(report["human_zone_lines"], 15)
        self.assertEqual(report["checkbox_count"], 3)
        self.assertIn("**【板块一 · Workflow 相关学习】**", md)
        self.assertIn("**【板块二 · 个人成长与领域新能力】**", md)
        self.assertIn("- [ ] 1. [bama-agent-cli-story-verification]", md)
        self.assertIn("- [ ] 2. [swift-concurrency-isolation]", md)
        self.assertIn("- [ ] 3. [spring-interrupt-velocity]", md)
        self.assertIn("<!-- KD-DAILY-START:2026-11-19 -->", md)
        self.assertIn("<!-- KD-DAILY-END:2026-11-19 -->", md)
        self.assertEqual(
            DIGEST._daily_description_keys(md),
            {"bama-agent-cli-story-verification", "swift-concurrency-isolation", "spring-interrupt-velocity"},
        )

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
