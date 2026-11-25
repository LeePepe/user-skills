from __future__ import annotations
from .common import *

# AST-partitioned skill-retro implementation module.

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
    limit_sessions: int | None = DEFAULT_MAX_COPILOT_SESSIONS_SCAN,
) -> dict[str, Any]:
    """Compute the 4-Layer / 6-Dimension Progressive Skill Evolution & Wide-Probe Multi-Angle Diagnostic."""
    explicit_source_override = bool(isolated_fixtures) or any(
        val is not None
        for val in (warehouse_db, copilot_db, copilot_sessions_dir, usage_jsonl, trace_export_path)
    )
    wh = warehouse_db if warehouse_db is not None else (
        None
        if explicit_source_override or (limit_sessions is not None and DEFAULT_WAREHOUSE_DB == DEFAULT_AIDATA_DIR / "L3_merge" / "warehouse.db")
        else DEFAULT_WAREHOUSE_DB
    )
    cp = copilot_db if copilot_db is not None else (
        None
        if explicit_source_override or (limit_sessions is not None and DEFAULT_COPILOT_DB == Path.home() / ".copilot" / "session-store.db")
        else DEFAULT_COPILOT_DB
    )
    sess_dir = (
        copilot_sessions_dir
        if copilot_sessions_dir is not None
        else (None if explicit_source_override else DEFAULT_COPILOT_SESSIONS_DIR)
    )
    u_jsonl = (
        usage_jsonl
        if usage_jsonl is not None
        else (
            None
            if explicit_source_override or (limit_sessions is not None and DEFAULT_SKILL_TELEMETRY_USAGE_JSONL == DEFAULT_USAGE_JSONL)
            else DEFAULT_SKILL_TELEMETRY_USAGE_JSONL
        )
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
    inventory = _discover_skill_inventory(s_root, d_root, skill_filter=skill)
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
        limit_sessions=limit_sessions,
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
