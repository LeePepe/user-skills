#!/usr/bin/env python3
"""Repository-wide verification suite for LeePepe/user-skills."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
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

        self.assertIn("worker: [writer, explorer, pr_monitor, mechanical]", content)
        self.assertIn("copilot_allowlist:", content)
        self.assertNotIn("gemini-3.8-flash", content)
        self.assertNotIn("composer-2.5", content)
        self.assertNotIn("kimi-k3-high", content)
        self.assertIn("kimi: moonshot", content)
        self.assertIn("model: kimi,", content)
        self.assertGreaterEqual(content.count("pr_monitor:"), 2)
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("- {cli: copilot,") or stripped.startswith("planner_reviewer: {cli: copilot,") or stripped.startswith("research_primary: {cli: copilot,") or stripped.startswith("team_lead: {cli: copilot,") or stripped.startswith("arbitration: {cli: copilot,"):
                self.assertIn("--allow-tool", stripped, f"Missing --allow-tool on copilot profile line: {stripped}")
                self.assertIn("--add-dir ~/Development", stripped, f"Missing --add-dir on copilot profile line: {stripped}")

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

    def test_goal_task_is_the_only_contract_skill(self) -> None:
        self.assertFalse(
            (SKILLS_DIR / "goal-target-task").exists(),
            "skills/goal-target-task compatibility mirror must stay removed",
        )
        marketplace = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        plugin_names = {plugin.get("name") for plugin in marketplace.get("plugins", [])}
        self.assertIn("goal-task", plugin_names)
        self.assertNotIn("goal-target-task", plugin_names)

        canonical_dir = SKILLS_DIR / "goal-task"
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
        self.assertEqual(canonical_fm.fields.get("name"), "goal-task")
        canonical_body = "\n".join(canonical_fm.body_lines)
        self.assertNotIn("../goal-target-task/", canonical_body)
        self.assertNotIn("兼容目录名", canonical_body)
        self.assertIn("唯一技能名是 `goal-task`", canonical_body)
        for anchor_heading in ("## 定义、冻结与缺口", "## 限定审查"):
            self.assertIn(
                anchor_heading,
                canonical_body,
                f"Missing required heading {anchor_heading!r} in SKILL.md",
            )

    def test_bot_tl_orchestration_shared_components_contract(self) -> None:
        skill_dir = SKILLS_DIR / "bot-tl-orchestration"
        skill_path = skill_dir / "SKILL.md"
        manifest_path = skill_dir / ".claude-plugin" / "plugin.json"
        skill_text = skill_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest.get("name"), "bot-tl-orchestration")
        self.assertEqual(
            manifest.get("description"),
            "常驻域主 Bot 编排 Herdr Team Lead (TL) 的共用生命周期合同：开 TL、热追加、巡检解卡与 Goal/Task 完结回收。",
        )
        self.assertEqual(manifest.get("category"), "workflow")
        self.assertIn("bot-tl-orchestration", skill_text)

        shared_targets = (
            "../goal-task/references/runtime-profiles.yaml",
            "../goal-task/references/bots-and-agents.md",
            "../goal-task/references/ledger.md",
            "../goal-task/scripts/goal-queue.py",
            "../goal-task/scripts/herdr-dispatch.sh",
        )
        for target in shared_targets:
            with self.subTest(target=target):
                self.assertIn(f"]({target})", skill_text)
                self.assertTrue((skill_path.parent / target).resolve().is_file())

        for document in (
            skill_path,
            SKILLS_DIR / "goal-task" / "references" / "playbook.md",
            SKILLS_DIR / "goal-task" / "references" / "bots-and-agents.md",
        ):
            with self.subTest(document=str(document.relative_to(REPO_ROOT))):
                text = document.read_text(encoding="utf-8")
                self.assertIn("PR_MERGED", text)
                self.assertIn("GOAL_DONE", text)
                self.assertIn("git worktree remove", text)
                self.assertIn("pr_monitor", text)

    def test_merged_worktree_cleanup_contract(self) -> None:
        skill_text = (SKILLS_DIR / "goal-task" / "SKILL.md").read_text(encoding="utf-8")
        agents_text = (
            SKILLS_DIR / "goal-task" / "references" / "bots-and-agents.md"
        ).read_text(encoding="utf-8")

        for text in (skill_text, agents_text):
            self.assertIn("PR_MERGED", text)
            self.assertIn("git worktree remove", text)
            self.assertIn("未合并／暂停（HOLD）", text)
            self.assertIn("Owner", text)

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
            *sorted((SKILLS_DIR / "goal-task").rglob("*.md")),
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

        canonical_helper = SKILLS_DIR / "goal-task" / "scripts" / "herdr-dispatch.sh"
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

        bots_and_agents_text = (
            SKILLS_DIR / "goal-task" / "references" / "bots-and-agents.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("高复杂度／并发 Task", bots_and_agents_text)

    def test_codex_trust_dedupes_adjacent_project_blocks(self) -> None:
        helper = SKILLS_DIR / "goal-task" / "scripts" / "herdr-dispatch.sh"
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

    def test_goal_queue_batch_and_hot_append(self) -> None:
        canonical_queue = SKILLS_DIR / "goal-task" / "scripts" / "goal-queue.py"
        self.assertTrue(canonical_queue.is_file())
        self.assertEqual(canonical_queue.stat().st_mode & 0o777, 0o755)

        for doc_rel in (
            "skills/goal-task/SKILL.md",
            "skills/goal-task/references/bots-and-agents.md",
            "skills/goal-task/references/ledger.md",
            "skills/goal-task/references/playbook.md",
        ):
            doc_text = (REPO_ROOT / doc_rel).read_text(encoding="utf-8")
            self.assertIn(
                "### 任务收集与批量／热追加队列（Batch Mode 姿势 1 & Hot-Append 姿势 3 — Bot 与 TL 协同）",
                doc_text,
                f"Missing Batch & Hot-Append section in {doc_rel}",
            )
        ledger_ref = (SKILLS_DIR / "goal-task" / "references" / "ledger.md").read_text(encoding="utf-8")
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
                    "skills/goal-task/SKILL.md",
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


if __name__ == "__main__":
    unittest.main()
