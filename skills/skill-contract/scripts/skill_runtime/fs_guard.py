"""Non-blocking local filesystem guards and a stat-watermark cache."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any

_SF_DATALESS = getattr(os, "SF_DATALESS", 0x40000000)
_CACHE: dict[tuple[str, str], tuple[float, tuple[int, int, int, str], Any]] = {}
_LOCK = threading.RLock()


def _path(value: str | os.PathLike[str]) -> Path:
    return Path(os.fspath(value)).expanduser()


def _unsafe(path: Path) -> bool:
    try:
        absolute = Path(os.path.abspath(os.fspath(path)))
        parts = {part.casefold() for part in absolute.parts}
        if absolute == Path("/Volumes") or Path("/Volumes") in absolute.parents:
            return True
        if "mobile documents" in parts or "com~apple~clouddocs" in parts:
            return True
        stat_result = os.lstat(absolute)
        return bool(getattr(stat_result, "st_flags", 0) & _SF_DATALESS)
    except (OSError, ValueError):
        return False


def is_safe_local_file(path: str | os.PathLike[str]) -> bool:
    """Return true only for an existing, regular, non-dataless local file."""
    candidate = _path(path)
    try:
        return candidate.is_file() and not candidate.is_symlink() and not _unsafe(candidate)
    except OSError:
        return False


def is_safe_local_dir(path: str | os.PathLike[str]) -> bool:
    """Return true only for an existing, regular, non-dataless local directory."""
    candidate = _path(path)
    try:
        return candidate.is_dir() and not candidate.is_symlink() and not _unsafe(candidate)
    except OSError:
        return False


def safe_read_text(path: str | os.PathLike[str], *, encoding: str = "utf-8", default: str | None = None, max_bytes: int = 4 * 1024 * 1024) -> str | None:
    if not is_safe_local_file(path):
        return default
    try:
        with open(_path(path), "rb") as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return default
        return raw.decode(encoding)
    except (OSError, UnicodeError):
        return default


def atomic_write_json(path: str | os.PathLike[str], payload: Any, *, encoding: str = "utf-8") -> Path:
    """Write JSON through a same-directory temporary file and atomic replace."""
    target = _path(path)
    parent = target.parent
    if not is_safe_local_dir(parent):
        raise OSError(f"unsafe local directory: {parent}")
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


def _watermark(path: Path) -> tuple[int, int, int, str]:
    stat_result = path.stat()
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return (stat_result.st_mtime_ns, stat_result.st_size, getattr(stat_result, "st_ino", 0), digest.hexdigest())


class StatWatermarkCache:
    """Object form of the module cache for callers that prefer an injected seam."""

    get = staticmethod(lambda path, key="default", ttl=60.0: get_cached(path, key, ttl=ttl))
    put = staticmethod(lambda path, value, key="default", ttl=60.0: put_cached(path, value, key, ttl=ttl))


stat_watermark_cache = StatWatermarkCache()


def get_cached(path: str | os.PathLike[str], key: str = "default", *, ttl: float = 60.0) -> Any:
    candidate = _path(path)
    try:
        watermark = _watermark(candidate)
    except OSError:
        return None
    with _LOCK:
        item = _CACHE.get((str(candidate), key))
        if item and time.monotonic() - item[0] <= ttl and item[1] == watermark:
            return item[2]
    return None


def put_cached(path: str | os.PathLike[str], value: Any, key: str = "default", *, ttl: float = 60.0) -> Any:
    del ttl
    candidate = _path(path)
    try:
        watermark = _watermark(candidate)
    except OSError:
        return value
    with _LOCK:
        _CACHE[(str(candidate), key)] = (time.monotonic(), watermark, value)
    return value
