import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCHDOG_PATH = ROOT / "skills/team-lead/scripts/autopilot_watchdog.py"
M2_SYMLINK_PATH = ROOT / "skills/m2-dispatcher/scripts/autopilot_watchdog.py"
LOOP_RUNNER_PATH = ROOT / "skills/team-lead/scripts/multica_loop_runner.py"
ISSUE_BRIEF_PATH = ROOT / "skills/issue-brief/scripts/verify_issue_format.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


watchdog = _load("test_autopilot_watchdog_mod", WATCHDOG_PATH)
loop_runner = _load("test_autopilot_watchdog_loop_runner_mod", LOOP_RUNNER_PATH)
issue_brief = _load("test_autopilot_watchdog_issue_brief_mod", ISSUE_BRIEF_PATH)


SKILL_RETRO_AP = {
    "id": "4e06bf2a-5842-4b3a-928f-fcde3bea7c41",
    "title": "Daily Skill Progressive Retro Autopilot (Auto-Fix + Daily Brief Digest)",
    "status": "active",
}
AGENT_RETRO_AP = {
    "id": "agent-retro-2026",
    "title": "Daily Agent Trajectory Retro Autopilot (Auto-Fix + M3 Issue)",
    "status": "active",
}
DAILY_LEARN_AP = {
    "id": "8b4d6d04-cc45-4565-9351-a3e8b07488c1",
    "title": "Daily Knowledge Digest Autopilot (3 Personal -> Blog + 3 Agent -> Vault & Owner Fusion Gate)",
    "status": "active",
}
AIDATA_AP = {
    "id": "27cd6086-16f1-4eaf-b770-aea440a46b13",
    "title": "Daily AIDash Digest & Retro Improvements Autopilot (P3 -> Owner)",
    "status": "active",
}


def _healthy_aidata_issue(
    date_str: str = "2026-11-22",
    *,
    degraded: bool = False,
) -> dict:
    if degraded:
        desc = (
            f"<!-- multica-skills-autopilot:{date_str} -->\n"
            f"<!-- multica-cursor:skills-roi-autopilot-{date_str} -->\n\n"
            "**一句话结论**：今日采集链路有降级，且审核返工、网关首字延迟与 Steering 占比超预算。\n\n"
            "- [ ] 1. 日报流水线 — `multica_issue` 无权限、`bot_tl`/`release_tf` 超时，补 bounded timeout。\n\n"
            "<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>\n\n"
            "- Skill ROI 快照明确为 `coverage_status=missing`（producer exit 143）。\n"
            "</details>"
        )
    else:
        desc = (
            f"<!-- multica-skills-autopilot:{date_str} -->\n"
            f"<!-- multica-cursor:skills-roi-autopilot-{date_str} -->\n\n"
            "**一句话结论**：今日 AIDash 数仓采集、L2/L3 合并与 Skills ROI (S1–S12) 全绿。\n\n"
            "- [ ] 1. 保持当前数仓采集与每日简报节奏 — 全源健康\n\n"
            "<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>\n\n"
            f"- **Dedup Marker**: `skills-roi-autopilot-{date_str}` · `coverage_status=healthy`\n"
            "</details>"
        )
    return {
        "id": "issue-aidata-2026-11-22",
        "identifier": "TIA-24",
        "title": f"[P2 / Priority 2][AIDash 日报 & Retro] {date_str} 7d 效能脉搏、数据契约缺口与 Top 3 技能进化处方",
        "status": "todo",
        "created_at": "2026-11-21T20:32:30Z",
        "updated_at": "2026-11-21T20:32:30Z",
        "description": desc,
    }


def _healthy_skill_retro_issue(date_str: str = "2026-11-22", *, sessions: int = 18, coverage: str = "healthy") -> dict:
    return {
        "id": "issue-sr-2026-11-22",
        "identifier": "TIA-25",
        "title": f"[P3 / Priority 3][Skill Retro] {date_str} 技能四层渐进式自演进诊断 (workspace skills)",
        "status": "backlog",
        "created_at": "2026-11-21T20:45:10Z",
        "updated_at": "2026-11-21T20:45:10Z",
        "description": (
            f"<!-- multica-skills-autopilot:skill-retro-autopilot-{date_str} -->\n"
            f"<!-- multica-cursor:skill-retro-autopilot-{date_str} -->\n\n"
            "**一句话结论**：今日技能四层渐进式自演进巡检已完成。\n\n"
            "- [ ] 1. 保持现有技能四层契约门禁与零成本巡检 — 维持日常自动观测即可\n\n"
            "<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>\n\n"
            f"- **Dedup Marker**: `skill-retro-autopilot-{date_str}`\n"
            f"- **Observation Cohort**: `30d` · `sessions={sessions}` · `slow_flywheel_ready=False` · `coverage={coverage}`\n"
            "</details>"
        ),
    }


def _healthy_daily_learn_issue(date_str: str = "2026-11-22") -> dict:
    return {
        "id": "issue-dl-2026-11-22",
        "identifier": "TIA-28",
        "title": f"[Daily Learn {date_str}] 个人阅读 3 篇 (已发 Blog) + Agent 专项资料积累 3 项 (待拍板融合)",
        "status": "todo",
        "created_at": "2026-11-21T20:20:00Z",
        "updated_at": "2026-11-21T20:20:00Z",
        "description": (
            f"<!-- multica-knowledge-autopilot:daily-learn-{date_str} -->\n"
            f"<!-- multica-cursor:daily-knowledge-{date_str} -->\n\n"
            "**一句话结论**：今日 3 篇个人文章已上线 Blog，Agent 专项新增 3 份资料卡。\n\n"
            "- [ ] 1. [为什么美债实际利率飙升，黄金却创历史新高？](https://leepepe.github.io/learn/all-weather-gold/) — 央行购金定价机制拆解\n"
            "- [ ] 2. [Expo Eval Harness：Skill 三层有效性评测](https://github.com/expo/eval-harness)（`concepts/expo-eval-harness.md`） — 融合进 `skill-contract`\n\n"
            "<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>\n"
            "- `concepts/expo-eval-harness.md`\n"
            "</details>"
        ),
    }


def _healthy_run(autopilot_id: str, *, run_id: str, session_id: str, output: str = "Completed ok") -> dict:
    return {
        "id": run_id,
        "autopilot_id": autopilot_id,
        "status": "completed",
        "triggered_at": "2026-11-21T20:20:22Z",  # 2026-11-22 04:20:22 CST
        "completed_at": "2026-11-21T20:25:00Z",
        "result": {
            "output": output,
            "session_id": session_id,
            "work_dir": "/tmp/workdir",
        },
    }


class AutopilotWatchdogTests(unittest.TestCase):
    def _create_aidata_fixtures(
        self,
        tmp_dir: str,
        *,
        yesterday_str: str = "2026-11-21",
        digest_content: str = "# AI 使用日报 2026-11-21\n\n> 数据源: raven✅ multica✅ ADO✅ state.db✅\n",
        create_digest: bool = True,
        skills_roi_coverage: str = "healthy",
        state_status: str = "ok",
        state_last_result: str = "success",
        stage_status: str = "ok",
        stage_detail: str = "33 源成功",
        cron_errors_content: str = "",
    ) -> dict:
        base = Path(tmp_dir)
        archive_dir = base / "archive" / "daily"
        archive_dir.mkdir(parents=True, exist_ok=True)
        digest_path = archive_dir / f"{yesterday_str}.md"
        if create_digest:
            digest_path.write_text(digest_content, encoding="utf-8")

        skills_roi_path = base / "skills-roi.json"
        skills_roi_path.write_text(
            json.dumps({"schema_version": 2, "coverage_status": skills_roi_coverage, "sample_size": 42}),
            encoding="utf-8",
        )

        state_json_path = base / "state.json"
        state_json_path.write_text(
            json.dumps(
                {
                    "id": "aidata-digest",
                    "status": state_status,
                    "lastResult": state_last_result,
                    "sourceAsOf": yesterday_str,
                    "stages": [{"id": "collect", "status": stage_status, "detail": stage_detail}],
                }
            ),
            encoding="utf-8",
        )

        cron_errors_path = base / "cron-errors.log"
        cron_errors_path.write_text(cron_errors_content, encoding="utf-8")

        digest_run_script = base / "aidata_digest_run.sh"
        digest_run_script.write_text(
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            'osascript -e \'display notification "test popup" with title "AIDash"\'\n'
            f'mkdir -p "{archive_dir}"\n'
            f'printf "# AI 使用日报 {yesterday_str}\\n\\n> 数据源: raven✅ multica✅ ADO✅ state.db✅\\n" > "{digest_path}"\n'
            f'printf \'{{"schema_version": 2, "coverage_status": "healthy", "sample_size": 45}}\' > "{skills_roi_path}"\n'
            f'printf \'{{"id": "aidata-digest", "status": "idle", "lastResult": "success", "lastRunAt": "2026-11-21T20:35:00Z", "sourceAsOf": "{yesterday_str}", "stages": [{{"id": "collect", "status": "ok", "detail": "35 源成功"}}]}}\' > "{state_json_path}"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        digest_run_script.chmod(0o755)

        return {
            "yesterday_date_str": yesterday_str,
            "digest_archive_dir": archive_dir,
            "digest_archive_path": digest_path,
            "skills_roi_path": skills_roi_path,
            "state_json_path": state_json_path,
            "cron_errors_path": cron_errors_path,
            "aidata_digest_run_script_path": digest_run_script,
        }

    def test_relative_symlink_points_to_team_lead_canonical_script(self):
        self.assertTrue(M2_SYMLINK_PATH.is_symlink())
        raw_link = os.readlink(M2_SYMLINK_PATH)
        self.assertEqual(raw_link, "../../team-lead/scripts/autopilot_watchdog.py")
        self.assertEqual(M2_SYMLINK_PATH.resolve(), WATCHDOG_PATH.resolve())

    def test_zero_desktop_popup_rule_enforced_in_watchdog_source(self):
        source_text = WATCHDOG_PATH.read_text(encoding="utf-8")
        self.assertNotIn("osascript", source_text)
        self.assertNotIn("display notification", source_text)

    def test_scope_enforces_skill_retro_daily_learn_and_aidata_targets(self):
        self.assertEqual(watchdog.ALLOWED_TARGETS, ("skill-retro", "agent-retro", "daily-learn", "aidata"))
        with self.assertRaises(ValueError):
            watchdog.run_watchdog(
                target_date=date(2026, 11, 22),
                targets=("some-other-autopilot",),
                auto_escalate=False,
            )

    def test_agent_retro_target_checks_cursor_and_non_empty_telemetry(self):
        def fake_runner(args, **kwargs):
            cmd = list(args)
            if cmd[:2] == ["autopilot", "list"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"autopilots": [AGENT_RETRO_AP, SKILL_RETRO_AP, DAILY_LEARN_AP]}), stderr="")
            if cmd[:3] == ["autopilot", "runs", AGENT_RETRO_AP["id"]]:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"runs": [_healthy_run(AGENT_RETRO_AP["id"], run_id="run-ar-1", session_id="sess-ar-1")]}), stderr="")
            if cmd[:2] == ["autopilot", "runs"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"runs": []}), stderr="")
            if cmd[:2] == ["issue", "list"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"issues": [{
                    "id": "issue-ar-1", "identifier": "TIA-99", "title": "[Agent Retro 2026-11-22]",
                    "status": "backlog", "description": "<!-- multica-cursor:agent-retro-autopilot-2026-11-22 -->\n<details>sessions=7 coverage=healthy</details>",
                }]}), stderr="")
            raise AssertionError(f"Unexpected multica call: {cmd}")

        report = watchdog.run_watchdog(target_date=date(2026, 11, 22), targets=("agent-retro",),
                                       auto_escalate=False, runner=fake_runner)
        self.assertTrue(report["ok"])
        self.assertTrue(report["checks"]["agent-retro"]["metrics"]["non_empty_telemetry"]["ok"])
        self.assertEqual(report["checks"]["agent-retro"]["metrics"]["non_empty_telemetry"]["sessions"], 7)

    def test_healthy_pass_exits_silently_at_zero_token_cost_without_escalation(self):
        escalations = []

        with tempfile.TemporaryDirectory() as tmp:
            aidata_paths = self._create_aidata_fixtures(tmp)

            def fake_runner(args, **kwargs):
                cmd = list(args)
                if cmd[:2] == ["autopilot", "list"]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({"autopilots": [SKILL_RETRO_AP, DAILY_LEARN_AP, AIDATA_AP]}),
                        stderr="",
                    )
                if cmd[:3] == ["autopilot", "runs", SKILL_RETRO_AP["id"]]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({"runs": [_healthy_run(SKILL_RETRO_AP["id"], run_id="run-sr-1", session_id="sess-sr-1")]}),
                        stderr="",
                    )
                if cmd[:3] == ["autopilot", "runs", DAILY_LEARN_AP["id"]]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({"runs": [_healthy_run(DAILY_LEARN_AP["id"], run_id="run-dl-1", session_id="sess-dl-1")]}),
                        stderr="",
                    )
                if cmd[:3] == ["autopilot", "runs", AIDATA_AP["id"]]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({"runs": [_healthy_run(AIDATA_AP["id"], run_id="run-ad-1", session_id="sess-ad-1")]}),
                        stderr="",
                    )
                if cmd[:2] == ["issue", "list"]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps(
                            {
                                "issues": [
                                    _healthy_skill_retro_issue("2026-11-22", sessions=24, coverage="healthy"),
                                    _healthy_daily_learn_issue("2026-11-22"),
                                    _healthy_aidata_issue("2026-11-22", degraded=False),
                                ]
                            }
                        ),
                        stderr="",
                    )
                raise AssertionError(f"Unexpected multica call: {cmd}")

            report = watchdog.run_watchdog(
                target_date=date(2026, 11, 22),
                runner=fake_runner,
                escalator_fn=lambda *a, **kw: escalations.append((a, kw)) or {"ok": True},
                **aidata_paths,
            )
            self.assertTrue(report["ok"])
            self.assertEqual(report["status"], "HEALTHY")
            self.assertEqual(report["token_cost"], 0)
            self.assertEqual(report["failures"], [])
            self.assertEqual(escalations, [])
            for target_key in ("skill-retro", "daily-learn", "aidata"):
                self.assertTrue(report["checks"][target_key]["ok"])

    def test_skill_retro_sessions_zero_and_coverage_missing_triggers_m2_escalation_rerun_and_reverify(self):
        """Catches the exact TIA-25 silent fallback (`sessions=0`, `coverage=missing`), escalates to M2, reruns 4e06bf2a, and re-verifies."""
        state = {"healed": False}
        triggered_autopilots = []
        escalation_evidence = []

        def fake_runner(args, **kwargs):
            cmd = list(args)
            if cmd[:2] == ["autopilot", "list"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"autopilots": [SKILL_RETRO_AP, DAILY_LEARN_AP]}), stderr="")
            if cmd[:3] == ["autopilot", "runs", SKILL_RETRO_AP["id"]]:
                out_msg = (
                    "Summary issue `TIA-25` created in `backlog`; Telemetry coverage recorded as `missing` (`0` sessions)"
                    if not state["healed"]
                    else "Rollup and collection completed with real telemetry (`sessions=19`, `coverage=healthy`)"
                )
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps({"runs": [_healthy_run(SKILL_RETRO_AP["id"], run_id="01a1ffc4-6251", session_id="2aed81f4-93c1", output=out_msg)]}),
                    stderr="",
                )
            if cmd[:3] == ["autopilot", "runs", DAILY_LEARN_AP["id"]]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps({"runs": [_healthy_run(DAILY_LEARN_AP["id"], run_id="run-dl-ok", session_id="sess-dl-ok")]}),
                    stderr="",
                )
            if cmd[:2] == ["issue", "list"]:
                sr_issue = (
                    _healthy_skill_retro_issue("2026-11-22", sessions=0, coverage="missing")
                    if not state["healed"]
                    else _healthy_skill_retro_issue("2026-11-22", sessions=19, coverage="healthy")
                )
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps({"issues": [sr_issue, _healthy_daily_learn_issue("2026-11-22")]}),
                    stderr="",
                )
            if cmd[:2] == ["autopilot", "trigger"]:
                triggered_autopilots.append(cmd[2])
                state["healed"] = True
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"run_id": "rerun-sr-2", "status": "queued"}), stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        def fake_m2_escalator(evidence, *, target_date_str, runner, **kwargs):
            escalation_evidence.append(dict(evidence))
            prompt = watchdog.build_m2_escalation_prompt(evidence, target_date_str=target_date_str)
            self.assertIn("4e06bf2a", prompt)
            self.assertIn("non_empty_telemetry", prompt)
            runner(["autopilot", "trigger", evidence["autopilot_id"], "--output", "json"])
            return {
                "ok": True,
                "action": "m2_escalated_and_rerun_triggered",
                "target": evidence["target"],
                "autopilot_id": evidence["autopilot_id"],
                "rerun_triggered": True,
                "m2_prompt": prompt,
            }

        report = watchdog.run_watchdog(
            target_date=date(2026, 11, 22),
            targets=("skill-retro", "daily-learn"),
            runner=fake_runner,
            escalator_fn=fake_m2_escalator,
            reverify_polls=1,
        )
        self.assertEqual(len(escalation_evidence), 1)
        ev = escalation_evidence[0]
        self.assertEqual(ev["target"], "skill-retro")
        self.assertEqual(ev["failed_metrics"], ["non_empty_telemetry"])
        self.assertEqual(ev["latest_run_id"], "01a1ffc4-6251")
        self.assertEqual(ev["session_id"], "2aed81f4-93c1")
        self.assertEqual(ev["sessions"], 0)
        self.assertEqual(ev["coverage"], "missing")
        self.assertEqual(triggered_autopilots, [SKILL_RETRO_AP["id"]])
        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "RECOVERED_AFTER_M2_RERUN")
        self.assertTrue(report["reverified"])
        self.assertTrue(report["checks"]["skill-retro"]["metrics"]["non_empty_telemetry"]["ok"])

    def test_daily_learn_missing_or_empty_issue_triggers_m2_escalation_and_rerun(self):
        """Catches missing `[Daily Learn YYYY-MM-DD]` issue or 0-candidate shell -> escalates to M2 -> reruns 8b4d6d04 -> re-verifies."""
        state = {"phase": "missing_issue"}
        triggered_autopilots = []
        escalated_targets = []

        empty_dl_issue = {
            "id": "issue-dl-empty",
            "identifier": "TIA-29",
            "title": "[Daily Learn 2026-11-22] 今日双轨学习选题推荐",
            "status": "todo",
            "created_at": "2026-11-21T20:16:00Z",
            "updated_at": "2026-11-21T20:16:00Z",
            "description": (
                "<!-- multica-cursor:daily-learn-2026-11-22 -->\n\n"
                "**一句话结论**：本轮暂无新增候选。\n\n"
                "- [ ] 1. 今日暂无新候选 — 保持观察\n\n"
                "<details><summary>🤖 AI 执行上下文与确定性证据（人无需展开）</summary>\n</details>"
            ),
        }

        def fake_runner(args, **kwargs):
            cmd = list(args)
            if cmd[:2] == ["autopilot", "list"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"autopilots": [SKILL_RETRO_AP, DAILY_LEARN_AP]}), stderr="")
            if cmd[:3] == ["autopilot", "runs", SKILL_RETRO_AP["id"]]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps({"runs": [_healthy_run(SKILL_RETRO_AP["id"], run_id="run-sr-ok", session_id="sess-sr-ok")]}),
                    stderr="",
                )
            if cmd[:3] == ["autopilot", "runs", DAILY_LEARN_AP["id"]]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps(
                        {
                            "runs": [
                                _healthy_run(
                                    DAILY_LEARN_AP["id"],
                                    run_id="01a1ffbf-ce89",
                                    session_id="90eeafc0-b24d",
                                    output="已完成 2026-11-22 双轨知识产出（但未创建 Multica Issue）",
                                )
                            ]
                        }
                    ),
                    stderr="",
                )
            if cmd[:2] == ["issue", "list"]:
                issues = [_healthy_skill_retro_issue("2026-11-22", sessions=15, coverage="healthy")]
                if state["phase"] == "empty_issue":
                    issues.append(empty_dl_issue)
                elif state["phase"] == "healed":
                    issues.append(_healthy_daily_learn_issue("2026-11-22"))
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"issues": issues}), stderr="")
            if cmd[:2] == ["autopilot", "trigger"]:
                triggered_autopilots.append(cmd[2])
                state["phase"] = "healed"
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"run_id": "rerun-dl-2", "status": "queued"}), stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        # 1) Check when Daily Learn issue is completely missing (like on 2026-11-22 when 8b4d6d04 only pushed Blog)
        check_missing = watchdog.run_watchdog(
            target_date=date(2026, 11, 22),
            targets=("skill-retro", "daily-learn"),
            auto_escalate=False,
            runner=fake_runner,
        )
        self.assertFalse(check_missing["ok"])
        self.assertEqual(check_missing["status"], "UNHEALTHY")
        dl_failed = check_missing["checks"]["daily-learn"]["failed_metrics"]
        self.assertEqual(dl_failed, ["issue_materialized", "non_empty_candidates"])

        # 2) Check when Daily Learn issue exists but is an empty/fallback shell (`0` real candidates) + auto-escalate & rerun
        state["phase"] = "empty_issue"

        def fake_escalator(evidence, *, target_date_str, runner, **kwargs):
            escalated_targets.append(dict(evidence))
            runner(["autopilot", "trigger", evidence["autopilot_id"], "--output", "json"])
            return {
                "ok": True,
                "action": "m2_escalated_and_rerun_triggered",
                "target": evidence["target"],
                "autopilot_id": evidence["autopilot_id"],
                "rerun_triggered": True,
            }

        recovered = watchdog.run_watchdog(
            target_date=date(2026, 11, 22),
            targets=("skill-retro", "daily-learn"),
            auto_escalate=True,
            runner=fake_runner,
            escalator_fn=fake_escalator,
            reverify_polls=1,
        )
        self.assertEqual(len(escalated_targets), 1)
        self.assertEqual(escalated_targets[0]["target"], "daily-learn")
        self.assertEqual(escalated_targets[0]["failed_metrics"], ["non_empty_candidates"])
        self.assertEqual(triggered_autopilots, [DAILY_LEARN_AP["id"]])
        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["status"], "RECOVERED_AFTER_M2_RERUN")
        self.assertGreater(recovered["checks"]["daily-learn"]["metrics"]["non_empty_candidates"]["candidate_count"], 0)

    def test_aidata_degraded_sources_and_missing_snapshot_triggers_m2_escalation_rerun_and_reverify(self):
        """Catches TIA-24 style aidata failures (multica_issue permission denied, bot_tl/release_tf timeout, coverage_status=missing, missing digest archive, or cron-errors.log) -> escalates to aidash-aidata M2 -> runs aidata_digest_run.sh + triggers 27cd6086 -> re-verifies."""
        with tempfile.TemporaryDirectory() as tmp:
            aidata_paths = self._create_aidata_fixtures(
                tmp,
                create_digest=False,
                skills_roi_coverage="missing",
                state_status="failed",
                state_last_result="failure",
                stage_status="warning",
                stage_detail="33 源成功 · 2 降级",
                cron_errors_content="2026-11-21T20:05:12Z — digest 2026-11-22 数据不全: multica_issue permission denied; bot_tl collect timed out\n",
            )
            state = {"healed": False}
            triggered_autopilots = []
            escalation_evidence = []

            def fake_runner(args, **kwargs):
                cmd = list(args)
                if cmd[:2] == ["autopilot", "list"]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({"autopilots": [SKILL_RETRO_AP, DAILY_LEARN_AP, AIDATA_AP]}),
                        stderr="",
                    )
                if cmd[:3] == ["autopilot", "runs", SKILL_RETRO_AP["id"]]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({"runs": [_healthy_run(SKILL_RETRO_AP["id"], run_id="run-sr-ok", session_id="sess-sr-ok")]}),
                        stderr="",
                    )
                if cmd[:3] == ["autopilot", "runs", DAILY_LEARN_AP["id"]]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({"runs": [_healthy_run(DAILY_LEARN_AP["id"], run_id="run-dl-ok", session_id="sess-dl-ok")]}),
                        stderr="",
                    )
                if cmd[:3] == ["autopilot", "runs", AIDATA_AP["id"]]:
                    out_msg = (
                        "L1/L2 完成采集与合并：`multica_issue` 权限不足，`bot_tl`/`release_tf` 超时并回退旧数据；coverage_status=missing"
                        if not state["healed"]
                        else "aidata-digest 全部阶段与 S1-S12 ROI 快照重跑完成，全源健康"
                    )
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({"runs": [_healthy_run(AIDATA_AP["id"], run_id="01a1ffb2-12ee", session_id="f16e2c14-0439", output=out_msg)]}),
                        stderr="",
                    )
                if cmd[:2] == ["issue", "list"]:
                    ad_issue = _healthy_aidata_issue("2026-11-22", degraded=not state["healed"])
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps(
                            {
                                "issues": [
                                    _healthy_skill_retro_issue("2026-11-22", sessions=20, coverage="healthy"),
                                    _healthy_daily_learn_issue("2026-11-22"),
                                    ad_issue,
                                ]
                            }
                        ),
                        stderr="",
                    )
                if cmd[:2] == ["autopilot", "trigger"]:
                    triggered_autopilots.append(cmd[2])
                    state["healed"] = True
                    return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"run_id": "rerun-ad-2", "status": "queued"}), stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

            with unittest.mock.patch.object(watchdog, "M2_ROUTER_SCRIPT_PATH", Path(tmp) / "nonexistent-m2-router.py"):
                report = watchdog.run_watchdog(
                    target_date=date(2026, 11, 22),
                    runner=fake_runner,
                    reverify_polls=1,
                    **aidata_paths,
                )
            self.assertEqual(len(report["initial_failures"]), 1)
            ev = report["initial_failures"][0]
            self.assertEqual(ev["target"], "aidata")
            self.assertEqual(ev["failed_metrics"], ["issue_and_snapshot_materialized", "sources_healthy"])
            self.assertEqual(ev["latest_run_id"], "01a1ffb2-12ee")
            self.assertEqual(ev["session_id"], "f16e2c14-0439")
            self.assertFalse(ev["digest_exists"])
            self.assertEqual(ev["skills_roi_coverage"], "missing")
            self.assertTrue(len(ev["degraded_sources"]) > 0)
            self.assertTrue(len(ev["cron_error_lines"]) > 0)
            self.assertEqual(triggered_autopilots, [AIDATA_AP["id"]])
            self.assertEqual(len(report["escalations"]), 1)
            esc = report["escalations"][0]
            self.assertIn("aidata_digest_run_sh_executed", esc["actions_taken"])
            self.assertIn("multica_autopilot_triggered", esc["actions_taken"])
            self.assertEqual(esc["remediation_detail"]["aidata_digest_run_timeout_sec"], watchdog.DEFAULT_AIDATA_DIGEST_RUN_TIMEOUT_SEC)
            self.assertGreaterEqual(watchdog.DEFAULT_AIDATA_DIGEST_RUN_TIMEOUT_SEC, 2400.0)
            self.assertIn("27cd6086", esc["m2_prompt"])
            self.assertIn("aidash-aidata", esc["m2_prompt"])
            self.assertTrue(report["ok"])
            self.assertEqual(report["status"], "RECOVERED_AFTER_M2_RERUN")
            self.assertTrue(report["reverified"])
            self.assertTrue(report["checks"]["aidata"]["metrics"]["issue_and_snapshot_materialized"]["ok"])
            self.assertTrue(report["checks"]["aidata"]["metrics"]["sources_healthy"]["ok"])
            # Historical cron-errors.log entry is preserved (not truncated) and classified as historical after recovery
            self.assertIn(
                "2026-11-21T20:05:12Z",
                aidata_paths["cron_errors_path"].read_text(encoding="utf-8"),
            )
            self.assertEqual(
                len(report["checks"]["aidata"]["metrics"]["sources_healthy"]["historical_cron_error_lines"]),
                1,
            )

    def test_no_popup_shim_intercepts_subprocess_desktop_notification_calls(self):
        """Verifies _build_no_popup_subprocess_env places a shim first in PATH so child processes cannot trigger macOS popups."""
        with tempfile.TemporaryDirectory() as tmp:
            shim_dir = Path(tmp) / "shim"
            env = watchdog._build_no_popup_subprocess_env(
                {"PATH": f"{tmp}:/usr/bin:/bin"},
                shim_dir=shim_dir,
            )
            marker = Path(tmp) / "popup_fired.txt"
            fake_system_osascript = Path(tmp) / ("osa" + "script")
            fake_system_osascript.write_text(
                f"#!/bin/sh\necho fired > {marker}\nexit 0\n",
                encoding="utf-8",
            )
            fake_system_osascript.chmod(0o755)

            proc = subprocess.run(
                ["/bin/sh", "-c", 'osascript -e \'display notification "hi"\''],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertFalse(marker.exists())

    def test_aidata_default_escalator_timeout_remains_escalated_unhealthy(self):
        """Verifies that if aidata_digest_run.sh times out and does not repair local snapshots, re-verification honestly reports ESCALATED_UNHEALTHY."""
        with tempfile.TemporaryDirectory() as tmp:
            aidata_paths = self._create_aidata_fixtures(
                tmp,
                create_digest=False,
                skills_roi_coverage="missing",
                state_status="failed",
                state_last_result="failure",
                stage_status="warning",
                stage_detail="33 源成功 · 2 降级",
            )
            child_pid_file = Path(tmp) / "child_worker.pid"
            slow_script = Path(tmp) / "slow_digest.sh"
            slow_script.write_text(
                "#!/bin/bash\n"
                f'python3 -c "import os, signal, time; os.setpgid(0, 0); signal.signal(signal.SIGTERM, signal.SIG_IGN); open({str(child_pid_file)!r}, \'w\').write(str(os.getpid())); time.sleep(30)" &\n'
                "while [ ! -s " + repr(str(child_pid_file)) + " ]; do sleep 0.01; done\n"
                "sleep 30\n",
                encoding="utf-8",
            )
            slow_script.chmod(0o755)
            aidata_paths["aidata_digest_run_script_path"] = slow_script

            def fake_runner(args, **kwargs):
                cmd = list(args)
                if cmd[:2] == ["autopilot", "list"]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({"autopilots": [SKILL_RETRO_AP, DAILY_LEARN_AP, AIDATA_AP]}),
                        stderr="",
                    )
                if cmd[:3] == ["autopilot", "runs", AIDATA_AP["id"]]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({"runs": [_healthy_run(AIDATA_AP["id"], run_id="run-ad-timeout", session_id="sess-ad-timeout")]}),
                        stderr="",
                    )
                if cmd[:2] == ["issue", "list"]:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({"issues": [_healthy_aidata_issue("2026-11-22", degraded=False)]}),
                        stderr="",
                    )
                if cmd[:2] == ["autopilot", "trigger"]:
                    return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"run_id": "rerun-ad-timeout", "status": "queued"}), stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

            with unittest.mock.patch.object(watchdog, "M2_ROUTER_SCRIPT_PATH", Path(tmp) / "nonexistent-m2-router.py"):
                report = watchdog.run_watchdog(
                    target_date=date(2026, 11, 22),
                    targets=("aidata",),
                    runner=fake_runner,
                    reverify_polls=1,
                    aidata_digest_run_timeout_sec=0.8,
                    **aidata_paths,
                )
            self.assertFalse(report["ok"])
            self.assertEqual(report["status"], "ESCALATED_UNHEALTHY")
            esc = report["escalations"][0]
            self.assertEqual(esc["remediation_detail"]["aidata_digest_run_exit"], 124)
            self.assertTrue(esc["remediation_detail"]["aidata_digest_run_timed_out"])
            self.assertTrue(esc["remediation_detail"]["aidata_digest_process_group_terminated"])
            self.assertNotIn("aidata_digest_run_sh_executed", esc["actions_taken"])
            self.assertTrue(child_pid_file.is_file())
            child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)

    def test_multica_loop_runner_wires_autopilot_health_watchdog_spec_and_sync(self):
        spec = loop_runner.resolve_loop_spec("autopilot-health-watchdog")
        self.assertEqual(spec.loop_id, "autopilot-health-watchdog")
        self.assertEqual(spec.cron, "10 5 * * *")
        self.assertEqual(spec.delivery_mode, "auto_fix_and_digest_only")
        self.assertIn("Daily Autopilot Health Watchdog", spec.autopilot_title)
        self.assertIn("aidata", spec.autopilot_title)

        desc = loop_runner.build_loop_autopilot_description(spec)
        self.assertIn("Target 3 · `aidata` (`27cd6086`)", desc)
        self.assertIn("Zero desktop popups", desc)

        dry = loop_runner.sync_autopilots(loop_id="autopilot-health-watchdog", dry_run=True)
        self.assertTrue(dry["ok"])
        self.assertEqual(len(dry["loops"]), 1)
        self.assertEqual(dry["loops"][0]["loop_id"], "autopilot-health-watchdog")
        self.assertEqual(dry["loops"][0]["cron"], "10 5 * * *")

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "watermarks.json"
            digest_file = Path(tmp) / "digest.json"
            collected_healthy = {
                "loop_id": "autopilot-health-watchdog",
                "date": "2026-11-22",
                "status": "HEALTHY",
                "ok": True,
                "has_delta": False,
                "delta_count": 0,
                "actionable": False,
                "severity": "low",
                "token_cost": 0,
                "watermark": "autopilot-health-watchdog:2026-11-22:healthy:abc",
                "cursor": "autopilot-health-watchdog-2026-11-22",
                "title": "[P3 / Priority 3][Autopilot Watchdog] 2026-11-22 定时任务健康巡检与 M2 自愈报告",
                "summary": "今日 `skill-retro` 与 `daily-learn` 三项核心生命体征全部健康通过。",
                "items": [],
                "jev": {"actionable": False, "decision_source": "watchdog_deterministic_vitals"},
            }
            res = loop_runner.run_loop(
                "autopilot-health-watchdog",
                target_date=date(2026, 11, 22),
                state_path=state_file,
                digest_buffer_path=digest_file,
                collector_fn=lambda _spec, target_date: collected_healthy,
            )
            self.assertEqual(res["status"], "SKIPPED_NO_DELTA")
            self.assertEqual(res["layer_reached"], 0)
            self.assertEqual(res["token_cost"], 0)


if __name__ == "__main__":
    unittest.main()
