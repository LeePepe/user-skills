#!/usr/bin/env python3
"""Classify changed paths as SwiftPM-only, Xcode, or mixed."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath


IGNORED_PARTS = {".build", ".git", "DerivedData", "node_modules"}
XCODE_EXACT_NAMES = {
    "project.yml",
    "project.yaml",
    "Project.swift",
    "Workspace.swift",
    "Tuist.swift",
    "Podfile",
    "Cartfile",
}
NEUTRAL_NAMES = {"AGENTS.md", "CLAUDE.md", "LICENSE", "LICENSE.md", "README", "README.md"}
NEUTRAL_SUFFIXES = {".md", ".markdown", ".rst", ".adoc"}


def git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def nul_paths(output: bytes) -> set[str]:
    return {
        value.decode("utf-8", errors="surrogateescape")
        for value in output.split(b"\0")
        if value
    }


def changed_paths(repo: Path, base: str | None) -> list[str]:
    paths: set[str] = set()
    if base:
        merge_base = git(repo, "merge-base", base, "HEAD").decode().strip()
        paths |= nul_paths(git(repo, "diff", "--name-only", "-z", f"{merge_base}..HEAD"))
    paths |= nul_paths(git(repo, "diff", "--name-only", "-z", "HEAD"))
    paths |= nul_paths(git(repo, "ls-files", "--others", "--exclude-standard", "-z"))
    return sorted(paths)


def xcode_reason(path: PurePosixPath, package_owned: bool) -> str | None:
    if path.name in XCODE_EXACT_NAMES:
        return "project generator or dependency integration metadata"
    if any(part.endswith((".xcodeproj", ".xcworkspace")) for part in path.parts):
        return "Xcode project or workspace metadata"
    if path.suffix in (".xcconfig", ".entitlements"):
        return "Xcode build settings, signing, or interface metadata"
    if package_owned:
        return None
    if path.name == "Info.plist" or path.suffix in (".storyboard", ".xib"):
        return "Xcode build settings or interface metadata"
    for part in path.parts:
        if part.endswith((".xcassets", ".xcdatamodeld")):
            return "Xcode model or asset container"
    return None


def is_neutral(path: PurePosixPath) -> bool:
    return (
        path.name in NEUTRAL_NAMES
        or path.suffix.lower() in NEUTRAL_SUFFIXES
        or (path.parts and path.parts[0] in {"docs", ".github"})
    )


def package_root(repo: Path, relative: PurePosixPath) -> Path | None:
    if any(part in IGNORED_PARTS for part in relative.parts):
        return None
    candidate = (repo / Path(*relative.parts)).parent
    if relative.name == "Package.swift":
        candidate = repo / Path(*relative.parent.parts)
    while candidate == repo or repo in candidate.parents:
        if (candidate / "Package.swift").is_file():
            return candidate
        if candidate == repo:
            break
        candidate = candidate.parent
    return None


def classify(repo: Path, paths: list[str]) -> dict[str, object]:
    package_roots: set[str] = set()
    package_files: list[str] = []
    neutral_files: list[str] = []
    xcode_inputs: list[dict[str, str]] = []

    for raw_path in paths:
        path = PurePosixPath(raw_path)
        root = package_root(repo, path)
        if root is None and is_neutral(path):
            neutral_files.append(raw_path)
            continue
        reason = xcode_reason(path, package_owned=root is not None)
        if reason is not None:
            xcode_inputs.append({"path": raw_path, "reason": reason})
        elif root is not None:
            package_roots.add("." if root == repo else root.relative_to(repo).as_posix())
            package_files.append(raw_path)
        else:
            xcode_inputs.append({
                "path": raw_path,
                "reason": "path is not owned by a discovered Swift package",
            })

    if not paths:
        route = "none"
    elif xcode_inputs and package_roots:
        route = "mixed"
    elif xcode_inputs:
        route = "xcode"
    elif package_roots:
        route = "swiftpm"
    else:
        route = "none"

    return {
        "route": route,
        "changedFiles": paths,
        "packageFiles": package_files,
        "packageRoots": sorted(package_roots),
        "neutralFiles": neutral_files,
        "xcodeInputs": xcode_inputs,
        "note": "Confirm custom Package.swift target paths before executing commands.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Explicit repo-relative paths; otherwise inspect git changes")
    parser.add_argument("--repo", default=".", help="Git repository root or subdirectory")
    parser.add_argument("--base", help="Base ref for committed branch changes, for example origin/main")
    args = parser.parse_args()

    try:
        repo = Path(git(Path(args.repo).resolve(), "rev-parse", "--show-toplevel").decode().strip())
        paths = sorted(set(args.paths)) if args.paths else changed_paths(repo, args.base)
        print(json.dumps(classify(repo, paths), indent=2, sort_keys=True))
        return 0
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        print(f"classify_changes: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
