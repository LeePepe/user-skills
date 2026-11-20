#!/usr/bin/env python3
"""Hermetic unit tests for `icloud_skills_sync.py` and multi-device shard merge in `retro_story_engine.py`."""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SYNC_MOD = _load_module("icloud_skills_sync_test", ROOT / "skills" / "team-lead" / "scripts" / "icloud_skills_sync.py")
RETRO_ENGINE = _load_module("retro_story_engine_icloud_test", ROOT / "skills" / "retro-loop" / "scripts" / "retro_story_engine.py")


def _init_git_repo(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
    (repo_dir / ".githooks").mkdir(exist_ok=True)
    for skill_name in ("team-lead", "m2-dispatcher", "issue-brief"):
        sk_dir = repo_dir / "skills" / skill_name
        (sk_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (sk_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: Test {skill_name}\n---\n# {skill_name}\nLine 2\n",
            encoding="utf-8",
        )
        (sk_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": skill_name, "version": "1.0.0"}) + "\n",
            encoding="utf-8",
        )
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo_dir, check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return sha


class ICloudSkillsSyncTests(unittest.TestCase):
    def test_relative_symlink_in_m2_dispatcher(self) -> None:
        link_path = ROOT / "skills" / "m2-dispatcher" / "scripts" / "icloud_skills_sync.py"
        self.assertTrue(link_path.is_symlink())
        self.assertEqual(os.readlink(link_path), "../../team-lead/scripts/icloud_skills_sync.py")
        self.assertEqual(link_path.resolve(), (ROOT / "skills" / "team-lead" / "scripts" / "icloud_skills_sync.py").resolve())

    def test_publish_pull_ff_only_and_symlink_healer_preserves_external_repos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            origin_bare = base / "origin.git"
            subprocess.run(["git", "init", "--bare", "-b", "main", str(origin_bare)], check=True, capture_output=True)

            # Producer checkout (Mac mini)
            mac_mini_repo = base / "mac-mini" / "user-skills"
            sha1 = _init_git_repo(mac_mini_repo)
            subprocess.run(["git", "remote", "add", "origin", str(origin_bare)], cwd=mac_mini_repo, check=True)
            subprocess.run(["git", "push", "-u", "origin", "main"], cwd=mac_mini_repo, check=True, capture_output=True)

            # Consumer checkout (MacBook) initially at sha1
            macbook_dev = base / "macbook"
            macbook_repo = macbook_dev / "user-skills"
            subprocess.run(["git", "clone", str(origin_bare), str(macbook_repo)], check=True, capture_output=True)

            # External sibling Skills repo on MacBook (e.g. repo-kit, save-tool)
            external_skills_repo = macbook_dev / "Skills"
            ext_repo_kit = external_skills_repo / "skills" / "repo" / "repo-kit"
            ext_repo_kit.mkdir(parents=True)
            (ext_repo_kit / "SKILL.md").write_text("---\nname: repo-kit\n---\n# repo-kit\n", encoding="utf-8")

            icloud_dir = base / "AIDashSync"

            # Advance Mac mini with a new skill `ios-concurrency-swiftui` and push to origin
            new_sk = mac_mini_repo / "skills" / "ios-concurrency-swiftui"
            new_sk.mkdir(parents=True)
            (new_sk / "SKILL.md").write_text("---\nname: ios-concurrency-swiftui\n---\n# iOS\n", encoding="utf-8")
            subprocess.run(["git", "config", "user.email", "mini@example.com"], cwd=mac_mini_repo, check=True)
            subprocess.run(["git", "config", "user.name", "Mini"], cwd=mac_mini_repo, check=True)
            subprocess.run(["git", "add", "."], cwd=mac_mini_repo, check=True)
            subprocess.run(["git", "commit", "-m", "add ios-concurrency-swiftui"], cwd=mac_mini_repo, check=True, capture_output=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=mac_mini_repo, check=True, capture_output=True)
            sha2 = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=mac_mini_repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertNotEqual(sha1, sha2)

            # Publish from Mac mini to iCloud
            pub1 = SYNC_MOD.publish_skills_to_icloud(
                repo_root=mac_mini_repo,
                icloud_root=icloud_dir,
                hostname="MacMini",
            )
            self.assertTrue(pub1["published"])
            self.assertEqual(pub1["head_sha"], sha2)
            self.assertEqual(pub1["skill_count"], 4)
            manifest = SYNC_MOD.read_sync_manifest(icloud_dir)
            assert manifest is not None
            self.assertEqual(manifest["head_sha"], sha2)
            self.assertEqual(manifest["updated_by_host"], "macmini")
            self.assertTrue((icloud_dir / "skills-mirror" / "ios-concurrency-swiftui" / "SKILL.md").is_file())

            # Second publish at same SHA is idempotent no-op
            pub2 = SYNC_MOD.publish_skills_to_icloud(
                repo_root=mac_mini_repo,
                icloud_root=icloud_dir,
                hostname="MacMini",
            )
            self.assertFalse(pub2["published"])
            self.assertEqual(pub2["reason"], "already_at_head_sha")

            # Prepare MacBook client dirs with 1 external symlink and 1 dangling user-skills symlink
            client_dirs = [macbook_dev / name for name in ("agents", "copilot", "claude", "codex")]
            for c_dir in client_dirs:
                c_dir.mkdir(parents=True)
                (c_dir / "repo-kit").symlink_to(ext_repo_kit)
                (c_dir / "obsolete-removed-skill").symlink_to(macbook_repo / "skills" / "obsolete-removed-skill")

            # Pull on MacBook: should fast-forward from sha1 -> sha2 and heal all 4 client dirs
            pull_res = SYNC_MOD.pull_skills_from_icloud(
                repo_root=macbook_repo,
                icloud_root=icloud_dir,
                client_dirs=client_dirs,
                sibling_skills_repo=external_skills_repo,
                run_pi_harness=False,
            )
            self.assertTrue(pull_res["ok"])
            self.assertTrue(pull_res["git_pulled"])
            self.assertEqual(pull_res["action"], "git_ff_pulled")
            self.assertEqual(pull_res["local_head_sha"], sha2)

            heal_info = pull_res["heal_symlinks"]
            self.assertEqual(heal_info["removed_dangling_count"], 4)
            for c_dir in client_dirs:
                self.assertFalse(os.path.lexists(c_dir / "obsolete-removed-skill"))
                # External symlink preserved
                self.assertEqual((c_dir / "repo-kit").resolve(), ext_repo_kit.resolve())
                # Newly pulled skill linked
                self.assertEqual(
                    (c_dir / "ios-concurrency-swiftui").resolve(),
                    (macbook_repo / "skills" / "ios-concurrency-swiftui").resolve(),
                )

    def test_dirty_worktree_skips_git_pull_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "user-skills"
            _init_git_repo(repo)
            # Dirty tracked file
            (repo / "skills" / "team-lead" / "SKILL.md").write_text("---\nname: dirty\n---\n", encoding="utf-8")

            icloud_dir = base / "AIDashSync"
            icloud_dir.mkdir(parents=True)
            (icloud_dir / "skills-sync-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "repo": "LeePepe/user-skills",
                        "head_sha": "f" * 40,
                        "updated_at": "2026-11-20T12:00:00+08:00",
                        "updated_by_host": "other-mac",
                        "skill_count": 3,
                        "skills": [],
                    }
                ),
                encoding="utf-8",
            )
            res = SYNC_MOD.pull_skills_from_icloud(
                repo_root=repo,
                icloud_root=icloud_dir,
                heal=False,
                run_pi_harness=False,
            )
            self.assertEqual(res["action"], "skipped_dirty_or_non_main")
            self.assertFalse(res["git_pulled"])

    def test_export_telemetry_shards_and_multi_device_dedup_in_retro_story_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "user-skills"
            _init_git_repo(repo)
            # Give team-lead a deterministic script so S6/script_items are populated
            tl_scripts = repo / "skills" / "team-lead" / "scripts"
            tl_scripts.mkdir(parents=True, exist_ok=True)
            (tl_scripts / "icloud_skills_sync.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            tl_refs = repo / "skills" / "team-lead" / "references"
            tl_refs.mkdir(parents=True, exist_ok=True)
            (tl_refs / "ledger.md").write_text("# Ledger\n", encoding="utf-8")

            icloud_dir = base / "AIDashSync"
            now_iso = datetime.now(timezone.utc).isoformat()

            # Host A (mac-mini): warehouse + magpie usage + handoff ledger
            wh_mini = base / "mini-warehouse.db"
            with contextlib.closing(sqlite3.connect(wh_mini)) as conn:
                conn.execute(
                    """
                    CREATE TABLE fact_turn (
                        turn_uuid TEXT, session_id TEXT, ts TEXT, role TEXT, model TEXT,
                        attribution_skill TEXT, input_tokens INTEGER, output_tokens INTEGER,
                        cache_read INTEGER, tool_calls TEXT, skill_names TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE script_call (
                        call_uid TEXT, source TEXT, session_id TEXT, turn_uuid TEXT,
                        skill TEXT, script_path TEXT, exit_code INTEGER, duration_ms REAL, ts TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE reference_hits (
                        hit_uid TEXT, source TEXT, session_id TEXT, turn_uuid TEXT,
                        skill TEXT, reference_path TEXT, byte_len INTEGER, ts TEXT
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO fact_turn VALUES ('t-mini-1', 'sess-mini-1', ?, 'assistant', 'claude-opus', 'team-lead', 3000, 400, 9000, '[\"Bash\"]', '[\"team-lead\"]')",
                    (now_iso,),
                )
                conn.execute(
                    "INSERT INTO script_call VALUES ('c-mini-1', 'copilot_cli', 'sess-mini-1', 't-mini-1', 'team-lead', 'skills/team-lead/scripts/icloud_skills_sync.py', 0, 145.0, ?)",
                    (now_iso,),
                )
                conn.execute(
                    "INSERT INTO reference_hits VALUES ('h-mini-1', 'copilot_cli', 'sess-mini-1', 't-mini-1', 'team-lead', 'skills/team-lead/references/ledger.md', 512, ?)",
                    (now_iso,),
                )
                conn.commit()

            mp_mini = base / "mini-magpie.jsonl"
            mp_mini.write_text(
                json.dumps(
                    {
                        "route_id": 1001,
                        "t": now_iso,
                        "agent": "copilot",
                        "req": "group/steering",
                        "served": "gemini-4-carbon",
                        "status": 200,
                        "in": 2000,
                        "out": 250,
                        "cache_read": 8000,
                        "tries": 1,
                        "ttft_ms": 500,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            handoff_mini = base / "handoff"
            (handoff_mini / "demo-goal").mkdir(parents=True)
            (handoff_mini / "demo-goal" / "ledger.md").write_text(
                "# Goal: demo-goal\n- 2026-11-20T10:00:00+08:00 | EVT=GOAL_DONE | note=done\n",
                encoding="utf-8",
            )

            mini_local_usage = base / "mini-local-usage.jsonl"
            exp_mini_1 = SYNC_MOD.export_telemetry_shards(
                repo_root=repo,
                icloud_root=icloud_dir,
                hostname="leedemac-mini",
                local_usage_path=mini_local_usage,
                magpie_usage_path=mp_mini,
                copilot_db_path=base / "missing-cp.db",
                warehouse_db_path=wh_mini,
                handoff_root=handoff_mini,
            )
            self.assertTrue(exp_mini_1["ok"])
            self.assertEqual(exp_mini_1["shard_total_records"], 4)
            self.assertEqual(exp_mini_1["ledger_goal_count"], 1)

            # Re-running export on Mac mini is strictly idempotent (0 duplicate appends)
            exp_mini_2 = SYNC_MOD.export_telemetry_shards(
                repo_root=repo,
                icloud_root=icloud_dir,
                hostname="leedemac-mini",
                local_usage_path=mini_local_usage,
                magpie_usage_path=mp_mini,
                copilot_db_path=base / "missing-cp.db",
                warehouse_db_path=wh_mini,
                handoff_root=handoff_mini,
            )
            self.assertEqual(exp_mini_2["local_appended_records"], 0)
            self.assertEqual(exp_mini_2["shard_appended_records"], 0)
            self.assertEqual(exp_mini_2["shard_total_records"], 4)

            # Simulate Host B (MacBook) shard in iCloud with:
            # 1) A brand-new MacBook session for `issue-brief` and `team-lead`
            # 2) An accidental duplicate copy of `c-mini-1` (must be deduplicated!)
            shards_dir = icloud_dir / "telemetry-shards"
            mini_shard_lines = (shards_dir / "leedemac-mini-skill-usage.jsonl").read_text(encoding="utf-8").splitlines()
            dup_record = json.loads(mini_shard_lines[0])
            macbook_records = [
                dup_record,
                {
                    "kind": "skill_turn",
                    "host": "macbook-pro",
                    "channel": "codex",
                    "session_id": "sess-mbp-99",
                    "turn_id": "t-mbp-1",
                    "ts": now_iso,
                    "skill": "issue-brief",
                    "input_tokens": 1500,
                    "output_tokens": 300,
                    "cache_read": 6000,
                    "tool_calls": ["Read"],
                },
                {
                    "kind": "script_call",
                    "host": "macbook-pro",
                    "channel": "codex",
                    "call_uid": "c-mbp-1",
                    "session_id": "sess-mbp-99",
                    "turn_id": "t-mbp-1",
                    "ts": now_iso,
                    "skill": "team-lead",
                    "script_path": "skills/team-lead/scripts/icloud_skills_sync.py",
                    "exit_code": 0,
                    "duration_ms": 210.0,
                },
                {
                    "kind": "gateway_usage",
                    "host": "macbook-pro",
                    "ts": now_iso,
                    "route_id": 2002,
                    "agent": "codex",
                    "req": "group/coding",
                    "model": "gpt-5.3-codex",
                    "status": 200,
                    "in": 4000,
                    "out": 600,
                    "cache_read": 16000,
                    "tries": 1,
                    "ttft_ms": 620,
                },
            ]
            (shards_dir / "macbook-pro-skill-usage.jsonl").write_text(
                "\n".join(json.dumps(r) for r in macbook_records) + "\n",
                encoding="utf-8",
            )

            icloud_roi_target = icloud_dir / "jobs" / "aidata-digest" / "skills-roi.json"
            roi_report = RETRO_ENGINE.compute_skill_roi_diagnostics(
                warehouse_db=wh_mini,
                copilot_db=base / "missing-cp.db",
                skills_root=repo / "skills",
                dev_root=base,
                magpie_usage=mp_mini,
                telemetry_shards_dir=shards_dir,
                mirror_icloud_roi=True,
                icloud_roi_path=icloud_roi_target,
            )
            self.assertTrue(roi_report["ok"])
            details_by_skill = {d["skill"]: d for d in roi_report["skill_details"]}

            # `issue-brief` was used ONLY on MacBook; must appear active via shard merge!
            self.assertEqual(details_by_skill["issue-brief"]["loads_30d"], 1)
            self.assertEqual(details_by_skill["issue-brief"]["source_breakdown"]["codex"], 1)

            # `team-lead` had 1 local warehouse script call + 1 MacBook script call = 2 calls (no double counting of mini's local + mini's shard + duplicate!)
            tl_scripts_stats = {s["rel_path"]: s for s in details_by_skill["team-lead"]["scripts"]}
            self.assertEqual(tl_scripts_stats["scripts/icloud_skills_sync.py"]["calls_30d"], 2)

            # Verify iCloud `AIDashSync/jobs/aidata-digest/skills-roi.json` was mirrored
            self.assertTrue(icloud_roi_target.is_file())
            mirrored_doc = json.loads(icloud_roi_target.read_text(encoding="utf-8"))
            self.assertEqual(mirrored_doc["schema_version"], 1)
            self.assertEqual(mirrored_doc["diagnostics"]["query_id"], "retro/skill_roi_diagnostics")

            # Verify compute_agent_efficiency merges MacBook gateway_usage shard without double-counting route_id 1001
            eff_report = RETRO_ENGINE.compute_agent_efficiency(
                warehouse_db=wh_mini,
                magpie_usage=mp_mini,
                copilot_db=base / "missing-cp.db",
                telemetry_shards_dir=shards_dir,
            )
            self.assertEqual(eff_report["metrics"]["M3_flow_and_blockers"]["sampled_requests"], 2)

    def test_install_launchd_writes_valid_plist_and_post_merge_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "user-skills"
            _init_git_repo(repo)
            icloud_dir = base / "AIDashSync"
            plist_file = base / "LaunchAgents" / "com.tianpli.skills.icloud-sync.plist"

            res = SYNC_MOD.install_launchd_agent(
                plist_path=plist_file,
                script_path=repo / "skills" / "team-lead" / "scripts" / "icloud_skills_sync.py",
                icloud_root=icloud_dir,
                repo_root=repo,
                load_agent=False,
                install_post_merge_hook=True,
            )
            self.assertTrue(res["ok"])
            self.assertTrue(plist_file.is_file())
            parsed_plist = plistlib.loads(plist_file.read_bytes())
            self.assertEqual(parsed_plist["Label"], "com.tianpli.skills.icloud-sync")
            self.assertEqual(parsed_plist["StartInterval"], 300)
            self.assertIn(str(icloud_dir / "skills-sync-manifest.json"), parsed_plist["WatchPaths"])
            # Ensure git status --porcelain in repo remains clean (no untracked .githooks/post-merge dirtiness)
            st_proc = subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True)
            self.assertEqual(st_proc.stdout.strip(), "")
            self.assertTrue((repo / ".git" / "hooks" / "post-merge").is_file())

            # Verify CLI status subcommand returns valid JSON
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = SYNC_MOD.main(
                    [
                        "status",
                        "--repo-root",
                        str(repo),
                        "--icloud-root",
                        str(icloud_dir),
                        "--client-dir",
                        str(base / "empty-client"),
                        "--local-usage-path",
                        str(base / "none.jsonl"),
                    ]
                )
            self.assertEqual(rc, 0)
            status_json = json.loads(buf.getvalue())
            self.assertTrue(status_json["ok"])
            self.assertEqual(status_json["local_skill_count"], 3)


if __name__ == "__main__":
    unittest.main()
