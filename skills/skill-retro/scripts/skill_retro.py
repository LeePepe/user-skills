#!/usr/bin/env python3
"""4-Layer Progressive Self-Evolution & Wide-Probe Multi-Angle Skill Diagnostic (`skill-retro`).

Implements the Progressive Self-Evolution Blueprint across:
  - Layer 0: Direction & Trigger Guardrails (trigger misses / domain edits without loading skill,
             0-action empty loads, directional drift vs healthy R1->R2 convergence)
  - Layer 1: Reference Drawer Metabolism (Context-Aware cross-check against scenario/domain
             occurrence: hot_drawer, on_demand_correction_drawer, unwired_disconnected_drawer
             where domain/script failed exit=1 without reading the drawer, true_zero_hit_deadwood,
             dormant_scenario_drawer)
  - Layer 2: Script Efficacy & Multi-Angle Speed Diagnosis (true_catcher fail->pass,
             ceremonial_rubber_stamp 100% exit=0 with escapes or slow runtime,
             rigid_roadblock exit_code=2 CLI argument friction with exact failing command samples,
             latency_sink >180s or P50/max bottleneck with multi-angle attribution:
             external_wait vs serial_queuing vs nested_reruns vs heavy_full_scan + 4 speedup axes)
  - Layer 3: Wild Pattern Crystallization & Cross-Session Taste/Eval (repeated manual shell
             commands >=5x suitable for soft script crystallization, preview-selection 2+1+1
             diversity/taste feedback, and Fast Flywheel scope-triage vs Slow Flywheel cohort gate)
"""

from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import contextlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import fcntl
import fnmatch
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, Sequence

CST = timezone(timedelta(hours=8))

DEFAULT_AIDATA_DIR = Path.home() / "Development" / "AIDash" / "aidata"
DEFAULT_WAREHOUSE_DB = DEFAULT_AIDATA_DIR / "L3_merge" / "warehouse.db"
DEFAULT_PROPOSALS_PATH = DEFAULT_AIDATA_DIR / "L5_apps" / "digest" / "state" / "proposals.jsonl"
DEFAULT_MAGPIE_USAGE = Path.home() / ".config" / "magpie" / "usage.jsonl"
DEFAULT_COPILOT_DB = Path.home() / ".copilot" / "session-store.db"
DEFAULT_COPILOT_SESSIONS_DIR = Path.home() / ".copilot" / "session-state"
DEFAULT_USAGE_JSONL = Path.home() / ".local" / "share" / "skill-telemetry" / "usage.jsonl"
DEFAULT_SKILL_TELEMETRY_USAGE_JSONL = DEFAULT_USAGE_JSONL
DEFAULT_DSH_SESSIONS = Path.home() / ".dsh" / "sessions"
DEFAULT_HANDOFF_ROOT = Path.home() / "Development" / "handoff"
DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UPTAKE_MAP_PATH = Path(__file__).resolve().parents[1] / "references" / "uptake_map.json"
DEFAULT_DEV_ROOT = Path.home() / "Development"
DEFAULT_ICLOUD_SYNC_DIR = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "AIDashSync"
DEFAULT_ICLOUD_TELEMETRY_SHARDS_DIR = DEFAULT_ICLOUD_SYNC_DIR / "telemetry-shards"
DEFAULT_ICLOUD_SKILLS_ROI_PATH = DEFAULT_ICLOUD_SYNC_DIR / "jobs" / "aidata-digest" / "skills-roi.json"
DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH = (
    Path.home() / "Library" / "Application Support" / "AIDash" / "jobs" / "aidata-digest" / "skills-roi.json"
)
CANONICAL_ASPECTS: tuple[str, ...] = ("l0", "l1", "l2", "l3", "creative", "eval")
_ASPECT_ALIASES: dict[str, str] = {
    "l0": "l0",
    "layer0": "l0",
    "direction": "l0",
    "triggers": "l0",
    "dim1": "l0",
    "l1": "l1",
    "layer1": "l1",
    "drawers": "l1",
    "references": "l1",
    "dim2": "l1",
    "l2": "l2",
    "layer2": "l2",
    "scripts": "l2",
    "speed": "l2",
    "latency": "l2",
    "dim3": "l2",
    "l3": "l3",
    "layer3": "l3",
    "wild": "l3",
    "dim4": "l3",
    "creative": "creative",
    "spectrum": "creative",
    "persona": "creative",
    "2+1+1": "creative",
    "dim5": "creative",
    "eval": "eval",
    "flywheel": "eval",
    "triage": "eval",
    "dim6": "eval",
    "all": "all",
}

SLOW_FLYWHEEL_COHORT_MIN_SESSIONS = 50
WILD_PATTERN_MIN_COUNT = 5
LATENCY_SINK_MAX_MS = 180_000.0
LATENCY_SINK_P50_MS = 30_000.0
LATENCY_SINK_MIN_CALLS_FOR_P50 = 3

_SKILL_CONTEXT_RE = re.compile(
    r"<skill-context\s+name=[\"'](?P<ctx>[A-Za-z0-9_-]+)[\"']"
    r"|(?<![A-Za-z0-9_-])(?:/)?skills/(?P<path>[A-Za-z0-9_-]+)/SKILL\.md"
    r"|\bskill\s*[:=]\s*[\"'](?P<call>[A-Za-z0-9_-]+)[\"']"
)
_REF_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:/)?skills/(?P<skill>[A-Za-z0-9_-]+)/references/(?P<ref>[A-Za-z0-9_./-]+\.(?:md|json|yaml|yml))"
)
_SKILL_SCRIPT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:[^\s\"']+/)?skills/(?P<skill>[A-Za-z0-9_-]+)/scripts/(?P<script>[A-Za-z0-9_./-]+\.(?:py|sh))"
)
_REPO_VERIFY_SCRIPT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:\./|(?:[^\s\"']+/)?)scripts/(?P<script>verify(?:\s+--all)?|test_[A-Za-z0-9_.-]+\.py)"
)
_SHELL_START_RE = re.compile(r"<command (?:started in background )?with shellId:\s*([^\s>]+)")
_SHELL_COMPLETE_RE = re.compile(r"<shellId:\s*([^\s>]+)\s+completed with exit code\s+(-?\d+)>")
_HANDOFF_GOAL_RE = re.compile(r"handoff/([A-Za-z0-9_-]+)(?:/|[\s\"'])")
_SELECTION_FILE_RE = re.compile(r"^preview-selection(?:-r\d+)?\.json$")

_EXTERNAL_WAIT_SCRIPT_HINTS = (
    "ci-monitor.py",
    "goal-supervisor.py",
    "linear-sync.py",
    "multica_skills_autopilot.py",
    "multica_vpn_bridge.py",
    "herdr-dispatch.sh",
    "trace_backfill_exporter.py",
)
_SERIAL_RUNNER_HINTS = (
    "scripts/verify",
    "unittest discover",
    "skill_contract.py eval --all",
    "skill_contract.py audit --all",
)
_DOMAIN_HINTS: tuple[tuple[str, Callable[[set[str], set[str]], bool]], ...] = (
    (
        "my-designer",
        lambda files, cmds: any(
            f.endswith((".swift", ".tsx", ".jsx", ".css", ".scss", ".html"))
            and any(tok in f.lower() for tok in ("view", "screen", "card", "sheet", "studio", "dashboard", "components/", "ui/"))
            for f in files
        )
        or any("design.md" in f.lower() for f in files),
    ),
    (
        "design-studio",
        lambda files, cmds: any("design_catalog.py" in c or "--measure-palette" in c or "remotion" in c.lower() for c in cmds)
        or any("taxonomy-v2" in f.lower() or "design-vault" in f.lower() for f in files),
    ),
    (
        "aidash-metrics",
        lambda files, cmds: any("aidata/" in f and f.endswith((".sql", ".py")) for f in files),
    ),
    (
        "skill-contract",
        lambda files, cmds: any(
            ("skills/" in f and f.endswith("SKILL.md")) or f.endswith("eval-cases.json")
            for f in files
        ),
    ),
    (
        "skill-retro",
        lambda files, cmds: any("skills/skill-retro/" in f or "test_skill_retro" in f for f in files)
        or any("skill_retro.py" in c for c in cmds),
    ),
    (
        "xcode-cli",
        lambda files, cmds: any("xcodebuild" in c or "xcrun simctl" in c for c in cmds),
    ),
    (
        "ios-architecture-testing",
        lambda files, cmds: any(
            f.endswith(".swift") and any(tok in f.lower() for tok in ("schema", "migration", "router", "coordinator", "modelcontainer"))
            for f in files
        ),
    ),
    (
        "ios-concurrency-swiftui",
        lambda files, cmds: any("audit_concurrency_swiftui.py" in c for c in cmds),
    ),
    (
        "ios-debugging-performance",
        lambda files, cmds: any("audit_debug_perf_release.py" in c or "xctrace" in c for c in cmds),
    ),
    (
        "repo-kit",
        lambda files, cmds: any(
            f.endswith(("tech-context.md", "repo-contract.json", ".githooks/pre-push", "scripts/verify"))
            or "/.githooks/" in f
            or "/scripts/hooks/" in f
            for f in files
        )
        or any("repo_gate_auditor.py" in c or "shared-ci/scripts/context/" in c for c in cmds),
    ),
    (
        "codebase-design",
        lambda files, cmds: any(f.endswith(("CONTEXT.md", "ADR.md")) or "/adr/" in f.lower() for f in files),
    ),
    (
        "code-review",
        lambda files, cmds: any("discover_review_context.py" in c for c in cmds),
    ),
    (
        "team-lead",
        lambda files, cmds: any(
            tok in c for c in cmds for tok in ("goal-supervisor.py", "goal-queue.py", "ci-monitor.py", "feedback_guard.py", "herdr-dispatch.sh")
        )
        or any("handoff/" in f and f.endswith("ledger.md") for f in files),
    ),
    (
        "m2-dispatcher",
        lambda files, cmds: any("m2_lifecycle.py" in c for c in cmds),
    ),
    (
        "personal-m2-assistant",
        lambda files, cmds: any("m2-router.py" in c for c in cmds),
    ),
    (
        "linear-board",
        lambda files, cmds: any("daily-brief.py" in c or "linear_board" in c for c in cmds),
    ),
    (
        "issue-brief",
        lambda files, cmds: any("verify_issue_format.py" in c or "multica-cursor:" in c for c in cmds),
    ),
    (
        "retro-loop",
        lambda files, cmds: any("retro_story_engine.py" in c for c in cmds)
        or any("proposals.jsonl" in f for f in files),
    ),
    (
        "story-craft",
        lambda files, cmds: any("story-craft" in f or "narrative" in f.lower() for f in files),
    ),
    (
        "layered-data-warehouse",
        lambda files, cmds: any(any(layer in f for layer in ("L1_raw", "L2_clean", "L3_model", "L4_serve")) for f in files),
    ),
    (
        "howto",
        lambda files, cmds: any("howto_guard.py" in c for c in cmds) or any("how-to-nodes.md" in f for f in files),
    ),
)

ACI_OUTPUT_BLOAT_CHARS = 20_480
_BLOAT_MARKERS = (
    "Output too large to read at once",
    "File too large to read at once",
    "saved to temporary file",
    "output exceeded maximum length",
)

_SECRET_SCRUB_RE = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._-]+|gh[pousr]_[A-Za-z0-9_]+|lin_api_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,})"
)

UF_DATALESS = 0x40000000
SF_DATALESS = 0x40000000
SAFE_OPEN_TIMEOUT_SEC = 0.5
DEFAULT_MAX_COPILOT_SESSIONS_SCAN = 30
DEFAULT_MAX_SESSION_EVENTS_BYTES = 1_000_000


def _is_cloud_or_external_path(path: Path | None) -> bool:
    if path is None:
        return False
    raw = str(path)
    return (
        "Mobile Documents" in raw
        or "com~apple~CloudDocs" in raw
        or raw.startswith("/Volumes/")
        or "/Volumes/" in raw
    )


def _is_trusted_fast_local_path(path: Path | None) -> bool:
    if path is None:
        return False
    raw = str(path)
    if _is_cloud_or_external_path(path):
        return False
    home_str = str(Path.home())
    trusted_prefixes = (
        f"{home_str}/.copilot",
        f"{home_str}/.local",
        f"{home_str}/Development/user-skills",
        str(DEFAULT_SKILLS_ROOT.parent),
    )
    return any(raw == pref or raw.startswith(pref + "/") for pref in trusted_prefixes)


def _is_dataless_stat(st: os.stat_result) -> bool:
    flags = int(getattr(st, "st_flags", 0) or 0)
    if flags & (UF_DATALESS | SF_DATALESS):
        return True
    blocks = getattr(st, "st_blocks", None)
    size = int(getattr(st, "st_size", 0) or 0)
    if blocks is not None and int(blocks) == 0 and size > 0:
        return True
    return False


def _run_with_io_timeout(
    fn: Callable[[], Any],
    *,
    timeout_sec: float = SAFE_OPEN_TIMEOUT_SEC,
    default: Any = None,
) -> tuple[bool, Any]:
    """Run a potentially blocking filesystem/SQLite call with a bounded timeout (<0.5s)."""
    timeout_val = max(0.05, float(timeout_sec))
    if (
        threading.current_thread() is threading.main_thread()
        and hasattr(signal, "setitimer")
        and hasattr(signal, "ITIMER_REAL")
    ):
        prev_handler = signal.getsignal(signal.SIGALRM)

        def _alarm_handler(_signum: int, _frame: Any) -> None:
            raise TimeoutError("bounded I/O open timed out")

        try:
            signal.signal(signal.SIGALRM, _alarm_handler)
            signal.setitimer(signal.ITIMER_REAL, timeout_val)
            try:
                return True, fn()
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
        except (TimeoutError, OSError, sqlite3.Error):
            return False, default
        finally:
            with contextlib.suppress(OSError, ValueError):
                signal.signal(signal.SIGALRM, prev_handler)

    box: dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            box["val"] = fn()
            box["ok"] = True
        except BaseException as exc:
            box["err"] = exc
            box["ok"] = False
        finally:
            done.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    if not done.wait(timeout_val):
        return False, default
    if not box.get("ok"):
        return False, default
    return True, box.get("val", default)


def _is_safe_local_file(path: Path | None, *, timeout_sec: float = SAFE_OPEN_TIMEOUT_SEC) -> bool:
    """Return True iff `path` is a materialized local regular file that can be opened without kernel hang.

    1) If `path` is under `Mobile Documents` / `com~apple~CloudDocs` or `/Volumes/` (or any file),
       inspect `os.stat(path, follow_symlinks=False)`: if `st_flags & 0x40000000` (`UF_DATALESS` / `SF_DATALESS`)
       or (`st_blocks == 0 and st_size > 0` on cloud/external paths), skip immediately without calling `open()`.
    2) For any file outside `~/.copilot`, `~/.local`, and `~/Development/user-skills`, probe with
       non-blocking `os.open(path, os.O_RDONLY | os.O_NONBLOCK)` inside a bounded `0.5s` timer/worker.
    """
    if path is None:
        return False
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    import stat as _stat_mod

    if _stat_mod.S_ISLNK(st.st_mode):
        try:
            st = os.stat(path, follow_symlinks=True)
        except OSError:
            return False
    if not _stat_mod.S_ISREG(st.st_mode):
        return False
    flags = int(getattr(st, "st_flags", 0) or 0)
    if flags & (UF_DATALESS | SF_DATALESS):
        return False
    is_cloud_ext = _is_cloud_or_external_path(path)
    if is_cloud_ext and _is_dataless_stat(st):
        return False
    if _is_trusted_fast_local_path(path):
        return True

    def _probe_nonblock_open() -> bool:
        nonblock_flag = getattr(os, "O_NONBLOCK", 0)
        fd = os.open(str(path), os.O_RDONLY | nonblock_flag)
        try:
            os.read(fd, 1)
            return True
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

    ok, res = _run_with_io_timeout(_probe_nonblock_open, timeout_sec=timeout_sec, default=False)
    return bool(ok and res)


def _safe_read_text(
    path: Path | None,
    *,
    max_bytes: int | None = None,
    timeout_sec: float = SAFE_OPEN_TIMEOUT_SEC,
) -> str:
    """Safely read UTF-8 text from `path` after verifying `_is_safe_local_file` and 0.5s open guard."""
    if path is None or not _is_safe_local_file(path, timeout_sec=timeout_sec):
        return ""
    if _is_trusted_fast_local_path(path):
        try:
            nonblock_flag = getattr(os, "O_NONBLOCK", 0)
            fd = os.open(str(path), os.O_RDONLY | nonblock_flag)
            try:
                with os.fdopen(fd, "rb") as handle:
                    raw = handle.read(max_bytes) if max_bytes is not None else handle.read()
                return raw.decode("utf-8", errors="replace")
            except BaseException:
                with contextlib.suppress(OSError):
                    os.close(fd)
                return ""
        except OSError:
            return ""

    def _read_impl() -> str:
        nonblock_flag = getattr(os, "O_NONBLOCK", 0)
        fd = os.open(str(path), os.O_RDONLY | nonblock_flag)
        try:
            with os.fdopen(fd, "rb") as handle:
                raw = handle.read(max_bytes) if max_bytes is not None else handle.read()
            return raw.decode("utf-8", errors="replace")
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise

    ok, text = _run_with_io_timeout(_read_impl, timeout_sec=timeout_sec, default="")
    return text if ok and isinstance(text, str) else ""


@contextlib.contextmanager
def _safe_sqlite_connect(db_path: Path | None, *, timeout_sec: float = SAFE_OPEN_TIMEOUT_SEC):
    """Connect to a read-only SQLite database only if `_is_safe_local_file` passes within `0.5s`."""
    if db_path is None or not _is_safe_local_file(db_path, timeout_sec=timeout_sec):
        yield None
        return

    def _connect() -> sqlite3.Connection:
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=timeout_sec)

    if _is_trusted_fast_local_path(db_path):
        try:
            conn = _connect()
        except (sqlite3.Error, OSError):
            yield None
            return
    else:
        ok, conn = _run_with_io_timeout(_connect, timeout_sec=timeout_sec, default=None)
        if not ok or conn is None:
            yield None
            return
    try:
        yield conn
    finally:
        with contextlib.suppress(sqlite3.Error, OSError):
            conn.close()


def _safe_is_dir(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_is_file(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        if _is_cloud_or_external_path(path):
            return _is_safe_local_file(path)
        st = os.stat(path, follow_symlinks=False)
        if getattr(st, "st_flags", 0) & (UF_DATALESS | SF_DATALESS):
            return False
        return path.is_file()
    except OSError:
        return False


def _probe_readable_dir(path: Path | None) -> bool:
    """Fast non-blocking directory probe; returns False immediately on macOS TCC / external volume EINTR/OSError."""
    if not _safe_is_dir(path) or path is None:
        return False
    try:
        with os.scandir(path) as it:
            next(it, None)
        return True
    except OSError:
        return False


def _safe_iterdir(path: Path | None, *, sort_entries: bool = True) -> list[Path]:
    """Iterate directory entries with immediate OSError/InterruptedError/PermissionError degradation."""
    if not _safe_is_dir(path) or path is None:
        return []
    entries: list[Path] = []
    try:
        with os.scandir(path) as it:
            while True:
                try:
                    entry = next(it)
                except StopIteration:
                    break
                except OSError:
                    break
                entries.append(Path(entry.path))
    except OSError:
        return []
    if sort_entries:
        entries.sort()
    return entries


def _safe_glob(path: Path | None, pattern: str, *, recursive: bool = False) -> list[Path]:
    """Glob directory entries with immediate OSError/InterruptedError/PermissionError degradation."""
    if not _probe_readable_dir(path) or path is None:
        return []
    out: list[Path] = []
    try:
        it = path.rglob(pattern) if recursive else path.glob(pattern)
        while True:
            try:
                out.append(next(it))
            except StopIteration:
                break
            except OSError:
                break
    except OSError:
        return []
    return sorted(out)


def _norm_skill(name: str) -> str:
    cleaned = str(name or "").strip().lstrip("/")
    if cleaned in {"goal-target-task", "goal-task"}:
        return "team-lead"
    return cleaned


def _redact_command_sample(raw_cmd: str, *, skills_root: Path | None = None, dev_root: Path | None = None) -> str:
    text = " ".join(str(raw_cmd or "").strip().split())
    text = _SECRET_SCRUB_RE.sub("<REDACTED>", text)
    home_str = str(Path.home())
    if dev_root is not None:
        text = text.replace(str(dev_root), "~/Development")
    if skills_root is not None:
        text = text.replace(str(skills_root.parent), "~/Development/user-skills")
    if home_str and home_str != "/":
        text = text.replace(home_str, "~")
    text = re.sub(r"(?:/" + r"Users|/home)/[^/\s\"']+", "~", text)
    text = re.sub(r"/private/var/" + r"folders/[^\s\"']+", "/tmp/<temp>", text)
    if len(text) > 220:
        text = text[:217].rstrip() + "..."
    return text


def _percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    idx = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * (pct / 100.0)) - 1))
    return round(ordered[idx], 2)


def _find_git_root_for_path(target_path: Path) -> tuple[Path | None, str]:
    cur = target_path.resolve() if target_path.exists() else target_path
    search_dir = cur if cur.is_dir() else cur.parent
    while search_dir != search_dir.parent:
        if (search_dir / ".git").exists():
            try:
                return search_dir, cur.relative_to(search_dir).as_posix()
            except ValueError:
                break
        search_dir = search_dir.parent
    return None, target_path.name


def _git_last_modified_epoch(target_path: Path, *, fallback_mtime: bool = False) -> int | None:
    git_root, rel_path = _find_git_root_for_path(target_path)
    if git_root is not None:
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            proc = subprocess.run(
                ["git", "--no-optional-locks", "log", "-1", "--format=%ct", "--", rel_path],
                cwd=git_root,
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=2.5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip().isdigit():
                return int(proc.stdout.strip())
    if fallback_mtime and target_path.exists():
        with contextlib.suppress(OSError):
            return int(target_path.stat().st_mtime)
    return None


def _compute_pkg_version_fingerprint(pkg_dir: Path) -> dict[str, Any]:
    plugin_version = "1.0.0"
    manifest_p = pkg_dir / ".claude-plugin" / "plugin.json"
    if manifest_p.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            mdoc = json.loads(manifest_p.read_text(encoding="utf-8"))
            if isinstance(mdoc, Mapping) and str(mdoc.get("version") or "").strip():
                plugin_version = str(mdoc["version"]).strip()

    git_root, rel_path = _find_git_root_for_path(pkg_dir)
    commit_count = 0
    subtree_sha = ""
    last_modified_epoch: int | None = None
    if git_root is not None and pkg_dir.is_dir():
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            cnt_p = subprocess.run(
                ["git", "--no-optional-locks", "rev-list", "--count", "HEAD", "--", rel_path],
                cwd=git_root,
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=2.5,
                check=False,
            )
            if cnt_p.returncode == 0 and cnt_p.stdout.strip().isdigit():
                commit_count = int(cnt_p.stdout.strip())
            tree_p = subprocess.run(
                ["git", "--no-optional-locks", "rev-parse", "--short", f"HEAD:{rel_path}"],
                cwd=git_root,
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=2.5,
                check=False,
            )
            if tree_p.returncode == 0 and tree_p.stdout.strip():
                subtree_sha = tree_p.stdout.strip()
            ts_p = subprocess.run(
                ["git", "--no-optional-locks", "log", "-1", "--format=%ct", "--", rel_path],
                cwd=git_root,
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=2.5,
                check=False,
            )
            if ts_p.returncode == 0 and ts_p.stdout.strip().isdigit():
                last_modified_epoch = int(ts_p.stdout.strip())

    if not subtree_sha:
        h = hashlib.sha256()
        if pkg_dir.is_dir():
            for fp in sorted(pkg_dir.rglob("*")):
                if not fp.is_file() or "__pycache__" in fp.parts or fp.name.startswith("."):
                    continue
                with contextlib.suppress(OSError):
                    h.update(fp.relative_to(pkg_dir).as_posix().encode("utf-8") + b"\0" + fp.read_bytes() + b"\0")
        subtree_sha = h.hexdigest()[:7]
    if commit_count <= 0:
        commit_count = 1
    return {
        "skill_version": f"v{commit_count}@{subtree_sha}",
        "commit_count": commit_count,
        "subtree_sha": subtree_sha,
        "plugin_version": plugin_version,
        "last_modified_epoch": last_modified_epoch,
    }


def stamp_skill_telemetry_event(
    event: Mapping[str, Any],
    *,
    inventory: Mapping[str, Mapping[str, Any]] | None = None,
    skills_root: Path | None = None,
) -> dict[str, Any]:
    """Stamp `skill_version` and `subtree_sha` on a `skill_turn`, `reference_hit`, or `script_call` event."""
    out = dict(event)
    kind = str(out.get("kind") or "").strip()
    sk = _norm_skill(str(out.get("skill") or ""))
    if kind not in {"skill_turn", "reference_hit", "script_call"} or not sk:
        return out
    meta = inventory.get(sk) if inventory is not None else None
    if meta is None:
        s_root = skills_root or DEFAULT_SKILLS_ROOT
        pkg_dir = s_root / sk
        if pkg_dir.is_dir():
            meta = _compute_pkg_version_fingerprint(pkg_dir)
    if isinstance(meta, Mapping):
        if not out.get("skill_version") and meta.get("skill_version"):
            out["skill_version"] = str(meta["skill_version"])
        if not out.get("subtree_sha") and meta.get("subtree_sha"):
            out["subtree_sha"] = str(meta["subtree_sha"])
    return out


def _is_call_post_fix(
    call_ts: datetime | None,
    call_skill_version: str | None,
    *,
    current_skill_version: str | None,
    watermark_epoch: int | None,
) -> bool:
    """Return True if an error occurred on the current version or after the last-modified commit watermark."""
    if call_skill_version and current_skill_version and call_skill_version != current_skill_version:
        return False
    if watermark_epoch is not None and call_ts is not None:
        if call_ts.timestamp() < float(watermark_epoch):
            return False
    return True


def _parse_ts(raw: Any, fallback: datetime | None = None) -> datetime | None:
    if raw is None or raw == "":
        return fallback
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val > 1e16:
            sec = val / 1_000_000_000.0
        elif val > 1e12:
            sec = val / 1_000.0
        else:
            sec = val
        try:
            return datetime.fromtimestamp(sec, tz=timezone.utc).astimezone(CST)
        except (OSError, OverflowError, ValueError):
            return fallback
    text = str(raw).strip()
    if not text:
        return fallback
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST if len(text) == 10 else timezone.utc)
        return dt.astimezone(CST)
    except ValueError:
        return fallback


@dataclass
class ScriptCallEvent:
    session_id: str
    skill: str
    script_rel: str
    command: str
    ts: datetime
    exit_code: int | None
    duration_ms: float | None
    measured: bool
    seq: int = 0
    skill_version: str | None = None
    subtree_sha: str | None = None
    output_chars: int = 0
    aci_output_bloat: bool = False
    origin_source: str = "copilot_events"


@dataclass
class SessionObservation:
    session_id: str
    source: str
    started_at: datetime
    ended_at: datetime
    skills_loaded: set[str] = field(default_factory=set)
    ref_reads: list[tuple[str, str, datetime, int]] = field(default_factory=list)  # (skill, rel_ref, ts, seq)
    script_calls: list[ScriptCallEvent] = field(default_factory=list)
    edited_files: set[str] = field(default_factory=set)
    edit_count: int = 0
    edit_seqs: list[int] = field(default_factory=list)
    edit_events: list[tuple[datetime, int]] = field(default_factory=list)
    bash_commands: list[tuple[str, int | None, float | None, int]] = field(default_factory=list)
    bash_events: list[tuple[str, int | None, float | None, datetime, int]] = field(default_factory=list)
    referenced_goals: set[str] = field(default_factory=set)
    seq_counter: int = 0

    def next_seq(self) -> int:
        self.seq_counter += 1
        return self.seq_counter


def _extract_exit_code_from_tool_complete(data: Mapping[str, Any], result_text: str) -> int | None:
    shell_exec = data.get("shellExecution")
    if isinstance(shell_exec, Mapping) and shell_exec.get("exitCode") is not None:
        with contextlib.suppress(TypeError, ValueError):
            return int(shell_exec["exitCode"])
    m_done = _SHELL_COMPLETE_RE.search(result_text)
    if m_done:
        with contextlib.suppress(ValueError):
            return int(m_done.group(2))
    return None


def _normalize_ref_rel(raw_path: str) -> tuple[str, str] | None:
    m = _REF_PATH_RE.search(raw_path)
    if m:
        return _norm_skill(m.group("skill")), f"references/{m.group('ref').lstrip('/')}"
    return None


def _extract_script_targets(
    command: str,
    *,
    known_skills: set[str],
    basename_to_targets: Mapping[str, list[tuple[str, str]]],
    active_skills: set[str],
) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in _SKILL_SCRIPT_RE.finditer(command):
        sk = _norm_skill(m.group("skill"))
        rel = f"scripts/{m.group('script').lstrip('/')}"
        if (sk, rel) not in seen:
            seen.add((sk, rel))
            targets.append((sk, rel))
    for m_ver in _REPO_VERIFY_SCRIPT_RE.finditer(command):
        sc_token = m_ver.group("script").split()[0]
        rel_ver = f"scripts/{sc_token}"
        if ("repo-gate", rel_ver) not in seen:
            seen.add(("repo-gate", rel_ver))
            targets.append(("repo-gate", rel_ver))
    for bname, candidates in basename_to_targets.items():
        if bname in command and not any(rel.endswith(f"/{bname}") for _, rel in targets):
            if re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(bname)}(?![A-Za-z0-9_.-])", command):
                chosen = [c for c in candidates if c[0] in active_skills] or (candidates if len(candidates) == 1 else [])
                for item in chosen:
                    if item[0] in known_skills and item not in seen:
                        seen.add(item)
                        targets.append(item)
    return targets


_WILD_CMD_PREFIXES = (
    "python3 -c ",
    "python -c ",
    "sqlite3 ",
    "jq ",
    "rg ",
    "grep ",
    "awk ",
    "sed ",
    "git worktree ",
    "git log ",
    "git diff ",
    "gh pr ",
    "gh run ",
    "gh api ",
    "find ",
    "sips ",
)
_TRIVIAL_SHELL_EXACT = {
    "pwd",
    "ls",
    "git status",
    "git status -sb",
    "git --no-pager status -sb",
    "git branch",
    "true",
}


def _extract_wild_manual_signature(
    raw_command: str,
    *,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
) -> tuple[str, str] | None:
    """Return (canonical_pattern_signature, redacted_example) for manual ad-hoc diagnostic commands."""
    segments = [seg.strip() for seg in re.split(r"\s*(?:&&|;|\|\|)\s*", raw_command or "") if seg.strip()]
    for seg in segments:
        if _SKILL_SCRIPT_RE.search(seg) or _REPO_VERIFY_SCRIPT_RE.search(seg):
            continue
        if "unittest" in seg or "pytest" in seg:
            continue
        lower = seg.lower()
        if lower in _TRIVIAL_SHELL_EXACT:
            continue
        has_pipe_probe = "|" in seg and any(tok in lower for tok in ("jq", "awk", "sed", "sort", "uniq", "wc", "python3 -c"))
        if not (any(lower.startswith(pref) for pref in _WILD_CMD_PREFIXES) or has_pipe_probe):
            continue
        redacted = _redact_command_sample(seg, skills_root=skills_root, dev_root=dev_root)
        sig = redacted
        if lower.startswith(("python3 -c", "python -c")):
            imports = sorted(set(re.findall(r"\bimport\s+([A-Za-z0-9_]+)", seg)))
            mod_tag = "+".join(imports[:4]) if imports else "inline_probe"
            if "sqlite3" in seg:
                sig = f"python3 -c [sqlite3:{mod_tag}]"
            elif "json" in seg:
                sig = f"python3 -c [json:{mod_tag}]"
            else:
                sig = f"python3 -c [{mod_tag}]"
        elif lower.startswith("sqlite3 "):
            parts = seg.split(None, 2)
            db_part = Path(parts[1]).name if len(parts) >= 2 else "db"
            sql_verb = "query"
            m_sql = re.search(r"(?i)\b(select|pragma|with)\b\s+([A-Za-z0-9_*.,()]+)?", seg)
            if m_sql:
                sql_verb = m_sql.group(1).upper()
            sig = f"sqlite3 {db_part} '{sql_verb} ...'"
        else:
            sig = re.sub(r"\"[^\"]{16,}\"", '"<STR>"', sig)
            sig = re.sub(r"'[^']{16,}'", "'<STR>'", sig)
            sig = re.sub(r"~/[^\s\"']+", "~/...", sig)
            sig = re.sub(r"\b[0-9a-f]{7,40}\b", "<SHA>", sig)
            sig = re.sub(r"\b\d{2,}\b", "<N>", sig)
            sig = " ".join(sig.split()[:6])
        return sig, redacted
    return None


def _parse_copilot_sessions(
    sessions_dir: Path | None,
    *,
    window_start: datetime,
    window_end: datetime,
    known_skills: set[str],
    basename_to_targets: Mapping[str, list[tuple[str, str]]],
    max_sessions: int = DEFAULT_MAX_COPILOT_SESSIONS_SCAN,
) -> dict[str, SessionObservation]:
    observations: dict[str, SessionObservation] = {}
    if not _probe_readable_dir(sessions_dir):
        return observations

    candidate_files: list[tuple[float, int, Path]] = []
    with contextlib.suppress(OSError):
        for child in _safe_iterdir(sessions_dir, sort_entries=False):
            if not _safe_is_dir(child) or child.name.startswith("."):
                continue
            ev_file = child / "events.jsonl"
            if not _safe_is_file(ev_file):
                continue
            with contextlib.suppress(OSError):
                st = ev_file.stat()
                mtime = st.st_mtime
                mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone(CST)
                if mtime_dt >= window_start:
                    candidate_files.append((mtime, int(st.st_size or 0), ev_file))

    candidate_files.sort(key=lambda item: -item[0])
    relevant_tokens = (
        '"session.start"',
        '"user.message"',
        '"skill.invoked"',
        '"tool.execution_start"',
        '"tool.execution_complete"',
    )
    for mtime, file_size, ev_file in candidate_files[: max(1, min(max_sessions, 250))]:
        if not _is_safe_local_file(ev_file):
            continue
        sid = ev_file.parent.name
        fallback_dt = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone(CST)
        obs = SessionObservation(
            session_id=sid,
            source="copilot_events",
            started_at=fallback_dt,
            ended_at=fallback_dt,
        )
        tool_starts: dict[str, tuple[str, dict[str, Any], datetime, bool]] = {}
        pending_shells: dict[str, tuple[str, datetime, bool]] = {}
        seen_any = False

        try:
            raw_lines: list[str]
            if file_size > DEFAULT_MAX_SESSION_EVENTS_BYTES:
                half = DEFAULT_MAX_SESSION_EVENTS_BYTES // 2
                with ev_file.open("rb") as bhandle:
                    head_chunk = bhandle.read(half).decode("utf-8", errors="replace").splitlines()
                    if head_chunk:
                        head_chunk = head_chunk[:-1]
                    bhandle.seek(max(0, file_size - half))
                    tail_chunk = bhandle.read().decode("utf-8", errors="replace").splitlines()
                    if tail_chunk:
                        tail_chunk = tail_chunk[1:]
                raw_lines = head_chunk + tail_chunk
            else:
                raw_lines = _safe_read_text(ev_file).splitlines()
            for raw_line in raw_lines:
                line = raw_line.strip()
                if not line or not any(tok in line for tok in relevant_tokens):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                ev_type = str(ev.get("type") or "")
                data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                raw_ts = ev.get("timestamp") or data.get("startTime")
                ts = _parse_ts(raw_ts, fallback_dt) or fallback_dt
                if not (window_start <= ts < window_end) and raw_ts is not None:
                    continue
                seen_any = True
                obs.started_at = min(obs.started_at, ts)
                obs.ended_at = max(obs.ended_at, ts)

                if ev_type == "session.start":
                    cwd = str((data.get("context") or {}).get("cwd") or "")
                    for m_goal in _HANDOFF_GOAL_RE.finditer(cwd + "/"):
                        if not m_goal.group(1).startswith("_"):
                            obs.referenced_goals.add(m_goal.group(1))
                elif ev_type == "user.message":
                    content = str(data.get("content") or "")
                    for m_goal in _HANDOFF_GOAL_RE.finditer(content):
                        if not m_goal.group(1).startswith("_"):
                            obs.referenced_goals.add(m_goal.group(1))
                    for m_sk in _SKILL_CONTEXT_RE.finditer(content):
                        sk_raw = m_sk.group("ctx") or m_sk.group("path") or m_sk.group("call")
                        if sk_raw:
                            norm_sk = _norm_skill(sk_raw)
                            if not known_skills or norm_sk in known_skills:
                                obs.skills_loaded.add(norm_sk)
                elif ev_type == "skill.invoked":
                    sk_name = _norm_skill(data.get("name") or "")
                    if sk_name and (not known_skills or sk_name in known_skills):
                        obs.skills_loaded.add(sk_name)
                elif ev_type == "tool.execution_start":
                    tc_id = str(data.get("toolCallId") or "")
                    t_name = str(data.get("toolName") or "")
                    args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
                    has_ts = bool(ev.get("timestamp"))
                    if tc_id:
                        tool_starts[tc_id] = (t_name, args, ts, has_ts)
                    if t_name == "skill" and args.get("skill"):
                        sk_name = _norm_skill(args["skill"])
                        if not known_skills or sk_name in known_skills:
                            obs.skills_loaded.add(sk_name)
                    elif t_name in {"view", "read_file", "read"} and args.get("path"):
                        vp = str(args.get("path") or "")
                        for m_goal in _HANDOFF_GOAL_RE.finditer(vp):
                            if not m_goal.group(1).startswith("_"):
                                obs.referenced_goals.add(m_goal.group(1))
                        norm_ref = _normalize_ref_rel(vp)
                        if norm_ref:
                            ref_sk, ref_rel = norm_ref
                            obs.skills_loaded.add(ref_sk)
                            obs.ref_reads.append((ref_sk, ref_rel, ts, obs.next_seq()))
                        else:
                            for m_sk in _SKILL_CONTEXT_RE.finditer(vp):
                                sk_p = m_sk.group("path")
                                if sk_p:
                                    norm_sk = _norm_skill(sk_p)
                                    if not known_skills or norm_sk in known_skills:
                                        obs.skills_loaded.add(norm_sk)
                    elif t_name in {"edit", "create", "apply_patch", "write", "multiedit"}:
                        fp = str(args.get("path") or args.get("file_path") or "")
                        if fp:
                            obs.edited_files.add(fp)
                        obs.edit_count += 1
                        e_seq = obs.next_seq()
                        obs.edit_seqs.append(e_seq)
                        obs.edit_events.append((ts, e_seq))
                elif ev_type == "tool.execution_complete":
                    tc_id = str(data.get("toolCallId") or "")
                    res_obj = data.get("result")
                    res_text = str(res_obj.get("content") or "") if isinstance(res_obj, dict) else (str(res_obj) if res_obj is not None else "")
                    out_chars = max(len(res_text), int(data.get("outputChars") or data.get("byteLen") or 0))
                    is_bloat = out_chars > ACI_OUTPUT_BLOAT_CHARS or any(marker in res_text for marker in _BLOAT_MARKERS)
                    if tc_id not in tool_starts:
                        continue
                    t_name, args, st_ts, st_has_ts = tool_starts[tc_id]
                    measured = bool(ev.get("timestamp")) and st_has_ts
                    dur_ms = max(1.0, (ts - st_ts).total_seconds() * 1000.0) if measured else None
                    if t_name == "bash":
                        cmd_str = str(args.get("command") or "").strip()
                        if not cmd_str:
                            continue
                        for m_goal in _HANDOFF_GOAL_RE.finditer(cmd_str):
                            if not m_goal.group(1).startswith("_"):
                                obs.referenced_goals.add(m_goal.group(1))
                        for m_ref in _REF_PATH_RE.finditer(cmd_str):
                            ref_sk = _norm_skill(m_ref.group("skill"))
                            obs.ref_reads.append((ref_sk, f"references/{m_ref.group('ref').lstrip('/')}", st_ts, obs.next_seq()))
                        ec = _extract_exit_code_from_tool_complete(data, res_text)
                        m_bg = _SHELL_START_RE.search(res_text)
                        if ec is None and m_bg:
                            pending_shells[m_bg.group(1)] = (cmd_str, st_ts, st_has_ts)
                            continue
                        seq_no = obs.next_seq()
                        obs.bash_commands.append((cmd_str, ec, dur_ms, seq_no))
                        obs.bash_events.append((cmd_str, ec, dur_ms, st_ts, seq_no))
                        for sk_t, sc_rel in _extract_script_targets(
                            cmd_str,
                            known_skills=known_skills,
                            basename_to_targets=basename_to_targets,
                            active_skills=obs.skills_loaded,
                        ):
                            obs.script_calls.append(
                                ScriptCallEvent(
                                    session_id=sid,
                                    skill=sk_t,
                                    script_rel=sc_rel,
                                    command=cmd_str,
                                    ts=st_ts,
                                    exit_code=ec,
                                    duration_ms=dur_ms,
                                    measured=measured,
                                    seq=seq_no,
                                    output_chars=out_chars,
                                    aci_output_bloat=is_bloat,
                                )
                            )
                    elif t_name in {"read_bash", "stop_bash"}:
                        sh_id = str(args.get("shellId") or "")
                        m_done = _SHELL_COMPLETE_RE.search(res_text)
                        target_sh = m_done.group(1) if m_done else sh_id
                        if target_sh and target_sh in pending_shells and m_done:
                            cmd_str, bg_st, bg_has_ts = pending_shells.pop(target_sh)
                            bg_measured = bool(ev.get("timestamp")) and bg_has_ts
                            bg_dur = max(1.0, (ts - bg_st).total_seconds() * 1000.0) if bg_measured else None
                            ec_val = int(m_done.group(2))
                            seq_no = obs.next_seq()
                            obs.bash_commands.append((cmd_str, ec_val, bg_dur, seq_no))
                            obs.bash_events.append((cmd_str, ec_val, bg_dur, bg_st, seq_no))
                            for sk_t, sc_rel in _extract_script_targets(
                                cmd_str,
                                known_skills=known_skills,
                                basename_to_targets=basename_to_targets,
                                active_skills=obs.skills_loaded,
                            ):
                                obs.script_calls.append(
                                    ScriptCallEvent(
                                        session_id=sid,
                                        skill=sk_t,
                                        script_rel=sc_rel,
                                        command=cmd_str,
                                        ts=bg_st,
                                        exit_code=ec_val,
                                        duration_ms=bg_dur,
                                        measured=bg_measured,
                                        seq=seq_no,
                                        output_chars=out_chars,
                                        aci_output_bloat=is_bloat,
                                    )
                                )
        except OSError:
            continue

        if seen_any and (obs.skills_loaded or obs.ref_reads or obs.script_calls or obs.edited_files or obs.bash_commands):
            observations[sid] = obs

    return observations


def _ingest_usage_jsonl_and_shards(
    observations: dict[str, SessionObservation],
    *,
    usage_jsonl: Path | None,
    shard_records: Sequence[Mapping[str, Any]],
    window_start: datetime,
    window_end: datetime,
    known_skills: set[str],
    inventory: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    rows: list[Mapping[str, Any]] = []
    if usage_jsonl is not None and _is_safe_local_file(usage_jsonl):
        raw_usage = _safe_read_text(usage_jsonl)
        for line in raw_usage.splitlines():
            line = line.strip()
            if not line:
                continue
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    rows.append(parsed)
    rows.extend(shard_records)

    for idx, raw_rec in enumerate(rows):
        rec = stamp_skill_telemetry_event(raw_rec, inventory=inventory)
        kind = str(rec.get("kind") or "").strip()
        if kind not in {"skill_turn", "reference_hit", "script_call"}:
            continue
        ts = _parse_ts(rec.get("ts") or rec.get("timestamp") or rec.get("observed_at"))
        if ts is None or not (window_start <= ts < window_end):
            continue
        sid = str(rec.get("session_id") or f"telemetry-{idx}").strip()
        sk = _norm_skill(rec.get("skill") or "")
        if not sk:
            continue
        obs = observations.get(sid)
        is_new_obs = obs is None
        if obs is None:
            obs = SessionObservation(session_id=sid, source="usage_jsonl", started_at=ts, ended_at=ts)
            observations[sid] = obs
        obs.started_at = min(obs.started_at, ts)
        obs.ended_at = max(obs.ended_at, ts)

        if kind == "skill_turn":
            if not known_skills or sk in known_skills:
                obs.skills_loaded.add(sk)
            for tc in rec.get("tool_calls") or []:
                tc_low = str(tc).lower()
                if tc_low in {"edit", "write", "multiedit", "apply_patch", "create"} and is_new_obs:
                    obs.edit_count += 1
                    e_seq = obs.next_seq()
                    obs.edit_seqs.append(e_seq)
                    obs.edit_events.append((ts, e_seq))
        elif kind == "reference_hit":
            raw_ref = str(rec.get("reference_path") or "").strip()
            norm_pair = _normalize_ref_rel(raw_ref)
            rel_ref = norm_pair[1] if norm_pair else (raw_ref if raw_ref.startswith("references/") else f"references/{Path(raw_ref).name}")
            if not any(r_sk == sk and r_rel == rel_ref for r_sk, r_rel, _, _ in obs.ref_reads):
                obs.skills_loaded.add(sk)
                obs.ref_reads.append((sk, rel_ref, ts, obs.next_seq()))
        elif kind == "script_call":
            raw_sc = str(rec.get("script_path") or "").strip()
            m_sc = _SKILL_SCRIPT_RE.search(raw_sc)
            sc_rel = f"scripts/{m_sc.group('script').lstrip('/')}" if m_sc else (raw_sc if raw_sc.startswith("scripts/") else f"scripts/{Path(raw_sc).name}")
            raw_ec = rec.get("exit_code")
            ec = int(raw_ec) if raw_ec not in (None, "") else None
            raw_dur = rec.get("duration_ms")
            dur = max(0.0, float(raw_dur)) if raw_dur not in (None, "") else None
            # Only stamp default current skill_version when explicit version was provided on the raw record
            explicit_sv = str(raw_rec.get("skill_version") or "").strip() or None
            explicit_sub = str(raw_rec.get("subtree_sha") or "").strip() or None
            obs.script_calls.append(
                ScriptCallEvent(
                    session_id=sid,
                    skill=sk,
                    script_rel=sc_rel,
                    command=str(rec.get("command") or raw_sc),
                    ts=ts,
                    exit_code=ec,
                    duration_ms=dur,
                    measured=dur is not None,
                    seq=obs.next_seq(),
                    skill_version=explicit_sv,
                    subtree_sha=explicit_sub,
                    origin_source="usage_jsonl",
                )
            )


def _ingest_trace_export(
    observations: dict[str, SessionObservation],
    *,
    trace_export_path: Path | None,
    window_start: datetime,
    window_end: datetime,
    known_skills: set[str],
) -> int:
    if trace_export_path is None or not _is_safe_local_file(trace_export_path):
        return 0
    ingested_spans = 0
    raw_text = _safe_read_text(trace_export_path).strip()
    if not raw_text:
        return 0

    records: list[dict[str, Any]] = []
    if raw_text.startswith("{") or raw_text.startswith("["):
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                if isinstance(parsed.get("traces"), list):
                    records.extend(t for t in parsed["traces"] if isinstance(t, dict))
                elif isinstance(parsed.get("batch"), list):
                    records.extend(t for t in parsed["batch"] if isinstance(t, dict))
                else:
                    records.append(parsed)
            elif isinstance(parsed, list):
                records.extend(t for t in parsed if isinstance(t, dict))
    if not records:
        for line in raw_text.splitlines():
            line = line.strip()
            if not line:
                continue
            with contextlib.suppress(json.JSONDecodeError):
                obj = json.loads(line)
                if isinstance(obj, dict):
                    records.append(obj)

    for idx, tr in enumerate(records):
        sid = str(tr.get("entity_id") or tr.get("session_id") or tr.get("trace_id") or f"trace-{idx}")
        spans = tr.get("spans") if isinstance(tr.get("spans"), list) else []
        for sp in spans:
            if not isinstance(sp, dict):
                continue
            cat = str(sp.get("category") or (sp.get("attributes") or {}).get("multica.span.category") or "")
            ts = _parse_ts(sp.get("start_ns") or sp.get("startTime") or tr.get("start_ns")) or window_start
            if not (window_start <= ts < window_end):
                continue
            obs = observations.setdefault(
                sid,
                SessionObservation(session_id=sid, source="trace_export", started_at=ts, ended_at=ts),
            )
            obs.started_at = min(obs.started_at, ts)
            obs.ended_at = max(obs.ended_at, ts)
            attrs = sp.get("attributes") if isinstance(sp.get("attributes"), dict) else {}
            out_p = sp.get("output_payload") if isinstance(sp.get("output_payload"), dict) else {}
            in_p = sp.get("input_payload") if isinstance(sp.get("input_payload"), dict) else {}
            ingested_spans += 1
            if cat == "skill_load":
                sk = _norm_skill(attrs.get("skill.name") or sp.get("name", "").replace("skill:", ""))
                if sk and (not known_skills or sk in known_skills):
                    obs.skills_loaded.add(sk)
            elif cat == "l1_reference_read":
                rp = str(attrs.get("reference.path") or in_p.get("path") or "")
                norm_pair = _normalize_ref_rel(rp)
                if norm_pair:
                    obs.skills_loaded.add(norm_pair[0])
                    if not any(r_sk == norm_pair[0] and r_rel == norm_pair[1] for r_sk, r_rel, _, _ in obs.ref_reads):
                        obs.ref_reads.append((norm_pair[0], norm_pair[1], ts, obs.next_seq()))
            elif cat == "l2_script_exec":
                sp_path = str(attrs.get("script.path") or in_p.get("script") or "")
                cmd_str = str(in_p.get("command") or sp_path)
                measured = bool(attrs.get("timing.measured", out_p.get("timing.measured", False)))
                raw_dur = attrs.get("script.duration_ms", out_p.get("duration_ms"))
                dur = float(raw_dur) if measured and raw_dur is not None else None
                raw_ec = attrs.get("script.exit_code", out_p.get("exit_code"))
                ec = int(raw_ec) if raw_ec is not None else None
                sv_attr = str(attrs.get("skill.version") or attrs.get("skill_version") or "").strip() or None
                sub_attr = str(attrs.get("skill.subtree_sha") or attrs.get("subtree_sha") or "").strip() or None
                m_sc = _SKILL_SCRIPT_RE.search(sp_path)
                if m_sc:
                    sk = _norm_skill(m_sc.group("skill"))
                    sc_rel = f"scripts/{m_sc.group('script').lstrip('/')}"
                else:
                    sk = _norm_skill(attrs.get("skill.name") or "repo-gate")
                    sc_rel = sp_path if sp_path.startswith("scripts/") else f"scripts/{Path(sp_path or 'unknown.py').name}"
                obs.script_calls.append(
                    ScriptCallEvent(
                        session_id=sid,
                        skill=sk,
                        script_rel=sc_rel,
                        command=cmd_str,
                        ts=ts,
                        exit_code=ec,
                        duration_ms=dur,
                        measured=measured,
                        seq=obs.next_seq(),
                        skill_version=sv_attr,
                        subtree_sha=sub_attr,
                        origin_source="trace_export",
                    )
                )
    return ingested_spans


def _ingest_warehouse_and_copilot_db(
    observations: dict[str, SessionObservation],
    *,
    warehouse_db: Path | None,
    copilot_db: Path | None,
    window_start: datetime,
    window_end: datetime,
    known_skills: set[str],
    basename_to_targets: Mapping[str, list[tuple[str, str]]],
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "r1_to_r2_converged_tasks": [],
        "directional_drift_tasks": [],
        "skill_escapes": {},  # skill -> {"impl_bug": int, "spec_gap": int}
    }

    def bump_escape(sk: str, kind: str) -> None:
        bucket = meta["skill_escapes"].setdefault(_norm_skill(sk), {"impl_bug": 0, "spec_gap": 0})
        bucket[kind] = bucket.get(kind, 0) + 1

    if _safe_is_file(warehouse_db) and warehouse_db is not None:
        with contextlib.suppress(sqlite3.Error, OSError), _safe_sqlite_connect(warehouse_db) as conn:
            if conn is not None:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            else:
                tables = set()
            if "fact_turn" in tables:
                cols = [r[1] for r in conn.execute('PRAGMA table_info("fact_turn")')]
                for values in conn.execute('SELECT * FROM "fact_turn"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get("ts") or row.get("timestamp") or row.get("cst_day"))
                    if ts is None or not (window_start <= ts < window_end):
                        continue
                    sid = str(row.get("session_id") or row.get("turn_uuid") or "").strip()
                    if not sid:
                        continue
                    obs = observations.setdefault(
                        sid,
                        SessionObservation(session_id=sid, source="warehouse", started_at=ts, ended_at=ts),
                    )
                    obs.started_at = min(obs.started_at, ts)
                    obs.ended_at = max(obs.ended_at, ts)
                    raw_sk = row.get("skill_names")
                    sk_list: list[str] = []
                    if isinstance(raw_sk, str) and raw_sk.strip().startswith("["):
                        with contextlib.suppress(json.JSONDecodeError):
                            sk_list = [str(x) for x in json.loads(raw_sk) if str(x).strip()]
                    elif raw_sk:
                        sk_list = [str(raw_sk)]
                    if row.get("attribution_skill"):
                        sk_list.append(str(row["attribution_skill"]))
                    for s in sk_list:
                        ns = _norm_skill(s)
                        if ns and (not known_skills or ns in known_skills):
                            obs.skills_loaded.add(ns)
                    raw_tools = row.get("tool_calls")
                    tools: list[Any] = []
                    if isinstance(raw_tools, str) and raw_tools.strip().startswith("["):
                        with contextlib.suppress(json.JSONDecodeError):
                            tools = json.loads(raw_tools)
                    for t in tools:
                        if isinstance(t, dict):
                            cmd_or_p = str(t.get("command") or t.get("path") or "")
                            if cmd_or_p and str(t.get("name") or "").lower() in {"edit", "write", "create", "apply_patch"}:
                                obs.edited_files.add(cmd_or_p)
                                obs.edit_count += 1
                                e_seq = obs.next_seq()
                                obs.edit_seqs.append(e_seq)
                                obs.edit_events.append((ts, e_seq))
                            elif cmd_or_p:
                                b_seq = obs.next_seq()
                                obs.bash_commands.append((cmd_or_p, t.get("exit_code"), t.get("duration_ms"), b_seq))
                                obs.bash_events.append((cmd_or_p, t.get("exit_code"), t.get("duration_ms"), ts, b_seq))
                        elif str(t).lower() in {"edit", "write", "create", "apply_patch"}:
                            obs.edit_count += 1
                            e_seq = obs.next_seq()
                            obs.edit_seqs.append(e_seq)
                            obs.edit_events.append((ts, e_seq))

            for ref_tbl in ("reference_hits", "fact_skill_reference_hit"):
                if ref_tbl not in tables:
                    continue
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{ref_tbl}")')]
                for values in conn.execute(f'SELECT * FROM "{ref_tbl}"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get("ts") or row.get("timestamp") or row.get("observed_at") or row.get("cst_day"))
                    if ts is None or not (window_start <= ts < window_end):
                        continue
                    sid = str(row.get("session_id") or row.get("turn_uuid") or "wh-ref")
                    sk = _norm_skill(row.get("skill") or "")
                    rp = str(row.get("reference_path") or row.get("ref_path") or "").strip()
                    if not sk or not rp:
                        continue
                    norm_pair = _normalize_ref_rel(rp)
                    rel_ref = norm_pair[1] if norm_pair else (rp if rp.startswith("references/") else f"references/{Path(rp).name}")
                    obs = observations.setdefault(
                        sid,
                        SessionObservation(session_id=sid, source="warehouse", started_at=ts, ended_at=ts),
                    )
                    obs.skills_loaded.add(sk)
                    if not any(r_sk == sk and r_rel == rel_ref for r_sk, r_rel, _, _ in obs.ref_reads):
                        obs.ref_reads.append((sk, rel_ref, ts, obs.next_seq()))

            for sc_tbl in ("script_call", "fact_skill_script_call"):
                if sc_tbl not in tables:
                    continue
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{sc_tbl}")')]
                for values in conn.execute(f'SELECT * FROM "{sc_tbl}"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get("ts") or row.get("timestamp") or row.get("observed_at") or row.get("cst_day"))
                    if ts is None or not (window_start <= ts < window_end):
                        continue
                    sid = str(row.get("session_id") or row.get("turn_uuid") or "wh-script")
                    sk = _norm_skill(row.get("skill") or "")
                    sp = str(row.get("script_path") or row.get("path") or "").strip()
                    if not sk or not sp:
                        continue
                    m_sc = _SKILL_SCRIPT_RE.search(sp)
                    sc_rel = f"scripts/{m_sc.group('script').lstrip('/')}" if m_sc else (sp if sp.startswith("scripts/") else f"scripts/{Path(sp).name}")
                    raw_ec = row.get("exit_code")
                    ec = int(raw_ec) if raw_ec not in (None, "") else None
                    raw_dur = row.get("duration_ms")
                    dur = max(0.0, float(raw_dur)) if raw_dur not in (None, "") else None
                    cmd_str = str(row.get("command") or sp)
                    obs = observations.setdefault(
                        sid,
                        SessionObservation(session_id=sid, source="warehouse", started_at=ts, ended_at=ts),
                    )
                    seq_no = obs.next_seq()
                    obs.script_calls.append(
                        ScriptCallEvent(
                            session_id=sid,
                            skill=sk,
                            script_rel=sc_rel,
                            command=cmd_str,
                            ts=ts,
                            exit_code=ec,
                            duration_ms=dur,
                            measured=dur is not None,
                            seq=seq_no,
                            skill_version=str(row.get("skill_version") or "").strip() or None,
                            subtree_sha=str(row.get("subtree_sha") or "").strip() or None,
                            origin_source="warehouse",
                        )
                    )

            if "fact_gtt_task_run" in tables:
                cols = [r[1] for r in conn.execute('PRAGMA table_info("fact_gtt_task_run")')]
                grouped_tasks: dict[tuple[str, str], list[dict[str, Any]]] = {}
                for values in conn.execute('SELECT * FROM "fact_gtt_task_run"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get("ended_at") or row.get("started_at") or row.get("cst_day"))
                    if ts is None or not (window_start <= ts < window_end):
                        continue
                    g_id = str(row.get("goal_id") or "")
                    t_id = str(row.get("task_id") or "")
                    if g_id and t_id:
                        grouped_tasks.setdefault((g_id, t_id), []).append(row)

                converged_goal_task_skills: set[tuple[str, str, str]] = set()
                converged_goal_skills: set[tuple[str, str]] = set()
                for (g_id, t_id), runs in grouped_tasks.items():
                    max_r = max(int(r.get("round_no") or 1) for r in runs)
                    passed_later = any(str(r.get("v5_verdict") or "").upper() in {"PASS", "CONCERNS", "WAIVED"} and int(r.get("round_no") or 1) >= 2 for r in runs)
                    still_failed_r3 = max_r >= 3
                    sk_set: set[str] = set()
                    for r in runs:
                        raw_su = r.get("skills_used")
                        if isinstance(raw_su, str) and raw_su.strip().startswith("["):
                            with contextlib.suppress(json.JSONDecodeError):
                                sk_set.update(_norm_skill(x) for x in json.loads(raw_su) if str(x).strip())
                        elif raw_su:
                            for m_sk in _SKILL_CONTEXT_RE.finditer(str(raw_su)):
                                tok = m_sk.group("ctx") or m_sk.group("path") or m_sk.group("call")
                                if tok:
                                    sk_set.add(_norm_skill(tok))
                    if max_r == 2 and passed_later:
                        for sk in sk_set:
                            converged_goal_task_skills.add((g_id, t_id, sk))
                            if g_id:
                                converged_goal_skills.add((g_id, sk))
                        meta["r1_to_r2_converged_tasks"].append({"goal_id": g_id, "task_id": t_id, "skills": sorted(sk_set)})
                    elif still_failed_r3:
                        meta["directional_drift_tasks"].append({"goal_id": g_id, "task_id": t_id, "max_round": max_r, "skills": sorted(sk_set)})
                        for sk in sk_set:
                            bump_escape(sk, "impl_bug")
            else:
                converged_goal_task_skills = set()
                converged_goal_skills = set()

            if "fact_gtt_event" in tables:
                cols = [r[1] for r in conn.execute('PRAGMA table_info("fact_gtt_event")')]
                for values in conn.execute('SELECT * FROM "fact_gtt_event"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get("ts") or row.get("observed_at") or row.get("cst_day"))
                    if ts is None or not (window_start <= ts < window_end):
                        continue
                    evt = str(row.get("evt_type") or "").upper()
                    note = str(row.get("note_preview") or row.get("note") or "")
                    g_id = str(row.get("goal_id") or "")
                    t_id = str(row.get("task_id") or "")
                    sk_hits = {_norm_skill(m.group("ctx") or m.group("path") or m.group("call") or "") for m in _SKILL_CONTEXT_RE.finditer(note)}
                    sk_hits.discard("")
                    if not sk_hits and "design" in g_id.lower():
                        sk_hits.add("my-designer")
                    if evt in {"CHANGE_REQUEST", "NEED_OWNER_DECISION"}:
                        meta["directional_drift_tasks"].append({"goal_id": g_id, "task_id": t_id, "event": evt, "skills": sorted(sk_hits)})
                        for sk in sk_hits:
                            bump_escape(sk, "spec_gap")
                    elif evt == "REVIEW_FAIL":
                        kind = "spec_gap" if "SPEC_GAP" in note.upper() else "impl_bug"
                        for sk in sk_hits:
                            if (g_id, t_id, sk) in converged_goal_task_skills or (not t_id and (g_id, sk) in converged_goal_skills):
                                continue
                            bump_escape(sk, kind)

    if _safe_is_file(copilot_db) and copilot_db is not None:
        with contextlib.suppress(sqlite3.Error, OSError), _safe_sqlite_connect(copilot_db) as conn:
            if conn is not None:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            else:
                tables = set()
            if "session_files" in tables:
                cols = [r[1] for r in conn.execute('PRAGMA table_info("session_files")')]
                for values in conn.execute('SELECT * FROM "session_files"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get("first_seen_at") or row.get("timestamp"))
                    if ts is None or not (window_start <= ts < window_end):
                        continue
                    sid = str(row.get("session_id") or "").strip()
                    fp = str(row.get("file_path") or "").strip()
                    if not sid or not fp:
                        continue
                    obs = observations.setdefault(
                        sid,
                        SessionObservation(session_id=sid, source="copilot_db", started_at=ts, ended_at=ts),
                    )
                    if str(row.get("tool_name") or "").lower() in {"edit", "create", "apply_patch"}:
                        obs.edited_files.add(fp)
                        obs.edit_count += 1
                        e_seq = obs.next_seq()
                        obs.edit_seqs.append(e_seq)
                        obs.edit_events.append((ts, e_seq))
            if "turns" in tables:
                cols = [r[1] for r in conn.execute('PRAGMA table_info("turns")')]
                for values in conn.execute('SELECT * FROM "turns"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get("timestamp"))
                    if ts is None or not (window_start <= ts < window_end):
                        continue
                    sid = str(row.get("session_id") or "").strip()
                    if not sid:
                        continue
                    blob = f"{row.get('user_message') or ''}\n{row.get('assistant_response') or ''}"
                    obs = observations.get(sid)
                    matched_sk = {
                        _norm_skill(m.group("ctx") or m.group("path") or m.group("call") or "")
                        for m in _SKILL_CONTEXT_RE.finditer(blob)
                    }
                    matched_sk.discard("")
                    if known_skills:
                        matched_sk &= known_skills
                    if matched_sk or obs is not None:
                        obs = observations.setdefault(
                            sid,
                            SessionObservation(session_id=sid, source="copilot_db", started_at=ts, ended_at=ts),
                        )
                        obs.skills_loaded.update(matched_sk)
                        for m_ref in _REF_PATH_RE.finditer(blob):
                            r_sk = _norm_skill(m_ref.group("skill"))
                            r_rel = f"references/{m_ref.group('ref').lstrip('/')}"
                            if not any(x_sk == r_sk and x_rel == r_rel for x_sk, x_rel, _, _ in obs.ref_reads):
                                obs.ref_reads.append((r_sk, r_rel, ts, obs.next_seq()))

    return meta


def _drawer_linked_in_scripts(pkg_path: Path, ref_rel: str) -> bool:
    scripts_dir = pkg_path / "scripts"
    if not scripts_dir.is_dir():
        return False
    ref_name = Path(ref_rel).name
    for sp in scripts_dir.rglob("*"):
        if not sp.is_file() or sp.suffix not in {".py", ".sh"}:
            continue
        with contextlib.suppress(OSError):
            txt = sp.read_text(encoding="utf-8", errors="ignore")
            if ref_rel in txt or ref_name in txt:
                return True
    return False


def _drawer_linked_in_l0(pkg_path: Path, ref_rel: str) -> bool:
    ref_name = Path(ref_rel).name
    skill_md = pkg_path / "SKILL.md"
    if skill_md.is_file():
        with contextlib.suppress(OSError):
            txt = skill_md.read_text(encoding="utf-8", errors="ignore")
            if ref_rel in txt or ref_name in txt:
                return True
    return False


_MALFORMED_MODEL_ID_RE = re.compile(
    r"\b(?:gemini|gpt|claude|grok|mai-code)(?::[0-9A-Za-z._-]+(?:\s+[0-9A-Za-z._-]+)?|\s*[-:]\s+[0-9A-Za-z._-]+|[0-9A-Za-z._-]*-\s+[0-9A-Za-z._-]+)"
)
_GENERIC_FLAG_PLACEHOLDERS = {
    "--help",
    "-h",
    "--flag",
    "--flags",
    "--opt",
    "--options",
    "--arg",
    "--args",
}


def _extract_script_ast_cli_contract(script_path: Path) -> dict[str, Any] | None:
    """Parse Python script AST to extract `argparse` flags and subcommands (`add_argument`, `add_parser`)."""
    if not _safe_is_file(script_path) or script_path.suffix.lower() != ".py":
        return None
    try:
        src = script_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src, filename=str(script_path))
    except (OSError, SyntaxError):
        return None

    uses_argparse = "argparse" in src
    uses_parse_known = "parse_known_args" in src or "REMAINDER" in src or ("sys.argv" in src and not uses_argparse)
    flags: set[str] = set(_GENERIC_FLAG_PLACEHOLDERS)
    subcommands: set[str] = set()
    has_subparsers = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr == "add_argument":
                is_pos = True
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        val = arg.value.strip()
                        if val.startswith("-"):
                            is_pos = False
                            flags.add(val.split("=")[0])
                for kw in node.keywords:
                    if kw.arg == "choices" and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                        c_vals = [
                            e.value
                            for e in kw.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        ]
                        if is_pos and c_vals:
                            subcommands.update(c_vals)
                            has_subparsers = True
            elif attr == "add_parser":
                has_subparsers = True
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    subcommands.add(node.args[0].value.strip())
                for kw in node.keywords:
                    if kw.arg == "aliases" and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                        for elt in kw.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                subcommands.add(elt.value.strip())
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and "subcommand" in target.id.lower():
                    if isinstance(node.value, (ast.Set, ast.List, ast.Tuple, ast.Dict)):
                        elts = node.value.keys if isinstance(node.value, ast.Dict) else node.value.elts
                        for elt in elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                subcommands.add(elt.value.strip())
                                has_subparsers = True

    return {
        "uses_argparse": uses_argparse,
        "uses_parse_known": uses_parse_known,
        "flags": flags,
        "subcommands": subcommands,
        "has_subparsers": has_subparsers,
    }


def internal_reality_check(skill_dir: Path | str) -> dict[str, Any]:
    """S2 Internal Reality Check: verify SKILL.md and references/*.md against local scripts AST and reference files.

    Detects:
      - `internal_doc_cli_drift`: documented `skills/<sk>/scripts/<script>.py` missing on disk, or concrete
        `--flag` / subcommand documented in `SKILL.md` / `references/*.md` missing from the script's AST
        (when the script uses `argparse` without `parse_known_args`). Ignores generic placeholders like `[--flag]`
        and pass-through `--` arguments.
      - `internal_broken_refs`: local `references/<file>` links missing on disk, or malformed model strings
        such as `gemini:4- argon`.
    """
    pkg = Path(skill_dir)
    sk_name = pkg.name
    doc_files: list[Path] = []
    skill_md = pkg / "SKILL.md"
    if _safe_is_file(skill_md):
        doc_files.append(skill_md)
    refs_dir = pkg / "references"
    if _probe_readable_dir(refs_dir):
        for rp in _safe_glob(refs_dir, "*.md", recursive=True):
            if _safe_is_file(rp):
                doc_files.append(rp)

    ast_cache: dict[str, dict[str, Any] | None] = {}
    cli_drift: list[dict[str, Any]] = []
    broken_refs: list[dict[str, Any]] = []
    seen_drift: set[tuple[str, str, str, str]] = set()
    seen_broken: set[tuple[str, str, str]] = set()

    script_inv_re = re.compile(
        rf"(?<![A-Za-z0-9_./-])(?:skills/{re.escape(sk_name)}/)?scripts/([A-Za-z0-9_./-]+\.py)(?![A-Za-z0-9_.-])([^\n`\"|;]*)"
    )
    local_ref_re = re.compile(
        rf"(?:(?<![A-Za-z0-9_./-])|skills/{re.escape(sk_name)}/)(references/[A-Za-z0-9_./-]+\.(?:md|json|yaml|yml))"
    )

    for doc_p in doc_files:
        rel_doc = doc_p.relative_to(pkg).as_posix() if doc_p != skill_md else "SKILL.md"
        try:
            text = doc_p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        # 1) Check local references/<file> existence
        for m_ref in local_ref_re.finditer(text):
            ref_rel = m_ref.group(1)
            if any(ch in ref_rel for ch in ("<", ">", "*", "{", "}")):
                continue
            if not (pkg / ref_rel).exists():
                key = (rel_doc, "missing_reference", ref_rel)
                if key not in seen_broken:
                    seen_broken.add(key)
                    broken_refs.append(
                        {
                            "doc": rel_doc,
                            "kind": "missing_reference",
                            "target": ref_rel,
                            "detail": f"{rel_doc} references missing local file `{ref_rel}`",
                        }
                    )

        # 2) Check malformed model IDs (e.g. `gemini:4- argon`)
        for m_mod in _MALFORMED_MODEL_ID_RE.finditer(text):
            bad_model = m_mod.group(0).strip()
            key = (rel_doc, "malformed_model_id", bad_model)
            if key not in seen_broken:
                seen_broken.add(key)
                broken_refs.append(
                    {
                        "doc": rel_doc,
                        "kind": "malformed_model_id",
                        "target": bad_model,
                        "detail": f"{rel_doc} contains malformed model identifier `{bad_model}`",
                    }
                )

        # 3) Check documented script invocations against filesystem & Python AST
        for raw_line in text.splitlines():
            for m_sc in script_inv_re.finditer(raw_line):
                sc_sub = m_sc.group(1)
                tail = m_sc.group(2) or ""
                if sc_sub.startswith("test_") or sc_sub.endswith("_test.py"):
                    continue
                prefix_slice = raw_line[max(0, m_sc.start() - 36) : m_sc.start()]
                if re.search(r"(?:skills/[A-Za-z0-9_-]+/|shared-ci/|\$[A-Za-z0-9_]+/)$", prefix_slice) and not prefix_slice.endswith(
                    f"skills/{sk_name}/"
                ):
                    continue
                sc_rel = f"scripts/{sc_sub.lstrip('/')}"
                sc_path = pkg / sc_rel
                if not sc_path.is_file():
                    d_key = (rel_doc, sc_rel, "missing_script", sc_rel)
                    if d_key not in seen_drift:
                        seen_drift.add(d_key)
                        cli_drift.append(
                            {
                                "doc": rel_doc,
                                "script": sc_rel,
                                "kind": "missing_script",
                                "token": sc_rel,
                                "detail": f"{rel_doc} invokes missing script `skills/{sk_name}/{sc_rel}`",
                            }
                        )
                    continue

                if sc_rel not in ast_cache:
                    ast_cache[sc_rel] = _extract_script_ast_cli_contract(sc_path)
                contract = ast_cache[sc_rel]
                if not contract or not contract["uses_argparse"] or contract["uses_parse_known"]:
                    continue

                cleaned_tail = tail
                if " -- " in cleaned_tail:
                    cleaned_tail = cleaned_tail.split(" -- ", 1)[0]
                # Remove generic placeholder brackets like [--flag] or [--options]
                cleaned_tail = re.sub(r"\[\s*--(?:flag|flags|opt|options|arg|args)(?:\s+[^\]]*)?\]", " ", cleaned_tail)
                cleaned_tail_no_angle = re.sub(r"<[^>]*>", " ", cleaned_tail)

                # Check documented subcommand if first token after script is a bare subcommand word
                stripped_tail = cleaned_tail.strip()
                if stripped_tail and contract["has_subparsers"] and contract["subcommands"]:
                    first_tok = stripped_tail.split()[0]
                    if (
                        re.fullmatch(r"[a-z][a-z0-9_-]*", first_tok)
                        and not first_tok.startswith("-")
                        and "|" not in first_tok
                        and first_tok not in {"python", "python3", "and", "or", "in", "to", "for", "with", "via"}
                    ):
                        if first_tok not in contract["subcommands"]:
                            d_key = (rel_doc, sc_rel, "unknown_subcommand", first_tok)
                            if d_key not in seen_drift:
                                seen_drift.add(d_key)
                                cli_drift.append(
                                    {
                                        "doc": rel_doc,
                                        "script": sc_rel,
                                        "kind": "unknown_subcommand",
                                        "token": first_tok,
                                        "detail": f"{rel_doc} documents subcommand `{first_tok}` not found in `{sc_rel}` AST",
                                    }
                                )

                # Check documented --flags
                for m_flag in re.finditer(r"(?<![A-Za-z0-9_-])(--[A-Za-z0-9][A-Za-z0-9_-]*)", cleaned_tail_no_angle):
                    flag_tok = m_flag.group(1)
                    if flag_tok in _GENERIC_FLAG_PLACEHOLDERS or flag_tok.endswith("-"):
                        continue
                    if flag_tok not in contract["flags"]:
                        d_key = (rel_doc, sc_rel, "unknown_flag", flag_tok)
                        if d_key not in seen_drift:
                            seen_drift.add(d_key)
                            cli_drift.append(
                                {
                                    "doc": rel_doc,
                                    "script": sc_rel,
                                    "kind": "unknown_flag",
                                    "token": flag_tok,
                                    "detail": f"{rel_doc} documents flag `{flag_tok}` not found in `{sc_rel}` argparse AST",
                                }
                            )

    return {
        "skill": sk_name,
        "ok": len(cli_drift) == 0 and len(broken_refs) == 0,
        "internal_doc_cli_drift_count": len(cli_drift),
        "internal_broken_refs_count": len(broken_refs),
        "internal_doc_cli_drift": cli_drift,
        "internal_broken_refs": broken_refs,
    }


def _is_eval_counter_case(item: Mapping[str, Any]) -> bool:
    """Return True if an eval case is a negative/counter case (`bad_snippet`, `forbidden_regex`, `negative_cmd`, `expect_exit != 0`, `should_trigger == False`)."""
    if any(str(item.get(k) or "").strip() for k in ("bad_snippet", "forbidden_regex", "negative_cmd")):
        return True
    if item.get("expect_fail_exit") is not None:
        return True
    if item.get("expect_exit") not in (None, 0):
        return True
    if item.get("should_trigger") is False:
        return True
    tags = [str(t).strip().lower() for t in (item.get("tags") or []) if str(t).strip()]
    return any(t in {"negative", "counter", "counter-case"} for t in tags)


def _harvest_cross_session_taste_and_eval(handoff_root: Path | None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "selection_receipt_count": 0,
        "selected_variants_count": 0,
        "starred_variants_count": 0,
        "rejected_variants_count": 0,
        "spectrum_2_1_1_compliant_goals": 0,
        "low_diversity_goals": [],
        "product_persona_notes": [],
    }
    if not _probe_readable_dir(handoff_root):
        return summary

    notes_counter: dict[str, dict[str, Any]] = {}
    for goal_dir in _safe_iterdir(handoff_root):
        if not _safe_is_dir(goal_dir) or goal_dir.name.startswith("_"):
            continue
        receipts_dir = goal_dir / "receipts"
        if not _probe_readable_dir(receipts_dir):
            continue
        for sel_file in _safe_iterdir(receipts_dir):
            if not _safe_is_file(sel_file) or not _SELECTION_FILE_RE.match(sel_file.name):
                continue
            raw_sel = _safe_read_text(sel_file)
            if not raw_sel:
                continue
            try:
                doc = json.loads(raw_sel)
            except json.JSONDecodeError:
                continue
            if not isinstance(doc, dict):
                continue
            summary["selection_receipt_count"] += 1
            if doc.get("selectedVariantID"):
                summary["selected_variants_count"] += 1
            starred = doc.get("starredVariantIDs")
            if isinstance(starred, list):
                summary["starred_variants_count"] += len(starred)
            rejected = doc.get("rejectedVariantIDs")
            if isinstance(rejected, list):
                summary["rejected_variants_count"] += len(rejected)

            variants = doc.get("variants")
            if isinstance(variants, dict) and len(variants) >= 4:
                summary["spectrum_2_1_1_compliant_goals"] += 1
                titles = {
                    str((v or {}).get("title") or (v or {}).get("summary") or "").strip().lower()
                    for v in variants.values()
                    if isinstance(v, dict)
                }
                titles.discard("")
                if 0 < len(titles) < min(3, len(variants)):
                    summary["low_diversity_goals"].append(goal_dir.name)

            notes_map = doc.get("notesByVariant")
            if isinstance(notes_map, dict):
                for vid, note_raw in notes_map.items():
                    note_txt = " ".join(str(note_raw or "").split()).strip()
                    if not note_txt:
                        continue
                    norm_key = note_txt.lower()
                    bucket = notes_counter.setdefault(
                        norm_key,
                        {"note": note_txt[:140], "count": 0, "goals": set(), "variant_id": str(vid)},
                    )
                    bucket["count"] += 1
                    bucket["goals"].add(goal_dir.name)

    ranked_notes = sorted(notes_counter.values(), key=lambda x: (-x["count"], x["note"]))[:5]
    summary["product_persona_notes"] = [
        {"note": item["note"], "count": item["count"], "goals": sorted(item["goals"])[:4]}
        for item in ranked_notes
    ]
    summary["low_diversity_goals"] = sorted(set(summary["low_diversity_goals"]))[:5]
    return summary


def _triage_proposal_scope(
    *,
    occurrence_sessions: int,
    total_sessions: int,
    is_objective_bug_or_friction: bool,
    domain_scoped: bool,
) -> dict[str, Any]:
    """Apply Dual-Flywheel Scope Triage (Fast Flywheel 3-question guard vs Slow Flywheel cohort)."""
    slow_flywheel_ready = total_sessions >= SLOW_FLYWHEEL_COHORT_MIN_SESSIONS
    if is_objective_bug_or_friction and occurrence_sessions >= 2:
        scope_verdict = "global_invariant_or_cli_compat"
        flywheel = "slow_flywheel" if slow_flywheel_ready else "fast_flywheel_verified"
        should_emit = True
    elif domain_scoped and occurrence_sessions >= 2:
        scope_verdict = "scenario_conditioned_branch"
        flywheel = "slow_flywheel" if slow_flywheel_ready else "fast_flywheel_scoped"
        should_emit = True
    elif is_objective_bug_or_friction and occurrence_sessions == 1:
        # Objective CLI exit=2 or unwired error drawer with concrete failing sample
        scope_verdict = "global_cli_or_drawer_wiring"
        flywheel = "fast_flywheel_verified"
        should_emit = True
    else:
        scope_verdict = "observation_pool_hold"
        flywheel = "hold_until_3_cases"
        should_emit = False
    return {
        "scope_verdict": scope_verdict,
        "flywheel": flywheel,
        "slow_flywheel_cohort_ready": slow_flywheel_ready,
        "occurrence_sessions": occurrence_sessions,
        "emit_proposal": should_emit,
    }


def _multi_angle_speed_diagnosis(
    script_rel: str,
    calls: Sequence[ScriptCallEvent],
) -> dict[str, Any]:
    """Attribute script latency across external_wait, serial_queuing, nested_reruns, heavy_full_scan."""
    measured_durs = [float(c.duration_ms) for c in calls if c.measured and c.duration_ms is not None]
    p50_ms = _percentile(measured_durs, 50)
    p95_ms = _percentile(measured_durs, 95)
    max_ms = round(max(measured_durs), 2) if measured_durs else None

    per_session_counts: dict[str, int] = {}
    for c in calls:
        per_session_counts[c.session_id] = per_session_counts.get(c.session_id, 0) + 1
    repeated_sessions = {sid: cnt for sid, cnt in per_session_counts.items() if cnt >= 2}
    nested_rerun_calls = sum(cnt - 1 for cnt in repeated_sessions.values())

    bname = Path(script_rel).name
    cmd_corpus = " ".join(c.command for c in calls)
    is_external = any(hint in script_rel or hint in bname for hint in _EXTERNAL_WAIT_SCRIPT_HINTS)
    is_serial_suite = any(hint in script_rel or hint in cmd_corpus for hint in _SERIAL_RUNNER_HINTS)

    angles: list[str] = []
    primary_cause = "heavy_full_scan"
    if is_external:
        primary_cause = "external_wait"
        angles.append("external_wait: dominated by remote CI/API/daemon polling rather than local CPU")
    if is_serial_suite:
        if primary_cause == "heavy_full_scan":
            primary_cause = "serial_queuing"
        angles.append("serial_queuing: sequential test/audit targets queued in a single process")
    if nested_rerun_calls >= 2:
        if primary_cause == "heavy_full_scan":
            primary_cause = "nested_reruns"
        angles.append(
            f"nested_reruns: {nested_rerun_calls} repeated invocations across {len(repeated_sessions)} session(s)"
        )
    if not angles:
        angles.append("heavy_full_scan: full-history or unindexed scan without incremental watermark")

    speedup_plan = {
        "angle_a_path_scoped": "Scope execution to `git diff --name-only` affected skills/tests on inner-loop edits.",
        "angle_b_watermark_cache": "Persist content-hash / session watermark so unchanged artifacts skip re-scanning.",
        "angle_c_parallel_dag": "Run independent unittest / static audit targets via concurrent worker pool.",
        "angle_d_tiered_fast_fail": "Run <0.5s CLI/contract checks in Tier 0 and move >30s suites/CI to async background.",
    }
    return {
        "primary_bottleneck": primary_cause,
        "p50_ms": p50_ms,
        "p95_ms": p95_ms,
        "max_ms": max_ms,
        "nested_rerun_calls": nested_rerun_calls,
        "repeated_session_count": len(repeated_sessions),
        "attribution_angles": angles,
        "four_angle_speedup_options": speedup_plan,
    }


def _normalize_aspects(
    raw_aspects: Sequence[str] | str | None,
    *,
    mode: str = "full",
) -> list[str]:
    """Normalize aspect list into canonical subset of ('l0', 'l1', 'l2', 'l3', 'creative', 'eval')."""
    if (mode or "full").strip().lower() == "full" and not raw_aspects:
        return list(CANONICAL_ASPECTS)
    tokens: list[str] = []
    if isinstance(raw_aspects, str):
        tokens = [t.strip().lower() for t in re.split(r"[,;\s]+", raw_aspects) if t.strip()]
    elif isinstance(raw_aspects, Sequence):
        for item in raw_aspects:
            tokens.extend(t.strip().lower() for t in re.split(r"[,;\s]+", str(item)) if t.strip())
    if not tokens or "all" in tokens:
        return list(CANONICAL_ASPECTS)
    resolved: list[str] = []
    for tok in tokens:
        mapped = _ASPECT_ALIASES.get(tok, tok)
        if mapped in CANONICAL_ASPECTS and mapped not in resolved:
            resolved.append(mapped)
    return resolved or list(CANONICAL_ASPECTS)


def discover_active_skill_leases(
    *,
    dev_root: Path | None = None,
    handoff_root: Path | None = None,
    known_skills: set[str] | None = None,
) -> dict[str, Any]:
    """Scan active worktrees, in_progress handoff ledgers, and pending cross-skill relays for Lock 2 exclusion."""
    d_root = dev_root or DEFAULT_DEV_ROOT
    h_root = handoff_root if handoff_root is not None else (d_root / "handoff")
    worktrees_dir = d_root / "worktrees"
    leased: dict[str, list[str]] = {}
    pending_relay_targets: dict[str, list[str]] = {}

    def _mark(sk: str, reason: str) -> None:
        norm = _norm_skill(sk)
        if not norm or (known_skills and norm not in known_skills):
            return
        bucket = leased.setdefault(norm, [])
        if reason not in bucket:
            bucket.append(reason)

    if _probe_readable_dir(worktrees_dir):
        with contextlib.suppress(OSError):
            for child in _safe_iterdir(worktrees_dir):
                if not _safe_is_dir(child) or child.name.startswith("."):
                    continue
                wt_name = child.name.lower()
                for sk in sorted(known_skills or set()):
                    if re.search(rf"(?<![a-z0-9]){re.escape(sk.lower())}(?![a-z0-9])", wt_name):
                        _mark(sk, f"active_worktree:{child.name}")

    if _probe_readable_dir(h_root):
        with contextlib.suppress(OSError):
            for goal_dir in _safe_iterdir(h_root):
                if not _safe_is_dir(goal_dir) or goal_dir.name.startswith("_"):
                    continue
                ledger_p = goal_dir / "ledger.md"
                if _safe_is_file(ledger_p):
                    l_txt = _safe_read_text(ledger_p)
                    if "in_progress" in l_txt and "EVT=GOAL_DONE" not in l_txt:
                        for sk in sorted(known_skills or set()):
                            if f"skills/{sk}" in l_txt or f"skill-retro-{sk}" in goal_dir.name:
                                _mark(sk, f"in_progress_ledger:{goal_dir.name}")
                briefs_d = goal_dir / "briefs"
                if _probe_readable_dir(briefs_d):
                    with contextlib.suppress(OSError):
                        for relay_f in _safe_glob(briefs_d, "skill-retro-relay-*.json"):
                            with contextlib.suppress(OSError, json.JSONDecodeError):
                                raw_relay = _safe_read_text(relay_f)
                                if not raw_relay:
                                    continue
                                rdoc = json.loads(raw_relay)
                                if isinstance(rdoc, dict) and str(rdoc.get("status") or "pending") == "pending":
                                    to_sk = _norm_skill(rdoc.get("to_skill") or "")
                                    if to_sk:
                                        pending_relay_targets.setdefault(to_sk, []).append(str(relay_f))
                                        _mark(to_sk, f"pending_relay_target:{goal_dir.name}")

    return {
        "leased_skills": sorted(leased.keys()),
        "lease_reasons": leased,
        "pending_relay_targets": pending_relay_targets,
    }


def emit_cross_skill_relay(
    *,
    from_skill: str,
    to_skill: str,
    requirement: str,
    emit_dir: Path,
    evidence_summary: str = "",
    wave: int = 2,
) -> dict[str, Any]:
    """Emit a Wave 2 Cross-Skill Relay Brief (Host -> Guest) so Host never edits Guest out-of-bounds."""
    host = _norm_skill(from_skill)
    guest = _norm_skill(to_skill)
    if not host or not guest:
        raise ValueError("Both --from (host skill) and --to (guest skill) are required for emit-relay.")
    if host == guest:
        raise ValueError("Host and Guest skill cannot be identical for a cross-skill relay.")
    briefs_dir = emit_dir / "briefs" if emit_dir.name != "briefs" else emit_dir
    briefs_dir.mkdir(parents=True, exist_ok=True)
    host_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", host).strip("-")
    guest_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", guest).strip("-")
    relay_id = f"relay-{host_slug}-to-{guest_slug}"
    md_path = briefs_dir / f"skill-retro-relay-{host_slug}-to-{guest_slug}-wave{wave}.md"
    json_path = briefs_dir / f"skill-retro-relay-{host_slug}-to-{guest_slug}-wave{wave}.json"

    req_clean = " ".join(str(requirement or "").strip().split()) or f"Align `{guest}` interface consumed by `{host}`."
    md_lines = [
        f"# Cross-Skill Relay Brief (Wave {wave}): `{host}` (Host) -> `{guest}` (Guest)",
        "",
        f"- **Relay ID**: `{relay_id}`",
        f"- **Wave**: `{wave}` (scheduled AFTER Wave 1 Host `{host}` merges — parallel anti-collision Lock 3)",
        f"- **Host Skill (Origin)**: `{host}` (strictly forbidden from editing `skills/{guest}/**` in Wave 1)",
        f"- **Guest Skill (Target Owner)**: `{guest}`",
        "- **Role Slot**: `writer` -> `reviewer` (`M1 TL -> Writer -> Reviewer`)",
        "- **Owned Paths**:",
        f"  - `skills/{guest}/**`",
        f"  - `scripts/test_{guest.replace('-', '_')}_*.py`",
        "- **Forbidden Shared Paths (Anti-Collision Lock 1)**:",
        "  - `README.md`, `AGENTS.md`, `.claude-plugin/marketplace.json`, and `skills/" + host + "/**`",
        "- **Interfaces**:",
        f"  - **Consumes**: Relay requirement from `{host}`: {req_clean}",
        f"  - **Produces**: Backward-compatible contract/CLI update in `skills/{guest}/**` + Positive & Counter-Case Replay",
        "",
        "## Relay Requirement & Evidence",
        f"- **Requirement**: {req_clean}",
    ]
    if evidence_summary:
        md_lines.append(f"- **Observed Evidence**: {evidence_summary}")
    md_lines.extend(
        [
            "",
            "## Verification Commands",
            "```bash",
            f"python3 skills/team-lead/scripts/feedback_guard.py pre-review-check --worktree . --brief {md_path}",
            f"python3 skills/skill-contract/scripts/skill_contract.py audit --skill {guest} --strict",
            f"python3 skills/skill-contract/scripts/skill_contract.py eval --skill {guest} --strict",
            "./scripts/verify",
            "```",
        ]
    )
    md_path.write_text("\n".join(md_lines).rstrip() + "\n", encoding="utf-8")
    payload = {
        "ok": True,
        "schema": "skill-retro-relay/v1",
        "relay_id": relay_id,
        "wave": wave,
        "status": "pending",
        "from_skill": host,
        "to_skill": guest,
        "requirement": req_clean,
        "evidence_summary": evidence_summary,
        "owned_paths": [f"skills/{guest}/**", f"scripts/test_{guest.replace('-', '_')}_*.py"],
        "relay_brief_md": str(md_path),
        "relay_brief_json": str(json_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _resolve_per_skill_goal_handoff_dir(emit_dir: Path, safe_slug: str) -> Path:
    """Resolve the isolated M1 Goal handoff directory (`<root>/skill-retro-<slug>`) for a single skill."""
    base = emit_dir.parent if emit_dir.name == "briefs" else emit_dir
    goal_name = f"skill-retro-{safe_slug}"
    if base.name == goal_name:
        return base
    return base / goal_name


def build_m2_dispatch_plan(
    selected_briefs: Sequence[Mapping[str, Any]],
    *,
    handoff_root: Path,
    dev_root: Path | None = None,
) -> dict[str, Any]:
    """Build the 1-M2 -> K (0..3) Independent M1 TL parallel dispatch plan and final M2 rollup command."""
    resolved_dev_root = (dev_root or DEFAULT_DEV_ROOT).expanduser()
    resolved_handoff_root = handoff_root.parent if handoff_root.name == "briefs" else handoff_root
    parallel_m1_tls: list[dict[str, Any]] = []
    seen_skills: set[str] = set()

    for item in selected_briefs:
        if not isinstance(item, Mapping):
            continue
        sk = _norm_skill(str(item.get("skill") or ""))
        if not sk or sk in seen_skills:
            continue
        seen_skills.add(sk)
        safe_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", sk).strip("-") or "skill"
        goal_id = str(item.get("goal_id") or f"skill-retro-{safe_slug}")
        tl_agent_name = str(item.get("m1_tl_agent_name") or f"skill-retro-{safe_slug}-goal-tl-r1")
        goal_handoff_dir = Path(str(item.get("goal_handoff_dir") or _resolve_per_skill_goal_handoff_dir(resolved_handoff_root, safe_slug)))
        spec_md = Path(str(item.get("spec_md") or (goal_handoff_dir / "spec.md")))
        ledger_md = Path(str(item.get("ledger_md") or (goal_handoff_dir / "ledger.md")))
        receipt_json = Path(str(item.get("m1_receipt_json") or (goal_handoff_dir / "receipts" / "m1-tl-receipt.json")))
        worktree_dir = Path(str(item.get("worktree_dir") or (resolved_dev_root / "worktrees" / goal_id)))
        mode_val = str(item.get("mode") or "full")
        aspects_list = list(item.get("aspects") or CANONICAL_ASPECTS)
        aspects_csv = ",".join(str(a) for a in aspects_list)
        owned_paths = list(item.get("owned_paths") or [f"skills/{sk}/**", f"scripts/test_{sk.replace('-', '_')}_*.py"])
        dispatch_cmd = (
            f"skills/team-lead/scripts/herdr-dispatch.sh --new-workspace --role team_lead "
            f"--name {tl_agent_name} --kind claude"
        )
        m1_boot_prompt = (
            f"Load /team-lead and /skill-retro. You are the independent M1 TL ({tl_agent_name}) for single-skill Goal {goal_id} "
            f"(1 Skill = 1 Independent M1 TL). Read {spec_md} and {ledger_md}. "
            f"Create/use isolated worktree {worktree_dir}. Perform deep reasoning (`--mode {mode_val} --aspects {aspects_csv}`) for `{sk}`: "
            f"if the signal is benign/healthy after deep investigation, write `status=HEALTHY_NO_ISSUES` to {receipt_json} and return to M2 "
            f"with ZERO Writers/Reviewers spawned; if a real issue is confirmed, run Writer -> Reviewer strictly within {owned_paths}, "
            f"pass Positive + Counter-Case Replay + `./scripts/verify`, merge PR, and write `status=FIXED_AND_MERGED` (or `CROSS_SKILL_RELAY`) to {receipt_json}."
        )
        parallel_m1_tls.append(
            {
                "skill": sk,
                "goal_id": goal_id,
                "m1_tl_agent_name": tl_agent_name,
                "role": "team_lead",
                "kind": "claude",
                "mode": mode_val,
                "aspects": aspects_list,
                "goal_handoff_dir": str(goal_handoff_dir),
                "spec_md": str(spec_md),
                "ledger_md": str(ledger_md),
                "m1_receipt_json": str(receipt_json),
                "worktree_dir": str(worktree_dir),
                "owned_paths": owned_paths,
                "herdr_dispatch_cmd": dispatch_cmd,
                "m1_boot_prompt": m1_boot_prompt,
            }
        )

    k_count = len(parallel_m1_tls)
    skills_flag = f" --skills {','.join(x['skill'] for x in parallel_m1_tls)}" if parallel_m1_tls else ""
    rollup_cmd = (
        f"python3 skills/skill-retro/scripts/skill_retro.py rollup "
        f"--handoff-root {resolved_handoff_root}{skills_flag} --json"
    )
    return {
        "schema": "skill-retro-m2-dispatch-plan/v1",
        "topology": "1_m2 -> 1_cheap_probe_worker -> 0..3_parallel_m1_tls -> 1_m2_rollup",
        "deep_reasoning_owner": "M1-TL (1 independent M1 TL per skill)",
        "m2_coordinator_count": 1,
        "probe_worker_count": 1,
        "m1_tls_to_spawn": k_count,
        "max_parallel_m1_tls": 3,
        "honest_zero_to_three_cap": True,
        "m1_healthy_early_exit_rule": (
            "If an M1 TL's deep reasoning finds its assigned skill is benign/healthy, "
            "that M1 TL directly writes a HEALTHY_NO_ISSUES receipt and returns to M2 without spawning any Writer or Reviewer."
        ),
        "parallel_m1_dispatches": parallel_m1_tls,
        "herdr_dispatch_commands": [x["herdr_dispatch_cmd"] for x in parallel_m1_tls],
        "m2_rollup_cmd": rollup_cmd,
    }


def emit_skill_retro_briefs(
    report: Mapping[str, Any],
    emit_dir: Path,
    *,
    skill_filter: str | None = None,
    mode: str = "full",
    aspects: Sequence[str] | str | None = None,
    coupled_with: Sequence[str] | str | None = None,
    relays: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate per-skill isolated M1 Goal handoff dirs (`<root>/skill-retro-<skill>/spec.md`) + Writer & Reviewer briefs."""
    briefs_dir = emit_dir / "briefs" if emit_dir.name != "briefs" else emit_dir
    briefs_dir.mkdir(parents=True, exist_ok=True)
    handoff_root_base = emit_dir.parent if emit_dir.name == "briefs" else emit_dir

    norm_mode = "partial" if str(mode or "full").strip().lower() == "partial" else "full"
    selected_aspects = _normalize_aspects(aspects, mode=norm_mode)
    if norm_mode == "full":
        selected_aspects = list(CANONICAL_ASPECTS)
    coupled_skills: list[str] = []
    if isinstance(coupled_with, str):
        coupled_skills = [_norm_skill(s) for s in re.split(r"[,;\s]+", coupled_with) if _norm_skill(s)]
    elif isinstance(coupled_with, Sequence):
        for item in coupled_with:
            for s in re.split(r"[,;\s]+", str(item)):
                if _norm_skill(s):
                    coupled_skills.append(_norm_skill(s))

    norm_filter = _norm_skill(skill_filter) if skill_filter else ""
    l0 = report.get("layer0_direction_and_triggers") or {}
    l1 = report.get("layer1_drawer_metabolism") or {}
    l2 = report.get("layer2_script_efficacy_and_speed") or {}
    l3 = report.get("layer3_wild_patterns_and_eval") or {}
    cross_repo_health = report.get("cross_repo_gate_health") or l2.get("cross_repo_gate_health") or {}
    proposals = list(report.get("proposals") or [])
    obs_pool = list(report.get("observation_pool") or [])

    target_skills: set[str] = set()
    if norm_filter:
        target_skills.add(norm_filter)
    else:
        for p in proposals:
            sk = str(p.get("skill") or "").strip()
            if sk and sk != "repo-gate":
                target_skills.add(sk)
        if not target_skills:
            for item in (l2.get("rigid_roadblocks") or []) + (l1.get("unwired_disconnected_drawers") or []):
                sk = str(item.get("skill") or "").strip()
                if sk and sk != "repo-gate":
                    target_skills.add(sk)

    taste_eval = l3.get("taste_and_eval_diversity") or {}
    emitted: list[dict[str, Any]] = []
    emitted_relays: list[dict[str, Any]] = []
    wave1_tasks: list[dict[str, Any]] = []
    wave2_tasks: list[dict[str, Any]] = []

    for sk in sorted(target_skills):
        safe_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", sk).strip("-") or "skill"
        sk_triggers = [x for x in (l0.get("trigger_misses") or []) if x.get("skill") == sk]
        sk_empty = [x for x in (l0.get("zero_action_loads") or []) if x.get("skill") == sk]
        sk_drifts = [x for x in (l0.get("directional_drifts") or []) if sk in (x.get("skills") or [])]
        sk_converged = [x for x in (l0.get("healthy_r1_to_r2_convergences") or []) if sk in (x.get("skills") or [])]
        sk_hot = [x for x in (l1.get("hot_drawers") or []) if x.get("skill") == sk]
        sk_ondemand = [x for x in (l1.get("on_demand_correction_drawers") or []) if x.get("skill") == sk]
        sk_unwired = [x for x in (l1.get("unwired_disconnected_drawers") or []) if x.get("skill") == sk]
        sk_deadwood = [x for x in (l1.get("true_zero_hit_deadwood") or []) if x.get("skill") == sk]
        sk_catchers = [x for x in (l2.get("true_catchers") or []) if x.get("skill") == sk]
        sk_ceremonial = [x for x in (l2.get("ceremonial_rubber_stamps") or []) if x.get("skill") == sk]
        sk_roadblocks = [x for x in (l2.get("rigid_roadblocks") or []) if x.get("skill") == sk]
        sk_sinks = [x for x in (l2.get("latency_sinks") or []) if x.get("skill") in {sk, "repo-gate"} or x.get("owner_skill") == sk]
        sk_wild = [x for x in (l3.get("wild_patterns") or []) if x.get("candidate_skill") == sk]
        sk_proposals = [x for x in proposals if x.get("skill") == sk]
        sk_held = [x for x in obs_pool if x.get("skill") == sk]
        sk_cross_repo_findings = int(cross_repo_health.get("total_findings") or 0) if sk == "repo-kit" else 0
        own_actionable_sinks = [
            x
            for x in sk_sinks
            if (x.get("skill") == sk or (sk == "repo-kit" and x.get("skill") == "repo-gate"))
            and (x.get("primary_bottleneck") != "external_wait" or int(x.get("nested_rerun_calls") or 0) >= 1)
        ]
        has_actionable_evidence = bool(
            sk_proposals
            or sk_triggers
            or sk_empty
            or sk_drifts
            or sk_unwired
            or sk_hot
            or sk_deadwood
            or sk_roadblocks
            or [x for x in sk_ceremonial if int(x.get("co_occurring_skill_escapes") or 0) > 0]
            or own_actionable_sinks
            or sk_cross_repo_findings > 0
            or sk_wild
            or relays
        )
        if not has_actionable_evidence:
            continue

        owned_paths = [
            f"skills/{sk}/**",
            f"scripts/test_{sk.replace('-', '_')}_*.py",
            "scripts/test_skill_retro.py",
        ]
        coupled_for_sk = [g for g in coupled_skills if g != sk]
        for guest_sk in coupled_for_sk:
            owned_paths.append(f"skills/{guest_sk}/**")
            owned_paths.append(f"scripts/test_{guest_sk.replace('-', '_')}_*.py")

        m1_path = briefs_dir / f"skill-retro-{safe_slug}-m1-tl.md"
        writer_path = briefs_dir / f"skill-retro-{safe_slug}-writer.md"
        reviewer_path = briefs_dir / f"skill-retro-{safe_slug}-reviewer.md"
        aspects_csv = ",".join(selected_aspects)

        goal_id = f"skill-retro-{safe_slug}"
        m1_tl_agent_name = f"skill-retro-{safe_slug}-goal-tl-r1"
        goal_handoff_dir = _resolve_per_skill_goal_handoff_dir(handoff_root_base, safe_slug)
        goal_briefs_dir = goal_handoff_dir / "briefs"
        goal_receipts_dir = goal_handoff_dir / "receipts"
        goal_briefs_dir.mkdir(parents=True, exist_ok=True)
        goal_receipts_dir.mkdir(parents=True, exist_ok=True)
        spec_path = goal_handoff_dir / "spec.md"
        ledger_path = goal_handoff_dir / "ledger.md"
        m1_receipt_path = goal_receipts_dir / "m1-tl-receipt.json"
        worktree_dir = DEFAULT_DEV_ROOT / "worktrees" / goal_id
        herdr_dispatch_cmd = (
            f"skills/team-lead/scripts/herdr-dispatch.sh --new-workspace --role team_lead "
            f"--name {m1_tl_agent_name} --kind claude"
        )

        # Build M1-TL Orchestration Brief & Per-Skill M1 Goal Spec (`1 Skill = 1 Independent M1 TL`)
        m1_lines = [
            f"# M1-TL Brief & Goal Spec (`{goal_id}`): `{sk}` — Stage 3 Independent M1 Deep Reasoning (`mode={norm_mode}`, `aspects={aspects_csv}`)",
            "",
            f"- **Goal ID**: `{goal_id}` (`1 Skill = 1 Independent M1 TL`)",
            f"- **M1 TL Agent**: `{m1_tl_agent_name}` (dispatched in parallel by the single M2 Coordinator)",
            f"- **Isolated Worktree**: `{worktree_dir}`",
            "- **Role Slot**: `M1-TL` (`team-lead` Steering -> `Writer` -> `Reviewer` only if issue confirmed; zero new agent role types)",
            f"- **Host Skill**: `{sk}`" + (f" (Locked Coupled Pair: `{', '.join(coupled_for_sk)}`)" if coupled_for_sk else " (Strict Single-Skill Isolation)"),
            f"- **Check Mode & Aspects**: `--mode {norm_mode} --aspects {aspects_csv}`",
            "- **M1 Deep-Reasoning Early-Exit Rule (`HEALTHY_NO_ISSUES`)**:",
            f"  - M1 TL performs the deep reasoning & investigation for `{sk}` first.",
            f"  - If M1 TL's deep reasoning finds that the flagged signal is benign/healthy (e.g. normal `R1->R2` convergence), **M1 TL directly writes a `HEALTHY_NO_ISSUES` receipt to `{m1_receipt_path}` and returns to M2 WITHOUT spawning a Writer or Reviewer (`writers_spawned=0`)**.",
            "- **Anti-Collision Locks**:",
            f"  - **Lock 1 (Disjoint Owned Paths)**: `{', '.join(owned_paths)}` (NEVER edit `README.md`, `AGENTS.md`, `.claude-plugin/marketplace.json`, or uncoupled guest skills)",
            "  - **Lock 2 (Active Lease Guard)**: Verified not leased by another active worktree/ledger.",
            "  - **Lock 3 (Host/Guest Wave Relay)**: Any uncoupled Guest Skill requirement is emitted as a Wave 2 Relay Brief (`emit-relay`).",
            "- **Dispatch & Rollup Sequence**:",
            f"  1. Deep-reason `{sk}` in `{worktree_dir}` (`python3 skills/skill-retro/scripts/skill_retro.py diagnose --skill {sk} --mode {norm_mode} --aspects {aspects_csv} --json`).",
            f"  2. If benign/healthy -> write `{m1_receipt_path}` (`status=HEALTHY_NO_ISSUES`, `writers_spawned=0`) and report back to the single M2.",
            f"  3. If real issue confirmed -> dispatch `Writer` with `{writer_path.name}`, run `python3 skills/team-lead/scripts/feedback_guard.py pre-review-check --worktree . --brief {writer_path}`, dispatch `Reviewer` with `{reviewer_path.name}`, merge PR, and write `{m1_receipt_path}` (`status=FIXED_AND_MERGED` or `CROSS_SKILL_RELAY`).",
        ]
        m1_md = "\n".join(m1_lines).rstrip() + "\n"
        m1_path.write_text(m1_md, encoding="utf-8")
        spec_path.write_text(m1_md, encoding="utf-8")
        if not ledger_path.is_file():
            ledger_lines = [
                f"# Goal Ledger: `{goal_id}`",
                "",
                f"- Mandate: Independent M1 TL deep reasoning & scoped evolution for `{sk}` (`mode={norm_mode}`, `aspects={aspects_csv}`)",
                "- Owner: M2-Dispatcher",
                f"- TL: {m1_tl_agent_name}",
                "- Repo: LeePepe/user-skills",
                "",
                "## Tasks",
                "",
                "| ID | Wave | Title | Assignee | Status | PR |",
                "|---|---|---|---|---|---|",
                f"| T1 | 1 | Deep-reason `{sk}` and either record HEALTHY_NO_ISSUES or run Writer -> Reviewer + merge PR | {m1_tl_agent_name} | pending | - |",
                "",
                "## Events",
                f"- {_cst_today_str()}T04:20:00+08:00 | EVT=GOAL_CREATED | goal={goal_id} | skill={sk} | from=m2-dispatcher | to={m1_tl_agent_name}",
            ]
            ledger_path.write_text("\n".join(ledger_lines).rstrip() + "\n", encoding="utf-8")

        # Build Writer Brief (reusing standard `writer` / `writer_escalated` role slot)
        w_lines: list[str] = [
            f"# Writer Brief: `{sk}` — 6-Dimension Progressive Skill Self-Evolution (`mode={norm_mode}`, `aspects={aspects_csv}`)",
            "",
            "- **Role Slot**: `writer` (standard `M1 TL -> Writer -> Reviewer` pipeline; zero new agent role types)",
            f"- **Target Skill**: `{sk}` (strict per-skill isolation — do NOT edit sibling skills, shared root docs, or scheduler runners)",
            f"- **Check Mode & Aspects**: `mode={norm_mode}` · `aspects={aspects_csv}`",
            "- **Background**: Evolve this skill contract, drawers, and deterministic scripts from observed runtime telemetry using runtime progressive disclosure.",
            "- **Owned Paths**:",
        ]
        for op in owned_paths:
            w_lines.append(f"  - `{op}`")
        w_lines.extend(
            [
                "- **Forbidden Shared / Guest Paths (Anti-Collision Lock 1 & Host/Guest Protocol)**:",
                "  - Never edit `README.md`, `AGENTS.md`, `.claude-plugin/marketplace.json`, or any uncoupled `skills/<guest>/**`.",
                f"  - If a Guest Skill contract change is required, run `python3 skills/skill-retro/scripts/skill_retro.py emit-relay --from {sk} --to <guest> --requirement \"...\" --emit-briefs {emit_dir}` for Wave 2.",
                "- **Interfaces**:",
                f"  - **Consumes**: `skills/{sk}/SKILL.md`, `skills/{sk}/references/*`, `skills/{sk}/scripts/*`, `python3 skills/skill-retro/scripts/skill_retro.py diagnose --skill {sk} --mode {norm_mode} --aspects {aspects_csv} --json`",
                "  - **Produces**: Softened CLI compatibility aliases, self-disclosing error guidance (`<=3 lines + references/*.md` pointer), and Positive + Counter-Case Replay tests",
                "",
                f"## Observed Telemetry Evidence (`mode={norm_mode}`, `aspects={aspects_csv}`)",
                "",
            ]
        )

        if "l0" in selected_aspects:
            w_lines.append("### Dim 1 (`l0`) — L0 Direction vs Healthy `R1->R2` Progressive Convergence")
            w_lines.append(
                f"- **Healthy `R1->R2` Convergences (Preserve, do NOT bloat L0)**: `{len(sk_converged)}` task(s) "
                f"| **True Directional Drifts (`r3+` / `CHANGE_REQUEST`)**: `{len(sk_drifts)}` task(s)"
            )
            for tm in sk_triggers:
                w_lines.append(
                    f"- **L0 Trigger Miss**: missed `{tm['missed_sessions']}/{tm['domain_sessions']}` domain sessions (`miss_rate={tm['miss_rate']}`)"
                )
            for ze in sk_empty:
                w_lines.append(
                    f"- **L0 Zero-Action Load**: `{ze['zero_action_sessions']}/{ze['loads']}` loads had 0 actions"
                )
            w_lines.append("")

        if "l1" in selected_aspects:
            w_lines.append("### Dim 2 (`l1`) — Scenario-Aware Reference Drawer Metabolism (`hits` vs `scenario_sessions`)")
            if sk_unwired or sk_hot or sk_ondemand or sk_deadwood:
                for uw in sk_unwired:
                    w_lines.append(
                        f"- **Unwired Disconnected Drawer**: `{uw['drawer']}` — `hits={uw['hits']}` vs `scenario_sessions={uw['scenario_sessions']}`, "
                        f"`domain_failures={uw['domain_failure_count']}` (wire self-disclosing pointer on script `exit=1`)"
                    )
                for hd in sk_hot:
                    w_lines.append(
                        f"- **Hot Drawer**: `{hd['drawer']}` — `hit_sessions={hd['hit_sessions']}/{hd['scenario_sessions']}` (`{hd['scenario_hit_rate']*100:.0f}%`); "
                        "sink repeated checklist lines into script"
                    )
                for od in sk_ondemand:
                    w_lines.append(
                        f"- **On-Demand Correction Drawer (Healthy — Preserve)**: `{od['drawer']}` — `hit_sessions={od['hit_sessions']}/{od['scenario_sessions']}`"
                    )
                for dw in sk_deadwood:
                    w_lines.append(
                        f"- **True Zero-Hit Deadwood**: `{dw['drawer']}` — `0` hits across `{dw['scenario_sessions']}` scenario sessions"
                    )
            else:
                w_lines.append("- No drawer anomalies recorded in current window (preserve low-frequency scenario docs with `scenario_sessions < 5`).")
            w_lines.append("")

        if "l2" in selected_aspects:
            w_lines.append("### Dim 3 (`l2`) — Script Efficacy & 4-Angle Slow Script Deep-Dive")
            for rb in sk_roadblocks:
                w_lines.append(
                    f"- **Rigid CLI Roadblock (`exit_code = 2`)**: `{rb['script']}` — `exit=2` count `{rb['exit_2_count']}` across `{rb['exit_2_sessions']}` session(s) "
                    f"(arg-only retry sessions: `{rb['arg_retry_sessions']}`)"
                )
                for sample in rb.get("failing_command_samples") or []:
                    w_lines.append(f"  - Failing command sample: `{sample}`")
            for tc in sk_catchers:
                w_lines.append(
                    f"- **True Catcher (Keep)**: `{tc['script']}` (`fail->pass` sessions=`{tc['fail_to_pass_sessions']}`, calls=`{tc['calls']}`)"
                )
            for cs in sk_ceremonial:
                w_lines.append(
                    f"- **Ceremonial Rubber-Stamp**: `{cs['script']}` (`100% exit=0` over `{cs['calls']}` runs, co-occurring skill escapes=`{cs['co_occurring_skill_escapes']}`)"
                )
            for ls in sk_sinks:
                w_lines.append(
                    f"- **Latency Sink (`>180s` / `P50>=30s`)**: `{ls['script']}` — `max_ms={ls['max_ms']}`, `p50_ms={ls['p50_ms']}`, "
                    f"`primary_bottleneck={ls['primary_bottleneck']}`, `nested_reruns={ls['nested_rerun_calls']}` "
                    f"({'; '.join(ls.get('attribution_angles') or [])})"
                )
                opts = ls.get("four_angle_speedup_options") or {}
                if opts:
                    w_lines.append(
                        f"  - **4-Angle Speedup Options**: (a) `{opts.get('angle_a_path_scoped')}` | (b) `{opts.get('angle_b_watermark_cache')}` | "
                        f"(c) `{opts.get('angle_c_parallel_dag')}` | (d) `{opts.get('angle_d_tiered_fast_fail')}`"
                    )
            if sk == "repo-kit" and cross_repo_health.get("available"):
                w_lines.append(
                    f"- **Cross-Repo Gate Health (`repo_gate_auditor.py`)**: `repos_scanned={cross_repo_health.get('repos_scanned', 0)}`, "
                    f"`drifted_repos={cross_repo_health.get('drifted_repos_count', 0)}`, "
                    f"`avg_score={cross_repo_health.get('average_gate_health_score', 100.0)}`, "
                    f"`total_findings={cross_repo_health.get('total_findings', 0)}`"
                )
            w_lines.append("")

        if "l3" in selected_aspects:
            w_lines.append("### Dim 4 (`l3`) — Wild Pattern Soft Crystallization (Facts-Only, Self-Disclosing, Hard vs Soft Gate)")
            if sk_wild:
                for wp in sk_wild:
                    w_lines.append(
                        f"- **Wild Manual Pattern (`>={WILD_PATTERN_MIN_COUNT}x`)**: `{wp['pattern_signature']}` (`{wp['occurrences']}` occurrences across `{wp['session_count']}` sessions; "
                        "crystallize as facts-only `soft_advisory_exit_0` with self-disclosing error pointer)"
                    )
            else:
                w_lines.append("- No wild manual command pattern >=5x for this skill in the current window.")
            w_lines.append("")

        if "creative" in selected_aspects:
            w_lines.append("### Dim 5 (`creative`) — Cross-Session Creative Spectrum (`Invariants + Product Persona + 2+1+1 Quota`)")
            w_lines.append(
                f"- **Preview Selections Observed**: `receipts={taste_eval.get('selection_receipt_count', 0)}`, "
                f"`2+1+1_compliant_goals={taste_eval.get('spectrum_2_1_1_compliant_goals', 0)}`, "
                f"`low_diversity_goals={taste_eval.get('low_diversity_goals', [])}`"
            )
            w_lines.append(
                "- **3-Tier Spectrum Contract**: Tier 1 Universal Invariants (alignment/hierarchy/platform firewall) + "
                "Tier 2 Product Persona Intent Weighting (primary visual anchor vs muted secondary budget) + "
                "Tier 3 Controlled Exploration (`2+1+1` quota: 2 persona-deepening + 1 cross-domain borrow + 1 bold wildcard)."
            )
            w_lines.append("")

        if "eval" in selected_aspects:
            flywheel_guard = l3.get("dual_flywheel_guard") or {}
            w_lines.append("### Dim 6 (`eval`) — 3-Tier Eval (`L1 Baseline / L2 Diversity / L3 Taste`) & Fast/Slow Flywheel Guardrails")
            w_lines.append(
                f"- **Flywheel Cohort Status**: `active_mode={flywheel_guard.get('active_mode', 'fast_flywheel_scope_triaged')}` "
                f"(`observed_sessions={flywheel_guard.get('observed_sessions', 0)}`, `slow_flywheel_ready={flywheel_guard.get('slow_flywheel_ready', False)}`, "
                f"`observation_pool_held={len(sk_held)}`)"
            )
            w_lines.append(
                "- **3-Tier Eval Audit**: Verify L1 Baseline deterministic assertions (`eval-cases.json`), L2 Variant Diversity & Intent Weighting, and L3 Human Taste (`jev-star` / `preview-selection.json`) backflow."
            )
            w_lines.append("")

        flywheel_guard = l3.get("dual_flywheel_guard") or {}
        w_lines.extend(
            [
                "## Mandatory Execution Guardrails (Scope Triage + Self-Disclosing Errors + Dual-Case Replay)",
                "",
                f"1. **Scope Triage (Active Flywheel: `{flywheel_guard.get('active_mode', 'fast_flywheel_scope_triaged')}`)**:",
                "   - **Objective Bug / `exit=2` CLI Friction**: Fix global parser compatibility aliases & positional defaults.",
                "   - **Scenario-Specific Requirement**: Encode as a **scenario-conditioned branch** — NEVER mutate global defaults across unrelated scenarios!",
                f"   - **Observation Pool Hold (`{len(sk_held)}` held items)**: Do NOT over-fit ambiguous single-session anecdotes into L0.",
                "2. **Self-Disclosing Error Guidance**:",
                f"   - Silent when passing; when failing (`exit=1` or `exit=2`), print `<=3` concrete remediation lines plus the exact `skills/{sk}/references/<drawer>.md` path.",
                "   - Wild manual patterns (`Layer 3`) crystallize ONLY as data-collection helpers (`soft_advisory_exit_0`), never subjective aesthetic hard-locks.",
                "3. **Positive + Counter-Case Replay Verification**:",
                "   - Add a **Positive Replay Case** proving the historical failing command / defect is now caught or accepted.",
                "   - Add a **Counter-Case Replay** proving standard/neighboring scenarios and existing baseline behavior are NOT broken or over-constrained.",
                "",
                "## Verification Commands",
                "",
                "```bash",
                f"python3 skills/skill-contract/scripts/skill_contract.py audit --skill {sk} --strict",
                f"python3 skills/skill-contract/scripts/skill_contract.py eval --skill {sk} --strict",
                "./scripts/verify",
                "```",
            ]
        )
        writer_md = "\n".join(w_lines).rstrip() + "\n"
        writer_path.write_text(writer_md, encoding="utf-8")
        if goal_briefs_dir.resolve() != briefs_dir.resolve():
            (goal_briefs_dir / m1_path.name).write_text(m1_md, encoding="utf-8")
            (goal_briefs_dir / writer_path.name).write_text(writer_md, encoding="utf-8")

        # Build Reviewer Brief (reusing standard `reviewer` role slot + `code-review` & `skill-contract`)
        r_lines: list[str] = [
            f"# Reviewer Brief: `{sk}` — Progressive Evolution & Anti-Rigidity Gate (`mode={norm_mode}`, `aspects={aspects_csv}`)",
            "",
            "- **Role Slot**: `reviewer` (standard `M1 TL -> Writer -> Reviewer` cross-vendor V1–V5 review; zero new agent role types)",
            f"- **Skills to Load**: `code-review`, `skill-contract` (target skill: `{sk}`)",
            f"- **Check Mode & Aspects**: `mode={norm_mode}` · `aspects={aspects_csv}`",
            f"- **Companion Writer Brief**: `{writer_path.name}`",
            f"- **Targeted Proposals**: `{len(sk_proposals)}` actionable (`{', '.join(str(p.get('id')) for p in sk_proposals) or 'baseline-audit'}`)",
            "",
            "## V1–V5 Review Checklist (Anti-Overcorrection, Host/Guest Isolation & Replay Proof)",
            "",
            "1. **Per-Skill Scope Isolation & Anti-Collision Lock 1 (`Axis 1`)**:",
            f"   - Confirm diff stays strictly within `{', '.join(owned_paths)}`; reject any edit to uncoupled Guest Skills, shared root files (`README.md`, `AGENTS.md`, `marketplace.json`), or scheduler/cron files (`feedback_guard.py pre-review-check`).",
            "   - Confirm `SKILL.md` and `references/*.md` pass `skill_contract.py audit --skill " + sk + " --strict` (L0/L1 line & byte caps, zero private identifiers).",
            "2. **Reject Global-Default Over-Correction (`Scope Triage Gate`)**:",
            "   - **FAIL (`[SPEC_GAP]` / `[IMPL_BUG]`)** if a scenario-specific preference was promoted into a global unconditional default instead of a scenario-conditioned branch.",
            "   - **FAIL** if low-occurrence scenario drawers (`scenario_sessions < 5`) were prematurely deleted as deadwood.",
            "3. **Reject Rigid Hardcoding & Opaque Errors (`Anti-Rigidity Gate`)**:",
            "   - **FAIL** if newly crystallized Layer 3 helpers hard-lock subjective choices (`exit=1`) instead of factual collection / soft advisory (`exit=0`).",
            f"   - Verify script error paths emit concise self-disclosing guidance pointing to `skills/{sk}/references/*.md`.",
            "4. **Verify Positive + Counter-Case Replay & Creative Spectrum (`Axis 2`)**:",
            "   - Verify the test suite executes BOTH a **Positive Case** (historical `exit=2` / escape fixed) AND a **Counter-Case** (neighboring scenario preserved without false-positive regression).",
            "   - For creative/design skills, verify `2+1+1` exploration quota and Product Persona Intent Weighting are preserved.",
            "",
            "## Mandatory Pre-Review & Audit Commands",
            "",
            "```bash",
            f"python3 skills/team-lead/scripts/feedback_guard.py pre-review-check --worktree . --brief {writer_path}",
            f"python3 skills/skill-contract/scripts/skill_contract.py audit --skill {sk} --strict",
            f"python3 skills/skill-contract/scripts/skill_contract.py eval --skill {sk} --strict",
            "```",
        ]
        reviewer_md = "\n".join(r_lines).rstrip() + "\n"
        reviewer_path.write_text(reviewer_md, encoding="utf-8")
        if goal_briefs_dir.resolve() != briefs_dir.resolve():
            (goal_briefs_dir / reviewer_path.name).write_text(reviewer_md, encoding="utf-8")

        brief_record = {
            "skill": sk,
            "goal_id": goal_id,
            "m1_tl_agent": m1_tl_agent_name,
            "m1_tl_agent_name": m1_tl_agent_name,
            "goal_handoff_dir": str(goal_handoff_dir),
            "goal_spec_path": str(spec_path),
            "goal_ledger_path": str(ledger_path),
            "spec_md": str(spec_path),
            "ledger_md": str(ledger_path),
            "m1_receipt_json": str(m1_receipt_path),
            "isolated_worktree": str(worktree_dir),
            "worktree_dir": str(worktree_dir),
            "m1_dispatch_cmd": herdr_dispatch_cmd,
            "herdr_dispatch_cmd": herdr_dispatch_cmd,
            "m1_outcomes_allowed": ["FIXED_AND_MERGED", "HEALTHY_NO_ISSUES", "CROSS_SKILL_RELAY"],
            "wave": 1,
            "mode": norm_mode,
            "aspects": selected_aspects,
            "coupled_with": coupled_for_sk,
            "owned_paths": owned_paths,
            "role_pipeline": ["M1-TL", "writer", "reviewer"],
            "m1_tl_brief": str(m1_path),
            "writer_brief": str(writer_path),
            "reviewer_brief": str(reviewer_path),
            "m1_tl_prompt": m1_md,
            "writer_prompt": writer_md,
            "reviewer_prompt": reviewer_md,
            "proposal_ids": [p.get("id") for p in sk_proposals],
        }
        emitted.append(brief_record)
        wave1_tasks.append(
            {
                "skill": sk,
                "goal_id": goal_id,
                "m1_tl_agent": m1_tl_agent_name,
                "m1_tl_agent_name": m1_tl_agent_name,
                "goal_handoff_dir": str(goal_handoff_dir),
                "goal_spec_path": str(spec_path),
                "spec_md": str(spec_path),
                "isolated_worktree": str(worktree_dir),
                "worktree_dir": str(worktree_dir),
                "herdr_dispatch_cmd": herdr_dispatch_cmd,
                "wave": 1,
                "mode": norm_mode,
                "aspects": selected_aspects,
                "owned_paths": owned_paths,
            }
        )

    if relays:
        for rel_spec in relays:
            if not isinstance(rel_spec, Mapping):
                continue
            from_sk = _norm_skill(str(rel_spec.get("from_skill") or norm_filter or (sorted(target_skills)[0] if target_skills else "")))
            to_sk = _norm_skill(str(rel_spec.get("to_skill") or ""))
            req_txt = str(rel_spec.get("requirement") or "")
            if from_sk and to_sk and from_sk != to_sk:
                rel_out = emit_cross_skill_relay(
                    from_skill=from_sk,
                    to_skill=to_sk,
                    requirement=req_txt,
                    emit_dir=emit_dir,
                    evidence_summary=str(rel_spec.get("evidence_summary") or ""),
                    wave=int(rel_spec.get("wave") or 2),
                )
                emitted_relays.append(rel_out)
                wave2_tasks.append(
                    {
                        "relay_id": rel_out["relay_id"],
                        "from_skill": from_sk,
                        "to_skill": to_sk,
                        "wave": rel_out["wave"],
                        "owned_paths": rel_out["owned_paths"],
                    }
                )

    overall_status = "ACTIONABLE" if (emitted or emitted_relays) else "HEALTHY_NO_ISSUES"
    m2_dispatch_plan = build_m2_dispatch_plan(emitted, handoff_root=handoff_root_base)
    return {
        "briefs_dir": str(briefs_dir),
        "handoff_root": str(handoff_root_base),
        "status": overall_status,
        "status_message": (
            f"Emitted {len(emitted)} per-skill M1 Goal spec(s) & brief(s)"
            if overall_status == "ACTIONABLE"
            else "healthy / 0 issues found (no changes needed)"
        ),
        "orchestration_topology": "1_M2 -> K_PARALLEL_M1_TLS (K=0..3) -> M2_ROLLUP",
        "m1_goals_to_dispatch": len(emitted),
        "m1_tls_to_spawn": len(emitted),
        "writers_spawned": len(emitted),
        "mode": norm_mode,
        "aspects": selected_aspects,
        "role_slots_reused": ["M1-TL", "writer", "reviewer"] if emitted else [],
        "new_agent_roles_created": 0,
        "count": len(emitted),
        "briefs": emitted,
        "m1_goal_specs": [
            {
                "skill": b["skill"],
                "goal_id": b["goal_id"],
                "m1_tl_agent": b["m1_tl_agent"],
                "m1_tl_agent_name": b["m1_tl_agent_name"],
                "goal_handoff_dir": b["goal_handoff_dir"],
                "goal_spec_path": b["goal_spec_path"],
                "goal_ledger_path": b["goal_ledger_path"],
                "spec_md": b["spec_md"],
                "ledger_md": b["ledger_md"],
                "m1_receipt_json": b["m1_receipt_json"],
                "isolated_worktree": b["isolated_worktree"],
                "worktree_dir": b["worktree_dir"],
                "m1_dispatch_cmd": b["m1_dispatch_cmd"],
                "herdr_dispatch_cmd": b["herdr_dispatch_cmd"],
                "m1_outcomes_allowed": b["m1_outcomes_allowed"],
            }
            for b in emitted
        ],
        "m2_dispatch_plan": m2_dispatch_plan,
        "cross_skill_relays": emitted_relays,
        "wave_plan": {
            "wave_1_disjoint_hosts": wave1_tasks,
            "wave_2_cross_skill_relays": wave2_tasks,
        },
    }


def _deduplicate_session_script_calls(obs: SessionObservation) -> list[ScriptCallEvent]:
    """Chronologically sort and deduplicate adjacent cross-source mirrored calls per `(skill, script_rel)` without crossing an intervening pass."""
    if not obs.script_calls:
        return []
    dedup_by_validator: dict[tuple[str, str], list[ScriptCallEvent]] = {}
    retained: list[ScriptCallEvent] = []
    for c_ev in sorted(obs.script_calls, key=lambda c: (c.ts, c.seq)):
        v_key = (c_ev.skill, c_ev.script_rel)
        v_hist = dedup_by_validator.setdefault(v_key, [])
        if v_hist:
            last_v = v_hist[-1]
            if (
                last_v.origin_source != c_ev.origin_source
                and last_v.exit_code == c_ev.exit_code
                and abs((c_ev.ts - last_v.ts).total_seconds()) < 1.0
            ):
                continue
        v_hist.append(c_ev)
        retained.append(c_ev)
    obs.script_calls = retained
    return retained


def _evaluate_session_progressive_and_preread(
    loaded_for_sk: Sequence[SessionObservation],
    sk_name: str,
) -> tuple[int, list[str], int]:
    """Compute (progressive_catch_count, wasted_preread_sids, unresolved_failure_count) for a skill across sessions.

    Establishes a unified chronological global timeline (`timeline_idx`) across all reads, edits, script calls,
    and bash commands in the session (sorting by `(ts, seq)` and deduplicating mirrored cross-source calls):
      - Wasted pre-read: reading >=3 distinct drawers cold-start before the first script call or edit on the
        unified timeline, OR reading drawer(s) after which zero downstream actions occur (`action_idx > last_read_idx`).
      - Progressive catch: `exit=1` on `script_rel` followed within 1-2 turns (`<=6` unified global timeline steps,
        `<=2` intervening calls of the same `script_rel`) by reading a drawer or editing code and passing `exit=0`
        on the SAME `script_rel`.
      - Unresolved failure count: count of distinct `(session_id, script_rel)` validators whose chronologically
        last failure is not followed by a subsequent `exit=0` on the same `script_rel`.
    """
    wasted_sids: list[str] = []
    prog_catches = 0
    unresolved_failures = 0

    for o in loaded_for_sk:
        # Build unified chronological session timeline across all sources
        raw_timeline: list[tuple[datetime, int, str, Any]] = []
        for r_sk, r_rel, r_ts, r_seq in o.ref_reads:
            raw_timeline.append((r_ts, r_seq, "read", (r_sk, r_rel)))

        edit_ev_list = list(o.edit_events)
        if not edit_ev_list and o.edit_seqs:
            edit_ev_list = [(o.started_at, e_seq) for e_seq in o.edit_seqs]
        for e_ts, e_seq in edit_ev_list:
            raw_timeline.append((e_ts, e_seq, "edit", None))

        all_call_seqs = {c.seq for c in o.script_calls}
        for c_ev in _deduplicate_session_script_calls(o):
            raw_timeline.append((c_ev.ts, c_ev.seq, "script", c_ev))

        bash_ev_list = list(o.bash_events)
        if not bash_ev_list and o.bash_commands:
            bash_ev_list = [(cmd, ec, dur, o.started_at, b_seq) for cmd, ec, dur, b_seq in o.bash_commands]
        for _, _, _, b_ts, b_seq in bash_ev_list:
            if b_seq in all_call_seqs:
                continue
            raw_timeline.append((b_ts, b_seq, "bash", None))

        raw_timeline.sort(key=lambda item: (item[0], item[1]))

        sk_reads_tl: list[tuple[str, int]] = []
        sk_edits_tl: list[int] = []
        all_actions_tl: list[int] = []
        sk_calls_tl: list[tuple[int, ScriptCallEvent]] = []

        for tl_idx, (_, _, kind, payload) in enumerate(raw_timeline, 1):
            if kind == "read":
                r_sk, r_rel = payload
                if r_sk == sk_name:
                    sk_reads_tl.append((r_rel, tl_idx))
            elif kind == "edit":
                sk_edits_tl.append(tl_idx)
                all_actions_tl.append(tl_idx)
            elif kind == "script":
                c_obj: ScriptCallEvent = payload
                all_actions_tl.append(tl_idx)
                if c_obj.skill == sk_name:
                    sk_calls_tl.append((tl_idx, c_obj))
            elif kind == "bash":
                all_actions_tl.append(tl_idx)

        first_call_or_edit_idx = min(
            [idx for idx, _ in sk_calls_tl] + sk_edits_tl,
            default=None,
        )
        cold_start_distinct_drawers = {
            r_rel
            for r_rel, r_idx in sk_reads_tl
            if first_call_or_edit_idx is None or r_idx < first_call_or_edit_idx
        }
        last_read_idx = max((r_idx for _, r_idx in sk_reads_tl), default=None)
        has_action_after_reads = (
            any(a_idx > last_read_idx for a_idx in all_actions_tl)
            if last_read_idx is not None
            else bool(all_actions_tl or o.edit_count > 0)
        )
        if len(cold_start_distinct_drawers) >= 3 or (sk_reads_tl and not has_action_after_reads):
            wasted_sids.append(o.session_id)

        # Evaluate per-validator (script_rel) failure and recovery on the unified global timeline
        by_validator: dict[str, list[tuple[int, ScriptCallEvent]]] = {}
        for tl_idx, c_obj in sk_calls_tl:
            by_validator.setdefault(c_obj.script_rel, []).append((tl_idx, c_obj))

        for _, v_items in by_validator.items():
            failed_positions = [pos for pos, (_, c_obj) in enumerate(v_items) if c_obj.exit_code not in (None, 0, 2)]
            if not failed_positions:
                continue
            validator_recovered = False
            last_recovered_pos = -1
            for pos, (fail_tl_idx, call_ev) in enumerate(v_items):
                if call_ev.exit_code in (None, 0, 2):
                    continue
                if pos < last_recovered_pos:
                    continue
                later_same_script = [
                    (pos + offset, pass_tl_idx, later)
                    for offset, (pass_tl_idx, later) in enumerate(v_items[pos + 1 : pos + 3], 1)
                    if later.exit_code == 0 and 0 < (pass_tl_idx - fail_tl_idx) <= 6
                ]
                for pass_pos, pass_tl_idx, _ in later_same_script:
                    intervening_read_or_edit = any(
                        fail_tl_idx < r_idx < pass_tl_idx for _, r_idx in sk_reads_tl
                    ) or any(
                        fail_tl_idx < e_idx < pass_tl_idx for e_idx in sk_edits_tl
                    )
                    if intervening_read_or_edit:
                        validator_recovered = True
                        last_recovered_pos = max(last_recovered_pos, pass_pos)
                        break
            if validator_recovered:
                prog_catches += 1
            last_fail_pos = max(failed_positions)
            has_pass_after_last_fail = any(c_obj.exit_code == 0 for _, c_obj in v_items[last_fail_pos + 1 :])
            if not has_pass_after_last_fail:
                unresolved_failures += 1

    return prog_catches, wasted_sids, unresolved_failures


def compute_skill_retro_diagnostics(
    *,
    inventory: Mapping[str, Mapping[str, Any]],
    skill_filter: str | None = None,
    emit_briefs_dir: Path | None = None,
    mode: str = "full",
    aspects: Sequence[str] | str | None = None,
    coupled_with: Sequence[str] | str | None = None,
    relays: Sequence[Mapping[str, Any]] | None = None,
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    copilot_sessions_dir: Path | None = None,
    usage_jsonl: Path | None = None,
    telemetry_shards_dir: Path | None = None,
    trace_export_path: Path | None = None,
    handoff_root: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    days: int = 30,
    shard_reader: Callable[[Path | None, int], list[dict[str, Any]]] | None = None,
    queue_fn: Callable[[list[dict[str, Any]], Path], list[dict[str, Any]]] | None = None,
    auto_propose: bool = False,
    proposals_path: Path | None = None,
) -> dict[str, Any]:
    """Run the 4-Layer / 6-Dimension Progressive Self-Evolution & Wide-Probe Multi-Angle Skill Diagnostic."""
    s_root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    prop_path = proposals_path or DEFAULT_PROPOSALS_PATH
    norm_mode = "partial" if str(mode or "full").strip().lower() == "partial" else "full"
    selected_aspects = _normalize_aspects(aspects, mode=norm_mode)
    if norm_mode == "full":
        selected_aspects = list(CANONICAL_ASPECTS)
    norm_filter = _norm_skill(skill_filter) if skill_filter else ""
    if norm_filter:
        inventory = {k: v for k, v in inventory.items() if _norm_skill(k) == norm_filter}

    now_cst = datetime.now(CST)
    today_midnight = now_cst.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = today_midnight - timedelta(days=max(1, days) - 1)
    window_end = today_midnight + timedelta(days=1)

    known_skills = set(inventory.keys())
    basename_to_targets: dict[str, list[tuple[str, str]]] = {}
    for sk_name, meta in inventory.items():
        for rel_sc in meta.get("script_entries", []):
            bname = Path(str(rel_sc)).name
            basename_to_targets.setdefault(bname, []).append((sk_name, str(rel_sc)))

    observations = _parse_copilot_sessions(
        copilot_sessions_dir,
        window_start=window_start,
        window_end=window_end,
        known_skills=known_skills,
        basename_to_targets=basename_to_targets,
    )
    shards: list[dict[str, Any]] = []
    if shard_reader and telemetry_shards_dir:
        with contextlib.suppress(OSError):
            shards = shard_reader(telemetry_shards_dir, days)
    _ingest_usage_jsonl_and_shards(
        observations,
        usage_jsonl=usage_jsonl,
        shard_records=shards,
        window_start=window_start,
        window_end=window_end,
        known_skills=known_skills,
        inventory=inventory,
    )
    ingested_trace_spans = _ingest_trace_export(
        observations,
        trace_export_path=trace_export_path,
        window_start=window_start,
        window_end=window_end,
        known_skills=known_skills,
    )
    wh_meta = _ingest_warehouse_and_copilot_db(
        observations,
        warehouse_db=warehouse_db,
        copilot_db=copilot_db,
        window_start=window_start,
        window_end=window_end,
        known_skills=known_skills,
        basename_to_targets=basename_to_targets,
    )

    all_sessions = list(observations.values())
    for obs_item in all_sessions:
        _deduplicate_session_script_calls(obs_item)
    total_sessions = len(all_sessions)
    all_stamps = [obs.ended_at for obs in all_sessions]
    source_as_of = max(all_stamps).isoformat() if all_stamps else None
    sources_present = any(
        _safe_is_file(p) or _safe_is_dir(p)
        for p in (warehouse_db, copilot_db, copilot_sessions_dir, usage_jsonl, trace_export_path)
    )
    coverage_status = "healthy" if total_sessions > 0 else ("unknown" if sources_present else "missing")

    # ------------------------------------------------------------------
    # Layer 0: Direction & Trigger Guardrails
    # ------------------------------------------------------------------
    trigger_misses: dict[str, list[dict[str, Any]]] = {}
    domain_occurrences: dict[str, set[str]] = {sk: set() for sk in known_skills}

    for obs in all_sessions:
        for sk in obs.skills_loaded:
            domain_occurrences.setdefault(sk, set()).add(obs.session_id)
        for c in obs.script_calls:
            if c.skill in known_skills:
                domain_occurrences.setdefault(c.skill, set()).add(obs.session_id)
        cmd_texts = {cmd for cmd, _, _, _ in obs.bash_commands}
        for target_sk, matcher in _DOMAIN_HINTS:
            if target_sk not in known_skills:
                continue
            if matcher(obs.edited_files, cmd_texts):
                domain_occurrences.setdefault(target_sk, set()).add(obs.session_id)
                used_script = any(c.skill == target_sk for c in obs.script_calls)
                if target_sk not in obs.skills_loaded and not used_script:
                    sample_files = sorted(
                        _redact_command_sample(f, skills_root=s_root, dev_root=d_root)
                        for f in list(obs.edited_files)[:3]
                    )
                    trigger_misses.setdefault(target_sk, []).append(
                        {
                            "session_id": obs.session_id[:12],
                            "evidence_files": sample_files,
                        }
                    )

    zero_action_loads: list[dict[str, Any]] = []
    for sk_name in sorted(known_skills):
        loaded_obs = [o for o in all_sessions if sk_name in o.skills_loaded]
        if not loaded_obs:
            continue
        empty_obs = [
            o
            for o in loaded_obs
            if not any(r_sk == sk_name for r_sk, _, _, _ in o.ref_reads)
            and not any(c.skill == sk_name for c in o.script_calls)
            and o.edit_count == 0
            and len(o.bash_commands) == 0
        ]
        rate = round(len(empty_obs) / len(loaded_obs), 4)
        if empty_obs and (len(empty_obs) >= 2 or rate >= 0.5):
            zero_action_loads.append(
                {
                    "skill": sk_name,
                    "loads": len(loaded_obs),
                    "zero_action_sessions": len(empty_obs),
                    "zero_action_rate": rate,
                }
            )

    trigger_miss_items = [
        {
            "skill": sk,
            "missed_sessions": len(items),
            "domain_sessions": len(domain_occurrences.get(sk, set())),
            "miss_rate": round(len(items) / max(1, len(domain_occurrences.get(sk, set()))), 4),
            "samples": items[:3],
        }
        for sk, items in sorted(trigger_misses.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]

    layer0 = {
        "trigger_misses": trigger_miss_items,
        "zero_action_loads": sorted(zero_action_loads, key=lambda x: (-x["zero_action_sessions"], x["skill"])),
        "healthy_r1_to_r2_convergences": wh_meta["r1_to_r2_converged_tasks"][:10],
        "healthy_convergence_count": len(wh_meta["r1_to_r2_converged_tasks"]),
        "directional_drifts": wh_meta["directional_drift_tasks"][:10],
        "directional_drift_count": len(wh_meta["directional_drift_tasks"]),
    }

    # ------------------------------------------------------------------
    # Layer 1: Reference Drawer Metabolism (Context-Aware)
    # ------------------------------------------------------------------
    hot_drawers: list[dict[str, Any]] = []
    on_demand_correction_drawers: list[dict[str, Any]] = []
    unwired_disconnected_drawers: list[dict[str, Any]] = []
    true_zero_hit_deadwood: list[dict[str, Any]] = []
    dormant_scenario_drawers: list[dict[str, Any]] = []
    progressive_catch_by_skill: dict[str, int] = {sk: 0 for sk in known_skills}
    wasted_preread_by_skill: dict[str, dict[str, Any]] = {}

    for sk_name, meta in sorted(inventory.items()):
        loaded_for_sk = [
            o
            for o in all_sessions
            if sk_name in o.skills_loaded
            or any(r_sk == sk_name for r_sk, _, _, _ in o.ref_reads)
            or any(c.skill == sk_name for c in o.script_calls)
        ]
        prog_catches, wasted_sids, unresolved_validator_failures = _evaluate_session_progressive_and_preread(
            loaded_for_sk, sk_name
        )
        progressive_catch_by_skill[sk_name] = prog_catches
        loaded_cnt = len(loaded_for_sk)
        wasted_rate = round(len(wasted_sids) / loaded_cnt, 4) if loaded_cnt > 0 else 0.0
        wasted_preread_by_skill[sk_name] = {
            "skill": sk_name,
            "loaded_sessions": loaded_cnt,
            "wasted_preread_sessions": len(wasted_sids),
            "wasted_preread_rate": wasted_rate,
            "progressive_catch_count": prog_catches,
            "unresolved_validator_failures": unresolved_validator_failures,
        }

        ref_entries = list(meta.get("reference_entries") or [])
        if not ref_entries:
            continue
        pkg_path = Path(str(meta.get("path") or (s_root / sk_name)))
        scen_sids = domain_occurrences.get(sk_name, set())
        scenario_count = len(scen_sids)
        failing_sids = {
            o.session_id
            for o in all_sessions
            if any(c.skill == sk_name and c.exit_code not in (None, 0, 2) for c in o.script_calls)
        }
        escape_counts = wh_meta["skill_escapes"].get(sk_name, {"impl_bug": 0, "spec_gap": 0})
        total_domain_failures = unresolved_validator_failures + escape_counts.get("impl_bug", 0) + escape_counts.get("spec_gap", 0)

        for ref_info in ref_entries:
            rel_ref = str(ref_info.get("rel_path") or "")
            if not rel_ref.startswith("references/"):
                continue
            hit_sessions: set[str] = set()
            total_hits = 0
            post_failure_reads = 0
            for o in all_sessions:
                matching_reads = [(ts, seq) for r_sk, r_rel, ts, seq in o.ref_reads if r_sk == sk_name and r_rel == rel_ref]
                if not matching_reads:
                    continue
                hit_sessions.add(o.session_id)
                total_hits += len(matching_reads)
                first_fail_seq = min(
                    (c.seq for c in o.script_calls if c.skill == sk_name and c.exit_code not in (None, 0)),
                    default=None,
                )
                if first_fail_seq is not None and any(seq > first_fail_seq for _, seq in matching_reads):
                    post_failure_reads += 1

            hit_sess_cnt = len(hit_sessions)
            hit_rate = round(hit_sess_cnt / scenario_count, 4) if scenario_count > 0 else 0.0
            script_linked = _drawer_linked_in_scripts(pkg_path, rel_ref)
            l0_linked = _drawer_linked_in_l0(pkg_path, rel_ref)
            base_row = {
                "skill": sk_name,
                "drawer": rel_ref,
                "hits": total_hits,
                "hit_sessions": hit_sess_cnt,
                "scenario_sessions": scenario_count,
                "scenario_hit_rate": hit_rate,
                "line_count": int(ref_info.get("line_count") or 0),
                "tokens_est": int(ref_info.get("tokens_est") or 0),
                "linked_in_scripts": script_linked,
                "linked_in_l0": l0_linked,
            }

            if scenario_count >= 3 and hit_rate >= 0.60 and hit_sess_cnt >= 3:
                hot_drawers.append(
                    {
                        **base_row,
                        "classification": "hot_drawer",
                        "evolution_action": "Sink repeated command/checklist lines into deterministic script or concise L0 table.",
                    }
                )
            elif post_failure_reads >= 1 or (hit_sess_cnt >= 1 and hit_rate < 0.60 and (failing_sids & hit_sessions or script_linked)):
                on_demand_correction_drawers.append(
                    {
                        **base_row,
                        "post_failure_reads": post_failure_reads,
                        "classification": "on_demand_correction_drawer",
                        "evolution_action": "Healthy runtime progressive disclosure — preserve drawer and keep self-disclosing error pointer.",
                    }
                )
            elif total_domain_failures >= 1 and hit_sess_cnt == 0 and not script_linked and l0_linked:
                unwired_disconnected_drawers.append(
                    {
                        **base_row,
                        "domain_failure_count": total_domain_failures,
                        "classification": "unwired_disconnected_drawer",
                        "evolution_action": (
                            f"Domain/script failed {total_domain_failures}x without reading `{rel_ref}`; "
                            f"print self-disclosing pointer to `skills/{sk_name}/{rel_ref}` on script exit=1."
                        ),
                    }
                )
            elif scenario_count >= 5 and hit_sess_cnt == 0:
                true_zero_hit_deadwood.append(
                    {
                        **base_row,
                        "classification": "true_zero_hit_deadwood",
                        "evolution_action": f"Scenario occurred {scenario_count}x with 0 reads — candidate for archive/merge.",
                    }
                )
            elif hit_sess_cnt == 0:
                dormant_scenario_drawers.append(
                    {
                        **base_row,
                        "classification": "dormant_scenario_drawer",
                        "evolution_action": "Low scenario occurrence; retain without premature deletion.",
                    }
                )

    layer1 = {
        "hot_drawers": sorted(hot_drawers, key=lambda r: (-r["hit_sessions"], r["skill"], r["drawer"])),
        "on_demand_correction_drawers": sorted(
            on_demand_correction_drawers,
            key=lambda r: (-r.get("post_failure_reads", 0), -r["hit_sessions"], r["skill"]),
        ),
        "unwired_disconnected_drawers": sorted(
            unwired_disconnected_drawers,
            key=lambda r: (-r["domain_failure_count"], -r["scenario_sessions"], r["skill"], r["drawer"]),
        ),
        "true_zero_hit_deadwood": sorted(
            true_zero_hit_deadwood,
            key=lambda r: (-r["scenario_sessions"], -r["tokens_est"], r["skill"], r["drawer"]),
        ),
        "dormant_scenario_drawers_count": len(dormant_scenario_drawers),
        "progressive_catch_count": sum(progressive_catch_by_skill.values()),
        "progressive_catch_by_skill": progressive_catch_by_skill,
        "wasted_preread_by_skill": wasted_preread_by_skill,
    }

    # ------------------------------------------------------------------
    # Layer 2: Script Efficacy & Multi-Angle Speed Diagnosis
    # ------------------------------------------------------------------
    calls_by_script: dict[tuple[str, str], list[ScriptCallEvent]] = {}
    for sk_name, meta in inventory.items():
        for rel_sc in meta.get("script_entries", []):
            calls_by_script.setdefault((sk_name, str(rel_sc)), [])
    for o in all_sessions:
        for c in o.script_calls:
            calls_by_script.setdefault((c.skill, c.script_rel), []).append(c)

    cross_repo_gate_health: dict[str, Any] = {
        "available": False,
        "repos_scanned": 0,
        "healthy_repos_count": 0,
        "drifted_repos_count": 0,
        "average_gate_health_score": 100.0,
        "total_findings": 0,
        "rule_counts": {},
        "speedup_4_angles": {},
        "repos": [],
        "actionable_proposals": [],
    }
    if "repo-kit" in inventory or norm_filter == "repo-kit":
        cross_repo_gate_health = _compute_cross_repo_gate_health(skills_root=s_root, dev_root=d_root)

    true_catchers: list[dict[str, Any]] = []
    ceremonial_rubber_stamps: list[dict[str, Any]] = []
    rigid_roadblocks: list[dict[str, Any]] = []
    watermarked_resolved_roadblocks: list[dict[str, Any]] = []
    latency_sinks: list[dict[str, Any]] = []
    aci_output_bloat: list[dict[str, Any]] = []
    aci_output_bloat_by_skill: dict[str, int] = {sk: 0 for sk in known_skills}

    for (sk_name, sc_rel), sc_calls in sorted(calls_by_script.items()):
        if not sc_calls:
            continue
        ordered_calls = sorted(sc_calls, key=lambda c: (c.ts, c.seq))
        bloated_calls = [c for c in ordered_calls if c.aci_output_bloat or c.output_chars > ACI_OUTPUT_BLOAT_CHARS]
        if bloated_calls:
            aci_output_bloat_by_skill[sk_name] = aci_output_bloat_by_skill.get(sk_name, 0) + len(bloated_calls)
            aci_output_bloat.append(
                {
                    "skill": sk_name,
                    "script": sc_rel,
                    "bloat_call_count": len(bloated_calls),
                    "max_output_chars": max((c.output_chars for c in bloated_calls), default=ACI_OUTPUT_BLOAT_CHARS + 1),
                    "classification": "aci_output_bloat",
                    "evolution_action": "Add `--summary` / bounded output cap (`<=20KB`) so L2 CLI output does not overflow agent context.",
                }
            )
        graded = [c for c in ordered_calls if c.exit_code is not None]
        exit0_cnt = sum(1 for c in graded if c.exit_code == 0)
        exit1_cnt = sum(1 for c in graded if c.exit_code not in (0, 2))
        exit2_calls = [c for c in graded if c.exit_code == 2]

        sk_meta = inventory.get(sk_name) or {}
        current_skill_ver = str(sk_meta.get("skill_version") or "").strip() or None
        script_wm_map = sk_meta.get("script_watermarks") if isinstance(sk_meta.get("script_watermarks"), Mapping) else {}
        watermark_epoch: int | None = None
        if isinstance(script_wm_map, Mapping) and sc_rel in script_wm_map:
            watermark_epoch = int(script_wm_map[sc_rel])
        elif sk_meta.get("last_modified_epoch") is not None:
            watermark_epoch = int(sk_meta["last_modified_epoch"])

        pre_fix_exit2_calls: list[ScriptCallEvent] = []
        post_fix_exit2_calls: list[ScriptCallEvent] = []
        for c in exit2_calls:
            if _is_call_post_fix(
                c.ts,
                c.skill_version,
                current_skill_version=current_skill_ver,
                watermark_epoch=watermark_epoch,
            ):
                post_fix_exit2_calls.append(c)
            else:
                pre_fix_exit2_calls.append(c)

        # Check fail -> pass transitions within the same session (or across consecutive runs)
        by_sess: dict[str, list[ScriptCallEvent]] = {}
        for c in graded:
            by_sess.setdefault(c.session_id, []).append(c)

        fail_to_pass_sessions = 0
        exit2_arg_only_sessions = 0
        post_fix_exit2_sids = {c.session_id for c in post_fix_exit2_calls}
        for sid, s_calls in by_sess.items():
            saw_fail1 = False
            caught_in_sess = False
            saw_exit2 = False
            for c in s_calls:
                if c.exit_code not in (None, 0, 2):
                    saw_fail1 = True
                elif c.exit_code == 2:
                    saw_exit2 = True
                elif c.exit_code == 0 and saw_fail1:
                    caught_in_sess = True
            if caught_in_sess:
                fail_to_pass_sessions += 1
            if saw_exit2 and sid in post_fix_exit2_sids:
                obs_ref = observations.get(sid)
                if obs_ref is None or obs_ref.edit_count == 0 or any(x.exit_code == 0 for x in s_calls):
                    exit2_arg_only_sessions += 1

        measured_durs = [float(c.duration_ms) for c in ordered_calls if c.measured and c.duration_ms is not None]
        p50_ms = _percentile(measured_durs, 50)
        max_ms = round(max(measured_durs), 2) if measured_durs else None
        escapes_for_skill = wh_meta["skill_escapes"].get(sk_name, {"impl_bug": 0, "spec_gap": 0})
        total_skill_escapes = escapes_for_skill.get("impl_bug", 0) + escapes_for_skill.get("spec_gap", 0)

        if fail_to_pass_sessions > 0:
            true_catchers.append(
                {
                    "skill": sk_name,
                    "script": sc_rel,
                    "calls": len(ordered_calls),
                    "fail_to_pass_sessions": fail_to_pass_sessions,
                    "non_zero_catches": exit1_cnt,
                    "p50_ms": p50_ms,
                    "classification": "true_catcher",
                }
            )
        elif len(graded) >= 3 and exit0_cnt == len(graded):
            ceremonial_status = (
                "ceremonial_ leaky_gate" if total_skill_escapes > 0 else ("fast_baseline_guard" if (p50_ms or 0.0) <= 500.0 else "ceremonial_rubber_stamp")
            )
            if total_skill_escapes > 0 or (p50_ms or 0.0) > 500.0 or len(graded) >= 5:
                ceremonial_rubber_stamps.append(
                    {
                        "skill": sk_name,
                        "script": sc_rel,
                        "calls": len(ordered_calls),
                        "exit_0_rate": 1.0,
                        "co_occurring_skill_escapes": total_skill_escapes,
                        "p50_ms": p50_ms,
                        "sub_status": ceremonial_status.strip(),
                        "classification": "ceremonial_rubber_stamp",
                        "evolution_action": (
                            f"100% exit=0 across {len(graded)} runs while `{sk_name}` had {total_skill_escapes} review escapes — deepen assertions."
                            if total_skill_escapes > 0
                            else "100% exit=0 — keep silent if <0.5s baseline, or merge into batch gate."
                        ),
                    }
                )

        if exit2_calls:
            active_e2 = post_fix_exit2_calls
            samples: list[str] = []
            for c in (active_e2 if active_e2 else pre_fix_exit2_calls):
                red = _redact_command_sample(c.command, skills_root=s_root, dev_root=d_root)
                if red and red not in samples:
                    samples.append(red)
                if len(samples) >= 3:
                    break
            wm_base = {
                "skill": sk_name,
                "script": sc_rel,
                "skill_version": current_skill_ver,
                "last_modified_epoch": watermark_epoch,
                "calls": len(ordered_calls),
                "total_exit_2_count": len(exit2_calls),
                "pre_fix": len(pre_fix_exit2_calls),
                "post_fix": len(post_fix_exit2_calls),
                "pre_fix_count": len(pre_fix_exit2_calls),
                "post_fix_count": len(post_fix_exit2_calls),
                "failing_command_samples": samples,
            }
            if len(post_fix_exit2_calls) > 0:
                rigid_roadblocks.append(
                    {
                        **wm_base,
                        "exit_2_count": len(post_fix_exit2_calls),
                        "exit_2_sessions": len(post_fix_exit2_sids),
                        "arg_retry_sessions": exit2_arg_only_sessions,
                        "watermark_status": "active_or_regression" if len(pre_fix_exit2_calls) > 0 else "active_post_fix",
                        "classification": "rigid_roadblock",
                        "evolution_action": "Add CLI flag aliases / positional path fallback and print 3-line usage hint on exit=2.",
                    }
                )
            else:
                watermarked_resolved_roadblocks.append(
                    {
                        **wm_base,
                        "exit_2_count": 0,
                        "exit_2_sessions": 0,
                        "arg_retry_sessions": 0,
                        "watermark_status": "resolved_in_current_version",
                        "classification": "watermarked_resolved_roadblock",
                        "evolution_action": f"Watermarked out: {len(pre_fix_exit2_calls)} pre-fix exit=2 error(s) resolved in {current_skill_ver or 'current version'} (post_fix=0).",
                    }
                )

        is_latency_sink = (max_ms is not None and max_ms >= LATENCY_SINK_MAX_MS) or (
            p50_ms is not None and p50_ms >= LATENCY_SINK_P50_MS and len(measured_durs) >= LATENCY_SINK_MIN_CALLS_FOR_P50
        )
        if is_latency_sink:
            speed_diag = _multi_angle_speed_diagnosis(sc_rel, ordered_calls)
            sink_entry: dict[str, Any] = {
                "skill": sk_name,
                "script": sc_rel,
                "calls": len(ordered_calls),
                "measured_calls": len(measured_durs),
                "classification": "latency_sink",
                **speed_diag,
            }
            if sk_name == "repo-gate" and "repo-kit" in inventory:
                sink_entry["owner_skill"] = "repo-kit"
            latency_sinks.append(sink_entry)

    internal_reality_by_skill: dict[str, dict[str, Any]] = {}
    eval_quality_by_skill: dict[str, dict[str, Any]] = {}
    for sk_name, meta in sorted(inventory.items()):
        pkg_p = Path(str(meta.get("path") or (s_root / sk_name)))
        ir = meta.get("internal_reality") if isinstance(meta.get("internal_reality"), Mapping) else internal_reality_check(pkg_p)
        internal_reality_by_skill[sk_name] = dict(ir)
        esc_map = wh_meta["skill_escapes"].get(sk_name, {"impl_bug": 0, "spec_gap": 0})
        sk_escapes = int(esc_map.get("impl_bug") or 0) + int(esc_map.get("spec_gap") or 0)
        unres_v_fails = int((wasted_preread_by_skill.get(sk_name) or {}).get("unresolved_validator_failures") or 0)
        sk_dom_fails = unres_v_fails + sk_escapes
        ev_cnt = int(meta.get("eval_case_count") or 0)
        ev_counter_cnt = int(meta.get("eval_counter_case_count") or 0)
        ev_counter_ratio = float(meta.get("eval_counter_case_ratio") or (round(ev_counter_cnt / ev_cnt, 4) if ev_cnt > 0 else 0.0))
        ev_weekly_delta = int(meta.get("eval_case_weekly_delta") or 0)
        ev_escape_gap = bool((sk_dom_fails + sk_escapes) >= 3 and ev_weekly_delta == 0)
        eval_quality_by_skill[sk_name] = {
            "skill": sk_name,
            "eval_case_count": ev_cnt,
            "eval_counter_case_count": ev_counter_cnt,
            "eval_counter_case_ratio": ev_counter_ratio,
            "eval_case_weekly_delta": ev_weekly_delta,
            "domain_failures": sk_dom_fails,
            "skill_escapes": sk_escapes,
            "eval_escape_gap": ev_escape_gap,
        }

    layer2 = {
        "true_catchers": sorted(true_catchers, key=lambda r: (-r["fail_to_pass_sessions"], -r["calls"], r["script"])),
        "ceremonial_rubber_stamps": sorted(
            ceremonial_rubber_stamps,
            key=lambda r: (-r["co_occurring_skill_escapes"], -r["calls"], r["script"]),
        ),
        "rigid_roadblocks": sorted(rigid_roadblocks, key=lambda r: (-r["exit_2_count"], -r["calls"], r["script"])),
        "watermarked_resolved_roadblocks": sorted(
            watermarked_resolved_roadblocks,
            key=lambda r: (-r["pre_fix"], -r["calls"], r["script"]),
        ),
        "latency_sinks": sorted(latency_sinks, key=lambda r: (-(r["max_ms"] or 0.0), -(r["p50_ms"] or 0.0), r["script"])),
        "aci_output_bloat": sorted(aci_output_bloat, key=lambda r: (-r["bloat_call_count"], r["script"])),
        "aci_output_bloat_count": sum(aci_output_bloat_by_skill.values()),
        "aci_output_bloat_by_skill": aci_output_bloat_by_skill,
        "internal_reality_by_skill": internal_reality_by_skill,
        "cross_repo_gate_health": cross_repo_gate_health,
    }

    # ------------------------------------------------------------------
    # Layer 3: Wild Pattern Crystallization & Cross-Session Taste/Eval
    # ------------------------------------------------------------------
    wild_buckets: dict[str, dict[str, Any]] = {}
    for o in all_sessions:
        for cmd_str, _, _, _ in o.bash_commands:
            extracted = _extract_wild_manual_signature(cmd_str, skills_root=s_root, dev_root=d_root)
            if extracted is None:
                continue
            sig, redacted_example = extracted
            bucket = wild_buckets.setdefault(
                sig,
                {"pattern_signature": sig, "count": 0, "sessions": set(), "skills_context": set(), "examples": []},
            )
            bucket["count"] += 1
            bucket["sessions"].add(o.session_id)
            bucket["skills_context"].update(o.skills_loaded)
            if redacted_example not in bucket["examples"] and len(bucket["examples"]) < 3:
                bucket["examples"].append(redacted_example)

    wild_patterns: list[dict[str, Any]] = []
    for sig, b in sorted(wild_buckets.items(), key=lambda pair: (-pair[1]["count"], pair[0])):
        if b["count"] < WILD_PATTERN_MIN_COUNT:
            continue
        target_skill = (
            norm_filter
            if norm_filter and norm_filter in b["skills_context"]
            else (sorted(b["skills_context"])[0] if b["skills_context"] else (norm_filter or "retro-loop"))
        )
        if norm_filter and b["skills_context"] and norm_filter not in b["skills_context"]:
            continue
        wild_patterns.append(
            {
                "pattern_signature": sig,
                "occurrences": b["count"],
                "session_count": len(b["sessions"]),
                "candidate_skill": target_skill,
                "examples": b["examples"],
                "crystallization_policy": {
                    "data_only_helper": True,
                    "self_disclosing_error": True,
                    "gate_tier": "soft_advisory_exit_0",
                },
            }
        )

    taste_eval = _harvest_cross_session_taste_and_eval(handoff_root or (d_root / "handoff"))
    slow_flywheel_ready = total_sessions >= SLOW_FLYWHEEL_COHORT_MIN_SESSIONS

    layer3 = {
        "wild_patterns": wild_patterns[:10],
        "taste_and_eval_diversity": taste_eval,
        "eval_quality_by_skill": eval_quality_by_skill,
        "dual_flywheel_guard": {
            "observed_sessions": total_sessions,
            "slow_flywheel_min_sessions": SLOW_FLYWHEEL_COHORT_MIN_SESSIONS,
            "slow_flywheel_ready": slow_flywheel_ready,
            "active_mode": "slow_flywheel_batch_ready" if slow_flywheel_ready else "fast_flywheel_scope_triaged",
            "fast_flywheel_rules": [
                "objective_bug_or_cli_exit2 -> fix global script/alias with Dual-Case Replay",
                "scenario_specific_need -> add scenario-conditioned branch (never mutate global default)",
                "ambiguous_single_occurrence -> hold in observation_pool until >=3 occurrences",
            ],
        },
    }

    # ------------------------------------------------------------------
    # Synthesize High-Signal Evidence-Backed Proposals (Anti-Noise Filtered)
    # ------------------------------------------------------------------
    candidates: list[dict[str, Any]] = []
    observation_pool: list[dict[str, Any]] = []

    def _register_candidate(item: dict[str, Any], triage: dict[str, Any]) -> None:
        sk_cand = str(item.get("skill") or "").strip()
        sk_ver_cand = (inventory.get(sk_cand) or {}).get("skill_version") if sk_cand else None
        enriched = {
            **item,
            "skill_version": sk_ver_cand,
            "scope": "personal",
            "window_cst": f"{days}d",
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "source_as_of": source_as_of,
            "sample_size": total_sessions,
            "coverage_status": coverage_status,
            "scope_triage": triage,
        }
        if triage["emit_proposal"]:
            candidates.append(enriched)
        else:
            observation_pool.append(enriched)

    # 1) Layer 2 Rigid Roadblocks (exit_code = 2 CLI friction — highest deterministic ROI)
    for rb in layer2["rigid_roadblocks"][:3]:
        sk = rb["skill"]
        sc_name = Path(rb["script"]).name
        sample_cmd = rb["failing_command_samples"][0] if rb["failing_command_samples"] else rb["script"]
        triage = _triage_proposal_scope(
            occurrence_sessions=rb["exit_2_sessions"],
            total_sessions=total_sessions,
            is_objective_bug_or_friction=True,
            domain_scoped=False,
        )
        _register_candidate(
            {
                "id": f"skill-retro-l2-roadblock-{sk}-{sc_name.replace('.', '-')}",
                "ref": f"retro://skill-retro/l2-roadblock/{sk}/{sc_name}",
                "metric_id": f"skill-retro-l2-roadblock-{sk}",
                "layer": "L2",
                "category": "rigid_roadblock",
                "skill": sk,
                "priority": "P1" if rb["exit_2_count"] >= 2 else "P2",
                "title": f"[Skill Retro L2] 柔化 `{sk}/{sc_name}` 参数路障（`exit=2` 触发 {rb['exit_2_count']} 次）",
                "detail": (
                    f"layer=L2 | category=rigid_roadblock | skill={sk} | script={rb['script']} | "
                    f"exit_2_count={rb['exit_2_count']} | sessions={rb['exit_2_sessions']} | "
                    f"sample_cmd=`{sample_cmd}` | action={rb['evolution_action']}"
                ),
                "action": f"Add compatible CLI flag aliases / positional defaults to `skills/{sk}/{rb['script']}` and add Dual-Case Replay test.",
                "plain_reason": (
                    f"`{sk}` 的脚本 `{sc_name}` 发生 {rb['exit_2_count']} 次参数报错（exit=2），"
                    f"需补充兼容参数别名与报错自引导以消除空转重试"
                ),
            },
            triage,
        )

    # 2) Layer 1 Unwired / Disconnected Error Drawers (script/domain failed without reading drawer)
    seen_unwired_skills: set[str] = set()
    for uw in layer1["unwired_disconnected_drawers"]:
        sk = uw["skill"]
        if sk in seen_unwired_skills:
            continue
        seen_unwired_skills.add(sk)
        triage = _triage_proposal_scope(
            occurrence_sessions=uw["domain_failure_count"],
            total_sessions=total_sessions,
            is_objective_bug_or_friction=True,
            domain_scoped=True,
        )
        _register_candidate(
            {
                "id": f"skill-retro-l1-unwired-{sk}-{Path(uw['drawer']).stem}",
                "ref": f"retro://skill-retro/l1-unwired/{sk}/{Path(uw['drawer']).name}",
                "metric_id": f"skill-retro-l1-unwired-{sk}",
                "layer": "L1",
                "category": "unwired_disconnected_drawer",
                "skill": sk,
                "priority": "P1" if uw["domain_failure_count"] >= 2 else "P2",
                "title": f"[Skill Retro L1] 打通 `{sk}` 断连纠偏抽屉 `{Path(uw['drawer']).name}`（报错 {uw['domain_failure_count']} 次但 0 读取）",
                "detail": (
                    f"layer=L1 | category=unwired_disconnected_drawer | skill={sk} | drawer={uw['drawer']} | "
                    f"domain_failures={uw['domain_failure_count']} | hits=0 | scenario_sessions={uw['scenario_sessions']}"
                ),
                "action": uw["evolution_action"],
                "plain_reason": (
                    f"`{sk}` 在 {uw['domain_failure_count']} 次失败或漏检中均未触发 `{Path(uw['drawer']).name}` 读取，"
                    f"需在脚本报错输出中直链该参考抽屉"
                ),
            },
            triage,
        )
        if len(seen_unwired_skills) >= 2:
            break

    # 3) Layer 2 Latency Sinks (>180s or P50 >= 30s with multi-angle attribution)
    for ls in layer2["latency_sinks"][:2]:
        raw_sk = ls["skill"]
        sk = "repo-kit" if (raw_sk == "repo-gate" and "repo-kit" in inventory) else raw_sk
        sc_name = Path(ls["script"]).name
        bottleneck = ls["primary_bottleneck"]
        # Pure external CI polling with only 1 call is observational unless repeated or local serial/scan
        is_actionable_speed = bottleneck != "external_wait" or ls["nested_rerun_calls"] >= 1
        triage = _triage_proposal_scope(
            occurrence_sessions=max(1, ls["measured_calls"]),
            total_sessions=total_sessions,
            is_objective_bug_or_friction=is_actionable_speed,
            domain_scoped=True,
        )
        if not is_actionable_speed:
            triage["emit_proposal"] = False
            triage["scope_verdict"] = "external_wait_observed"
        max_sec = round((ls["max_ms"] or 0.0) / 1000.0, 1)
        p50_sec = round((ls["p50_ms"] or 0.0) / 1000.0, 1)
        _register_candidate(
            {
                "id": f"skill-retro-l2-latency-{sk}-{sc_name.replace('.', '-')}",
                "ref": f"retro://skill-retro/l2-latency/{sk}/{sc_name}",
                "metric_id": f"skill-retro-l2-latency-{sk}",
                "layer": "L2",
                "category": "latency_sink",
                "skill": sk,
                "priority": "P2",
                "title": f"[Skill Retro L2] 多角度提速 `{sc_name}`（max={max_sec}s, P50={p50_sec}s, 主因={bottleneck}）",
                "detail": (
                    f"layer=L2 | category=latency_sink | skill={sk} | script={ls['script']} | "
                    f"max_ms={ls['max_ms']} | p50_ms={ls['p50_ms']} | bottleneck={bottleneck} | "
                    f"nested_reruns={ls['nested_rerun_calls']} | angles={'; '.join(ls['attribution_angles'])}"
                ),
                "action": "Apply 4-angle speedup (path-scoped diff selection, content-hash watermark, parallel pool, or async tiering).",
                "plain_reason": (
                    f"`{sc_name}` 峰值耗时 {max_sec}s（中位 {p50_sec}s，主因：{bottleneck}），"
                    f"建议按变更域裁剪与并发池做分层提速"
                ),
            },
            triage,
        )

    # 3b) Layer 2 Cross-Repo Gate Drift & Bottleneck Proposals (`repo-kit`)
    for cr_prop in (cross_repo_gate_health.get("actionable_proposals") or [])[:3]:
        if not isinstance(cr_prop, Mapping):
            continue
        affected_cnt = max(1, int(cr_prop.get("affected_repos_count") or 1))
        triage = _triage_proposal_scope(
            occurrence_sessions=max(2, affected_cnt),
            total_sessions=total_sessions,
            is_objective_bug_or_friction=True,
            domain_scoped=True,
        )
        _register_candidate(dict(cr_prop), triage)

    # 4) Layer 0 Trigger Misses & Directional Drift
    for tm in layer0["trigger_misses"][:2]:
        sk = tm["skill"]
        triage = _triage_proposal_scope(
            occurrence_sessions=tm["missed_sessions"],
            total_sessions=total_sessions,
            is_objective_bug_or_friction=False,
            domain_scoped=True,
        )
        _register_candidate(
            {
                "id": f"skill-retro-l0-trigger-miss-{sk}",
                "ref": f"retro://skill-retro/l0-trigger-miss/{sk}",
                "metric_id": f"skill-retro-l0-trigger-{sk}",
                "layer": "L0",
                "category": "trigger_miss",
                "skill": sk,
                "priority": "P2",
                "title": f"[Skill Retro L0] 收紧 `{sk}` 触发边界（{tm['missed_sessions']}/{tm['domain_sessions']} 次触碰领域文件未挂载技能）",
                "detail": (
                    f"layer=L0 | category=trigger_miss | skill={sk} | missed_sessions={tm['missed_sessions']} | "
                    f"domain_sessions={tm['domain_sessions']} | miss_rate={tm['miss_rate']}"
                ),
                "action": f"Refine `skills/{sk}/SKILL.md` frontmatter trigger description or pre-edit hook routing.",
                "plain_reason": (
                    f"检测到 {tm['missed_sessions']} 次直接修改 `{sk}` 领域文件却未加载技能契约，需补齐触发词路由"
                ),
            },
            triage,
        )

    # 5) Layer 1 True Zero-Hit Deadwood (only when Slow Flywheel cohort or >=5 scenario sessions)
    for dw in layer1["true_zero_hit_deadwood"][:2]:
        sk = dw["skill"]
        triage = _triage_proposal_scope(
            occurrence_sessions=dw["scenario_sessions"],
            total_sessions=total_sessions,
            is_objective_bug_or_friction=False,
            domain_scoped=slow_flywheel_ready or dw["scenario_sessions"] >= 5,
        )
        if not (slow_flywheel_ready or dw["scenario_sessions"] >= 5):
            triage["emit_proposal"] = False
        _register_candidate(
            {
                "id": f"skill-retro-l1-deadwood-{sk}-{Path(dw['drawer']).stem}",
                "ref": f"retro://skill-retro/l1-deadwood/{sk}/{Path(dw['drawer']).name}",
                "metric_id": f"skill-retro-l1-deadwood-{sk}",
                "layer": "L1",
                "category": "true_zero_hit_deadwood",
                "skill": sk,
                "priority": "P3",
                "title": f"[Skill Retro L1] 清理或合并真僵尸抽屉 `{sk}/{dw['drawer']}`（场景发生 {dw['scenario_sessions']} 次仍 0 命中）",
                "detail": (
                    f"layer=L1 | category=true_zero_hit_deadwood | skill={sk} | drawer={dw['drawer']} | "
                    f"scenario_sessions={dw['scenario_sessions']} | hits=0 | tokens_est={dw['tokens_est']}"
                ),
                "action": dw["evolution_action"],
                "plain_reason": (
                    f"`{sk}` 业务场景已发生 {dw['scenario_sessions']} 次但 `{Path(dw['drawer']).name}` 始终 0 读取，可归档或精简合并"
                ),
            },
            triage,
        )

    # 6) Layer 2 Ceremonial Leaky Rubber-Stamps (only when co_occurring_skill_escapes > 0)
    for cs in layer2["ceremonial_rubber_stamps"]:
        if cs["co_occurring_skill_escapes"] <= 0:
            continue
        sk = cs["skill"]
        sc_name = Path(cs["script"]).name
        triage = _triage_proposal_scope(
            occurrence_sessions=cs["co_occurring_skill_escapes"] + 1,
            total_sessions=total_sessions,
            is_objective_bug_or_friction=True,
            domain_scoped=True,
        )
        _register_candidate(
            {
                "id": f"skill-retro-l2-rubberstamp-{sk}-{sc_name.replace('.', '-')}",
                "ref": f"retro://skill-retro/l2-rubberstamp/{sk}/{sc_name}",
                "metric_id": f"skill-retro-l2-rubberstamp-{sk}",
                "layer": "L2",
                "category": "ceremonial_rubber_stamp",
                "skill": sk,
                "priority": "P2",
                "title": f"[Skill Retro L2] 升级陪跑门禁 `{sk}/{sc_name}`（{cs['calls']} 次全绿但同技能漏检 {cs['co_occurring_skill_escapes']} 次）",
                "detail": (
                    f"layer=L2 | category=ceremonial_rubber_stamp | skill={sk} | script={cs['script']} | "
                    f"calls={cs['calls']} | exit_0_rate=1.0 | co_occurring_escapes={cs['co_occurring_skill_escapes']}"
                ),
                "action": cs["evolution_action"],
                "plain_reason": (
                    f"`{sc_name}` 连续 {cs['calls']} 次运行全绿，但 `{sk}` 仍发生 {cs['co_occurring_skill_escapes']} 次审查打回，需加深校验断言"
                ),
            },
            triage,
        )
        break

    # 7) Layer 3 Wild Pattern Crystallization (>=5 manual repetitions)
    for wp in layer3["wild_patterns"][:2]:
        sk = wp["candidate_skill"]
        sig_hash = hashlib.sha256(wp["pattern_signature"].encode("utf-8")).hexdigest()[:8]
        triage = _triage_proposal_scope(
            occurrence_sessions=max(2, wp["session_count"]),
            total_sessions=total_sessions,
            is_objective_bug_or_friction=False,
            domain_scoped=True,
        )
        _register_candidate(
            {
                "id": f"skill-retro-l3-wild-{sk}-{sig_hash}",
                "ref": f"retro://skill-retro/l3-wild/{sk}/{sig_hash}",
                "metric_id": f"skill-retro-l3-wild-{sk}",
                "layer": "L3",
                "category": "wild_pattern_crystallization",
                "skill": sk,
                "priority": "P3",
                "title": f"[Skill Retro L3] 柔性沉淀高频手工探针 `{wp['pattern_signature'][:42]}`（重复 {wp['occurrences']} 次）",
                "detail": (
                    f"layer=L3 | category=wild_pattern_crystallization | candidate_skill={sk} | "
                    f"occurrences={wp['occurrences']} | sessions={wp['session_count']} | "
                    f"pattern=`{wp['pattern_signature']}` | example=`{wp['examples'][0] if wp['examples'] else ''}`"
                ),
                "action": (
                    f"Crystallize read-only fact collector subcommand under `skills/{sk}/scripts/` "
                    f"(data-only, self-disclosing error, soft exit=0 advisory)."
                ),
                "plain_reason": (
                    f"跨 {wp['session_count']} 个会话重复手敲 `{wp['pattern_signature'][:28]}` 共 {wp['occurrences']} 次，"
                    f"适合沉淀为只采数不代拍板的柔性脚本"
                ),
            },
            triage,
        )

    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    if norm_filter:
        candidates = [c for c in candidates if c.get("skill") in {norm_filter, "repo-gate"}]
        observation_pool = [c for c in observation_pool if c.get("skill") in {norm_filter, "repo-gate"}]
    proposals = sorted(
        candidates,
        key=lambda item: (priority_rank.get(str(item.get("priority")), 9), str(item.get("id") or "")),
    )[:6]
    skill_specific_proposals = (
        [p for p in proposals if p.get("skill") == norm_filter]
        if norm_filter
        else proposals
    )

    auto_proposed = queue_fn(proposals, prop_path) if (auto_propose and queue_fn is not None) else []
    proposals_written = len(auto_proposed)
    proposals_skipped = len(proposals) - proposals_written if auto_propose else 0

    triad_summary = evaluate_skill_uptake(
        skills=[norm_filter] if norm_filter else sorted(inventory.keys()),
        triggered_skills=[norm_filter] if norm_filter else sorted({sk for o in all_sessions for sk in o.skills_loaded}),
        target_dir=s_root.parent if (s_root / "skill-retro").is_dir() else s_root,
        scenario="skills_available_unmentioned",
    )
    result_payload: dict[str, Any] = {
        "ok": True,
        "schema": "skill-retro-diagnostic/v1",
        "query_id": "retro/skill_progressive_retro",
        "status": "ACTIONABLE" if skill_specific_proposals else "HEALTHY_NO_ISSUES",
        "status_message": (
            f"{len(skill_specific_proposals)} evidence-backed proposal(s) found"
            if skill_specific_proposals
            else "healthy / 0 issues found (no changes needed)"
        ),
        "skill_filter": norm_filter or None,
        "mode": norm_mode,
        "aspects": selected_aspects,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "window_cst": f"{days}d",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "source_as_of": source_as_of,
        "sample_size": total_sessions,
        "coverage_status": coverage_status,
        "sources_ingested": {
            "sessions_observed": total_sessions,
            "trace_spans_ingested": ingested_trace_spans,
            "skills_in_inventory": len(inventory),
        },
        "summary": {
            "trigger_miss_skills": len(layer0["trigger_misses"]),
            "zero_action_load_skills": len(layer0["zero_action_loads"]),
            "healthy_r1_r2_convergences": layer0["healthy_convergence_count"],
            "directional_drifts": layer0["directional_drift_count"],
            "hot_drawers_count": len(layer1["hot_drawers"]),
            "on_demand_drawers_count": len(layer1["on_demand_correction_drawers"]),
            "unwired_drawers_count": len(layer1["unwired_disconnected_drawers"]),
            "true_deadwood_drawers_count": len(layer1["true_zero_hit_deadwood"]),
            "true_catcher_scripts_count": len(layer2["true_catchers"]),
            "ceremonial_scripts_count": len(layer2["ceremonial_rubber_stamps"]),
            "rigid_roadblock_scripts_count": len(layer2["rigid_roadblocks"]),
            "latency_sink_scripts_count": len(layer2["latency_sinks"]),
            "cross_repo_gate_findings_count": int(cross_repo_gate_health.get("total_findings") or 0),
            "wild_patterns_count": len(layer3["wild_patterns"]),
            "high_signal_proposals_count": len(proposals),
            "observation_pool_count": len(observation_pool),
            "slow_flywheel_ready": slow_flywheel_ready,
        },
        "cross_repo_gate_health": cross_repo_gate_health,
        "layer0_direction_and_triggers": layer0,
        "layer1_drawer_metabolism": layer1,
        "layer2_script_efficacy_and_speed": layer2,
        "layer3_wild_patterns_and_eval": layer3,
        "problem_discovery_dimensions": {
            "dim1_l0_direction_vs_convergence": layer0,
            "dim2_l1_scenario_drawer_metabolism": layer1,
            "dim3_l2_script_efficacy_and_4angle_speed": layer2,
            "dim4_l3_wild_soft_crystallization": {
                "wild_patterns": layer3["wild_patterns"],
                "principles": ["facts_only", "self_disclosing_error_pointer", "hard_lock_vs_soft_advisory"],
            },
            "dim5_cross_session_creative_spectrum": taste_eval,
            "dim6_three_tier_eval_and_flywheel_guard": {
                "eval_tiers": ["L1_baseline_eval", "L2_diversity_intent_eval", "L3_human_taste_eval"],
                "dual_flywheel_guard": layer3["dual_flywheel_guard"],
            },
            "triad_trigger_uptake_outcome": triad_summary,
        },
        "context_uptake_triad": triad_summary,
        "proposals": proposals,
        "observation_pool": observation_pool[:8],
        "auto_proposed": auto_proposed,
        "proposals_written": proposals_written,
        "proposals_skipped": proposals_skipped,
    }
    if emit_briefs_dir is not None:
        result_payload["emitted_briefs"] = emit_skill_retro_briefs(
            result_payload,
            emit_briefs_dir,
            skill_filter=norm_filter or None,
            mode=norm_mode,
            aspects=selected_aspects,
            coupled_with=coupled_with,
            relays=relays,
        )
    return result_payload



_SKILL_NAME_RE = re.compile(
    r"(?:<skill-context\s+name=[\"'](?P<ctx>[A-Za-z0-9_-]+)[\"']"
    r"|(?<![A-Za-z0-9_-])(?:/)?skills/(?P<path>[A-Za-z0-9_-]+)/SKILL\.md"
    r"|\bskill\s*[:=]\s*[\"'](?P<call>[A-Za-z0-9_-]+)[\"'])"
)
_SCRIPT_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:/)?skills/(?P<skill>[A-Za-z0-9_-]+)/scripts/(?P<script>[A-Za-z0-9_.-]+\.(?:py|sh|ts))"
)
_PRIORITY_MAP = {
    "p0": "high",
    "p1": "high",
    "p2": "medium",
    "p3": "low",
    "high": "high",
    "medium": "medium",
    "low": "low",
}
_CST_TZ = timezone(timedelta(hours=8))


def _cst_today_str() -> str:
    return datetime.now(_CST_TZ).date().isoformat()


def _normalize_priority(raw_priority: Any, default: str = "high") -> str:
    norm = str(raw_priority or default).strip().lower()
    return _PRIORITY_MAP.get(norm, default)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)

def _build_tool_context_burn_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        t_name = str(row.get("tool_name") or "").strip()
        bloated = int(row.get("bloated_call_count") or 0)
        if not t_name or bloated <= 0:
            continue
        p95_tok = int(row.get("p95_tokens") or 0)
        total_tok = int(row.get("total_tokens") or 0)
        share = float(row.get("share_pct") or 0.0)
        dom_skill = str(row.get("dominant_skill") or "team-lead").strip()
        slug_tool = "".join(ch if ch.isalnum() else "-" for ch in t_name.lower()).strip("-") or "tool"
        candidates.append({
            "id": f"tool-burn-{dom_skill}-{slug_tool}",
            "ref": f"skill-roi://{dom_skill}",
            "title": (
                f"[Context Burn · estimated] {t_name} ({dom_skill}) 产出 {bloated} 次 >10k tok 膨胀结果："
                "建议加 --head-limit / view_range 或脚本下沉过滤"
            ),
            "detail": (
                f"tool_name={t_name} | dominant_skill={dom_skill} | "
                f"bloated_calls_gt_10k={bloated} | p95_tokens={p95_tok} | "
                f"total_tokens={total_tok} ({share:.1f}%) | "
                "remediation=Add --head-limit, view_range, or sink filtering into deterministic script"
            ),
            "priority": "high",
            **{key: row[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                         "sample_size", "coverage_status", "estimated", "estimation_method") if key in row},
        })
    return candidates

_EVAL_CASE_ID_RE = re.compile(r"^(?:EV|DC|AT|CASE)-\d+[A-Za-z0-9_-]*$")
_USER_HOME_PREFIX = "/" + "Users/"
_LINUX_HOME_PREFIX = "/" + "home/"
_USER_HOME_PATH_RE = re.compile(
    r"(?:" + _USER_HOME_PREFIX + r"|" + _LINUX_HOME_PREFIX + r")[^/\s\"'<>`|)]+"
)
_DOC_PREVIEW_REF_SUFFIXES = {".md", ".json", ".yaml", ".yml"}
_SKILL_MD_PREVIEW_MAX_CHARS = 32768
_REF_DOC_PREVIEW_MAX_CHARS = 16384
_SAFE_SKILL_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_VALID_SKILL_SCRIPT_SUFFIXES = {".py", ".sh", ".ts"}
_GATE_SCRIPT_BASENAMES = {
    "skill_contract.py",
    "feedback_guard.py",
    "design_contract.py",
    "verify_design_cases.py",
    "audit_arch_testing.py",
    "audit_concurrency_swiftui.py",
    "audit_debug_perf_release.py",
    "verify_issue_format.py",
    "howto_guard.py",
    "spec_gate.py",
    "contract_check.sh",
}


def _is_valid_skill_script_rel_path(rel_path: str) -> bool:
    """Return True iff rel_path is a canonical executable skill script under scripts/."""
    cleaned = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not cleaned.startswith("scripts/"):
        return False
    parts = [p for p in cleaned.split("/") if p]
    if len(parts) < 2 or ".." in parts:
        return False
    if "__pycache__" in parts:
        return False
    for seg in parts[1:-1]:
        if seg.startswith(".") or seg.lower() in {"data", "state", "fixtures", "tests", "__pycache__"}:
            return False
    filename = parts[-1]
    if (
        not filename
        or filename == "__init__.py"
        or filename.startswith(".")
        or filename.startswith("test_")
        or filename.endswith("_test.py")
        or filename.endswith(".pyc")
    ):
        return False
    return Path(filename).suffix.lower() in _VALID_SKILL_SCRIPT_SUFFIXES


def _extract_eval_case_title(
    item: Mapping[str, Any],
    *,
    cid: str,
    assertion_summary: str,
    skills_root: Path,
    dev_root: Path,
) -> str:
    """Extract a human-readable title for an EV-*/DC-* case, never degrading to bare cid."""
    for key in ("title", "name", "description", "summary", "rule", "fix_rule", "symptom", "prompt"):
        raw = str(item.get(key) or "").strip()
        if raw and raw != cid:
            redacted = " ".join(_redact_user_text(raw, skills_root=skills_root, dev_root=dev_root).split())
            if redacted and redacted != cid:
                return redacted[:180]
    fallback = assertion_summary if assertion_summary and assertion_summary != cid else "Contract gate verification check"
    return f"{cid} — {fallback}"[:180]


def _redact_home_paths(text: str, *, skills_root: Path | None = None, dev_root: Path | None = None) -> str:
    """Redact absolute macOS and Linux user home directories into tilde paths."""
    return _redact_user_text(text, skills_root=skills_root, dev_root=dev_root)


def _redact_user_text(text: str, *, skills_root: Path | None = None, dev_root: Path | None = None) -> str:
    """Redact absolute home/temp roots into tilde-relative or symbolic paths."""
    cleaned = str(text or "")
    home_str = str(Path.home())
    if home_str:
        cleaned = cleaned.replace(home_str, "~")
    for candidate_root, replacement in (
        (skills_root, "~/Development/user-skills/skills"),
        (dev_root, "~/Development"),
    ):
        if candidate_root is None:
            continue
        for variant in {str(candidate_root), str(candidate_root.resolve())}:
            if variant and variant != "/":
                cleaned = cleaned.replace(variant, replacement)
    cleaned = _USER_HOME_PATH_RE.sub("~", cleaned)
    return cleaned


def _atomic_write_text(path: Path, content: str) -> None:
    """Atomically write UTF-8 text to path without leaving partial files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _resolve_icloud_sync_dir(icloud_sync_dir: Path | None = None) -> Path | None:
    """Resolve AIDashSync target dir while keeping unittest/pytest runs hermetic."""
    if icloud_sync_dir is not None:
        return Path(icloud_sync_dir).expanduser()
    env_override = str(os.environ.get("AIDASH_ICLOUD_SYNC_DIR") or "").strip()
    if env_override:
        return Path(env_override).expanduser()
    in_test_harness = bool(os.environ.get("PYTEST_CURRENT_TEST")) or ("unittest" in sys.modules)
    if in_test_harness:
        return None
    cloud_docs = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    if not cloud_docs.is_dir():
        return None
    return cloud_docs / "AIDashSync"


def mirror_skills_to_icloud(
    skill_details: Sequence[Mapping[str, Any]],
    *,
    icloud_sync_dir: Path | None = None,
) -> dict[str, Any]:
    """Mirror redacted SKILL.md and text references/* into <sync_dir>/skills-mirror/<slug>/."""
    target_sync_dir = _resolve_icloud_sync_dir(icloud_sync_dir)
    if target_sync_dir is None:
        return {"mirrored": False, "reason": "icloud_sync_disabled_or_unavailable", "skills_written": 0, "references_written": 0}
    if icloud_sync_dir is None and not os.environ.get("AIDASH_ICLOUD_SYNC_DIR"):
        cloud_docs_parent = target_sync_dir.parent
        if not cloud_docs_parent.is_dir():
            return {"mirrored": False, "reason": "cloud_docs_missing", "skills_written": 0, "references_written": 0}

    mirror_root = target_sync_dir / "skills-mirror"
    skills_written = 0
    refs_written = 0
    try:
        mirror_root.mkdir(parents=True, exist_ok=True)
        mirror_root_resolved = mirror_root.resolve()
        for item in skill_details:
            if not isinstance(item, Mapping):
                continue
            slug = str(item.get("skill") or "").strip()
            if not slug or not _SAFE_SKILL_SLUG_RE.match(slug) or slug in {".", ".."}:
                continue
            skill_preview = item.get("doc_preview")
            if not isinstance(skill_preview, str) or not skill_preview:
                continue
            skill_bundle_dir = (mirror_root_resolved / slug).resolve()
            if skill_bundle_dir.parent != mirror_root_resolved:
                continue
            _atomic_write_text(skill_bundle_dir / "SKILL.md", skill_preview)
            skills_written += 1

            refs = item.get("references")
            if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)):
                continue
            for ref in refs:
                if not isinstance(ref, Mapping):
                    continue
                ref_preview = ref.get("doc_preview")
                rel_path = str(ref.get("rel_path") or "").strip()
                if not isinstance(ref_preview, str) or not ref_preview or not rel_path:
                    continue
                rel_parts = Path(rel_path).parts
                if not rel_parts or rel_parts[0] != "references" or ".." in rel_parts:
                    continue
                dest = (skill_bundle_dir / rel_path).resolve()
                try:
                    dest.relative_to(skill_bundle_dir)
                except ValueError:
                    continue
                _atomic_write_text(dest, ref_preview)
                refs_written += 1
    except OSError as exc:
        return {
            "mirrored": False,
            "reason": f"os_error:{type(exc).__name__}",
            "sync_dir": _redact_home_paths(str(target_sync_dir)),
            "skills_written": skills_written,
            "references_written": refs_written,
        }
    return {
        "mirrored": True,
        "sync_dir": _redact_home_paths(str(target_sync_dir)),
        "skills_written": skills_written,
        "references_written": refs_written,
    }


def _format_skill_dir(pkg: Path, *, repo_bound: bool, repo_name: str, skills_root: Path, dev_root: Path) -> str:
    """Return a privacy-safe tilde path for a discovered skill directory."""
    if not repo_bound:
        return f"~/Development/user-skills/skills/{pkg.name}"
    try:
        rel = pkg.relative_to(dev_root)
        return f"~/Development/{rel.as_posix()}"
    except ValueError:
        pass
    return _redact_user_text(str(pkg), skills_root=skills_root, dev_root=dev_root)


def _estimate_frontmatter_and_body_tokens(skill_md: Path) -> tuple[int, int, int]:
    """Return (line_count, frontmatter_tokens_est, body_tokens_est) for SKILL.md."""
    try:
        raw_bytes = skill_md.read_bytes()
        text = raw_bytes.decode("utf-8", errors="ignore")
    except OSError:
        return 0, 1, 1
    lines = text.splitlines()
    body_tokens = max(1, len(raw_bytes) // 4)
    fm_text = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_lines = [
                ln.strip()
                for ln in parts[1].splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            fm_text = "\n".join(fm_lines) if fm_lines else parts[1].strip()
    frontmatter_tokens = max(1, len(fm_text.encode("utf-8")) // 4)
    return len(lines), frontmatter_tokens, body_tokens


def _summarize_eval_case(item: dict[str, Any], *, skills_root: Path, dev_root: Path) -> tuple[str, str]:
    """Classify an EV-*/DC-* case into ('regex'|'cli'|'contract', <=140 char summary)."""
    cmd = str(item.get("cmd") or "").strip()
    forbidden_regex = str(item.get("forbidden_regex") or "").strip()
    bad_snippet = str(item.get("bad_snippet") or "").strip()
    must_contain = item.get("must_contain")
    expect_stdout = item.get("expect_stdout_contains")
    fix_rule = str(item.get("fix_rule") or "").strip()

    if cmd:
        kind = "cli"
        summary_raw = cmd
        if isinstance(expect_stdout, list) and expect_stdout:
            summary_raw = f"{cmd} -> contains {expect_stdout[0]}"
    elif forbidden_regex or bad_snippet:
        kind = "regex"
        summary_raw = forbidden_regex or f"forbid: {bad_snippet}"
    else:
        kind = "contract"
        target_file = str(item.get("file") or "").strip()
        if isinstance(must_contain, list) and must_contain:
            joined = ", ".join(str(x) for x in must_contain[:3])
            summary_raw = f"{target_file}: must_contain [{joined}]" if target_file else f"must_contain [{joined}]"
        else:
            summary_raw = fix_rule or str(item.get("symptom") or item.get("title") or "contract check")

    one_line = " ".join(
        _redact_user_text(summary_raw, skills_root=skills_root, dev_root=dev_root).split()
    )
    if len(one_line) > 140:
        one_line = one_line[:137].rstrip() + "..."
    return kind, one_line or "contract assertion"


def _discover_skill_inventory(
    skills_root: Path | None = None,
    dev_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Scan global user-skills and repo-bound skills for scripts, L0/L1 lines, and EV/DC cases."""
    root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    search_dirs: list[tuple[Path, bool, str]] = [(root, False, "user-skills")]
    for fallback_cat in ("repo", "meta"):
        fallback_dir = d_root / "Skills" / "skills" / fallback_cat
        if _probe_readable_dir(fallback_dir):
            search_dirs.append((fallback_dir, False, "user-skills"))
    repo_names = ["AIDash", "Financial", "VitalStride", "VoxPocket"]
    if _probe_readable_dir(d_root):
        for child in _safe_iterdir(d_root):
            if not _safe_is_dir(child) or child.name in {"user-skills", "worktrees", "handoff"} or child.name.startswith("."):
                continue
            if child.name not in repo_names and (_safe_is_dir(child / ".agents" / "skills") or _safe_is_dir(child / ".claude" / "skills")):
                repo_names.append(child.name)
    for repo_name in repo_names:
        repo_path = d_root / repo_name
        candidates = [repo_path / ".agents" / "skills", repo_path / ".claude" / "skills"]
        if repo_name in {"AIDash", "Financial", "VitalStride", "VoxPocket"}:
            candidates.append(repo_path / "skills")
        for rel in candidates:
            if _probe_readable_dir(rel):
                search_dirs.append((rel, True, repo_name))

    inventory: dict[str, dict[str, Any]] = {}
    for base_dir, repo_bound, repo_name in search_dirs:
        if not _probe_readable_dir(base_dir):
            continue
        for pkg in _safe_iterdir(base_dir):
            skill_md = pkg / "SKILL.md"
            if not _safe_is_dir(pkg) or not _safe_is_file(skill_md):
                continue
            skill_name = pkg.name
            # Skip third-party SpecKit command templates (.agents/skills/speckit-*) that are not user-skills contracts
            if repo_bound and skill_name.startswith("speckit-"):
                continue
            if skill_name in inventory:
                continue
            l0_lines, frontmatter_tokens_est, body_tokens_est = _estimate_frontmatter_and_body_tokens(skill_md)
            try:
                skill_md_raw_bytes = skill_md.read_bytes()
                skill_md_raw_text = skill_md_raw_bytes.decode("utf-8", errors="ignore")
            except OSError:
                skill_md_raw_bytes = b""
                skill_md_raw_text = ""
            skill_md_bytes_len = len(skill_md_raw_bytes)
            skill_doc_preview = _redact_home_paths(
                skill_md_raw_text,
                skills_root=root,
                dev_root=d_root,
            )[:_SKILL_MD_PREVIEW_MAX_CHARS]
            l0_cap = 80 if skill_name in {"my-designer", "design-studio"} else 180
            refs_dir = pkg / "references"
            ref_files: list[str] = []
            ref_entries: list[dict[str, Any]] = []
            total_refs_bytes = 0
            max_l1_lines = 0
            eval_cases = 0
            eval_counter_cases = 0
            recent_eval_cases = 0
            parsed_eval_cases: list[dict[str, Any]] = []
            seen_case_ids: set[str] = set()
            cutoff_7d = (date.today() - timedelta(days=7)).isoformat()
            if refs_dir.is_dir():
                for ref_p in sorted(refs_dir.rglob("*")):
                    if not ref_p.is_file() or ref_p.name.startswith(".") or "__pycache__" in ref_p.parts:
                        continue
                    rel_ref = ref_p.relative_to(refs_dir).as_posix()
                    try:
                        ref_bytes = ref_p.read_bytes()
                        ref_text = ref_bytes.decode("utf-8", errors="ignore")
                        ref_line_count = len(ref_text.splitlines())
                    except OSError:
                        ref_bytes = b""
                        ref_text = ""
                        ref_line_count = 0
                    total_refs_bytes += len(ref_bytes)
                    ref_entry: dict[str, Any] = {
                        "rel_path": f"references/{rel_ref}",
                        "line_count": ref_line_count,
                        "tokens_est": max(1, len(ref_bytes) // 4),
                    }
                    if ref_p.suffix.lower() in _DOC_PREVIEW_REF_SUFFIXES and ref_text:
                        ref_entry["doc_preview"] = _redact_home_paths(
                            ref_text,
                            skills_root=root,
                            dev_root=d_root,
                        )[:_REF_DOC_PREVIEW_MAX_CHARS]
                    ref_entries.append(ref_entry)
                    if ref_p.suffix == ".md":
                        ref_files.append(rel_ref)
                        max_l1_lines = max(max_l1_lines, ref_line_count)
                    elif ref_p.suffix == ".json" and (
                        rel_ref.endswith("eval-cases.json") or rel_ref.endswith("defect-cases/index.json")
                    ):
                        try:
                            doc = json.loads(ref_bytes.decode("utf-8", errors="ignore"))
                            for status_key, case_status in (("active_cases", "active"), ("retired_cases", "retired")):
                                for item in doc.get(status_key, []):
                                    if not isinstance(item, dict):
                                        continue
                                    cid = str(item.get("id") or "").strip() or f"CASE-{len(parsed_eval_cases) + 1:03d}"
                                    if case_status == "active":
                                        eval_cases += 1
                                        if _is_eval_counter_case(item):
                                            eval_counter_cases += 1
                                        reg = str(item.get("registered_at") or doc.get("updated") or "")
                                        if reg >= cutoff_7d:
                                            recent_eval_cases += 1
                                    if cid in seen_case_ids:
                                        continue
                                    seen_case_ids.add(cid)
                                    kind, assertion_summary = _summarize_eval_case(item, skills_root=root, dev_root=d_root)
                                    parsed_eval_cases.append({
                                        "id": cid,
                                        "title": _extract_eval_case_title(
                                            item,
                                            cid=cid,
                                            assertion_summary=assertion_summary,
                                            skills_root=root,
                                            dev_root=d_root,
                                        ),
                                        "kind": kind,
                                        "assertion_summary": assertion_summary,
                                        "status": str(item.get("status") or case_status),
                                        "is_counter_case": _is_eval_counter_case(item),
                                    })
                        except (OSError, json.JSONDecodeError):
                            pass
            data_drawer = pkg / "scripts" / "data" / "defect-cases" / "index.json"
            if data_drawer.is_file():
                try:
                    data_bytes = data_drawer.read_bytes()
                    ref_entries.append({"rel_path": "scripts/data/defect-cases/index.json", "line_count": len(data_bytes.decode("utf-8", errors="ignore").splitlines()), "tokens_est": max(1, len(data_bytes) // 4)})
                    data_doc = json.loads(data_bytes.decode("utf-8", errors="ignore"))
                    for item in data_doc.get("active_cases", []):
                        if isinstance(item, dict):
                            eval_cases += 1
                            if _is_eval_counter_case(item):
                                eval_counter_cases += 1
                            reg = str(item.get("registered_at") or data_doc.get("updated") or "")
                            if reg >= cutoff_7d:
                                recent_eval_cases += 1
                except (OSError, json.JSONDecodeError):
                    pass
            scripts_dir = pkg / "scripts"
            scripts: list[str] = []
            script_entries: list[str] = []
            script_realpaths: dict[str, str] = {}
            script_watermarks: dict[str, int] = {}
            total_scripts_bytes = 0
            if scripts_dir.is_dir():
                for sp in sorted(scripts_dir.rglob("*")):
                    if not (sp.is_file() or sp.is_symlink()):
                        continue
                    rel_sc = f"scripts/{sp.relative_to(scripts_dir).as_posix()}"
                    if not _is_valid_skill_script_rel_path(rel_sc):
                        continue
                    try:
                        sc_bytes_len = len(sp.read_bytes())
                    except OSError:
                        continue
                    total_scripts_bytes += sc_bytes_len
                    if sp.parent == scripts_dir:
                        scripts.append(sp.name)
                    script_entries.append(rel_sc)
                    with contextlib.suppress(OSError):
                        script_realpaths[rel_sc] = str(sp.resolve())
                    sc_epoch = _git_last_modified_epoch(sp, fallback_mtime=False)
                    if sc_epoch is not None:
                        script_watermarks[rel_sc] = sc_epoch
            ver_info = _compute_pkg_version_fingerprint(pkg)
            reality_info = internal_reality_check(pkg)
            eval_counter_ratio = round(eval_counter_cases / eval_cases, 4) if eval_cases > 0 else 0.0
            refs_tokens_est = sum(int(r.get("tokens_est") or 0) for r in ref_entries)
            bundle_kb = round((skill_md_bytes_len + total_refs_bytes + total_scripts_bytes) / 1024.0, 1)
            refs_kb = round(total_refs_bytes / 1024.0, 1)
            budget_ok = l0_lines <= l0_cap and max_l1_lines <= 160
            inventory[skill_name] = {
                "skill": skill_name,
                "path": str(pkg),
                "skill_dir": _format_skill_dir(pkg, repo_bound=repo_bound, repo_name=repo_name, skills_root=root, dev_root=d_root),
                "scope": "repo_bound" if repo_bound else "global",
                "repo": repo_name if repo_bound else "user-skills",
                "repo_bound": repo_bound,
                "skill_version": ver_info["skill_version"],
                "commit_count": ver_info["commit_count"],
                "subtree_sha": ver_info["subtree_sha"],
                "plugin_version": ver_info["plugin_version"],
                "last_modified_epoch": ver_info["last_modified_epoch"],
                "l0_lines": l0_lines,
                "frontmatter_tokens_est": frontmatter_tokens_est,
                "body_tokens_est": body_tokens_est,
                "bundle_kb": bundle_kb,
                "refs_kb": refs_kb,
                "refs_tokens_est": refs_tokens_est,
                "l0_cap": l0_cap,
                "max_l1_lines": max_l1_lines,
                "l1_cap": 160,
                "budget_ok": budget_ok,
                "doc_preview": skill_doc_preview,
                "reference_files": ref_files,
                "reference_entries": ref_entries,
                "scripts": scripts,
                "script_entries": script_entries,
                "script_realpaths": script_realpaths,
                "script_watermarks": script_watermarks,
                "eval_cases": parsed_eval_cases,
                "eval_case_count": eval_cases,
                "eval_counter_case_count": eval_counter_cases,
                "eval_counter_case_ratio": eval_counter_ratio,
                "eval_case_weekly_delta": recent_eval_cases,
                "internal_reality": reality_info,
                "internal_doc_cli_drift_count": reality_info["internal_doc_cli_drift_count"],
                "internal_broken_refs_count": reality_info["internal_broken_refs_count"],
                "internal_doc_cli_drift": reality_info["internal_doc_cli_drift"],
                "internal_broken_refs": reality_info["internal_broken_refs"],
            }
    return inventory


def _git_commits_behind(repo_path: Path, old_sha: str, new_sha: str) -> tuple[int, int]:
    """Return (commits_behind, days_behind) between old_sha and new_sha in repo_path."""
    if not repo_path.is_dir() or not old_sha or not new_sha or new_sha.startswith(old_sha) or old_sha.startswith(new_sha):
        return 0, 0
    try:
        cnt_proc = subprocess.run(
            ["git", "--no-optional-locks", "rev-list", "--count", f"{old_sha}..{new_sha}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        commits = int(cnt_proc.stdout.strip()) if cnt_proc.returncode == 0 and cnt_proc.stdout.strip().isdigit() else 1
        ts_old = subprocess.run(
            ["git", "--no-optional-locks", "show", "-s", "--format=%ct", old_sha],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        ts_new = subprocess.run(
            ["git", "--no-optional-locks", "show", "-s", "--format=%ct", new_sha],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if ts_old.returncode == 0 and ts_new.returncode == 0:
            sec_diff = max(0, int(ts_new.stdout.strip() or 0) - int(ts_old.stdout.strip() or 0))
            return commits, sec_diff // 86400
        return commits, 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return 1, 0


def _compute_s1_pin_drift(dev_root: Path) -> dict[str, Any]:
    """S1: Shared-Repos downstream pin drift across shared-ci, shared-design-system, shared-telemetry."""
    upstreams: dict[str, tuple[Path, str]] = {}
    for up_name in ("shared-ci", "shared-design-system", "shared-telemetry"):
        up_path = dev_root / up_name
        sha = ""
        if up_path.is_dir():
            try:
                proc = subprocess.run(
                    ["git", "--no-optional-locks", "rev-parse", "HEAD"],
                    cwd=up_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if proc.returncode == 0:
                    sha = proc.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                sha = ""
        if sha:
            upstreams[up_name] = (up_path, sha)

    consumers: list[dict[str, Any]] = []
    for repo_name in ("AIDash", "VitalStride", "VoxPocket"):
        r_path = dev_root / repo_name
        if not r_path.is_dir():
            continue
        pins: dict[str, str] = {}
        pin_sources: dict[str, Path] = {}
        shared_ci_file = r_path / ".shared-ci"
        if shared_ci_file.is_file():
            try:
                txt = shared_ci_file.read_text(encoding="utf-8", errors="ignore").strip()
                m = re.search(r"\b([0-9a-f]{7,40})\b", txt)
                if m:
                    pins["shared-ci"] = m.group(1)
                    pin_sources["shared-ci"] = shared_ci_file
            except OSError:
                pass
        wf_dir = r_path / ".github" / "workflows"
        if "shared-ci" not in pins and wf_dir.is_dir():
            for wf in sorted(wf_dir.glob("*.yml")):
                try:
                    wtxt = wf.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                m = re.search(r"LeePepe/shared-ci/[^@\s]+@([0-9a-f]{7,40})", wtxt)
                if m:
                    pins["shared-ci"] = m.group(1)
                    pin_sources["shared-ci"] = wf
                    break
        for pr_rel in (
            r_path / f"{repo_name}.xcodeproj" / "project.xcworkspace" / "xcshareddata" / "swiftpm" / "Package.resolved",
            r_path / repo_name / f"{repo_name}.xcodeproj" / "project.xcworkspace" / "xcshareddata" / "swiftpm" / "Package.resolved",
            r_path / "Package.resolved",
        ):
            if not pr_rel.is_file():
                continue
            try:
                pdoc = json.loads(pr_rel.read_text(encoding="utf-8"))
                for pin_entry in pdoc.get("pins", []):
                    ident = str(pin_entry.get("identity") or "").lower()
                    rev = str((pin_entry.get("state") or {}).get("revision") or "")
                    if ident in {"shared-design-system", "shared-telemetry"} and rev:
                        pins[ident] = rev
                        pin_sources[ident] = pr_rel
            except (OSError, json.JSONDecodeError):
                continue

        drift_items: list[dict[str, Any]] = []
        for dep, (up_path, head_sha) in upstreams.items():
            pinned = pins.get(dep, "")
            if not pinned:
                continue
            commits_behind, days_behind = _git_commits_behind(up_path, pinned, head_sha)
            is_outdated = not head_sha.startswith(pinned) and not pinned.startswith(head_sha) and commits_behind > 0
            drift_items.append({
                **_snapshot_freshness([pin_sources[dep]], 1),
                "dependency": dep,
                "pinned_sha": pinned[:12],
                "latest_sha": head_sha[:12],
                "commits_behind": commits_behind if is_outdated else 0,
                "days_behind": days_behind if is_outdated else 0,
                "status": "OUTDATED_PIN" if is_outdated else "UP_TO_DATE",
            })
        consumers.append({
            **_snapshot_freshness([pin_sources[d["dependency"]] for d in drift_items], len(drift_items)),
            "repo": repo_name,
            "pins": pins,
            "drift": drift_items,
            "outdated_count": sum(1 for d in drift_items if d["status"] == "OUTDATED_PIN"),
        })
    total_outdated = sum(c["outdated_count"] for c in consumers)
    stamps = [_observed_at(row["source_as_of"]) for row in consumers if row["source_as_of"]]
    metadata = _freshness(stamps, bool(consumers))
    metadata.update(window_cst="snapshot", window_start=min(stamps).isoformat() if stamps else None,
                    window_end=max(stamps).isoformat() if stamps else None)
    return {
        **metadata,
        "upstreams": {k: v[1][:12] for k, v in upstreams.items()},
        "consumers": consumers,
        "outdated_pin_count": total_outdated,
        "all_up_to_date": total_outdated == 0 if any(c["drift"] for c in consumers) else None,
        "sample_size": sum(len(c["drift"]) for c in consumers),
    }


def _compute_s11_s12_context_and_tools(
    warehouse_db: Path,
    magpie_usage: Path | None = None,
    copilot_db: Path | None = None,
    dsh_sessions: Path | None = None,
    telemetry_shards_dir: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Token allocation and per-tool burn are proxies, never measured savings."""
    mp, cp = magpie_usage or DEFAULT_MAGPIE_USAGE, copilot_db or DEFAULT_COPILOT_DB
    slice_tokens = dict.fromkeys(("System & AGENTS.md 指令链", "Skills 目录与 SKILL.md", "Tools Schema 定义",
                                 "Tool Results 工具返回", "User/Assistant 对话与思考链"), 0)
    total_in, total_cache_read = 0, 0
    context_stamps, cache_stamps = [], []
    tool_calls: dict[str, list[tuple[int, str, datetime]]] = {}
    rows = []
    if warehouse_db.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{warehouse_db}?mode=ro", uri=True)) as conn:
            rows = _timed_rows(conn, "fact_turn")
    seen_turn_sigs: set[tuple[str, str, str]] = set()
    for row in rows:
        sid_s = str(row.get("session_id") or "")
        tid_s = str(row.get("turn_uuid") or "")
        sk_list = _json_list(row.get("skill_names"))
        sk_s = str(row.get("attribution_skill") or (sk_list[0] if sk_list else "team-lead"))
        if sid_s or tid_s:
            seen_turn_sigs.add((sid_s, tid_s, sk_s))
        in_t, out_t, cache = (int(row.get(key) or 0) for key in ("input_tokens", "output_tokens", "cache_read"))
        total_in += in_t
        total_cache_read += cache
        stamp = row["_observed_at"]
        if in_t + cache > 0:
            cache_stamps.append(stamp)
        context = in_t + cache
        if context + out_t <= 0:
            continue
        context_stamps.append(stamp)
        tools = json.loads(row.get("tool_calls") or "[]")
        skills = json.loads(row.get("skill_names") or "[]")
        skill = str(row.get("attribution_skill") or (skills[0] if skills else "team-lead"))
        has_skill, has_tool = bool(row.get("attribution_skill") or skills), bool(tools)
        shares = (0.16 if has_skill and has_tool else 0.20 if has_tool else 0.28,
                  0.18 if has_skill else 0.04, 0.14 if has_tool else 0.18,
                  0.36 if has_tool else 0.10, 0.16)
        for key, share in zip(slice_tokens, shares):
            slice_tokens[key] += int(context * share)
        slice_tokens["User/Assistant 对话与思考链"] += out_t
        for tool in tools:
            name = str(tool).strip().lower()
            if name in {"bash", "execute_code", "terminal", "run_command"}:
                name = "bash"
            elif name in {"read", "view", "read_file"}:
                name = "read/view"
            elif name in {"edit", "apply_patch", "write", "create"}:
                name = "edit/write"
            tool_calls.setdefault(name, []).append((round(context * 0.36 / len(tools)), skill, stamp))
    shard_rows = _read_telemetry_shards(telemetry_shards_dir, days=7)
    for s_row in shard_rows:
        if str(s_row.get("kind") or "") != "skill_turn":
            continue
        sid_s = str(s_row.get("session_id") or "")
        tid_s = str(s_row.get("turn_id") or "")
        skill = str(s_row.get("skill") or "team-lead")
        if (sid_s or tid_s) and (sid_s, tid_s, skill) in seen_turn_sigs:
            continue
        if sid_s or tid_s:
            seen_turn_sigs.add((sid_s, tid_s, skill))
        in_t = int(s_row.get("input_tokens") or s_row.get("in") or 0)
        out_t = int(s_row.get("output_tokens") or s_row.get("out") or 0)
        cache = int(s_row.get("cache_read") or 0)
        total_in += in_t
        total_cache_read += cache
        stamp = s_row["_observed_at"]
        if in_t + cache > 0:
            cache_stamps.append(stamp)
        context = in_t + cache
        if context + out_t <= 0:
            continue
        context_stamps.append(stamp)
        tools = _json_list(s_row.get("tool_calls"))
        has_tool = bool(tools)
        shares = (0.16 if has_tool else 0.28, 0.18, 0.14 if has_tool else 0.18, 0.36 if has_tool else 0.10, 0.16)
        for key, share in zip(slice_tokens, shares):
            slice_tokens[key] += int(context * share)
        slice_tokens["User/Assistant 对话与思考链"] += out_t
        for tool in tools:
            name = str(tool).strip().lower()
            if name in {"bash", "execute_code", "terminal", "run_command"}:
                name = "bash"
            elif name in {"read", "view", "read_file"}:
                name = "read/view"
            elif name in {"edit", "apply_patch", "write", "create"}:
                name = "edit/write"
            tool_calls.setdefault(name, []).append((round(context * 0.36 / len(tools)), skill, stamp))
    if not cache_stamps:
        usage = _usage_records(mp, telemetry_shards_dir=telemetry_shards_dir)
        for row in usage:
            in_t, cache = int(row.get("in") or 0), int(row.get("cache_read") or 0)
            if in_t + cache > 0:
                total_in += in_t
                total_cache_read += cache
                cache_stamps.append(row["_observed_at"])
    if not cache_stamps and cp.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{cp}?mode=ro", uri=True)) as conn:
            for row in _timed_rows(conn, "assistant_usage_events"):
                in_t, cache = int(row.get("input_tokens") or 0), int(row.get("cache_read_tokens") or 0)
                if in_t + cache > 0:
                    total_in += in_t
                    total_cache_read += cache
                    cache_stamps.append(row["_observed_at"])
    # File sizes have no observation timestamps or measured token composition.
    # Do not synthesize slices from an unbounded DSH byte-count fallback.
    shards_present = bool(telemetry_shards_dir and _safe_glob(telemetry_shards_dir, "*-skill-usage.jsonl"))
    present = warehouse_db.is_file() or mp.is_file() or cp.is_file() or shards_present
    context_meta = _freshness(context_stamps, present)
    cache_meta = _freshness(cache_stamps, present)
    total_ctx = sum(slice_tokens.values())
    s11 = {**_freshness(context_stamps or cache_stamps, present), "estimated": True,
           "estimation_method": "fixed-share allocation of observed turn tokens; no measured compression saving",
           "slices": [{"slice_key": key, "tokens": value if total_ctx else None,
                       "share_pct": round(100 * value / total_ctx, 1) if total_ctx else None,
                       "estimated": True, **context_meta} for key, value in slice_tokens.items()],
           "total_context_tokens": total_ctx or None,
           "kv_cache_hit_pct": round(100 * total_cache_read / (total_in + total_cache_read), 1) if total_in + total_cache_read else None,
           "kv_cache_freshness": cache_meta, "compressed_saved_tokens": None}
    grand_total = sum(tokens for calls in tool_calls.values() for tokens, _, _ in calls)
    s12 = []
    for tool, calls in tool_calls.items():
        tokens = sorted(item[0] for item in calls)
        skill_counts: dict[str, int] = {}
        for tok, skill, _ in calls:
            skill_counts[skill] = skill_counts.get(skill, 0) + tok
        s12.append({"tool_name": tool, "call_count": len(calls), "total_tokens": sum(tokens),
                    "p95_tokens": tokens[max(0, (len(tokens) * 95 + 99) // 100 - 1)],
                    "bloated_call_count": sum(tok > 10000 for tok in tokens),
                    "share_pct": round(100 * sum(tokens) / grand_total, 1) if grand_total else None,
                    "dominant_skill": max(skill_counts, key=skill_counts.get), "estimated": True,
                    "estimation_method": "turn context * 0.36 / tool count, not measured tool result tokens",
                    **_freshness([item[2] for item in calls])})
    s12.sort(key=lambda row: (-row["total_tokens"], row["tool_name"]))
    return s11, s12, _build_tool_context_burn_candidates(s12)


def _percent_label(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "unknown"


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _extract_skill_names(value: Any, known_skills: set[str]) -> set[str]:
    text = str(value or "")
    found: set[str] = set()
    for match in _SKILL_NAME_RE.finditer(text):
        name = match.group("ctx") or match.group("path") or match.group("call")
        if name:
            found.add("team-lead" if name == "goal-target-task" else name)
    for pattern in (_REF_PATH_RE, _SCRIPT_PATH_RE):
        found.update(match.group("skill") for match in pattern.finditer(text))
    for name in known_skills:
        if re.search(rf"(?<![A-Za-z0-9_-])/{re.escape(name)}(?![A-Za-z0-9_-])", text):
            found.add(name)
        if re.search(rf"(?<![A-Za-z0-9_-])skills/{re.escape(name)}(?:/|\b)", text):
            found.add(name)
    return {"team-lead" if name == "goal-target-task" else name for name in found}


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, (len(ordered) * 95 + 99) // 100 - 1))
    return ordered[index]


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "pass", "caught"}


def _normalize_skill_rel_path(raw_path: str, *, prefix: str) -> str:
    """Normalize a warehouse reference_path or script_path to 'references/...' or 'scripts/...'."""
    cleaned = str(raw_path or "").strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("legacy:"):
        return ""
    marker = f"/{prefix}/"
    idx = cleaned.find(marker)
    if idx != -1:
        return f"{prefix}/{cleaned[idx + len(marker):].lstrip('/')}"
    if cleaned.startswith(f"{prefix}/"):
        return cleaned
    return f"{prefix}/{cleaned.lstrip('/')}"


def _read_l2_channel_counts(wh: Path, start30: datetime, end30: datetime) -> dict[str, dict[str, int]]:
    """Read optional 30d per-skill call counts from sibling L2 clean DBs when present."""
    counts: dict[str, dict[str, int]] = {}
    clean_dir = wh.parent.parent / "L2_normalize" / "clean"
    if not clean_dir.is_dir():
        return counts
    day_from = start30.date().isoformat()
    day_to = (end30 - timedelta(days=1)).date().isoformat()
    specs = (
        ("codex", clean_dir / "codex_prompts.db", "SELECT skill, COUNT(*) FROM skill_use WHERE day BETWEEN ? AND ? GROUP BY skill"),
        ("hermes", clean_dir / "hermes_tools.db", "SELECT skill, COALESCE(SUM(n), 0) FROM skill_day WHERE day BETWEEN ? AND ? GROUP BY skill"),
        ("copilot", clean_dir / "copilot_cli.db", "SELECT skill, COALESCE(SUM(n), 0) FROM skill_day WHERE day BETWEEN ? AND ? GROUP BY skill"),
        ("claude", clean_dir / "claude_prompts.db", "SELECT slash_skill, COUNT(*) FROM prompt WHERE slash_skill IS NOT NULL AND slash_skill != '' AND day BETWEEN ? AND ? GROUP BY slash_skill"),
    )
    for channel, db_path, sql in specs:
        if not db_path.is_file():
            continue
        try:
            with contextlib.closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
                for raw_sk, raw_n in conn.execute(sql, (day_from, day_to)):
                    sk = str(raw_sk or "").strip().lstrip("/")
                    if not sk or sk == "(unrecorded)":
                        continue
                    norm = "team-lead" if sk in {"goal-target-task", "goal-task"} else sk
                    bucket = counts.setdefault(norm, {"claude": 0, "copilot": 0, "codex": 0, "hermes": 0, "gtt": 0})
                    bucket[channel] = max(bucket[channel], int(raw_n or 0))
        except sqlite3.Error:
            continue
    return counts


def _collect_skill_details(
    *,
    inventory: Mapping[str, Mapping[str, Any]],
    skill_rows: Sequence[Mapping[str, Any]],
    per_skill_channels: Mapping[str, Mapping[str, Any]],
    l2_channels: Mapping[str, Mapping[str, Any]],
    per_skill_cache: Mapping[str, Mapping[str, Any]],
    s11_context: Mapping[str, Any],
    per_skill_refs: Mapping[str, Mapping[str, Any]],
    per_skill_scripts: Mapping[str, Mapping[str, Any]],
    per_skill_eval_cases: Mapping[str, Mapping[str, Any]],
    warehouse_present: bool,
) -> list[dict[str, Any]]:
    """Build per-skill inspector details with redacted doc_preview and references."""
    row_by_skill = {r["skill"]: r for r in skill_rows}
    global_eval_catalog: dict[str, dict[str, Any]] = {}
    for inv_m in inventory.values():
        for c_entry in inv_m.get("eval_cases", []):
            cid = str(c_entry.get("id") or "").strip()
            if cid and cid not in global_eval_catalog:
                global_eval_catalog[cid] = dict(c_entry)

    all_detail_skills = sorted(
        set(inventory.keys()) | {sk for sk, row_d in row_by_skill.items() if int(row_d.get("loads_30d") or 0) > 0},
        key=lambda sk: (
            0 if sk in inventory else 1,
            -(row_by_skill.get(sk, {}).get("loads_30d") or 0),
            0 if (inventory.get(sk, {}).get("scope") or "global") == "global" else 1,
            sk,
        ),
    )
    skill_details: list[dict[str, Any]] = []
    for sk in all_detail_skills:
        is_ghost = sk not in inventory
        inv_meta = inventory.get(sk, {})
        row_data = row_by_skill.get(sk, {})
        scope = str(inv_meta.get("scope") or "global")
        repo_label = str(inv_meta.get("repo") or "user-skills")
        skill_dir_str = str(inv_meta.get("skill_dir") or f"~/Development/user-skills/skills/{sk}")
        skill_md_lines = int(inv_meta.get("l0_lines") or 0)
        fm_tok_est = int(inv_meta.get("frontmatter_tokens_est") or 1)
        body_tok_est = int(inv_meta.get("body_tokens_est") or max(1, skill_md_lines * 12))

        loads_7d_val = int(row_data.get("loads_7d") or 0)
        loads_30d_val = int(row_data.get("loads_30d") or 0)
        needs_opt = bool(row_data.get("needs_optimization"))
        if is_ghost:
            status_label = "ghost"
        elif loads_30d_val == 0:
            status_label = "zombie_30d"
        elif needs_opt:
            status_label = "needs_optimization"
        else:
            status_label = "active"

        obs_ch = per_skill_channels.get(sk, {})
        l2_ch = l2_channels.get(sk, {})
        source_breakdown = {
            ch_key: max(int(obs_ch.get(ch_key) or 0), int(l2_ch.get(ch_key) or 0))
            for ch_key in ("claude", "copilot", "codex", "hermes", "gtt")
        }

        cache_b = per_skill_cache.get(sk, {})
        tot_in_sk = int(cache_b.get("in") or 0)
        tot_cr_sk = int(cache_b.get("cache_read") or 0)
        sk_kv_cache_pct = (
            round(100.0 * tot_cr_sk / (tot_in_sk + tot_cr_sk), 1)
            if (tot_in_sk + tot_cr_sk) > 0
            else s11_context.get("kv_cache_hit_pct")
        )

        # Merge physical references + observed warehouse reference_hits
        phys_refs = {entry["rel_path"]: dict(entry) for entry in inv_meta.get("reference_entries", [])}
        obs_refs = per_skill_refs.get(sk, {})
        merged_ref_paths = sorted(set(phys_refs.keys()) | set(obs_refs.keys()))
        ref_items: list[dict[str, Any]] = []
        for rel_p in merged_ref_paths:
            p_info = phys_refs.get(rel_p)
            o_info = obs_refs.get(rel_p)
            hits_val: int | None
            sessions_val: int | None
            if warehouse_present:
                hits_val = int(o_info["hits"]) if o_info else 0
                sessions_val = len(o_info["sessions"]) if o_info else 0
            else:
                hits_val = None
                sessions_val = None
            obs_bytes = int(o_info["max_bytes"]) if o_info else 0
            tok_est = int(p_info["tokens_est"]) if p_info else max(1, obs_bytes // 4)
            ln_cnt = int(p_info["line_count"]) if p_info else max(1, obs_bytes // 48) if obs_bytes else 0
            ref_item: dict[str, Any] = {
                "rel_path": rel_p,
                "line_count": ln_cnt,
                "tokens_est": tok_est,
                "hits_30d": hits_val,
                "sessions_30d": sessions_val,
                "is_unhit": bool(hits_val == 0),
            }
            if p_info and isinstance(p_info.get("doc_preview"), str) and p_info["doc_preview"]:
                ref_item["doc_preview"] = p_info["doc_preview"]
            ref_items.append(ref_item)

        unhit_refs_count = sum(1 for r in ref_items if r["is_unhit"])
        unhit_refs_ratio = round(unhit_refs_count / len(ref_items), 4) if ref_items else 0.0
        refs_tokens_est = (
            int(inv_meta["refs_tokens_est"])
            if "refs_tokens_est" in inv_meta
            else sum(int(r.get("tokens_est") or 0) for r in ref_items)
        )
        refs_kb = (
            float(inv_meta["refs_kb"])
            if "refs_kb" in inv_meta
            else round((refs_tokens_est * 4) / 1024.0, 1)
        )
        bundle_kb = (
            float(inv_meta["bundle_kb"])
            if "bundle_kb" in inv_meta
            else round(((body_tok_est + refs_tokens_est) * 4) / 1024.0, 1)
        )

        # Merge physical scripts + observed warehouse script_call (strictly filtered)
        phys_scripts = {sc for sc in inv_meta.get("script_entries", []) if _is_valid_skill_script_rel_path(sc)}
        obs_scripts = {
            sc: info
            for sc, info in per_skill_scripts.get(sk, {}).items()
            if _is_valid_skill_script_rel_path(sc)
        }
        merged_script_paths = sorted(phys_scripts | set(obs_scripts.keys()))
        script_items: list[dict[str, Any]] = []
        for rel_sc in merged_script_paths:
            o_sc = obs_scripts.get(rel_sc)
            if warehouse_present:
                calls_val: int | None = int(o_sc["calls"]) if o_sc else 0
            else:
                calls_val = None
            if o_sc and int(o_sc["calls"]) > 0:
                graded = int(o_sc.get("graded_calls") or 0)
                succ_rate = round(int(o_sc["successes"]) / graded, 4) if graded > 0 else None
                durs = [float(v) for v in o_sc.get("durations", [])]
                p95_val = round(float(_p95(durs)), 2) if durs else None
            else:
                succ_rate = None
                p95_val = None
            script_items.append({
                "rel_path": rel_sc,
                "calls_30d": calls_val,
                "success_rate": succ_rate,
                "p95_ms": p95_val,
                "is_unpracticed": bool(calls_val == 0),
            })

        gate_script_calls_30d = sum(
            int(s.get("calls_30d") or 0)
            for s in script_items
            if Path(str(s["rel_path"])).name in _GATE_SCRIPT_BASENAMES
        )

        # Merge parsed eval_cases + observed per-skill eval case events
        parsed_cases = [dict(c) for c in inv_meta.get("eval_cases", [])]
        obs_cases = per_skill_eval_cases.get(sk, {})
        known_case_ids = {c["id"] for c in parsed_cases}
        eval_case_items: list[dict[str, Any]] = []
        for idx_c, c_entry in enumerate(parsed_cases):
            cid = c_entry["id"]
            c_obs = obs_cases.get(cid, {})
            int_30d = int(c_obs.get("interceptions_30d") or 0)
            if idx_c == 0 and int_30d == 0 and gate_script_calls_30d > 0 and c_entry.get("status", "active") == "active":
                int_30d = gate_script_calls_30d
            raw_title = str(c_entry.get("title") or "").strip()
            safe_title = (
                raw_title
                if raw_title and raw_title != cid
                else f"{cid} — {c_entry.get('assertion_summary') or 'Contract gate verification check'}"
            )
            eval_case_items.append({
                "id": cid,
                "title": safe_title,
                "kind": c_entry["kind"],
                "assertion_summary": c_entry["assertion_summary"],
                "status": c_entry.get("status", "active"),
                "interceptions_30d": int_30d,
                "escapes_30d": int(c_obs.get("escapes_30d") or 0),
            })
        for cid, c_obs in sorted(obs_cases.items()):
            if cid in known_case_ids or not _EVAL_CASE_ID_RE.match(cid):
                continue
            global_match = global_eval_catalog.get(cid)
            if global_match:
                m_title = str(global_match.get("title") or "").strip()
                m_summary = str(global_match.get("assertion_summary") or f"Observed gate event for {cid}")
                m_kind = str(global_match.get("kind") or "contract")
                safe_title = m_title if m_title and m_title != cid else f"{cid} — {m_summary}"
            else:
                m_summary = f"Observed gate verification event for {cid}"
                m_kind = "contract"
                safe_title = f"{cid} — Gate contract verification"
            eval_case_items.append({
                "id": cid,
                "title": safe_title,
                "kind": m_kind,
                "assertion_summary": m_summary,
                "status": "active",
                "interceptions_30d": int(c_obs.get("interceptions_30d") or 0),
                "escapes_30d": int(c_obs.get("escapes_30d") or 0),
            })

        total_eval_interceptions = max(
            int(row_data.get("eval_interceptions") or 0),
            sum(int(c.get("interceptions_30d") or 0) for c in eval_case_items),
        )

        detail_meta = {
            key: row_data[key]
            for key in ("window_cst", "window_start", "window_end", "source_as_of", "sample_size", "coverage_status")
            if key in row_data
        }
        if not detail_meta:
            detail_meta = _freshness([], warehouse_present, 30)

        skill_details.append({
            **detail_meta,
            "skill": sk,
            "is_ghost": is_ghost,
            "scope": scope,
            "repo": repo_label,
            "skill_dir": skill_dir_str,
            "skill_md_path": "SKILL.md",
            "skill_md_lines": skill_md_lines,
            "frontmatter_tokens_est": fm_tok_est,
            "body_tokens_est": body_tok_est,
            "bundle_kb": bundle_kb,
            "refs_kb": refs_kb,
            "refs_tokens_est": refs_tokens_est,
            "doc_preview": str(inv_meta.get("doc_preview") or ""),
            "loads_7d": loads_7d_val,
            "loads_30d": loads_30d_val,
            "empty_load_rate": row_data.get("empty_load_rate"),
            "unpracticed_rate": row_data.get("unpracticed_rate"),
            "unhit_refs_count": unhit_refs_count,
            "unhit_refs_ratio": unhit_refs_ratio,
            "roi_score": row_data.get("roi_score"),
            "r1_pass_rate": row_data.get("r1_pass_rate"),
            "avg_context_tokens": row_data.get("avg_context_tokens"),
            "kv_cache_hit_pct": sk_kv_cache_pct,
            "edit_churn": int(row_data.get("edit_churn_count") or 0),
            "retry_churn": int(row_data.get("retry_commands") or 0),
            "impl_bug_escapes": int(row_data.get("impl_bug_escapes") or 0),
            "spec_gap_escapes": int(row_data.get("spec_gap_escapes") or 0),
            "eval_interceptions": total_eval_interceptions,
            "eval_escapes": int(row_data.get("eval_escapes") or 0),
            "status_label": status_label,
            "source_breakdown": source_breakdown,
            "references": ref_items,
            "scripts": script_items,
            "eval_cases": eval_case_items,
        })
    return skill_details


def compute_skill_roi_diagnostics(
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    magpie_usage: Path | None = None,
    dsh_sessions: Path | None = None,
    telemetry_shards_dir: Path | None = None,
    *,
    auto_propose: bool = False,
    proposals_path: Path | None = None,
    icloud_sync_dir: Path | None = None,
    mirror_icloud_roi: bool | None = None,
    icloud_roi_path: Path | None = None,
) -> dict[str, Any]:
    """Compute S1-S4, S6-S10 Skill ROI diagnostics, and S11-S12 Context & Tool Burn."""
    wh = warehouse_db or DEFAULT_WAREHOUSE_DB
    cp = copilot_db or DEFAULT_COPILOT_DB
    s_root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    prop_path = proposals_path if proposals_path is not None else DEFAULT_PROPOSALS_PATH
    resolved_sync = _resolve_icloud_sync_dir(icloud_sync_dir)
    shards_dir = (
        telemetry_shards_dir
        if telemetry_shards_dir is not None
        else (
            resolved_sync / "telemetry-shards"
            if resolved_sync is not None and (icloud_sync_dir is not None or (warehouse_db is None and copilot_db is None))
            else None
        )
    )
    effective_roi_path = icloud_roi_path or (
        resolved_sync / "jobs" / "aidata-digest" / "skills-roi.json" if resolved_sync is not None else None
    )
    should_mirror_roi = (
        mirror_icloud_roi
        if mirror_icloud_roi is not None
        else (icloud_roi_path is not None or icloud_sync_dir is not None or (resolved_sync is not None and warehouse_db is None and copilot_db is None))
    )

    inventory = _discover_skill_inventory(s_root, d_root)
    known_skills = set(inventory.keys())
    script_to_skill: dict[str, str] = {}
    script_basename_to_targets: dict[str, list[tuple[str, str]]] = {}
    script_realpath_to_targets: dict[str, list[tuple[str, str]]] = {}
    for s_name, meta in inventory.items():
        for sc in meta.get("scripts", []):
            script_to_skill[sc] = s_name
        for rel_sc in meta.get("script_entries", []):
            if not _is_valid_skill_script_rel_path(rel_sc):
                continue
            bname = Path(rel_sc).name
            script_basename_to_targets.setdefault(bname, []).append((s_name, rel_sc))
        for rel_sc, resolved_p in (meta.get("script_realpaths") or {}).items():
            if resolved_p and _is_valid_skill_script_rel_path(rel_sc):
                script_realpath_to_targets.setdefault(resolved_p, []).append((s_name, rel_sc))
    basename_regex = (
        re.compile(
            r"(?<![A-Za-z0-9_./-])("
            + "|".join(re.escape(bn) for bn in sorted(script_basename_to_targets.keys(), key=len, reverse=True))
            + r")(?![A-Za-z0-9_.-])"
        )
        if script_basename_to_targets
        else None
    )

    wall_clock_date = _cst_today_str()
    historical_dates: list[datetime] = []
    if _safe_is_file(wh):
        with contextlib.suppress(sqlite3.Error, OSError), _safe_sqlite_connect(wh) as conn:
            if conn is not None:
                for table in ("fact_turn", "fact_gtt_task_run", "skill_day", "hermes_skill_day"):
                    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                    column = next((name for name in ("cst_day", "day", "date", "occurred_at", "timestamp", "started_at", "created_at") if name in columns), None)
                    if column:
                        for row in conn.execute(f'SELECT "{column}" FROM "{table}"'):
                            stamp = _observed_at(row[0])
                            if stamp and stamp < _window_bounds()[1]:
                                historical_dates.append(stamp)
    anchor_as_of = max(historical_dates).date().isoformat() if historical_dates else None
    staleness_days = (date.fromisoformat(wall_clock_date) - date.fromisoformat(anchor_as_of)).days if anchor_as_of else None
    window_anchor = {"anchor_date": anchor_as_of, "source_as_of": anchor_as_of, "wall_clock_date": wall_clock_date,
                     "staleness_days": staleness_days, "coverage_status": "missing" if staleness_days is None else "fresh" if staleness_days <= 2 else "stale",
                     "window_days_7d": 7, "window_days_30d": 30, "basis": "historical_source_watermark; not the live metric window"}

    # Per-skill accumulators
    stats: dict[str, dict[str, Any]] = {
        s_name: {
            "skill": s_name,
            "loads_7d": 0,
            "loads_30d": 0,
            "script_execs": 0,
            "script_failures": 0,
            "script_touch_sessions": 0,
            "script_durations_ms": [],
            "has_scripts": bool(meta.get("scripts")),
            "reference_hit_count": 0,
            "reference_bytes": 0,
            "empty_load_sessions": 0,
            "eval_interceptions": 0,
            "eval_escapes": 0,
            "scripts": list(meta.get("scripts", [])),
            "l0_only_sessions": 0,
            "l1_selective_sessions": 0,
            "l1_greedy_sessions": 0,
            "total_tokens": 0,
            "token_session_count": 0,
            "session_count": 0,
            "edit_churn_count": 0,
            "retry_commands": 0,
            "r1_total": 0,
            "r1_pass": 0,
            "rework_rounds": 0,
            "impl_bug_escapes": 0,
            "spec_gap_escapes": 0,
        }
        for s_name, meta in inventory.items()
    }

    def _ensure_skill(name: str) -> dict[str, Any]:
        norm = "team-lead" if name == "goal-target-task" else name
        if norm not in stats:
            stats[norm] = {
                "skill": norm,
                "loads_7d": 0,
                "loads_30d": 0,
                "script_execs": 0,
                "script_failures": 0,
                "script_touch_sessions": 0,
                "script_durations_ms": [],
                "has_scripts": False,
                "reference_hit_count": 0,
                "reference_bytes": 0,
                "empty_load_sessions": 0,
                "eval_interceptions": 0,
                "eval_escapes": 0,
                "scripts": [],
                "l0_only_sessions": 0,
                "l1_selective_sessions": 0,
                "l1_greedy_sessions": 0,
                "total_tokens": 0,
                "token_session_count": 0,
                "session_count": 0,
                "edit_churn_count": 0,
                "retry_commands": 0,
                "r1_total": 0,
                "r1_pass": 0,
                "rework_rounds": 0,
                "impl_bug_escapes": 0,
                "spec_gap_escapes": 0,
            }
        return stats[norm]

    populations: dict[str, dict[str, list[datetime]]] = {}
    observed: list[datetime] = []
    def observe(skill: str, kind: str, stamp: datetime) -> dict[str, Any]:
        entry = _ensure_skill(skill)
        populations.setdefault(entry["skill"], {}).setdefault(kind, []).append(stamp)
        return entry

    s2_gate = {"total_runs_7d": 0, "true_positive_catches_7d": 0, "config_or_rerun_noise_7d": 0,
               "classified_sample_size": 0, "gate_catch_rate": None, "noise_rate": None,
               **_freshness([], wh.is_file())}
    eval_total = 0
    eval_interceptions = 0
    eval_escapes = 0
    eval_by_case: dict[str, dict[str, int]] = {}
    per_skill_eval_cases: dict[str, dict[str, dict[str, int]]] = {}
    per_skill_refs: dict[str, dict[str, dict[str, Any]]] = {}
    per_skill_scripts: dict[str, dict[str, dict[str, Any]]] = {}
    per_skill_channels: dict[str, dict[str, int]] = {}
    per_skill_cache: dict[str, dict[str, int]] = {}
    session_records: dict[tuple[str, str, str], dict[str, Any]] = {}
    turn_skill_links: dict[str, set[str]] = {}
    start7, _ = _window_bounds()
    start30, end30 = _window_bounds(30)
    shard_records_30d = _read_telemetry_shards(shards_dir, days=30)
    shards_present = bool(shards_dir and _safe_glob(shards_dir, "*-skill-usage.jsonl"))
    warehouse_present = wh.is_file() or cp.is_file() or shards_present
    l2_channels = _read_l2_channel_counts(wh, start30, end30)
    seen_turn_sigs_30d: set[tuple[str, str, str]] = set()
    seen_ref_sigs_30d: set[tuple[str, str, str]] = set()
    seen_script_sigs_30d: set[tuple[str, str, str]] = set()

    def _norm_skill(name: str) -> str:
        return "team-lead" if name in {"goal-target-task", "goal-task"} else name

    def _bump_channel(skill: str, channel: str, delta: int = 1) -> None:
        sk = _norm_skill(skill)
        bucket = per_skill_channels.setdefault(sk, {"claude": 0, "copilot": 0, "codex": 0, "hermes": 0, "gtt": 0})
        bucket[channel] = bucket.get(channel, 0) + max(0, int(delta))

    def session_for(source: str, sid: str, skill: str, stamp: datetime, *, reference_source: bool = False) -> dict[str, Any]:
        skill = _norm_skill(skill)
        key = (source, sid or f"row-{len(session_records)}", skill)
        record = session_records.setdefault(key, {
            "source": source, "sid": sid, "skill": skill, "stamp": stamp, "loaded": True,
            "refs": {}, "reference_source": reference_source, "read_proxy": 0,
            "scripts": [], "edits": 0, "tokens": 0, "token_seen": False,
        })
        record["stamp"] = max(record["stamp"], stamp)
        record["reference_source"] = record["reference_source"] or reference_source
        return record

    def add_reference(source: str, sid: str, skill: str, path: str, stamp: datetime, byte_len: Any = 0) -> None:
        norm_sk = _norm_skill(skill)
        record = session_for(source, sid, norm_sk, stamp, reference_source=True)
        ref_path = str(path or "").strip()
        if ref_path:
            rel_norm = _normalize_skill_rel_path(ref_path, prefix="references") or ref_path
            if sid:
                seen_ref_sigs_30d.add((sid, norm_sk, rel_norm))
            try:
                size = max(0, int(byte_len or 0))
            except (TypeError, ValueError):
                size = 0
            record["refs"].setdefault(ref_path, size)
            rel_ref = _normalize_skill_rel_path(ref_path, prefix="references")
            if rel_ref:
                ref_bucket = per_skill_refs.setdefault(norm_sk, {}).setdefault(
                    rel_ref, {"hits": 0, "sessions": set(), "max_bytes": 0}
                )
                ref_bucket["hits"] += 1
                ref_bucket["sessions"].add(sid or f"{source}:{stamp.isoformat()}")
                ref_bucket["max_bytes"] = max(ref_bucket["max_bytes"], size)
        observed.append(stamp)
        observe(norm_sk, "usage", stamp)

    def add_script(source: str, sid: str, skill: str, path: str, stamp: datetime, exit_code: Any = 0, duration_ms: Any = None) -> None:
        norm_sk = _norm_skill(skill)
        raw_path_str = str(path or "").strip()
        is_legacy_proxy = raw_path_str == "legacy:bash"
        rel_script = "" if is_legacy_proxy else _normalize_skill_rel_path(raw_path_str, prefix="scripts")
        if not is_legacy_proxy and not _is_valid_skill_script_rel_path(rel_script):
            return

        has_exit = exit_code not in (None, "")
        try:
            code = int(exit_code) if has_exit else 0
        except (TypeError, ValueError):
            code = 0
            has_exit = False
        has_dur = duration_ms not in (None, "")
        try:
            duration = max(0.0, float(duration_ms)) if has_dur else 0.0
        except (TypeError, ValueError):
            duration = 0.0
            has_dur = False

        def _record_single_script(target_sk: str, target_rel: str, *, is_proxy: bool = False) -> None:
            record = session_for(source, sid, target_sk, stamp, reference_source=source == "copilot")
            sig_path = "legacy:bash" if is_proxy else target_rel
            call = (sig_path, stamp.isoformat(), code, duration)
            if sid and sig_path:
                seen_script_sigs_30d.add((sid, target_sk, sig_path))
            if call not in record["scripts"]:
                record["scripts"].append(call)
                if not is_proxy and target_rel:
                    sc_bucket = per_skill_scripts.setdefault(target_sk, {}).setdefault(
                        target_rel, {"calls": 0, "successes": 0, "graded_calls": 0, "durations": []}
                    )
                    sc_bucket["calls"] += 1
                    if has_exit or source == "warehouse":
                        sc_bucket["graded_calls"] += 1
                        if code == 0:
                            sc_bucket["successes"] += 1
                    if has_dur:
                        sc_bucket["durations"].append(duration)
            entry = _ensure_skill(target_sk)
            entry["has_scripts"] = True
            if not is_proxy and target_rel and target_rel not in entry["scripts"]:
                entry["scripts"].append(target_rel)
            observed.append(stamp)
            observe(target_sk, "usage", stamp)

        if is_legacy_proxy:
            _record_single_script(norm_sk, "", is_proxy=True)
            return

        targets: list[tuple[str, str]] = []
        inv_entry = inventory.get(norm_sk)
        if inv_entry and rel_script in set(inv_entry.get("script_entries", [])):
            targets.append((norm_sk, rel_script))
            rp = (inv_entry.get("script_realpaths") or {}).get(rel_script)
            if rp:
                for sym_sk, sym_rel in script_realpath_to_targets.get(rp, []):
                    if (sym_sk, sym_rel) not in targets:
                        targets.append((sym_sk, sym_rel))
        else:
            bname = Path(rel_script).name
            by_name = script_basename_to_targets.get(bname, [])
            if any(t_sk == norm_sk for t_sk, _ in by_name):
                for t_sk, t_rel in by_name:
                    if t_sk == norm_sk and (t_sk, t_rel) not in targets:
                        targets.append((t_sk, t_rel))
            elif len(by_name) == 1 and norm_sk not in known_skills:
                targets.append(by_name[0])
            else:
                targets.append((norm_sk, rel_script))

        for t_sk, t_rel in targets:
            _record_single_script(t_sk, t_rel)

    def _attribute_basename_scripts_in_text(
        source: str,
        sid: str,
        text: str,
        active_skills: set[str],
        stamp: datetime,
        exit_code: Any = None,
        duration_ms: Any = None,
    ) -> set[str]:
        """Match bare script basenames (e.g. goal-queue.py) when full skills/<sk>/scripts/ prefix is omitted."""
        credited: set[str] = set()
        if not basename_regex or not text:
            return credited
        explicit_basenames = {Path(m.group("script")).name for m in _SCRIPT_PATH_RE.finditer(text)}
        for m in basename_regex.finditer(text):
            bname = m.group(1)
            if bname in explicit_basenames:
                continue
            candidates = script_basename_to_targets.get(bname, [])
            if not candidates:
                continue
            overlapping = [(c_sk, c_rel) for c_sk, c_rel in candidates if c_sk in active_skills]
            chosen = overlapping if overlapping else (candidates if len(candidates) == 1 else [])
            for c_sk, c_rel in chosen:
                add_script(source, sid, c_sk, f"skills/{c_sk}/{c_rel}", stamp, exit_code, duration_ms)
                credited.add(c_sk)
        return credited

    goal_skills_cache: dict[str, set[str]] = {}

    def _handoff_skills_for_goal(goal: str) -> set[str]:
        if not goal:
            return set()
        if goal in goal_skills_cache:
            return goal_skills_cache[goal]
        handoff = d_root / "handoff" / goal
        text_parts: list[str] = []
        if _probe_readable_dir(handoff):
            spec_p = handoff / "spec.md"
            if _safe_is_file(spec_p):
                try:
                    text_parts.append(spec_p.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    pass
            briefs_dir = handoff / "briefs"
            if _probe_readable_dir(briefs_dir):
                for path in _safe_glob(briefs_dir, "*.md"):
                    if _safe_is_file(path):
                        try:
                            text_parts.append(path.read_text(encoding="utf-8", errors="ignore"))
                        except OSError:
                            pass
        extracted = _extract_skill_names("\n".join(text_parts), known_skills) if text_parts else set()
        goal_skills_cache[goal] = extracted
        return extracted

    def task_skills(row: dict[str, Any], *, keys_to_skills: dict[str, set[str]]) -> set[str]:
        raw_explicit = {str(item).strip() for item in _json_list(row.get("skills_used")) if str(item).strip()}
        if not raw_explicit and row.get("skills_used"):
            raw_explicit = _extract_skill_names(row.get("skills_used"), known_skills)
        normalized_explicit = {"team-lead" if item in {"goal-target-task", "goal-task"} else item for item in raw_explicit}
        explicit = normalized_explicit & known_skills if known_skills else normalized_explicit
        if explicit:
            return explicit
        goal = str(row.get("goal_id") or "")
        inferred = set(_handoff_skills_for_goal(goal))
        for key in (row.get("goal_id"), row.get("git_branch"), row.get("repo")):
            if key:
                inferred.update(keys_to_skills.get(str(key).strip().lower(), set()))
        if inferred:
            return inferred
        goal_lower = goal.lower()
        inferred = {"team-lead"}
        for skill, hints in (("my-designer", ("design", "ui", "feel", "glass", "hallmark")),
                             ("design-studio", ("ingest", "palette", "motion")),
                             ("retro-loop", ("retro", "telemetry", "roi", "aidash")),
                             ("knowledge-digest", ("learn", "digest", "blog", "vault")),
                             ("skill-contract", ("skill", "contract")),
                             ("m2-dispatcher", ("m2", "herdr", "dispatch"))):
            if any(hint in goal_lower for hint in hints):
                inferred.add(skill)
        return inferred

    if wh.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{wh}?mode=ro", uri=True)) as conn:
            ci_rows = _timed_rows(conn, "fact_ci_job_run")
            catches = sum(row.get("failure_category") in {"true_positive_gate", "build_or_test_failure"} for row in ci_rows)
            noise = sum(str(row.get("failure_category") or "").startswith("config_drift_") or row.get("failure_category") == "runner_capacity_or_sim" for row in ci_rows)
            classified = catches + noise
            s2_gate.update(total_runs_7d=len(ci_rows), true_positive_catches_7d=catches,
                           config_or_rerun_noise_7d=noise, classified_sample_size=classified,
                           gate_catch_rate=round(catches / classified, 4) if classified else None,
                           noise_rate=round(noise / len(ci_rows), 4) if ci_rows else None)
            s2_gate.update(_freshness([r["_observed_at"] for r in ci_rows], True))
            if not classified:
                s2_gate["coverage_status"] = "unknown"
            observed.extend(row["_observed_at"] for row in ci_rows)
            turn_rows = _timed_rows(conn, "fact_turn", 30)
            wh_session_tokens: dict[str, tuple[int, int, int]] = {}
            if _table_columns(conn, "fact_request"):
                with contextlib.suppress(sqlite3.Error):
                    req_start_ms = int(start30.timestamp() * 1000)
                    req_end_ms = int(end30.timestamp() * 1000)
                    for r_sid, r_in, r_out, r_cr in conn.execute(
                        "SELECT session_uuid, COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), COALESCE(SUM(cache_read), 0) "
                        "FROM fact_request WHERE session_uuid IS NOT NULL AND session_uuid != '' AND ts >= ? AND ts < ? GROUP BY session_uuid",
                        (req_start_ms, req_end_ms),
                    ):
                        wh_session_tokens[str(r_sid)] = (int(r_in or 0), int(r_out or 0), int(r_cr or 0))
            ref_table = next((name for name in ("reference_hits", "fact_skill_reference_hit") if _table_columns(conn, name)), None)
            script_table = next((name for name in ("script_call", "fact_skill_script_call") if _table_columns(conn, name)), None)
            for number, row in enumerate(turn_rows):
                stamp = row["_observed_at"]
                skills = {str(item) for item in _json_list(row.get("skill_names")) if str(item).strip()}
                if row.get("attribution_skill"):
                    skills.add(str(row["attribution_skill"]))
                sid = str(row.get("session_id") or row.get("turn_uuid") or f"turn-{number}")
                keys = [row.get(key) for key in ("goal_id", "git_branch", "repo")]
                for key in keys:
                    if key:
                        turn_skill_links.setdefault(str(key).strip().lower(), set()).update(skills)
                tools = _json_list(row.get("tool_calls"))
                norm_skills_in_turn = {_norm_skill(s) for s in skills}
                for skill in skills:
                    norm_sk = _norm_skill(skill)
                    seen_turn_sigs_30d.add((sid, str(row.get("turn_uuid") or f"turn-{number}"), norm_sk))
                    _bump_channel(norm_sk, "claude")
                    cache_bucket = per_skill_cache.setdefault(norm_sk, {"in": 0, "cache_read": 0})
                    cache_bucket["in"] += int(row.get("input_tokens") or 0)
                    cache_bucket["cache_read"] += int(row.get("cache_read") or 0)
                    record = session_for("warehouse", sid, skill, stamp, reference_source=bool(ref_table))
                    record["read_proxy"] += sum(str(tool).lower() in {"read", "view", "read_file"} for tool in tools)
                    record["edits"] += sum(str(tool).lower() in {"edit", "write", "multiedit", "apply_patch", "create"} for tool in tools)
                    record["tokens"] += sum(int(row.get(key) or 0) for key in ("input_tokens", "output_tokens", "cache_read"))
                    record["token_seen"] = record["token_seen"] or any(row.get(key) is not None for key in ("input_tokens", "output_tokens", "cache_read"))
                    if (not script_table and any(str(tool).lower() in {"bash", "execute_code", "terminal"} for tool in tools)
                            and not any(_SCRIPT_PATH_RE.search(str(tool)) for tool in tools) and _ensure_skill(skill)["has_scripts"]):
                        # Legacy fixtures predate script_call; retain their Bash proxy without overriding observed calls.
                        add_script("warehouse", sid, skill, "legacy:bash", stamp)
                    observe(skill, "usage", stamp)
                for tool in tools:
                    text = str(tool.get("command") or tool.get("path") or tool) if isinstance(tool, dict) else str(tool)
                    for match in _REF_PATH_RE.finditer(text):
                        add_reference("warehouse", sid, match.group("skill"), f"skills/{match.group('skill')}/references/{match.group('ref')}", stamp)
                    for match in _SCRIPT_PATH_RE.finditer(text):
                        add_script("warehouse", sid, match.group("skill"), f"skills/{match.group('skill')}/scripts/{match.group('script')}", stamp)
                    _attribute_basename_scripts_in_text("warehouse", sid, text, norm_skills_in_turn, stamp)
                if skills:
                    observed.append(stamp)
            for (src_key, sid_key, sk_key), rec_val in session_records.items():
                if src_key == "warehouse" and not rec_val["token_seen"] and sid_key in wh_session_tokens:
                    r_in, r_out, r_cr = wh_session_tokens[sid_key]
                    if r_in + r_out + r_cr > 0:
                        rec_val["tokens"] = r_in + r_out + r_cr
                        rec_val["token_seen"] = True
                        cache_b = per_skill_cache.setdefault(sk_key, {"in": 0, "cache_read": 0})
                        cache_b["in"] += r_in
                        cache_b["cache_read"] += r_cr
            for source_table in ("copilot_cli", "codex_prompts", "skill_day", "hermes_skill_day"):
                if not _table_columns(conn, source_table):
                    continue
                for row in _timed_rows(conn, source_table, 30):
                    if source_table in {"skill_day", "hermes_skill_day"}:
                        sk = str(row.get("skill") or "").strip()
                        cnt = int(row.get("calls") or row.get("n") or 1)
                        if sk:
                            _bump_channel(sk, "hermes" if source_table == "hermes_skill_day" else "copilot", cnt)
                        continue
                    blob = " ".join(str(row.get(key) or "") for key in ("skill_names", "attribution_skill", "user_message", "assistant_response", "prompt"))
                    skills = {str(item) for item in _json_list(row.get("skill_names")) if str(item).strip()}
                    skills.update(_extract_skill_names(blob, known_skills))
                    if row.get("attribution_skill"):
                        skills.add(str(row["attribution_skill"]))
                    ch_name = "codex" if source_table == "codex_prompts" else "copilot"
                    for sk in skills:
                        _bump_channel(sk, ch_name)
                    for key in (row.get("goal_id"), row.get("git_branch"), row.get("repo")):
                        if key:
                            turn_skill_links.setdefault(str(key).strip().lower(), set()).update(skills)
            if ref_table:
                for row in _timed_rows(conn, ref_table, 30):
                    skill = str(row.get("skill") or "").strip()
                    path = str(row.get("reference_path") or row.get("ref_path") or "").strip()
                    if skill and path:
                        src_col = str(row.get("source") or "").lower()
                        _bump_channel(skill, "codex" if "codex" in src_col else "copilot")
                        add_reference("warehouse", str(row.get("session_id") or row.get("turn_uuid") or ""), skill, path, row["_observed_at"], row.get("byte_len"))
            if script_table:
                for row in _timed_rows(conn, script_table, 30):
                    skill = str(row.get("skill") or "").strip()
                    path = str(row.get("script_path") or row.get("path") or "").strip()
                    if skill and path:
                        src_col = str(row.get("source") or "").lower()
                        _bump_channel(skill, "codex" if "codex" in src_col else "copilot")
                        add_script("warehouse", str(row.get("session_id") or row.get("turn_uuid") or ""), skill, path, row["_observed_at"], row.get("exit_code"), row.get("duration_ms"))
            gtt_task_groups: dict[tuple[str, str], dict[str, Any]] = {}
            for idx_tr, row in enumerate(_timed_rows(conn, "fact_gtt_task_run", 30)):
                g_id = str(row.get("goal_id") or "")
                t_id = str(row.get("task_id") or f"task-{idx_tr}")
                role = str(row.get("role_name") or "").strip().lower()
                stamp = row["_observed_at"]
                row_skills = task_skills(row, keys_to_skills=turn_skill_links)
                for key in (row.get("goal_id"), row.get("git_branch"), row.get("repo")):
                    if key:
                        turn_skill_links.setdefault(str(key).strip().lower(), set()).update(row_skills)
                grp = gtt_task_groups.setdefault(
                    (g_id, t_id),
                    {
                        "skills": set(),
                        "max_round": 1,
                        "latest_stamp": stamp,
                        "has_reviewer": False,
                        "r1_failed": False,
                        "any_failed": False,
                        "rework_rounds": 0,
                    },
                )
                grp["skills"].update(_norm_skill(s) for s in row_skills)
                grp["latest_stamp"] = max(grp["latest_stamp"], stamp)
                r_no = max(1, int(row.get("round_no") or 1))
                grp["max_round"] = max(grp["max_round"], r_no)
                verdict_up = str(row.get("v5_verdict") or "").strip().upper()
                status_low = str(row.get("status") or "").strip().lower()
                is_reviewer_role = role in {"reviewer", "code_reviewer", "planner_reviewer"}
                if is_reviewer_role:
                    grp["has_reviewer"] = True
                    if r_no == 1:
                        if verdict_up not in {"PASS", "CONCERNS", "WAIVED"} or status_low == "failed":
                            grp["r1_failed"] = True
                    elif r_no >= 2:
                        grp["rework_rounds"] += 1
                else:
                    if status_low == "failed" or verdict_up == "FAIL":
                        grp["any_failed"] = True
                    if r_no >= 2:
                        grp["rework_rounds"] = max(grp["rework_rounds"], r_no - 1)
                for skill in row_skills:
                    _bump_channel(skill, "gtt")

            for grp in gtt_task_groups.values():
                stamp = grp["latest_stamp"]
                r1_passed = (
                    (not grp["r1_failed"] and grp["max_round"] == 1)
                    if grp["has_reviewer"]
                    else (grp["max_round"] == 1 and not grp["any_failed"])
                )
                for skill in grp["skills"]:
                    entry = observe(skill, "reviews", stamp)
                    entry["r1_total"] += 1
                    if r1_passed:
                        entry["r1_pass"] += 1
                    elif grp["r1_failed"] or grp["any_failed"]:
                        entry["impl_bug_escapes"] += 1
                    if grp["rework_rounds"] > 0:
                        entry["rework_rounds"] += grp["rework_rounds"]
                observed.append(stamp)
            for row in _timed_rows(conn, "fact_gtt_event", 30):
                event = row.get("evt_type")
                skills = task_skills(row, keys_to_skills=turn_skill_links)
                has_eval = any(row.get(key) not in (None, "") for key in ("eval_case", "interception", "escape"))
                is_7d = row["_observed_at"] >= start7
                if has_eval:
                    case = str(row.get("eval_case") or "unclassified")
                    int_flag = int(_flag(row.get("interception")))
                    esc_flag = int(_flag(row.get("escape")))
                    for skill in skills:
                        norm_sk = _norm_skill(skill)
                        _bump_channel(norm_sk, "gtt")
                        case_bucket = per_skill_eval_cases.setdefault(norm_sk, {}).setdefault(
                            case, {"interceptions_30d": 0, "escapes_30d": 0}
                        )
                        case_bucket["interceptions_30d"] += int_flag
                        case_bucket["escapes_30d"] += esc_flag
                    if is_7d:
                        bucket = eval_by_case.setdefault(case, {"observations": 0, "interceptions": 0, "escapes": 0})
                        bucket["observations"] += 1
                        eval_total += 1
                        if int_flag:
                            eval_interceptions += 1
                            bucket["interceptions"] += 1
                        if esc_flag:
                            eval_escapes += 1
                            bucket["escapes"] += 1
                        for skill in skills:
                            entry = observe(skill, "events", row["_observed_at"])
                            entry["eval_interceptions"] += int_flag
                            entry["eval_escapes"] += esc_flag
                if is_7d and event in {"REVIEW_FAIL", "RULING", "CHANGE_REQUEST"}:
                    note = str(row.get("note_preview") or "").upper()
                    for skill in skills:
                        entry = observe(skill, "events", row["_observed_at"])
                        entry["spec_gap_escapes" if "SPEC_GAP" in note or event in {"RULING", "CHANGE_REQUEST"} else "impl_bug_escapes"] += 1
                if is_7d and (event or has_eval):
                    observed.append(row["_observed_at"])

    if cp.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{cp}?mode=ro", uri=True)) as conn:
            files = _timed_rows(conn, "session_files", 30)
            turns = _timed_rows(conn, "turns", 30)
            cp_session_usage: dict[str, tuple[int, int, int, int]] = {}
            if _table_columns(conn, "assistant_usage_events"):
                for u_row in _timed_rows(conn, "assistant_usage_events", 30):
                    u_sid = str(u_row.get("session_id") or "")
                    if not u_sid:
                        continue
                    prev_in, prev_out, prev_cr, prev_cnt = cp_session_usage.get(u_sid, (0, 0, 0, 0))
                    cp_session_usage[u_sid] = (
                        prev_in + int(u_row.get("input_tokens") or 0),
                        prev_out + int(u_row.get("output_tokens") or 0),
                        prev_cr + int(u_row.get("cache_read_tokens") or 0),
                        prev_cnt + 1,
                    )
            pending_edits: dict[str, int] = {}
            for row in files + turns:
                sid = str(row.get("session_id") or "")
                stamp = row["_observed_at"]
                text = str(row.get("file_path") or "") if "file_path" in row else f"{row.get('user_message') or ''}\n{row.get('assistant_response') or ''}"
                matched = _extract_skill_names(text, known_skills)
                ref_matches = list(_REF_PATH_RE.finditer(text))
                script_matches = list(_SCRIPT_PATH_RE.finditer(text))
                matched.update(match.group("skill") for match in ref_matches)
                matched.update(match.group("skill") for match in script_matches)
                matched_norm = {_norm_skill(s) for s in matched}
                if "file_path" not in row:
                    matched_norm.update(
                        _attribute_basename_scripts_in_text(
                            "copilot",
                            sid,
                            text,
                            matched_norm,
                            stamp,
                            row.get("exit_code"),
                            row.get("duration_ms"),
                        )
                    )
                for skill in matched_norm:
                    norm_sk = _norm_skill(skill)
                    if sid:
                        seen_turn_sigs_30d.add((sid, f"turn-{row.get('turn_index', '')}", norm_sk))
                    _bump_channel(norm_sk, "copilot")
                    record = session_for("copilot", sid, norm_sk, stamp, reference_source=True)
                    if "input_tokens" in row:
                        in_tok = int(row.get("input_tokens") or 0)
                        cr_tok = int(row.get("cache_read") or 0)
                        cache_bucket = per_skill_cache.setdefault(norm_sk, {"in": 0, "cache_read": 0})
                        cache_bucket["in"] += in_tok
                        cache_bucket["cache_read"] += cr_tok
                        record["tokens"] += sum(int(row.get(key) or 0) for key in ("input_tokens", "output_tokens", "cache_read"))
                        record["token_seen"] = True
                    observe(norm_sk, "usage", stamp)
                for match in ref_matches:
                    add_reference("copilot", sid, match.group("skill"), f"skills/{match.group('skill')}/references/{match.group('ref')}", stamp, row.get("byte_len"))
                for match in script_matches:
                    add_script("copilot", sid, match.group("skill"), f"skills/{match.group('skill')}/scripts/{match.group('script')}", stamp, row.get("exit_code"), row.get("duration_ms"))
                tool_name = str(row.get("tool_name") or "").lower()
                if tool_name in {"edit", "write", "multiedit", "apply_patch", "create"}:
                    if matched_norm:
                        for skill in matched_norm:
                            session_for("copilot", sid, skill, stamp, reference_source=True)["edits"] += 1
                    else:
                        pending_edits[sid] = pending_edits.get(sid, 0) + 1
                observed.append(stamp)
            for (source, sid, skill), record in session_records.items():
                if source == "copilot" and pending_edits.get(sid):
                    record["edits"] += pending_edits[sid]
                if source == "copilot" and not record["token_seen"] and sid in cp_session_usage:
                    u_in, u_out, u_cr, u_cnt = cp_session_usage[sid]
                    if u_in + u_out + u_cr > 0:
                        avg_evt_tokens = max(1, (u_in + u_out + u_cr) // max(1, u_cnt))
                        record["tokens"] = avg_evt_tokens
                        record["token_seen"] = True
                        cache_b = per_skill_cache.setdefault(skill, {"in": 0, "cache_read": 0})
                        cache_b["in"] += u_in
                        cache_b["cache_read"] += u_cr
            for record in session_records.values():
                if record["source"] == "copilot" and record["edits"] >= 4:
                    observe(record["skill"], "churn", record["stamp"])["edit_churn_count"] += record["edits"]

    # Ingest deduplicated multi-device iCloud telemetry shards (*-skill-usage.jsonl)
    for s_row in shard_records_30d:
        kind = str(s_row.get("kind") or "")
        if kind not in {"skill_turn", "script_call", "reference_hit"}:
            continue
        raw_sk = str(s_row.get("skill") or "").strip()
        if not raw_sk:
            continue
        norm_sk = _norm_skill(raw_sk)
        sid = str(s_row.get("session_id") or "")
        tid = str(s_row.get("turn_id") or s_row.get("call_uid") or s_row.get("hit_uid") or "")
        host_tag = str(s_row.get("host") or "shard").strip()
        shard_src = f"shard:{host_tag}"
        stamp = s_row["_observed_at"]
        ch = str(s_row.get("channel") or "copilot").strip().lower()
        if ch not in {"claude", "copilot", "codex", "hermes", "gtt"}:
            ch = "copilot"

        if kind == "skill_turn":
            if (sid or tid) and (sid, tid, norm_sk) in seen_turn_sigs_30d:
                continue
            if sid or tid:
                seen_turn_sigs_30d.add((sid, tid, norm_sk))
            _bump_channel(norm_sk, ch)
            in_tok = int(s_row.get("input_tokens") or s_row.get("in") or 0)
            out_tok = int(s_row.get("output_tokens") or s_row.get("out") or 0)
            cr_tok = int(s_row.get("cache_read") or 0)
            cache_bucket = per_skill_cache.setdefault(norm_sk, {"in": 0, "cache_read": 0})
            cache_bucket["in"] += in_tok
            cache_bucket["cache_read"] += cr_tok
            record = session_for(shard_src, sid or tid, norm_sk, stamp, reference_source=True)
            tools = _json_list(s_row.get("tool_calls"))
            record["read_proxy"] += sum(str(t).lower() in {"read", "view", "read_file"} for t in tools)
            record["edits"] += sum(str(t).lower() in {"edit", "write", "multiedit", "apply_patch", "create"} for t in tools)
            if in_tok + out_tok + cr_tok > 0:
                record["tokens"] += in_tok + out_tok + cr_tok
                record["token_seen"] = True
            observed.append(stamp)
            observe(norm_sk, "usage", stamp)
        elif kind == "reference_hit":
            ref_p = str(s_row.get("reference_path") or "").strip()
            rel_norm = _normalize_skill_rel_path(ref_p, prefix="references") or ref_p
            if sid and (sid, norm_sk, rel_norm) in seen_ref_sigs_30d:
                continue
            _bump_channel(norm_sk, ch)
            add_reference(shard_src, sid or tid, norm_sk, ref_p, stamp, s_row.get("byte_len"))
        elif kind == "script_call":
            sc_p = str(s_row.get("script_path") or "").strip()
            rel_sc = _normalize_skill_rel_path(sc_p, prefix="scripts") or sc_p
            if sid and (sid, norm_sk, rel_sc) in seen_script_sigs_30d:
                continue
            _bump_channel(norm_sk, ch)
            add_script(shard_src, sid or tid, norm_sk, sc_p, stamp, s_row.get("exit_code"), s_row.get("duration_ms"))

    for record in session_records.values():
        if not record["loaded"]:
            continue
        skill = record["skill"]
        entry = _ensure_skill(skill)
        entry["session_count"] += 1
        entry["loads_30d"] += 1
        entry["loads_7d"] += record["stamp"] >= start7
        entry["total_tokens"] += record["tokens"]
        entry["token_session_count"] += int(record["token_seen"])
        refs = record["refs"]
        ref_count = len(refs) if record["reference_source"] else record["read_proxy"]
        entry["reference_hit_count"] += len(refs)
        entry["reference_bytes"] += sum(refs.values())
        entry["l0_only_sessions" if ref_count == 0 else "l1_selective_sessions" if ref_count <= 2 else "l1_greedy_sessions"] += 1
        if record["scripts"]:
            entry["script_touch_sessions"] += 1
            entry["script_execs"] += len(record["scripts"])
            entry["script_failures"] += sum(call[2] != 0 for call in record["scripts"])
            entry["script_durations_ms"].extend(call[3] for call in record["scripts"])
        if not refs and not record["scripts"] and record["edits"] == 0:
            entry["empty_load_sessions"] += 1
        if record["edits"] >= 4:
            entry["edit_churn_count"] += record["edits"]

    s2_gate["eval_effect"] = {
        "interceptions_7d": eval_interceptions,
        "escapes_7d": eval_escapes,
        "interception_rate": round(eval_interceptions / (eval_interceptions + eval_escapes), 4) if eval_interceptions + eval_escapes else None,
        "by_eval_case": eval_by_case,
    }
    source_meta = _freshness(observed, warehouse_present, 30)
    source_as_of = source_meta["source_as_of"]
    start7, end7 = _window_bounds()
    root_stamps = [stamp for stamp in observed if start7 <= stamp < end7]
    root_meta = _freshness(root_stamps, warehouse_present)

    # 3. Synthesize S1, S2, S3, S4, S6, S7, S8, S9, S10
    s1_pins = _compute_s1_pin_drift(d_root)

    # S3: Skill Health & Eval Growth
    budget_passed = sum(1 for m in inventory.values() if m["budget_ok"])
    top_l0 = sorted(inventory.values(), key=lambda m: (-m["l0_lines"], m["skill"]))[:3]
    top_l1 = sorted(inventory.values(), key=lambda m: (-m["max_l1_lines"], m["skill"]))[:3]
    total_eval_cases = sum(m["eval_case_count"] for m in inventory.values())
    weekly_eval_delta = sum(m["eval_case_weekly_delta"] for m in inventory.values())
    s3_health = {
        "total_skills": len(inventory),
        "audit_pass_count": budget_passed,
        "audit_pass_rate": round(budget_passed / len(inventory), 4) if inventory else None,
        "top3_l0_watermarks": [
            {"skill": m["skill"], "l0_lines": m["l0_lines"], "l0_cap": m["l0_cap"]}
            for m in top_l0
        ],
        "top3_l1_watermarks": [
            {"skill": m["skill"], "max_l1_lines": m["max_l1_lines"], "l1_cap": m["l1_cap"]}
            for m in top_l1
        ],
        "eval_case_total": total_eval_cases,
        "eval_case_weekly_delta": weekly_eval_delta,
    }

    s11_context, s12_tools, burn_proposals = _compute_s11_s12_context_and_tools(
        wh,
        magpie_usage=magpie_usage,
        copilot_db=cp,
        dsh_sessions=dsh_sessions,
        telemetry_shards_dir=shards_dir,
    )

    for l2_sk, l2_map in l2_channels.items():
        if sum(int(v or 0) for v in l2_map.values()) > 0:
            _ensure_skill(l2_sk)

    # Build per-skill ROI table for S4, S6, S7, S8, S9, S10
    skill_rows: list[dict[str, Any]] = []
    idle_skills_30d: list[str] = []
    for s_name, st in sorted(stats.items()):
        l2_ch_map = l2_channels.get(s_name, {})
        l2_sum_30d = sum(int(v or 0) for v in l2_ch_map.values())
        loads = max(st["loads_30d"], st["loads_7d"], st["session_count"], l2_sum_30d)
        if loads == 0 and s_name in known_skills:
            idle_skills_30d.append(s_name)

        meta_inv = inventory.get(s_name, {})
        l0_lines = int(meta_inv.get("l0_lines") or 0)
        max_l1_lines = int(meta_inv.get("max_l1_lines") or 0)
        fm_tok_est = int(meta_inv.get("frontmatter_tokens_est") or 1)
        body_tok_est = int(meta_inv.get("body_tokens_est") or max(1, l0_lines * 12))

        r1_tot = st["r1_total"]
        if r1_tot > 0:
            r1_pass_rate: float | None = round(st["r1_pass"] / r1_tot, 4)
        elif loads > 0:
            clean_loads = max(
                0,
                loads
                - st["impl_bug_escapes"]
                - st["spec_gap_escapes"]
                - min(loads, st["script_failures"])
                - (1 if st["edit_churn_count"] >= 4 else 0),
            )
            r1_pass_rate = round(max(0.05, clean_loads / loads), 4)
        else:
            r1_pass_rate = None

        obs_script_calls_30d = sum(
            int(sc_info.get("calls") or 0)
            for sc_path, sc_info in per_skill_scripts.get(s_name, {}).items()
            if _is_valid_skill_script_rel_path(sc_path)
        )
        script_execs = max(st["script_execs"], obs_script_calls_30d)
        has_valid_scripts = bool(meta_inv.get("script_entries")) or bool(st["has_scripts"])
        if has_valid_scripts and loads > 0:
            practiced_sessions = min(loads, max(st["script_touch_sessions"], min(loads, script_execs)))
            script_ratio: float | None = round(practiced_sessions / loads, 4)
            read_only_unpracticed_rate: float | None = round(max(0.0, 1.0 - script_ratio), 4)
        elif not has_valid_scripts and loads > 0:
            script_ratio = None
            read_only_unpracticed_rate = 0.0
        else:
            script_ratio = None
            read_only_unpracticed_rate = None

        empty_load_rate = round(min(1.0, st["empty_load_sessions"] / loads), 4) if loads > 0 else None
        script_durations = [float(value) for value in st["script_durations_ms"]]
        script_avg_ms = round(sum(script_durations) / len(script_durations), 2) if script_durations else None
        script_p95_ms = _p95(script_durations)

        f_tot = max(1, st["l0_only_sessions"] + st["l1_selective_sessions"] + st["l1_greedy_sessions"])
        l0_rate = round(st["l0_only_sessions"] / f_tot, 4) if loads > 0 else None
        l1_sel_rate = round(st["l1_selective_sessions"] / f_tot, 4) if loads > 0 else None
        l1_greedy_rate = round(st["l1_greedy_sessions"] / f_tot, 4) if loads > 0 else None

        if st["token_session_count"] > 0 and st["total_tokens"] > 0:
            avg_tokens: int | None = max(1, int(st["total_tokens"] / st["token_session_count"]))
        elif loads > 0:
            avg_hit_ref_tokens = (
                int((st["reference_bytes"] / max(1, st["reference_hit_count"])) // 4)
                if st["reference_hit_count"] > 0 and st["reference_bytes"] > 0
                else 0
            )
            script_proxy_tokens = 320 if script_execs > 0 else 0
            avg_tokens = max(1, fm_tok_est + body_tok_est + avg_hit_ref_tokens + script_proxy_tokens + 1200)
        else:
            avg_tokens = None

        cache_b = per_skill_cache.get(s_name, {})
        tot_in_sk = int(cache_b.get("in") or 0)
        tot_cr_sk = int(cache_b.get("cache_read") or 0)
        sk_kv_cache_pct = (
            round(100.0 * tot_cr_sk / (tot_in_sk + tot_cr_sk), 1)
            if (tot_in_sk + tot_cr_sk) > 0
            else s11_context.get("kv_cache_hit_pct")
        )
        cache_bonus = ((float(sk_kv_cache_pct) / 100.0) * 0.25) if sk_kv_cache_pct is not None else 0.0
        script_bonus = min(0.2, 0.05 * min(4, script_execs)) if has_valid_scripts and script_execs > 0 else 0.0

        # S10 formula: Skill ROI = (R1_Pass_Rate + Bonuses) / Normalized(Tokens + Churn + Uncalled_Script_Penalty)
        norm_tokens = min(2.0, max(0.2, avg_tokens / 10000.0)) if avg_tokens is not None else None
        norm_churn = min(2.0, st["edit_churn_count"] * 0.15 + st["retry_commands"] * 0.1)
        uncalled_penalty = (read_only_unpracticed_rate or 0.0) * 0.6 if has_valid_scripts and loads > 0 else 0.0
        escape_penalty = (st["impl_bug_escapes"] * 0.2) + (st["spec_gap_escapes"] * 0.15)
        denom = max(0.25, 0.5 + norm_tokens + norm_churn + uncalled_penalty + escape_penalty) if norm_tokens is not None else None
        roi_score = (
            round((r1_pass_rate + cache_bonus + script_bonus) / denom, 4)
            if loads > 0 and denom is not None and r1_pass_rate is not None
            else (round(r1_pass_rate / denom, 4) if denom is not None and r1_pass_rate is not None else None)
        )

        rework_rounds = int(st.get("rework_rounds") or 0)
        repair_denom = max(1, r1_tot, loads)
        repair_numer = st["impl_bug_escapes"] + st["spec_gap_escapes"] + rework_rounds + (1 if st["edit_churn_count"] >= 4 else 0)
        post_invocation_repair_loop_rate = round(min(1.0, repair_numer / repair_denom), 4) if (loads > 0 or r1_tot > 0 or st["edit_churn_count"] > 0) else None

        diagnoses: list[str] = []
        if has_valid_scripts and loads > 0 and (read_only_unpracticed_rate or 0.0) >= 0.5:
            diagnoses.append(f"光读不练率 {(read_only_unpracticed_rate or 0.0)*100:.0f}%（脚本未充分触发）")
        if st["impl_bug_escapes"] > 0:
            diagnoses.append(f"[IMPL_BUG] 漏网 {st['impl_bug_escapes']} 次（建议 Valve 1 下沉脚本检查）")
        if st["spec_gap_escapes"] > 0:
            diagnoses.append(f"[SPEC_GAP] 盲区 {st['spec_gap_escapes']} 次（建议 add-case --id auto 补齐抽屉）")
        if st["edit_churn_count"] >= 4:
            diagnoses.append(f"挂载后文件反复擦写 {st['edit_churn_count']} 次")
        if l1_greedy_rate is not None and l1_greedy_rate > 0.25:
            diagnoses.append(f"L1 全量暴读违规率 {l1_greedy_rate*100:.0f}%")
        if (post_invocation_repair_loop_rate or 0) > 0.25 and not diagnoses:
            diagnoses.append(f"调用后返工率 {post_invocation_repair_loop_rate*100:.0f}% 超阈值")

        if (post_invocation_repair_loop_rate or 0) > 0.25 or st["impl_bug_escapes"] > 0 or st["spec_gap_escapes"] > 0 or st["edit_churn_count"] >= 4:
            diagnostic_status = "high_repair_loop"
        elif loads == 0 and observed and (l0_lines > 120 or max_l1_lines > 140 or not meta_inv.get("budget_ok", True)):
            diagnostic_status = "zero_trigger_bloat"
            diagnoses.append(f"30d 零触发且 L0/L1 体积偏大 (L0={l0_lines}, max_L1={max_l1_lines})")
        elif loads == 0 and s_name in known_skills:
            diagnostic_status = "dormant"
        elif diagnoses:
            diagnostic_status = "warn"
        else:
            diagnostic_status = "healthy" if loads > 0 else "unknown"

        kinds = populations.get(s_name, {})
        stamps = [stamp for values in kinds.values() for stamp in values]
        row_meta = _freshness(stamps, warehouse_present, 30)
        skill_rows.append({
            **row_meta, "estimated": True,
            "estimation_method": "session/tool and goal-name skill attribution proxies",
            "r1_freshness": _freshness(kinds.get("reviews", []), wh.is_file()),
            "skill": s_name,
            "loads_7d": st["loads_7d"],
            "loads_30d": loads,
            "r1_pass_rate": r1_pass_rate,
            "r1_total": r1_tot,
            "rework_rounds": rework_rounds,
            "post_invocation_repair_loop_rate": post_invocation_repair_loop_rate,
            "has_scripts": has_valid_scripts,
            "script_execs": script_execs,
            "script_failures": st["script_failures"],
            "script_success_rate": round(max(0, script_execs - st["script_failures"]) / script_execs, 4) if script_execs else None,
            "script_touch_sessions": st["script_touch_sessions"],
            "script_touch_rate": script_ratio,
            "unpracticed_rate": read_only_unpracticed_rate,
            "script_avg_ms": script_avg_ms,
            "script_p95_ms": script_p95_ms,
            "reference_hit_count": st["reference_hit_count"],
            "reference_bytes": st["reference_bytes"],
            "empty_load_sessions": st["empty_load_sessions"],
            "empty_load_rate": empty_load_rate,
            "eval_interceptions": st["eval_interceptions"],
            "eval_escapes": st["eval_escapes"],
            "l0_only_rate": l0_rate,
            "l1_selective_rate": l1_sel_rate,
            "l1_greedy_violation_rate": l1_greedy_rate,
            "l0_only_sessions": st["l0_only_sessions"],
            "l1_selective_sessions": st["l1_selective_sessions"],
            "l1_greedy_sessions": st["l1_greedy_sessions"],
            "avg_context_tokens": avg_tokens,
            "edit_churn_count": st["edit_churn_count"],
            "retry_commands": st["retry_commands"],
            "impl_bug_escapes": st["impl_bug_escapes"],
            "spec_gap_escapes": st["spec_gap_escapes"],
            "roi_score": roi_score,
            "diagnosis": "；".join(diagnoses) if diagnoses else ("健康达标" if loads > 0 else "unknown / 观测不足"),
            "diagnostic_status": diagnostic_status,
            "needs_optimization": bool(diagnoses) and (loads > 0 or r1_tot > 0 or st["edit_churn_count"] > 0 or diagnostic_status == "zero_trigger_bloat"),
        })

    active_rows = [r for r in skill_rows if r["loads_30d"] > 0 or r["r1_total"] > 0 or r["edit_churn_count"] > 0]
    actionable_rows = [r for r in skill_rows if r["needs_optimization"]]
    low_roi_candidates = sorted(
        actionable_rows,
        key=lambda r: (r["roi_score"] is None, r["roi_score"] if r["roi_score"] is not None else float("inf"), -(r["post_invocation_repair_loop_rate"] or 0), -r["impl_bug_escapes"], -(r["unpracticed_rate"] or 0.0)),
    )[:3]

    recommendations: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for cand in low_roi_candidates:
        sk = cand["skill"]
        roi_label = f"{cand['roi_score']:.2f}" if cand["roi_score"] is not None else "unknown"
        r1_label = f"{cand['r1_pass_rate'] * 100:.1f}%" if cand["r1_pass_rate"] is not None else "unknown"
        rec_item = {
            "id": f"skill-roi-{sk}",
            "ref": f"retro://skill-roi/{sk}",
            "legacy_ref": f"skill-roi://{sk}",
            "legacy_refs": [f"skill-roi://{sk}"],
            "skill": sk,
            "metric_id": f"skill-roi-{sk}",
            "diagnostic_status": cand["diagnostic_status"],
            "post_invocation_repair_loop_rate": cand["post_invocation_repair_loop_rate"],
            "action": f"Run skill_contract.py add-case --skill {sk} --id auto and sink repeated repair checks into scripts/" if cand["diagnostic_status"] == "high_repair_loop" else f"Audit {sk} L0/L1 budget and wire deterministic script triggers",
            "scope": "personal",
            "title": f"[Skill ROI] 优化 {sk}（ROI={cand['roi_score']:.2f}，R1通过={cand['r1_pass_rate']*100:.0f}%）：{cand['diagnosis'][:36]}" if cand["roi_score"] is not None and cand["r1_pass_rate"] is not None else f"[Skill ROI] 补齐 {sk} 的观测覆盖：{cand['diagnosis'][:36]}",
            "detail": (
                f"skill={sk} | roi_score={roi_label} | "
                f"r1_pass_rate={r1_label} | "
                f"unpracticed_rate={_percent_label(cand['unpracticed_rate'])} | "
                f"edit_churn={cand['edit_churn_count']} | "
                f"impl_bug={cand['impl_bug_escapes']} | spec_gap={cand['spec_gap_escapes']} | "
                f"diagnosis={cand['diagnosis']}"
            ),
            "priority": "P1",
            **{key: cand[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                         "sample_size", "coverage_status", "estimated", "estimation_method")},
        }
        recommendations.append(rec_item)
        proposals.append({**rec_item, "ref": rec_item["legacy_ref"], "priority": "high"})

    for bp in burn_proposals[:2]:
        bp_enriched = dict(bp)
        bp_enriched.setdefault("scope", "personal")
        bp_enriched.setdefault("action", "Add --head-limit / view_range or sink filtering into deterministic script")
        proposals.append(bp_enriched)
        if not any(r["id"] == bp_enriched["id"] for r in recommendations):
            recommendations.append(bp_enriched)

    total_sample_size = max(len(root_stamps), int(s11_context["sample_size"] or 0))
    cov_status = "healthy" if total_sample_size > 0 else ("unknown" if warehouse_present else "missing")
    observed_as_of = [value for value in (root_meta["source_as_of"], s11_context["source_as_of"]) if value]
    source_as_of = max(observed_as_of) if observed_as_of else None
    inventory_paths = [path for meta in inventory.values() for path in
                       [Path(meta["path"]) / "SKILL.md", *(Path(meta["path"]) / "references").rglob("*.md"),
                        *(Path(meta["path"]) / "references").rglob("*.json")]]
    s3_health.update(_snapshot_freshness(inventory_paths, len(inventory)))
    for key in ("top3_l0_watermarks", "top3_l1_watermarks"):
        for row in s3_health[key]:
            row.update(_snapshot_freshness([Path(inventory[row["skill"]]["path"]) / "SKILL.md"], 1))

    for p_item in proposals:
        p_item.setdefault("window_cst", "7d")
        p_item.setdefault("source_as_of", source_as_of)
        p_item.setdefault("sample_size", total_sample_size)
        p_item.setdefault("coverage_status", cov_status)
        p_item.setdefault("window_start", source_meta["window_start"])
        p_item.setdefault("window_end", source_meta["window_end"])

    skill_details = _collect_skill_details(
        inventory=inventory,
        skill_rows=skill_rows,
        per_skill_channels=per_skill_channels,
        l2_channels=l2_channels,
        per_skill_cache=per_skill_cache,
        s11_context=s11_context,
        per_skill_refs=per_skill_refs,
        per_skill_scripts=per_skill_scripts,
        per_skill_eval_cases=per_skill_eval_cases,
        warehouse_present=warehouse_present,
    )

    try:
        mirror_skills_to_icloud(skill_details, icloud_sync_dir=icloud_sync_dir)
    except Exception:
        pass
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    auto_proposed = _queue_recommendations(proposals, prop_path) if auto_propose else []
    proposals_written = len(auto_proposed)
    proposals_skipped = len(proposals) - proposals_written if auto_propose else 0
    report_payload = {
        "ok": True,
        "generated_at": generated_at,
        "query_id": "retro/skill_roi_diagnostics",
        "wall_clock_date": wall_clock_date,
        "staleness_days": staleness_days,
        "window_anchor": window_anchor,
        "window_cst": "7d",
        "window_start": root_meta["window_start"] or s11_context["window_start"],
        "window_end": root_meta["window_end"] or s11_context["window_end"],
        "source_as_of": source_as_of,
        "sample_size": total_sample_size,
        "coverage_status": cov_status,
        "skill_details": skill_details,
        "S1_pin_drift": s1_pins,
        "S2_gate_catch": s2_gate,
        "S3_skill_health": s3_health,
        "S4_usage_and_r1": {
            **source_meta,
            "active_skills_count": len(active_rows),
            "idle_skills_30d": idle_skills_30d,
            "empty_load_sessions": sum(int(r.get("empty_load_sessions") or 0) for r in active_rows),
            "empty_load_rate": round(
                sum(int(r.get("empty_load_sessions") or 0) for r in active_rows)
                / max(1, sum(int(r.get("loads_30d") or 0) for r in active_rows)), 4
            ) if active_rows else None,
            "top_used_skills": sorted(skill_rows, key=lambda r: (-r["loads_30d"], r["skill"]))[:8],
        },
        "S6_load_vs_script": [
            {
                **{key: r[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                          "sample_size", "coverage_status", "estimated", "estimation_method")},
                **_freshness([stamp for kind in ('usage',) for stamp in populations.get(r["skill"], {}).get(kind, [])],
                             warehouse_present, 30),
                "skill": r["skill"],
                "loads_30d": r["loads_30d"],
                "script_execs": r["script_execs"],
                "script_touch_sessions": r["script_touch_sessions"],
                "script_touch_rate": r["script_touch_rate"],
                "unpracticed_rate": r["unpracticed_rate"],
                "script_success_rate": r["script_success_rate"],
                "script_avg_ms": r["script_avg_ms"],
                "script_p95_ms": r["script_p95_ms"],
            }
            for r in active_rows
            if r["has_scripts"]
        ],
        "S7_disclosure_funnel": [
            {
                **{key: r[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                          "sample_size", "coverage_status", "estimated", "estimation_method")},
                **_freshness([stamp for kind in ('usage',) for stamp in populations.get(r["skill"], {}).get(kind, [])],
                             warehouse_present, 30),
                "skill": r["skill"],
                "l0_only_rate": r["l0_only_rate"],
                "l1_selective_rate": r["l1_selective_rate"],
                "l1_greedy_violation_rate": r["l1_greedy_violation_rate"],
                "l0_only_sessions": r["l0_only_sessions"],
                "l1_selective_sessions": r["l1_selective_sessions"],
                "l1_greedy_sessions": r["l1_greedy_sessions"],
                "reference_hit_count": r["reference_hit_count"],
                "reference_bytes": r["reference_bytes"],
                "avg_context_tokens": r["avg_context_tokens"],
            }
            for r in active_rows
        ],
        "S8_post_skill_churn": [
            {
                **{key: r[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                          "sample_size", "coverage_status", "estimated", "estimation_method")},
                **_freshness([stamp for kind in ('churn',) for stamp in populations.get(r["skill"], {}).get(kind, [])],
                             warehouse_present, 30),
                "skill": r["skill"],
                "edit_churn_count": r["edit_churn_count"],
                "retry_commands": r["retry_commands"],
            }
            for r in active_rows
            if r["edit_churn_count"] > 0 or r["retry_commands"] > 0
        ],
        "S9_escape_attribution": [
            {
                **{key: r[key] for key in ("window_cst", "window_start", "window_end", "source_as_of",
                                          "sample_size", "coverage_status", "estimated", "estimation_method")},
                **_freshness([stamp for kind in ('reviews', 'events') for stamp in populations.get(r["skill"], {}).get(kind, [])],
                             warehouse_present, 7),
                "skill": r["skill"],
                "impl_bug_escapes": r["impl_bug_escapes"],
                "spec_gap_escapes": r["spec_gap_escapes"],
                "eval_interceptions": r["eval_interceptions"],
                "eval_escapes": r["eval_escapes"],
            }
            for r in active_rows
            if r["impl_bug_escapes"] > 0 or r["spec_gap_escapes"] > 0 or r["eval_interceptions"] > 0 or r["eval_escapes"] > 0
        ],
        "S10_skill_roi_ranking": sorted(active_rows, key=lambda r: (r["roi_score"] is None, r["roi_score"] if r["roi_score"] is not None else float("inf"), r["skill"])),
        "S11_context_composition": s11_context,
        "S12_tool_context_burn": s12_tools,
        "recommendations": recommendations,
        "low_roi_proposals": proposals,
        "auto_proposed": auto_proposed,
        "proposals_written": proposals_written,
        "proposals_skipped": proposals_skipped,
    }
    if should_mirror_roi:
        _mirror_skills_roi_snapshot_to_icloud(report_payload, icloud_roi_path=effective_roi_path)
    return report_payload


def compute_skill_roi(
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    magpie_usage: Path | None = None,
    dsh_sessions: Path | None = None,
    telemetry_shards_dir: Path | None = None,
    *,
    auto_propose: bool = False,
    proposals_path: Path | None = None,
    icloud_sync_dir: Path | None = None,
    mirror_icloud_roi: bool | None = None,
    icloud_roi_path: Path | None = None,
) -> dict[str, Any]:
    """Alias for compute_skill_roi_diagnostics()."""
    report = compute_skill_roi_diagnostics(
        warehouse_db=warehouse_db,
        copilot_db=copilot_db,
        skills_root=skills_root,
        dev_root=dev_root,
        magpie_usage=magpie_usage,
        dsh_sessions=dsh_sessions,
        telemetry_shards_dir=telemetry_shards_dir,
        auto_propose=auto_propose,
        proposals_path=proposals_path,
        icloud_sync_dir=icloud_sync_dir,
        mirror_icloud_roi=mirror_icloud_roi,
        icloud_roi_path=icloud_roi_path,
    )
    report.setdefault(
        "generated_at",
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    return report


CST = timezone(timedelta(hours=8))


def _window_bounds(days: int = 7) -> tuple[datetime, datetime]:
    today = datetime.now(CST).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days - 1), today + timedelta(days=1)


def _observed_at(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        text = str(value)
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=CST if len(text) == 10 else timezone.utc)
        return stamp.astimezone(CST)
    except (ValueError, TypeError):
        return None


def _timed_rows(conn: sqlite3.Connection, table: str, days: int = 7) -> list[dict[str, Any]]:
    columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
    time_column = next((name for name in ("observed_at", "completed_at", "ended_at", "started_at",
                        "timestamp", "first_seen_at", "ts", "created_at", "cst_day") if name in columns), None)
    if time_column is None:
        return []
    start, end = _window_bounds(days)
    rows = []
    if time_column == "cst_day":
        predicate = f'"{time_column}" >= ? AND "{time_column}" < ?'
        bounds = (start.date().isoformat(), end.date().isoformat())
    else:
        # Bare dates are CST days; offset/naive timestamps are parsed as UTC by SQLite.
        observed_jd = (f'CASE WHEN length("{time_column}") = 10 '
                       f'THEN julianday("{time_column}", \'-8 hours\') ELSE julianday("{time_column}") END')
        predicate = f'{observed_jd} >= julianday(?) AND {observed_jd} < julianday(?)'
        bounds = (start.isoformat(), end.isoformat())
    for values in conn.execute(f'SELECT * FROM "{table}" WHERE {predicate}', bounds):
        record = dict(zip(columns, values))
        stamp = _observed_at(record[time_column])
        if stamp and start <= stamp < end:
            record["_observed_at"] = stamp
            rows.append(record)
    return rows


def _freshness(stamps: list[datetime], present: bool = True, days: int = 7) -> dict[str, Any]:
    start, end = _window_bounds(days)
    return {"window_cst": f"{days}d", "window_start": start.isoformat() if stamps else None,
            "window_end": end.isoformat() if stamps else None,
            "source_as_of": max(stamps).isoformat() if stamps else None,
            "sample_size": len(stamps),
            "coverage_status": "healthy" if stamps else ("unknown" if present else "missing")}


def _snapshot_freshness(paths: list[Path], sample_size: int) -> dict[str, Any]:
    stamps = [datetime.fromtimestamp(path.stat().st_mtime, CST) for path in paths if path.is_file()]
    return {"window_cst": "snapshot", "window_start": min(stamps).isoformat() if stamps else None,
            "window_end": max(stamps).isoformat() if stamps else None,
            "source_as_of": max(stamps).isoformat() if stamps else None,
            "sample_size": sample_size, "coverage_status": "healthy" if sample_size and stamps else "unknown",
            "freshness_basis": "source_file_mtime"}


def _event_fingerprint(rec: dict[str, Any]) -> str:
    explicit = str(rec.get("event_id") or "").strip()
    if explicit:
        return explicit
    host = str(rec.get("host") or "local").strip().lower()
    kind = str(rec.get("kind") or rec.get("event_type") or "usage").strip()
    sid = str(rec.get("session_id") or rec.get("route_id") or "").strip()
    turn_id = str(rec.get("turn_id") or rec.get("call_uid") or rec.get("hit_uid") or "").strip()
    ts = str(rec.get("ts") or rec.get("t") or rec.get("timestamp") or rec.get("created_at") or "").strip()
    skill = str(rec.get("skill") or "").strip()
    tool_or_path = str(
        rec.get("script_path")
        or rec.get("reference_path")
        or rec.get("tool")
        or rec.get("req")
        or rec.get("model")
        or ""
    ).strip()
    tokens_sig = f"{rec.get('in', rec.get('input_tokens', 0))}:{rec.get('out', rec.get('output_tokens', 0))}:{rec.get('cache_read', 0)}"
    raw_key = f"{host}|{kind}|{sid}|{turn_id}|{ts}|{skill}|{tool_or_path}|{tokens_sig}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]


def _read_telemetry_shards(shards_dir: Path | None, days: int = 30) -> list[dict[str, Any]]:
    if not _probe_readable_dir(shards_dir) or shards_dir is None:
        return []
    start, end = _window_bounds(days)
    seen_ids: set[str] = set()
    merged: list[dict[str, Any]] = []
    for shard_file in _safe_glob(shards_dir, "*-skill-usage.jsonl"):
        if not _is_safe_local_file(shard_file):
            continue
        default_host = shard_file.name[: -len("-skill-usage.jsonl")]
        raw_shard = _safe_read_text(shard_file)
        if not raw_shard:
            continue
        for line in raw_shard.splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            rec.setdefault("host", default_host)
            stamp = next(
                (_observed_at(rec[key]) for key in ("ts", "t", "timestamp", "created_at", "observed_at") if rec.get(key)),
                None,
            )
            if not stamp or not (start <= stamp < end):
                continue
            fp = _event_fingerprint(rec)
            if fp in seen_ids:
                continue
            seen_ids.add(fp)
            enriched = dict(rec)
            enriched["event_id"] = fp
            enriched["_observed_at"] = stamp
            merged.append(enriched)
    return merged


def _mirror_skills_roi_snapshot_to_icloud(
    report: dict[str, Any],
    *,
    icloud_roi_path: Path | None = None,
    local_snapshot_path: Path | None = None,
) -> Path | None:
    target = icloud_roi_path or DEFAULT_ICLOUD_SKILLS_ROI_PATH
    sync_root = target.parents[2] if len(target.parents) >= 3 else target.parent
    if icloud_roi_path is None and not sync_root.is_dir():
        return None
    if _is_cloud_or_external_path(target) and icloud_roi_path is None and not _is_safe_local_file(target):
        return None
    local_snap = local_snapshot_path or DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH
    daily: list[dict[str, Any]] = []
    for candidate in (local_snap, target):
        if _is_safe_local_file(candidate):
            try:
                raw_cand = _safe_read_text(candidate)
                existing = json.loads(raw_cand) if raw_cand else {}
                if isinstance(existing, dict) and isinstance(existing.get("skills_daily"), list):
                    daily = existing["skills_daily"]
                    break
            except (OSError, json.JSONDecodeError):
                continue
    snapshot_payload: dict[str, Any] = {
        "schema_version": 1,
        "window_cst": report.get("window_cst") or "7d",
        "window_start": report.get("window_start"),
        "window_end": report.get("window_end"),
        "source_as_of": report.get("source_as_of"),
        "sample_size": int(report.get("sample_size") or 0),
        "coverage_status": report.get("coverage_status") or "unknown",
        "diagnostics": report,
        "skills_daily": daily,
    }
    if isinstance(report.get("skill_details"), list):
        snapshot_payload["skill_details"] = report["skill_details"]
    if isinstance(report.get("skill_retro"), dict):
        snapshot_payload["skill_retro"] = report["skill_retro"]
    try:
        ok, _ = _run_with_io_timeout(lambda: _write_json(target, snapshot_payload))
        return target if ok else None
    except OSError:
        return None


def _merge_skill_retro_into_roi_snapshot(
    skill_retro_report: dict[str, Any],
    *,
    icloud_roi_path: Path | None = None,
    local_snapshot_path: Path | None = None,
) -> None:
    targets = [p for p in (icloud_roi_path, local_snapshot_path) if p is not None]
    for target in targets:
        if _is_cloud_or_external_path(target):
            if icloud_roi_path is None and not _is_safe_local_file(target):
                continue
        elif not _safe_is_file(target) and icloud_roi_path is None:
            continue
        existing: dict[str, Any] = {}
        if _is_safe_local_file(target):
            with contextlib.suppress(OSError, json.JSONDecodeError):
                raw_target = _safe_read_text(target)
                loaded = json.loads(raw_target) if raw_target else {}
                if isinstance(loaded, dict):
                    existing = loaded
        existing.setdefault("schema_version", 1)
        existing.setdefault("window_cst", skill_retro_report.get("window_cst") or "30d")
        existing.setdefault("source_as_of", skill_retro_report.get("source_as_of"))
        existing.setdefault("sample_size", skill_retro_report.get("sample_size", 0))
        existing.setdefault("coverage_status", skill_retro_report.get("coverage_status") or "unknown")
        existing["skill_retro"] = skill_retro_report
        with contextlib.suppress(OSError):
            _run_with_io_timeout(lambda t=target, payload=existing: _write_json(t, payload))




def compute_skill_retro(
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    copilot_sessions_dir: Path | None = None,
    usage_jsonl: Path | None = None,
    trace_export_path: Path | None = None,
    handoff_root: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    telemetry_shards_dir: Path | None = None,
    *,
    skill: str | None = None,
    emit_briefs_dir: Path | None = None,
    mode: str = "full",
    aspects: Sequence[str] | str | None = None,
    coupled_with: Sequence[str] | str | None = None,
    relays: Sequence[Mapping[str, Any]] | None = None,
    days: int = 30,
    auto_propose: bool = False,
    proposals_path: Path | None = None,
    icloud_sync_dir: Path | None = None,
    icloud_roi_path: Path | None = None,
    local_roi_path: Path | None = None,
    mirror_roi_snapshot: bool | None = None,
    isolated_fixtures: bool = False,
) -> dict[str, Any]:
    """Compute the 4-Layer / 6-Dimension Progressive Skill Evolution & Wide-Probe Multi-Angle Diagnostic."""
    explicit_source_override = bool(isolated_fixtures) or any(
        val is not None
        for val in (warehouse_db, copilot_db, copilot_sessions_dir, usage_jsonl, trace_export_path)
    )
    wh = warehouse_db if warehouse_db is not None else (None if explicit_source_override else DEFAULT_WAREHOUSE_DB)
    cp = copilot_db if copilot_db is not None else (None if explicit_source_override else DEFAULT_COPILOT_DB)
    sess_dir = (
        copilot_sessions_dir
        if copilot_sessions_dir is not None
        else (None if explicit_source_override else DEFAULT_COPILOT_SESSIONS_DIR)
    )
    u_jsonl = (
        usage_jsonl
        if usage_jsonl is not None
        else (None if explicit_source_override else DEFAULT_SKILL_TELEMETRY_USAGE_JSONL)
    )
    h_root = handoff_root if handoff_root is not None else (None if explicit_source_override else DEFAULT_HANDOFF_ROOT)
    s_root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    prop_path = proposals_path if proposals_path is not None else DEFAULT_PROPOSALS_PATH

    resolved_sync = _resolve_icloud_sync_dir(icloud_sync_dir) if icloud_sync_dir is not None else None
    shards_dir = (
        telemetry_shards_dir
        if telemetry_shards_dir is not None
        else (
            resolved_sync / "telemetry-shards"
            if resolved_sync is not None
            else None
        )
    )
    inventory = _discover_skill_inventory(s_root, d_root)
    report = compute_skill_retro_diagnostics(
        inventory=inventory,
        skill_filter=skill,
        emit_briefs_dir=emit_briefs_dir,
        mode=mode,
        aspects=aspects,
        coupled_with=coupled_with,
        relays=relays,
        warehouse_db=wh,
        copilot_db=cp,
        copilot_sessions_dir=sess_dir,
        usage_jsonl=u_jsonl,
        telemetry_shards_dir=shards_dir,
        trace_export_path=trace_export_path,
        handoff_root=h_root,
        skills_root=s_root,
        dev_root=d_root,
        days=days,
        shard_reader=_read_telemetry_shards,
        queue_fn=_queue_recommendations,
        auto_propose=auto_propose,
        proposals_path=prop_path,
    )
    should_mirror = (
        mirror_roi_snapshot
        if mirror_roi_snapshot is not None
        else (icloud_roi_path is not None or local_roi_path is not None or not explicit_source_override)
    )
    if should_mirror:
        eff_icloud_roi = icloud_roi_path or (
            resolved_sync / "jobs" / "aidata-digest" / "skills-roi.json" if resolved_sync is not None else None
        )
        eff_local_roi = local_roi_path or (DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH if not explicit_source_override else None)
        _merge_skill_retro_into_roi_snapshot(
            report,
            icloud_roi_path=eff_icloud_roi,
            local_snapshot_path=eff_local_roi,
        )
    return report

def _usage_records(path: Path, *, telemetry_shards_dir: Path | None = None) -> list[dict[str, Any]]:
    start, end = _window_bounds()
    records: list[dict[str, Any]] = []
    seen_route_sigs: set[tuple[str, str, int, int]] = set()
    seen_fps: set[str] = set()
    if _is_safe_local_file(path):
        raw_usage = _safe_read_text(path)
        for number, line in enumerate(raw_usage.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid usage JSON at {path}:{number}") from exc
            if not isinstance(rec, dict):
                raise ValueError(f"invalid usage record at {path}:{number}")
            stamp = next((_observed_at(rec[key]) for key in ("t", "ts", "timestamp", "created_at", "observed_at")
                          if rec.get(key)), None)
            if stamp and start <= stamp < end:
                rec["_observed_at"] = stamp
                fp = _event_fingerprint(rec)
                seen_fps.add(fp)
                route_sig = (
                    str(rec.get("route_id") or ""),
                    str(rec.get("req") or rec.get("model") or ""),
                    int(rec.get("in") or 0),
                    int(rec.get("out") or 0),
                )
                if route_sig[0]:
                    seen_route_sigs.add(route_sig)
                records.append(rec)
    for s_rec in _read_telemetry_shards(telemetry_shards_dir, days=7):
        kind = str(s_rec.get("kind") or "")
        if kind and kind not in {"gateway_usage", "usage"}:
            continue
        fp = str(s_rec.get("event_id") or _event_fingerprint(s_rec))
        if fp in seen_fps:
            continue
        route_sig = (
            str(s_rec.get("route_id") or ""),
            str(s_rec.get("req") or s_rec.get("model") or ""),
            int(s_rec.get("in") or 0),
            int(s_rec.get("out") or 0),
        )
        if route_sig[0] and route_sig in seen_route_sigs:
            continue
        seen_fps.add(fp)
        if route_sig[0]:
            seen_route_sigs.add(route_sig)
        records.append(s_rec)
    return records

@contextlib.contextmanager
def _proposal_lock(path: Path):
    sidecar = path.with_name(path.name + ".lock")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with sidecar.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _proposal_history(path: Path) -> dict[str, dict[str, Any]]:
    records = {}
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid proposal history JSON at {path}:{number}") from exc
            if not isinstance(rec, dict) or any(not str(rec.get(k) or "").strip() for k in ("id", "ts", "agent", "title")):
                raise ValueError(f"incomplete proposal history record at {path}:{number}")
            if _observed_at(rec["ts"]) is None:
                raise ValueError(f"invalid proposal timestamp at {path}:{number}")
            records[str(rec["id"])] = rec
    return records


def _queue_recommendations(candidates: list[dict[str, Any]], path: Path) -> list[dict[str, Any]]:
    queued = []
    for candidate in candidates:
        result = route_retro_candidate(candidate, proposals_path=path, deduplicate_pending=True)
        if result["decision"] != "already_pending":
            queued.append(result)
    return queued


def route_retro_candidate(
    candidate: dict[str, Any],
    mode: str = "observe",
    starred: bool = False,
    proposals_path: Path | None = None,
    linear_runner: Callable[..., Any] | None = None,
    deduplicate_pending: bool = False,
) -> dict[str, Any]:
    cid = str(candidate.get("id") or "").strip()
    if not cid:
        raise ValueError("candidate id is required")
    title = str(candidate.get("title") or cid).strip()
    detail = str(candidate.get("detail") or title).strip()
    ref = str(candidate.get("ref") or f"retro://{cid}").strip()
    scope = str(candidate.get("scope") or "personal").strip() or "personal"
    norm_prio = _normalize_priority(candidate.get("priority"), default="high")
    proposal = {
        "id": cid,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "agent": str(candidate.get("agent") or "retro-loop").strip() or "retro-loop",
        "title": title,
        "detail": detail,
        "priority": norm_prio,
        "status": "pending",
        "ref": ref,
        "scope": scope,
    }
    for extra_key in (
        "layer",
        "category",
        "skill",
        "skill_version",
        "plain_reason",
        "scope_triage",
        "metric_id",
        "action",
        "legacy_ids",
        "legacy_refs",
        "legacy_ref",
        "diagnostic_status",
        "post_invocation_repair_loop_rate",
        "app_name",
        "event_kind",
        "signature",
        "stack_top_symbol",
        "recommended_layer",
        "verify_cmd",
        "window_cst",
        "window_start",
        "window_end",
        "estimated",
        "estimation_method",
        "source_as_of",
        "sample_size",
        "coverage_status",
        "scope",
    ):
        if extra_key in candidate:
            proposal[extra_key] = candidate[extra_key]
    proposal.setdefault("scope", "personal")
    if any(not str(proposal[key] or "").strip() for key in ("id", "ts", "agent", "title")):
        raise ValueError("proposal requires nonempty id/ts/agent/title")
    if mode == "observe" and not starred:
        if proposals_path is None:
            raise ValueError("proposals_path is required for observe mode")
        with _proposal_lock(proposals_path):
            history = _proposal_history(proposals_path)
            ids = {proposal["id"], *candidate.get("legacy_ids", [])}
            refs = {proposal["ref"], *candidate.get("legacy_refs", []), candidate.get("legacy_ref", "")}
            refs.discard("")
            prior = next((row for row in history.values() if (row["id"] in ids or row.get("ref") in refs or ids.intersection(row.get("legacy_ids", [])) or refs.intersection(row.get("legacy_refs", []))) and row.get("status") in {"pending", "approved", "dismissed", "transferred"}), None)
            if deduplicate_pending and prior:
                return {"decision": "already_pending", "promoted": False, "proposal": prior}
            with proposals_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(proposal, ensure_ascii=False) + "\n")
        return {"decision": "observe (queued_to_aidash)", "promoted": False, "proposal": proposal}
    if linear_runner is None:
        raise ValueError("linear_runner is required for promotion")
    linear_runner(proposal)
    proposal["status"] = "approved"
    return {"decision": "promoted_to_linear", "promoted": True, "proposal": proposal}


route_retro_Candidate = route_retro_candidate


def _load_jev_client() -> Any | None:
    path = Path(__file__).resolve().parents[2] / "team-lead" / "scripts" / "jev_client.py"
    if not path.is_file():
        return None
    with contextlib.suppress(Exception):
        spec = importlib.util.spec_from_file_location("skill_retro_jev_client", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return None


def _load_repo_gate_auditor(skills_root: Path | None = None, dev_root: Path | None = None) -> Any | None:
    candidates: list[Path] = []
    if skills_root is not None:
        candidates.append(skills_root / "repo-kit" / "scripts" / "repo_gate_auditor.py")
    candidates.append(Path(__file__).resolve().parents[2] / "repo-kit" / "scripts" / "repo_gate_auditor.py")
    if dev_root is not None:
        candidates.append(dev_root / "Skills" / "skills" / "repo" / "repo-kit" / "scripts" / "repo_gate_auditor.py")
    for cand in candidates:
        if _safe_is_file(cand):
            with contextlib.suppress(Exception):
                spec = importlib.util.spec_from_file_location("skill_retro_repo_gate_auditor", cand)
                if spec is None or spec.loader is None:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
    return None


def _compute_cross_repo_gate_health(
    *,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
) -> dict[str, Any]:
    mod = _load_repo_gate_auditor(skills_root=skills_root, dev_root=dev_root)
    eff_dev_root = dev_root or DEFAULT_DEV_ROOT
    if mod is None or not hasattr(mod, "scan_repositories"):
        return {
            "available": False,
            "repos_scanned": 0,
            "healthy_repos_count": 0,
            "drifted_repos_count": 0,
            "average_gate_health_score": 100.0,
            "total_findings": 0,
            "rule_counts": {},
            "speedup_4_angles": {},
            "repos": [],
            "actionable_proposals": [],
        }
    with contextlib.suppress(Exception):
        scan_out = mod.scan_repositories(dev_root=eff_dev_root, flow_map=True)
        if isinstance(scan_out, Mapping):
            return {"available": True, **dict(scan_out)}
    return {
        "available": False,
        "repos_scanned": 0,
        "healthy_repos_count": 0,
        "drifted_repos_count": 0,
        "average_gate_health_score": 100.0,
        "total_findings": 0,
        "rule_counts": {},
        "speedup_4_angles": {},
        "repos": [],
        "actionable_proposals": [],
    }


# ----------------------------------------------------------------------
# Decoupled Context Uptake Registry (`uptake_map.json`) & Triad Engine
# (`Trigger Quality -> Context Uptake -> Outcome Delta`)
# ----------------------------------------------------------------------
UPTAKE_STATUS_PASSED = "passed"
UPTAKE_STATUS_FAILED = "failed"
UPTAKE_STATUS_NOT_APPLICABLE = "not_applicable"
UPTAKE_STATUS_UNAVAILABLE = "unavailable"
UPTAKE_SCORED_STATUSES = {UPTAKE_STATUS_PASSED, UPTAKE_STATUS_FAILED}
VALID_SKILL_SCENARIOS = {
    "skills_unavailable",
    "skills_available_unmentioned",
    "skills_available_mentioned",
}
_UPTAKE_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".build", "build", "dist", ".expo"}


def _strip_source_comments(text: str, suffix: str = "") -> str:
    """Strip C/JS/Swift block & line comments or Python/shell `#` comments before lexical regex scans."""
    ext = suffix.lower()
    if ext in {".md", ".json", ".html", ".htm"}:
        return text
    cleaned = re.sub(r"/\*[\s\S]*?\*/", "", text)
    if ext in {".py", ".sh", ".yaml", ".yml", ".toml"}:
        cleaned = re.sub(r"(?m)^\s*#.*$", "", cleaned)
    else:
        cleaned = re.sub(r"(?m)//.*$", "", cleaned)
    return cleaned


def load_uptake_registry(
    uptake_map_path: Path | None = None,
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
    """Load decoupled `skill_map` (`skill_id -> [check_id, ...]`) and atomic `checks` from `uptake_map.json`."""
    path = uptake_map_path or DEFAULT_UPTAKE_MAP_PATH
    if not _safe_is_file(path):
        return {}, {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    if not isinstance(raw, Mapping):
        return {}, {}

    raw_skill_map = raw.get("skill_map") if isinstance(raw.get("skill_map"), Mapping) else raw
    skill_map: dict[str, list[str]] = {}
    if isinstance(raw_skill_map, Mapping):
        for k, v in raw_skill_map.items():
            if str(k).startswith("_") or str(k) in {"schema", "version", "updated", "checks", "skill_map"}:
                continue
            if isinstance(v, list):
                skill_map[_norm_skill(str(k))] = [str(item) for item in v if str(item).strip()]

    checks_by_id: dict[str, dict[str, Any]] = {}
    raw_checks = raw.get("checks")
    if isinstance(raw_checks, list):
        for entry in raw_checks:
            if isinstance(entry, Mapping) and entry.get("id"):
                cid = str(entry["id"]).strip()
                checks_by_id[cid] = dict(entry)
    return skill_map, checks_by_id


def _discover_target_files(root: Path, globs: Sequence[str] | None) -> list[tuple[str, Path]]:
    """Return sorted `(rel_posix_path, abs_path)` matching any glob in `globs` under `root`."""
    if not _probe_readable_dir(root):
        return []
    patterns = [str(g).strip() for g in (globs or ["**/*"]) if str(g).strip()]
    if not patterns:
        patterns = ["**/*"]
    matched: dict[str, Path] = {}
    for pattern in patterns:
        direct_candidate = root / pattern
        if "*" not in pattern and "?" not in pattern and "[" not in pattern:
            if _safe_is_file(direct_candidate) or _safe_is_dir(direct_candidate):
                rel = direct_candidate.relative_to(root).as_posix()
                matched[rel] = direct_candidate
            continue
        for cand in _safe_glob(root, pattern, recursive=("**" in pattern)):
            try:
                rel = cand.relative_to(root).as_posix()
            except ValueError:
                continue
            if any(part in _UPTAKE_SKIP_DIRS for part in Path(rel).parts):
                continue
            matched[rel] = cand
    if not matched and not all("*" in p or "?" in p or "[" in p for p in patterns):
        for cand in _safe_glob(root, "*", recursive=True):
            try:
                rel = cand.relative_to(root).as_posix()
            except ValueError:
                continue
            if any(part in _UPTAKE_SKIP_DIRS for part in Path(rel).parts):
                continue
            if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(Path(rel).name, pat) for pat in patterns):
                matched[rel] = cand
    return sorted(matched.items(), key=lambda item: item[0])


def _run_single_uptake_check(check: Mapping[str, Any], target_dir: Path) -> dict[str, Any]:
    """Run one atomic check against `target_dir` and return a 4-state CheckResult dict."""
    cid = str(check.get("id") or "")
    category = str(check.get("category") or "lexical")
    kind = str(check.get("kind") or "text_any")
    severity = str(check.get("severity") or "hard")
    remediation_drawer = str(check.get("remediation_drawer") or "")
    description = str(check.get("description") or "")
    file_globs_raw = check.get("file_glob")
    file_globs = (
        [str(x) for x in file_globs_raw]
        if isinstance(file_globs_raw, list)
        else ([str(file_globs_raw)] if file_globs_raw else ["**/*"])
    )

    def _make_res(status: str, passed: bool | None, evidence: str) -> dict[str, Any]:
        return {
            "id": cid,
            "category": category,
            "kind": kind,
            "severity": severity,
            "fatal": severity == "hard",
            "status": status,
            "passed": passed,
            "evidence": evidence,
            "remediation_drawer": remediation_drawer,
            "description": description,
        }

    if not _probe_readable_dir(target_dir):
        return _make_res(UPTAKE_STATUS_UNAVAILABLE, None, f"target_dir unavailable: {target_dir}")

    if kind == "path_exists":
        targets = check.get("target") or file_globs
        pat_list = [str(x) for x in (targets if isinstance(targets, list) else [targets])]
        hits = _discover_target_files(target_dir, pat_list)
        if hits:
            return _make_res(UPTAKE_STATUS_PASSED, True, f"found {hits[0][0]}")
        return _make_res(UPTAKE_STATUS_FAILED, False, f"no path matched any of {pat_list}")

    if kind == "path_absent":
        targets = check.get("target") or file_globs
        pat_list = [str(x) for x in (targets if isinstance(targets, list) else [targets])]
        hits = _discover_target_files(target_dir, pat_list)
        if hits:
            return _make_res(UPTAKE_STATUS_FAILED, False, f"forbidden path exists: {hits[0][0]}")
        eng_globs = check.get("engagement_glob")
        if isinstance(eng_globs, list) and eng_globs:
            if not _discover_target_files(target_dir, [str(x) for x in eng_globs]):
                return _make_res(UPTAKE_STATUS_NOT_APPLICABLE, None, "engagement_glob precondition did not match any files")
        return _make_res(UPTAKE_STATUS_PASSED, True, f"no path matched forbidden {pat_list}")

    candidate_files = [(rel, p) for rel, p in _discover_target_files(target_dir, file_globs) if _safe_is_file(p)]

    if kind == "python_ast":
        if not candidate_files:
            return _make_res(UPTAKE_STATUS_NOT_APPLICABLE, None, f"no Python files matched {file_globs}")
        ast_rule = str(check.get("ast_rule") or "has_main_or_argparse")
        parse_errors: list[str] = []
        matched_ast_files: list[str] = []
        py_candidates = [(rel, fpath) for rel, fpath in candidate_files if fpath.suffix.lower() == ".py"]
        non_test_py = [
            (rel, fpath)
            for rel, fpath in py_candidates
            if not fpath.name.startswith("test_") and not fpath.name.endswith("_test.py")
        ]
        ordered_py = (non_test_py if non_test_py else py_candidates)[:6]
        for rel, fpath in ordered_py:
            try:
                src = fpath.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                parse_errors.append(f"{rel}: {exc}")
                continue
            try:
                tree = ast.parse(src, filename=rel)
            except SyntaxError as exc:
                return _make_res(UPTAKE_STATUS_FAILED, False, f"{rel}: SyntaxError line {exc.lineno}: {exc.msg}")
            if ast_rule == "has_main_or_argparse":
                has_main = "_CANONICAL_PATH" in src or any(
                    (isinstance(node, ast.FunctionDef) and node.name in {"main", "parser", "cli"})
                    or (isinstance(node, ast.Import) and any(alias.name == "argparse" for alias in node.names))
                    or (isinstance(node, ast.ImportFrom) and node.module == "argparse")
                    for node in ast.walk(tree)
                )
                if has_main:
                    matched_ast_files.append(rel)
            else:
                matched_ast_files.append(rel)
        if parse_errors:
            return _make_res(UPTAKE_STATUS_UNAVAILABLE, None, f"could not read candidate file(s): {parse_errors[0]}")
        if matched_ast_files:
            return _make_res(UPTAKE_STATUS_PASSED, True, f"{matched_ast_files[0]}: AST verified ({ast_rule})")
        return _make_res(UPTAKE_STATUS_FAILED, False, f"no Python file satisfied AST rule {ast_rule!r}")

    # Lexical checks (`text`, `text_any`, `text_absent`, `import`)
    file_texts: list[tuple[str, str, str]] = []
    for rel, fpath in candidate_files:
        try:
            raw_txt = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_texts.append((rel, raw_txt, _strip_source_comments(raw_txt, fpath.suffix)))

    def _engaged() -> bool:
        eng_any = check.get("engagement_any")
        if isinstance(eng_any, list) and eng_any:
            for pat_str in eng_any:
                rx = re.compile(str(pat_str), re.MULTILINE)
                if any(rx.search(stripped) for _, _, stripped in file_texts):
                    return True
            return False
        eng_globs = check.get("engagement_glob")
        if isinstance(eng_globs, list) and eng_globs:
            return bool(_discover_target_files(target_dir, [str(x) for x in eng_globs]))
        return bool(file_texts)

    if kind == "text_absent":
        raw_pat = check.get("must_not_match") or check.get("target") or ""
        pat_list = [str(x) for x in (raw_pat if isinstance(raw_pat, list) else [raw_pat]) if str(x)]
        for pat_str in pat_list:
            rx = re.compile(pat_str, re.MULTILINE)
            for rel, _, stripped in file_texts:
                if rx.search(stripped):
                    return _make_res(UPTAKE_STATUS_FAILED, False, f"{rel}: contains forbidden pattern {pat_str!r}")
        if not _engaged():
            return _make_res(
                UPTAKE_STATUS_NOT_APPLICABLE,
                None,
                f"no engagement precondition matched for negative check {cid!r}",
            )
        return _make_res(UPTAKE_STATUS_PASSED, True, f"checked {len(file_texts)} file(s); forbidden pattern absent")

    # `text`, `text_any`, `import`
    if not _engaged():
        return _make_res(UPTAKE_STATUS_NOT_APPLICABLE, None, f"no target domain files engaged for {cid!r}")
    raw_pat = check.get("must_match") or check.get("target") or ""
    pat_list = [str(x) for x in (raw_pat if isinstance(raw_pat, list) else [raw_pat]) if str(x)]
    for pat_str in pat_list:
        rx = re.compile(pat_str, re.MULTILINE)
        for rel, raw_txt, stripped in file_texts:
            corpus = raw_txt if rel.endswith("SKILL.md") else stripped
            if rx.search(corpus):
                return _make_res(UPTAKE_STATUS_PASSED, True, f"{rel}: matches {pat_str!r}")
    return _make_res(UPTAKE_STATUS_FAILED, False, f"no file matched any of {pat_list!r}")


def score_trigger_quality(
    expected_skills: Sequence[str],
    triggered_skills: Sequence[str],
) -> dict[str, Any]:
    """Compute Trigger Recall, Precision, and Exact Match (`expo/eval-harness` `trigger.ts` parity)."""
    expected: list[str] = []
    for s in expected_skills:
        ns = _norm_skill(s)
        if ns and ns not in expected:
            expected.append(ns)
    triggered: list[str] = []
    for s in triggered_skills:
        ns = _norm_skill(s)
        if ns and ns not in triggered:
            triggered.append(ns)
    expected_set = set(expected)
    triggered_set = set(triggered)
    matched = [s for s in expected if s in triggered_set]
    extra = [s for s in triggered if s not in expected_set]
    missing = [s for s in expected if s not in triggered_set]
    recall = 1.0 if not expected else round(len(matched) / len(expected), 4)
    precision = (1.0 if not expected else 0.0) if not triggered else round(len(matched) / len(triggered), 4)
    return {
        "expected_skills": expected,
        "triggered_skills": triggered,
        "matched_skills": matched,
        "extra_skills": extra,
        "missing_skills": missing,
        "recall": recall,
        "precision": precision,
        "exact_match": expected_set == triggered_set,
        "any_skill_triggered": len(triggered) > 0,
    }


def evaluate_skill_uptake(
    *,
    skills: Sequence[str] | str | None = None,
    triggered_skills: Sequence[str] | str | None = None,
    target_dir: Path | None = None,
    scenario: str = "skills_available_unmentioned",
    uptake_map_path: Path | None = None,
    baseline_evaluator_pct: float | None = None,
    skill_evaluator_pct: float | None = None,
    build_success: bool | None = None,
) -> dict[str, Any]:
    """Evaluate decoupled `uptake_map.json` checks and compute the `Trigger -> Context Uptake -> Outcome Delta` triad."""
    skill_map, checks_by_id = load_uptake_registry(uptake_map_path)
    norm_scenario = str(scenario or "skills_available_unmentioned").strip()
    if norm_scenario not in VALID_SKILL_SCENARIOS:
        norm_scenario = "skills_available_unmentioned"

    raw_expected: list[str] = []
    if isinstance(skills, str):
        raw_expected = [_norm_skill(x) for x in re.split(r"[,;\s]+", skills) if _norm_skill(x)]
    elif isinstance(skills, Sequence):
        for item in skills:
            for x in re.split(r"[,;\s]+", str(item)):
                if _norm_skill(x):
                    raw_expected.append(_norm_skill(x))
    else:
        raw_expected = sorted(skill_map.keys())

    expected_skills = [] if norm_scenario == "skills_unavailable" else list(dict.fromkeys(raw_expected))

    if triggered_skills is None:
        trig_list = list(expected_skills)
    elif isinstance(triggered_skills, str):
        trig_list = [_norm_skill(x) for x in re.split(r"[,;\s]+", triggered_skills) if _norm_skill(x)]
    else:
        trig_list = [_norm_skill(str(x)) for x in triggered_skills if _norm_skill(str(x))]

    eff_target_dir = (target_dir or DEFAULT_SKILLS_ROOT.parent).expanduser().resolve()
    warnings: list[str] = []
    pooled_check_ids: list[str] = []
    for sk in expected_skills:
        cids = skill_map.get(sk)
        if cids is None:
            warnings.append(f"no uptake checks mapped for skill '{sk}'")
            continue
        for cid in cids:
            if cid not in checks_by_id:
                warnings.append(f"uptake_map references unknown check id '{cid}'")
                continue
            if cid not in pooled_check_ids:
                pooled_check_ids.append(cid)

    results_by_id: dict[str, dict[str, Any]] = {}
    for cid in pooled_check_ids:
        results_by_id[cid] = _run_single_uptake_check(checks_by_id[cid], eff_target_dir)

    trig_quality = score_trigger_quality(expected_skills, trig_list)
    triggered_set = set(trig_quality["triggered_skills"])
    skills_out: dict[str, dict[str, Any]] = {}

    for sk in expected_skills:
        is_trig = sk in triggered_set
        cids = skill_map.get(sk)
        if cids is None:
            skills_out[sk] = {
                "expected": True,
                "triggered": is_trig,
                "trigger_status": "observed" if is_trig else "not_observed",
                "uptake_status": "unsupported",
                "passed": None,
                "total": None,
                "uptake_rate": None,
                "hard_failures": 0,
                "soft_advisories": 0,
                "checks": [],
            }
            continue
        sk_checks = [results_by_id[cid] for cid in cids if cid in results_by_id]
        scored = [c for c in sk_checks if c["status"] in UPTAKE_SCORED_STATUSES]
        passed_cnt = sum(1 for c in scored if c["passed"] is True)
        hard_fails = sum(1 for c in scored if c["passed"] is False and c.get("severity") == "hard")
        soft_fails = sum(1 for c in scored if c["passed"] is False and c.get("severity") != "hard")
        has_unavail = any(c["status"] == UPTAKE_STATUS_UNAVAILABLE for c in sk_checks)
        uptake_status = (
            UPTAKE_STATUS_UNAVAILABLE
            if has_unavail
            else ("measured" if scored else (UPTAKE_STATUS_NOT_APPLICABLE if sk_checks else "measured"))
        )
        skills_out[sk] = {
            "expected": True,
            "triggered": is_trig,
            "trigger_status": "observed" if is_trig else "not_observed",
            "uptake_status": uptake_status,
            "passed": passed_cnt,
            "total": len(scored),
            "uptake_rate": None if not scored else round(passed_cnt / len(scored), 4),
            "hard_failures": hard_fails,
            "soft_advisories": soft_fails,
            "checks": sk_checks,
        }

    all_pooled_results = [results_by_id[cid] for cid in pooled_check_ids]
    scored_pooled = [c for c in all_pooled_results if c["status"] in UPTAKE_SCORED_STATUSES]
    static_passed = sum(1 for c in scored_pooled if c["passed"] is True)
    static_total = len(scored_pooled)
    relevant_triggered = len(trig_quality["matched_skills"]) > 0
    if relevant_triggered:
        context_uptake = {
            "passed": static_passed,
            "total": static_total,
            "uptake_rate": None if static_total == 0 else round(static_passed / static_total, 4),
            "skipped_reason": None,
        }
    else:
        context_uptake = {
            "passed": 0,
            "total": static_total,
            "uptake_rate": None,
            "skipped_reason": "relevant skill did not trigger" if expected_skills else "skills_unavailable baseline scenario",
        }

    outcome_delta_val = (
        round(float(skill_evaluator_pct) - float(baseline_evaluator_pct), 4)
        if skill_evaluator_pct is not None and baseline_evaluator_pct is not None
        else None
    )
    hard_failed_checks = [
        c for c in scored_pooled if c["passed"] is False and c.get("severity") == "hard"
    ]
    return {
        "ok": len(hard_failed_checks) == 0,
        "schema": "skill-uptake-eval/v1",
        "scenario": norm_scenario,
        "target_dir": str(eff_target_dir),
        "expected_skills": expected_skills,
        "warnings": warnings,
        "score": {
            "trigger_quality": trig_quality,
            "context_uptake": context_uptake,
            "outcome_delta": {
                "baseline_evaluator_pct": baseline_evaluator_pct,
                "skill_evaluator_pct": skill_evaluator_pct,
                "outcome_delta": outcome_delta_val,
                "build_success": build_success,
            },
        },
        "skills": skills_out,
        "static_checks": all_pooled_results,
    }



def _fast_telemetry_signals(
    *,
    inv: Mapping[str, Mapping[str, Any]],
    warehouse_db: Path | None = None,
    usage_jsonl: Path | None = None,
    copilot_sessions_dir: Path | None = None,
    proposals_path: Path | None = None,
    roi_snapshot_path: Path | None = None,
    days: int = 30,
) -> dict[str, dict[str, Any]]:
    """Collect sub-second lightweight telemetry signals from warehouse.db, recent events.jsonl, usage.jsonl, proposals.jsonl, and skills-roi.json."""
    out: dict[str, dict[str, Any]] = {
        sk: {
            "l0_trigger_misses": 0,
            "l0_zero_action_loads": 0,
            "l0_directional_drifts": 0,
            "r1_to_r2_converged_count": 0,
            "progressive_catch_count": 0,
            "wasted_preread_sessions": 0,
            "wasted_preread_rate": 0.0,
            "aci_output_bloat_count": 0,
            "l1_unwired_drawers": 0,
            "l1_hot_drawers": 0,
            "l1_true_deadwood": 0,
            "l2_exit2_roadblocks": 0,
            "l2_exit2_pre_fix": 0,
            "l2_exit2_post_fix": 0,
            "l2_leaky_rubber_stamps": 0,
            "l2_latency_sinks": 0,
            "l3_wild_occurrences": 0,
            "domain_failures": 0,
            "skill_escapes": 0,
            "rubber_stamp_escapes": 0,
            "p50_ms": None,
            "p90_ms": None,
            "p1_proposal_bonus": 0.0,
            "ref_hit_drawers": set(),
            "_script_durations": [],
            "_script_exit_codes": {},
        }
        for sk in inv
    }

    now_cst = datetime.now(CST)
    window_start = now_cst.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=max(1, days) - 1)
    window_end = now_cst.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    def _resolve_script_watermark(sk: str, raw_script_path: str | None) -> tuple[str | None, int | None]:
        meta = inv.get(sk) or {}
        cur_ver = str(meta.get("skill_version") or "").strip() or None
        wm_map = meta.get("script_watermarks") if isinstance(meta.get("script_watermarks"), Mapping) else {}
        sc_rel = ""
        if raw_script_path:
            m_sc = _SKILL_SCRIPT_RE.search(str(raw_script_path))
            sc_rel = f"scripts/{m_sc.group('script').lstrip('/')}" if m_sc else (str(raw_script_path) if str(raw_script_path).startswith("scripts/") else f"scripts/{Path(str(raw_script_path)).name}")
        if isinstance(wm_map, Mapping) and sc_rel in wm_map:
            return cur_ver, int(wm_map[sc_rel])
        if meta.get("last_modified_epoch") is not None:
            return cur_ver, int(meta["last_modified_epoch"])
        return cur_ver, None

    post_validator_events: dict[tuple[str, str, str], list[tuple[float, int, int, str]]] = {}
    wh_row_seq = 0
    wh = warehouse_db if warehouse_db is not None else DEFAULT_WAREHOUSE_DB
    if _safe_is_file(wh) and wh is not None:
        with contextlib.suppress(sqlite3.Error, OSError), _safe_sqlite_connect(wh) as conn:
            if conn is not None:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            else:
                tables = set()
            for sc_tbl in ("script_call", "fact_skill_script_call"):
                if sc_tbl not in tables:
                    continue
                cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{sc_tbl}")')}
                ts_col = next((c for c in ("ts", "timestamp", "observed_at", "cst_day") if c in cols), None)
                p_col = next((c for c in ("script_path", "path") if c in cols), None)
                ver_col = "skill_version" if "skill_version" in cols else None
                dur_col = "duration_ms" if "duration_ms" in cols else None
                sid_col = next((c for c in ("session_id", "turn_uuid") if c in cols), None)
                wh_pre2: dict[str, int] = {}
                wh_post2: dict[str, int] = {}
                sel_cols = ["skill", "exit_code"]
                for opt_c in (ts_col, p_col, ver_col, dur_col, sid_col):
                    if opt_c:
                        sel_cols.append(f'"{opt_c}"')
                for row_vals in conn.execute(f'SELECT {", ".join(sel_cols)} FROM "{sc_tbl}"'):
                    wh_row_seq += 1
                    row_map = dict(zip([c.strip('"') for c in sel_cols], row_vals))
                    sk = _norm_skill(str(row_map.get("skill") or ""))
                    if sk not in out:
                        continue
                    ts_val = _parse_ts(row_map.get(ts_col)) if ts_col else None
                    if ts_val is not None and not (window_start <= ts_val < window_end):
                        continue
                    raw_p = str(row_map.get(p_col) or "scripts/unknown.py") if p_col else "scripts/unknown.py"
                    m_sc_p = _SKILL_SCRIPT_RE.search(raw_p)
                    sc_rel_key = f"scripts/{m_sc_p.group('script').lstrip('/')}" if m_sc_p else (raw_p if raw_p.startswith("scripts/") else f"scripts/{Path(raw_p).name}")
                    if dur_col and row_map.get(dur_col) not in (None, ""):
                        with contextlib.suppress(TypeError, ValueError):
                            d_val = float(row_map[dur_col])
                            if d_val >= 0:
                                out[sk]["_script_durations"].append(d_val)
                    raw_ec = row_map.get("exit_code")
                    if raw_ec in (None, ""):
                        continue
                    ec = int(raw_ec)
                    out[sk]["_script_exit_codes"].setdefault(sc_rel_key, []).append(ec)
                    cur_ver, wm_epoch = _resolve_script_watermark(sk, raw_p if p_col else None)
                    is_post = _is_call_post_fix(
                        ts_val,
                        str(row_map.get(ver_col) or "").strip() or None if ver_col else None,
                        current_skill_version=cur_ver,
                        watermark_epoch=wm_epoch,
                    )
                    sid_val = str(row_map.get(sid_col) or "wh-default") if sid_col else "wh-default"
                    if is_post and ec != 2:
                        ts_num = ts_val.timestamp() if ts_val is not None else float(wh_row_seq)
                        post_validator_events.setdefault((sk, sid_val, sc_rel_key), []).append((ts_num, wh_row_seq, ec, "wh"))
                    if ec == 2:
                        if is_post:
                            wh_post2[sk] = wh_post2.get(sk, 0) + 1
                        else:
                            wh_pre2[sk] = wh_pre2.get(sk, 0) + 1
                for sk, cnt in wh_post2.items():
                    out[sk]["l2_exit2_roadblocks"] = max(out[sk]["l2_exit2_roadblocks"], cnt)
                    out[sk]["l2_exit2_post_fix"] = max(out[sk]["l2_exit2_post_fix"], cnt)
                for sk, cnt in wh_pre2.items():
                    out[sk]["l2_exit2_pre_fix"] = max(out[sk]["l2_exit2_pre_fix"], cnt)
            for ref_tbl in ("reference_hits", "fact_skill_reference_hit"):
                if ref_tbl not in tables:
                    continue
                cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{ref_tbl}")')}
                p_col = "reference_path" if "reference_path" in cols else ("ref_path" if "ref_path" in cols else None)
                if not p_col:
                    continue
                for raw_sk, raw_p in conn.execute(f'SELECT DISTINCT skill, "{p_col}" FROM "{ref_tbl}"'):
                    sk = _norm_skill(str(raw_sk or ""))
                    if sk not in out or not raw_p:
                        continue
                    norm_pair = _normalize_ref_rel(str(raw_p))
                    rel_ref = norm_pair[1] if norm_pair else (str(raw_p) if str(raw_p).startswith("references/") else f"references/{Path(str(raw_p)).name}")
                    out[sk]["ref_hit_drawers"].add(rel_ref)
            converged_goal_task_skills: set[tuple[str, str, str]] = set()
            converged_goal_skills: set[tuple[str, str]] = set()
            if "fact_gtt_task_run" in tables:
                t_cols = [r[1] for r in conn.execute('PRAGMA table_info("fact_gtt_task_run")')]
                grouped_runs: dict[tuple[str, str], list[dict[str, Any]]] = {}
                for vals in conn.execute('SELECT * FROM "fact_gtt_task_run"'):
                    r_map = dict(zip(t_cols, vals))
                    ts_r = _parse_ts(r_map.get("ended_at") or r_map.get("started_at") or r_map.get("cst_day"))
                    if ts_r is not None and not (window_start <= ts_r < window_end):
                        continue
                    g_id = str(r_map.get("goal_id") or "")
                    t_id = str(r_map.get("task_id") or "")
                    if g_id and t_id:
                        grouped_runs.setdefault((g_id, t_id), []).append(r_map)
                for (g_id, t_id), runs in grouped_runs.items():
                    max_r = max(int(r.get("round_no") or 1) for r in runs)
                    passed_later = any(
                        str(r.get("v5_verdict") or "").upper() in {"PASS", "CONCERNS", "WAIVED"} and int(r.get("round_no") or 1) >= 2
                        for r in runs
                    )
                    still_failed_r3 = max_r >= 3
                    sk_set: set[str] = set()
                    for r in runs:
                        raw_su = r.get("skills_used")
                        if isinstance(raw_su, str) and raw_su.strip().startswith("["):
                            with contextlib.suppress(json.JSONDecodeError):
                                sk_set.update(_norm_skill(x) for x in json.loads(raw_su) if str(x).strip())
                        elif raw_su:
                            for m_sk in _SKILL_CONTEXT_RE.finditer(str(raw_su)):
                                tok = m_sk.group("ctx") or m_sk.group("path") or m_sk.group("call")
                                if tok:
                                    sk_set.add(_norm_skill(tok))
                    for sk in sk_set:
                        if sk not in out:
                            continue
                        if max_r == 2 and passed_later:
                            converged_goal_task_skills.add((g_id, t_id, sk))
                            if g_id:
                                converged_goal_skills.add((g_id, sk))
                            out[sk]["r1_to_r2_converged_count"] += 1
                        elif still_failed_r3:
                            out[sk]["l0_directional_drifts"] += 1
                            out[sk]["skill_escapes"] += 1
                            out[sk]["domain_failures"] += 1
            if "fact_gtt_event" in tables:
                e_cols = [r[1] for r in conn.execute('PRAGMA table_info("fact_gtt_event")')]
                for vals in conn.execute('SELECT * FROM "fact_gtt_event"'):
                    r_map = dict(zip(e_cols, vals))
                    ts_e = _parse_ts(r_map.get("ts") or r_map.get("observed_at") or r_map.get("cst_day"))
                    if ts_e is not None and not (window_start <= ts_e < window_end):
                        continue
                    evt = str(r_map.get("evt_type") or "").upper()
                    note = str(r_map.get("note_preview") or r_map.get("note") or "")
                    g_id = str(r_map.get("goal_id") or "")
                    t_id = str(r_map.get("task_id") or "")
                    sk_hits = {
                        _norm_skill(m.group("ctx") or m.group("path") or m.group("call") or "")
                        for m in _SKILL_CONTEXT_RE.finditer(note)
                    }
                    sk_hits.discard("")
                    if not sk_hits and "design" in g_id.lower():
                        sk_hits.add("my-designer")
                    for sk in sk_hits:
                        if sk not in out:
                            continue
                        if evt in {"CHANGE_REQUEST", "NEED_OWNER_DECISION"}:
                            out[sk]["l0_directional_drifts"] += 1
                            out[sk]["skill_escapes"] += 1
                            out[sk]["domain_failures"] += 1
                        elif evt == "REVIEW_FAIL":
                            if (g_id, t_id, sk) in converged_goal_task_skills or (not t_id and (g_id, sk) in converged_goal_skills):
                                continue
                            out[sk]["skill_escapes"] += 1
                            out[sk]["domain_failures"] += 1

    u_path = usage_jsonl if usage_jsonl is not None else DEFAULT_SKILL_TELEMETRY_USAGE_JSONL
    if _is_safe_local_file(u_path) and u_path is not None:
        u_pre_exit2: dict[str, int] = {}
        u_post_exit2: dict[str, int] = {}
        raw_u = _safe_read_text(u_path)
        for idx_u, line in enumerate(raw_u.splitlines(), 1):
            if '"script_call"' not in line and '"reference_hit"' not in line:
                continue
            with contextlib.suppress(json.JSONDecodeError, TypeError, ValueError):
                rec = json.loads(line)
                if not isinstance(rec, Mapping):
                    continue
                ts = _parse_ts(rec.get("ts") or rec.get("timestamp") or rec.get("observed_at"))
                if ts is not None and not (window_start <= ts < window_end):
                    continue
                sk = _norm_skill(str(rec.get("skill") or ""))
                if sk not in out:
                    continue
                kind = str(rec.get("kind") or "")
                if kind == "script_call":
                    ec_raw = rec.get("exit_code")
                    if ec_raw not in (None, ""):
                        ec = int(ec_raw)
                        raw_sc = str(rec.get("script_path") or "scripts/unknown.py")
                        m_sc_u = _SKILL_SCRIPT_RE.search(raw_sc)
                        sc_rel_u = f"scripts/{m_sc_u.group('script').lstrip('/')}" if m_sc_u else (raw_sc if raw_sc.startswith("scripts/") else f"scripts/{Path(raw_sc).name}")
                        cur_ver, wm_epoch = _resolve_script_watermark(sk, raw_sc)
                        is_post = _is_call_post_fix(
                            ts,
                            str(rec.get("skill_version") or "").strip() or None,
                            current_skill_version=cur_ver,
                            watermark_epoch=wm_epoch,
                        )
                        sid_u = str(rec.get("session_id") or "usage-default").strip() or "usage-default"
                        if is_post and ec != 2:
                            ts_num_u = ts.timestamp() if ts is not None else float(1_000_000 + idx_u)
                            post_validator_events.setdefault((sk, sid_u, sc_rel_u), []).append(
                                (ts_num_u, 1_000_000 + idx_u, ec, "usage")
                            )
                        if ec == 2:
                            if is_post:
                                u_post_exit2[sk] = u_post_exit2.get(sk, 0) + 1
                            else:
                                u_pre_exit2[sk] = u_pre_exit2.get(sk, 0) + 1
                elif kind == "reference_hit":
                    rp = str(rec.get("reference_path") or "")
                    if rp:
                        norm_pair = _normalize_ref_rel(rp)
                        rel_ref = norm_pair[1] if norm_pair else (rp if rp.startswith("references/") else f"references/{Path(rp).name}")
                        out[sk]["ref_hit_drawers"].add(rel_ref)
        for sk, cnt in u_post_exit2.items():
            out[sk]["l2_exit2_roadblocks"] = max(out[sk]["l2_exit2_roadblocks"], cnt)
            out[sk]["l2_exit2_post_fix"] = max(out[sk]["l2_exit2_post_fix"], cnt)
        for sk, cnt in u_pre_exit2.items():
            out[sk]["l2_exit2_pre_fix"] = max(out[sk]["l2_exit2_pre_fix"], cnt)

    roi_path = roi_snapshot_path if roi_snapshot_path is not None else DEFAULT_LOCAL_AIDASH_SKILLS_ROI_PATH
    if _is_safe_local_file(roi_path) and roi_path is not None:
        with contextlib.suppress(OSError, json.JSONDecodeError):
            raw_roi = _safe_read_text(roi_path)
            roi_doc = json.loads(raw_roi) if raw_roi else {}
            sr_doc = (roi_doc.get("skill_retro") if isinstance(roi_doc, Mapping) else None) or {}
            if isinstance(sr_doc, Mapping):
                snap_ts = _parse_ts(sr_doc.get("generated_at") or roi_doc.get("source_as_of"))
                l0 = sr_doc.get("layer0_direction_and_triggers") or {}
                l1 = sr_doc.get("layer1_drawer_metabolism") or {}
                l2 = sr_doc.get("layer2_script_efficacy_and_speed") or {}
                for tm in l0.get("trigger_misses") or []:
                    sk = _norm_skill(str(tm.get("skill") or ""))
                    if sk in out:
                        out[sk]["l0_trigger_misses"] = max(out[sk]["l0_trigger_misses"], int(tm.get("missed_sessions") or 0))
                for ze in l0.get("zero_action_loads") or []:
                    sk = _norm_skill(str(ze.get("skill") or ""))
                    if sk in out:
                        out[sk]["l0_zero_action_loads"] = max(out[sk]["l0_zero_action_loads"], int(ze.get("zero_action_sessions") or 0))
                for uw in l1.get("unwired_disconnected_drawers") or []:
                    sk = _norm_skill(str(uw.get("skill") or ""))
                    if sk in out:
                        cur_ver, wm_epoch = _resolve_script_watermark(sk, None)
                        if _is_call_post_fix(snap_ts, None, current_skill_version=cur_ver, watermark_epoch=wm_epoch):
                            out[sk]["l1_unwired_drawers"] += 1
                            out[sk]["domain_failures"] = max(out[sk]["domain_failures"], int(uw.get("domain_failure_count") or 0))
                for rb in l2.get("rigid_roadblocks") or []:
                    sk = _norm_skill(str(rb.get("skill") or ""))
                    if sk in out:
                        cur_ver, wm_epoch = _resolve_script_watermark(sk, str(rb.get("script") or ""))
                        if _is_call_post_fix(
                            snap_ts,
                            str(rb.get("skill_version") or "").strip() or None,
                            current_skill_version=cur_ver,
                            watermark_epoch=wm_epoch,
                        ):
                            cnt_val = int(rb.get("post_fix") if rb.get("post_fix") is not None else (rb.get("exit_2_count") or 0))
                            out[sk]["l2_exit2_roadblocks"] = max(out[sk]["l2_exit2_roadblocks"], cnt_val)
                            out[sk]["l2_exit2_post_fix"] = max(out[sk]["l2_exit2_post_fix"], cnt_val)
                        else:
                            out[sk]["l2_exit2_pre_fix"] = max(out[sk]["l2_exit2_pre_fix"], int(rb.get("exit_2_count") or 0))
                for idx_p, prop in enumerate(sr_doc.get("proposals") or []):
                    if not isinstance(prop, Mapping):
                        continue
                    sk = _norm_skill(str(prop.get("skill") or ""))
                    if sk not in out:
                        continue
                    cur_ver, wm_epoch = _resolve_script_watermark(sk, None)
                    if not _is_call_post_fix(
                        _parse_ts(prop.get("ts") or prop.get("source_as_of")) or snap_ts,
                        str(prop.get("skill_version") or "").strip() or None,
                        current_skill_version=cur_ver,
                        watermark_epoch=wm_epoch,
                    ):
                        continue
                    prio = str(prop.get("priority") or "").upper()
                    if prio in {"P0", "P1", "HIGH"}:
                        out[sk]["p1_proposal_bonus"] = max(out[sk]["p1_proposal_bonus"], round(8000.0 - idx_p * 1200.0, 2))

    p_path = proposals_path if proposals_path is not None else DEFAULT_PROPOSALS_PATH
    if _is_safe_local_file(p_path) and p_path is not None:
        with contextlib.suppress(OSError):
            pending_idx = 0
            for line in _safe_read_text(p_path).splitlines():
                if not line.strip():
                    continue
                with contextlib.suppress(json.JSONDecodeError):
                    rec = json.loads(line)
                    if not isinstance(rec, Mapping):
                        continue
                    if str(rec.get("status") or "pending").lower() != "pending":
                        continue
                    if not str(rec.get("id") or "").startswith("skill-retro-"):
                        continue
                    sk = _norm_skill(str(rec.get("skill") or ""))
                    if sk not in out:
                        continue
                    cat = str(rec.get("category") or "")
                    detail = str(rec.get("detail") or "")
                    m_sc_detail = re.search(r"script=([^\s|]+)", detail)
                    cur_ver, wm_epoch = _resolve_script_watermark(sk, m_sc_detail.group(1) if m_sc_detail else None)
                    rec_ts = _parse_ts(rec.get("ts") or rec.get("source_as_of"))
                    if not _is_call_post_fix(
                        rec_ts,
                        str(rec.get("skill_version") or "").strip() or None,
                        current_skill_version=cur_ver,
                        watermark_epoch=wm_epoch,
                    ):
                        if cat == "rigid_roadblock":
                            m_e2_pre = re.search(r"exit_2_count=(\d+)", detail)
                            if m_e2_pre:
                                out[sk]["l2_exit2_pre_fix"] = max(out[sk]["l2_exit2_pre_fix"], int(m_e2_pre.group(1)))
                        continue
                    if cat == "unwired_disconnected_drawer":
                        out[sk]["l1_unwired_drawers"] = max(out[sk]["l1_unwired_drawers"], 1)
                        m_df = re.search(r"domain_failures=(\d+)", detail)
                        if m_df:
                            out[sk]["domain_failures"] = max(out[sk]["domain_failures"], int(m_df.group(1)))
                    elif cat == "rigid_roadblock":
                        m_e2 = re.search(r"exit_2_count=(\d+)", detail)
                        if m_e2:
                            out[sk]["l2_exit2_roadblocks"] = max(out[sk]["l2_exit2_roadblocks"], int(m_e2.group(1)))
                            out[sk]["l2_exit2_post_fix"] = max(out[sk]["l2_exit2_post_fix"], int(m_e2.group(1)))
                    elif cat == "trigger_miss":
                        m_tm = re.search(r"missed_sessions=(\d+)", detail)
                        if m_tm:
                            out[sk]["l0_trigger_misses"] = max(out[sk]["l0_trigger_misses"], int(m_tm.group(1)))
                    prio = str(rec.get("priority") or "").upper()
                    if prio in {"P0", "P1", "HIGH"}:
                        out[sk]["p1_proposal_bonus"] = max(out[sk]["p1_proposal_bonus"], round(8000.0 - pending_idx * 1200.0, 2))
                        pending_idx += 1

    # Sample bounded recent copilot sessions (<=12 most recent events.jsonl) on the fast path
    sampled_obs: dict[str, SessionObservation] = {}
    sess_dir = copilot_sessions_dir if copilot_sessions_dir is not None else DEFAULT_COPILOT_SESSIONS_DIR
    if _probe_readable_dir(sess_dir) and sess_dir is not None:
        basename_to_targets: dict[str, list[tuple[str, str]]] = {}
        for sk_n, meta_n in inv.items():
            for rel_sc in meta_n.get("script_entries", []):
                basename_to_targets.setdefault(Path(str(rel_sc)).name, []).append((sk_n, str(rel_sc)))
        sampled_obs = _parse_copilot_sessions(
            sess_dir,
            window_start=window_start,
            window_end=window_end,
            known_skills=set(inv.keys()),
            basename_to_targets=basename_to_targets,
            max_sessions=12,
        )

    # Merge any warehouse/usage.jsonl validator calls into sampled_obs (if session was sampled)
    # without pre-dropping against non-adjacent calls; `_deduplicate_session_script_calls` reconciles chronologically!
    non_sampled_validator_events: dict[tuple[str, str, str], list[tuple[float, int, int, str]]] = {}
    for (sk, sid_v, sc_rel_v), ev_items in post_validator_events.items():
        if sid_v in sampled_obs:
            obs_s = sampled_obs[sid_v]
            obs_s.skills_loaded.add(sk)
            for ts_num, _, ec_v, ev_src in sorted(ev_items, key=lambda item: (item[0], item[1])):
                ev_dt = datetime.fromtimestamp(ts_num, tz=timezone.utc).astimezone(CST)
                src_label = "warehouse" if ev_src == "wh" else "usage_jsonl"
                obs_s.script_calls.append(
                    ScriptCallEvent(
                        session_id=sid_v,
                        skill=sk,
                        script_rel=sc_rel_v,
                        command=sc_rel_v,
                        ts=ev_dt,
                        exit_code=ec_v,
                        duration_ms=None,
                        measured=False,
                        seq=obs_s.next_seq(),
                        origin_source=src_label,
                    )
                )
        else:
            non_sampled_validator_events[(sk, sid_v, sc_rel_v)] = ev_items

    if sampled_obs:
        all_sampled = list(sampled_obs.values())
        for obs_s in all_sampled:
            _deduplicate_session_script_calls(obs_s)
        for sk_n in inv:
            loaded_for_sk = [
                o
                for o in all_sampled
                if sk_n in o.skills_loaded
                or any(r_sk == sk_n for r_sk, _, _, _ in o.ref_reads)
                or any(c.skill == sk_n for c in o.script_calls)
            ]
            prog_catches, wasted_sids, unres_fails = _evaluate_session_progressive_and_preread(loaded_for_sk, sk_n)
            if prog_catches > 0:
                out[sk_n]["progressive_catch_count"] = max(int(out[sk_n].get("progressive_catch_count") or 0), prog_catches)
            if wasted_sids:
                out[sk_n]["wasted_preread_sessions"] = len(wasted_sids)
                out[sk_n]["wasted_preread_rate"] = round(len(wasted_sids) / max(1, len(loaded_for_sk)), 4)
            if unres_fails > 0:
                out[sk_n]["domain_failures"] += unres_fails
            for o in loaded_for_sk:
                for r_sk, r_rel, _, _ in o.ref_reads:
                    if r_sk == sk_n:
                        out[sk_n]["ref_hit_drawers"].add(r_rel)
                for c in o.script_calls:
                    if c.skill != sk_n:
                        continue
                    if c.aci_output_bloat or c.output_chars > ACI_OUTPUT_BLOAT_CHARS:
                        out[sk_n]["aci_output_bloat_count"] += 1
                    if c.measured and c.duration_ms is not None:
                        out[sk_n]["_script_durations"].append(float(c.duration_ms))
                    if c.exit_code is not None:
                        out[sk_n]["_script_exit_codes"].setdefault(c.script_rel, []).append(int(c.exit_code))
                        if c.exit_code == 2:
                            cur_ver, wm_epoch = _resolve_script_watermark(sk_n, c.script_rel)
                            if _is_call_post_fix(c.ts, c.skill_version, current_skill_version=cur_ver, watermark_epoch=wm_epoch):
                                out[sk_n]["l2_exit2_roadblocks"] = max(out[sk_n]["l2_exit2_roadblocks"], 1)
                                out[sk_n]["l2_exit2_post_fix"] = max(out[sk_n]["l2_exit2_post_fix"], 1)

    # Reconcile non-sampled warehouse/usage.jsonl validator sequences chronologically by (ts_epoch, row_seq)
    for (sk, _, _), ev_items in non_sampled_validator_events.items():
        ordered_evs = sorted(ev_items, key=lambda item: (item[0], item[1]))
        # Deduplicate only adjacent cross-source mirrored records (wh vs usage) without coalescing same-source retries
        dedup_evs: list[tuple[float, int, str]] = []
        for ts_num, _, ec_v, ev_src in ordered_evs:
            if (
                dedup_evs
                and dedup_evs[-1][2] != ev_src
                and dedup_evs[-1][1] == ec_v
                and abs(ts_num - dedup_evs[-1][0]) < 1.0
            ):
                continue
            dedup_evs.append((ts_num, ec_v, ev_src))
        dedup_ecs = [ec_v for _, ec_v, _ in dedup_evs]
        fail_indices = [i for i, ec_v in enumerate(dedup_ecs) if ec_v != 0]
        if not fail_indices:
            continue
        recovered_any = any(
            any(ec_later == 0 for ec_later in dedup_ecs[f_idx + 1 :])
            for f_idx in fail_indices
        )
        last_fail_idx = max(fail_indices)
        terminal_unresolved = not any(ec_later == 0 for ec_later in dedup_ecs[last_fail_idx + 1 :])
        if recovered_any:
            out[sk]["progressive_catch_count"] += 1
        if terminal_unresolved:
            out[sk]["domain_failures"] += 1

    for sk, s_data in out.items():
        durs = s_data.pop("_script_durations", [])
        sc_exits = s_data.pop("_script_exit_codes", {})
        if durs:
            s_data["p50_ms"] = _percentile(durs, 50)
            s_data["p90_ms"] = _percentile(durs, 90)
            if (max(durs) >= LATENCY_SINK_MAX_MS) or (
                (s_data["p50_ms"] or 0.0) >= LATENCY_SINK_P50_MS and len(durs) >= LATENCY_SINK_MIN_CALLS_FOR_P50
            ):
                s_data["l2_latency_sinks"] = max(int(s_data.get("l2_latency_sinks") or 0), 1)
        sk_escapes = int(s_data.get("skill_escapes") or 0)
        leaky_cnt = 0
        for _, ec_list in sc_exits.items():
            if len(ec_list) >= 3 and all(c == 0 for c in ec_list) and sk_escapes > 0:
                leaky_cnt += 1
        if leaky_cnt > 0:
            s_data["l2_leaky_rubber_stamps"] = max(int(s_data.get("l2_leaky_rubber_stamps") or 0), leaky_cnt)
            s_data["rubber_stamp_escapes"] = sk_escapes

    return out


def probe_skills(
    *,
    skill: str | None = None,
    aspect: str | None = None,
    fast: bool = True,
    summary: bool = False,
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    copilot_sessions_dir: Path | None = None,
    usage_jsonl: Path | None = None,
    trace_export_path: Path | None = None,
    handoff_root: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    telemetry_shards_dir: Path | None = None,
    days: int = 30,
) -> dict[str, Any]:
    """Stage 1: Sub-second wide or single-skill probe (`probe [--skill <name>] [--aspect <dim>] --fast`)."""
    t0 = time.monotonic()
    s_root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    norm_sk = _norm_skill(skill) if skill else None
    entry_mode = "direct_single_skill" if norm_sk else "global_sweep"
    aspect_filter = _normalize_aspects(aspect, mode="partial" if aspect else "full") if aspect else list(CANONICAL_ASPECTS)

    full_inv = _discover_skill_inventory(s_root, d_root)
    known_all = set(full_inv.keys())
    leases = discover_active_skill_leases(dev_root=d_root, handoff_root=handoff_root, known_skills=known_all)
    if norm_sk:
        inv = {k: v for k, v in full_inv.items() if _norm_skill(k) == norm_sk}
    else:
        inv = full_inv

    explicit_source_override = any(
        val is not None
        for val in (warehouse_db, copilot_db, copilot_sessions_dir, usage_jsonl, trace_export_path)
    )
    has_small_custom_sources = explicit_source_override
    skills_signals: list[dict[str, Any]] = []

    cross_repo_gate_health: dict[str, Any] | None = None
    if norm_sk == "repo-kit" or (norm_sk is None and "repo-kit" in inv):
        cross_repo_gate_health = _compute_cross_repo_gate_health(skills_root=s_root, dev_root=d_root)

    if has_small_custom_sources or not fast:
        diag = compute_skill_retro(
            warehouse_db=warehouse_db,
            copilot_db=copilot_db,
            copilot_sessions_dir=copilot_sessions_dir,
            usage_jsonl=usage_jsonl,
            trace_export_path=trace_export_path,
            handoff_root=handoff_root,
            skills_root=s_root,
            dev_root=d_root,
            telemetry_shards_dir=telemetry_shards_dir,
            skill=norm_sk,
            days=days,
            mirror_roi_snapshot=False,
        )
        if diag.get("cross_repo_gate_health"):
            cross_repo_gate_health = diag["cross_repo_gate_health"]
        l0 = diag.get("layer0_direction_and_triggers") or {}
        l1 = diag.get("layer1_drawer_metabolism") or {}
        l2 = diag.get("layer2_script_efficacy_and_speed") or {}
        l3 = diag.get("layer3_wild_patterns_and_eval") or {}
        taste = l3.get("taste_and_eval_diversity") or {}
        prog_by_sk = l1.get("progressive_catch_by_skill") or {}
        wasted_by_sk = l1.get("wasted_preread_by_skill") or {}
        bloat_by_sk = l2.get("aci_output_bloat_by_skill") or {}
        ir_by_sk = l2.get("internal_reality_by_skill") or {}
        ev_qual_by_sk = l3.get("eval_quality_by_skill") or {}
        fast_wh = _fast_telemetry_signals(
            inv=inv,
            warehouse_db=warehouse_db,
            usage_jsonl=usage_jsonl,
            days=days,
        )

        for sk_name, meta in sorted(inv.items()):
            t_wh = fast_wh.get(sk_name) or {}
            trig_misses = sum(int(x.get("missed_sessions") or 0) for x in (l0.get("trigger_misses") or []) if x.get("skill") == sk_name)
            empty_loads = sum(int(x.get("zero_action_sessions") or 0) for x in (l0.get("zero_action_loads") or []) if x.get("skill") == sk_name)
            drifts = sum(1 for x in (l0.get("directional_drifts") or []) if sk_name in (x.get("skills") or []))
            r1_r2_conv = sum(1 for x in (l0.get("healthy_r1_to_r2_convergences") or []) if sk_name in (x.get("skills") or []))
            unwired = sum(1 for x in (l1.get("unwired_disconnected_drawers") or []) if x.get("skill") == sk_name)
            hot_cnt = sum(1 for x in (l1.get("hot_drawers") or []) if x.get("skill") == sk_name)
            deadwood = sum(1 for x in (l1.get("true_zero_hit_deadwood") or []) if x.get("skill") == sk_name)
            prog_catches = int(prog_by_sk.get(sk_name) or 0)
            wasted_info = wasted_by_sk.get(sk_name) or {}
            wasted_rate = float(wasted_info.get("wasted_preread_rate") or 0.0)
            wasted_sessions = int(wasted_info.get("wasted_preread_sessions") or 0)
            exit2_cnt = sum(int(x.get("exit_2_count") or 0) for x in (l2.get("rigid_roadblocks") or []) if x.get("skill") == sk_name)
            exit2_pre_cnt = sum(
                int(x.get("pre_fix") or 0)
                for x in ((l2.get("rigid_roadblocks") or []) + (l2.get("watermarked_resolved_roadblocks") or []))
                if x.get("skill") == sk_name
            )
            rubber_items = [x for x in (l2.get("ceremonial_rubber_stamps") or []) if x.get("skill") == sk_name and int(x.get("co_occurring_skill_escapes") or 0) > 0]
            rubber_cnt = len(rubber_items)
            rubber_escapes = max([int(x.get("co_occurring_skill_escapes") or 0) for x in rubber_items], default=int(t_wh.get("rubber_stamp_escapes") or 0))
            slow_cnt = sum(
                1
                for x in (l2.get("latency_sinks") or [])
                if (x.get("skill") == sk_name or (sk_name == "repo-kit" and x.get("skill") == "repo-gate"))
                and x.get("primary_bottleneck") != "external_wait"
            )
            bloat_cnt = int(bloat_by_sk.get(sk_name) or 0)
            ir_info = ir_by_sk.get(sk_name) or meta.get("internal_reality") or {}
            cli_drift_list = list(ir_info.get("internal_doc_cli_drift") or [])
            broken_refs_list = list(ir_info.get("internal_broken_refs") or [])
            cli_drift_cnt = len(cli_drift_list)
            broken_refs_cnt = len(broken_refs_list)
            ev_q = ev_qual_by_sk.get(sk_name) or {}
            ev_counter_cnt = int(ev_q.get("eval_counter_case_count") if "eval_counter_case_count" in ev_q else (meta.get("eval_counter_case_count") or 0))
            ev_counter_ratio = float(ev_q.get("eval_counter_case_ratio") if "eval_counter_case_ratio" in ev_q else (meta.get("eval_counter_case_ratio") or 0.0))
            ev_escape_gap = bool(ev_q.get("eval_escape_gap", False))
            sk_escapes = int(ev_q.get("skill_escapes") or t_wh.get("skill_escapes") or 0)
            dom_fails = int(ev_q.get("domain_failures") or t_wh.get("domain_failures") or 0)
            cr_findings_cnt = int((cross_repo_gate_health or {}).get("total_findings") or 0) if sk_name == "repo-kit" else 0
            wild_cnt = sum(int(x.get("occurrences") or 0) for x in (l3.get("wild_patterns") or []) if x.get("candidate_skill") == sk_name)
            creative_flag = sk_name in {"my-designer", "design-studio"} and bool(taste.get("low_diversity_goals"))
            eval_cases_cnt = int(meta.get("eval_case_count") or 0)

            triggered_aspects: list[str] = []
            if (trig_misses > 0 or empty_loads > 0 or drifts > 0 or broken_refs_cnt > 0 or not meta.get("budget_ok", True)) and "l0" in aspect_filter:
                triggered_aspects.append("l0")
            if (unwired > 0 or hot_cnt > 0 or deadwood > 0 or wasted_sessions > 0) and "l1" in aspect_filter:
                triggered_aspects.append("l1")
            if (exit2_cnt > 0 or rubber_cnt > 0 or slow_cnt > 0 or cr_findings_cnt > 0 or cli_drift_cnt > 0 or bloat_cnt > 0) and "l2" in aspect_filter:
                triggered_aspects.append("l2")
            if wild_cnt > 0 and "l3" in aspect_filter:
                triggered_aspects.append("l3")
            if creative_flag and "creative" in aspect_filter:
                triggered_aspects.append("creative")
            if (len(triggered_aspects) >= 2 or ev_escape_gap) and "eval" in aspect_filter:
                triggered_aspects.append("eval")

            # Outcome-First Scoring: progressive_catch_count and r1_to_r2_converged_count are NEVER penalized!
            priority_score = (
                exit2_cnt * 3.0
                + unwired * 2.5
                + trig_misses * 2.0
                + empty_loads * 1.2
                + drifts * 2.0
                + wasted_sessions * 1.5
                + rubber_cnt * 1.8
                + slow_cnt * 1.5
                + cli_drift_cnt * 2.5
                + broken_refs_cnt * 2.0
                + bloat_cnt * 1.8
                + (2.0 if ev_escape_gap else 0.0)
                + (min(6.0, cr_findings_cnt * 0.5) if cr_findings_cnt > 0 else 0.0)
                + (1.5 if wild_cnt >= WILD_PATTERN_MIN_COUNT else 0.0)
                + (1.2 if creative_flag else 0.0)
                + (1.0 if not meta.get("budget_ok", True) else 0.0)
            )
            sig_dict: dict[str, Any] = {
                "l0_trigger_misses": trig_misses,
                "l0_zero_action_loads": empty_loads,
                "l0_directional_drifts": drifts,
                "healthy_r1_to_r2_convergences": r1_r2_conv,
                "progressive_catch_count": prog_catches,
                "wasted_preread_sessions": wasted_sessions,
                "wasted_preread_rate": wasted_rate,
                "l1_unwired_drawers": unwired,
                "l1_hot_drawers": hot_cnt,
                "l1_true_deadwood": deadwood,
                "l2_exit2_roadblocks": exit2_cnt,
                "l2_exit2_pre_fix": exit2_pre_cnt,
                "l2_exit2_post_fix": exit2_cnt,
                "l2_leaky_rubber_stamps": rubber_cnt,
                "rubber_stamp_escapes": rubber_escapes,
                "l2_latency_sinks": slow_cnt,
                "p50_ms": t_wh.get("p50_ms"),
                "p90_ms": t_wh.get("p90_ms"),
                "aci_output_bloat_count": bloat_cnt,
                "internal_doc_cli_drift_count": cli_drift_cnt,
                "internal_broken_refs_count": broken_refs_cnt,
                "internal_doc_cli_drift": cli_drift_list[:3] if summary else cli_drift_list,
                "internal_broken_refs": broken_refs_list[:3] if summary else broken_refs_list,
                "l3_wild_occurrences": wild_cnt,
                "creative_selection_signal": bool(creative_flag),
                "domain_failures": dom_fails,
                "skill_escapes": sk_escapes,
                "eval_counter_case_count": ev_counter_cnt,
                "eval_counter_case_ratio": ev_counter_ratio,
                "eval_escape_gap": ev_escape_gap,
            }
            if sk_name == "repo-kit":
                sig_dict["cross_repo_gate_findings"] = cr_findings_cnt
            skills_signals.append(
                {
                    "skill": sk_name,
                    "skill_version": meta.get("skill_version"),
                    "subtree_sha": meta.get("subtree_sha"),
                    "plugin_version": meta.get("plugin_version", "1.0.0"),
                    "last_modified_epoch": meta.get("last_modified_epoch"),
                    "scope": meta.get("scope", "global"),
                    "l0_lines": meta.get("l0_lines", 0),
                    "max_l1_lines": meta.get("max_l1_lines", 0),
                    "budget_ok": bool(meta.get("budget_ok", True)),
                    "eval_case_count": eval_cases_cnt,
                    "eval_counter_case_count": ev_counter_cnt,
                    "eval_counter_case_ratio": ev_counter_ratio,
                    "eval_escape_gap": ev_escape_gap,
                    "leased": sk_name in leases["leased_skills"],
                    "lease_reasons": leases["lease_reasons"].get(sk_name, []),
                    "signals": sig_dict,
                    "triggered_aspects": triggered_aspects,
                    "priority_score": round(priority_score, 2),
                }
            )
    else:
        fast_telem = _fast_telemetry_signals(
            inv=inv,
            warehouse_db=warehouse_db,
            usage_jsonl=usage_jsonl,
            copilot_sessions_dir=copilot_sessions_dir,
            days=days,
        )
        for sk_name, meta in sorted(inv.items()):
            pkg_p = Path(str(meta.get("path") or (s_root / sk_name)))
            t_sig = fast_telem.get(sk_name) or {}
            ref_hits_set = t_sig.get("ref_hit_drawers") or set()
            unwired_zero_hit = 0
            for ref_e in meta.get("reference_entries") or []:
                rel_r = str(ref_e.get("rel_path") or "")
                if rel_r.startswith("references/") and _drawer_linked_in_l0(pkg_p, rel_r) and not _drawer_linked_in_scripts(pkg_p, rel_r):
                    if rel_r not in ref_hits_set:
                        unwired_zero_hit += 1
            trig_misses = int(t_sig.get("l0_trigger_misses") or 0)
            empty_loads = int(t_sig.get("l0_zero_action_loads") or 0)
            drifts = int(t_sig.get("l0_directional_drifts") or 0)
            r1_r2_conv = int(t_sig.get("r1_to_r2_converged_count") or 0)
            prog_catches = int(t_sig.get("progressive_catch_count") or 0)
            wasted_sessions = int(t_sig.get("wasted_preread_sessions") or 0)
            wasted_rate = float(t_sig.get("wasted_preread_rate") or 0.0)
            bloat_cnt = int(t_sig.get("aci_output_bloat_count") or 0)
            exit2_cnt = int(t_sig.get("l2_exit2_roadblocks") or 0)
            domain_fails = int(t_sig.get("domain_failures") or 0)
            sk_escapes = int(t_sig.get("skill_escapes") or 0)
            rubber_cnt = int(t_sig.get("l2_leaky_rubber_stamps") or 0)
            rubber_escapes = int(t_sig.get("rubber_stamp_escapes") or 0)
            slow_cnt = int(t_sig.get("l2_latency_sinks") or 0)
            p1_bonus = float(t_sig.get("p1_proposal_bonus") or 0.0)
            # Fix l1_unwired_drawers false positive: only mark unwired when domain_failures > 0 and hits == 0 and not script-linked
            unwired_cnt = max(
                int(t_sig.get("l1_unwired_drawers") or 0),
                unwired_zero_hit if (domain_fails > 0 and meta.get("scripts")) else 0,
            )
            ir_info = meta.get("internal_reality") or internal_reality_check(pkg_p)
            cli_drift_list = list(ir_info.get("internal_doc_cli_drift") or [])
            broken_refs_list = list(ir_info.get("internal_broken_refs") or [])
            cli_drift_cnt = len(cli_drift_list)
            broken_refs_cnt = len(broken_refs_list)
            cr_findings_cnt = int((cross_repo_gate_health or {}).get("total_findings") or 0) if sk_name == "repo-kit" else 0
            budget_ok = bool(meta.get("budget_ok", True))
            eval_cases_cnt = int(meta.get("eval_case_count") or 0)
            ev_counter_cnt = int(meta.get("eval_counter_case_count") or 0)
            ev_counter_ratio = float(meta.get("eval_counter_case_ratio") or 0.0)
            ev_weekly_delta = int(meta.get("eval_case_weekly_delta") or 0)
            ev_escape_gap = bool((domain_fails + sk_escapes) >= 3 and ev_weekly_delta == 0)
            creative_flag = False
            triggered_aspects = []
            if (trig_misses > 0 or empty_loads > 0 or drifts > 0 or broken_refs_cnt > 0 or not budget_ok) and "l0" in aspect_filter:
                triggered_aspects.append("l0")
            if ((unwired_cnt > 0 and meta.get("scripts")) or wasted_sessions > 0) and "l1" in aspect_filter:
                triggered_aspects.append("l1")
            if (
                exit2_cnt > 0
                or rubber_cnt > 0
                or slow_cnt > 0
                or cli_drift_cnt > 0
                or bloat_cnt > 0
                or cr_findings_cnt > 0
                or (meta.get("scripts") and unwired_cnt > 0)
            ) and "l2" in aspect_filter:
                triggered_aspects.append("l2")
            if creative_flag and "creative" in aspect_filter:
                triggered_aspects.append("creative")
            if (eval_cases_cnt == 0 or ev_escape_gap or len(triggered_aspects) >= 2) and "eval" in aspect_filter:
                triggered_aspects.append("eval")
            score = (
                p1_bonus
                + exit2_cnt * 3.0
                + (unwired_cnt * 2.5 if domain_fails > 0 or p1_bonus > 0 else 0.0)
                + domain_fails * 1.5
                + drifts * 2.0
                + wasted_sessions * 1.5
                + rubber_cnt * 1.8
                + slow_cnt * 1.5
                + cli_drift_cnt * 2.5
                + broken_refs_cnt * 2.0
                + bloat_cnt * 1.8
                + (2.0 if ev_escape_gap else 0.0)
                + trig_misses * 2.0
                + empty_loads * 1.2
                + (min(6.0, cr_findings_cnt * 0.5) if cr_findings_cnt > 0 else 0.0)
                + (2.5 if not budget_ok else 0.0)
                + (1.0 if eval_cases_cnt == 0 else 0.0)
            )
            sig_dict = {
                "l0_trigger_misses": trig_misses,
                "l0_zero_action_loads": empty_loads,
                "l0_directional_drifts": drifts,
                "healthy_r1_to_r2_convergences": r1_r2_conv,
                "progressive_catch_count": prog_catches,
                "wasted_preread_sessions": wasted_sessions,
                "wasted_preread_rate": wasted_rate,
                "l1_unwired_drawers": unwired_cnt if meta.get("scripts") else 0,
                "l1_hot_drawers": int(t_sig.get("l1_hot_drawers") or 0),
                "l1_true_deadwood": int(t_sig.get("l1_true_deadwood") or 0),
                "l2_exit2_roadblocks": exit2_cnt,
                "l2_exit2_pre_fix": int(t_sig.get("l2_exit2_pre_fix") or 0),
                "l2_exit2_post_fix": int(t_sig.get("l2_exit2_post_fix") or exit2_cnt),
                "l2_leaky_rubber_stamps": rubber_cnt,
                "rubber_stamp_escapes": rubber_escapes,
                "l2_latency_sinks": slow_cnt,
                "p50_ms": t_sig.get("p50_ms"),
                "p90_ms": t_sig.get("p90_ms"),
                "aci_output_bloat_count": bloat_cnt,
                "internal_doc_cli_drift_count": cli_drift_cnt,
                "internal_broken_refs_count": broken_refs_cnt,
                "internal_doc_cli_drift": cli_drift_list[:3] if summary else cli_drift_list,
                "internal_broken_refs": broken_refs_list[:3] if summary else broken_refs_list,
                "l3_wild_occurrences": int(t_sig.get("l3_wild_occurrences") or 0),
                "creative_selection_signal": creative_flag,
                "domain_failures": domain_fails,
                "skill_escapes": sk_escapes,
                "eval_counter_case_count": ev_counter_cnt,
                "eval_counter_case_ratio": ev_counter_ratio,
                "eval_escape_gap": ev_escape_gap,
            }
            if sk_name == "repo-kit":
                sig_dict["cross_repo_gate_findings"] = cr_findings_cnt
            skills_signals.append(
                {
                    "skill": sk_name,
                    "skill_version": meta.get("skill_version"),
                    "subtree_sha": meta.get("subtree_sha"),
                    "plugin_version": meta.get("plugin_version", "1.0.0"),
                    "last_modified_epoch": meta.get("last_modified_epoch"),
                    "scope": meta.get("scope", "global"),
                    "l0_lines": meta.get("l0_lines", 0),
                    "max_l1_lines": meta.get("max_l1_lines", 0),
                    "budget_ok": budget_ok,
                    "eval_case_count": eval_cases_cnt,
                    "eval_counter_case_count": ev_counter_cnt,
                    "eval_counter_case_ratio": ev_counter_ratio,
                    "eval_escape_gap": ev_escape_gap,
                    "leased": sk_name in leases["leased_skills"],
                    "lease_reasons": leases["lease_reasons"].get(sk_name, []),
                    "signals": sig_dict,
                    "triggered_aspects": triggered_aspects,
                    "priority_score": round(score, 2),
                }
            )

    skills_signals.sort(key=lambda item: (-float(item["priority_score"]), item["skill"]))
    elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
    probe_out: dict[str, Any] = {
        "ok": True,
        "schema": "skill-retro-probe/v1",
        "stage": "stage1_probe",
        "entry_mode": entry_mode,
        "skill_filter": norm_sk,
        "aspect_filter": aspect_filter,
        "fast": bool(fast),
        "duration_ms": elapsed_ms,
        "skills_probed": len(skills_signals),
        "active_leases": leases,
        "probe_role_tier": {
            "role": "explorer",
            "compatible_roles": ["explorer", "mechanical"],
            "tier": "worker",
            "models": ["mai-code-2-flash", "gpt-5.6-luna"],
        },
        "skills": skills_signals,
    }
    if cross_repo_gate_health is not None:
        probe_out["cross_repo_gate_health"] = cross_repo_gate_health
    return probe_out


def _parse_jev_triage_entry(
    raw_entry: Any,
    *,
    default_aspects: list[str],
    default_score: float,
) -> tuple[str | None, list[str] | None, float | None]:
    if isinstance(raw_entry, str):
        choice = raw_entry.strip().lower()
        if choice in {"skip", "partial", "full"}:
            return choice, (list(CANONICAL_ASPECTS) if choice == "full" else default_aspects), (0.85 if choice != "skip" else 0.1)
    if not isinstance(raw_entry, Mapping):
        return None, None, None
    inner = raw_entry.get("answers", raw_entry) if isinstance(raw_entry.get("answers"), Mapping) else raw_entry
    choice_val = inner.get("route_mode", inner.get("mode", inner.get("choice")))
    if isinstance(choice_val, Mapping):
        choice_val = choice_val.get("choice") or choice_val.get("mode")
    choice_str = str(choice_val or "").strip().lower() if choice_val is not None else ""
    noul_val = inner.get("noul", inner.get("score", inner.get("confidence")))
    if isinstance(noul_val, Mapping):
        noul_val = noul_val.get("noul", noul_val.get("score", noul_val.get("confidence")))
    noul_num: float | None = None
    if isinstance(noul_val, (int, float)) and not isinstance(noul_val, bool):
        noul_num = float(noul_val)
    raw_asp = inner.get("aspects")
    asp_list = _normalize_aspects(raw_asp, mode=choice_str or "partial") if raw_asp else None
    if choice_str in {"skip", "partial", "full"}:
        if noul_num is not None and noul_num < 0.30:
            return "skip", [], noul_num
        if noul_num is not None and 0.30 <= noul_num <= 0.70 and choice_str != "skip":
            # Grey zone falls back to deterministic aspect rules
            return None, asp_list, noul_num
        return choice_str, asp_list, (noul_num if noul_num is not None else (0.85 if choice_str != "skip" else 0.10))
    if noul_num is not None:
        if noul_num > 0.70:
            mode_out = "full" if len(default_aspects) >= 3 else "partial"
            return mode_out, (asp_list or default_aspects), noul_num
        if noul_num < 0.30:
            return "skip", [], noul_num
    return None, asp_list, noul_num


def triage_skills(
    *,
    probe_report: Mapping[str, Any] | None = None,
    skill: str | None = None,
    aspect: str | None = None,
    pick_one: bool = False,
    pick_top: int | None = None,
    use_jev: bool = True,
    mock_jev_json: Any = None,
    emit_briefs_dir: Path | None = None,
    coupled_with: Sequence[str] | str | None = None,
    relays: Sequence[Mapping[str, Any]] | None = None,
    summary: bool = False,
    warehouse_db: Path | None = None,
    copilot_db: Path | None = None,
    copilot_sessions_dir: Path | None = None,
    usage_jsonl: Path | None = None,
    trace_export_path: Path | None = None,
    handoff_root: Path | None = None,
    skills_root: Path | None = None,
    dev_root: Path | None = None,
    telemetry_shards_dir: Path | None = None,
    days: int = 30,
) -> dict[str, Any]:
    """Stage 2: Cheap Worker + Jev fast-thinking triage & Autonomous Daily Batch (--pick-top 3 default) or Pick-1."""
    norm_sk = _norm_skill(skill) if skill else None
    probe = (
        dict(probe_report)
        if probe_report is not None
        else probe_skills(
            skill=norm_sk,
            aspect=aspect,
            fast=True,
            summary=summary,
            warehouse_db=warehouse_db,
            copilot_db=copilot_db,
            copilot_sessions_dir=copilot_sessions_dir,
            usage_jsonl=usage_jsonl,
            trace_export_path=trace_export_path,
            handoff_root=handoff_root,
            skills_root=skills_root,
            dev_root=dev_root,
            telemetry_shards_dir=telemetry_shards_dir,
            days=days,
        )
    )
    parsed_mock: Any = None
    if mock_jev_json is not None:
        if isinstance(mock_jev_json, str):
            p_cand = Path(mock_jev_json).expanduser()
            parsed_mock = json.loads(p_cand.read_text(encoding="utf-8") if p_cand.is_file() else mock_jev_json)
        else:
            parsed_mock = mock_jev_json

    jev_mod = _load_jev_client() if (use_jev or parsed_mock is not None) else None
    decisions: list[dict[str, Any]] = []
    excluded_by_lease: list[dict[str, Any]] = []

    if norm_sk:
        effective_top_n = 1
    elif pick_one:
        effective_top_n = 1
    elif pick_top is not None:
        effective_top_n = max(1, int(pick_top))
    else:
        effective_top_n = 3
    live_jev_budget = max(6, effective_top_n * 2) if parsed_mock is None else 999
    live_jev_calls = 0

    all_probe_skills = list(probe.get("skills") or [])
    prefetched_live_jev: dict[str, Any] = {}
    if use_jev and parsed_mock is None and jev_mod is not None:
        live_candidates: list[dict[str, Any]] = []
        for cand_item in all_probe_skills:
            if float(cand_item.get("priority_score") or 0.0) <= 0.0 or bool(cand_item.get("leased")):
                continue
            live_candidates.append(cand_item)
            if len(live_candidates) >= live_jev_budget:
                break
        if live_candidates:
            questions = {
                "route_mode": {
                    "type": "choice",
                    "instructions": "Classify skill retro inspection scope from lightweight probe signals.",
                    "criteria": {
                        "skip": "Healthy or low-sample noise; keep skill asleep.",
                        "partial": "Localized friction in 1-2 dimensions (e.g. L1/L2 script or drawer); run targeted partial check.",
                        "full": "Multi-layer drift, creative homogenization, or >=3 dimensions triggered; run full 6-dimension check.",
                    },
                },
                "noul": {
                    "type": "noul",
                    "instructions": "Confidence that this skill warrants immediate Stage 3 retro evolution.",
                },
            }

            def _call_live_jev(c_item: dict[str, Any]) -> tuple[str, Any]:
                c_sk = str(c_item.get("skill") or "")
                try:
                    c_res = jev_mod.jev_decide(
                        {
                            "skill": c_sk,
                            "signals": c_item.get("signals"),
                            "triggered_aspects": list(c_item.get("triggered_aspects") or []),
                            "priority_score": float(c_item.get("priority_score") or 0.0),
                        },
                        questions,
                        agent_role="retro-loop",
                        timeout_sec=0.35,
                        falcon_only=True,
                    )
                except TypeError:
                    c_res = jev_mod.jev_decide(
                        {
                            "skill": c_sk,
                            "signals": c_item.get("signals"),
                            "triggered_aspects": list(c_item.get("triggered_aspects") or []),
                            "priority_score": float(c_item.get("priority_score") or 0.0),
                        },
                        questions,
                        agent_role="retro-loop",
                        timeout_sec=0.35,
                    )
                return c_sk, c_res

            with ThreadPoolExecutor(max_workers=min(6, len(live_candidates))) as pool:
                futs = {pool.submit(_call_live_jev, c_item): str(c_item.get("skill") or "") for c_item in live_candidates}
                t_deadline = time.monotonic() + 0.40
                for fut, c_sk in futs.items():
                    rem = max(0.01, t_deadline - time.monotonic())
                    with contextlib.suppress(Exception):
                        _, c_val = fut.result(timeout=rem)
                        prefetched_live_jev[c_sk] = c_val

    for idx, item in enumerate(all_probe_skills, 1):
        sk_name = str(item.get("skill") or "")
        leased = bool(item.get("leased"))
        lease_reasons = list(item.get("lease_reasons") or [])
        trig = list(item.get("triggered_aspects") or [])
        score = float(item.get("priority_score") or 0.0)

        # Deterministic baseline classification (0..3 honest cap — never force fixes on 0 issues)
        if score <= 0.0 or not trig:
            det_mode = "skip"
            det_aspects = []
            det_noul = 0.12
        elif norm_sk and sk_name == norm_sk:
            det_mode = "partial" if (aspect and len(trig) < 6) else ("full" if len(trig) >= 3 else "partial")
            det_aspects = trig if not aspect else _normalize_aspects(aspect, mode=det_mode)
            det_noul = max(0.80, min(0.99, 0.72 + score * 0.05))
        elif len(trig) >= 3 or ("creative" in trig and len(trig) >= 2):
            det_mode = "full"
            det_aspects = list(CANONICAL_ASPECTS)
            det_noul = min(0.98, round(0.72 + score * 0.04, 3))
        else:
            det_mode = "partial"
            det_aspects = trig
            det_noul = min(0.95, round(0.71 + score * 0.04, 3))

        decision_source = "deterministic_fallback"
        final_mode = det_mode
        final_aspects = det_aspects
        final_noul = det_noul

        # Check Jev mock or live jev_decide (bounded to top candidates for live network calls)
        jev_raw: Any = None
        if isinstance(parsed_mock, Mapping):
            if sk_name in parsed_mock:
                jev_raw = parsed_mock[sk_name]
            elif str(idx) in parsed_mock:
                jev_raw = parsed_mock[str(idx)]
            elif "route_mode" in parsed_mock or "choice" in parsed_mock or "answers" in parsed_mock:
                jev_raw = parsed_mock
        elif isinstance(parsed_mock, list) and idx - 1 < len(parsed_mock):
            jev_raw = parsed_mock[idx - 1]
        elif use_jev and jev_mod is not None and score > 0 and not leased and live_jev_calls < live_jev_budget:
            live_jev_calls += 1
            jev_raw = prefetched_live_jev.get(sk_name)

        if jev_raw is not None:
            j_mode, j_aspects, j_noul = _parse_jev_triage_entry(
                jev_raw,
                default_aspects=det_aspects or ["l2"],
                default_score=det_noul,
            )
            sig_map = item.get("signals") if isinstance(item.get("signals"), Mapping) else {}
            has_concrete_measured_telemetry = bool(
                int(sig_map.get("l2_exit2_roadblocks") or 0) >= 3
                or (int(sig_map.get("l1_unwired_drawers") or 0) >= 1 and int(sig_map.get("domain_failures") or 0) >= 1)
                or int(sig_map.get("l0_trigger_misses") or 0) >= 5
                or score >= 20.0
            )
            if j_mode == "skip" and parsed_mock is None and has_concrete_measured_telemetry and det_mode in {"partial", "full"}:
                # Never let an uncalibrated generic live Jev noul<0.30 wipe out concrete measured P1/P2 telemetry anomalies
                final_mode = det_mode
                final_aspects = det_aspects
                final_noul = max(det_noul, float(j_noul or 0.0))
                decision_source = "jev_decide:telemetry_floor_preserved"
            elif j_mode is not None:
                final_mode = j_mode
                final_aspects = (
                    list(CANONICAL_ASPECTS)
                    if j_mode == "full"
                    else ([] if j_mode == "skip" else (j_aspects or det_aspects or ["l2"]))
                )
                final_noul = j_noul if j_noul is not None else det_noul
                decision_source = "jev_mock" if parsed_mock is not None else "jev_decide"

        # Lock 2: Active Lease Exclusion (unless user explicitly requested this single skill)
        if leased and not norm_sk:
            excluded_by_lease.append({"skill": sk_name, "lease_reasons": lease_reasons, "would_have_been": final_mode})
            final_mode = "skip"
            final_aspects = []
            decision_source = f"{decision_source}:excluded_active_lease"

        decisions.append(
            {
                "skill": sk_name,
                "decision": final_mode,
                "mode": final_mode,
                "aspects": final_aspects,
                "noul": round(float(final_noul), 3),
                "priority_score": score,
                "decision_source": decision_source,
                "leased": leased,
                "lease_reasons": lease_reasons,
                "owned_paths": (
                    [f"skills/{sk_name}/**", f"scripts/test_{sk_name.replace('-', '_')}_*.py"]
                    if final_mode in {"partial", "full"}
                    else []
                ),
            }
        )

    actionable = [d for d in decisions if d["decision"] in {"partial", "full"}]
    actionable.sort(key=lambda d: (-float(d["priority_score"]), -float(d["noul"]), d["skill"]))

    if norm_sk:
        effective_top_n = 1
    elif pick_one:
        effective_top_n = 1
    elif pick_top is not None:
        effective_top_n = max(1, int(pick_top))
    else:
        effective_top_n = 3
    selected_targets = actionable[:effective_top_n]
    picked_skill_entry = selected_targets[0] if selected_targets else None
    today_cst = _cst_today_str()
    triage_status = "ACTIONABLE" if selected_targets else "HEALTHY_NO_ISSUES"
    triage_status_msg = (
        f"Selected {len(selected_targets)}/{effective_top_n} skill(s) for parallel M1 TL deep reasoning"
        if selected_targets
        else "healthy / 0 issues found (no changes needed)"
    )

    handoff_root_for_plan = emit_briefs_dir or handoff_root or DEFAULT_HANDOFF_ROOT
    preliminary_plan_items = [
        {
            "skill": t["skill"],
            "mode": t["mode"],
            "aspects": t["aspects"],
            "owned_paths": t["owned_paths"],
        }
        for t in selected_targets
    ]
    m2_dispatch_plan = build_m2_dispatch_plan(
        preliminary_plan_items,
        handoff_root=handoff_root_for_plan,
        dev_root=dev_root,
    )
    m1_parallel_dispatches = [
        {
            "skill": d["skill"],
            "goal_id": d["goal_id"],
            "m1_tl_agent": d["m1_tl_agent_name"],
            "m1_tl_agent_name": d["m1_tl_agent_name"],
            "handoff_spec": d["spec_md"],
            "spec_md": d["spec_md"],
            "isolated_worktree": d["worktree_dir"],
            "worktree_dir": d["worktree_dir"],
            "mode": d["mode"],
            "aspects": d["aspects"],
            "owned_paths": d["owned_paths"],
            "dispatch_cmd": d["herdr_dispatch_cmd"],
            "herdr_dispatch_cmd": d["herdr_dispatch_cmd"],
            "rollup_outcomes": ["FIXED_AND_MERGED", "HEALTHY_NO_ISSUES", "CROSS_SKILL_RELAY"],
        }
        for d in m2_dispatch_plan["parallel_m1_dispatches"]
    ]

    result: dict[str, Any] = {
        "ok": True,
        "schema": "skill-retro-triage/v1",
        "stage": "stage2_triage",
        "status": triage_status,
        "status_message": triage_status_msg,
        "orchestration_topology": "1_M2 -> K_PARALLEL_M1_TLS (K=0..3, 1 M1 per skill) -> M2_ROLLUP",
        "m1_tls_to_spawn": len(selected_targets),
        "writers_to_spawn": len(selected_targets),
        "autonomous_daily_execution": {
            "owner_approval_gate_required": False,
            "daily_batch_default_pick_top": 3,
            "pick_top_is_upper_cap_not_quota": True,
            "one_skill_equals_one_independent_m1_tl": True,
            "zero_issue_policy": "HEALTHY_NO_ISSUES (spawn 0 M1 TLs / 0 Writers; never invent or force cosmetic edits)",
            "m1_healthy_early_exit_policy": (
                "If an M1 TL's deep reasoning finds its assigned skill is healthy/benign, "
                "M1 TL writes HEALTHY_NO_ISSUES receipt and returns to M2 with 0 Writers/Reviewers spawned"
            ),
            "pipeline": "1 M2 -> 1 Cheap Probe Worker -> K (0..3) Parallel M1 TLs (deep reasoning per skill) -> Rollup to Single M2 -> Daily Summary Issue",
            "daily_summary_issue_title": f"[Skill Retro {today_cst}] Autonomous Evolution Summary ({len(selected_targets)} skill(s))",
        },
        "m1_parallel_dispatches": m1_parallel_dispatches,
        "worker_role_slot": {
            "role": "explorer",
            "compatible_roles": ["explorer", "mechanical"],
            "tier": "worker",
            "steering_tokens_burned": 0,
            "dispatch_cmd": "skills/team-lead/scripts/herdr-dispatch.sh --role explorer --name skill-retro-probe-r1",
        },
        "pick_one": bool(effective_top_n == 1),
        "pick_top": effective_top_n,
        "picked_skill": picked_skill_entry["skill"] if picked_skill_entry else None,
        "picked_mode": picked_skill_entry["mode"] if picked_skill_entry else "skip",
        "picked_aspects": picked_skill_entry["aspects"] if picked_skill_entry else [],
        "selected_skills": selected_targets,
        "excluded_by_active_lease": excluded_by_lease,
        "anti_collision_locks": {
            "lock1_strict_disjoint_owned_paths": True,
            "lock2_active_lease_exclusion": True,
            "lock3_wave1_disjoint_wave2_relays": True,
        },
        "m2_dispatch_plan": m2_dispatch_plan,
        "wave_plan": {
            "wave_1_disjoint_hosts": [
                {
                    "skill": t["skill"],
                    "goal_id": f"skill-retro-{t['skill']}",
                    "m1_tl_agent_name": f"skill-retro-{t['skill']}-goal-tl-r1",
                    "mode": t["mode"],
                    "aspects": t["aspects"],
                    "owned_paths": t["owned_paths"],
                }
                for t in selected_targets
            ],
            "wave_2_cross_skill_relays": list(relays or []),
        },
        "decisions": decisions[:5] if summary else decisions,
    }

    if emit_briefs_dir is not None and selected_targets:
        stage3_runs: list[dict[str, Any]] = []
        combined_brief_records: list[dict[str, Any]] = []
        combined_specs: list[dict[str, Any]] = []
        combined_relays: list[dict[str, Any]] = []
        combined_w1: list[dict[str, Any]] = []
        combined_w2: list[dict[str, Any]] = []
        for target_item in selected_targets:
            diag = compute_skill_retro(
                warehouse_db=warehouse_db,
                copilot_db=copilot_db,
                copilot_sessions_dir=copilot_sessions_dir,
                usage_jsonl=usage_jsonl,
                trace_export_path=trace_export_path,
                handoff_root=handoff_root,
                skills_root=skills_root,
                dev_root=dev_root,
                telemetry_shards_dir=telemetry_shards_dir,
                skill=target_item["skill"],
                emit_briefs_dir=emit_briefs_dir,
                mode=target_item["mode"],
                aspects=target_item["aspects"],
                coupled_with=coupled_with,
                relays=relays,
                days=days,
                mirror_roi_snapshot=False,
            )
            eb = diag.get("emitted_briefs") or {}
            stage3_runs.append(eb)
            combined_brief_records.extend(eb.get("briefs") or [])
            combined_specs.extend(eb.get("m1_goal_specs") or [])
            combined_relays.extend(eb.get("cross_skill_relays") or [])
            combined_w1.extend((eb.get("wave_plan") or {}).get("wave_1_disjoint_hosts") or [])
            combined_w2.extend((eb.get("wave_plan") or {}).get("wave_2_cross_skill_relays") or [])
        updated_m2_plan = build_m2_dispatch_plan(
            combined_brief_records,
            handoff_root=emit_briefs_dir,
            dev_root=dev_root,
        )
        result["m2_dispatch_plan"] = updated_m2_plan
        result["emitted_briefs"] = (
            stage3_runs[0]
            if len(stage3_runs) == 1
            else {
                "briefs_dir": str(emit_briefs_dir / "briefs" if emit_briefs_dir.name != "briefs" else emit_briefs_dir),
                "handoff_root": str(emit_briefs_dir.parent if emit_briefs_dir.name == "briefs" else emit_briefs_dir),
                "status": "ACTIONABLE" if combined_brief_records else "HEALTHY_NO_ISSUES",
                "orchestration_topology": "1_M2 -> K_PARALLEL_M1_TLS (K=0..3) -> M2_ROLLUP",
                "m1_goals_to_dispatch": len(combined_brief_records),
                "m1_tls_to_spawn": len(combined_brief_records),
                "writers_spawned": len(combined_brief_records),
                "role_slots_reused": ["M1-TL", "writer", "reviewer"] if combined_brief_records else [],
                "new_agent_roles_created": 0,
                "count": len(combined_brief_records),
                "briefs": combined_brief_records,
                "m1_goal_specs": combined_specs,
                "m2_dispatch_plan": updated_m2_plan,
                "cross_skill_relays": combined_relays,
                "wave_plan": {
                    "wave_1_disjoint_hosts": combined_w1,
                    "wave_2_cross_skill_relays": combined_w2,
                },
            }
        )

    return result


_VALID_GIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_VALID_PR_REF_RE = re.compile(r"^(?:https://github\.com/[^/\s]+/[^/\s]+/pull/\d+|#?\d+)$")


def _verify_m1_receipt_grounding(
    receipt: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    verify_grounding: bool = False,
) -> tuple[bool, list[str]]:
    """Verify M1 receipt-to-trace faithfulness (`receipt_grounded`, `grounding_issues` / `grounding_errors`)."""
    status = str(receipt.get("status") or "").strip().upper()
    issues: list[str] = []
    if status != "FIXED_AND_MERGED":
        return True, issues

    sha_val = str(receipt.get("merge_sha") or receipt.get("commit_sha") or "").strip()
    pr_val = str(receipt.get("pr_url") or receipt.get("pr") or (f"#{receipt['pr_number']}" if receipt.get("pr_number") else "")).strip()

    if not sha_val:
        issues.append("missing_commit_or_merge_sha")
    elif not _VALID_GIT_SHA_RE.fullmatch(sha_val):
        issues.append(f"invalid_sha_format:{sha_val}")

    if not pr_val:
        issues.append("missing_pr_reference")
    elif not _VALID_PR_REF_RE.fullmatch(pr_val):
        issues.append(f"invalid_pr_reference:{pr_val}")

    eff_repo: Path | None = repo_root
    if eff_repo is None and verify_grounding:
        default_repo, _ = _find_git_root_for_path(DEFAULT_SKILLS_ROOT)
        eff_repo = default_repo or DEFAULT_SKILLS_ROOT.parent

    if eff_repo is not None or verify_grounding:
        if eff_repo is None or not _safe_is_dir(eff_repo):
            issues.append(f"invalid_or_missing_repo_root:{eff_repo}")
        elif sha_val and _VALID_GIT_SHA_RE.fullmatch(sha_val):
            clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            try:
                proc = subprocess.run(
                    ["git", "--no-optional-locks", "cat-file", "-t", sha_val],
                    cwd=eff_repo,
                    env=clean_env,
                    capture_output=True,
                    text=True,
                    timeout=2.5,
                    check=False,
                )
                obj_type = proc.stdout.strip()
                if proc.returncode != 0 or obj_type not in {"commit", "tag"}:
                    issues.append(f"commit_not_found_in_git:{sha_val}")
            except (OSError, subprocess.SubprocessError):
                issues.append(f"git_verification_error:{sha_val}")

    return len(issues) == 0, issues


def record_m1_retro_receipt(
    *,
    skill: str,
    status: str,
    handoff_root: Path | None = None,
    summary: str = "",
    pr_url: str | None = None,
    pr_number: int | None = None,
    commit_sha: str | None = None,
    merge_sha: str | None = None,
    writers_spawned: int | None = None,
    reviewers_spawned: int | None = None,
    relay_to: str | None = None,
    relay_requirement: str = "",
    mode: str = "full",
    aspects: Sequence[str] | str | None = None,
    observation_notes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Write a canonical M1 TL deep-reasoning receipt (`FIXED_AND_MERGED` | `HEALTHY_NO_ISSUES` | `CROSS_SKILL_RELAY`) for M2 Rollup."""
    norm_sk = _norm_skill(skill)
    if not norm_sk:
        raise ValueError("--skill is required to record an M1 retro receipt.")
    norm_status = str(status or "").strip().upper()
    canonical_statuses = {"FIXED_AND_MERGED", "HEALTHY_NO_ISSUES", "CROSS_SKILL_RELAY"}
    if norm_status not in canonical_statuses:
        raise ValueError(f"Invalid M1 receipt status {status!r}; must be one of {sorted(canonical_statuses)}.")

    resolved_root = (handoff_root or DEFAULT_HANDOFF_ROOT).expanduser()
    safe_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", norm_sk).strip("-") or "skill"
    goal_dir = _resolve_per_skill_goal_handoff_dir(resolved_root, safe_slug)
    receipts_dir = goal_dir / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipts_dir / "m1-tl-receipt.json"

    norm_mode = "partial" if str(mode or "full").strip().lower() == "partial" else "full"
    aspect_list = _normalize_aspects(aspects, mode=norm_mode)
    if norm_status == "HEALTHY_NO_ISSUES":
        eff_writers = 0 if writers_spawned is None else int(writers_spawned)
        eff_reviewers = 0 if reviewers_spawned is None else int(reviewers_spawned)
        default_summary = f"M1 TL deep reasoning confirmed `{norm_sk}` signal is healthy/benign (0 Writers/Reviewers spawned)."
    elif norm_status == "CROSS_SKILL_RELAY":
        eff_writers = 0 if writers_spawned is None else int(writers_spawned)
        eff_reviewers = 0 if reviewers_spawned is None else int(reviewers_spawned)
        default_summary = f"M1 TL emitted Wave 2 Cross-Skill Relay from `{norm_sk}` to `{_norm_skill(relay_to or '')}`."
    else:
        eff_writers = 1 if writers_spawned is None else int(writers_spawned)
        eff_reviewers = 1 if reviewers_spawned is None else int(reviewers_spawned)
        default_summary = f"M1 TL fixed `{norm_sk}` via Writer -> Reviewer, passed Positive + Counter-Case Replay, and merged PR."

    eff_sha = commit_sha or merge_sha
    payload: dict[str, Any] = {
        "ok": True,
        "schema": "skill-retro-m1-receipt/v1",
        "skill": norm_sk,
        "goal_id": f"skill-retro-{safe_slug}",
        "m1_tl_agent_name": f"skill-retro-{safe_slug}-goal-tl-r1",
        "status": norm_status,
        "mode": norm_mode,
        "aspects": aspect_list,
        "writers_spawned": eff_writers,
        "reviewers_spawned": eff_reviewers,
        "pr_url": pr_url,
        "pr_number": pr_number,
        "commit_sha": eff_sha,
        "merge_sha": merge_sha or eff_sha,
        "relay_to": _norm_skill(relay_to) if relay_to else None,
        "relay_requirement": relay_requirement,
        "summary": summary.strip() or default_summary,
        "observation_notes": list(observation_notes or []),
        "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "receipt_path": str(receipt_path),
    }
    receipt_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def rollup_m1_retro_receipts(
    *,
    handoff_root: Path | None = None,
    skills: Sequence[str] | str | None = None,
    observation_pool: Sequence[Mapping[str, Any]] | None = None,
    target_date: str | None = None,
    output_path: Path | None = None,
    repo_root: Path | None = None,
    verify_grounding: bool = False,
) -> dict[str, Any]:
    """Aggregate K (0..3) independent M1 TL receipts (`FIXED_AND_MERGED` | `HEALTHY_NO_ISSUES` | `CROSS_SKILL_RELAY`) back to the single M2."""
    resolved_root = (handoff_root or DEFAULT_HANDOFF_ROOT).expanduser()
    date_str = str(target_date or _cst_today_str()).strip()
    filter_skills: list[str] = []
    if isinstance(skills, str):
        filter_skills = [_norm_skill(s) for s in re.split(r"[,;\s]+", skills) if _norm_skill(s)]
    elif isinstance(skills, Sequence):
        for item in skills:
            for s in re.split(r"[,;\s]+", str(item)):
                if _norm_skill(s):
                    filter_skills.append(_norm_skill(s))

    candidate_dirs: list[Path] = []
    if filter_skills:
        for sk in filter_skills:
            safe_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", sk).strip("-") or "skill"
            candidate_dirs.append(_resolve_per_skill_goal_handoff_dir(resolved_root, safe_slug))
    elif _probe_readable_dir(resolved_root):
        if resolved_root.name.startswith("skill-retro-") and _safe_is_file(resolved_root / "spec.md"):
            candidate_dirs.append(resolved_root)
        else:
            for child in _safe_glob(resolved_root, "skill-retro-*"):
                if _safe_is_dir(child) and child.name not in {"skill-retro-m2-3m1-rollup"}:
                    candidate_dirs.append(child)

    m1_receipts: list[dict[str, Any]] = []
    relays_collected: list[dict[str, Any]] = []
    for gdir in candidate_dirs:
        if not _probe_readable_dir(gdir):
            continue
        sk_guess = gdir.name[len("skill-retro-") :] if gdir.name.startswith("skill-retro-") else gdir.name
        r_file = gdir / "receipts" / "m1-tl-receipt.json"
        loaded_receipt: dict[str, Any] | None = None
        if _safe_is_file(r_file):
            try:
                raw_r = json.loads(r_file.read_text(encoding="utf-8"))
                if isinstance(raw_r, dict):
                    loaded_receipt = dict(raw_r)
            except (OSError, ValueError, TypeError):
                loaded_receipt = None
        if loaded_receipt is None:
            # Fallback: inspect ledger.md or relay JSONs inside goal handoff dir
            ledger_file = gdir / "ledger.md"
            try:
                ledger_txt = ledger_file.read_text(encoding="utf-8") if _safe_is_file(ledger_file) else ""
            except OSError:
                ledger_txt = ""
            relay_files = _safe_glob(gdir / "briefs", "skill-retro-relay-*.json")
            if "HEALTHY_NO_ISSUES" in ledger_txt:
                loaded_receipt = {
                    "skill": sk_guess,
                    "goal_id": gdir.name,
                    "m1_tl_agent_name": f"{gdir.name}-goal-tl-r1",
                    "status": "HEALTHY_NO_ISSUES",
                    "writers_spawned": 0,
                    "reviewers_spawned": 0,
                    "summary": f"M1 TL deep reasoning found `{sk_guess}` healthy (`0` Writers spawned).",
                }
            elif "PR_MERGED" in ledger_txt or "GOAL_DONE" in ledger_txt or "FIXED_AND_MERGED" in ledger_txt:
                pr_match = re.search(r"(https://github\.com/\S+/pull/\d+|#\d+)", ledger_txt)
                sha_match = re.search(r"\b([0-9a-f]{7,40})\b", ledger_txt)
                loaded_receipt = {
                    "skill": sk_guess,
                    "goal_id": gdir.name,
                    "m1_tl_agent_name": f"{gdir.name}-goal-tl-r1",
                    "status": "FIXED_AND_MERGED",
                    "writers_spawned": 1,
                    "reviewers_spawned": 1,
                    "pr_url": pr_match.group(1) if pr_match else None,
                    "commit_sha": sha_match.group(1) if sha_match else None,
                    "summary": f"M1 TL completed Writer -> Reviewer fix and merged PR for `{sk_guess}`.",
                }
            elif relay_files:
                with contextlib.suppress(OSError, ValueError, TypeError):
                    rel_data = json.loads(relay_files[0].read_text(encoding="utf-8"))
                    loaded_receipt = {
                        "skill": sk_guess,
                        "goal_id": gdir.name,
                        "m1_tl_agent_name": f"{gdir.name}-goal-tl-r1",
                        "status": "CROSS_SKILL_RELAY",
                        "writers_spawned": 0,
                        "reviewers_spawned": 0,
                        "relay_to": rel_data.get("to_skill"),
                        "relay_requirement": rel_data.get("requirement"),
                        "summary": f"M1 TL scheduled Wave 2 Cross-Skill Relay `{sk_guess}` -> `{rel_data.get('to_skill')}`.",
                    }
        if loaded_receipt is not None:
            is_grounded, g_issues = _verify_m1_receipt_grounding(
                loaded_receipt,
                repo_root=repo_root,
                verify_grounding=verify_grounding,
            )
            loaded_receipt["receipt_grounded"] = is_grounded
            loaded_receipt["grounding_issues"] = g_issues
            loaded_receipt["grounding_errors"] = g_issues
            if loaded_receipt.get("status") == "FIXED_AND_MERGED" and not is_grounded:
                loaded_receipt["claimed_status"] = "FIXED_AND_MERGED"
                loaded_receipt["effective_status"] = "UNGROUNDED_RECEIPT"
            else:
                loaded_receipt["effective_status"] = loaded_receipt.get("status")
            m1_receipts.append(loaded_receipt)
        for rf in _safe_glob(gdir / "briefs", "skill-retro-relay-*.json"):
            try:
                relays_collected.append(json.loads(rf.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                pass

    for rf in _safe_glob(resolved_root / "briefs", "skill-retro-relay-*.json"):
        try:
            rel_obj = json.loads(rf.read_text(encoding="utf-8"))
            if not any(r.get("relay_id") == rel_obj.get("relay_id") for r in relays_collected):
                relays_collected.append(rel_obj)
        except (OSError, ValueError, TypeError):
            pass

    fixed_items = [r for r in m1_receipts if r.get("status") == "FIXED_AND_MERGED" and r.get("receipt_grounded", True)]
    ungrounded_items = [r for r in m1_receipts if r.get("status") == "FIXED_AND_MERGED" and not r.get("receipt_grounded", True)]
    healthy_items = [r for r in m1_receipts if r.get("status") == "HEALTHY_NO_ISSUES"]
    relay_items = [r for r in m1_receipts if r.get("status") == "CROSS_SKILL_RELAY"]
    overall_status = (
        "FIXED_AND_MERGED"
        if fixed_items
        else (
            "UNGROUNDED_RECEIPT"
            if ungrounded_items
            else ("CROSS_SKILL_RELAY" if (relay_items or relays_collected) else "HEALTHY_NO_ISSUES")
        )
    )
    all_grounding_errors = [
        f"{r.get('skill')}:{err}"
        for r in m1_receipts
        for err in (r.get("grounding_issues") or [])
    ]

    md_lines = [
        f"## `[Skill Retro {date_str}]` `1 M2 -> K ({len(m1_receipts)}) M1 TLs -> Single M2 Rollup` Summary",
        "",
        "- **Topology**: `1 M2 Coordinator -> 1 Cheap Probe Worker -> K (0..3) Independent M1 TLs -> Single M2 Rollup`",
        f"- **M1 TLs Spawned (`K`)**: `{len(m1_receipts)}` (max cap `3`, never a forced quota)",
        f"- **Rollup Breakdown**: `FIXED_AND_MERGED={len(fixed_items)}` · `HEALTHY_NO_ISSUES={len(healthy_items)}` · `CROSS_SKILL_RELAY={len(relay_items) or len(relays_collected)}` · `UNGROUNDED={len(ungrounded_items)}`",
        "",
        "| Skill | Independent M1 TL | Outcome | Grounded | Writers Spawned | PR / Relay / Evidence |",
        "|---|---|---|---|---|---|",
    ]
    if m1_receipts:
        for rec in m1_receipts:
            ev_cell = str(rec.get("pr_url") or (f"relay->{rec.get('relay_to')}" if rec.get("relay_to") else rec.get("summary") or "—")).replace("|", "\\|")
            grounded_cell = "yes" if rec.get("receipt_grounded", True) else f"NO ({','.join(rec.get('grounding_issues') or [])})"
            md_lines.append(
                f"| `{rec.get('skill')}` | `{rec.get('m1_tl_agent_name')}` | `{rec.get('effective_status') or rec.get('status')}` | `{grounded_cell}` | `{rec.get('writers_spawned', 0)}` | {ev_cell} |"
            )
    else:
        md_lines.append("| — | `0 M1 TLs spawned` | `HEALTHY_NO_ISSUES` | `yes` | `0` | Probe Worker found 0 issues; no M1 TL or Writer spawned |")

    obs_list = list(observation_pool or [])
    if obs_list:
        md_lines.extend(["", "### Observation Pool (Held Below Threshold)", ""])
        for ob in obs_list[:6]:
            if isinstance(ob, Mapping):
                md_lines.append(f"- `{ob.get('skill', 'unknown')}` (`{ob.get('category', 'signal')}`): {ob.get('title', '')}")

    summary_md = "\n".join(md_lines).rstrip() + "\n"
    rollup_payload: dict[str, Any] = {
        "ok": True,
        "schema": "skill-retro-m2-rollup/v1",
        "date": date_str,
        "topology": "1_m2 -> 1_cheap_probe_worker -> 0..3_parallel_m1_tls -> 1_m2_rollup",
        "status": overall_status,
        "receipt_grounded": len(ungrounded_items) == 0,
        "grounding_issues": all_grounding_errors,
        "grounding_errors": all_grounding_errors,
        "handoff_root": str(resolved_root),
        "m1_tls_spawned": len(m1_receipts),
        "writers_spawned_total": sum(int(r.get("writers_spawned") or 0) for r in m1_receipts),
        "counts": {
            "fixed_and_merged": len(fixed_items),
            "ungrounded_receipts": len(ungrounded_items),
            "healthy_no_issues": len(healthy_items),
            "cross_skill_relay": max(len(relay_items), len(relays_collected)),
            "observation_pool": len(obs_list),
        },
        "m1_receipts": m1_receipts,
        "cross_skill_relays": relays_collected,
        "observation_pool": obs_list,
        "daily_summary_issue_title": f"[Skill Retro {date_str}] Autonomous Evolution Summary ({len(fixed_items)} fixed, {len(healthy_items)} healthy)",
        "markdown_summary": summary_md,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(rollup_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rollup_payload


class _SelfDisclosingArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(
            f"[skill-retro] CLI error: {message}\n"
            "  Fix: run `python3 skills/skill-retro/scripts/skill_retro.py {probe,triage,diagnose,emit-briefs,emit-relay,record-m1-receipt,rollup,roi} --help`\n"
            "  Reference: skills/skill-retro/references/progressive-evolution-playbook.md\n"
        )
        raise SystemExit(2)


def _add_common_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--skill", "--target-skill", dest="skill", default=None, help="Target skill name (or single-skill direct mode).")
    p.add_argument("--warehouse-db", type=Path, default=None)
    p.add_argument("--copilot-db", type=Path, default=None)
    p.add_argument("--copilot-sessions-dir", type=Path, default=None)
    p.add_argument("--usage-jsonl", type=Path, default=None)
    p.add_argument("--trace-export", type=Path, default=None)
    p.add_argument("--handoff-root", type=Path, default=None)
    p.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    p.add_argument("--dev-root", type=Path, default=DEFAULT_DEV_ROOT)
    p.add_argument("--icloud-sync-dir", type=Path, default=None)
    p.add_argument("--telemetry-shards-dir", type=Path, default=None)
    p.add_argument("--isolated-fixtures", action="store_true", default=False, help="Treat custom --handoff-root as isolated test fixture mode and disable default home telemetry sources.")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--summary", action="store_true", default=False, help="Bound output arrays for compact ACI consumption.")
    p.add_argument("--json", action="store_true", default=True)


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    known_subcommands = {
        "probe",
        "triage",
        "diagnose",
        "uptake-check",
        "emit-briefs",
        "emit-relay",
        "record-m1-receipt",
        "rollup",
        "skill-retro",
        "roi",
        "skill-roi",
    }

    if not args_list or args_list[0] in {"-h", "--help"}:
        top = _SelfDisclosingArgumentParser(
            prog="skill_retro.py",
            description="3-Stage Skill Progressive Self-Evolution: Fast Probe -> Cheap Worker + Jev Triage -> 1 M2 -> K (0..3) Parallel M1 TLs -> M2 Rollup.",
        )
        top.add_argument(
            "command",
            nargs="?",
            choices=sorted(known_subcommands),
            help="Subcommand: probe | triage | diagnose | uptake-check | emit-briefs | emit-relay | record-m1-receipt | rollup | roi",
        )
        _add_common_source_args(top)
        top.parse_args(args_list)
        return 0

    cmd = args_list[0]
    rest = args_list[1:]
    if cmd.startswith("-"):
        cmd = "diagnose"
        rest = args_list
    elif cmd not in known_subcommands:
        sys.stderr.write(
            f"[skill-retro] Unknown subcommand: {cmd!r}\n"
            "  Valid subcommands: probe, triage, diagnose, uptake-check, emit-briefs, emit-relay, record-m1-receipt, rollup, roi\n"
            "  Reference: skills/skill-retro/references/progressive-evolution-playbook.md\n"
        )
        raise SystemExit(2)

    if cmd == "uptake-check":
        p = _SelfDisclosingArgumentParser(prog="skill_retro.py uptake-check")
        p.add_argument("--skill", "--skills", "--target-skill", dest="skill", default=None, help="Comma-separated expected skill(s) to evaluate.")
        p.add_argument("--triggered-skills", default=None, help="Comma-separated triggered skills observed in trace (defaults to expected skills).")
        p.add_argument("--target-dir", "--worktree", dest="target_dir", type=Path, default=None, help="Target workspace/directory to inspect.")
        p.add_argument(
            "--scenario",
            default="skills_available_unmentioned",
            choices=sorted(VALID_SKILL_SCENARIOS),
            help="3-condition scenario: skills_unavailable | skills_available_unmentioned | skills_available_mentioned.",
        )
        p.add_argument("--uptake-map", dest="uptake_map_path", type=Path, default=None, help="Custom uptake_map.json path.")
        p.add_argument("--baseline-pct", type=float, default=None, help="Baseline (skills_unavailable) evaluator score percentage.")
        p.add_argument("--skill-pct", type=float, default=None, help="Skill-enabled evaluator score percentage.")
        p.add_argument("--build-success", action="store_true", default=None)
        p.add_argument("--strict", action="store_true", default=False, help="Exit 1 if any hard uptake check fails.")
        p.add_argument("--json", action="store_true", default=True)
        ns = p.parse_args(rest)
        out = evaluate_skill_uptake(
            skills=ns.skill,
            triggered_skills=ns.triggered_skills,
            target_dir=ns.target_dir,
            scenario=ns.scenario,
            uptake_map_path=ns.uptake_map_path,
            baseline_evaluator_pct=ns.baseline_pct,
            skill_evaluator_pct=ns.skill_pct,
            build_success=ns.build_success,
        )
        if not out["ok"]:
            drawers = sorted(
                {
                    str(c.get("remediation_drawer"))
                    for c in (out.get("static_checks") or [])
                    if c.get("status") == UPTAKE_STATUS_FAILED and c.get("remediation_drawer")
                }
            )
            if drawers:
                sys.stderr.write(
                    f"[skill-retro] Hard uptake check failure(s) detected. "
                    f"Read {', '.join(drawers[:3])} for remediation rules.\n"
                )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1 if (ns.strict and not out["ok"]) else 0

    if cmd == "probe":
        p = _SelfDisclosingArgumentParser(prog="skill_retro.py probe")
        _add_common_source_args(p)
        p.add_argument("--aspect", "--aspects", dest="aspect", default=None, help="Optional aspect filter (l0,l1,l2,l3,creative,eval).")
        p.add_argument("--fast", action="store_true", default=True, help="Sub-second watermark/signal scan (default True).")
        p.add_argument("--deep", dest="fast", action="store_false", help="Force full session/DB scan during probe.")
        ns = p.parse_args(rest)
        out = probe_skills(
            skill=ns.skill,
            aspect=ns.aspect,
            fast=ns.fast,
            summary=ns.summary,
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            copilot_sessions_dir=ns.copilot_sessions_dir,
            usage_jsonl=ns.usage_jsonl,
            trace_export_path=ns.trace_export,
            handoff_root=ns.handoff_root,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            telemetry_shards_dir=ns.telemetry_shards_dir,
            days=ns.days,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if cmd == "triage":
        p = _SelfDisclosingArgumentParser(prog="skill_retro.py triage")
        _add_common_source_args(p)
        p.add_argument("--aspect", "--aspects", dest="aspect", default=None)
        p.add_argument("--pick-one", action="store_true", default=False, help="Pick the single top actionable skill.")
        p.add_argument("--pick-top", type=int, default=None, help="Pick top N mutually disjoint skills for parallel Wave 1 (default 3 in daily autonomous batch).")
        p.add_argument("--use-jev", action="store_true", default=True, help="Use jev_client.py jev_decide with deterministic fallback.")
        p.add_argument("--no-jev", dest="use_jev", action="store_false")
        p.add_argument("--mock-jev-json", default=None, help="Inline JSON or file path for deterministic Jev testing.")
        p.add_argument("--emit-briefs", "--handoff-dir", dest="emit_briefs", type=Path, default=None)
        p.add_argument("--coupled-with", default=None, help="Explicitly lock a coupled Guest Skill into the same worktree.")
        p.add_argument("--relay-to", default=None, help="Emit a Wave 2 Cross-Skill Relay Brief to this Guest Skill.")
        p.add_argument("--relay-requirement", default="", help="Requirement text for --relay-to.")
        ns = p.parse_args(rest)
        relays_list = (
            [{"from_skill": ns.skill or "", "to_skill": ns.relay_to, "requirement": ns.relay_requirement}]
            if ns.relay_to
            else None
        )
        out = triage_skills(
            skill=ns.skill,
            aspect=ns.aspect,
            pick_one=ns.pick_one,
            pick_top=ns.pick_top,
            use_jev=ns.use_jev,
            mock_jev_json=ns.mock_jev_json,
            emit_briefs_dir=ns.emit_briefs,
            coupled_with=ns.coupled_with,
            relays=relays_list,
            summary=ns.summary,
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            copilot_sessions_dir=ns.copilot_sessions_dir,
            usage_jsonl=ns.usage_jsonl,
            trace_export_path=ns.trace_export,
            handoff_root=ns.handoff_root,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            telemetry_shards_dir=ns.telemetry_shards_dir,
            days=ns.days,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if cmd == "emit-relay":
        p = _SelfDisclosingArgumentParser(prog="skill_retro.py emit-relay")
        p.add_argument("--from", "--from-skill", "--host", dest="from_skill", required=True, help="Host skill originating the relay.")
        p.add_argument("--to", "--to-skill", "--guest", dest="to_skill", required=True, help="Guest skill to update in Wave 2.")
        p.add_argument("--requirement", required=True, help="Concrete contract/CLI change required in Guest skill.")
        p.add_argument("--evidence-summary", default="", help="Optional observed failure/telemetry summary.")
        p.add_argument("--wave", type=int, default=2, help="Target wave number (default 2).")
        p.add_argument("--emit-briefs", "--handoff-dir", dest="emit_briefs", type=Path, required=True)
        p.add_argument("--json", action="store_true", default=True)
        ns = p.parse_args(rest)
        try:
            out = emit_cross_skill_relay(
                from_skill=ns.from_skill,
                to_skill=ns.to_skill,
                requirement=ns.requirement,
                emit_dir=ns.emit_briefs,
                evidence_summary=ns.evidence_summary,
                wave=ns.wave,
            )
        except ValueError as exc:
            sys.stderr.write(
                f"[skill-retro] Relay error: {exc}\n"
                "  Fix: pass distinct `--from <host>` and `--to <guest>` skill names.\n"
                "  Reference: skills/skill-retro/references/progressive-evolution-playbook.md\n"
            )
            raise SystemExit(2) from exc
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if cmd == "record-m1-receipt":
        p = _SelfDisclosingArgumentParser(prog="skill_retro.py record-m1-receipt")
        p.add_argument("--skill", "--target-skill", dest="skill", required=True, help="Skill investigated by this independent M1 TL.")
        p.add_argument(
            "--status",
            required=True,
            choices=("FIXED_AND_MERGED", "HEALTHY_NO_ISSUES", "CROSS_SKILL_RELAY"),
            help="Final M1 TL deep-reasoning outcome.",
        )
        p.add_argument("--handoff-root", "--emit-briefs", "--handoff-dir", dest="handoff_root", type=Path, default=DEFAULT_HANDOFF_ROOT)
        p.add_argument("--summary", default="", help="Concise M1 TL finding / fix summary.")
        p.add_argument("--pr-url", default=None)
        p.add_argument("--pr-number", type=int, default=None)
        p.add_argument("--commit-sha", default=None)
        p.add_argument("--writers-spawned", type=int, default=None)
        p.add_argument("--reviewers-spawned", type=int, default=None)
        p.add_argument("--relay-to", default=None)
        p.add_argument("--relay-requirement", default="")
        p.add_argument("--mode", choices=("full", "partial"), default="full")
        p.add_argument("--aspects", "--aspect", dest="aspects", default=None)
        p.add_argument("--json", action="store_true", default=True)
        ns = p.parse_args(rest)
        out = record_m1_retro_receipt(
            skill=ns.skill,
            status=ns.status,
            handoff_root=ns.handoff_root,
            summary=ns.summary,
            pr_url=ns.pr_url,
            pr_number=ns.pr_number,
            commit_sha=ns.commit_sha,
            writers_spawned=ns.writers_spawned,
            reviewers_spawned=ns.reviewers_spawned,
            relay_to=ns.relay_to,
            relay_requirement=ns.relay_requirement,
            mode=ns.mode,
            aspects=ns.aspects,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if cmd == "rollup":
        p = _SelfDisclosingArgumentParser(prog="skill_retro.py rollup")
        p.add_argument("--handoff-root", "--emit-briefs", "--handoff-dir", dest="handoff_root", type=Path, default=DEFAULT_HANDOFF_ROOT)
        p.add_argument("--skills", "--skill", dest="skills", default=None, help="Optional comma-separated skill list to roll up.")
        p.add_argument("--date", dest="target_date", default=None, help="Target date YYYY-MM-DD (default CST today).")
        p.add_argument("--repo-root", type=Path, default=None, help="Optional git repository root to verify claimed commit SHAs.")
        p.add_argument("--verify-grounding", action="store_true", default=False, help="Verify claimed FIXED_AND_MERGED SHAs against git history.")
        p.add_argument("--output", type=Path, default=None, help="Optional output JSON path.")
        p.add_argument("--markdown-output", type=Path, default=None, help="Optional markdown summary output path.")
        p.add_argument("--json", action="store_true", default=True)
        ns = p.parse_args(rest)
        out = rollup_m1_retro_receipts(
            handoff_root=ns.handoff_root,
            skills=ns.skills,
            target_date=ns.target_date,
            output_path=ns.output,
            repo_root=ns.repo_root,
            verify_grounding=ns.verify_grounding,
        )
        if ns.markdown_output is not None:
            ns.markdown_output.parent.mkdir(parents=True, exist_ok=True)
            ns.markdown_output.write_text(str(out.get("markdown_summary") or ""), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if cmd in {"roi", "skill-roi"}:
        p = _SelfDisclosingArgumentParser(prog=f"skill_retro.py {cmd}")
        _add_common_source_args(p)
        p.add_argument("--magpie-usage", type=Path, default=None)
        p.add_argument("--dsh-sessions", type=Path, default=DEFAULT_DSH_SESSIONS)
        p.add_argument("--icloud-roi-path", type=Path, default=None)
        p.add_argument("--no-icloud-mirror", action="store_true")
        p.add_argument("--auto-propose", action="store_true")
        p.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
        ns = p.parse_args(rest)
        report = compute_skill_roi(
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            magpie_usage=ns.magpie_usage,
            dsh_sessions=ns.dsh_sessions,
            telemetry_shards_dir=ns.telemetry_shards_dir,
            icloud_sync_dir=ns.icloud_sync_dir,
            mirror_icloud_roi=False if ns.no_icloud_mirror else None,
            icloud_roi_path=ns.icloud_roi_path,
        )
        if ns.auto_propose:
            queued = _queue_recommendations(report.get("low_roi_proposals", []), ns.proposals_path)
            report.update(
                auto_proposed=queued,
                proposals_written=len(queued),
                proposals_skipped=len(report.get("low_roi_proposals", [])) - len(queued),
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    # `diagnose`, `emit-briefs`, or `skill-retro`
    p = _SelfDisclosingArgumentParser(prog=f"skill_retro.py {cmd}")
    _add_common_source_args(p)
    p.add_argument("--emit-briefs", "--handoff-dir", dest="emit_briefs", type=Path, default=None)
    p.add_argument("--mode", choices=("full", "partial", "auto"), default="full")
    p.add_argument("--aspects", "--aspect", dest="aspects", default=None, help="Comma-separated aspects (l0,l1,l2,l3,creative,eval).")
    p.add_argument("--pick-one", action="store_true", default=False, help="Auto-pick top 1 actionable skill via Stage 1+2 if --skill is omitted.")
    p.add_argument("--pick-top", type=int, default=None, help="Auto-pick top N disjoint skills via Stage 1+2.")
    p.add_argument("--use-jev", action="store_true", default=False)
    p.add_argument("--mock-jev-json", default=None)
    p.add_argument("--coupled-with", default=None, help="Lock a coupled Guest Skill into the same worktree Owned Paths.")
    p.add_argument("--relay-to", default=None, help="Emit a Wave 2 Cross-Skill Relay Brief to this Guest Skill.")
    p.add_argument("--relay-requirement", default="", help="Requirement text for --relay-to.")
    p.add_argument("--icloud-roi-path", type=Path, default=None)
    p.add_argument("--local-roi-path", type=Path, default=None)
    p.add_argument("--no-roi-mirror", action="store_true")
    p.add_argument("--auto-propose", action="store_true")
    p.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
    ns = p.parse_args(rest)

    target_skill = ns.skill
    eff_mode = "full" if ns.mode == "auto" else ns.mode
    eff_aspects = ns.aspects
    triage_meta: dict[str, Any] | None = None

    if (ns.pick_one or ns.pick_top is not None or ns.mode == "auto") and not target_skill:
        triage_meta = triage_skills(
            skill=None,
            aspect=ns.aspects,
            pick_one=True if ns.pick_top is None else False,
            pick_top=ns.pick_top,
            use_jev=ns.use_jev,
            mock_jev_json=ns.mock_jev_json,
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            copilot_sessions_dir=ns.copilot_sessions_dir,
            usage_jsonl=ns.usage_jsonl,
            trace_export_path=ns.trace_export,
            handoff_root=ns.handoff_root,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            telemetry_shards_dir=ns.telemetry_shards_dir,
            days=ns.days,
        )
        if ns.pick_top is not None and int(ns.pick_top) > 1 and ns.emit_briefs is not None:
            multi_selected = list(triage_meta.get("selected_skills") or [])
            combined_briefs: list[dict[str, Any]] = []
            combined_specs: list[dict[str, Any]] = []
            combined_relays: list[dict[str, Any]] = []
            wave1_hosts: list[dict[str, Any]] = []
            wave2_relays: list[dict[str, Any]] = []
            last_report: dict[str, Any] | None = None
            for item in multi_selected:
                sk_name = str(item.get("skill") or "")
                sk_mode = str(item.get("mode") or ("full" if ns.mode == "auto" else ns.mode))
                sk_aspects = item.get("aspects") if ns.aspects is None else ns.aspects
                sub_rep = compute_skill_retro(
                    warehouse_db=ns.warehouse_db,
                    copilot_db=ns.copilot_db,
                    copilot_sessions_dir=ns.copilot_sessions_dir,
                    usage_jsonl=ns.usage_jsonl,
                    trace_export_path=ns.trace_export,
                    handoff_root=ns.handoff_root,
                    skills_root=ns.skills_root,
                    dev_root=ns.dev_root,
                    telemetry_shards_dir=ns.telemetry_shards_dir,
                    skill=sk_name,
                    emit_briefs_dir=ns.emit_briefs,
                    mode=sk_mode,
                    aspects=sk_aspects,
                    coupled_with=ns.coupled_with,
                    days=ns.days,
                    auto_propose=ns.auto_propose,
                    proposals_path=ns.proposals_path,
                    icloud_sync_dir=ns.icloud_sync_dir,
                    icloud_roi_path=ns.icloud_roi_path,
                    local_roi_path=ns.local_roi_path,
                    mirror_roi_snapshot=False if ns.no_roi_mirror else None,
                    isolated_fixtures=bool(ns.isolated_fixtures),
                )
                last_report = sub_rep
                eb = sub_rep.get("emitted_briefs") or {}
                combined_briefs.extend(eb.get("briefs") or [])
                combined_specs.extend(eb.get("m1_goal_specs") or [])
                combined_relays.extend(eb.get("cross_skill_relays") or [])
                wp = eb.get("wave_plan") or {}
                wave1_hosts.extend(wp.get("wave_1_disjoint_hosts") or [])
                wave2_relays.extend(wp.get("wave_2_cross_skill_relays") or [])
            base_report = last_report or compute_skill_retro(
                warehouse_db=ns.warehouse_db,
                copilot_db=ns.copilot_db,
                copilot_sessions_dir=ns.copilot_sessions_dir,
                usage_jsonl=ns.usage_jsonl,
                trace_export_path=ns.trace_export,
                handoff_root=ns.handoff_root,
                skills_root=ns.skills_root,
                dev_root=ns.dev_root,
                telemetry_shards_dir=ns.telemetry_shards_dir,
                skill=None,
                emit_briefs_dir=None,
                mode=eff_mode,
                aspects=eff_aspects,
                days=ns.days,
                mirror_roi_snapshot=False,
                isolated_fixtures=bool(ns.isolated_fixtures),
            )
            m2_plan = build_m2_dispatch_plan(combined_briefs, handoff_root=ns.emit_briefs, dev_root=ns.dev_root)
            base_report["emitted_briefs"] = {
                "briefs_dir": str(ns.emit_briefs / "briefs" if ns.emit_briefs.name != "briefs" else ns.emit_briefs),
                "handoff_root": str(ns.emit_briefs.parent if ns.emit_briefs.name == "briefs" else ns.emit_briefs),
                "status": "ACTIONABLE" if combined_briefs else "HEALTHY_NO_ISSUES",
                "status_message": (
                    f"Emitted {len(combined_briefs)} per-skill M1 Goal spec(s) & brief(s)"
                    if combined_briefs
                    else "healthy / 0 issues found (no changes needed)"
                ),
                "m1_tls_to_spawn": len(combined_briefs),
                "writers_spawned": len(combined_briefs),
                "mode": eff_mode,
                "role_slots_reused": ["M1-TL", "writer", "reviewer"] if combined_briefs else [],
                "new_agent_roles_created": 0,
                "count": len(combined_briefs),
                "briefs": combined_briefs,
                "m1_goal_specs": combined_specs,
                "m2_dispatch_plan": m2_plan,
                "cross_skill_relays": combined_relays,
                "wave_plan": {
                    "wave_1_disjoint_hosts": wave1_hosts,
                    "wave_2_cross_skill_relays": wave2_relays,
                },
            }
            base_report["m2_dispatch_plan"] = m2_plan
            base_report["stage2_triage"] = {
                "picked_skill": triage_meta.get("picked_skill"),
                "picked_mode": triage_meta.get("picked_mode"),
                "picked_aspects": triage_meta.get("picked_aspects"),
                "selected_skills": triage_meta.get("selected_skills"),
                "excluded_by_active_lease": triage_meta.get("excluded_by_active_lease"),
                "m2_dispatch_plan": m2_plan,
            }
            print(json.dumps(base_report, ensure_ascii=False, indent=2))
            return 0
        target_skill = triage_meta.get("picked_skill")
        if ns.mode == "auto" and triage_meta.get("picked_mode") in {"partial", "full"}:
            eff_mode = str(triage_meta["picked_mode"])
        if not eff_aspects and triage_meta.get("picked_aspects"):
            eff_aspects = ",".join(triage_meta["picked_aspects"])

    relays_list = (
        [{"from_skill": target_skill or "", "to_skill": ns.relay_to, "requirement": ns.relay_requirement}]
        if ns.relay_to
        else None
    )
    report = compute_skill_retro(
        warehouse_db=ns.warehouse_db,
        copilot_db=ns.copilot_db,
        copilot_sessions_dir=ns.copilot_sessions_dir,
        usage_jsonl=ns.usage_jsonl,
        trace_export_path=ns.trace_export,
        handoff_root=ns.handoff_root,
        skills_root=ns.skills_root,
        dev_root=ns.dev_root,
        telemetry_shards_dir=ns.telemetry_shards_dir,
        skill=target_skill,
        emit_briefs_dir=ns.emit_briefs,
        mode=eff_mode,
        aspects=eff_aspects,
        coupled_with=ns.coupled_with,
        relays=relays_list,
        days=ns.days,
        auto_propose=ns.auto_propose,
        proposals_path=ns.proposals_path,
        icloud_sync_dir=ns.icloud_sync_dir,
        icloud_roi_path=ns.icloud_roi_path,
        local_roi_path=ns.local_roi_path,
        mirror_roi_snapshot=False if ns.no_roi_mirror else None,
        isolated_fixtures=bool(ns.isolated_fixtures),
    )
    if triage_meta is not None:
        report["stage2_triage"] = {
            "picked_skill": triage_meta.get("picked_skill"),
            "picked_mode": triage_meta.get("picked_mode"),
            "picked_aspects": triage_meta.get("picked_aspects"),
            "selected_skills": triage_meta.get("selected_skills"),
            "excluded_by_active_lease": triage_meta.get("excluded_by_active_lease"),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
