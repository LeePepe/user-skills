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

_VOLATILE_TUI_CHROME_RE = re.compile(
    r"(?:Session:\s*[\d.]+\s*AIC\s+used"
    r"|←\s*open\s+sidebar"
    r"|Interactive\s*·\s*Manual\s+Approval"
    r"|(?:Working|Thinking)\s*·\s*[\d.]+\s*[KMG]?i?B\b.*context\s*\(\d+%\))",
    re.I,
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

def write_watch_results_to_issues(
    plan: dict[str, Any],
    target_reports: list[dict[str, Any]],
    *,
    state_path: Path | None = None,
    hub_issue: str | None = None,
    target_issue: str | None = None,
    linear_runner: CommandRunner | None = None,
    linear_sync_result: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Directly write each settled/blocked M2 watchdog report to its corresponding Linear Issue."""
    effective_hub = _text(hub_issue or os.environ.get("M2_SYNC_HUB_ISSUE"), DEFAULT_M2_SYNC_HUB_ISSUE)
    exec_runner = linear_runner or _run_command
    linear_script = _text(os.environ.get("M2_LINEAR_SYNC_SCRIPT"), str(LINEAR_SYNC_SCRIPT_PATH))
    plan_id = _text(plan.get("plan_id"), "m2p-plan")
    reported_markers: set[str] = set()
    if state_path and state_path.is_file():
        try:
            loaded_state = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded_state, dict) and isinstance(loaded_state.get("reported_issue_markers"), list):
                reported_markers = {str(m) for m in loaded_state["reported_issue_markers"] if _text(m)}
        except (OSError, ValueError):
            reported_markers = set()

    new_markers: list[str] = []
    receipts: list[dict[str, Any]] = []
    for rep in target_reports:
        if rep.get("superseded_by_run_seq"):
            continue
        slot_id = _text(rep.get("slot_id") or rep.get("run_label"), "m2")
        run_seq = int(rep.get("run_seq") or 1)
        run_label = _text(rep.get("run_label"), slot_id)
        status = _text(rep.get("status"), "done")
        marker = f"<!-- M2-WATCH-REPORT:{plan_id}:{slot_id}:r{run_seq}:{status} -->"

        issue_id = _text(target_issue or rep.get("linear_issue") or plan.get("linear_issue"))
        if not issue_id:
            text_blob = "\n".join(_text(rep.get(k)) for k in ("sub_prompt", "visible_summary", "terminal_title", "m1_progress"))
            found_issues = re.findall(r"\bTIA-\d+\b", text_blob, re.I)
            parent_issue = _text(rep.get("parent_linear_issue")).upper()
            issue_id = next((i.upper() for i in found_issues if i.upper() != parent_issue), "") or (found_issues[0].upper() if found_issues else "")

        topic_id = _text(rep.get("topic_id") or slot_id)
        target_name = _text(rep.get("target") or rep.get("session_id") or slot_id)
        pane_id = _text(rep.get("pane_id"))
        target_repo = _text(rep.get("target_repo"), "unspecified")
        m1_prog = _text(rep.get("m1_progress"), "—")
        vis_summary = _text(rep.get("visible_summary"), "—")
        attn_line = (
            f"\n- **阻塞原因 (`Needs Owner Decision`)**: `{_text(rep.get('blocked_reason'), 'owner_decision_required')}`"
            if rep.get("needs_owner_attention")
            else ""
        )
        report_body = (
            f"{marker}\n"
            f"### 🤖 M2 Watchdog 任务回报 · `{run_label}` (`{topic_id}`)\n"
            f"- **状态 (`Status`)**: `{status}` (`quiescent={bool(rep.get('quiescent'))}`){attn_line}\n"
            f"- **会话 / 窗格 (`Session / Pane`)**: `{target_name}`"
            + (f" (`{pane_id}`)" if pane_id else "")
            + f" · Wave `{rep.get('wave_order', 1)}`\n"
            f"- **关联仓库 (`Target Repo`)**: `{target_repo}`\n"
            f"- **M1 推进进度 (`M1 Progress`)**: {m1_prog}\n"
            f"- **最新结果摘要 (`Result Summary`)**:\n"
            f"```text\n{vis_summary}\n```"
        )

        # If target is bound to a specific issue (distinct from Hub Issue TIA-463, or explicitly requested via --target-issue)
        if issue_id and (issue_id.upper() != effective_hub.upper() or bool(_text(target_issue))):
            if marker in reported_markers:
                receipts.append({
                    "slot_id": slot_id,
                    "issue": issue_id,
                    "ok": True,
                    "updated": False,
                    "skipped_reason": "already_reported",
                    "method": "linear_comment",
                    "marker": marker,
                })
                continue
            handoff_dir = _text(rep.get("handoff_dir"))
            ledger_synced = False
            if handoff_dir and (Path(handoff_dir).expanduser() / "ledger.md").is_file():
                try:
                    sl_res = exec_runner(
                        [sys.executable, linear_script, "sync-ledger", "--handoff-dir", str(Path(handoff_dir).expanduser()), "--quiet"],
                        timeout=15.0,
                    )
                    ledger_synced = getattr(sl_res, "returncode", 1) == 0
                except (OSError, subprocess.SubprocessError):
                    ledger_synced = False
            try:
                c_res = exec_runner(
                    [sys.executable, linear_script, "comment", "--id", issue_id, "--body", report_body],
                    timeout=15.0,
                )
            except (OSError, subprocess.SubprocessError) as error:
                receipts.append({
                    "slot_id": slot_id,
                    "issue": issue_id,
                    "ok": False,
                    "updated": False,
                    "method": "linear_comment",
                    "marker": marker,
                    "error": str(error),
                })
                continue
            if getattr(c_res, "returncode", 1) != 0:
                receipts.append({
                    "slot_id": slot_id,
                    "issue": issue_id,
                    "ok": False,
                    "updated": False,
                    "method": "linear_comment",
                    "marker": marker,
                    "error": _text(getattr(c_res, "stderr", "") or getattr(c_res, "stdout", ""), "comment failed"),
                })
                continue
            reported_markers.add(marker)
            new_markers.append(marker)
            receipts.append({
                "slot_id": slot_id,
                "issue": issue_id,
                "ok": True,
                "updated": True,
                "method": "linear_comment",
                "ledger_synced": ledger_synced,
                "marker": marker,
                "body": report_body,
            })
        else:
            receipts.append({
                "slot_id": slot_id,
                "issue": effective_hub,
                "ok": bool((linear_sync_result or {}).get("ok")),
                "updated": bool((linear_sync_result or {}).get("updated")),
                "method": "hub_issue_sync",
                "marker": marker,
            })

    if new_markers and state_path is not None:
        def _record_markers(st: dict[str, Any]) -> dict[str, Any]:
            existing_m = [str(m) for m in (st.get("reported_issue_markers") or []) if _text(m)]
            for m in new_markers:
                if m not in existing_m:
                    existing_m.append(m)
            st["reported_issue_markers"] = existing_m[-200:]
            return st
        _with_locked_supervision_state(state_path, _record_markers)

    return receipts
