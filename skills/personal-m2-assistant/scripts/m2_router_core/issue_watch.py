from __future__ import annotations

from ._imports import *

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
