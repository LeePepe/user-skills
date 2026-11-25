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

# Shared runtime primitives: filesystem safety, redaction, bounded CLI, traces, and Git grounding.
from skill_runtime import (
    SelfDisclosingArgumentParser,
    is_safe_local_dir,
    is_safe_local_file,
    read_copilot_sessions,
    redact_payload,
    redact_text,
    resolve_skill_version,
    safe_read_text,
    verify_commit_exists,
)
from skill_runtime.fs_guard import _SF_DATALESS as RUNTIME_SF_DATALESS

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
DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_UPTAKE_MAP_PATH = Path(__file__).resolve().parents[2] / "references" / "uptake_map.json"
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
SF_DATALESS = RUNTIME_SF_DATALESS
SAFE_OPEN_TIMEOUT_SEC = 0.5
DEFAULT_MAX_COPILOT_SESSIONS_SCAN = 60
DEFAULT_MAX_SESSION_EVENTS_BYTES = 1_000_000



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

CST = timezone(timedelta(hours=8))

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

_VALID_GIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")

_VALID_PR_REF_RE = re.compile(r"^(?:https://github\.com/[^/\s]+/[^/\s]+/pull/\d+|#?\d+)$")


# Export imported names and runtime primitives to every partition.
__all__ = [name for name in globals() if not name.startswith("__")]
