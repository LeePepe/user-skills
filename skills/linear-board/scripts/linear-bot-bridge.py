#!/usr/bin/env python3
"""Bidirectional Linear @bot bridge for the personal M2 runtime.

The bridge is intentionally stdlib-only.  Linear and Herdr are treated as
untrusted, fallible transports; durable state is committed before dispatch and
completion is never inferred from a timeout.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

BOT_ACTOR_ID = "6105e16e-700a-4ea5-8283-67aec10daf20"
OWNER_USER_ID = "0680d5e4-720a-4724-a1d4-b9660c0d1495"
EXCLUDED_NAMES = {"work business", "bing", "workplace"}
ACK = "🤖 已接单，正在唤醒目标 M2 处理..."
BOT_MARKER = "<!-- BOT-BRIDGE:"
DEFAULT_STATE = Path("~/.config/linear/bot-bridge-state.json").expanduser()
VAULT = Path("~/Development/Personal/knowledge-vault").expanduser()
ROOT = Path(__file__).resolve().parents[3]
SYNC_PATH = ROOT / "skills" / "team-lead" / "scripts" / "linear-sync.py"
ROUTER_PATH = ROOT / "skills" / "personal-m2-assistant" / "scripts" / "m2-router.py"


def _sync_module() -> Any:
    spec = importlib.util.spec_from_file_location("linear_bot_bridge_sync", SYNC_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("canonical Linear client unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_bot_comment(comment: dict[str, Any]) -> bool:
    user = comment.get("user") or {}
    actor = comment.get("botActor")
    body = str(comment.get("body") or "")
    return (str(user.get("id") or "") == BOT_ACTOR_ID or bool(user.get("app"))
            or actor is not None or BOT_MARKER in body or body.startswith("🤖 已接单"))


def excluded_scope(issue: dict[str, Any]) -> bool:
    values: list[str] = []
    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("name", "displayName", "key"):
                if value.get(key):
                    values.append(str(value[key]))
            for child in value.values():
                if isinstance(child, (dict, list)):
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(issue.get("project")); collect(issue.get("team")); collect(issue.get("parent"))
    return any(any(term in value.casefold() for term in EXCLUDED_NAMES) for value in values)


def is_daily(issue: dict[str, Any]) -> bool:
    return "[daily learn" in str(issue.get("title") or "").casefold() or "KD-DAILY-START" in str(issue.get("description") or "")


def topic_selection(issue: dict[str, Any], comment: dict[str, Any] | None = None) -> list[str]:
    body = str((comment or {}).get("body") or "")
    if re.search(r"\b(?:all|都做|全部)\b", body, re.I):
        return ["all"]
    numbers = re.findall(r"(?<!\d)([123])(?!\d)", body)
    if numbers:
        return list(dict.fromkeys(numbers))
    checked = re.findall(r"(?mi)^\s*-\s*\[[xX]\]\s*(?:\[[^]]+\]\s*)?(.+?)\s*$", str(issue.get("description") or ""))
    return checked or ["1"]


def thread_context(issue: dict[str, Any], comment: dict[str, Any]) -> str:
    comments = issue.get("comments") or {}
    nodes = comments.get("nodes", []) if isinstance(comments, dict) else comments
    lines = [f"Issue: {issue.get('title', '')}", "Description:", str(issue.get("description") or ""), "Comment thread:"]
    for item in nodes if isinstance(nodes, list) else []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('id')}: {item.get('body', '')}")
    lines.extend(["Current directive:", str(comment.get("body") or "")])
    return "\n".join(lines)


class State:
    def __init__(self, path: Path, dry_run: bool = False) -> None:
        self.path, self.dry_run = path, dry_run
        self.lock_path = path.with_name(path.name + ".lock")
        self.data: dict[str, Any] = {"version": 1, "processed_comment_ids": [], "issue_assignments": {}, "pending": {}}
        try:
            self.data.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        self.data.setdefault("processed_comment_ids", []); self.data.setdefault("issue_assignments", {}); self.data.setdefault("pending", {})

    def save(self) -> None:
        if self.dry_run:
            return
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            fd, name = tempfile.mkstemp(prefix=".bot-bridge-", dir=str(self.path.parent))
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    json.dump(self.data, out, ensure_ascii=False, indent=2); out.write("\n")
                os.replace(name, self.path); os.chmod(self.path, 0o600)
            finally:
                if os.path.exists(name): os.unlink(name)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    if not secret or not signature:
        return False
    supplied = signature.removeprefix("sha256=")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def resolve_learning_agent() -> str | None:
    result = _run(["herdr", "agent", "list", "--json"], 10)
    if result.returncode:
        return None
    try:
        payload = json.loads(result.stdout)
    except (ValueError, json.JSONDecodeError):
        return None
    agents = payload if isinstance(payload, list) else payload.get("agents", []) if isinstance(payload, dict) else []
    for agent in agents:
        name = str(agent.get("name") or agent.get("id") or "") if isinstance(agent, dict) else ""
        if name.startswith("m2-learning-and-blog"):
            return name
    return None


def _fresh_cards(before: float) -> list[Path]:
    if not VAULT.is_dir(): return []
    return sorted((p for p in VAULT.rglob("*.md") if p.stat().st_mtime > before), key=lambda p: p.stat().st_mtime)


def dispatch(issue: dict[str, Any], directive: str, dry_run: bool, before: float) -> tuple[bool, str, dict[str, Any]]:
    payload = thread_context(issue, {"body": directive})
    if is_daily(issue):
        agent = resolve_learning_agent()
        if not agent: return False, "registered learning M2 not found", {}
        argv = ["herdr", "agent", "prompt", agent, f"/learn {directive}\n\n{payload}"]
    else:
        plan = [sys.executable, str(ROUTER_PATH), "--plan", "--message", payload, "--json"]
        if dry_run: return True, "dry-run router plan", {"argv": plan}
        planned = _run(plan, 30)
        if planned.returncode: return False, planned.stderr[-1000:], {"argv": plan}
        try: parsed = json.loads(planned.stdout)
        except (ValueError, json.JSONDecodeError): return False, "router returned invalid JSON", {"argv": plan}
        plan_file = Path(tempfile.mkstemp(prefix="bot-bridge-plan-", suffix=".json")[1])
        plan_file.write_text(json.dumps(parsed), encoding="utf-8")
        argv = [sys.executable, str(ROUTER_PATH), "--apply", "--plan-file", str(plan_file), "--approve", "--json"]
    if dry_run: return True, "dry-run dispatch", {"argv": argv}
    sent = _run(argv, 30)
    if sent.returncode: return False, sent.stderr[-1000:], {"argv": argv}
    # Wait/read only after this dispatch; old idle output is never accepted.
    target = argv[3] if argv[0] == "herdr" else ""
    if target:
        waited = _run(["herdr", "agent", "wait", target, "--timeout", "60000"], 70)
        if waited.returncode: return False, "target did not complete", {"argv": argv}
        read = _run(["herdr", "agent", "read", target, "--source", "recent-unwrapped", "--lines", "200"], 15)
        output = read.stdout.strip() if read.returncode == 0 else ""
    else:
        output = sent.stdout.strip()
    cards = _fresh_cards(before) if is_daily(issue) else []
    result = "\n\n".join(p.read_text(encoding="utf-8") for p in cards) if cards else output
    return bool(result), result or "no verified output", {"argv": argv, "agent": target, "cards": [str(p) for p in cards]}


def process_once(client: Any, state: State, *, dry_run: bool = False) -> dict[str, Any]:
    issues = client.list_issues("TIA", limit=250)
    report: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict) or excluded_scope(issue): continue
        state_value = issue.get("state") or {}
        if str(state_value.get("type") or state_value.get("name") or "").casefold() in {"completed", "canceled", "cancelled", "done"}: continue
        comments = (issue.get("comments") or {}).get("nodes", [])
        comment_by_id = {str(c.get("id")): c for c in comments if isinstance(c, dict)}
        triggers: list[dict[str, Any]] = []
        for comment in comments if isinstance(comments, list) else []:
            if not isinstance(comment, dict) or is_bot_comment(comment) or str(comment.get("id")) in state.data["processed_comment_ids"]: continue
            parent = comment.get("parent") or {}
            parent_comment = comment_by_id.get(str(parent.get("id")))
            mentions = "@bot" in str(comment.get("body") or "").casefold()
            reply_to_bot = bool(parent_comment and is_bot_comment(parent_comment))
            if mentions or reply_to_bot:
                triggers.append(comment)
        assignee = issue.get("assignee") or {}; delegate = issue.get("delegate") or {}
        assigned = str(assignee.get("id") or "") == BOT_ACTOR_ID or str(delegate.get("id") or "") == BOT_ACTOR_ID
        assignment_key = f"{issue.get('id')}:{assignee.get('id','')}:{delegate.get('id','')}:{issue.get('updatedAt','')}"
        if assigned and not triggers and state.data["issue_assignments"].get(str(issue.get("id"))) != assignment_key:
            triggers.append({"id": None, "body": "assigned to bot", "assignment": True})
        for trigger in triggers:
            issue_id = str(issue.get("id") or issue.get("identifier"))
            pending = state.data["pending"].get(issue_id)
            if pending and pending.get("trigger") == trigger.get("id"):
                report.append({"issue": issue.get("identifier") or issue_id, "trigger": trigger.get("id"), "ok": False, "pending": True})
                continue
            state.data["pending"][issue_id] = {"started_at": time.time(), "trigger": trigger.get("id"), "assignment_key": assignment_key}
            state.save()
            if not dry_run:
                client.create_comment(issue_id, ACK, parent_id=trigger.get("id") if trigger.get("id") else None)
                team = client.team_metadata("TIA"); in_progress = next((s.get("id") for s in team.get("states", {}).get("nodes", []) if str(s.get("name", "")).casefold() == "in progress"), None)
                if in_progress: client.update_issue(issue_id, state_id=in_progress)
            before = time.time()
            ok, output, detail = dispatch(issue, ", ".join(topic_selection(issue, trigger)), dry_run, before)
            entry = {"issue": issue.get("identifier") or issue_id, "trigger": trigger.get("id"), "ok": ok, "detail": detail}
            if ok and not dry_run:
                client.create_comment(issue_id, f"@ltp19941128\n\n{output}", parent_id=trigger.get("id") if trigger.get("id") else None)
                client.update_issue(issue_id, assignee_id=OWNER_USER_ID, clear_delegate=True)
                state.data["pending"].pop(issue_id, None); state.data["processed_comment_ids"].append(trigger.get("id")) if trigger.get("id") else None
                if assigned: state.data["issue_assignments"][issue_id] = assignment_key
                state.save()
            report.append(entry)
    return {"ok": True, "dry_run": dry_run, "triggers": report}


def serve_webhook(port: int, client: Any, state: State) -> None:
    secret = os.environ.get("LINEAR_WEBHOOK_SECRET", "")
    if not secret: raise SystemExit("LINEAR_WEBHOOK_SECRET is required")
    bridge = (client, state)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "-1"))
            if length < 0 or length > 1_000_000: self.send_error(413); return
            body = self.rfile.read(length)
            if not verify_signature(secret, body, self.headers.get("Linear-Signature", "")): self.send_error(401); return
            process_once(*bridge); self.send_response(202); self.end_headers()
        def log_message(self, *_: Any) -> None: pass
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--once", action="store_true"); parser.add_argument("--daemon", action="store_true"); parser.add_argument("--interval", type=float, default=20); parser.add_argument("--json", action="store_true"); parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE); parser.add_argument("--webhook-port", type=int)
    args = parser.parse_args(argv); sync = _sync_module(); token = sync.load_api_key()
    if not token: print(json.dumps({"ok": False, "error": "Linear credentials unavailable"})); return 2
    client = sync.GraphQLClient(token); state = State(args.state_file, args.dry_run)
    if args.webhook_port: serve_webhook(args.webhook_port, client, state); return 0
    while True:
        try: report = process_once(client, state, dry_run=args.dry_run)
        except Exception as exc: report = {"ok": False, "error": str(exc)}
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if not args.daemon: return 0 if report.get("ok") else 1
        time.sleep(max(1, args.interval))

if __name__ == "__main__": raise SystemExit(main())
