#!/usr/bin/env python3
"""Repository-wide verification suite for LeePepe/user-skills."""

from __future__ import annotations

import json
import os
import re
import subprocess
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SKILLS_DIR: Path = REPO_ROOT / "skills"
FENCED_CODE_RE: re.Pattern[str] = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE: re.Pattern[str] = re.compile(r"`[^`\n]+`")
MARKDOWN_LINK_RE: re.Pattern[str] = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


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
        profile_path = SKILLS_DIR / "goal-task" / "references" / "runtime-profiles.yaml"
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

    def test_goal_task_and_goal_target_task_parity(self) -> None:
        canonical_dir = SKILLS_DIR / "goal-task"
        compat_dir = SKILLS_DIR / "goal-target-task"
        canonical_refs = canonical_dir / "references"
        compat_refs = compat_dir / "references"

        self.assertTrue(canonical_refs.is_dir())
        self.assertTrue(compat_refs.is_dir())

        canonical_files = {p.name: p for p in canonical_refs.iterdir() if p.is_file()}
        compat_files = {p.name: p for p in compat_refs.iterdir() if p.is_file()}
        self.assertEqual(set(canonical_files.keys()), set(compat_files.keys()))
        self.assertGreaterEqual(len(canonical_files), 1)

        for filename, canonical_file in sorted(canonical_files.items()):
            compat_file = compat_files[filename]
            self.assertEqual(
                canonical_file.read_bytes(),
                compat_file.read_bytes(),
                f"Reference drift detected between goal-task and goal-target-task in {filename}",
            )

        canonical_fm = parse_frontmatter(canonical_dir / "SKILL.md")
        compat_fm = parse_frontmatter(compat_dir / "SKILL.md")
        self.assertEqual(canonical_fm.fields.get("description"), compat_fm.fields.get("description"))

        def strip_compat_banner(body_lines: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(
                line
                for line in body_lines
                if not line.startswith("> **向后兼容说明**：")
                and not line.startswith("> **兼容转发说明**：")
            )

        self.assertEqual(
            strip_compat_banner(canonical_fm.body_lines),
            strip_compat_banner(compat_fm.body_lines),
            "SKILL.md body drift detected between skills/goal-task and skills/goal-target-task",
        )

        canonical_body = "\n".join(canonical_fm.body_lines)
        for anchor_heading in ("## 定义、冻结与缺口", "## 限定审查"):
            self.assertIn(
                anchor_heading,
                canonical_body,
                f"Missing required heading {anchor_heading!r} in SKILL.md",
            )

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


if __name__ == "__main__":
    unittest.main()
