#!/usr/bin/env python3
"""Cross-device iCloud (`AIDashSync`) auto-sync for M2 skills definitions, symlinks & telemetry.

Subcommands:
1. `publish`: Exports `skills-sync-manifest.json` (`head_sha`, `updated_at`, `updated_by_host`,
   `skills`) and mirrors `skills/<skill>/SKILL.md` (+ `.claude-plugin/plugin.json`) into
   `<icloud_root>/skills-mirror/<skill>/` when local `user-skills` `main` HEAD advances.
2. `pull`: Compares `<icloud_root>/skills-sync-manifest.json` against local `user-skills` `HEAD`;
   runs `git fetch origin main --quiet && git pull --ff-only` when local `main` is clean and behind
   (or falls back to `skills-mirror/` when offline), then heals client symlinks.
3. `heal-symlinks`: Idempotently heals symlinks across `~/.agents/skills`, `~/.copilot/skills`,
   `~/.claude/skills`, and `~/.codex/skills` (preserving valid symlinks to sibling repos such as
   `~/Development/Skills/skills/...` and cleaning dangling `user-skills` links), plus runs
   `install-pi-harness.sh` when present.
4. `export-telemetry`: Incrementally aggregates deduplicated skill/gateway usage events into
   `~/.local/share/skill-telemetry/usage.jsonl` and mirrors per-host shards to
   `<icloud_root>/telemetry-shards/<hostname>-skill-usage.jsonl` +
   `<icloud_root>/m2-ledger-shards/<hostname>-ledgers.jsonl`.
5. `sync`: Runs `pull` -> `publish` -> `heal-symlinks` -> `export-telemetry` (and optional
   `--install-launchd`).
6. `install-launchd`: Installs and bootstraps `~/Library/LaunchAgents/com.tianpli.skills.icloud-sync.plist`
   (`WatchPaths` on `<icloud_root>/skills-sync-manifest.json` + `StartInterval=300`).
7. `status`: Reports local vs. iCloud manifest SHA, symlink health, shard counts, and LaunchAgent status.
"""

from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1
REPO_SLUG = "LeePepe/user-skills"
DEFAULT_LAUNCHD_LABEL = "com.tianpli.skills.icloud-sync"
CST = timezone(timedelta(hours=8))

SCRIPT_PATH = Path(__file__).resolve()

_HOME_PATH_RE = re.compile(r"(?:/" + r"Users|/home)/[^/\s\"']+")
_SECRET_RE = re.compile(
    r"(?i)(?:ghp_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+|AKIA[0-9A-Z]{16}|"
    r"(?<![A-Za-z0-9])((?:api[_-]?key|token|password|secret)\s*[:=]\s*)['\"]?[^'\"\s,;]+)"
)
_SKILL_NAME_RE = re.compile(
    r"(?:<skill-context\s+name=[\"'](?P<ctx>[A-Za-z0-9_-]+)[\"']"
    r"|(?<![A-Za-z0-9_-])(?:/)?skills/(?P<path>[A-Za-z0-9_-]+)/SKILL\.md"
    r"|\bskill\s*[:=]\s*[\"'](?P<call>[A-Za-z0-9_-]+)[\"'])"
)
_REF_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:/)?skills/(?P<skill>[A-Za-z0-9_-]+)/references/(?P<ref>[A-Za-z0-9_./-]+)"
)
_SCRIPT_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:/)?skills/(?P<skill>[A-Za-z0-9_-]+)/scripts/(?P<script>[A-Za-z0-9_.-]+\.(?:py|sh|ts))"
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


def default_icloud_root() -> Path:
    env_val = os.environ.get("AIDASH_ICLOUD_SYNC_DIR", "").strip()
    if env_val:
        return Path(env_val).expanduser()
    return Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "AIDashSync"


def default_repo_root() -> Path:
    env_val = os.environ.get("USER_SKILLS_REPO_ROOT", "").strip()
    if env_val:
        return Path(env_val).expanduser()
    canonical = Path.home() / "Development" / "user-skills"
    if (canonical / "skills").is_dir():
        return canonical
    return SCRIPT_PATH.parents[3]


def default_client_skill_dirs(home_dir: Path | None = None) -> tuple[Path, ...]:
    base = home_dir or Path.home()
    return (
        base / ".agents" / "skills",
        base / ".copilot" / "skills",
        base / ".claude" / "skills",
        base / ".codex" / "skills",
    )


def default_local_telemetry_path(home_dir: Path | None = None) -> Path:
    env_val = os.environ.get("SKILL_TELEMETRY_USAGE_PATH", "").strip()
    if env_val:
        return Path(env_val).expanduser()
    base = home_dir or Path.home()
    return base / ".local" / "share" / "skill-telemetry" / "usage.jsonl"


def default_magpie_usage_path(home_dir: Path | None = None) -> Path:
    base = home_dir or Path.home()
    return base / ".config" / "magpie" / "usage.jsonl"


def default_copilot_db_path(home_dir: Path | None = None) -> Path:
    base = home_dir or Path.home()
    return base / ".copilot" / "session-store.db"


def default_warehouse_db_path(home_dir: Path | None = None) -> Path:
    base = home_dir or Path.home()
    return base / "Development" / "AIDash" / "aidata" / "L3_merge" / "warehouse.db"


def default_handoff_root(home_dir: Path | None = None) -> Path:
    base = home_dir or Path.home()
    return base / "Development" / "handoff"


def normalize_hostname(raw: str | None = None) -> str:
    candidate = (raw or os.environ.get("SKILLS_SYNC_HOSTNAME") or "").strip()
    if not candidate:
        try:
            proc = subprocess.run(
                ["scutil", "--get", "LocalHostName"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                candidate = proc.stdout.strip()
        except OSError:
            candidate = ""
    if not candidate:
        candidate = socket.gethostname().split(".", 1)[0]
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", candidate.strip().lower()).strip("-")
    return cleaned or "unknown-host"


def redact_text(value: Any) -> str:
    text = str(value or "")
    home_str = str(Path.home())
    if home_str and home_str != "/":
        text = text.replace(home_str, "~")
    text = _HOME_PATH_RE.sub("~", text)
    text = _SECRET_RE.sub(lambda m: (m.group(1) or "") + "[REDACTED]", text)
    return text


def _default_runner(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    clean_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    try:
        return subprocess.run(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            env=clean_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(list(command), 124, "", "command timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(list(command), 127, "", str(exc))


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write_bytes(path, encoded)


@contextlib.contextmanager
def _file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _git_head_info(repo_root: Path, *, ref: str = "HEAD", runner: Runner = _default_runner) -> tuple[str, int, str]:
    """Return (sha40, commit_epoch, branch_name)."""
    sha_proc = runner(["git", "--no-optional-locks", "rev-parse", ref], cwd=repo_root)
    sha = sha_proc.stdout.strip() if sha_proc.returncode == 0 else ""
    ts_proc = runner(["git", "--no-optional-locks", "show", "-s", "--format=%ct", ref], cwd=repo_root)
    try:
        epoch = int(ts_proc.stdout.strip()) if ts_proc.returncode == 0 else 0
    except ValueError:
        epoch = 0
    br_proc = runner(["git", "--no-optional-locks", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
    branch = br_proc.stdout.strip() if br_proc.returncode == 0 else ""
    return sha, epoch, branch


def _git_is_ancestor(
    repo_root: Path,
    ancestor_sha: str,
    descendant_sha: str,
    *,
    runner: Runner = _default_runner,
) -> bool:
    if not ancestor_sha or not descendant_sha:
        return False
    proc = runner(
        ["git", "--no-optional-locks", "merge-base", "--is-ancestor", ancestor_sha, descendant_sha],
        cwd=repo_root,
    )
    return proc.returncode == 0


def _git_is_clean(repo_root: Path, *, runner: Runner = _default_runner) -> bool:
    proc = runner(["git", "--no-optional-locks", "status", "--porcelain", "--untracked-files=no"], cwd=repo_root)
    return proc.returncode == 0 and not proc.stdout.strip()


def enumerate_repo_skills(repo_root: Path) -> list[dict[str, Any]]:
    skills_dir = repo_root / "skills"
    if not skills_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for entry in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        skill_md = entry / "SKILL.md"
        if not entry.is_dir() or not skill_md.is_file():
            continue
        raw = skill_md.read_bytes()
        sha256 = hashlib.sha256(raw).hexdigest()
        line_count = len(raw.decode("utf-8", errors="replace").splitlines())
        plugin_json = entry / ".claude-plugin" / "plugin.json"
        items.append(
            {
                "name": entry.name,
                "skill_md_sha256": sha256,
                "lines": line_count,
                "mirror_rel_path": f"skills-mirror/{entry.name}/SKILL.md",
                "has_plugin_json": plugin_json.is_file(),
            }
        )
    return items


def enumerate_all_local_skill_sources(
    repo_root: Path,
    *,
    sibling_skills_repo: Path | None = None,
) -> dict[str, Path]:
    """Return canonical skill_name -> target directory map (`user-skills` first, then sibling `Skills` repo)."""
    resolved_map: dict[str, Path] = {}
    skills_dir = repo_root / "skills"
    if skills_dir.is_dir():
        for pkg in sorted(skills_dir.iterdir(), key=lambda p: p.name):
            if pkg.is_dir() and (pkg / "SKILL.md").is_file():
                resolved_map[pkg.name] = pkg

    sib_repo = sibling_skills_repo
    if sib_repo is None:
        candidate = repo_root.parent / "Skills"
        if candidate.is_dir():
            sib_repo = candidate
    if sib_repo is not None and (sib_repo / "skills").is_dir():
        for cat_dir in sorted((sib_repo / "skills").iterdir(), key=lambda p: p.name):
            if not cat_dir.is_dir():
                continue
            if (cat_dir / "SKILL.md").is_file():
                resolved_map.setdefault(cat_dir.name, cat_dir)
                continue
            for pkg in sorted(cat_dir.iterdir(), key=lambda p: p.name):
                if pkg.is_dir() and (pkg / "SKILL.md").is_file():
                    resolved_map.setdefault(pkg.name, pkg)
    return resolved_map


def read_sync_manifest(icloud_root: Path) -> dict[str, Any] | None:
    manifest_path = icloud_root / "skills-sync-manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def publish_skills_to_icloud(
    *,
    repo_root: Path | None = None,
    icloud_root: Path | None = None,
    hostname: str | None = None,
    force: bool = False,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    repo = (repo_root or default_repo_root()).expanduser()
    icloud = (icloud_root or default_icloud_root()).expanduser()
    host = normalize_hostname(hostname)
    manifest_path = icloud / "skills-sync-manifest.json"
    mirror_root = icloud / "skills-mirror"

    ref = "refs/heads/main"
    head_sha, head_epoch, branch = _git_head_info(repo, ref=ref, runner=runner)
    if not head_sha:
        head_sha, head_epoch, branch = _git_head_info(repo, ref="HEAD", runner=runner)
    skills = enumerate_repo_skills(repo)

    existing = read_sync_manifest(icloud)
    existing_sha = str((existing or {}).get("head_sha") or "").strip()
    existing_epoch = int((existing or {}).get("head_commit_epoch") or 0)

    should_publish = force or existing is None or not (icloud / "skills-mirror").is_dir()
    reason = "forced_or_initial"
    if not should_publish:
        if head_sha and head_sha == existing_sha:
            missing_mirror = any(not (icloud / item["mirror_rel_path"]).is_file() for item in skills)
            if missing_mirror:
                should_publish = True
                reason = "repair_missing_mirror_files"
            else:
                reason = "already_at_head_sha"
        elif head_sha and existing_sha:
            if _git_is_ancestor(repo, existing_sha, head_sha, runner=runner):
                should_publish = True
                reason = "local_main_is_descendant"
            elif _git_is_ancestor(repo, head_sha, existing_sha, runner=runner):
                should_publish = False
                reason = "local_main_behind_manifest"
            elif head_epoch >= existing_epoch:
                should_publish = True
                reason = "local_commit_newer_diverged"
            else:
                should_publish = False
                reason = "remote_manifest_newer"
        elif head_sha:
            should_publish = True
            reason = "manifest_missing_sha"

    mirrored_count = 0
    if should_publish:
        mirror_root.mkdir(parents=True, exist_ok=True)
        for item in skills:
            sk_name = item["name"]
            src_dir = repo / "skills" / sk_name
            dst_dir = mirror_root / sk_name
            dst_dir.mkdir(parents=True, exist_ok=True)
            src_md = src_dir / "SKILL.md"
            if src_md.is_file():
                _atomic_write_bytes(dst_dir / "SKILL.md", src_md.read_bytes())
                mirrored_count += 1
            src_plugin = src_dir / ".claude-plugin" / "plugin.json"
            if src_plugin.is_file():
                (dst_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
                _atomic_write_bytes(dst_dir / ".claude-plugin" / "plugin.json", src_plugin.read_bytes())

        manifest_payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "repo": REPO_SLUG,
            "head_sha": head_sha,
            "head_commit_epoch": head_epoch,
            "branch": branch or "main",
            "updated_at": datetime.now(CST).isoformat(timespec="seconds"),
            "updated_by_host": host,
            "skill_count": len(skills),
            "skills": [
                {
                    "name": item["name"],
                    "skill_md_sha256": item["skill_md_sha256"],
                    "lines": item["lines"],
                    "mirror_rel_path": item["mirror_rel_path"],
                }
                for item in skills
            ],
        }
        _atomic_write_json(manifest_path, manifest_payload)

    return {
        "ok": True,
        "published": should_publish,
        "reason": reason,
        "head_sha": head_sha,
        "previous_manifest_sha": existing_sha or None,
        "skill_count": len(skills),
        "mirrored_count": mirrored_count,
        "manifest_path": redact_text(manifest_path),
        "mirror_root": redact_text(mirror_root),
    }


def heal_skill_symlinks(
    *,
    repo_root: Path | None = None,
    client_dirs: Sequence[Path] | None = None,
    sibling_skills_repo: Path | None = None,
    run_pi_harness: bool = True,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    repo = (repo_root or default_repo_root()).expanduser()
    targets = list(client_dirs) if client_dirs is not None else list(default_client_skill_dirs())
    skill_sources = enumerate_all_local_skill_sources(repo, sibling_skills_repo=sibling_skills_repo)
    user_skills_prefix = str((repo / "skills").resolve())

    created: list[str] = []
    updated: list[str] = []
    preserved_external: list[str] = []
    removed_dangling: list[str] = []
    per_dir_counts: dict[str, int] = {}

    for client_dir in targets:
        c_dir = client_dir.expanduser()
        c_dir.mkdir(parents=True, exist_ok=True)

        # Remove dangling symlinks that pointed into deleted user-skills directories
        for existing_entry in sorted(c_dir.iterdir(), key=lambda p: p.name):
            if existing_entry.name.startswith("."):
                continue
            if existing_entry.is_symlink() and not existing_entry.exists():
                raw_target = os.readlink(existing_entry)
                resolved_guess = (existing_entry.parent / raw_target)
                if "user-skills/skills/" in str(raw_target) or str(resolved_guess).startswith(user_skills_prefix):
                    existing_entry.unlink(missing_ok=True)
                    removed_dangling.append(redact_text(existing_entry))

        valid_count = 0
        for skill_name, target_pkg in sorted(skill_sources.items()):
            link_path = c_dir / skill_name
            desired_target = target_pkg
            is_from_user_skills = (repo / "skills" / skill_name / "SKILL.md").is_file()

            if link_path.is_symlink():
                if link_path.exists():
                    current_resolved = link_path.resolve()
                    if current_resolved == desired_target.resolve():
                        valid_count += 1
                        continue
                    # Preserve valid symlinks pointing outside user-skills when skill is not in user-skills,
                    # OR preserve explicit non-user-skills repo links unless dangling.
                    raw_link = os.readlink(link_path)
                    points_into_user_skills = (
                        "user-skills/skills/" in raw_link
                        or str(current_resolved).startswith(user_skills_prefix)
                    )
                    if not points_into_user_skills:
                        preserved_external.append(redact_text(link_path))
                        valid_count += 1
                        continue
                    if is_from_user_skills:
                        link_path.unlink()
                        link_path.symlink_to(desired_target)
                        updated.append(redact_text(link_path))
                        valid_count += 1
                else:
                    link_path.unlink(missing_ok=True)
                    link_path.symlink_to(desired_target)
                    updated.append(redact_text(link_path))
                    valid_count += 1
            elif link_path.exists():
                # Real directory (e.g. .system or synced); never clobber real non-symlink dirs
                valid_count += 1
            else:
                link_path.symlink_to(desired_target)
                created.append(redact_text(link_path))
                valid_count += 1

        per_dir_counts[redact_text(c_dir)] = valid_count

    pi_harness_result: dict[str, Any] = {"ran": False, "ok": True}
    if run_pi_harness:
        pi_script = repo / "skills" / "m2-dispatcher" / "scripts" / "install-pi-harness.sh"
        if pi_script.is_file():
            proc = runner(["bash", str(pi_script)], cwd=repo, timeout=30.0)
            pi_harness_result = {
                "ran": True,
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
            }

    return {
        "ok": True,
        "total_canonical_skills": len(skill_sources),
        "user_skills_count": len(enumerate_repo_skills(repo)),
        "per_client_dir_valid_links": per_dir_counts,
        "created_count": len(created),
        "updated_count": len(updated),
        "removed_dangling_count": len(removed_dangling),
        "preserved_external_count": len(preserved_external),
        "created": created,
        "updated": updated,
        "removed_dangling": removed_dangling,
        "pi_harness": pi_harness_result,
    }


def pull_skills_from_icloud(
    *,
    repo_root: Path | None = None,
    icloud_root: Path | None = None,
    client_dirs: Sequence[Path] | None = None,
    sibling_skills_repo: Path | None = None,
    allow_mirror_fallback: bool = False,
    heal: bool = True,
    run_pi_harness: bool = True,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    repo = (repo_root or default_repo_root()).expanduser()
    icloud = (icloud_root or default_icloud_root()).expanduser()
    manifest = read_sync_manifest(icloud)
    local_sha, _, branch = _git_head_info(repo, ref="HEAD", runner=runner)

    action = "noop_no_manifest"
    git_pulled = False
    mirror_applied = False
    manifest_sha = str((manifest or {}).get("head_sha") or "").strip()

    if manifest and manifest_sha:
        if manifest_sha == local_sha:
            action = "up_to_date"
        elif _git_is_ancestor(repo, manifest_sha, local_sha, runner=runner):
            action = "local_ahead_of_manifest"
        elif branch == "main" and _git_is_clean(repo, runner=runner):
            fetch_proc = runner(["git", "fetch", "origin", "main", "--quiet"], cwd=repo, timeout=45.0)
            pull_proc = runner(["git", "pull", "--ff-only", "origin", "main"], cwd=repo, timeout=45.0)
            if fetch_proc.returncode == 0 and pull_proc.returncode == 0:
                git_pulled = True
                local_sha, _, branch = _git_head_info(repo, ref="HEAD", runner=runner)
                action = "git_ff_pulled"
            elif allow_mirror_fallback and (icloud / "skills-mirror").is_dir():
                for item in manifest.get("skills", []):
                    sk_name = str(item.get("name") or "").strip()
                    rel = str(item.get("mirror_rel_path") or "").strip()
                    if not sk_name or not rel:
                        continue
                    src = icloud / rel
                    if src.is_file():
                        dst = repo / "skills" / sk_name / "SKILL.md"
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        _atomic_write_bytes(dst, src.read_bytes())
                        mirror_applied = True
                action = "mirror_fallback_applied" if mirror_applied else "git_pull_failed"
            else:
                action = "git_pull_failed"
        else:
            action = "skipped_dirty_or_non_main"

    heal_report = (
        heal_skill_symlinks(
            repo_root=repo,
            client_dirs=client_dirs,
            sibling_skills_repo=sibling_skills_repo,
            run_pi_harness=run_pi_harness,
            runner=runner,
        )
        if heal
        else None
    )

    return {
        "ok": True,
        "action": action,
        "git_pulled": git_pulled,
        "mirror_applied": mirror_applied,
        "local_head_sha": local_sha or None,
        "manifest_head_sha": manifest_sha or None,
        "branch": branch or None,
        "heal_symlinks": heal_report,
    }


# ---------------------------------------------------------------------------
# Part 2: Incremental Per-Host Skill Telemetry & M2 Ledger Shards
# ---------------------------------------------------------------------------

def compute_telemetry_event_id(record: Mapping[str, Any]) -> str:
    explicit = str(record.get("event_id") or "").strip()
    if explicit:
        return explicit
    host = normalize_hostname(str(record.get("host") or "local"))
    kind = str(record.get("kind") or record.get("event_type") or "usage").strip()
    sid = str(record.get("session_id") or record.get("route_id") or "").strip()
    turn_id = str(record.get("turn_id") or record.get("call_uid") or record.get("hit_uid") or "").strip()
    ts = str(record.get("ts") or record.get("t") or record.get("timestamp") or "").strip()
    skill = str(record.get("skill") or "").strip()
    tool_or_path = str(
        record.get("script_path")
        or record.get("reference_path")
        or record.get("tool")
        or record.get("req")
        or record.get("model")
        or ""
    ).strip()
    tokens_sig = f"{record.get('in', record.get('input_tokens', 0))}:{record.get('out', record.get('output_tokens', 0))}:{record.get('cache_read', 0)}"
    raw_key = f"{host}|{kind}|{sid}|{turn_id}|{ts}|{skill}|{tool_or_path}|{tokens_sig}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]


def _parse_iso_cst(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST if len(text) == 10 else timezone.utc)
        return dt.astimezone(CST)
    except ValueError:
        return None


def _extract_skills_from_text(text: str, known_skills: set[str]) -> set[str]:
    found: set[str] = set()
    for match in _SKILL_NAME_RE.finditer(text):
        name = match.group("ctx") or match.group("path") or match.group("call")
        if name:
            found.add("team-lead" if name in {"goal-target-task", "goal-task"} else name)
    for pattern in (_REF_PATH_RE, _SCRIPT_PATH_RE):
        for m in pattern.finditer(text):
            sk = m.group("skill")
            found.add("team-lead" if sk in {"goal-target-task", "goal-task"} else sk)
    for sk in known_skills:
        if f"/{sk}" in text or f"skills/{sk}" in text:
            found.add(sk)
    return found


def _collect_local_telemetry_events(
    *,
    host: str,
    known_skills: set[str],
    local_usage_path: Path,
    magpie_usage_path: Path,
    copilot_db_path: Path,
    warehouse_db_path: Path,
    days: int = 30,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(CST) - timedelta(days=max(1, days))
    cutoff_7d = datetime.now(CST) - timedelta(days=7)
    by_id: dict[str, dict[str, Any]] = {}

    def _add(rec: dict[str, Any]) -> None:
        rec["host"] = normalize_hostname(str(rec.get("host") or host))
        eid = compute_telemetry_event_id(rec)
        rec["event_id"] = eid
        by_id.setdefault(eid, rec)

    # 1. Existing ~/.local/share/skill-telemetry/usage.jsonl records
    if local_usage_path.is_file():
        with local_usage_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                stamp = _parse_iso_cst(obj.get("ts") or obj.get("t") or obj.get("timestamp"))
                if stamp and stamp >= cutoff:
                    _add(obj)

    # 2. Gateway usage stream (~/.config/magpie/usage.jsonl, recent 7d window for M3-M5 & S11)
    if magpie_usage_path.is_file():
        try:
            # Read tail efficiently if file is huge, or scan lines within 7d
            with magpie_usage_path.open("rb") as bfh:
                bfh.seek(0, os.SEEK_END)
                fsize = bfh.tell()
                # Read up to last 8 MB to stay fast on multi-ten-MB gateway logs
                offset = max(0, fsize - 8 * 1024 * 1024)
                bfh.seek(offset)
                chunk = bfh.read().decode("utf-8", errors="replace")
                lines = chunk.splitlines()
                if offset > 0 and lines:
                    lines = lines[1:]
            for line in lines:
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                ts_raw = raw.get("t") or raw.get("ts") or raw.get("timestamp")
                stamp = _parse_iso_cst(ts_raw)
                if not stamp or stamp < cutoff_7d:
                    continue
                rec = {
                    "kind": "gateway_usage",
                    "host": host,
                    "ts": stamp.isoformat(),
                    "route_id": raw.get("route_id"),
                    "agent": raw.get("agent"),
                    "req": raw.get("req") or raw.get("model"),
                    "model": raw.get("served") or raw.get("model"),
                    "status": int(raw.get("status") or 200),
                    "in": int(raw.get("in") or 0),
                    "out": int(raw.get("out") or 0),
                    "cache_read": int(raw.get("cache_read") or 0),
                    "tries": int(raw.get("tries") or 1),
                    "retried_ms": int(raw.get("retried_ms") or 0),
                    "ttft_ms": int(raw.get("ttft_ms") or 0),
                }
                _add(rec)
        except OSError:
            pass

    # 3. Warehouse skill turns, script_call, reference_hits
    if warehouse_db_path.is_file():
        cutoff_iso = cutoff.isoformat()
        try:
            with contextlib.closing(sqlite3.connect(f"file:{warehouse_db_path}?mode=ro", uri=True)) as conn:
                tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "fact_turn" in tables:
                    cur = conn.execute(
                        "SELECT turn_uuid, session_id, ts, role, model, attribution_skill, "
                        "input_tokens, output_tokens, cache_read, tool_calls, skill_names "
                        "FROM fact_turn WHERE ts >= ? AND ((attribution_skill IS NOT NULL AND attribution_skill != '') "
                        "OR (skill_names IS NOT NULL AND skill_names != '' AND skill_names != '[]'))",
                        (cutoff.astimezone(timezone.utc).strftime("%Y-%m-%d"),),
                    )
                    for row in cur:
                        turn_uuid, sid, ts_val, role, model, attr_sk, in_t, out_t, cr_t, tools_raw, sk_raw = row
                        stamp = _parse_iso_cst(ts_val)
                        if not stamp or stamp < cutoff:
                            continue
                        skills: set[str] = set()
                        if attr_sk:
                            skills.add("team-lead" if attr_sk in {"goal-target-task", "goal-task"} else str(attr_sk))
                        try:
                            parsed_sk = json.loads(sk_raw) if sk_raw else []
                            if isinstance(parsed_sk, list):
                                for s in parsed_sk:
                                    if s:
                                        skills.add("team-lead" if s in {"goal-target-task", "goal-task"} else str(s))
                        except json.JSONDecodeError:
                            pass
                        try:
                            parsed_tools = json.loads(tools_raw) if tools_raw else []
                        except json.JSONDecodeError:
                            parsed_tools = []
                        tool_list = [str(t) for t in parsed_tools] if isinstance(parsed_tools, list) else []
                        for sk in sorted(skills):
                            _add(
                                {
                                    "kind": "skill_turn",
                                    "host": host,
                                    "channel": "claude",
                                    "session_id": str(sid or ""),
                                    "turn_id": str(turn_uuid or ""),
                                    "ts": stamp.isoformat(),
                                    "skill": sk,
                                    "model": str(model or ""),
                                    "input_tokens": int(in_t or 0),
                                    "output_tokens": int(out_t or 0),
                                    "cache_read": int(cr_t or 0),
                                    "tool_calls": tool_list[:20],
                                }
                            )

                if "script_call" in tables:
                    cur = conn.execute(
                        "SELECT call_uid, source, session_id, turn_uuid, skill, script_path, exit_code, duration_ms, ts "
                        "FROM script_call WHERE ts >= ?",
                        (cutoff.astimezone(timezone.utc).strftime("%Y-%m-%d"),),
                    )
                    for call_uid, src, sid, turn_uuid, sk, sc_path, code, dur, ts_val in cur:
                        stamp = _parse_iso_cst(ts_val)
                        if not stamp or stamp < cutoff or not sk:
                            continue
                        norm_sk = "team-lead" if sk in {"goal-target-task", "goal-task"} else str(sk)
                        _add(
                            {
                                "kind": "script_call",
                                "host": host,
                                "channel": "codex" if "codex" in str(src or "").lower() else "copilot",
                                "call_uid": str(call_uid or ""),
                                "session_id": str(sid or ""),
                                "turn_id": str(turn_uuid or ""),
                                "ts": stamp.isoformat(),
                                "skill": norm_sk,
                                "script_path": redact_text(sc_path),
                                "exit_code": int(code) if code is not None else 0,
                                "duration_ms": float(dur) if dur is not None else None,
                            }
                        )

                if "reference_hits" in tables:
                    cur = conn.execute(
                        "SELECT hit_uid, source, session_id, turn_uuid, skill, reference_path, byte_len, ts "
                        "FROM reference_hits WHERE ts >= ?",
                        (cutoff.astimezone(timezone.utc).strftime("%Y-%m-%d"),),
                    )
                    for hit_uid, src, sid, turn_uuid, sk, ref_path, b_len, ts_val in cur:
                        stamp = _parse_iso_cst(ts_val)
                        if not stamp or stamp < cutoff or not sk:
                            continue
                        norm_sk = "team-lead" if sk in {"goal-target-task", "goal-task"} else str(sk)
                        _add(
                            {
                                "kind": "reference_hit",
                                "host": host,
                                "channel": "codex" if "codex" in str(src or "").lower() else "copilot",
                                "hit_uid": str(hit_uid or ""),
                                "session_id": str(sid or ""),
                                "turn_id": str(turn_uuid or ""),
                                "ts": stamp.isoformat(),
                                "skill": norm_sk,
                                "reference_path": redact_text(ref_path),
                                "byte_len": int(b_len or 0),
                            }
                        )
        except sqlite3.Error:
            pass

    # 4. Recent Copilot CLI turns (~/.copilot/session-store.db)
    if copilot_db_path.is_file():
        try:
            with contextlib.closing(sqlite3.connect(f"file:{copilot_db_path}?mode=ro", uri=True)) as conn:
                tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "turns" in tables:
                    cur = conn.execute(
                        "SELECT session_id, turn_index, user_message, assistant_response, timestamp "
                        "FROM turns WHERE timestamp >= ?",
                        (cutoff.astimezone(timezone.utc).strftime("%Y-%m-%d"),),
                    )
                    for sid, turn_idx, u_msg, a_msg, ts_val in cur:
                        stamp = _parse_iso_cst(ts_val)
                        if not stamp or stamp < cutoff:
                            continue
                        blob = f"{u_msg or ''}\n{a_msg or ''}"
                        matched = _extract_skills_from_text(blob, known_skills)
                        for sk in sorted(matched):
                            _add(
                                {
                                    "kind": "skill_turn",
                                    "host": host,
                                    "channel": "copilot",
                                    "session_id": str(sid or ""),
                                    "turn_id": f"turn-{turn_idx}",
                                    "ts": stamp.isoformat(),
                                    "skill": sk,
                                    "input_tokens": 0,
                                    "output_tokens": 0,
                                    "cache_read": 0,
                                    "tool_calls": [],
                                }
                            )
                        for m in _REF_PATH_RE.finditer(blob):
                            sk = "team-lead" if m.group("skill") in {"goal-target-task", "goal-task"} else m.group("skill")
                            _add(
                                {
                                    "kind": "reference_hit",
                                    "host": host,
                                    "channel": "copilot",
                                    "session_id": str(sid or ""),
                                    "turn_id": f"turn-{turn_idx}",
                                    "ts": stamp.isoformat(),
                                    "skill": sk,
                                    "reference_path": f"skills/{sk}/references/{m.group('ref')}",
                                    "byte_len": 0,
                                }
                            )
                        for m in _SCRIPT_PATH_RE.finditer(blob):
                            sk = "team-lead" if m.group("skill") in {"goal-target-task", "goal-task"} else m.group("skill")
                            _add(
                                {
                                    "kind": "script_call",
                                    "host": host,
                                    "channel": "copilot",
                                    "session_id": str(sid or ""),
                                    "turn_id": f"turn-{turn_idx}",
                                    "ts": stamp.isoformat(),
                                    "skill": sk,
                                    "script_path": f"skills/{sk}/scripts/{m.group('script')}",
                                    "exit_code": 0,
                                    "duration_ms": None,
                                }
                            )
        except sqlite3.Error:
            pass

    ordered = sorted(
        by_id.values(),
        key=lambda item: (str(item.get("ts") or ""), str(item.get("event_id") or "")),
    )
    return ordered


def _collect_m2_ledger_summaries(handoff_root: Path, *, host: str, days: int = 14) -> list[dict[str, Any]]:
    if not handoff_root.is_dir():
        return []
    cutoff_epoch = (datetime.now(CST) - timedelta(days=max(1, days))).timestamp()
    rows: list[dict[str, Any]] = []
    for ledger_path in sorted(handoff_root.glob("*/ledger.md")):
        try:
            st = ledger_path.stat()
        except OSError:
            continue
        if st.st_mtime < cutoff_epoch:
            continue
        try:
            raw_text = ledger_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        goal_id = ledger_path.parent.name
        last_evt = ""
        for line in reversed(raw_text.splitlines()):
            if "EVT=" in line:
                last_evt = redact_text(line.strip())[:240]
                break
        rows.append(
            {
                "goal_id": goal_id,
                "host": host,
                "updated_at": datetime.fromtimestamp(st.st_mtime, CST).isoformat(timespec="seconds"),
                "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16],
                "is_done": "EVT=GOAL_DONE" in raw_text,
                "last_event": last_evt,
            }
        )
    return rows


def _read_existing_event_ids(jsonl_path: Path) -> tuple[set[str], list[dict[str, Any]]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    if not jsonl_path.is_file():
        return seen, rows
    with jsonl_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            eid = compute_telemetry_event_id(obj)
            obj["event_id"] = eid
            if eid not in seen:
                seen.add(eid)
                rows.append(obj)
    return seen, rows


def export_telemetry_shards(
    *,
    repo_root: Path | None = None,
    icloud_root: Path | None = None,
    hostname: str | None = None,
    local_usage_path: Path | None = None,
    magpie_usage_path: Path | None = None,
    copilot_db_path: Path | None = None,
    warehouse_db_path: Path | None = None,
    handoff_root: Path | None = None,
    days: int = 30,
) -> dict[str, Any]:
    repo = (repo_root or default_repo_root()).expanduser()
    icloud = (icloud_root or default_icloud_root()).expanduser()
    host = normalize_hostname(hostname)
    loc_usage = (local_usage_path or default_local_telemetry_path()).expanduser()
    mp_usage = (magpie_usage_path or default_magpie_usage_path()).expanduser()
    cp_db = (copilot_db_path or default_copilot_db_path()).expanduser()
    wh_db = (warehouse_db_path or default_warehouse_db_path()).expanduser()
    h_root = (handoff_root or default_handoff_root()).expanduser()

    known_skills = set(enumerate_all_local_skill_sources(repo).keys())
    collected = _collect_local_telemetry_events(
        host=host,
        known_skills=known_skills,
        local_usage_path=loc_usage,
        magpie_usage_path=mp_usage,
        copilot_db_path=cp_db,
        warehouse_db_path=wh_db,
        days=days,
    )

    # 1. Incrementally update canonical local ~/.local/share/skill-telemetry/usage.jsonl
    loc_usage.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(loc_usage.with_name(loc_usage.name + ".lock")):
        local_seen, _ = _read_existing_event_ids(loc_usage)
        new_local = [r for r in collected if r["event_id"] not in local_seen]
        if new_local or not loc_usage.is_file():
            with loc_usage.open("a", encoding="utf-8") as fh:
                for row in new_local:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        total_local_records = len(local_seen) + len(new_local)

    # 2. Incrementally mirror host shard to <icloud_root>/telemetry-shards/<host>-skill-usage.jsonl
    shards_dir = icloud / "telemetry-shards"
    shards_dir.mkdir(parents=True, exist_ok=True)
    shard_path = shards_dir / f"{host}-skill-usage.jsonl"
    watermark_path = shards_dir / f".{host}-watermark.json"

    shard_seen, shard_existing = _read_existing_event_ids(shard_path)
    new_shard = [r for r in collected if r["event_id"] not in shard_seen]
    if new_shard or not shard_path.is_file():
        merged_shard = shard_existing + new_shard
        payload_bytes = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged_shard).encode("utf-8")
        _atomic_write_bytes(shard_path, payload_bytes)
    total_shard_records = len(shard_seen) + len(new_shard)

    latest_ts = max((str(r.get("ts") or "") for r in collected), default=None)
    _atomic_write_json(
        watermark_path,
        {
            "host": host,
            "updated_at": datetime.now(CST).isoformat(timespec="seconds"),
            "latest_event_ts": latest_ts,
            "total_records": total_shard_records,
            "appended_records": len(new_shard),
        },
    )

    # 3. Mirror M2/M1 ledger summaries to <icloud_root>/m2-ledger-shards/<host>-ledgers.jsonl
    ledger_shards_dir = icloud / "m2-ledger-shards"
    ledger_shards_dir.mkdir(parents=True, exist_ok=True)
    ledger_shard_path = ledger_shards_dir / f"{host}-ledgers.jsonl"
    ledger_rows = _collect_m2_ledger_summaries(h_root, host=host)
    ledger_bytes = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ledger_rows).encode("utf-8")
    _atomic_write_bytes(ledger_shard_path, ledger_bytes)

    return {
        "ok": True,
        "host": host,
        "local_usage_path": redact_text(loc_usage),
        "local_total_records": total_local_records,
        "local_appended_records": len(new_local),
        "shard_path": redact_text(shard_path),
        "shard_total_records": total_shard_records,
        "shard_appended_records": len(new_shard),
        "ledger_shard_path": redact_text(ledger_shard_path),
        "ledger_goal_count": len(ledger_rows),
        "latest_event_ts": latest_ts,
    }


# ---------------------------------------------------------------------------
# LaunchAgent & Git Hook Installation
# ---------------------------------------------------------------------------

def render_launchd_plist(
    *,
    script_path: Path | None = None,
    icloud_root: Path | None = None,
    repo_root: Path | None = None,
    label: str = DEFAULT_LAUNCHD_LABEL,
    start_interval: int = 300,
) -> bytes:
    repo = (repo_root or default_repo_root()).expanduser()
    icloud = (icloud_root or default_icloud_root()).expanduser()
    resolved_script = script_path or (repo / "skills" / "team-lead" / "scripts" / "icloud_skills_sync.py")
    log_dir = Path.home() / "Library" / "Logs"
    manifest_watch = icloud / "skills-sync-manifest.json"
    plist_dict: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": [
            sys.executable or "/usr/bin/python3",
            str(resolved_script),
            "sync",
            "--json",
        ],
        "StartInterval": int(start_interval),
        "WatchPaths": [
            str(icloud),
            str(manifest_watch),
        ],
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(log_dir / f"{label}.out.log"),
        "StandardErrorPath": str(log_dir / f"{label}.err.log"),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }
    return plistlib.dumps(plist_dict, fmt=plistlib.FMT_XML, sort_keys=True)


def install_launchd_agent(
    *,
    plist_path: Path | None = None,
    script_path: Path | None = None,
    icloud_root: Path | None = None,
    repo_root: Path | None = None,
    label: str = DEFAULT_LAUNCHD_LABEL,
    start_interval: int = 300,
    load_agent: bool = True,
    install_post_merge_hook: bool = True,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    repo = (repo_root or default_repo_root()).expanduser()
    icloud = (icloud_root or default_icloud_root()).expanduser()
    icloud.mkdir(parents=True, exist_ok=True)

    target_plist = (
        plist_path.expanduser()
        if plist_path is not None
        else Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    )
    canonical_repo = Path.home() / "Development" / "user-skills"
    effective_repo = canonical_repo if (canonical_repo / "skills").is_dir() else repo
    canonical_script = effective_repo / "skills" / "team-lead" / "scripts" / "icloud_skills_sync.py"
    chosen_script = script_path or canonical_script

    plist_bytes = render_launchd_plist(
        script_path=chosen_script,
        icloud_root=icloud,
        repo_root=effective_repo,
        label=label,
        start_interval=start_interval,
    )
    _atomic_write_bytes(target_plist, plist_bytes)

    loaded = False
    boot_msg = ""
    if load_agent and sys.platform == "darwin":
        uid = os.getuid()
        domain = f"gui/{uid}"
        runner(["launchctl", "bootout", f"{domain}/{label}"], timeout=10.0)
        boot_proc = runner(["launchctl", "bootstrap", domain, str(target_plist)], timeout=10.0)
        if boot_proc.returncode == 0:
            loaded = True
        else:
            # Fallback to legacy load -w if bootstrap reports already loaded or session constraint
            fallback = runner(["launchctl", "load", "-w", str(target_plist)], timeout=10.0)
            loaded = fallback.returncode == 0
            boot_msg = (boot_proc.stderr or fallback.stderr or "").strip()

    hook_installed = False
    if install_post_merge_hook and (effective_repo / ".git").exists():
        hooks_dir = effective_repo / ".githooks"
        if hooks_dir.is_dir():
            post_merge = hooks_dir / "post-merge"
            marker = "icloud_skills_sync.py"
            hook_snippet = (
                '#!/usr/bin/env bash\n'
                'set -euo pipefail\n'
                'REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\n'
                'SYNC_SCRIPT="$REPO_ROOT/skills/team-lead/scripts/icloud_skills_sync.py"\n'
                'if [[ -f "$SYNC_SCRIPT" ]]; then\n'
                '  python3 "$SYNC_SCRIPT" sync --quiet >/dev/null 2>&1 || true\n'
                'fi\n'
            )
            if not post_merge.exists():
                _atomic_write_bytes(post_merge, hook_snippet.encode("utf-8"))
                post_merge.chmod(0o755)
                hook_installed = True
            else:
                existing_txt = post_merge.read_text(encoding="utf-8", errors="replace")
                if marker not in existing_txt:
                    updated_txt = existing_txt.rstrip() + (
                        '\n\nSYNC_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/skills/team-lead/scripts/icloud_skills_sync.py"\n'
                        'if [[ -f "$SYNC_SCRIPT" ]]; then\n'
                        '  python3 "$SYNC_SCRIPT" sync --quiet >/dev/null 2>&1 || true\n'
                        'fi\n'
                    )
                    _atomic_write_bytes(post_merge, updated_txt.encode("utf-8"))
                    post_merge.chmod(0o755)
                    hook_installed = True

    return {
        "ok": True,
        "label": label,
        "plist_path": redact_text(target_plist),
        "loaded": loaded,
        "bootstrap_note": redact_text(boot_msg) if boot_msg else None,
        "post_merge_hook_updated": hook_installed,
    }


def get_sync_status(
    *,
    repo_root: Path | None = None,
    icloud_root: Path | None = None,
    client_dirs: Sequence[Path] | None = None,
    local_usage_path: Path | None = None,
    label: str = DEFAULT_LAUNCHD_LABEL,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    repo = (repo_root or default_repo_root()).expanduser()
    icloud = (icloud_root or default_icloud_root()).expanduser()
    targets = list(client_dirs) if client_dirs is not None else list(default_client_skill_dirs())
    loc_usage = (local_usage_path or default_local_telemetry_path()).expanduser()

    local_sha, _, branch = _git_head_info(repo, ref="HEAD", runner=runner)
    manifest = read_sync_manifest(icloud)
    manifest_sha = str((manifest or {}).get("head_sha") or "").strip()

    shards_dir = icloud / "telemetry-shards"
    shard_files = sorted(shards_dir.glob("*-skill-usage.jsonl")) if shards_dir.is_dir() else []
    shard_summaries = []
    for sf in shard_files:
        try:
            line_cnt = sum(1 for ln in sf.open("r", encoding="utf-8", errors="replace") if ln.strip())
        except OSError:
            line_cnt = 0
        shard_summaries.append({"file": sf.name, "records": line_cnt})

    skill_sources = enumerate_all_local_skill_sources(repo)
    symlink_health: dict[str, Any] = {}
    for c_dir in targets:
        expanded = c_dir.expanduser()
        missing = [
            sk for sk in sorted(skill_sources)
            if not (expanded / sk).exists()
        ]
        symlink_health[redact_text(expanded)] = {
            "exists": expanded.is_dir(),
            "missing_skills": missing,
            "ok": len(missing) == 0,
        }

    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    launchd_loaded = False
    if sys.platform == "darwin":
        proc = runner(["launchctl", "print", f"gui/{os.getuid()}/{label}"], timeout=5.0)
        launchd_loaded = proc.returncode == 0

    local_records = 0
    if loc_usage.is_file():
        try:
            local_records = sum(1 for ln in loc_usage.open("r", encoding="utf-8", errors="replace") if ln.strip())
        except OSError:
            local_records = 0

    return {
        "ok": True,
        "host": normalize_hostname(),
        "repo_root": redact_text(repo),
        "branch": branch or None,
        "local_head_sha": local_sha or None,
        "icloud_root": redact_text(icloud),
        "manifest_present": manifest is not None,
        "manifest_head_sha": manifest_sha or None,
        "in_sync": bool(local_sha and manifest_sha and local_sha == manifest_sha),
        "manifest_skill_count": int((manifest or {}).get("skill_count") or 0),
        "local_skill_count": len(enumerate_repo_skills(repo)),
        "canonical_total_skills": len(skill_sources),
        "symlink_health": symlink_health,
        "local_telemetry_records": local_records,
        "telemetry_shards": shard_summaries,
        "launchd": {
            "label": label,
            "plist_present": plist_path.is_file(),
            "loaded": launchd_loaded,
        },
    }


def run_full_sync(
    *,
    repo_root: Path | None = None,
    icloud_root: Path | None = None,
    hostname: str | None = None,
    client_dirs: Sequence[Path] | None = None,
    sibling_skills_repo: Path | None = None,
    local_usage_path: Path | None = None,
    magpie_usage_path: Path | None = None,
    copilot_db_path: Path | None = None,
    warehouse_db_path: Path | None = None,
    handoff_root: Path | None = None,
    install_launchd: bool = False,
    plist_path: Path | None = None,
    load_launchd: bool = True,
    run_pi_harness: bool = True,
    days: int = 30,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    repo = (repo_root or default_repo_root()).expanduser()
    icloud = (icloud_root or default_icloud_root()).expanduser()

    pull_res = pull_skills_from_icloud(
        repo_root=repo,
        icloud_root=icloud,
        client_dirs=client_dirs,
        sibling_skills_repo=sibling_skills_repo,
        heal=False,
        run_pi_harness=False,
        runner=runner,
    )
    pub_res = publish_skills_to_icloud(
        repo_root=repo,
        icloud_root=icloud,
        hostname=hostname,
        runner=runner,
    )
    heal_res = heal_skill_symlinks(
        repo_root=repo,
        client_dirs=client_dirs,
        sibling_skills_repo=sibling_skills_repo,
        run_pi_harness=run_pi_harness,
        runner=runner,
    )
    telem_res = export_telemetry_shards(
        repo_root=repo,
        icloud_root=icloud,
        hostname=hostname,
        local_usage_path=local_usage_path,
        magpie_usage_path=magpie_usage_path,
        copilot_db_path=copilot_db_path,
        warehouse_db_path=warehouse_db_path,
        handoff_root=handoff_root,
        days=days,
    )
    launchd_res = (
        install_launchd_agent(
            plist_path=plist_path,
            icloud_root=icloud,
            repo_root=repo,
            load_agent=load_launchd,
            runner=runner,
        )
        if install_launchd
        else None
    )

    return {
        "ok": True,
        "host": normalize_hostname(hostname),
        "pull": pull_res,
        "publish": pub_res,
        "heal_symlinks": heal_res,
        "telemetry": telem_res,
        "launchd": launchd_res,
    }


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", type=Path, default=None, help="Override user-skills checkout root")
    parser.add_argument("--icloud-root", type=Path, default=None, help="Override iCloud AIDashSync directory")
    parser.add_argument("--hostname", default=None, help="Override normalized host identifier")
    parser.add_argument("--json", action="store_true", help="Emit JSON output (default)")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout on success")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="icloud_skills_sync.py",
        description="Cross-device iCloud auto-sync for M2 skills definitions, symlinks & skill telemetry.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_pub = sub.add_parser("publish", help="Publish local user-skills manifest and SKILL.md mirror to iCloud")
    _add_common_args(p_pub)
    p_pub.add_argument("--force", action="store_true", help="Force mirror + manifest rewrite")

    p_pull = sub.add_parser("pull", help="Pull newer skills commit from origin/main (or mirror) and heal symlinks")
    _add_common_args(p_pull)
    p_pull.add_argument("--allow-mirror-fallback", action="store_true")
    p_pull.add_argument("--no-heal", action="store_true")
    p_pull.add_argument("--no-pi-harness", action="store_true")

    p_heal = sub.add_parser("heal-symlinks", help="Idempotently heal ~/.agents, ~/.copilot, ~/.claude, ~/.codex skill symlinks")
    _add_common_args(p_heal)
    p_heal.add_argument("--client-dir", action="append", type=Path, default=None, help="Override target client skill dir(s)")
    p_heal.add_argument("--no-pi-harness", action="store_true")

    p_exp = sub.add_parser("export-telemetry", help="Incrementally export local skill usage telemetry shard to iCloud")
    _add_common_args(p_exp)
    p_exp.add_argument("--local-usage-path", type=Path, default=None)
    p_exp.add_argument("--magpie-usage-path", type=Path, default=None)
    p_exp.add_argument("--copilot-db-path", type=Path, default=None)
    p_exp.add_argument("--warehouse-db-path", type=Path, default=None)
    p_exp.add_argument("--handoff-root", type=Path, default=None)
    p_exp.add_argument("--days", type=int, default=30)

    p_sync = sub.add_parser("sync", help="Run pull + publish + heal-symlinks + export-telemetry")
    _add_common_args(p_sync)
    p_sync.add_argument("--client-dir", action="append", type=Path, default=None)
    p_sync.add_argument("--local-usage-path", type=Path, default=None)
    p_sync.add_argument("--magpie-usage-path", type=Path, default=None)
    p_sync.add_argument("--copilot-db-path", type=Path, default=None)
    p_sync.add_argument("--warehouse-db-path", type=Path, default=None)
    p_sync.add_argument("--handoff-root", type=Path, default=None)
    p_sync.add_argument("--days", type=int, default=30)
    p_sync.add_argument("--install-launchd", action="store_true", help="Also install & bootstrap LaunchAgent")
    p_sync.add_argument("--plist-path", type=Path, default=None)
    p_sync.add_argument("--no-load-launchd", action="store_true")
    p_sync.add_argument("--no-pi-harness", action="store_true")

    p_ld = sub.add_parser("install-launchd", help="Install ~/Library/LaunchAgents/com.tianpli.skills.icloud-sync.plist")
    _add_common_args(p_ld)
    p_ld.add_argument("--plist-path", type=Path, default=None)
    p_ld.add_argument("--label", default=DEFAULT_LAUNCHD_LABEL)
    p_ld.add_argument("--start-interval", type=int, default=300)
    p_ld.add_argument("--no-load", action="store_true")
    p_ld.add_argument("--no-hook", action="store_true")

    p_st = sub.add_parser("status", help="Inspect sync manifest, symlink health, telemetry shards, and LaunchAgent")
    _add_common_args(p_st)
    p_st.add_argument("--client-dir", action="append", type=Path, default=None)
    p_st.add_argument("--local-usage-path", type=Path, default=None)
    p_st.add_argument("--label", default=DEFAULT_LAUNCHD_LABEL)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)

    if ns.subcommand == "publish":
        res = publish_skills_to_icloud(
            repo_root=ns.repo_root,
            icloud_root=ns.icloud_root,
            hostname=ns.hostname,
            force=ns.force,
        )
    elif ns.subcommand == "pull":
        res = pull_skills_from_icloud(
            repo_root=ns.repo_root,
            icloud_root=ns.icloud_root,
            allow_mirror_fallback=ns.allow_mirror_fallback,
            heal=not ns.no_heal,
            run_pi_harness=not ns.no_pi_harness,
        )
    elif ns.subcommand == "heal-symlinks":
        res = heal_skill_symlinks(
            repo_root=ns.repo_root,
            client_dirs=ns.client_dir,
            run_pi_harness=not ns.no_pi_harness,
        )
    elif ns.subcommand == "export-telemetry":
        res = export_telemetry_shards(
            repo_root=ns.repo_root,
            icloud_root=ns.icloud_root,
            hostname=ns.hostname,
            local_usage_path=ns.local_usage_path,
            magpie_usage_path=ns.magpie_usage_path,
            copilot_db_path=ns.copilot_db_path,
            warehouse_db_path=ns.warehouse_db_path,
            handoff_root=ns.handoff_root,
            days=ns.days,
        )
    elif ns.subcommand == "sync":
        res = run_full_sync(
            repo_root=ns.repo_root,
            icloud_root=ns.icloud_root,
            hostname=ns.hostname,
            client_dirs=ns.client_dir,
            local_usage_path=ns.local_usage_path,
            magpie_usage_path=ns.magpie_usage_path,
            copilot_db_path=ns.copilot_db_path,
            warehouse_db_path=ns.warehouse_db_path,
            handoff_root=ns.handoff_root,
            install_launchd=ns.install_launchd,
            plist_path=ns.plist_path,
            load_launchd=not ns.no_load_launchd,
            run_pi_harness=not ns.no_pi_harness,
            days=ns.days,
        )
    elif ns.subcommand == "install-launchd":
        res = install_launchd_agent(
            plist_path=ns.plist_path,
            icloud_root=ns.icloud_root,
            repo_root=ns.repo_root,
            label=ns.label,
            start_interval=ns.start_interval,
            load_agent=not ns.no_load,
            install_post_merge_hook=not ns.no_hook,
        )
    elif ns.subcommand == "status":
        res = get_sync_status(
            repo_root=ns.repo_root,
            icloud_root=ns.icloud_root,
            client_dirs=ns.client_dir,
            local_usage_path=ns.local_usage_path,
            label=ns.label,
        )
    else:
        parser.error(f"Unknown subcommand: {ns.subcommand}")
        return 2

    if not getattr(ns, "quiet", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
