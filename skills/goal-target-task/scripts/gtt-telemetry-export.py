#!/usr/bin/env python3
"""Export redacted, incremental Goal/task telemetry as JSONL using only Python stdlib."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


HOME_PATH_RE = re.compile(r"(?:/Users|/home)/[^/\s]+")
SECRET_RE = re.compile(
    r"(?i)(?:ghp_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+|AKIA[0-9A-Z]{16}|"
    r"(?<![A-Za-z0-9])(?:api[_-]?key|token|password|secret)(\s*[:=]\s*)['\"]?[^'\"\s,;]+)"
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
RECORD_FILES = {
    "ledger.md": "ledger",
    "goal.md": "goal",
    "queue.json": "queue",
}


def fail(message: str) -> "NoReturn":
    raise ValueError(message)


def normalized_machine_id(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value).strip("-")
    return value or "unknown-machine"


def normalized_text(text: str) -> str:
    text = HOME_PATH_RE.sub("~", text)
    text = SECRET_RE.sub(lambda match: match.group(1) + "[REDACTED]" if match.group(1) else "[REDACTED]", text)
    text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    lines = []
    for line in text.splitlines(keepends=True):
        if "://" in line:
            parts = re.split(r"(\S+://\S+)", line)
            for index, part in enumerate(parts):
                if "://" not in part:
                    continue
                try:
                    parsed = urlsplit(part)
                    if parsed.query:
                        query = [(key, "[REDACTED]") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)]
                        parts[index] = urlunsplit(parsed._replace(query=urlencode(query)))
                except ValueError:
                    pass
            line = "".join(parts)
        lines.append(line)
    return "".join(lines)


def safe_root(raw: str) -> Path:
    path = Path(raw).expanduser()
    if ".." in path.parts:
        fail(f"refusing path traversal: {raw}")
    if path.exists() and path.is_symlink():
        fail(f"refusing symlinked handoff directory: {path}")
    return path.resolve()


def ensure_safe_file(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError:
        fail(f"path escapes handoff directory: {path}")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            fail(f"refusing symlink: {current}")


def iter_input_files(root: Path) -> Iterable[tuple[Path, str]]:
    if not root.is_dir():
        return
    for goal_dir in sorted(root.iterdir()):
        if goal_dir.name.startswith("_"):
            continue
        if goal_dir.is_symlink():
            fail(f"refusing symlink: {goal_dir}")
        if not goal_dir.is_dir():
            continue
        for filename, record_type in RECORD_FILES.items():
            candidate = goal_dir / filename
            if candidate.exists() or candidate.is_symlink():
                ensure_safe_file(candidate, root)
                if candidate.is_file():
                    yield candidate, record_type
        receipts = goal_dir / "receipts"
        if receipts.exists() or receipts.is_symlink():
            ensure_safe_file(receipts, root)
            if receipts.is_dir():
                for receipt in sorted(receipts.iterdir()):
                    ensure_safe_file(receipt, root)
                    if receipt.is_file() and receipt.suffix == ".md":
                        yield receipt, "receipt"


def read_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if path.is_symlink():
        fail(f"refusing symlinked state file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        fail("state file must contain a {relpath: sha256} JSON object")
    return value


def atomic_write(path: Path, content: str) -> None:
    if path.is_symlink():
        fail(f"refusing symlinked output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_record(path: Path, root: Path, record_type: str, machine_id: str) -> tuple[str, dict[str, str]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    exported = normalized_text(raw)
    rel_path = path.relative_to(root).as_posix()
    goal_id = rel_path.split("/", 1)[0]
    raw_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    record = {
        "schema_version": 1,
        "record_type": record_type,
        "machine_id": machine_id,
        "goal_id": goal_id,
        "rel_path": rel_path,
        "content_sha256": hashlib.sha256(exported.encode("utf-8")).hexdigest(),
        "mtime_iso": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).astimezone().isoformat(timespec="seconds"),
        "exported_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "content": exported,
    }
    return raw_sha, record


def export(roots: list[Path], machine_id: str, output: Path, state_path: Path | None, incremental: bool) -> int:
    machine_id = normalized_machine_id(machine_id)
    state = read_state(state_path) if state_path and incremental else {}
    records: list[str] = []
    next_state: dict[str, str] = {}
    seen: set[str] = set()
    for root in roots:
        for path, record_type in iter_input_files(root):
            rel_key = f"{root}:{path.relative_to(root).as_posix()}"
            raw_sha, record = build_record(path, root, record_type, machine_id)
            next_state[rel_key] = raw_sha
            seen.add(rel_key)
            if incremental and state.get(rel_key) == raw_sha:
                continue
            records.append(json.dumps(record, ensure_ascii=False, sort_keys=True))

    if incremental:
        output.parent.mkdir(parents=True, exist_ok=True)
        content = ("\n".join(records) + "\n") if records else ""
        with output.open("a", encoding="utf-8") as handle:
            handle.write(content)
    else:
        atomic_write(output, ("\n".join(records) + "\n") if records else "")
    if state_path:
        atomic_write(state_path, json.dumps({key: next_state[key] for key in sorted(seen)}, indent=2) + "\n")
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-dir", action="append")
    parser.add_argument("--machine-id", default=normalized_machine_id(socket.gethostname().split(".", 1)[0]))
    parser.add_argument("--out")
    parser.add_argument("--state-file")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--rsync-to")
    args = parser.parse_args(argv)
    roots: list[Path] = []
    for item in (args.handoff_dir or ["~/Development/handoff"]):
        roots.extend(safe_root(piece) for piece in item.split(":") if piece)
    machine_id = normalized_machine_id(args.machine_id)
    output = Path(args.out).expanduser() if args.out else roots[0] / "_box-xfer" / f"gtt-bundle-{machine_id}.jsonl"
    state = Path(args.state_file).expanduser() if args.state_file else None
    count = export(roots, machine_id, output, state, args.incremental)
    if args.rsync_to:
        subprocess.run(["rsync", "-az", str(output), args.rsync_to], check=True)
    print(f"exported {count} record(s) to {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
