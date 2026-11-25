from __future__ import annotations

from . import base as _base
from . import metrics as _metrics
from . import contracts as _contracts
from . import horizon as _horizon
from . import analysis as _analysis
globals().update({k: v for module in (_base, _metrics, _contracts, _horizon, _analysis) for k, v in vars(module).items() if not k.startswith("__")})

def _compact_print(payload: Any, as_json: bool, summary: bool = False) -> None:
    payload = _redact_obj(payload)
    if as_json:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    else:
        if isinstance(payload, dict):
            text = "\n".join([f"agent-retro: {payload.get('schema', 'ok')}", f"observations={len(payload.get('observations', []))} k={payload.get('k_cap', 0)}", "unknown sources remain unknown; no issue was published."])
        else:
            text = _text(payload)
    if len(text) > MAX_OUTPUT and isinstance(payload, dict):
        reduced = {key: payload[key] for key in (
            "schema", "ok", "entry_mode", "role_filter", "source", "coverage", "outcome_gate",
            "active_leases", "excluded_by_active_lease", "version_watermark", "skill_attribution",
            "metrics", "observations", "horizon", "healthy_exemptions", "observation_pool", "k_cap",
        ) if key in payload}
        for key in ("issue_verified", "dry_run", "published", "written", "config", "path", "action"):
            if key in payload:
                reduced[key] = payload[key]
        for size in (10, 5, 2, 0):
            reduced["observations"] = reduced.get("observations", [])[:size]
            reduced["healthy_exemptions"] = reduced.get("healthy_exemptions", [])[:size]
            reduced["observation_pool"] = reduced.get("observation_pool", [])[:size]
            text = json.dumps(reduced, ensure_ascii=False, separators=(",", ":")) if as_json else text[:MAX_OUTPUT - 40] + "\n... output compacted"
            if len(text) <= MAX_OUTPUT:
                break
    print(_redact(text))


def _load_issue_renderer():
    path = ROOT / "skills" / "issue-brief" / "scripts" / "verify_issue_format.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("agent_retro_issue_renderer", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError):
        return None
    return module


def _safe_write(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    path.write_text(_redact(text), encoding="utf-8")
    return True


def _selected(scan: dict[str, Any], pick_top: int = 3, skill: str | None = None, *, include_pre_fix: bool = False) -> list[dict[str, Any]]:
    by_skill: dict[str, dict[str, Any]] = {}
    priority = {"E4_unfaithful_receipt_and_cancelled_noise": 100, "R2_m2_direct_code_edit": 95, "I3_l2_exit2_and_output_bloat": 90, "E1_blind_spin_loop_vs_progressive_catch": 80}
    active_leases = set(scan.get("active_leases", []))
    excluded = set(scan.get("excluded_by_active_lease", []))
    for item in scan.get("observations", []):
        if not include_pre_fix and item.get("fix_bucket") == "pre_fix" and int(item.get("post_fix_count", 0) or 0) == 0:
            continue
        if skill and item.get("upstream_skill") != skill and item.get("skill") != skill:
            continue
        candidate_skill = item.get("upstream_skill") or item.get("skill") or "unknown"
        if not skill and candidate_skill in active_leases:
            excluded.add(candidate_skill)
            continue
        if item.get("count", 0) < 3 and item["metric"] not in priority:
            continue
        key = item.get("upstream_skill") or item.get("skill") or "unknown"
        base_count = int(item.get("post_fix_count", item.get("count", 0)) or 0) if not include_pre_fix else int(item.get("count", 0) or 0)
        score = base_count + priority.get(item["metric"], 0)
        if key not in by_skill or score > by_skill[key].get("_score", -1):
            copy = dict(item); copy["_score"] = score; by_skill[key] = copy
    chosen = sorted(by_skill.values(), key=lambda x: (-x["_score"], x["upstream_skill"]))[:pick_top]
    for item in chosen:
        item.pop("_score", None)
    scan["excluded_by_active_lease"] = sorted(excluded)
    return chosen


def _brief_text(skill: str, item: dict[str, Any], root: Path) -> tuple[str, str, str]:
    owned = f"skills/{skill}/**\nscripts/test_{skill.replace('-', '_')}*.py"
    spec = (f"# Agent Retro remediation: {skill}\n\nGoal: address `{item['metric']}` using the first-fault evidence below.\n\n"
            f"## Owned Paths\n- {owned}\n\n## Interfaces\n- Consumes: events.jsonl, handoff receipts, optional aidata (read-only).\n"
            "- Produces: one physical lock, positive replay, counter-case replay, and R10 receipt.\n\n"
            f"## Acceptance\n- Preserve progressive disclosure exemption when applicable.\n- Attribute the first upstream skill, not downstream symptoms.\n- Run targeted tests and a strict skill-contract audit.\n\n## M1 dispatch guardrails\n- Do not pre-read full target scripts before dispatching Writer.\n- Require an explicit coding or cross-vendor model; never inherit `group/steering`.\n- Expand absolute owned paths before Writer source inspection.\n\nEvidence: {json.dumps(item, ensure_ascii=False)}\n")
    brief = (f"# Writer brief — agent-retro remediation for {skill}\n\n## Owned Paths\n- {owned}\n- {root}/receipts/* (R10 only)\n\n## Required behavior\nImplement the smallest physical lock for `{item['metric']}`. Do not change unrelated skills, CI policy, or public secrets. Add positive and counter-case fixtures.\n\n## M1 dispatch guardrails\n- Do not pre-read full target scripts in M1 before dispatching Writer; the Writer owns source inspection.\n- Dispatch Writer with an explicit coding or cross-vendor model; never inherit `group/steering`.\n- Expand and pass absolute owned paths before source inspection.\n\n## Verification\nRun the targeted unit test, `skill_contract.py audit --skill {skill} --strict`, and record exact exit codes in R10.\n")
    ledger = (f"# Goal: agent-retro-{skill}\n\nstatus: ready\n\n## Owned Paths\n- {owned}\n\n## Interfaces\n- Consumes: events.jsonl, handoff receipts, optional aidata.\n- Produces: physical lock, positive/counter replay, R10 receipt.\n\n## Acceptance\n- Exact targeted test and strict audit exit 0.\n- No live issue publication or schedule mutation during validation.\n")
    return spec, brief, ledger


def command_probe(args: argparse.Namespace) -> dict[str, Any]:
    if int(getattr(args, "days", 30) or 30) <= 3 and not getattr(args, "fast", False):
        args._probe_budget = True
    return _scan(args)


def command_horizon(args: argparse.Namespace) -> dict[str, Any]:
    args._include_horizon = True
    args._horizon_only = True
    result = _scan(args)
    result["schema"] = "agent-retro-horizon/v1"
    result["horizon"] = [row for row in result.get("observations", []) if row.get("layer") == "horizon"]
    result["min_turns"] = args.min_turns
    result["min_duration_min"] = args.min_duration_min
    return result


def _agent_retro_coverage(scan: Mapping[str, Any]) -> str:
    source = scan.get("source") if isinstance(scan.get("source"), Mapping) else scan
    event_source = source.get("events") if isinstance(source.get("events"), Mapping) else {}
    handoff_source = source.get("handoff") if isinstance(source.get("handoff"), Mapping) else {}
    if not event_source.get("available") and not handoff_source.get("available"):
        return "missing"
    coverage = scan.get("coverage") if isinstance(scan.get("coverage"), Mapping) else {}
    sessions = int(coverage.get("sessions", 0) or 0)
    return "healthy" if sessions > 0 and not coverage.get("incomplete") else "degraded"


def collect_agent_retro_snapshot(
    *,
    days: int = 3,
    handoff_root: Path | None = None,
    output_path: Path | None = None,
    watermark_path: Path | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Collect a deterministic three-day snapshot without network or Multica side effects."""
    args = argparse.Namespace(
        days=max(1, int(days)), role=None, layer=None, fast=True,
        handoff_root=str(handoff_root) if handoff_root else None, handoff_dir=None,
    )
    scan = _scan(args)
    date_str = datetime.now(timezone.utc).astimezone(CST).date().isoformat()
    coverage = _agent_retro_coverage(scan)
    sessions = int((scan.get("coverage") or {}).get("sessions", 0) or 0)
    cohort = hashlib.sha256(json.dumps(
        {"observations": scan.get("observations", []), "sessions": sessions, "coverage": coverage},
        ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")).hexdigest()[:16]
    snapshot = {
        "schema": "agent-retro-collect/v1",
        "ok": True,
        "date": date_str,
        "window_days": max(1, int(days)),
        "dedup_key": f"agent-retro-autopilot-{date_str}",
        "cursor": f"agent-retro-autopilot-{date_str}",
        "summary": {
            "sample_size": sessions,
            "sessions": sessions,
            "coverage_status": coverage,
            "coverage": coverage,
            "observation_count": len(scan.get("observations", [])),
        },
        "coverage": {"sessions": sessions, "status": coverage},
        "scan": scan,
        "watermark": cohort,
    }
    if persist:
        target = output_path or DEFAULT_COLLECT_OUTPUT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        watermark = watermark_path or _default_agent_retro_watermark_path()
        watermark.parent.mkdir(parents=True, exist_ok=True)
        watermark.write_text(json.dumps({
            "schema": "agent-retro-watermark/v1", "cohort_watermark": cohort,
            "date": date_str, "sessions": sessions, "coverage": coverage,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        snapshot["output"] = str(target)
        snapshot["watermark_path"] = str(watermark)
    return snapshot


def command_collect(args: argparse.Namespace) -> dict[str, Any]:
    return collect_agent_retro_snapshot(
        days=int(getattr(args, "days", 3) or 3),
        handoff_root=Path(args.handoff_root).expanduser() if getattr(args, "handoff_root", None) else None,
        output_path=Path(args.output).expanduser() if getattr(args, "output", None) else DEFAULT_COLLECT_OUTPUT,
        watermark_path=Path(args.watermark_file).expanduser() if getattr(args, "watermark_file", None) else None,
        persist=True,
    )


def _mock_jev(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    candidate = Path(value).expanduser()
    raw = candidate.read_text(encoding="utf-8") if candidate.is_file() else value
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _load_jev_client() -> Any | None:
    path = ROOT / "skills" / "team-lead" / "scripts" / "jev_client.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("agent_retro_jev_client", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except (OSError, ImportError, AttributeError):
        return None


def _run_jev(selected: list[dict[str, Any]], *, enabled: bool, mock: dict[str, Any] | None) -> dict[str, Any]:
    fallback = {"choice": "deterministic_fallback", "noul": 0.0, "confidence": 0.0}
    if not enabled:
        return {"used": False, "bounded_seconds": 1.5, "decision": fallback}
    module = _load_jev_client()
    if module is None or not hasattr(module, "jev_decide"):
        return {"used": False, "bounded_seconds": 1.5, "decision": fallback, "fallback": "client_unavailable"}
    questions = {"choice": {"type": "choice", "options": [str(x.get("upstream_skill", "unknown")) for x in selected]},
                 "noul": {"type": "number", "instructions": "Probability this is novel and actionable, 0 to 1."}}
    try:
        decision = module.jev_decide({"candidates": selected[:3]}, questions, agent_role="explorer",
                                     timeout_sec=1.5, mock_response=mock)
    except (OSError, ValueError, TypeError):
        decision = None
    if not isinstance(decision, dict):
        return {"used": False, "bounded_seconds": 1.5, "decision": fallback, "fallback": "timeout_or_invalid"}
    try:
        noul = float(decision.get("noul", decision.get("novelty", 0.0)))
    except (TypeError, ValueError):
        noul = 0.0
    choice = decision.get("choice") if noul > 0.70 else "deterministic_fallback"
    if noul < 0.30:
        choice = "deterministic_fallback"
    return {"used": True, "bounded_seconds": 1.5, "decision": {"choice": choice, "noul": noul}, "raw": decision}


def command_triage(args: argparse.Namespace) -> dict[str, Any]:
    args._include_horizon = True
    scan = _scan(args)
    selected = _selected(scan, 1 if args.pick_one else min(3, args.pick_top), include_pre_fix=bool(getattr(args, "include_pre_fix", False)))
    mock = _mock_jev(args.mock_jev_json)
    jev = _run_jev(selected, enabled=bool(args.use_jev), mock=mock)
    root = Path(args.emit_briefs).expanduser() if args.emit_briefs else None
    emitted = []
    if root:
        for item in selected:
            skill = re.sub(r"[^A-Za-z0-9_-]+", "-", str(item.get("upstream_skill", "unknown"))).strip("-")
            target = root / f"agent-retro-{skill}"
            spec, brief, ledger = _brief_text(skill, item, target)
            emitted.append({"skill": skill, "spec": _safe_write(target / "spec.md", spec), "brief": _safe_write(target / "briefs" / "agent-retro.md", brief), "ledger": _safe_write(target / "ledger.md", ledger), "path": str(target)})
    return {"schema": "agent-retro-triage/v1", "ok": True, "picked": selected, "picked_count": len(selected), "healthy_no_issues": not selected,
            "excluded_by_active_lease": sorted(scan.get("excluded_by_active_lease", [])),
            "worker_role_slot": {"role": "explorer", "tier": "worker", "tokens": 0},
            "jev": {**jev, "mock": bool(mock), "mock_payload": mock},
            "emitted": emitted, "m2_dispatch_plan": {"schema": "agent-retro-m2-dispatch-plan/v1", "m2_coordinator_count": 1,
                "k": len(selected), "skills": [x.get("upstream_skill") for x in selected], "honest_cap": 3}}


def command_record_receipt(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root or "~/Development/handoff").expanduser()
    target = root / f"agent-retro-{args.skill}" / "receipts" / "m1-r10.md"
    lines = [f"# R10 Receipt — agent-retro-{args.skill}", "", f"- Skill: `{args.skill}`", f"- Goal ID: `{args.goal_id}`", f"- Status: `{args.status}`"]
    if args.merge_sha:
        lines.append(f"- Merge SHA: `{args.merge_sha}`")
    if args.pr:
        lines.append(f"- PR: {args.pr}")
    lines.extend(["", args.summary or "Recorded by agent-retro."])
    written = _safe_write(target, "\n".join(lines) + "\n")
    return {"schema": "agent-retro-m1-receipt/v1", "ok": True, "path": str(target), "written": written,
            "skill": args.skill, "goal_id": args.goal_id, "status": args.status, "merge_sha": args.merge_sha, "pr": args.pr}


def command_emit_relay(args: argparse.Namespace) -> dict[str, Any]:
    if args.from_skill == args.to_skill:
        raise ValueError("Host and Guest skill must be distinct")
    root = Path(args.handoff_root or "~/Development/handoff").expanduser()
    target = root / f"agent-retro-relay-{args.from_skill}-to-{args.to_skill}" / "relay.md"
    text = (f"# Cross-Skill Relay\n\n- From: `{args.from_skill}`\n- To: `{args.to_skill}`\n- Requirement: {args.requirement}\n\n"
            "Wave 2 only; the host must not edit Guest-owned paths.\n")
    written = _safe_write(target, text)
    return {"schema": "agent-retro-relay/v1", "ok": True, "from_skill": args.from_skill, "to_skill": args.to_skill,
            "path": str(target), "written": written}


def command_diagnose(args: argparse.Namespace) -> dict[str, Any]:
    scan = _scan(args)
    matches = [x for x in scan["observations"] if x.get("upstream_skill") == args.skill or x.get("skill") == args.skill]
    return {"schema": "agent-retro-diagnose/v1", "ok": True, "skill": args.skill, "observations": matches,
            "first_fault": matches[0] if matches else None, "status": "actionable" if matches else "HEALTHY_NO_ISSUES",
            "positive_counter_replay": {"required": bool(matches), "positive": True, "counter": True}}


def command_emit(args: argparse.Namespace) -> dict[str, Any]:
    scan = _scan(args)
    selected = _selected(scan, 1 if args.pick_one else 3, args.skill)
    root = Path(args.emit_briefs or "~/Development/handoff").expanduser()
    emitted = []
    for item in selected:
        skill = str(item.get("upstream_skill", "unknown"))
        target = root / f"agent-retro-{skill}"
        spec, brief, ledger = _brief_text(skill, item, target)
        emitted.append({"skill": skill, "path": str(target), "spec_created": _safe_write(target / "spec.md", spec), "brief_created": _safe_write(target / "briefs" / "agent-retro.md", brief), "ledger_created": _safe_write(target / "ledger.md", ledger)})
    return {"schema": "agent-retro-emit-briefs/v1", "ok": True, "k": len(emitted), "emitted": emitted, "honest_cap": 3,
            "excluded_by_active_lease": sorted(scan.get("excluded_by_active_lease", []))}


def _receipts(root: Path) -> list[dict[str, Any]]:
    rows = []
    if not _external_path_safe(root) or not root.is_dir():
        return rows
    for directory in sorted(root.iterdir())[:128]:
        receipt_dir = directory / "receipts"
        if not receipt_dir.is_dir():
            continue
        for path in sorted(receipt_dir.glob("*.md"))[:16]:
            text = _safe_text(path, 20_000)
            rows.append({"path": _redact(str(path)), "status": next((s for s in ("FIXED_AND_MERGED", "HEALTHY_NO_ISSUES", "CROSS_SKILL_RELAY") if s in text), "UNKNOWN"), "text": _redact(text[:1000])})
    return rows[:100]


def command_rollup(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root or "~/Development/handoff").expanduser()
    days = max(1, int(getattr(args, "days", 30) or 30))
    scan = _scan(argparse.Namespace(days=days, role=None, layer=None, fast=True, handoff_root=str(root), handoff_dir=None, _include_horizon=True, min_turns=40, min_duration_min=15))
    receipts = _receipts(root)
    grounding: list[dict[str, Any]] = []
    for receipt in receipts:
        match = re.search(r"Merge SHA:\s*`?([0-9a-f]{7,40})", receipt.get("text", ""), re.I)
        if match:
            sha = match.group(1)
            grounded = _git_exists(f"{sha}^{{commit}}")
            grounding.append({"path": receipt["path"], "merge_sha": sha, "grounded": grounded})
    day = args.date or datetime.now().date().isoformat()
    payload = {"schema": "agent-retro-rollup/v1", "date": day, "source": scan.get("source"), "coverage": scan.get("coverage"), "observations": scan["observations"][:30], "metrics": scan["metrics"], "receipts": receipts[:10], "receipt_grounding": grounding, "observation_pool": scan["observation_pool"][:20], "version_watermark": scan["version_watermark"]}
    markdown = None
    verified = None
    if args.issue_brief:
        renderer = _load_issue_renderer()
        items = [{"what": f"核对技能 {x['upstream_skill']} 的首错证据", "why": f"复现 {x['metric']} 的 {x['count']} 次可观察信号"} for x in _selected(scan, 3)]
        if not items:
            items = [{"what": "保留当前观测池并等待更多场景", "why": "当前没有达到派发阈值的可执行异常"}]
        if renderer:
            markdown = renderer.render_dual_zone_issue(tldr=f"本次轨迹复盘覆盖 {scan['coverage']['sessions']} 个会话，派发上限为 {scan['k_cap']} 个技能。", items=items, cursor=f"agent-retro-{day}", heading=f"[Agent Retro {day}]", ai_payload=payload)
            verified = renderer.verify_dual_zone_issue(markdown)
    return {"schema": "agent-retro-rollup/v1", "ok": True, "date": day, "receipts": len(receipts), "k": scan["k_cap"], "payload": payload, "issue_markdown": markdown, "issue_verified": verified}


def _default_multica_runner(
    command: list[str], *, stdin_text: str | None = None,
    multica_bin: str | None = None, server_url: str | None = None,
    workspace_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    binary = multica_bin or os.environ.get("MULTICA_BIN") or shutil.which("multica") or "/opt/homebrew/bin/multica"
    cmd = [binary]
    if server_url:
        cmd += ["--server-url", server_url]
    if workspace_id:
        cmd += ["--workspace-id", workspace_id]
    cmd += command
    env = dict(os.environ)
    if server_url:
        env["MULTICA_SERVER_URL"] = server_url
    if workspace_id:
        env["MULTICA_WORKSPACE_ID"] = workspace_id
    return subprocess.run(cmd, input=stdin_text, capture_output=True, text=True, env=env, check=False)


def _parse_multica_json(proc: subprocess.CompletedProcess[str], context: str) -> Any:
    if proc.returncode != 0:
        raise RuntimeError(f"multica {context} failed (exit {proc.returncode}): {(proc.stderr or proc.stdout or '').strip()[:300]}")
    raw = (proc.stdout or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"multica {context} returned invalid JSON: {raw[:300]}") from exc


def _telemetry_issue_markdown(markdown: str, *, day: str, sessions: int, coverage: str) -> str:
    marker = f"agent-retro-autopilot-{day}"
    result = markdown.replace(f"<!-- multica-cursor:agent-retro-{day} -->", f"<!-- multica-cursor:{marker} -->")
    telemetry = f"- **Telemetry**: `sessions={sessions}` · `coverage={coverage}`"
    if "</details>" in result and telemetry not in result:
        result = result.replace("</details>", telemetry + "\n</details>", 1)
    return result


def _apply_agent_retro_issue(
    *, title: str, description: str, assignee_id: str, priority: str, status: str,
    server_url: str, workspace_id: str, runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    listed_proc = runner(["issue", "list", "--assignee-id", assignee_id, "--limit", "50", "--output", "json"],
                         server_url=server_url, workspace_id=workspace_id)
    listed = _parse_multica_json(listed_proc, "issue list")
    issues = listed.get("issues", []) if isinstance(listed, Mapping) else (listed if isinstance(listed, list) else [])
    marker = re.search(r"agent-retro-autopilot-\d{4}-\d{2}-\d{2}", description)
    cursor = marker.group(0) if marker else ""
    existing = next((item for item in issues if isinstance(item, Mapping) and str(item.get("status") or "").lower() not in {"done", "cancelled", "canceled"}
                     and (str(item.get("title") or "") == title or (cursor and cursor in str(item.get("description") or "")))), None)
    if existing is not None and (existing.get("id") or existing.get("identifier")):
        target = str(existing.get("id") or existing.get("identifier"))
        proc = runner(["issue", "update", target, "--title", title, "--description-stdin", "--priority", priority,
                       "--status", status, "--assignee-id", assignee_id, "--no-start", "--output", "json"],
                      stdin_text=description, server_url=server_url, workspace_id=workspace_id)
        return {"action": "updated_existing", "issue": _parse_multica_json(proc, "issue update"), "dedup_key": cursor}
    proc = runner(["issue", "create", "--title", title, "--priority", priority, "--status", status,
                   "--assignee-id", assignee_id, "--description-stdin", "--output", "json"],
                  stdin_text=description, server_url=server_url, workspace_id=workspace_id)
    return {"action": "created", "issue": _parse_multica_json(proc, "issue create"), "dedup_key": cursor}


def _sync_linear_agent_retro(*, title: str, description: str, day: str) -> dict[str, Any]:
    """Use the existing Linear client only when explicitly requested by --sync-linear."""
    path = ROOT / "skills" / "linear-board" / "scripts" / "daily-brief.py"
    spec = importlib.util.spec_from_file_location("agent_retro_linear_bridge", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Linear client bridge unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = module._load_linear_client(dry_run=False)
    if client is None:
        raise RuntimeError("Linear credentials unavailable for --sync-linear")
    team = client.team_metadata("TIA")
    marker = f"<!-- multica-cursor:agent-retro-autopilot-{day} -->"
    issues = client.list_issues("TIA", limit=None)
    existing = next((item for item in issues if marker in str(item.get("description") or "") or item.get("title") == title), None)
    kwargs = {"title": title, "description": description, "assignee_id": DEFAULT_OWNER_ID}
    if existing:
        return client.update_issue(existing["id"], **kwargs)
    states = team.get("states", {}).get("nodes", [])
    todo = next((state for state in states if str(state.get("name", "")).casefold() == "todo"), None)
    kwargs.update({"team_id": team["id"], "state_id": todo["id"] if todo else None, "parent_id": None})
    return client.create_issue(**kwargs)


def command_file_issue(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root or "~/Development/handoff").expanduser()
    days = max(1, int(getattr(args, "days", 30) or 30))
    roll = command_rollup(argparse.Namespace(handoff_root=str(root), date=args.date, days=days, issue_brief=True))
    day = str(roll["date"])
    payload = roll.get("payload", {}) if isinstance(roll.get("payload"), Mapping) else {}
    scan = payload.get("coverage", {}) if isinstance(payload.get("coverage"), Mapping) else {}
    sessions = int(scan.get("sessions", 0) or 0)
    coverage = _agent_retro_coverage(payload)
    markdown = _telemetry_issue_markdown(str(roll.get("issue_markdown") or ""), day=day, sessions=sessions, coverage=coverage)
    verification = _load_issue_renderer().verify_dual_zone_issue(markdown) if _load_issue_renderer() else {"ok": False, "violations": ["renderer unavailable"]}
    if not verification.get("ok"):
        raise ValueError(f"Dual-Zone issue verification failed before Multica publish: {verification.get('violations')}")
    dry_run = bool(getattr(args, "dry_run", False) or not getattr(args, "apply", False))
    server_url = getattr(args, "server", "") or DEFAULT_SERVER_URL
    workspace = getattr(args, "workspace", "") or DEFAULT_WORKSPACE_ID
    owner = getattr(args, "owner", "") or DEFAULT_OWNER_ID
    title = f"[Agent Retro {day}]"
    result: dict[str, Any] = {"schema": "agent-retro-file-issue/v1", "ok": True, "dry_run": dry_run,
                              "published": False, "server": server_url, "workspace": workspace,
                              "owner": owner, "agent": getattr(args, "agent", "agent-retro"),
                              "dedup_key": f"agent-retro-autopilot-{day}", "issue_markdown": markdown,
                              "dual_zone_verification": verification, "action": "dry_run"}
    if not dry_run:
        applied = _apply_agent_retro_issue(title=title, description=markdown, assignee_id=owner,
                                           priority="medium", status="backlog", server_url=server_url,
                                           workspace_id=workspace, runner=getattr(args, "runner", _default_multica_runner))
        result.update(applied, published=True, action=applied["action"])
        if getattr(args, "sync_linear", False):
            result["linear"] = _sync_linear_agent_retro(title=title, description=markdown, day=day)
    return result


def _agent_retro_prompt() -> str:
    """Return the structured runbook mirrored by the canonical skill-retro autopilot."""
    return (
        f"# {DEFAULT_AUTOPILOT_TITLE}\n\n"
        "- **Loop ID**: `agent-retro-diagnostic`\n"
        "- **Primary Engine**: `skills/agent-retro/scripts/agent_retro.py`\n"
        "- **Orchestration Topology**: `1 M2 -> 1 Cheap Probe Worker -> K (0..3) Independent Per-Skill M1 TLs -> Rollup to Single M2 -> M3 [Agent Retro YYYY-MM-DD] Multica & Linear Issue`\n"
        "- **Execution Mode**: `run_only`; collect and triage remain deterministic and side-effect-free until explicit apply.\n\n"
        "## Stage 1+2 · Scheduled Tick and Cheap Probe Worker (04:40 CST)\n\n"
        "Run the Layer 0 loop collector followed by the single bounded worker/Jev triage. "
        "The honest cap is K=0..3; K=0 is `HEALTHY_NO_ISSUES` and never forces an M1.\n\n"
        "```bash\n"
        "python3 skills/team-lead/scripts/multica_loop_runner.py run-loop --loop agent-retro-diagnostic --json\n"
        "python3 skills/agent-retro/scripts/agent_retro.py triage --pick-top 3 --use-jev --emit-briefs ~/Development/handoff --json\n"
        "```\n\n"
        "## Stage 3 · K (0..3) Independent Per-Skill M1 TLs\n\n"
        "For each selected skill, dispatch one independent M1 TL as "
        "`agent-retro-<skill>-goal-tl-r1` in `~/Development/worktrees/agent-retro-<skill>`. "
        "If deep reasoning confirms a benign signal, record `HEALTHY_NO_ISSUES` and spawn zero Writers/Reviewers. "
        "If confirmed actionable, run Writer -> Reviewer, positive and counter-case replay, `./scripts/verify`, and merge; "
        "each M1 returns an R10 receipt to the single M2.\n\n"
        "## Final Rollup · Single M2\n\n"
        "After all K M1 results return, collect the three-day cohort, render and verify the Dual-Zone issue, "
        "then explicitly publish Multica and sync the standalone Linear Team TIA M3 issue:\n\n"
        "```bash\n"
        "python3 skills/agent-retro/scripts/agent_retro.py collect --days 3 --json\n"
        "python3 skills/agent-retro/scripts/agent_retro.py rollup --issue-brief --json\n"
        "python3 skills/agent-retro/scripts/agent_retro.py file-issue --apply --sync-linear --json\n"
        "```\n\n"
        "## v2 Parity + Horizon Pass\n\n"
        "Before triage, resolve per-skill `vN@sha` watermarks and rank recurring post-fix anomalies; run H5 cross-skill AST/warehouse drift and I5 wild-glue discovery. "
        "Use `record-m1-receipt` or `emit-relay` for every M1 outcome and ground merge SHAs during rollup. "
        "Run `python3 skills/agent-retro/scripts/agent_retro.py horizon --days 7 --min-turns 40 --json` so successful long runs receive HZN1..HZN5 optimization profiling.\n\n"
        "The issue must retain `<!-- multica-cursor:agent-retro-autopilot-YYYY-MM-DD -->`, "
        "`sessions=<N>`, and `coverage=<healthy|degraded>` in its AI evidence block."
    )


def _apply_agent_retro_autopilot(*, runner: Callable[..., subprocess.CompletedProcess[str]], server_url: str,
                                  workspace: str, receipt: Path | None = None) -> dict[str, Any]:
    prompt = _agent_retro_prompt()
    listed = _parse_multica_json(runner(["autopilot", "list", "--output", "json"], server_url=server_url, workspace_id=workspace), "autopilot list")
    autopilots = listed.get("autopilots", []) if isinstance(listed, Mapping) else (listed if isinstance(listed, list) else [])
    existing = next((ap for ap in autopilots if isinstance(ap, Mapping) and
                     ("Daily Agent Trajectory Retro" in str(ap.get("title") or "") or "agent-retro-diagnostic" in str(ap.get("description") or ""))), None)
    if existing:
        ap_id = str(existing.get("id"))
        ap_action = "updated"
        ap_obj = _parse_multica_json(runner(["autopilot", "update", ap_id, "--title", DEFAULT_AUTOPILOT_TITLE,
                                             "--agent", DEFAULT_AGENT_ID, "--mode", "run_only", "--status", "active",
                                             "--subscriber", DEFAULT_OWNER_ID, "--description", prompt, "--output", "json"],
                                            server_url=server_url, workspace_id=workspace), "autopilot update")
    else:
        ap_action = "created"
        ap_obj = _parse_multica_json(runner(["autopilot", "create", "--title", DEFAULT_AUTOPILOT_TITLE,
                                             "--agent", DEFAULT_AGENT_ID, "--mode", "run_only", "--subscriber", DEFAULT_OWNER_ID,
                                             "--description", prompt, "--output", "json"], server_url=server_url, workspace_id=workspace), "autopilot create")
        ap_id = str(ap_obj.get("id") or (ap_obj.get("autopilot") or {}).get("id") or "")
        if not ap_id:
            raise RuntimeError(f"Could not extract agent-retro autopilot id: {ap_obj}")
    trig_data = _parse_multica_json(runner(["autopilot", "trigger-list", ap_id, "--output", "json"], server_url=server_url, workspace_id=workspace), "autopilot trigger-list")
    triggers = trig_data.get("triggers", []) if isinstance(trig_data, Mapping) else (trig_data if isinstance(trig_data, list) else [])
    sched = next((t for t in triggers if isinstance(t, Mapping) and str(t.get("kind") or "schedule") == "schedule"), None)
    if sched:
        trig_id = str(sched.get("id"))
        trigger_action = "unchanged" if (str(sched.get("cron") or sched.get("cron_expression") or "") == DEFAULT_CRON and
                                          str(sched.get("timezone") or "") == DEFAULT_TIMEZONE and
                                          str(sched.get("label") or DEFAULT_TRIGGER_LABEL) == DEFAULT_TRIGGER_LABEL) else "updated"
        if trigger_action == "updated":
            trigger = _parse_multica_json(runner(["autopilot", "trigger-update", ap_id, trig_id, "--cron", DEFAULT_CRON,
                                                   "--timezone", DEFAULT_TIMEZONE, "--label", DEFAULT_TRIGGER_LABEL, "--output", "json"],
                                                  server_url=server_url, workspace_id=workspace), "autopilot trigger-update")
        else:
            trigger = sched
    else:
        trigger_action = "created"
        trigger = _parse_multica_json(runner(["autopilot", "trigger-add", ap_id, "--kind", "schedule", "--cron", DEFAULT_CRON,
                                               "--timezone", DEFAULT_TIMEZONE, "--label", DEFAULT_TRIGGER_LABEL, "--output", "json"],
                                              server_url=server_url, workspace_id=workspace), "autopilot trigger-add")
    result = {"ok": True, "autopilot_action": ap_action, "autopilot_id": ap_id, "autopilot": ap_obj,
              "trigger_action": trigger_action, "trigger": trigger, "prompt": prompt}
    if receipt:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["receipt"] = str(receipt)
    return result


def command_autopilot(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.handoff_root or "~/Development/handoff").expanduser()
    config = {"name": DEFAULT_AUTOPILOT_TITLE, "schedule": {"cron": DEFAULT_CRON, "timezone": DEFAULT_TIMEZONE, "mode": "run_only"},
              "command": "python3 skills/agent-retro/scripts/agent_retro.py collect --days 3 --json", "skill": "agent-retro",
              "prompt": _agent_retro_prompt()}
    path = root / "agent-retro-autopilot.json"
    dry_run = bool(getattr(args, "dry_run", False) or not getattr(args, "apply", False))
    result = {"schema": "agent-retro-autopilot/v1", "ok": True, "dry_run": dry_run, "config": config,
              "path": str(path), "written": False, "skill_retro_schedule_untouched": True}
    if not dry_run:
        result.update(_apply_agent_retro_autopilot(runner=getattr(args, "runner", _default_multica_runner),
                                                   server_url=getattr(args, "server", "") or DEFAULT_SERVER_URL,
                                                   workspace=getattr(args, "workspace", "") or DEFAULT_WORKSPACE_ID,
                                                   receipt=Path(args.receipt).expanduser() if getattr(args, "receipt", None) else None))
        result["written"] = _safe_write(path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return result
