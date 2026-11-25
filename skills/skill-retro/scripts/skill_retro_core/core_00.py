from __future__ import annotations
from .common import *

# AST-partitioned skill-retro implementation module.

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
    max_sessions: int | None = DEFAULT_MAX_COPILOT_SESSIONS_SCAN,
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
    for mtime, file_size, ev_file in (candidate_files if max_sessions is None else candidate_files[: max(0, int(max_sessions))]):
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
