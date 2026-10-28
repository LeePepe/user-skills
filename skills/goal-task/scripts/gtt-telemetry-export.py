#!/usr/bin/env python3
"""Collect redacted telemetry and optionally transport the local outbox."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

HOME_PATH_RE = re.compile(r"(?:/" + "Users|/home)/[^/\\s]+")
SECRET_RE = re.compile(
    r"(?i)(?:ghp_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+|AKIA[0-9A-Z]{16}|"
    r"(?<![A-Za-z0-9])((?:api[_-]?key|token|password|secret)\s*[:=]\s*)['\"]?[^'\"\s,;]+)"
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
RECORD_FILES = {"ledger.md": "ledger", "goal.md": "goal", "queue.json": "queue"}
UTC = timezone.utc


def fail(message: str) -> "NoReturn":
    raise ValueError(message)


def normalized_machine_id(value: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower()).strip("-")
    return value or "unknown-machine"


def normalized_text(text: str) -> str:
    text = HOME_PATH_RE.sub("~", text)
    text = SECRET_RE.sub(lambda m: m.group(1) + "[REDACTED]" if m.group(1) else "[REDACTED]", text)
    text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    for line in text.splitlines(keepends=True):
        if "://" in line:
            parts = re.split(r"(\S+://\S+)", line)
            for i, part in enumerate(parts):
                if "://" not in part:
                    continue
                try:
                    parsed = urlsplit(part)
                    if parsed.query:
                        query = [(key, "[REDACTED]") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)]
                        parts[i] = urlunsplit(parsed._replace(query=urlencode(query))).replace("%5BREDACTED%5D", "[REDACTED]")
                except ValueError:
                    pass
            text = text.replace(line, "".join(parts))
    return text


def safe_root(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if ".." in path.parts:
        fail(f"refusing path traversal: {raw}")
    if path.exists() and path.is_symlink():
        fail(f"refusing symlinked directory: {path}")
    return path.resolve()


def ensure_safe_file(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError:
        fail(f"path escapes root: {path}")
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            fail(f"refusing symlink: {current}")


def atomic_write(path: Path, content: str, mode: int | None = None) -> None:
    if path.is_symlink():
        fail(f"refusing symlinked output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _iso(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(timestamp or datetime.now().timestamp(), UTC).astimezone().isoformat(timespec="seconds")


def _device(state_dir: Path, alias: str) -> dict[str, str]:
    path = state_dir / "device.json"
    if path.is_symlink():
        fail(f"refusing symlinked state file: {path}")
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("device_id"), str):
            fail("device.json is invalid")
        return value
    value = {"device_id": str(uuid.uuid4()), "machine_alias": normalized_machine_id(alias), "created_at": _iso()}
    atomic_write(path, json.dumps(value, indent=2) + "\n", 0o600)
    path.chmod(0o600)
    return value


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _sync_config(state_dir: Path | None, state_path: Path | None) -> dict:
    candidates = []
    if state_dir:
        candidates.append(state_dir / "config.json")
    if state_path:
        candidates.append(state_path.parent / "config.json")
    for path in candidates:
        if path.is_file():
            value = _read_json(path)
            if not isinstance(value, dict):
                fail(f"sync config must be an object: {path}")
            return value
    return {}


def _goal_files(root: Path):
    if not root.is_dir():
        return
    for goal in sorted(root.iterdir()):
        if goal.name.startswith("_"):
            continue
        ensure_safe_file(goal, root)
        if not goal.is_dir():
            continue
        for filename, record_type in RECORD_FILES.items():
            path = goal / filename
            if path.exists() or path.is_symlink():
                ensure_safe_file(path, root)
                if path.is_file():
                    yield path, record_type, goal.name
        receipts = goal / "receipts"
        if receipts.exists() or receipts.is_symlink():
            ensure_safe_file(receipts, root)
        for path in sorted(receipts.glob("*.md")) if receipts.is_dir() else []:
            ensure_safe_file(path, root)
            yield path, "receipt", goal.name


def _extra_files(root: Path, collector: str):
    for goal in sorted(root.iterdir()) if root.is_dir() else []:
        ensure_safe_file(goal, root)
        if goal.is_dir() and not goal.name.startswith("_"):
            for filename, record_type in (("pr-dag.json", "pr_dag"), ("pr-watch-state.json", "pr_watch_state")):
                path = goal / filename
                if path.exists() or path.is_symlink():
                    ensure_safe_file(path, root)
                if path.is_file():
                    yield path, record_type, goal.name


def _bot_events(root: Path):
    for goal in sorted(root.iterdir()) if root.is_dir() else []:
        ensure_safe_file(goal, root)
        ledger = goal / "ledger.md"
        if ledger.exists() or ledger.is_symlink():
            ensure_safe_file(ledger, root)
        if not goal.is_dir() or goal.name.startswith("_") or not ledger.is_file():
            continue
        for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
            if ("TL_PROFILE" in line or "quota_precheck" in line or re.search(r"\bid=[BJ]\d+\b", line)
                    or "EVT=HOT_APPEND" in line or "EVT=UNBLOCK" in line or "NEED_GO" in line):
                action = "BOT_TL_EVENT"
                if "Allow path" in line:
                    action = "UNBLOCK_PATH"
                elif "CMD_CONFIRM" in line:
                    action = "UNBLOCK_CMD_CONFIRM"
                elif "艺术家" in line:
                    action = "DESIGN_ROUTE_ARTIST"
                yield ledger, goal.name, line, action


def _prmon_files(root: Path):
    base = root / "_watch" / "prmon"
    if base.exists() or base.is_symlink():
        ensure_safe_file(base, root)
    history = base / "heartbeat-history.jsonl"
    if history.exists() or history.is_symlink():
        ensure_safe_file(history, root)
    if history.is_file():
        yield history, "prmon_heartbeat", "_prmon", "jsonl"
    for name, kind in (("heartbeat.json", "prmon_heartbeat"), ("scheduler-state.json", "prmon_state")):
        path = base / name
        if path.exists() or path.is_symlink():
            ensure_safe_file(path, root)
        if path.is_file():
            yield path, kind, "_prmon", "snapshot"
    repair = base / "repair-queue"
    if repair.exists() or repair.is_symlink():
        ensure_safe_file(repair, root)
    if repair.is_dir():
        for path in sorted(repair.glob("*.json")):
            ensure_safe_file(path, root)
            yield path, "prmon_repair", "_prmon", "snapshot"


def _descriptor_files(directory: Path):
    if not directory.is_dir():
        return
    descriptor_root = directory.resolve()
    for descriptor in sorted(directory.glob("*.json")):
        ensure_safe_file(descriptor, descriptor_root)
        value = _read_json(descriptor)
        if not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in ("collector", "root", "glob", "record_type")):
            fail(f"invalid collector descriptor: {descriptor}")
        mode = value.get("mode", "snapshot")
        if mode not in ("snapshot", "jsonl_offset"):
            fail(f"invalid collector mode: {descriptor}")
        root = safe_root(value["root"])
        for path in sorted(root.glob(value["glob"])):
            ensure_safe_file(path, root)
            if path.is_file():
                yield path, value["collector"], value["record_type"], mode


def _cursor_key(path: Path, collector: str) -> str:
    return f"{collector}:{path}"


def _envelope(path: Path, root: Path, collector: str, record_type: str, device: dict[str, str], scope: str, content: str, payload: object = None) -> dict:
    redacted = normalized_text(content)
    value = {
        "schema_version": 1, "collector": collector, "record_type": record_type,
        "device_id": device["device_id"], "machine_alias": device["machine_alias"],
        "machine_id": device["machine_alias"], "goal_id": scope, "scope_id": scope,
        "rel_path": (str(path.relative_to(root)).replace(os.sep, "/")
                     if path.is_relative_to(root) else normalized_text(str(path))),
        "content_sha256": hashlib.sha256(redacted.encode()).hexdigest(),
        "mtime_iso": _iso(path.stat().st_mtime), "exported_at": _iso(), "observed_at": _iso(),
        "content": redacted,
    }
    if payload is not None:
        value["payload"] = _normalized_payload(payload)
    return value


def _normalized_payload(value: object) -> object:
    if isinstance(value, str):
        return normalized_text(value)
    if isinstance(value, list):
        return [_normalized_payload(item) for item in value]
    if isinstance(value, dict):
        return {normalized_text(str(key)): _normalized_payload(item) for key, item in value.items()}
    return value


def _azure_entity_args(entity: dict[str, object]) -> list[str]:
    return [
        f"{key}={','.join(sorted(value)) if isinstance(value, list) else value}"
        for key, value in entity.items()
    ]


def _collect(roots: list[Path], device: dict[str, str], state: dict, custom_dir: Path,
             quota_precheck_path: Path | None = None) -> tuple[list[dict], dict]:
    records, cursors = [], {}
    def unchanged(key: str, digest: str) -> bool:
        previous = state.get(key, {})
        return (previous.get("sha256") if isinstance(previous, dict) else previous) == digest
    for root in roots:
        for path, kind, scope in _goal_files(root):
            raw = path.read_text(encoding="utf-8", errors="replace")
            key = _cursor_key(path, "gtt")
            digest = hashlib.sha256(raw.encode()).hexdigest()
            cursors[key] = {"inode": path.stat().st_ino, "mtime_ns": path.stat().st_mtime_ns, "size": path.stat().st_size, "sha256": digest}
            if not unchanged(key, digest):
                records.append(_envelope(path, root, "gtt", kind, device, scope, raw, _read_json(path) if path.suffix == ".json" else None))
        for path, kind, scope in _extra_files(root, "bot_tl"):
            raw = path.read_text(encoding="utf-8", errors="replace")
            key = _cursor_key(path, "bot_tl")
            digest = hashlib.sha256(raw.encode()).hexdigest()
            cursors[key] = {"inode": path.stat().st_ino, "mtime_ns": path.stat().st_mtime_ns, "size": path.stat().st_size, "sha256": digest}
            if not unchanged(key, digest):
                records.append(_envelope(path, root, "bot_tl", kind, device, scope, raw, _read_json(path)))
        for path, scope, line, action in _bot_events(root):
            key = f"{_cursor_key(path, 'bot_tl:event')}:{hashlib.sha256(line.encode()).hexdigest()}"
            cursors[key] = {"sha256": hashlib.sha256(line.encode()).hexdigest()}
            if key not in state:
                records.append(_envelope(path, root, "bot_tl", "bot_tl_event", device, scope, line,
                                         {"line": line, "action_kind": action}))
        for path, kind, scope, mode in _prmon_files(root):
            key = _cursor_key(path, "prmon")
            stat = path.stat()
            if mode == "jsonl":
                previous = state.get(key, {})
                offset = 0 if previous.get("inode") != stat.st_ino or stat.st_size < previous.get("offset", 0) else previous.get("offset", 0)
                with path.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                for line in chunk.splitlines():
                    text = line.decode("utf-8", "replace")
                    records.append(_envelope(path, root, "prmon", kind, device, scope, text, {"line": text}))
                cursors[key] = {"inode": stat.st_ino, "offset": stat.st_size, "size": stat.st_size}
            else:
                raw = path.read_text(encoding="utf-8", errors="replace")
                digest = hashlib.sha256(raw.encode()).hexdigest()
                cursors[key] = {"inode": stat.st_ino, "mtime_ns": stat.st_mtime_ns, "size": stat.st_size, "sha256": digest}
                if not unchanged(key, digest):
                    records.append(_envelope(path, root, "prmon", kind, device, scope, raw, _read_json(path)))
    quota_path = quota_precheck_path or Path("~/.config/claude/gtt-weekly-remaining-pct.json").expanduser()
    if quota_path.exists() or quota_path.is_symlink():
        quota_root = quota_path.parent.resolve()
        ensure_safe_file(quota_path, quota_root)
        raw = quota_path.read_text(encoding="utf-8", errors="replace")
        key = _cursor_key(quota_path, "bot_tl")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        cursors[key] = {"inode": quota_path.stat().st_ino, "mtime_ns": quota_path.stat().st_mtime_ns,
                        "size": quota_path.stat().st_size, "sha256": digest}
        if not unchanged(key, digest):
            records.append(_envelope(quota_path, quota_root, "bot_tl", "quota_precheck", device, "_global",
                                     raw, _read_json(quota_path)))
    for path, collector, kind, mode in _descriptor_files(custom_dir):
        key = _cursor_key(path, collector)
        stat = path.stat()
        if mode == "jsonl_offset":
            previous = state.get(key, {})
            offset = 0 if previous.get("inode") != stat.st_ino or stat.st_size < previous.get("offset", 0) else previous.get("offset", 0)
            with path.open("rb") as handle:
                handle.seek(offset)
                for line in handle.read().splitlines():
                    text = line.decode("utf-8", "replace")
                    records.append(_envelope(path, path.parent, collector, kind, device, "_global", text, {"line": text}))
            cursors[key] = {"inode": stat.st_ino, "offset": stat.st_size, "size": stat.st_size}
        else:
            raw = path.read_text(encoding="utf-8", errors="replace")
            digest = hashlib.sha256(raw.encode()).hexdigest()
            cursors[key] = {"inode": stat.st_ino, "mtime_ns": stat.st_mtime_ns, "size": stat.st_size, "sha256": digest}
            if not unchanged(key, digest):
                records.append(_envelope(path, path.parent, collector, kind, device, "_global", raw, _read_json(path) if path.suffix == ".json" else None))
    return records, cursors


def _transport(state_dir: Path, account: str, container: str, table: str, prefix: str, rsync_to: str | None) -> bool:
    outbox = state_dir / "outbox.jsonl"
    state_path = state_dir / "outbox.state.json"
    if outbox.is_symlink() or state_path.is_symlink():
        fail(f"refusing symlinked transport state: {outbox if outbox.is_symlink() else state_path}")
    if not outbox.exists():
        return True
    state = json.loads(state_path.read_text()) if state_path.exists() else {"offset": 0}
    offset = int(state.get("offset", 0))
    data = outbox.read_bytes()[offset:]
    if not data:
        return True
    digest = hashlib.sha256(data).hexdigest()
    now = datetime.now()
    ts = now.strftime("%Y%m%dT%H%M%S")
    batch_id = f"{ts}_{digest[:12]}"
    name = f"{batch_id}.jsonl"
    stamp = now.strftime("%Y-%m-%d")
    device = _device(state_dir, socket.gethostname())
    device_id = device["device_id"]
    batch = f"{prefix.strip('/')}/machines/{device_id}/batches/{stamp}/{name}"
    commands = []
    blob = None
    records = [json.loads(line) for line in data.decode("utf-8").splitlines()]
    collectors = sorted({str(record.get("collector", "")) for record in records if record.get("collector")})
    metadata = {
        "machine_alias": device["machine_alias"], "MachineAlias": device["machine_alias"],
        "updated_at": _iso(), "UpdatedAt": _iso(),
    }
    if account:
        blob = state_dir / name
        commands.append(["az", "storage", "blob", "upload", "--account-name", account, "--container-name", container, "--name", batch, "--file", str(blob), "--auth-mode", "login", "--overwrite"])
        commands.append(["az", "storage", "table", "create", "--account-name", account, "--name", table, "--auth-mode", "login"])
        commands.append(["az", "storage", "entity", "insert", "--if-exists", "replace", "--account-name", account, "--table-name", table,
                         "--entity", *_azure_entity_args({"PartitionKey": "DEVICE", "RowKey": device_id, **metadata}),
                         "--auth-mode", "login"])
        batch_metadata = {**metadata, "collectors": collectors, "Collectors": collectors,
                          "record_count": len(records), "RecordCount": len(records),
                          "blob_path": batch, "BlobPath": batch,
                          "content_sha256": digest, "ContentSha256": digest}
        commands.append(["az", "storage", "entity", "insert", "--if-exists", "replace", "--account-name", account, "--table-name", table,
                         "--entity", *_azure_entity_args({"PartitionKey": f"BATCH_{device_id}", "RowKey": batch_id, **batch_metadata}),
                         "--auth-mode", "login"])
    if rsync_to:
        commands.append(["rsync", "-az", str(outbox), rsync_to])
    try:
        if blob is not None:
            blob.write_bytes(data)
        for command in commands:
            subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    finally:
        if blob is not None:
            blob.unlink(missing_ok=True)
    state.update(offset=offset + len(data), last_batch_id=batch_id, updated_at=_iso())
    atomic_write(state_path, json.dumps(state, indent=2) + "\n", 0o600)
    return True


def export(roots: list[Path], machine_id: str, output: Path, state_path: Path | None, incremental: bool,
           sync_state_dir: Path | None = None, collectors_dir: Path | None = None,
           azure_account: str = "", azure_container: str = "telemetry-raw", azure_table: str = "AidataSyncIndex",
           azure_prefix: str = "aidata-sync/v1", rsync_to: str | None = None,
           quota_precheck_path: Path | None = None, quota_path: Path | None = None) -> int:
    machine_id = normalized_machine_id(machine_id)
    state_dir = sync_state_dir or (state_path.parent if state_path else None)
    state_dir = state_dir.expanduser() if state_dir else None
    for path in (state_dir, state_path, output):
        if path is not None and path.is_symlink():
            fail(f"refusing symlinked path: {path}")
    config = _sync_config(state_dir, state_path)
    if not azure_account:
        azure_account = str(config.get("azure_account", ""))
    if azure_container == "telemetry-raw":
        azure_container = str(config.get("azure_container", azure_container))
    if azure_table == "AidataSyncIndex":
        azure_table = str(config.get("azure_table", azure_table))
    if azure_prefix == "aidata-sync/v1":
        azure_prefix = str(config.get("azure_prefix", azure_prefix))
    device = _device(state_dir, machine_id) if state_dir else {"device_id": machine_id, "machine_alias": machine_id}
    cursor_path = state_dir / "cursors.json" if state_dir else state_path
    if cursor_path and cursor_path.is_symlink():
        fail(f"refusing symlinked cursor state: {cursor_path}")
    old = json.loads(cursor_path.read_text()) if incremental and cursor_path and cursor_path.exists() else {}
    records, cursors = _collect(
        roots, device, old,
        collectors_dir or (state_dir / "collectors.d" if state_dir else Path("~/.config/aidata-sync/collectors.d").expanduser()),
        quota_precheck_path or quota_path,
    )
    lines = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records)
    if state_dir:
        outbox = state_dir / "outbox.jsonl"
        if outbox.is_symlink():
            fail(f"refusing symlinked outbox: {outbox}")
        state_file = state_dir / "outbox.state.json"
        if state_file.is_symlink():
            fail(f"refusing symlinked outbox state: {state_file}")
        outbox.parent.mkdir(parents=True, exist_ok=True)
        if not state_file.exists():
            atomic_write(state_file, json.dumps({"offset": 0}, indent=2) + "\n", 0o600)
        if lines:
            with outbox.open("ab") as handle:
                handle.write(lines.encode())
        if output:
            if output.is_symlink():
                fail(f"refusing symlinked output: {output}")
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("a" if incremental else "w", encoding="utf-8") as handle:
                handle.write(lines)
        atomic_write(cursor_path, json.dumps(cursors, indent=2, sort_keys=True) + "\n", 0o600)
        transport_requested = bool(azure_account or rsync_to)
        if state_path and state_path != cursor_path and not transport_requested:
            atomic_write(state_path, json.dumps(cursors, indent=2, sort_keys=True) + "\n", 0o600)
        if transport_requested:
            if not _transport(state_dir, azure_account, azure_container, azure_table, azure_prefix, rsync_to):
                fail("telemetry transport failed; outbox state was not advanced")
            if state_path and state_path != cursor_path:
                atomic_write(state_path, json.dumps(cursors, indent=2, sort_keys=True) + "\n", 0o600)
    elif output:
        if output.is_symlink():
            fail(f"refusing symlinked output: {output}")
        if incremental:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("a", encoding="utf-8") as handle:
                handle.write(lines)
        else:
            atomic_write(output, lines)
        if state_path:
            atomic_write(state_path, json.dumps(cursors, indent=2, sort_keys=True) + "\n")
        if rsync_to:
            subprocess.run(["rsync", "-az", str(output), rsync_to], check=True)
    return len(records)


def notify(sync_state_dir: Path, roots: list[Path] | None = None, machine_id: str | None = None,
           output: Path | None = None, state_path: Path | None = None, incremental: bool = True,
           collectors_dir: Path | None = None, azure_account: str = "",
           azure_container: str = "telemetry-raw", azure_table: str = "AidataSyncIndex",
           azure_prefix: str = "aidata-sync/v1", rsync_to: str | None = None) -> int:
    sync_state_dir.mkdir(parents=True, exist_ok=True)
    signal = sync_state_dir / "notify.signal"
    signal.touch()
    lock = sync_state_dir / "sync.lock"
    with lock.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        export_roots = roots or [safe_root("~/Development/handoff")]
        export_output = output or (export_roots[0] / "_box-xfer" / f"gtt-bundle-{normalized_machine_id(machine_id or socket.gethostname())}.jsonl")
        while True:
            before = signal.stat().st_mtime_ns
            export(export_roots, machine_id or socket.gethostname(), export_output, state_path, incremental,
                   sync_state_dir, collectors_dir, quota_precheck_path=None,
                   azure_account=azure_account, azure_container=azure_container, azure_table=azure_table,
                   azure_prefix=azure_prefix, rsync_to=rsync_to)
            if signal.stat().st_mtime_ns <= before:
                break
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-dir", action="append")
    parser.add_argument("--machine-id", default=normalized_machine_id(socket.gethostname().split(".", 1)[0]))
    parser.add_argument("--out")
    parser.add_argument("--state-file")
    parser.add_argument("--sync-state-dir")
    parser.add_argument("--collectors-dir")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--azure-account", default="")
    parser.add_argument("--azure-container", default="telemetry-raw")
    parser.add_argument("--azure-table", default="AidataSyncIndex")
    parser.add_argument("--azure-prefix", default="aidata-sync/v1")
    parser.add_argument("--rsync-to")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args(argv)
    roots = [safe_root(piece) for item in (args.handoff_dir or ["~/Development/handoff"]) for piece in item.split(":") if piece]
    sync_dir = Path(args.sync_state_dir).expanduser() if args.sync_state_dir else None
    output = Path(args.out).expanduser() if args.out else roots[0] / "_box-xfer" / f"gtt-bundle-{normalized_machine_id(args.machine_id)}.jsonl"
    state = Path(args.state_file).expanduser() if args.state_file else None
    if args.notify:
        return notify(sync_dir or Path("~/.config/aidata-sync").expanduser(), roots, args.machine_id,
                      output, state, args.incremental,
                      Path(args.collectors_dir).expanduser() if args.collectors_dir else None,
                      args.azure_account, args.azure_container, args.azure_table, args.azure_prefix, args.rsync_to)
    count = export(roots, args.machine_id, output, state, args.incremental, sync_dir,
                   Path(args.collectors_dir).expanduser() if args.collectors_dir else None,
                   args.azure_account, args.azure_container, args.azure_table, args.azure_prefix, args.rsync_to)
    print(f"exported {count} record(s) to {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
