from __future__ import annotations

from ._imports import *

from .constants_and_paths import _run_command

def _post_json(url: str, payload: dict[str, Any], *, token: str = "", timeout: float = 5.0) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={"Content-Type": "application/json", "Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise RouterError(f"non-object response from {url}")
    if body.get("ok") is False:
        raise RouterError(_text(body.get("message") or body.get("error"), f"request failed: {url}"))
    return body

def _url_base(target: dict[str, Any], plan: dict[str, Any]) -> str:
    explicit = _text(target.get("dsh_base_url") or target.get("base_url") or os.environ.get("DSH_M2_BASE_URL"))
    if explicit:
        return explicit.rstrip("/")
    state_url = _text(plan.get("discovery", {}).get("dsh_state_url"))
    return re.sub(r"/m2-omni-hub/api/state/?$", "", state_url).rstrip("/")

def _apply_dsh(target: dict[str, Any], plan: dict[str, Any], prompt: str, *, poster: Callable[..., dict[str, Any]] = _post_json) -> dict[str, Any]:
    base = _url_base(target, plan)
    if not base:
        raise RouterError(f"no dsh endpoint for {target.get('target_repo', 'target')}")
    token = _text(target.get("dsh_token") or os.environ.get("DSH_M2_TOKEN"))
    session_id = _text(target.get("session_id"))
    expanded_cwd = _expand_cwd(target.get("cwd") or os.environ.get("DSH_M2_CWD", ""))
    created = False
    if target.get("m2_status") == "NEW_M2":
        created_body = poster(base + "/m2-omni-hub/api/create-session", {"topic": target.get("target_repo", "New M2"), "cwd": expanded_cwd}, token=token)
        session = created_body.get("session") if isinstance(created_body.get("session"), dict) else created_body
        session_id = _text(session.get("sessionId") or session.get("id"))
        if not session_id:
            raise RouterError("dsh create-session did not return a session id")
        created = True
    if not session_id:
        raise RouterError("dsh target has no session id")
    delivery = {"sessionStrategy": "pinned", "sessionKey": f"m2-router:{target.get('target_repo', session_id)}", "sessionId": session_id, "title": target.get("title") or f"[M2] {target.get('target_repo', 'M2')}", "markdown": prompt, "deliveryKey": f"{plan['plan_id']}:{target.get('target_repo', session_id)}"}
    try:
        receipt = poster(base + "/dsh-session-sink/api/deliver", delivery, token=token)
        return {"ok": True, "method": "dsh.session-sink", "target": session_id, "created": created, "cwd": expanded_cwd, "receipt": receipt}
    except Exception:
        if created:
            raise
        receipt = poster(base + "/m2-omni-hub/api/dispatch", {"message": prompt, "targetSessionIds": [session_id], "forceMode": "single"}, token=token)
        return {"ok": True, "method": "dsh.omni-hub.dispatch", "target": session_id, "created": False, "cwd": expanded_cwd, "receipt": receipt}

def _herdr_binary(target: dict[str, Any]) -> str:
    return _text(target.get("herdr_bin") or os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr")

def _new_herdr_agent_name(target: dict[str, Any], *, index: int = 1, used_names: set[str] | None = None) -> str:
    explicit = _text(target.get("agent_name"))
    if explicit:
        base_name = explicit[:32].rstrip("-")
    else:
        raw_topic = _text(target.get("topic_id")).lower()
        if raw_topic.startswith("new-topic-"):
            raw_topic = raw_topic[len("new-topic-"):]
        topic_slug = re.sub(r"[^a-z0-9_-]+", "-", raw_topic).strip("-")
        repo_slug = re.sub(r"[^a-z0-9_-]+", "-", _text(target.get("target_repo"), "m2").lower()).strip("-") or "m2"
        if topic_slug and topic_slug not in {NEW_UNMAPPED_DOMAIN.lower(), "unmapped-domain"} and topic_slug != repo_slug:
            full_slug = topic_slug if repo_slug in topic_slug else f"{topic_slug}-{repo_slug}"
        else:
            full_slug = repo_slug
        if len(full_slug) > 25:
            digest = hashlib.sha256(f"{repo_slug}:{topic_slug or full_slug}".encode("utf-8")).hexdigest()[:6]
            slug = f"{full_slug[:18].rstrip('-')}-{digest}"
        else:
            slug = full_slug
        base_name = f"m2-{slug[:25]}-r1"[:32].rstrip("-")
    if used_names is not None and base_name in used_names:
        stem = re.sub(r"-r\d+$", "", base_name)[:24].rstrip("-")
        base_name = f"{stem}-{index}-r1"[:32].rstrip("-")
    if used_names is not None:
        used_names.add(base_name)
    return base_name

def _resolve_target_transport(target: dict[str, Any], plan: dict[str, Any], requested: str) -> str:
    selected = requested if requested != "auto" else _text(target.get("transport"), "auto")
    if selected != "auto":
        return selected
    source = _text(target.get("source"))
    if source in {"herdr", "dsh"}:
        return source
    discovered_sources = {_text(item.get("source")) for item in (plan.get("discovery") or {}).get("sessions", []) if isinstance(item, dict)}
    if "herdr" in discovered_sources:
        return "herdr"
    if "dsh" in discovered_sources or _url_base(target, plan):
        return "dsh"
    if _text(target.get("dispatch_script") or os.environ.get("M2_ROUTER_HERDR_DISPATCH") or os.environ.get("HERDR_PANE_ID") or os.environ.get("HERDR_BIN") or shutil.which("herdr")):
        return "herdr"
    raise RouterError(f"cannot resolve delivery transport for NEW_M2 target {target.get('target_repo', 'unspecified')}; specify --transport herdr|dsh|stdout")

def _authoritative_apply_space(target: dict[str, Any], plan: dict[str, Any], domain_map_file: str | Path | None = None) -> tuple[str, list[str]]:
    topic_id = _text(target.get("topic_id"))
    if topic_id and topic_id != NEW_UNMAPPED_DOMAIN:
        try:
            topic = _topic_by_id(load_domain_map(domain_map_file or plan.get("domain_map_file"))).get(topic_id)
        except RouterError:
            topic = None
        if topic:
            return _text(topic.get("space")) or _text(topic.get("target_repo"), "M2"), list(topic.get("space_aliases") or [])
    proposed = target.get("proposed_domain") if isinstance(target.get("proposed_domain"), dict) else {}
    return (
        _text(proposed.get("space") or target.get("space") or target.get("target_repo"), "M2"),
        list(proposed.get("space_aliases") or target.get("space_aliases") or []),
    )

def _apply_herdr(target: dict[str, Any], plan: dict[str, Any], prompt: str, *, runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command, index: int = 1, used_names: set[str] | None = None, created_workspaces: dict[str, str] | None = None, domain_map_file: str | Path | None = None) -> dict[str, Any]:
    binary = _herdr_binary(target)
    model = _text(target.get("model"), _text(plan.get("default_model"), DEFAULT_M2_MODEL))
    context = _text(target.get("context"), _text(plan.get("default_context"), DEFAULT_M2_CONTEXT))
    model_label = _text(target.get("model_label"), _text(plan.get("default_model_label"), DEFAULT_M2_MODEL_LABEL))
    expanded_cwd = _expand_cwd(_text(target.get("cwd")) or str(Path.home() / "Development"))
    if target.get("m2_status") == "EXISTING_M2":
        agent = _text(target.get("herdr_target") or target.get("session_id"))
        if not agent:
            raise RouterError("Herdr target has no agent name")
        result = runner([binary, "agent", "prompt", agent, prompt], timeout=10.0)
        if result.returncode != 0:
            raise RouterError(f"Herdr prompt failed for {agent}: {result.stderr.strip()}")
        return {"ok": True, "method": "herdr.agent.prompt", "target": agent, "created": False, "model": model, "context": context, "model_label": model_label, "cwd": expanded_cwd, "stdout": result.stdout.strip()}

    name = _new_herdr_agent_name(target, index=index, used_names=used_names)
    dispatch = _text(target.get("dispatch_script") or os.environ.get("M2_ROUTER_HERDR_DISPATCH")) or str(Path.home() / "Development" / "user-skills" / "skills" / "team-lead" / "scripts" / "herdr-dispatch.sh")
    mapped_space, _ = _authoritative_apply_space(target, plan, domain_map_file)
    spawn_mode = "new_workspace"
    args = [
        dispatch,
        "--name",
        name,
        "--kind",
        _text(target.get("kind"), "copilot"),
        "--cwd",
        expanded_cwd,
        "--prompt",
        prompt,
        "--new-workspace",
        "--workspace-label",
        mapped_space,
        "--",
        "--model",
        model,
        "--context",
        context,
    ]
    result = runner(args, timeout=30.0)
    if result.returncode != 0:
        raise RouterError(f"Herdr new M2 failed for {name}: {result.stderr.strip()}")
    parsed = _parse_json_output(result.stdout)
    reused = bool(isinstance(parsed, dict) and parsed.get("reused_existing"))
    resolved_ws = _text((parsed or {}).get("workspace_id")) if isinstance(parsed, dict) else ""
    if resolved_ws and created_workspaces is not None:
        created_workspaces[name] = resolved_ws
    if reused and _text((parsed or {}).get("agent_status")).casefold() == "working":
        prompt_res = runner([binary, "agent", "prompt", name, prompt], timeout=10.0)
        if prompt_res.returncode != 0:
            raise RouterError(f"Herdr prompt failed for reused working agent {name}: {prompt_res.stderr.strip()}")
    return {"ok": True, "method": "herdr-dispatch", "target": name, "created": not reused, "model": model, "context": context, "model_label": model_label, "cwd": expanded_cwd, "space": mapped_space, "workspace_id": resolved_ws, "spawn_mode": spawn_mode, "receipt": parsed or result.stdout.strip()}

def _register_new_domains(plan: dict[str, Any], domain_map_file: str | Path | None = None, *, dry_run: bool = False) -> list[str]:
    candidates = plan.get("new_domain_candidates", [])
    if not candidates:
        return []
    path = Path(domain_map_file or plan.get("domain_map_file") or DEFAULT_DOMAIN_MAP_PATH).expanduser()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RouterError(f"unable to update domain map {path}: {error}") from error
    groups = value.setdefault("groups", [])
    registered: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or not _text(candidate.get("topic_id")):
            continue
        topic_id = candidate["topic_id"]
        if any(isinstance(group, dict) and any(isinstance(topic, dict) and topic.get("topic_id") == topic_id for topic in group.get("topics", [])) for group in groups):
            continue
        group_id = _text(candidate.get("group"), "new-domain")
        group = next((item for item in groups if isinstance(item, dict) and item.get("group") == group_id), None)
        if group is None:
            group = {"group": group_id, "title": group_id.replace("-", " ").title(), "topics": []}
            groups.append(group)
        topic = {key: candidate.get(key) for key in ("topic_id", "group", "title", "target_repo", "cwd", "space", "space_aliases", "keywords", "aliases")}
        group.setdefault("topics", []).append(topic)
        registered.append(topic_id)
    if registered and not dry_run:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    return registered

def _revalidate_herdr_candidate(
    candidate: dict[str, Any],
    *,
    binary: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[bool, str]:
    pane_id = _text(candidate.get("pane_id"))
    session_id = _text(candidate.get("session_id"))
    try:
        pane_res = runner([binary, "pane", "list"], timeout=3.0)
        agent_res = runner([binary, "agent", "list"], timeout=3.0)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"revalidation_failed:{error}"
    pane_records = _records(_parse_json_output(pane_res.stdout)) if getattr(pane_res, "returncode", 1) == 0 else []
    agent_records = _records(_parse_json_output(agent_res.stdout)) if getattr(agent_res, "returncode", 1) == 0 else []
    if not pane_records and not agent_records:
        return False, "revalidation_inventory_unavailable"
    merged = _merge_herdr_records(pane_records, agent_records)
    live = next(
        (
            rec for rec in merged
            if _text(rec.get("pane_id") or rec.get("paneId")) == pane_id
            or (session_id and _session_id(rec) == session_id)
        ),
        None,
    )
    if live is None:
        return False, "candidate_missing"
    live_pane = _text(live.get("pane_id") or live.get("paneId"))
    if not live_pane or live_pane != pane_id:
        return False, "pane_identity_changed"
    live_sid = _session_id(live)
    if session_id and live_sid and live_sid not in {session_id, pane_id}:
        return False, "session_identity_changed"
    live_status = _text(live.get("agent_status") or live.get("status")).casefold()
    if live_status not in {"idle", "done"}:
        return False, f"pane_not_idle:{live_status or 'unknown'}"
    if _gc_protected(live) or pane_id in {"w2J:p1", "w1:p1"}:
        return False, "protected_or_active_tail"
    visible = _text(live.get("visible") or live.get("visible_text"))
    if not visible:
        visible = _read_herdr_visible(binary, pane_id, runner=runner)
    if not visible or _has_active_visible_tail(visible):
        return False, "visible_tail_unavailable_or_active"
    return True, "ok"

def _close_herdr_pane(
    candidate: dict[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run_command,
    dry_run: bool = False,
) -> dict[str, Any]:
    pane_id = _text(candidate.get("pane_id"))
    if not pane_id or pane_id == _text(os.environ.get("HERDR_PANE_ID")) or pane_id in {"w1:p1", "w2J:p1"} or re.search(r"Cindy|大总管", " ".join(_text(candidate.get(k)) for k in ("workspace_label", "workspaceLabel", "space", "label", "title", "name")), re.I):
        return {"ok": False, "method": "herdr.pane.close", "pane_id": pane_id, "skipped": True, "reason": "protected_pane"}
    if dry_run:
        return {"ok": True, "method": "stdout", "pane_id": pane_id, "skipped": False, "simulated": True}
    binary = _text(os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr")
    eligible, reason = _revalidate_herdr_candidate(candidate, binary=binary, runner=runner)
    if not eligible:
        return {"ok": False, "method": "herdr.pane.close", "pane_id": pane_id, "skipped": True, "reason": reason}
    result = runner([binary, "pane", "close", pane_id], timeout=10.0)
    if result.returncode != 0:
        raise RouterError(f"Herdr pane close failed for {pane_id}: {result.stderr.strip()}")
    return {"ok": True, "method": "herdr.pane.close", "pane_id": pane_id, "skipped": False, "stdout": result.stdout.strip()}

def _cleanup_stale(
    plan: dict[str, Any],
    mode: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    dry_run: bool = False,
    dry_run_sessions: set[str] | None = None,
) -> list[dict[str, Any]]:
    if mode == "none":
        return []
    receipts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in plan.get("stale_m2_candidates", []):
        session_id = _text(candidate.get("session_id"))
        if session_id in seen:
            continue
        seen.add(session_id)
        if mode == "duplicates" and candidate.get("reason") != "duplicate_m2":
            continue
        if mode == "replace_context" and candidate.get("reason") != "context_eviction":
            continue
        candidate_dry_run = dry_run or bool(dry_run_sessions and session_id in dry_run_sessions)
        receipts.append({"session_id": session_id, "reason": candidate.get("reason"), **_close_herdr_pane(candidate, runner=runner, dry_run=candidate_dry_run)})
    return receipts

def _resolve_supervision_state_path(
    state_file: str | Path | None = None,
    *,
    plan_file: str | Path | None = None,
    apply_receipt_file: str | Path | None = None,
    apply_receipt: dict[str, Any] | None = None,
    safe_plan_id: str = "m2p-plan",
    persist_by_default: bool = False,
) -> Path | None:
    if state_file:
        return Path(state_file).expanduser()
    if isinstance(apply_receipt, dict) and _text(apply_receipt.get("supervision_state_path")):
        return Path(_text(apply_receipt.get("supervision_state_path"))).expanduser()
    env_state = _text(os.environ.get("M2_SUPERVISION_STATE_FILE"))
    if env_state:
        return Path(env_state).expanduser()
    if apply_receipt_file or plan_file:
        anchor = Path(apply_receipt_file or plan_file).expanduser()
        if anchor.parent in {Path("/tmp"), Path("/private/tmp")}:
            return Path("/tmp/m2-supervision-state.json")
        return anchor.parent / "m2-supervision-state.json"
    if persist_by_default:
        return Path("/tmp/m2-supervision-state.json")
    return None

def _with_locked_supervision_state(
    state_path: Path | None,
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    reset: bool = False,
) -> dict[str, Any]:
    if state_path is None:
        initial: dict[str, Any] = {"schema_version": 1, "slots": [], "events": []}
        return mutator(initial)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            current: dict[str, Any] = {"schema_version": 1, "slots": [], "events": []}
            preserved_keys: dict[str, Any] = {}
            if not reset and state_path.is_file():
                try:
                    loaded = json.loads(state_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        current = {
                            "schema_version": 1,
                            "slots": [dict(s) for s in (loaded.get("slots") or []) if isinstance(s, dict)],
                            "events": [dict(e) for e in (loaded.get("events") or []) if isinstance(e, dict)],
                        }
                        if "backlog" in loaded and isinstance(loaded.get("backlog"), list):
                            current["backlog"] = [dict(b) for b in loaded["backlog"] if isinstance(b, dict)]
                            preserved_keys["backlog"] = current["backlog"]
                        if "spaces" in loaded and isinstance(loaded.get("spaces"), list):
                            current["spaces"] = [dict(sp) for sp in loaded["spaces"] if isinstance(sp, dict)]
                            preserved_keys["spaces"] = current["spaces"]
                        if "agents" in loaded and isinstance(loaded.get("agents"), list):
                            current["agents"] = [dict(ag) for ag in loaded["agents"] if isinstance(ag, dict)]
                            preserved_keys["agents"] = current["agents"]
                        if "reported_issue_markers" in loaded and isinstance(loaded.get("reported_issue_markers"), list):
                            current["reported_issue_markers"] = [str(m) for m in loaded["reported_issue_markers"] if _text(m)]
                            preserved_keys["reported_issue_markers"] = current["reported_issue_markers"]
                except (OSError, ValueError):
                    pass
            updated = mutator(current)
            for p_key, p_val in preserved_keys.items():
                if p_key not in updated:
                    updated[p_key] = p_val
            temp_path = state_path.with_suffix(state_path.suffix + f".{os.getpid()}.tmp")
            temp_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp_path.replace(state_path)
            return updated
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

def _match_existing_slot_index(
    slots: list[dict[str, Any]],
    rec: dict[str, Any],
    target: dict[str, Any],
) -> int | None:
    explicit_slot = _text(target.get("slot_id") or rec.get("slot_id"))
    if explicit_slot:
        for idx, slot in enumerate(slots):
            if _text(slot.get("slot_id")).casefold() == explicit_slot.casefold():
                return idx
        return None
    explicit_label = _text(target.get("task_label") or rec.get("task_label"))
    if explicit_label:
        for idx, slot in enumerate(slots):
            if _text(slot.get("slot_id")).casefold() == explicit_label.casefold() or _text(slot.get("base_label")).casefold() == explicit_label.casefold():
                return idx
        m_prefix = re.match(r"^(.+?)\d+$", explicit_label)
        if m_prefix:
            base_cand = m_prefix.group(1).strip()
            for idx, slot in enumerate(slots):
                if _text(slot.get("slot_id")).casefold() == base_cand.casefold() or _text(slot.get("base_label")).casefold() == base_cand.casefold():
                    return idx
        return None
    topic_id = _text(rec.get("topic_id") or target.get("topic_id"))
    has_named_topic = bool(topic_id and topic_id not in {"unspecified", NEW_UNMAPPED_DOMAIN} and not topic_id.startswith("new-topic-unspecified"))
    pane_id = _text(rec.get("pane_id") or target.get("pane_id"))
    if pane_id:
        for idx, slot in enumerate(slots):
            slot_topic = _text(slot.get("topic_id"))
            if has_named_topic and slot_topic and slot_topic.casefold() != topic_id.casefold():
                continue
            if _text(slot.get("pane_id")) == pane_id:
                return idx
    is_stdout = _text(rec.get("method") or target.get("transport")).lower() == "stdout"
    session_id = _text(rec.get("session_id") or target.get("session_id"))
    if session_id and session_id.casefold() != "unspecified" and not (is_stdout and session_id == _text(rec.get("target_repo"))):
        for idx, slot in enumerate(slots):
            slot_topic = _text(slot.get("topic_id"))
            if has_named_topic and slot_topic and slot_topic.casefold() != topic_id.casefold():
                continue
            if _text(slot.get("session_id")) == session_id or _text(slot.get("target")) == session_id:
                return idx
    target_ref = _text(rec.get("target") or target.get("herdr_target"))
    if target_ref and target_ref.casefold() != "unspecified" and not (is_stdout and target_ref == _text(rec.get("target_repo"))):
        for idx, slot in enumerate(slots):
            slot_topic = _text(slot.get("topic_id"))
            if has_named_topic and slot_topic and slot_topic.casefold() != topic_id.casefold():
                continue
            if _text(slot.get("target")) == target_ref or _text(slot.get("session_id")) == target_ref:
                return idx
    if has_named_topic:
        for idx, slot in enumerate(slots):
            if _text(slot.get("topic_id")).casefold() == topic_id.casefold() or _text(slot.get("slot_id")).casefold() == topic_id.casefold():
                return idx
    return None
