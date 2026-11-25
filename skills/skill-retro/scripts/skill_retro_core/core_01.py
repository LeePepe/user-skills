from __future__ import annotations
from .common import *

# AST-partitioned skill-retro implementation module.

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

    # Bounded callers only need recent, relevant records; avoid git work for unrelated skills.
    if len(rows) > 5000:
        rows = rows[-5000:]
    for idx, raw_rec in enumerate(rows):
        raw_skill = _norm_skill(str(raw_rec.get("skill") or ""))
        if known_skills and raw_skill and raw_skill not in known_skills:
            continue
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
