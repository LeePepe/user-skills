import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROOMER_PATH = ROOT / "skills/linear-board/scripts/backlog_groomer.py"
DAILY_BRIEF_PATH = ROOT / "skills/linear-board/scripts/daily-brief.py"
LOOP_RUNNER_PATH = ROOT / "skills/team-lead/scripts/multica_loop_runner.py"
ISSUE_BRIEF_PATH = ROOT / "skills/issue-brief/scripts/verify_issue_format.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


groomer = _load("test_backlog_groomer_mod", GROOMER_PATH)
daily_brief = _load("test_backlog_daily_brief_mod", DAILY_BRIEF_PATH)
loop_runner = _load("test_backlog_loop_runner_mod", LOOP_RUNNER_PATH)
issue_brief = _load("test_backlog_issue_brief_mod", ISSUE_BRIEF_PATH)


class BacklogGroomerLoopTests(unittest.TestCase):
    def _sample_issues(self) -> list[dict]:
        return [
            # Duplicate cluster 1: same problem across Linear TIA & Multica
            {
                "id": "lin-101",
                "identifier": "TIA-501",
                "source": "linear",
                "title": "[Backlog] 实现 AIDash 技能 ROI 遥测快照自动导出门禁",
                "description": "在 aidata L5_apps 中增加技能效能快照导出与防呆门禁校验 (#188)",
                "state": {"name": "Todo", "type": "unstarted"},
                "priority": 2,
                "project": {"name": "AIDash"},
                "url": "https://linear.app/tianpeili/issue/TIA-501",
                "createdAt": "2026-11-10T08:00:00+08:00",
            },
            {
                "id": "mul-201",
                "identifier": "MUL-19",
                "source": "multica",
                "title": "实现 AIDash 技能 ROI 遥测快照自动导出门禁",
                "description": "同步 #188 的技能 ROI 遥测快照导出门禁",
                "status": "backlog",
                "priority": "medium",
                "project": "AIDash",
                "created_at": "2026-11-12T09:00:00+08:00",
            },
            # Candidate 2: High-ROI ready item
            {
                "id": "lin-102",
                "identifier": "TIA-502",
                "source": "linear",
                "title": "收敛 VoxPocket 离线语音转写缓存防抖与端到端门禁",
                "description": "自动化验证录音转写缓存回放，消除重复构建耗时",
                "state": {"name": "Backlog", "type": "backlog"},
                "priority": 2,
                "project": {"name": "VoxPocket"},
                "url": "https://linear.app/tianpeili/issue/TIA-502",
                "createdAt": "2026-11-11T08:00:00+08:00",
            },
            # Candidate 3: Ready medium-high ROI item
            {
                "id": "lin-103",
                "identifier": "TIA-503",
                "source": "linear",
                "title": "补齐 shared-design-system 双外观玻璃态对比度自动巡检",
                "description": "在 design_contract.py 增加亮暗双模式对比度自动校验",
                "state": {"name": "Todo", "type": "unstarted"},
                "priority": 2,
                "project": {"name": "shared-design-system"},
                "url": "https://linear.app/tianpeili/issue/TIA-503",
                "createdAt": "2026-11-14T08:00:00+08:00",
            },
            # Candidate 4: Blocked by upstream dependency -> must NOT enter Top 3
            {
                "id": "lin-104",
                "identifier": "TIA-504",
                "source": "linear",
                "title": "接入外部 Apple 证书自动续期 Webhook",
                "description": "Blocked by TIA-499，等待外部管理员审批权限后才能继续",
                "state": {"name": "Backlog", "type": "backlog"},
                "priority": 1,
                "blocked_by": ["TIA-499"],
                "project": {"name": "user-skills"},
                "url": "https://linear.app/tianpeili/issue/TIA-504",
            },
            # Candidate 5: Low priority / low ROI item -> ranked below Top 3
            {
                "id": "lin-105",
                "identifier": "TIA-505",
                "source": "linear",
                "title": "微调历史文档排版缩进",
                "description": "纯样式微调",
                "state": {"name": "Backlog", "type": "backlog"},
                "priority": 4,
                "project": {"name": "user-skills"},
                "url": "https://linear.app/tianpeili/issue/TIA-505",
            },
            # Excluded items: Work Business, Daily Brief container, Done issue
            {
                "id": "lin-work",
                "identifier": "TIA-506",
                "source": "linear",
                "title": "Bing China feed latency",
                "state": {"name": "Todo", "type": "unstarted"},
                "priority": 1,
                "project": {"name": "Work Business"},
            },
            {
                "id": "lin-brief",
                "identifier": "TIA-507",
                "source": "linear",
                "title": "[Daily Brief 2026-11-19] 昨日汇总与今日待办",
                "state": {"name": "Todo", "type": "unstarted"},
                "priority": 2,
                "project": {"name": "user-skills"},
            },
            {
                "id": "lin-done",
                "identifier": "TIA-508",
                "source": "linear",
                "title": "Already finished item",
                "state": {"name": "Done", "type": "completed"},
                "priority": 1,
                "project": {"name": "user-skills"},
            },
        ]

    def test_stage1_fingerprint_deduplication_groups_cross_board_duplicates_and_picks_canonical_tia_survivor(self):
        report = groomer.cluster_and_deduplicate_issues(self._sample_issues())
        # 6 open personal backlog/todo issues -> 5 deduplicated candidates, 1 duplicate cluster
        self.assertEqual(report["total_open_backlog_issues"], 6)
        self.assertEqual(report["deduplicated_count"], 5)
        self.assertEqual(report["duplicate_cluster_count"], 1)
        self.assertEqual(report["duplicate_issue_count"], 1)

        cluster = report["duplicate_clusters"][0]
        self.assertEqual(cluster["canonical_issue"]["identifier"], "TIA-501")
        self.assertEqual([d["identifier"] for d in cluster["duplicate_issues"]], ["MUL-19"])
        self.assertIn("TIA-501", cluster["recommended_action"])
        self.assertIn("MUL-19", cluster["recommended_action"])

    def test_stage2_jev_scoring_and_deterministic_fallback_select_daily_top3_and_reject_blocked(self):
        mock_jev = {
            "TIA-501": {
                "answers": {
                    "readiness_choice": {"choice": "READY_HIGH_ROI"},
                    "readiness_confidence": {"noul": 0.92},
                    "roi_score": {"score": 0.95},
                }
            },
            "TIA-502": {
                "answers": {
                    "readiness_choice": {"choice": "READY_HIGH_ROI"},
                    "readiness_confidence": {"noul": 0.88},
                    "roi_score": {"score": 0.89},
                }
            },
            "TIA-503": {
                "answers": {
                    "readiness_choice": {"choice": "READY_HIGH_ROI"},
                    "readiness_confidence": {"noul": 0.81},
                    "roi_score": {"score": 0.82},
                }
            },
            "TIA-504": {
                "answers": {
                    "readiness_choice": {"choice": "BLOCKED_DEPENDENCY"},
                    "readiness_confidence": {"noul": 0.94},
                    "roi_score": {"score": 0.90},
                }
            },
            "TIA-505": {
                "answers": {
                    "readiness_choice": {"choice": "DEFER_LOW_ROI"},
                    "readiness_confidence": {"noul": 0.85},
                    "roi_score": {"score": 0.20},
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            state_out = Path(tmp) / "top3-groomed.json"
            res = groomer.groom_backlog(
                self._sample_issues(),
                target_date=date(2026, 11, 19),
                top_k=3,
                mock_jev_json=mock_jev,
                state_output_path=state_out,
                write_state=True,
            )
            self.assertTrue(res["ok"])
            self.assertTrue(state_out.is_file())
            top3_ids = [item["identifier"] for item in res["top3_push_candidates"]]
            self.assertEqual(top3_ids, ["TIA-501", "TIA-502", "TIA-503"])
            self.assertNotIn("TIA-504", top3_ids)
            self.assertNotIn("TIA-505", top3_ids)

            # Verify deterministic fallback when Jev is offline/unavailable
            fallback_res = groomer.groom_backlog(
                self._sample_issues(),
                target_date=date(2026, 11, 19),
                top_k=3,
                jev_fn=lambda *a, **kw: None,
            )
            fb_top3_ids = [item["identifier"] for item in fallback_res["top3_push_candidates"]]
            self.assertEqual(set(fb_top3_ids), {"TIA-501", "TIA-502", "TIA-503"})
            self.assertEqual(fb_top3_ids[0], "TIA-501")
            for row in fallback_res["top3_push_candidates"]:
                self.assertEqual(row["decision_source"], "deterministic_fallback_unavailable")

    def test_stage3_daily_brief_injects_groomed_top3_and_passes_plan_a_and_dual_zone_verifiers(self):
        groom_res = groomer.groom_backlog(
            self._sample_issues(),
            target_date=date(2026, 11, 19),
            top_k=3,
            jev_fn=lambda *a, **kw: None,
        )
        top3 = groom_res["top3_push_candidates"]
        self.assertEqual(len(top3), 3)

        plan_a_md, todos = daily_brief.render_brief(
            date(2026, 11, 19),
            [],
            [],
            [],
            [],
            groomed_top3=top3,
        )
        self.assertEqual(daily_brief.validate_markdown(plan_a_md), [])
        rec_todos = [t for t in todos if t["group"] == "recommended"]
        self.assertEqual(len(rec_todos), 3)
        for t in rec_todos:
            self.assertLessEqual(len(daily_brief._plain(t["summary"])), 25)
            self.assertTrue(t["summary"].startswith("顺推 "))

        dual_zone_md = daily_brief.render_dual_zone_brief(
            date(2026, 11, 19),
            plan_a_md,
            todos,
            rollup_counts={"linear": 0, "commits": 0, "prs": 0, "events": 0},
        )
        dz_verify = issue_brief.verify_dual_zone_issue(dual_zone_md)
        self.assertTrue(dz_verify["ok"], dz_verify)
        self.assertLessEqual(dz_verify["human_zone_lines"], 15)

    def test_multica_loop_runner_registers_and_executes_backlog_grooming_and_push_loop(self):
        spec = loop_runner.resolve_loop_spec("backlog-grooming-and-push")
        self.assertEqual(spec.loop_id, "backlog-grooming-and-push")
        self.assertEqual(spec.default_priority, "medium")
        self.assertIn(spec, loop_runner.CANONICAL_LOOP_SPECS)

        dry_sync = loop_runner.sync_autopilots(dry_run=True)
        loop_ids = [row["loop_id"] for row in dry_sync["loops"]]
        self.assertIn("backlog-grooming-and-push", loop_ids)

        collected = groomer.run_loop_check(
            target_date=date(2026, 11, 19),
            issues_json=self._sample_issues(),
            dry_run=True,
            jev_fn=lambda *a, **kw: None,
        )
        self.assertTrue(collected["ok"])
        self.assertTrue(collected["has_delta"])
        self.assertTrue(issue_brief.verify_dual_zone_issue(collected["dual_zone_markdown"])["ok"])

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "watermarks.json"
            created_bodies: list[str] = []

            def fake_multica(args, *, stdin_text=None, **kwargs):
                if args[:2] == ["issue", "list"]:
                    return subprocess.CompletedProcess(["multica", *args], 0, stdout='{"issues":[]}', stderr="")
                if args[:2] == ["issue", "create"]:
                    created_bodies.append(stdin_text or "")
                    return subprocess.CompletedProcess(
                        ["multica", *args],
                        0,
                        stdout=json.dumps({"id": "bg-1", "identifier": "TIA-601"}),
                        stderr="",
                    )
                return subprocess.CompletedProcess(["multica", *args], 0, stdout="{}", stderr="")

            mock_runner_jev = {
                "answers": {
                    "actionable_probability": {"noul": 0.90},
                    "delivery_route": {"choice": "dual_zone_issue"},
                    "delivery_route_match": {"noul": 0.93},
                    "urgency_score": {"score": 0.76},
                }
            }
            res = loop_runner.run_loop(
                "backlog-grooming-and-push",
                target_date=date(2026, 11, 19),
                state_path=state_path,
                mock_jev_json=mock_runner_jev,
                collector_fn=lambda _spec, target_date: collected,
                runner=fake_multica,
            )
            self.assertEqual(res["status"], "ACTION_TAKEN")
            self.assertEqual(res["layer_reached"], 2)
            self.assertEqual(len(created_bodies), 1)
            self.assertTrue(issue_brief.verify_dual_zone_issue(created_bodies[0])["ok"])

    def test_cli_subcommands_scan_groom_top3_and_loop_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            issues_file = Path(tmp) / "issues.json"
            out_file = Path(tmp) / "top3.json"
            issues_file.write_text(json.dumps(self._sample_issues(), ensure_ascii=False), encoding="utf-8")

            for subcmd in ("scan", "groom", "top3", "loop-check"):
                cmd = [
                    sys.executable,
                    str(GROOMER_PATH),
                    subcmd,
                    "--date",
                    "2026-11-19",
                    "--issues-json",
                    str(issues_file),
                    "--output",
                    str(out_file),
                    "--dry-run",
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
                self.assertEqual(proc.returncode, 0, f"{subcmd} failed: {proc.stderr}")
                parsed = json.loads(proc.stdout)
                self.assertTrue(parsed["ok"])


if __name__ == "__main__":
    unittest.main()
