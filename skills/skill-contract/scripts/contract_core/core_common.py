#!/usr/bin/env python3
"""Scaffold and audit progressive-disclosure skill packages."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from skill_runtime.cli_contract import SelfDisclosingArgumentParser

ROOT = Path(__file__).resolve().parents[4]
PRIVATE = re.compile(r"\b(?:progressRail|isUnilateral|DashboardL10n)\b")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
BUDGET_EXEMPTIONS: set[str] = set()
COORDINATE_KEYS = ("platforms", "dimensions", "containers", "tags", "routes", "layers")
DRAWERS = {
    "defect-cases/index.json": ("active_cases", ("platforms", "dimensions", "containers")),
    "eval-cases.json": ("active_cases", None),
    "review-smells.json": ("active_smells", ("languages", "layers")),
    "build-cases.json": ("active_cases", ("routes",)),
    "source-tiers.json": ("high_signal_tiers", ("topics", "patterns")),
    "card-templates.json": ("templates", ("tags",)),
}
EVAL_DRAWERS = (
    "references/eval-cases.json",
    "scripts/data/defect-cases/index.json",
    "references/defect-cases/index.json",
)
SCRIPT_SUFFIXES = {".py", ".sh", ".ts"}
REFERENCE_SUFFIXES = {".md", ".json", ".yaml", ".yml"}
VALVE_REFERENCE_BASE = "skills/skill-contract/references/valves-and-metabolism.md"
VALVE_FINDING_GUIDANCE: dict[str, tuple[str, str]] = {
    "private-identifier": (
        f"{VALVE_REFERENCE_BASE}#valve-1--sink-deterministic-checks",
        "Remove project-private symbols from global skills or scope via --repo (see Valve 1).",
    ),
    "raw-regex-gates-in-markdown": (
        f"{VALVE_REFERENCE_BASE}#valve-1--sink-deterministic-checks",
        "Sink deterministic regex gates into executable scripts or JSON drawers instead of Markdown.",
    ),
    "invalid-drawer": (
        f"{VALVE_REFERENCE_BASE}#valve-2--coordinate-and-eval-drawers",
        "Ensure drawer JSON is valid UTF-8 JSON matching schema v1.",
    ),
    "drawer-not-list": (
        f"{VALVE_REFERENCE_BASE}#valve-2--coordinate-and-eval-drawers",
        "Drawer entries key (e.g. active_cases) must be a JSON array.",
    ),
    "top-3-disclosure-cap": (
        f"{VALVE_REFERENCE_BASE}#valve-2--coordinate-and-eval-drawers",
        "Keep active_cases <= 15; archive the oldest ordinary case via One-In-One-Out.",
    ),
    "invalid-case": (
        f"{VALVE_REFERENCE_BASE}#valve-2--coordinate-and-eval-drawers",
        "Each drawer entry must be a JSON object with id, coordinates, and zero-token assertions.",
    ),
    "missing-coordinate": (
        f"{VALVE_REFERENCE_BASE}#valve-2--coordinate-and-eval-drawers",
        "Add required coordinate arrays (e.g. tags, platforms, dimensions, containers, routes, or layers).",
    ),
    "missing-skill": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Create SKILL.md with valid YAML frontmatter (init --skill <name> --category <cat>).",
    ),
    "description-length": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Keep SKILL.md and plugin.json description non-empty and <= 320 characters.",
    ),
    "invalid-manifest": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Fix .claude-plugin/plugin.json syntax and ensure description <= 320 characters.",
    ),
    "missing-version": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Declare a valid semantic version (X.Y.Z) in .claude-plugin/plugin.json for every skill package.",
    ),
    "l0-lines": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Keep SKILL.md <= 180 lines (<= 80 for design skills); move deep details to references/.",
    ),
    "l0-bytes": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Keep SKILL.md <= 16KB; move detailed procedures into references/ or scripts/.",
    ),
    "l0-bytes-soft": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Advisory: keep SKILL.md at or below 12KB when practical; move deep procedures to L1/L2.",
    ),
    "l1-lines": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Keep each references/*.md drawer <= 160 lines; split or sink deterministic checks into scripts/.",
    ),
    "l1-bytes": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Keep each references/*.md drawer <= 16KB.",
    ),
    "l1-bytes-soft": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Advisory: keep each references/*.md drawer at or below 12KB when practical.",
    ),
    "data-drawer-bytes-soft": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Script-Only Data Drawer: query via the L2 CLI; never full-file view the machine/config drawer.",
    ),
    "script-lines-soft": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Advisory: keep executable drawers at or below 800 lines when practical; extract deep modules before 1,000.",
    ),
    "fenced-code-lines": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Keep fenced code blocks in Markdown <= 25 lines; move longer code to scripts/.",
    ),
    "broken-symlink": (
        f"{VALVE_REFERENCE_BASE}#valve-4--sharing-and-metabolism",
        "Use valid package-relative symlinks (never absolute paths or missing targets).",
    ),
    "orphan-reference": (
        f"{VALVE_REFERENCE_BASE}#valve-4--sharing-and-metabolism",
        "Link every references/* file from SKILL.md, a sibling drawer, or archive unused drawers.",
    ),
    "duplicate-skill": (
        f"{VALVE_REFERENCE_BASE}#valve-4--sharing-and-metabolism",
        "Deduplicate identically named skill directories within the same root.",
    ),
    "script-lines": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Keep new or refactored scripts at or below 1,000 lines; extract a deep module instead of appending.",
    ),
    "script-monolith-growth": (
        f"{VALVE_REFERENCE_BASE}#valve-3--budgets",
        "Extract behavior before a listed legacy monolith exceeds its ratcheted line cap.",
    ),
    "duplicate-script-fork": (
        f"{VALVE_REFERENCE_BASE}#valve-4--sharing-and-metabolism",
        "Consolidate duplicate scripts into one canonical script and delegate via a <=25-line _CANONICAL_PATH wrapper.",
    ),
    "assertion-count-mismatch": (
        f"{VALVE_REFERENCE_BASE}#valve-2--coordinate-and-eval-drawers",
        "Enforce 1-Verify-1-Assertion: len(assertions) must equal len(verify_lines) (or verify_count) without splitting or dropping Verify lines.",
    ),
    "soft-assertion-missing-evidence": (
        f"{VALVE_REFERENCE_BASE}#valve-2--coordinate-and-eval-drawers",
        "Every soft assertion (severity='soft' or fatal=false) must include non-empty evidence and why_soft justification.",
    ),
}
FINDING_REMEDIATIONS = VALVE_FINDING_GUIDANCE
CACHE_TTL_SECONDS = 60.0
CACHE_DIR = Path(tempfile.gettempdir()) / "skill-contract-watermark-cache"


class _SelfDisclosingContractParser(SelfDisclosingArgumentParser):
    """Keep parser failures actionable without dumping argparse's long usage text."""

    def error(self, message: str) -> None:
        sys.stderr.write(f"skill-contract: {message}\n")
        sys.stderr.write("Try --help or an unscoped command (audit/eval default to --all).\n")
        sys.stderr.write(f"Reference: {VALVE_REFERENCE_BASE}\n")
        raise SystemExit(2)


def _normalize_repo_path(value: str | Path | None) -> str | None:
    if value is None:
        return None
    path = Path(value).expanduser().resolve()
    if path.name == "skills" and not (path / "SKILL.md").exists():
        return str(path.parent)
    return str(path)


def _emit_json(payload: Mapping[str, Any], args: argparse.Namespace | None = None) -> None:
    compact = bool(args and (getattr(args, "summary", False) or getattr(args, "compact", False)))
    text = json.dumps(payload, ensure_ascii=False, indent=None if compact else 2)
    if len(text.encode("utf-8")) > 20 * 1024:
        summary: dict[str, Any] = {
            "ok": payload.get("ok"),
            "summary": True,
            "schema": payload.get("schema"),
            "count": payload.get("count"),
            "skills": payload.get("skills"),
            "drawers_evaluated": payload.get("drawers_evaluated"),
            "cases_run": payload.get("cases_run"),
            "passed": payload.get("passed"),
            "skill_eval_summary": payload.get("skill_eval_summary"),
            "failures": payload.get("failures") if isinstance(payload.get("failures"), list) else payload.get("failures"),
            "findings": len(payload.get("findings", [])) if isinstance(payload.get("findings"), list) else payload.get("findings"),
            "advisories": len(payload.get("advisories", [])) if isinstance(payload.get("advisories"), list) else payload.get("advisories"),
        }
        text = json.dumps({key: value for key, value in summary.items() if value is not None}, ensure_ascii=False, separators=(",", ":"))
    print(text)


def _git_head_sha(path: Path) -> str:
    cur = path.resolve()
    while cur != cur.parent:
        git_dir = cur / ".git"
        if git_dir.is_dir():
            head = git_dir / "HEAD"
            try:
                value = head.read_text(encoding="utf-8").strip()
                if value.startswith("ref: "):
                    ref = git_dir / value[5:]
                    if ref.is_file():
                        return ref.read_text(encoding="utf-8").strip()
                    packed = git_dir / "packed-refs"
                    if packed.is_file():
                        for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                            if line and not line.startswith("#") and not line.startswith("^") and " " in line:
                                sha, name = line.split(" ", 1)
                                if name == value[5:]:
                                    return sha
                elif value:
                    return value
            except OSError:
                pass
            return ""
        cur = cur.parent
    return ""


def _watermark_fingerprint(repo: str | Path | None, mode: str, args_key: Mapping[str, Any]) -> str:
    roots = package_roots(repo=repo)
    base = Path(repo).expanduser().resolve() if repo else ROOT
    records: list[tuple[str, int, int, str]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() and not path.is_symlink():
                continue
            if (
                path.name != "SKILL.md"
                and path.name != "plugin.json"
                and not (path.parent.name == "references" and path.suffix in REFERENCE_SUFFIXES)
                and not ("scripts" in path.parts and path.suffix in SCRIPT_SUFFIXES)
            ):
                continue
            try:
                stat = path.stat()
                target = os.readlink(path) if path.is_symlink() else ""
                rel = str(path.relative_to(base)) if path.is_relative_to(base) else str(path)
                records.append((rel, stat.st_mtime_ns, stat.st_size, target))
            except OSError:
                records.append((str(path), -1, -1, "unreadable"))
    script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    payload = {"script_sha256": script_sha, "mode": mode, "args": dict(args_key), "records": records, "git_head": _git_head_sha(base)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _cache_path(mode: str, repo: str | Path | None, args_key: Mapping[str, Any]) -> Path:
    identity = {"mode": mode, "repo": str(Path(repo).expanduser().resolve()) if repo else str(ROOT), "args": dict(args_key)}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{mode}-{digest}.json"


def _cache_read(mode: str, repo: str | Path | None, args_key: Mapping[str, Any], *, no_cache: bool = False) -> Any:
    if no_cache:
        return None
    path = _cache_path(mode, repo, args_key)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(doc.get("created", 0)) <= CACHE_TTL_SECONDS and doc.get("fingerprint") == _watermark_fingerprint(repo, mode, args_key):
            return doc.get("payload")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def _cache_write(mode: str, repo: str | Path | None, args_key: Mapping[str, Any], payload: Any) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(mode, repo, args_key)
        path.write_text(json.dumps({"created": time.time(), "fingerprint": _watermark_fingerprint(repo, mode, args_key), "payload": payload}, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass


def _enrich_finding(finding: dict[str, Any]) -> dict[str, Any]:
    code = str(finding.get("code", ""))
    ref, hint = VALVE_FINDING_GUIDANCE.get(
        code,
        (VALVE_REFERENCE_BASE, "Inspect skills/skill-contract/references/valves-and-metabolism.md for contract limits."),
    )
    finding.setdefault("reference", ref)
    finding.setdefault("hint", hint)
    return finding


def package_roots(repo: str | Path | None = None) -> list[Path]:
    if repo is not None:
        repo_path = Path(repo).expanduser().resolve()
        if repo_path.name == "skills" and not (repo_path / "SKILL.md").exists():
            repo_path = repo_path.parent
        return [
            candidate
            for candidate in (repo_path / "skills", repo_path / ".agents" / "skills")
            if candidate.is_dir()
        ]
    roots = [ROOT / "skills"]
    for candidate in (
        ROOT / "Skills" / "skills" / "meta",
        ROOT / "Skills" / "skills" / "repo",
        ROOT.parent / "Skills" / "skills" / "meta",
        ROOT.parent / "Skills" / "skills" / "repo",
        ROOT.parent.parent / "Skills" / "skills" / "meta",
        ROOT.parent.parent / "Skills" / "skills" / "repo",
    ):
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return roots


def _resolve_skill_dir(skill: str, repo: str | Path | None = None, create: bool = False) -> Path:
    if repo is None:
        for root in package_roots():
            if (root / skill / "SKILL.md").is_file() or (root / skill).is_dir():
                return root / skill
        return ROOT / "skills" / skill
    repo_path = Path(repo).expanduser().resolve()
    candidates = [repo_path / "skills" / skill, repo_path / ".agents" / "skills" / skill]
    for base in (repo_path / "skills", repo_path / ".agents" / "skills"):
        if base.is_dir():
            candidates.extend(sub / skill for sub in sorted(base.iterdir()) if sub.is_dir())
    for candidate in candidates:
        if (candidate / "SKILL.md").is_file() or candidate.is_dir():
            return candidate
    if create:
        if (repo_path / ".agents" / "skills").is_dir() and not (repo_path / "skills").is_dir():
            return repo_path / ".agents" / "skills" / skill
        return repo_path / "skills" / skill
    return candidates[0]


def _discover_packages(skill: str | None = None, repo: str | Path | None = None) -> list[Path]:
    if skill:
        return [_resolve_skill_dir(skill, repo=repo, create=False)]
    seen: set[Path] = set()
    packages: list[Path] = []
    for root in package_roots(repo=repo):
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if p.is_dir() and (p / "SKILL.md").exists():
                resolved = p.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    packages.append(p)
    return packages


def _find_git_root_and_rel_path(pkg_dir: Path, repo: str | Path | None = None) -> tuple[Path | None, str]:
    target = pkg_dir.resolve()
    if repo is not None:
        cand_repo = Path(repo).expanduser().resolve()
        if (cand_repo / ".git").exists():
            try:
                return cand_repo, target.relative_to(cand_repo).as_posix()
            except ValueError:
                pass
    cur = target
    while cur != cur.parent:
        if (cur / ".git").exists():
            try:
                return cur, target.relative_to(cur).as_posix()
            except ValueError:
                break
        cur = cur.parent
    return None, f"skills/{pkg_dir.name}"


def _content_hash_subtree(pkg_dir: Path) -> str:
    h = hashlib.sha256()
    if pkg_dir.is_dir():
        for fpath in sorted(pkg_dir.rglob("*")):
            if not fpath.is_file() or "__pycache__" in fpath.parts or fpath.name.startswith("."):
                continue
            try:
                rel = fpath.relative_to(pkg_dir).as_posix()
                h.update(rel.encode("utf-8") + b"\0" + fpath.read_bytes() + b"\0")
            except OSError:
                continue
    return h.hexdigest()[:7]


def resolve_skill_version(
    skill_or_dir: str | Path,
    *,
    repo: str | Path | None = None,
) -> dict[str, Any]:
    """Compute deterministic per-skill version fingerprint `v<commit_count>@<subtree_sha>` + `plugin_version`."""
    if isinstance(skill_or_dir, Path):
        pkg_dir = skill_or_dir
        skill_name = pkg_dir.name
    else:
        skill_name = str(skill_or_dir).strip().lstrip("/")
        if skill_name in {"goal-target-task", "goal-task"}:
            skill_name = "team-lead"
        pkg_dir = _resolve_skill_dir(skill_name, repo=repo, create=False)

    plugin_version = "1.0.0"
    manifest_path = pkg_dir / ".claude-plugin" / "plugin.json"
    if manifest_path.is_file():
        try:
            mdoc = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(mdoc, Mapping):
                raw_ver = str(mdoc.get("version") or "").strip()
                if raw_ver:
                    plugin_version = raw_ver
        except (OSError, json.JSONDecodeError):
            pass

    git_root, rel_path = _find_git_root_and_rel_path(pkg_dir, repo=repo)
    commit_count = 0
    subtree_sha = ""
    last_modified_epoch: int | None = None
    source = "content_hash_fallback"

    if git_root is not None and pkg_dir.is_dir():
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        try:
            cnt_proc = subprocess.run(
                ["git", "--no-optional-locks", "rev-list", "--count", "HEAD", "--", rel_path],
                cwd=git_root,
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            if cnt_proc.returncode == 0 and cnt_proc.stdout.strip().isdigit():
                commit_count = int(cnt_proc.stdout.strip())
            tree_proc = subprocess.run(
                ["git", "--no-optional-locks", "rev-parse", "--short", f"HEAD:{rel_path}"],
                cwd=git_root,
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            if tree_proc.returncode == 0 and tree_proc.stdout.strip():
                subtree_sha = tree_proc.stdout.strip()
            ts_proc = subprocess.run(
                ["git", "--no-optional-locks", "log", "-1", "--format=%ct", "--", rel_path],
                cwd=git_root,
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            if ts_proc.returncode == 0 and ts_proc.stdout.strip().isdigit():
                last_modified_epoch = int(ts_proc.stdout.strip())
            if commit_count > 0 and subtree_sha:
                source = "git_subtree"
        except (OSError, subprocess.SubprocessError):
            pass

    if not subtree_sha:
        subtree_sha = _content_hash_subtree(pkg_dir)
    if commit_count <= 0:
        commit_count = 1 if pkg_dir.is_dir() else 0
    if last_modified_epoch is None and (pkg_dir / "SKILL.md").is_file():
        try:
            last_modified_epoch = int((pkg_dir / "SKILL.md").stat().st_mtime)
        except OSError:
            last_modified_epoch = None

    skill_version = f"v{commit_count}@{subtree_sha}"
    return {
        "ok": pkg_dir.is_dir() and (pkg_dir / "SKILL.md").is_file(),
        "skill": skill_name,
        "skill_version": skill_version,
        "commit_count": commit_count,
        "subtree_sha": subtree_sha,
        "plugin_version": plugin_version,
        "last_modified_epoch": last_modified_epoch,
        "source": source,
    }


def resolve_all_skill_versions(repo: str | Path | None = None, *, no_cache: bool = False) -> dict[str, dict[str, Any]]:
    repo = _normalize_repo_path(repo)
    args_key = {"repo": str(repo or ""), "all": True}
    cached = _cache_read("versions", repo, args_key, no_cache=no_cache)
    if isinstance(cached, dict):
        return cached
    packages = _discover_packages(skill=None, repo=repo)
    if len(packages) <= 1:
        values = [resolve_skill_version(pkg, repo=repo) for pkg in packages]
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(packages))) as pool:
            values = list(pool.map(lambda pkg: resolve_skill_version(pkg, repo=repo), packages))
    result = {pkg.name: info for pkg, info in zip(packages, values)}
    _cache_write("versions", repo, args_key, result)
    return result


def version_cmd(args: argparse.Namespace) -> int:
    repo_arg = _normalize_repo_path(getattr(args, "repo", None))
    skill_arg = getattr(args, "skill", None)
    if skill_arg and not getattr(args, "all", False):
        info = resolve_skill_version(skill_arg, repo=repo_arg)
        _emit_json(info, args)
        return 0 if info["ok"] else 1
    versions_map = resolve_all_skill_versions(repo=repo_arg, no_cache=bool(getattr(args, "no_cache", False)))
    payload = {
        "ok": True,
        "count": len(versions_map),
        "skills": [versions_map[k] for k in sorted(versions_map)],
        "versions": {k: v["skill_version"] for k, v in sorted(versions_map.items())},
    }
    _emit_json(payload, args)
    return 0


def init_skill(args: argparse.Namespace) -> int:
    target = _resolve_skill_dir(args.skill, repo=getattr(args, "repo", None), create=True)
    target.mkdir(parents=True, exist_ok=True)
    for dirname in (".claude-plugin", "references", "scripts"):
        (target / dirname).mkdir(exist_ok=True)
    design = " Design skills use an 80-line L0 budget." if args.design else ""
    (target / "SKILL.md").write_text(
        f'---\nname: {args.skill}\ndescription: "{args.skill} skill."\n---\n\n'
        f"# {args.skill}\n\nProgressive disclosure entry point for the {args.category} category.{design}\n\n"
        "Read references on demand; keep deterministic checks in scripts.\n",
        encoding="utf-8",
    )
    version = str(getattr(args, "version", None) or "1.0.0").strip()
    if not SEMVER_RE.fullmatch(version):
        raise SystemExit(f"invalid semantic version: {version!r} (expected X.Y.Z)")
    manifest = {"name": args.skill, "description": f"{args.skill} skill", "version": version, "category": args.category}
    (target / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _emit_json({"ok": True, "skill": args.skill, "path": str(target)}, args)
    return 0



__all__ = [name for name in globals() if not name.startswith("__")]
