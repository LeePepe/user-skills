#!/usr/bin/env python3
"""Build and verify deterministic, clean-room review packets."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable


LANES = (
    "spec-fidelity",
    "adversarial-security-performance",
    "design-traceback",
    "layer-cross-layer",
)
KNOWN_VENDORS = {"anthropic", "google", "openai", "microsoft", "xai", "moonshot"}
FORBIDDEN_FILENAME = re.compile(r"(?i)(?:^|[-_.])(r10|receipt|writer|transcript|chat|conversation)(?:[-_.]|$)")


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def model_vendor(model: str) -> str | None:
    """Resolve only concrete models known by runtime-profiles.yaml."""
    exact = {
        "opus": "anthropic",
        "gpt-6.1-sol": "openai",
        "gpt-5.6-luna": "openai",
        "mai-code-2": "microsoft",
        "mai-code-2-flash": "microsoft",
        "gemini-4-argon": "google",
        "gemini-4-carbon": "google",
        "kimi": "moonshot",
        "grok-4.7": "xai",
    }
    return exact.get(model)


def load_discovery_module():
    path = Path(__file__).with_name("discover_review_context.py")
    spec = importlib.util.spec_from_file_location("review_discovery", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_inputs(repo: Path, base: str | None, requested_spec: str | None) -> dict[str, Any]:
    discovery = load_discovery_module()
    base_ref, merge_base = discovery.resolve_base(repo, base)
    changes = discovery.changed_files(repo, merge_base, committed_only=True)
    if not changes:
        raise RuntimeError("review surface is empty")
    branch = run_git(repo, "branch", "--show-current").strip() or "HEAD"
    spec_kit = discovery.discover_specs(repo, branch, changes, requested_spec)
    context, layers, unmapped = discovery.discover_context(repo, changes)
    return {
        "base_ref": base_ref,
        "merge_base": merge_base,
        "branch": branch,
        "changes": changes,
        "spec_kit": spec_kit,
        "context": context,
        "layers": layers,
        "unmapped": unmapped,
    }


def ensure_clean(repo: Path) -> None:
    status = run_git(repo, "status", "--porcelain", "--untracked-files=all").strip()
    if status:
        raise RuntimeError("repository must be clean; build the manifest from an immutable commit")


def safe_relative(repo: Path, raw: str) -> Path:
    path = (repo / raw).resolve()
    try:
        return path.relative_to(repo.resolve())
    except ValueError as error:
        raise RuntimeError(f"authority path escapes repository: {raw}") from error


def copy_authority(repo: Path, paths: Iterable[str], destination: Path) -> list[str]:
    copied: list[str] = []
    for raw in sorted(set(paths)):
        relative = safe_relative(repo, raw)
        source = repo / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(relative.as_posix())
    return copied


def copy_design_files(files: Iterable[str], destination: Path) -> list[str]:
    copied: list[str] = []
    for raw in files:
        source = Path(raw).expanduser().resolve()
        if not source.is_file():
            raise RuntimeError(f"design authority file does not exist: {source}")
        if FORBIDDEN_FILENAME.search(source.name):
            raise RuntimeError(f"design authority filename is not allowed in a clean-room packet: {source.name}")
        target = destination / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(target.relative_to(destination).as_posix())
    return copied


def copy_screenshots(directory: str | None, destination: Path) -> list[str]:
    if not directory:
        return []
    source_root = Path(directory).expanduser().resolve()
    if not source_root.is_dir():
        raise RuntimeError(f"screenshot directory does not exist: {source_root}")
    allowed = {".png", ".jpg", ".jpeg", ".webp"}
    copied: list[str] = []
    for source in sorted(path for path in source_root.rglob("*") if path.is_file() and path.suffix.lower() in allowed):
        target = destination / source.relative_to(source_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(target.relative_to(destination).as_posix())
    return copied


def packet_hash(root: Path) -> tuple[str, list[str]]:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    digest = hashlib.sha256()
    names: list[str] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if FORBIDDEN_FILENAME.search(Path(relative).name):
            raise RuntimeError(f"forbidden Writer/R10 artifact in packet: {relative}")
        names.append(relative)
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), names


def relative_file_digests(root: Path, files: Iterable[str]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for raw in sorted(set(files)):
        path = root / raw
        if path.is_file():
            digests[raw] = sha256_file(path)
    return digests


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    packet_root = Path(args.packet_root).expanduser().resolve() if args.packet_root else output.parent / "review-packets"
    if not repo.is_dir() or not (repo / ".git").exists():
        raise RuntimeError(f"not a git worktree: {repo}")
    ensure_clean(repo)
    if args.writer_vendor not in KNOWN_VENDORS:
        raise RuntimeError(f"unknown Writer vendor: {args.writer_vendor}")
    resolved_writer_vendor = model_vendor(args.writer_model)
    if resolved_writer_vendor and resolved_writer_vendor != args.writer_vendor:
        raise RuntimeError(
            f"Writer model/vendor mismatch: {args.writer_model} resolves to {resolved_writer_vendor}, not {args.writer_vendor}"
        )
    lanes = tuple(item.strip() for item in args.lanes.split(",") if item.strip())
    invalid = sorted(set(lanes) - set(LANES))
    if not lanes or invalid:
        raise RuntimeError(f"lanes must be a non-empty subset of {', '.join(LANES)}")
    if output.exists() or packet_root.exists():
        raise RuntimeError("output manifest or packet root already exists; refusing to overwrite")

    inputs = resolve_inputs(repo, args.base, args.spec)
    head_sha = run_git(repo, "rev-parse", "HEAD").strip()
    diff = run_git(repo, "diff", "--binary", "--full-index", f"{inputs['merge_base']}...HEAD", "--")
    packet_root.mkdir(parents=True)
    common = packet_root / "common"
    common.mkdir()
    (common / "diff.patch").write_text(diff, encoding="utf-8")
    write_json(common / "discovery.json", {
        "base_ref": inputs["base_ref"],
        "merge_base": inputs["merge_base"],
        "head_sha": head_sha,
        "changed_files": inputs["changes"],
        "spec_kit": inputs["spec_kit"],
        "context": inputs["context"],
        "layers": inputs["layers"],
        "unmapped_files": inputs["unmapped"],
    })

    authority_paths = (
        inputs["context"]["routing_indexes"]
        + inputs["context"]["constitutions"]
        + inputs["context"]["top_technical_context"]
        + [layer["context"] for layer in inputs["layers"]]
    )
    spec_paths = list(inputs["spec_kit"].get("artifacts", {}).values())
    common_authority = common / "authority"
    common_authority.mkdir(parents=True, exist_ok=True)
    copied_authority = copy_authority(repo, authority_paths + spec_paths, common_authority)
    design_files = copy_design_files(args.design_file, common / "design") if args.design_file else []
    screenshots = copy_screenshots(args.screenshots, common / "screenshots")
    if args.design_contract:
        contract_files = copy_design_files([args.design_contract], common / "design-contract")
    else:
        contract_files = []

    packet_entries: list[dict[str, Any]] = []
    for lane in lanes:
        root = packet_root / lane
        root.mkdir()
        shutil.copyfile(common / "diff.patch", root / "diff.patch")
        if lane == "spec-fidelity":
            shutil.copytree(common_authority, root / "authority")
            (root / "lane.md").write_text(
                "Clean-room Spec-Fidelity review. Use only this packet. Return JSON findings for scope, acceptance, and spec intent.\n",
                encoding="utf-8",
            )
        elif lane == "adversarial-security-performance":
            shutil.copytree(common_authority, root / "authority")
            (root / "lane.md").write_text(
                "Clean-room Adversarial/Security/Performance review. Focus on security, privacy, failure, concurrency, abuse, and resource/performance risks. Return JSON only.\n",
                encoding="utf-8",
            )
        elif lane == "design-traceback":
            if (common / "design").exists():
                shutil.copytree(common / "design", root / "design")
            if (common / "design-contract").exists():
                shutil.copytree(common / "design-contract", root / "design-contract")
            if (common / "screenshots").exists():
                shutil.copytree(common / "screenshots", root / "screenshots")
            (root / "lane.md").write_text(
                "Clean-room Design-Traceback review. Use design authority, screenshots, and deterministic contract output only. Return JSON with IMPL_BUG or SPEC_GAP attribution.\n",
                encoding="utf-8",
            )
        elif lane == "layer-cross-layer":
            shutil.copytree(common_authority, root / "authority")
            shutil.copyfile(common / "discovery.json", root / "discovery.json")
            (root / "lane.md").write_text(
                "Clean-room Layer/Cross-layer review. Focus only on ownership, dependency direction, and independently verifiable layer boundaries. Return JSON only.\n",
                encoding="utf-8",
            )
        digest, files = packet_hash(root)
        packet_entries.append({"lane": lane, "root": str(root), "sha256": digest, "files": files})

    authority_digests = relative_file_digests(common_authority, [
        path for path in copied_authority if path in spec_paths
    ])
    spec_digest = sha256_bytes(canonical_json({"artifacts": spec_paths, "files": authority_digests}))
    manifest: dict[str, Any] = {
        "schema": "review-manifest/v1",
        "repository": str(repo),
        "branch": inputs["branch"],
        "base_ref": inputs["base_ref"],
        "merge_base": inputs["merge_base"],
        "head_sha": head_sha,
        "diff_sha256": sha256_bytes(diff.encode()),
        "spec_sha256": spec_digest,
        "changed_files": inputs["changes"],
        "writer": {"model": args.writer_model, "vendor": args.writer_vendor},
        "review": {
            "lanes": list(lanes),
            "cross_vendor_required": True,
            "actual_vendor_required": True,
            "read_only_required": True,
        },
        "packets": packet_entries,
        "packet_root": str(packet_root),
        "clean_room_worktree": None,
        "authority_files": copied_authority,
        "design_files": design_files,
        "screenshots": screenshots,
        "design_contract_files": contract_files,
        "excluded_inputs": {
            "writer_r10_receipt": str(Path(args.writer_receipt).expanduser().resolve()) if args.writer_receipt else None,
            "writer_transcripts": True,
            "tl_transcripts": True,
            "mutable_worktree": True,
        },
        "packet_policy": {
            "allow_writer_r10_prose": False,
            "forbidden_filename_regex": FORBIDDEN_FILENAME.pattern,
            "reviewer_cwd_must_be_lane_root": True,
        },
    }
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(manifest))
    write_json(output, manifest)
    print(json.dumps({"ok": True, "manifest": str(output), "packets": packet_entries}, ensure_ascii=False))
    return 0


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"missing review manifest: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = manifest.get("manifest_sha256")
    if not isinstance(expected, str):
        raise RuntimeError("manifest is missing manifest_sha256")
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256", None)
    actual = sha256_bytes(canonical_json(unsigned))
    if actual != expected:
        raise RuntimeError("review manifest hash mismatch")
    if manifest.get("schema") != "review-manifest/v1":
        raise RuntimeError("unsupported review manifest schema")
    return manifest


def verify(args: argparse.Namespace) -> int:
    manifest = load_manifest(Path(args.manifest).expanduser().resolve())
    lanes = [entry.get("lane") for entry in manifest.get("packets", [])]
    if args.lane and args.lane not in lanes:
        raise RuntimeError(f"lane is not present in manifest: {args.lane}")
    if args.writer_vendor and args.writer_vendor != manifest.get("writer", {}).get("vendor"):
        raise RuntimeError("Writer vendor does not match manifest")
    writer = manifest.get("writer", {})
    writer_vendor = writer.get("vendor")
    writer_model = writer.get("model")
    if writer_vendor not in KNOWN_VENDORS:
        raise RuntimeError("manifest contains an unknown Writer vendor")
    resolved_writer_vendor = model_vendor(writer_model) if isinstance(writer_model, str) else None
    if resolved_writer_vendor and resolved_writer_vendor != writer_vendor:
        raise RuntimeError("manifest Writer model/vendor mismatch")
    repo = Path(manifest["repository"]).expanduser().resolve()
    if repo.is_dir() and (repo / ".git").exists():
        current_head = run_git(repo, "rev-parse", "HEAD").strip()
        if current_head != manifest.get("head_sha"):
            raise RuntimeError("review source HEAD changed after manifest creation")
    if args.cwd and args.lane:
        entry = next(item for item in manifest["packets"] if item["lane"] == args.lane)
        if Path(args.cwd).expanduser().resolve() != Path(entry["root"]).expanduser().resolve():
            raise RuntimeError("reviewer cwd must be the immutable lane packet root")
    for entry in manifest.get("packets", []):
        root = Path(entry["root"]).expanduser().resolve()
        if not root.is_dir():
            raise RuntimeError(f"missing packet root: {root}")
        digest, files = packet_hash(root)
        if digest != entry.get("sha256") or files != entry.get("files"):
            raise RuntimeError(f"packet hash or file list mismatch for lane {entry.get('lane')}")
        diff_path = root / "diff.patch"
        if not diff_path.is_file() or sha256_file(diff_path) != manifest.get("diff_sha256"):
            raise RuntimeError(f"diff hash mismatch for lane {entry.get('lane')}")
    clean_room = manifest.get("clean_room_worktree")
    if clean_room:
        clean_path = Path(clean_room).expanduser().resolve()
        if not clean_path.is_dir():
            raise RuntimeError(f"missing clean-room worktree: {clean_path}")
        if run_git(clean_path, "rev-parse", "HEAD").strip() != manifest.get("head_sha"):
            raise RuntimeError("clean-room worktree HEAD does not match manifest")
        if run_git(clean_path, "branch", "--show-current").strip():
            raise RuntimeError("clean-room worktree must remain detached")
    if manifest.get("excluded_inputs", {}).get("writer_r10_receipt"):
        receipt = Path(manifest["excluded_inputs"]["writer_r10_receipt"]).expanduser().resolve()
        if receipt.exists() and receipt.is_dir():
            raise RuntimeError("Writer R10 exclusion points to a directory")
    print(json.dumps({"ok": True, "manifest": str(Path(args.manifest).expanduser().resolve()), "lanes": lanes}))
    return 0


def clean_room(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_manifest(manifest_path)
    worktree = Path(args.worktree).expanduser().resolve()
    if worktree.exists():
        if any(worktree.iterdir()):
            raise RuntimeError(f"clean-room worktree is not empty: {worktree}")
    else:
        worktree.parent.mkdir(parents=True, exist_ok=True)
    repo = Path(manifest["repository"]).expanduser().resolve()
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree), manifest["head_sha"]], check=True)
    for path in worktree.rglob("*"):
        if path.is_file() and path.name != ".git":
            path.chmod(path.stat().st_mode & ~0o222)
    manifest["clean_room_worktree"] = str(worktree)
    manifest["manifest_sha256"] = ""
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(unsigned))
    write_json(manifest_path, manifest)
    print(json.dumps({"ok": True, "worktree": str(worktree), "manifest": str(manifest_path)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--repo", required=True)
    build_parser.add_argument("--output", required=True)
    build_parser.add_argument("--packet-root")
    build_parser.add_argument("--base")
    build_parser.add_argument("--spec")
    build_parser.add_argument("--writer-model", required=True)
    build_parser.add_argument("--writer-vendor", required=True)
    build_parser.add_argument("--writer-receipt")
    build_parser.add_argument("--design-file", action="append", default=[])
    build_parser.add_argument("--design-contract")
    build_parser.add_argument("--screenshots")
    build_parser.add_argument("--lanes", default=",".join(LANES))
    build_parser.set_defaults(func=build)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--manifest", required=True)
    verify_parser.add_argument("--lane")
    verify_parser.add_argument("--cwd")
    verify_parser.add_argument("--writer-model")
    verify_parser.add_argument("--writer-vendor")
    verify_parser.set_defaults(func=verify)
    clean_parser = sub.add_parser("clean-room")
    clean_parser.add_argument("--manifest", required=True)
    clean_parser.add_argument("--worktree", required=True)
    clean_parser.set_defaults(func=clean_room)
    vendor_parser = sub.add_parser("model-vendor")
    vendor_parser.add_argument("--model", required=True)
    vendor_parser.set_defaults(func=lambda a: print(model_vendor(a.model) or "unknown") or 0)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"review manifest failed: {error}", file=sys.stderr)
        raise SystemExit(2)
