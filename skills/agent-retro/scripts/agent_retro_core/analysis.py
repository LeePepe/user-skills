from __future__ import annotations

from . import base as _base
from . import metrics as _metrics
from . import contracts as _contracts
from . import horizon as _horizon
globals().update({k: v for module in (_base, _metrics, _contracts, _horizon) for k, v in vars(module).items() if not k.startswith("__")})

def _version_watermark(events: list[dict[str, Any]] | None = None, observations: list[dict[str, Any]] | None = None, *, include_targets: bool = True) -> dict[str, Any]:
    try:
        count = int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip())
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD:skills/agent-retro"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        modified = int(subprocess.check_output(["git", "log", "-1", "--format=%ct", "--", "skills/agent-retro"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        count, sha, modified = 0, "unknown", 0
    current = f"v{count}@{sha}"
    sessions: dict[str, tuple[datetime | None, str]] = {}
    scenario_sessions: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"pre_fix": set(), "post_fix": set()})
    for event in events or []:
        sid = _session_id(event, str(event.get("_retro_session", "unknown")))
        timestamp = _parse_time(event.get("timestamp") or _field(event, "timestamp", "createdAt"))
        version = str(_field(event, "skillVersion", "skill_version", "version") or "")
        prior = sessions.get(sid)
        sessions[sid] = (min((prior[0], timestamp), key=lambda x: x or datetime.max.replace(tzinfo=timezone.utc)) if prior else timestamp, version or (prior[1] if prior else ""))
        skill = _event_skill(event)
        if skill:
            bucket = "pre_fix" if (version and version != current) or (timestamp and modified and timestamp.timestamp() < modified) else "post_fix"
            scenario_sessions[skill][bucket].add(sid)
    totals = {"pre_fix": {"anomalies": 0, "scenario_sessions": 0, "by_skill": {}},
              "post_fix": {"anomalies": 0, "scenario_sessions": 0, "by_skill": {}}}
    for skill, buckets in scenario_sessions.items():
        for bucket, ids in buckets.items():
            totals[bucket]["scenario_sessions"] += len(ids)
            totals[bucket]["by_skill"].setdefault(skill, {"anomalies": 0, "scenario_sessions": len(ids)})["scenario_sessions"] = len(ids)
    for item in observations or []:
        skill = item.get("upstream_skill") or item.get("skill") or "unknown"
        scope = str(item.get("evidence", [""])[0]).split(":", 1)[0]
        bucket = "pre_fix" if scope in {sid for sid, (ts, version) in sessions.items() if (version and version != current) or (ts and modified and ts.timestamp() < modified)} else "post_fix"
        totals[bucket]["anomalies"] += int(item.get("count", 0) or 0)
        row = totals[bucket]["by_skill"].setdefault(skill, {"anomalies": 0, "scenario_sessions": 0})
        row["anomalies"] += int(item.get("count", 0) or 0)
    return {"current": current, "last_modified_epoch": modified, "pre_fix": totals["pre_fix"], "post_fix": totals["post_fix"],
            "target_skill_watermarks": resolve_target_skill_watermarks() if include_targets else {},
            "transition": f"v{max(0, count - 1)}@previous -> {current}"}


def _classify_observations(observations: list[dict[str, Any]], events: list[dict[str, Any]], watermark: dict[str, Any]) -> dict[str, Any]:
    groups = _session_groups(events)
    target = watermark.get("target_skill_watermarks", {})
    for item in observations:
        skill = str(item.get("upstream_skill") or item.get("skill") or "")
        cutoff = int(target.get(skill, {}).get("last_modified_epoch", 0) or 0)
        evidence = item.get("evidence", [])
        session_ids = {str(e).split(":", 1)[0] for e in evidence if ":" in str(e)}
        buckets: dict[str, str] = {}
        for sid in session_ids:
            timestamps = [_parse_time(e.get("timestamp") or _field(e, "timestamp", "createdAt")) for e in groups.get(sid, [])]
            timestamps = [timestamp for timestamp in timestamps if timestamp]
            buckets[sid] = "pre_fix" if cutoff and timestamps and min(timestamps).timestamp() < cutoff else "post_fix"
        count = int(item.get("count", 0) or 0)
        if buckets:
            post_sessions = sum(value == "post_fix" for value in buckets.values())
            pre_sessions = sum(value == "pre_fix" for value in buckets.values())
            total_sessions = len(buckets)
            post_count = round(count * post_sessions / total_sessions)
            pre_count = count - post_count
        else:
            post_count, pre_count = (count, 0) if not cutoff else (count, 0)
        item["fix_bucket"] = "mixed" if post_count and pre_count else ("pre_fix" if pre_count else "post_fix")
        item["post_fix_count"] = post_count
        item["pre_fix_count"] = pre_count
    return watermark


def _aggregate_observations(rows: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("metric", "")), str(row.get("upstream_skill", row.get("skill", "unknown"))),
               str(row.get("status", "actionable")))
        current = grouped.get(key)
        if current is None:
            current = dict(row)
            current["evidence"] = []
            grouped[key] = current
        current["count"] = int(current.get("count", 0)) + int(row.get("count", 0) or 0)
        current["scenario_sessions"] = int(current.get("scenario_sessions", 0)) + int(row.get("scenario_sessions", 0) or 0)
        for evidence in row.get("evidence", []):
            if evidence not in current["evidence"] and len(current["evidence"]) < 3:
                current["evidence"].append(evidence)
    return sorted(grouped.values(), key=lambda row: (-int(row.get("count", 0)), str(row.get("metric", ""))))[:limit]


def _outcome_summary(gate: dict[str, Any]) -> dict[str, Any]:
    healthy = sum(value.get("status") == "OUTCOME_HEALTHY" for value in gate.values())
    at_risk = sum(value.get("status") == "OUTCOME_AT_RISK" for value in gate.values())
    unknown = sum(value.get("status") == "OUTCOME_UNKNOWN" for value in gate.values())
    entries = dict(list(sorted(gate.items()))[:15])
    return {"healthy_count": healthy, "at_risk_count": at_risk, "unknown_count": unknown, "entries": entries}


def _skill_attribution(observations: list[dict[str, Any]], watermark: dict[str, Any]) -> list[dict[str, Any]]:
    by_skill: dict[str, dict[str, Any]] = {}
    post = watermark.get("post_fix", {}).get("by_skill", {})
    pre = watermark.get("pre_fix", {}).get("by_skill", {})
    physical_locks = {
        "agent-retro": "bounded trace reader and compact output gate",
        "team-lead": "feedback and lease gate",
        "m2-dispatcher": "owned-path and lease gate",
        "code-review": "cross-vendor reviewer routing gate",
        "personal-m2-assistant": "prompt boundary gate",
        "repo-kit": "CI contract gate",
        "my-designer": "spec/design contract gate",
    }
    for row in observations:
        skill = str(row.get("upstream_skill") or row.get("skill") or "unknown")
        entry = by_skill.setdefault(skill, {"skill": skill, "total_anomalies": 0, "metrics": [],
                                             "physical_lock": physical_locks.get(skill, "upstream skill lock"),
                                             "pre_fix_anomalies": int(pre.get(skill, {}).get("anomalies", 0)),
                                             "post_fix_anomalies": int(post.get(skill, {}).get("anomalies", 0))})
        entry["total_anomalies"] += int(row.get("count", 0) or 0)
        if row.get("metric") not in entry["metrics"]:
            entry["metrics"].append(row["metric"])
    return sorted(by_skill.values(), key=lambda row: (-row["total_anomalies"], row["skill"]))[:15]


def _owned_path_skills(source: str) -> set[str]:
    match = re.search(r"(?ims)^#{0,6}\s*owned paths\b(?P<body>.*?)(?=^#{1,6}\s|\Z)", source)
    if not match:
        return set()
    skills: set[str] = set()
    for line in match.group("body").splitlines():
        if not re.match(r"^\s*(?:[-*]|\d+\.)\s+", line):
            continue
        skills.update(name for name in re.findall(r"\bskills/([^/\s]+)(?:/|\b)", line)
                     if is_valid_skill_slug(name, require_repo_dir=True))
    return {skill for skill in skills if is_valid_skill_slug(skill, require_repo_dir=True)}


def _lease_name_skill(name: str) -> str | None:
    match = re.fullmatch(r"(?:agent|skill)-retro-([A-Za-z0-9_-]+)", name)
    if not match:
        return None
    candidate = match.group(1)
    return candidate if is_valid_skill_slug(candidate, require_repo_dir=True) else None


def _active_leases(handoffs: list[dict[str, Any]]) -> set[str]:
    leases: set[str] = set()
    for record in handoffs:
        if not record.get("active_lease"):
            continue
        if skill := _lease_name_skill(record["name"]):
            leases.add(skill)
        for source in (record.get("spec", ""), record.get("ledger", "")):
            leases.update(_owned_path_skills(source))
    return leases




def _scan(args: argparse.Namespace) -> dict[str, Any]:
    sessions, handoff_root, warehouse = _roots(args)
    events, event_source = _read_events(
        sessions, int(getattr(args, "days", 30) or 30),
        fast=bool(getattr(args, "fast", False) or getattr(args, "_probe_budget", False)),
    )
    handoffs, handoff_source = _handoffs(handoff_root)
    if getattr(args, "_horizon_only", False):
        outcome_gate, profile_sessions = {}, set()
    else:
        outcome_gate, profile_sessions = _outcome_gate(_session_groups(events))
    for record in handoffs:
        if re.search(r"\b(?:done|completed|pr_merged)\b", record["ledger"], re.I):
            outcome_gate[f"handoff:{record['name']}"] = {"status": "OUTCOME_HEALTHY" if record["receipts"] else "OUTCOME_AT_RISK", "faithful_receipts": bool(record["receipts"]), "goal": record["name"]}
    analysis_events = events
    all_session_groups = _session_groups(events)
    if getattr(args, "_horizon_only", False):
        analysis_events = events
    elif len(all_session_groups) > 45:
        recent_session_ids = set(list(all_session_groups)[-45:])
        analysis_events = [event for event in events if _session_id(event, str(event.get("_retro_session", ""))) in recent_session_ids]
    observations = [] if getattr(args, "_horizon_only", False) else _metric_scan(analysis_events, handoffs, event_source, profile_sessions)
    include_horizon = bool(getattr(args, "_include_horizon", False) or "horizon" in str(getattr(args, "layer", "") or "").split(","))
    if include_horizon:
        observations.extend(_horizon_profile(analysis_events, handoffs,
                                             min_turns=int(getattr(args, "min_turns", 40) or 40),
                                             min_duration_min=float(getattr(args, "min_duration_min", 15) or 15)))
    active_leases = _active_leases(handoffs)
    layers = set(filter(None, str(getattr(args, "layer", "") or "").split(",")))
    role_filter = str(getattr(args, "role", "") or "").lower()
    if layers:
        observations = [x for x in observations if x["layer"] in layers]
    if role_filter:
        role_skills = {
            "m3": {"personal-m2-assistant"}, "m2": {"m2-dispatcher", "personal-m2-assistant"},
            "m1": {"team-lead"}, "writer": {"team-lead", "agent-retro"},
            "reviewer": {"code-review", "team-lead"},
        }
        allowed = role_skills.get(role_filter, {role_filter})
        observations = [x for x in observations if x.get("upstream_skill") in allowed or role_filter in x.get("skill", "").lower()]
    metrics: list[dict[str, Any]] = []
    counts = Counter(x["metric"] for x in observations if x["status"] != "healthy_progressive_disclosure")
    for layer, metric in METRIC_ORDER:
        healthy = any(x["metric"] == metric and x["status"] == "healthy_progressive_disclosure" for x in observations)
        missing_coverage = (not event_source["available"] and not handoff_source["available"]) or event_source["truncated"]
        metrics.append({"metric": metric, "layer": layer, "count": counts.get(metric, 0), "status": "healthy_progressive_disclosure" if healthy and not counts.get(metric) else ("unknown" if missing_coverage and not counts.get(metric) else "observed")})
    actionable = [x for x in observations if x["status"] != "healthy_progressive_disclosure"]
    watermark = _version_watermark(events, observations, include_targets=not getattr(args, "_horizon_only", False))
    watermark = _classify_observations(observations, events, watermark)
    warehouse_signals = _warehouse_signals(warehouse)
    compact_actionable = _aggregate_observations(actionable)
    compact_healthy = _aggregate_observations(
        [x for x in observations if x["status"] == "healthy_progressive_disclosure"], limit=20
    )
    compact_pool = [x for x in compact_actionable if x["count"] < 3][:20]
    outcome = _outcome_summary(outcome_gate) if getattr(args, "summary", False) or len(outcome_gate) > 15 else outcome_gate
    return {"schema": "agent-retro-probe/v1", "ok": True, "entry_mode": "fast" if getattr(args, "fast", False) else "bounded", "role_filter": role_filter or None,
            "source": {"events": event_source, "handoff": handoff_source, "warehouse": warehouse_signals},
            "coverage": {"events": len(events), "sessions": len(_session_groups(events)), "observations": len(observations), "incomplete": bool(event_source["truncated"])},
            "outcome_gate": outcome, "active_leases": sorted(active_leases), "excluded_by_active_lease": [],
            "version_watermark": watermark, "skill_attribution": _skill_attribution(actionable, watermark),
            "metrics": metrics, "observations": compact_actionable,
            "healthy_exemptions": compact_healthy, "observation_pool": compact_pool,
            "k_cap": min(3, len({x['upstream_skill'] for x in actionable if x['upstream_skill'] not in active_leases}))}
