"""Skill version fingerprints and commit-grounding helpers."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from .fs_guard import get_cached, put_cached


@dataclass(frozen=True)
class SkillVersionInfo:
    version: str
    commits: int
    subtree_sha: str
    last_modified_epoch: int | None

    def as_dict(self) -> dict[str, Any]:
        return {"version": self.version, "commits": self.commits, "subtree_sha": self.subtree_sha, "last_modified_epoch": self.last_modified_epoch}

_MEMO: dict[tuple[str, str], SkillVersionInfo] = {}


def _skill_dir(repo_root: Path, skill_name: str) -> Path:
    for base in (repo_root / "skills", repo_root / ".agents" / "skills"):
        candidate = base / skill_name
        if (candidate / "SKILL.md").is_file():
            return candidate
    return repo_root / "skills" / skill_name


def _run(repo: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(["git", "--no-optional-locks", *args], cwd=repo, capture_output=True, text=True, timeout=3, check=False)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def resolve_skill_version(repo_root: str | Path, skill_name: str) -> SkillVersionInfo:
    repo = Path(repo_root).expanduser().resolve()
    package = _skill_dir(repo, str(skill_name))
    key = (str(repo), str(skill_name))
    cached = _MEMO.get(key)
    if cached is not None:
        try:
            if get_cached(package / "SKILL.md", "skill-version") is not None:
                return cached
        except OSError:
            return cached
    try:
        rel = package.relative_to(repo).as_posix()
    except ValueError:
        rel = f"skills/{package.name}"
    commits = int(_run(repo, ["rev-list", "--count", "HEAD", "--", rel]) or 0)
    subtree = _run(repo, ["rev-parse", "--short", f"HEAD:{rel}"])
    modified = _run(repo, ["log", "-1", "--format=%ct", "--", rel])
    try:
        last_modified = int(modified) if modified else None
    except ValueError:
        last_modified = None
    if not subtree:
        digest = hashlib.sha256()
        for child in sorted(package.rglob("*")):
            if child.is_file() and "__pycache__" not in child.parts:
                digest.update(child.relative_to(package).as_posix().encode() + b"\0" + child.read_bytes())
        subtree = digest.hexdigest()[:7]
    commits = commits or (1 if package.is_dir() else 0)
    info = SkillVersionInfo(f"v{commits}@{subtree}", commits, subtree, last_modified)
    _MEMO[key] = info
    if (package / "SKILL.md").is_file():
        put_cached(package / "SKILL.md", info.as_dict(), "skill-version")
    return info


def verify_commit_exists(repo_root: str | Path, sha: str) -> bool:
    value = str(sha).strip()
    if not value or any(char.isspace() for char in value):
        return False
    try:
        return subprocess.run(["git", "cat-file", "-e", f"{value}^{{commit}}"], cwd=Path(repo_root).expanduser().resolve(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
