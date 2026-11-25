from __future__ import annotations

from ._imports import *

from .constants_and_paths import _run_command

def _extract_command_block_from_frame(frame_text: str) -> str | None:
    """Extract the full command block from an active command confirmation frame, or None if multiline/ambiguous."""
    lines = [ln.strip() for ln in frame_text.splitlines() if ln.strip()]
    cmd_lines: list[str] = []
    collecting = False
    for line in lines:
        if re.match(r"^(?:[>❯●◉▶]|\d+[.)])\s+", line):
            collecting = False
            continue
        if re.match(
            r"^(?:CMD_CONFIRM|Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command|"
            r"Do\s+you\s+want\s+to\s+run|Allow\s+running|Run\s+shell\s+command)\b",
            line,
            re.I,
        ):
            m_inline = re.match(
                r"^(?:CMD_CONFIRM|Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command)\s*:\s*(.+)$",
                line,
                re.I,
            )
            if m_inline:
                rest = m_inline.group(1).strip()
                if not re.match(r"^(?:Do\s+you|Allow|Confirm|Run\s+command|Execute\s+command)\b", rest, re.I):
                    cmd_lines.append(rest.strip("`"))
                    collecting = True
            continue
        m_cmd = re.match(r"^(?:Command|Run|Execute|Shell|bash|sh)\s*:\s*(.+)$", line, re.I)
        if m_cmd:
            cmd_lines.append(m_cmd.group(1).strip().strip("`"))
            collecting = True
            continue
        if line.startswith("$ "):
            cmd_lines.append(line[2:].strip().strip("`"))
            collecting = True
            continue
        if collecting:
            # Any extra non-choice line after Command: makes it a multiline command block -> reject
            cmd_lines.append(line)
    if len(cmd_lines) != 1:
        return None
    return cmd_lines[0]

def _is_whitelisted_command(cmd_text: str) -> bool:
    stripped = cmd_text.strip()
    if not stripped or "\n" in stripped or "\r" in stripped or "\\" in stripped:
        return False
    # Reject shell chaining, pipes, redirections, command/process substitutions, subshells, backgrounding, or globs
    if re.search(r"[`<>()$*{}\[\];|&]", stripped):
        return False
    try:
        return _is_whitelisted_command_segment(stripped)
    except ValueError:
        return False

def _normalize_mcp_server_and_tool(raw_server: str, raw_tool: str) -> tuple[str, str]:
    server = _strip_matching_quotes(_text(raw_server)).lower()
    tool = _strip_matching_quotes(_text(raw_tool))
    if not server and tool:
        lower_tool = tool.lower()
        for prefix in ("github-mcp-server-", "ado-edge-", "linear-", "github-", "ado-"):
            if lower_tool.startswith(prefix):
                server = prefix[:-1]
                tool = tool[len(prefix) :]
                break
        if not server:
            for sep in ("/", ":"):
                if sep in tool:
                    head, tail = tool.split(sep, 1)
                    if head.strip() and tail.strip():
                        server = head.strip().lower()
                        tool = tail.strip()
                        break
    if server in {"linear-mcp", "linear_mcp"}:
        server = "linear"
    tool_norm = re.sub(r"[\s_-]+", "_", tool.strip().lower()).strip("_")
    for prefix in ("github_mcp_server_", "ado_edge_", "linear_", "github_", "ado_"):
        if tool_norm.startswith(prefix) and not server:
            server = prefix[:-1].replace("_", "-")
            tool_norm = tool_norm[len(prefix) :]
            break
    if server in {"linear-mcp", "linear_mcp"}:
        server = "linear"
    return server, tool_norm

def _is_whitelisted_mcp_tool(server: str, tool: str) -> bool:
    server_norm, tool_norm = _normalize_mcp_server_and_tool(server, tool)
    if not server_norm or not tool_norm:
        return False
    if _FORBIDDEN_MCP_TOOL_KEYWORDS_RE.search(tool_norm):
        return False
    if server_norm == "linear":
        if tool_norm in WHITELISTED_LINEAR_MCP_TOOLS:
            return True
        if tool_norm.startswith(("get_", "list_", "search_")):
            return True
        return False
    if server_norm in WHITELISTED_READONLY_MCP_SERVERS:
        if _FORBIDDEN_READONLY_MCP_KEYWORDS_RE.search(tool_norm):
            return False
        if tool_norm in WHITELISTED_ADO_READONLY_MCP_TOOLS:
            return True
        if tool_norm.startswith(("get_", "list_", "search_", "show_", "read_", "find_")):
            return True
        return False
    return False

def _is_double_boxed_or_json_payload_line(raw_line: str, unboxed_line: str) -> bool:
    """Return True when a line inside an MCP tool confirmation dialog is part of the inner JSON argument preview box."""
    if re.match(r"^\s*[│┃]\s*[│┃╭╰]", raw_line):
        return True
    stripped = unboxed_line.strip()
    if not stripped:
        return True
    if stripped[0] in {'{', '}', '[', ']', '"'}:
        return True
    if re.match(r'^"[A-Za-z0-9_.-]+"\s*:', stripped):
        return True
    return False

def _extract_mcp_tool_prompt(visible_text: str, raw_active_frame: str) -> dict[str, Any] | None:
    """Extract active MCP tool confirmation details from a 'Do you want to use this tool?' bottom frame."""
    raw_lines = [ln for ln in _text(raw_active_frame).splitlines() if ln.strip()]
    if not raw_lines:
        return None
    unboxed_lines = [_strip_tui_box_chrome(ln) for ln in raw_lines]
    q_idx = -1
    for idx, uline in enumerate(unboxed_lines):
        if re.match(r"^Do\s+you\s+want\s+to\s+use\s+this\s+tool\s*\?\s*$", uline.strip(), re.I):
            q_idx = idx
    if q_idx < 0:
        return None

    post_lines = [ln.strip() for ln in unboxed_lines[q_idx + 1 :] if ln.strip()]
    # If any completion boundary, business decision, or another non-option question appears AFTER the tool question, it is stale/ambiguous.
    for pline in post_lines:
        if _COMPLETION_BOUNDARY_RE.search(pline) or _BUSINESS_DECISION_RE.search(pline):
            return None
        if pline.endswith("?") and not re.match(r"^(?:[>❯●◉▶]?\s*)?\d+[.)]\s+", pline):
            return None

    # Check non-JSON lines before 'Do you want to use this tool?' for conflicting dialog headers
    ambiguous = False
    header_candidates: list[str] = []
    for idx in range(q_idx):
        raw_ln = raw_lines[idx]
        unboxed_ln = unboxed_lines[idx].strip()
        if not unboxed_ln or _is_double_boxed_or_json_payload_line(raw_ln, unboxed_ln):
            continue
        header_candidates.append(unboxed_ln)
        if _BUSINESS_DECISION_RE.search(unboxed_ln):
            ambiguous = True
        elif re.search(
            r"(?:Allow\s+(?:path|directory)\s+access|Read\s+access\s+covers\s+this\s+directory|"
            r"Confirm\s+folder\s+trust|Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|"
            r"\bCurate\s+Memory\b|curate\s+this\s+memory|\bstore_memory\b|\bvote_memory\b|"
            r"\bCMD_CONFIRM\b|Do\s+you\s+want\s+to\s+run|Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command)",
            unboxed_ln,
            re.I,
        ):
            ambiguous = True
        elif unboxed_ln.endswith("?"):
            ambiguous = True

    post_joined = " ".join(post_lines)
    post_multiline = "\n".join(post_lines)
    server = ""
    tool = ""

    # 1. Authoritative extraction from Copilot CLI's Option 2:
    #    '2. Yes, and don't ask again for tool "Save issue" from "linear" in this repo (...)'
    m_opt2 = re.search(
        r"for\s+tool\s+(?:[\"']([^\"']+)[\"']|([A-Za-z0-9_:/ -]+?))\s+from\s+(?:MCP\s+server\s+)?(?:[\"']([^\"']+)[\"']|([A-Za-z0-9_.-]+))",
        post_joined,
        re.I,
    )
    if m_opt2:
        tool = (m_opt2.group(1) or m_opt2.group(2) or "").strip()
        server = (m_opt2.group(3) or m_opt2.group(4) or "").strip()
    else:
        m_opt2_tool_only = re.search(
            r"for\s+tool\s+(?:[\"']([^\"']+)[\"']|([A-Za-z0-9_:/ -]+?))(?:\s+in\s+this\s+repo|\s+for\s+this\s+session|\s*\(|\s+3[.)]|\s*$)",
            post_joined,
            re.I,
        )
        if m_opt2_tool_only:
            tool = (m_opt2_tool_only.group(1) or m_opt2_tool_only.group(2) or "").strip()

    # 2. Fallback / supplement from header lines above the JSON box (in frame and visible scrollback)
    if not server or not tool:
        all_raw_lines = [ln for ln in _text(visible_text).splitlines() if ln.strip()][-80:]
        extra_headers: list[str] = []
        for rln in reversed(all_raw_lines):
            uln = _strip_tui_box_chrome(rln).strip()
            if not uln or _is_double_boxed_or_json_payload_line(rln, uln):
                continue
            extra_headers.append(uln)
        for hline in [*header_candidates, *extra_headers]:
            m_mcp_paren = re.search(
                r"(?:^|[○●❯>\s]|Tool\s*:\s*)([A-Za-z0-9_ -]+?)\s*\(\s*MCP\s*:\s*([A-Za-z0-9_.-]+)\s*\)",
                hline,
                re.I,
            )
            if m_mcp_paren:
                tool = tool or m_mcp_paren.group(1).strip()
                server = server or m_mcp_paren.group(2).strip()
                break
            m_server_kv = re.match(r"^(?:MCP\s+)?Server\s*[:=]\s*[\"']?([A-Za-z0-9_.-]+)[\"']?\s*$", hline, re.I)
            if m_server_kv and not server:
                server = m_server_kv.group(1).strip()
                continue
            m_tool_kv = re.match(
                r"^Tool(?:\s+name)?\s*[:=]\s*[\"']?([A-Za-z0-9_:/ -]+?)[\"']?(?:\s*\(\s*(?:MCP\s*:\s*)?([A-Za-z0-9_.-]+)\s*\))?\s*$",
                hline,
                re.I,
            )
            if m_tool_kv:
                tool = tool or m_tool_kv.group(1).strip()
                if m_tool_kv.group(2) and not server:
                    server = m_tool_kv.group(2).strip()
                continue
            m_pair = re.match(
                r"^(linear|ado-edge|ado|github-mcp-server|github)\s*(?:/|:|\s+-\s+)\s*([A-Za-z0-9_ -]+?)(?:\s*\(MCP\))?\s*$",
                hline,
                re.I,
            )
            if m_pair:
                server = server or m_pair.group(1).strip()
                tool = tool or m_pair.group(2).strip()
                break

    server_norm, tool_norm = _normalize_mcp_server_and_tool(server, tool)
    has_remember_option = bool(
        re.search(
            r"(?m)^\s*(?:[>❯●◉▶]\s*)?2[.)]\s*(?:Yes,\s*and\s+(?:don't\s+ask\s+again|remember)|Always\s+allow)\b",
            post_multiline,
            re.I,
        )
    )
    return {
        "ambiguous": ambiguous,
        "mcp_server": server_norm or server,
        "mcp_tool": tool or tool_norm,
        "mcp_tool_norm": tool_norm,
        "has_remember_option": has_remember_option,
        "signature_text": f"mcp:{server_norm}:{tool_norm}:{post_joined}",
    }

def _extract_active_bottom_frame(visible_text: str) -> str:
    """Slice only the bottom-most active interactive frame after the last output/completion boundary."""
    lines = [ln.rstrip() for ln in _text(visible_text).splitlines() if ln.strip()]
    if not lines:
        return ""
    tail = lines[-20:]
    last_boundary_idx = -1
    for idx, raw in enumerate(tail):
        stripped = raw.strip()
        if _COMPLETION_BOUNDARY_RE.search(stripped):
            last_boundary_idx = idx
    if last_boundary_idx >= 0 and last_boundary_idx + 1 < len(tail):
        tail = tail[last_boundary_idx + 1 :]
    # Also if there are multiple prompt headers separated by non-choice output, slice from the last header/question
    header_indices = [
        idx for idx, raw in enumerate(tail)
        if _PROMPT_HEADER_RE.search(raw) or (raw.strip().endswith("?") and not re.match(r"^(?:[>❯●◉▶]|\d+[.)])\s+", raw.strip()))
    ]
    if len(header_indices) > 1:
        # Check if there are multiple distinct prompt headers -> if separated or conflicting, return full tail so ambiguity check rejects it
        pass
    return "\n".join(tail)

def _validate_apply_receipt_for_target(
    plan: dict[str, Any],
    apply_receipt: dict[str, Any] | None,
    *,
    target_ref: str = "",
    pane_id: str = "",
) -> bool:
    if not isinstance(apply_receipt, dict):
        return False
    if apply_receipt.get("ok") is not True or apply_receipt.get("approved") is not True:
        return False
    plan_id = _text(plan.get("plan_id"))
    receipt_plan_id = _text(apply_receipt.get("plan_id"))
    if not plan_id or receipt_plan_id != plan_id:
        return False
    receipts = apply_receipt.get("receipts")
    if not isinstance(receipts, list) or not receipts:
        return False
    for rec in receipts:
        if not isinstance(rec, dict) or rec.get("ok") is not True:
            continue
        method = _text(rec.get("method"))
        if method == "stdout":
            continue
        rec_target = _text(rec.get("target"))
        rec_pane = _text(
            rec.get("pane_id")
            or ((rec.get("receipt") or {}).get("pane_id") if isinstance(rec.get("receipt"), dict) else "")
        )
        if target_ref and rec_target and target_ref == rec_target:
            return True
        if pane_id and rec_pane and pane_id == rec_pane:
            return True
    return False

def _classify_permission_prompt(
    visible_text: str,
    target_cwd: str = "",
    *,
    target_ref: str = "",
    live_status: str = "blocked",
    auto_unblock_enabled: bool = True,
    herdr_bin: str = "herdr",
    runner: CommandRunner | None = None,
    already_unblocked_signatures: set[str] | None = None,
) -> dict[str, Any]:
    """Classify the bottom-most active permission prompt and auto-unblock only when authorized."""
    text = _text(visible_text)
    if not text:
        return {"action": "none", "kind": "", "keys": [], "blocked_reason": "", "auto_unblock_enabled": auto_unblock_enabled}

    if _text(live_status).lower() not in {"blocked", "needs_input"}:
        return {"action": "none", "kind": "", "keys": [], "blocked_reason": "", "auto_unblock_enabled": auto_unblock_enabled}

    raw_active_frame = _extract_active_bottom_frame(text)
    active_frame = _strip_tui_box_chrome(raw_active_frame)
    if not active_frame:
        return {"action": "none", "kind": "", "keys": [], "blocked_reason": "", "auto_unblock_enabled": auto_unblock_enabled}

    mcp_prompt = _extract_mcp_tool_prompt(text, raw_active_frame)
    if mcp_prompt is not None:
        if mcp_prompt.get("ambiguous"):
            return {
                "action": "owner_decision",
                "kind": "ambiguous_dialog",
                "keys": [],
                "blocked_reason": "owner_decision_required",
                "auto_unblock_enabled": auto_unblock_enabled,
            }
        mcp_server = _text(mcp_prompt.get("mcp_server"))
        mcp_tool = _text(mcp_prompt.get("mcp_tool"))
        if not _is_whitelisted_mcp_tool(mcp_server, mcp_tool):
            return {
                "action": "owner_decision",
                "kind": "non_whitelisted_mcp_tool",
                "keys": [],
                "mcp_server": mcp_server,
                "mcp_tool": mcp_tool,
                "blocked_reason": "owner_decision_required",
                "auto_unblock_enabled": auto_unblock_enabled,
            }
        kind = "whitelisted_mcp_tool"
        keys = ["down", "enter"] if mcp_prompt.get("has_remember_option") else ["enter"]
        signature_basis = _text(mcp_prompt.get("signature_text")) or active_frame
        if not auto_unblock_enabled:
            return {
                "action": "owner_decision",
                "kind": kind,
                "keys": [],
                "mcp_server": mcp_server,
                "mcp_tool": mcp_tool,
                "blocked_reason": "passive_watch_no_verified_apply_receipt",
                "auto_unblock_enabled": False,
            }
        signature = f"{kind}:{hashlib.sha256(signature_basis.encode('utf-8')).hexdigest()[:12]}"
        if already_unblocked_signatures is not None and signature in already_unblocked_signatures:
            return {
                "action": "already_unblocked",
                "kind": kind,
                "keys": keys,
                "mcp_server": mcp_server,
                "mcp_tool": mcp_tool,
                "blocked_reason": "",
                "auto_unblock_enabled": True,
            }
        cmd = [herdr_bin, "agent", "send-keys", target_ref, *keys]
        if runner is None and not target_ref:
            ok = True
        else:
            effective_runner = runner or _run_command
            res = effective_runner(cmd, timeout=5.0)
            ok = getattr(res, "returncode", 1) == 0
        if ok and already_unblocked_signatures is not None:
            already_unblocked_signatures.add(signature)
        return {
            "action": "auto_unblocked" if ok else "unblock_failed",
            "ok": ok,
            "kind": kind,
            "keys": keys,
            "mcp_server": mcp_server,
            "mcp_tool": mcp_tool,
            "command": cmd,
            "target": target_ref,
            "blocked_reason": "" if ok else "unblock_send_keys_failed",
            "auto_unblock_enabled": True,
        }

    # Business / Spec ask_user or custom decision questions must NEVER be auto-unblocked.
    if _BUSINESS_DECISION_RE.search(active_frame):
        return {
            "action": "owner_decision",
            "kind": "ask_user",
            "keys": [],
            "blocked_reason": "owner_decision_required",
            "auto_unblock_enabled": auto_unblock_enabled,
        }

    baseline_roots = _safe_roots_for_target(include_target_cwd=False)
    safe_roots = _safe_roots_for_target(target_cwd, include_target_cwd=True)

    is_cmd_prompt = bool(
        re.search(
            r"(?:CMD_CONFIRM|Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command|"
            r"Do\s+you\s+want\s+to\s+run|Allow\s+running|Run\s+shell\s+command)",
            active_frame,
            re.I,
        )
    )
    is_path_prompt = bool(
        re.search(
            r"(?:Allow\s+path\s+access|Allow\s+directory\s+access|Read\s+access\s+covers\s+this\s+directory|\baccess\s+to\b)",
            active_frame,
            re.I,
        )
    )
    is_folder_trust_prompt = bool(
        re.search(
            r"(?:Confirm\s+folder\s+trust|Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|Trust\s+folder|Yes,\s*I\s+trust)",
            active_frame,
            re.I,
        )
    )
    is_curate_memory_prompt = bool(
        re.search(r"(?:Curate\s+Memory|curate\s+this\s+memory|\bstore_memory\b|\bvote_memory\b)", active_frame, re.I)
        and re.search(r"(?:\?|[>❯●◉▶]|\bAllow\b|\bConfirm\b|\bYes\b|1[.)])", active_frame, re.I)
    )

    # Also detect any unrecognized question lines in active_frame alongside a dialog header
    other_questions = [
        ln.strip()
        for ln in active_frame.splitlines()
        if ln.strip().endswith("?")
        and not re.search(
            r"(?:Allow\s+(?:path|directory)\s+access|Read\s+access\s+covers\s+this\s+directory|"
            r"Do\s+you\s+want\s+to\s+allow\s+this|Do\s+you\s+want\s+to\s+use\s+this\s+tool|"
            r"Allow\s+Copilot\s+to\s+curate\s+this\s+memory|"
            r"Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|"
            r"Confirm\s+folder\s+trust|Trust\s+folder|Curate\s+Memory|store_memory|vote_memory|"
            r"CMD_CONFIRM|Do\s+you\s+want\s+to\s+run|Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command)",
            ln,
            re.I,
        )
    ]
    matched_kinds = sum(bool(flag) for flag in (is_cmd_prompt, is_path_prompt, is_folder_trust_prompt, is_curate_memory_prompt))
    if matched_kinds > 1 or (matched_kinds == 1 and other_questions):
        return {
            "action": "owner_decision",
            "kind": "ambiguous_dialog",
            "keys": [],
            "blocked_reason": "owner_decision_required",
            "auto_unblock_enabled": auto_unblock_enabled,
        }

    kind = ""
    keys: list[str] = []

    if is_cmd_prompt:
        cmd_block = _extract_command_block_from_frame(active_frame)
        if cmd_block is not None and _is_whitelisted_command(cmd_block):
            kind = "whitelisted_cmd_confirm"
            keys = ["enter"]
        else:
            return {
                "action": "owner_decision",
                "kind": "non_whitelisted_cmd",
                "keys": [],
                "blocked_reason": "owner_decision_required",
                "auto_unblock_enabled": auto_unblock_enabled,
            }
    elif is_path_prompt:
        dialog_paths = _extract_dialog_paths(raw_active_frame)
        if dialog_paths and all(_path_is_under_safe_roots(p, safe_roots) for p in dialog_paths):
            kind = "allow_path_access"
            pkg_roots = [os.path.abspath(r).rstrip("/") for r in READONLY_PACKAGE_MANAGER_ROOTS]
            is_pkg_or_symlink_path = any(
                ("->" in p or "→" in p)
                or _lexical_within(os.path.abspath(_expand_cwd(p)), pkg_roots)
                for p in dialog_paths
            )
            has_tui_remember = is_pkg_or_symlink_path or (
                bool(re.search(r"(?m)^[>❯]?\s*1[.)]\s*Yes\b", active_frame, re.I))
                and (
                    (
                        any(ch in raw_active_frame for ch in ("│", "╭", "╰"))
                        and bool(re.search(r"(?m)^2[.)]\s*Yes,\s*and\s+remember\b", active_frame, re.I))
                    )
                    or bool(
                        re.search(
                            r"(?m)^2[.)]\s*Yes,\s*and\s+(?:remember\s+(?:this|these)\s+paths?\b|don't\s+ask\s+again\b)",
                            active_frame,
                            re.I,
                        )
                    )
                )
            )
            keys = ["down", "enter"] if has_tui_remember else ["enter"]
        else:
            return {
                "action": "owner_decision",
                "kind": "unsafe_path_access",
                "keys": [],
                "blocked_reason": "owner_decision_required",
                "auto_unblock_enabled": auto_unblock_enabled,
            }
    elif is_folder_trust_prompt:
        single_path = _extract_single_dialog_path(active_frame)
        has_any_path_like = bool(re.search(r"(?:^|[\s'\"`:])(?:~|/)[A-Za-z0-9_.-]", active_frame))
        if single_path is not None:
            if _path_is_under_safe_roots(single_path, baseline_roots):
                kind = "confirm_folder_trust"
                keys = ["enter"]
            else:
                return {
                    "action": "owner_decision",
                    "kind": "untrusted_folder",
                    "keys": [],
                    "blocked_reason": "owner_decision_required",
                    "auto_unblock_enabled": auto_unblock_enabled,
                }
        elif not has_any_path_like and target_cwd and _path_is_under_safe_roots(target_cwd, baseline_roots):
            kind = "confirm_folder_trust"
            keys = ["enter"]
        else:
            return {
                "action": "owner_decision",
                "kind": "untrusted_folder",
                "keys": [],
                "blocked_reason": "owner_decision_required",
                "auto_unblock_enabled": auto_unblock_enabled,
            }
    elif is_curate_memory_prompt:
        kind = "curate_memory"
        keys = ["enter"]

    if not kind:
        if re.search(r"(?:\?|\bconfirm\b|\bapprove\b|\bchoose\b|\bselect\b|\[y/n\])", active_frame, re.I):
            return {
                "action": "owner_decision",
                "kind": "owner_prompt",
                "keys": [],
                "blocked_reason": "owner_decision_required",
                "auto_unblock_enabled": auto_unblock_enabled,
            }
        return {"action": "none", "kind": "", "keys": [], "blocked_reason": "", "auto_unblock_enabled": auto_unblock_enabled}

    if not auto_unblock_enabled:
        return {
            "action": "owner_decision",
            "kind": kind,
            "keys": [],
            "blocked_reason": "passive_watch_no_verified_apply_receipt",
            "auto_unblock_enabled": False,
        }

    signature = f"{kind}:{hashlib.sha256(active_frame.encode('utf-8')).hexdigest()[:12]}"
    if already_unblocked_signatures is not None and signature in already_unblocked_signatures:
        return {"action": "already_unblocked", "kind": kind, "keys": keys, "blocked_reason": "", "auto_unblock_enabled": True}

    cmd = [herdr_bin, "agent", "send-keys", target_ref, *keys]
    if runner is None and not target_ref:
        ok = True
    else:
        effective_runner = runner or _run_command
        res = effective_runner(cmd, timeout=5.0)
        ok = getattr(res, "returncode", 1) == 0
    if ok and already_unblocked_signatures is not None:
        already_unblocked_signatures.add(signature)
    return {
        "action": "auto_unblocked" if ok else "unblock_failed",
        "ok": ok,
        "kind": kind,
        "keys": keys,
        "command": cmd,
        "target": target_ref,
        "blocked_reason": "" if ok else "unblock_send_keys_failed",
        "auto_unblock_enabled": True,
    }

def _classify_and_unblock_dialog(
    visible_text: str,
    *,
    target_ref: str = "",
    target_cwd: str = "",
    live_status: str = "blocked",
    auto_unblock_enabled: bool = False,
    herdr_bin: str = "herdr",
    runner: CommandRunner = _run_command,
    already_unblocked_signatures: set[str] | None = None,
) -> dict[str, Any]:
    """Classify the bottom-most active dialog frame and auto-unblock only when authorized."""
    return _classify_permission_prompt(
        visible_text,
        target_cwd=target_cwd,
        target_ref=target_ref,
        live_status=live_status,
        auto_unblock_enabled=auto_unblock_enabled,
        herdr_bin=herdr_bin,
        runner=runner,
        already_unblocked_signatures=already_unblocked_signatures,
    )

def _summarize_visible_lines(text: str, *, max_lines: int = 8) -> str:
    raw_lines = [re.sub(r"\s+", " ", line).strip(" ┃│╻╹▄▀") for line in _text(text).splitlines()]
    word_lines = [line for line in raw_lines if re.search(r"\w", line)]
    filtered = [line for line in word_lines if not _VOLATILE_TUI_CHROME_RE.search(line)]
    chosen = filtered if filtered else word_lines
    return "\n".join(chosen[-max_lines:])[-800:]

def _read_herdr_visible(binary: str, target_ref: str, *, runner: CommandRunner = _run_command, lines: int = 40) -> str:
    if not target_ref:
        return ""
    try:
        res = runner([binary, "agent", "read", target_ref, "--source", "visible", "--lines", str(lines)], timeout=5.0)
    except (OSError, subprocess.SubprocessError):
        return ""
    if getattr(res, "returncode", 1) != 0:
        return ""
    stdout = _text(getattr(res, "stdout", ""))
    parsed = _parse_json_output(stdout)
    if isinstance(parsed, dict):
        for key in ("visible", "visible_text", "text", "content", "output"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                return val
        result = parsed.get("result")
        if isinstance(result, dict):
            for key in ("visible", "visible_text", "text", "content", "output"):
                val = result.get(key)
                if isinstance(val, str) and val.strip():
                    return val
    return stdout
