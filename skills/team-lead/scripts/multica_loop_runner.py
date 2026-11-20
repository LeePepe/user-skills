#!/usr/bin/env python3
"""0-Agent Script Precheck + Jev Fast-Thinking -> Wake-on-Actionable Autopilot Hub (`multica_loop_runner.py`).

Implements:
1. `verify-vpn`: Deterministic 5-point LAN / cross-network VPN remote access check
   (zero-localhost audit of `~/.multica`, Web UI & `/api/config` proxy reachability,
   CORS preflight across VPN/Safari/Electron origins, `/auth/send-code` + `/auth/verify-code`
   Cookie `SameSite` / non-Secure HTTP session exchange, and WebSocket `/ws` upgrade).
2. `sync-autopilots`: Idempotently ensures the canonical scheduled loops are registered
   in NAS Multica Autopilot UI (the single registry of automation loops).
3. `run-loop`: Executes the 3-step Wake-on-Actionable pipeline:
   - Step 1 (Layer 0): Deterministic collector + watermark diff (`SKIPPED_NO_DELTA` at $0 token cost).
   - Step 2 (Layer 1): Jev fast-thinking judgment (`jev_decide` `choice` + companion `noul` + `score`,
     with `>0.70` actionable, `<0.30` ignore, `0.30..0.70` deterministic fallback).
   - Step 3 (Layer 2): Wake-on-Actionable — either directly materializes/updates a Dual-Zone
     (`issue-brief` verified) Priority 3 Issue assigned to Owner, or triggers the registered
     Multica Autopilot (`multica autopilot trigger <id>`) when heavy Agent synthesis is needed.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import http.cookiejar
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_PATH = Path(__file__).resolve()
TEAM_LEAD_SCRIPTS_DIR = SCRIPT_PATH.parent
SKILLS_ROOT = SCRIPT_PATH.parents[2]
ISSUE_BRIEF_SCRIPT_PATH = SKILLS_ROOT / "issue-brief" / "scripts" / "verify_issue_format.py"
JEV_CLIENT_SCRIPT_PATH = TEAM_LEAD_SCRIPTS_DIR / "jev_client.py"

DEFAULT_SERVER_URL = "http://192.168.31.17:18080"
DEFAULT_APP_URL = "http://192.168.31.17:13000"
DEFAULT_WS_URL = "ws://192.168.31.17:18080/ws"
DEFAULT_VPN_GATEWAY_HOST = "10.9.0.1"
DEFAULT_VPN_GATEWAY_APP_URL = "http://10.9.0.1:13000"
DEFAULT_VPN_GATEWAY_API_URL = "http://10.9.0.1:18080"
DEFAULT_WORKSPACE_ID = "6f6b9a0b-4375-4ebc-a997-ff0c99b2645c"
DEFAULT_WORKSPACE_SLUG = "leepepe"
DEFAULT_OWNER_ID = "1a895cfa-3ed5-45f5-aaf1-a24b10b502fc"
DEFAULT_OWNER_EMAIL = "lee@multica.local"
DEFAULT_AGENT_ID = "d550a00a-1173-4bed-8bf3-621384cdfb10"
DEFAULT_DEV_CODE = "888888"
DEFAULT_TIMEZONE = "Asia/Shanghai"
CST = timezone(timedelta(hours=8))

LOCALHOST_RE = re.compile(r"(?i)(?:^|//)(?:localhost|127\.0\.0\.1|\[::1\])(?::|/|$)")


@dataclass(frozen=True)
class LoopSpec:
    loop_id: str
    aliases: tuple[str, ...]
    autopilot_title: str
    cron: str
    trigger_label: str
    delivery_mode: str  # "auto_fix_and_digest_only" | "dual_zone_issue" | "autopilot_trigger"
    issue_title_template: str
    default_status: str = "todo"
    default_priority: str = "medium"


CANONICAL_LOOP_SPECS: tuple[LoopSpec, ...] = (
    LoopSpec(
        loop_id="daily-brief",
        aliases=("daily_brief",),
        autopilot_title="Daily Brief Loop (0-Agent + Jev -> Dual-Zone Brief -> Owner)",
        cron="0 5 * * *",
        trigger_label="Daily CST 05:00 Personal Daily Brief",
        delivery_mode="dual_zone_issue",
        issue_title_template="[Daily Brief {date}] 昨日汇总与今日重点待办",
        default_status="todo",
        default_priority="medium",
    ),
    LoopSpec(
        loop_id="skills-roi-diagnostic",
        aliases=("skills-roi", "skills_roi"),
        autopilot_title="Daily Skills ROI Diagnostic Autopilot (Auto-Fix + Daily Brief Digest)",
        cron="20 4 * * *",
        trigger_label="Daily CST 04:20 Morning Skills ROI & Skill-Retro Diagnostic",
        delivery_mode="auto_fix_and_digest_only",
        issue_title_template="[P3 / Priority 3][Skills ROI] {date} 技能效能诊断与待优化清单",
        default_status="backlog",
        default_priority="medium",
    ),
    LoopSpec(
        loop_id="daily-learn-recommend",
        aliases=("daily-learn", "daily_learn", "knowledge-digest"),
        autopilot_title="Daily Knowledge Digest Autopilot (Workflow + Personal Two Tracks -> Owner)",
        cron="15 4 * * *",
        trigger_label="Daily CST Morning Two-Track Learning Topics",
        delivery_mode="dual_zone_issue",
        issue_title_template="[Daily Learn {date}] 今日双轨学习选题推荐",
        default_status="todo",
        default_priority="medium",
    ),
    LoopSpec(
        loop_id="skill-agent-daily-research",
        aliases=("skill-agent-research", "skill_agent_daily_research"),
        autopilot_title="Daily Skill Agent Architecture Research Autopilot (Workflow -> Daily Learn)",
        cron="30 4 * * *",
        trigger_label="Daily CST Workflow Skill & Agent Architecture Research",
        delivery_mode="dual_zone_issue",
        issue_title_template="[Skill Agent Research {date}] Workflow 架构候选日报",
        default_status="todo",
        default_priority="medium",
    ),
    LoopSpec(
        loop_id="aidash-retro-delta",
        aliases=("retro-discover", "retro_discover", "aidash-retro"),
        autopilot_title="Daily AIDash Digest & Retro Improvements Autopilot (Auto-Fix + Daily Brief Digest)",
        cron="0 4 * * *",
        trigger_label="Daily CST AIDash Digest & Retro Delta",
        delivery_mode="auto_fix_and_digest_only",
        issue_title_template="[P3 / Priority 3][AIDash & Retro] {date} 异常与工程卫生待办清单",
        default_status="todo",
        default_priority="medium",
    ),
    LoopSpec(
        loop_id="local-herdr-worktree-gc",
        aliases=("local_hygiene_patrol", "local-hygiene-patrol", "herdr-worktree-gc"),
        autopilot_title="Daily Local Herdr & Worktree Hygiene Patrol Autopilot (Auto-Fix + Daily Brief Digest)",
        cron="30 3 * * *",
        trigger_label="Daily CST 03:30 Local Herdr & Worktree Hygiene Patrol",
        delivery_mode="auto_fix_and_digest_only",
        issue_title_template="[P3 / Priority 3][Local Hygiene] {date} 本地环境与主仓卫生异常巡检清单",
        default_status="todo",
        default_priority="medium",
    ),
    LoopSpec(
        loop_id="spec-code-drift-patrol",
        aliases=("spec_code_drift_patrol", "loop-4-spec-code-drift"),
        autopilot_title="Daily Spec-Code Drift Patrol Autopilot (Auto-Fix + Daily Brief Digest)",
        cron="15 3 * * *",
        trigger_label="Daily CST 03:15 Spec-Code Drift Patrol",
        delivery_mode="auto_fix_and_digest_only",
        issue_title_template="[P3 / Priority 3][Spec-Code Drift] {date} 规格与代码一致性巡检清单",
        default_status="todo",
        default_priority="medium",
    ),
    LoopSpec(
        loop_id="backlog-grooming-and-push",
        aliases=("backlog_grooming_and_push", "backlog-groomer", "backlog_groomer"),
        autopilot_title="Daily Backlog Grooming & Top-3 Push Autopilot (Auto-Fix + Daily Brief Digest)",
        cron="45 4 * * *",
        trigger_label="Daily CST 04:45 Backlog Grooming & Top-3 Push",
        delivery_mode="auto_fix_and_digest_only",
        issue_title_template="[P3 / Priority 3][Backlog Grooming] {date} 待办去重与每日 Top 3 推荐顺推清单",
        default_status="todo",
        default_priority="medium",
    ),
    LoopSpec(
        loop_id="design-taste-evolution",
        aliases=("taste-evolution", "design_taste_evolution"),
        autopilot_title="Daily UI Design Taste Evolution Autopilot (Mac node -> Daily Brief Digest)",
        cron="50 4 * * *",
        trigger_label="Daily CST 04:50 UI Design Taste Evolution on Mac node",
        delivery_mode="auto_fix_and_digest_only",
        issue_title_template="[P3 / Priority 3][Taste Evolution] {date} UI 品味偏好与反模式候选",
        default_status="todo",
        default_priority="medium",
    ),
)

LOOP_SPECS: tuple[LoopSpec, ...] = CANONICAL_LOOP_SPECS

LOOP_BY_KEY: dict[str, LoopSpec] = {}
for _spec in CANONICAL_LOOP_SPECS:
    LOOP_BY_KEY[_spec.loop_id] = _spec
    for _alias in _spec.aliases:
        LOOP_BY_KEY[_alias] = _spec


def resolve_loop_spec(loop_name: str) -> LoopSpec:
    key = str(loop_name or "").strip().lower()
    if key in LOOP_BY_KEY:
        return LOOP_BY_KEY[key]
    return LoopSpec(
        loop_id=key or "custom-loop",
        aliases=(),
        autopilot_title=f"Custom Loop ({key or 'custom-loop'})",
        cron="0 9 * * *",
        trigger_label=f"Scheduled {key or 'custom-loop'}",
        delivery_mode="dual_zone_issue",
        issue_title_template=f"[P3 / Priority 3][{key or 'custom-loop'}] {{date}} 待处理事项",
        default_status="todo",
        default_priority="medium",
    )


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    old_flag = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old_flag
    return module


def _load_issue_brief() -> Any:
    return _load_module("multica_loop_issue_brief", ISSUE_BRIEF_SCRIPT_PATH)


def _load_jev_client() -> Any:
    return _load_module("multica_loop_jev_client", JEV_CLIENT_SCRIPT_PATH)


def _resolve_multica_bin(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env_bin = os.environ.get("MULTICA_BIN")
    if env_bin:
        return env_bin
    if Path("/opt/homebrew/bin/multica").is_file():
        return "/opt/homebrew/bin/multica"
    return shutil.which("multica") or "/opt/homebrew/bin/multica"


def _default_multica_runner(
    args: Sequence[str],
    *,
    stdin_text: str | None = None,
    multica_bin: str | None = None,
    server_url: str | None = None,
    workspace_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    binary = _resolve_multica_bin(multica_bin)
    cmd = [binary]
    if server_url:
        cmd.extend(["--server-url", server_url])
    if workspace_id:
        cmd.extend(["--workspace-id", workspace_id])
    cmd.extend(args)
    env = dict(os.environ)
    if server_url:
        env["MULTICA_SERVER_URL"] = server_url
    if workspace_id:
        env["MULTICA_WORKSPACE_ID"] = workspace_id
    return subprocess.run(
        cmd,
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _parse_json_stdout(proc: subprocess.CompletedProcess[str], context: str) -> Any:
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"multica {context} failed (exit {proc.returncode}): {detail}")
    raw = (proc.stdout or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"multica {context} returned invalid JSON: {raw[:300]}") from exc


def _default_digest_buffer_path() -> Path:
    override = os.environ.get("MULTICA_DAILY_DIGEST_BUFFER_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "multica-loops" / "daily-digest-buffer.json"


def load_daily_digest_buffer(path: Path | None = None) -> dict[str, Any]:
    target = path or _default_digest_buffer_path()
    if not target.is_file():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {"version": 1, "entries": {}}


def record_daily_digest_entry(
    *,
    loop_id: str,
    target_date: str,
    collected: Mapping[str, Any],
    actionable: bool,
    buffer_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    target = buffer_path or _default_digest_buffer_path()
    entry = {
        "loop_id": loop_id,
        "date": target_date,
        "recorded_at": datetime.now(CST).isoformat(timespec="seconds"),
        "actionable": bool(actionable),
        "has_delta": bool(collected.get("has_delta")),
        "delta_count": int(collected.get("delta_count") or 0),
        "severity": str(collected.get("severity") or "low"),
        "watermark": str(collected.get("watermark") or ""),
        "summary": str(collected.get("summary") or ""),
        "items": list(collected.get("items") or [])[:6],
        "auto_gc": collected.get("auto_gc") or (collected.get("gc") if isinstance(collected.get("gc"), Mapping) else None),
        "auto_fixed": collected.get("auto_fixed"),
        "skill_retro": collected.get("skill_retro") if isinstance(collected.get("skill_retro"), Mapping) else None,
    }
    if not dry_run:
        buf = load_daily_digest_buffer(target)
        entries = buf.setdefault("entries", {})
        day_bucket = entries.setdefault(target_date, {})
        if isinstance(day_bucket, dict):
            day_bucket[loop_id] = entry
        buf["updated_at"] = datetime.now(CST).isoformat(timespec="seconds")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(buf, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "action": "dry_run_buffered_to_daily_digest" if dry_run else "buffered_to_daily_digest",
        "buffer_path": str(target),
        "loop_id": loop_id,
        "date": target_date,
        "entry": entry,
    }


def _default_watermark_path() -> Path:
    override = os.environ.get("MULTICA_LOOP_STATE_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "multica-loop-runner" / "watermarks.json"


def load_watermarks(path: Path | None = None) -> dict[str, Any]:
    target = path or _default_watermark_path()
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_watermarks(state: Mapping[str, Any], path: Path | None = None) -> None:
    target = path or _default_watermark_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(state), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Deliverable 1: Deterministic 5-Point Cross-Network / VPN Verification
# ---------------------------------------------------------------------------

def audit_local_multica_configs(multica_dir: Path | None = None) -> dict[str, Any]:
    base_dir = (multica_dir or (Path.home() / ".multica")).expanduser()
    desktop_path = base_dir / "desktop.json"
    config_path = base_dir / "config.json"
    findings: list[str] = []
    checked_files: dict[str, Any] = {}

    for label, path, required_keys in (
        ("desktop.json", desktop_path, ("apiUrl", "wsUrl", "appUrl")),
        ("config.json", config_path, ("server_url", "app_url")),
    ):
        if not path.is_file():
            findings.append(f"missing {label} at {path}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            findings.append(f"unreadable {label}: {exc}")
            continue
        if not isinstance(payload, dict):
            findings.append(f"{label} must be a JSON object")
            continue
        redacted = {k: ("***" if "token" in k.lower() else v) for k, v in payload.items()}
        checked_files[label] = redacted
        for key in required_keys:
            val = str(payload.get(key) or "").strip()
            if not val:
                findings.append(f"{label} missing {key}")
            elif LOCALHOST_RE.search(val):
                findings.append(f"{label}.{key} points to localhost ({val})")

    return {
        "ok": len(findings) == 0,
        "checked_files": checked_files,
        "findings": findings,
    }


def _default_http_requester(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 8.0,
) -> tuple[int, dict[str, list[str]], str]:
    req = urllib.request.Request(url, data=body, headers=dict(headers or {}), method=method)
    # Bypass system HTTP proxies for LAN/OpenVPN IPs and preserve Set-Cookie headers across redirects
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPRedirectHandler(),
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = int(resp.status)
            raw_headers: dict[str, list[str]] = {}
            for k, v in resp.headers.items():
                raw_headers.setdefault(k.lower(), []).append(v)
            text = resp.read().decode("utf-8", errors="replace")
            return status, raw_headers, text
    except urllib.error.HTTPError as exc:
        raw_headers = {}
        if exc.headers is not None:
            for k, v in exc.headers.items():
                raw_headers.setdefault(k.lower(), []).append(v)
        text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return int(exc.code), raw_headers, text


def _default_ws_prober(
    ws_url: str,
    *,
    origin: str | None = None,
    cookie_header: str | None = None,
    timeout: float = 6.0,
) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(ws_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/ws"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {ws_key}",
        "Sec-WebSocket-Version: 13",
    ]
    if origin:
        lines.append(f"Origin: {origin}")
    if cookie_header:
        lines.append(f"Cookie: {cookie_header}")
    raw_req = "\r\n".join(lines) + "\r\n\r\n"

    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(raw_req.encode("utf-8"))
        resp_bytes = sock.recv(4096)
    resp_text = resp_bytes.decode("utf-8", errors="replace")
    status_line = resp_text.splitlines()[0] if resp_text.splitlines() else ""
    parts = status_line.split(" ", 2)
    code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
    return {
        "ok": code == 101,
        "status_code": code,
        "status_line": status_line,
        "origin": origin,
    }


def detect_active_openvpn_tunnel(
    *,
    ifconfig_output: str | None = None,
) -> list[dict[str, str]]:
    """Detect active OpenVPN utun/tun interfaces on 10.9.0.x or 10.8.0.x."""
    if ifconfig_output is None:
        proc = subprocess.run(["ifconfig"], capture_output=True, text=True, check=False)
        ifconfig_output = proc.stdout or ""
    found: list[dict[str, str]] = []
    current_iface = ""
    hdr_re = re.compile(r"^([a-zA-Z0-9_]+):\s+flags=")
    inet_re = re.compile(r"^\s*inet\s+(10\.(?:9|8)\.0\.\d+)(?:\s+-->\s+(\d+\.\d+\.\d+\.\d+))?")
    for line in ifconfig_output.splitlines():
        m_hdr = hdr_re.match(line)
        if m_hdr:
            current_iface = m_hdr.group(1)
            continue
        m_inet = inet_re.match(line)
        if m_inet and current_iface:
            ip_val = m_inet.group(1)
            subnet_prefix = ".".join(ip_val.split(".")[:3])
            peer_val = m_inet.group(2) or f"{subnet_prefix}.1"
            found.append(
                {
                    "interface": current_iface,
                    "client_ip": ip_val,
                    "gateway_ip": peer_val,
                }
            )
    return found


def _verify_endpoint_bundle(
    *,
    app_url: str,
    server_url: str,
    ws_url: str | None,
    workspace_slug: str,
    email: str,
    verification_code: str,
    perform_login_code_exchange: bool = True,
    shared_auth_cookie: str = "",
    http_requester: Callable[..., tuple[int, dict[str, list[str]], str]],
    ws_prober: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Verify HTTP 200, CORS preflight, 888888 login Cookie SameSite/non-Secure, and WS 101 across a VPN/bridge endpoint."""
    clean_app = app_url.rstrip("/")
    clean_server = server_url.rstrip("/")
    parsed_server = urllib.parse.urlparse(clean_server)
    resolved_ws_base = (ws_url or f"{'wss' if parsed_server.scheme == 'https' else 'ws'}://{parsed_server.netloc}/ws").rstrip("/")
    findings: list[str] = []

    # 1. Web UI HTTP 200 + /api/config + /health
    try:
        login_st, _, _ = http_requester(f"{clean_app}/login", method="GET")
    except OSError as exc:
        login_st = 0
        findings.append(f"{clean_app}/login unreachable: {exc}")
    else:
        if login_st != 200:
            findings.append(f"{clean_app}/login returned HTTP {login_st}")

    try:
        cfg_st, _, _ = http_requester(f"{clean_app}/api/config", method="GET")
    except OSError as exc:
        cfg_st = 0
        findings.append(f"{clean_app}/api/config unreachable: {exc}")
    else:
        if cfg_st != 200:
            findings.append(f"{clean_app}/api/config returned HTTP {cfg_st}")

    try:
        health_st, _, _ = http_requester(f"{clean_server}/health", method="GET")
    except OSError as exc:
        health_st = 0
        findings.append(f"{clean_server}/health unreachable: {exc}")
    else:
        if health_st != 200:
            findings.append(f"{clean_server}/health returned HTTP {health_st}")

    # 2. CORS preflight (Origin = clean_app -> Access-Control-Allow-Origin = clean_app)
    cors_st = 0
    allow_origin = ""
    try:
        cors_st, cors_hdrs, _ = http_requester(
            f"{clean_server}/auth/send-code",
            method="OPTIONS",
            headers={
                "Origin": clean_app,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,authorization,x-csrf-token",
            },
        )
        allow_origin = (cors_hdrs.get("access-control-allow-origin") or [""])[0]
    except OSError as exc:
        findings.append(f"CORS preflight to {clean_server}/auth/send-code failed: {exc}")
    else:
        if not (cors_st in (200, 204) and allow_origin in (clean_app, "*")):
            findings.append(
                f"CORS preflight rejected Origin={clean_app} on {clean_server} (allow_origin={allow_origin!r}, status={cors_st})"
            )

    # 3. Auth send-code + verify-code (888888) & Cookie SameSite / non-Secure over HTTP
    send_st = 0
    ver_st = 0
    ws_me_st = 0
    auth_cookie_val = shared_auth_cookie
    samesite_policy = "reused_from_gateway" if (not perform_login_code_exchange and shared_auth_cookie) else ""
    try:
        if perform_login_code_exchange:
            for attempt in range(2):
                send_st, _, send_body = http_requester(
                    f"{clean_app}/auth/send-code",
                    method="POST",
                    headers={"Content-Type": "application/json", "Origin": clean_app},
                    body=json.dumps({"email": email}).encode("utf-8"),
                )
                if send_st == 429 and attempt == 0 and http_requester is _default_http_requester:
                    # Wait out Multica's 60s per-email send-code cooldown if an earlier probe just requested a code
                    import time as _time
                    _time.sleep(61.0)
                    continue
                break
            if send_st != 200:
                findings.append(f"{clean_app}/auth/send-code failed with HTTP {send_st}: {send_body[:120]}")

            ver_st, ver_hdrs, ver_body = http_requester(
                f"{clean_app}/auth/verify-code",
                method="POST",
                headers={"Content-Type": "application/json", "Origin": clean_app},
                body=json.dumps({"email": email, "code": verification_code}).encode("utf-8"),
            )
            if ver_st != 200:
                findings.append(f"{clean_app}/auth/verify-code failed with HTTP {ver_st}: {ver_body[:120]}")
            auth_cookie_val = ""
            is_http = clean_app.lower().startswith("http://")
            for raw_sc in ver_hdrs.get("set-cookie") or []:
                lower_sc = raw_sc.lower()
                if raw_sc.startswith("multica_auth="):
                    auth_cookie_val = raw_sc.split(";", 1)[0].split("=", 1)[1]
                    ss_match = re.search(r"samesite=([a-z]+)", lower_sc)
                    samesite_policy = ss_match.group(1) if ss_match else ""
                    if samesite_policy not in {"lax", "strict"}:
                        findings.append(f"multica_auth unexpected SameSite={samesite_policy!r} on {clean_app}")
                    if is_http and "; secure" in lower_sc:
                        findings.append(f"multica_auth sets Secure over plain HTTP on {clean_app}")
                    if "domain=" in lower_sc:
                        dom_m = re.search(r"domain=([^;]+)", lower_sc)
                        if dom_m and re.match(r"^\.?\d+\.\d+\.\d+\.\d+$", dom_m.group(1).strip()):
                            findings.append(f"multica_auth sets IP literal Domain={dom_m.group(1)} on {clean_app}")
            if ver_st == 200 and not auth_cookie_val:
                findings.append(f"missing multica_auth Set-Cookie on {clean_app}/auth/verify-code")

        if auth_cookie_val:
            ws_me_st, _, ws_me_body = http_requester(
                f"{clean_app}/api/workspaces",
                method="GET",
                headers={"Cookie": f"multica_auth={auth_cookie_val}", "Origin": clean_app},
            )
            if ws_me_st != 200:
                findings.append(f"Cookie-authenticated {clean_app}/api/workspaces returned HTTP {ws_me_st}: {ws_me_body[:120]}")
    except OSError as exc:
        findings.append(f"Auth flow on {clean_app} failed: {exc}")

    # 4. WebSocket 101 Switching Protocols on ws://<gateway>:18080/ws and same-origin :13000/ws
    cookie_hdr_opt = f"multica_auth={auth_cookie_val}" if auth_cookie_val else None
    direct_ws_url = f"{resolved_ws_base}?workspace_slug={urllib.parse.quote(workspace_slug)}"
    parsed_app = urllib.parse.urlparse(clean_app)
    app_ws_url = f"{'wss' if parsed_app.scheme == 'https' else 'ws'}://{parsed_app.netloc}/ws?workspace_slug={urllib.parse.quote(workspace_slug)}"

    ws_results: dict[str, Any] = {}
    for label, target_ws in (
        ("api_ws_upgrade", direct_ws_url),
        ("web_proxy_ws_upgrade", app_ws_url),
    ):
        try:
            res = ws_prober(target_ws, origin=clean_app, cookie_header=cookie_hdr_opt)
        except OSError as exc:
            res = {"ok": False, "status_code": 0, "error": str(exc), "origin": clean_app}
        ws_results[label] = res
        if not res.get("ok"):
            findings.append(f"WebSocket upgrade failed for {label} ({target_ws}, Origin={clean_app}): {res}")

    return {
        "ok": len(findings) == 0,
        "app_url": clean_app,
        "server_url": clean_server,
        "ws_url": resolved_ws_base,
        "login_status": login_st,
        "api_config_status": cfg_st,
        "health_status": health_st,
        "cors_allow_origin": allow_origin,
        "verify_code_status": ver_st,
        "cookie_samesite": samesite_policy,
        "cookie_workspaces_status": ws_me_st,
        "has_auth_cookie": bool(auth_cookie_val),
        "_auth_cookie": auth_cookie_val,
        "websocket": ws_results,
        "findings": findings,
    }


def verify_vpn_access(
    *,
    server_url: str = DEFAULT_SERVER_URL,
    app_url: str = DEFAULT_APP_URL,
    ws_url: str | None = None,
    vpn_gateway_host: str | None = None,
    vpn_gateway_url: str | None = None,
    vpn_api_url: str | None = None,
    vpn_ws_url: str | None = None,
    auto_probe_vpn_gateway: bool | None = None,
    ifconfig_output: str | None = None,
    workspace_slug: str = DEFAULT_WORKSPACE_SLUG,
    email: str = DEFAULT_OWNER_EMAIL,
    verification_code: str = DEFAULT_DEV_CODE,
    multica_dir: Path | None = None,
    http_requester: Callable[..., tuple[int, dict[str, list[str]], str]] = _default_http_requester,
    ws_prober: Callable[..., dict[str, Any]] = _default_ws_prober,
) -> dict[str, Any]:
    clean_server = server_url.rstrip("/")
    clean_app = app_url.rstrip("/")
    parsed_server = urllib.parse.urlparse(clean_server)
    resolved_ws_base = (ws_url or f"{'wss' if parsed_server.scheme == 'https' else 'ws'}://{parsed_server.netloc}/ws").rstrip("/")

    checks: dict[str, dict[str, Any]] = {}

    # Check 1: Local ~/.multica zero-localhost audit
    checks["desktop_zero_localhost"] = audit_local_multica_configs(multica_dir)

    # Check 2: Web UI (:13000) & Next.js /api/config + /health runtime rewrite
    web_findings: list[str] = []
    login_status, _, login_html = http_requester(f"{clean_app}/login", method="GET")
    if login_status != 200:
        web_findings.append(f"{clean_app}/login returned HTTP {login_status}")
    if "ws://192.168.31.17:18080/ws" in login_html or "ws://localhost" in login_html:
        # Check if hardcoded cross-port or localhost wsUrl was injected into RSC payload
        if "ws://localhost:8080" not in login_html and "ws://192.168.31.17:18080/ws" in login_html:
            web_findings.append("Web /login HTML still hardcodes cross-port ws://192.168.31.17:18080/ws instead of dynamic same-origin /ws")
    cfg_status, cfg_headers, cfg_body = http_requester(f"{clean_app}/api/config", method="GET")
    if cfg_status != 200:
        web_findings.append(f"{clean_app}/api/config rewrite returned HTTP {cfg_status}")
    health_status, _, _ = http_requester(f"{clean_server}/health", method="GET")
    if health_status != 200:
        web_findings.append(f"{clean_server}/health returned HTTP {health_status}")
    checks["web_and_proxy_reachability"] = {
        "ok": len(web_findings) == 0,
        "login_status": login_status,
        "proxy_api_config_status": cfg_status,
        "server_health_status": health_status,
        "proxy_rewrite_header": (cfg_headers.get("x-middleware-rewrite") or [None])[0],
        "findings": web_findings,
    }

    # Check 3: CORS preflight across LAN, mDNS/VPN hostnames, and Electron/WebView origins
    cors_origins = (
        clean_app,
        "http://nas.local:13000",
        "file://",
    )
    cors_findings: list[str] = []
    cors_results: dict[str, Any] = {}
    for origin in cors_origins:
        st, hdrs, _ = http_requester(
            f"{clean_server}/auth/send-code",
            method="OPTIONS",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,authorization,x-csrf-token",
            },
        )
        allow_origin = (hdrs.get("access-control-allow-origin") or [""])[0]
        allow_creds = (hdrs.get("access-control-allow-credentials") or [""])[0]
        matched = st in (200, 204) and allow_origin in (origin, "*")
        cors_results[origin] = {
            "status": st,
            "allow_origin": allow_origin,
            "allow_credentials": allow_creds,
            "ok": matched,
        }
        if not matched:
            cors_findings.append(f"CORS preflight rejected Origin={origin} (allow_origin={allow_origin!r}, status={st})")
    checks["cors_preflight"] = {
        "ok": len(cors_findings) == 0,
        "origins": cors_results,
        "findings": cors_findings,
    }

    # Check 4: Auth send-code + verify-code via Web proxy (:13000) & Cookie SameSite / non-Secure over HTTP
    auth_findings: list[str] = []
    send_payload = json.dumps({"email": email}).encode("utf-8")
    send_status = 0
    send_body = ""
    for attempt in range(2):
        send_status, _, send_body = http_requester(
            f"{clean_app}/auth/send-code",
            method="POST",
            headers={"Content-Type": "application/json", "Origin": clean_app},
            body=send_payload,
        )
        if send_status == 429 and attempt == 0 and http_requester is _default_http_requester:
            import time as _time
            _time.sleep(61.0)
            continue
        break
    if send_status != 200:
        auth_findings.append(f"/auth/send-code failed with HTTP {send_status}: {send_body[:160]}")

    verify_payload = json.dumps({"email": email, "code": verification_code}).encode("utf-8")
    ver_status, ver_headers, ver_body = http_requester(
        f"{clean_app}/auth/verify-code",
        method="POST",
        headers={"Content-Type": "application/json", "Origin": clean_app},
        body=verify_payload,
    )
    set_cookies = ver_headers.get("set-cookie") or []
    auth_cookie_val = ""
    csrf_cookie_val = ""
    is_http = clean_app.lower().startswith("http://")
    for raw_sc in set_cookies:
        lower_sc = raw_sc.lower()
        if raw_sc.startswith("multica_auth="):
            auth_cookie_val = raw_sc.split(";", 1)[0].split("=", 1)[1]
            if is_http and "; secure" in lower_sc:
                auth_findings.append("multica_auth cookie sets Secure over plain HTTP (breaks iOS Safari / VPN HTTP)")
            if "domain=" in lower_sc:
                domain_part = re.search(r"domain=([^;]+)", lower_sc)
                if domain_part and re.match(r"^\.?\d+\.\d+\.\d+\.\d+$", domain_part.group(1).strip()):
                    auth_findings.append(f"multica_auth sets IP literal Domain={domain_part.group(1)} (dropped by RFC 6265)")
        elif raw_sc.startswith("multica_csrf_session=") or raw_sc.startswith("multica_csrf="):
            if not csrf_cookie_val:
                csrf_cookie_val = raw_sc.split(";", 1)[0].split("=", 1)[1]

    jwt_token = ""
    if ver_status != 200:
        auth_findings.append(f"/auth/verify-code failed with HTTP {ver_status}: {ver_body[:160]}")
    else:
        try:
            ver_json = json.loads(ver_body)
            jwt_token = str(ver_json.get("token") or "")
        except ValueError:
            auth_findings.append("/auth/verify-code returned invalid JSON")
        if not auth_cookie_val:
            auth_findings.append("missing multica_auth Set-Cookie on /auth/verify-code")

    # Verify authenticated session read (/api/issues or /api/workspaces) via cookie on :13000 (simulating iPhone Safari)
    me_status = 0
    if auth_cookie_val:
        cookie_hdr = f"multica_auth={auth_cookie_val}"
        me_status, _, me_body = http_requester(
            f"{clean_app}/api/workspaces",
            method="GET",
            headers={"Cookie": cookie_hdr, "Origin": clean_app},
        )
        if me_status != 200:
            auth_findings.append(f"Authenticated /api/workspaces via cookie on {clean_app} returned HTTP {me_status}: {me_body[:120]}")

    checks["auth_and_cookie_session"] = {
        "ok": len(auth_findings) == 0,
        "send_code_status": send_status,
        "verify_code_status": ver_status,
        "cookie_authenticated_workspaces_status": me_status,
        "has_auth_cookie": bool(auth_cookie_val),
        "has_csrf_cookie": bool(csrf_cookie_val),
        "has_bearer_token": bool(jwt_token),
        "set_cookie_count": len(set_cookies),
        "findings": auth_findings,
    }

    # Check 5: WebSocket /ws upgrade on both :18080 (Desktop Electron `file://` & VPN) and :13000 (Safari same-origin proxy)
    ws_findings: list[str] = []
    cookie_hdr_opt = f"multica_auth={auth_cookie_val}" if auth_cookie_val else None
    parsed_app = urllib.parse.urlparse(clean_app)
    app_ws_url = f"{'wss' if parsed_app.scheme == 'https' else 'ws'}://{parsed_app.netloc}/ws?workspace_slug={urllib.parse.quote(workspace_slug)}"
    direct_ws_url = f"{resolved_ws_base}?workspace_slug={urllib.parse.quote(workspace_slug)}"

    ws_probes: dict[str, Any] = {}
    for probe_name, target_ws, origin in (
        ("desktop_electron_file_origin", direct_ws_url, "file://"),
        ("safari_web_proxy_same_origin", app_ws_url, clean_app),
        ("lan_or_vpn_hostname_origin", direct_ws_url, "http://nas.local:13000"),
    ):
        try:
            res = ws_prober(target_ws, origin=origin, cookie_header=cookie_hdr_opt)
        except OSError as exc:
            res = {"ok": False, "status_code": 0, "error": str(exc), "origin": origin}
        ws_probes[probe_name] = res
        if not res.get("ok"):
            ws_findings.append(f"WebSocket upgrade failed for {probe_name} ({target_ws}, Origin={origin}): {res}")

    checks["websocket_upgrade"] = {
        "ok": len(ws_findings) == 0,
        "probes": ws_probes,
        "findings": ws_findings,
    }

    # Check 6: Option A OpenVPN Internal Gateway Bridge (10.9.0.1 / 10.8.0.1 -> Mac mini 10.9.0.6 -> NAS)
    explicit_vpn_requested = any(
        val is not None for val in (vpn_gateway_host, vpn_gateway_url, vpn_api_url, vpn_ws_url)
    )
    should_auto_probe = (
        auto_probe_vpn_gateway
        if auto_probe_vpn_gateway is not None
        else (explicit_vpn_requested or http_requester is _default_http_requester)
    )
    tunnels = detect_active_openvpn_tunnel(ifconfig_output=ifconfig_output) if should_auto_probe else []

    if explicit_vpn_requested or tunnels:
        gw_host = vpn_gateway_host or (tunnels[0]["gateway_ip"] if tunnels else DEFAULT_VPN_GATEWAY_HOST)
        gw_app = vpn_gateway_url or f"http://{gw_host}:13000"
        gw_api = vpn_api_url or f"http://{gw_host}:18080"
        gw_bundle = _verify_endpoint_bundle(
            app_url=gw_app,
            server_url=gw_api,
            ws_url=vpn_ws_url,
            workspace_slug=workspace_slug,
            email=email,
            verification_code=verification_code,
            perform_login_code_exchange=True,
            http_requester=http_requester,
            ws_prober=ws_prober,
        )
        gw_cookie = str(gw_bundle.pop("_auth_cookie", "") or auth_cookie_val)

        peer_bundles: dict[str, Any] = {}
        peer_findings: list[str] = []
        for tun in tunnels:
            peer_ip = tun["client_ip"]
            if peer_ip and peer_ip != gw_host:
                p_bundle = _verify_endpoint_bundle(
                    app_url=f"http://{peer_ip}:13000",
                    server_url=f"http://{peer_ip}:18080",
                    ws_url=f"ws://{peer_ip}:18080/ws",
                    workspace_slug=workspace_slug,
                    email=email,
                    verification_code=verification_code,
                    perform_login_code_exchange=False,
                    shared_auth_cookie=gw_cookie,
                    http_requester=http_requester,
                    ws_prober=ws_prober,
                )
                p_bundle.pop("_auth_cookie", None)
                peer_bundles[peer_ip] = p_bundle
                if not p_bundle["ok"]:
                    peer_findings.extend(p_bundle["findings"])

        combined_vpn_findings = list(gw_bundle["findings"]) + peer_findings
        checks["openvpn_gateway_bridge"] = {
            "ok": len(combined_vpn_findings) == 0,
            "detected_tunnels": tunnels,
            "gateway": gw_bundle,
            "client_peers": peer_bundles,
            "findings": combined_vpn_findings,
        }

    overall_ok = all(section.get("ok") for section in checks.values())
    return {
        "ok": overall_ok,
        "server_url": clean_server,
        "app_url": clean_app,
        "ws_url": resolved_ws_base,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Deliverable 2: 0-Agent Script Precheck + Jev -> Wake-on-Actionable Hub
# ---------------------------------------------------------------------------

def build_loop_autopilot_description(spec: LoopSpec) -> str:
    runner_rel = "$HOME/Development/user-skills/skills/m2-dispatcher/scripts/multica_loop_runner.py"
    if spec.loop_id == "design-taste-evolution":
        execution_note = (
            "   Run this collector on the Mac node with access to the local handoff receipts; "
            "do not use the NAS filesystem as the evidence source.\n"
        )
    else:
        execution_note = ""
    return (
        f"# {spec.autopilot_title}\n\n"
        f"- **Loop ID**: `{spec.loop_id}`\n"
        f"- **Execution Architecture**: `Layer 0 Deterministic Script Precheck -> Layer 1 Jev Fast-Thinking -> Layer 2 Wake-on-Actionable ({spec.delivery_mode})`\n\n"
        "## Execution Protocol\n\n"
        "1. **Normal Scheduled Tick (0-Agent Precheck)**:\n"
        "   ```bash\n"
        f'   python3 "{runner_rel}" run-loop --loop "{spec.loop_id}" --json\n'
        "   ```\n"
        + execution_note
        + "2. **Agent Synthesis Fallback (only when triggered by `multica_loop_runner.py` or manual run)**:\n"
        "   Inspect `/tmp/multica-loop-" + spec.loop_id + "-collect.json`, synthesize root causes, and file/update a Dual-Zone (`issue-brief` verified) issue assigned to Owner (`lee@multica.local`).\n"
    )


def sync_autopilots(
    *,
    loop_id: str | None = None,
    server_url: str = DEFAULT_SERVER_URL,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    agent_id: str = DEFAULT_AGENT_ID,
    subscriber_id: str = DEFAULT_OWNER_ID,
    tz_name: str = DEFAULT_TIMEZONE,
    dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _default_multica_runner,
    multica_bin: str | None = None,
) -> dict[str, Any]:
    selected_specs = CANONICAL_LOOP_SPECS
    if loop_id is not None:
        requested = str(loop_id).strip().lower()
        selected_specs = tuple(spec for spec in CANONICAL_LOOP_SPECS if spec.loop_id == requested or requested in spec.aliases)
        if not selected_specs:
            raise ValueError(f"Unknown canonical loop: {loop_id}")

    if dry_run:
        return {
            "ok": True,
            "action": "dry_run",
            "loops": [
                {
                    "loop_id": s.loop_id,
                    "title": s.autopilot_title,
                    "cron": s.cron,
                    "timezone": tz_name,
                    "delivery_mode": s.delivery_mode,
                }
                for s in selected_specs
            ],
        }

    list_proc = runner(
        ["autopilot", "list", "--output", "json"],
        multica_bin=multica_bin,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    listed = _parse_json_stdout(list_proc, "autopilot list")
    existing_list = listed.get("autopilots", []) if isinstance(listed, dict) else (listed if isinstance(listed, list) else [])

    synced: list[dict[str, Any]] = []
    for spec in selected_specs:
        matched = None
        for ap in existing_list:
            if not isinstance(ap, dict):
                continue
            title_str = str(ap.get("title") or "")
            desc_str = str(ap.get("description") or "")
            if (
                title_str == spec.autopilot_title
                or f"`{spec.loop_id}`" in desc_str
                or (spec.loop_id == "daily-learn-recommend" and "Daily Knowledge Digest Autopilot" in title_str)
                or (spec.loop_id == "aidash-retro-delta" and "Daily AIDash Digest & Retro" in title_str)
                or (spec.loop_id == "skills-roi-diagnostic" and "Skills ROI" in title_str)
                or (spec.loop_id == "local-herdr-worktree-gc" and "Local Herdr & Worktree Hygiene Patrol" in title_str)
                or (spec.loop_id == "spec-code-drift-patrol" and "Spec-Code Drift Patrol" in title_str)
                or (spec.loop_id == "backlog-grooming-and-push" and "Backlog Grooming & Top-3 Push" in title_str)
                or (spec.loop_id == "design-taste-evolution" and ("UI Design Taste Evolution" in title_str or "Taste Evolution" in title_str))
            ):
                matched = ap
                break

        description = matched.get("description") if matched and spec.loop_id in {"daily-learn-recommend", "aidash-retro-delta"} else build_loop_autopilot_description(spec)
        if matched is not None:
            autopilot_id = str(matched["id"])
            upd_args = [
                "autopilot",
                "update",
                autopilot_id,
                "--agent",
                agent_id,
                "--mode",
                "run_only",
                "--status",
                "active",
                "--subscriber",
                subscriber_id,
                "--output",
                "json",
            ]
            if spec.loop_id not in {"daily-learn-recommend", "aidash-retro-delta"}:
                upd_args.extend(["--title", spec.autopilot_title, "--description", str(description)])
            upd_proc = runner(
                upd_args,
                multica_bin=multica_bin,
                server_url=server_url,
                workspace_id=workspace_id,
            )
            ap_obj = _parse_json_stdout(upd_proc, "autopilot update")
            ap_action = "updated"
        else:
            create_proc = runner(
                [
                    "autopilot",
                    "create",
                    "--title",
                    spec.autopilot_title,
                    "--agent",
                    agent_id,
                    "--mode",
                    "run_only",
                    "--description",
                    str(description),
                    "--subscriber",
                    subscriber_id,
                    "--output",
                    "json",
                ],
                multica_bin=multica_bin,
                server_url=server_url,
                workspace_id=workspace_id,
            )
            ap_obj = _parse_json_stdout(create_proc, "autopilot create")
            autopilot_id = str(ap_obj.get("id") or (ap_obj.get("autopilot") or {}).get("id") or "")
            if not autopilot_id:
                raise RuntimeError(f"Could not extract autopilot id for {spec.loop_id}: {ap_obj}")
            ap_action = "created"

        trig_proc = runner(
            ["autopilot", "trigger-list", autopilot_id, "--output", "json"],
            multica_bin=multica_bin,
            server_url=server_url,
            workspace_id=workspace_id,
        )
        trig_data = _parse_json_stdout(trig_proc, "autopilot trigger-list")
        triggers = trig_data.get("triggers", []) if isinstance(trig_data, dict) else (trig_data if isinstance(trig_data, list) else [])
        sched = next((t for t in triggers if isinstance(t, dict) and str(t.get("kind") or "schedule") == "schedule"), None)
        if sched is None:
            runner(
                [
                    "autopilot",
                    "trigger-add",
                    autopilot_id,
                    "--kind",
                    "schedule",
                    "--cron",
                    spec.cron,
                    "--timezone",
                    tz_name,
                    "--label",
                    spec.trigger_label,
                    "--output",
                    "json",
                ],
                multica_bin=multica_bin,
                server_url=server_url,
                workspace_id=workspace_id,
            )
            trig_action = "created"
        else:
            trig_action = "kept"

        synced.append(
            {
                "loop_id": spec.loop_id,
                "autopilot_id": autopilot_id,
                "title": matched.get("title") if matched else spec.autopilot_title,
                "action": ap_action,
                "trigger_action": trig_action,
            }
        )

    return {"ok": True, "workspace_id": workspace_id, "autopilots": synced}


def _compute_digest_signature(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _decode_mock_for_routing(mock: Any) -> Any:
    if isinstance(mock, (str, Path)):
        try:
            candidate = Path(str(mock)).expanduser()
            raw = candidate.read_text(encoding="utf-8") if candidate.is_file() else str(mock)
            return json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return mock
    return mock


def _mock_answer_keys(mock: Any) -> set[str]:
    mock = _decode_mock_for_routing(mock)
    if isinstance(mock, Mapping):
        keys = set(str(key) for key in mock)
        answers = mock.get("answers")
        if isinstance(answers, Mapping):
            keys.update(str(key) for key in answers)
        for key in ("default", "collector", "taste", "runner"):
            if key in mock:
                keys.update(_mock_answer_keys(mock[key]))
        return keys
    if isinstance(mock, list):
        merged: set[str] = set()
        for item in mock:
            merged.update(_mock_answer_keys(item))
        return merged
    return set()


def _collector_jev_mock(mock: Any) -> Any:
    mock = _decode_mock_for_routing(mock)
    if isinstance(mock, Mapping):
        if "collector" in mock or "taste" in mock:
            return mock.get("collector", mock.get("taste"))
        keys = _mock_answer_keys(mock)
        if keys & {"promotion", "confidence", "score", "classification"}:
            return mock
        if keys & {"actionable_probability", "delivery_route", "delivery_route_match", "urgency_score"}:
            return os.environ.get("DESIGN_TASTE_MOCK_JEV_JSON") or None
    return mock if mock is not None and not (isinstance(mock, Mapping) and _mock_answer_keys(mock) & {"actionable_probability", "delivery_route"}) else None


def _runner_jev_mock(mock: Any) -> Any:
    mock = _decode_mock_for_routing(mock)
    if isinstance(mock, Mapping):
        if "runner" in mock:
            return mock["runner"]
        if "collector" in mock or "taste" in mock:
            return None
        keys = _mock_answer_keys(mock)
        if keys & {"actionable_probability", "delivery_route", "delivery_route_match", "urgency_score"}:
            return mock
        if keys & {"promotion", "confidence", "score", "classification"}:
            return None
    return mock


def _default_loop_collector(spec: LoopSpec, *, target_date: date, dry_run: bool = False, mock_jev_json: Any = None) -> dict[str, Any]:
    date_str = target_date.isoformat()
    if spec.loop_id == "local-herdr-worktree-gc":
        if not dry_run:
            icloud_script = TEAM_LEAD_SCRIPTS_DIR / "icloud_skills_sync.py"
            if icloud_script.is_file():
                with contextlib.suppress(Exception):
                    icloud_mod = _load_module("loop_icloud_skills_sync", icloud_script)
                    icloud_mod.run_full_sync(run_pi_harness=False)
        hygiene_mod = _load_module("loop_local_hygiene_patrol", TEAM_LEAD_SCRIPTS_DIR / "local_hygiene_patrol.py")
        dev_root = Path(os.environ.get("HYGIENE_PATROL_DEV_ROOT", "~/Development")).expanduser()
        handoff_env = os.environ.get("HYGIENE_PATROL_HANDOFF_ROOT")
        handoff_root = Path(handoff_env).expanduser() if handoff_env else None
        return hygiene_mod.run_loop_check(
            dev_root=dev_root,
            handoff_root=handoff_root,
            target_date=target_date,
            auto_gc=True,
            dry_run=dry_run,
        )

    if spec.loop_id == "spec-code-drift-patrol":
        drift_mod = _load_module("loop_spec_code_drift_patrol", SKILLS_ROOT / "codebase-design" / "scripts" / "spec_code_drift_patrol.py")
        fixture_env = os.environ.get("SPEC_CODE_DRIFT_PATROL_FIXTURE_ROOT")
        dev_env = os.environ.get("SPEC_CODE_DRIFT_PATROL_DEV_ROOT")
        handoff_env = os.environ.get("SPEC_CODE_DRIFT_PATROL_HANDOFF_ROOT")
        repos_env = os.environ.get("SPEC_CODE_DRIFT_PATROL_REPOS")
        mock_env = os.environ.get("SPEC_CODE_DRIFT_PATROL_MOCK_JEV_JSON")
        return drift_mod.loop_check(
            fixture_root=Path(fixture_env).expanduser() if fixture_env else None,
            dev_root=Path(dev_env).expanduser() if dev_env else None,
            handoff_root=Path(handoff_env).expanduser() if handoff_env else None,
            repos=repos_env,
            mock_jev_json=mock_jev_json if mock_jev_json is not None else mock_env,
            target_date=target_date,
            dry_run=dry_run,
        )

    if spec.loop_id == "backlog-grooming-and-push":
        groomer_mod = _load_module("loop_backlog_groomer", SKILLS_ROOT / "linear-board" / "scripts" / "backlog_groomer.py")
        issues_env = os.environ.get("LINEAR_BACKLOG_GROOMER_ISSUES_JSON")
        issues_json = Path(issues_env).expanduser() if issues_env else None
        return groomer_mod.run_loop_check(
            target_date=target_date,
            issues_json=issues_json,
            dry_run=dry_run,
        )

    if spec.loop_id == "design-taste-evolution":
        taste_mod = _load_module("loop_design_taste_evolution", SKILLS_ROOT / "design-studio" / "scripts" / "taste_evolution_loop.py")
        handoff_root = Path(os.environ.get("DESIGN_TASTE_HANDOFF_ROOT", "~/Development/handoff")).expanduser()
        return taste_mod.loop_check(handoff_root, target_date=target_date, mock_jev_json=_collector_jev_mock(mock_jev_json))

    if spec.loop_id == "daily-brief":
        brief_mod = _load_module("loop_daily_brief", SKILLS_ROOT / "linear-board" / "scripts" / "daily-brief.py")
        res = brief_mod.generate(
            target_date,
            dev_root=Path(os.environ.get("LINEAR_DAILY_BRIEF_DEV_ROOT", "~/Development")).expanduser(),
            handoff_root=Path(os.environ.get("LINEAR_DAILY_BRIEF_HANDOFF_ROOT", "~/Development/handoff")).expanduser(),
            client=brief_mod._load_linear_client(dry_run=True),
            propose_linear=False,
            dry_run=True,
            dual_zone=True,
            groom_backlog_on_generate=True,
        )
        todos = list(res.get("todo_items") or [])
        rollup = dict(res.get("rollup_counts") or {})
        total_signals = len(todos) + sum(int(v or 0) for v in rollup.values())
        sig = _compute_digest_signature({"date": date_str, "todos": [t.get("summary") for t in todos], "rollup": rollup})
        return {
            "loop_id": spec.loop_id,
            "date": date_str,
            "has_delta": total_signals > 0,
            "delta_count": total_signals,
            "severity": "high" if any(t.get("group") == "needs" for t in todos) else ("medium" if todos else "low"),
            "watermark": f"daily-brief:{date_str}:{sig}",
            "cursor": f"daily-brief-{date_str}",
            "title": res.get("title") or spec.issue_title_template.format(date=date_str),
            "dual_zone_markdown": res.get("markdown"),
            "summary": f"昨日完成 {sum(int(v or 0) for v in rollup.values())} 项，今日重点待办 {len(todos)} 项。",
            "items": [
                {"what": str(t.get("summary") or "待办事项"), "why": "今日重点推进事项"}
                for t in todos[:6]
            ] or [{"what": "查看今日简报概览", "why": "保持日常研发节奏"}],
            "raw": {"rollup_counts": rollup, "todo_count": len(todos)},
        }

    if spec.loop_id == "skills-roi-diagnostic":
        if not dry_run:
            icloud_script = TEAM_LEAD_SCRIPTS_DIR / "icloud_skills_sync.py"
            if icloud_script.is_file():
                with contextlib.suppress(Exception):
                    icloud_mod = _load_module("loop_icloud_skills_sync_telem", icloud_script)
                    icloud_mod.export_telemetry_shards()
        roi_mod = _load_module("loop_skills_roi", SKILLS_ROOT / "retro-loop" / "scripts" / "multica_skills_autopilot.py")
        wh_env = os.environ.get("SKILLS_ROI_WAREHOUSE_DB")
        cp_env = os.environ.get("SKILLS_ROI_COPILOT_DB")
        sess_env = os.environ.get("SKILLS_ROI_COPILOT_SESSIONS_DIR")
        usage_env = os.environ.get("SKILLS_ROI_USAGE_JSONL")
        handoff_env = os.environ.get("SKILLS_ROI_HANDOFF_ROOT")
        skills_env = os.environ.get("SKILLS_ROI_SKILLS_ROOT")
        dev_env = os.environ.get("SKILLS_ROI_DEV_ROOT")
        prop_env = os.environ.get("SKILLS_ROI_PROPOSALS_PATH")
        wm_env = os.environ.get("MULTICA_SKILL_RETRO_WATERMARK_FILE")
        snap = roi_mod.collect_skills_roi_snapshot(
            warehouse_db=Path(wh_env).expanduser() if wh_env else None,
            copilot_db=Path(cp_env).expanduser() if cp_env else None,
            copilot_sessions_dir=Path(sess_env).expanduser() if sess_env else None,
            usage_jsonl=Path(usage_env).expanduser() if usage_env else None,
            handoff_root=Path(handoff_env).expanduser() if handoff_env else None,
            skills_root=Path(skills_env).expanduser() if skills_env else None,
            dev_root=Path(dev_env).expanduser() if dev_env else None,
            proposals_path=Path(prop_env).expanduser() if prop_env else None,
            top_n=3,
            include_skill_retro=True,
            skill_retro_auto_propose=not dry_run,
            skill_retro_watermark_path=(
                None
                if dry_run
                else (Path(wm_env).expanduser() if wm_env else roi_mod._default_skill_retro_watermark_path())
            ),
        )
        findings = list(snap.get("top_findings") or [])
        skill_retro = dict(snap.get("skill_retro") or {})
        retro_proposals = list(skill_retro.get("proposals") or [])
        summary_dict = dict(snap.get("summary") or {})
        actionable_roi_count = int(
            summary_dict["actionable_skills_count"]
            if "actionable_skills_count" in summary_dict
            else len(findings)
        )
        retro_count = len(retro_proposals)
        total_delta_count = actionable_roi_count + retro_count
        cohort_sig = _compute_digest_signature(
            {
                "roi": [{"skill": f.get("skill"), "status": f.get("diagnostic_status")} for f in findings if actionable_roi_count > 0],
                "skill_retro": [
                    {"id": p.get("id"), "skill": p.get("skill"), "layer": p.get("layer"), "priority": p.get("priority")}
                    for p in retro_proposals
                ],
                "slow_flywheel_ready": bool(skill_retro.get("slow_flywheel_ready")),
            }
        )
        items: list[dict[str, str]] = []
        seen_what: set[str] = set()
        for prop in retro_proposals[:3]:
            what_txt = str(prop.get("human_what") or "").strip()
            why_txt = str(prop.get("human_why") or "").strip()
            if what_txt and what_txt.casefold() not in seen_what:
                seen_what.add(what_txt.casefold())
                items.append({"what": what_txt, "why": why_txt or "补齐四层技能自演进防呆指引"})
        for f in findings[:3]:
            if actionable_roi_count <= 0 or len(items) >= 5:
                break
            what_txt = f"收敛技能 `{f.get('skill')}` 的触发与回归门禁"
            if what_txt.casefold() not in seen_what:
                seen_what.add(what_txt.casefold())
                items.append(
                    {
                        "what": what_txt,
                        "why": roi_mod._plain_chinese_reason_for_skill(f).rstrip("。"),
                    }
                )
        if retro_count > 0 and actionable_roi_count > 0:
            summary_text = f"检测到 {actionable_roi_count} 个待治理技能与 {retro_count} 项四层自演进提案。"
        elif retro_count > 0:
            summary_text = f"四层技能自演进巡检发现 {retro_count} 项高信号改进提案。"
        elif actionable_roi_count > 0:
            summary_text = f"检测到 {actionable_roi_count} 个需优化的低效能或高频返工技能。"
        else:
            summary_text = "今日技能效能与四层自演进巡检无新增异常。"
        has_high_priority = any(str(p.get("priority") or "") in {"P0", "P1"} for p in retro_proposals)
        return {
            "loop_id": spec.loop_id,
            "date": date_str,
            "has_delta": total_delta_count > 0,
            "delta_count": total_delta_count,
            "severity": "high" if has_high_priority else ("medium" if total_delta_count > 0 else "low"),
            "watermark": f"skills-roi:cohort:{cohort_sig}",
            "cursor": str(snap.get("dedup_key") or f"skills-roi-{date_str}"),
            "title": str(snap.get("default_issue_title") or spec.issue_title_template.format(date=date_str)),
            "dual_zone_markdown": roi_mod.build_issue_markdown(snap) if total_delta_count > 0 else "",
            "summary": summary_text,
            "items": items,
            "skill_retro": {
                "schema": skill_retro.get("schema"),
                "sample_size": skill_retro.get("sample_size", 0),
                "slow_flywheel_ready": bool(skill_retro.get("slow_flywheel_ready")),
                "high_signal_proposals_count": retro_count,
                "observation_pool_count": skill_retro.get("observation_pool_count", 0),
                "proposals_written": skill_retro.get("proposals_written", 0),
                "summary": skill_retro.get("summary") or {},
                "proposals": retro_proposals[:4],
            },
            "raw": summary_dict,
        }

    if spec.loop_id == "skill-agent-daily-research":
        research_script = SKILLS_ROOT / "knowledge-digest" / "scripts" / "skill_agent_research_loop.py"
        args = [sys.executable, str(research_script), "loop-check", "--day", date_str, "--fetch-public"]
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        try:
            result = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except ValueError:
            result = {"ok": False, "status": "collector_error", "diagnostics": [proc.stderr.strip()[:300]]}
        if proc.returncode != 0:
            result.setdefault("ok", False)
            result.setdefault("status", "collector_error")
        result.setdefault("loop_id", spec.loop_id)
        result.setdefault("date", date_str)
        return result

    if spec.loop_id == "daily-learn-recommend":
        dv_script = SKILLS_ROOT / "knowledge-digest" / "scripts" / "digest_vault.py"
        proc = subprocess.run(
            [sys.executable, str(dv_script), "discover-topics", "--top", "4"],
            capture_output=True,
            text=True,
            check=False,
        )
        topics: list[dict[str, Any]] = []
        if proc.returncode == 0 and (proc.stdout or "").strip():
            try:
                parsed = json.loads(proc.stdout)
                if isinstance(parsed, dict):
                    topics = list(parsed.get("topics") or parsed.get("candidates") or [])
                elif isinstance(parsed, list):
                    topics = list(parsed)
            except ValueError:
                topics = []
        sig = _compute_digest_signature([t.get("topic_key") or t.get("title") for t in topics])
        items = [
            {
                "what": f"学习选题：{str(t.get('title') or t.get('topic_key') or '新主题')[:36]}",
                "why": str(t.get("why") or t.get("reason") or "补齐当前工程或个人成长知识缺口")[:48],
            }
            for t in topics[:4]
        ]
        return {
            "loop_id": spec.loop_id,
            "date": date_str,
            "has_delta": len(topics) > 0,
            "delta_count": len(topics),
            "severity": "medium" if topics else "low",
            "watermark": f"daily-learn:{date_str}:{sig}",
            "cursor": f"daily-learn-{date_str}",
            "title": spec.issue_title_template.format(date=date_str),
            "summary": f"今日发现 {len(topics)} 个高价值双轨学习候选主题，勾选即可生成知识卡片。",
            "items": items,
            "raw": {"topics": topics},
        }

    # aidash-retro-delta or custom loop
    proposals_path = Path.home() / "Development" / "AIDash" / "aidata" / "L5_apps" / "digest" / "state" / "proposals.jsonl"
    pending_rows: list[dict[str, Any]] = []
    if proposals_path.is_file():
        for line in proposals_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and str(obj.get("status") or "open").lower() in {"open", "pending", "proposed"}:
                pending_rows.append(obj)
    sig = _compute_digest_signature([r.get("id") or r.get("title") for r in pending_rows[:10]])
    return {
        "loop_id": spec.loop_id,
        "date": date_str,
        "has_delta": len(pending_rows) > 0,
        "delta_count": len(pending_rows),
        "severity": "medium" if pending_rows else "low",
        "watermark": f"{spec.loop_id}:{date_str}:{sig}",
        "cursor": f"{spec.loop_id}-{date_str}",
        "title": spec.issue_title_template.format(date=date_str),
        "summary": f"发现 {len(pending_rows)} 项待处理的工程复盘与质量改进提案。",
        "items": [
            {
                "what": f"处理提案 `{r.get('id') or idx}`：{str(r.get('title') or '复盘改进项')[:36]}",
                "why": str(r.get("root_cause") or r.get("category") or f"固化第 {idx} 项工程防回归门禁")[:48],
            }
            for idx, r in enumerate(pending_rows[:5], 1)
        ],
        "raw": {"pending_count": len(pending_rows)},
    }


def _extract_prob(raw: Any, field: str) -> float | None:
    if isinstance(raw, Mapping):
        raw = raw.get(field, raw.get("value"))
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val) or val < 0.0 or val > 1.0:
        return None
    return val


def evaluate_jev_actionability(
    collected: Mapping[str, Any],
    *,
    spec: LoopSpec,
    mock_jev_json: Any = None,
    jev_fn: Callable[..., dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Step 2 (Layer 1): Jev fast-thinking judgment with >0.70 / <0.30 / 0.30..0.70 truth table."""
    delta_count = int(collected.get("delta_count") or 0)
    severity = str(collected.get("severity") or "medium").lower()
    deterministic_fallback_actionable = bool(collected.get("has_delta")) and delta_count > 0 and severity in {"high", "medium"}
    default_route = spec.delivery_mode

    questions = {
        "actionable_probability": {
            "type": "noul",
            "instructions": "Is this periodic loop delta concrete and actionable enough to wake an Autopilot or file an Owner issue today?",
        },
        "delivery_route": {
            "type": "choice",
            "instructions": "Choose the lightest sufficient action path for this delta.",
            "criteria": {
                "dual_zone_issue": "Materialize a compact Dual-Zone checklist issue directly for Owner review.",
                "autopilot_trigger": "Trigger the registered Multica Autopilot agent for deeper root-cause synthesis.",
                "ignore": "Noise or non-actionable delta; skip waking any agent or filing an issue.",
            },
        },
        "delivery_route_match": {
            "type": "noul",
            "instructions": "Does the selected delivery_route accurately fit the observed loop delta?",
        },
        "urgency_score": {
            "type": "score",
            "instructions": "Score urgency from 0.0 (routine noise) to 1.0 (requires immediate Owner attention).",
        },
    }
    state_payload = {
        "loop_id": spec.loop_id,
        "date": collected.get("date"),
        "delta_count": delta_count,
        "severity": severity,
        "summary": str(collected.get("summary") or "")[:300],
    }

    decide = jev_fn or _load_jev_client().jev_decide
    response = decide(
        state=state_payload,
        questions=questions,
        agent_role="m2-dispatcher",
        goal_id=f"multica-loop-{spec.loop_id}",
        timeout_sec=1.5,
        falcon_only=True,
        mock_response=mock_jev_json,
    )

    if not isinstance(response, Mapping) or response.get("fallback_reason"):
        return {
            "actionable": deterministic_fallback_actionable,
            "route": default_route if deterministic_fallback_actionable else "ignore",
            "probability": None,
            "urgency_score": None,
            "decision_source": "deterministic_fallback_unavailable",
        }

    answers = response.get("answers", response)
    if not isinstance(answers, Mapping):
        return {
            "actionable": deterministic_fallback_actionable,
            "route": default_route if deterministic_fallback_actionable else "ignore",
            "probability": None,
            "urgency_score": None,
            "decision_source": "deterministic_fallback_malformed",
        }

    prob = _extract_prob(answers.get("actionable_probability"), "noul")
    route_raw = answers.get("delivery_route")
    if isinstance(route_raw, Mapping):
        route_raw = route_raw.get("choice")
    route_match = _extract_prob(answers.get("delivery_route_match"), "noul")
    urgency = _extract_prob(answers.get("urgency_score"), "score")

    valid_route = (
        str(route_raw)
        if isinstance(route_raw, str)
        and route_raw in {"auto_fix_and_digest_only", "dual_zone_issue", "autopilot_trigger", "ignore"}
        and (route_match is None or route_match >= 0.50)
        else default_route
    )

    if prob is None:
        return {
            "actionable": deterministic_fallback_actionable,
            "route": default_route if deterministic_fallback_actionable else "ignore",
            "probability": None,
            "urgency_score": urgency,
            "decision_source": "deterministic_fallback_missing_prob",
        }

    if route_raw == "ignore" and (route_match is None or route_match >= 0.50) and prob <= 0.70:
        return {
            "actionable": False,
            "route": "ignore",
            "probability": prob,
            "urgency_score": urgency,
            "decision_source": "jev_choice_ignore",
        }

    if prob > 0.70:
        chosen = default_route if valid_route == "ignore" else valid_route
        return {
            "actionable": True,
            "route": chosen,
            "probability": prob,
            "urgency_score": urgency,
            "decision_source": "jev_high_confidence",
        }

    if prob < 0.30:
        return {
            "actionable": False,
            "route": "ignore",
            "probability": prob,
            "urgency_score": urgency,
            "decision_source": "jev_low_confidence_reject",
        }

    # Grey zone inclusive 0.30..0.70 -> deterministic fallback rule
    return {
        "actionable": deterministic_fallback_actionable,
        "route": (default_route if valid_route == "ignore" else valid_route) if deterministic_fallback_actionable else "ignore",
        "probability": prob,
        "urgency_score": urgency,
        "decision_source": "jev_grey_zone_fallback",
    }


def _render_verified_loop_issue(collected: Mapping[str, Any], *, spec: LoopSpec) -> str:
    issue_brief = _load_issue_brief()
    pre_rendered = str(collected.get("dual_zone_markdown") or "").strip()
    if pre_rendered:
        check = issue_brief.verify_dual_zone_issue(pre_rendered)
        if check["ok"]:
            return pre_rendered + ("\n" if not pre_rendered.endswith("\n") else "")

    items = list(collected.get("items") or [])
    if not items:
        items = [{"what": "审阅本次巡检增量并决定是否推进", "why": "确定性预检发现新变化"}]

    rendered = issue_brief.render_dual_zone_issue(
        tldr=str(collected.get("summary") or "预检发现待关注增量事项，请勾选确认需推进项。"),
        items=items[:8],
        cursor=str(collected.get("cursor") or f"{spec.loop_id}-{collected.get('date')}"),
        ai_payload={
            "loop_id": spec.loop_id,
            "watermark": collected.get("watermark"),
            "delta_count": collected.get("delta_count"),
            "raw": collected.get("raw"),
        },
    )
    verification = issue_brief.verify_dual_zone_issue(rendered)
    if not verification["ok"]:
        raise ValueError(f"Dual-Zone verification failed for loop {spec.loop_id}: {verification['violations']}")
    return rendered


def upsert_multica_dual_zone_issue(
    *,
    title: str,
    description: str,
    cursor: str,
    assignee_id: str = DEFAULT_OWNER_ID,
    priority: str = "medium",
    status: str = "todo",
    retire_prior_prefix: str | None = None,
    current_date: str | None = None,
    server_url: str = DEFAULT_SERVER_URL,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _default_multica_runner,
    multica_bin: str | None = None,
) -> dict[str, Any]:
    verification = _load_issue_brief().verify_dual_zone_issue(description)
    if not verification["ok"]:
        raise ValueError(f"Refusing to publish non-Dual-Zone issue body: {verification['violations']}")

    cursor_token = f"multica-cursor:{cursor}"
    if dry_run:
        return {
            "ok": True,
            "action": "dry_run",
            "title": title,
            "cursor": cursor,
            "priority": priority,
            "status": status,
            "assignee_id": assignee_id,
            "dual_zone_verification": verification,
        }

    list_proc = runner(
        ["issue", "list", "--limit", "100", "--output", "json"],
        multica_bin=multica_bin,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    listed = _parse_json_stdout(list_proc, "issue list")
    issues = listed.get("issues", []) if isinstance(listed, dict) else (listed if isinstance(listed, list) else [])

    existing: dict[str, Any] | None = None
    for row in issues:
        if not isinstance(row, dict):
            continue
        st_val = str(row.get("status") or row.get("status_category") or "").lower()
        if st_val in {"cancelled", "canceled"}:
            continue
        row_title = str(row.get("title") or "")
        row_desc = str(row.get("description") or "")
        if cursor_token in row_desc or row_title == title:
            existing = row
            break

    # Retire older daily issues (e.g. previous `[Daily Brief YYYY-MM-DD]`) by marking Done + unassigning
    retired_ids: list[str] = []
    if retire_prior_prefix and current_date:
        date_pat = re.compile(re.escape(retire_prior_prefix) + r"\s+(\d{4}-\d{2}-\d{2})\]")
        for row in issues:
            if not isinstance(row, dict):
                continue
            if existing and row.get("id") == existing.get("id"):
                continue
            row_title = str(row.get("title") or "")
            m = date_pat.search(row_title)
            if not m or m.group(1) >= current_date:
                continue
            st_val = str(row.get("status") or row.get("status_category") or "").lower()
            if st_val in {"done", "completed", "cancelled", "canceled"} and not row.get("assignee_id"):
                continue
            row_id = str(row.get("id") or row.get("identifier") or "")
            if not row_id:
                continue
            runner(
                ["issue", "status", row_id, "done", "--no-start", "--output", "json"],
                multica_bin=multica_bin,
                server_url=server_url,
                workspace_id=workspace_id,
            )
            runner(
                ["issue", "assign", row_id, "--unassign", "--no-start", "--output", "json"],
                multica_bin=multica_bin,
                server_url=server_url,
                workspace_id=workspace_id,
            )
            retired_ids.append(row_id)

    if existing is not None:
        issue_id = str(existing.get("id") or existing.get("identifier"))
        upd_proc = runner(
            [
                "issue",
                "update",
                issue_id,
                "--title",
                title,
                "--description-stdin",
                "--priority",
                priority,
                "--assignee-id",
                assignee_id,
                "--status",
                status,
                "--no-start",
                "--output",
                "json",
            ],
            stdin_text=description,
            multica_bin=multica_bin,
            server_url=server_url,
            workspace_id=workspace_id,
        )
        updated = _parse_json_stdout(upd_proc, "issue update")
        return {
            "ok": True,
            "action": "updated",
            "cursor": cursor,
            "retired_prior_ids": retired_ids,
            "issue": updated,
        }

    create_proc = runner(
        [
            "issue",
            "create",
            "--title",
            title,
            "--description-stdin",
            "--priority",
            priority,
            "--assignee-id",
            assignee_id,
            "--status",
            status,
            "--output",
            "json",
        ],
        stdin_text=description,
        multica_bin=multica_bin,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    created = _parse_json_stdout(create_proc, "issue create")
    return {
        "ok": True,
        "action": "created",
        "cursor": cursor,
        "retired_prior_ids": retired_ids,
        "issue": created,
    }


def _find_autopilot_id(
    spec: LoopSpec,
    *,
    server_url: str,
    workspace_id: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    multica_bin: str | None,
) -> str | None:
    list_proc = runner(
        ["autopilot", "list", "--output", "json"],
        multica_bin=multica_bin,
        server_url=server_url,
        workspace_id=workspace_id,
    )
    listed = _parse_json_stdout(list_proc, "autopilot list")
    autopilots = listed.get("autopilots", []) if isinstance(listed, dict) else (listed if isinstance(listed, list) else [])
    for ap in autopilots:
        if not isinstance(ap, dict):
            continue
        t = str(ap.get("title") or "")
        d = str(ap.get("description") or "")
        if (
            t == spec.autopilot_title
            or f"`{spec.loop_id}`" in d
            or (spec.loop_id == "skills-roi-diagnostic" and "Skills ROI" in t)
            or (spec.loop_id == "aidash-retro-delta" and "AIDash Digest & Retro" in t)
            or (spec.loop_id == "daily-learn-recommend" and "Daily Knowledge Digest" in t)
            or (spec.loop_id == "local-herdr-worktree-gc" and "Local Herdr & Worktree Hygiene Patrol" in t)
            or (spec.loop_id == "spec-code-drift-patrol" and "Spec-Code Drift Patrol" in t)
            or (spec.loop_id == "backlog-grooming-and-push" and "Backlog Grooming & Top-3 Push" in t)
            or (spec.loop_id == "design-taste-evolution" and ("UI Design Taste Evolution" in t or "Taste Evolution" in t))
        ):
            return str(ap.get("id") or "") or None
    return None


def run_loop(
    loop_name: str,
    *,
    target_date: date | None = None,
    state_path: Path | None = None,
    digest_buffer_path: Path | None = None,
    force: bool = False,
    force_create_issue: bool = False,
    dry_run: bool = False,
    mock_jev_json: Any = None,
    collector_json: Path | None = None,
    server_url: str = DEFAULT_SERVER_URL,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    owner_id: str = DEFAULT_OWNER_ID,
    collector_fn: Callable[..., dict[str, Any]] | None = None,
    jev_fn: Callable[..., dict[str, Any] | None] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _default_multica_runner,
    multica_bin: str | None = None,
) -> dict[str, Any]:
    spec = resolve_loop_spec(loop_name)
    resolved_date = target_date or datetime.now(CST).date()

    # Step 1: Layer-0 Deterministic Collection & Watermark Diff
    if collector_json is not None:
        collected = json.loads(Path(collector_json).read_text(encoding="utf-8"))
    elif collector_fn is not None:
        collected = collector_fn(spec, target_date=resolved_date)
    else:
        collected = _default_loop_collector(spec, target_date=resolved_date, dry_run=dry_run, mock_jev_json=mock_jev_json)

    watermarks = load_watermarks(state_path)
    previous_entry = watermarks.get(spec.loop_id) if isinstance(watermarks.get(spec.loop_id), dict) else {}
    prev_watermark = str(previous_entry.get("watermark") or "")
    new_watermark = str(collected.get("watermark") or "")

    if not force and (not collected.get("has_delta") or (new_watermark and new_watermark == prev_watermark)):
        if spec.delivery_mode == "auto_fix_and_digest_only":
            record_daily_digest_entry(
                loop_id=spec.loop_id,
                target_date=resolved_date.isoformat(),
                collected=collected,
                actionable=False,
                buffer_path=digest_buffer_path,
                dry_run=dry_run,
            )
        return {
            "ok": True,
            "loop_id": spec.loop_id,
            "status": "SKIPPED_NO_DELTA",
            "layer_reached": 0,
            "token_cost": 0,
            "watermark": new_watermark or prev_watermark,
            "previous_watermark": prev_watermark,
        }

    # Persist collected snapshot for downstream Autopilot consumption if triggered
    tmp_snapshot = Path(f"/tmp/multica-loop-{spec.loop_id}-collect.json")
    if not dry_run:
        try:
            tmp_snapshot.write_text(json.dumps(collected, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    # Step 2: Layer-1 Jev Fast-Thinking Judgment. Loop 4 may already have
    # triaged each finding; preserve that verdict instead of re-triaging noise.
    collector_triaged = (
        spec.loop_id == "spec-code-drift-patrol"
        and isinstance(collected.get("actionable"), bool)
        and isinstance(collected.get("jev"), Mapping)
    )
    if collector_triaged:
        jev_verdict = {
            "actionable": bool(collected.get("actionable")),
            "route": spec.delivery_mode if collected.get("actionable") else "ignore",
            "decision_source": "collector_triage",
            "collector_jev": collected.get("jev"),
        }
    else:
        jev_verdict = evaluate_jev_actionability(
            collected,
            spec=spec,
            mock_jev_json=_runner_jev_mock(mock_jev_json),
            jev_fn=jev_fn,
        )
    if not jev_verdict["actionable"]:
        if spec.delivery_mode == "auto_fix_and_digest_only":
            record_daily_digest_entry(
                loop_id=spec.loop_id,
                target_date=resolved_date.isoformat(),
                collected=collected,
                actionable=False,
                buffer_path=digest_buffer_path,
                dry_run=dry_run,
            )
        if not dry_run and new_watermark:
            watermarks[spec.loop_id] = {
                "watermark": new_watermark,
                "updated_at": datetime.now(CST).isoformat(),
                "last_status": "SKIPPED_NOT_ACTIONABLE",
            }
            save_watermarks(watermarks, state_path)
        return {
            "ok": True,
            "loop_id": spec.loop_id,
            "status": "SKIPPED_NOT_ACTIONABLE",
            "layer_reached": 1,
            "watermark": new_watermark,
            "jev": jev_verdict,
        }

    # Step 3: Layer-2 Wake-on-Actionable
    explicit_mock_route = False
    decoded_runner_mock = _runner_jev_mock(mock_jev_json)
    if isinstance(decoded_runner_mock, Mapping):
        ans_obj = decoded_runner_mock.get("answers", decoded_runner_mock)
        if isinstance(ans_obj, Mapping) and ans_obj.get("delivery_route") is not None:
            explicit_mock_route = True

    if spec.delivery_mode == "auto_fix_and_digest_only" and not force_create_issue and not explicit_mock_route:
        route = "auto_fix_and_digest_only"
    elif force_create_issue and spec.delivery_mode == "auto_fix_and_digest_only":
        route = "dual_zone_issue"
    else:
        route = str(jev_verdict.get("route") or spec.delivery_mode)

    if route == "auto_fix_and_digest_only":
        layer2_result = record_daily_digest_entry(
            loop_id=spec.loop_id,
            target_date=resolved_date.isoformat(),
            collected=collected,
            actionable=True,
            buffer_path=digest_buffer_path,
            dry_run=dry_run,
        )
    elif route == "autopilot_trigger":
        if dry_run:
            layer2_result: dict[str, Any] = {
                "action": "dry_run_autopilot_trigger",
                "autopilot_title": spec.autopilot_title,
            }
        else:
            ap_id = _find_autopilot_id(
                spec,
                server_url=server_url,
                workspace_id=workspace_id,
                runner=runner,
                multica_bin=multica_bin,
            )
            if ap_id:
                trig_proc = runner(
                    ["autopilot", "trigger", ap_id, "--output", "json"],
                    multica_bin=multica_bin,
                    server_url=server_url,
                    workspace_id=workspace_id,
                )
                layer2_result = {
                    "action": "triggered_autopilot",
                    "autopilot_id": ap_id,
                    "trigger_response": _parse_json_stdout(trig_proc, "autopilot trigger"),
                }
            else:
                # Graceful fallback: materialize Dual-Zone issue directly so actionable finding is never lost
                md = _render_verified_loop_issue(collected, spec=spec)
                layer2_result = upsert_multica_dual_zone_issue(
                    title=str(collected.get("title") or spec.issue_title_template.format(date=resolved_date.isoformat())),
                    description=md,
                    cursor=str(collected.get("cursor") or f"{spec.loop_id}-{resolved_date.isoformat()}"),
                    assignee_id=owner_id,
                    priority=spec.default_priority,
                    status=spec.default_status,
                    server_url=server_url,
                    workspace_id=workspace_id,
                    dry_run=False,
                    runner=runner,
                    multica_bin=multica_bin,
                )
    else:
        md = _render_verified_loop_issue(collected, spec=spec)
        retire_prefix = (
            "[Daily Brief"
            if spec.loop_id == "daily-brief"
            else (
                "[Daily Learn"
                if spec.loop_id == "daily-learn-recommend"
                else (
                    "[P3 / Priority 3][Local Hygiene]"
                    if spec.loop_id == "local-herdr-worktree-gc"
                    else (
                        "[P3 / Priority 3][Spec-Code Drift]"
                        if spec.loop_id == "spec-code-drift-patrol"
                        else (
                            "[P3 / Priority 3][Backlog Grooming]"
                            if spec.loop_id == "backlog-grooming-and-push"
                            else ("[P3 / Priority 3][Taste Evolution]" if spec.loop_id == "design-taste-evolution" else None)
                        )
                    )
                )
            )
        )
        layer2_result = upsert_multica_dual_zone_issue(
            title=str(collected.get("title") or spec.issue_title_template.format(date=resolved_date.isoformat())),
            description=md,
            cursor=str(collected.get("cursor") or f"{spec.loop_id}-{resolved_date.isoformat()}"),
            assignee_id=owner_id,
            priority=spec.default_priority,
            status=spec.default_status,
            retire_prior_prefix=retire_prefix,
            current_date=resolved_date.isoformat(),
            server_url=server_url,
            workspace_id=workspace_id,
            dry_run=dry_run,
            runner=runner,
            multica_bin=multica_bin,
        )

    if not dry_run and new_watermark:
        watermarks[spec.loop_id] = {
            "watermark": new_watermark,
            "updated_at": datetime.now(CST).isoformat(),
            "last_status": "ACTION_TAKEN",
            "route": route,
        }
        save_watermarks(watermarks, state_path)

    return {
        "ok": True,
        "loop_id": spec.loop_id,
        "status": "ACTION_TAKEN",
        "layer_reached": 2,
        "route": route,
        "watermark": new_watermark,
        "jev": jev_verdict,
        "delivery": layer2_result,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multica_loop_runner.py",
        description="0-Agent Script Precheck + Jev Fast-Thinking -> Wake-on-Actionable Multica Autopilot Hub",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_vpn = sub.add_parser("verify-vpn", help="Run deterministic LAN + OpenVPN internal gateway Multica probe")
    p_vpn.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    p_vpn.add_argument("--app-url", default=DEFAULT_APP_URL)
    p_vpn.add_argument("--ws-url", default=None)
    p_vpn.add_argument("--vpn-gateway-host", default=None, help="OpenVPN internal gateway host (e.g. 10.9.0.1)")
    p_vpn.add_argument("--vpn-gateway-url", default=None, help="OpenVPN internal Web URL (e.g. http://10.9.0.1:13000)")
    p_vpn.add_argument("--vpn-api-url", default=None, help="OpenVPN internal API URL (e.g. http://10.9.0.1:18080)")
    p_vpn.add_argument("--vpn-ws-url", default=None, help="OpenVPN internal WS URL (e.g. ws://10.9.0.1:18080/ws)")
    p_vpn.add_argument("--no-vpn-gateway", action="store_true", help="Skip auto-probing active OpenVPN 10.9.0.x/10.8.0.x gateway")
    p_vpn.add_argument("--workspace-slug", default=DEFAULT_WORKSPACE_SLUG)
    p_vpn.add_argument("--email", default=DEFAULT_OWNER_EMAIL)
    p_vpn.add_argument("--verification-code", default=DEFAULT_DEV_CODE)
    p_vpn.add_argument("--multica-dir", type=Path, default=None)
    p_vpn.add_argument("--json", action="store_true", default=True)

    p_sync = sub.add_parser("sync-autopilots", help="Register/update canonical loops in Multica Autopilot")
    p_sync.add_argument("--loop", dest="loop_id", default=None, help="Optionally sync only one canonical loop (backwards-compatible selector)")
    p_sync.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    p_sync.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    p_sync.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    p_sync.add_argument("--subscriber-id", default=DEFAULT_OWNER_ID)
    p_sync.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.add_argument("--json", action="store_true", default=True)

    p_run = sub.add_parser("run-loop", help="Execute Layer-0 precheck -> Layer-1 Jev -> Layer-2 Wake-on-Actionable")
    p_run.add_argument(
        "--loop",
        required=True,
        help="Loop ID (daily-brief, skills-roi-diagnostic, daily-learn-recommend, skill-agent-daily-research, aidash-retro-delta, local-herdr-worktree-gc, spec-code-drift-patrol, backlog-grooming-and-push, design-taste-evolution)",
    )
    p_run.add_argument("--date", default="today")
    p_run.add_argument("--state-file", type=Path, default=None)
    p_run.add_argument("--digest-buffer-file", type=Path, default=None, help="Path to daily-digest-buffer.json")
    p_run.add_argument("--collector-json", type=Path, default=None)
    p_run.add_argument("--mock-jev-json", default=None)
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--force-create-issue", action="store_true", help="Force creating/updating a standalone Multica issue even for auto_fix_and_digest_only loops")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    p_run.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    p_run.add_argument("--owner-id", default=DEFAULT_OWNER_ID)
    p_run.add_argument("--json", action="store_true", default=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if ns.subcommand == "verify-vpn":
        report = verify_vpn_access(
            server_url=ns.server_url,
            app_url=ns.app_url,
            ws_url=ns.ws_url,
            vpn_gateway_host=ns.vpn_gateway_host,
            vpn_gateway_url=ns.vpn_gateway_url,
            vpn_api_url=ns.vpn_api_url,
            vpn_ws_url=ns.vpn_ws_url,
            auto_probe_vpn_gateway=(False if ns.no_vpn_gateway else None),
            workspace_slug=ns.workspace_slug,
            email=ns.email,
            verification_code=ns.verification_code,
            multica_dir=ns.multica_dir,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if ns.subcommand == "sync-autopilots":
        report = sync_autopilots(
            loop_id=ns.loop_id,
            server_url=ns.server_url,
            workspace_id=ns.workspace_id,
            agent_id=ns.agent_id,
            subscriber_id=ns.subscriber_id,
            tz_name=ns.timezone,
            dry_run=ns.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if ns.subcommand == "run-loop":
        target = datetime.now(CST).date() if ns.date == "today" else date.fromisoformat(ns.date)
        report = run_loop(
            ns.loop,
            target_date=target,
            state_path=ns.state_file,
            digest_buffer_path=ns.digest_buffer_file,
            force=ns.force,
            force_create_issue=ns.force_create_issue,
            dry_run=ns.dry_run,
            mock_jev_json=ns.mock_jev_json,
            collector_json=ns.collector_json,
            server_url=ns.server_url,
            workspace_id=ns.workspace_id,
            owner_id=ns.owner_id,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
