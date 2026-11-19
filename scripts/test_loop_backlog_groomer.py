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

    def test_multica_live_listing_paginates_by_raw_rows_with_bounded_contract(self):
        calls = []
        # Page 1 has 1 open item + 1 closed item (filtered out) -> offset MUST advance by raw_count (2), not 1
        pages = [
            {
                "has_more": True,
                "issues": [
                    {"id": "m1", "identifier": "MUL-1", "title": "First open", "status": "todo"},
                    {"id": "m2", "identifier": "MUL-2", "title": "Closed on page 1", "status": "done"},
                ],
                "limit": 100,
                "offset": 0,
                "total": 3,
            },
            {
                "has_more": False,
                "issues": [{"id": "m3", "identifier": "MUL-3", "title": "Third open", "status": "todo"}],
                "limit": 100,
                "offset": 2,
                "total": 3,
            },
        ]

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            offset = int(args[args.index("--offset") + 1])
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(pages[offset // 2]), stderr="")

        rows, warnings = groomer.collect_backlog_issues(
            linear_rows=[], include_multica=True, multica_bin="multica-test", runner=runner, dry_run=True
        )
        self.assertEqual([row["identifier"] for row in rows], ["MUL-1", "MUL-3"])
        self.assertEqual([int(args[args.index("--offset") + 1]) for args, _ in calls], [0, 2])
        self.assertEqual([args[args.index("--limit") + 1] for args, _ in calls], ["100", "100"])
        self.assertTrue(all(call_kwargs.get("timeout") == 15 for _, call_kwargs in calls))
        self.assertEqual(warnings, [])

    def test_scope_ancestry_excludes_work_grandparent_team_empty_parent_and_preserves_multihop_personal(self):
        personal_root = {
            "id": "p-root",
            "identifier": "TIA-300",
            "title": "[Epic] Personal Platform",
            "team": {"name": "Personal"},
            "project": {"name": "user-skills"},
            "parent": None,
        }
        personal_mid = {
            "id": "p-mid",
            "identifier": "TIA-301",
            "title": "Personal Milestone Sub-Epic",
            "team": {"name": "Personal"},
            "project": {"name": "user-skills"},
            "parent": {"id": "p-root"},
        }
        personal_leaf = {
            "id": "p-leaf",
            "identifier": "TIA-902",
            "title": "Automate telemetry gate",
            "state": {"name": "Todo", "type": "unstarted"},
            "priority": 2,
            "team": {"name": "Personal"},
            "project": {"name": "user-skills"},
            "parent": {"id": "p-mid", "title": "Personal Milestone Sub-Epic"},
        }
        work_grandparent = {
            "id": "w-root",
            "identifier": "TIA-365",
            "title": "[Root] Work Business",
            "team": {"name": "Personal"},
            "project": {"name": "Work Business"},
            "parent": None,
        }
        work_mid = {
            "id": "w-mid",
            "identifier": "TIA-366",
            "title": "Neutral Mid Container",
            "team": {"name": "Personal"},
            "project": {"name": "Personal"},
            "parent": {"id": "w-root"},
        }
        work_leaf = {
            "id": "w-leaf",
            "identifier": "TIA-900",
            "title": "Generic pipeline cleanup",
            "state": {"name": "Todo", "type": "unstarted"},
            "priority": 1,
            "team": {"name": "Personal"},
            "project": None,
            "parent": {"id": "w-mid", "title": "Neutral Mid Container"},
        }
        work_team_issue = {
            "id": "w-team",
            "identifier": "TIA-903",
            "title": "Generic task under Bing team",
            "state": {"name": "Todo", "type": "unstarted"},
            "priority": 2,
            "team": {"name": "Bing"},
            "project": {"name": "user-skills"},
            "parent": None,
        }
        empty_parent_dict = {
            "id": "bad-parent",
            "identifier": "TIA-901",
            "title": "Malformed empty parent dict must fail closed",
            "state": {"name": "Todo", "type": "unstarted"},
            "priority": 2,
            "project": {"name": "user-skills"},
            "parent": {},
        }

        scope_map = {
            "p-mid": personal_mid,
            "p-root": personal_root,
            "w-mid": work_mid,
            "w-root": work_grandparent,
        }

        class ScopeClient:
            def issue_scope(self, identifier: str):
                return scope_map[identifier]

        rows, warnings = groomer.collect_backlog_issues(
            linear_rows=[personal_leaf, work_leaf, work_team_issue, empty_parent_dict],
            linear_client=ScopeClient(),
            include_multica=False,
            dry_run=True,
        )
        self.assertEqual([row["identifier"] for row in rows], ["TIA-902"])
        self.assertTrue(any("scope" in warning for warning in warnings))

        # Ensure second-pass groom_backlog / cluster_and_deduplicate_issues does NOT drop validated 2-hop personal_leaf!
        groomed = groomer.groom_backlog(
            rows,
            target_date=date(2026, 11, 19),
            top_k=3,
            jev_fn=lambda *a, **kw: None,
        )
        self.assertEqual([c["identifier"] for c in groomed["top3_push_candidates"]], ["TIA-902"])

        # Permutation invariance check for watermark/clusters
        perm_a = groomer.run_loop_check(
            target_date=date(2026, 11, 19),
            issues_json=self._sample_issues(),
            dry_run=True,
            jev_fn=lambda *a, **kw: None,
        )
        perm_b = groomer.run_loop_check(
            target_date=date(2026, 11, 19),
            issues_json=list(reversed(self._sample_issues())),
            dry_run=True,
            jev_fn=lambda *a, **kw: None,
        )
        self.assertEqual(perm_a["watermark"], perm_b["watermark"])

    def test_combined_linear_and_groomed_top3_beyond_row8_preserves_bot_bridge_selection_and_skips_needs(self):
        bridge_spec = importlib.util.spec_from_file_location(
            "backlog_test_bridge", ROOT / "skills/linear-board/scripts/linear-bot-bridge.py"
        )
        assert bridge_spec and bridge_spec.loader
        bridge = importlib.util.module_from_spec(bridge_spec)
        bridge_spec.loader.exec_module(bridge)

        linear = [
            # [1] Needs Decision item (even if stale groomed state lists TIA-700, it MUST NOT be marked ready Top3!)
            {
                "id": "lin-needs",
                "identifier": "TIA-700",
                "title": "Choose gallery v01 or v02",
                "description": "Demo gallery awaiting Owner selection",
                "url": "https://linear.app/test/TIA-700",
                "state": {"name": "Todo", "type": "unstarted"},
                "priority": 1,
                "project": {"name": "VoxPocket"},
                "team": {"name": "Personal"},
                "parent": None,
                "labels": {"nodes": [{"name": "Needs Decision"}]},
            }
        ]
        # Add 9 planned P2 items -> [2]..[10], so TIA-708..TIA-709 are at [9] and [10] (beyond row 8!)
        for index in range(1, 10):
            linear.append(
                {
                    "id": f"lin-{index}",
                    "identifier": f"TIA-{700 + index}",
                    "title": f"Automate gate {index}",
                    "url": f"https://linear.app/test/TIA-{700 + index}",
                    "state": {"name": "Todo", "type": "unstarted"},
                    "priority": 2,
                    "project": {"name": "user-skills"},
                    "team": {"name": "Personal"},
                    "parent": None,
                    "labels": {"nodes": []},
                }
            )

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "top3-groomed.json"
            # Groomed candidates include stale TIA-700 (now Needs Decision), plus TIA-708 ([9]), TIA-709 ([10]), and MUL-88 ([11])
            groomed_input = [
                {
                    "identifier": "TIA-700",
                    "title": "Choose gallery v01 or v02",
                    "url": "https://linear.app/test/TIA-700",
                    "project": "VoxPocket",
                    "ready_to_push": True,
                },
                {
                    "identifier": "TIA-708",
                    "title": "Automate gate 8",
                    "url": "https://linear.app/test/TIA-708",
                    "project": "user-skills",
                    "ready_to_push": True,
                },
                {
                    "identifier": "TIA-709",
                    "title": "Automate gate 9",
                    "url": "https://linear.app/test/TIA-709",
                    "project": "user-skills",
                    "ready_to_push": True,
                },
                {
                    "identifier": "MUL-88",
                    "title": "Cross-board Multica ready task",
                    "project": "AIDash",
                    "ready_to_push": True,
                },
            ]
            state_path.write_text(
                json.dumps({"date": "2026-11-19", "top3_push_candidates": groomed_input[:3], "ranked_candidates": groomed_input}),
                encoding="utf-8",
            )
            loaded = groomer.load_groomed_top3_state(state_path, target_date=date(2026, 11, 19))

        plan_a, todos = daily_brief.render_brief(date(2026, 11, 19), linear, [], [], [], groomed_top3=loaded)
        # Plan A validator MUST pass with 0 errors
        self.assertEqual(daily_brief.validate_markdown(plan_a), [])

        # TIA-700 is in `needs` and MUST NOT be marked groomed_top3; backfill picks TIA-708 ([9]), TIA-709 ([10]), and MUL-88 ([11])
        groomed_rows = [row for row in todos if row.get("groomed_top3")]
        self.assertEqual([row["groomed_identifier"] for row in groomed_rows], ["TIA-708", "TIA-709", "MUL-88"])
        self.assertEqual([row["number"] for row in groomed_rows], [9, 10, 11])
        # Zero duplicate TIA-708 / TIA-709 rows
        self.assertEqual(sum("TIA-708" in row["ref"] for row in todos), 1)
        self.assertEqual(sum("TIA-709" in row["ref"] for row in todos), 1)

        dual = daily_brief.render_dual_zone_brief(date(2026, 11, 19), plan_a, todos)
        dz_verify = issue_brief.verify_dual_zone_issue(dual)
        self.assertTrue(dz_verify["ok"], dz_verify)
        self.assertLessEqual(dz_verify["human_zone_lines"], 15)

        # Human zone MUST contain [9], [10], [11] without `1. [9]` ordinal prefix drift!
        human_zone = dual.split("<details>")[0]
        self.assertIn("- [ ] **[9] Top3 Automate gate 8（[打开](https://linear.app/test/TIA-708)）**", human_zone)
        self.assertIn("- [ ] **[10] Top3 Automate gate 9（[打开](https://linear.app/test/TIA-709)）**", human_zone)
        self.assertIn("- [ ] **[11] Top3 顺推 Cross-board Multica", human_zone)
        self.assertNotIn("1. [", human_zone)

        # Verify linear-bot-bridge.py brief_selection works for BOTH `@bot 9, 11` comment AND checked `- [x] **[9]` box on Dual-Zone!
        brief_title = "[Daily Brief 2026-11-19] 昨日汇总 (11-18) 与今日 Todo (11-19)"
        sel_comment = bridge.brief_selection({"title": brief_title, "description": dual}, {"body": "@bot 9, 11"})
        self.assertIn("[9]", sel_comment)
        self.assertIn("[11]", sel_comment)
        self.assertNotIn("[1]", sel_comment)

        checked_dual = dual.replace("- [ ] **[9]", "- [x] **[9]", 1)
        sel_checked = bridge.brief_selection({"title": brief_title, "description": checked_dual})
        self.assertIn("[9]", sel_checked)
        self.assertNotIn("Owner decision required", sel_checked)

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
