"""Owner learning-event transitions, state reconciliation, and CLI parser/entrypoint."""

from __future__ import annotations

from .common import *
from .cards_and_blog import _vault_coverage, archive_card, precheck_card, publish_blog
from .discovery import discover_topics
from .ledger_and_scan import (
    _append_valid_proposal,
    _load_learning_state_registry,
    _load_topic_ledger,
    _reconcile_proposals_history,
    _save_learning_state_registry,
)


def _handle_learning_event_locked(
    *,
    action: str,
    topic_key: str | None = None,
    ref: str | None = None,
    external_id: str | None = None,
    event_id: str | None = None,
    note: str = "",
    revision: int | None = None,
    proposals_path: Path = DEFAULT_PROPOSALS_PATH,
    state_path: Path | None = None,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Deterministic Owner-decision & post-approval exploration state handler for learning topics."""
    now_dt = now_utc or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    now_iso = now_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    today_cst = _cst_date_str(now_dt)
    today_date = date.fromisoformat(today_cst)

    registry = _load_learning_state_registry(
        proposals_path=proposals_path,
        state_path=state_path,
        vault_dir=vault_dir,
        now_utc=now_dt,
    )
    topics_reg: dict[str, dict[str, Any]] = registry.setdefault("topics", {})
    consumed_events: dict[str, dict[str, Any]] = registry.setdefault("consumed_events", {})

    resolved_tk = (topic_key or "").strip()
    if resolved_tk.startswith("learning-topic://"):
        resolved_tk = resolved_tk.removeprefix("learning-topic://").strip("/")
    elif resolved_tk.startswith("knowledge://"):
        resolved_tk = resolved_tk.removeprefix("knowledge://").strip("/")
    elif resolved_tk.startswith("learning-topic-"):
        resolved_tk = resolved_tk.removeprefix("learning-topic-")
    elif resolved_tk.startswith("learn-"):
        resolved_tk = resolved_tk.removeprefix("learn-")

    raw_ref = (ref or "").strip()
    raw_ext = (external_id or "").strip()
    if not resolved_tk and raw_ref:
        if raw_ref.startswith("learning-topic://"):
            resolved_tk = raw_ref.removeprefix("learning-topic://").strip("/")
        elif raw_ref.startswith("knowledge://"):
            resolved_tk = raw_ref.removeprefix("knowledge://").strip("/")
        else:
            for tk_cand, ent in topics_reg.items():
                if raw_ref in {ent.get("ref"), ent.get("origin_ref"), ent.get("external_id"), ent.get("proposal_id")}:
                    resolved_tk = tk_cand
                    break
    if not resolved_tk and raw_ext:
        for tk_cand, ent in topics_reg.items():
            if raw_ext in {ent.get("external_id"), ent.get("ref"), ent.get("proposal_id")}:
                resolved_tk = tk_cand
                break
        if not resolved_tk and raw_ext.lower().startswith("linear://"):
            resolved_tk = raw_ext.split("://", 1)[1].strip().lower()

    if not resolved_tk:
        raise ValueError("topic_key, ref, or matching external_id is required")

    resolved_tk = _topic_alias_map(topics_reg).get(resolved_tk, resolved_tk)
    entry = dict(topics_reg.get(resolved_tk, {}))
    entry.setdefault("created_at", now_iso)
    entry.setdefault("proposed_on", _cst_day_str(_parse_iso_ts(entry["created_at"])))
    current_rev = int(entry.get("revision") or 1)
    current_state = str(entry.get("learning_state") or "proposed").strip().lower()
    origin_ref = str(entry.get("origin_ref") or f"learning-topic://{resolved_tk}")
    prop_id = str(entry.get("proposal_id") or f"learning-topic-{resolved_tk}")
    title = str(entry.get("title") or f"学习探索：{resolved_tk}")
    detail = str(entry.get("detail") or f"topic_key={resolved_tk} · ref={origin_ref}")
    priority = str(entry.get("priority") or "P2")

    raw_action = (action or "").strip()
    norm_action = raw_action.casefold()

    eff_event_id = (event_id or "").strip()
    if eff_event_id and eff_event_id in consumed_events:
        prior = consumed_events[eff_event_id]
        if prior.get("topic_key") == resolved_tk:
            return {
                "ok": True,
                "deduplicated": True,
                "event_id": eff_event_id,
                "topic_key": resolved_tk,
                "action": prior.get("action", norm_action),
                "learning_state": entry.get("learning_state", current_state),
                "revision": current_rev,
                "exploration_authorized": bool(entry.get("exploration_authorized", False)),
                "exploration_started": False,
                "blog_published": False,
                "design_studio_synced": False,
            }

    if revision is not None and revision != current_rev:
        return {
            "ok": False,
            "conflict": True,
            "error": "stale topic revision",
            "topic_key": resolved_tk,
            "revision": current_rev,
            "learning_state": current_state,
        }

    if norm_action in {"star", "bookmark", "favorite"}:
        entry.setdefault("topic_key", resolved_tk)
        entry.setdefault("origin_ref", origin_ref)
        entry["bookmarked"] = True
        entry["bookmarked_at"] = now_iso
        entry["updated_at"] = now_iso
        topics_reg[resolved_tk] = entry
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": current_rev,
                "action": "bookmarked",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "bookmarked",
            "learning_state": current_state,
            "revision": current_rev,
            "exploration_authorized": False,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
        }

    if norm_action in {"sync_triage", "transfer", "transferred"}:
        new_rev = current_rev + 1
        ext_val = raw_ext or (raw_ref if raw_ref.lower().startswith("linear://") else str(entry.get("external_id") or ""))
        entry.update({
            "topic_key": resolved_tk,
            "proposal_id": prop_id,
            "title": title,
            "detail": detail,
            "priority": priority,
            "agent": "knowledge-digest",
            "origin_ref": origin_ref,
            "ref": ext_val or origin_ref,
            "external_id": ext_val or None,
            "learning_state": "transferred",
            "exploration_authorized": False,
            "revision": new_rev,
            "updated_at": now_iso,
        })
        topics_reg[resolved_tk] = entry
        prop_record = {
            "id": prop_id,
            "ts": now_iso,
            "agent": "knowledge-digest",
            "title": title,
            "detail": detail,
            "priority": priority,
            "status": "approved",
            "ref": ext_val or origin_ref,
            "origin_ref": origin_ref,
            "topic_key": resolved_tk,
            "external_id": ext_val or None,
            "source": "Learning",
            "scope": "personal",
            "learning_state": "transferred",
            "sync_triage": True,
            "exploration_authorized": False,
            "revision": new_rev,
        }
        prop_record.update({key: entry[key] for key in ("created_at", "proposed_on", "external_id") if key in entry})
        _append_valid_proposal(proposals_path, prop_record)
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": new_rev,
                "action": "transferred",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "transferred",
            "learning_state": "transferred",
            "external_id": ext_val or None,
            "origin_ref": origin_ref,
            "revision": new_rev,
            "exploration_authorized": False,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
            "proposal_written": prop_record,
        }

    if norm_action in {"approve", "approve to todo", "快速学习", "深度调研", "quick_learn", "deep_research"}:
        if current_state in {"learned", "dismissed", "blocked"} and not entry.get("reopened"):
            return {
                "ok": False,
                "conflict": True,
                "error": f"topic '{resolved_tk}' is already in terminal state '{current_state}'; explicit Reopen required",
                "topic_key": resolved_tk,
                "learning_state": current_state,
                "revision": current_rev,
                "exploration_authorized": False,
                "exploration_started": False,
                "blog_published": False,
                "design_studio_synced": False,
            }
        if current_state in {"approved", "exploring"}:
            return {
                "ok": True,
                "deduplicated": True,
                "topic_key": resolved_tk,
                "learning_state": current_state,
                "revision": current_rev,
                "exploration_authorized": True,
                "exploration_started": False,
                "exploration_task_key": entry.get("exploration_task_key"),
                "blog_published": False,
                "design_studio_synced": False,
            }
        route = "deep-research" if (raw_action == "深度调研" or norm_action == "deep_research" or "深度" in note) else "knowledge-digest"
        new_rev = current_rev + 1
        task_key = f"explore-{resolved_tk}-r{new_rev}"
        entry.update({
            "topic_key": resolved_tk,
            "proposal_id": prop_id,
            "title": title,
            "detail": detail if not note else f"{detail} | note={note}",
            "priority": priority,
            "agent": "knowledge-digest",
            "origin_ref": origin_ref,
            "ref": entry.get("ref") or origin_ref,
            "learning_state": "approved",
            "exploration_authorized": True,
            "exploration_route": route,
            "exploration_task_key": task_key,
            "owner_note": note,
            "revision": new_rev,
            "updated_at": now_iso,
        })
        topics_reg[resolved_tk] = entry
        prop_record = {
            "id": prop_id,
            "ts": now_iso,
            "agent": "knowledge-digest",
            "title": title,
            "detail": entry["detail"],
            "priority": priority,
            "status": "approved",
            "ref": origin_ref,
            "origin_ref": origin_ref,
            "topic_key": resolved_tk,
            "source": "Learning",
            "scope": "personal",
            "learning_state": "approved",
            "exploration_authorized": True,
            "exploration_route": route,
            "exploration_task_key": task_key,
            "revision": new_rev,
        }
        prop_record.update({key: entry[key] for key in ("created_at", "proposed_on", "external_id") if key in entry})
        _append_valid_proposal(proposals_path, prop_record)
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": new_rev,
                "action": "approved",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "approved",
            "learning_state": "approved",
            "exploration_authorized": True,
            "exploration_route": route,
            "exploration_task_key": task_key,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
            "revision": new_rev,
            "proposal_written": prop_record,
        }

    if norm_action in {"done", "mark done", "确认", "confirm", "resolve", "learned"}:
        new_rev = current_rev + 1
        entry.update({
            "topic_key": resolved_tk,
            "proposal_id": prop_id,
            "title": title,
            "detail": detail if not note else f"{detail} | note={note}",
            "priority": priority,
            "agent": "knowledge-digest",
            "origin_ref": origin_ref,
            "ref": origin_ref,
            "learning_state": "learned",
            "outcome": "owner_reported",
            "exploration_authorized": False,
            "revision": new_rev,
            "updated_at": now_iso,
        })
        topics_reg[resolved_tk] = entry
        prop_record = {
            "id": prop_id,
            "ts": now_iso,
            "agent": "knowledge-digest",
            "title": title,
            "detail": entry["detail"],
            "priority": priority,
            "status": "dismissed",
            "ref": origin_ref,
            "origin_ref": origin_ref,
            "topic_key": resolved_tk,
            "source": "Learning",
            "scope": "personal",
            "learning_state": "learned",
            "outcome": "owner_reported",
            "revision": new_rev,
        }
        prop_record.update({key: entry[key] for key in ("created_at", "proposed_on", "external_id") if key in entry})
        _append_valid_proposal(proposals_path, prop_record)
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": new_rev,
                "action": "learned",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "done",
            "learning_state": "learned",
            "outcome": "owner_reported",
            "revision": new_rev,
            "exploration_authorized": False,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
            "proposal_written": prop_record,
        }

    if norm_action in {"dismiss", "cancel", "canceled", "cancelled", "reject"}:
        new_rev = current_rev + 1
        next_el = (today_date + timedelta(days=DISMISS_COOLDOWN_DAYS)).isoformat()
        entry.update({
            "topic_key": resolved_tk,
            "proposal_id": prop_id,
            "title": title,
            "detail": detail if not note else f"{detail} | note={note}",
            "priority": priority,
            "agent": "knowledge-digest",
            "origin_ref": origin_ref,
            "ref": origin_ref,
            "learning_state": "dismissed",
            "next_eligible_at": next_el,
            "auto_revive": False,
            "reopened": False,
            "exploration_authorized": False,
            "revision": new_rev,
            "updated_at": now_iso,
        })
        topics_reg[resolved_tk] = entry
        prop_record = {
            "id": prop_id,
            "ts": now_iso,
            "agent": "knowledge-digest",
            "title": title,
            "detail": entry["detail"],
            "priority": priority,
            "status": "dismissed",
            "ref": origin_ref,
            "origin_ref": origin_ref,
            "topic_key": resolved_tk,
            "source": "Learning",
            "scope": "personal",
            "learning_state": "dismissed",
            "next_eligible_at": next_el,
            "auto_revive": False,
            "revision": new_rev,
        }
        prop_record.update({key: entry[key] for key in ("created_at", "proposed_on", "external_id") if key in entry})
        _append_valid_proposal(proposals_path, prop_record)
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": new_rev,
                "action": "dismissed",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "dismissed",
            "learning_state": "dismissed",
            "next_eligible_at": next_el,
            "auto_revive": False,
            "revision": new_rev,
            "exploration_authorized": False,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
            "proposal_written": prop_record,
        }

    if norm_action in {"snooze", "snooze 7d", "defer", "later", "延后"}:
        new_rev = current_rev + 1
        next_el = (today_date + timedelta(days=SNOOZE_COOLDOWN_DAYS)).isoformat()
        entry.update({
            "topic_key": resolved_tk,
            "proposal_id": prop_id,
            "title": title,
            "detail": detail if not note else f"{detail} | note={note}",
            "priority": priority,
            "agent": "knowledge-digest",
            "origin_ref": origin_ref,
            "ref": origin_ref,
            "learning_state": "snoozed",
            "next_eligible_at": next_el,
            "exploration_authorized": False,
            "revision": new_rev,
            "updated_at": now_iso,
        })
        topics_reg[resolved_tk] = entry
        prop_record = {
            "id": prop_id,
            "ts": now_iso,
            "agent": "knowledge-digest",
            "title": title,
            "detail": entry["detail"],
            "priority": priority,
            "status": "dismissed",
            "ref": origin_ref,
            "origin_ref": origin_ref,
            "topic_key": resolved_tk,
            "source": "Learning",
            "scope": "personal",
            "learning_state": "snoozed",
            "next_eligible_at": next_el,
            "revision": new_rev,
        }
        prop_record.update({key: entry[key] for key in ("created_at", "proposed_on", "external_id") if key in entry})
        _append_valid_proposal(proposals_path, prop_record)
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": new_rev,
                "action": "snoozed",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "snoozed",
            "learning_state": "snoozed",
            "next_eligible_at": next_el,
            "revision": new_rev,
            "exploration_authorized": False,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
            "proposal_written": prop_record,
        }

    if norm_action in {"reopen", "reset", "queue"}:
        new_rev = current_rev + 1
        entry.update({
            "topic_key": resolved_tk,
            "learning_state": "queued",
            "reopened": True,
            "next_eligible_at": today_cst,
            "exploration_authorized": False,
            "revision": new_rev,
            "updated_at": now_iso,
        })
        topics_reg[resolved_tk] = entry
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": new_rev,
                "action": "reopened",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "reopened",
            "learning_state": "queued",
            "revision": new_rev,
            "exploration_authorized": False,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
        }

    raise ValueError(f"unsupported learning event action: {action!r}")


def handle_learning_event(*, proposals_path: Path = DEFAULT_PROPOSALS_PATH, **kwargs: Any) -> dict[str, Any]:
    with _proposal_lock(proposals_path):
        return _handle_learning_event_locked(proposals_path=proposals_path, **kwargs)


def reconcile_learning_state(
    *,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    proposals_path: Path = DEFAULT_PROPOSALS_PATH,
    state_path: Path | None = None,
    topic_ledger_path: Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Detect and optionally repair learned topics in topic-ledger.json that remain pending/proposed in state or proposals."""
    eff_vault = vault_dir.expanduser().resolve()
    eff_proposals = proposals_path.expanduser().resolve()
    eff_state = _resolve_state_path(eff_proposals, state_path.expanduser().resolve() if state_path else None, eff_vault)
    ledger_entries = _load_topic_ledger(eff_vault, topic_ledger_path.expanduser().resolve() if topic_ledger_path else None)
    alias_map = _topic_alias_map(ledger_entries)
    covered_ids, _ = _vault_coverage(eff_vault)

    learned_keys: set[str] = {
        key
        for key, item in ledger_entries.items()
        if str(item.get("learning_state") or "").strip().lower() == "learned" or key in covered_ids
    }

    with _proposal_lock(eff_proposals):
        raw_state: dict[str, Any] = {"schema_version": 1, "updated_at": "", "topics": {}, "consumed_events": {}}
        if eff_state.is_file():
            loaded = json.loads(eff_state.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw_state.update(loaded)
        state_topics = raw_state.setdefault("topics", {})
        history = _reconcile_proposals_history(eff_proposals if eff_proposals.is_file() else None, ledger_entries)
        by_topic = history["by_topic"]

        drifts: list[dict[str, Any]] = []
        reconciled: list[str] = []
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

        for raw_key in sorted(set(state_topics) | set(by_topic)):
            canon_key = alias_map.get(raw_key, raw_key)
            if canon_key not in learned_keys:
                continue
            ledger_item = ledger_entries.get(canon_key, {})
            st_entry = state_topics.get(raw_key) or state_topics.get(canon_key) or {}
            prop_rec = by_topic.get(canon_key) or by_topic.get(raw_key) or {}

            st_val = str(st_entry.get("learning_state") or "").strip().lower()
            prop_state = str(prop_rec.get("learning_state") or "").strip().lower()
            prop_status = str(prop_rec.get("status") or "").strip().lower()

            state_stale = st_val in {"pending", "proposed", "queued", "approved", "exploring", "transferred"}
            prop_stale = prop_status == "pending" or prop_state in {"pending", "proposed", "queued"}
            if not state_stale and not prop_stale:
                continue

            max_rev = max(
                int(ledger_item.get("revision") or 1),
                int(st_entry.get("revision") or 0),
                int(prop_rec.get("revision") or 0),
            )
            new_rev = max_rev + 1
            drifts.append({
                "topic_key": canon_key,
                "ledger_state": "learned",
                "state_file_learning_state": st_val or None,
                "proposal_status": prop_status or None,
                "proposal_learning_state": prop_state or None,
                "target_revision": new_rev,
            })

            if apply:
                merged_entry = {
                    **ledger_item,
                    **st_entry,
                    "topic_key": canon_key,
                    "learning_state": "learned",
                    "exploration_authorized": False,
                    "revision": new_rev,
                    "updated_at": now_iso,
                }
                state_topics[canon_key] = merged_entry
                if raw_key != canon_key and raw_key in state_topics:
                    del state_topics[raw_key]

                if prop_stale and eff_proposals.is_file():
                    prop_id = str(prop_rec.get("id") or merged_entry.get("proposal_id") or f"learning-topic-{canon_key}")
                    title = str(prop_rec.get("title") or merged_entry.get("title") or canon_key)
                    detail = str(prop_rec.get("detail") or merged_entry.get("question") or title)
                    priority = str(prop_rec.get("priority") or merged_entry.get("priority") or "P2")
                    origin_ref = str(prop_rec.get("origin_ref") or prop_rec.get("ref") or f"learning-topic://{canon_key}")
                    close_rec: dict[str, Any] = {
                        "id": prop_id,
                        "ts": now_iso,
                        "agent": "knowledge-digest",
                        "title": title,
                        "detail": detail,
                        "priority": priority,
                        "status": "dismissed",
                        "ref": origin_ref,
                        "origin_ref": origin_ref,
                        "topic_key": canon_key,
                        "source": "Learning",
                        "scope": "personal",
                        "learning_state": "learned",
                        "outcome": "reconciled_from_ledger",
                        "revision": new_rev,
                    }
                    for keep_k in ("created_at", "proposed_on", "external_id"):
                        if keep_k in prop_rec:
                            close_rec[keep_k] = prop_rec[keep_k]
                    _append_valid_proposal(eff_proposals, close_rec)
                reconciled.append(canon_key)

        if apply and reconciled:
            _save_learning_state_registry(
                raw_state,
                proposals_path=eff_proposals,
                state_path=eff_state,
                vault_dir=eff_vault,
            )

    return {
        "ok": len(drifts) == 0 or apply,
        "apply": apply,
        "drift_count": len(drifts),
        "drifts": drifts,
        "reconciled": reconciled,
        "state_path": str(eff_state),
        "proposals_path": str(eff_proposals),
    }
