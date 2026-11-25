"""Topic ledger persistence, proposal history reconciliation, and candidate scanning."""

from __future__ import annotations

from .common import *
from .cards_and_blog import _vault_coverage, parse_frontmatter


def _load_topic_ledger_document(vault_dir: Path, topic_ledger_path: Path | None = None) -> dict[str, Any]:
    path = topic_ledger_path or vault_dir / "topic-ledger.json"
    if not path.is_file():
        raise TopicLedgerError(f"missing authoritative topic ledger: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TopicLedgerError(f"invalid topic ledger: {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise TopicLedgerError(f"invalid topic ledger document: {path}")
    return doc


def _load_topic_ledger(vault_dir: Path, topic_ledger_path: Path | None = None) -> dict[str, dict[str, Any]]:
    ledger_path = topic_ledger_path or vault_dir / "topic-ledger.json"
    if not ledger_path.is_file():
        raise TopicLedgerError(f"missing authoritative topic ledger: {ledger_path}")
    try:
        doc = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TopicLedgerError(f"invalid topic ledger: {ledger_path}: {exc}") from exc
    raw_topics = doc.get("topics") if isinstance(doc, dict) else None
    if not isinstance(raw_topics, list) or not raw_topics:
        raise TopicLedgerError(f"incomplete topic ledger: {ledger_path}")
    entries: dict[str, dict[str, Any]] = {}
    required = ("topic_key", "source_uri", "question", "learning_state", "source_fingerprint")
    for item in raw_topics:
        if not isinstance(item, dict) or any(not str(item.get(key) or "").strip() for key in required):
            raise TopicLedgerError(f"incomplete topic entry in {ledger_path}")
        key = str(item["topic_key"]).strip()
        if key in entries or str(item["learning_state"]).lower() not in {
            "unknown", "queued", "proposed", "approved", "exploring", "learned",
            "dismissed", "snoozed", "blocked", "transferred",
        }:
            raise TopicLedgerError(f"invalid or duplicate topic state: {key}")
        if any(not isinstance(item.get(field, []), list) for field in ("aliases", "coverage_refs")):
            raise TopicLedgerError(f"invalid topic metadata lists: {key}")
        entries[key] = dict(item)
    return entries


def _save_topic_ledger(
    vault_dir: Path,
    entries: dict[str, dict[str, Any]] | list[dict[str, Any]],
    *,
    topic_ledger_path: Path | None = None,
) -> Path:
    """Save topic-ledger.json while preserving schema_version, policy, sources, vault_coverage_inventory, and unknown topic fields."""
    ledger_path = topic_ledger_path or (vault_dir / "topic-ledger.json")
    existing_doc = _load_topic_ledger_document(vault_dir, topic_ledger_path=ledger_path)

    existing_topics_raw = existing_doc.get("topics", []) if isinstance(existing_doc, dict) else []
    existing_by_tk: dict[str, dict[str, Any]] = {}
    ordered_keys: list[str] = []
    if isinstance(existing_topics_raw, list):
        for item in existing_topics_raw:
            if isinstance(item, dict) and item.get("topic_key"):
                tk = str(item["topic_key"])
                existing_by_tk[tk] = dict(item)
                if tk not in ordered_keys:
                    ordered_keys.append(tk)
    elif isinstance(existing_topics_raw, dict):
        for tk, item in existing_topics_raw.items():
            if isinstance(item, dict):
                row = dict(item)
                row.setdefault("topic_key", str(tk))
                existing_by_tk[str(tk)] = row
                if str(tk) not in ordered_keys:
                    ordered_keys.append(str(tk))

    incoming_list: list[dict[str, Any]] = []
    if isinstance(entries, dict):
        seen_in: set[str] = set()
        for k, v in entries.items():
            if not isinstance(v, dict):
                continue
            tk = str(v.get("topic_key") or k).strip()
            if not tk or tk in seen_in:
                continue
            if k != tk and k.lower() != tk.lower() and tk in entries:
                continue
            seen_in.add(tk)
            row = dict(v)
            row["topic_key"] = tk
            incoming_list.append(row)
    elif isinstance(entries, list):
        for v in entries:
            if isinstance(v, dict) and v.get("topic_key"):
                incoming_list.append(dict(v))

    for inc in incoming_list:
        tk = str(inc["topic_key"])
        if tk in existing_by_tk:
            merged_topic = dict(existing_by_tk[tk])
            for field_k, field_v in inc.items():
                if (
                    field_v is None
                    and field_k in merged_topic
                    and merged_topic[field_k] is not None
                    and field_k not in {"proposed_on", "next_eligible_at", "external_id", "event_id"}
                ):
                    continue
                if field_k == "aliases" and isinstance(merged_topic.get("aliases"), list) and isinstance(field_v, list):
                    merged_topic["aliases"] = list(dict.fromkeys([*merged_topic["aliases"], *field_v]))
                else:
                    merged_topic[field_k] = field_v
            existing_by_tk[tk] = merged_topic
        else:
            existing_by_tk[tk] = dict(inc)
            ordered_keys.append(tk)

    merged_topics = [existing_by_tk[tk] for tk in ordered_keys if tk in existing_by_tk]

    payload: dict[str, Any] = {}
    if isinstance(existing_doc, dict):
        payload.update(existing_doc)
    payload["schema_version"] = (
        existing_doc.get("schema_version", "personal-topic-ledger/v1")
        if isinstance(existing_doc, dict)
        else "personal-topic-ledger/v1"
    )
    payload["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload["topics"] = merged_topics
    _atomic_write_json(ledger_path, payload)
    return ledger_path


def _reconcile_proposals_history(
    proposals_path: Path | None,
    entries: dict[str, dict[str, Any]] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    by_topic: dict[str, dict[str, Any]] = {}
    creation_days: dict[str, str] = {}
    aliases = _topic_alias_map(entries or {})
    question_owners: dict[str, set[str]] = {}
    for key, item in (entries or {}).items():
        if item.get("question"):
            question_owners.setdefault(_question_identity(str(item["question"])), set()).add(key)
    if proposals_path and proposals_path.is_file():
        for line_no, line in enumerate(proposals_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid proposal history JSON at line {line_no}: {exc}") from exc
            required = ("id", "ts", "agent", "title")
            if not isinstance(rec, dict) or any(not str(rec.get(k) or "").strip() for k in required):
                raise ValueError(f"invalid proposal history record at line {line_no}: missing {required}")
            if not _parse_iso_ts(rec["ts"]):
                raise ValueError(f"invalid proposal timestamp at line {line_no}")
            key = str(rec.get("topic_key") or "").strip()
            if not key:
                for ref in (rec.get("origin_ref", ""), rec.get("ref", "")):
                    if str(ref).startswith(("learning-topic://", "knowledge://")):
                        key = str(ref).split("://", 1)[1].strip("/")
                        break
            if not key and str(rec["id"]).startswith(("learn-", "learning-", "knowledge-")):
                key = re.sub(r"^(?:learn|learning|knowledge)-", "", str(rec["id"]))
            if not key:
                continue
            key = aliases.get(key, key)
            owners = question_owners.get(_question_identity(str(rec.get("question") or rec["title"])), set())
            if key not in (entries or {}) and len(owners) == 1:
                key = next(iter(owners))
            created = rec.get("proposed_on") or rec.get("created_at")
            created_at = _parse_iso_ts(str(created or rec["ts"]))
            if created_at is None:
                raise ValueError(f"invalid immutable proposal time at line {line_no}")
            day = _cst_day_str(created_at)
            if created or key not in creation_days:
                creation_days[key] = min(creation_days.get(key, day), day)
            previous = by_topic.get(key)
            if previous is None or int(rec.get("revision", 0)) >= int(previous.get("revision", 0)):
                by_topic[key] = rec

    pending: set[str] = set()
    excluded: set[str] = set()
    now = now_utc or datetime.now(timezone.utc)
    for key in set(entries or {}) | set(by_topic):
        ledger = (entries or {}).get(key, {})
        rec = by_topic.get(key, ledger)
        if int(ledger.get("revision") or 0) > int(rec.get("revision") or 0) or (
            ledger.get("learning_state")
            in {"learned", "dismissed", "blocked", "approved", "exploring", "transferred"}
            and int(rec.get("revision") or 0) <= int(ledger.get("revision") or 1)
        ):
            rec = ledger
        state = str(rec.get("learning_state") or "").lower()
        status = str(rec.get("status") or "").lower()
        if state and state not in {
            "unknown", "queued", "proposed", "approved", "exploring", "learned",
            "dismissed", "snoozed", "blocked", "transferred",
        }:
            raise ValueError(f"invalid learning state for {key}: {state}")
        if state == "snoozed" or status == "snoozed":
            eligible = _parse_iso_ts(str(rec.get("next_eligible_at") or ""))
            changed = _parse_iso_ts(str(rec.get("updated_at") or rec.get("ts") or ""))
            eligible = eligible or (changed + timedelta(days=7) if changed else None)
            if eligible is None or now < eligible:
                excluded.add(key)
        elif state in {"proposed", "transferred"} or status == "transferred" or (not state and status == "pending"):
            pending.add(key)
            excluded.add(key)
        elif state not in {"queued"} or status in {"approved", "dismissed", "blocked", "transferred"}:
            excluded.add(key)
    return {
        "by_topic": by_topic,
        "pending_topics": pending,
        "excluded_topics": excluded,
        "creation_days": creation_days,
        "proposed_today_cst": {key for key, day in creation_days.items() if day == _cst_day_str(now)},
    }


def validate_proposal_record(record: dict[str, Any]) -> list[str]:
    required_fields = ("id", "ts", "agent", "title", "detail", "priority", "status", "ref")
    missing = [
        f
        for f in required_fields
        if not isinstance(record.get(f), str) or not str(record.get(f)).strip()
    ]
    return missing


def _append_valid_proposal(proposals_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    missing = validate_proposal_record(record)
    if missing:
        raise ValueError(f"proposal record missing required non-empty fields: {', '.join(missing)}")
    record.setdefault("created_at", record["ts"])
    record.setdefault("proposed_on", _cst_day_str(_parse_iso_ts(record["created_at"])))
    proposals_path.parent.mkdir(parents=True, exist_ok=True)
    with proposals_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _extract_topic_key_from_proposal(rec: dict[str, Any]) -> str | None:
    tk = str(rec.get("topic_key") or "").strip()
    if tk:
        return tk
    for field in ("origin_ref", "ref"):
        val = str(rec.get(field) or "").strip()
        if val.startswith("learning-topic://"):
            return val.removeprefix("learning-topic://").strip("/")
        if val.startswith("knowledge://"):
            return val.removeprefix("knowledge://").strip("/")
    pid = str(rec.get("id") or "").strip()
    if pid.startswith("learning-topic-"):
        return pid.removeprefix("learning-topic-")
    if pid.startswith("learn-"):
        return pid.removeprefix("learn-")
    return None


def _load_learning_state_registry(
    *,
    proposals_path: Path | None = None,
    state_path: Path | None = None,
    vault_dir: Path | None = None,
    topic_ledger_path: Path | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    resolved = _resolve_state_path(proposals_path, state_path, vault_dir)
    registry: dict[str, Any] = {"schema_version": 1, "updated_at": "", "topics": {}, "consumed_events": {}}
    if resolved.is_file():
        loaded = json.loads(resolved.read_text(encoding="utf-8"))
        if (
            not isinstance(loaded, dict)
            or not isinstance(loaded.get("topics"), dict)
            or not isinstance(loaded.get("consumed_events", {}), dict)
        ):
            raise ValueError(f"invalid learning state registry: {resolved}")
        registry.update(loaded)
    ledger_path = topic_ledger_path or (vault_dir or DEFAULT_VAULT_DIR) / "topic-ledger.json"
    ledger = _load_topic_ledger(vault_dir or DEFAULT_VAULT_DIR, ledger_path) if ledger_path.is_file() else {}
    entries = {key: dict(item) for key, item in ledger.items()}
    for key, item in registry["topics"].items():
        if not isinstance(item, dict):
            raise ValueError(f"invalid learning state entry: {key}")
        if int(item.get("revision") or 0) >= int(entries.get(key, {}).get("revision") or 0):
            entries[key] = {**entries.get(key, {}), **item}
    history = _reconcile_proposals_history(proposals_path, entries, now_utc)
    for key, item in list(entries.items()):
        if item.get("learning_state") == "proposed" and key not in history["by_topic"] and key in ledger:
            entries[key] = dict(ledger[key])
    for key, rec in history["by_topic"].items():
        prior = entries.get(key, {})
        if int(rec.get("revision") or 0) < int(prior.get("revision") or 0) or (
            prior.get("learning_state")
            in {"learned", "dismissed", "blocked", "approved", "exploring", "transferred"}
            and int(rec.get("revision") or 0) <= int(prior.get("revision") or 1)
        ):
            continue
        state = str(rec.get("learning_state") or "")
        if not state:
            status = rec.get("status")
            state = (
                "transferred"
                if status == "approved" and str(rec.get("ref", "")).startswith("linear://") and not rec.get("exploration_authorized")
                else {"pending": "proposed", "approved": "approved", "dismissed": "dismissed"}.get(status, "unknown")
            )
        entries[key] = {**prior, **rec, "topic_key": key, "proposal_id": rec["id"], "learning_state": state}
        if str(rec.get("ref", "")).startswith("linear://"):
            entries[key]["external_id"] = rec["ref"]
        if key in history["creation_days"]:
            entries[key]["proposed_on"] = history["creation_days"][key]
    registry["topics"] = entries
    return registry


def _save_learning_state_registry(
    registry: dict[str, Any],
    *,
    proposals_path: Path | None = None,
    state_path: Path | None = None,
    vault_dir: Path | None = None,
) -> Path:
    resolved = _resolve_state_path(proposals_path, state_path, vault_dir)
    registry["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    _atomic_write_json(resolved, registry)
    return resolved


def _topic_exclusion_reason(
    cand: dict[str, Any],
    registry: dict[str, Any],
    today_cst: date,
) -> str | None:
    topics_map: dict[str, dict[str, Any]] = registry.get("topics", {})
    cand_tk = str(cand.get("topic_key") or cand.get("slug") or "").strip()
    cand_aliases = {cand_tk.lower(), str(cand.get("slug") or "").lower()}
    for al in cand.get("aliases") or []:
        if str(al).strip():
            cand_aliases.add(str(al).strip().lower())

    matched_entry: dict[str, Any] | None = None
    if cand_tk in topics_map:
        matched_entry = topics_map[cand_tk]
    else:
        for t_k, t_entry in topics_map.items():
            entry_aliases = {t_k.lower()}
            for al in t_entry.get("aliases") or []:
                if str(al).strip():
                    entry_aliases.add(str(al).strip().lower())
            if cand_aliases & entry_aliases:
                matched_entry = t_entry
                break

    if matched_entry is None:
        return None

    lstate = str(matched_entry.get("learning_state") or "").strip().lower()
    if lstate in {"proposed", "pending"}:
        return "already_pending"
    if lstate == "transferred":
        return "already_transferred_to_linear"
    if lstate == "approved":
        return "already_approved"
    if lstate == "exploring":
        return "already_exploring"
    if lstate in {"learned", "covered"}:
        return "already_learned"
    if lstate == "blocked":
        return "blocked_requires_attention"
    if lstate == "dismissed":
        if not matched_entry.get("reopened"):
            return "dismissed_cooldown_or_no_auto_revive"
    if lstate == "snoozed":
        next_el = _parse_iso_date(matched_entry.get("next_eligible_at"))
        if next_el is None:
            upd = _parse_iso_date(matched_entry.get("updated_at") or matched_entry.get("proposed_on"))
            if upd is not None:
                next_el = upd + timedelta(days=SNOOZE_COOLDOWN_DAYS)
        if next_el is None or today_cst < next_el:
            return "snoozed_cooldown"
    return None


def _scan_collected_tools_candidates(
    collected_dir: Path,
    existing_corpus: str,
    vault_dir: Path | None = None,
    ledger_entries: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not vault_dir or not collected_dir.is_dir():
        return []
    entries = ledger_entries if ledger_entries is not None else _load_topic_ledger(vault_dir)
    covered, covered_questions = _vault_coverage(vault_dir)
    source_paths: dict[str, Path] = {}
    uri_paths: dict[str, Path] = {}
    for note in sorted(collected_dir.glob("*.md")):
        if note.name.lower() == "readme.md":
            continue
        text = note.read_text(encoding="utf-8")
        if not text.strip():
            continue
        source_paths[note.stem] = note
        meta, _ = parse_frontmatter(text)
        uri = str(meta.get("source_uri") or "")
        if not uri:
            match = re.search(r"https?://[^\s<>\])]+|git@github\.com:[^\s<>\])]+", text)
            uri = match.group(0) if match else ""
        if uri:
            uri_paths[_normalize_source_uri(uri)] = note
    for repo in sorted((collected_dir / "repos").glob("*")):
        readme = next((p for p in (repo / "README.md", repo / "readme.md") if p.is_file()), None)
        if not readme or not readme.read_text(encoding="utf-8").strip():
            continue
        source_paths[repo.name] = readme
        config = repo / ".git" / "config"
        if config.is_file():
            match = re.search(r"(?m)^\s*url\s*=\s*(\S+)", config.read_text(encoding="utf-8"))
            if match:
                uri_paths[_normalize_source_uri(match.group(1))] = readme
    candidates: list[dict[str, Any]] = []
    topic_aliases = _topic_alias_map(entries)
    for key, item in entries.items():
        state = str(item["learning_state"]).lower()
        if state not in {"queued", "snoozed"} or item.get("scope", "personal") != "personal":
            continue
        aliases = {key} | {alias for alias in item.get("aliases", []) if topic_aliases.get(alias) == key}
        question = str(item["question"]).strip()
        if aliases & covered or _question_identity(question) in covered_questions:
            continue
        if len(question) < 12 or not re.search(r"[?？]|如何|怎样|为什么|\b(how|why|what|when)\b", question, re.I):
            continue
        source_id = str(item.get("source_id") or "")
        names = {source_id, *item.get("aliases", [])}
        source_ref = next((source_paths[name] for name in sorted(names) if name in source_paths), None)
        source_ref = source_ref or uri_paths.get(_normalize_source_uri(str(item["source_uri"])))
        if source_ref is None:
            continue
        candidates.append({
            "slug": key,
            "topic_key": key,
            "source_id": source_id,
            "source_uri": _normalize_source_uri(str(item["source_uri"])),
            "source_fingerprint": item["source_fingerprint"],
            "aliases": list(item.get("aliases", [])),
            "title": str(item.get("title") or question),
            "question": question,
            "category": str(item.get("category") or "工程与设计"),
            "source_kind": str(item.get("source_kind") or "collected_tools"),
            "source_ref": str(source_ref),
            "why_now": str(item.get("why_now") or question),
            "coverage_refs": list(item.get("coverage_refs", [])),
            "excerpt": question,
            "rank": int(item.get("rank") or item.get("priority_rank") or 99),
            "scope": "personal",
            "track": infer_topic_track(item),
        })
    return sorted(candidates, key=lambda item: (item["rank"], item["topic_key"]))


def _scan_git_tech_stack_candidates(git_roots: tuple[Path, ...], existing_corpus: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    commit_lines: list[str] = []
    for root in git_roots:
        if not (root / ".git").exists():
            continue
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), "--no-pager", "log", "--since=7.days", "-n", "15", "--oneline"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                commit_lines.extend(proc.stdout.strip().splitlines())
        except (OSError, subprocess.SubprocessError):
            continue

    if not commit_lines:
        return []

    joined_commits = "\n".join(commit_lines).lower()
    topic_probes = [
        (
            "jev-r09-r06-truth-table",
            "Jev System One R09 重排与 R06 引用核查：用零幻觉真值表替代黑盒打分",
            "计算机与网络",
            ("r09", "r06", "jev", "systemone", "rerank"),
            "Recent commits wire Jev R09 score reranking and R06 citation verification across research and digest pipelines.",
            0.84,
            2.4,
        ),
        (
            "progressive-disclosure-four-valves",
            "渐进式披露 Skill 四道防爆阀门：为什么 L0/L1/L2 预算能让 Token 降一个量级",
            "工程与设计",
            ("skill-contract", "repo-bound", "progressive", "drawer", "audit"),
            "Recent git commits refine skill-contract L0/L1/L2 progressive disclosure budgets and executable eval drawers.",
            0.82,
            2.3,
        ),
        (
            "aidash-xpc-event-bridge-flywheel",
            "AIDash 单向数据流与 Event Bridge 闭环：从 L1 遥测采集到点 Star 自动触发动作",
            "工程与设计",
            ("event_bridge", "aidata", "linear_issue", "aidash", "proposal"),
            "Recent commits in AIDash connect L1-L5 telemetry with event_bridge Star/Done actions.",
            0.80,
            2.2,
        ),
    ]
    for slug, title, category, keywords, excerpt, p_direct, s_tier in topic_probes:
        if slug in existing_corpus:
            continue
        matched = any(k in joined_commits for k in keywords)
        if matched:
            candidates.append({
                "slug": slug,
                "topic_key": slug,
                "source_id": slug,
                "source_uri": f"git://{slug}",
                "aliases": [slug],
                "title": title,
                "question": title,
                "category": category,
                "source_kind": "git_tech_stack",
                "source_ref": "git-log",
                "why_now": excerpt,
                "coverage_refs": [],
                "excerpt": excerpt,
                "heuristic_direct": p_direct,
                "heuristic_tier": s_tier,
                "rank": 120,
                "scope": "personal",
                "track": infer_topic_track({"topic_key": slug, "title": title, "category": category}),
            })
    return candidates


_scan_git_candidates = _scan_git_tech_stack_candidates


def _rerank_candidates_r09(
    candidates: list[dict[str, Any]],
    *,
    mock_r09_answers: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    research_helper = _load_helper_module(
        "jev_research_helper",
        SKILLS_ROOT / "deep-research" / "scripts" / "jev_research_helper.py",
    )
    jev_mod = _load_helper_module(
        "jev_client",
        SKILLS_ROOT / "team-lead" / "scripts" / "jev_client.py",
    )

    answers: dict[str, Any] | None = mock_r09_answers
    if answers is None and research_helper is not None and jev_mod is not None:
        req = research_helper.build_r09_request(
            research_goal="Select high-value learning topics for knowledge-vault and AIDash",
            sub_question="Which candidate topic fills a high-leverage gap in recent engineering & tool stockpile?",
            candidates=[
                {"slug": c["slug"], "title": c["title"], "excerpt": c["excerpt"]}
                for c in candidates
            ],
        )
        decision = jev_mod.jev_decide(
            req["state"],
            req["questions"],
            agent_role="knowledge-digest",
        )
        if isinstance(decision, dict):
            answers = decision.get("answers", decision)

    if answers is None:
        answers = {}
        for idx, cand in enumerate(candidates):
            p_d = float(cand.get("heuristic_direct", 0.82))
            s_t = float(cand.get("heuristic_tier", 2.4))
            answers[f"cand_{idx}_direct"] = {"noul": p_d}
            answers[f"cand_{idx}_tier"] = {
                "score": s_t,
                "probabilities": {"0": 0.05, "2": 0.45, "3": 0.50},
            }

    if research_helper is not None:
        rows = research_helper.evaluate_r09_truth_table(answers, candidates)
    else:
        rows = []
        for idx, cand in enumerate(candidates):
            p_d = float(answers.get(f"cand_{idx}_direct", {}).get("noul", 0.8))
            s_t = float(answers.get(f"cand_{idx}_tier", {}).get("score", 2.4))
            rows.append({
                "index": idx,
                "composite_score": round(0.65 * p_d + 0.35 * (s_t / 3.0), 6),
                "action": "KEEP_PRIMARY" if p_d >= 0.7 and s_t >= 2.0 else "KEEP_SUPPORTING",
                "p_direct": p_d,
                "s_tier": s_t,
            })
        rows.sort(key=lambda r: (-r["composite_score"], r["index"]))

    ranked: list[dict[str, Any]] = []
    for row in rows:
        if row["action"] == "DROP_NOISE":
            continue
        cand = dict(candidates[row["index"]])
        cand["r09"] = row
        ranked.append(cand)
    return ranked


def _reconcile_collected_tools_entities(
    collected_dir: Path,
    existing_corpus: str,
    *,
    vault_dir: Path,
    topic_ledger_path: Path | None = None,
    include_unreconciled_repos: bool = False,
):
    entries = _load_topic_ledger(vault_dir, topic_ledger_path)
    candidates = _scan_collected_tools_candidates(collected_dir, existing_corpus, vault_dir, entries)
    sources = {str(item.get("source_id") or "") for item in entries.values()}
    local_sources = {p.name for p in (collected_dir / "repos").glob("*") if p.is_dir()}
    learned = sorted({str(item.get("source_id")) for item in entries.values() if item["learning_state"] == "learned"})
    return candidates, {
        "merged_entities": sorted({p.stem for p in collected_dir.glob("*.md") if (collected_dir / "repos" / p.stem).is_dir()}),
        "covered_sources": learned,
        "integrated_sources": learned,
        "unknown_sources": sorted(local_sources - sources),
    }
