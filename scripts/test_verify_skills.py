#!/usr/bin/env python3
"""Repository-wide verification suite for LeePepe/user-skills."""

from __future__ import annotations

import argparse
import json
import importlib.util
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from unittest.mock import patch


os.environ.setdefault("GTT_LINEAR_SYNC", "0")

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SKILLS_DIR: Path = REPO_ROOT / "skills"


def _png_fixture(width=390, height=844):
    import struct, zlib, binascii
    raw = b''.join(b'\x00' + b'\xff\xff\xff\xff' * width for _ in range(height))
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', binascii.crc32(kind + payload) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
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
    def test_web_liquid_glass_capability_isolated_from_mobile(self) -> None:
        catalog = SKILLS_DIR / "design-studio" / "scripts" / "design_catalog.py"
        taxonomy = SKILLS_DIR / "design-studio" / "references" / "taxonomy-v2.json"
        vault = SKILLS_DIR / "design-studio" / "references" / "vault"
        def run_catalog(platform: str) -> dict:
            result = subprocess.run([sys.executable, str(catalog), "vault-search", "--platform", platform, "--dimension", "glass-material", "--query", "Liquid Glass", "--taxonomy", str(taxonomy), "--vault-dir", str(vault)], capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        web = run_catalog("web")
        gm = next(item for item in web["matches"] if item["id"] == "GM-004")
        self.assertIn("feDisplacementMap", gm["web_implementation"])
        self.assertIn("html_recipe_ref", gm)
        mobile_text = json.dumps(run_catalog("mobile"), ensure_ascii=False)
        for forbidden in ("GM-004", "feDisplacementMap", "apple-liquid-glass-html-recipe"):
            self.assertNotIn(forbidden, mobile_text)
        route = subprocess.run([sys.executable, str(SKILLS_DIR / "my-designer" / "scripts" / "verify_design_cases.py"), "route", "--platform", "web", "--dimensions", "glass-material"], capture_output=True, text=True, check=True)
        self.assertIn("apple_liquid_glass_web", json.loads(route.stdout)["external_skill_sources"]["allowed_web"])
        mobile_route = subprocess.run([sys.executable, str(SKILLS_DIR / "my-designer" / "scripts" / "verify_design_cases.py"), "route", "--platform", "mobile", "--dimensions", "glass-material"], capture_output=True, text=True, check=True)
        mobile_route_text = mobile_route.stdout
        self.assertIn("apple_liquid_glass_web", mobile_route_text)
        self.assertNotIn("feDisplacementMap", mobile_route_text)
        self.assertNotIn("apple-liquid-glass-html-recipe", mobile_route_text)

    def test_multiplatform_gallery_contract_is_wired(self) -> None:
        catalog = SKILLS_DIR / "design-studio" / "scripts" / "design_catalog.py"
        evidence = SKILLS_DIR / "my-designer" / "scripts" / "design_evidence.py"
        result = subprocess.run(['node', str(catalog.with_name('gallery_chooser_test.cjs'))],
                                capture_output=True, text=True, check=True)
        self.assertIn('PASS:', result.stdout)
        spec = importlib.util.spec_from_file_location('gallery_contract_test', catalog)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        manifest = {'goal': 'contract', 'round': 1, 'platform': 'web', 'variants': []}
        chooser = module._gallery_index(manifest)
        # Execute the renderer, not string-search its Python implementation.
        self.assertIn('id="responsive-toggle"', chooser)
        self.assertIn('src="current.html"', chooser)
        self.assertIn(module._gallery_tokens(), chooser)
        self.assertIn('c._gallery_index(manifest)', evidence.read_text(encoding='utf-8'))
        for path in (SKILLS_DIR / "my-designer" / "SKILL.md", SKILLS_DIR / "design-studio" / "SKILL.md"):
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 130)

    def test_gallery_portal_gate_rejects_actual_runtime_and_binding_mutations(self) -> None:
        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            return module
        chooser = load('verify_chooser', SKILLS_DIR / 'design-studio/scripts/gallery_chooser.py')
        evidence = load('verify_gallery_evidence', SKILLS_DIR / 'my-designer/scripts/design_evidence.py')
        manifests = [{'goal': 'verify', 'category': category, 'round': 2, 'platform': 'web', '_path': category,
                      'variants': [{'id': 'v01', 'title': title, 'summary': summary, 'p0': 'task', 'axis_y': 'rail', 'archetype': 'declared'}]}
                     for category, title, summary in [('A', 'Alpha', '任务轨道清楚展示真实观察与后续决策。'), ('B', 'Beta', '独立内容面板保持实际备注与分类归属。')]]
        html = chooser.render('verify', manifests, '', portal=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); index = root / 'index.html'
            index.write_text(html, encoding='utf-8')
            self.assertEqual(evidence.portal_errors(root), [])
            for old, new, expected in [
                ('data-note="A/v01"', 'data-note="v01"', 'data-note'),
                ('aria-label="A/v01 Notes"', 'aria-label="B/v01 Notes"', 'aria-label'),
                ('data-variant="A/v01"', 'data-variant="v01"', 'data-variant'),
                ("return lines.join('\\n');", "return lines.join('\\n') + '\\nNOTES:\\nflat';", 'runtime'),
                ('root, config.cards)', 'root)', 'runtime'),
                ('const scoped = all.filter(s => s.category === category)', 'const scoped = all', 'runtime'),
                ('const text = selectionSummary(config.goal, currentState(), root, config.cards)',
                 "const text = (false ? selectionSummary(config.goal, currentState(), root, config.cards) : 'legacy flat summary')", 'runtime'),
            ]:
                with self.subTest(expected=expected, mutation=old):
                    mutated = html.replace(old, new, 1); self.assertNotEqual(html, mutated)
                    index.write_text(mutated, encoding='utf-8')
                    self.assertTrue(any(expected in error for error in evidence.portal_errors(root)))

    def test_two_stage_prompt_binding_and_linked_debug_css_gate(self) -> None:
        catalog = SKILLS_DIR / 'design-studio/scripts/design_catalog.py'
        gate = SKILLS_DIR / 'my-designer/scripts/verify_design_cases.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'designer'; source.mkdir()
            intents = [
                'Keep Back beside the itinerary rail so committed work has a clear return.',
                'Separate room destinations from the primary action pill to protect context.',
                'Place the lens toolbar above solid task content with explicit tool ownership.',
                'Make deck destination edges readable without clipped inspector controls.',
            ]
            for i, intent in enumerate(intents, 1):
                (source / f'app{i:02d}-prompt.md').write_bytes(f'# Independent {i}\r\n{intent} Never render .ruler or caustic stripes.\r\n'.encode())
            env = dict(os.environ, HOME=directory)
            result = subprocess.run([sys.executable, str(catalog), 'scaffold-gallery', '--goal', 'prompt-gate', '--round', '1', '--platform', 'web', '--mode', 'app-nav', '--prompts-dir', str(source), '--no-blog-mount'], env=env, capture_output=True, text=True, check=True)
            payload = json.loads(result.stdout); gallery = Path(payload['gallery'])
            for variant in payload['manifest']['variants']:
                self.assertEqual((gallery / variant['prompt_file']).read_bytes(), (source / (variant['id'] + '-prompt.md')).read_bytes())
            command = [sys.executable, str(gate), 'jev-gate', '--platform', 'web', '--stage', 'gallery', '--gallery-dir', str(gallery), '--no-jev']
            accepted = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(accepted.returncode, 0, accepted.stdout)
            html = gallery / 'app01.html'
            html.write_text(html.read_text() + '<link rel="stylesheet" href="debug.css">', encoding='utf-8')
            (gallery / 'debug.css').write_text('.ruler { width:28px; height:28px; border:1px dashed }', encoding='utf-8')
            rejected = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertTrue(any('debug.css' in e and 'forbidden debug ruler' in e for e in json.loads(rejected.stdout)['errors']))
        for name in ('SKILL.md', 'references/interaction-layering.md', 'references/design-flow-architecture.md', 'references/navigation-evidence-contract.md'):
            text = (SKILLS_DIR / 'my-designer' / name).read_text(encoding='utf-8')
            self.assertIn('magpie/group/designer', text); self.assertIn('magpie/group/coding', text)
            self.assertLessEqual(len(text.splitlines()), 120 if name.endswith('design-flow-architecture.md') else 130)

    def test_macos_route_normalizes_to_desktop(self) -> None:
        result = subprocess.run([sys.executable, str(SKILLS_DIR / "my-designer/scripts/verify_design_cases.py"), "route", "--platform", "macos", "--dimensions", "layout-navigation"], capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["platform"], "desktop")
        self.assertIn("allowed_native_sources", payload["external_skill_sources"])

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

    def _aidata_fixture(self, root: Path, *, detailed: bool = False) -> None:
        def write(rel: str, text: str) -> None:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        turn_ddl = "CREATE TABLE turn(turn_uuid TEXT PRIMARY KEY, session_id TEXT, skill_names TEXT, attribution_skill TEXT, input_tokens INTEGER, output_tokens INTEGER, tool_calls TEXT);"
        if detailed:
            turn_ddl += 'CREATE TABLE tool_observation(reference_path TEXT, script_path TEXT, duration_ms INTEGER); CREATE TABLE eval_event(eval_case TEXT, interception INTEGER, "escape" INTEGER);'
        write("adapters/claude_jsonl.py", 'SOURCE = "claude_jsonl"\ndef collect():\n    return [{"sessionId": "session", "skill_names": ["team-lead"], "usage": {"input_tokens": 1, "output_tokens": 1}, "tool_calls": ["Read"]}]\n_CLEAN_DDL = ' + repr(turn_ddl) + '\ndef normalize():\n    return []\n')
        records = [{"sessionId": "session", "skill_names": ["team-lead"], "usage": {"input_tokens": 11, "output_tokens": 7}, "tool_calls": ["Read", "Bash"], "note": "reference_path duration_ms eval_case interception escape"}]
        if detailed:
            records.extend([
                {"sessionId": "session", "reference_path": "references/a.md", "reference_hits": 1},
                {"sessionId": "session", "script_path": "scripts/a.py", "duration_ms": 12},
                {"sessionId": "session", "eval_case": "EV-1", "interception": True, "escape": False},
            ])
        write("L1_collect/raw/claude_jsonl/2026-11-15.jsonl", "".join(json.dumps(record) + "\n" for record in records))
        (root / "L2_normalize/clean").mkdir(parents=True)
        with sqlite3.connect(root / "L2_normalize/clean/claude_jsonl.db") as db:
            db.executescript(turn_ddl)
        warehouse_ddl = "CREATE TABLE fact_turn(turn_uuid TEXT PRIMARY KEY, session_id TEXT, skill_names TEXT, input_tokens INTEGER, output_tokens INTEGER, tool_calls TEXT);"
        sql = "SELECT skill_names AS skill, input_tokens + output_tokens AS tokens FROM fact_turn;"
        snapshot = 'def build_snapshot(report, daily):\n    return {"skills_daily": daily, "coverage_status": report.get("coverage_status"), "source_as_of": report.get("source_as_of")}\n'
        if detailed:
            warehouse_ddl += 'CREATE TABLE reference_hit(reference_path TEXT); CREATE TABLE script_call(script_path TEXT, duration_ms INTEGER); CREATE TABLE fact_gtt_event(eval_case TEXT, interception INTEGER, "escape" INTEGER);'
            sql += '\nSELECT count(*) AS reference_hits, reference_path FROM reference_hit GROUP BY reference_path;\nSELECT avg(duration_ms) AS script_latency, duration_ms, max(duration_ms) AS p95_ms FROM script_call;\nSELECT count(*) AS eval_effect, interception, "escape" FROM fact_gtt_event;'
            snapshot = 'def build_snapshot(report, daily):\n    return {"skills_daily": daily, "coverage_status": report.get("coverage_status"), "source_as_of": report.get("source_as_of"), "reference_hits": {"reference_path": report.get("reference_path")}, "script_latency": {"duration_ms": report.get("duration_ms"), "p95_ms": report.get("p95_ms")}, "eval_effect": {"interception": report.get("interception"), "escape": report.get("escape")}}\n'
        write("schema/warehouse.sql", warehouse_ddl)
        (root / "L3_merge").mkdir()
        with sqlite3.connect(root / "L3_merge/warehouse.db") as db:
            db.executescript(warehouse_ddl)
        write("L4_serve/queries/retro/skill_roi_diagnostics.sql", sql)
        write("L5_apps/digest/skills_roi_snapshot.py", snapshot)

    def _inspect_aidata(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SKILLS_DIR / "aidash-metrics/scripts/inspect_aidata_pipeline.py"), "--aidata-root", str(root), *args, "--json"], check=False, capture_output=True, text=True)

    def _aidata_tree(self, root: Path) -> dict:
        return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None for path in root.rglob("*")}

    def test_aidash_metrics_inspector_classifies_hermetic_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._aidata_fixture(root, detailed=True)
            before = self._aidata_tree(root)
            ready = self._inspect_aidata(root, "--check-contract", "--metrics", "skills_roi,reference_hits,script_latency,eval_effect")
            self.assertEqual(ready.returncode, 0, ready.stderr)
            payload = json.loads(ready.stdout)
            self.assertEqual(payload["status"], "ready")
            for metric in ("skills_roi", "reference_hits", "script_latency", "eval_effect"):
                self.assertEqual(payload["metrics"][metric]["status"], "ready", metric)
                self.assertTrue(all(item["complete"] for item in payload["metrics"][metric]["coverage"].values()), metric)
                self.assertEqual(payload["metrics"][metric]["coverage"]["l1"]["status"], "observed")
                self.assertEqual(payload["metrics"][metric]["coverage"]["l2"]["status"], "observed")
            self.assertIn("L2_normalize/clean/claude_jsonl.db", payload["layers"]["l2"])
            l2 = payload["metrics"]["skills_roi"]["coverage"]["l2"]["evidence"]
            self.assertTrue(any(entry["table"] == "turn" and entry["kind"] == "observed" and entry["complete"] for entry in l2))
            self.assertEqual(before, self._aidata_tree(root))

            (root / "L4_serve/queries/retro/skill_roi_diagnostics.sql").write_text("SELECT 1 AS unrelated;", encoding="utf-8")
            amber = self._inspect_aidata(root)
            self.assertEqual(amber.returncode, 0, amber.stderr)
            for metric, item in json.loads(amber.stdout)["metrics"].items():
                self.assertEqual(item["status"], "needs_l2_l4_extension", metric)
            raw = root / "L1_collect/raw/claude_jsonl/2026-11-15.jsonl"
            raw.write_text("", encoding="utf-8")
            red = self._inspect_aidata(root)
            self.assertEqual(red.returncode, 0, red.stderr)
            for metric, item in json.loads(red.stdout)["metrics"].items():
                self.assertEqual(item["status"], "needs_l1_collection", metric)
            raw.unlink()
            missing = self._inspect_aidata(root, "--metric", "skills_roi")
            self.assertEqual(json.loads(missing.stdout)["status"], "needs_l1_collection")

    def test_aidash_metrics_inspector_scopes_sources_and_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._aidata_fixture(root)
            baseline = self._inspect_aidata(root, "--check-contract")
            self.assertEqual(baseline.returncode, 0, baseline.stderr)
            original = json.loads(baseline.stdout)
            self.assertEqual(original["metrics"]["skills_roi"]["status"], "ready")
            for metric in ("reference_hits", "script_latency", "eval_effect"):
                self.assertEqual(original["metrics"][metric]["status"], "needs_l1_collection")
            for source in ("local_git", "github_repo", "workspace_hygiene", "linear_issue"):
                raw = root / f"L1_collect/raw/{source}/2026-11-15.jsonl"
                raw.parent.mkdir(parents=True)
                raw.write_text(json.dumps({"file_path": "references/a.md", "reference_path": "references/a.md", "script_path": "scripts/a.py", "duration_ms": 1, "eval_case": "EV-1", "interception": True, "escape": True}) + "\n", encoding="utf-8")
            vendor = root / "L1_collect/.venv/lib/decoy.jsonl"
            vendor.parent.mkdir(parents=True)
            vendor.write_text("this is not JSON\n", encoding="utf-8")
            snapshot = root / "L5_apps/digest/skills_roi_snapshot.py"
            snapshot.write_text(snapshot.read_text(encoding="utf-8") + '\n# reference_hits script_latency eval_effect\nnote = "reference_path duration_ms p95_ms interception escape"\n', encoding="utf-8")
            adapter = root / "adapters/claude_jsonl.py"
            adapter.write_text(adapter.read_text(encoding="utf-8") + '\ndef unused_normalizer():\n    return {"reference_path": "references/a.md", "script_path": "scripts/a.py", "duration_ms": 1, "eval_case": "EV-1", "interception": True, "escape": False}\n', encoding="utf-8")
            decoys = self._inspect_aidata(root)
            self.assertEqual(decoys.returncode, 0, decoys.stderr)
            self.assertEqual(original["metrics"], json.loads(decoys.stdout)["metrics"])

            raw = root / "L1_collect/raw/claude_jsonl/2026-11-15.jsonl"
            raw.write_text(json.dumps({"sessionId": "session", "attributionSkill": "team-lead", "usage": {"input_tokens": 11, "output_tokens": 7}}) + "\n", encoding="utf-8")
            alias = self._inspect_aidata(root, "--metric", "skills_roi")
            item = json.loads(alias.stdout)["metrics"]["skills_roi"]
            self.assertEqual(item["status"], "ready")
            self.assertIn("attributionskill", item["coverage"]["l1"]["fields"])
            raw.write_text(json.dumps({"sessionId": "session", "skill_names": ["team-lead"], "usage": {"input_tokens": 11}}) + "\n", encoding="utf-8")
            partial = self._inspect_aidata(root, "--metric", "skills_roi")
            item = json.loads(partial.stdout)["metrics"]["skills_roi"]
            self.assertEqual(item["status"], "needs_l2_l4_extension")
            self.assertEqual(item["coverage"]["l1"]["status"], "declared")
            self.assertFalse(item["raw_retained"])
            raw.write_text(json.dumps({"sessionId": "session", "note": "x" * 2_000_001}) + "\n", encoding="utf-8")
            limited = self._inspect_aidata(root, "--check-contract", "--metric", "skills_roi")
            self.assertEqual(limited.returncode, 0, limited.stderr)
            payload = json.loads(limited.stdout)
            self.assertEqual(payload["status"], "needs_l1_collection")
            self.assertIn(str(raw.relative_to(root)), payload["sampling"]["limit_reached_shards"])

    def test_aidash_metrics_inspector_source_only_ddl_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._aidata_fixture(root)
            (root / "L3_merge/warehouse.db").unlink()
            (root / "L2_normalize/clean/claude_jsonl.db").unlink()
            (root / "L1_collect/raw/claude_jsonl/2026-11-15.jsonl").unlink()
            before = self._aidata_tree(root)
            for args in ((), ("--check-contract",)):
                result = self._inspect_aidata(root, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "needs_l1_collection")
                self.assertEqual(payload["layers"]["l3"], ["schema/warehouse.sql"])
                self.assertIn("adapters/claude_jsonl.py", payload["layers"]["l1"])
                self.assertEqual(payload["metrics"]["skills_roi"]["coverage"]["l2"]["status"], "declared")
                self.assertIn("skill_names", payload["metrics"]["skills_roi"]["coverage"]["l2"]["fields"])
                self.assertEqual(before, self._aidata_tree(root))
            for rel in ("schema/warehouse.sql", "L4_serve/queries/retro/skill_roi_diagnostics.sql", "L5_apps/digest/skills_roi_snapshot.py", "adapters/claude_jsonl.py"):
                path = root / rel
                text = path.read_text(encoding="utf-8")
                path.unlink()
                failed = self._inspect_aidata(root, "--check-contract")
                self.assertEqual(failed.returncode, 3, rel)
                self.assertTrue(failed.stderr)
                path.write_text(text, encoding="utf-8")

    def test_aidash_metrics_inspector_fails_closed(self) -> None:
        inspector = SKILLS_DIR / "aidash-metrics" / "scripts" / "inspect_aidata_pipeline.py"
        for args, expected in (
            (["--aidata-root", "/definitely/missing"], 2),
            (["--aidata-root", ".", "--metric", "unknown"], 2),
            (["--aidata-root", ".", "--metrics", "unknown"], 2),
        ):
            result = subprocess.run([sys.executable, str(inspector), *args], check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected)
            self.assertTrue(result.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._aidata_fixture(root)
            collector = root / "adapters/claude_jsonl.py"
            text = collector.read_text(encoding="utf-8")
            collector.unlink()
            failed = self._inspect_aidata(root, "--check-contract")
            self.assertEqual(failed.returncode, 3, failed.stderr)
            collector.write_text(text, encoding="utf-8")
            for rel, invalid in (
                ("L2_normalize/clean/claude_jsonl.db", b"not SQLite"),
                ("L1_collect/raw/claude_jsonl/2026-11-15.jsonl", b'{"usage":'),
                ("schema/warehouse.sql", b"not SQL"),
                ("schema/warehouse.sql", b"\xff"),
                ("schema/warehouse.sql", b" " * 2_000_001),
                ("L4_serve/queries/retro/skill_roi_diagnostics.sql", b"skill_roi"),
                ("L5_apps/digest/skills_roi_snapshot.py", b"def broken(:"),
                ("adapters/claude_jsonl.py", b"def broken(:"),
            ):
                path = root / rel
                content = path.read_bytes()
                path.write_bytes(invalid)
                before = self._aidata_tree(root)
                failed = self._inspect_aidata(root, "--check-contract")
                self.assertEqual(failed.returncode, 3, rel)
                self.assertTrue(failed.stderr)
                self.assertEqual(before, self._aidata_tree(root))
                path.write_bytes(content)

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
            "Linear Team TIA",
            "preview_markdown",
            "preview_text",
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

    def test_designer_role_routing_and_demo_contract(self) -> None:
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
            self.assertIn("Designer", text)
            self.assertIn("10", text)
            self.assertIn("Prompt", text)
            self.assertNotIn("设计者（Designer）", text)
            self.assertNotIn("艺术家", text)
            self.assertNotIn("writer_stays_and_cleans_own_worktree", text)
            self.assertNotIn("weekly_remaining_pct", text)
        self.assertIn("group/designer", profiles_text)
        self.assertIn("grok-4.7", profiles_text)
        self.assertIn("designer", profiles_text)
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
        self.assertIn("cleanup-demo --goal <goal>", designer_text)
        self.assertIn("leepepe.github.io/demos/<goal>", studio_text)
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


class JevGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('design_gate_tests', SKILLS_DIR / 'my-designer/scripts/verify_design_cases.py')
        cls.gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.gate)
        cls.specs = {
            'mobile': 'Axis X: single P0 per viewport.\nAxis Y: NavigationStack; 48pt targets; bottom CTA.\nAxis Z: solid content Z=0; floating native glass Z=10..20.',
            'desktop': 'Axis X: single P0 per viewport.\nAxis Y: NavigationSplitView Inspector with 28x28pt targets and Cmd keyboard parity.\nAxis Z: functional glass dimming in inactive windows.',
            'web': 'Axis X: single P0 per viewport.\nAxis Y: editorial prose at 60–72ch, natural CJK wrapping at 390px.\nAxis Z: GM-004 SVG feDisplacementMap squircle bezel refraction on floating chrome.'}

    def test_every_platform_clause_polarity_and_native_firewall(self):
        g = self.gate
        prohibitions = {'mobile': 'Never use feDisplacementMap or HTML SVG. No hover-only actions.',
                        'desktop': 'No mobile bottom tabs on desktop. Never use HTML or CSS.',
                        'web': 'No box-in-box cages or decorative heading emojis. Avoid tracked-capital eyebrows.'}
        violations = {'mobile': ['Implement React DOM and CSS HTML.', 'Use SVG feDisplacementMap.', 'No NavigationStack or TabView or sheet; use hover-only actions.'],
                      'desktop': ['Implement the native screen with React DOM and CSS HTML.', 'Use mobile bottom tabs.', '20x20pt targets.', 'No keyboard parity or inactive dimming.'],
                      'web': ['Use box-in-box cages or decorative heading emojis.', '150ch prose.', 'No CJK wrapping.', 'Never use feDisplacementMap.']}
        for platform, spec in self.specs.items():
            self.assertEqual(g._platform_errors(platform, spec + '\n' + prohibitions[platform]), [])
            for directive in violations[platform]:
                with self.subTest(platform=platform, directive=directive):
                    # Remove required baseline when testing an explicit lack of it.
                    text = spec + '\n' + prohibitions[platform] + '\n' + directive
                    if directive.startswith('No Navigation'): text = 'Axis X: single P0. Axis Y: 44x44pt targets. Axis Z: solid content. ' + directive
                    self.assertTrue(g._platform_errors(platform, text), text)
        self.assertEqual(g._platform_errors('mobile', self.specs['mobile'] + '\n## HTML sandbox\nUse React DOM SVG feDisplacementMap.\n## Native implementation\nNever use HTML.'), [])
        for separator in ('; ', ', ', '. ', ' but ', ' and '):
            self.assertTrue(g._platform_errors('mobile', self.specs['mobile'] + '\nNever use feDisplacementMap' + separator + 'implement React DOM and CSS HTML.'))

    def test_native_contrast_clauses_and_temporal_while_keep_independent_polarity(self):
        for platform in ('mobile', 'desktop'):
            spec = self.specs[platform]
            for separator in (', whereas ', ' whereas ', ', while ', ' while '):
                for directive in ('React DOM renders the native screen.', 'CSS HTML is used in native views.',
                                  'React DOM powers the native screen.', 'CSS HTML supplies native views.',
                                  'the native screen relies on React DOM.', 'native screens depend on CSS HTML.'):
                    with self.subTest(platform=platform, separator=separator, directive=directive):
                        errors = self.gate._platform_errors(platform, spec + '\nNever use SVG in native views' + separator + directive)
                        self.assertTrue(any('leaks Web primitives' in error for error in errors))
                self.assertEqual(self.gate._platform_errors(platform, spec + '\nNever use SVG' + separator + 'never use React DOM or CSS HTML.'), [])
            for temporal in ('Never use SVG while rendering HTML sandbox examples.',
                             'Never use CSS HTML while migrating native views.',
                             'Avoid SVG while inspecting CSS HTML migration examples.'):
                for spacing in (' ', '  ', '\t', ' \t '):
                    with self.subTest(platform=platform, temporal=temporal, spacing=spacing):
                        text = temporal.replace('while ', 'while' + spacing)
                        self.assertEqual(self.gate._platform_errors(platform, spec + '\n' + text), [])

    def test_total_deadline_stops_trickled_headers_and_body_io(self):
        import http.server
        import threading
        import time
        from unittest import mock
        disconnected = threading.Event()
        writes = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                encoded = json.dumps({q: {'noul': 1} for q in body['questions']}).encode()
                try:
                    self.wfile.write(b'HTTP/1.0 200 OK\r\n')
                    if body['state']['phase'] == 'headers':
                        for i in range(12):
                            self.wfile.write(f'X-Delay-{i}: ready\r\n'.encode()); self.wfile.flush()
                            writes.append(i); time.sleep(.035)
                    self.wfile.write(f'Content-Length: {len(encoded)}\r\n\r\n'.encode()); self.wfile.flush()
                    if body['state']['phase'] == 'body':
                        for byte in encoded:
                            self.wfile.write(bytes([byte])); self.wfile.flush()
                            writes.append(byte); time.sleep(.035)
                    else: self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError): disconnected.set()
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{server.server_port}/v1/systemone'
        try:
            with mock.patch.object(self.gate.JEV, '_auth', return_value=(None, None)):
                for phase in ('headers', 'body'):
                    disconnected.clear(); writes.clear()
                    start = time.monotonic()
                    with self.assertRaises(OSError):
                        self.gate.JEV.live_response(url, .1, {'evidence': [{'spec_text': self.specs['mobile']}], 'phase': phase},
                                                    self.gate.TAXONOMY['jev_verification_gates']['mobile'])
                    self.assertLess(time.monotonic() - start, .25, phase)
                    self.assertTrue(disconnected.wait(.3), 'deadline must close actual network I/O')
                    self.assertLess(len(writes), 12, phase)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_required_answers_truth_table_and_final_structural_rejection(self):
        import types
        g = self.gate
        for platform in self.specs:
            questions = g.TAXONOMY['jev_verification_gates'][platform]
            positive = {q: {'status': 'PASS', 'noul': 1} for q in questions}
            self.assertEqual(g.JEV.validate_response(positive, questions, 'mock')['status'], 'PASS')
            for invalid in ({}, {'noul': True}, {'noul': 'bad'}, {'noul': float('nan')}, {'noul': 2}, {'noul': 0},
                            {'noul': 0, 'status': 'PASS'}, {'noul': 1, 'status': 'REJECT_REVISE'},
                            {'score': 1, 'noul': 0}, {'score': 1, 'decision': 'REJECT_REVISE'}):
                for q in questions:
                    data = dict(positive, score=1); data[q] = invalid
                    with self.subTest(platform=platform, question=q, invalid=invalid):
                        self.assertEqual(g.JEV.validate_response(data, questions, 'mock')['status'], 'REJECT_REVISE')
            for data in ({'score': 1}, dict(positive, score=True), dict(positive, status='REJECT_REVISE'), {'answers': []}):
                self.assertEqual(g.JEV.validate_response(data, questions, 'mock')['status'], 'REJECT_REVISE')
        args = types.SimpleNamespace(no_jev=True, mock_jev_json=None)
        self.assertFalse(g._jev_response(args, [], 'mobile')['certified_by_jev'])
        args.mock_jev_json = '{"score":1}'
        self.assertEqual(g._jev_response(args, [], 'mobile')['status'], 'REJECT_REVISE')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.md'; path.write_text(self.specs['mobile'] + '\nImplement React DOM HTML.')
            answers = {q: {'noul': 1} for q in g.TAXONOMY['jev_verification_gates']['mobile']}
            result = subprocess.run([sys.executable, str(SKILLS_DIR / 'my-designer/scripts/verify_design_cases.py'), 'jev-gate', '--platform', 'mobile', '--stage', 'spec', '--spec-file', str(path), '--mock-jev-json', json.dumps(answers)], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)['status'], 'REJECT_REVISE')

    def test_live_loopback_systemone_request_missing_timeout_and_rejection(self):
        import http.server
        import threading
        import time
        from unittest import mock
        g = self.gate
        requests = []
        questions = g.TAXONOMY['jev_verification_gates']['mobile']
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body))
                if body['state'].get('slow'): time.sleep(.08)
                answers = {q: {'noul': 1, 'status': 'PASS'} for q in body['questions']}
                if body['state'].get('missing'): answers.pop(next(iter(answers)))
                encoded = json.dumps(answers).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(encoded))); self.end_headers()
                try: self.wfile.write(encoded)
                except BrokenPipeError: pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{server.server_port}/v1/systemone'
        try:
            with mock.patch.object(g.JEV, '_auth', return_value=(None, None)):
                state = {'platform': 'mobile', 'stage': 'spec', 'evidence': [{'spec_text': self.specs['mobile']}]}
                response = g.JEV.live_response(url, .5, state, questions)
                self.assertEqual(g.JEV.validate_response(response, questions, 'live')['status'], 'PASS')
                self.assertEqual(requests[0][0], '/v1/systemone')
                self.assertEqual(set(requests[0][1]['questions']), set(questions))
                self.assertEqual(requests[0][1]['state'], state)
                self.assertTrue(all(q['type'] == 'noul' and q['criteria'] for q in requests[0][1]['questions'].values()))
                response = g.JEV.live_response(url.replace('127.0.0.1', 'LOCALHOST'), .5, state, questions)
                self.assertEqual(g.JEV.validate_response(response, questions, 'live')['status'], 'PASS')
                response = g.JEV.live_response(url, .5, dict(state, missing=True), questions)
                self.assertEqual(g.JEV.validate_response(response, questions, 'live')['status'], 'REJECT_REVISE')
                with self.assertRaises(OSError): g.JEV.live_response(url, .01, dict(state, slow=True), questions)
                with self.assertRaises(ValueError): g.JEV.live_response('https://example.org/v1/systemone', .5, state, questions)
                with self.assertRaises(ValueError): g.JEV.live_response(url, .5, {'evidence': []}, questions)
        finally:
            server.shutdown(); server.server_close(); thread.join()
        with self.assertRaises(OSError): g.JEV.live_response(url, .05, state, questions)

    def test_traceback_evidence_relationship_and_routing_not_vocabulary(self):
        import copy
        g = self.gate
        image_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        image_file.write(_png_fixture())
        image_file.close()
        image_source = image_file.name + '#button'
        violation = {'classification': 'IMPL_BUG', 'constraint': {'declared': True, 'source': 'spec.md:12', 'requirement': '48pt hit targets'},
                     'implementation': {'source': image_source, 'observation': 'Measured 36pt', 'conforms': False, 'assertion': {'operator': 'minimum', 'required': 48, 'observed': 36}},
                     'route': {'owner': 'Writer', 'amend_spec_first': False}}
        gap = {'classification': 'SPEC_GAP', 'constraint': {'declared': False, 'source': 'spec.md#copy', 'requirement': 'Follows every declared constraint', 'missing_requirement': 'CJK wrapping at 390px'},
               'implementation': {'source': image_file.name + '#copy', 'observation': 'Declared layout followed; CJK copy overflows', 'conforms': True},
               'route': {'owner': 'Steering', 'amend_spec_first': True}}
        rendered = {'rendered_evidence': [{'kind': 'screenshot', 'source': image_source, 'observation': 'Rendered viewport inspected'}]}
        def check(f): return g.EVIDENCE.traceback_errors(json.dumps({'schema': 'visual-traceback/v1', 'findings': [f], **rendered}))[0]
        self.assertEqual(check(violation), [])
        self.assertEqual(check(gap), [])
        wrong = copy.deepcopy(violation); wrong['classification'] = 'SPEC_GAP'
        self.assertTrue(check(wrong))
        wrong = copy.deepcopy(gap); wrong['route']['owner'] = 'Writer'
        self.assertTrue(check(wrong))
        wrong = copy.deepcopy(violation); wrong['implementation']['conforms'] = True
        self.assertTrue(check(wrong))
        clean = {'schema': 'visual-traceback/v1', 'findings': [], **rendered, 'clean': {'spec_source': 'spec.md:12', 'implementation_source': image_source, 'checks': [{'requirement': '48pt targets', 'assertion': {'operator': 'minimum', 'required': 48, 'observed': 48}}]}}
        self.assertEqual(g.EVIDENCE.traceback_errors(json.dumps(clean))[0], [])
        self.assertTrue(g.EVIDENCE.traceback_errors('Allowed words [IMPL_BUG] and [SPEC_GAP]')[0])
        import types
        answers = {q: {'noul': 1} for q in g.TAXONOMY['jev_verification_gates']['mobile']}
        args = types.SimpleNamespace(no_jev=False, mock_jev_json=json.dumps(answers), stage='traceback')
        self.assertEqual(g._jev_response(args, [gap], 'mobile')['status'], 'REJECT_REVISE')
        answers['q_traceback_evidence_attribution'] = {'noul': 1}
        args.mock_jev_json = json.dumps(answers)
        self.assertEqual(g._jev_response(args, [gap], 'mobile')['status'], 'PASS')
        answers['q_traceback_evidence_attribution'] = {'noul': 0, 'status': 'PASS'}
        args.mock_jev_json = json.dumps(answers)
        self.assertEqual(g._jev_response(args, [gap], 'mobile')['status'], 'REJECT_REVISE')
        Path(image_file.name).unlink(missing_ok=True)


    def test_swift_audit_masks_comments_strings_and_reports_native_defects(self):
        gate = SKILLS_DIR / 'my-designer/scripts/verify_design_cases.py'
        bad = '/* .glassEffect(.prominent) */\nText(".glassEffect(.prominent)").padding().glassEffect(.prominent)'
        result = subprocess.run([sys.executable, str(gate), 'audit-code', '--platform', 'mobile', '--path', 'snippet.swift', '--snippet', bad], cwd=REPO_ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('DC-011', result.stdout)
        self.assertIn('line', result.stdout)
        good = 'Text("x").padding().frame(width: 44).glassEffect(.regular)'
        result = subprocess.run([sys.executable, str(gate), 'audit-code', '--platform', 'mobile', '--path', 'snippet.swift', '--snippet', good], cwd=REPO_ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_traceback_rejects_unvalidated_or_missing_rendered_evidence(self):
        g = self.gate
        payload = {'schema': 'visual-traceback/v1', 'findings': [], 'status': 'UNVALIDATED'}
        self.assertTrue(g.EVIDENCE.traceback_errors(json.dumps(payload))[0])
        payload['status'] = 'PASS'
        self.assertTrue(g.EVIDENCE.traceback_errors(json.dumps(payload))[0])

    def test_traceback_requires_real_image_for_direct_and_jev_paths(self):
        g = self.gate
        png = _png_fixture()
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            root = Path(tmp); image = root / 'screen.png'; image.write_bytes(png); source = str(image) + '#button'
            base = {'schema': 'visual-traceback/v1', 'findings': [], 'rendered_evidence': [{'kind': 'screenshot', 'source': source, 'observation': 'real rendered viewport inspected'}], 'clean': {'spec_source': 'spec.md:1', 'implementation_source': source, 'checks': [{'requirement': 'target', 'assertion': {'operator': 'minimum', 'required': 1, 'observed': 1}}]}}
            def run(payload, extra=()):
                path = root / 'trace.json'; path.write_text(json.dumps(payload), encoding='utf-8')
                return subprocess.run([sys.executable, str(SKILLS_DIR / 'my-designer/scripts/verify_design_cases.py'), 'jev-gate', '--platform', 'mobile', '--stage', 'traceback', '--spec-file', str(path), *extra], cwd=REPO_ROOT, text=True, capture_output=True)
            self.assertEqual(g.EVIDENCE.traceback_errors(json.dumps(base), base_dir=root)[0], [])
            self.assertEqual(run(base, ('--no-jev',)).returncode, 0)
            answers = {q: {'noul': 1} for q in g.TAXONOMY['jev_verification_gates']['mobile']}; answers['q_traceback_evidence_attribution'] = {'noul': 1}
            self.assertEqual(run(base, ('--mock-jev-json', json.dumps(answers))).returncode, 0)
            undersized = root / 'small.png'; undersized.write_bytes(_png_fixture(1, 1))
            truncated = root / 'truncated.png'; truncated.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 24)
            jpeg = root / 'truncated.jpg'; jpeg.write_bytes(b'\xff\xd8\xff')
            for bad in (
                {**base, 'rendered_evidence': [{'kind': 'screenshot', 'source': str(root / 'phantom.png') + '#button', 'observation': 'real'}]},
                {**base, 'rendered_evidence': [{'kind': 'observation', 'source': 'View.swift:42', 'observation': 'code only'}]},
                {**base, 'clean': {**base['clean'], 'status': 'UNVALIDATED'}},
                {**base, 'rendered_evidence': [{'kind': 'screenshot', 'source': str(root / 'not-image.txt'), 'observation': 'real'}]},
                {**base, 'rendered_evidence': [{'kind': 'screenshot', 'source': str(undersized) + '#button', 'observation': 'real'}]},
                {**base, 'rendered_evidence': [{'kind': 'screenshot', 'source': str(truncated) + '#button', 'observation': 'real'}]},
                {**base, 'rendered_evidence': [{'kind': 'screenshot', 'source': str(jpeg) + '#button', 'observation': 'real'}]},
            ):
                self.assertTrue(g.EVIDENCE.traceback_errors(json.dumps(bad), base_dir=root)[0])
                self.assertNotEqual(run(bad, ('--no-jev',)).returncode, 0)


class SkillContractV2Tests(unittest.TestCase):
    def _script(self) -> Path:
        return SKILLS_DIR / "skill-contract" / "scripts" / "skill_contract.py"

    def _module(self):
        spec = importlib.util.spec_from_file_location("skill_contract_repair", self._script())
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def _cli(self, *args):
        result = subprocess.run(
            [sys.executable, "-B", str(self._script()), *map(str, args)],
            cwd=REPO_ROOT, capture_output=True, text=True,
            env={**os.environ, "GTT_JEV_MOCK_JSON": "null"},
        )
        self.assertNotIn("Traceback", result.stderr, result.stderr)
        return result, json.loads(result.stdout)

    def _package(self, root, name="demo", prefix="skills"):
        package = root / prefix / name
        (package / "references").mkdir(parents=True)
        (package / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Audit progressive disclosure skill contracts.\n---\n"
            "See [eval-cases.json](references/eval-cases.json).\n", encoding="utf-8",
        )
        return package

    def _drawer(self, package, entries=None, triggers=None):
        drawer = package / "references" / "eval-cases.json"
        drawer.write_text(json.dumps({"active_cases": entries or [], "trigger_evals": triggers or []}), encoding="utf-8")
        return drawer

    def _star_db(self, path, source="demo"):
        with sqlite3.connect(path) as con:
            con.executescript(
                "CREATE TABLE sources (id INTEGER PRIMARY KEY, name TEXT);"
                "CREATE TABLE requests (id INTEGER PRIMARY KEY, source_id INTEGER, summary_json TEXT, received_request TEXT, upstream_response TEXT, review_state TEXT);"
                "CREATE TABLE reviews (request_id INTEGER, state TEXT);"
            )
            con.executemany("INSERT INTO sources VALUES (?, ?)", [(1, source), (2, "unrelated")])
        return path

    def _star_row(self, db, sid, marker="summary", source_id=1):
        with sqlite3.connect(db) as con:
            con.execute("INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?)", (
                sid, source_id, json.dumps({"starredAt": "2026-11-14"} if marker == "summary" else {}),
                json.dumps({"state": {"input": "synthetic"}, "questions": {"q": {"type": "noul"}}}),
                json.dumps({"answers": {"q": {"noul": 0.9}}}),
                "starred" if marker == "request" else None,
            ))
            if marker == "review":
                con.executemany("INSERT INTO reviews VALUES (?, ?)", [(sid, "starred"), (sid, "starred")])

    def test_mock_truth_table_and_companion_boundaries(self) -> None:
        module = self._module()
        case = {"query": "audit progressive disclosure skill contracts", "should_trigger": True}
        self.assertEqual(module._mock_trigger({"1": True, "3": True}, 2, case), (False, "incomplete mock truth table"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self._package(root)
            self._drawer(package, triggers=[case, {"id": "miss", "query": "Xcode simulator build", "should_trigger": False}])
            run = ("description-eval", "--repo", root, "--skill", "demo", "--strict")
            for value, expected, source in (
                ({"choice": "yes", "noul": 0.1}, False, "mock"),
                ({"choice": "no", "noul": 0.9}, False, "mock"),
                ({"choice": {"choice": "no", "confidence": 0.098}, "noul": {"noul": 0.535}}, True, "deterministic-fallback"),
                ({"choice": "no", "confidence": 0.098, "noul": 0.535}, True, "deterministic-fallback"),
                ({"choice": "no", "noul": 0.535}, True, "deterministic-fallback"),
                ({"choice": {"choice": "no", "confidence": 0.8}, "noul": 0.535}, False, "mock"),
                ({"answers": {"choice": {"choice": "yes", "confidence": 0.8}, "noul": {"noul": 0.9}}}, True, "mock"),
                *[({"choice": "yes", "noul": score}, True, "deterministic-fallback") for score in (0.3, 0.5, 0.7)],
            ):
                with self.subTest(value=value):
                    result, payload = self._cli(*run, "--mock-jev-json", json.dumps({"1": value, "miss": False}))
                    self.assertEqual(payload["decisions"][0]["matched"], expected)
                    self.assertEqual(payload["decisions"][0]["source"], source)
                    self.assertEqual(result.returncode, 0 if expected else 1)
            for value in (None, {"choice": "yes"}, {"choice": "maybe", "noul": 0.9}, {"choice": "yes", "noul": None},
                          {"noul": float("nan")}, {"noul": float("inf")}, {"noul": -0.1}, {"noul": 1.1}, {"confidence": True},
                          {"choice": "yes", "noul": {"noul": 0.9, "confidence": float("nan")}}):
                with self.subTest(malformed=value):
                    result, payload = self._cli(*run, "--mock-jev-json", json.dumps({"1": value, "miss": False}))
                    self.assertEqual(result.returncode, 1)
                    self.assertTrue(any("error" in failure for failure in payload["failures"]))
            for score in (0.3, 0.5, 0.7):
                for value in ({"noul": score}, {"choice": {"choice": "no", "confidence": 0.098}, "noul": score}):
                    with self.subTest(grey=value):
                        result, payload = self._cli(*run, "--mock-jev-json", json.dumps({"1": value, "miss": value}))
                        self.assertEqual(result.returncode, 0, payload)
                        self.assertEqual([d["matched"] for d in payload["decisions"]], [True, False])
                        self.assertTrue(all(d["source"] == "deterministic-fallback" for d in payload["decisions"]))
            for mock in ({"1": True}, {"0": True, "1": False}, {"unknown": True}, None):
                result, payload = self._cli(*run, "--mock-jev-json", json.dumps(mock))
                self.assertEqual(result.returncode, 1, payload)
            for mock in ([True, False], {case["query"]: True, "miss": False}):
                mock_file = root / "mock.json"
                mock_file.write_text(json.dumps(mock), encoding="utf-8")
                result, payload = self._cli(*run, "--mock-jev-json", mock_file)
                self.assertEqual(result.returncode, 0, payload)
                self.assertEqual(payload["passed"], 2)
            result, payload = self._cli("description-eval", "--skill", "howto", "--strict")
            self.assertEqual(result.returncode, 0, payload)
            trigger_evals = json.loads((SKILLS_DIR / "howto" / "references" / "eval-cases.json").read_text())["trigger_evals"]
            self.assertGreaterEqual(len(trigger_evals), 5)
            self.assertEqual(payload["total"], len(trigger_evals))
            self.assertEqual(payload["passed"], len(trigger_evals))
            self.assertEqual(payload["failures"], [])

    def test_canonical_live_context_and_fallback(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self._package(root)
            query = "audit progressive disclosure skill contracts"
            self._drawer(package, triggers=[{"query": query, "should_trigger": True}])
            args = module.parser().parse_args(["description-eval", "--repo", str(root), "--skill", "demo", "--strict"])
            client = self._module()
            for response, expected_rc, expected_source in (
                ({"choice": {"choice": "yes"}, "noul": {"noul": 0.9}}, 0, "jev"),
                ({"answers": {"choice": {"choice": "yes"}, "noul": {"noul": 0.1}}}, 1, "jev"),
                ({"choice": "no", "noul": 0.9}, 1, "jev"),
                ({"answers": {"choice": {"choice": "no", "confidence": 0.098, "probabilities": {"no": 0.549, "yes": 0.451}}, "noul": {"noul": 0.535}}}, 0, "deterministic-fallback"),
                ({"choice": {"choice": "no", "confidence": 0.8}, "noul": 0.535}, 1, "jev"),
                ({"choice": "yes", "noul": 0.7}, 0, "deterministic-fallback"),
                ({"choice": "yes"}, 0, "deterministic-fallback"),
                (None, 0, "deterministic-fallback"),
            ):
                with self.subTest(response=response), patch("importlib.util.module_from_spec", return_value=client), \
                     patch("importlib.util.spec_from_file_location") as spec_factory, \
                     patch.object(client, "jev_decide", create=True, return_value=response) as decide, \
                     patch("builtins.print") as printed:
                    spec_factory.return_value.loader.exec_module.return_value = None
                    self.assertEqual(module.description_eval(args), expected_rc)
                    self.assertEqual(spec_factory.call_args.args[1], SKILLS_DIR / "team-lead" / "scripts" / "jev_client.py")
                    state, questions = decide.call_args.args
                    self.assertEqual(state, {"skill": "demo", "description": module._frontmatter_description(package / "SKILL.md"), "query": query})
                    self.assertEqual(questions["choice"]["type"], "choice")
                    self.assertEqual(set(questions["choice"]["criteria"]), {"yes", "no"})
                    self.assertTrue(questions["choice"]["instructions"])
                    self.assertEqual(questions["noul"]["type"], "noul")
                    self.assertTrue(questions["noul"]["instructions"])
                    self.assertEqual(decide.call_args.kwargs["timeout_sec"], 1.5)
                    self.assertEqual(json.loads(printed.call_args.args[0])["decisions"][0]["source"], expected_source)
            with patch.object(module, "_jev_trigger", side_effect=AssertionError("mock must not contact live client")), patch("builtins.print"):
                args.mock_jev_json = '{"1": true}'
                self.assertEqual(module.description_eval(args), 0)

    def test_negative_only_registration_rejects_before_write(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self._package(root)
            drawer = self._drawer(package)
            run = ("add-case", "--repo", root, "--skill", "demo", "--title", "neg")
            for exit_code, exact in ((3, 3), (7, None), (127, None)):
                cmd = f"{sys.executable} -c 'import sys; print(\"genuine\", file=sys.stderr); raise SystemExit({exit_code})'"
                result, payload = self._cli(*run, "--negative-cmd", cmd, *(("--expect-fail-exit", exact) if exact is not None else ()))
                self.assertEqual(result.returncode, 0, payload)
            before = (drawer.read_bytes(), drawer.stat().st_mtime_ns)
            for command, exact in (("python3 -c 'raise SystemExit(0)'", None), ("python3 -c 'raise SystemExit(0)'", 0),
                                   ("python3 -c 'raise SystemExit(3)'", 4), ("definitely-missing-binary-jev", None), ("   ", None), ("'", None)):
                with self.subTest(command=command, exact=exact):
                    result, payload = self._cli(*run, "--negative-cmd", command, *(("--expect-fail-exit", exact) if exact is not None else ()))
                    self.assertEqual(result.returncode, 1, payload)
                    self.assertEqual((drawer.read_bytes(), drawer.stat().st_mtime_ns), before)
            for empty in ("", [], "   "):
                self.assertTrue(module.evaluate_case({"id": "empty", "negative_cmd": empty}, package, root))
            result, payload = self._cli("eval", "--repo", root, "--strict")
            self.assertEqual(result.returncode, 0, payload)
            self.assertEqual(payload["cases_run"], 3)

    def test_star_agents_path_refresh_and_snapshot_validation(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self._package(root, "agent-skill", ".agents/skills")
            drawer = self._drawer(package)
            db = self._star_db(root / "fixture.sqlite", "agent-skill")
            self._star_row(db, 10)
            run = ("sync-jev-stars", "--repo", root, "--skill", "agent-skill", "--db-path", db)
            result, payload = self._cli(*run)
            self.assertEqual(result.returncode, 0, payload)
            doc = json.loads(drawer.read_text())
            golden = doc["active_cases"][0]
            self.assertEqual(golden["file"], ".agents/skills/agent-skill/SKILL.md")
            custom = package / "assertion.txt"
            custom.write_text("strong regression", encoding="utf-8")
            golden.update({"title": "custom title", "file": ".agents/skills/agent-skill/assertion.txt", "must_contain": ["strong regression"],
                           "must_not_contain": ["forbidden"], "cmd": [sys.executable, "-c", "print('preserved')"], "expect_exit": 0,
                           "expect_stdout_contains": ["preserved"], "registered_at": "2020-01-01", "metadata": {"owner": "fixture"}})
            ordinary = {"id": "EV-ordinary", "tags": ["ordinary"], "file": "SKILL.md"}
            doc["active_cases"] = [golden, ordinary]
            drawer.write_text(json.dumps(doc), encoding="utf-8")
            result, payload = self._cli(*run)
            self.assertEqual(result.returncode, 0, payload)
            self.assertEqual(json.loads(drawer.read_text())["active_cases"], [golden, ordinary])
            for changes in ({"jev_questions": {}}, {"jev_questions": []}, {"mock_jev_response": {}}, {"mock_jev_response": []},
                            {"mock_jev_response": {"wrong": 1}}, {"jev_questions": [{"id": "absent", "type": "noul"}]},
                            {"jev_state": float("nan")}, {"jev_state": object()}):
                with self.subTest(changes=changes):
                    self.assertTrue(module.evaluate_case({**golden, **changes}, package, root))
            malformed = {**golden, "jev_questions": {}}
            drawer.write_text(json.dumps({"active_cases": [malformed]}), encoding="utf-8")
            result, payload = self._cli("eval", "--repo", root, "--strict")
            self.assertEqual(result.returncode, 1, payload)
            self.assertIn("jev_questions", str(payload["failures"]))

    def test_star_external_relative_package_and_cmd_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            external = self._package(base / "external", "demo")
            (root / "skills").mkdir(parents=True)
            (root / "skills" / "demo").symlink_to(os.path.relpath(external, root / "skills"))
            drawer = self._drawer(external)
            db = self._star_db(base / "fixture.sqlite")
            self._star_row(db, 10)
            run = ("sync-jev-stars", "--repo", root, "--skill", "demo", "--db-path", db)
            result, payload = self._cli(*run)
            self.assertEqual(result.returncode, 0, payload)
            self.assertEqual(json.loads(drawer.read_text())["active_cases"][0]["file"], "skills/demo/SKILL.md")
            with sqlite3.connect(db) as con:
                con.execute("UPDATE requests SET upstream_response=?", (json.dumps({"answers": {"q": {"noul": 0.9, "note": "quotes ' and ; $(not-a-command)"}}}),))
            template = 'python3 -c "import json,sys; assert json.loads(sys.argv[1])[\'q\'][\'noul\'] == 0.9" {mock_json}'
            for candidate in (template, template.replace("{mock_json}", '"{mock_json}"')):
                result, payload = self._cli(*run, "--cmd-template", candidate)
                self.assertEqual(result.returncode, 0, payload)
                entry = json.loads(drawer.read_text())["active_cases"][0]
                self.assertEqual(len(entry["cmd"]), 4)
                self.assertEqual(json.loads(entry["cmd"][-1])["q"]["noul"], 0.9)
            before = (drawer.read_bytes(), drawer.stat().st_mtime_ns)
            result, payload = self._cli(*run, "--cmd-template", "python3 -c 'raise SystemExit(5)' {mock_json}")
            self.assertEqual(result.returncode, 1, payload)
            self.assertEqual((drawer.read_bytes(), drawer.stat().st_mtime_ns), before)

    def test_star_markers_dedupe_fallback_eviction_and_saturation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = self._package(root, "howto")
            ordinary = [{"id": f"EV-{i:03d}", "tags": ["ordinary"], "registered_at": f"2026-01-{16-i:02d}", "file": "SKILL.md"} for i in range(1, 16)]
            drawer = self._drawer(package, ordinary)
            db = self._star_db(root / "fixture.sqlite", "goal-task")
            for sid, marker in ((10, "summary"), (11, "review"), (12, "request")):
                self._star_row(db, sid, marker)
            self._star_row(db, 99, source_id=2)
            run = ("sync-jev-stars", "--repo", root, "--skill", "howto", "--db-path", db)
            before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in (drawer, db)]
            result, dry = self._cli(*run, "--dry-run")
            self.assertEqual(result.returncode, 0, dry)
            self.assertEqual([(p.read_bytes(), p.stat().st_mtime_ns) for p in (drawer, db)], before)
            result, live = self._cli(*run)
            self.assertEqual(result.returncode, 0, live)
            self.assertEqual({k: v for k, v in dry.items() if k != "dry_run"}, {k: v for k, v in live.items() if k != "dry_run"})
            self.assertEqual(live["added"], 3)
            self.assertEqual(live["evicted"], ["EV-015", "EV-014", "EV-013"])
            doc = json.loads(drawer.read_text())
            self.assertEqual([c["jev_starred_id"] for c in doc["active_cases"] if "jev_starred_id" in c], [10, 11, 12])
            self.assertEqual(doc["archived_cases"], list(reversed(ordinary[-3:])))
            self.assertEqual((db.read_bytes(), db.stat().st_mtime_ns), before[1])
            result, payload = self._cli(*run)
            self.assertEqual(payload["added"], 0)
            self.assertEqual(json.loads(drawer.read_text())["active_cases"], doc["active_cases"])
            result, payload = self._cli(*run, "--source-name", "unrelated", "--dry-run")
            self.assertEqual(payload["added"], 1)
            golden = [{"id": f"EV-{i:03d}", "tags": ["jev-star"], "file": "SKILL.md", "metadata": i} for i in range(1, 16)]
            drawer.write_text(json.dumps({"active_cases": golden}), encoding="utf-8")
            result, payload = self._cli(*run)
            self.assertEqual(result.returncode, 0, payload)
            self.assertEqual(payload["skipped"], ["10", "11", "12"])
            self.assertEqual(payload["evicted"], [])
            self.assertEqual(payload["added"], 0)
            self.assertEqual(json.loads(drawer.read_text())["active_cases"], golden)

    def test_single_skill_sibling_links_and_per_repo_script_forks(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = self._package(root, "canonical")
            consumer = self._package(root, "consumer")
            (canonical / "references" / "shared.md").write_text("shared contract", encoding="utf-8")
            (consumer / "SKILL.md").write_text((consumer / "SKILL.md").read_text() + "[shared](../canonical/references/shared.md)\n", encoding="utf-8")
            result, payload = self._cli("audit", "--repo", root, "--skill", "canonical", "--strict")
            self.assertEqual(result.returncode, 0, payload)
            (consumer / "SKILL.md").write_text("---\ndescription: consumer\n---\nUnrelated bare shared stem.\n", encoding="utf-8")
            result, payload = self._cli("audit", "--repo", root, "--skill", "canonical", "--strict")
            self.assertIn("orphan-reference", str(payload["findings"]))
            (consumer / "SKILL.md").write_text("---\ndescription: consumer\n---\n[shared](../canonical/references/shared.md)\n", encoding="utf-8")
            wrapper = self._package(root, "wrapper")
            for package in (canonical, consumer, wrapper):
                (package / "scripts").mkdir()
                (package / "scripts" / "same.py").write_text("_CANONICAL_PATH = '../canonical/scripts/same.py'\n" if package == wrapper else "print('real')\n", encoding="utf-8")
            result, payload = self._cli("audit", "--repo", root, "--strict")
            self.assertEqual(sum(f["code"] == "duplicate-script-fork" for f in payload["findings"]), 1)
            (consumer / "scripts" / "same.py").unlink()
            result, payload = self._cli("audit", "--repo", root, "--strict")
            self.assertEqual(result.returncode, 0, payload)
            other_root = root / "independent-repo"
            other = self._package(other_root, "independent")
            (other / "scripts").mkdir()
            (other / "scripts" / "same.py").write_text("print('different repository')\n", encoding="utf-8")
            args = module.parser().parse_args(["audit", "--all", "--strict"])
            with patch.object(module, "_discover_packages", return_value=[canonical, consumer, wrapper, other]), patch("builtins.print") as printed:
                self.assertEqual(module.audit(args), 0)
                self.assertNotIn("duplicate-script-fork", printed.call_args.args[0])

    def test_audit_v2_description_symlink_and_duplicate_script_checks(self) -> None:
        script = self._script()
        spec = importlib.util.spec_from_file_location("skill_contract_audit_v2", script)
        module = importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); first = root / "skills" / "one"; second = root / "skills" / "two"
            for package in (first, second):
                (package / "references").mkdir(parents=True); (package / "scripts").mkdir()
                (package / "SKILL.md").write_text("---\nname: x\ndescription: " + ("x" * (321 if package is first else 1)) + "\n---\n", encoding="utf-8")
                (package / "scripts" / "same.py").write_text("print(1)\n", encoding="utf-8")
            (first / "references" / "missing.md").symlink_to(root / "absent.md")
            findings = module.audit_package(first)
            codes = {item["code"] for item in findings}
            self.assertIn("description-length", codes); self.assertIn("broken-symlink", codes)
            audit = subprocess.run([sys.executable, str(script), "audit", "--repo", str(root), "--strict"], capture_output=True, text=True)
            self.assertIn("duplicate-script-fork", audit.stdout + audit.stderr)

    def test_description_eval_and_negative_cmd(self) -> None:
        script = self._script()
        result = subprocess.run([sys.executable, str(script), "description-eval", "--skill", "skill-contract", "--mock-jev-json", '{"1":true,"2":false,"3":true}', "--strict"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(result.stdout)["ok"])
        stored_cases = json.loads((SKILLS_DIR / "skill-contract" / "references" / "eval-cases.json").read_text())["active_cases"]
        routing_fixture = next(case for case in stored_cases if case["id"] == "EV-007")
        with patch.dict(os.environ, {"GTT_JEV_MOCK_JSON": '{"choice":"no","noul":0.9}', "GTT_JEV_LIVE": "1"}):
            self.assertEqual(self._module().evaluate_case(routing_fixture, SKILLS_DIR / "skill-contract", REPO_ROOT), [])
        case = {"id": "EV-test", "negative_cmd": f"{sys.executable} -c 'raise SystemExit(7)'", "expect_fail_exit": 7}
        spec = importlib.util.spec_from_file_location("skill_contract_v2", script)
        module = importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(module)
        self.assertEqual(module.evaluate_case(case, SKILLS_DIR / "skill-contract", REPO_ROOT), [])

    def test_sync_jev_stars_fixture_is_idempotent_and_caps(self) -> None:
        script = self._script()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "falcon.sqlite"
            con = sqlite3.connect(db)
            con.executescript("CREATE TABLE sources (id INTEGER PRIMARY KEY, name TEXT); CREATE TABLE requests (id INTEGER PRIMARY KEY, source_id INTEGER, summary_json TEXT, received_request TEXT, upstream_response TEXT, review_state TEXT); CREATE TABLE reviews (request_id INTEGER, state TEXT);")
            con.execute("INSERT INTO sources VALUES (1, 'skill-contract')")
            con.execute("INSERT INTO requests VALUES (1, 1, '{\"starredAt\":\"2026-11-14\"}', '{\"state\":\"ok\",\"questions\":[\"q\"]}', '{\"answers\":{\"a\":1}}', NULL)")
            con.commit(); con.close()
            target = Path(tmp) / "repo" / "skills" / "skill-contract" / "references"; target.mkdir(parents=True)
            (target.parent / "SKILL.md").write_text("---\nname: skill-contract\ndescription: test\n---\n", encoding="utf-8")
            (target / "eval-cases.json").write_text(json.dumps({"active_cases":[{"id":f"EV-{i:03d}","tags":["ordinary"]} for i in range(1,16)]}), encoding="utf-8")
            run = [sys.executable, str(script), "sync-jev-stars", "--skill", "skill-contract", "--repo", str(Path(tmp)/"repo"), "--db-path", str(db)]
            first = subprocess.run(run, cwd=REPO_ROOT, capture_output=True, text=True, check=True)
            self.assertTrue(json.loads(first.stdout)["ok"])
            second = subprocess.run(run, cwd=REPO_ROOT, capture_output=True, text=True, check=True)
            self.assertTrue(json.loads(second.stdout)["ok"])
            doc = json.loads((target / "eval-cases.json").read_text())
            self.assertLessEqual(len(doc["active_cases"]), 15)
            self.assertEqual(sum(1 for c in doc["active_cases"] if c.get("jev_starred_id") == 1), 1)

    def test_v2_frontmatter_negative_and_mock_boundaries(self) -> None:
        script = self._script()
        spec = importlib.util.spec_from_file_location("skill_contract_boundary", script)
        module = importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); skill = root / "skills" / "sample"; (skill / ".claude-plugin").mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: sample\ndescription: >-\n  folded Unicode ✓\n  continuation\nother: ignored\n---\n", encoding="utf-8")
            (skill / ".claude-plugin" / "plugin.json").write_text('{"description": ""}', encoding="utf-8")
            self.assertEqual(module._frontmatter_description(skill / "SKILL.md"), "folded Unicode ✓ continuation")
            codes = {item["code"] for item in module.audit_package(skill)}
            self.assertIn("description-length", codes)
            launch_fail = {"id": "EV-launch", "negative_cmd": "definitely-missing-binary-jev"}
            self.assertTrue(module.evaluate_case(launch_fail, skill, root))
        routed = subprocess.run([sys.executable, str(script), "description-eval", "--skill", "skill-contract", "--mock-jev-json", '{"1":true,"2":false,"3":true}', "--strict"], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        payload = json.loads(routed.stdout)
        self.assertEqual(payload["precision"], 1.0); self.assertEqual(payload["recall"], 1.0)

    def test_prompt_overlap_exact_boundary_and_provided_duplicate_rejection(self) -> None:
        prompts_path = SKILLS_DIR / "design-studio" / "scripts" / "gallery_prompts.py"
        spec = importlib.util.spec_from_file_location("gallery_prompts_correction", prompts_path)
        module = importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(module)
        paragraphs = [f"Candidate paragraph {i} contains enough semantic text for the overlap gate." for i in range(5)]
        self.assertEqual(module._overlap_ratio(set(paragraphs), {paragraphs[0], *(f"Other paragraph {i} has independent semantic content." for i in range(4))}), 0.2)
        self.assertEqual(module._paragraphs("# Heading\n\nshort\n\n" + paragraphs[0]), {paragraphs[0]})
        left = '\n\n'.join('# Heading\n' + p for p in paragraphs)
        right = '\n\n'.join([paragraphs[0], *(f'Other paragraph {i} has independent semantic content.' for i in range(4))])
        module._check_overlap({'left': left, 'right': right})
        with self.assertRaisesRegex(ValueError, 'exceeds 20%'):
            module._check_overlap({'left': left, 'right': right.replace('Other paragraph 0 has independent semantic content.', paragraphs[1])})
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            duplicate = "# Candidate\n\n" + "\n\n".join(paragraphs)
            for name in ("v01", "v02"):
                (source / f"{name}-prompt.md").write_text(duplicate, encoding="utf-8")
            manifest = {"platform": "web", "mode": "screen", "variants": [{"id": "v01"}, {"id": "v02"}]}
            with self.assertRaisesRegex(ValueError, "exceeds 20%"):
                module.prepare(manifest, source)
            evidence_path = SKILLS_DIR / "my-designer" / "scripts" / "design_evidence.py"
            evidence_spec = importlib.util.spec_from_file_location("navigation_evidence_prompt", evidence_path)
            evidence = importlib.util.module_from_spec(evidence_spec); assert evidence_spec.loader is not None; evidence_spec.loader.exec_module(evidence)
            root = source / "gallery"; (root / "prompts").mkdir(parents=True)
            evidence_manifest = {"prompt_contract": {"schema": "design-prompts/v1", "source": "provided", "count": 2}, "variants": []}
            import hashlib
            for name in ("v01", "v02"):
                data = duplicate.encode(); (root / "prompts" / f"{name}-prompt.md").write_bytes(data)
                evidence_manifest["variants"].append({"id": name, "prompt_file": f"prompts/{name}-prompt.md", "prompt_sha256": hashlib.sha256(data).hexdigest()})
            self.assertTrue(any("exceeds 20%" in error for error in evidence.prompt_errors(root, evidence_manifest)))

    def test_round2_prompt_topology_and_two_screen_end_to_end(self) -> None:
        import itertools
        catalog_path = SKILLS_DIR / 'design-studio/scripts/design_catalog.py'
        spec = importlib.util.spec_from_file_location('round2_catalog', catalog_path)
        catalog = importlib.util.module_from_spec(spec); spec.loader.exec_module(catalog)
        evidence_path = SKILLS_DIR / 'my-designer/scripts/design_evidence.py'
        spec = importlib.util.spec_from_file_location('round2_evidence', evidence_path)
        evidence = importlib.util.module_from_spec(spec); spec.loader.exec_module(evidence)
        self.assertEqual(catalog.PROMPTS._paragraphs('# Heading\nSubstantive prose immediately beneath a heading.'), {'Substantive prose immediately beneath a heading.'})
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            env = dict(os.environ, HOME=tmp)
            for mode in ('screen', 'app-nav'):
                schemes = catalog.NAVIGATION.starter_schemes()
                options = []
                if mode == 'app-nav':
                    for i, scheme in enumerate(schemes):
                        scheme['architecture']['kind'] = 'stack'
                        if i == 1: scheme['screens'][0]['actions'][0]['transition']['type'] = 'switchTab'
                        if i == 2: scheme['screens'][0]['actions'][0]['transition'] = {'type': 'toggleSheet', 'to': 'settings'}
                        if i == 3: scheme['screens'][0]['actions'][0] = {'label': 'Start capture', 'frequency': 'P0', 'commit': 'captured'}
                    options = ['--app-flows-json', json.dumps(schemes)]
                command = [sys.executable, str(catalog_path), 'scaffold-gallery', '--goal', f'round2-{mode}', '--round', '1', '--platform', 'mobile', '--mode', mode, *(['--count', '10'] if mode == 'screen' else []), '--no-blog-mount', *options]
                result = subprocess.run(command, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                root = Path(json.loads(result.stdout)['gallery']); manifest = json.loads((root / 'manifest.json').read_text())
                self.assertEqual(evidence.gallery_errors(root, manifest)[0], [])
                bodies = []
                for v in manifest['variants']:
                    text = re.sub(r'\b(?:app|v)\d{2}\b|\d+(?:\.\d+)?', '', (root / v['prompt_file']).read_text())
                    paragraphs = catalog.PROMPTS._paragraphs(text)
                    self.assertGreaterEqual(len(paragraphs), 8)
                    self.assertEqual(paragraphs, evidence._prompt_paragraphs(text))
                    bodies.append(paragraphs)
                self.assertTrue(all(catalog.PROMPTS._overlap_ratio(a, b) <= .2 for a, b in itertools.combinations(bodies, 2)))
                if mode == 'screen':
                    self.assertEqual(len({evidence.topology_signature((root / (v['id'] + '.html')).read_text())[1] for v in manifest['variants']}), 10)
                    first, second = root / 'v01.html', root / 'v02.html'
                    a, b = first.read_text(), second.read_text()
                    grid = '<div class="bento-grid"><article>Signal</article><article>Next step</article></div>'
                    rail = '<ol class="timeline-rail"><li>Captured</li><li>Reviewed</li><li>Ready</li></ol>'
                    second.write_text(b.replace(rail, grid.replace('bento-grid', 'cosmetic')))
                    self.assertTrue(any('topology' in e for e in evidence.gallery_errors(root, manifest)[0]))
                    first.write_text(a.replace(grid, rail)); second.write_text(b.replace(rail, grid))
                    self.assertTrue(any('topology' in e for e in evidence.gallery_errors(root, manifest)[0]))
            for sheet in (True, False):
                schemes = catalog.NAVIGATION.starter_schemes('Recorder')
                for scheme in schemes:
                    if sheet:
                        scheme['screens'] = [scheme['screens'][0], scheme['screens'][2]]
                        scheme['screens'][0]['actions'][0].pop('transition')
                        scheme['architecture']['destinations'] = ['capture']
                    else:
                        scheme['screens'].pop(); scheme['architecture']['sheet'] = None
                        if scheme['architecture']['kind'] != 'stack':
                            scheme['screens'][0]['actions'][0]['transition']['type'] = 'switchTab'
                result = subprocess.run([sys.executable, str(catalog_path), 'scaffold-gallery', '--goal', f'round2-minimal-{sheet}', '--round', '1', '--platform', 'mobile', '--mode', 'app-nav', '--app-flows-json', json.dumps(schemes), '--no-blog-mount'], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                root = Path(json.loads(result.stdout)['gallery']); manifest = json.loads((root / 'manifest.json').read_text())
                self.assertEqual(evidence.gallery_errors(root, manifest)[0], [])
                self.assertEqual(len({catalog.NAVIGATION.initial_state_signature(s) for s in manifest['schemes']}), 4)
                manifest['schemes'][0]['architecture']['initial_state'] = {'active_screen': 'capture', 'history': [], 'overlay': None, 'detent': None, 'overflow_open': False, 'title_collapsed': False}
                self.assertTrue(any('closed root baseline' in e for e in evidence.gallery_errors(root, manifest)[0]))

    def test_d4_default_four_gallery_and_explicit_ten_gate(self) -> None:
        catalog_path = SKILLS_DIR / 'design-studio/scripts/design_catalog.py'
        gate = SKILLS_DIR / 'my-designer/scripts/verify_design_cases.py'
        evidence_path = SKILLS_DIR / 'my-designer/scripts/design_evidence.py'
        spec = importlib.util.spec_from_file_location('d4_count_evidence', evidence_path)
        evidence = importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(evidence)
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            env = dict(os.environ, HOME=tmp)
            for extra, count in (([], 4), (['--count', '10'], 10)):
                result = subprocess.run([sys.executable, str(catalog_path), 'scaffold-gallery', '--goal', f'd4-count-{count}', '--round', '1', '--platform', 'mobile', '--mode', 'screen', '--no-blog-mount', *extra], env=env, capture_output=True, text=True, check=True)
                root = Path(json.loads(result.stdout)['gallery']); manifest = json.loads((root / 'manifest.json').read_text())
                self.assertEqual(manifest['count'], count)
                self.assertEqual(evidence.gallery_errors(root, manifest)[0], [])
                accepted = subprocess.run([sys.executable, str(gate), 'jev-gate', '--platform', 'mobile', '--stage', 'gallery', '--gallery-dir', str(root), '--no-jev'], capture_output=True, text=True)
                self.assertEqual(accepted.returncode, 0, accepted.stdout)
                payload = json.loads(accepted.stdout)
                gallery_evidence = next(item for item in payload['evidence'] if 'variant_count' in item)
                self.assertEqual(gallery_evidence['variant_count'], count)

    def test_d4_anti_clone_gallery_gate_and_jev_gallery_stage(self) -> None:
        catalog_path = SKILLS_DIR / 'design-studio/scripts/design_catalog.py'
        evidence_path = SKILLS_DIR / 'my-designer/scripts/design_evidence.py'
        spec = importlib.util.spec_from_file_location('d4_evidence', evidence_path)
        evidence = importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(evidence)
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as tmp:
            env = dict(os.environ, HOME=tmp)
            result = subprocess.run([sys.executable, str(catalog_path), 'scaffold-gallery', '--goal', 'd4-gate', '--round', '1', '--platform', 'mobile', '--mode', 'screen', '--count', '10', '--no-blog-mount'], env=env, capture_output=True, text=True, check=True)
            root = Path(json.loads(result.stdout)['gallery']); manifest = json.loads((root / 'manifest.json').read_text())
            self.assertEqual(evidence.anti_clone_css_and_layout_errors(root), [])
            self.assertEqual(evidence.rendered_gallery_errors(root), [])
            gate = subprocess.run([sys.executable, str(SKILLS_DIR / 'my-designer/scripts/verify_design_cases.py'), 'jev-gate', '--platform', 'mobile', '--stage', 'gallery', '--gallery-dir', str(root), '--no-jev'], capture_output=True, text=True)
            self.assertEqual(gate.returncode, 0, gate.stdout)
            screen = root / 'screen.css'; original_css = screen.read_text()
            screen.write_text(original_css + '\n.inset-group-card, .glass-row-stack, .ledger-strip-body { display:grid; }\n')
            self.assertTrue(any('comma-aliased' in error for error in evidence.rendered_gallery_errors(root)))
            gate = subprocess.run([sys.executable, str(SKILLS_DIR / 'my-designer/scripts/verify_design_cases.py'), 'jev-gate', '--platform', 'mobile', '--stage', 'gallery', '--gallery-dir', str(root), '--no-jev'], capture_output=True, text=True)
            self.assertNotEqual(gate.returncode, 0)
            screen.write_text(original_css)
            for i in range(1, 4):
                path = root / f'v{i:02d}.html'; text = path.read_text(); text = re.sub(r'--variant-accent:[^;]+', '--variant-accent:#112233', text); path.write_text(text)
            self.assertTrue(any('palette' in error or 'accent' in error for error in evidence.anti_clone_css_and_layout_errors(root)))
            for i in range(1, 11):
                path = root / f'v{i:02d}.html'; text = path.read_text()
                classes = ' '.join(f'clone-v{i:02d}-{j}' for j in range(10))
                rules = ''.join(f'.clone-v{i:02d}-{j}{{display:grid;grid-template-columns:repeat({j + 1},1fr);}}' for j in range(10))
                text = re.sub(r'<main class="', '<main class="' + classes + ' ', text, count=1)
                path.write_text(text.replace('</head>', '<style>' + rules + '</style></head>'))
            self.assertTrue(any('overlap' in error for error in evidence.anti_clone_css_and_layout_errors(root)))
            screen.write_text('.mockup{display:grid;grid-template-columns:1fr 1fr;}')
            self.assertTrue(any('spatial signatures' in error for error in evidence.layout_diversity_errors(root)))

    def test_gallery_portal_gate_and_hub_contract(self) -> None:
        chooser = SKILLS_DIR / 'design-studio/scripts/gallery_chooser.py'
        source = chooser.read_text(encoding='utf-8')
        for token in ('gallery-breadcrumbs', 'selection-telemetry', 'render_demos_hub', 'data-copy-goal', 'aidash-cards-r2:'):
            self.assertIn(token, source)
        css = (SKILLS_DIR / 'design-studio/assets/tenfold-gallery.css').read_text(encoding='utf-8')
        self.assertIn('.variant-card[hidden]', css); self.assertIn('min-height:44px', css); self.assertIn('.variant-summary', css)

        evidence_path = SKILLS_DIR / 'my-designer/scripts/design_evidence.py'
        spec = importlib.util.spec_from_file_location('portal_gate_contract', evidence_path)
        evidence = importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(evidence)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'index.html').write_text('<nav class="gallery-breadcrumbs"><a href="/demos/">Demos</a></nav><button id="copy-summary"></button><p id="selection-telemetry"></p><article class="variant-card" data-group="a" data-select data-star data-note></article><article class="variant-card" data-group="b" aria-hidden="true"></article><script>STAR: no NOTES: no</script>', encoding='utf-8')
            errors = evidence.portal_errors(root)
            self.assertTrue(any('hidden' in error for error in errors))
            self.assertTrue(any('multi-category' in error for error in errors))
            rendered = evidence.rendered_gallery_errors(root)
            self.assertTrue(any('variant-summary' in error for error in rendered))

    def test_navigation_gallery_rejects_meta_copy_and_state_selectors(self) -> None:
        evidence_path = SKILLS_DIR / "my-designer" / "scripts" / "design_evidence.py"
        spec = importlib.util.spec_from_file_location("navigation_evidence", evidence_path)
        module = importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad.html").write_text(
                '<main><p>演示数据</p><select><option>ready</option></select></main>', encoding="utf-8"
            )
            errors = module.rendered_gallery_errors(root)
            self.assertTrue(any("meta-disclaimer" in error for error in errors))
            self.assertTrue(any("test-state select" in error for error in errors))
            (root / "nested-bad.html").write_text('<p><span>mock </span><em>data</em></p>', encoding="utf-8")
            nested_errors = module.rendered_gallery_errors(root)
            self.assertTrue(any(error.startswith("nested-bad.html:") and "meta-disclaimer" in error for error in nested_errors))
            (root / "valid.html").write_text('<!-- 模拟 --><script>mock data</script><div data-note="演示数据"><p>严禁使用演示数据或模拟状态</p><select aria-label="Error policy"><option value="ready-to-ship">Escalate on failure</option></select>', encoding="utf-8")
            valid_errors = module.rendered_gallery_errors(root)
            self.assertFalse(any(error.startswith("valid.html:") and ("meta-disclaimer" in error or "test-state select" in error) for error in valid_errors))
            current = root / 'current.html'
            for text in ('<p>No items yet</p><button>Run Now · 模拟</button>',
                         '<p>Never use mock data; synthetic results are shown.</p>',
                         '<p>禁止使用模拟状态，但现在是演示数据</p>',
                         '<p><span>演示</span><span>数据</span></p>',
                         '<p>mock <em>data</em></p>', '<input type="button" value="确认模拟">',
                         '<input placeholder="示意"><button>Save</button>', '<button title="非实时">Run</button>',
                         '<select><option value="stale">Last run</option></select>'):
                with self.subTest(text=text):
                    current.write_text(text)
                    self.assertTrue(any(e.startswith('current.html:') for e in module.rendered_gallery_errors(root)))
            current.write_text('<!-- 模拟 --><style>.x{--copy:"synthetic"}</style><script>mock data</script><main data-copy="演示数据"><p>No items yet</p><p>Do not use synthetic or mock data.</p><p>严禁使用模拟或示意</p><p>mock</p><p>data</p><input value="Saved"><select aria-label="Error policy"><option value="ready-to-ship">Production release</option></select></main>')
            self.assertFalse(any(e.startswith('current.html:') for e in module.rendered_gallery_errors(root)))


if __name__ == "__main__":
    unittest.main()
