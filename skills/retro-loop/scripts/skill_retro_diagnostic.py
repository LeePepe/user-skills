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

import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Mapping, Sequence

CST = timezone(timedelta(hours=8))

DEFAULT_AIDATA_DIR = Path.home() / "Development" / "AIDash" / "aidata"
DEFAULT_WAREHOUSE_DB = DEFAULT_AIDATA_DIR / "L3_merge" / "warehouse.db"
DEFAULT_PROPOSALS_PATH = DEFAULT_AIDATA_DIR / "L5_apps" / "digest" / "state" / "proposals.jsonl"
DEFAULT_COPILOT_DB = Path.home() / ".copilot" / "session-store.db"
DEFAULT_COPILOT_SESSIONS_DIR = Path.home() / ".copilot" / "session-state"
DEFAULT_USAGE_JSONL = Path.home() / ".local" / "share" / "skill-telemetry" / "usage.jsonl"
DEFAULT_HANDOFF_ROOT = Path.home() / "Development" / "handoff"
DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEV_ROOT = Path.home() / "Development"
DEFAULT_ICLOUD_SYNC_DIR = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "AIDashSync"

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
        "xcode-cli",
        lambda files, cmds: any("xcodebuild" in c or "xcrun simctl" in c for c in cmds),
    ),
)

_SECRET_SCRUB_RE = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._-]+|gh[pousr]_[A-Za-z0-9_]+|lin_api_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,})"
)


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
    bash_commands: list[tuple[str, int | None, float | None, int]] = field(default_factory=list)
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
    max_sessions: int = 250,
) -> dict[str, SessionObservation]:
    observations: dict[str, SessionObservation] = {}
    if sessions_dir is None or not sessions_dir.is_dir():
        return observations

    candidate_files: list[tuple[float, Path]] = []
    with contextlib.suppress(OSError):
        for child in sessions_dir.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            ev_file = child / "events.jsonl"
            if not ev_file.is_file():
                continue
            with contextlib.suppress(OSError):
                mtime = ev_file.stat().st_mtime
                mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone(CST)
                if mtime_dt >= window_start:
                    candidate_files.append((mtime, ev_file))

    candidate_files.sort(key=lambda item: -item[0])
    for mtime, ev_file in candidate_files[:max_sessions]:
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
            with ev_file.open("r", encoding="utf-8", errors="replace") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
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
                    elif ev_type == "tool.execution_complete":
                        tc_id = str(data.get("toolCallId") or "")
                        res_obj = data.get("result")
                        res_text = str(res_obj.get("content") or "") if isinstance(res_obj, dict) else ""
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
) -> None:
    rows: list[Mapping[str, Any]] = []
    if usage_jsonl is not None and usage_jsonl.is_file():
        with contextlib.suppress(OSError):
            with usage_jsonl.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    with contextlib.suppress(json.JSONDecodeError):
                        parsed = json.loads(line)
                        if isinstance(parsed, dict):
                            rows.append(parsed)
    rows.extend(shard_records)

    for idx, rec in enumerate(rows):
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
            if not any(c.skill == sk and c.script_rel == sc_rel and c.exit_code == ec for c in obs.script_calls):
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
    if trace_export_path is None or not trace_export_path.is_file():
        return 0
    ingested_spans = 0
    try:
        raw_text = trace_export_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return 0
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

    if warehouse_db is not None and warehouse_db.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{warehouse_db}?mode=ro", uri=True)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
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
                            elif cmd_or_p:
                                obs.bash_commands.append((cmd_or_p, t.get("exit_code"), t.get("duration_ms"), obs.next_seq()))
                        elif str(t).lower() in {"edit", "write", "create", "apply_patch"}:
                            obs.edit_count += 1

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

                for (g_id, t_id), runs in grouped_tasks.items():
                    max_r = max(int(r.get("round_no") or 1) for r in runs)
                    passed_later = any(str(r.get("v5_verdict") or "").upper() in {"PASS", "CONCERNS", "WAIVED"} and int(r.get("round_no") or 1) >= 2 for r in runs)
                    still_failed_r3 = max_r >= 3 or (max_r >= 2 and not passed_later and any(str(r.get("v5_verdict") or "").upper() == "FAIL" for r in runs))
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
                        meta["r1_to_r2_converged_tasks"].append({"goal_id": g_id, "task_id": t_id, "skills": sorted(sk_set)})
                    elif still_failed_r3:
                        meta["directional_drift_tasks"].append({"goal_id": g_id, "task_id": t_id, "max_round": max_r, "skills": sorted(sk_set)})
                        for sk in sk_set:
                            bump_escape(sk, "impl_bug")

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
                    sk_hits = {_norm_skill(m.group("ctx") or m.group("path") or m.group("call") or "") for m in _SKILL_CONTEXT_RE.finditer(note)}
                    sk_hits.discard("")
                    if not sk_hits and "design" in g_id.lower():
                        sk_hits.add("my-designer")
                    if evt in {"CHANGE_REQUEST", "NEED_OWNER_DECISION"}:
                        meta["directional_drift_tasks"].append({"goal_id": g_id, "task_id": str(row.get("task_id") or ""), "event": evt, "skills": sorted(sk_hits)})
                        for sk in sk_hits:
                            bump_escape(sk, "spec_gap")
                    elif evt == "REVIEW_FAIL":
                        kind = "spec_gap" if "SPEC_GAP" in note.upper() else "impl_bug"
                        for sk in sk_hits:
                            bump_escape(sk, kind)

    if copilot_db is not None and copilot_db.is_file():
        with contextlib.closing(sqlite3.connect(f"file:{copilot_db}?mode=ro", uri=True)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
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
    if handoff_root is None or not handoff_root.is_dir():
        return summary

    notes_counter: dict[str, dict[str, Any]] = {}
    for goal_dir in sorted(handoff_root.iterdir()):
        if not goal_dir.is_dir() or goal_dir.name.startswith("_"):
            continue
        receipts_dir = goal_dir / "receipts"
        if not receipts_dir.is_dir():
            continue
        for sel_file in sorted(receipts_dir.iterdir()):
            if not sel_file.is_file() or not _SELECTION_FILE_RE.match(sel_file.name):
                continue
            try:
                doc = json.loads(sel_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
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


def emit_skill_retro_briefs(
    report: Mapping[str, Any],
    emit_dir: Path,
    *,
    skill_filter: str | None = None,
) -> dict[str, Any]:
    """Generate per-skill isolated Writer & Reviewer briefs reusing standard M1 TL -> Writer -> Reviewer slots."""
    briefs_dir = emit_dir / "briefs" if emit_dir.name != "briefs" else emit_dir
    briefs_dir.mkdir(parents=True, exist_ok=True)

    norm_filter = _norm_skill(skill_filter) if skill_filter else ""
    l0 = report.get("layer0_direction_and_triggers") or {}
    l1 = report.get("layer1_drawer_metabolism") or {}
    l2 = report.get("layer2_script_efficacy_and_speed") or {}
    l3 = report.get("layer3_wild_patterns_and_eval") or {}
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

    emitted: list[dict[str, Any]] = []
    for sk in sorted(target_skills):
        safe_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", sk).strip("-") or "skill"
        sk_triggers = [x for x in (l0.get("trigger_misses") or []) if x.get("skill") == sk]
        sk_empty = [x for x in (l0.get("zero_action_loads") or []) if x.get("skill") == sk]
        sk_hot = [x for x in (l1.get("hot_drawers") or []) if x.get("skill") == sk]
        sk_ondemand = [x for x in (l1.get("on_demand_correction_drawers") or []) if x.get("skill") == sk]
        sk_unwired = [x for x in (l1.get("unwired_disconnected_drawers") or []) if x.get("skill") == sk]
        sk_deadwood = [x for x in (l1.get("true_zero_hit_deadwood") or []) if x.get("skill") == sk]
        sk_catchers = [x for x in (l2.get("true_catchers") or []) if x.get("skill") == sk]
        sk_ceremonial = [x for x in (l2.get("ceremonial_rubber_stamps") or []) if x.get("skill") == sk]
        sk_roadblocks = [x for x in (l2.get("rigid_roadblocks") or []) if x.get("skill") == sk]
        sk_sinks = [x for x in (l2.get("latency_sinks") or []) if x.get("skill") in {sk, "repo-gate"}]
        sk_wild = [x for x in (l3.get("wild_patterns") or []) if x.get("candidate_skill") == sk]
        sk_proposals = [x for x in proposals if x.get("skill") == sk]
        sk_held = [x for x in obs_pool if x.get("skill") == sk]

        writer_path = briefs_dir / f"skill-retro-{safe_slug}-writer.md"
        reviewer_path = briefs_dir / f"skill-retro-{safe_slug}-reviewer.md"

        # Build Writer Brief (reusing standard `writer` / `writer_escalated` role slot)
        w_lines: list[str] = [
            f"# Writer Brief: `{sk}` — 4-Layer Progressive Skill Self-Evolution",
            "",
            f"- **Role Slot**: `writer` (standard `M1 TL -> Writer -> Reviewer` pipeline; zero new agent role types)",
            f"- **Target Skill**: `{sk}` (strict per-skill isolation — do NOT edit sibling skills or scheduler runners)",
            "- **Background**: Evolve this skill contract, drawers, and deterministic scripts from observed runtime telemetry using runtime progressive disclosure.",
            "- **Owned Paths**:",
            f"  - `skills/{sk}/**`",
            f"  - `scripts/test_{sk.replace('-', '_')}_*.py`",
            "  - `scripts/test_skill_retro.py`",
            "- **Interfaces**:",
            f"  - **Consumes**: `skills/{sk}/SKILL.md`, `skills/{sk}/references/*`, `skills/{sk}/scripts/*`, `retro_story_engine.py skill-retro --skill {sk} --json`",
            f"  - **Produces**: Softened CLI compatibility aliases, self-disclosing error guidance (`3 lines + references/*.md` pointer), and Positive + Counter-Case Replay tests",
            "",
            "## Observed 4-Layer Telemetry Evidence",
            "",
        ]

        if sk_roadblocks:
            w_lines.append("### Layer 2 — Rigid CLI Roadblocks (`exit_code = 2` Friction)")
            for rb in sk_roadblocks:
                w_lines.append(
                    f"- `{rb['script']}`: `exit=2` count `{rb['exit_2_count']}` across `{rb['exit_2_sessions']}` session(s) "
                    f"(arg-only retry sessions: `{rb['arg_retry_sessions']}`)"
                )
                for sample in rb.get("failing_command_samples") or []:
                    w_lines.append(f"  - Failing command sample: `{sample}`")
            w_lines.append("")

        w_lines.append("### Layer 1 — Context-Aware Reference Drawer Metabolism (`hits` vs `scenario_sessions`)")
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
            w_lines.append("- No drawer anomalies recorded in current window (preserve low-frequency scenario docs).")
        w_lines.append("")

        if sk_sinks or sk_ceremonial or sk_catchers:
            w_lines.append("### Layer 2 — Script Efficacy & Multi-Angle Latency Breakdown")
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
                    f"- **Latency Sink**: `{ls['script']}` — `max_ms={ls['max_ms']}`, `p50_ms={ls['p50_ms']}`, "
                    f"`primary_bottleneck={ls['primary_bottleneck']}`, `nested_reruns={ls['nested_rerun_calls']}` "
                    f"({'; '.join(ls.get('attribution_angles') or [])})"
                )
            w_lines.append("")

        if sk_triggers or sk_empty or sk_wild:
            w_lines.append("### Layer 0 & Layer 3 — Trigger Guardrails & Wild Manual Probes")
            for tm in sk_triggers:
                w_lines.append(
                    f"- **L0 Trigger Miss**: missed `{tm['missed_sessions']}/{tm['domain_sessions']}` domain sessions (`miss_rate={tm['miss_rate']}`)"
                )
            for ze in sk_empty:
                w_lines.append(
                    f"- **L0 Zero-Action Load**: `{ze['zero_action_sessions']}/{ze['loads']}` loads had 0 actions"
                )
            for wp in sk_wild:
                w_lines.append(
                    f"- **L3 Wild Manual Pattern (`>={WILD_PATTERN_MIN_COUNT}x`)**: `{wp['pattern_signature']}` (`{wp['occurrences']}` occurrences across `{wp['session_count']}` sessions)"
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

        # Build Reviewer Brief (reusing standard `reviewer` role slot + `code-review` & `skill-contract`)
        r_lines: list[str] = [
            f"# Reviewer Brief: `{sk}` — Progressive Evolution & Anti-Rigidity Gate",
            "",
            "- **Role Slot**: `reviewer` (standard `M1 TL -> Writer -> Reviewer` cross-vendor V1–V5 review; zero new agent role types)",
            f"- **Skills to Load**: `code-review`, `skill-contract` (target skill: `{sk}`)",
            f"- **Companion Writer Brief**: `{writer_path.name}`",
            f"- **Targeted Proposals**: `{len(sk_proposals)}` actionable (`{', '.join(str(p.get('id')) for p in sk_proposals) or 'baseline-audit'}`)",
            "",
            "## V1–V5 Review Checklist (Anti-Overcorrection & Replay Proof)",
            "",
            "1. **Per-Skill Scope Isolation (`Axis 1`)**:",
            f"   - Confirm diff stays strictly within `skills/{sk}/**` and its targeted test file(s); reject cross-skill or scheduler/cron drift (`feedback_guard.py pre-review-check`).",
            "   - Confirm `SKILL.md` and `references/*.md` pass `skill_contract.py audit --skill " + sk + " --strict` (L0/L1 line & byte caps, zero private identifiers).",
            "2. **Reject Global-Default Over-Correction (`Scope Triage Gate`)**:",
            "   - **FAIL (`[SPEC_GAP]` / `[IMPL_BUG]`)** if a scenario-specific preference was promoted into a global unconditional default instead of a scenario-conditioned branch.",
            "   - **FAIL** if low-occurrence scenario drawers (`scenario_sessions < 5`) were prematurely deleted as deadwood.",
            "3. **Reject Rigid Hardcoding & Opaque Errors (`Anti-Rigidity Gate`)**:",
            "   - **FAIL** if newly crystallized Layer 3 helpers hard-lock subjective choices (`exit=1`) instead of factual collection / soft advisory (`exit=0`).",
            f"   - Verify script error paths emit concise self-disclosing guidance pointing to `skills/{sk}/references/*.md`.",
            "4. **Verify Positive + Counter-Case Replay Execution (`Axis 2`)**:",
            "   - Verify the test suite executes BOTH a **Positive Case** (historical `exit=2` / escape fixed) AND a **Counter-Case** (neighboring scenario preserved without false-positive regression).",
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

        emitted.append(
            {
                "skill": sk,
                "role_pipeline": ["M1-TL", "writer", "reviewer"],
                "writer_brief": str(writer_path),
                "reviewer_brief": str(reviewer_path),
                "proposal_ids": [p.get("id") for p in sk_proposals],
            }
        )

    return {
        "briefs_dir": str(briefs_dir),
        "role_slots_reused": ["M1-TL", "writer", "reviewer"],
        "new_agent_roles_created": 0,
        "count": len(emitted),
        "briefs": emitted,
    }


def compute_skill_retro_diagnostics(
    *,
    inventory: Mapping[str, Mapping[str, Any]],
    skill_filter: str | None = None,
    emit_briefs_dir: Path | None = None,
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
    """Run the 4-Layer Progressive Self-Evolution & Wide-Probe Multi-Angle Skill Diagnostic."""
    s_root = skills_root or DEFAULT_SKILLS_ROOT
    d_root = dev_root or DEFAULT_DEV_ROOT
    prop_path = proposals_path or DEFAULT_PROPOSALS_PATH
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
    shards = shard_reader(telemetry_shards_dir, days) if (shard_reader and telemetry_shards_dir) else []
    _ingest_usage_jsonl_and_shards(
        observations,
        usage_jsonl=usage_jsonl,
        shard_records=shards,
        window_start=window_start,
        window_end=window_end,
        known_skills=known_skills,
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
    total_sessions = len(all_sessions)
    all_stamps = [obs.ended_at for obs in all_sessions]
    source_as_of = max(all_stamps).isoformat() if all_stamps else None
    sources_present = any(
        p is not None and p.exists()
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

    for sk_name, meta in sorted(inventory.items()):
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
        total_domain_failures = len(failing_sids) + escape_counts.get("impl_bug", 0) + escape_counts.get("spec_gap", 0)

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

    true_catchers: list[dict[str, Any]] = []
    ceremonial_rubber_stamps: list[dict[str, Any]] = []
    rigid_roadblocks: list[dict[str, Any]] = []
    latency_sinks: list[dict[str, Any]] = []

    for (sk_name, sc_rel), sc_calls in sorted(calls_by_script.items()):
        if not sc_calls:
            continue
        ordered_calls = sorted(sc_calls, key=lambda c: (c.ts, c.seq))
        graded = [c for c in ordered_calls if c.exit_code is not None]
        exit0_cnt = sum(1 for c in graded if c.exit_code == 0)
        exit1_cnt = sum(1 for c in graded if c.exit_code not in (0, 2))
        exit2_calls = [c for c in graded if c.exit_code == 2]

        # Check fail -> pass transitions within the same session (or across consecutive runs)
        by_sess: dict[str, list[ScriptCallEvent]] = {}
        for c in graded:
            by_sess.setdefault(c.session_id, []).append(c)

        fail_to_pass_sessions = 0
        exit2_arg_only_sessions = 0
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
            if saw_exit2:
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
            samples: list[str] = []
            for c in exit2_calls:
                red = _redact_command_sample(c.command, skills_root=s_root, dev_root=d_root)
                if red and red not in samples:
                    samples.append(red)
                if len(samples) >= 3:
                    break
            rigid_roadblocks.append(
                {
                    "skill": sk_name,
                    "script": sc_rel,
                    "calls": len(ordered_calls),
                    "exit_2_count": len(exit2_calls),
                    "exit_2_sessions": len({c.session_id for c in exit2_calls}),
                    "arg_retry_sessions": exit2_arg_only_sessions,
                    "failing_command_samples": samples,
                    "classification": "rigid_roadblock",
                    "evolution_action": "Add CLI flag aliases / positional path fallback and print 3-line usage hint on exit=2.",
                }
            )

        is_latency_sink = (max_ms is not None and max_ms >= LATENCY_SINK_MAX_MS) or (
            p50_ms is not None and p50_ms >= LATENCY_SINK_P50_MS and len(measured_durs) >= LATENCY_SINK_MIN_CALLS_FOR_P50
        )
        if is_latency_sink:
            speed_diag = _multi_angle_speed_diagnosis(sc_rel, ordered_calls)
            latency_sinks.append(
                {
                    "skill": sk_name,
                    "script": sc_rel,
                    "calls": len(ordered_calls),
                    "measured_calls": len(measured_durs),
                    "classification": "latency_sink",
                    **speed_diag,
                }
            )

    layer2 = {
        "true_catchers": sorted(true_catchers, key=lambda r: (-r["fail_to_pass_sessions"], -r["calls"], r["script"])),
        "ceremonial_rubber_stamps": sorted(
            ceremonial_rubber_stamps,
            key=lambda r: (-r["co_occurring_skill_escapes"], -r["calls"], r["script"]),
        ),
        "rigid_roadblocks": sorted(rigid_roadblocks, key=lambda r: (-r["exit_2_count"], -r["calls"], r["script"])),
        "latency_sinks": sorted(latency_sinks, key=lambda r: (-(r["max_ms"] or 0.0), -(r["p50_ms"] or 0.0), r["script"])),
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
        enriched = {
            **item,
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
        sk = ls["skill"]
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

    auto_proposed = queue_fn(proposals, prop_path) if (auto_propose and queue_fn is not None) else []
    proposals_written = len(auto_proposed)
    proposals_skipped = len(proposals) - proposals_written if auto_propose else 0

    result_payload: dict[str, Any] = {
        "ok": True,
        "schema": "skill-retro-diagnostic/v1",
        "query_id": "retro/skill_progressive_retro",
        "skill_filter": norm_filter or None,
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
            "wild_patterns_count": len(layer3["wild_patterns"]),
            "high_signal_proposals_count": len(proposals),
            "observation_pool_count": len(observation_pool),
            "slow_flywheel_ready": slow_flywheel_ready,
        },
        "layer0_direction_and_triggers": layer0,
        "layer1_drawer_metabolism": layer1,
        "layer2_script_efficacy_and_speed": layer2,
        "layer3_wild_patterns_and_eval": layer3,
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
        )
    return result_payload
