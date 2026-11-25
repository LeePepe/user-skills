"""Deterministic candidate discovery, quota/cooldown enforcement, and AIDash/Linear proposing."""

from __future__ import annotations

from .common import *
from .cards_and_blog import _existing_vault_corpus, archive_card, render_concept_card_markdown
from .daily_digest import (
    _daily_description_keys,
    _learning_brief,
    _linear_transport_from_repo,
    _update_proposal_external_ids,
    _upsert_linear_daily_issue,
)
from .ledger_and_scan import (
    _append_valid_proposal,
    _load_learning_state_registry,
    _load_topic_ledger,
    _reconcile_proposals_history,
    _save_learning_state_registry,
    _save_topic_ledger,
    _scan_collected_tools_candidates,
    _scan_git_tech_stack_candidates,
    _topic_exclusion_reason,
)


def discover_topics(
    *,
    top: int | None = None,
    track: str | None = None,
    propose_aidash: bool = False,
    propose_linear: bool = False,
    dual_zone: bool = False,
    linear_transport: Any | None = None,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    collected_tools_dir: Path = DEFAULT_COLLECTED_TOOLS_DIR,
    git_roots: tuple[Path, ...] = DEFAULT_GIT_ROOTS,
    proposals_path: Path = DEFAULT_PROPOSALS_PATH,
    state_path: Path | None = None,
    topic_ledger_path: Path | None = None,
    materialize_cards: bool | None = None,
    materialize_before_approval: bool = False,
    include_unreconciled_repos: bool = False,
    now_utc: datetime | None = None,
    mock_r09_answers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if top is not None and top <= 0:
        return {
            "ok": False,
            "status": "invalid_quota",
            "error": "--top must be a positive integer",
            "selected_count": 0,
            "topics": [],
            "tracks_summary": {"workflow": [], "personal": []},
            "proposals_written": [],
        }
    propose_aidash = propose_aidash or propose_linear
    with _proposal_lock(proposals_path):
        now_dt = now_utc or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        eff_materialize = bool(materialize_cards) if not propose_aidash else False
        try:
            existing_corpus = _existing_vault_corpus(vault_dir)
            try:
                ledger_entries = _load_topic_ledger(vault_dir, topic_ledger_path)
            except TopicLedgerError:
                if propose_aidash:
                    raise
                ledger_entries = {}
            registry = _load_learning_state_registry(
                proposals_path=proposals_path,
                state_path=state_path,
                vault_dir=vault_dir,
                topic_ledger_path=topic_ledger_path,
                now_utc=now_dt,
            )
            effective_entries = registry["topics"]
            history = _reconcile_proposals_history(proposals_path, effective_entries, now_dt)
        except (TopicLedgerError, ValueError) as exc:
            return {
                "ok": False,
                "status": "health_error",
                "error": str(exc),
                "selected_count": 0,
                "topics": [],
                "tracks_summary": {"workflow": [], "personal": []},
                "proposals_written": [],
            }
        excluded_topics: set[str] = set(history["excluded_topics"])
        pending_topics: set[str] = set(history["pending_topics"])
        proposed_today_cst: set[str] = set(history["proposed_today_cst"])

        raw_candidates = (
            _scan_collected_tools_candidates(collected_tools_dir, existing_corpus, vault_dir=vault_dir, ledger_entries=ledger_entries)
            + ([] if propose_aidash else _scan_git_tech_stack_candidates(git_roots, existing_corpus))
        )
        if track is not None:
            if track not in VALID_TRACKS:
                return {
                    "ok": False,
                    "status": "invalid_track",
                    "error": f"unsupported track: {track}",
                    "selected_count": 0,
                    "topics": [],
                    "tracks_summary": {"workflow": [], "personal": []},
                    "proposals_written": [],
                }
            raw_candidates = [candidate for candidate in raw_candidates if infer_topic_track(candidate) == track]
        day = _cst_day_str(now_dt)
        daily_map = registry.get("daily_linear_issues", {})
        legacy_map = registry.get("daily_linear_digests", {})
        daily_topics = {
            **(legacy_map.get(day, {}) if isinstance(legacy_map.get(day, {}), dict) else {}),
            **(daily_map.get(day, {}) if isinstance(daily_map.get(day, {}), dict) else {}),
        }
        daily_topic_keys = set(daily_topics.get("topic_keys", []))
        linear_client = linear_transport
        if propose_linear:
            try:
                linear_client = linear_client or _linear_transport_from_repo()
                day_title = f"[Daily Learn {_cst_day_str(now_dt)}] 今日 3 个推荐学习主题"
                remote_issues = linear_client.list_issues("TIA", query=day_title, limit=50)
                remote = next(
                    (
                        x
                        for x in remote_issues
                        if f"GTT-LINEAR-DEDUP:daily-learn-{_cst_day_str(now_dt)}" in str(x.get("description") or "")
                        or x.get("title") == day_title
                    ),
                    None,
                )
                if daily_topics and not remote:
                    raise ValueError("Saved daily issue was not found; refusing to create a duplicate")
                remote_keys = _daily_description_keys(str(remote.get("description") or "")) if remote else set()
                if remote_keys and daily_topic_keys and remote_keys != daily_topic_keys:
                    raise ValueError("Remote daily topics conflict with saved topic keys; manual reconciliation required")
                if remote and not daily_topic_keys:
                    if not remote_keys:
                        raise ValueError("Existing daily issue has no recoverable topic keys; refusing to replace its topics")
                    daily_topic_keys = remote_keys
                    recovered = {
                        "topic_keys": sorted(daily_topic_keys),
                        "external_id": f"linear://{remote.get('identifier') or remote.get('id')}",
                        "url": remote.get("url"),
                        "identifier": remote.get("identifier"),
                        "title": remote.get("title"),
                    }
                    registry.setdefault("daily_linear_issues", {})[day] = {**daily_topics, **recovered}
                    registry.setdefault("daily_linear_digests", {})[day] = {
                        **daily_topics,
                        **recovered,
                        "date": day,
                        "dedup_key": f"daily-learn-{day}",
                    }
            except Exception as exc:
                return {
                    "ok": False,
                    "status": "linear_error",
                    "error": f"Linear daily digest lookup failed: {exc}",
                    "selected_count": 0,
                    "topics": [],
                    "tracks_summary": {"workflow": [], "personal": []},
                    "proposals_written": [],
                }
        if daily_topic_keys:
            known = {str(c.get("topic_key")) for c in raw_candidates}
            for key in sorted(daily_topic_keys):
                if key in known or key not in ledger_entries:
                    continue
                item = ledger_entries[key]
                raw_candidates.append({
                    "slug": key,
                    "topic_key": key,
                    "source_id": item.get("source_id", key),
                    "source_uri": item.get("source_uri", ""),
                    "source_fingerprint": item.get("source_fingerprint", ""),
                    "aliases": list(item.get("aliases", [])),
                    "title": item.get("title") or item.get("question") or key,
                    "question": item.get("question") or key,
                    "category": item.get("category", "工程与设计"),
                    "source_kind": item.get("source_kind", "collected_tools"),
                    "source_ref": item.get("source_uri", ""),
                    "why_now": item.get("why_now") or item.get("excerpt") or f"Daily digest topic {key}",
                    "coverage_refs": list(item.get("coverage_refs", [])),
                    "excerpt": item.get("excerpt") or item.get("question") or key,
                    "rank": int(item.get("rank") or 99),
                    "scope": item.get("scope", "personal"),
                    "track": infer_topic_track(item),
                    "learning_guide": item.get("learning_guide"),
                })
        if daily_topic_keys:
            reconstructable = {str(c.get("topic_key")) for c in raw_candidates}
            missing_daily = sorted(daily_topic_keys - reconstructable)
            if missing_daily:
                return {
                    "ok": False,
                    "status": "daily_reconciliation_error",
                    "error": f"Unable to reconstruct existing daily topics: {', '.join(missing_daily)}",
                    "selected_count": 0,
                    "topics": [],
                    "tracks_summary": {"workflow": [], "personal": []},
                    "proposals_written": [],
                }
        deduped: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        seen_questions: set[str] = set()
        excluded_count = 0
        excluded_reasons = {
            key: reason
            for key, entry in effective_entries.items()
            if (reason := _topic_exclusion_reason({"topic_key": key}, registry, now_dt.astimezone(_CST_TZ).date()))
        }
        topic_aliases = _topic_alias_map(ledger_entries)
        for cand in raw_candidates:
            t_key = str(cand.get("topic_key") or cand.get("slug") or "").strip()
            question = re.sub(r"\s+", " ", str(cand.get("question") or cand.get("title") or "").strip()).casefold()
            aliases = {str(a).strip() for a in cand.get("aliases", []) if topic_aliases.get(str(a)) == t_key} | {t_key}
            question_key = question
            if not t_key or (aliases & seen_keys) or question_key in seen_questions:
                continue
            seen_keys |= aliases
            seen_questions.add(question_key)
            if aliases & excluded_topics and not (propose_linear and t_key in daily_topic_keys):
                excluded_count += 1
                continue
            cand["track"] = infer_topic_track(cand)
            deduped.append(cand)

        requested_quota = max(1, top or (3 if propose_linear else 1))
        explicit_multi = propose_linear or (top is not None and top >= 3)
        quota = requested_quota if explicit_multi else 1
        daily_cap_reached = propose_aidash and not daily_topic_keys and len(proposed_today_cst) >= quota
        pending_cap_reached = propose_aidash and not daily_topic_keys and len(pending_topics) >= 3
        if daily_topic_keys:
            deduped = [c for c in deduped if c.get("topic_key") in daily_topic_keys]
        if pending_cap_reached:
            quota = 0
        elif propose_aidash and not daily_topic_keys:
            quota = min(quota, max(0, 3 - len(pending_topics)))
        if daily_cap_reached or pending_cap_reached or not deduped:
            reason = (
                "pending_cap_reached"
                if pending_cap_reached
                else "daily_cap_reached"
                if daily_cap_reached
                else "no_qualified_candidates"
            )
            return {
                "ok": True,
                "status": "no_new_topic",
                "reason": reason,
                "quota_blocked_reason": (
                    "pending_cap_reached"
                    if pending_cap_reached
                    else "daily_slot_already_used"
                    if daily_cap_reached
                    else None
                ),
                "excluded_reasons": excluded_reasons,
                "materialized": False,
                "candidate_count": len(deduped),
                "excluded_count": excluded_count,
                "pending_learning_count": len(pending_topics),
                "selected_count": 0,
                "topics": [],
                "tracks_summary": {"workflow": [], "personal": []},
                "archived_cards": [],
                "proposals_written": [],
            }

        ranked = sorted(deduped, key=lambda item: (int(item.get("rank", 99)), str(item.get("topic_key", ""))))
        eff_top = len(daily_topic_keys) if propose_linear and daily_topic_keys else quota if propose_aidash else max(1, top or 1)
        if eff_top >= 2 and not (propose_linear and daily_topic_keys):
            selected = _select_balanced_by_track(ranked, eff_top)
        elif propose_linear and daily_topic_keys:
            ordered_existing_keys = list(daily_topics.get("topic_keys") or [])
            order_index = {k: idx for idx, k in enumerate(ordered_existing_keys)}
            selected = sorted(
                ranked[:eff_top],
                key=lambda item: (order_index.get(str(item.get("topic_key")), 999), int(item.get("rank", 99)), str(item.get("topic_key", ""))),
            )
        else:
            selected = ranked[:eff_top]
        for item in selected:
            item["track"] = infer_topic_track(item)
            item["learning_brief"] = _learning_brief(item)
        if not selected:
            return {
                "ok": True,
                "status": "no_new_topic",
                "reason": "no_qualified_candidates",
                "candidate_count": len(deduped),
                "excluded_count": excluded_count,
                "pending_learning_count": len(pending_topics),
                "selected_count": 0,
                "topics": [],
                "tracks_summary": {"workflow": [], "personal": []},
                "archived_cards": [],
                "proposals_written": [],
            }

        archived_cards: list[dict[str, Any]] = []
        proposals_written: list[dict[str, Any]] = []
        now_iso = now_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
        today_cst = _cst_day_str(now_dt)

        for item in selected:
            slug = str(item.get("topic_key") or item["slug"]).strip()
            item_track = infer_topic_track(item)
            if eff_materialize:
                card_path = vault_dir / "concepts" / f"{slug}.md"
                if not card_path.is_file():
                    card_path.parent.mkdir(parents=True, exist_ok=True)
                    card_path.write_text(render_concept_card_markdown(item), encoding="utf-8")
                archived = archive_card(
                    slug=slug,
                    category=item["category"],
                    title=item["title"],
                    file_path=card_path,
                    vault_dir=vault_dir,
                    folder="concepts",
                )
                archived_cards.append(archived)

            if propose_aidash:
                ref_uri = f"learning-topic://{slug}"
                why_now = str(item.get("why_now") or item.get("excerpt") or "").strip()
                source_uri = str(item.get("source_uri") or item.get("source_ref") or "").strip()
                cov_refs = list(item.get("coverage_refs") or [])
                cov_note = f"已核对相邻卡片: {', '.join(cov_refs)}" if cov_refs else "未覆盖缺口（6 concepts / 2 craft 均无同题）"
                detail_text = (
                    f"[{TRACK_LABELS[item_track]}] 为什么现在学：{why_now} · 来源依据：{source_uri} · 覆盖对账：{cov_note} · "
                    f"批准后由后台启动 knowledge-digest 探索并归档至 knowledge-vault；不自动发博客，不同步 design-studio。"
                )
                prior = effective_entries.get(slug, {})
                record = {
                    "id": str(prior.get("proposal_id") or prior.get("id") or f"learning-topic-{slug}"),
                    "ref": ref_uri,
                    "origin_ref": ref_uri,
                    "topic_key": slug,
                    "track": item_track,
                    "source": "Learning",
                    "scope": "personal",
                    "source_uri": source_uri,
                    "source_fingerprint": item.get("source_fingerprint"),
                    "why_now": why_now,
                    "coverage_refs": cov_refs,
                    "learning_state": "proposed",
                    "proposed_on": history["creation_days"].get(slug, str(prior.get("proposed_on") or today_cst)),
                    "revision": int(prior.get("revision") or 0) + 1,
                    "ts": now_iso,
                    "created_at": prior.get("created_at") or now_iso,
                    "question": item["question"],
                    "agent": "knowledge-digest",
                    "title": item["title"],
                    "detail": detail_text,
                    "priority": "P2",
                    "status": "pending",
                }
                if not prior.get("proposal_id") or slug not in history["by_topic"]:
                    _append_valid_proposal(proposals_path, record)
                    proposals_written.append(record)
                else:
                    record = {
                        **prior,
                        **record,
                        "learning_state": prior.get("learning_state", "proposed"),
                        "status": prior.get("status", "pending"),
                        "revision": int(prior.get("revision") or 1),
                    }
                registry["topics"][slug] = {**prior, **item, **record, "proposal_id": record["id"]}

        linear_issue = None
        if proposals_written:
            _save_learning_state_registry(registry, proposals_path=proposals_path, state_path=state_path, vault_dir=vault_dir)
        if propose_linear:
            try:
                client = linear_client or linear_transport or _linear_transport_from_repo()
                day = _cst_day_str(now_dt)
                linear_issue = _upsert_linear_daily_issue(client, day, selected, existing_issue=remote, dual_zone=dual_zone)
                external_id = f"linear://{linear_issue.get('identifier') or linear_issue.get('id')}"
                daily_registry = registry.setdefault("daily_linear_issues", {})
                daily_registry[day] = {
                    **daily_registry.get(day, {}),
                    "external_id": external_id,
                    "url": linear_issue.get("url"),
                    "identifier": linear_issue.get("identifier"),
                    "title": linear_issue.get("title"),
                    "topic_keys": [str(x.get("topic_key")) for x in selected],
                    "tracks_summary": _tracks_summary(selected),
                    "updated_at": now_iso,
                }
                registry.setdefault("daily_linear_digests", {})[day] = {
                    **registry.get("daily_linear_digests", {}).get(day, {}),
                    **daily_registry[day],
                    "date": day,
                    "dedup_key": f"daily-learn-{day}",
                }
                for item in selected:
                    entry = registry["topics"].get(item["topic_key"], {})
                    entry["external_id"] = external_id
                    entry["daily_linear_issue"] = external_id
                    entry["track"] = infer_topic_track(item)
                    registry["topics"][item["topic_key"]] = entry
                _update_proposal_external_ids(proposals_path, selected, external_id)
                _save_topic_ledger(vault_dir, registry["topics"], topic_ledger_path=topic_ledger_path)
            except Exception as exc:
                return {
                    "ok": False,
                    "status": "linear_error",
                    "error": f"Linear daily digest failed: {exc}",
                    "selected_count": 0,
                    "topics": [],
                    "tracks_summary": {"workflow": [], "personal": []},
                    "proposals_written": proposals_written,
                }

        if proposals_written or linear_issue:
            _save_learning_state_registry(registry, proposals_path=proposals_path, state_path=state_path, vault_dir=vault_dir)

        return {
            "ok": True,
            "status": "selected",
            "materialized": bool(archived_cards),
            "quota_blocked_reason": None,
            "excluded_reasons": excluded_reasons,
            "candidate_count": len(deduped),
            "excluded_count": excluded_count,
            "pending_learning_count": len(pending_topics) + len(proposals_written),
            "selected_count": len(selected),
            "topics": selected,
            "tracks_summary": _tracks_summary(selected),
            "archived_cards": archived_cards,
            "proposals_written": proposals_written,
            "linear_issue": linear_issue,
        }
