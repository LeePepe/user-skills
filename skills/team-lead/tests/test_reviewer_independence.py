#!/usr/bin/env python3
"""Contract tests for strict reviewer dispatch and read-only capability."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DISPATCH = ROOT / "skills/team-lead/scripts/herdr-dispatch.sh"
MANIFEST_BUILDER = ROOT / "skills/code-review/scripts/review_manifest.py"


class ReviewerDispatchTests(unittest.TestCase):
    HERDR = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ.get('HERDR_TEST_LOG')
if log:
    with open(log, 'a', encoding='utf-8') as f:
        f.write(json.dumps(args) + '\\n')
if args[:2] == ['pane', 'split']:
    print(json.dumps({'result': {'pane': {'pane_id': 'p99'}}}))
elif args[:2] == ['agent', 'start']:
    print(json.dumps({'ok': True}))
elif args[:2] == ['agent', 'get']:
    print(json.dumps({'interactive_ready': True, 'agent_status': 'working', 'pane_id': 'p99'}))
elif args[:2] == ['agent', 'prompt']:
    print(json.dumps({'ok': True}))
"""

    def make_review(self) -> tuple[Path, Path, Path, Path]:
        root = Path(tempfile.mkdtemp(prefix="review-dispatch-test-"))
        subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
        (root / "app.py").write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "feature"], cwd=root, check=True, capture_output=True)
        (root / "app.py").write_text("value = 2\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-m", "feature"], cwd=root, check=True, capture_output=True)
        output = Path(tempfile.mkdtemp(prefix="review-manifest-output-")) / "manifest.json"
        subprocess.run([
            "python3", str(MANIFEST_BUILDER), "build", "--repo", str(root), "--base", "main",
            "--output", str(output), "--writer-model", "gpt-5.6-luna", "--writer-vendor", "openai",
        ], cwd=root, check=True, capture_output=True, text=True)
        manifest = json.loads(output.read_text(encoding="utf-8"))
        lane = next(item for item in manifest["packets"] if item["lane"] == "spec-fidelity")
        return root, output, Path(lane["root"]), root / "fake-herdr"

    def run_dispatch(self, root: Path, manifest: Path, lane_root: Path, model: str, *extra: str, dispatch_extra: tuple[str, ...] = ()):
        fake = root / "fake-bin"
        fake.mkdir(exist_ok=True)
        herdr = fake / "herdr"
        herdr.write_text(self.HERDR, encoding="utf-8")
        herdr.chmod(0o755)
        log = root / "herdr.log"
        env = {
            **os.environ,
            "PATH": f"{fake}:{os.environ['PATH']}",
            "HERDR_ENV": "1",
            "HERDR_PANE_ID": "p1",
            "HERDR_MOCK_MAGPIE_ONLINE": "0",
            "HERDR_PROMPT_IDLE_GRACE": "0",
            "HERDR_TEST_LOG": str(log),
        }
        command = [
            str(DISPATCH), "--name", "goal-reviewer-r1", "--kind", "copilot", "--cwd", str(lane_root),
            "--prompt", "Clean-room review: use only the immutable lane packet; return JSON findings only; do not modify files.",
            "--role", "code_reviewer", "--review-manifest", str(manifest), "--review-lane", "spec-fidelity",
            "--writer-model", "gpt-5.6-luna", "--writer-vendor", "openai", "--exclude-vendor", "openai",
            *dispatch_extra, "--", "--model", model, *extra,
        ]
        return subprocess.run(command, cwd=root, env=env, text=True, capture_output=True)

    def test_strict_dispatch_is_cross_vendor_read_only_and_packet_scoped(self) -> None:
        root, manifest, lane_root, _ = self.make_review()
        result = self.run_dispatch(root, manifest, lane_root, "gemini-4-argon")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in (root / "herdr.log").read_text(encoding="utf-8").splitlines()]
        start = next(call for call in calls if call[:2] == ["agent", "start"])
        self.assertNotIn("--allow-tool=write", start)
        self.assertNotIn(["--allow-tool", "write"], [start[index:index + 2] for index in range(len(start) - 1)])
        self.assertIn(str(lane_root), start)
        self.assertNotIn("/Volumes/SSD", start)

    def test_strict_dispatch_rejects_same_vendor_and_missing_contract_fields(self) -> None:
        root, manifest, lane_root, _ = self.make_review()
        same_vendor = self.run_dispatch(root, manifest, lane_root, "gpt-6.1-sol")
        self.assertNotEqual(same_vendor.returncode, 0)
        self.assertTrue(
            "matches Writer vendor" in same_vendor.stderr
            or "violates --exclude-vendor" in same_vendor.stderr
        )
        missing = subprocess.run([
            str(DISPATCH), "--name", "goal-reviewer-r1", "--kind", "copilot", "--cwd", str(lane_root),
            "--prompt", "clean-room", "--role", "code_reviewer", "--writer-vendor", "openai",
        ], cwd=root, env={**os.environ, "HERDR_ENV": "1", "HERDR_PANE_ID": "p1"}, text=True, capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("strict reviewer dispatch requires", missing.stderr)

    def test_reviewer_write_capability_is_rejected(self) -> None:
        root, manifest, lane_root, _ = self.make_review()
        result = self.run_dispatch(root, manifest, lane_root, "gemini-4-argon", "--allow-tool", "write")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("read-only", result.stderr)

    def test_generic_excluded_vendor_is_validated(self) -> None:
        root, manifest, lane_root, _ = self.make_review()
        result = self.run_dispatch(root, manifest, lane_root, "gemini-4-argon", dispatch_extra=("--exclude-vendor", "bogus"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported excluded vendor", result.stderr)


if __name__ == "__main__":
    unittest.main()
