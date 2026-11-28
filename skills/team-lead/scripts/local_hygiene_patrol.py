#!/usr/bin/env python3
"""Loop 3A: Local Herdr & Worktree Hygiene Patrol Loop (`local_hygiene_patrol.py`).

Implements the 3-stage hygiene patrol pipeline:
1. Stage 1 (Layer 0 — 0-Agent Deterministic Scan & Safe Auto-GC):
   - Scans Herdr completed/orphan duplicate M1 panes (`herdr workspace list` +
     `herdr agent list` + `handoff/*/ledger.md` `EVT=GOAL_DONE`), strictly excluding
     protected workspaces (`w1`, `w2J`), unnamed Owner panes, and active M2 sessions.
   - Scans worktrees across personal/core repos via `git-gc.py` (`daily-cleanup`),
     reclaiming ONLY clean already-merged worktrees (`is_dirty == 0`,
     `is_merged_to_default == 1`, `has_active_pane == 0`, not held).
   - Scans main checkouts of core repos (`user-skills`, `AIDash`, `VoxPocket`,
     `VitalStride`, `Financial`, `shared-ci`, `shared-design-system`) for dirty
     uncommitted files on `main` or branches behind `origin/main`.
2. Stage 2 (Layer 1 — Jev Fast-Thinking Judgment via `jev_client.py`):
   - Evaluates residual anomalies (dirty main checkouts, unmerged stale drift,
     or unclosable zombie panes) using `jev_decide()` (`choice` + companion `noul`
     + `score`, with `--mock-jev-json` and deterministic truth-table fallback).
3. Stage 3 (Layer 2 — Dual-Zone Alert Payload & `multica_loop_runner.py` Collector):
   - Emits a Dual-Zone (`issue-brief` verified) Priority 3 (`medium`) Hygiene Alert
     payload when `actionable == True` (`>0.70` or deterministic fallback).
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

SCRIPT_PATH = Path(__file__).resolve()
TEAM_LEAD_SCRIPTS_DIR = SCRIPT_PATH.parent
SKILLS_ROOT = SCRIPT_PATH.parents[2]
GIT_GC_SCRIPT_PATH = TEAM_LEAD_SCRIPTS_DIR / "git-gc.py"
GOAL_SUPERVISOR_SCRIPT_PATH = TEAM_LEAD_SCRIPTS_DIR / "goal-supervisor.py"
JEV_CLIENT_SCRIPT_PATH = TEAM_LEAD_SCRIPTS_DIR / "jev_client.py"
ISSUE_BRIEF_SCRIPT_PATH = SKILLS_ROOT / "issue-brief" / "scripts" / "verify_issue_format.py"

LOOP_ID = "local-herdr-worktree-gc"
CST = timezone(timedelta(hours=8))

DEFAULT_CORE_REPOS: tuple[str, ...] = (
    "user-skills",
    "AIDash",
    "VoxPocket",
    "VitalStride",
    "Financial",
    "shared-ci",
    "shared-design-system",
)

KNOWN_RUNTIME_CACHE_EXCLUDES: tuple[str, ...] = (
    "skills/linear-board/scripts/state/",
    ".agents/",
    ".orca/",
    "aidata/L5_apps/digest/state/",
    "docs/prompts/",
    "tmp????????/",
)

PROTECTED_WORKSPACE_IDS: frozenset[str] = frozenset({"w1", "w2J"})
HOLD_LEDGER_PATTERN = re.compile(
    r"\bEVT=(?:HOLD|FREEZE|NEED_GO|NEED_OWNER_DECISION|TASK_BLOCKED|INPUT_BLOCKED)\b"
    r"|\bOWNER_FREEZE\b|\bstatus\s*[:=]\s*hold\b",
    re.IGNORECASE,
)
GOAL_DONE_PATTERN = re.compile(r"\bEVT=GOAL_DONE\b")
M1_ROLE_MARKERS: tuple[str, ...] = (
    "-goal-tl-",
    "-team-lead-",
    "-writer-",
    "-rev-",
    "-reviewer-",
    "-exp-",
    "-explorer-",
    "-prmon-",
    "-pr-monitor-",
    "-des-",
    "-designer-",
)

HYGIENE_CACHE_DIR = Path(tempfile.gettempdir()) / "team-lead-hygiene-patrol-cache"
HYGIENE_CACHE_TTL_SECONDS = 30.0

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _cache_watermark(dev_root: Path, handoff_root: Path, repos: Sequence[str]) -> str:
    entries: list[tuple[str, int, int]] = []
    candidates: list[Path] = []
    for repo_name in repos:
        repo_dir = (dev_root / repo_name).expanduser()
        candidates.extend((repo_dir / ".git" / name for name in ("HEAD", "index")))
        worktree_meta = repo_dir / ".git" / "worktrees"
        if worktree_meta.is_dir():
            candidates.extend(sorted(worktree_meta.rglob("*") ))
    if handoff_root.is_dir():
        candidates.extend(sorted(handoff_root.glob("*/ledger.md")))
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((str(path), stat.st_mtime_ns, stat.st_size))
    return _compute_signature({"dev_root": str(dev_root), "handoff_root": str(handoff_root), "repos": list(repos), "stats": entries})


def _cache_read(name: str, signature: str) -> dict[str, Any] | None:
    path = HYGIENE_CACHE_DIR / f"{name}.json"
    try:
        if time.time() - path.stat().st_mtime > HYGIENE_CACHE_TTL_SECONDS:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("signature") == signature and isinstance(payload.get("data"), dict):
            return payload["data"]
    except (OSError, ValueError, TypeError):
        return None
    return None


def _cache_write(name: str, signature: str, data: Mapping[str, Any]) -> None:
    try:
        HYGIENE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = HYGIENE_CACHE_DIR / f"{name}.json"
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps({"signature": signature, "data": data}, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        pass


def _cache_enabled(runner: Runner, use_cache: bool) -> bool:
    return use_cache and runner is _default_runner


class _SelfDisclosingHygieneParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(
            f"[local_hygiene_patrol] CLI error: {message}\n"
            "  Fix: run `python3 skills/team-lead/scripts/local_hygiene_patrol.py --help` and check the subcommand flags.\n"
            "  Reference: skills/team-lead/references/bots-and-agents.md\n"
        )
        raise SystemExit(2)


def _default_runner(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = False,
    timeout: float = 20.0,
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
            check=check,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(list(command), 124, "", "command timed out")


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    old_flag = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old_flag
    return module


def _load_git_gc() -> Any:
    return _load_module("hygiene_patrol_git_gc", GIT_GC_SCRIPT_PATH)


def _load_jev_client() -> Any:
    return _load_module("hygiene_patrol_jev_client", JEV_CLIENT_SCRIPT_PATH)


def _load_issue_brief() -> Any:
    return _load_module("hygiene_patrol_issue_brief", ISSUE_BRIEF_SCRIPT_PATH)


def _compute_signature(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _extract_json_objects(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _extract_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _extract_json_objects(child)


def _parse_herdr_agents(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, Mapping):
        result = raw.get("result", raw)
        if isinstance(result, Mapping) and isinstance(result.get("agents"), list):
            return [dict(item) for item in result["agents"] if isinstance(item, Mapping)]
        if isinstance(raw.get("agents"), list):
            return [dict(item) for item in raw["agents"] if isinstance(item, Mapping)]
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    return []


def _parse_herdr_workspaces(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, Mapping):
        result = raw.get("result", raw)
        if isinstance(result, Mapping) and isinstance(result.get("workspaces"), list):
            return [dict(item) for item in result["workspaces"] if isinstance(item, Mapping)]
        if isinstance(raw.get("workspaces"), list):
            return [dict(item) for item in raw["workspaces"] if isinstance(item, Mapping)]
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, Mapping)]
    return []


def _load_herdr_snapshot(
    *,
    herdr_json: Path | Mapping[str, Any] | None = None,
    runner: Runner = _default_runner,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if herdr_json is not None:
        if isinstance(herdr_json, Mapping):
            payload = dict(herdr_json)
        else:
            payload = json.loads(Path(herdr_json).expanduser().read_text(encoding="utf-8"))
        return _parse_herdr_workspaces(payload), _parse_herdr_agents(payload)

    workspaces: list[dict[str, Any]] = []
    agents: list[dict[str, Any]] = []
    try:
        ws_proc = runner(["herdr", "workspace", "list"], check=False)
        if ws_proc.returncode == 0 and (ws_proc.stdout or "").strip():
            workspaces = _parse_herdr_workspaces(json.loads(ws_proc.stdout))
    except (OSError, ValueError, TypeError):
        workspaces = []

    try:
        ag_proc = runner(["herdr", "agent", "list"], check=False)
        if ag_proc.returncode == 0 and (ag_proc.stdout or "").strip():
            agents = _parse_herdr_agents(json.loads(ag_proc.stdout))
    except (OSError, ValueError, TypeError):
        agents = []
    return workspaces, agents


def _tl_stem(name: str) -> str:
    for marker in ("-goal-tl-", "-team-lead-"):
        idx = name.find(marker)
        if idx > 0:
            return name[:idx]
    return ""


def _stem_matches_goal(stem: str, goal_name: str) -> bool:
    if not stem or not goal_name:
        return False
    if stem == goal_name:
        return True
    stem_parts = stem.split("-")
    goal_parts = goal_name.split("-")
    if len(stem_parts) != len(goal_parts) or stem_parts[:-1] != goal_parts[:-1]:
        return False
    last_stem, last_goal = stem_parts[-1], goal_parts[-1]
    return len(last_stem) >= 3 and last_goal.startswith(last_stem)


def _scan_handoff_ledgers(handoff_root: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not handoff_root.is_dir():
        return records
    for goal_dir in sorted(handoff_root.iterdir()):
        if not goal_dir.is_dir() or goal_dir.name.startswith("_"):
            continue
        ledger_path = goal_dir / "ledger.md"
        if not ledger_path.is_file():
            continue
        try:
            text = ledger_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tl_name = ""
        for line in text.splitlines():
            if "TL:" in line:
                tok = line.split("TL:", 1)[1].strip().split()
                if tok:
                    tl_name = tok[0].strip()
                    break
        agent_refs = set(re.findall(r"\b(?:from|to)=([a-z][a-z0-9_-]{0,31})\b", text))
        if tl_name:
            agent_refs.add(tl_name)
        goal_done = bool(GOAL_DONE_PATTERN.search(text))
        # A goal is held if there is a hold/freeze/blocked marker after the last GOAL_DONE or without GOAL_DONE
        last_done_pos = -1
        for m in GOAL_DONE_PATTERN.finditer(text):
            last_done_pos = m.end()
        tail_text = text[last_done_pos:] if last_done_pos >= 0 else text
        is_held = bool((goal_dir / ".hold").exists() or HOLD_LEDGER_PATTERN.search(tail_text))
        records[goal_dir.name] = {
            "goal": goal_dir.name,
            "path": str(goal_dir),
            "tl_name": tl_name,
            "tl_stem": _tl_stem(tl_name),
            "agent_refs": sorted(agent_refs),
            "goal_done": goal_done and not is_held,
            "is_held": is_held,
        }
    return records


def _is_protected_or_m2_agent(agent: Mapping[str, Any], workspaces_by_id: Mapping[str, Mapping[str, Any]]) -> tuple[bool, str]:
    pane_id = str(agent.get("pane_id") or "")
    ws_id = str(agent.get("workspace_id") or (pane_id.split(":", 1)[0] if ":" in pane_id else "")).strip()
    name = str(agent.get("name") or agent.get("agent_name") or "").strip()

    if ws_id in PROTECTED_WORKSPACE_IDS or pane_id.startswith(("w1:", "w2J:")):
        return True, "protected_workspace"
    ws_meta = workspaces_by_id.get(ws_id) or {}
    ws_label = str(ws_meta.get("label") or "").strip()
    if ws_label in {"大总管", "Owner", "main"}:
        return True, "protected_owner_workspace"
    if not name:
        return True, "unnamed_owner_pane"
    if name.startswith("m2-") or "-m2-" in name or name.startswith("personal-m2"):
        return True, "active_m2_pane"
    if not any(marker in name for marker in M1_ROLE_MARKERS):
        return True, "non_m1_named_pane"
    return False, ""


_ROLE_SUFFIX = r"(?:goal-tl-r[a-z0-9]+|team-lead-r[a-z0-9]+|(?:writer|reviewer|rev|explorer|exp|pr-monitor|prmon|designer|des)[a-z0-9_]*(?:-[a-z0-9_]+)*)"


def _valid_goal_suffix(suffix: str) -> bool:
    return bool(re.fullmatch(rf"(?:(?:tk|t)\d+[a-z0-9]*-)?{_ROLE_SUFFIX}", suffix))


def _match_goal_name(name: str, ledgers: Mapping[str, Mapping[str, Any]]) -> str | None:
    ordered = sorted(ledgers.items(), key=lambda kv: len(kv[0]), reverse=True)
    if name in ledgers:
        return name
    prefixes = [(g, g) for g, _ in ordered if name.startswith(g + "-")]
    if not prefixes:
        prefixes = [(g, str(m.get("tl_stem"))) for g, m in ordered
                    if m.get("tl_stem") and name.startswith(str(m["tl_stem"]) + "-")]
    if prefixes:
        longest = max(len(prefix) for _, prefix in prefixes)
        matches = {g for g, prefix in prefixes if len(prefix) == longest and _valid_goal_suffix(name[len(prefix) + 1:])}
        return next(iter(matches)) if len(matches) == 1 else "__ambiguous__"
    stem = _tl_stem(name) or name
    matches = [g for g, _ in ordered if _stem_matches_goal(stem, g)]
    if matches and stem != name and not _valid_goal_suffix(name[len(stem) + 1:]):
        return "__ambiguous__"
    return matches[0] if len(matches) == 1 else ("__ambiguous__" if matches else None)


def _match_agent_to_goal(agent: Mapping[str, Any], ledgers: Mapping[str, Mapping[str, Any]]) -> str | None:
    name = str(agent.get("name") or agent.get("agent_name") or "").strip()
    matches = {_match_goal_name(name, ledgers)} - {None}
    refs = {g for g, m in ledgers.items() if name and (name == m.get("tl_name") or name in (m.get("agent_refs") or ()))}
    matches.update(refs)
    for value in (agent.get("cwd") or agent.get("foreground_cwd"), agent.get("terminal_title_stripped") or agent.get("terminal_title")):
        text = str(value or "")
        matches.update(g for g in ledgers if re.search(rf"/{re.escape(g)}(?=/|$|[\s'\"])", text))
        for slug in re.findall(r"/(?:worktrees|handoff)/([A-Za-z0-9_.-]+)", text):
            match = _match_goal_name(slug, ledgers)
            if match:
                matches.add(match)
    return next(iter(matches)) if len(matches) == 1 else ("__ambiguous__" if matches else None)


def scan_herdr_panes(
    *,
    handoff_root: Path,
    herdr_json: Path | Mapping[str, Any] | None = None,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    workspaces, agents = _load_herdr_snapshot(herdr_json=herdr_json, runner=runner)
    workspaces_by_id = {
        str(ws.get("workspace_id") or ws.get("id") or ""): ws
        for ws in workspaces
        if ws.get("workspace_id") or ws.get("id")
    }
    ledgers = _scan_handoff_ledgers(handoff_root)

    reclaimable_panes: list[dict[str, Any]] = []
    protected_panes: list[dict[str, Any]] = []
    zombie_panes: list[dict[str, Any]] = []
    active_m1_panes: list[dict[str, Any]] = []

    seen_agent_names: dict[str, str] = {}
    current_pane_id = os.environ.get("HERDR_PANE_ID", "").strip()

    for agent in agents:
        pane_id = str(agent.get("pane_id") or "").strip()
        ws_id = str(agent.get("workspace_id") or (pane_id.split(":", 1)[0] if ":" in pane_id else "")).strip()
        name = str(agent.get("name") or agent.get("agent_name") or "").strip()
        status = str(agent.get("agent_status") or agent.get("status") or "").lower()

        if current_pane_id and pane_id == current_pane_id:
            protected_panes.append({"pane_id": pane_id, "workspace_id": ws_id, "name": name, "reason": "self_current_pane"})
            continue

        is_prot, prot_reason = _is_protected_or_m2_agent(agent, workspaces_by_id)
        if is_prot:
            protected_panes.append({"pane_id": pane_id, "workspace_id": ws_id, "name": name, "reason": prot_reason})
            continue

        matched_goal = _match_agent_to_goal(agent, ledgers)
        goal_meta = ledgers.get(matched_goal) if matched_goal else None
        if matched_goal == "__ambiguous__":
            active_m1_panes.append({"pane_id": pane_id, "workspace_id": ws_id, "name": name,
                                    "status": status, "goal": None, "reason": "ambiguous_goal_identity"})
            continue

        # Duplicate M1 pane name check
        if name and name in seen_agent_names and status in {"done", "idle", "exited"}:
            reclaimable_panes.append(
                {
                    "pane_id": pane_id,
                    "workspace_id": ws_id,
                    "name": name,
                    "status": status,
                    "goal": matched_goal,
                    "reason": "duplicate_completed_m1_pane",
                }
            )
            continue
        if name:
            seen_agent_names[name] = pane_id

        if goal_meta and goal_meta.get("goal_done"):
            if status in {"working", "running"}:
                zombie_panes.append(
                    {
                        "pane_id": pane_id,
                        "workspace_id": ws_id,
                        "name": name,
                        "status": status,
                        "goal": matched_goal,
                        "reason": "working_after_goal_done",
                    }
                )
            else:
                reclaimable_panes.append(
                    {
                        "pane_id": pane_id,
                        "workspace_id": ws_id,
                        "name": name,
                        "status": status,
                        "goal": matched_goal,
                        "reason": "goal_done_m1_pane",
                    }
                )
        elif matched_goal is None and status == "done" and any(m in name for m in ("-writer-", "-rev-", "-reviewer-", "-exp-", "-explorer-")):
            reclaimable_panes.append(
                {
                    "pane_id": pane_id,
                    "workspace_id": ws_id,
                    "name": name,
                    "status": status,
                    "goal": None,
                    "reason": "orphan_done_leaf_subagent",
                }
            )
        elif status in {"error", "zombie"}:
            zombie_panes.append(
                {
                    "pane_id": pane_id,
                    "workspace_id": ws_id,
                    "name": name,
                    "status": status,
                    "goal": matched_goal,
                    "reason": f"m1_pane_{status}",
                }
            )
        elif status == "blocked":
            # A blocked leaf/TL pane in an active in-progress Goal is handled by notify-bridge / its active TL;
            # only flag as a zombie hygiene anomaly when orphan or no active goal owns it.
            has_active_goal = bool(goal_meta and not goal_meta.get("goal_done"))
            if not has_active_goal:
                zombie_panes.append(
                    {
                        "pane_id": pane_id,
                        "workspace_id": ws_id,
                        "name": name,
                        "status": status,
                        "goal": matched_goal,
                        "reason": "m1_pane_blocked",
                    }
                )
            else:
                active_m1_panes.append(
                    {
                        "pane_id": pane_id,
                        "workspace_id": ws_id,
                        "name": name,
                        "status": status,
                        "goal": matched_goal,
                    }
                )
        else:
            active_m1_panes.append(
                {
                    "pane_id": pane_id,
                    "workspace_id": ws_id,
                    "name": name,
                    "status": status,
                    "goal": matched_goal,
                }
            )

    return {
        "reclaimable_panes": reclaimable_panes,
        "zombie_panes": zombie_panes,
        "active_m1_panes": active_m1_panes,
        "protected_count": len(protected_panes),
    }


def _active_pane_cwds(agents: Sequence[Mapping[str, Any]], *, dev_root: Path | None = None) -> list[Path]:
    paths: list[Path] = []
    wt_root = (dev_root / "worktrees") if dev_root is not None else None
    for ag in agents:
        status = str(ag.get("agent_status") or ag.get("status") or "").lower()
        if status not in {"active", "working", "idle", "blocked", "running"}:
            continue
        raw_cwd = ag.get("cwd") or ag.get("foreground_cwd") or ag.get("worktree")
        if raw_cwd:
            try:
                paths.append(Path(str(raw_cwd)).expanduser().resolve())
            except OSError:
                pass
        for title_field in ("terminal_title_stripped", "terminal_title"):
            title_val = str(ag.get(title_field) or "")
            for m in re.finditer(r"/(?:worktrees|handoff)/([A-Za-z0-9_.-]+)", title_val):
                slug = m.group(1)
                if wt_root is not None:
                    cand = wt_root / slug
                    if cand.exists():
                        try:
                            paths.append(cand.resolve())
                        except OSError:
                            pass
    return paths


def _path_has_active_pane(worktree: Path, active_cwds: Sequence[Path]) -> bool:
    target = worktree.expanduser().resolve()
    return any(cwd == target or target in cwd.parents for cwd in active_cwds)


def _held_goal_names(handoff_root: Path) -> set[str]:
    ledgers = _scan_handoff_ledgers(handoff_root)
    return {name for name, meta in ledgers.items() if meta.get("is_held")}


def _is_worktree_held(worktree: Path, held_goals: set[str]) -> bool:
    if (worktree / ".hold").exists() or (worktree / ".keep").exists():
        return True
    wt_name = worktree.name
    return wt_name in held_goals or any(wt_name.startswith(g + "-") and _valid_goal_suffix(wt_name[len(g) + 1:]) for g in held_goals)


def _match_worktree_to_goal(worktree: Path, ledgers: Mapping[str, Mapping[str, Any]]) -> str | None:
    return _match_goal_name(worktree.name, ledgers)


def _is_positively_merged(
    repo_dir: Path,
    wt_path: Path,
    branch: str | None,
    *,
    ledgers: Mapping[str, Mapping[str, Any]],
    git_gc: Any,
    runner: Runner,
) -> bool:
    """Require positive merge evidence; never classify a fresh 0-commit branch as merged."""
    matched_goal = _match_worktree_to_goal(wt_path, ledgers)
    if matched_goal == "__ambiguous__":
        return False
    goal_meta = ledgers.get(matched_goal) if matched_goal else None

    if not branch:
        return bool(goal_meta and goal_meta.get("goal_done"))

    # Check if tip equals HEAD and is not in a Merge commit parent
    head_proc = runner(["git", "-C", str(repo_dir), "rev-parse", "--verify", "HEAD"], check=False)
    branch_proc = runner(["git", "-C", str(repo_dir), "rev-parse", "--verify", branch], check=False)
    head_sha = (head_proc.stdout or "").strip() if head_proc.returncode == 0 else ""
    branch_sha = (branch_proc.stdout or "").strip() if branch_proc.returncode == 0 else ""

    # If the worktree corresponds to a completed Goal ledger (EVT=GOAL_DONE), check ancestor or GitHub PR
    if goal_meta and goal_meta.get("goal_done"):
        anc_proc = runner(
            ["git", "-C", str(repo_dir), "merge-base", "--is-ancestor", branch, "HEAD"],
            check=False,
        )
        if anc_proc.returncode == 0:
            return True

    # If branch tip is a proper ancestor of HEAD (branch_sha != head_sha) and merged into HEAD
    if head_sha and branch_sha and branch_sha != head_sha:
        anc_proc = runner(
            ["git", "-C", str(repo_dir), "merge-base", "--is-ancestor", branch, "HEAD"],
            check=False,
        )
        if anc_proc.returncode == 0:
            # Verify branch was merged (e.g. second parent of a merge commit on HEAD) rather than an old base
            merges_proc = runner(
                ["git", "-C", str(repo_dir), "rev-list", "--merges", "--parents", f"{branch_sha}..HEAD"],
                check=False,
            )
            if any(branch_sha in line.split()[2:] for line in (merges_proc.stdout or "").splitlines() if len(line.split()) >= 3):
                return True

    # If branch_sha == head_sha without GOAL_DONE, check if HEAD itself is a fast-forward merge of an explicit PR
    # Otherwise, delegate to git_gc.is_merged ONLY when branch is not a fresh unmerged checkout at HEAD
    if head_sha and branch_sha and branch_sha == head_sha and not (goal_meta and goal_meta.get("goal_done")):
        # Check if there is an explicit MERGED PR on GitHub via gh/REST; do NOT let git merge-base fallback return True
        slug = git_gc._github_slug(repo_dir, runner=lambda cmd, **kw: runner(cmd, check=kw.get("check", True)))
        if not slug:
            return False
        try:
            pr_res = runner(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    slug,
                    "--state",
                    "all",
                    "--head",
                    branch,
                    "--limit",
                    "20",
                    "--json",
                    "number,state,mergedAt",
                ],
                check=False,
            )
            rows = json.loads(pr_res.stdout or "[]")
            if isinstance(rows, list):
                if any(str(r.get("state", "")).upper() == "OPEN" for r in rows):
                    return False
                return any(r.get("mergedAt") or str(r.get("state", "")).upper() == "MERGED" for r in rows)
        except (OSError, ValueError, TypeError):
            return False
        return False

    return bool(
        git_gc.is_merged(
            repo_dir,
            branch=branch,
            runner=lambda cmd, **kw: runner(cmd, check=kw.get("check", True)),
        )
    )


def scan_worktrees(
    *,
    dev_root: Path,
    handoff_root: Path,
    repos: Sequence[str] = DEFAULT_CORE_REPOS,
    herdr_json: Path | Mapping[str, Any] | None = None,
    runner: Runner = _default_runner,
    merge_checker: Callable[[Path, str | None], bool] | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    signature = _cache_watermark(Path(dev_root).expanduser(), Path(handoff_root).expanduser(), repos)
    if _cache_enabled(runner, use_cache) and merge_checker is None:
        cached = _cache_read("worktrees", signature)
        if cached is not None:
            return cached
    git_gc = _load_git_gc()
    _, agents = _load_herdr_snapshot(herdr_json=herdr_json, runner=runner)
    active_cwds = _active_pane_cwds(agents, dev_root=dev_root)
    ledgers = _scan_handoff_ledgers(handoff_root)
    held_goals = {name for name, meta in ledgers.items() if meta.get("is_held")}

    clean_merged: list[dict[str, Any]] = []
    preserved: list[dict[str, Any]] = []

    def inspect_repo(repo_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        repo_clean: list[dict[str, Any]] = []
        repo_preserved: list[dict[str, Any]] = []
        repo_dir = (dev_root / repo_name).expanduser()
        if not (repo_dir / ".git").exists():
            return repo_clean, repo_preserved
        try:
            wt_records = git_gc._worktrees(repo_dir, runner=lambda cmd, **kw: runner(cmd, **kw))
        except (OSError, subprocess.SubprocessError):
            return repo_clean, repo_preserved
        for item in wt_records:
            wt_str = item.get("worktree")
            if not wt_str:
                continue
            wt_path = Path(wt_str).expanduser()
            if wt_path.resolve() == repo_dir.resolve() or not wt_path.exists():
                continue
            branch = item.get("branch")
            status_proc = runner(["git", "-C", str(wt_path), "status", "--porcelain"], check=False)
            dirty_lines = [
                ln for ln in (status_proc.stdout or "").splitlines()
                if ln.strip()
                and not any(noise in ln for noise in git_gc.NOISE_NAMES)
                and not _is_known_runtime_cache_status_line(ln)
            ]
            is_dirty = int(len(dirty_lines) > 0)
            active_detected = _path_has_active_pane(wt_path, active_cwds)
            if not active_detected and herdr_json is None and not agents:
                active_detected = git_gc.has_active_herdr_pane(
                    wt_path,
                    runner=lambda cmd, **kw: runner(cmd, check=False),
                )
            has_active_pane = int(active_detected)
            is_held = int(_is_worktree_held(wt_path, held_goals))
            ambiguous_goal = _match_worktree_to_goal(wt_path, ledgers) == "__ambiguous__"
            if merge_checker is not None:
                merged_bool = merge_checker(repo_dir, branch)
            else:
                merged_bool = _is_positively_merged(
                    repo_dir, wt_path, branch, ledgers=ledgers, git_gc=git_gc, runner=runner
                )
            is_merged_to_default = int(bool(merged_bool))
            record = {
                "repo": repo_name,
                "repo_path": str(repo_dir),
                "worktree": str(wt_path),
                "branch": branch,
                "is_dirty": is_dirty,
                "is_merged_to_default": is_merged_to_default,
                "has_active_pane": has_active_pane,
                "is_held": is_held,
            }
            if is_dirty == 0 and is_merged_to_default == 1 and has_active_pane == 0 and is_held == 0 and not ambiguous_goal:
                repo_clean.append(record)
            else:
                reasons: list[str] = ["ambiguous_goal_identity"] if ambiguous_goal else []
                if is_dirty:
                    reasons.append("dirty")
                if not is_merged_to_default:
                    reasons.append("unmerged")
                if has_active_pane:
                    reasons.append("active_pane")
                if is_held:
                    reasons.append("held")
                record["preserve_reasons"] = reasons
                repo_preserved.append(record)
        return repo_clean, repo_preserved

    existing_repos = [repo for repo in repos if ((dev_root / repo).expanduser() / ".git").exists()]
    if len(existing_repos) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(existing_repos))) as pool:
            reports = list(pool.map(inspect_repo, existing_repos))
    else:
        reports = [inspect_repo(repo) for repo in existing_repos]
    for repo_clean, repo_preserved in reports:
        clean_merged.extend(repo_clean)
        preserved.extend(repo_preserved)

    result = {
        "clean_merged_worktrees": clean_merged,
        "preserved_worktrees": preserved,
    }
    if _cache_enabled(runner, use_cache) and merge_checker is None:
        _cache_write("worktrees", signature, result)
    return result


def _resolve_git_info_exclude_path(repo_or_wt: Path) -> Path | None:
    dot_git = repo_or_wt / ".git"
    if dot_git.is_dir():
        return dot_git / "info" / "exclude"
    if dot_git.is_file():
        try:
            raw = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        if raw.startswith("gitdir:"):
            gitdir_str = raw.split(":", 1)[1].strip()
            gitdir_path = Path(gitdir_str)
            if not gitdir_path.is_absolute():
                gitdir_path = (repo_or_wt / gitdir_path).resolve()
            commondir_file = gitdir_path / "commondir"
            if commondir_file.is_file():
                try:
                    rel_common = commondir_file.read_text(encoding="utf-8", errors="replace").strip()
                    common_path = (gitdir_path / rel_common).resolve()
                    return common_path / "info" / "exclude"
                except OSError:
                    pass
            return gitdir_path / "info" / "exclude"
    return None


def _is_known_runtime_cache_status_line(status_line: str) -> bool:
    stripped = status_line.strip()
    if not stripped.startswith("?? "):
        return False
    rel = stripped[3:].strip().strip('"')
    if re.fullmatch(r"tmp[a-z0-9_]{8}/?", rel):
        return True
    return any(
        pat != "tmp????????/" and (rel == pat.rstrip("/") or rel.startswith(pat))
        for pat in KNOWN_RUNTIME_CACHE_EXCLUDES
    )


def ensure_runtime_cache_excludes(
    repo_dir: Path,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Idempotently ensure known untracked runtime cache patterns are listed in .git/info/exclude."""
    exclude_path = _resolve_git_info_exclude_path(repo_dir)
    if exclude_path is None:
        return []
    existing_lines: list[str] = []
    if exclude_path.is_file():
        try:
            existing_lines = exclude_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
    existing_set = {ln.strip() for ln in existing_lines if ln.strip() and not ln.strip().startswith("#")}
    missing = [pat for pat in KNOWN_RUNTIME_CACHE_EXCLUDES if pat not in existing_set and pat.rstrip("/") not in existing_set]
    if not missing:
        return []
    if not dry_run:
        try:
            exclude_path.parent.mkdir(parents=True, exist_ok=True)
            prefix = ""
            if existing_lines and existing_lines[-1].strip():
                prefix = "\n"
            block = prefix + "# local_hygiene_patrol runtime state excludes\n" + "\n".join(missing) + "\n"
            with exclude_path.open("a", encoding="utf-8") as handle:
                handle.write(block)
        except OSError:
            return []
    return missing


def _remove_runtime_cache_in_merged_worktree(wt_dir: Path) -> None:
    for rel in KNOWN_RUNTIME_CACHE_EXCLUDES:
        cand = wt_dir / rel.rstrip("/")
        if cand.is_dir():
            import shutil as _shutil
            _shutil.rmtree(cand, ignore_errors=True)
        elif cand.is_file():
            cand.unlink(missing_ok=True)


def _remove_empty_or_stale_linear_state_dir(checkout_dir: Path, *, dry_run: bool = False) -> bool:
    state_dir = checkout_dir / "skills" / "linear-board" / "scripts" / "state"
    if not state_dir.is_dir():
        return False
    entries = [p for p in state_dir.iterdir() if p.name != ".DS_Store"]
    if not entries:
        if not dry_run:
            try:
                for ds in state_dir.glob(".DS_Store"):
                    ds.unlink(missing_ok=True)
                state_dir.rmdir()
            except OSError:
                return False
        return True
    return False


def remediate_main_checkouts(
    *,
    dev_root: Path,
    repos: Sequence[str] = DEFAULT_CORE_REPOS,
    fetch_remote: bool = False,
    dry_run: bool = False,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    """Auto-exclude known runtime caches and fast-forward clean behind main checkouts."""
    excluded_repos: list[dict[str, Any]] = []
    fast_forwarded_repos: list[dict[str, Any]] = []
    ff_errors: list[dict[str, Any]] = []

    for repo_name in repos:
        repo_dir = (dev_root / repo_name).expanduser()
        if not (repo_dir / ".git").exists():
            continue
        status_before = runner(["git", "-C", str(repo_dir), "status", "--porcelain"], check=False)
        raw_lines = [ln.strip() for ln in (status_before.stdout or "").splitlines() if ln.strip()]
        has_runtime_untracked = any(_is_known_runtime_cache_status_line(ln) for ln in raw_lines)
        removed_empty = _remove_empty_or_stale_linear_state_dir(repo_dir, dry_run=dry_run)
        added_patterns: list[str] = []
        if has_runtime_untracked:
            added_patterns = ensure_runtime_cache_excludes(repo_dir, dry_run=dry_run)
        if added_patterns or removed_empty:
            excluded_repos.append(
                {
                    "repo": repo_name,
                    "added_excludes": added_patterns,
                    "removed_empty_state_dir": removed_empty,
                    "dry_run": dry_run,
                }
            )

        if fetch_remote and not dry_run:
            runner(["git", "-C", str(repo_dir), "fetch", "--quiet", "origin"], check=False)

        branch_proc = runner(["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"], check=False)
        branch = (branch_proc.stdout or "").strip() or "HEAD"
        if branch not in {"main", "master"}:
            continue

        origin_ref = f"origin/{branch}"
        origin_probe = runner(["git", "-C", str(repo_dir), "rev-parse", "--verify", origin_ref], check=False)
        if origin_probe.returncode != 0:
            continue

        rev_proc = runner(
            ["git", "-C", str(repo_dir), "rev-list", "--left-right", "--count", f"HEAD...{origin_ref}"],
            check=False,
        )
        parts = (rev_proc.stdout or "").strip().split()
        if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
            continue
        ahead_count, behind_count = int(parts[0]), int(parts[1])
        if behind_count <= 0 or ahead_count != 0:
            continue

        status_after = runner(["git", "-C", str(repo_dir), "status", "--porcelain"], check=False)
        tracked_conflicts = [
            ln.strip()
            for ln in (status_after.stdout or "").splitlines()
            if ln.strip() and not ln.strip().startswith("?? ") and not ln.strip().endswith(".DS_Store")
        ]
        if tracked_conflicts:
            continue

        if dry_run:
            fast_forwarded_repos.append(
                {
                    "repo": repo_name,
                    "branch": branch,
                    "behind_before": behind_count,
                    "action": "dry_run_ff_only",
                }
            )
            continue

        pull_proc = runner(["git", "-C", str(repo_dir), "pull", "--ff-only", "--quiet", "origin", branch], check=False)
        if pull_proc.returncode == 0:
            fast_forwarded_repos.append(
                {
                    "repo": repo_name,
                    "branch": branch,
                    "behind_before": behind_count,
                    "action": "fast_forwarded",
                }
            )
        else:
            ff_errors.append(
                {
                    "repo": repo_name,
                    "branch": branch,
                    "behind_before": behind_count,
                    "error": (pull_proc.stderr or pull_proc.stdout or "").strip()[:200],
                }
            )

    return {
        "excluded_runtime_cache_repos": excluded_repos,
        "fast_forwarded_repos": fast_forwarded_repos,
        "fast_forward_errors": ff_errors,
    }


def scan_main_checkouts(
    *,
    dev_root: Path,
    repos: Sequence[str] = DEFAULT_CORE_REPOS,
    fetch_remote: bool = False,
    runner: Runner = _default_runner,
    use_cache: bool = True,
) -> dict[str, Any]:
    cache_root = Path(dev_root).expanduser()
    signature = _cache_watermark(cache_root, cache_root / "handoff", repos)
    if _cache_enabled(runner, use_cache) and not fetch_remote:
        cached = _cache_read("main-checkouts", signature)
        if cached is not None:
            return cached
    repo_reports: list[dict[str, Any]] = []
    dirty_repos: list[dict[str, Any]] = []
    behind_repos: list[dict[str, Any]] = []

    def inspect_repo(repo_name: str) -> dict[str, Any] | None:
        repo_dir = (dev_root / repo_name).expanduser()
        if not (repo_dir / ".git").exists():
            return None
        if fetch_remote:
            runner(["git", "-C", str(repo_dir), "fetch", "--quiet", "origin"], check=False)
        branch_proc = runner(["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"], check=False)
        branch = (branch_proc.stdout or "").strip() or "HEAD"
        status_proc = runner(["git", "-C", str(repo_dir), "status", "--porcelain"], check=False)
        dirty_files = [line.strip() for line in (status_proc.stdout or "").splitlines() if line.strip() and not line.strip().endswith(".DS_Store")]
        behind_count = 0
        ahead_count = 0
        origin_probe = runner(["git", "-C", str(repo_dir), "rev-parse", "--verify", "origin/main"], check=False)
        if origin_probe.returncode == 0:
            rev_proc = runner(["git", "-C", str(repo_dir), "rev-list", "--left-right", "--count", "HEAD...origin/main"], check=False)
            parts = (rev_proc.stdout or "").strip().split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                ahead_count, behind_count = int(parts[0]), int(parts[1])
        return {
            "repo": repo_name,
            "branch": branch,
            "dirty_count": len(dirty_files),
            "dirty_files": dirty_files[:20],
            "ahead_origin_main": ahead_count,
            "behind_origin_main": behind_count,
            "non_default_checkout": branch not in {"main", "master"},
        }

    existing_repos = [repo for repo in repos if ((dev_root / repo).expanduser() / ".git").exists()]
    if len(existing_repos) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(existing_repos))) as pool:
            repo_reports = [entry for entry in pool.map(inspect_repo, existing_repos) if entry is not None]
    else:
        repo_reports = [entry for entry in (inspect_repo(repo) for repo in existing_repos) if entry is not None]
    for entry in repo_reports:
        if entry["dirty_count"] > 0 or entry["non_default_checkout"]:
            dirty_repos.append(entry)
        if entry["behind_origin_main"] > 0:
            behind_repos.append(entry)

    result = {
        "repos_scanned": len(repo_reports),
        "repos": repo_reports,
        "dirty_main_repos": dirty_repos,
        "behind_origin_repos": behind_repos,
    }
    if _cache_enabled(runner, use_cache) and not fetch_remote:
        _cache_write("main-checkouts", signature, result)
    return result


def scan_environment(
    *,
    dev_root: Path,
    handoff_root: Path | None = None,
    repos: Sequence[str] = DEFAULT_CORE_REPOS,
    herdr_json: Path | Mapping[str, Any] | None = None,
    fetch_remote: bool = False,
    runner: Runner = _default_runner,
    merge_checker: Callable[[Path, str | None], bool] | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    resolved_dev = Path(dev_root).expanduser()
    resolved_handoff = Path(handoff_root).expanduser() if handoff_root is not None else (resolved_dev / "handoff")

    herdr_report = scan_herdr_panes(
        handoff_root=resolved_handoff,
        herdr_json=herdr_json,
        runner=runner,
    )
    worktree_report = scan_worktrees(
        dev_root=resolved_dev,
        handoff_root=resolved_handoff,
        repos=repos,
        herdr_json=herdr_json,
        runner=runner,
        merge_checker=merge_checker,
        use_cache=use_cache,
    )
    main_report = scan_main_checkouts(
        dev_root=resolved_dev,
        repos=repos,
        fetch_remote=fetch_remote,
        runner=runner,
        use_cache=use_cache,
    )

    anomalies: list[dict[str, Any]] = []
    for d_repo in main_report["dirty_main_repos"]:
        if d_repo["dirty_count"] > 0:
            anomalies.append(
                {
                    "kind": "dirty_main_checkout",
                    "repo": d_repo["repo"],
                    "detail": f"主仓 {d_repo['repo']} ({d_repo['branch']}) 存在 {d_repo['dirty_count']} 个未提交脏文件",
                }
            )
        if d_repo["non_default_checkout"]:
            anomalies.append(
                {
                    "kind": "main_checkout_off_main",
                    "repo": d_repo["repo"],
                    "detail": f"主仓 {d_repo['repo']} 当前停留在非默认分支 {d_repo['branch']}",
                }
            )
    for b_repo in main_report["behind_origin_repos"]:
        anomalies.append(
            {
                "kind": "behind_origin_main",
                "repo": b_repo["repo"],
                "detail": f"主仓 {b_repo['repo']} 落后远端主分支 {b_repo['behind_origin_main']} 个提交",
            }
        )
    for z_pane in herdr_report["zombie_panes"]:
        anomalies.append(
            {
                "kind": "zombie_herdr_pane",
                "pane_id": z_pane["pane_id"],
                "name": z_pane["name"],
                "detail": f"终端窗格 {z_pane['name']} 处于异常或卡死状态 ({z_pane['reason']})",
            }
        )
    dirty_unmerged_wts = [
        wt for wt in worktree_report["preserved_worktrees"]
        if wt.get("is_dirty") and not wt.get("has_active_pane")
    ]
    for u_wt in dirty_unmerged_wts:
        wt_path_obj = Path(str(u_wt["worktree"]))
        try:
            age_sec = datetime.now(CST).timestamp() - wt_path_obj.stat().st_mtime
        except OSError:
            age_sec = 0.0
        # Historical archived worktrees untouched for >7 days are preserved safely without noisy daily alerts
        if age_sec > 7 * 86400:
            continue
        anomalies.append(
            {
                "kind": "unattended_dirty_worktree",
                "repo": u_wt["repo"],
                "worktree": wt_path_obj.name,
                "detail": f"工作树 {wt_path_obj.name} 含未提交改动且无活跃窗格，已安全保留",
            }
        )

    return {
        "ok": True,
        "herdr": herdr_report,
        "worktrees": worktree_report,
        "main_checkouts": main_report,
        "anomalies": anomalies,
        "anomaly_count": len(anomalies),
    }


def run_safe_gc(
    *,
    dev_root: Path,
    handoff_root: Path | None = None,
    state_dir: Path | None = None,
    repos: Sequence[str] = DEFAULT_CORE_REPOS,
    herdr_json: Path | Mapping[str, Any] | None = None,
    dry_run: bool = False,
    scan_result: Mapping[str, Any] | None = None,
    runner: Runner = _default_runner,
    merge_checker: Callable[[Path, str | None], bool] | None = None,
) -> dict[str, Any]:
    resolved_dev = Path(dev_root).expanduser()
    resolved_handoff = Path(handoff_root).expanduser() if handoff_root is not None else (resolved_dev / "handoff")
    resolved_state = (
        Path(state_dir).expanduser()
        if state_dir is not None
        else (Path.home() / ".local" / "state" / "git-gc")
    )

    main_remediation = remediate_main_checkouts(
        dev_root=resolved_dev,
        repos=repos,
        fetch_remote=False,
        dry_run=dry_run,
        runner=runner,
    )

    snapshot = dict(
        scan_result
        if (scan_result is not None and dry_run)
        else scan_environment(
            dev_root=resolved_dev,
            handoff_root=resolved_handoff,
            repos=repos,
            herdr_json=herdr_json,
            runner=runner,
            merge_checker=merge_checker,
        )
    )

    closed_panes: list[dict[str, Any]] = []
    pane_errors: list[dict[str, Any]] = []
    for pane in snapshot["herdr"]["reclaimable_panes"]:
        pane_id = str(pane.get("pane_id") or "")
        ws_id = str(pane.get("workspace_id") or "")
        if not pane_id or ws_id in PROTECTED_WORKSPACE_IDS or pane_id.startswith(("w1:", "w2J:")):
            continue
        if dry_run:
            closed_panes.append({**pane, "action": "dry_run_close"})
            continue
        proc = runner(["herdr", "pane", "close", pane_id], check=False)
        if proc.returncode == 0:
            closed_panes.append({**pane, "action": "closed"})
        else:
            pane_errors.append({**pane, "error": (proc.stderr or proc.stdout or "").strip()})

    git_gc = _load_git_gc()
    reclaimed_worktrees: list[dict[str, Any]] = []
    worktree_errors: list[dict[str, Any]] = []
    for wt_entry in snapshot["worktrees"]["clean_merged_worktrees"]:
        # Triple-check safety invariants before touching any worktree
        if (
            wt_entry.get("is_dirty") != 0
            or wt_entry.get("is_merged_to_default") != 1
            or wt_entry.get("has_active_pane") != 0
            or wt_entry.get("is_held") != 0
        ):
            continue
        repo_path = Path(str(wt_entry["repo_path"]))
        wt_path = Path(str(wt_entry["worktree"]))
        branch = wt_entry.get("branch")
        if dry_run:
            reclaimed_worktrees.append(
                {
                    "repo": wt_entry["repo"],
                    "worktree": str(wt_path),
                    "branch": branch,
                    "action": "dry_run_reclaim",
                }
            )
            continue
        try:
            _remove_runtime_cache_in_merged_worktree(wt_path)
            removed_items: list[str] = []
            git_gc.clean_noise(wt_path)
            wt_rm = runner(["git", "-C", str(repo_path), "worktree", "remove", str(wt_path)], check=False)
            if wt_rm.returncode == 0:
                removed_items.append(str(wt_path))
                runner(["git", "-C", str(repo_path), "worktree", "prune"], check=False)
                if branch:
                    br_rm = runner(["git", "-C", str(repo_path), "branch", "-D", str(branch)], check=False)
                    if br_rm.returncode == 0:
                        removed_items.append(f"local:{branch}")
            else:
                worktree_errors.append(
                    {
                        "repo": wt_entry["repo"],
                        "worktree": str(wt_path),
                        "branch": branch,
                        "error": (wt_rm.stderr or wt_rm.stdout or "").strip()[:200],
                    }
                )
                continue
        except (OSError, subprocess.SubprocessError) as exc:
            worktree_errors.append(
                {
                    "repo": wt_entry["repo"],
                    "worktree": str(wt_path),
                    "branch": branch,
                    "error": str(exc),
                }
            )
            continue
        if removed_items:
            reclaimed_worktrees.append(
                {
                    "repo": wt_entry["repo"],
                    "worktree": str(wt_path),
                    "branch": branch,
                    "action": "reclaimed",
                    "removed": removed_items,
                }
            )

    # Always run git-gc.py daily-cleanup with --dry-run on the subprocess sweep so that
    # unguarded sweep(apply_tier1=True) inside git-gc.py can NEVER delete held or active-pane
    # worktrees; worktree reclamation is exclusively owned by the guarded loop above.
    daily_summary: dict[str, Any] = {}
    daily_cmd = [
        sys.executable,
        str(GIT_GC_SCRIPT_PATH),
        "daily-cleanup",
        "--dev-root",
        str(resolved_dev),
        "--worktrees-root",
        str(resolved_dev / "worktrees"),
        "--handoff-root",
        str(resolved_handoff),
        "--state-dir",
        str(resolved_state),
        "--repos",
        ",".join(repos),
        "--dry-run",
    ]
    daily_proc = runner(daily_cmd, check=False)
    if daily_proc.returncode == 0 and (daily_proc.stdout or "").strip():
        try:
            daily_summary = json.loads(daily_proc.stdout)
        except ValueError:
            daily_summary = {"raw_stdout": daily_proc.stdout[:200]}

    return {
        "ok": True,
        "dry_run": dry_run,
        "closed_panes": closed_panes,
        "pane_close_errors": pane_errors,
        "reclaimed_worktrees": reclaimed_worktrees,
        "worktree_reclaim_errors": worktree_errors,
        "preserved_worktrees_count": len(snapshot["worktrees"]["preserved_worktrees"]),
        "fast_forwarded_repos": main_remediation["fast_forwarded_repos"],
        "fast_forward_errors": main_remediation["fast_forward_errors"],
        "excluded_runtime_cache_repos": main_remediation["excluded_runtime_cache_repos"],
        "daily_cleanup": daily_summary,
    }


def _extract_prob(raw: Any, field: str) -> float | None:
    if isinstance(raw, Mapping):
        raw = raw.get(field, raw.get("value"))
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val) or val < 0.0 or val > 1.0:
        return None
    return val


def evaluate_hygiene_with_jev(
    scan_report: Mapping[str, Any],
    *,
    gc_report: Mapping[str, Any] | None = None,
    mock_jev_json: Any = None,
    jev_fn: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    anomalies = list(scan_report.get("anomalies") or [])
    pane_errors = list((gc_report or {}).get("pane_close_errors") or [])
    total_anomalies = len(anomalies) + len(pane_errors)

    if total_anomalies == 0:
        return {
            "actionable": False,
            "classification": "CLEAN_OR_AUTO_HEALED",
            "probability": 0.0,
            "urgency_score": 0.0,
            "decision_source": "layer0_zero_anomalies",
        }

    has_dirty_or_zombie = any(
        a.get("kind") in {"dirty_main_checkout", "main_checkout_off_main", "zombie_herdr_pane"}
        for a in anomalies
    ) or bool(pane_errors)
    deterministic_fallback = has_dirty_or_zombie or total_anomalies > 0

    questions = {
        "hygiene_classification": {
            "type": "choice",
            "instructions": "Classify whether the local repository/Herdr hygiene anomalies require Owner attention.",
            "criteria": {
                "ACTIONABLE_HYGIENE_DRIFT": "Dirty main checkouts, off-main checkouts, behind-origin drift, or zombie panes require Owner review.",
                "BENIGN_TRANSIENT_NOISE": "Harmless transient state that does not need an Owner alert issue.",
            },
        },
        "actionable_confidence": {
            "type": "noul",
            "instructions": "Probability from 0.0 to 1.0 that these residual hygiene anomalies are actionable (>0.70 wakes Owner issue).",
        },
        "urgency_score": {
            "type": "score",
            "instructions": "Score urgency from 0.0 (low) to 1.0 (high risk of merge conflict or lost local work).",
        },
    }
    state_payload = {
        "loop_id": LOOP_ID,
        "anomaly_count": total_anomalies,
        "anomalies": anomalies[:10],
        "pane_close_errors": pane_errors[:5],
    }

    decide = jev_fn or _load_jev_client().jev_decide
    response = decide(
        state=state_payload,
        questions=questions,
        agent_role="team-lead",
        goal_id=f"loop-{LOOP_ID}",
        timeout_sec=1.5,
        falcon_only=True,
        mock_response=mock_jev_json,
    )

    if not isinstance(response, Mapping) or response.get("fallback_reason"):
        return {
            "actionable": deterministic_fallback,
            "classification": "ACTIONABLE_HYGIENE_DRIFT" if deterministic_fallback else "BENIGN_TRANSIENT_NOISE",
            "probability": None,
            "urgency_score": None,
            "decision_source": "deterministic_fallback_unavailable",
        }

    answers = response.get("answers", response)
    if not isinstance(answers, Mapping):
        return {
            "actionable": deterministic_fallback,
            "classification": "ACTIONABLE_HYGIENE_DRIFT" if deterministic_fallback else "BENIGN_TRANSIENT_NOISE",
            "probability": None,
            "urgency_score": None,
            "decision_source": "deterministic_fallback_malformed",
        }

    raw_choice = answers.get("hygiene_classification")
    if isinstance(raw_choice, Mapping):
        raw_choice = raw_choice.get("choice")
    choice = str(raw_choice or "").strip()

    prob = _extract_prob(answers.get("actionable_confidence"), "noul")
    if prob is None:
        prob = _extract_prob(answers.get("actionable_probability"), "noul")
    urgency = _extract_prob(answers.get("urgency_score"), "score")

    if choice not in {"ACTIONABLE_HYGIENE_DRIFT", "BENIGN_TRANSIENT_NOISE"} or prob is None:
        return {
            "actionable": deterministic_fallback,
            "classification": "ACTIONABLE_HYGIENE_DRIFT" if deterministic_fallback else "BENIGN_TRANSIENT_NOISE",
            "probability": prob,
            "urgency_score": urgency,
            "decision_source": "deterministic_fallback_incomplete_jev",
        }

    if choice == "BENIGN_TRANSIENT_NOISE" and prob <= 0.70:
        return {
            "actionable": False,
            "classification": choice,
            "probability": prob,
            "urgency_score": urgency,
            "decision_source": "jev_benign_suppress",
        }

    if prob > 0.70:
        return {
            "actionable": True,
            "classification": "ACTIONABLE_HYGIENE_DRIFT",
            "probability": prob,
            "urgency_score": urgency,
            "decision_source": "jev_high_confidence",
        }

    if prob < 0.30:
        return {
            "actionable": False,
            "classification": "BENIGN_TRANSIENT_NOISE",
            "probability": prob,
            "urgency_score": urgency,
            "decision_source": "jev_low_confidence_reject",
        }

    return {
        "actionable": deterministic_fallback,
        "classification": "ACTIONABLE_HYGIENE_DRIFT" if deterministic_fallback else "BENIGN_TRANSIENT_NOISE",
        "probability": prob,
        "urgency_score": urgency,
        "decision_source": "jev_grey_zone_fallback",
    }


def _sanitize_human_text(text: str) -> str:
    """Strip commit-like hex tokens or internal codes so Dual-Zone Human Zone stays clean."""
    cleaned = re.sub(r"\b[0-9a-f]{7,40}\b", "快照", text)
    cleaned = re.sub(
        r"(?<![A-Za-z0-9_-])(?:S(?:1[0-2]|[1-9])|M[1-5]|EV-\d{1,4}|DC-\d{1,4}|H[1-5]|R10|V[1-5])(?![A-Za-z0-9_-])",
        "运行态",
        cleaned,
    )
    return cleaned


def _build_human_items(scan_report: Mapping[str, Any], gc_report: Mapping[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for d_repo in scan_report.get("main_checkouts", {}).get("dirty_main_repos", []):
        repo_name = _sanitize_human_text(str(d_repo.get("repo") or "仓库"))
        branch_name = _sanitize_human_text(str(d_repo.get("branch") or "main"))
        dirty_sample = _sanitize_human_text(
            ", ".join(str(x).split()[-1] for x in (d_repo.get("dirty_files") or [])[:2])
        )[:36]
        if int(d_repo.get("dirty_count") or 0) > 0:
            items.append(
                {
                    "what": f"清理或归档 `{repo_name}` 主分支的 {d_repo['dirty_count']} 个未提交文件",
                    "why": f"{repo_name} ({branch_name}) 含 `{dirty_sample or '未跟踪改动'}`，需保持主分支基线干净",
                }
            )
        elif d_repo.get("non_default_checkout"):
            items.append(
                {
                    "what": f"将 `{repo_name}` 主工作目录切回默认主分支",
                    "why": f"{repo_name} 当前停留在特性分支 `{branch_name}`，易引发主仓误提交",
                }
            )
    for b_repo in scan_report.get("main_checkouts", {}).get("behind_origin_repos", []):
        repo_name = _sanitize_human_text(str(b_repo.get("repo") or "仓库"))
        items.append(
            {
                "what": f"快进同步 `{repo_name}` 主分支（落后远端 {b_repo['behind_origin_main']} 个提交）",
                "why": f"{repo_name} 落后远端主线 {b_repo['behind_origin_main']} 个提交，需对齐最新基线",
            }
        )
    for z_pane in scan_report.get("herdr", {}).get("zombie_panes", []):
        pane_label = _sanitize_human_text(str(z_pane.get("name") or z_pane.get("pane_id") or "异常窗格"))
        reason_label = _sanitize_human_text(str(z_pane.get("reason") or "blocked"))
        items.append(
            {
                "what": f"检查并处置异常滞留窗格 `{pane_label}`",
                "why": f"会话 `{pane_label}` 处于 `{reason_label}` 滞留状态，需确认后回收",
            }
        )
    for p_wt in scan_report.get("worktrees", {}).get("preserved_worktrees", []):
        if p_wt.get("is_dirty") and not p_wt.get("has_active_pane"):
            wt_label = _sanitize_human_text(Path(str(p_wt.get("worktree") or "工作树")).name)
            repo_label = _sanitize_human_text(str(p_wt.get("repo") or "repo"))
            items.append(
                {
                    "what": f"审阅已保留的未合并脏工作树 `{wt_label}`",
                    "why": f"{repo_label} 工作树 `{wt_label}` 含未提交改动且无活跃会话，已跳过自动删除",
                }
            )
    if not items:
        reclaimed_n = len(gc_report.get("reclaimed_worktrees") or [])
        closed_n = len(gc_report.get("closed_panes") or [])
        items.append(
            {
                "what": f"确认本次环境卫生自动回收结果（关窗 {closed_n} 个、回收工作树 {reclaimed_n} 个）",
                "why": "本地代码与终端环境已完成安全巡检",
            }
        )
    return items[:6]


def run_loop_check(
    *,
    dev_root: Path,
    handoff_root: Path | None = None,
    state_dir: Path | None = None,
    target_date: date | None = None,
    repos: Sequence[str] = DEFAULT_CORE_REPOS,
    herdr_json: Path | Mapping[str, Any] | None = None,
    fetch_remote: bool = False,
    auto_gc: bool = True,
    dry_run: bool = False,
    mock_jev_json: Any = None,
    runner: Runner = _default_runner,
    merge_checker: Callable[[Path, str | None], bool] | None = None,
    jev_fn: Callable[..., dict[str, Any] | None] | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    resolved_date = target_date or datetime.now(CST).date()
    date_str = resolved_date.isoformat()

    scan_report = scan_environment(
        dev_root=dev_root,
        handoff_root=handoff_root,
        repos=repos,
        herdr_json=herdr_json,
        fetch_remote=fetch_remote,
        runner=runner,
        merge_checker=merge_checker,
        use_cache=use_cache,
    )

    if auto_gc:
        if fetch_remote and not dry_run:
            for repo_name in repos:
                repo_dir = (Path(dev_root).expanduser() / repo_name).expanduser()
                if (repo_dir / ".git").exists():
                    runner(["git", "-C", str(repo_dir), "fetch", "--quiet", "origin"], check=False)
        gc_report = run_safe_gc(
            dev_root=dev_root,
            handoff_root=handoff_root,
            state_dir=state_dir,
            repos=repos,
            herdr_json=herdr_json,
            dry_run=dry_run,
            scan_result=scan_report if dry_run else None,
            runner=runner,
            merge_checker=merge_checker,
        )
        if not dry_run:
            scan_report = scan_environment(
                dev_root=dev_root,
                handoff_root=handoff_root,
                repos=repos,
                herdr_json=herdr_json,
                fetch_remote=False,
                runner=runner,
                merge_checker=merge_checker,
                use_cache=use_cache,
            )
    else:
        gc_report = {
            "ok": True,
            "dry_run": dry_run,
            "closed_panes": [],
            "pane_close_errors": [],
            "reclaimed_worktrees": [],
            "fast_forwarded_repos": [],
            "fast_forward_errors": [],
            "excluded_runtime_cache_repos": [],
            "preserved_worktrees_count": len(scan_report["worktrees"]["preserved_worktrees"]),
            "daily_cleanup": {},
        }

    jev_verdict = evaluate_hygiene_with_jev(
        scan_report,
        gc_report=gc_report,
        mock_jev_json=mock_jev_json,
        jev_fn=jev_fn,
    )

    anomalies = scan_report["anomalies"]
    sig = _compute_signature(
        {
            "anomalies": [(a.get("kind"), a.get("repo"), a.get("name"), a.get("worktree")) for a in anomalies],
            "pane_errors": [e.get("pane_id") for e in gc_report.get("pane_close_errors", [])],
        }
    )
    cursor = f"{LOOP_ID}-{date_str}"
    watermark = f"{LOOP_ID}:{date_str}:{sig}"
    title = f"[P3 / Priority 3][Local Hygiene] {date_str} 本地环境与主仓卫生异常巡检清单"

    closed_n = len(gc_report.get("closed_panes") or [])
    reclaimed_n = len(gc_report.get("reclaimed_worktrees") or [])
    summary = _sanitize_human_text(
        f"自动安全回收 {closed_n} 个完结窗格与 {reclaimed_n} 个已合并工作树；"
        f"发现 {len(anomalies)} 项主仓脏文件或环境漂移需确认。"
    )
    items = _build_human_items(scan_report, gc_report)

    issue_brief = _load_issue_brief()
    dual_zone_md = issue_brief.render_dual_zone_issue(
        tldr=summary,
        items=items,
        cursor=cursor,
        ai_payload={
            "loop_id": LOOP_ID,
            "date": date_str,
            "watermark": watermark,
            "auto_gc": {
                "closed_panes": gc_report.get("closed_panes"),
                "reclaimed_worktrees": gc_report.get("reclaimed_worktrees"),
                "fast_forwarded_repos": gc_report.get("fast_forwarded_repos"),
                "excluded_runtime_cache_repos": gc_report.get("excluded_runtime_cache_repos"),
                "preserved_worktrees_count": gc_report.get("preserved_worktrees_count"),
            },
            "anomalies": anomalies,
            "jev": jev_verdict,
        },
    )
    verification = issue_brief.verify_dual_zone_issue(dual_zone_md)
    if not verification["ok"]:
        raise ValueError(f"Dual-Zone hygiene issue failed verification: {verification['violations']}")

    severity = "high" if any(a.get("kind") in {"dirty_main_checkout", "zombie_herdr_pane"} for a in anomalies) else (
        "medium" if anomalies else "low"
    )
    actionable = bool(jev_verdict.get("actionable"))

    return {
        "ok": True,
        "loop_id": LOOP_ID,
        "date": date_str,
        "has_delta": len(anomalies) > 0,
        "delta_count": len(anomalies),
        "anomalies": anomalies,
        "anomaly_count": len(anomalies),
        "auto_gc": {
            "closed_panes": gc_report.get("closed_panes", []),
            "reclaimed_worktrees": gc_report.get("reclaimed_worktrees", []),
            "fast_forwarded_repos": gc_report.get("fast_forwarded_repos", []),
            "excluded_runtime_cache_repos": gc_report.get("excluded_runtime_cache_repos", []),
            "preserved_worktrees_count": gc_report.get("preserved_worktrees_count", 0),
        },
        "actionable": actionable,
        "severity": severity,
        "watermark": watermark,
        "cursor": cursor,
        "title": title,
        "summary": summary,
        "items": items,
        "dual_zone_markdown": dual_zone_md if actionable else "",
        "alert_issue": {
            "title": title,
            "priority": "medium",
            "status": "todo",
            "assignee_email": "lee@multica.local",
            "cursor": cursor,
            "markdown": dual_zone_md,
            "dual_zone_ok": verification["ok"],
        }
        if actionable
        else None,
        "jev": jev_verdict,
        "gc": gc_report,
        "scan": scan_report,
        "raw": {
            "anomaly_count": len(anomalies),
            "closed_panes_count": closed_n,
            "reclaimed_worktrees_count": reclaimed_n,
            "preserved_worktrees_count": gc_report.get("preserved_worktrees_count", 0),
        },
    }


def _parse_repos_arg(raw: str) -> tuple[str, ...]:
    if not raw or not raw.strip():
        return DEFAULT_CORE_REPOS
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _build_parser() -> argparse.ArgumentParser:
    parser = _SelfDisclosingHygieneParser(
        prog="local_hygiene_patrol.py",
        description="Loop 3A: 0-Agent Herdr & Worktree Hygiene Patrol + Safe Auto-GC + Jev Alerting",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    for name, help_text in (
        ("scan", "Scan Herdr orphan/completed panes, worktrees, and core main checkouts without mutating"),
        ("run-gc", "Run safe Auto-GC on GOAL_DONE M1 panes, clean merged worktrees, runtime excludes, and --ff-only pulls"),
        ("collect", "Run safe auto-remediation (excludes + --ff-only + GC) and emit full hygiene snapshot"),
        ("loop-check", "Run full Stage 1 scan + safe Auto-GC -> Stage 2 Jev -> Stage 3 Dual-Zone payload"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--dev-root",
            type=Path,
            default=Path(os.environ.get("HYGIENE_PATROL_DEV_ROOT", "~/Development")).expanduser(),
        )
        p.add_argument("--handoff-root", type=Path, default=None)
        p.add_argument("--repos", default=",".join(DEFAULT_CORE_REPOS))
        p.add_argument("--herdr-json", type=Path, default=None)
        p.add_argument("--json", action="store_true", default=True)
        p.add_argument("--no-cache", action="store_true", help="Bypass the 30-second stat-watermarked scan cache")
        if name in {"scan", "collect", "loop-check"}:
            p.add_argument("--fetch-remote", action="store_true")
        if name in {"run-gc", "collect", "loop-check"}:
            p.add_argument("--state-dir", type=Path, default=None)
            p.add_argument("--dry-run", action="store_true")
        if name in {"collect", "loop-check"}:
            p.add_argument("--date", default="today")
            p.add_argument("--mock-jev-json", default=None)
            p.add_argument("--no-gc", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(sys.argv[1:] if argv is None else argv)
    repos = _parse_repos_arg(ns.repos)

    if ns.subcommand == "scan":
        payload = scan_environment(
            dev_root=ns.dev_root,
            handoff_root=ns.handoff_root,
            repos=repos,
            herdr_json=ns.herdr_json,
            fetch_remote=ns.fetch_remote,
            use_cache=not ns.no_cache,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if ns.subcommand == "run-gc":
        payload = run_safe_gc(
            dev_root=ns.dev_root,
            handoff_root=ns.handoff_root,
            state_dir=ns.state_dir,
            repos=repos,
            herdr_json=ns.herdr_json,
            dry_run=ns.dry_run,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if ns.subcommand in {"collect", "loop-check"}:
        target = datetime.now(CST).date() if ns.date == "today" else date.fromisoformat(ns.date)
        payload = run_loop_check(
            dev_root=ns.dev_root,
            handoff_root=ns.handoff_root,
            state_dir=ns.state_dir,
            target_date=target,
            repos=repos,
            herdr_json=ns.herdr_json,
            fetch_remote=ns.fetch_remote,
            auto_gc=not ns.no_gc,
            dry_run=ns.dry_run,
            mock_jev_json=ns.mock_jev_json,
            use_cache=not ns.no_cache,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
