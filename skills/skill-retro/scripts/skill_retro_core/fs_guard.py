from __future__ import annotations
from .common import *

"""Fs Guard implementation for the skill-retro engine."""

def _is_cloud_or_external_path(path: Path | None) -> bool:
    if path is None:
        return False
    raw = str(path)
    return 'Mobile Documents' in raw or 'com~apple~CloudDocs' in raw or raw.startswith('/Volumes/') or ('/Volumes/' in raw)

def _is_trusted_fast_local_path(path: Path | None) -> bool:
    if path is None:
        return False
    raw = str(path)
    if _is_cloud_or_external_path(path):
        return False
    home_str = str(Path.home())
    trusted_prefixes = (f'{home_str}/.copilot', f'{home_str}/.local', f'{home_str}/Development/user-skills', str(DEFAULT_SKILLS_ROOT.parent))
    return any((raw == pref or raw.startswith(pref + '/') for pref in trusted_prefixes))

def _is_dataless_stat(st: os.stat_result) -> bool:
    flags = int(getattr(st, 'st_flags', 0) or 0)
    if flags & (UF_DATALESS | SF_DATALESS):
        return True
    blocks = getattr(st, 'st_blocks', None)
    size = int(getattr(st, 'st_size', 0) or 0)
    if blocks is not None and int(blocks) == 0 and (size > 0):
        return True
    return False

def _run_with_io_timeout(fn: Callable[[], Any], *, timeout_sec: float=SAFE_OPEN_TIMEOUT_SEC, default: Any=None) -> tuple[bool, Any]:
    """Run a potentially blocking filesystem/SQLite call with a bounded timeout (<0.5s)."""
    timeout_val = max(0.05, float(timeout_sec))
    if threading.current_thread() is threading.main_thread() and hasattr(signal, 'setitimer') and hasattr(signal, 'ITIMER_REAL'):
        prev_handler = signal.getsignal(signal.SIGALRM)

        def _alarm_handler(_signum: int, _frame: Any) -> None:
            raise TimeoutError('bounded I/O open timed out')
        try:
            signal.signal(signal.SIGALRM, _alarm_handler)
            signal.setitimer(signal.ITIMER_REAL, timeout_val)
            try:
                return (True, fn())
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
        except (TimeoutError, OSError, sqlite3.Error):
            return (False, default)
        finally:
            with contextlib.suppress(OSError, ValueError):
                signal.signal(signal.SIGALRM, prev_handler)
    box: dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            box['val'] = fn()
            box['ok'] = True
        except BaseException as exc:
            box['err'] = exc
            box['ok'] = False
        finally:
            done.set()
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    if not done.wait(timeout_val):
        return (False, default)
    if not box.get('ok'):
        return (False, default)
    return (True, box.get('val', default))

def _is_safe_local_file(path: Path | None, *, timeout_sec: float=SAFE_OPEN_TIMEOUT_SEC) -> bool:
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
    flags = int(getattr(st, 'st_flags', 0) or 0)
    if flags & (UF_DATALESS | SF_DATALESS):
        return False
    is_cloud_ext = _is_cloud_or_external_path(path)
    if is_cloud_ext and _is_dataless_stat(st):
        return False
    if _is_trusted_fast_local_path(path):
        return True

    def _probe_nonblock_open() -> bool:
        nonblock_flag = getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(str(path), os.O_RDONLY | nonblock_flag)
        try:
            os.read(fd, 1)
            return True
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)
    ok, res = _run_with_io_timeout(_probe_nonblock_open, timeout_sec=timeout_sec, default=False)
    return bool(ok and res)

def _safe_read_text(path: Path | None, *, max_bytes: int | None=None, timeout_sec: float=SAFE_OPEN_TIMEOUT_SEC) -> str:
    """Safely read UTF-8 text from `path` after verifying `_is_safe_local_file` and 0.5s open guard."""
    if path is None or not _is_safe_local_file(path, timeout_sec=timeout_sec):
        return ''
    if _is_trusted_fast_local_path(path):
        try:
            nonblock_flag = getattr(os, 'O_NONBLOCK', 0)
            fd = os.open(str(path), os.O_RDONLY | nonblock_flag)
            try:
                with os.fdopen(fd, 'rb') as handle:
                    raw = handle.read(max_bytes) if max_bytes is not None else handle.read()
                return raw.decode('utf-8', errors='replace')
            except BaseException:
                with contextlib.suppress(OSError):
                    os.close(fd)
                return ''
        except OSError:
            return ''

    def _read_impl() -> str:
        nonblock_flag = getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(str(path), os.O_RDONLY | nonblock_flag)
        try:
            with os.fdopen(fd, 'rb') as handle:
                raw = handle.read(max_bytes) if max_bytes is not None else handle.read()
            return raw.decode('utf-8', errors='replace')
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
    ok, text = _run_with_io_timeout(_read_impl, timeout_sec=timeout_sec, default='')
    return text if ok and isinstance(text, str) else ''

@contextlib.contextmanager
def _safe_sqlite_connect(db_path: Path | None, *, timeout_sec: float=SAFE_OPEN_TIMEOUT_SEC):
    """Connect to a read-only SQLite database only if `_is_safe_local_file` passes within `0.5s`."""
    if db_path is None or not _is_safe_local_file(db_path, timeout_sec=timeout_sec):
        yield None
        return

    def _connect() -> sqlite3.Connection:
        return sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=timeout_sec)
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
        if getattr(st, 'st_flags', 0) & (UF_DATALESS | SF_DATALESS):
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

def _safe_iterdir(path: Path | None, *, sort_entries: bool=True) -> list[Path]:
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

def _safe_glob(path: Path | None, pattern: str, *, recursive: bool=False) -> list[Path]:
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
