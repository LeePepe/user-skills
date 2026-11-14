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
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
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
            for key in ("name", "displayName", "key", "title"):
                if value.get(key): values.append(str(value[key]))
            for child in value.values():
                if isinstance(child, (dict, list)): collect(child)
        elif isinstance(value, list):
            for child in value: collect(child)
    collect(issue.get("project")); collect(issue.get("team")); collect(issue.get("parent"))
    # Missing hierarchy is fail-closed when it looks like a restricted title.
    return any(any(term in value.casefold() for term in EXCLUDED_NAMES) for value in values)


def _topic_rows(description: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pattern = re.compile(r"(?mi)^\s*[-*+]\s*\[([ xX])\]\s*(?:\*{1,2})?\\?\[?(\d+)\\?\]?\s*(?:\*{1,2})?(.+?)\s*$")
    for match in pattern.finditer(description):
        text = re.sub(r"[*`]", "", match.group(3)).strip()
        slug_match = re.search(r"(?:learning-topic://|\(`?)([a-z0-9][a-z0-9-]+)", text, re.I)
        slug = slug_match.group(1) if slug_match else ""
        if not slug:
            slug_match = re.search(r"\(([a-z0-9][a-z0-9-]+)\)", text, re.I)
            slug = slug_match.group(1) if slug_match else ""
        rows.append({"number": match.group(2), "slug": slug, "text": text, "checked": match.group(1).casefold() == "x"})
    return rows


def _selectors(body: str) -> list[str]:
    text = body.casefold()
    if re.search(r"(?:^|\s|@bot)(?:all|都做|全部)(?:$|\s|[，。,．.!！])", text): return ["all"]
    return list(dict.fromkeys(re.findall(r"(?<!\d)([123])(?!\d)", text)))


def topic_selection(issue: dict[str, Any], comment: dict[str, Any] | None = None) -> list[str]:
    rows = _topic_rows(str(issue.get("description") or ""))
    body = str((comment or {}).get("body") or "")
    selected = _selectors(body)
    if selected == ["all"]: return [r["slug"] or r["text"] for r in rows] or ["all"]
    if selected: return [r["slug"] or r["text"] for r in rows if r["number"] in selected] or selected
    checked = [r["slug"] or r["text"] for r in rows if r["checked"]]
    return checked or ([rows[0]["slug"] or rows[0]["text"]] if rows else ["1"])


def is_daily(issue: dict[str, Any]) -> bool:
    return "[daily learn" in str(issue.get("title") or "").casefold() or "KD-DAILY-START" in str(issue.get("description") or "")



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


def _herdr_payload(stdout: str) -> dict[str, Any]:
    try: return json.loads(stdout)
    except (ValueError, json.JSONDecodeError): return {}


def resolve_learning_agent() -> str | None:
    result = _run(["herdr", "agent", "list"], 10)
    if result.returncode: return None
    payload = _herdr_payload(result.stdout)
    root = payload.get("result", payload) if isinstance(payload, dict) else {}
    agents = root.get("agents", []) if isinstance(root, dict) else []
    for agent in agents:
        name = str(agent.get("name") or "") if isinstance(agent, dict) else ""
        if name.startswith("m2-learning-and-blog"): return name
    return None


def _agent_state(name: str) -> dict[str, Any]:
    result = _run(["herdr", "agent", "get", name], 10)
    payload = _herdr_payload(result.stdout)
    return payload.get("result", {}).get("agent", payload.get("agent", {})) if isinstance(payload, dict) else {}


def _fresh_cards(before: float, slugs: list[str] | None = None) -> list[Path]:
    if not VAULT.is_dir(): return []
    roots = [VAULT / part for part in ("concepts", "craft", "thoughts")]
    found = [p for root in roots if root.is_dir() for p in root.rglob("*.md") if p.name != "index.md" and p.stat().st_mtime > before]
    if slugs:
        wanted = {s.casefold() for s in slugs if s and s != "all"}
        found = [p for p in found if p.stem.casefold() in wanted or any(s in p.stem.casefold() for s in wanted)]
    return sorted(found, key=lambda p: p.stat().st_mtime)


def dispatch(issue: dict[str, Any], directive: str, dry_run: bool, before: float, slugs: list[str] | None = None) -> tuple[bool, str, dict[str, Any]]:
    payload = thread_context(issue, {"body": directive})
    if is_daily(issue):
        agent = resolve_learning_agent()
        if not agent: return False, "registered learning M2 not found", {}
        prior = _agent_state(agent)
        prior_seq = int(prior.get("completion_seq") or prior.get("completionSeq") or 0)
        prompt = "/learn " + " ".join(slugs or [directive]) + "\n\n" + payload
        argv = ["herdr", "agent", "prompt", agent, prompt, "--wait", "--until", "idle", "--until", "done", "--timeout", "60000"]
        if dry_run: return True, "dry-run dispatch", {"argv": argv, "agent": agent, "prior_completion_seq": prior_seq}
        sent = _run(argv, 75)
        after = _agent_state(agent)
        after_seq = int(after.get("completion_seq") or after.get("completionSeq") or 0)
        status = str(after.get("agent_status") or after.get("status") or "").casefold()
        if sent.returncode or status in {"blocked", "unknown"} or after_seq <= prior_seq:
            return False, "target did not produce a fresh completion", {"argv": argv, "agent": agent, "prior_completion_seq": prior_seq, "completion_seq": after_seq, "status": status}
        read = _run(["herdr", "agent", "read", agent, "--source", "recent-unwrapped", "--lines", "400"], 15)
        output = read.stdout.strip() if read.returncode == 0 else ""
        cards = _fresh_cards(before, slugs)
        result = "\n\n".join(p.read_text(encoding="utf-8") for p in cards)
        if not result: result = output
        return bool(result), result or "no verified output", {"argv": argv, "agent": agent, "cards": [str(p) for p in cards], "completion_seq": after_seq}

    plan_argv = [sys.executable, str(ROUTER_PATH), "--plan", "--message", payload, "--json"]
    if dry_run: return True, "dry-run router plan", {"argv": plan_argv}
    planned = _run(plan_argv, 30)
    if planned.returncode: return False, planned.stderr[-1000:], {"argv": plan_argv}
    parsed = _herdr_payload(planned.stdout)
    if not parsed: return False, "router returned invalid JSON", {"argv": plan_argv}
    plan_fd, plan_name = tempfile.mkstemp(prefix="bot-bridge-plan-", suffix=".json")
    os.close(plan_fd)
    receipt_fd, receipt_name = tempfile.mkstemp(prefix="bot-bridge-receipt-", suffix=".json")
    os.close(receipt_fd)
    plan_file, receipt_file = Path(plan_name), Path(receipt_name)
    try:
        plan_file.write_text(json.dumps(parsed), encoding="utf-8"); os.chmod(plan_file, 0o600)
        apply_argv = [sys.executable, str(ROUTER_PATH), "--apply", "--plan-file", str(plan_file), "--apply-receipt-file", str(receipt_file), "--approve", "--json"]
        applied = _run(apply_argv, 45)
        if applied.returncode: return False, applied.stderr[-1000:], {"argv": apply_argv}
        watch_argv = [sys.executable, str(ROUTER_PATH), "--watch", "--plan-file", str(plan_file), "--apply-receipt-file", str(receipt_file), "--json", "--interval", "1", "--max-wait", "60"]
        watched = _run(watch_argv, 75)
        if watched.returncode: return False, watched.stderr[-1000:], {"argv": watch_argv}
        watched_payload = _herdr_payload(watched.stdout)
        output = str(watched_payload.get("completed_rollup") or watched_payload.get("summary") or watched_payload.get("result") or "").strip()
        if not output: return False, "router completed without a verified summary", {"argv": watch_argv}
        return True, output, {"argv": watch_argv, "apply": apply_argv, "receipt": str(receipt_file)}
    finally:
        for path in (plan_file, receipt_file):
            try: path.unlink()
            except OSError: pass


def _root_comment(comment: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    current = comment
    seen: set[str] = set()
    while True:
        parent_id = str((current.get("parent") or {}).get("id") or "")
        if not parent_id or parent_id in seen or parent_id not in by_id: return current
        seen.add(parent_id); current = by_id[parent_id]


def _latest_owner(comments: list[dict[str, Any]]) -> dict[str, Any] | None:
    owner = [c for c in comments if str((c.get("user") or {}).get("id") or "") == OWNER_USER_ID and not is_bot_comment(c)]
    return max(owner, key=lambda c: (str(c.get("createdAt") or ""), str(c.get("id") or "")), default=None)


def _directive(issue: dict[str, Any], trigger: dict[str, Any], comments: list[dict[str, Any]]) -> tuple[str, list[str]]:
    if is_daily(issue):
        source = trigger if trigger.get("id") else (_latest_owner(comments) or trigger)
        slugs = topic_selection(issue, source)
        return ", ".join(slugs), slugs
    if trigger.get("id"): return str(trigger.get("body") or ""), []
    return str(issue.get("description") or issue.get("title") or "assigned issue"), []


def process_once(client: Any, state: State, *, dry_run: bool = False) -> dict[str, Any]:
    issues = client.list_issues("TIA", limit=250)
    report: list[dict[str, Any]] = []
    for issue in sorted((i for i in issues if isinstance(i, dict)), key=lambda i: str(i.get("updatedAt") or "")):
        if excluded_scope(issue): continue
        state_value = issue.get("state") or {}
        if str(state_value.get("type") or state_value.get("name") or "").casefold() in {"completed", "canceled", "cancelled", "done"}: continue
        comments = (issue.get("comments") or {}).get("nodes", [])
        comments = sorted((c for c in comments if isinstance(c, dict)), key=lambda c: (str(c.get("createdAt") or ""), str(c.get("id") or "")))
        by_id = {str(c.get("id")): c for c in comments}
        unprocessed = [c for c in comments if not is_bot_comment(c) and str(c.get("id")) not in state.data["processed_comment_ids"]]
        triggers = [c for c in unprocessed if "@bot" in str(c.get("body") or "").casefold() or ((c.get("parent") or {}).get("id") and is_bot_comment(by_id.get(str((c.get("parent") or {}).get("id")), {})))]
        assignee = issue.get("assignee") or {}; delegate = issue.get("delegate") or {}
        assigned = str(assignee.get("id") or "") == BOT_ACTOR_ID or str(delegate.get("id") or "") == BOT_ACTOR_ID
        latest_event = str((unprocessed[-1] if unprocessed else {}).get("id") or "")
        assignment_key = f"{issue.get('id')}:{assignee.get('id','')}:{delegate.get('id','')}:{latest_event}"
        issue_id = str(issue.get("id") or issue.get("identifier"))
        if not assigned:
            state.data["issue_assignments"].pop(issue_id, None)
        if assigned and not triggers and state.data["issue_assignments"].get(issue_id) != assignment_key:
            triggers = [{"id": None, "body": "assigned to bot", "assignment": True}]
        if not triggers: continue
        # One turn per issue: consume all eligible comments and use the newest directive.
        trigger = triggers[-1]; root = _root_comment(trigger, by_id) if trigger.get("id") else trigger
        pending = state.data["pending"].get(issue_id)
        resuming = bool(pending and pending.get("phase") == "ack" and time.time() - float(pending.get("started_at", 0)) < 600)
        if pending and not resuming and time.time() - float(pending.get("started_at", 0)) < 600:
            report.append({"issue": issue.get("identifier") or issue_id, "pending": True, "phase": pending.get("phase")})
            continue
        if resuming:
            trigger_ids = pending.get("trigger_ids", [])
            trigger = by_id.get(str(trigger_ids[-1]), trigger) if trigger_ids else trigger
            root = _root_comment(trigger, by_id) if trigger.get("id") else trigger
        else:
            directive, slugs = _directive(issue, trigger, comments)
            pending = {"started_at": time.time(), "trigger_ids": [c.get("id") for c in triggers if c.get("id")], "assignment_key": assignment_key, "phase": "ack"}
            state.data["pending"][issue_id] = pending; state.save()
        if resuming:
            directive, slugs = _directive(issue, trigger, comments)
        marker = f"{BOT_MARKER}ack:{issue_id}:{latest_event} -->"
        ack_id = None
        if not dry_run:
            existing_ack = next((c for c in comments if marker in str(c.get("body") or "")), None)
            if not existing_ack:
                ack_id = client.create_comment(issue_id, f"{ACK}\n{marker}", parent_id=root.get("id") if root.get("id") else None)
            team = client.team_metadata("TIA"); in_progress = next((s.get("id") for s in team.get("states", {}).get("nodes", []) if str(s.get("name", "")).casefold() == "in progress"), None)
            if in_progress: client.update_issue(issue_id, state_id=in_progress)
        pending.update({"phase": "dispatch", "ack_id": ack_id}); state.data["pending"][issue_id] = pending; state.save()
        before = time.time()
        ok, output, detail = dispatch(issue, directive, dry_run, before, slugs)
        entry = {"issue": issue.get("identifier") or issue_id, "trigger_ids": pending["trigger_ids"], "ok": ok, "detail": detail}
        if ok and not dry_run:
            pending["phase"] = "result-comment"; state.save()
            result_marker = f"{BOT_MARKER}result:{issue_id}:{latest_event} -->"
            existing_result = next((c for c in comments if result_marker in str(c.get("body") or "")), None)
            if not existing_result:
                client.create_comment(issue_id, f"@ltp19941128\n\n{output}\n{result_marker}", parent_id=root.get("id") if root.get("id") else None)
            pending["phase"] = "owner-reassign"; state.save()
            client.update_issue(issue_id, assignee_id=OWNER_USER_ID, clear_delegate=True)
            state.data["pending"].pop(issue_id, None); state.data["issue_assignments"][issue_id] = assignment_key
            state.data["processed_comment_ids"].extend(x for x in pending["trigger_ids"] if x and x not in state.data["processed_comment_ids"])
            state.save()
        report.append(entry)
    return {"ok": True, "dry_run": dry_run, "triggers": report}


def serve_webhook(port: int, client: Any, state: State) -> None:
    secret = os.environ.get("LINEAR_WEBHOOK_SECRET", "")
    if not secret: raise SystemExit("LINEAR_WEBHOOK_SECRET is required")
    bridge = (client, state); seen: dict[str, float] = {}; max_age = 300
    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup(); self.connection.settimeout(5)
        def do_POST(self) -> None:
            try: length = int(self.headers.get("Content-Length", "-1"))
            except (TypeError, ValueError): self.send_error(400); return
            if length < 0 or length > 1_000_000: self.send_error(413); return
            try: body = self.rfile.read(length)
            except (TimeoutError, socket.timeout): self.send_error(408); return
            if len(body) != length or not verify_signature(secret, body, self.headers.get("Linear-Signature", "")): self.send_error(401); return
            try: payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError): self.send_error(400); return
            stamp = payload.get("webhookTimestamp") or payload.get("timestamp")
            if stamp is not None:
                try: stamp_num = float(stamp) / (1000 if float(stamp) > 10_000_000_000 else 1)
                except (TypeError, ValueError): self.send_error(400); return
                now = time.time()
                if abs(now - stamp_num) > max_age: self.send_error(408); return
                delivery = self.headers.get("Linear-Delivery") or hashlib.sha256(body).hexdigest()
                if delivery in seen and now - seen[delivery] < max_age: self.send_error(409); return
                seen[delivery] = now
            threading.Thread(target=process_once, args=bridge, daemon=True).start()
            self.send_response(202); self.end_headers()
        def log_message(self, *_: Any) -> None: pass
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler); server.daemon_threads = True; server.serve_forever()


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
