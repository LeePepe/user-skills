#!/usr/bin/env python3
"""SQLite state store and durable effects queue for team-lead handoffs."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator, Mapping


SCHEMA_VERSION = "1"
FAILURES_BEFORE_ERROR = 3
EFFECT_KINDS = {
    "linear_sync",
    "telemetry_notify",
    "herdr_launch",
    "herdr_deliver",
    "alert_write",
    "worktree_gc",
}


class Later(Exception):
    """Signal that an effect should be rescheduled without counting a failure."""

    def __init__(self, due_epoch_ms: int | None = None) -> None:
        super().__init__("effect deferred")
        self.due_epoch_ms = due_epoch_ms


def _safe_handoff_dir(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if ".." in path.parts:
        raise ValueError(f"Refusing path traversal in handoff directory: {raw}")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Refusing symlinked handoff directory: {path}")
    return path.resolve()


def state_path(handoff_dir: str | Path) -> Path:
    directory = _safe_handoff_dir(handoff_dir)
    target = directory / "state.sqlite"
    if target.is_symlink():
        raise ValueError(f"Refusing symlinked state database: {target}")
    return target


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def connect(handoff_dir: str | Path) -> sqlite3.Connection:
    """Open and initialize the handoff database."""
    connection = _connect(state_path(handoff_dir))
    _initialize(connection)
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()


def _initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS goal_state (
            goal_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            intent TEXT NOT NULL DEFAULT 'deliver',
            owner TEXT NOT NULL DEFAULT 'tianpei',
            coordinator TEXT NOT NULL DEFAULT 'unknown',
            tl_agent TEXT NOT NULL DEFAULT '',
            machine TEXT NOT NULL DEFAULT '',
            linear_issue TEXT NOT NULL DEFAULT '',
            contract_sha TEXT NOT NULL DEFAULT '',
            spec_sha TEXT NOT NULL DEFAULT '',
            max_workers INTEGER NOT NULL DEFAULT 3,
            budget_json TEXT NOT NULL DEFAULT '{}',
            feedback_json TEXT,
            updated_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'normal',
            repo TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            brief TEXT NOT NULL DEFAULT '',
            owner_agent TEXT NOT NULL DEFAULT '—',
            pr_ref TEXT NOT NULL DEFAULT '—',
            value_json TEXT NOT NULL,
            is_active INTEGER GENERATED ALWAYS AS (
                CASE WHEN status IN ('in_progress','in_review','changes_requested') THEN 1 ELSE 0 END
            ) VIRTUAL,
            is_pending INTEGER GENERATED ALWAYS AS (
                CASE WHEN status IN ('planned','ready') THEN 1 ELSE 0 END
            ) VIRTUAL,
            updated_at TEXT NOT NULL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_tasks_active
            ON tasks(status)
            WHERE status IN ('in_progress','in_review','changes_requested');
        CREATE INDEX IF NOT EXISTS idx_tasks_pending
            ON tasks(status)
            WHERE status IN ('planned','ready');
        CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            at TEXT NOT NULL,
            evt TEXT NOT NULL,
            task_id TEXT NOT NULL DEFAULT '',
            from_actor TEXT NOT NULL DEFAULT '',
            to_actor TEXT NOT NULL DEFAULT '',
            ref TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            resolved_by_id TEXT,
            meta_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_events_unresolved
            ON events(evt) WHERE resolved_by_id IS NULL;
        CREATE TABLE IF NOT EXISTS effects (
            kind TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            seq INTEGER NOT NULL,
            generation INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            due_epoch_ms INTEGER NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            since TEXT NOT NULL,
            PRIMARY KEY (kind, key)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS idx_effects_due
            ON effects(status, due_epoch_ms) WHERE status = 'pending';
        CREATE TABLE IF NOT EXISTS experiments (
            exp_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            baseline_metric TEXT NOT NULL,
            observed_metric TEXT NOT NULL,
            evidence_ref TEXT NOT NULL DEFAULT '',
            at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS inbox_triggers (
            trigger_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            actor TEXT NOT NULL,
            thread TEXT NOT NULL DEFAULT '',
            human INTEGER NOT NULL,
            addressed TEXT NOT NULL DEFAULT '',
            at TEXT NOT NULL,
            text TEXT NOT NULL,
            disposition_json TEXT NOT NULL DEFAULT '{}'
        ) STRICT;
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    connection.commit()


def _now_ms() -> int:
    return int(time.time() * 1000)


def enqueue_effect(
    connection: sqlite3.Connection,
    kind: str,
    key: str,
    payload: Mapping[str, Any] | Any,
    now_ms: int | None = None,
) -> None:
    """Coalesce an effect by key in the caller's transaction."""
    if kind not in EFFECT_KINDS:
        raise ValueError(f"unsupported effect kind: {kind}")
    now = _now_ms() if now_ms is None else int(now_ms)
    value = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    connection.execute(
        """
        INSERT INTO effects
            (kind, key, value_json, seq, due_epoch_ms, since)
        VALUES (?, ?, ?, COALESCE((SELECT MAX(seq) + 1 FROM effects), 1), ?, ?)
        ON CONFLICT(kind, key) DO UPDATE SET
            value_json = excluded.value_json,
            generation = effects.generation + 1,
            status = 'pending',
            attempts = 0,
            error = '',
            due_epoch_ms = excluded.due_epoch_ms
        """,
        (kind, key, value, now, str(now)),
    )


def mark_effect_later(
    connection: sqlite3.Connection,
    kind: str,
    key: str,
    due_epoch_ms: int | None = None,
) -> None:
    connection.execute(
        "UPDATE effects SET status = 'pending', due_epoch_ms = ? WHERE kind = ? AND key = ?",
        (_now_ms() + 2000 if due_epoch_ms is None else int(due_epoch_ms), kind, key),
    )


def mark_effect_error(
    connection: sqlite3.Connection,
    kind: str,
    key: str,
    error: str,
    now_ms: int | None = None,
) -> int:
    row = connection.execute(
        "SELECT attempts FROM effects WHERE kind = ? AND key = ?", (kind, key)
    ).fetchone()
    if row is None:
        raise KeyError(f"effect not found: {kind}/{key}")
    attempts = int(row["attempts"]) + 1
    delay = min(60000, 2000 * (2 ** max(0, attempts - 1)))
    status = "failed" if attempts >= FAILURES_BEFORE_ERROR else "pending"
    connection.execute(
        """
        UPDATE effects
        SET attempts = ?, status = ?, error = ?, due_epoch_ms = ?
        WHERE kind = ? AND key = ?
        """,
        (attempts, status, str(error), (now_ms or _now_ms()) + delay, kind, key),
    )
    return attempts


def record_effect_result(
    connection: sqlite3.Connection,
    kind: str,
    key: str,
    result: Any = None,
    *,
    error: Exception | None = None,
    now_ms: int | None = None,
) -> None:
    """Apply a worker result, preserving Later and retry semantics."""
    if isinstance(error, Later):
        mark_effect_later(connection, kind, key, error.due_epoch_ms)
        return
    if error is not None:
        mark_effect_error(connection, kind, key, str(error), now_ms)
        return
    connection.execute(
        "UPDATE effects SET status = 'done', error = '' WHERE kind = ? AND key = ?",
        (kind, key),
    )


def _event_id(connection: sqlite3.Connection) -> str:
    row = connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()
    return f"E{int(row['count']) + 1}"


def sync_to_sqlite(
    handoff_dir: str | Path,
    queue_data: Mapping[str, Any],
    events: list[Mapping[str, Any]] | None = None,
) -> None:
    """Mirror queue JSON and newly rendered ledger events into SQLite."""
    connection = connect(handoff_dir)
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    goal_id = str(queue_data.get("goal") or Path(handoff_dir).name)
    budget = queue_data.get("budget")
    max_workers = queue_data.get("max_workers")
    if max_workers is None:
        max_workers = 3
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO goal_state
              (goal_id, status, intent, owner, coordinator, tl_agent, machine,
               linear_issue, contract_sha, spec_sha, max_workers, budget_json,
               feedback_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(goal_id) DO UPDATE SET
              status=excluded.status, intent=excluded.intent, max_workers=excluded.max_workers,
              budget_json=excluded.budget_json, feedback_json=excluded.feedback_json,
              updated_at=excluded.updated_at
            """,
            (
                goal_id,
                str(queue_data.get("status") or "planning"),
                str(queue_data.get("intent") or "deliver"),
                str(queue_data.get("owner") or "tianpei"),
                str(queue_data.get("coordinator") or "unknown"),
                str(queue_data.get("tl_agent") or ""),
                str(queue_data.get("machine") or ""),
                str(queue_data.get("linear_issue") or ""),
                str(queue_data.get("contract_sha") or ""),
                str(queue_data.get("spec_sha") or ""),
                int(max_workers),
                json.dumps(budget or {}, ensure_ascii=False, sort_keys=True),
                json.dumps(queue_data.get("feedback_manifest"), ensure_ascii=False)
                if queue_data.get("feedback_manifest") is not None
                else None,
                now,
            ),
        )
        for task in queue_data.get("tasks", []):
            task_id = str(task.get("task_id"))
            connection.execute(
                """
                INSERT INTO tasks
                  (task_id, status, priority, repo, title, brief, owner_agent, pr_ref, value_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  status=excluded.status, priority=excluded.priority, repo=excluded.repo,
                  title=excluded.title, brief=excluded.brief, owner_agent=excluded.owner_agent,
                  pr_ref=excluded.pr_ref, value_json=excluded.value_json, updated_at=excluded.updated_at
                """,
                (
                    task_id,
                    str(task.get("status") or "planned"),
                    str(task.get("priority") or "normal"),
                    str(task.get("repo") or ""),
                    str(task.get("title") or task_id),
                    str(task.get("brief") or ""),
                    str(task.get("owner") or "—"),
                    str(task.get("pr") or "—"),
                    json.dumps(task, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
        next_event = int(
            connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
        ) + 1
        for event in events or []:
            event_id = str(event.get("event_id") or f"E{next_event}")
            next_event += 1
            connection.execute(
                """
                INSERT OR IGNORE INTO events
                  (event_id, at, evt, task_id, from_actor, to_actor, ref, note, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    str(event.get("at") or now),
                    str(event.get("evt") or ""),
                    str(event.get("task") or event.get("task_id") or ""),
                    str(event.get("from_actor") or ""),
                    str(event.get("to_actor") or ""),
                    str(event.get("ref") or ""),
                    str(event.get("note") or ""),
                    json.dumps(event.get("meta") or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
        enqueue_effect(connection, "linear_sync", f"{goal_id}:goal", {"goal_id": goal_id}, int(time.time() * 1000))
        enqueue_effect(connection, "telemetry_notify", f"{goal_id}:goal", {"goal_id": goal_id}, int(time.time() * 1000))
    connection.close()


def sync_from_queue_and_ledger(handoff_dir: str | Path) -> dict[str, Any]:
    """Import legacy queue.json data into state.sqlite and return the queue."""
    directory = _safe_handoff_dir(handoff_dir)
    queue_path = directory / "queue.json"
    if queue_path.is_symlink():
        raise ValueError(f"Refusing symlinked queue artifact: {queue_path}")
    if queue_path.exists():
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    else:
        data = {"goal": directory.name, "version": 1, "tasks": []}
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError(f"Corrupt queue.json in {directory}")
    sync_to_sqlite(directory, data)
    return data


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Initialize or inspect team-lead state.sqlite.")
    parser.add_argument("--handoff-dir", required=True)
    parser.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    data = sync_from_queue_and_ledger(args.handoff_dir) if args.sync else None
    if data is None:
        with connect(args.handoff_dir) as connection:
            data = {
                "schema_version": connection.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()[0]
            }
    print(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
