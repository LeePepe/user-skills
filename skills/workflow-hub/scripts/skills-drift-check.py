#!/usr/bin/env python3
"""Inspect skill package drift across source repositories and CLI targets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_file() and ".git" not in child.parts:
            digest.update(str(child.relative_to(path)).encode())
            digest.update(child.read_bytes())
    return digest.hexdigest()


def _source_skills(sources: list[Path]) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    found: dict[str, list[dict[str, str]]] = {}
    for source in sources:
        root = Path(source).expanduser()
        skills_root = root / "skills"
        if not skills_root.is_dir():
            skills_root = root
        for skill in sorted(skills_root.iterdir()) if skills_root.is_dir() else []:
            if skill.is_dir() and (skill / "SKILL.md").is_file():
                found.setdefault(skill.name, []).append({"repo": str(root), "digest": _digest(skill)})
    clashes = [
        {"code": "DUPLICATE_NAME_CLASH", "skill": name, "sources": values}
        for name, values in sorted(found.items()) if len(values) > 1
    ]
    return found, clashes


def inspect_skills_topology(
    sources: list[Path | str],
    targets: list[Path | str],
    otter_runner: Any = None,
) -> dict[str, Any]:
    found, clashes = _source_skills([Path(item) for item in sources])
    target_states: dict[str, dict[str, str]] = {}
    issues: list[dict[str, str]] = []
    for raw_target in targets:
        target = Path(raw_target).expanduser()
        states = {}
        for name, entries in sorted(found.items()):
            link = target / name
            expected = entries[0]["digest"]
            if not link.exists() and not link.is_symlink():
                state = "MISSING_TARGET_LINK"
            elif link.is_symlink() and not link.exists():
                state = "BROKEN_SYMLINK"
            elif link.is_dir() and not link.is_symlink() and not name.startswith("."):
                state = "UNMANAGED_REAL_DIR"
            else:
                try:
                    state = "CLEAN" if _digest(link.resolve()) == expected else "CONTENT_DRIFT"
                except (OSError, ValueError):
                    state = "CONTENT_DRIFT"
            states[name] = state
            if state != "CLEAN":
                issues.append({"code": state, "target": str(target), "skill": name})
        target_states[str(target)] = states
    fingerprint_input = [
        (name, entries[0]["digest"], tuple(sorted(states.get(name, "") for states in target_states.values())))
        for name, entries in sorted(found.items())
    ]
    fingerprint = hashlib.sha256(json.dumps(fingerprint_input, sort_keys=True).encode()).hexdigest()
    result = {
        "schema_version": 1,
        "skills": {name: entries[0]["digest"] for name, entries in sorted(found.items())},
        "issues": clashes + issues,
        "configurationFingerprint": fingerprint,
        "targets": target_states,
    }
    if otter_runner is not None:
        result["otter"] = otter_runner(result)
    return result


def diff_manifests(first: Path, second: Path) -> dict[str, Any]:
    left = json.loads(first.read_text(encoding="utf-8"))
    right = json.loads(second.read_text(encoding="utf-8"))
    left_skills, right_skills = left.get("skills", {}), right.get("skills", {})
    names = sorted(set(left_skills) | set(right_skills))
    return {
        "changed": [name for name in names if left_skills.get(name) != right_skills.get(name)],
        "fingerprint_changed": left.get("configurationFingerprint") != right.get("configurationFingerprint"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--diff-manifests", nargs=2, type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = diff_manifests(*args.diff_manifests) if args.diff_manifests else inspect_skills_topology(args.source, args.target)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
