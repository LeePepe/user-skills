import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATROL_PATH = ROOT / "skills/team-lead/scripts/local_hygiene_patrol.py"
M2_SYMLINK_PATH = ROOT / "skills/m2-dispatcher/scripts/local_hygiene_patrol.py"
LOOP_RUNNER_PATH = ROOT / "skills/team-lead/scripts/multica_loop_runner.py"
ISSUE_BRIEF_PATH = ROOT / "skills/issue-brief/scripts/verify_issue_format.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


patrol = _load("test_local_hygiene_patrol_mod", PATROL_PATH)
loop_runner = _load("test_local_hygiene_loop_runner_mod", LOOP_RUNNER_PATH)
issue_brief = _load("test_local_hygiene_issue_brief_mod", ISSUE_BRIEF_PATH)


def _git(cmd, *, cwd=None, check=True):
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(cmd, cwd=cwd, env=clean_env, check=check, capture_output=True, text=True)


class LocalHygienePatrolTests(unittest.TestCase):
    _TEMPLATE_HOLDER: tempfile.TemporaryDirectory[str] | None = None

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._TEMPLATE_HOLDER = cls._create_raw_fixture_env()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._TEMPLATE_HOLDER is not None:
            cls._TEMPLATE_HOLDER.cleanup()
            cls._TEMPLATE_HOLDER = None
        super().tearDownClass()

    @classmethod
    def _create_raw_fixture_env(cls) -> tempfile.TemporaryDirectory[str]:
        holder = tempfile.TemporaryDirectory()
        base = Path(holder.name)
        dev_root = base / "Development"
        handoff_root = dev_root / "handoff"
        wt_root = dev_root / "worktrees"
        dev_root.mkdir(parents=True)
        handoff_root.mkdir(parents=True)
        wt_root.mkdir(parents=True)

        # Create bare remote + local core repo `user-skills`
        remote_bare = base / "remotes" / "user-skills.git"
        _git(["git", "init", "--bare", "-q", "-b", "main", str(remote_bare)])

        repo = dev_root / "user-skills"
        _git(["git", "init", "-q", "-b", "main", str(repo)])
        _git(["git", "-C", str(repo), "config", "user.email", "test@example.com"])
        _git(["git", "-C", str(repo), "config", "user.name", "Test"])
        _git(["git", "-C", str(repo), "remote", "add", "origin", str(remote_bare)])
        (repo / "README.md").write_text("initial\n", encoding="utf-8")
        _git(["git", "-C", str(repo), "add", "README.md"])
        _git(["git", "-C", str(repo), "commit", "-qm", "initial"])
        _git(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"])

        # 1) Clean already-merged worktree -> MUST BE RECLAIMED
        wt_clean_merged = wt_root / "goal-clean-merged"
        _git(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feat/clean-merged", str(wt_clean_merged), "main"])
        (wt_clean_merged / "merged.txt").write_text("merged feature\n", encoding="utf-8")
        _git(["git", "-C", str(wt_clean_merged), "add", "merged.txt"])
        _git(["git", "-C", str(wt_clean_merged), "commit", "-qm", "feat: merged"])
        # Merge into main and push
        _git(["git", "-C", str(repo), "merge", "--no-ff", "-qm", "Merge feat/clean-merged", "feat/clean-merged"])
        _git(["git", "-C", str(repo), "push", "-q", "origin", "main"])

        # 2) Dirty worktree (even though branch is ancestor of main) -> MUST BE PRESERVED
        wt_dirty = wt_root / "goal-dirty-wt"
        _git(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feat/dirty-wt", str(wt_dirty), "main"])
        (wt_dirty / "uncommitted.txt").write_text("wip work\n", encoding="utf-8")

        # 3) Unmerged clean worktree -> MUST BE PRESERVED
        wt_unmerged = wt_root / "goal-unmerged-wt"
        _git(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feat/unmerged-wt", str(wt_unmerged), "main"])
        (wt_unmerged / "unmerged.txt").write_text("ahead of main\n", encoding="utf-8")
        _git(["git", "-C", str(wt_unmerged), "add", "unmerged.txt"])
        _git(["git", "-C", str(wt_unmerged), "commit", "-qm", "feat: unmerged"])

        # 4) Held worktree (clean & merged, but goal ledger has EVT=NEED_GO / HOLD) -> MUST BE PRESERVED
        wt_held = wt_root / "goal-held-wt"
        _git(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feat/held-wt", str(wt_held), "main"])
        held_goal_dir = handoff_root / "goal-held-wt"
        held_goal_dir.mkdir(parents=True)
        (held_goal_dir / "ledger.md").write_text(
            "# Goal: goal-held-wt\n- TL: held-goal-tl-r1\n"
            "- 2026-11-19T10:00:00+08:00 | EVT=NEED_GO | id=E1 | note=Owner hold\n",
            encoding="utf-8",
        )

        # 5) Active pane worktree (clean & merged, but Herdr reports active pane) -> MUST BE PRESERVED
        wt_active = wt_root / "goal-active-pane"
        _git(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feat/active-pane", str(wt_active), "main"])

        # Completed Goal ledger (`EVT=GOAL_DONE`) for Herdr pane reclaim check
        done_goal_dir = handoff_root / "goal-clean-merged"
        done_goal_dir.mkdir(parents=True)
        (done_goal_dir / "ledger.md").write_text(
            "# Goal: goal-clean-merged\n- TL: clean-mrg-goal-tl-r1\n"
            "- 2026-11-19T09:00:00+08:00 | EVT=GOAL_DONE | id=E2 | note=merged\n",
            encoding="utf-8",
        )
        return holder

    def _build_fixture_env(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        assert self._TEMPLATE_HOLDER is not None
        src_base = Path(self._TEMPLATE_HOLDER.name)
        holder = tempfile.TemporaryDirectory()
        dst_base = Path(holder.name)
        shutil.copytree(src_base, dst_base, dirs_exist_ok=True)
        old_prefix = str(src_base)
        new_prefix = str(dst_base)
        old_res = str(src_base.resolve())
        new_res = str(dst_base.resolve())
        # Rewrite absolute worktree/remote gitdir pointers inside the cloned fixture
        repo_git = dst_base / "Development" / "user-skills" / ".git"
        for p in [repo_git / "config", *repo_git.glob("worktrees/*/gitdir"), *(dst_base / "Development" / "worktrees").glob("*/.git")]:
            if p.is_file():
                txt = p.read_text(encoding="utf-8")
                p.write_text(txt.replace(old_res, new_res).replace(old_prefix, new_prefix), encoding="utf-8")
        return holder, dst_base / "Development", dst_base / "Development" / "handoff"

    def _sample_herdr_snapshot(self, dev_root: Path) -> dict:
        wt_active = dev_root / "worktrees" / "goal-active-pane"
        return {
            "workspaces": [
                {"workspace_id": "w2J", "label": "大总管"},
                {"workspace_id": "wBV", "label": "user-skills"},
                {"workspace_id": "wC1", "label": "clean-mrg"},
                {"workspace_id": "wC2", "label": "active-goal"},
            ],
            "agents": [
                {
                    "pane_id": "w2J:p1",
                    "workspace_id": "w2J",
                    "agent_status": "idle",
                    "cwd": str(dev_root),
                },
                {
                    "pane_id": "wBV:p1",
                    "workspace_id": "wBV",
                    "name": "m2-skills-retro-story-r1",
                    "agent_status": "done",
                    "cwd": str(dev_root / "user-skills"),
                },
                {
                    "pane_id": "wC1:p1",
                    "workspace_id": "wC1",
                    "name": "clean-mrg-goal-tl-r1",
                    "agent_status": "done",
                    "cwd": str(dev_root / "user-skills"),
                },
                {
                    "pane_id": "wC1:p2",
                    "workspace_id": "wC1",
                    "name": "orphan-sub-writer-r1",
                    "agent_status": "done",
                    "cwd": str(dev_root),
                },
                {
                    "pane_id": "wC2:p1",
                    "workspace_id": "wC2",
                    "name": "active-goal-writer-r1",
                    "agent_status": "idle",
                    "cwd": str(wt_active),
                },
            ],
        }

    def test_relative_symlink_points_to_team_lead_canonical_script(self):
        self.assertTrue(M2_SYMLINK_PATH.is_symlink())
        raw_link = os.readlink(M2_SYMLINK_PATH)
        self.assertFalse(raw_link.startswith("/"), f"Expected relative symlink, got {raw_link}")
        self.assertEqual(raw_link, "../../team-lead/scripts/local_hygiene_patrol.py")
        self.assertEqual(M2_SYMLINK_PATH.resolve(), PATROL_PATH.resolve())

    def test_scan_and_safe_gc_reclaims_only_clean_merged_and_protects_dirty_unmerged_held_and_m2(self):
        holder, dev_root, handoff_root = self._build_fixture_env()
        self.addCleanup(holder.cleanup)
        herdr_snap = self._sample_herdr_snapshot(dev_root)
        repo_dir = dev_root / "user-skills"

        closed_pane_cmds: list[str] = []

        def fake_runner(cmd, *, cwd=None, check=False):
            if list(cmd)[:3] == ["herdr", "pane", "close"]:
                closed_pane_cmds.append(cmd[3])
                return subprocess.CompletedProcess(list(cmd), 0, stdout='{"ok":true}', stderr="")
            return _git(cmd, cwd=cwd, check=check)

        def local_merge_checker(r_path: Path, branch: str | None) -> bool:
            if not branch:
                return False
            # Only branches whose tip is an ancestor of main AND have at least one commit or explicit merge
            anc = _git(["git", "-C", str(r_path), "merge-base", "--is-ancestor", branch, "main"], check=False)
            return anc.returncode == 0

        scan_res = patrol.scan_environment(
            dev_root=dev_root,
            handoff_root=handoff_root,
            repos=("user-skills",),
            herdr_json=herdr_snap,
            runner=fake_runner,
            merge_checker=local_merge_checker,
        )
        reclaim_pane_ids = [p["pane_id"] for p in scan_res["herdr"]["reclaimable_panes"]]
        self.assertEqual(reclaim_pane_ids, ["wC1:p1", "wC1:p2"])
        # Protected w2J and M2 pane wBV:p1 must never be marked reclaimable
        self.assertNotIn("w2J:p1", reclaim_pane_ids)
        self.assertNotIn("wBV:p1", reclaim_pane_ids)
        self.assertNotIn("wC2:p1", reclaim_pane_ids)

        clean_wts = [Path(w["worktree"]).name for w in scan_res["worktrees"]["clean_merged_worktrees"]]
        preserved_by_name = {
            Path(w["worktree"]).name: w["preserve_reasons"]
            for w in scan_res["worktrees"]["preserved_worktrees"]
        }
        self.assertEqual(clean_wts, ["goal-clean-merged"])
        self.assertIn("dirty", preserved_by_name["goal-dirty-wt"])
        self.assertIn("unmerged", preserved_by_name["goal-unmerged-wt"])
        self.assertIn("held", preserved_by_name["goal-held-wt"])
        self.assertIn("active_pane", preserved_by_name["goal-active-pane"])

        gc_res = patrol.run_safe_gc(
            dev_root=dev_root,
            handoff_root=handoff_root,
            state_dir=dev_root / ".gc-state",
            repos=("user-skills",),
            herdr_json=herdr_snap,
            dry_run=False,
            runner=fake_runner,
            merge_checker=local_merge_checker,
        )
        self.assertTrue(gc_res["ok"])
        self.assertEqual(closed_pane_cmds, ["wC1:p1", "wC1:p2"])
        self.assertFalse((dev_root / "worktrees" / "goal-clean-merged").exists())
        # All 4 protected worktrees MUST still exist untouched on disk
        self.assertTrue((dev_root / "worktrees" / "goal-dirty-wt" / "uncommitted.txt").is_file())
        self.assertTrue((dev_root / "worktrees" / "goal-unmerged-wt" / "unmerged.txt").is_file())
        self.assertTrue((dev_root / "worktrees" / "goal-held-wt").is_dir())
        self.assertTrue((dev_root / "worktrees" / "goal-active-pane").is_dir())
        self.assertTrue(repo_dir.is_dir())

    def test_production_run_safe_gc_without_merge_checker_preserves_zero_commit_and_held_worktrees(self):
        holder, dev_root, handoff_root = self._build_fixture_env()
        self.addCleanup(holder.cleanup)
        herdr_snap = self._sample_herdr_snapshot(dev_root)

        # Also create a brand-new worktree branched from main with 0 commits and no active pane
        wt_zero = dev_root / "worktrees" / "goal-fresh-zero-commit"
        _git(["git", "-C", str(dev_root / "user-skills"), "worktree", "add", "-q", "-b", "feat/fresh-zero", str(wt_zero), "main"])

        def prod_runner(cmd, *, cwd=None, check=False):
            if list(cmd)[:3] == ["herdr", "pane", "close"]:
                return subprocess.CompletedProcess(list(cmd), 0, stdout='{"ok":true}', stderr="")
            if list(cmd)[:3] == ["gh", "pr", "list"]:
                return subprocess.CompletedProcess(list(cmd), 0, stdout="[]", stderr="")
            return _git(cmd, cwd=cwd, check=check)

        # Call run_safe_gc with merge_checker=None and dry_run=False (production path)
        gc_res = patrol.run_safe_gc(
            dev_root=dev_root,
            handoff_root=handoff_root,
            state_dir=dev_root / ".gc-state",
            repos=("user-skills",),
            herdr_json=herdr_snap,
            dry_run=False,
            runner=prod_runner,
            merge_checker=None,
        )
        self.assertTrue(gc_res["ok"])
        reclaimed_names = [Path(r["worktree"]).name for r in gc_res["reclaimed_worktrees"]]
        self.assertEqual(reclaimed_names, ["goal-clean-merged"])
        # Zero-commit brand-new worktree, held worktree, dirty worktree, unmerged worktree, and active-pane worktree MUST survive
        self.assertTrue(wt_zero.is_dir())
        self.assertTrue((dev_root / "worktrees" / "goal-held-wt").is_dir())
        self.assertTrue((dev_root / "worktrees" / "goal-dirty-wt").is_dir())
        self.assertTrue((dev_root / "worktrees" / "goal-unmerged-wt").is_dir())
        self.assertTrue((dev_root / "worktrees" / "goal-active-pane").is_dir())

    def test_loop_check_detects_dirty_main_and_behind_origin_and_emits_verified_dual_zone_alert(self):
        holder, dev_root, handoff_root = self._build_fixture_env()
        self.addCleanup(holder.cleanup)
        repo_dir = dev_root / "user-skills"
        remote_bare = dev_root.parent / "remotes" / "user-skills.git"

        # Advance origin/main by 1 commit from a separate clone so main checkout is behind origin/main
        other_clone = dev_root.parent / "other-clone"
        _git(["git", "clone", "-q", str(remote_bare), str(other_clone)])
        _git(["git", "-C", str(other_clone), "config", "user.email", "test@example.com"])
        _git(["git", "-C", str(other_clone), "config", "user.name", "Test"])
        (other_clone / "upstream.txt").write_text("new upstream commit\n", encoding="utf-8")
        _git(["git", "-C", str(other_clone), "add", "upstream.txt"])
        _git(["git", "-C", str(other_clone), "commit", "-qm", "upstream advance"])
        _git(["git", "-C", str(other_clone), "push", "-q", "origin", "main"])
        _git(["git", "-C", str(repo_dir), "fetch", "-q", "origin"])

        # Dirty main checkout
        (repo_dir / "dirty_local.py").write_text("print('dirty')\n", encoding="utf-8")

        mock_jev = {
            "answers": {
                "hygiene_classification": {"choice": "ACTIONABLE_HYGIENE_DRIFT"},
                "actionable_confidence": {"noul": 0.91},
                "urgency_score": {"score": 0.78},
            }
        }

        payload = patrol.run_loop_check(
            dev_root=dev_root,
            handoff_root=handoff_root,
            state_dir=dev_root / ".gc-state",
            target_date=date(2026, 11, 19),
            repos=("user-skills",),
            herdr_json={"workspaces": [], "agents": []},
            dry_run=True,
            mock_jev_json=mock_jev,
            merge_checker=lambda r_path, br: False,
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["has_delta"])
        self.assertTrue(payload["actionable"])
        self.assertEqual(payload["jev"]["decision_source"], "jev_high_confidence")
        kinds = {a["kind"] for a in payload["scan"]["anomalies"]}
        self.assertIn("dirty_main_checkout", kinds)
        self.assertIn("behind_origin_main", kinds)

        alert = payload["alert_issue"]
        self.assertIsNotNone(alert)
        self.assertEqual(alert["assignee_email"], "lee@multica.local")
        self.assertEqual(alert["priority"], "medium")
        verify_res = issue_brief.verify_dual_zone_issue(alert["markdown"])
        self.assertTrue(verify_res["ok"], verify_res)
        self.assertLessEqual(verify_res["human_zone_lines"], 15)

    def test_jev_low_confidence_suppresses_benign_drift_and_fallback_handles_unavailable_jev(self):
        holder, dev_root, handoff_root = self._build_fixture_env()
        self.addCleanup(holder.cleanup)
        repo_dir = dev_root / "user-skills"
        (repo_dir / "scratch.tmp").write_text("temp\n", encoding="utf-8")

        low_jev = {
            "answers": {
                "hygiene_classification": {"choice": "BENIGN_TRANSIENT_NOISE"},
                "actionable_confidence": {"noul": 0.18},
                "urgency_score": {"score": 0.10},
            }
        }
        suppressed = patrol.run_loop_check(
            dev_root=dev_root,
            handoff_root=handoff_root,
            target_date=date(2026, 11, 19),
            repos=("user-skills",),
            herdr_json={"workspaces": [], "agents": []},
            dry_run=True,
            mock_jev_json=low_jev,
            merge_checker=lambda r_path, br: False,
        )
        self.assertFalse(suppressed["actionable"])
        self.assertIsNone(suppressed["alert_issue"])
        self.assertEqual(suppressed["dual_zone_markdown"], "")

        # Deterministic fallback when Jev returns None (unavailable)
        fallback_res = patrol.run_loop_check(
            dev_root=dev_root,
            handoff_root=handoff_root,
            target_date=date(2026, 11, 19),
            repos=("user-skills",),
            herdr_json={"workspaces": [], "agents": []},
            dry_run=True,
            jev_fn=lambda *a, **kw: None,
            merge_checker=lambda r_path, br: False,
        )
        self.assertTrue(fallback_res["actionable"])
        self.assertEqual(fallback_res["jev"]["decision_source"], "deterministic_fallback_unavailable")
        self.assertIsNotNone(fallback_res["alert_issue"])

    def test_multica_loop_runner_registers_and_executes_local_herdr_worktree_gc(self):
        spec = loop_runner.resolve_loop_spec("local-herdr-worktree-gc")
        self.assertEqual(spec.loop_id, "local-herdr-worktree-gc")
        self.assertEqual(spec.default_priority, "medium")
        self.assertIn(spec, loop_runner.CANONICAL_LOOP_SPECS)

        dry_sync = loop_runner.sync_autopilots(dry_run=True)
        loop_ids = [row["loop_id"] for row in dry_sync["loops"]]
        self.assertIn("local-herdr-worktree-gc", loop_ids)

        holder, dev_root, handoff_root = self._build_fixture_env()
        self.addCleanup(holder.cleanup)
        (dev_root / "user-skills" / "dirty.txt").write_text("dirty\n", encoding="utf-8")

        collected = patrol.run_loop_check(
            dev_root=dev_root,
            handoff_root=handoff_root,
            target_date=date(2026, 11, 19),
            repos=("user-skills",),
            herdr_json={"workspaces": [], "agents": []},
            dry_run=True,
            jev_fn=lambda *a, **kw: None,
            merge_checker=lambda r_path, br: False,
        )

        with tempfile.TemporaryDirectory() as state_tmp:
            state_path = Path(state_tmp) / "watermarks.json"
            created_bodies: list[str] = []

            def fake_multica(args, *, stdin_text=None, **kwargs):
                if args[:2] == ["issue", "list"]:
                    return subprocess.CompletedProcess(["multica", *args], 0, stdout='{"issues":[]}', stderr="")
                if args[:2] == ["issue", "create"]:
                    created_bodies.append(stdin_text or "")
                    return subprocess.CompletedProcess(
                        ["multica", *args],
                        0,
                        stdout=json.dumps({"id": "hyg-1", "identifier": "TIA-602"}),
                        stderr="",
                    )
                return subprocess.CompletedProcess(["multica", *args], 0, stdout="{}", stderr="")

            mock_runner_jev = {
                "answers": {
                    "actionable_probability": {"noul": 0.89},
                    "delivery_route": {"choice": "dual_zone_issue"},
                    "delivery_route_match": {"noul": 0.94},
                    "urgency_score": {"score": 0.72},
                }
            }
            res = loop_runner.run_loop(
                "local-herdr-worktree-gc",
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

    def test_cli_subcommands_scan_run_gc_and_loop_check(self):
        holder, dev_root, handoff_root = self._build_fixture_env()
        self.addCleanup(holder.cleanup)
        herdr_file = dev_root / "herdr.json"
        herdr_file.write_text(json.dumps({"workspaces": [], "agents": []}), encoding="utf-8")
        bin_dir = dev_root / "fake-bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        fake_gh = bin_dir / "gh"
        fake_gh.write_text("#!/usr/bin/env sh\necho '[]'\n", encoding="utf-8")
        fake_gh.chmod(0o755)
        cli_env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}", "GTT_JEV_LIVE": "0"}

        for subcmd, extra in (
            ("scan", []),
            ("run-gc", ["--dry-run"]),
            ("collect", ["--dry-run", "--date", "2026-11-19"]),
            ("loop-check", ["--dry-run", "--date", "2026-11-19"]),
        ):
            proc = subprocess.run(
                [
                    sys.executable,
                    str(M2_SYMLINK_PATH),
                    subcmd,
                    "--dev-root",
                    str(dev_root),
                    "--handoff-root",
                    str(handoff_root),
                    "--repos",
                    "user-skills",
                    "--herdr-json",
                    str(herdr_file),
                    *extra,
                ],
                env=cli_env,
                capture_output=True,
                text=True,
                check=True,
            )
            parsed = json.loads(proc.stdout)
            self.assertTrue(parsed["ok"], subcmd)

    def test_collect_auto_excludes_runtime_caches_and_fast_forwards_behind_main(self):
        holder, dev_root, handoff_root = self._build_fixture_env()
        self.addCleanup(holder.cleanup)
        repo_dir = dev_root / "user-skills"
        remote_bare = dev_root.parent / "remotes" / "user-skills.git"

        # Advance origin/main by 1 commit
        other_clone = dev_root.parent / "other-ff-clone"
        _git(["git", "clone", "-q", str(remote_bare), str(other_clone)])
        _git(["git", "-C", str(other_clone), "config", "user.email", "test@example.com"])
        _git(["git", "-C", str(other_clone), "config", "user.name", "Test"])
        (other_clone / "ff_update.txt").write_text("ff\n", encoding="utf-8")
        _git(["git", "-C", str(other_clone), "add", "ff_update.txt"])
        _git(["git", "-C", str(other_clone), "commit", "-qm", "ff commit"])
        _git(["git", "-C", str(other_clone), "push", "-q", "origin", "main"])
        _git(["git", "-C", str(repo_dir), "fetch", "-q", "origin"])

        # Add known runtime cache dir on main checkout + remove dirty fixture worktree so repo has 0 residual anomalies
        import shutil as _shutil
        _shutil.rmtree(dev_root / "worktrees" / "goal-dirty-wt")
        _git(["git", "-C", str(repo_dir), "worktree", "prune"])
        cache_dir = repo_dir / ".agents"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "local-state.json").write_text("{}", encoding="utf-8")

        res = patrol.run_loop_check(
            dev_root=dev_root,
            handoff_root=handoff_root,
            state_dir=dev_root / ".gc-state",
            target_date=date(2026, 11, 20),
            repos=("user-skills",),
            herdr_json={"workspaces": [], "agents": []},
            dry_run=False,
            jev_fn=lambda *a, **kw: None,
            merge_checker=lambda _r, _b: False,
        )
        self.assertEqual(res["anomalies"], [])
        self.assertFalse(res["actionable"])
        self.assertEqual(len(res["auto_gc"]["fast_forwarded_repos"]), 1)
        self.assertEqual(len(res["auto_gc"]["excluded_runtime_cache_repos"]), 1)
        st = _git(["git", "-C", str(repo_dir), "status", "--porcelain"])
        self.assertEqual(st.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
