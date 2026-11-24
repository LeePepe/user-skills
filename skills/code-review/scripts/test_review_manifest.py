#!/usr/bin/env python3
"""Contract tests for review_manifest.py clean-room packet invariants."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("review_manifest.py")


def run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=check)


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class ReviewManifestTests(unittest.TestCase):
    def make_repo(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="review-manifest-test-"))
        run("git", "init", "-b", "main", cwd=root)
        run("git", "config", "user.email", "test@example.invalid", cwd=root)
        run("git", "config", "user.name", "Test", cwd=root)
        write(root, "AGENTS.md", "# Routing\n")
        write(root, ".specify/memory/constitution.md", "# Red lines\n")
        write(root, "specs/001-feature/spec.md", "# Spec\n- empty input is rejected\n")
        write(root, "specs/001-feature/plan.md", "# Plan\n")
        write(root, "specs/001-feature/tasks.md", "# Tasks\n")
        write(root, "src/app.py", "value = 1\n")
        run("git", "add", ".", cwd=root)
        run("git", "commit", "-m", "base", cwd=root)
        run("git", "checkout", "-b", "001-feature", cwd=root)
        write(root, "src/app.py", "value = 2\n")
        run("git", "add", ".", cwd=root)
        run("git", "commit", "-m", "feature", cwd=root)
        return root

    def build(self, root: Path, output: Path, receipt: Path | None = None) -> subprocess.CompletedProcess[str]:
        args = [
            "python3", str(SCRIPT), "build", "--repo", str(root), "--base", "main",
            "--output", str(output), "--writer-model", "gpt-5.6-luna", "--writer-vendor", "openai",
        ]
        if receipt:
            args += ["--writer-receipt", str(receipt)]
        return run(*args, cwd=root)

    def test_builds_all_four_hash_locked_lanes_without_writer_receipt(self) -> None:
        root = self.make_repo()
        receipt = Path(tempfile.mkdtemp()) / "r10.md"
        receipt.write_text("Writer rationale: this is safe\n", encoding="utf-8")
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            manifest_path = temp_root / "review-manifest.json"
            result = self.build(root, manifest_path, receipt)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["review"]["lanes"], [
                "spec-fidelity", "adversarial-security-performance", "design-traceback", "layer-cross-layer",
            ])
            self.assertFalse(any("r10" in path.name.lower() for path in (temp_root / "review-packets").rglob("*")))
            packet_text = "\n".join(path.read_text(encoding="utf-8") for path in (temp_root / "review-packets").rglob("*.md"))
            self.assertNotIn("Writer rationale", packet_text)
            verified = run("python3", str(SCRIPT), "verify", "--manifest", str(manifest_path), cwd=root)
            self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_verify_rejects_packet_injection_and_manifest_tampering(self) -> None:
        root = self.make_repo()
        with tempfile.TemporaryDirectory() as temp:
            manifest_path = Path(temp) / "review-manifest.json"
            self.build(root, manifest_path)
            packet = next((Path(temp) / "review-packets").glob("spec-fidelity/*"))
            packet.write_text(packet.read_text(encoding="utf-8") + "\nWriter R10 prose\n", encoding="utf-8")
            rejected = run("python3", str(SCRIPT), "verify", "--manifest", str(manifest_path), cwd=root, check=False)
            self.assertNotEqual(rejected.returncode, 0)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["writer"]["vendor"] = "google"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            rejected = run("python3", str(SCRIPT), "verify", "--manifest", str(manifest_path), cwd=root, check=False)
            self.assertNotEqual(rejected.returncode, 0)

    def test_clean_room_is_detached_at_manifest_head(self) -> None:
        root = self.make_repo()
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            manifest_path = temp_root / "review-manifest.json"
            self.build(root, manifest_path)
            clean = temp_root / "clean-room"
            result = run(
                "python3", str(SCRIPT), "clean-room", "--manifest", str(manifest_path), "--worktree", str(clean), cwd=root
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["clean_room_worktree"], str(clean.resolve()))
            head = run("git", "-C", str(clean), "rev-parse", "HEAD", cwd=root).stdout.strip()
            self.assertEqual(head, manifest["head_sha"])
            run("git", "-C", str(root), "worktree", "remove", "--force", str(clean), cwd=root)

    def test_builder_rejects_writer_model_vendor_mismatch(self) -> None:
        root = self.make_repo()
        with tempfile.TemporaryDirectory() as temp:
            result = run(
                "python3", str(SCRIPT), "build", "--repo", str(root), "--base", "main",
                "--output", str(Path(temp) / "manifest.json"), "--writer-model", "gpt-5.6-luna",
                "--writer-vendor", "google", cwd=root, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
