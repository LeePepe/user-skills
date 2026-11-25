from __future__ import annotations
from .common import *

"""Trace Ingest implementation for the skill-retro engine."""

def _norm_skill(name: str) -> str:
    cleaned = str(name or '').strip().lstrip('/')
    if cleaned in {'goal-target-task', 'goal-task'}:
        return 'team-lead'
    return cleaned

def _redact_command_sample(raw_cmd: str, *, skills_root: Path | None=None, dev_root: Path | None=None) -> str:
    text = ' '.join(str(raw_cmd or '').strip().split())
    text = _SECRET_SCRUB_RE.sub('<REDACTED>', text)
    home_str = str(Path.home())
    if dev_root is not None:
        text = text.replace(str(dev_root), '~/Development')
    if skills_root is not None:
        text = text.replace(str(skills_root.parent), '~/Development/user-skills')
    if home_str and home_str != '/':
        text = text.replace(home_str, '~')
    text = re.sub('(?:/' + 'Users|/home)/[^/\\s\\"\']+', '~', text)
    text = re.sub('/private/var/' + 'folders/[^\\s\\"\']+', '/tmp/<temp>', text)
    if len(text) > 220:
        text = text[:217].rstrip() + '...'
    return text

def _percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted((float(v) for v in values))
    idx = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * (pct / 100.0)) - 1))
    return round(ordered[idx], 2)

def _find_git_root_for_path(target_path: Path) -> tuple[Path | None, str]:
    cur = target_path.resolve() if target_path.exists() else target_path
    search_dir = cur if cur.is_dir() else cur.parent
    while search_dir != search_dir.parent:
        if (search_dir / '.git').exists():
            try:
                return (search_dir, cur.relative_to(search_dir).as_posix())
            except ValueError:
                break
        search_dir = search_dir.parent
    return (None, target_path.name)

def _git_last_modified_epoch(target_path: Path, *, fallback_mtime: bool=False) -> int | None:
    git_root, rel_path = _find_git_root_for_path(target_path)
    if git_root is not None:
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            proc = subprocess.run(['git', '--no-optional-locks', 'log', '-1', '--format=%ct', '--', rel_path], cwd=git_root, env=clean_env, capture_output=True, text=True, timeout=2.5, check=False)
            if proc.returncode == 0 and proc.stdout.strip().isdigit():
                return int(proc.stdout.strip())
    if fallback_mtime and target_path.exists():
        with contextlib.suppress(OSError):
            return int(target_path.stat().st_mtime)
    return None

def _compute_pkg_version_fingerprint(pkg_dir: Path) -> dict[str, Any]:
    plugin_version = '1.0.0'
    manifest_p = pkg_dir / '.claude-plugin' / 'plugin.json'
    if manifest_p.is_file():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            mdoc = json.loads(manifest_p.read_text(encoding='utf-8'))
            if isinstance(mdoc, Mapping) and str(mdoc.get('version') or '').strip():
                plugin_version = str(mdoc['version']).strip()
    git_root, rel_path = _find_git_root_for_path(pkg_dir)
    commit_count = 0
    subtree_sha = ''
    last_modified_epoch: int | None = None
    if git_root is not None and pkg_dir.is_dir():
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            cnt_p = subprocess.run(['git', '--no-optional-locks', 'rev-list', '--count', 'HEAD', '--', rel_path], cwd=git_root, env=clean_env, capture_output=True, text=True, timeout=2.5, check=False)
            if cnt_p.returncode == 0 and cnt_p.stdout.strip().isdigit():
                commit_count = int(cnt_p.stdout.strip())
            tree_p = subprocess.run(['git', '--no-optional-locks', 'rev-parse', '--short', f'HEAD:{rel_path}'], cwd=git_root, env=clean_env, capture_output=True, text=True, timeout=2.5, check=False)
            if tree_p.returncode == 0 and tree_p.stdout.strip():
                subtree_sha = tree_p.stdout.strip()
            ts_p = subprocess.run(['git', '--no-optional-locks', 'log', '-1', '--format=%ct', '--', rel_path], cwd=git_root, env=clean_env, capture_output=True, text=True, timeout=2.5, check=False)
            if ts_p.returncode == 0 and ts_p.stdout.strip().isdigit():
                last_modified_epoch = int(ts_p.stdout.strip())
    if not subtree_sha:
        h = hashlib.sha256()
        if pkg_dir.is_dir():
            for fp in sorted(pkg_dir.rglob('*')):
                if not fp.is_file() or '__pycache__' in fp.parts or fp.name.startswith('.'):
                    continue
                with contextlib.suppress(OSError):
                    h.update(fp.relative_to(pkg_dir).as_posix().encode('utf-8') + b'\x00' + fp.read_bytes() + b'\x00')
        subtree_sha = h.hexdigest()[:7]
    if commit_count <= 0:
        commit_count = 1
    return {'skill_version': f'v{commit_count}@{subtree_sha}', 'commit_count': commit_count, 'subtree_sha': subtree_sha, 'plugin_version': plugin_version, 'last_modified_epoch': last_modified_epoch}

def stamp_skill_telemetry_event(event: Mapping[str, Any], *, inventory: Mapping[str, Mapping[str, Any]] | None=None, skills_root: Path | None=None) -> dict[str, Any]:
    """Stamp `skill_version` and `subtree_sha` on a `skill_turn`, `reference_hit`, or `script_call` event."""
    out = dict(event)
    kind = str(out.get('kind') or '').strip()
    sk = _norm_skill(str(out.get('skill') or ''))
    if kind not in {'skill_turn', 'reference_hit', 'script_call'} or not sk:
        return out
    meta = inventory.get(sk) if inventory is not None else None
    if meta is None:
        s_root = skills_root or DEFAULT_SKILLS_ROOT
        pkg_dir = s_root / sk
        if pkg_dir.is_dir():
            meta = _compute_pkg_version_fingerprint(pkg_dir)
    if isinstance(meta, Mapping):
        if not out.get('skill_version') and meta.get('skill_version'):
            out['skill_version'] = str(meta['skill_version'])
        if not out.get('subtree_sha') and meta.get('subtree_sha'):
            out['subtree_sha'] = str(meta['subtree_sha'])
    return out

def _is_call_post_fix(call_ts: datetime | None, call_skill_version: str | None, *, current_skill_version: str | None, watermark_epoch: int | None) -> bool:
    """Return True if an error occurred on the current version or after the last-modified commit watermark."""
    if call_skill_version and current_skill_version and (call_skill_version != current_skill_version):
        return False
    if watermark_epoch is not None and call_ts is not None:
        if call_ts.timestamp() < float(watermark_epoch):
            return False
    return True

def _parse_ts(raw: Any, fallback: datetime | None=None) -> datetime | None:
    if raw is None or raw == '':
        return fallback
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val > 1e+16:
            sec = val / 1000000000.0
        elif val > 1000000000000.0:
            sec = val / 1000.0
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
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
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
    origin_source: str = 'copilot_events'

@dataclass
class SessionObservation:
    session_id: str
    source: str
    started_at: datetime
    ended_at: datetime
    skills_loaded: set[str] = field(default_factory=set)
    ref_reads: list[tuple[str, str, datetime, int]] = field(default_factory=list)
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
    shell_exec = data.get('shellExecution')
    if isinstance(shell_exec, Mapping) and shell_exec.get('exitCode') is not None:
        with contextlib.suppress(TypeError, ValueError):
            return int(shell_exec['exitCode'])
    m_done = _SHELL_COMPLETE_RE.search(result_text)
    if m_done:
        with contextlib.suppress(ValueError):
            return int(m_done.group(2))
    return None

def _normalize_ref_rel(raw_path: str) -> tuple[str, str] | None:
    m = _REF_PATH_RE.search(raw_path)
    if m:
        return (_norm_skill(m.group('skill')), f"references/{m.group('ref').lstrip('/')}")
    return None

def _extract_script_targets(command: str, *, known_skills: set[str], basename_to_targets: Mapping[str, list[tuple[str, str]]], active_skills: set[str]) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in _SKILL_SCRIPT_RE.finditer(command):
        sk = _norm_skill(m.group('skill'))
        rel = f"scripts/{m.group('script').lstrip('/')}"
        if (sk, rel) not in seen:
            seen.add((sk, rel))
            targets.append((sk, rel))
    for m_ver in _REPO_VERIFY_SCRIPT_RE.finditer(command):
        sc_token = m_ver.group('script').split()[0]
        rel_ver = f'scripts/{sc_token}'
        if ('repo-gate', rel_ver) not in seen:
            seen.add(('repo-gate', rel_ver))
            targets.append(('repo-gate', rel_ver))
    for bname, candidates in basename_to_targets.items():
        if bname in command and (not any((rel.endswith(f'/{bname}') for _, rel in targets))):
            if re.search(f'(?<![A-Za-z0-9_.-]){re.escape(bname)}(?![A-Za-z0-9_.-])', command):
                chosen = [c for c in candidates if c[0] in active_skills] or (candidates if len(candidates) == 1 else [])
                for item in chosen:
                    if item[0] in known_skills and item not in seen:
                        seen.add(item)
                        targets.append(item)
    return targets

def _extract_wild_manual_signature(raw_command: str, *, skills_root: Path | None=None, dev_root: Path | None=None) -> tuple[str, str] | None:
    """Return (canonical_pattern_signature, redacted_example) for manual ad-hoc diagnostic commands."""
    segments = [seg.strip() for seg in re.split('\\s*(?:&&|;|\\|\\|)\\s*', raw_command or '') if seg.strip()]
    for seg in segments:
        if _SKILL_SCRIPT_RE.search(seg) or _REPO_VERIFY_SCRIPT_RE.search(seg):
            continue
        if 'unittest' in seg or 'pytest' in seg:
            continue
        lower = seg.lower()
        if lower in _TRIVIAL_SHELL_EXACT:
            continue
        has_pipe_probe = '|' in seg and any((tok in lower for tok in ('jq', 'awk', 'sed', 'sort', 'uniq', 'wc', 'python3 -c')))
        if not (any((lower.startswith(pref) for pref in _WILD_CMD_PREFIXES)) or has_pipe_probe):
            continue
        redacted = _redact_command_sample(seg, skills_root=skills_root, dev_root=dev_root)
        sig = redacted
        if lower.startswith(('python3 -c', 'python -c')):
            imports = sorted(set(re.findall('\\bimport\\s+([A-Za-z0-9_]+)', seg)))
            mod_tag = '+'.join(imports[:4]) if imports else 'inline_probe'
            if 'sqlite3' in seg:
                sig = f'python3 -c [sqlite3:{mod_tag}]'
            elif 'json' in seg:
                sig = f'python3 -c [json:{mod_tag}]'
            else:
                sig = f'python3 -c [{mod_tag}]'
        elif lower.startswith('sqlite3 '):
            parts = seg.split(None, 2)
            db_part = Path(parts[1]).name if len(parts) >= 2 else 'db'
            sql_verb = 'query'
            m_sql = re.search('(?i)\\b(select|pragma|with)\\b\\s+([A-Za-z0-9_*.,()]+)?', seg)
            if m_sql:
                sql_verb = m_sql.group(1).upper()
            sig = f"sqlite3 {db_part} '{sql_verb} ...'"
        else:
            sig = re.sub('\\"[^\\"]{16,}\\"', '"<STR>"', sig)
            sig = re.sub("'[^']{16,}'", "'<STR>'", sig)
            sig = re.sub('~/[^\\s\\"\']+', '~/...', sig)
            sig = re.sub('\\b[0-9a-f]{7,40}\\b', '<SHA>', sig)
            sig = re.sub('\\b\\d{2,}\\b', '<N>', sig)
            sig = ' '.join(sig.split()[:6])
        return (sig, redacted)
    return None

def _parse_copilot_sessions(sessions_dir: Path | None, *, window_start: datetime, window_end: datetime, known_skills: set[str], basename_to_targets: Mapping[str, list[tuple[str, str]]], max_sessions: int | None=DEFAULT_MAX_COPILOT_SESSIONS_SCAN) -> dict[str, SessionObservation]:
    observations: dict[str, SessionObservation] = {}
    if not _probe_readable_dir(sessions_dir):
        return observations
    candidate_files: list[tuple[float, int, Path]] = []
    with contextlib.suppress(OSError):
        for child in _safe_iterdir(sessions_dir, sort_entries=False):
            if not _safe_is_dir(child) or child.name.startswith('.'):
                continue
            ev_file = child / 'events.jsonl'
            if not _safe_is_file(ev_file):
                continue
            with contextlib.suppress(OSError):
                st = ev_file.stat()
                mtime = st.st_mtime
                mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone(CST)
                if mtime_dt >= window_start:
                    candidate_files.append((mtime, int(st.st_size or 0), ev_file))
    candidate_files.sort(key=lambda item: -item[0])
    relevant_tokens = ('"session.start"', '"user.message"', '"skill.invoked"', '"tool.execution_start"', '"tool.execution_complete"')
    for mtime, file_size, ev_file in candidate_files if max_sessions is None else candidate_files[:max(0, int(max_sessions))]:
        if not _is_safe_local_file(ev_file):
            continue
        sid = ev_file.parent.name
        fallback_dt = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone(CST)
        obs = SessionObservation(session_id=sid, source='copilot_events', started_at=fallback_dt, ended_at=fallback_dt)
        tool_starts: dict[str, tuple[str, dict[str, Any], datetime, bool]] = {}
        pending_shells: dict[str, tuple[str, datetime, bool]] = {}
        seen_any = False
        try:
            raw_lines: list[str]
            if file_size > DEFAULT_MAX_SESSION_EVENTS_BYTES:
                half = DEFAULT_MAX_SESSION_EVENTS_BYTES // 2
                with ev_file.open('rb') as bhandle:
                    head_chunk = bhandle.read(half).decode('utf-8', errors='replace').splitlines()
                    if head_chunk:
                        head_chunk = head_chunk[:-1]
                    bhandle.seek(max(0, file_size - half))
                    tail_chunk = bhandle.read().decode('utf-8', errors='replace').splitlines()
                    if tail_chunk:
                        tail_chunk = tail_chunk[1:]
                raw_lines = head_chunk + tail_chunk
            else:
                raw_lines = _safe_read_text(ev_file).splitlines()
            for raw_line in raw_lines:
                line = raw_line.strip()
                if not line or not any((tok in line for tok in relevant_tokens)):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                ev_type = str(ev.get('type') or '')
                data = ev.get('data') if isinstance(ev.get('data'), dict) else {}
                raw_ts = ev.get('timestamp') or data.get('startTime')
                ts = _parse_ts(raw_ts, fallback_dt) or fallback_dt
                if not window_start <= ts < window_end and raw_ts is not None:
                    continue
                seen_any = True
                obs.started_at = min(obs.started_at, ts)
                obs.ended_at = max(obs.ended_at, ts)
                if ev_type == 'session.start':
                    cwd = str((data.get('context') or {}).get('cwd') or '')
                    for m_goal in _HANDOFF_GOAL_RE.finditer(cwd + '/'):
                        if not m_goal.group(1).startswith('_'):
                            obs.referenced_goals.add(m_goal.group(1))
                elif ev_type == 'user.message':
                    content = str(data.get('content') or '')
                    for m_goal in _HANDOFF_GOAL_RE.finditer(content):
                        if not m_goal.group(1).startswith('_'):
                            obs.referenced_goals.add(m_goal.group(1))
                    for m_sk in _SKILL_CONTEXT_RE.finditer(content):
                        sk_raw = m_sk.group('ctx') or m_sk.group('path') or m_sk.group('call')
                        if sk_raw:
                            norm_sk = _norm_skill(sk_raw)
                            if not known_skills or norm_sk in known_skills:
                                obs.skills_loaded.add(norm_sk)
                elif ev_type == 'skill.invoked':
                    sk_name = _norm_skill(data.get('name') or '')
                    if sk_name and (not known_skills or sk_name in known_skills):
                        obs.skills_loaded.add(sk_name)
                elif ev_type == 'tool.execution_start':
                    tc_id = str(data.get('toolCallId') or '')
                    t_name = str(data.get('toolName') or '')
                    args = data.get('arguments') if isinstance(data.get('arguments'), dict) else {}
                    has_ts = bool(ev.get('timestamp'))
                    if tc_id:
                        tool_starts[tc_id] = (t_name, args, ts, has_ts)
                    if t_name == 'skill' and args.get('skill'):
                        sk_name = _norm_skill(args['skill'])
                        if not known_skills or sk_name in known_skills:
                            obs.skills_loaded.add(sk_name)
                    elif t_name in {'view', 'read_file', 'read'} and args.get('path'):
                        vp = str(args.get('path') or '')
                        for m_goal in _HANDOFF_GOAL_RE.finditer(vp):
                            if not m_goal.group(1).startswith('_'):
                                obs.referenced_goals.add(m_goal.group(1))
                        norm_ref = _normalize_ref_rel(vp)
                        if norm_ref:
                            ref_sk, ref_rel = norm_ref
                            obs.skills_loaded.add(ref_sk)
                            obs.ref_reads.append((ref_sk, ref_rel, ts, obs.next_seq()))
                        else:
                            for m_sk in _SKILL_CONTEXT_RE.finditer(vp):
                                sk_p = m_sk.group('path')
                                if sk_p:
                                    norm_sk = _norm_skill(sk_p)
                                    if not known_skills or norm_sk in known_skills:
                                        obs.skills_loaded.add(norm_sk)
                    elif t_name in {'edit', 'create', 'apply_patch', 'write', 'multiedit'}:
                        fp = str(args.get('path') or args.get('file_path') or '')
                        if fp:
                            obs.edited_files.add(fp)
                        obs.edit_count += 1
                        e_seq = obs.next_seq()
                        obs.edit_seqs.append(e_seq)
                        obs.edit_events.append((ts, e_seq))
                elif ev_type == 'tool.execution_complete':
                    tc_id = str(data.get('toolCallId') or '')
                    res_obj = data.get('result')
                    res_text = str(res_obj.get('content') or '') if isinstance(res_obj, dict) else str(res_obj) if res_obj is not None else ''
                    out_chars = max(len(res_text), int(data.get('outputChars') or data.get('byteLen') or 0))
                    is_bloat = out_chars > ACI_OUTPUT_BLOAT_CHARS or any((marker in res_text for marker in _BLOAT_MARKERS))
                    if tc_id not in tool_starts:
                        continue
                    t_name, args, st_ts, st_has_ts = tool_starts[tc_id]
                    measured = bool(ev.get('timestamp')) and st_has_ts
                    dur_ms = max(1.0, (ts - st_ts).total_seconds() * 1000.0) if measured else None
                    if t_name == 'bash':
                        cmd_str = str(args.get('command') or '').strip()
                        if not cmd_str:
                            continue
                        for m_goal in _HANDOFF_GOAL_RE.finditer(cmd_str):
                            if not m_goal.group(1).startswith('_'):
                                obs.referenced_goals.add(m_goal.group(1))
                        for m_ref in _REF_PATH_RE.finditer(cmd_str):
                            ref_sk = _norm_skill(m_ref.group('skill'))
                            obs.ref_reads.append((ref_sk, f"references/{m_ref.group('ref').lstrip('/')}", st_ts, obs.next_seq()))
                        ec = _extract_exit_code_from_tool_complete(data, res_text)
                        m_bg = _SHELL_START_RE.search(res_text)
                        if ec is None and m_bg:
                            pending_shells[m_bg.group(1)] = (cmd_str, st_ts, st_has_ts)
                            continue
                        seq_no = obs.next_seq()
                        obs.bash_commands.append((cmd_str, ec, dur_ms, seq_no))
                        obs.bash_events.append((cmd_str, ec, dur_ms, st_ts, seq_no))
                        for sk_t, sc_rel in _extract_script_targets(cmd_str, known_skills=known_skills, basename_to_targets=basename_to_targets, active_skills=obs.skills_loaded):
                            obs.script_calls.append(ScriptCallEvent(session_id=sid, skill=sk_t, script_rel=sc_rel, command=cmd_str, ts=st_ts, exit_code=ec, duration_ms=dur_ms, measured=measured, seq=seq_no, output_chars=out_chars, aci_output_bloat=is_bloat))
                    elif t_name in {'read_bash', 'stop_bash'}:
                        sh_id = str(args.get('shellId') or '')
                        m_done = _SHELL_COMPLETE_RE.search(res_text)
                        target_sh = m_done.group(1) if m_done else sh_id
                        if target_sh and target_sh in pending_shells and m_done:
                            cmd_str, bg_st, bg_has_ts = pending_shells.pop(target_sh)
                            bg_measured = bool(ev.get('timestamp')) and bg_has_ts
                            bg_dur = max(1.0, (ts - bg_st).total_seconds() * 1000.0) if bg_measured else None
                            ec_val = int(m_done.group(2))
                            seq_no = obs.next_seq()
                            obs.bash_commands.append((cmd_str, ec_val, bg_dur, seq_no))
                            obs.bash_events.append((cmd_str, ec_val, bg_dur, bg_st, seq_no))
                            for sk_t, sc_rel in _extract_script_targets(cmd_str, known_skills=known_skills, basename_to_targets=basename_to_targets, active_skills=obs.skills_loaded):
                                obs.script_calls.append(ScriptCallEvent(session_id=sid, skill=sk_t, script_rel=sc_rel, command=cmd_str, ts=bg_st, exit_code=ec_val, duration_ms=bg_dur, measured=bg_measured, seq=seq_no, output_chars=out_chars, aci_output_bloat=is_bloat))
        except OSError:
            continue
        if seen_any and (obs.skills_loaded or obs.ref_reads or obs.script_calls or obs.edited_files or obs.bash_commands):
            observations[sid] = obs
    return observations

def _ingest_usage_jsonl_and_shards(observations: dict[str, SessionObservation], *, usage_jsonl: Path | None, shard_records: Sequence[Mapping[str, Any]], window_start: datetime, window_end: datetime, known_skills: set[str], inventory: Mapping[str, Mapping[str, Any]] | None=None) -> None:
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
    if len(rows) > 5000:
        rows = rows[-5000:]
    for idx, raw_rec in enumerate(rows):
        raw_skill = _norm_skill(str(raw_rec.get('skill') or ''))
        if known_skills and raw_skill and (raw_skill not in known_skills):
            continue
        rec = stamp_skill_telemetry_event(raw_rec, inventory=inventory)
        kind = str(rec.get('kind') or '').strip()
        if kind not in {'skill_turn', 'reference_hit', 'script_call'}:
            continue
        ts = _parse_ts(rec.get('ts') or rec.get('timestamp') or rec.get('observed_at'))
        if ts is None or not window_start <= ts < window_end:
            continue
        sid = str(rec.get('session_id') or f'telemetry-{idx}').strip()
        sk = _norm_skill(rec.get('skill') or '')
        if not sk:
            continue
        obs = observations.get(sid)
        is_new_obs = obs is None
        if obs is None:
            obs = SessionObservation(session_id=sid, source='usage_jsonl', started_at=ts, ended_at=ts)
            observations[sid] = obs
        obs.started_at = min(obs.started_at, ts)
        obs.ended_at = max(obs.ended_at, ts)
        if kind == 'skill_turn':
            if not known_skills or sk in known_skills:
                obs.skills_loaded.add(sk)
            for tc in rec.get('tool_calls') or []:
                tc_low = str(tc).lower()
                if tc_low in {'edit', 'write', 'multiedit', 'apply_patch', 'create'} and is_new_obs:
                    obs.edit_count += 1
                    e_seq = obs.next_seq()
                    obs.edit_seqs.append(e_seq)
                    obs.edit_events.append((ts, e_seq))
        elif kind == 'reference_hit':
            raw_ref = str(rec.get('reference_path') or '').strip()
            norm_pair = _normalize_ref_rel(raw_ref)
            rel_ref = norm_pair[1] if norm_pair else raw_ref if raw_ref.startswith('references/') else f'references/{Path(raw_ref).name}'
            if not any((r_sk == sk and r_rel == rel_ref for r_sk, r_rel, _, _ in obs.ref_reads)):
                obs.skills_loaded.add(sk)
                obs.ref_reads.append((sk, rel_ref, ts, obs.next_seq()))
        elif kind == 'script_call':
            raw_sc = str(rec.get('script_path') or '').strip()
            m_sc = _SKILL_SCRIPT_RE.search(raw_sc)
            sc_rel = f"scripts/{m_sc.group('script').lstrip('/')}" if m_sc else raw_sc if raw_sc.startswith('scripts/') else f'scripts/{Path(raw_sc).name}'
            raw_ec = rec.get('exit_code')
            ec = int(raw_ec) if raw_ec not in (None, '') else None
            raw_dur = rec.get('duration_ms')
            dur = max(0.0, float(raw_dur)) if raw_dur not in (None, '') else None
            explicit_sv = str(raw_rec.get('skill_version') or '').strip() or None
            explicit_sub = str(raw_rec.get('subtree_sha') or '').strip() or None
            obs.script_calls.append(ScriptCallEvent(session_id=sid, skill=sk, script_rel=sc_rel, command=str(rec.get('command') or raw_sc), ts=ts, exit_code=ec, duration_ms=dur, measured=dur is not None, seq=obs.next_seq(), skill_version=explicit_sv, subtree_sha=explicit_sub, origin_source='usage_jsonl'))

def _ingest_trace_export(observations: dict[str, SessionObservation], *, trace_export_path: Path | None, window_start: datetime, window_end: datetime, known_skills: set[str]) -> int:
    if trace_export_path is None or not _is_safe_local_file(trace_export_path):
        return 0
    ingested_spans = 0
    raw_text = _safe_read_text(trace_export_path).strip()
    if not raw_text:
        return 0
    records: list[dict[str, Any]] = []
    if raw_text.startswith('{') or raw_text.startswith('['):
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                if isinstance(parsed.get('traces'), list):
                    records.extend((t for t in parsed['traces'] if isinstance(t, dict)))
                elif isinstance(parsed.get('batch'), list):
                    records.extend((t for t in parsed['batch'] if isinstance(t, dict)))
                else:
                    records.append(parsed)
            elif isinstance(parsed, list):
                records.extend((t for t in parsed if isinstance(t, dict)))
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
        sid = str(tr.get('entity_id') or tr.get('session_id') or tr.get('trace_id') or f'trace-{idx}')
        spans = tr.get('spans') if isinstance(tr.get('spans'), list) else []
        for sp in spans:
            if not isinstance(sp, dict):
                continue
            cat = str(sp.get('category') or (sp.get('attributes') or {}).get('multica.span.category') or '')
            ts = _parse_ts(sp.get('start_ns') or sp.get('startTime') or tr.get('start_ns')) or window_start
            if not window_start <= ts < window_end:
                continue
            obs = observations.setdefault(sid, SessionObservation(session_id=sid, source='trace_export', started_at=ts, ended_at=ts))
            obs.started_at = min(obs.started_at, ts)
            obs.ended_at = max(obs.ended_at, ts)
            attrs = sp.get('attributes') if isinstance(sp.get('attributes'), dict) else {}
            out_p = sp.get('output_payload') if isinstance(sp.get('output_payload'), dict) else {}
            in_p = sp.get('input_payload') if isinstance(sp.get('input_payload'), dict) else {}
            ingested_spans += 1
            if cat == 'skill_load':
                sk = _norm_skill(attrs.get('skill.name') or sp.get('name', '').replace('skill:', ''))
                if sk and (not known_skills or sk in known_skills):
                    obs.skills_loaded.add(sk)
            elif cat == 'l1_reference_read':
                rp = str(attrs.get('reference.path') or in_p.get('path') or '')
                norm_pair = _normalize_ref_rel(rp)
                if norm_pair:
                    obs.skills_loaded.add(norm_pair[0])
                    if not any((r_sk == norm_pair[0] and r_rel == norm_pair[1] for r_sk, r_rel, _, _ in obs.ref_reads)):
                        obs.ref_reads.append((norm_pair[0], norm_pair[1], ts, obs.next_seq()))
            elif cat == 'l2_script_exec':
                sp_path = str(attrs.get('script.path') or in_p.get('script') or '')
                cmd_str = str(in_p.get('command') or sp_path)
                measured = bool(attrs.get('timing.measured', out_p.get('timing.measured', False)))
                raw_dur = attrs.get('script.duration_ms', out_p.get('duration_ms'))
                dur = float(raw_dur) if measured and raw_dur is not None else None
                raw_ec = attrs.get('script.exit_code', out_p.get('exit_code'))
                ec = int(raw_ec) if raw_ec is not None else None
                sv_attr = str(attrs.get('skill.version') or attrs.get('skill_version') or '').strip() or None
                sub_attr = str(attrs.get('skill.subtree_sha') or attrs.get('subtree_sha') or '').strip() or None
                m_sc = _SKILL_SCRIPT_RE.search(sp_path)
                if m_sc:
                    sk = _norm_skill(m_sc.group('skill'))
                    sc_rel = f"scripts/{m_sc.group('script').lstrip('/')}"
                else:
                    sk = _norm_skill(attrs.get('skill.name') or 'repo-gate')
                    sc_rel = sp_path if sp_path.startswith('scripts/') else f"scripts/{Path(sp_path or 'unknown.py').name}"
                obs.script_calls.append(ScriptCallEvent(session_id=sid, skill=sk, script_rel=sc_rel, command=cmd_str, ts=ts, exit_code=ec, duration_ms=dur, measured=measured, seq=obs.next_seq(), skill_version=sv_attr, subtree_sha=sub_attr, origin_source='trace_export'))
    return ingested_spans

def _ingest_warehouse_and_copilot_db(observations: dict[str, SessionObservation], *, warehouse_db: Path | None, copilot_db: Path | None, window_start: datetime, window_end: datetime, known_skills: set[str], basename_to_targets: Mapping[str, list[tuple[str, str]]]) -> dict[str, Any]:
    meta: dict[str, Any] = {'r1_to_r2_converged_tasks': [], 'directional_drift_tasks': [], 'skill_escapes': {}}

    def bump_escape(sk: str, kind: str) -> None:
        bucket = meta['skill_escapes'].setdefault(_norm_skill(sk), {'impl_bug': 0, 'spec_gap': 0})
        bucket[kind] = bucket.get(kind, 0) + 1
    if _safe_is_file(warehouse_db) and warehouse_db is not None:
        with contextlib.suppress(sqlite3.Error, OSError), _safe_sqlite_connect(warehouse_db) as conn:
            if conn is not None:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            else:
                tables = set()
            if 'fact_turn' in tables:
                cols = [r[1] for r in conn.execute('PRAGMA table_info("fact_turn")')]
                for values in conn.execute('SELECT * FROM "fact_turn"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get('ts') or row.get('timestamp') or row.get('cst_day'))
                    if ts is None or not window_start <= ts < window_end:
                        continue
                    sid = str(row.get('session_id') or row.get('turn_uuid') or '').strip()
                    if not sid:
                        continue
                    obs = observations.setdefault(sid, SessionObservation(session_id=sid, source='warehouse', started_at=ts, ended_at=ts))
                    obs.started_at = min(obs.started_at, ts)
                    obs.ended_at = max(obs.ended_at, ts)
                    raw_sk = row.get('skill_names')
                    sk_list: list[str] = []
                    if isinstance(raw_sk, str) and raw_sk.strip().startswith('['):
                        with contextlib.suppress(json.JSONDecodeError):
                            sk_list = [str(x) for x in json.loads(raw_sk) if str(x).strip()]
                    elif raw_sk:
                        sk_list = [str(raw_sk)]
                    if row.get('attribution_skill'):
                        sk_list.append(str(row['attribution_skill']))
                    for s in sk_list:
                        ns = _norm_skill(s)
                        if ns and (not known_skills or ns in known_skills):
                            obs.skills_loaded.add(ns)
                    raw_tools = row.get('tool_calls')
                    tools: list[Any] = []
                    if isinstance(raw_tools, str) and raw_tools.strip().startswith('['):
                        with contextlib.suppress(json.JSONDecodeError):
                            tools = json.loads(raw_tools)
                    for t in tools:
                        if isinstance(t, dict):
                            cmd_or_p = str(t.get('command') or t.get('path') or '')
                            if cmd_or_p and str(t.get('name') or '').lower() in {'edit', 'write', 'create', 'apply_patch'}:
                                obs.edited_files.add(cmd_or_p)
                                obs.edit_count += 1
                                e_seq = obs.next_seq()
                                obs.edit_seqs.append(e_seq)
                                obs.edit_events.append((ts, e_seq))
                            elif cmd_or_p:
                                b_seq = obs.next_seq()
                                obs.bash_commands.append((cmd_or_p, t.get('exit_code'), t.get('duration_ms'), b_seq))
                                obs.bash_events.append((cmd_or_p, t.get('exit_code'), t.get('duration_ms'), ts, b_seq))
                        elif str(t).lower() in {'edit', 'write', 'create', 'apply_patch'}:
                            obs.edit_count += 1
                            e_seq = obs.next_seq()
                            obs.edit_seqs.append(e_seq)
                            obs.edit_events.append((ts, e_seq))
            for ref_tbl in ('reference_hits', 'fact_skill_reference_hit'):
                if ref_tbl not in tables:
                    continue
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{ref_tbl}")')]
                for values in conn.execute(f'SELECT * FROM "{ref_tbl}"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get('ts') or row.get('timestamp') or row.get('observed_at') or row.get('cst_day'))
                    if ts is None or not window_start <= ts < window_end:
                        continue
                    sid = str(row.get('session_id') or row.get('turn_uuid') or 'wh-ref')
                    sk = _norm_skill(row.get('skill') or '')
                    rp = str(row.get('reference_path') or row.get('ref_path') or '').strip()
                    if not sk or not rp:
                        continue
                    norm_pair = _normalize_ref_rel(rp)
                    rel_ref = norm_pair[1] if norm_pair else rp if rp.startswith('references/') else f'references/{Path(rp).name}'
                    obs = observations.setdefault(sid, SessionObservation(session_id=sid, source='warehouse', started_at=ts, ended_at=ts))
                    obs.skills_loaded.add(sk)
                    if not any((r_sk == sk and r_rel == rel_ref for r_sk, r_rel, _, _ in obs.ref_reads)):
                        obs.ref_reads.append((sk, rel_ref, ts, obs.next_seq()))
            for sc_tbl in ('script_call', 'fact_skill_script_call'):
                if sc_tbl not in tables:
                    continue
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{sc_tbl}")')]
                for values in conn.execute(f'SELECT * FROM "{sc_tbl}"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get('ts') or row.get('timestamp') or row.get('observed_at') or row.get('cst_day'))
                    if ts is None or not window_start <= ts < window_end:
                        continue
                    sid = str(row.get('session_id') or row.get('turn_uuid') or 'wh-script')
                    sk = _norm_skill(row.get('skill') or '')
                    sp = str(row.get('script_path') or row.get('path') or '').strip()
                    if not sk or not sp:
                        continue
                    m_sc = _SKILL_SCRIPT_RE.search(sp)
                    sc_rel = f"scripts/{m_sc.group('script').lstrip('/')}" if m_sc else sp if sp.startswith('scripts/') else f'scripts/{Path(sp).name}'
                    raw_ec = row.get('exit_code')
                    ec = int(raw_ec) if raw_ec not in (None, '') else None
                    raw_dur = row.get('duration_ms')
                    dur = max(0.0, float(raw_dur)) if raw_dur not in (None, '') else None
                    cmd_str = str(row.get('command') or sp)
                    obs = observations.setdefault(sid, SessionObservation(session_id=sid, source='warehouse', started_at=ts, ended_at=ts))
                    seq_no = obs.next_seq()
                    obs.script_calls.append(ScriptCallEvent(session_id=sid, skill=sk, script_rel=sc_rel, command=cmd_str, ts=ts, exit_code=ec, duration_ms=dur, measured=dur is not None, seq=seq_no, skill_version=str(row.get('skill_version') or '').strip() or None, subtree_sha=str(row.get('subtree_sha') or '').strip() or None, origin_source='warehouse'))
            if 'fact_gtt_task_run' in tables:
                cols = [r[1] for r in conn.execute('PRAGMA table_info("fact_gtt_task_run")')]
                grouped_tasks: dict[tuple[str, str], list[dict[str, Any]]] = {}
                for values in conn.execute('SELECT * FROM "fact_gtt_task_run"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get('ended_at') or row.get('started_at') or row.get('cst_day'))
                    if ts is None or not window_start <= ts < window_end:
                        continue
                    g_id = str(row.get('goal_id') or '')
                    t_id = str(row.get('task_id') or '')
                    if g_id and t_id:
                        grouped_tasks.setdefault((g_id, t_id), []).append(row)
                converged_goal_task_skills: set[tuple[str, str, str]] = set()
                converged_goal_skills: set[tuple[str, str]] = set()
                for (g_id, t_id), runs in grouped_tasks.items():
                    max_r = max((int(r.get('round_no') or 1) for r in runs))
                    passed_later = any((str(r.get('v5_verdict') or '').upper() in {'PASS', 'CONCERNS', 'WAIVED'} and int(r.get('round_no') or 1) >= 2 for r in runs))
                    still_failed_r3 = max_r >= 3
                    sk_set: set[str] = set()
                    for r in runs:
                        raw_su = r.get('skills_used')
                        if isinstance(raw_su, str) and raw_su.strip().startswith('['):
                            with contextlib.suppress(json.JSONDecodeError):
                                sk_set.update((_norm_skill(x) for x in json.loads(raw_su) if str(x).strip()))
                        elif raw_su:
                            for m_sk in _SKILL_CONTEXT_RE.finditer(str(raw_su)):
                                tok = m_sk.group('ctx') or m_sk.group('path') or m_sk.group('call')
                                if tok:
                                    sk_set.add(_norm_skill(tok))
                    if max_r == 2 and passed_later:
                        for sk in sk_set:
                            converged_goal_task_skills.add((g_id, t_id, sk))
                            if g_id:
                                converged_goal_skills.add((g_id, sk))
                        meta['r1_to_r2_converged_tasks'].append({'goal_id': g_id, 'task_id': t_id, 'skills': sorted(sk_set)})
                    elif still_failed_r3:
                        meta['directional_drift_tasks'].append({'goal_id': g_id, 'task_id': t_id, 'max_round': max_r, 'skills': sorted(sk_set)})
                        for sk in sk_set:
                            bump_escape(sk, 'impl_bug')
            else:
                converged_goal_task_skills = set()
                converged_goal_skills = set()
            if 'fact_gtt_event' in tables:
                cols = [r[1] for r in conn.execute('PRAGMA table_info("fact_gtt_event")')]
                for values in conn.execute('SELECT * FROM "fact_gtt_event"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get('ts') or row.get('observed_at') or row.get('cst_day'))
                    if ts is None or not window_start <= ts < window_end:
                        continue
                    evt = str(row.get('evt_type') or '').upper()
                    note = str(row.get('note_preview') or row.get('note') or '')
                    g_id = str(row.get('goal_id') or '')
                    t_id = str(row.get('task_id') or '')
                    sk_hits = {_norm_skill(m.group('ctx') or m.group('path') or m.group('call') or '') for m in _SKILL_CONTEXT_RE.finditer(note)}
                    sk_hits.discard('')
                    if not sk_hits and 'design' in g_id.lower():
                        sk_hits.add('my-designer')
                    if evt in {'CHANGE_REQUEST', 'NEED_OWNER_DECISION'}:
                        meta['directional_drift_tasks'].append({'goal_id': g_id, 'task_id': t_id, 'event': evt, 'skills': sorted(sk_hits)})
                        for sk in sk_hits:
                            bump_escape(sk, 'spec_gap')
                    elif evt == 'REVIEW_FAIL':
                        kind = 'spec_gap' if 'SPEC_GAP' in note.upper() else 'impl_bug'
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
            if 'session_files' in tables:
                cols = [r[1] for r in conn.execute('PRAGMA table_info("session_files")')]
                for values in conn.execute('SELECT * FROM "session_files"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get('first_seen_at') or row.get('timestamp'))
                    if ts is None or not window_start <= ts < window_end:
                        continue
                    sid = str(row.get('session_id') or '').strip()
                    fp = str(row.get('file_path') or '').strip()
                    if not sid or not fp:
                        continue
                    obs = observations.setdefault(sid, SessionObservation(session_id=sid, source='copilot_db', started_at=ts, ended_at=ts))
                    if str(row.get('tool_name') or '').lower() in {'edit', 'create', 'apply_patch'}:
                        obs.edited_files.add(fp)
                        obs.edit_count += 1
                        e_seq = obs.next_seq()
                        obs.edit_seqs.append(e_seq)
                        obs.edit_events.append((ts, e_seq))
            if 'turns' in tables:
                cols = [r[1] for r in conn.execute('PRAGMA table_info("turns")')]
                for values in conn.execute('SELECT * FROM "turns"'):
                    row = dict(zip(cols, values))
                    ts = _parse_ts(row.get('timestamp'))
                    if ts is None or not window_start <= ts < window_end:
                        continue
                    sid = str(row.get('session_id') or '').strip()
                    if not sid:
                        continue
                    blob = f"{row.get('user_message') or ''}\n{row.get('assistant_response') or ''}"
                    obs = observations.get(sid)
                    matched_sk = {_norm_skill(m.group('ctx') or m.group('path') or m.group('call') or '') for m in _SKILL_CONTEXT_RE.finditer(blob)}
                    matched_sk.discard('')
                    if known_skills:
                        matched_sk &= known_skills
                    if matched_sk or obs is not None:
                        obs = observations.setdefault(sid, SessionObservation(session_id=sid, source='copilot_db', started_at=ts, ended_at=ts))
                        obs.skills_loaded.update(matched_sk)
                        for m_ref in _REF_PATH_RE.finditer(blob):
                            r_sk = _norm_skill(m_ref.group('skill'))
                            r_rel = f"references/{m_ref.group('ref').lstrip('/')}"
                            if not any((x_sk == r_sk and x_rel == r_rel for x_sk, x_rel, _, _ in obs.ref_reads)):
                                obs.ref_reads.append((r_sk, r_rel, ts, obs.next_seq()))
    return meta
