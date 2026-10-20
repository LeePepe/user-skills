#!/usr/bin/env python3
"""Discover a pinned diff, Spec Kit artifacts, and layered repository context."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


EXCLUDED_DIRS = {
    ".git",
    ".build",
    ".worktrees",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "DerivedData",
}
CONTEXT_NAMES = {"tech-context.md", "context.md"}


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def tracked_walk(root: Path):
    for path in root.rglob("*"):
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path


def relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def parse_inline_list(value: str) -> list[str]:
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return []
    return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]


def parse_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, re.S)
    if not match:
        return {}
    lines = match.group(1).splitlines()
    data: dict[str, object] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line or line.startswith((" ", "-")) or ":" not in line:
            index += 1
            continue
        key, raw = line.split(":", 1)
        key, raw = key.strip(), raw.strip()
        if raw:
            data[key] = parse_inline_list(raw) if raw.startswith("[") else raw.strip("'\"")
            index += 1
            continue
        index += 1
        block: list[str] | dict[str, list[str]] = []
        mapping: dict[str, list[str]] = {}
        while index < len(lines) and (lines[index].startswith(" ") or not lines[index].strip()):
            child = lines[index].strip()
            if child.startswith("-"):
                assert isinstance(block, list)
                block.append(child[1:].strip().strip("'\""))
            elif ":" in child:
                child_key, child_value = child.split(":", 1)
                mapping[child_key.strip()] = parse_inline_list(child_value.strip())
            index += 1
        data[key] = mapping if mapping else block
    return data


def resolve_base(repo: Path, requested: str | None) -> tuple[str, str]:
    if requested:
        git(repo, "rev-parse", "--verify", f"{requested}^{{commit}}")
        base_ref = requested
    else:
        remote_head = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False)
        candidates = [remote_head, "origin/main", "origin/master", "main", "master"]
        base_ref = ""
        for candidate in candidates:
            if candidate and git(repo, "rev-parse", "--verify", f"{candidate}^{{commit}}", check=False):
                base_ref = candidate
                break
        if not base_ref:
            raise RuntimeError("cannot derive a default branch; pass --base <ref>")
    merge_base = git(repo, "merge-base", "HEAD", base_ref)
    if not merge_base:
        raise RuntimeError(f"HEAD has no merge-base with {base_ref}")
    return base_ref, merge_base


def changed_files(repo: Path, merge_base: str, committed_only: bool) -> list[dict[str, str]]:
    diff_range = f"{merge_base}...HEAD" if committed_only else merge_base
    output = git(repo, "diff", "--name-status", "--find-renames", diff_range, "--")
    changes = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1]
        changes.append({"status": status, "path": path})
    if not committed_only:
        known_paths = {change["path"] for change in changes}
        untracked = git(repo, "ls-files", "--others", "--exclude-standard")
        for path in untracked.splitlines():
            if path and path not in known_paths:
                changes.append({"status": "??", "path": path})
    changes.sort(key=lambda change: change["path"])
    return changes


def discover_specs(repo: Path, branch: str, changes: list[dict[str, str]], requested: str | None) -> dict:
    candidates = sorted(repo.glob("specs/*/spec.md"))
    if requested:
        selected = (repo / requested).resolve()
        selected = selected / "spec.md" if selected.is_dir() else selected
        try:
            selected.relative_to(repo)
        except ValueError as error:
            raise RuntimeError("--spec must resolve inside the repository") from error
        if selected.name != "spec.md" or not selected.is_file():
            raise RuntimeError(f"invalid Spec Kit feature: {requested}")
        active = selected.parent
        artifacts = {}
        for name in ("spec.md", "plan.md", "tasks.md"):
            artifact = active / name
            if artifact.is_file():
                artifacts[name.removesuffix(".md")] = relative(repo, artifact)
        return {
            "active_feature_dir": relative(repo, active),
            "artifacts": artifacts,
            "candidates": [relative(repo, path) for path in candidates],
        }
    branch_leaf = branch.rsplit("/", 1)[-1].lower()
    number = re.match(r"(\d+)[-_]", branch_leaf)
    changed_features = {
        parts[1]
        for change in changes
        if len(parts := Path(change["path"]).parts) >= 3 and parts[0] == "specs"
    }
    matches = []
    for spec in candidates:
        feature = spec.parent.name.lower()
        if spec.parent.name in changed_features:
            matches.append(spec)
        elif feature == branch_leaf or feature in branch_leaf or branch_leaf in feature:
            matches.append(spec)
        elif number and feature.startswith(f"{number.group(1)}-"):
            matches.append(spec)
    active = matches[0].parent if len(matches) == 1 else None
    artifacts = {}
    if active:
        for name in ("spec.md", "plan.md", "tasks.md"):
            artifact = active / name
            if artifact.is_file():
                artifacts[name.removesuffix(".md")] = relative(repo, artifact)
    return {
        "active_feature_dir": relative(repo, active) if active else None,
        "artifacts": artifacts,
        "candidates": [relative(repo, path) for path in candidates],
    }


def discover_context(repo: Path, changes: list[dict[str, str]]) -> tuple[dict, list[dict], list[str]]:
    files = list(tracked_walk(repo))
    routing = sorted(relative(repo, path) for path in files if path.name.lower() in {"agents.md", "claude.md"})
    constitutions = sorted(
        relative(repo, path)
        for path in files
        if path.name.lower() == "constitution.md" or relative(repo, path) == ".specify/memory/constitution.md"
    )
    top_context = sorted(
        relative(repo, path)
        for path in files
        if path.parent == repo and path.name.lower() in CONTEXT_NAMES | {"architecture.md"}
    )
    layers = []
    for path in files:
        if path.name.lower() not in CONTEXT_NAMES:
            continue
        frontmatter = parse_frontmatter(path)
        layer_name = frontmatter.get("layer")
        if not isinstance(layer_name, str) or not layer_name:
            continue
        layer_root = relative(repo, path.parent)
        layers.append(
            {
                "name": layer_name,
                "root": layer_root,
                "context": relative(repo, path),
                "depends_on": frontmatter.get("depends_on", []),
                "depended_by": frontmatter.get("depended_by", []),
                "red_lines": frontmatter.get("red_lines", []),
                "roles": frontmatter.get("roles", {}),
                "test": frontmatter.get("test"),
                "owns": frontmatter.get("owns", []),
                "changed_files": [],
            }
        )
    layers.sort(key=lambda layer: len(layer["root"]), reverse=True)
    unmapped = []
    for change in changes:
        file_path = change["path"]
        owner = next(
            (
                layer
                for layer in layers
                if layer["root"] not in {"", "."}
                and (file_path == layer["root"] or file_path.startswith(layer["root"] + "/"))
            ),
            None,
        )
        if owner:
            owner["changed_files"].append(file_path)
        else:
            unmapped.append(file_path)
    layers.sort(key=lambda layer: layer["root"])
    return (
        {"routing_indexes": routing, "constitutions": constitutions, "top_technical_context": top_context},
        layers,
        sorted(unmapped),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base")
    parser.add_argument("--spec")
    parser.add_argument("--committed-only", action="store_true")
    args = parser.parse_args()
    repo = Path(git(Path(args.repo).resolve(), "rev-parse", "--show-toplevel")).resolve()
    base_ref, merge_base = resolve_base(repo, args.base)
    changes = changed_files(repo, merge_base, args.committed_only)
    if not changes:
        raise RuntimeError("review surface is empty")
    branch = git(repo, "branch", "--show-current") or "HEAD"
    spec_kit = discover_specs(repo, branch, changes, args.spec)
    context, layers, unmapped = discover_context(repo, changes)
    commits = git(repo, "log", "--oneline", f"{merge_base}..HEAD").splitlines()
    gaps = []
    if not context["routing_indexes"]:
        gaps.append("no AGENTS.md/CLAUDE.md routing index")
    if not context["constitutions"]:
        gaps.append("no constitution source")
    if not spec_kit["active_feature_dir"]:
        gaps.append("no unambiguous active Spec Kit feature")
    if not layers:
        gaps.append("no layer tech-context frontmatter")
    if unmapped:
        gaps.append(f"{len(unmapped)} changed path(s) are unmapped")
    result = {
        "repository": str(repo),
        "branch": branch,
        "base_ref": base_ref,
        "fixed_point": merge_base,
        "mode": "committed-only" if args.committed_only else "worktree",
        "commits": commits,
        "diff_command": (
            f"git diff {merge_base}...HEAD --"
            if args.committed_only
            else f"git diff {merge_base} --"
        ),
        "changed_files": changes,
        "context": context,
        "spec_kit": spec_kit,
        "layers": layers,
        "unmapped_files": unmapped,
        "coverage_gaps": gaps,
    }
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"code-review discovery failed: {error}", file=sys.stderr)
        raise SystemExit(2)
