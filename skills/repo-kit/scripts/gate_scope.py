"""Scenario-conditioned repository scope and self-provider classification."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Sequence

_INCREMENTAL_VERIFY_RE = re.compile(
    r"(?:--changed\b|VERIFY_BASE\b|git\s+diff\s+--name-only|detect_changed_files|verify_runner\.py)",
    re.MULTILINE,
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _git_remote_url(repo_dir: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo_dir), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    cfg = _read_text(repo_dir / ".git" / "config")
    match = re.search(r'(?ms)^\[remote "origin"\]\s*\n(?:\s*[^\n]*\n)*?\s*url\s*=\s*(\S+)', cfg)
    return match.group(1).strip() if match else ""


def _github_remote_slug(remote_url: str) -> str:
    match = re.search(r"github\.com[/:]([^/ :]+/[^/]+?)(?:\.git)?$", remote_url.strip())
    return match.group(1) if match else ""


def _has_tech_context(repo_dir: Path) -> bool:
    for candidate in (repo_dir / "tech-context.md", repo_dir / "docs" / "tech-context.md"):
        if candidate.is_file():
            return True
    try:
        return any(
            candidate.is_file()
            for candidate in repo_dir.rglob("tech-context.md")
            if ".git" not in candidate.parts and "node_modules" not in candidate.parts
        )
    except OSError:
        return False


def repo_scope(repo_dir: Path, *, has_ci_yml: bool, tracked_prepush_candidates: Sequence[Path]) -> tuple[bool, str]:
    contract_marker = (repo_dir / ".github" / "repo-contract.json").is_file()
    hook_marker = (repo_dir / ".githooks").is_dir() or (repo_dir / "scripts" / "hooks").is_dir()
    context_marker = _has_tech_context(repo_dir)
    agents_marker = (repo_dir / "AGENTS.md").is_file()
    code_dirs = ("Packages", "Sources", "src", "lib", "app", "Apps", "Tests", "test")
    code_surface = any((repo_dir / name).is_dir() for name in code_dirs) or any(
        (repo_dir / name).is_file() for name in ("Package.swift", "pyproject.toml", "package.json", "go.mod", "Cargo.toml")
    )
    code_surface = code_surface or any(repo_dir.glob("*.py"))
    github_slug = _github_remote_slug(_git_remote_url(repo_dir))
    third_party_upstream = bool(re.match(r"(?i)^nocoo/", github_slug))
    explicit_marker = contract_marker or hook_marker or context_marker or bool(tracked_prepush_candidates)
    owned_active_surface = has_ci_yml and code_surface and not third_party_upstream
    owned_context_surface = agents_marker and (has_ci_yml or code_surface) and not third_party_upstream
    if explicit_marker or owned_active_surface or owned_context_surface:
        return True, "active_engineering"
    return False, "exempt_third_party_upstream" if third_party_upstream else "exempt_non_contract"


def is_self_provider_verify(repo_dir: Path, verify_code: str) -> bool:
    if repo_dir.name == "shared-ci":
        return True
    if not re.search(r"ThreadPoolExecutor|ProcessPoolExecutor|VERIFY_JOBS", verify_code):
        return False
    docs = "\n".join(_read_text(repo_dir / name) for name in ("README.md", "ai/README.md", "ai/repo-contract.md", "scripts/verify"))
    return bool(
        re.search(r"self[- ]provider", docs, re.IGNORECASE)
        and re.search(r"\bfast\b", docs, re.IGNORECASE)
        and not _INCREMENTAL_VERIFY_RE.search(verify_code)
    )
