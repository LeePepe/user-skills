#!/usr/bin/env python3
"""Repository-wide verification suite for LeePepe/user-skills."""

from __future__ import annotations

import argparse
import json
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


os.environ.setdefault("GTT_LINEAR_SYNC", "0")

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SKILLS_DIR: Path = REPO_ROOT / "skills"
FENCED_CODE_RE: re.Pattern[str] = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE: re.Pattern[str] = re.compile(r"`[^`\n]+`")
MARKDOWN_LINK_RE: re.Pattern[str] = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
MODEL_SURFACE_BYTE_BUDGETS: dict[str, int] = {
    "skills/workflow-hub/SKILL.md": 12 * 1024,
    "skills/team-lead/SKILL.md": 36 * 1024,
    "skills/m2-dispatcher/SKILL.md": 24 * 1024,
    "skills/personal-m2-assistant/SKILL.md": 16 * 1024,
    "skills/linear-board/SKILL.md": 16 * 1024,
    "skills/retro-loop/SKILL.md": 16 * 1024,
    "skills/story-craft/SKILL.md": 16 * 1024,
    "skills/my-designer/SKILL.md": 12 * 1024,
    "skills/codebase-design/SKILL.md": 12 * 1024,
    "skills/team-lead/references/bots-and-agents.md": 38 * 1024,
    "skills/team-lead/references/ledger.md": 18 * 1024,
    "skills/team-lead/references/runtime-profiles.yaml": 34 * 1024,
    "skills/team-lead/references/playbook.md": 16 * 1024,
}
BANNED_SURFACE_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("../goal-target-task/", ("skills/",)),
    ("执行设计总审门禁", ("skills/",)),
    ("Bot → `艺术家`", ("skills/",)),
    ("遇到产品 UI/UX 设计 Goal 必须转交", ("skills/",)),
    ("gemini-3.8-flash", ("skills/",)),
    ("composer-2.5", ("skills/",)),
    ("kimi-k3-high", ("skills/",)),
    ("[总 45 kg]", ("skills/team-lead/SKILL.md", "skills/team-lead/references/bots-and-agents.md")),
    ("+ 添加动作", ("skills/team-lead/SKILL.md", "skills/team-lead/references/bots-and-agents.md")),
)


@dataclass(frozen=True)
class Frontmatter:
    """Parsed YAML frontmatter fields from a SKILL.md document."""

    fields: dict[str, str]
    body_lines: tuple[str, ...]


def parse_frontmatter(path: Path) -> Frontmatter:
    """Extract top-of-file YAML frontmatter key/value pairs and remaining body lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: missing opening '---' YAML frontmatter delimiter")

    end_index: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_index = idx
            break

    if end_index is None:
        raise ValueError(f"{path}: missing closing '---' YAML frontmatter delimiter")

    parsed: dict[str, str] = {}
    for raw_line in lines[1:end_index]:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in raw_line:
            raise ValueError(f"{path}: invalid frontmatter line: {raw_line!r}")
        key, value = raw_line.split(":", 1)
        parsed[key.strip()] = value.strip().strip("\"'")

    return Frontmatter(fields=parsed, body_lines=tuple(lines[end_index + 1 :]))


def iter_markdown_links(markdown_text: str) -> Sequence[str]:
    """Yield markdown link targets outside fenced and inline code spans."""
    without_fences = FENCED_CODE_RE.sub("", markdown_text)
    without_inline = INLINE_CODE_RE.sub("", without_fences)
    return tuple(match.group(1).strip() for match in MARKDOWN_LINK_RE.finditer(without_inline))


def markdown_anchors(path: Path) -> set[str]:
    """Return GitHub-style heading anchors and explicit HTML ids."""
    text = FENCED_CODE_RE.sub("", path.read_text(encoding="utf-8"))
    anchors = set(re.findall(r"<a\s+id=[\"']([^\"']+)[\"']", text, re.IGNORECASE))
    counts: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE):
        slug = re.sub(r"<[^>]+>", "", heading).lower()
        slug = re.sub(r"[^\w\s-]", "", slug, flags=re.UNICODE)
        slug = re.sub(r"\s+", "-", slug).strip("-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def tracked_and_candidate_files() -> tuple[Path, ...]:
    """Return tracked files plus untracked non-ignored files in the repository."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    raw_paths = [entry.decode("utf-8") for entry in result.stdout.split(b"\0") if entry]
    return tuple(sorted(REPO_ROOT / rel for rel in raw_paths if (REPO_ROOT / rel).is_file()))


class VerifySkillsTests(unittest.TestCase):
    """Contract and integrity checks for skills, metadata, links, and hygiene."""

    def _load_ci_monitor(self):
        path = SKILLS_DIR / "team-lead" / "scripts" / "ci-monitor.py"
        spec = importlib.util.spec_from_file_location("ci_monitor_test", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_ci_monitor_cli_and_layer_zero_contract(self) -> None:
        script = SKILLS_DIR / "team-lead" / "scripts" / "ci-monitor.py"
        self.assertTrue(script.is_file())
        self.assertEqual(script.stat().st_mode & 0o777, 0o755)
        for command in ("workflow", "pr"):
            result = subprocess.run(
                [sys.executable, str(script), command, "--help"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("usage:", result.stdout)
        skill_text = (SKILLS_DIR / "team-lead" / "SKILL.md").read_text(encoding="utf-8")
        agents_text = (SKILLS_DIR / "team-lead" / "references" / "bots-and-agents.md").read_text(encoding="utf-8")
        for text in (skill_text, agents_text):
            self.assertIn("ci-monitor.py", text)
            self.assertIn("Layer 0", text)
            self.assertIn("0 Token", text)
            self.assertIn("pr_monitor", text)
        self.assertIn("不为纯状态轮询启动 LLM", skill_text)
        self.assertIn("默认走 Layer 0", agents_text)

    def test_ci_monitor_workflow_receipts_and_job_fail_fast(self) -> None:
        module = self._load_ci_monitor()
        with tempfile.TemporaryDirectory() as tmp:
            success_receipt = Path(tmp) / "workflow-success.md"
            args = argparse.Namespace(
                repo="LeePepe/app", workflow="testflight.yml", run_id="42", trigger=False,
                force=False, ref="main", marker_tag="testflight/last-released", interval=0,
                timeout=10, receipt=str(success_receipt), notify_agent=None,
            )

            def success_run(command):
                if command[:3] == ["gh", "run", "view"]:
                    return json.dumps({"status": "completed", "conclusion": "success", "headSha": "abc",
                                       "url": "https://example/run/42", "jobs": [{"databaseId": 7, "conclusion": "success"}]})
                if command[:3] == ["gh", "api", "repos/LeePepe/app/actions/jobs/7/logs"]:
                    return "Latest upload succeeded with build 123\n"
                if command[:2] == ["git", "ls-remote"]:
                    return "deadbeef\trefs/tags/testflight/last-released\n"
                raise AssertionError(command)

            result, code = module.monitor_workflow(args, run=success_run, sleep=lambda _: None, now=lambda: 0)
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "WORKFLOW_SUCCESS")
            receipt = success_receipt.read_text(encoding="utf-8")
            self.assertIn("build_number`: 123", receipt)
            self.assertIn("last_released_tag_sha`: deadbeef", receipt)

            failed_receipt = Path(tmp) / "workflow-failed.md"
            args.receipt = str(failed_receipt)

            def failed_run(command):
                if command[:3] == ["gh", "run", "view"]:
                    return json.dumps({"status": "in_progress", "conclusion": None, "headSha": "abc",
                                       "url": "https://example/run/42", "jobs": [{"databaseId": 8, "conclusion": "failure"}]})
                if command[:3] == ["gh", "api", "repos/LeePepe/app/actions/jobs/8/logs"]:
                    return "failure line\n"
                raise AssertionError(command)

            result, code = module.monitor_workflow(args, run=failed_run, sleep=lambda _: None, now=lambda: 0)
            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "WORKFLOW_FAILED")
            self.assertIn("failure line", failed_receipt.read_text(encoding="utf-8"))

    def test_ci_monitor_pr_receipts_and_job_fail_fast(self) -> None:
        module = self._load_ci_monitor()
        with tempfile.TemporaryDirectory() as tmp:
            merged_receipt = Path(tmp) / "pr-merged.md"
            args = argparse.Namespace(repo="LeePepe/app", pr=9, head_sha="abc", interval=0,
                                      timeout=10, receipt=str(merged_receipt), notify_agent=None, exit_on_green=False)

            def merged_run(command):
                self.assertEqual(command[:3], ["gh", "pr", "view"])
                return json.dumps({"state": "MERGED", "mergedAt": "2026-01-01T00:00:00Z",
                                   "mergeCommit": {"oid": "fedcba"}, "headRefOid": "abc",
                                   "statusCheckRollup": [], "url": "https://example/pr/9"})

            result, code = module.monitor_pr(args, run=merged_run, sleep=lambda _: None, now=lambda: 0)
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "PR_MERGED")
            self.assertIn("merge_commit_sha`: fedcba", merged_receipt.read_text(encoding="utf-8"))

            failed_receipt = Path(tmp) / "pr-failed.md"
            args.receipt = str(failed_receipt)

            def failed_run(command):
                if command[:3] == ["gh", "pr", "view"]:
                    return json.dumps({"state": "OPEN", "headRefOid": "abc", "statusCheckRollup": [
                        {"conclusion": "FAILURE", "detailsUrl": "https://github.com/LeePepe/app/actions/runs/1/job/88"}
                    ], "url": "https://example/pr/9"})
                if command[:3] == ["gh", "api", "repos/LeePepe/app/actions/jobs/88/logs"]:
                    return "job failed\n"
                raise AssertionError(command)

            result, code = module.monitor_pr(args, run=failed_run, sleep=lambda _: None, now=lambda: 0)
            self.assertEqual(code, 1)
            self.assertEqual(result["status"], "PR_CHECK_FAILED")
            self.assertIn("job failed", failed_receipt.read_text(encoding="utf-8"))

    def test_json_files_valid_and_plugin_manifests_consistent(self) -> None:
        json_files = [
            path
            for path in sorted(REPO_ROOT.rglob("*.json"))
            if ".git" not in path.parts
        ]
        self.assertGreaterEqual(len(json_files), 7)

        for json_path in json_files:
            with self.subTest(json_path=str(json_path.relative_to(REPO_ROOT))):
                data = json.loads(json_path.read_text(encoding="utf-8"))
                self.assertIsInstance(data, dict)

        marketplace_path = REPO_ROOT / ".claude-plugin" / "marketplace.json"
        self.assertTrue(marketplace_path.is_file())
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
        self.assertEqual(marketplace.get("name"), "user-skills")
        self.assertIn("owner", marketplace)
        self.assertIn("metadata", marketplace)
        plugins = marketplace.get("plugins")
        self.assertIsInstance(plugins, list)
        self.assertGreaterEqual(len(plugins), 1)

        seen_names: set[str] = set()
        for plugin in plugins:
            name = plugin.get("name")
            source = plugin.get("source")
            description = plugin.get("description")
            category = plugin.get("category")
            self.assertTrue(isinstance(name, str) and name)
            self.assertNotIn(name, seen_names)
            seen_names.add(name)
            self.assertTrue(isinstance(source, str) and source.startswith("./skills/"))
            self.assertTrue(isinstance(description, str) and description)
            self.assertTrue(isinstance(category, str) and category)
            skill_dir = (REPO_ROOT / source).resolve()
            self.assertTrue((skill_dir / "SKILL.md").is_file(), f"Missing SKILL.md for {name}")

        plugin_manifests = sorted(SKILLS_DIR.glob("*/.claude-plugin/plugin.json"))
        self.assertGreaterEqual(len(plugin_manifests), 1)
        for manifest_path in plugin_manifests:
            skill_dir_name = manifest_path.parent.parent.name
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest.get("name"), skill_dir_name)
            self.assertTrue(isinstance(manifest.get("description"), str) and manifest["description"])
            self.assertTrue(isinstance(manifest.get("category"), str) and manifest["category"])

    def test_personal_m2_assistant_is_registered_with_canonical_router(self) -> None:
        skill_dir = SKILLS_DIR / "personal-m2-assistant"
        self.assertTrue((skill_dir / "SKILL.md").is_file())
        self.assertTrue((skill_dir / "scripts" / "m2-router.py").is_file())
        self.assertTrue((skill_dir / "references" / "m2-domain-map.json").is_file())
        manifest = json.loads((skill_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest, {
            "name": "personal-m2-assistant",
            "description": manifest["description"],
            "category": "workflow",
        })
        marketplace = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        plugin = next(item for item in marketplace["plugins"] if item["name"] == "personal-m2-assistant")
        self.assertEqual(plugin["source"], "./skills/personal-m2-assistant")
        legacy_router = SKILLS_DIR / "m2-dispatcher" / "scripts" / "m2-router.py"
        self.assertLess(legacy_router.stat().st_size, 2 * 1024)
        self.assertIn("personal-m2-assistant", legacy_router.read_text(encoding="utf-8"))
        expander = (SKILLS_DIR / "m2-dispatcher" / "scripts" / "skill-slash-expander.ts").read_text(encoding="utf-8")
        self.assertIn('skill: "personal-m2-assistant"', expander)
        self.assertIn("python3 skills/personal-m2-assistant/scripts/m2-router.py --plan", expander)
        skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        for token in (
            "Gemini 4 Argon · 983K context",
            "gemini-4-argon",
            "long_context",
            "orca file open",
            "--watch",
            "--watch-target",
            "watchdog_tasks",
            "watchdog_copilot_cli",
            "A2BCD",
            "Allow path access",
            "Curate Memory",
            "Confirm folder trust",
        ):
            self.assertIn(token, skill_text)

    def test_design_case_surface_contains_no_project_private_identifiers(self) -> None:
        for rel_path in (
            "skills/my-designer/references/defect-cases/index.json",
            "skills/my-designer/scripts/verify_design_cases.py",
        ):
            text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
            for private in ("progressRail", "isUnilateral", "DashboardL10n"):
                self.assertNotIn(private, text, f"{private} leaked into {rel_path}")

    def test_skill_contract_package_is_wired(self) -> None:
        skill_dir = SKILLS_DIR / "skill-contract"
        self.assertTrue((skill_dir / "SKILL.md").is_file())
        self.assertTrue((skill_dir / "scripts" / "skill_contract.py").is_file())
        manifest = json.loads((skill_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("category"), "meta")
        contract = (skill_dir / "scripts" / "skill_contract.py").read_text(encoding="utf-8")
        for token in ("audit_package", "sink_rule", "case-card", "mental-model", "regex-gate"):
            self.assertIn(token, contract)

    def test_skill_frontmatter_valid(self) -> None:
        skill_dirs = sorted(path for path in SKILLS_DIR.iterdir() if path.is_dir())
        self.assertGreaterEqual(len(skill_dirs), 1)

        for skill_dir in skill_dirs:
            skill_md = skill_dir / "SKILL.md"
            with self.subTest(skill=skill_dir.name):
                self.assertTrue(skill_md.is_file(), f"Missing {skill_md}")
                frontmatter = parse_frontmatter(skill_md)
                self.assertEqual(frontmatter.fields.get("name"), skill_dir.name)
                description = frontmatter.fields.get("description", "")
                self.assertTrue(description, f"Empty description in {skill_md}")

    def test_runtime_profiles_yaml_structure(self) -> None:
        profile_path = SKILLS_DIR / "team-lead" / "references" / "runtime-profiles.yaml"
        self.assertTrue(profile_path.is_file())
        content = profile_path.read_text(encoding="utf-8")

        for idx, line in enumerate(content.splitlines(), start=1):
            self.assertNotIn("\t", line, f"Tab indentation found on line {idx} of {profile_path}")

        required_top_keys = (
            "version:",
            "updated:",
            "tiers:",
            "vendors:",
            "common:",
            "machines:",
            "notes:",
        )
        for key in required_top_keys:
            self.assertRegex(
                content,
                re.compile(rf"^{re.escape(key)}", re.MULTILINE),
                f"Missing top-level key {key} in {profile_path}",
            )

        for section_key in ("  strong:", "  mid:", "  worker:", "  research:", "  mac-mini:", "  macbook:"):
            self.assertIn(f"\n{section_key}", content, f"Missing {section_key.strip()} in {profile_path}")

        self.assertIn("worker: [writer, explorer, pr_monitor, mechanical]", content)
        self.assertIn("copilot_allowlist:", content)
        self.assertNotIn("gemini-3.8-flash", content)
        self.assertNotIn("composer-2.5", content)
        self.assertNotIn("kimi-k3-high", content)
        self.assertIn("kimi: moonshot", content)
        self.assertIn("model: kimi,", content)
        self.assertGreaterEqual(content.count("pr_monitor:"), 2)
        code_reviewer_blocks = re.findall(
            r"code_reviewer:\n((?:[ \t]+- \{[^\n]+\}\n)+)",
            content,
        )
        self.assertEqual(len(code_reviewer_blocks), 2)
        for block in code_reviewer_blocks:
            models = re.findall(
                r"^[ \t]+- \{cli: [^,]+, model: ([^,]+),",
                block,
                re.MULTILINE,
            )
            self.assertEqual(models[2], "gpt-5.6-luna")

        explorer_blocks = re.findall(
            r"explorer:\n((?:[ \t]+- \{[^\n]+\}\n)+)",
            content,
        )
        self.assertEqual(len(explorer_blocks), 2)
        for block in explorer_blocks:
            models = re.findall(
                r"^[ \t]+- \{cli: [^,]+, model: ([^,]+),",
                block,
                re.MULTILINE,
            )
            self.assertEqual(models, ["gpt-5.6-luna", "mai-code-2-flash"])
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("- {cli: copilot,") or stripped.startswith("planner_reviewer: {cli: copilot,") or stripped.startswith("research_primary: {cli: copilot,") or stripped.startswith("team_lead: {cli: copilot,") or stripped.startswith("arbitration: {cli: copilot,"):
                self.assertIn("--allow-tool", stripped, f"Missing --allow-tool on copilot profile line: {stripped}")
                self.assertIn("--add-dir ~/Development", stripped, f"Missing --add-dir on copilot profile line: {stripped}")
                self.assertIn("--add-dir /Volumes/SSD", stripped, f"Missing /Volumes/SSD on copilot profile line: {stripped}")

    def test_relative_markdown_links_resolve(self) -> None:
        markdown_files = [
            REPO_ROOT / "README.md",
            *sorted(SKILLS_DIR.rglob("*.md")),
        ]
        for md_path in markdown_files:
            with self.subTest(markdown=str(md_path.relative_to(REPO_ROOT))):
                text = md_path.read_text(encoding="utf-8")
                for raw_target in iter_markdown_links(text):
                    target = raw_target.split()[0]
                    if target.startswith(("http://", "https://", "mailto:", "#")):
                        continue
                    path_part = target.split("#", 1)[0]
                    if not path_part:
                        continue
                    resolved = (md_path.parent / path_part).resolve()
                    self.assertTrue(
                        resolved.is_relative_to(REPO_ROOT),
                        f"Link {raw_target!r} in {md_path} escapes repository root",
                    )
                    self.assertTrue(
                        resolved.exists(),
                        f"Broken relative link {raw_target!r} in {md_path.relative_to(REPO_ROOT)}",
                    )
                    anchor = target.split("#", 1)[1] if "#" in target else ""
                    if anchor and resolved.suffix.lower() == ".md":
                        self.assertIn(
                            anchor,
                            markdown_anchors(resolved),
                            f"Broken heading anchor {raw_target!r} in {md_path.relative_to(REPO_ROOT)}",
                        )

    def test_model_surface_budget_and_topology(self) -> None:
        for rel_path, budget in MODEL_SURFACE_BYTE_BUDGETS.items():
            path = REPO_ROOT / rel_path
            self.assertTrue(path.is_file(), f"Missing model-surface path: {rel_path}")
            self.assertLessEqual(path.stat().st_size, budget, f"{rel_path} exceeds {budget} bytes")

        for phrase, scopes in BANNED_SURFACE_PHRASES:
            for scope in scopes:
                paths = sorted(SKILLS_DIR.rglob("*.md")) if scope == "skills/" else [REPO_ROOT / scope]
                for path in paths:
                    self.assertNotIn(phrase, path.read_text(encoding="utf-8"), f"{phrase!r} in {path}")

        path_re = re.compile(r"`(skills/[a-z0-9_-]+/(?:scripts|references|assets)/[A-Za-z0-9_./-]+)`")
        for rel_path in (
            "skills/team-lead/SKILL.md",
            "skills/m2-dispatcher/SKILL.md",
                    ):
            text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
            for match in path_re.findall(text):
                self.assertNotIn("*", match)
                self.assertNotIn("<", match)
                self.assertTrue((REPO_ROOT / match).is_file(), f"Missing referenced path {match}")

    def test_goal_task_is_the_only_contract_skill(self) -> None:
        self.assertFalse(
            (SKILLS_DIR / "goal-target-task").exists(),
            "skills/goal-target-task compatibility mirror must stay removed",
        )
        marketplace = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        plugin_names = {plugin.get("name") for plugin in marketplace.get("plugins", [])}
        self.assertIn("team-lead", plugin_names)
        self.assertNotIn("goal-target-task", plugin_names)

        canonical_dir = SKILLS_DIR / "team-lead"
        canonical_refs = canonical_dir / "references"
        self.assertTrue(canonical_refs.is_dir())
        canonical_files = [p for p in canonical_refs.iterdir() if p.is_file()]
        self.assertGreaterEqual(len(canonical_files), 1)

        canonical_scripts = canonical_dir / "scripts"
        self.assertTrue(canonical_scripts.is_dir())
        canonical_script_files = {p.name: p for p in canonical_scripts.iterdir() if p.is_file()}
        self.assertIn("goal-queue.py", canonical_script_files)
        for script_path in canonical_script_files.values():
            self.assertTrue(os.access(script_path, os.X_OK), f"{script_path} is not executable")

        canonical_fm = parse_frontmatter(canonical_dir / "SKILL.md")
        self.assertEqual(canonical_fm.fields.get("name"), "team-lead")
        canonical_body = "\n".join(canonical_fm.body_lines)
        self.assertNotIn("../goal-target-task/", canonical_body)
        self.assertNotIn("兼容目录名", canonical_body)
        self.assertIn("唯一技能名是 `team-lead`", canonical_body)
        for anchor_heading in ("## 定义、冻结与缺口", "## 限定审查"):
            self.assertIn(
                anchor_heading,
                canonical_body,
                f"Missing required heading {anchor_heading!r} in SKILL.md",
            )

    def test_workflow_hub_open_capability_graph_contract(self) -> None:
        expected_skills = {
            "workflow-hub": {
                "category": "workflow",
                "required": (
                    "Open Capability Graph",
                    "开放式能力节点与按需直连图谱",
                    "Idea & Goal State",
                    "Co-Design",
                    "Autonomous Build, Review & Merge",
                    "Product Delivery & Release",
                    "Learning & Narrative",
                    "spec.md`/`design.md",
                    "ledger.md",
                    "receipts/*-rN.md",
                    "TIA-xx",
                    "linear-board -> domain-modeling -> team-lead",
                    "team-lead -> code-review",
                    "linear-board",
                    "m2-dispatcher",
                    "team-lead",
                    "retro-loop",
                    "story-craft",
                    "Progressive Discovery Ladder",
                    "Not an upfront all-at-once constraint",
                    "Stage 1",
                    "Stage 5",
                    "自治脉搏：Cron / launchd 定时调度",
                    "Precheck-First, Wake-on-Actionable",
                    "pending == 0",
                ),
            },
            "linear-board": {
                "category": "workflow",
                "required": (
                    "Idea Inbox",
                    "Global Goal Status Board",
                    "1 Goal = 1 Issue",
                    "Promote Idea -> Goal",
                    "TIA",
                ),
            },
            "retro-loop": {
                "category": "workflow",
                "required": (
                    "Project Loop A",
                    "Engineering Loop B",
                    "H1–H5",
                    "3 Physical Locks",
                    "linear-board",
                ),
            },
            "story-craft": {
                "category": "workflow",
                "required": (
                    "4-Element Extraction Sheet",
                    "Desensitization",
                    "Story Series",
                    "Tech Deep-Dive Blog",
                    "Deck",
                ),
            },
        }

        marketplace = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        plugins = {plugin["name"]: plugin for plugin in marketplace["plugins"]}
        for name, contract in expected_skills.items():
            with self.subTest(skill=name):
                skill_dir = SKILLS_DIR / name
                skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
                manifest = json.loads(
                    (skill_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
                )
                self.assertIn(name, plugins)
                self.assertEqual(plugins[name]["source"], f"./skills/{name}")
                self.assertEqual(manifest["name"], name)
                self.assertEqual(manifest["category"], contract["category"])
                for phrase in contract["required"]:
                    self.assertIn(phrase, skill_text)
                self.assertNotIn("7-Ring lifecycle", skill_text)
                self.assertNotIn("7 环", skill_text)

        linear_script = SKILLS_DIR / "linear-board" / "scripts" / "linear-sync.py"
        self.assertTrue(linear_script.is_file())
        self.assertEqual(linear_script.stat().st_mode & 0o777, 0o755)
        self.assertIn("../team-lead/scripts/linear-sync.py", (SKILLS_DIR / "linear-board" / "SKILL.md").read_text(encoding="utf-8"))
        linear_board_text = (SKILLS_DIR / "linear-board" / "SKILL.md").read_text(encoding="utf-8")
        workflow_hub_text = (SKILLS_DIR / "workflow-hub" / "SKILL.md").read_text(encoding="utf-8")
        for project_name in ("Agent Workflow", "Bing", "Personal project"):
            self.assertIn(project_name, linear_board_text)
        self.assertIn('--project "Agent Workflow"', linear_board_text)
        self.assertIn("## 如何按需让 CLI / Agent 加载总 Workflow（On-Demand Invocation）", workflow_hub_text)
        self.assertIn("/workflow-hub", workflow_hub_text)
        self.assertIn("$workflow-hub", workflow_hub_text)
        for skill_dir in ("~/.agents/skills", "~/.copilot/skills", "~/.codex/skills", "~/.claude/skills"):
            self.assertIn(skill_dir, workflow_hub_text)

        goal_text = (SKILLS_DIR / "team-lead" / "SKILL.md").read_text(encoding="utf-8")
        playbook_text = (SKILLS_DIR / "team-lead" / "references" / "playbook.md").read_text(encoding="utf-8")
        roadmap_text = (SKILLS_DIR / "team-lead" / "references" / "workflow-refine-roadmap.md").read_text(encoding="utf-8")
        for target in ("../workflow-hub/SKILL.md", "../linear-board/SKILL.md", "../retro-loop/SKILL.md", "../story-craft/SKILL.md"):
            self.assertIn(f"]({target})", goal_text)
        for text in (playbook_text, roadmap_text):
            self.assertIn("Progressive Discovery Ladder", text)
            self.assertIn("Not an upfront all-at-once constraint", text)
        self.assertIn("Stage 1", playbook_text)
        self.assertIn("Stage 5", playbook_text)
        queue_path = SKILLS_DIR / "team-lead" / "scripts" / "goal-queue.py"
        spec = importlib.util.spec_from_file_location("goal_queue_contract_test", queue_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        queue_module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(queue_module)
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir:
            isolated = Path(tmp_dir) / "chain"
            isolated.mkdir()
            self.assertTrue(queue_module._is_isolated_handoff_dir(isolated))

    def test_thin_three_group_role_routing_without_designer_subagent(self) -> None:
        skill_path = SKILLS_DIR / "team-lead" / "SKILL.md"
        agents_path = skill_path.parent / "references" / "bots-and-agents.md"
        profiles_path = skill_path.parent / "references" / "runtime-profiles.yaml"
        skill_text = skill_path.read_text(encoding="utf-8")
        agents_text = agents_path.read_text(encoding="utf-8")
        profiles_text = profiles_path.read_text(encoding="utf-8")
        for text in (skill_text, agents_text):
            self.assertIn("never spawns a second `Steering`", text)
            self.assertIn("Context-Noise Threshold", text)
            self.assertIn("≤8", text)
            self.assertIn("≤3", text)
            self.assertIn("keep", text)
            self.assertIn("herdr-dispatch.sh", text)
            self.assertIn("herdr pane close", text)
            self.assertIn("blocked", text)
            self.assertIn("R10", text)
            self.assertIn('my-designer', text)
            self.assertIn("Explorer", text)
            self.assertNotIn("设计者（Designer）", text)
            self.assertNotIn("Designer 派遣与反内联守卫", text)
            self.assertNotIn("艺术家", text)
            self.assertNotIn("writer_stays_and_cleans_own_worktree", text)
            self.assertNotIn("weekly_remaining_pct", text)
        self.assertNotIn("quota_precheck:", profiles_text)

    def test_five_layer_design_skills_and_cli_pipeline_contract(self) -> None:
        designer_dir = SKILLS_DIR / "my-designer"
        studio_dir = SKILLS_DIR / "design-studio"
        modernization_dir = SKILLS_DIR / "visual-design-modernization"
        self.assertFalse(
            modernization_dir.exists(),
            "skills/visual-design-modernization must remain retired into my-designer/references/modernization-dimensions.md",
        )
        designer_text = (designer_dir / "SKILL.md").read_text(encoding="utf-8")
        studio_text = (studio_dir / "SKILL.md").read_text(encoding="utf-8")
        routing_text = (designer_dir / "references" / "routing-index.md").read_text(encoding="utf-8")
        cases_text = (designer_dir / "references" / "defect-cases" / "index.json").read_text(encoding="utf-8")
        modernization_text = (designer_dir / "references" / "modernization-dimensions.md").read_text(encoding="utf-8")
        goal_text = (SKILLS_DIR / "team-lead" / "SKILL.md").read_text(encoding="utf-8")
        agents_text = (SKILLS_DIR / "team-lead" / "references" / "bots-and-agents.md").read_text(encoding="utf-8")

        for path in (
            designer_dir / "SKILL.md",
            designer_dir / "references" / "routing-index.md",
            designer_dir / "references" / "modernization-dimensions.md",
            designer_dir / "references" / "defect-cases" / "index.json",
            designer_dir / "scripts" / "verify_design_cases.py",
            studio_dir / "SKILL.md",
            studio_dir / "scripts" / "design_catalog.py",
        ):
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_file())

        shared_design_scripts = ("verify_design_cases.py", "design_catalog.py")
        for script_name in shared_design_scripts:
            md_script = designer_dir / "scripts" / script_name
            ds_script = studio_dir / "scripts" / script_name
            self.assertTrue(md_script.exists(), f"Missing shared design script in my-designer: {md_script}")
            self.assertTrue(ds_script.exists(), f"Missing shared design script in design-studio: {ds_script}")
            self.assertEqual(md_script.resolve(), ds_script.resolve())

        shared_design_refs = (
            "appearance-ledger.md",
            "breathing-and-clarity.md",
            "design-flow-architecture.md",
            "interaction-layering.md",
            "routing-index.md",
            "modernization-dimensions.md",
            "defect-cases",
            "eval-cases.json",
            "catalog.json",
            "taxonomy-v2.json",
            "remotion-video.md",
            "toolchain-20-matrix.md",
            "vault",
        )
        for ref_name in shared_design_refs:
            md_ref = designer_dir / "references" / ref_name
            ds_ref = studio_dir / "references" / ref_name
            self.assertTrue(md_ref.exists(), f"Missing shared design reference in my-designer: {md_ref}")
            self.assertTrue(ds_ref.exists(), f"Missing shared design reference in design-studio: {ds_ref}")
            self.assertEqual(md_ref.resolve(), ds_ref.resolve())

        for token in ("L0 -> L1 -> L2", "Plan A", "Plan C"):
            self.assertIn(token, routing_text)
        self.assertIn("plan-a-apple-hig", cases_text)
        self.assertIn("handoff-only", designer_text)
        self.assertIn("Entry B", designer_text)
        self.assertIn("Entry A", studio_text)
        self.assertNotIn("艺术家", goal_text)
        self.assertNotIn("艺术家", agents_text)
        self.assertNotIn("执行设计总审门禁", agents_text)
        self.assertNotIn("Bot → `艺术家`", agents_text)
        self.assertIn("downstream `Writer`", designer_text)
        self.assertIn("Author the implementation-ready `design.md`/`spec.md` handoff and receipts", designer_text)
        self.assertIn("the downstream `Writer` implements **directly in the product or `shared-design-system` code**", designer_text)
        self.assertIn("No Code Demo", designer_text)
        self.assertIn("No Code Demo", modernization_text)

    def test_bot_tl_orchestration_shared_components_contract(self) -> None:
        for skill_name in ("m2-dispatcher",):
            skill_dir = SKILLS_DIR / skill_name
            skill_path = skill_dir / "SKILL.md"
            manifest_path = skill_dir / ".claude-plugin" / "plugin.json"
            skill_text = skill_path.read_text(encoding="utf-8")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest.get("name"), skill_name)
            self.assertEqual(manifest.get("category"), "workflow")
            self.assertIn(skill_name, skill_text)
            self.assertIn("M2-Dispatcher", skill_text)

        skill_dir = SKILLS_DIR / "m2-dispatcher"
        skill_path = skill_dir / "SKILL.md"
        skill_text = skill_path.read_text(encoding="utf-8")

        shared_targets = (
            "../team-lead/references/runtime-profiles.yaml",
            "../team-lead/references/bots-and-agents.md",
            "../team-lead/references/ledger.md",
            "../team-lead/scripts/goal-queue.py",
            "../team-lead/scripts/herdr-dispatch.sh",
        )
        for target in shared_targets:
            with self.subTest(target=target):
                self.assertIn(f"]({target})", skill_text)
                self.assertTrue((skill_path.parent / target).resolve().is_file())

        shared_m2_scripts = (
            "herdr-dispatch.sh",
            "goal-supervisor.py",
            "goal-queue.py",
            "state_store.py",
            "jev_client.py",
        )
        for script_name in shared_m2_scripts:
            m2_script = skill_dir / "scripts" / script_name
            m1_script = SKILLS_DIR / "team-lead" / "scripts" / script_name
            self.assertTrue(m2_script.exists(), f"Missing shared script in m2-dispatcher: {m2_script}")
            self.assertEqual(m2_script.resolve(), m1_script.resolve())

        shared_m2_refs = (
            "runtime-profiles.yaml",
            "bots-and-agents.md",
            "ledger.md",
        )
        for ref_name in shared_m2_refs:
            m2_ref = skill_dir / "references" / ref_name
            m1_ref = SKILLS_DIR / "team-lead" / "references" / ref_name
            self.assertTrue(m2_ref.exists(), f"Missing shared reference in m2-dispatcher: {m2_ref}")
            self.assertEqual(m2_ref.resolve(), m1_ref.resolve())

        readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Shared M2+M1 Orchestration Scripts", readme_text)
        self.assertIn("M2-Only Harness Scripts", readme_text)
        self.assertIn("M1 Task Execution Scripts", readme_text)

        for document in (
            skill_path,
            SKILLS_DIR / "team-lead" / "references" / "playbook.md",
            SKILLS_DIR / "team-lead" / "references" / "bots-and-agents.md",
        ):
            with self.subTest(document=str(document.relative_to(REPO_ROOT))):
                text = document.read_text(encoding="utf-8")
                self.assertIn("PR_MERGED", text)
                self.assertIn("GOAL_DONE", text)
                self.assertIn("git worktree remove", text)
                self.assertIn("pr_monitor", text)

        self.assertIn("每次只沟通一层", skill_text)
        self.assertIn("vault-ingest", skill_text)
        self.assertIn("--measure-palette", skill_text)
        self.assertIn("skill-slash-expander.ts", skill_text)
        self.assertIn("install-pi-harness.sh", skill_text)
        self.assertIn("Mid-Flight User Question Rule", skill_text)
        self.assertIn("detach", skill_text)
        self.assertIn("watch-<goal>", skill_text)
        self.assertIn("session.idle", skill_text)
        self.assertIn("worktrees/", skill_text)
        self.assertIn('if [ "$TL_STATUS" = "idle" ] || [ "$TL_STATUS" = "done" ]; then', skill_text)
        self.assertIn('herdr agent send-keys "$GOAL-goal-tl-r1" enter', skill_text)

        expander_ts = (skill_dir / "scripts" / "skill-slash-expander.ts").read_text(encoding="utf-8")
        self.assertIn('pi.on("tool_call"', expander_ts)
        self.assertIn("M2-Dispatcher Rule: M2 is only allowed to edit simple spec files (spec.md / design.md).", expander_ts)
        self.assertIn("NEVER poll with 'sleep && herdr agent read' or 'herdr agent wait'", expander_ts)
        self.assertIn("goal-supervisor.py watch --handoff-dir ~/Development/handoff/<goal> --interval 15 --max-wait 600 --json", expander_ts)
        self.assertIn("detach: true", expander_ts)
        self.assertIn("watch-<goal>", expander_ts)
        self.assertIn("session.idle", expander_ts)
        self.assertIn('pendingQueue.add("m2-dispatcher")', expander_ts)
        self.assertIn("HERDR_ENV", expander_ts)
        self.assertIn("CINDY_PI_SUBAGENT_RUN_DIR", expander_ts)
        self.assertIn("sanitizeGeminiSchema", expander_ts)
        self.assertIn("sanitizeGeminiSchemaInPlace", expander_ts)
        self.assertIn("sanitizeRegisteredToolsInPlace", expander_ts)
        self.assertIn("sanitizeGeminiChatPayload", expander_ts)
        self.assertIn("alignGeminiModelRuntimeConfig", expander_ts)
        self.assertIn("GEMINI_LONG_CONTEXT_WINDOW", expander_ts)
        self.assertIn("COPILOT_GEMINI_DEFAULT_HEADERS", expander_ts)
        self.assertIn("supportsReasoningEffort: false", expander_ts)
        self.assertIn("supportsFinishReason: false", expander_ts)
        self.assertIn("before_provider_request", expander_ts)
        self.assertIn("before_provider_headers", expander_ts)
        self.assertIn('value.filter((item) => item !== "")', expander_ts)
        self.assertIn("UNIVERSAL_SHORTCUT_MACROS", expander_ts)
        self.assertIn("expandShortcutMacros", expander_ts)
        self.assertIn("extractShortcutInvocations", expander_ts)
        self.assertIn("FOREGROUND_TO_BACKGROUND_5S_RULE_DIRECTIVE", expander_ts)
        self.assertIn("isLikelySlowForegroundCommand", expander_ts)
        for shortcut_token in (
            "/m2 <跨M2指令>",
            "/status",
            "/ingest <text_or_image>",
            "/learn <topic_or_url>",
            "/dash",
            "/retro",
            "goal-supervisor.py status --check",
            "cli.py digest --date today --aidash",
            "retro_story_engine.py agent-efficiency --auto-propose",
        ):
            self.assertIn(shortcut_token, skill_text)

        installer_sh = (skill_dir / "scripts" / "install-pi-harness.sh").read_text(encoding="utf-8")
        self.assertNotIn("rm -f", installer_sh)
        for prompt_name in ("m2.md", "status.md", "ingest.md", "learn.md", "dash.md", "retro.md"):
            self.assertIn(prompt_name, installer_sh)

        # Verify M2 outer adapter auto-bootstraps m2-dispatcher on first turn in M2 main session,
        # does not auto-inject when HERDR_ENV=1, does not block local bash/git tool calls,
        # and expands the 6 universal shortcut macros (/m2, /status, /ingest, /learn, /dash, /retro).
        node_probe = r"""
import assert from "node:assert/strict";
import skillSlashExpander, {
  FOREGROUND_TO_BACKGROUND_5S_RULE_DIRECTIVE,
  FOREGROUND_TO_BACKGROUND_5S_THRESHOLD_SEC,
  UNIVERSAL_SHORTCUT_MACROS,
  expandShortcutMacros,
  extractShortcutInvocations,
  isLikelyImageArgument,
  isLikelySlowForegroundCommand,
} from "./skills/m2-dispatcher/scripts/skill-slash-expander.ts";

async function runCase(envOverrides) {
  const prevHerdr = process.env.HERDR_ENV;
  const prevSub = process.env.CINDY_PI_SUBAGENT_RUN_DIR;
  try {
    if ("HERDR_ENV" in envOverrides) process.env.HERDR_ENV = envOverrides.HERDR_ENV;
    else delete process.env.HERDR_ENV;
    if ("CINDY_PI_SUBAGENT_RUN_DIR" in envOverrides) process.env.CINDY_PI_SUBAGENT_RUN_DIR = envOverrides.CINDY_PI_SUBAGENT_RUN_DIR;
    else delete process.env.CINDY_PI_SUBAGENT_RUN_DIR;

    const handlers = new Map();
    const pi = {
      on(event, fn) { handlers.set(event, fn); },
      appendEntry() {},
    };
    skillSlashExpander(pi);
    const ctx = { cwd: process.cwd() };
    await handlers.get("session_start")?.({}, ctx);
    const startRes = await handlers.get("before_agent_start")?.({ prompt: "hello world without slash token" }, ctx);
    const bashRes = await handlers.get("tool_call")?.({ toolName: "bash", input: { command: "git status" } }, ctx);
    return { startRes, bashRes, handlers, ctx };
  } finally {
    if (prevHerdr === undefined) delete process.env.HERDR_ENV;
    else process.env.HERDR_ENV = prevHerdr;
    if (prevSub === undefined) delete process.env.CINDY_PI_SUBAGENT_RUN_DIR;
    else process.env.CINDY_PI_SUBAGENT_RUN_DIR = prevSub;
  }
}

const m2Res = await runCase({});
assert.ok(m2Res.startRes?.message, "Expected M2 main session to auto-inject m2-dispatcher");
const m2Text = m2Res.startRes.message.content[0].text;
assert.ok(m2Text.includes('<skill-context name="m2-dispatcher"'), "Expected <skill-context name=\"m2-dispatcher\"> in M2 session");
assert.equal(m2Res.bashRes, undefined, "Expected local git/bash tool calls not to be blocked");

const herdrRes = await runCase({ HERDR_ENV: "1" });
assert.equal(herdrRes.startRes, undefined, "Expected HERDR_ENV=1 worker session not to auto-inject m2-dispatcher");

// Verify the 6 universal shortcut macros
assert.deepEqual(Object.keys(UNIVERSAL_SHORTCUT_MACROS).sort(), ["dash", "ingest", "learn", "m2", "retro", "status"]);
assert.equal(FOREGROUND_TO_BACKGROUND_5S_THRESHOLD_SEC, 5);
assert.ok(FOREGROUND_TO_BACKGROUND_5S_RULE_DIRECTIVE.includes("5s Foreground-to-Background Rule"));
assert.equal(isLikelySlowForegroundCommand("xcodebuild -scheme AIDashApp build"), true);
assert.equal(isLikelySlowForegroundCommand("pytest aidata/tests/"), true);
assert.equal(isLikelySlowForegroundCommand("git status --short"), false);
assert.equal(isLikelyImageArgument("/tmp/shot.png"), true);
assert.equal(isLikelyImageArgument("Linear glass card inspiration"), false);

const m2Exp = expandShortcutMacros("/m2 先让 shared-ci 更新设计门禁，再让 AIDash 接入");
assert.equal(m2Exp.transformed, true);
assert.ok(m2Exp.text.includes("m2-router.py --plan"));
assert.ok(m2Exp.text.includes("--apply --plan-file <approved-plan.json> --approve"));

const statusExp = expandShortcutMacros("/status");
assert.equal(statusExp.transformed, true);
assert.ok(statusExp.text.includes("goal-supervisor.py status --check"));

const ingestImgExp = expandShortcutMacros("/ingest /tmp/hero-shot.png");
assert.equal(ingestImgExp.transformed, true);
assert.ok(ingestImgExp.text.includes("design_catalog.py vault-ingest"));
assert.ok(ingestImgExp.text.includes("--measure-palette"));

const ingestTxtExp = expandShortcutMacros("/ingest Minimalist dark bento card");
assert.equal(ingestTxtExp.transformed, true);
assert.ok(ingestTxtExp.text.includes("design_catalog.py vault-ingest --text"));
assert.equal(ingestTxtExp.text.includes("--measure-palette"), false);

const learnExp = expandShortcutMacros("/learn https://example.com/swift-concurrency");
assert.equal(learnExp.transformed, true);
assert.ok(learnExp.text.includes("/knowledge-digest"));
assert.ok(learnExp.text.includes("knowledge-vault/index.md"));

const dashExp = expandShortcutMacros("/dash");
assert.equal(dashExp.transformed, true);
assert.ok(dashExp.text.includes("python3 cli.py digest --date today --aidash"));

const retroExp = expandShortcutMacros("/retro");
assert.equal(retroExp.transformed, true);
assert.ok(retroExp.text.includes("retro_story_engine.py agent-efficiency --auto-propose"));
assert.ok(retroExp.text.includes("discover"));

// Verify runtime input & before_agent_start skill injection for shortcut macro in worker mode
const prevHerdr = process.env.HERDR_ENV;
process.env.HERDR_ENV = "1";
try {
  const handlers = new Map();
  const pi = {
    on(event, fn) { handlers.set(event, fn); },
    appendEntry() {},
  };
  skillSlashExpander(pi);
  const ctx = { cwd: process.cwd() };
  await handlers.get("session_start")?.({}, ctx);
  const inputRes = await handlers.get("input")?.({ text: "/retro" }, ctx);
  assert.equal(inputRes?.action, "transform");
  assert.ok(inputRes.text.includes("/retro-loop"));
  const agentStartRes = await handlers.get("before_agent_start")?.({ prompt: inputRes.text }, ctx);
  assert.ok(agentStartRes?.message?.content?.[0]?.text?.includes('<skill-context name="retro-loop"'));
} finally {
  if (prevHerdr === undefined) delete process.env.HERDR_ENV;
  else process.env.HERDR_ENV = prevHerdr;
}
"""
        probe_proc = subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module", "-e", node_probe],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            probe_proc.returncode,
            0,
            f"skill-slash-expander M2 auto-bootstrap probe failed:\nstdout={probe_proc.stdout}\nstderr={probe_proc.stderr}",
        )

        agents_md_path = REPO_ROOT / "AGENTS.md"
        self.assertTrue(agents_md_path.is_file(), "user-skills/AGENTS.md must exist as a thin progressive-disclosure index")
        agents_md_text = agents_md_path.read_text(encoding="utf-8")
        self.assertIn("./scripts/verify", agents_md_text)
        self.assertIn("skill_contract.py audit --strict", agents_md_text)
        self.assertNotIn("herdr-dispatch.sh", agents_md_text)

        prepush_text = (REPO_ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")
        self.assertIn("refs/heads/main", prepush_text)
        self.assertIn("refs/heads/master", prepush_text)
        self.assertIn("ALLOW_MAIN_PUSH", prepush_text)
        precommit_text = (REPO_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
        self.assertNotIn("ALLOW_MAIN_COMMIT", precommit_text)

    def test_merged_worktree_cleanup_contract(self) -> None:
        skill_text = (SKILLS_DIR / "team-lead" / "SKILL.md").read_text(encoding="utf-8")
        agents_text = (
            SKILLS_DIR / "team-lead" / "references" / "bots-and-agents.md"
        ).read_text(encoding="utf-8")

        for text in (skill_text, agents_text):
            self.assertIn("PR_MERGED", text)
            self.assertIn("git worktree remove", text)
            self.assertIn("未合并／暂停（HOLD）", text)
            self.assertIn("Owner", text)

    def test_design_budget_upstream_sync_and_v2_truncation_gate_contract(self) -> None:
        skill_text = (SKILLS_DIR / "team-lead" / "SKILL.md").read_text(encoding="utf-8")
        agents_text = (
            SKILLS_DIR / "team-lead" / "references" / "bots-and-agents.md"
        ).read_text(encoding="utf-8")

        for text in (skill_text, agents_text):
            self.assertIn("shared-design-system", text)
            self.assertIn("upstream", text)
            self.assertIn("zero numeric", text.lower())
            self.assertIn("…", text)
            self.assertIn("followed the brief verbatim", text)
            self.assertIn("V2 P1", text)
        self.assertIn("sips -g pixelWidth", skill_text)
        self.assertIn("Package.resolved", agents_text)

    def test_spec_first_obsolete_test_manifest_and_visual_harmony_gate_contract(self) -> None:
        skill_text = (SKILLS_DIR / "team-lead" / "SKILL.md").read_text(encoding="utf-8")
        agents_text = (
            SKILLS_DIR / "team-lead" / "references" / "bots-and-agents.md"
        ).read_text(encoding="utf-8")

        for text in (skill_text, agents_text):
            self.assertIn("Spec-First Requirement Change Rule", text)
            self.assertIn("spec.md", text)
            self.assertIn("design.md", text)
            self.assertIn("Obsolete Test Manifest", text)
            self.assertIn("Deprecated Test Clauses to Remove/Rewrite", text)
            self.assertIn("Explorer", text)
            self.assertIn("Code Writer", text)
            self.assertIn("Code/UI Reviewer", text)
            self.assertIn("shared-design-system", text)

    def test_goal_supervisor_scan_check_pins_and_telemetry(self) -> None:
        supervisor = SKILLS_DIR / "team-lead" / "scripts" / "goal-supervisor.py"
        self.assertTrue(supervisor.is_file())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handoff = root / "goal-a"
            (handoff / "receipts").mkdir(parents=True)
            (handoff / "ledger.md").write_text(
                "Goal ID: goal-a TL: goal-a-goal-tl-r1\n"
                "2026-10-31T00:00:00Z EVT=START\n"
                "2026-10-31T00:01:00Z EVT=UNBLOCK\n"
                "2026-10-31T00:02:00Z EVT=REVIEW_FAIL\n"
                "2026-10-31T00:03:00Z EVT=PR_CHECK_FAILED\n",
                encoding="utf-8",
            )
            (handoff / "receipts" / "goal-a-writer-r1.md").write_text("", encoding="utf-8")
            agents = root / "agents.json"
            agents.write_text(
                json.dumps(
                    {
                        "result": {
                            "agents": [
                                {"name": "goal-a-goal-tl-r1", "agent_status": "working"},
                                {"name": "goal-a-writer-r1", "agent_status": "blocked"},
                                {"name": "goal-a-rev-r1", "agent_status": "idle"},
                                {"name": "goal-a-exp-r1", "agent_status": "working"},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            scan = subprocess.run(
                [
                    str(supervisor), "scan", "--handoff-root", str(root),
                    "--herdr-json", str(agents), "--now", "2026-10-31T01:00:00Z", "--json",
                ],
                capture_output=True, text=True, check=True,
            )
            anomaly_types = {item["type"] for item in json.loads(scan.stdout)["anomalies"]}
            self.assertEqual(anomaly_types, {"BLOCKED_AGENT", "ZOMBIE_LEASE", "ORPHAN_TL_WAIT"})

            pins = root / "pins.json"
            pins.write_text(
                json.dumps(
                    {
                        "upstreams": {"shared-design-system": "bf31a035", "user-skills": "ce722ff6"},
                        "consumers": [
                            {"repo": "VitalStride", "pins": {"shared-design-system": "781c09de", "user-skills": "ce722ff6"}},
                            {"repo": "AIDash", "pins": {"shared-design-system": "bf31a035", "user-skills": "ce722ff6"}},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            pin_result = subprocess.run(
                [str(supervisor), "check-pins", "--pins-file", str(pins), "--json"],
                capture_output=True, text=True, check=True,
            )
            pin_data = json.loads(pin_result.stdout)
            self.assertEqual(pin_data["consumers"][0]["status"], "OUTDATED_PIN")
            self.assertEqual(pin_data["consumers"][1]["status"], "UP_TO_DATE")

            telemetry = subprocess.run(
                [str(supervisor), "telemetry", "--handoff-dir", str(handoff), "--write", "--json"],
                capture_output=True, text=True, check=True,
            )
            telemetry_data = json.loads(telemetry.stdout)
            self.assertEqual(telemetry_data["total_duration_sec"], 180)
            self.assertEqual(telemetry_data["unblock_count"], 1)
            self.assertEqual(telemetry_data["changes_requested_count"], 1)
            self.assertEqual(telemetry_data["pr_check_failed_count"], 1)
            self.assertTrue((handoff / "receipts" / "contract-telemetry.json").is_file())

    def test_goal_supervisor_binds_shortened_names_and_cwd(self) -> None:
        supervisor = SKILLS_DIR / "team-lead" / "scripts" / "goal-supervisor.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            goal_dir = root / "good-designer"
            other_dir = root / "other-goal"
            (goal_dir / "receipts").mkdir(parents=True)
            other_dir.mkdir()
            (goal_dir / "ledger.md").write_text(
                "\n".join(
                    [
                        "# Goal: good-designer",
                        "- TL: good-designer-goal-tl-r1 / 模型 gemini-4-argon",
                        "- 2026-10-31T00:00:00Z | EVT=START | id=E1 | from=good-designer-goal-tl-r1",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (other_dir / "ledger.md").write_text(
                "\n".join(
                    [
                        "# Goal: other-goal",
                        "- TL: other-goal-goal-tl-r1 / 模型 gemini-4-argon",
                        "- 2026-10-31T00:00:00Z | EVT=START | id=E1",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            agents = root / "agents.json"
            agents.write_text(
                json.dumps(
                    {
                        "result": {
                            "agents": [
                                {"name": "good-des-goal-tl-r1", "agent_status": "working"},
                                {"name": "good-des-writer-r1", "agent_status": "blocked"},
                                {
                                    "name": "gd-e1-rev-r1",
                                    "agent_status": "blocked",
                                    "cwd": str(goal_dir),
                                },
                                {"name": "other-goal-writer-r1", "agent_status": "blocked"},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            scan = subprocess.run(
                [
                    str(supervisor),
                    "scan",
                    "--handoff-root",
                    str(root),
                    "--herdr-json",
                    str(agents),
                    "--now",
                    "2026-10-31T00:01:00Z",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(scan.stdout)
            by_goal = {item["goal_id"]: set(item["agents"]) for item in data["goals"]}
            self.assertEqual(
                by_goal["good-designer"],
                {"good-des-goal-tl-r1", "good-des-writer-r1", "gd-e1-rev-r1"},
            )
            self.assertEqual(by_goal["other-goal"], {"other-goal-writer-r1"})
            blocked = {
                (item["goal_id"], item["agent"])
                for item in data["anomalies"]
                if item["type"] == "BLOCKED_AGENT"
            }
            self.assertIn(("good-designer", "good-des-writer-r1"), blocked)
            self.assertIn(("good-designer", "gd-e1-rev-r1"), blocked)
            self.assertNotIn(("good-designer", "other-goal-writer-r1"), blocked)
            self.assertIn(
                ("good-designer", "good-des-goal-tl-r1"),
                {
                    (item["goal_id"], item["agent"])
                    for item in data["anomalies"]
                    if item["type"] == "ORPHAN_TL_WAIT"
                },
            )

    def test_goal_supervisor_notify_bridge_and_scan_alerts(self) -> None:
        supervisor = SKILLS_DIR / "team-lead" / "scripts" / "goal-supervisor.py"
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            root = Path(tmp)
            handoff = root / "goal-a"
            handoff.mkdir()
            (handoff / "ledger.md").write_text(
                "# Goal: goal-a\n- Owner: owner · 协调者 bot: flower\n"
                "- 2026-10-31T00:00:00Z EVT=START\n",
                encoding="utf-8",
            )
            event = {
                "params": {
                    "type": "pane.agent_status_changed",
                    "agent_name": "goal-a-writer-r1",
                    "status": "blocked",
                    "pane_id": "w2E:p2",
                    "cwd": str(handoff),
                    "visible": "blocked prompt",
                }
            }
            event_path = root / "event.json"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            result = subprocess.run(
                [str(supervisor), "notify-bridge", "--handoff-root", str(root),
                 "--event-json", str(event_path), "--now", "2026-11-02T08:06:45Z", "--json"],
                capture_output=True, text=True, check=True,
            )
            data = json.loads(result.stdout)
            self.assertTrue(data["written"])
            alert = Path(data["alert_path"])
            self.assertTrue(alert.is_file())
            text = alert.read_text(encoding="utf-8")
            for expected in ("goal-a-writer-r1", "w2E:p2", str(handoff),
                             "blocked prompt", "herdr agent get", "herdr workspace close"):
                self.assertIn(expected, text)

            for status in ("working", "idle", "done"):
                event["params"]["status"] = status
                event_path.write_text(json.dumps(event), encoding="utf-8")
                ignored = subprocess.run(
                    [str(supervisor), "notify-bridge", "--handoff-root", str(root),
                     "--event-json", str(event_path), "--now", "2026-11-02T08:06:46Z", "--json"],
                    capture_output=True, text=True, check=True,
                )
                self.assertFalse(json.loads(ignored.stdout)["written"])

            event["params"]["status"] = "blocked"
            event["params"]["type"] = "toast"
            event_path.write_text(json.dumps(event), encoding="utf-8")
            ignored = subprocess.run(
                [str(supervisor), "notify-bridge", "--handoff-root", str(root),
                 "--event-json", str(event_path), "--now", "2026-11-02T08:06:47Z", "--json"],
                capture_output=True, text=True, check=True,
            )
            self.assertFalse(json.loads(ignored.stdout)["written"])

            agents = root / "agents.json"
            agents.write_text(json.dumps({"result": {"agents": [{
                "name": "goal-a-writer-r1", "agent_status": "blocked",
                "pane_id": "w2E:p2", "cwd": str(handoff), "visible": "scan prompt"
            }]}}), encoding="utf-8")
            scan = subprocess.run(
                [str(supervisor), "scan", "--handoff-root", str(root),
                 "--herdr-json", str(agents), "--write-alerts",
                 "--now", "2026-11-02T08:06:45Z", "--json"],
                capture_output=True, text=True, check=True,
            )
            anomaly = next(item for item in json.loads(scan.stdout)["anomalies"]
                           if item["type"] == "BLOCKED_AGENT")
            self.assertTrue(Path(anomaly["alert_path"]).is_file())

    def test_blocked_alert_contract_is_documented(self) -> None:
        for path in (
            SKILLS_DIR / "m2-dispatcher" / "SKILL.md",
            SKILLS_DIR / "team-lead" / "references" / "bots-and-agents.md",
            SKILLS_DIR / "team-lead" / "references" / "playbook.md",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIn("blocked-<agent>-<ts>.md", text)
            self.assertIn("通知桥 ≠ 解卡", text)
            self.assertIn("GOAL_DONE 关窗与 blocked alerts 两条独立", text)
        supervisor_text = (
            SKILLS_DIR / "team-lead" / "scripts" / "goal-supervisor.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("send-keys", supervisor_text)

    def test_goal_supervisor_contract_and_xcode_allowlist(self) -> None:
        supervisor = SKILLS_DIR / "team-lead" / "scripts" / "goal-supervisor.py"
        self.assertTrue(supervisor.stat().st_mode & 0o111)
        dispatch = (SKILLS_DIR / "team-lead" / "scripts" / "herdr-dispatch.sh").read_text()
        profiles = (SKILLS_DIR / "team-lead" / "references" / "runtime-profiles.yaml").read_text()
        self.assertIn("/Applications/Xcode.app", dispatch)
        self.assertIn("/Applications/Xcode.app", profiles)
        self.assertIn('"/Volumes/SSD"', dispatch)
        self.assertIn("/Volumes/SSD", profiles)
        for path in (
            SKILLS_DIR / "team-lead" / "SKILL.md",
            SKILLS_DIR / "team-lead" / "references" / "bots-and-agents.md",
            SKILLS_DIR / "m2-dispatcher" / "SKILL.md",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIn("goal-supervisor.py", text)
            self.assertIn("ZOMBIE_LEASE", text)
            self.assertIn("30–60s", text)
            self.assertIn("/Volumes/SSD", text)
            self.assertIn("good-des", text)

    def test_tracked_files_do_not_leak_personal_paths_or_tokens(self) -> None:
        forbidden_patterns: dict[str, re.Pattern[str]] = {
            "macos_home_path": re.compile("/" + "Users" + "/"),
            "gh_config_dir_env": re.compile("GH_CONFIG" + "_DIR"),
            "github_identity_path": re.compile("github" + "-" + "identity"),
            "classic_github_token": re.compile("ghp" + "_" + "[A-Za-z0-9]{36}"),
            "fine_grained_github_token": re.compile("github_pat" + "_" + "[A-Za-z0-9_]{20,}"),
        }

        violations: list[str] = []
        for file_path in tracked_and_candidate_files():
            rel_path = file_path.relative_to(REPO_ROOT)
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            for rule_name, pattern in forbidden_patterns.items():
                match = pattern.search(content)
                if match is not None:
                    violations.append(f"{rel_path}: matched {rule_name} ({match.group(0)!r})")

        self.assertEqual(violations, [], "Forbidden personal path/token leak(s):\n" + "\n".join(violations))

    def test_hooks_verify_and_ci_workflow_contract(self) -> None:
        for rel_exec in ("scripts/verify", ".githooks/pre-commit", ".githooks/pre-push"):
            exec_path = REPO_ROOT / rel_exec
            self.assertTrue(exec_path.is_file(), f"Missing {rel_exec}")
            self.assertTrue(os.access(exec_path, os.X_OK), f"{rel_exec} is not executable")

        ci_workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        self.assertTrue(ci_workflow.is_file())
        ci_text = ci_workflow.read_text(encoding="utf-8")
        self.assertIn("pull_request:", ci_text)
        self.assertIn("contents: read", ci_text)
        self.assertIn("actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683", ci_text)
        self.assertIn("persist-credentials: false", ci_text)
        self.assertIn("./scripts/verify --all", ci_text)

    def test_herdr_cli_examples_and_dispatch_helper(self) -> None:
        md_files = [
            *sorted((SKILLS_DIR / "team-lead").rglob("*.md")),
        ]
        self.assertGreaterEqual(len(md_files), 6)

        split_re = re.compile(
            r"herdr\s+pane\s+split[^\n`\\]*(?:\\\s*\n[^\n`\\]*)*"
        )
        dup_binary_re = re.compile(
            r"herdr\s+agent\s+start[^\n`\\]*(?:\\\s*\n[^\n`\\]*)*"
            r"--\s+(?:codex|copilot|claude|cursor-agent)\s+"
        )

        split_matches_total = 0
        for md_path in md_files:
            rel = md_path.relative_to(REPO_ROOT)
            text = md_path.read_text(encoding="utf-8")
            for match in split_re.finditer(text):
                split_matches_total += 1
                snippet = match.group(0)
                self.assertIn(
                    "--direction",
                    snippet,
                    f"{rel}: 'herdr pane split' missing '--direction': {snippet!r}",
                )
            dup_match = dup_binary_re.search(text)
            self.assertIsNone(
                dup_match,
                f"{rel}: duplicate binary after 'herdr agent start --': "
                f"{dup_match.group(0) if dup_match else ''!r}",
            )

        self.assertGreaterEqual(split_matches_total, 4)

        canonical_helper = SKILLS_DIR / "team-lead" / "scripts" / "herdr-dispatch.sh"
        self.assertTrue(canonical_helper.is_file(), f"Missing {canonical_helper}")
        self.assertTrue(os.access(canonical_helper, os.X_OK), f"{canonical_helper} is not executable")

        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            bin_dir = tmp_dir / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            log_file = tmp_dir / "herdr-calls.jsonl"
            mock_herdr = bin_dir / "herdr"
            mock_herdr.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "log_path = os.environ.get('MOCK_HERDR_LOG')\n"
                "if log_path:\n"
                "    with open(log_path, 'a', encoding='utf-8') as fh:\n"
                "        fh.write(json.dumps(args) + '\\n')\n"
                "if args[:2] == ['pane', 'split']:\n"
                "    if '--direction' not in args:\n"
                "        sys.stderr.write('missing --direction\\n')\n"
                "        sys.exit(2)\n"
                "    if os.environ.get('MOCK_SPLIT_FAIL') == '1':\n"
                "        sys.stderr.write('split failed\\n')\n"
                "        sys.exit(2)\n"
                "    if os.environ.get('MOCK_SPLIT_EMPTY_PANE') == '1':\n"
                "        print(json.dumps({'result': {'pane': {'pane_id': ''}}}))\n"
                "        sys.exit(0)\n"
                "    print(json.dumps({'result': {'pane': {'pane_id': 'p99'}}}))\n"
                "    sys.exit(0)\n"
                "if args[:2] == ['agent', 'start']:\n"
                "    busy_marker = os.environ.get('MOCK_BUSY_ONCE_FILE', '')\n"
                "    if busy_marker and not os.path.exists(busy_marker):\n"
                "        open(busy_marker, 'w', encoding='utf-8').write('1')\n"
                "        sys.stderr.write(json.dumps({'error': {'code': 'agent_pane_busy', 'message': 'agent target pane p99 is not an available shell'}}) + '\\n')\n"
                "        sys.exit(1)\n"
                "    print(json.dumps({'ok': True}))\n"
                "    sys.exit(0)\n"
                "if args[:2] == ['agent', 'get']:\n"
                "    print(json.dumps({\n"
                "        'interactive_ready': True,\n"
                "        'agent_status': os.environ.get('MOCK_AGENT_STATUS', 'working'),\n"
                "    }))\n"
                "    sys.exit(0)\n"
                "if args[:2] == ['agent', 'prompt']:\n"
                "    if os.environ.get('MOCK_PROMPT_STALL') == '1':\n"
                "        sys.stderr.write('agent_prompt_stalled: initial state timeout 5000ms\\n')\n"
                "        sys.exit(1)\n"
                "    print(json.dumps({'ok': True}))\n"
                "    sys.exit(0)\n"
                "if args[:2] == ['agent', 'send-keys'] or args[:2] == ['pane', 'send-keys'] or args[:2] == ['agent', 'wait']:\n"
                "    sys.exit(0)\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            mock_herdr.chmod(0o755)

            base_env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "p1",
                "HERDR_PROMPT_IDLE_GRACE": "0",
                "MOCK_HERDR_LOG": str(log_file),
            }

            ok_res = subprocess.run(
                [
                    str(canonical_helper),
                    "--name",
                    "goal-tk4-writer-r1",
                    "--kind",
                    "codex",
                    "--cwd",
                    str(tmp_dir),
                    "--prompt",
                    "Read brief and write R10 receipt",
                    "--wait",
                    "--timeout",
                    "600000",
                    "--",
                    "codex",
                    "-m",
                    "gpt-5.6-luna",
                    "-s",
                    "danger-full-access",
                    "-a",
                    "never",
                ],
                cwd=REPO_ROOT,
                env=base_env,
                capture_output=True,
                text=True,
                check=True,
            )
            payload = json.loads(ok_res.stdout.strip())
            self.assertEqual(
                payload,
                {"ok": True, "pane_id": "p99", "agent": "goal-tk4-writer-r1"},
            )
            recorded_calls = [
                json.loads(line)
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(
                recorded_calls,
                [
                    [
                        "pane",
                        "split",
                        "--pane",
                        "p1",
                        "--direction",
                        "right",
                        "--cwd",
                        str(tmp_dir),
                        "--no-focus",
                    ],
                    [
                        "agent",
                        "start",
                        "goal-tk4-writer-r1",
                        "--kind",
                        "codex",
                        "--pane",
                        "p99",
                        "--",
                        "-m",
                        "gpt-5.6-luna",
                        "-s",
                        "danger-full-access",
                        "-a",
                        "never",
                    ],
                    ["agent", "get", "goal-tk4-writer-r1"],
                    [
                        "agent",
                        "prompt",
                        "goal-tk4-writer-r1",
                        "Read brief and write R10 receipt",
                        "--wait",
                        "--timeout",
                        "600000",
                    ],
                ],
            )

            log_file.write_text("", encoding="utf-8")
            fail_env = {**base_env, "MOCK_SPLIT_FAIL": "1"}
            fail_res = subprocess.run(
                [
                    str(canonical_helper),
                    "--name",
                    "goal-tk4-writer-r1",
                    "--kind",
                    "codex",
                    "--cwd",
                    str(tmp_dir),
                    "--prompt",
                    "Should never run",
                ],
                cwd=REPO_ROOT,
                env=fail_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(fail_res.returncode, 0)
            fail_calls = [
                json.loads(line)
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(fail_calls), 1)
            self.assertEqual(fail_calls[0][:2], ["pane", "split"])

            log_file.write_text("", encoding="utf-8")
            empty_pane_env = {**base_env, "MOCK_SPLIT_EMPTY_PANE": "1"}
            empty_res = subprocess.run(
                [
                    str(canonical_helper),
                    "--name",
                    "goal-tk4-writer-r1",
                    "--kind",
                    "codex",
                    "--cwd",
                    str(tmp_dir),
                    "--prompt",
                    "Should never run",
                ],
                cwd=REPO_ROOT,
                env=empty_pane_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(empty_res.returncode, 0)
            empty_calls = [
                json.loads(line)
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(empty_calls), 1)
            self.assertEqual(empty_calls[0][:2], ["pane", "split"])

            # Verify copilot auto-allowlist injection + agent_pane_busy retry
            log_file.write_text("", encoding="utf-8")
            busy_marker = tmp_dir / "busy.once"
            copilot_env = {
                **base_env,
                "MOCK_BUSY_ONCE_FILE": str(busy_marker),
                "HERDR_DISPATCH_RETRY_SLEEP": "0",
            }
            copilot_res = subprocess.run(
                [
                    str(canonical_helper),
                    "--name",
                    "goal-tk4-pr-monitor-r1",
                    "--kind",
                    "copilot",
                    "--cwd",
                    str(tmp_dir),
                    "--direction",
                    "down",
                    "--prompt",
                    "Monitor PR status",
                    "--",
                    "--model",
                    "mai-code-2-flash",
                ],
                cwd=REPO_ROOT,
                env=copilot_env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(
                json.loads(copilot_res.stdout.strip()),
                {"ok": True, "pane_id": "p99", "agent": "goal-tk4-pr-monitor-r1"},
            )
            copilot_calls = [
                json.loads(line)
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            # 1 pane split + 2 agent start (1st busy, 2nd ok) + get + prompt + idle get
            self.assertEqual(len(copilot_calls), 6)
            self.assertEqual(copilot_calls[1][:2], ["agent", "start"])
            self.assertEqual(copilot_calls[2][:2], ["agent", "start"])
            self.assertEqual(copilot_calls[3][:2], ["agent", "get"])
            self.assertEqual(copilot_calls[4][:2], ["agent", "prompt"])
            self.assertEqual(copilot_calls[5][:2], ["agent", "get"])
            self.assertIn("--allow-tool", copilot_calls[2])
            self.assertIn("write", copilot_calls[2])
            self.assertIn("--add-dir", copilot_calls[2])
            self.assertIn("/Volumes/SSD", copilot_calls[2])
            self.assertIn("--no-ask-user", copilot_calls[2])
            self.assertNotIn(["agent", "send-keys", "goal-tk4-pr-monitor-r1", "enter"], copilot_calls)

            log_file.write_text("", encoding="utf-8")
            stall_env = {**base_env, "MOCK_PROMPT_STALL": "1"}
            stall_res = subprocess.run(
                [
                    str(canonical_helper),
                    "--name",
                    "goal-tk4-writer-r1",
                    "--kind",
                    "codex",
                    "--cwd",
                    str(tmp_dir),
                    "--prompt",
                    "Submit the composer",
                    "--wait",
                    "--timeout",
                    "600000",
                ],
                cwd=REPO_ROOT,
                env=stall_env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(
                json.loads(stall_res.stdout.strip()),
                {"ok": True, "pane_id": "p99", "agent": "goal-tk4-writer-r1"},
            )
            stall_calls = [
                json.loads(line)
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(stall_calls[2][:2], ["agent", "get"])
            self.assertEqual(stall_calls[3][:2], ["agent", "prompt"])
            self.assertEqual(
                stall_calls[4],
                ["agent", "send-keys", "goal-tk4-writer-r1", "enter"],
            )
            self.assertEqual(
                stall_calls[5],
                ["agent", "wait", "goal-tk4-writer-r1", "--timeout", "600000"],
            )

            log_file.write_text("", encoding="utf-8")
            idle_env = {**base_env, "MOCK_AGENT_STATUS": "idle"}
            idle_res = subprocess.run(
                [
                    str(canonical_helper),
                    "--name",
                    "goal-tk4-writer-r1",
                    "--kind",
                    "codex",
                    "--cwd",
                    str(tmp_dir),
                    "--prompt",
                    "Needs enter",
                ],
                cwd=REPO_ROOT,
                env=idle_env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(json.loads(idle_res.stdout.strip())["ok"], True)
            idle_calls = [
                json.loads(line)
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertIn(
                ["agent", "send-keys", "goal-tk4-writer-r1", "enter"],
                idle_calls,
            )
            self.assertFalse(any(call[:2] == ["agent", "wait"] for call in idle_calls))

            log_file.write_text("", encoding="utf-8")
            done_env = {**base_env, "MOCK_AGENT_STATUS": "done"}
            done_res = subprocess.run(
                [
                    str(canonical_helper),
                    "--name",
                    "goal-tk4-writer-r1",
                    "--kind",
                    "codex",
                    "--cwd",
                    str(tmp_dir),
                    "--prompt",
                    "Needs enter after done",
                ],
                cwd=REPO_ROOT,
                env=done_env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(json.loads(done_res.stdout.strip())["ok"], True)
            done_calls = [
                json.loads(line)
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertIn(
                ["agent", "send-keys", "goal-tk4-writer-r1", "enter"],
                done_calls,
            )

        bots_and_agents_text = (
            SKILLS_DIR / "team-lead" / "references" / "bots-and-agents.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("高复杂度／并发 Task", bots_and_agents_text)

    def test_codex_trust_dedupes_adjacent_project_blocks(self) -> None:
        helper = SKILLS_DIR / "team-lead" / "scripts" / "herdr-dispatch.sh"
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            home = tmp_dir / "home"
            codex_dir = home / ".codex"
            codex_dir.mkdir(parents=True)
            config_path = codex_dir / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'model = "keep-me"',
                        "",
                        '[projects."/tmp"]',
                        'trust_level = "trusted"',
                        "",
                        '[projects."/tmp"]',
                        'trust_level = "trusted"',
                        "",
                        "[notice]",
                        "hide = true",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            bin_dir = tmp_dir / "bin"
            bin_dir.mkdir()
            mock_herdr = bin_dir / "herdr"
            mock_herdr.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "if args[:2] == ['pane', 'split']:\n"
                "    print(json.dumps({'result': {'pane': {'pane_id': 'p99'}}}))\n"
                "    sys.exit(0)\n"
                "if args[:2] == ['agent', 'get']:\n"
                "    print(json.dumps({'interactive_ready': True, 'agent_status': 'working'}))\n"
                "    sys.exit(0)\n"
                "if args[:2] == ['agent', 'start'] or args[:2] == ['agent', 'prompt']:\n"
                "    sys.exit(0)\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            mock_herdr.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
                "HOME": str(home),
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "p1",
                "HERDR_PROMPT_IDLE_GRACE": "0",
            }
            cmd = [
                str(helper),
                "--name",
                "goal-tk4-writer-r1",
                "--kind",
                "codex",
                "--cwd",
                str(tmp_dir),
                "--prompt",
                "trust",
                "--wait",
            ]
            first = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            text = config_path.read_text(encoding="utf-8")
            self.assertEqual(text.count('[projects."/tmp"]'), 1)
            self.assertIn('model = "keep-me"', text)
            self.assertIn("[notice]", text)
            self.assertIn("hide = true", text)
            second = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False)
            self.assertEqual(second.returncode, 0, second.stderr)
            again = config_path.read_text(encoding="utf-8")
            self.assertEqual(again.count('[projects."/tmp"]'), 1)
            self.assertEqual(again, text)

    def test_copilot_ssd_baseline_covers_handoff_sibling_realpath(self) -> None:
        helper = SKILLS_DIR / "team-lead" / "scripts" / "herdr-dispatch.sh"
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            home = tmp_dir / "home"
            copilot_dir = home / ".copilot"
            copilot_dir.mkdir(parents=True)
            config_path = copilot_dir / "config.json"
            config_path.write_text('{"trustedFolders": []}\n', encoding="utf-8")
            goal_dir = tmp_dir / "handoff" / "good-designer"
            goal_dir.mkdir(parents=True)
            bin_dir = tmp_dir / "bin"
            bin_dir.mkdir()
            log_file = tmp_dir / "herdr-calls.jsonl"
            mock_herdr = bin_dir / "herdr"
            mock_herdr.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "log_path = os.environ.get('MOCK_HERDR_LOG')\n"
                "if log_path:\n"
                "    with open(log_path, 'a', encoding='utf-8') as fh:\n"
                "        fh.write(json.dumps(args) + '\\n')\n"
                "if args[:2] == ['pane', 'split']:\n"
                "    print(json.dumps({'result': {'pane': {'pane_id': 'p99'}}}))\n"
                "    sys.exit(0)\n"
                "if args[:2] == ['agent', 'get']:\n"
                "    print(json.dumps({'interactive_ready': True, 'agent_status': 'working'}))\n"
                "    sys.exit(0)\n"
                "if args[:2] == ['agent', 'start'] or args[:2] == ['agent', 'prompt']:\n"
                "    sys.exit(0)\n"
                "sys.exit(1)\n",
                encoding="utf-8",
            )
            mock_herdr.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
                "HOME": str(home),
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "p1",
                "HERDR_PROMPT_IDLE_GRACE": "0",
                "MOCK_HERDR_LOG": str(log_file),
                "HERDR_DISPATCH_SKIP_TRUST": "0",
            }
            # MOCK_HERDR_LOG skips trust registration; run once without it for
            # trustedFolders, then again with the log to read session --add-dir.
            trust_env = {key: value for key, value in env.items() if key != "MOCK_HERDR_LOG"}
            trust = subprocess.run(
                [
                    str(helper),
                    "--name",
                    "good-des-goal-tl-r1",
                    "--kind",
                    "copilot",
                    "--cwd",
                    str(goal_dir),
                    "--prompt",
                    "trust siblings",
                ],
                cwd=REPO_ROOT,
                env=trust_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(trust.returncode, 0, trust.stderr)
            trusted = json.loads(config_path.read_text(encoding="utf-8"))["trustedFolders"]
            handoff_real = os.path.realpath(goal_dir.parent)
            self.assertIn("/Volumes/SSD", trusted)
            self.assertIn(handoff_real, trusted)
            self.assertIn(str(goal_dir), trusted)

            logged = subprocess.run(
                [
                    str(helper),
                    "--name",
                    "good-des-writer-r1",
                    "--kind",
                    "copilot",
                    "--cwd",
                    str(goal_dir),
                    "--prompt",
                    "allow siblings",
                    "--",
                    "--model",
                    "gpt-5.6-luna",
                    "--add-dir",
                    str(goal_dir),
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(logged.returncode, 0, logged.stderr)
            calls = [
                json.loads(line)
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            start = next(call for call in calls if call[:2] == ["agent", "start"])
            self.assertIn("/Volumes/SSD", start)
            self.assertIn(handoff_real, start)
            self.assertEqual(start.count(handoff_real), 1)

    def test_goal_queue_batch_and_hot_append(self) -> None:
        canonical_queue = SKILLS_DIR / "team-lead" / "scripts" / "goal-queue.py"
        self.assertTrue(canonical_queue.is_file())
        self.assertEqual(canonical_queue.stat().st_mode & 0o777, 0o755)

        for doc_rel in (
            "skills/team-lead/SKILL.md",
            "skills/team-lead/references/bots-and-agents.md",
            "skills/team-lead/references/ledger.md",
            "skills/team-lead/references/playbook.md",
        ):
            doc_text = (REPO_ROOT / doc_rel).read_text(encoding="utf-8")
            self.assertIn(
                "### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）",
                doc_text,
                f"Missing Batch & Hot-Append section in {doc_rel}",
            )
        ledger_ref = (SKILLS_DIR / "team-lead" / "references" / "ledger.md").read_text(encoding="utf-8")
        self.assertIn("`HOT_APPEND`", ledger_ref)

        def run_queue(*cli_args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(canonical_queue), *cli_args],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=check,
            )

        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            handoff_dir = tmp_dir / "handoff-goal"

            # Posture 1 (Batch Mode): 3 tasks — TK1 & TK2 disjoint repos (Wave 1), TK3 overlaps TK1 paths (Wave 2)
            batch_spec = [
                {
                    "task_id": "TK1",
                    "title": "Shared CI runner",
                    "repo": "LeePepe/shared-ci",
                    "paths": ["scripts/verify/**"],
                    "brief": "briefs/TK1.md",
                },
                {
                    "task_id": "TK2",
                    "title": "WebIQ CI config",
                    "repo": "LeePepe/WebIQSearch",
                    "paths": [".github/workflows/ci.yml"],
                    "brief": "briefs/TK2.md",
                },
                {
                    "task_id": "TK3",
                    "title": "Shared CI verify follow-up",
                    "repo": "LeePepe/shared-ci",
                    "paths": ["scripts/verify/parallel_verify.py"],
                    "brief": "briefs/TK3.md",
                },
            ]
            enq_res = json.loads(
                run_queue(
                    "enqueue",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--batch-json",
                    json.dumps(batch_spec),
                ).stdout
            )
            self.assertTrue(enq_res["ok"])
            self.assertFalse(enq_res["hot_append"])
            self.assertEqual(
                enq_res["auto_dependencies"],
                {"TK1": [], "TK2": [], "TK3": ["TK1"]},
            )
            self.assertFalse(enq_res["drained"])

            ledger_text = (handoff_dir / "ledger.md").read_text(encoding="utf-8")
            self.assertIn("EVT=READY", ledger_text)
            self.assertIn("| TK3 | TK1 |", ledger_text)

            # Wave 1 dispatches TK1 and TK2 in parallel; TK3 stays pending
            wave1 = json.loads(
                run_queue(
                    "next-wave",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--mark-in-progress",
                ).stdout
            )
            self.assertEqual([t["task_id"] for t in wave1["wave"]], ["TK1", "TK2"])
            self.assertEqual(wave1["in_progress_count"], 2)
            self.assertEqual(wave1["pending_count"], 1)
            self.assertFalse(wave1["drained"])

            # Posture 3 (Hot-Append while Wave 1 is in_progress):
            # 1) Hot-append TK4 in a disjoint repo -> eligible for immediate wave
            hot_disjoint = json.loads(
                run_queue(
                    "enqueue",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--task-id",
                    "TK4",
                    "--title",
                    "User skills docs",
                    "--repo",
                    "LeePepe/user-skills",
                    "--paths",
                    "skills/team-lead/SKILL.md",
                    "--brief",
                    "briefs/TK4.md",
                ).stdout
            )
            self.assertTrue(hot_disjoint["ok"])
            self.assertTrue(hot_disjoint["hot_append"])
            self.assertEqual(hot_disjoint["auto_dependencies"], {"TK4": []})

            # 2) Hot-append TK5 overlapping active TK2 (wildcard empty paths on same repo) -> held behind TK2
            hot_overlap = json.loads(
                run_queue(
                    "enqueue",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--task-id",
                    "TK5",
                    "--title",
                    "WebIQ repo-wide sweep",
                    "--repo",
                    "LeePepe/WebIQSearch",
                    "--brief",
                    "briefs/TK5.md",
                ).stdout
            )
            self.assertTrue(hot_overlap["ok"])
            self.assertTrue(hot_overlap["hot_append"])
            self.assertEqual(hot_overlap["auto_dependencies"], {"TK5": ["TK2"]})

            ledger_after_hot = (handoff_dir / "ledger.md").read_text(encoding="utf-8")
            self.assertIn("EVT=HOT_APPEND | id=E6 | task=TK4", ledger_after_hot)
            self.assertIn("EVT=HOT_APPEND | id=E7 | task=TK5", ledger_after_hot)

            # Immediate next-wave picks up disjoint hot-appended TK4 while holding TK3 and TK5
            wave_hot = json.loads(
                run_queue(
                    "next-wave",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--mark-in-progress",
                ).stdout
            )
            self.assertEqual([t["task_id"] for t in wave_hot["wave"]], ["TK4"])
            self.assertEqual(wave_hot["in_progress_count"], 3)
            self.assertEqual(wave_hot["pending_count"], 2)

            # Complete TK1, TK2, TK4 -> unlocks TK3 and TK5 for final wave
            for done_id in ("TK1", "TK2", "TK4"):
                st_res = json.loads(
                    run_queue(
                        "set-status",
                        "--handoff-dir",
                        str(handoff_dir),
                        "--task-id",
                        done_id,
                        "--status",
                        "done",
                        "--pr",
                        f"PR#{done_id}",
                    ).stdout
                )
                self.assertFalse(st_res["drained"])

            wave2 = json.loads(
                run_queue(
                    "next-wave",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--mark-in-progress",
                ).stdout
            )
            self.assertEqual([t["task_id"] for t in wave2["wave"]], ["TK3", "TK5"])
            self.assertEqual(wave2["in_progress_count"], 2)
            self.assertEqual(wave2["pending_count"], 0)
            self.assertFalse(wave2["drained"])

            run_queue(
                "set-status",
                "--handoff-dir",
                str(handoff_dir),
                "--task-id",
                "TK3",
                "--status",
                "done",
            )
            final_status = json.loads(
                run_queue(
                    "set-status",
                    "--handoff-dir",
                    str(handoff_dir),
                    "--task-id",
                    "TK5",
                    "--status",
                    "done",
                ).stdout
            )
            self.assertTrue(final_status["drained"])

            empty_wave = json.loads(
                run_queue("next-wave", "--handoff-dir", str(handoff_dir)).stdout
            )
            self.assertEqual(empty_wave["wave"], [])
            self.assertTrue(empty_wave["drained"])

            # Guard against path traversal and symlinks
            traversal_res = run_queue(
                "next-wave",
                "--handoff-dir",
                f"{handoff_dir}/../escape",
                check=False,
            )
            self.assertNotEqual(traversal_res.returncode, 0)

            symlink_dir = tmp_dir / "symlink-handoff"
            symlink_dir.symlink_to(handoff_dir)
            symlink_res = run_queue(
                "next-wave",
                "--handoff-dir",
                str(symlink_dir),
                check=False,
            )
            self.assertNotEqual(symlink_res.returncode, 0)

    def test_universal_skill_anti_explosion_valves(self) -> None:
        # (a) Every skills/*/SKILL.md is <= 260 lines
        for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            rel = skill_md.relative_to(REPO_ROOT).as_posix()
            line_count = len(skill_md.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(line_count, 260, f"{rel} exceeds 260-line L0 cap ({line_count} lines)")

        # (b) Every skills/*/references/*.md is <= 320 lines, and playbook*.md + leaf refs in
        # my-designer, xcode-cli, code-review, and deep-research are <= 160 lines
        strict_leaf_skills = {"my-designer", "xcode-cli", "code-review", "deep-research"}
        ref_md_files = sorted(SKILLS_DIR.glob("*/references/*.md"))
        self.assertGreaterEqual(len(ref_md_files), 20)
        for ref_md in ref_md_files:
            rel = ref_md.relative_to(REPO_ROOT).as_posix()
            skill_name = ref_md.parent.parent.name
            line_count = len(ref_md.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(line_count, 320, f"{rel} exceeds 320-line reference cap ({line_count} lines)")
            if skill_name in strict_leaf_skills or ref_md.name.startswith("playbook"):
                self.assertLessEqual(
                    line_count,
                    160,
                    f"{rel} exceeds 160-line strict L1 leaf cap ({line_count} lines)",
                )

        # (c) No orphan references/*.md: every reference .md is linked from a SKILL.md, sibling .md, or script
        searchable_files = [
            *sorted(SKILLS_DIR.rglob("*.md")),
            *sorted(SKILLS_DIR.rglob("*.py")),
            *sorted(SKILLS_DIR.rglob("*.sh")),
            REPO_ROOT / "README.md",
        ]
        for ref_md in ref_md_files:
            rel = ref_md.relative_to(REPO_ROOT).as_posix()
            ref_name = ref_md.name
            linked = any(
                candidate.resolve() != ref_md.resolve()
                and (ref_name in candidate.read_text(encoding="utf-8", errors="ignore")
                     or rel in candidate.read_text(encoding="utf-8", errors="ignore"))
                for candidate in searchable_files
            )
            self.assertTrue(linked, f"Orphan reference markdown not linked by any SKILL.md, sibling .md, or script: {rel}")

        # (d) All 4 domain flow-map CLIs exit 0 with valid JSON and all_l1_docs_within_budget == True
        flow_map_commands = (
            (SKILLS_DIR / "my-designer" / "scripts" / "verify_design_cases.py", ["flow-map"]),
            (SKILLS_DIR / "xcode-cli" / "scripts" / "classify_changes.py", ["--flow-map"]),
            (SKILLS_DIR / "code-review" / "scripts" / "discover_review_context.py", ["--flow-map"]),
            (SKILLS_DIR / "deep-research" / "scripts" / "jev_research_helper.py", ["flow-map"]),
        )
        for script_path, extra_args in flow_map_commands:
            with self.subTest(script=str(script_path.relative_to(REPO_ROOT))):
                res = subprocess.run(
                    [sys.executable, str(script_path), *extra_args],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                payload = json.loads(res.stdout)
                self.assertTrue(payload.get("ok"), f"{script_path.name} flow-map not ok: {payload}")
                self.assertTrue(
                    payload.get("anti_explosion_valves", {}).get("all_l1_docs_within_budget"),
                    f"{script_path.name} L1 docs exceeded budget: {payload}",
                )

    def test_post_implementation_visual_traceback_and_impl_bug_spec_gap_contract(self) -> None:
        my_designer_skill = (SKILLS_DIR / "my-designer" / "SKILL.md").read_text(encoding="utf-8")
        my_designer_routing = (
            SKILLS_DIR / "my-designer" / "references" / "routing-index.md"
        ).read_text(encoding="utf-8")
        my_designer_arch = (
            SKILLS_DIR / "my-designer" / "references" / "design-flow-architecture.md"
        ).read_text(encoding="utf-8")
        code_review_skill = (SKILLS_DIR / "code-review" / "SKILL.md").read_text(encoding="utf-8")
        reviewer_prompts = (
            SKILLS_DIR / "code-review" / "references" / "reviewer-prompts.md"
        ).read_text(encoding="utf-8")
        team_lead_skill = (SKILLS_DIR / "team-lead" / "SKILL.md").read_text(encoding="utf-8")
        bots_and_agents = (
            SKILLS_DIR / "team-lead" / "references" / "bots-and-agents.md"
        ).read_text(encoding="utf-8")

        self.assertLessEqual(
            len(my_designer_skill.splitlines()),
            80,
            "my-designer/SKILL.md must stay <= 80 lines",
        )
        for name, text in (
            ("skills/my-designer/SKILL.md", my_designer_skill),
            ("skills/my-designer/references/routing-index.md", my_designer_routing),
            ("skills/my-designer/references/design-flow-architecture.md", my_designer_arch),
            ("skills/code-review/SKILL.md", code_review_skill),
            ("skills/code-review/references/reviewer-prompts.md", reviewer_prompts),
            ("skills/team-lead/SKILL.md", team_lead_skill),
            ("skills/team-lead/references/bots-and-agents.md", bots_and_agents),
        ):
            with self.subTest(doc=name):
                self.assertIn("Visual Traceback", text)
                self.assertIn("[IMPL_BUG]", text)
                self.assertIn("[SPEC_GAP]", text)
                self.assertIn("design.md", text)

        for text in (my_designer_skill, my_designer_routing, team_lead_skill, bots_and_agents):
            self.assertIn("Playwright", text)
            self.assertIn("ImageRenderer", text)
            self.assertIn("spec_sha", text)
            self.assertIn("sink-rule", text)

    def test_skill_contract_add_case_and_eval_engine(self) -> None:
        script = SKILLS_DIR / "skill-contract" / "scripts" / "skill_contract.py"
        self.assertTrue(script.is_file())

        audit_res = subprocess.run(
            [sys.executable, str(script), "audit", "--all", "--strict"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        audit_payload = json.loads(audit_res.stdout)
        self.assertTrue(audit_payload.get("ok"), f"audit --all --strict failed: {audit_payload}")

        eval_res = subprocess.run(
            [sys.executable, str(script), "eval", "--all", "--strict"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        eval_payload = json.loads(eval_res.stdout)
        self.assertTrue(eval_payload.get("ok"), f"eval --all --strict failed: {eval_payload}")
        self.assertGreaterEqual(eval_payload.get("drawers_evaluated", 0), 5)
        self.assertGreaterEqual(eval_payload.get("cases_run", 0), 17)
        self.assertEqual(eval_payload.get("passed"), eval_payload.get("cases_run"))
        self.assertEqual(eval_payload.get("failures"), [])

        with tempfile.TemporaryDirectory() as tmp:
            drawer = Path(tmp) / "eval-cases.json"
            pass_res = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "add-case",
                    "--skill",
                    "skill-contract",
                    "--drawer",
                    str(drawer),
                    "--id",
                    "auto",
                    "--title",
                    "Reject inline force unwrap in sample pattern",
                    "--tags",
                    "safety,regex",
                    "--forbidden-regex",
                    r"try!\s+\w+",
                    "--bad-snippet",
                    "let x = try! load()",
                    "--good-snippet",
                    "let x = try? load()",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            pass_data = json.loads(pass_res.stdout)
            self.assertTrue(pass_data["ok"])
            self.assertEqual(pass_data["id"], "EV-001")
            saved_doc = json.loads(drawer.read_text(encoding="utf-8"))
            self.assertEqual(len(saved_doc["active_cases"]), 1)

            # Reject when good_snippet falsely trips forbidden_regex
            bad_regex_res = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "add-case",
                    "--skill",
                    "skill-contract",
                    "--drawer",
                    str(drawer),
                    "--id",
                    "auto",
                    "--title",
                    "Overly broad regex trips good snippet",
                    "--tags",
                    "safety",
                    "--forbidden-regex",
                    r"load\(\)",
                    "--bad-snippet",
                    "let x = try! load()",
                    "--good-snippet",
                    "let x = try? load()",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(bad_regex_res.returncode, 0)
            self.assertEqual(len(json.loads(drawer.read_text(encoding="utf-8"))["active_cases"]), 1)

            # Reject when private identifier leaks into case
            private_res = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "add-case",
                    "--skill",
                    "skill-contract",
                    "--drawer",
                    str(drawer),
                    "--id",
                    "auto",
                    "--title",
                    "Leak progressRail private symbol",
                    "--tags",
                    "privacy",
                    "--forbidden-regex",
                    r"progressRail",
                    "--bad-snippet",
                    "progressRail = 1",
                    "--good-snippet",
                    "rail = 1",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(private_res.returncode, 0)
            self.assertEqual(len(json.loads(drawer.read_text(encoding="utf-8"))["active_cases"]), 1)

            # Verify --repo mode for Repo-Bound Skills (init, add-case, audit, eval)
            temp_repo = Path(tmp) / "sample-repo"
            init_repo_res = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "init",
                    "--repo",
                    str(temp_repo),
                    "--skill",
                    "sample-domain-skill",
                    "--category",
                    "repo",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertTrue(json.loads(init_repo_res.stdout)["ok"])
            skill_md = temp_repo / "skills" / "sample-domain-skill" / "SKILL.md"
            self.assertTrue(skill_md.is_file())
            # Repo-bound skill may contain repo-specific identifiers without triggering global PRIVATE guard
            skill_md.write_text(
                skill_md.read_text(encoding="utf-8")
                + "\nRepo-bound domain reference: progressRail is allowed inside repo-bound skills.\n"
                + "See [eval-cases.json](references/eval-cases.json).\n",
                encoding="utf-8",
            )
            # Also add a second repo-bound skill under .agents/skills/
            dot_agent_skill = temp_repo / ".agents" / "skills" / "sample-agent-toolkit"
            dot_agent_skill.mkdir(parents=True, exist_ok=True)
            (dot_agent_skill / "SKILL.md").write_text(
                "---\nname: sample-agent-toolkit\ndescription: \"Toolkit\"\n---\n\n# Toolkit\n",
                encoding="utf-8",
            )

            add_repo_case_res = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "add-case",
                    "--repo",
                    str(temp_repo),
                    "--skill",
                    "sample-domain-skill",
                    "--id",
                    "auto",
                    "--title",
                    "Repo-bound skill SKILL.md contains domain note",
                    "--tags",
                    "repo-bound",
                    "--file",
                    "skills/sample-domain-skill/SKILL.md",
                    "--must-contain",
                    "progressRail",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertTrue(json.loads(add_repo_case_res.stdout)["ok"])

            audit_repo_res = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "audit",
                    "--repo",
                    str(temp_repo),
                    "--strict",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            audit_repo_payload = json.loads(audit_repo_res.stdout)
            self.assertTrue(audit_repo_payload["ok"], f"audit --repo failed: {audit_repo_payload}")
            self.assertEqual(audit_repo_payload["skills"], 2)

            eval_repo_res = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "eval",
                    "--repo",
                    str(temp_repo),
                    "--strict",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            eval_repo_payload = json.loads(eval_repo_res.stdout)
            self.assertTrue(eval_repo_payload["ok"], f"eval --repo failed: {eval_repo_payload}")
            self.assertEqual(eval_repo_payload["cases_run"], 1)
            self.assertEqual(eval_repo_payload["passed"], 1)

    def test_domain_modeling_consolidated_into_codebase_design(self) -> None:
        self.assertFalse(
            (SKILLS_DIR / "domain-modeling").exists(),
            "skills/domain-modeling must remain merged into skills/codebase-design",
        )
        marketplace = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        plugin_names = {plugin.get("name") for plugin in marketplace.get("plugins", [])}
        self.assertIn("codebase-design", plugin_names)
        self.assertNotIn("domain-modeling", plugin_names)

        codebase_dir = SKILLS_DIR / "codebase-design"
        skill_md = codebase_dir / "SKILL.md"
        skill_text = skill_md.read_text(encoding="utf-8")
        self.assertLessEqual(len(skill_text.splitlines()), 120)
        self.assertLessEqual(skill_md.stat().st_size, 12 * 1024)
        for block in FENCED_CODE_RE.findall(skill_text):
            inner_lines = block.splitlines()[1:-1]
            self.assertLessEqual(len(inner_lines), 25)

        for ref_name in ("ADR-FORMAT.md", "CONTEXT-FORMAT.md", "DEEPENING.md", "DESIGN-IT-TWICE.md", "PHASE0-PROPOSAL-CHECKPOINT.md"):
            ref_path = codebase_dir / "references" / ref_name
            self.assertTrue(ref_path.is_file(), f"Missing {ref_path}")
            self.assertIn(f"references/{ref_name}", skill_text)

        context_text = (codebase_dir / "references" / "CONTEXT-FORMAT.md").read_text(encoding="utf-8")
        for block in FENCED_CODE_RE.findall(context_text):
            inner_lines = block.splitlines()[1:-1]
            self.assertLessEqual(len(inner_lines), 25)


if __name__ == "__main__":
    unittest.main()
