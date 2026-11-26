#!/usr/bin/env python3
"""Regression tests for Goal `fix-pseudo-skill-skills`.

Covers:
1. `repo_gate_auditor.py` (`_compact_proposal` & `_maybe_compact_report`):
   - Preserves `skill == "repo-kit"`, `layer == "L2"`, `ref`, `metric_id`, and non-empty
     bounded `title` / `plain_reason` / `action` even under `minimal=True` and oversized
     multi-repo compaction, while keeping compacted JSON strictly `<= 15,000` bytes.
2. `multica_skills_autopilot.py`:
   - Deterministic skill resolution maps compacted `RG-001` / `RG-002` and `repo-kit` /
     `repo-gate` proposals to `skill="repo-kit"` (`layer="L2"`) with concrete
     `Proposal & Recommended Action` cells (never bare `—` or `演进 skills 渐进式防呆契约`).
   - Rejects/skips unattributable proposals whose skill is empty or in the pseudo-skill
     denylist (`{"skills", "skill", "scripts", "references", "tests", "main", "*"}`).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
AUDITOR_PATH = ROOT / "skills" / "repo-kit" / "scripts" / "repo_gate_auditor.py"
AUTOPILOT_PATH = ROOT / "skills" / "retro-loop" / "scripts" / "multica_skills_autopilot.py"


def _load_mod(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AUDITOR = _load_mod("test_fix_pseudo_auditor_mod", AUDITOR_PATH)
AUTOPILOT = _load_mod("test_fix_pseudo_autopilot_mod", AUTOPILOT_PATH)


def _init_git_repo(repo_dir: Path, *, hooks_path: str | None = None) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".git").mkdir(parents=True, exist_ok=True)
    cfg_lines = ["[core]", "\trepositoryformatversion = 0"]
    if hooks_path:
        cfg_lines.append(f"\thooksPath = {hooks_path}")
    (repo_dir / ".git" / "config").write_text("\n".join(cfg_lines) + "\n", encoding="utf-8")
    return repo_dir


def _write_exec(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


class FixPseudoSkillSkillsTests(unittest.TestCase):
    def test_compact_proposal_minimal_and_oversized_report_stay_under_15kb_with_repo_kit_fields(self) -> None:
        raw_prop = {
            "id": "skill-retro-repo-kit-rg-001",
            "ref": "retro://skill-retro/repo-kit/rg-001",
            "metric_id": "repo-gate-rg-001",
            "rule_id": "RG-001",
            "category": "missing_verify_entrypoint",
            "priority": "P1",
            "speedup_angle": "angle_d_tiered_fast_fail",
            "affected_repos": [f"sample-repo-{i:02d}" for i in range(12)],
            "affected_repos_count": 12,
            "title": "[Repo Gate RG-001] Missing executable `scripts/verify` entrypoint " + ("x" * 160),
            "plain_reason": "跨 12 个仓库检测到 `RG-001`，可通过 `repo-kit` 模板统一收敛 " + ("y" * 160),
            "action": "Create executable `scripts/verify` supporting `[--changed|--all]` and wire hooks + CI " + ("z" * 160),
        }
        minimal_prop = AUDITOR._compact_proposal(raw_prop, minimal=True)
        self.assertEqual(minimal_prop["skill"], "repo-kit")
        self.assertEqual(minimal_prop["layer"], "L2")
        self.assertEqual(minimal_prop["ref"], "retro://skill-retro/repo-kit/rg-001")
        self.assertEqual(minimal_prop["metric_id"], "repo-gate-rg-001")
        for field in ("title", "plain_reason", "action"):
            self.assertTrue(minimal_prop[field])
            self.assertLessEqual(len(minimal_prop[field]), 140)

        with tempfile.TemporaryDirectory() as tmp:
            dev_root = Path(tmp) / "Development"
            for idx in range(45):
                repo = _init_git_repo(dev_root / f"drifted-service-repo-{idx:02d}", hooks_path=".git/hooks-local")
                if idx % 3 == 0:
                    _write_exec(repo / ".githooks" / "pre-push", "#!/usr/bin/env bash\necho push\n")
                else:
                    _write_exec(
                        repo / "scripts" / "verify",
                        "#!/usr/bin/env bash\nfor layer in $layers; do context run \"$layer\"; done\n",
                    )
                    _write_exec(
                        repo / ".githooks" / "pre-push",
                        "#!/usr/bin/env bash\nexec \"$REPO_ROOT/scripts/verify\" --all\n",
                    )

            report = AUDITOR.scan_repositories(dev_root=dev_root, flow_map=True, no_cache=True)
            serialized = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
            self.assertTrue(report.get("summary_compacted"))
            self.assertLessEqual(AUDITOR.COMPACT_JSON_BYTES, 15_000)
            self.assertLessEqual(len(serialized), 15_000)
            self.assertGreaterEqual(len(report["actionable_proposals"]), 3)
            for prop in report["actionable_proposals"]:
                self.assertEqual(prop["skill"], "repo-kit")
                self.assertEqual(prop["layer"], "L2")
                self.assertTrue(prop.get("ref"))
                self.assertTrue(prop.get("metric_id"))
                self.assertTrue(prop.get("title"))
                self.assertTrue(prop.get("plain_reason"))
                self.assertTrue(prop.get("action"))

    def test_autopilot_maps_rg_proposals_to_repo_kit_and_drops_pseudo_skills(self) -> None:
        raw_retro = {
            "ok": True,
            "schema": "skill-retro-diagnostic/v1",
            "wall_clock_date": "2026-11-26",
            "window_cst": "30d",
            "source_as_of": "2026-11-26T22:00:00+08:00",
            "sample_size": 42,
            "coverage_status": "healthy",
            "sources_ingested": {"sessions_observed": 42, "trace_spans_ingested": 110, "skills_in_inventory": 28},
            "summary": {"slow_flywheel_ready": True, "observation_pool_count": 2},
            "proposals": [
                # Legacy/minimal compacted RG-001 proposal missing skill, layer, title, action, plain_reason
                {
                    "id": "skill-retro-repo-kit-rg-001",
                    "rule_id": "RG-001",
                    "category": "missing_verify_entrypoint",
                    "priority": "P1",
                    "affected_repos_count": 10,
                },
                # RG-002 proposal carrying pseudo-skill "skills" that should still resolve via RG-002 -> repo-kit
                {
                    "id": "skill-retro-repo-kit-rg-002",
                    "ref": "retro://skill-retro/repo-kit/rg-002",
                    "metric_id": "repo-gate-rg-002",
                    "rule_id": "RG-002",
                    "skill": "skills",
                    "category": "missing_incremental_verify",
                    "priority": "P1",
                    "title": "[Repo Gate RG-002] `scripts/verify` lacks incremental path-scoped selection",
                    "plain_reason": "`shared-ci` 缺少路径级增量选择",
                    "action": "Add incremental `git diff --name-only` path selection to `scripts/verify`.",
                },
                # Unattributable pseudo-skill rows that MUST be dropped
                {"id": "bogus-empty", "skill": "", "category": "trigger_miss", "title": "Orphan empty skill"},
                {"id": "bogus-skills", "skill": "skills", "category": "trigger_miss", "title": "Fake skills row"},
                {"id": "bogus-scripts", "skill": "scripts", "category": "rigid_roadblock", "title": "Fake scripts row"},
                {"id": "bogus-refs", "skill": "references", "category": "deadwood", "title": "Fake references row"},
                {"id": "bogus-tests", "skill": "tests", "category": "deadwood", "title": "Fake tests row"},
                {"id": "bogus-main", "skill": "main", "category": "deadwood", "title": "Fake main row"},
                {"id": "bogus-star", "skill": "*", "category": "deadwood", "title": "Fake wildcard row"},
            ],
            "observation_pool": [
                {"id": "obs-bad", "skill": "skills", "layer": "L1", "category": "deadwood", "title": "Drop me"},
                {"id": "obs-rg", "ref": "retro://skill-retro/repo-gate/rg-006", "layer": "L2", "category": "shared_ci_pin_drift", "title": "Keep repo-kit"},
            ],
        }

        snapshot = AUTOPILOT.collect_skill_retro_snapshot(skill_retro_fn=lambda **_kw: raw_retro)
        proposals = snapshot["skill_retro"]["proposals"]
        self.assertEqual([p["skill"] for p in proposals], ["repo-kit", "repo-kit"])
        self.assertEqual([p["layer"] for p in proposals], ["L2", "L2"])
        for prop in proposals:
            self.assertTrue(prop["title"])
            self.assertTrue(prop["action"])
            self.assertNotIn("演进 `skills`", prop["human_what"])
            self.assertTrue(any("--skill repo-kit" in cmd for cmd in prop["remediation_commands"]))

        obs_pool = snapshot["skill_retro"]["observation_pool"]
        self.assertEqual([o["skill"] for o in obs_pool], ["repo-kit"])

        issue_title = AUTOPILOT.build_issue_title(snapshot)
        self.assertIn("(repo-kit)", issue_title)
        self.assertNotIn("(skills", issue_title)

        md = AUTOPILOT.build_issue_markdown(snapshot, agent_analysis=AUTOPILOT.synthesize_deterministic_analysis(snapshot))
        self.assertIn("| `P1` | `L2` | `repo-kit` | `missing_verify_entrypoint` |", md)
        self.assertIn("| `P1` | `L2` | `repo-kit` | `missing_incremental_verify` |", md)
        self.assertNotIn("| `skills` |", md)
        self.assertNotIn("演进 `skills` 渐进式防呆契约", md)
        for line in md.splitlines():
            if line.startswith("| `P"):
                cell = line.split("|")[5].strip()
                self.assertNotEqual(cell, "—")
                self.assertFalse(cell.startswith("—"))
                self.assertFalse(cell.endswith("—"))

    def test_autopilot_all_pseudo_skills_renders_clean_healthy_state(self) -> None:
        raw_only_pseudo = {
            "ok": True,
            "schema": "skill-retro-diagnostic/v1",
            "wall_clock_date": "2026-11-26",
            "sample_size": 20,
            "coverage_status": "healthy",
            "summary": {"slow_flywheel_ready": False},
            "proposals": [
                {"id": "fake-1", "skill": "skills", "category": "trigger_miss"},
                {"id": "fake-2", "skill": "skill", "category": "trigger_miss"},
            ],
        }
        snapshot = AUTOPILOT.collect_skill_retro_snapshot(skill_retro_fn=lambda **_kw: raw_only_pseudo)
        self.assertEqual(snapshot["skill_retro"]["proposals"], [])
        self.assertEqual(snapshot["top_findings"], [])
        md = AUTOPILOT.build_issue_markdown(snapshot)
        self.assertIn("| — | — | `healthy` | `clean` |", md)
        self.assertNotIn("`skills`", md)


if __name__ == "__main__":
    unittest.main()
