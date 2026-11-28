from __future__ import annotations

from ._imports import *

from skill_runtime.cli_contract import SelfDisclosingArgumentParser, redact_text

from skill_runtime.fs_guard import safe_read_text

from .constants_and_paths import _run_command

def compute_board_fingerprint(state_or_path: dict[str, Any] | str | Path | None = None) -> str:
    """Compute a deterministic SHA-256 fingerprint of local supervision slot statuses and summaries."""
    if isinstance(state_or_path, dict):
        state = state_or_path
    else:
        state_path = _resolve_supervision_state_path(state_or_path, persist_by_default=True)
        state = {}
        if state_path and state_path.is_file():
            try:
                loaded = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state = loaded
            except (OSError, ValueError):
                state = {}
    slots_payload: list[dict[str, Any]] = []
    for raw_slot in (state.get("slots") or []):
        if not isinstance(raw_slot, dict):
            continue
        summary = _text(raw_slot.get("m1_progress") or raw_slot.get("visible_summary") or raw_slot.get("sub_prompt"))
        slots_payload.append({
            "slot_id": _text(raw_slot.get("slot_id")),
            "run_label": _text(raw_slot.get("run_label")),
            "topic_id": _text(raw_slot.get("topic_id")),
            "target_repo": _text(raw_slot.get("target_repo")),
            "target": _text(raw_slot.get("target")),
            "pane_id": _text(raw_slot.get("pane_id")),
            "linear_issue": _text(raw_slot.get("linear_issue")),
            "status": _text(raw_slot.get("status")),
            "raw_status": _text(raw_slot.get("raw_status")),
            "retained": bool(raw_slot.get("retained")),
            "needs_owner_attention": bool(raw_slot.get("needs_owner_attention")),
            "blocked_reason": _text(raw_slot.get("blocked_reason")),
            "summary": summary,
            "active_summary_source": _text(raw_slot.get("active_summary_source")),
        })
    slots_payload.sort(key=lambda item: (item["slot_id"], item["pane_id"], item["target"]))
    backlog_payload = [
        {
            "topic_id": _text(b.get("topic_id") or b.get("id")),
            "issue": _text(b.get("issue")),
            "summary": _text(b.get("summary") or b.get("title")),
        }
        for b in (state.get("backlog") or [])
        if isinstance(b, dict)
    ]
    backlog_payload.sort(key=lambda item: (item["topic_id"], item["issue"], item["summary"]))
    canonical = {"slots": slots_payload, "backlog": backlog_payload}
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

def install_m2_board_hook(
    *,
    hooks_dir: str | Path | None = None,
    hook_script_source: str | Path | None = None,
) -> dict[str, Any]:
    """Idempotently install m2-board.json and m2-board-hook.sh into ~/.copilot/hooks without touching sibling hooks."""
    target_dir = Path(hooks_dir or os.environ.get("COPILOT_HOOKS_DIR") or (Path.home() / ".copilot" / "hooks")).expanduser()
    source_script = Path(hook_script_source or DEFAULT_HOOK_SCRIPT_PATH).expanduser()
    if not source_script.is_file():
        raise RouterError(f"hook script source not found: {source_script}")
    target_dir.mkdir(parents=True, exist_ok=True)

    installed_script = target_dir / "m2-board-hook.sh"
    installed_json = target_dir / "m2-board.json"
    source_text = source_script.read_text(encoding="utf-8")

    updated_script = False
    existing_script_text = None
    if installed_script.is_file():
        try:
            existing_script_text = installed_script.read_text(encoding="utf-8")
        except OSError:
            existing_script_text = None
    if existing_script_text != source_text or not os.access(installed_script, os.X_OK):
        tmp_script = installed_script.with_suffix(f".{os.getpid()}.tmp")
        tmp_script.write_text(source_text, encoding="utf-8")
        tmp_script.chmod(0o755)
        tmp_script.replace(installed_script)
        installed_script.chmod(0o755)
        updated_script = True

    hook_cmd = f"sh '{installed_script}'"
    hook_config = {
        "version": 1,
        "hooks": {
            "agentStop": [
                {
                    "type": "command",
                    "bash": hook_cmd,
                    "timeoutSec": 5,
                }
            ],
            "sessionEnd": [
                {
                    "type": "command",
                    "bash": hook_cmd,
                    "timeoutSec": 5,
                }
            ],
        },
    }
    desired_json_text = json.dumps(hook_config, ensure_ascii=False, indent=2) + "\n"
    updated_json = False
    existing_json_text = None
    if installed_json.is_file():
        try:
            existing_json_text = installed_json.read_text(encoding="utf-8")
        except OSError:
            existing_json_text = None
    if existing_json_text != desired_json_text:
        tmp_json = installed_json.with_suffix(f".{os.getpid()}.tmp")
        tmp_json.write_text(desired_json_text, encoding="utf-8")
        tmp_json.replace(installed_json)
        updated_json = True

    preserved = sorted(
        p.name for p in target_dir.iterdir()
        if p.name not in {"m2-board.json", "m2-board-hook.sh"}
    )
    return {
        "ok": True,
        "hooks_dir": str(target_dir),
        "hook_json_path": str(installed_json),
        "hook_script_path": str(installed_script),
        "updated_json": updated_json,
        "updated_script": updated_script,
        "preserved_files": preserved,
    }

def supervision_status(
    *, state_file: str | Path | None = None, plan_file: str | Path | None = None,
    apply_receipt_file: str | Path | None = None, handoff_root: str | Path | None = None,
    agent_records: list[dict[str, Any]] | None = None, herdr_bin: str | None = None,
    runner: CommandRunner = _run_command, discover: bool = True,
    sync_linear: bool = False, no_sync_linear: bool = False,
    hub_issue: str | None = None, local_hostname: str | None = None,
    cross_device_state: dict[str, Any] | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state_path = _resolve_supervision_state_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file, persist_by_default=True)
    binary = _text(herdr_bin or os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr")
    records = agent_records if agent_records is not None else (_live_agent_records(binary, runner=runner) if discover else [])
    settled_in_status: list[str] = []

    def refresh(state: dict[str, Any]) -> dict[str, Any]:
        if discover or agent_records is not None:
            _discover_and_enrich_global_m2_slots(
                state,
                agent_records=records,
                runner=runner,
                herdr_bin=binary,
                handoff_root=handoff_root,
                allow_quiescent_settle=True,
                settled_slot_ids_out=settled_in_status,
            )
        state["slots"] = _compute_board_rollup(state, just_settled_slots=settled_in_status)["slots"]
        return state

    state = _with_locked_supervision_state(state_path, refresh)
    board = _compute_board_rollup(state, just_settled_slots=settled_in_status)
    cache_path = _resolve_cross_device_cache_path(state_file, plan_file=plan_file, apply_receipt_file=apply_receipt_file)
    should_sync, sync_reason = _should_trigger_linear_sync(
        sync_linear=sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=False,
        state_path=state_path,
        linear_runner=linear_runner,
        hub_issue_payload=hub_issue_payload,
    )
    linear_sync_result: dict[str, Any] | None = None
    if should_sync:
        linear_sync_result = sync_cross_device_hub(
            board,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
        )
        if cross_device_state is None and isinstance(linear_sync_result.get("state"), dict):
            cross_device_state = linear_sync_result["state"]
    elif sync_linear or no_sync_linear:
        linear_sync_result = {"ok": False, "updated": False, "skipped_reason": sync_reason}
    _attach_cross_device_to_rollup(
        board,
        cross_device_state,
        local_hostname=local_hostname,
        cache_path=cache_path,
    )
    out: dict[str, Any] = {"ok": True, "supervision_state_path": str(state_path), **board, "board_rollup": board, "summary_markdown": board["unified_rollup_markdown"]}
    if linear_sync_result is not None:
        out["linear_sync"] = linear_sync_result
    return out

class _SelfDisclosingRouterParser(SelfDisclosingArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(
            f"m2-router: {message}\n"
            "  Fix: pass one mode flag (`--plan`, `--rag-query`, `--apply`, `--watch`, `--status`, `--gc`, `--sync-linear`, `--board-fingerprint`, or `--install-hook`).\n"
            "  Reference: skills/personal-m2-assistant/references/eval-cases.json · skills/m2-dispatcher/references/bots-and-agents.md · skills/m2-dispatcher/references/pr-dag-auto-heal.md · skills/m2-dispatcher/references/runtime-profiles.yaml\n"
        )
        raise SystemExit(2)

def _parser() -> argparse.ArgumentParser:
    parser = _SelfDisclosingRouterParser(description="Plan and owner-gated dispatch for a unified M2 ingress")
    mode = parser.add_mutually_exclusive_group(required=False)
    mode.add_argument("--plan", action="store_true", help="build and print a dispatch preview")
    mode.add_argument("--rag-query", help="search historical handoff specs/ledgers/receipts via Hybrid RAG (<=12KB)")
    mode.add_argument("--apply", action="store_true", help="apply a previously saved plan")
    mode.add_argument("--watch", action="store_true", help="watch dispatched target M2 sessions until quiescent")
    mode.add_argument("--status", action="store_true", help="refresh the cross-round unified M2 rollup")
    mode.add_argument("--sync-herdr-to-issue", "--post-apply-sync", dest="sync_herdr_to_issue", action="store_true", help="scan live Herdr spaces/agents in background and write back to Linear Issue TIA-463")
    mode.add_argument("--gc", action="store_true", help="preview or owner-approved safe global cleanup")
    mode.add_argument("--board-fingerprint", action="store_true", help="print SHA-256 fingerprint of local supervision state")
    mode.add_argument("--install-hook", action="store_true", help="idempotently install m2-board.json and m2-board-hook.sh into ~/.copilot/hooks")
    parser.add_argument("--use-jev", action="store_true", help="enable Jev/Falcon reranking for --rag-query or --plan handoff RAG")
    parser.add_argument("--rag-top-k", "--top-k", dest="rag_top_k", type=int, default=5, help="max historical handoff hits to return for --rag-query (default: 5)")
    parser.add_argument("--hooks-dir", help="target hooks directory for --install-hook (default: ~/.copilot/hooks)")
    parser.add_argument("--sync-linear", action="store_true", help="bidirectional per-device shard sync with Linear cross-device M2 hub (TIA-463)")
    parser.add_argument("--no-sync-linear", action="store_true", help="explicitly disable automatic Linear cross-device hub sync")
    parser.add_argument("--hub-issue", default=os.environ.get("M2_SYNC_HUB_ISSUE", DEFAULT_M2_SYNC_HUB_ISSUE), help="Linear cross-device M2 sync hub issue identifier (default: TIA-463)")
    parser.add_argument("--hub-issue-json", help="offline Linear hub issue JSON fixture for --plan / --sync-herdr-to-issue")
    parser.add_argument("--target-issue", default="", help="explicit Linear issue identifier to write watchdog result into")
    parser.add_argument("--live-herdr", "--refresh-herdr", dest="live_herdr", action="store_true", help="force live Herdr pane/agent/workspace scan during --plan instead of reading Issue cache")
    parser.add_argument("--spawn-post-apply-sync", action="store_true", help="spawn detached background --sync-herdr-to-issue process during --apply")
    parser.add_argument("--local-hostname", default=os.environ.get("M2_LOCAL_HOSTNAME", ""), help="override local device hostname shard key")
    parser.add_argument("--handoff-root", help="Goal ledger root (default: M2_HANDOFF_ROOT or ~/Development/handoff)")
    parser.add_argument("--message")
    parser.add_argument("--message-file")
    parser.add_argument("--plan-file")
    parser.add_argument("--apply-receipt-file", help="optional apply receipt JSON file for --watch")
    parser.add_argument("--state-file", help="optional supervision state JSON file for per-M2 cumulative rollup")
    parser.add_argument("--reset-state", action="store_true", help="reset supervision state ledger before --apply")
    parser.add_argument("--watch-target", "--target-id", dest="watch_target", default="", help="watch a single M2 target/slot and update cumulative rollup immediately on completion")
    parser.add_argument("--preview-md-file", help="optional explicit markdown export path for --plan preview")
    parser.add_argument("--no-open-preview", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--orca-bin", help=argparse.SUPPRESS)
    parser.add_argument("--interval", type=float, default=15.0, help="polling interval in seconds for --watch")
    parser.add_argument("--max-wait", type=float, default=2400.0, help="max wait in seconds for --watch")
    parser.add_argument("--max-polls", type=int, default=None, help="optional max poll iterations for --watch")
    parser.add_argument("--sessions-json", help="combined discovery fixture")
    parser.add_argument("--herdr-json", help="combined Herdr discovery fixture")
    parser.add_argument("--herdr-pane-json", "--pane-json", dest="herdr_pane_json", help="Herdr pane list fixture")
    parser.add_argument("--herdr-agent-json", "--agent-json", dest="herdr_agent_json", help="Herdr agent list fixture")
    parser.add_argument("--herdr-workspace-json", dest="herdr_workspace_json", help="Herdr workspace list fixture")
    parser.add_argument("--dsh-state-json")
    parser.add_argument("--dsh-state-url", default=os.environ.get("DSH_M2_STATE_URL", ""))
    parser.add_argument("--herdr-bin")
    parser.add_argument("--dsh-token", default=os.environ.get("DSH_M2_TOKEN", ""))
    parser.add_argument("--transport", choices=("auto", "herdr", "dsh", "stdout"), default="auto")
    parser.add_argument("--magpie-url", default=os.environ.get("MAGPIE_BASE_URL", MAGPIE_BASE_URL))
    parser.add_argument("--magpie-model", default=os.environ.get("MAGPIE_MODEL", MAGPIE_DEFAULT_MODEL))
    parser.add_argument("--domain-map-file", default=str(DEFAULT_DOMAIN_MAP_PATH))
    parser.add_argument("--register-new-domains", action="store_true", help="register approved new domain candidates during --apply")
    parser.add_argument("--cleanup-duplicates", action="store_true", help="close safe duplicate idle Herdr panes during --apply")
    parser.add_argument("--replace-stale-m2", action="store_true", help="close safe idle stale panes and spawn a fresh M2 during --apply")
    parser.add_argument("--targets-json", help="target list/object supplied by an outer CLI model")
    parser.add_argument("--strict-prompts", action="store_true", help="fail --plan when target instructions lack actionable context")
    parser.add_argument("--mock-magpie-json", help="offline Magpie response fixture")
    parser.add_argument("--mock-jev-json", help="legacy Jev fixture alias")
    parser.add_argument("--approve", "--yes", action="store_true", help="explicitly approve dispatch")
    parser.add_argument("--caller-bot", default=os.environ.get("M2_CALLER_BOT", ""), help="caller bot kind (e.g. grok) for quota-protection routing")
    parser.add_argument("--no-supervision", action="store_true", help="disable background --watch watchdog tasks for quota-sensitive callers")
    parser.add_argument("--no-routine", action="store_true", help="disable periodic routine polling for quota-sensitive callers")
    parser.add_argument("--force-watch", action="store_true", help="force --watch polling even when Grok Bot quota protection is active")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--no-herdr", action="store_true")
    parser.add_argument("--no-dsh", action="store_true")
    return parser

def _message(args: argparse.Namespace) -> str:
    if args.message is not None:
        return args.message
    if args.message_file:
        value = safe_read_text(args.message_file)
        if value is None:
            raise OSError(f"unable to safely read message file: {args.message_file}")
        return value
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""

def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not (args.plan or args.rag_query is not None or args.apply or args.watch or args.status or args.sync_herdr_to_issue or args.gc or args.board_fingerprint or args.install_hook):
        if args.sync_linear:
            args.status = True
        else:
            parser.error("one of the arguments --plan --rag-query --apply --watch --status --sync-herdr-to-issue --gc --sync-linear --board-fingerprint --install-hook is required")
    try:
        hub_issue_payload = _json_value(args.hub_issue_json) if getattr(args, "hub_issue_json", None) else None
        if args.rag_query is not None:
            rag_res = query_handoff_rag(
                args.rag_query,
                handoff_root=args.handoff_root,
                top_k=args.rag_top_k,
                use_jev=args.use_jev,
                mock_jev=_json_value(args.mock_jev_json) if args.mock_jev_json else None,
            )
            if args.json:
                print(json.dumps(rag_res, ensure_ascii=False, sort_keys=True))
            else:
                print(format_rag_query_text(rag_res))
            return 0
        if args.board_fingerprint:
            fp = compute_board_fingerprint(args.state_file)
            output: Any = {"ok": True, "fingerprint": fp} if args.json else fp
        elif args.install_hook:
            output = install_m2_board_hook(hooks_dir=args.hooks_dir)
        elif args.sync_herdr_to_issue:
            herdr_payload = _json_value(args.herdr_json) if args.herdr_json else (_json_value(args.sessions_json) if args.sessions_json else None)
            herdr_pane_payload = _json_value(args.herdr_pane_json) if args.herdr_pane_json else None
            herdr_agent_payload = _json_value(args.herdr_agent_json) if args.herdr_agent_json else None
            herdr_workspace_payload = _json_value(args.herdr_workspace_json) if args.herdr_workspace_json else None
            sync_res = sync_herdr_inventory_to_issue(
                state_file=args.state_file,
                plan_file=args.plan_file,
                apply_receipt_file=args.apply_receipt_file,
                handoff_root=args.handoff_root,
                herdr_payload=herdr_payload,
                herdr_pane_payload=herdr_pane_payload,
                herdr_agent_payload=herdr_agent_payload,
                herdr_workspace_payload=herdr_workspace_payload,
                herdr_bin=args.herdr_bin,
                hub_issue=args.hub_issue,
                local_hostname=args.local_hostname or None,
                hub_issue_payload=hub_issue_payload,
                no_sync_linear=args.no_sync_linear,
            )
            output = sync_res if args.json else sync_res["summary_markdown"]
        elif args.plan:
            payload = _json_value(args.sessions_json) if args.sessions_json else None
            herdr_payload = _json_value(args.herdr_json) if args.herdr_json else payload
            herdr_pane_payload = _json_value(args.herdr_pane_json) if args.herdr_pane_json else None
            herdr_agent_payload = _json_value(args.herdr_agent_json) if args.herdr_agent_json else None
            herdr_workspace_payload = _json_value(args.herdr_workspace_json) if args.herdr_workspace_json else None
            dsh_payload = _json_value(args.dsh_state_json) if args.dsh_state_json else payload
            has_herdr_fixture = any(
                val is not None
                for val in (herdr_payload, herdr_pane_payload, herdr_agent_payload, herdr_workspace_payload)
            )
            use_issue_cache = not bool(args.live_herdr or has_herdr_fixture)
            sessions, warnings = discover_sessions(
                herdr_payload=herdr_payload,
                herdr_pane_payload=herdr_pane_payload,
                herdr_agent_payload=herdr_agent_payload,
                workspace_payload=herdr_workspace_payload,
                dsh_payload=dsh_payload,
                herdr_bin=args.herdr_bin,
                dsh_state_url=args.dsh_state_url,
                dsh_token=args.dsh_token,
                include_herdr=not args.no_herdr,
                include_dsh=not args.no_dsh,
                prefer_issue_cache=use_issue_cache,
                state_file=args.state_file,
                hub_issue=args.hub_issue,
                local_hostname=args.local_hostname or None,
                hub_issue_payload=hub_issue_payload,
                domain_map_file=args.domain_map_file,
                sync_linear=args.sync_linear,
                no_sync_linear=args.no_sync_linear,
            )
            raw_gc_records = _merge_herdr_records(_records(herdr_pane_payload), _records(herdr_agent_payload))
            if not raw_gc_records and herdr_payload is not None:
                raw_gc_records = _records(herdr_payload)
            plan = build_plan(
                _message(args),
                sessions,
                warnings,
                domain_map_file=args.domain_map_file,
                workspace_payload=herdr_workspace_payload,
                mock_magpie=_json_value(args.mock_magpie_json) if args.mock_magpie_json else None,
                targets_json=_json_value(args.targets_json) if args.targets_json else None,
                magpie_url=args.magpie_url,
                magpie_model=args.magpie_model,
                mock_jev=_json_value(args.mock_jev_json) if args.mock_jev_json else None,
                transport=args.transport,
                preview_md_file=args.preview_md_file,
                state_file=args.state_file,
                sync_linear=args.sync_linear,
                no_sync_linear=args.no_sync_linear,
                hub_issue=args.hub_issue,
                local_hostname=args.local_hostname or None,
                hub_issue_payload=hub_issue_payload,
                herdr_bin=args.herdr_bin,
                caller_bot=args.caller_bot,
                no_supervision=args.no_supervision,
                no_routine=args.no_routine,
                strict_prompts=args.strict_prompts,
                agent_records=raw_gc_records if raw_gc_records else ([] if (args.no_herdr or use_issue_cache) else None),
                prefer_issue_cache=use_issue_cache,
                handoff_root=args.handoff_root,
                scan_default_handoff=True,
                use_jev_rag=args.use_jev,
            )
            if raw_gc_records:
                plan["gc_candidates"] = discover_gc_candidates(raw_gc_records, handoff_root=args.handoff_root)
            if args.dsh_state_url:
                plan["discovery"]["dsh_state_url"] = args.dsh_state_url
            output: Any = plan if args.json else preview_text(plan)
        elif args.gc:
            agent_payload = _json_value(args.herdr_agent_json) if args.herdr_agent_json else (_json_value(args.herdr_json) if args.herdr_json else None)
            pane_payload = _json_value(args.herdr_pane_json) if args.herdr_pane_json else None
            workspace_payload = _json_value(args.herdr_workspace_json) if args.herdr_workspace_json else None
            if agent_payload is None and pane_payload is None:
                records = _live_agent_records(_text(args.herdr_bin or os.environ.get("HERDR_BIN") or shutil.which("herdr"), "herdr"))
            else:
                records = _merge_herdr_records(_records(pane_payload), _records(agent_payload))
            workspace_rows = _workspace_inventory(workspace_payload) or []
            labels = {_text(w.get("workspace_id")): _text(w.get("label") or w.get("workspace_label")) for w in workspace_rows if _text(w.get("workspace_id"))}
            for rec in records:
                ws = _text(rec.get("workspace_id") or rec.get("workspaceId"))
                if ws and labels.get(ws):
                    rec["workspace_label"] = labels[ws]
            output = run_gc(
                records,
                approve=args.approve,
                state_file=args.state_file,
                handoff_root=args.handoff_root,
                sync_linear=args.sync_linear,
                no_sync_linear=args.no_sync_linear,
                hub_issue=args.hub_issue,
                local_hostname=args.local_hostname or None,
            )
        elif args.status:
            result = supervision_status(
                state_file=args.state_file, plan_file=args.plan_file, apply_receipt_file=args.apply_receipt_file,
                handoff_root=args.handoff_root, herdr_bin=args.herdr_bin, discover=not args.no_herdr,
                agent_records=_records(_json_value(args.herdr_agent_json or args.herdr_json)) if (args.herdr_agent_json or args.herdr_json) else None,
                sync_linear=args.sync_linear, no_sync_linear=args.no_sync_linear,
                hub_issue=args.hub_issue, local_hostname=args.local_hostname or None,
            )
            output = result if args.json else result["unified_rollup_markdown"]
        elif args.apply:
            if not args.plan_file:
                raise RouterError("--apply requires --plan-file")
            plan = _json_value(args.plan_file)
            if not isinstance(plan, dict):
                raise RouterError("plan file must contain a JSON object")
            output = apply_plan(
                plan,
                approve=args.approve,
                register_new_domains=args.register_new_domains,
                cleanup_duplicates=args.cleanup_duplicates,
                replace_stale_m2=args.replace_stale_m2,
                domain_map_file=args.domain_map_file,
                plan_file=args.plan_file,
                apply_receipt_file=args.apply_receipt_file,
                state_file=args.state_file,
                reset_state=args.reset_state,
                handoff_root=args.handoff_root,
                agent_records=_records(_json_value(args.herdr_agent_json)) if args.herdr_agent_json else ([] if args.no_herdr else None),
                transport=args.transport,
                sync_linear=args.sync_linear,
                no_sync_linear=args.no_sync_linear,
                hub_issue=args.hub_issue,
                local_hostname=args.local_hostname or None,
                hub_issue_payload=hub_issue_payload,
                caller_bot=args.caller_bot,
                no_supervision=args.no_supervision,
                no_routine=args.no_routine,
                spawn_background_sync=bool(getattr(args, "spawn_post_apply_sync", False)),
            )
        else:
            if not args.plan_file:
                raise RouterError("--watch requires --plan-file")
            plan = _json_value(args.plan_file)
            if not isinstance(plan, dict):
                raise RouterError("plan file must contain a JSON object")
            apply_receipt = _json_value(args.apply_receipt_file) if args.apply_receipt_file else None
            if apply_receipt is not None and not isinstance(apply_receipt, dict):
                raise RouterError("apply receipt file must contain a JSON object")
            watch_result = watch_dispatched_m2(
                plan,
                apply_receipt=apply_receipt,
                watch_target=args.watch_target,
                state_file=_resolve_supervision_state_path(args.state_file, plan_file=args.plan_file, apply_receipt_file=args.apply_receipt_file, apply_receipt=apply_receipt, persist_by_default=True),
                handoff_root=args.handoff_root,
                interval=args.interval,
                max_wait=args.max_wait,
                max_polls=args.max_polls,
                herdr_bin=args.herdr_bin,
                dsh_state_url=args.dsh_state_url,
                dsh_token=args.dsh_token,
                sync_linear=args.sync_linear,
                no_sync_linear=args.no_sync_linear,
                hub_issue=args.hub_issue,
                target_issue=args.target_issue or None,
                local_hostname=args.local_hostname or None,
                hub_issue_payload=hub_issue_payload,
                caller_bot=args.caller_bot,
                no_supervision=args.no_supervision,
                no_routine=args.no_routine,
                force_watch=args.force_watch,
                agent_records=_records(_json_value(args.herdr_agent_json or args.herdr_json)) if (args.herdr_agent_json or args.herdr_json) else ([] if args.no_herdr else None),
            )
            output = watch_result if args.json else watch_result["summary_markdown"]
        print(json.dumps(output, ensure_ascii=False, indent=None if args.json else 2, sort_keys=True) if isinstance(output, dict) else output)
        return 0
    except (OSError, ValueError, RouterError, json.JSONDecodeError) as error:
        ref_hint = (
            "skills/personal-m2-assistant/references/eval-cases.json · "
            "skills/m2-dispatcher/references/bots-and-agents.md · "
            "skills/m2-dispatcher/references/pr-dag-auto-heal.md · "
            "skills/m2-dispatcher/references/runtime-profiles.yaml"
        )
        safe_error = redact_text(str(error))
        if args.json:
            print(json.dumps({"ok": False, "error": safe_error, "reference": redact_text(ref_hint)}, ensure_ascii=False))
        else:
            print(f"m2-router: {safe_error}\n  Reference: {redact_text(ref_hint)}", file=sys.stderr)
        return 2
