from __future__ import annotations

from ._imports import *
from skill_runtime.cli_contract import redact_payload, redact_text
from skill_runtime.hybrid_rag import (
    ACI_MAX_BYTES,
    format_rag_context_hint,
    hybrid_search,
    load_handoff_corpus,
)
from .constants_and_paths import merge_target_prompt

from .constants_and_paths import _run_command

DEFAULT_RAG_TOP_K = 3
MAX_COMBINED_RAG_HINT_BYTES = 840


def _cap_utf8_bytes(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    if max_bytes <= 3:
        return raw[:max_bytes].decode("utf-8", errors="ignore")
    return raw[: max_bytes - 3].decode("utf-8", errors="ignore") + "..."


def resolve_handoff_root(handoff_root: str | Path | None = None) -> Path:
    raw = _text(handoff_root) or _text(os.environ.get("M2_HANDOFF_ROOT")) or "~/Development/handoff"
    return Path(raw).expanduser()


def _mock_jev_reranker(mock_jev: Any) -> Callable[..., Any] | None:
    if callable(mock_jev):
        return mock_jev
    if isinstance(mock_jev, dict) and mock_jev:
        answers = mock_jev.get("answers") if isinstance(mock_jev.get("answers"), dict) else mock_jev
        if isinstance(answers, dict) and any(str(k).startswith("cand_") for k in answers):
            return lambda state, questions, **kwargs: mock_jev
    return None


def _compact_rag_hit(hit: dict[str, Any]) -> dict[str, Any]:
    meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    compact: dict[str, Any] = {
        "doc_id": _text(hit.get("doc_id")),
        "title": _text(hit.get("title")),
        "score": round(float(hit.get("score") or 0.0), 6),
        "bm25_score": round(float(hit.get("bm25_score") or 0.0), 6),
        "snippet": _text(hit.get("snippet")),
        "path": _text(hit.get("path")),
        "tags": [str(t) for t in (hit.get("tags") or [])[:8]],
    }
    if hit.get("jev_score") is not None:
        compact["jev_score"] = round(float(hit["jev_score"]), 6)
    if _text(meta.get("goal_id")):
        compact["goal_id"] = _text(meta.get("goal_id"))
    if isinstance(meta.get("owned_paths"), list) and meta["owned_paths"]:
        compact["owned_paths"] = [str(p) for p in meta["owned_paths"][:6]]
    if isinstance(meta.get("events"), list) and meta["events"]:
        compact["events"] = [str(e) for e in meta["events"][:4]]
    return redact_payload(compact)


def query_handoff_rag(
    query: str,
    *,
    handoff_root: str | Path | None = None,
    top_k: int = 5,
    use_jev: bool = False,
    mock_jev: Any = None,
    docs: list[Any] | None = None,
) -> dict[str, Any]:
    clean_query = _text(query)
    if not clean_query:
        raise RouterError("--rag-query requires a non-empty query")
    if int(top_k) <= 0:
        raise RouterError("--rag-top-k must be >= 1")
    resolved_root = resolve_handoff_root(handoff_root)
    corpus = docs if docs is not None else load_handoff_corpus(resolved_root)
    result = hybrid_search(
        clean_query,
        corpus,
        corpus_scope="handoff",
        top_k=int(top_k),
        use_jev=bool(use_jev),
        jev_fn=_mock_jev_reranker(mock_jev),
    )
    compact_hits = [_compact_rag_hit(h) for h in (result.get("hits") or []) if isinstance(h, dict)]
    hint = format_rag_context_hint(compact_hits) if compact_hits else ""
    safe_root = _cap_utf8_bytes(redact_text(str(resolved_root)), 512)
    payload: dict[str, Any] = redact_payload({
        **result,
        "handoff_root": safe_root,
        "hits": compact_hits,
        "hit_count": len(compact_hits),
        "rag_context_hint": hint,
    })
    while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > ACI_MAX_BYTES:
        if payload["hits"]:
            payload["hits"].pop()
            payload["hit_count"] = len(payload["hits"])
            payload["rag_context_hint"] = format_rag_context_hint(payload["hits"])
            payload["truncated"] = True
        else:
            payload["rag_context_hint"] = ""
            payload["handoff_root"] = _cap_utf8_bytes(str(payload.get("handoff_root") or ""), 128)
            payload["query"] = _cap_utf8_bytes(str(payload.get("query") or ""), 128)
            payload["truncated"] = True
            break
    return payload


def enrich_targets_with_handoff_rag(
    targets: list[dict[str, Any]],
    *,
    handoff_root: str | Path | None = None,
    scan_default: bool = False,
    top_k: int = DEFAULT_RAG_TOP_K,
    use_jev: bool = False,
    mock_jev: Any = None,
) -> None:
    if not targets:
        return
    explicit_root = _text(handoff_root) or _text(os.environ.get("M2_HANDOFF_ROOT"))
    if not explicit_root and not scan_default:
        for target in targets:
            target.setdefault("rag_hits", [])
        return
    corpus = load_handoff_corpus(resolve_handoff_root(explicit_root or None))
    if not corpus:
        for target in targets:
            target.setdefault("rag_hits", [])
        return
    jev_fn = _mock_jev_reranker(mock_jev)
    for target in targets:
        topic_part = _text(target.get("topic_id"))
        repo_part = _text(target.get("target_repo"))
        if repo_part == "unspecified":
            repo_part = ""
        prompt_part = _text(target.get("sub_prompt"))
        query = " ".join(part for part in (topic_part, repo_part, prompt_part) if part).strip()
        if not query:
            target.setdefault("rag_hits", [])
            continue
        res = hybrid_search(
            query,
            corpus,
            corpus_scope="handoff",
            top_k=max(1, int(top_k)),
            use_jev=bool(use_jev),
            jev_fn=jev_fn,
        )
        hits = [_compact_rag_hit(h) for h in (res.get("hits") or []) if isinstance(h, dict)]
        target["rag_hits"] = hits
        rag_hint = format_rag_context_hint(hits)
        if rag_hint:
            existing = _text(target.get("rag_context_hint"))
            if not existing:
                target["rag_context_hint"] = rag_hint
            elif rag_hint not in existing:
                target["rag_context_hint"] = _cap_utf8_bytes(
                    redact_text(f"{existing} | {rag_hint}"),
                    MAX_COMBINED_RAG_HINT_BYTES,
                )


def format_rag_query_text(payload: dict[str, Any]) -> str:
    lines = [
        f"Handoff Hybrid RAG · query={_text(payload.get('query'))!r} · backend={_text(payload.get('backend'), 'bm25')} · hits={payload.get('hit_count', 0)}/{payload.get('total_docs', 0)}",
    ]
    for idx, hit in enumerate(payload.get("hits") or [], start=1):
        lines.append(
            f"{idx}. [{_text(hit.get('doc_id'))}] {_text(hit.get('title'))} (score={hit.get('score', 0.0)}): {_text(hit.get('snippet'))}"
        )
    if _text(payload.get("rag_context_hint")):
        lines.append(f"rag_context_hint: {_text(payload.get('rag_context_hint'))}")
    return _cap_utf8_bytes(redact_text("\n".join(lines)), ACI_MAX_BYTES)

def _default_preview_path(plan_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", _text(plan_id, "m2p-plan")).strip("-") or "m2p-plan"
    return Path(f"/tmp/m2-plan-{safe_id}.md")

def _prompt_has_context(prompt: str) -> bool:
    return bool(re.search(r"(?:/|\.(?:py|swift|md|json|yaml|yml|sh|ts|tsx)\b|\bTIA-\d+\b|(?<!\w)#\d+\b|\b\d+[.)]\s+)", prompt, re.I))

def validate_target_prompt_completeness(target: dict[str, Any], message: str, *, target_index: int) -> dict[str, Any] | None:
    if target.get("phase0_discussion_only"):
        return None
    prompt = merge_target_prompt(target, message)
    if len(prompt) >= 80 or _prompt_has_context(prompt):
        return None
    return {
        "target_index": target_index,
        "target_repo": _text(target.get("target_repo"), "unspecified"),
        "reason": "under_specified_sub_prompt",
        "message": "Target instruction is shorter than 80 characters and lacks a file, repository, issue, PR, or numbered acceptance marker.",
        "sub_prompt": prompt,
    }

def render_preview_markdown(plan: dict[str, Any]) -> str:
    plan_id = _text(plan.get("plan_id"), "m2p-unknown")
    route = _text((plan.get("decision") or {}).get("route"), "ROUTE_SINGLE")
    default_label = _text(plan.get("default_model_label"), DEFAULT_M2_MODEL_LABEL)
    quota_mode = _text(plan.get("quota_protection_mode"))
    quota_info = plan.get("quota_protection") if isinstance(plan.get("quota_protection"), dict) else {}
    lines = [
        f"# M2 Dispatch Plan · {plan_id} ({route})",
        "",
        f"- **Message**: {_text(plan.get('message'))}",
        f"- **Route**: `{route}`",
        f"- **Default Model / Context**: `{default_label}` (`{_text(plan.get('default_model'), DEFAULT_M2_MODEL)}` / `{_text(plan.get('default_context'), DEFAULT_M2_CONTEXT)}`)",
        "- **Owner Gate**: Owner approval required before dispatch.",
    ]
    if quota_mode == GROK_QUOTA_PROTECTION_MODE or bool(quota_info.get("active")):
        lines.append(
            f"- **Quota Protection**: `quota_protection_mode: {GROK_QUOTA_PROTECTION_MODE}` "
            "(Grok Bot 省流防烧额度模式：仅执行单次派发 Fire-and-Forget，自动取消后台 `--watch` 监督与定时 Routine 轮询)"
        )
    lines.extend([
        "",
        "## Target Dispatch Table",
        "",
        "| Wave | Topic | Domain Status | Target Repo | Space | M2 Status | Transport | Model / Context | CWD |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    for target in plan.get("targets", []):
        model_label = _text(target.get("model_label"), DEFAULT_M2_MODEL_LABEL)
        cwd = _expand_cwd(target.get("cwd"))
        ws_badge = (
            "new-space"
            if _text(target.get("m2_status")) == "NEW_M2"
            else (_text(target.get("workspace_id") or target.get("target_workspace_id")) or "new-space")
        )
        lines.append(
            f"| {target.get('wave_order', 1)} | `{_text(target.get('topic_id'))}` | "
            f"`{_text(target.get('domain_status'))}` | `{_text(target.get('target_repo'), 'unspecified')}` | "
            f"`{_text(target.get('space'), 'M2')} ({ws_badge})` | "
            f"`{_text(target.get('m2_status'))}` | `{_text(target.get('transport'), 'auto')}` | "
            f"`{model_label}` | `{cwd}` |"
        )
    lines.extend(["", "## Target Instructions"])
    for index, target in enumerate(plan.get("targets", []), start=1):
        ws_badge = (
            "new-space"
            if _text(target.get("m2_status")) == "NEW_M2"
            else (_text(target.get("workspace_id") or target.get("target_workspace_id")) or "new-space")
        )
        lines.extend([
            "",
            f"### {index}. Wave {target.get('wave_order', 1)} · `{_text(target.get('topic_id'))}` → `{_text(target.get('target_repo'), 'unspecified')}` ({_text(target.get('m2_status'))})",
            f"- **Model / Context**: `{_text(target.get('model_label'), DEFAULT_M2_MODEL_LABEL)}` (`{_text(target.get('model'), DEFAULT_M2_MODEL)}` / `{_text(target.get('context'), DEFAULT_M2_CONTEXT)}`)",
            f"- **Space**: `{_text(target.get('space'), 'M2')} ({ws_badge})`",
            f"- **CWD**: `{_expand_cwd(target.get('cwd'))}`",
            f"- **Transport**: `{_text(target.get('transport'), 'auto')}`",
            "",
            "#### `sub_prompt`",
            "```text",
            _text(target.get("sub_prompt")),
            "```",
        ])
        hint = _text(target.get("rag_context_hint"))
        if hint:
            lines.extend([
                "",
                "#### `rag_context_hint`",
                "```text",
                hint,
                "```",
            ])
        recent_ctx = target.get("recent_session_context")
        if isinstance(recent_ctx, dict) and (recent_ctx.get("turns") or recent_ctx.get("summary")):
            lines.extend([
                "",
                "#### `recent_session_context`",
                f"- **Source**: `{_text(recent_ctx.get('source'), 'session')}` · **Last Activity**: `{_text(recent_ctx.get('last_session_activity_at') or target.get('last_session_activity_at'), '—')}`",
            ])
            for t_idx, turn in enumerate(recent_ctx.get("turns") or [], start=1):
                if isinstance(turn, dict):
                    u_m = _text(turn.get("user_message"))
                    a_m = _text(turn.get("assistant_message"))
                    ts_m = _text(turn.get("timestamp"))
                    ts_part = f" (`{ts_m}`)" if ts_m else ""
                    if u_m and a_m:
                        lines.append(f"- **Turn {t_idx}**{ts_part}: User `{u_m}` → Assistant `{a_m}`")
                    elif u_m:
                        lines.append(f"- **Turn {t_idx}**{ts_part}: User `{u_m}`")
                    elif a_m:
                        lines.append(f"- **Turn {t_idx}**{ts_part}: Assistant `{a_m}`")
            if not recent_ctx.get("turns") and _text(recent_ctx.get("summary")):
                lines.append(f"- **Summary**: {_text(recent_ctx.get('summary'))}")
    for target in plan.get("targets", []):
        eviction = target.get("context_eviction") or {}
        if target.get("m2_action") == "REPLACE_STALE_M2":
            lines.extend(["", "## Context Eviction", "", "🔄 旧 Goal 已完结且新旧任务语义无关 -> 抛弃旧上下文重开干净 M2", f"- completion=`{eviction.get('completion_evidence')}`; is_same_goal_continuation=`{eviction.get('is_same_goal_continuation')}`; prior_context_value=`{eviction.get('prior_context_value')}`"])
    stale = plan.get("stale_m2_candidates", [])
    if stale:
        lines.extend(["", "## Stale / Duplicate M2 Cleanup Candidates", ""])
        for candidate in stale:
            lines.append(
                f"- `{_text(candidate.get('topic_id'))}`: `{_text(candidate.get('session_id'))}` "
                f"`{_text(candidate.get('pane_id'))}` [{_text(candidate.get('reason'))}] {_text(candidate.get('title'))}"
            )
    candidates = plan.get("new_domain_candidates", [])
    if candidates:
        lines.extend(["", "## New Domain Candidates", ""])
        for candidate in candidates:
            lines.append(
                f"- `{_text(candidate.get('topic_id'))}`: {_text(candidate.get('title'))} → "
                f"`{_text(candidate.get('target_repo'))}` (`{_text(candidate.get('group'))}`, cwd `{_expand_cwd(candidate.get('cwd'))}`)"
            )
    prompt_warnings = plan.get("prompt_warnings") or []
    if prompt_warnings:
        lines.extend(["", "## ⚠️ Handoff Context Completeness Warnings", ""])
        for warning in prompt_warnings:
            lines.append(
                f"- Target {warning.get('target_index')}: `{_text(warning.get('target_repo'), 'unspecified')}` — "
                f"{_text(warning.get('message'))} Prompt: `{_text(warning.get('sub_prompt'))}`"
            )
    cross_lines = _render_cross_device_rollup_lines({
        "cross_device_in_flight": plan.get("cross_device_in_flight") or [],
        "cross_device_pending_decisions": plan.get("cross_device_pending_decisions") or [],
    })
    if cross_lines:
        lines.extend(["", "## Cross-Device Active & Pending (Linear `TIA-463`)", "", *cross_lines[1:]])
    return "\n".join(lines) + "\n"

def write_preview_markdown(plan: dict[str, Any], path: str | Path | None = None) -> Path:
    preview_path = Path(path).expanduser() if path else _default_preview_path(_text(plan.get("plan_id"), "m2p-plan"))
    markdown = _text(plan.get("preview_markdown"))
    if not markdown:
        markdown = render_preview_markdown(plan)
    else:
        markdown = markdown + ("\n" if not markdown.endswith("\n") else "")
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(markdown, encoding="utf-8")
    plan["preview_markdown"] = markdown
    plan["preview_markdown_path"] = str(preview_path)
    return preview_path

def build_plan(
    message: str,
    sessions: list[dict[str, Any]] | None = None,
    warnings: Iterable[str] = (),
    *,
    domain_map: dict[str, Any] | None = None,
    domain_map_file: str | Path | None = None,
    workspace_payload: Any = None,
    herdr_workspace_payload: Any = None,
    mock_magpie: Any = None,
    targets_json: Any = None,
    magpie_url: str = "",
    magpie_model: str = "",
    mock_jev: Any = None,
    transport: str = "auto",
    preview_md_file: str | Path | None = None,
    write_preview_md: bool = False,
    state_file: str | Path | None = None,
    cross_device_state: dict[str, Any] | None = None,
    sync_linear: bool = False,
    no_sync_linear: bool = False,
    hub_issue: str | None = None,
    local_hostname: str | None = None,
    linear_runner: CommandRunner | None = None,
    hub_issue_payload: dict[str, Any] | None = None,
    runner: CommandRunner = _run_command,
    herdr_bin: str | None = None,
    session_state_dir: str | Path | None = None,
    caller_bot: str = "",
    no_supervision: bool = False,
    no_routine: bool = False,
    agent_records: Iterable[dict[str, Any]] | None = None,
    prefer_issue_cache: bool = False,
    strict_prompts: bool = False,
    handoff_root: str | Path | None = None,
    scan_default_handoff: bool = False,
    use_jev_rag: bool = False,
) -> dict[str, Any]:
    message = _text(message)
    if not message:
        raise RouterError("message is required")
    plan_id = f"m2p-{uuid.uuid4().hex[:10]}"
    loaded_domain_map = domain_map or load_domain_map(domain_map_file)
    warnings_list = list(warnings)
    if sessions is None or (prefer_issue_cache and len(sessions) == 0 and not getattr(sessions, "discovery_source", "")):
        cached_sessions, cache_warn = discover_from_issue_cache(
            state_file=state_file,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
            cross_device_state=cross_device_state,
            domain_map=loaded_domain_map,
            domain_map_file=domain_map_file,
            sync_linear=sync_linear,
            no_sync_linear=no_sync_linear,
        )
        sessions = cached_sessions
        if cache_warn:
            warnings_list.append(cache_warn)
    workspace_inventory = _workspace_inventory(workspace_payload if workspace_payload is not None else herdr_workspace_payload)
    if workspace_inventory is None:
        workspace_inventory = _workspace_records(getattr(sessions, "workspaces", []))
    discovery_source = _text(getattr(sessions, "discovery_source", "")) or ("issue_cache" if prefer_issue_cache else "sessions")
    route, targets, decision = classify(
        message,
        sessions,
        domain_map=loaded_domain_map,
        mock_magpie=mock_magpie,
        targets_json=targets_json,
        magpie_url=magpie_url,
        magpie_model=magpie_model,
        mock=mock_jev,
        goal_id=plan_id,
        workspaces=workspace_inventory,
        runner=runner,
        herdr_bin=herdr_bin,
        session_state_dir=session_state_dir,
    )
    phase0_discussion_only = is_exploratory_discussion_message(message)
    if phase0_discussion_only:
        for target in targets:
            _apply_phase0_discussion_lock(target, message)
    for target in targets:
        if not target.get("phase0_discussion_only"):
            target["sub_prompt"] = merge_target_prompt(target, message)
        topic = _topic_by_id(loaded_domain_map).get(_text(target.get("topic_id")))
        resolve_target_space(target, topic, workspace_inventory, loaded_domain_map)
        if isinstance(target.get("proposed_domain"), dict):
            resolve_target_space(target, None, workspace_inventory, loaded_domain_map)
    effective_handoff_root = handoff_root if handoff_root is not None else os.environ.get("M2_HANDOFF_ROOT")
    if phase0_discussion_only:
        for target in targets:
            target.setdefault("rag_hits", [])
    else:
        enrich_targets_with_handoff_rag(
            targets,
            handoff_root=effective_handoff_root,
            scan_default=scan_default_handoff,
            top_k=DEFAULT_RAG_TOP_K,
            use_jev=use_jev_rag,
            mock_jev=mock_jev if use_jev_rag else None,
        )
    stale_candidates: list[dict[str, Any]] = []
    for target in targets:
        topic = _topic_by_id(loaded_domain_map).get(_text(target.get("topic_id")))
        if transport != "auto" and target["m2_status"] == "EXISTING_M2":
            target["transport"] = transport
        elif transport != "auto":
            target["transport"] = transport
        raw_cwd = _text(target.get("cwd"))
        if not raw_cwd:
            repo_name = _clean_target_name(target.get("target_repo"))
            raw_cwd = f"~/Development/{repo_name}" if repo_name and repo_name != "unspecified" else "~/Development"
        target["cwd"] = _expand_cwd(raw_cwd)
        if isinstance(target.get("proposed_domain"), dict):
            target["proposed_domain"]["cwd"] = _expand_cwd(target["proposed_domain"].get("cwd") or target["cwd"])
        target["model"] = _text(target.get("model"), DEFAULT_M2_MODEL)
        target["context"] = _text(target.get("context"), DEFAULT_M2_CONTEXT)
        target["model_label"] = _text(target.get("model_label"), DEFAULT_M2_MODEL_LABEL)
        matched = _target_session(target, topic, sessions, loaded_domain_map) if target.get("m2_status") == "EXISTING_M2" else None
        if target.get("m2_status") == "EXISTING_M2":
            ctx_source = matched if isinstance(matched, dict) else target
            recent_ctx = target.get("recent_session_context") if isinstance(target.get("recent_session_context"), dict) else read_recent_session_context(
                ctx_source,
                runner=runner,
                herdr_bin=herdr_bin,
                session_state_dir=session_state_dir,
            )
            if isinstance(recent_ctx, dict) and (recent_ctx.get("turns") or recent_ctx.get("last_session_activity_at")):
                target["recent_session_context"] = recent_ctx
                if _text(recent_ctx.get("last_session_activity_at")):
                    target["last_session_activity_at"] = _text(recent_ctx.get("last_session_activity_at"))
                if _text(recent_ctx.get("agent_session_id")):
                    target["agent_session_id"] = _text(recent_ctx.get("agent_session_id"))
        target.setdefault(
            "context_pass",
            {
                "enabled": bool(target.get("rag_context_hint") or target.get("rag_hits") or target.get("recent_session_context")),
                "hint": target.get("rag_context_hint"),
                **(
                    {"rag_hits": target["rag_hits"]}
                    if target.get("rag_hits")
                    else {}
                ),
                **(
                    {"recent_session_context": target["recent_session_context"]}
                    if isinstance(target.get("recent_session_context"), dict)
                    else {}
                ),
            },
        )
        target["cleanup_candidates"] = _stale_candidates(target, sessions, loaded_domain_map)
        if matched and _text(matched.get("status"), "idle").casefold() in {"idle", "done"}:
            continuity = _evaluate_m2_context_continuity(
                target, matched, message, domain_map=loaded_domain_map, mock_jev=mock_jev,
                handoff_root=effective_handoff_root,
                visible_text=_text(matched.get("visible") or matched.get("visible_text")),
            )
            target.update({"m2_action": continuity["m2_action"]})
            target["context_eviction"] = continuity
            if continuity["m2_action"] == "REPLACE_STALE_M2":
                candidate = {"session_id": matched.get("session_id", ""), "pane_id": matched.get("pane_id", ""), "title": matched.get("title", ""), "status": matched.get("status", "idle"), "target_repo": matched.get("target_repo", ""), "topic_id": target.get("topic_id", ""), "reason": "context_eviction", "eviction_reason": continuity["eviction_reason"], "old_goal_done": True, "is_same_goal_continuation": continuity["is_same_goal_continuation"], "prior_context_value": continuity["prior_context_value"]}
                target["cleanup_candidates"] = [candidate]
        stale_candidates.extend(target["cleanup_candidates"])
    unique_stale = {item["session_id"]: item for item in stale_candidates}
    prompt_warnings: list[dict[str, Any]] = []
    for index, target in enumerate(targets, start=1):
        warning = validate_target_prompt_completeness(target, message, target_index=index)
        if warning:
            target["prompt_completeness_warning"] = warning
            prompt_warnings.append(warning)
    if strict_prompts and prompt_warnings:
        raise RouterError(f"strict prompt completeness guard rejected {len(prompt_warnings)} target(s)")
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "message": message,
        "default_model": DEFAULT_M2_MODEL,
        "default_context": DEFAULT_M2_CONTEXT,
        "default_model_label": DEFAULT_M2_MODEL_LABEL,
        "decision": {"route": route, **decision},
        "domain_map_file": loaded_domain_map.get("path", str(domain_map_file or DEFAULT_DOMAIN_MAP_PATH)),
        "discovery": {"source": discovery_source, "sessions": sessions, "workspaces": workspace_inventory, "warnings": warnings_list},
        "warnings": [*warnings_list, *prompt_warnings],
        "prompt_warnings": prompt_warnings,
        "waves": sorted({target["wave_order"] for target in targets}),
        "targets": targets,
        "new_domain_candidates": decision.get("new_domain_candidates", []),
        "stale_m2_candidates": list(unique_stale.values()),
        "gc_candidates": discover_gc_candidates(list(sessions), handoff_root=effective_handoff_root),
        "approval": {"required": True, "approved": False},
        "phase0_discussion_only": phase0_discussion_only,
    }
    cache_path = _resolve_cross_device_cache_path(state_file)
    resolved_cross_state = cross_device_state
    should_sync, _ = _should_trigger_linear_sync(
        sync_linear=sync_linear,
        no_sync_linear=no_sync_linear,
        auto_trigger=False,
        state_path=_resolve_supervision_state_path(state_file, persist_by_default=True),
        linear_runner=linear_runner,
        hub_issue_payload=hub_issue_payload,
    )
    if should_sync and resolved_cross_state is None:
        state_path = _resolve_supervision_state_path(state_file, persist_by_default=True)
        local_board = _compute_board_rollup(_with_locked_supervision_state(state_path, lambda s: s))
        sync_res = sync_cross_device_hub(
            local_board,
            hub_issue=hub_issue,
            local_hostname=local_hostname,
            cache_path=cache_path,
            linear_runner=linear_runner,
            hub_issue_payload=hub_issue_payload,
        )
        plan["linear_sync"] = sync_res
        if isinstance(sync_res.get("state"), dict):
            resolved_cross_state = sync_res["state"]
    if resolved_cross_state is None:
        resolved_cross_state = _load_cross_device_cache(cache_path)
    cross_summary = _extract_remote_devices_summary(resolved_cross_state, local_hostname=local_hostname)
    plan["local_hostname"] = cross_summary["local_hostname"]
    plan["remote_devices"] = cross_summary["remote_devices"]
    plan["cross_device_in_flight"] = cross_summary["cross_device_in_flight"]
    plan["cross_device_pending_decisions"] = cross_summary["cross_device_pending_decisions"]
    if is_grok_bot_caller(
        caller_bot=caller_bot,
        no_supervision=no_supervision,
        no_routine=no_routine,
        agent_records=agent_records if agent_records is not None else (sessions if sessions else None),
        herdr_bin=herdr_bin,
        runner=runner,
        probe_herdr=not (prefer_issue_cache or discovery_source == "issue_cache"),
    ):
        plan["quota_protection_mode"] = GROK_QUOTA_PROTECTION_MODE
        plan["quota_protection"] = _quota_protection_payload()
    plan["preview_markdown"] = render_preview_markdown(plan)
    if write_preview_md or preview_md_file is not None:
        write_preview_markdown(plan, preview_md_file)
    return plan

def preview_text(plan: dict[str, Any]) -> str:
    lines = [
        f"Plan {plan['plan_id']} · {plan['decision']['route']}",
        f"Default Model / Context: {_text(plan.get('default_model_label'), DEFAULT_M2_MODEL_LABEL)}",
    ]
    quota_mode = _text(plan.get("quota_protection_mode"))
    quota_info = plan.get("quota_protection") if isinstance(plan.get("quota_protection"), dict) else {}
    if quota_mode == GROK_QUOTA_PROTECTION_MODE or bool(quota_info.get("active")):
        lines.append(
            f"quota_protection_mode: {GROK_QUOTA_PROTECTION_MODE} "
            "(Grok Bot fire-and-forget: background --watch supervision and routine polling disabled)"
        )
    preview_md_path = _text(plan.get("preview_markdown_path"))
    if preview_md_path:
        lines.append(f"Preview Markdown: {preview_md_path}")
    lines.extend([
        "Owner approval required before dispatch.",
        "",
        "wave | topic_id | space | domain status | target repo | M2 status | transport | content",
        "-----|----------|-------|---------------|-------------|-----------|-----------|--------",
    ])
    for target in plan.get("targets", []):
        marker = " [NEW_DOMAIN_CANDIDATE]" if target.get("domain_status") == NEW_DOMAIN_CANDIDATE else ""
        ws_badge = (
            "new-space"
            if _text(target.get("m2_status")) == "NEW_M2"
            else (_text(target.get("workspace_id") or target.get("target_workspace_id")) or "new-space")
        )
        lines.append(f"{target.get('wave_order', 1)} | {target.get('topic_id', '')} | {target.get('space', 'M2')} ({ws_badge}) | {target.get('domain_status', '')}{marker} | {target.get('target_repo', 'unspecified')} | {target.get('m2_status')} | {target.get('transport', 'auto')} | {target.get('sub_prompt', '')}")
        hint = _text(target.get("rag_context_hint"))
        if hint:
            lines.append(f"  rag_context_hint: {hint}")
        recent_ctx = target.get("recent_session_context")
        if isinstance(recent_ctx, dict) and (recent_ctx.get("turns") or recent_ctx.get("summary")):
            ts_label = _text(recent_ctx.get("last_session_activity_at") or target.get("last_session_activity_at"), "recent")
            lines.append(f"  recent_session_context ({ts_label}): {_text(recent_ctx.get('summary'))}")
    for target in plan.get("targets", []):
        eviction = target.get("context_eviction") or {}
        if target.get("m2_action") == "REPLACE_STALE_M2":
            lines.extend(["", "🔄 旧 Goal 已完结且新旧任务语义无关 -> 抛弃旧上下文重开干净 M2", f"completion={eviction.get('completion_evidence')} · continuation={eviction.get('is_same_goal_continuation')} · prior_context_value={eviction.get('prior_context_value')}"])
    stale = plan.get("stale_m2_candidates", [])
    if stale:
        lines.extend(["", "Stale / Duplicate M2 Cleanup Candidates:"])
        for candidate in stale:
            lines.append(f"- {candidate.get('topic_id')}: {candidate.get('session_id')} {candidate.get('pane_id')} [{candidate.get('reason')}] {candidate.get('title')}")
        lines.append("Use --cleanup-duplicates or --replace-stale-m2 only after Owner approval.")
    candidates = plan.get("new_domain_candidates", [])
    if candidates:
        lines.extend(["", "New domain candidates (Owner decision required):"])
        for candidate in candidates:
            lines.append(f"- {candidate.get('topic_id')}: {candidate.get('title')} → {candidate.get('target_repo')} ({candidate.get('group')})")
    cross_lines = _render_cross_device_rollup_lines({
        "cross_device_in_flight": plan.get("cross_device_in_flight") or [],
        "cross_device_pending_decisions": plan.get("cross_device_pending_decisions") or [],
    })
    if cross_lines:
        lines.extend(["", "Cross-Device Active & Pending (TIA-463):", *cross_lines[1:]])
    return "\n".join(lines)
