#!/usr/bin/env python3
"""Deterministic Loop 5 collector for design taste evidence.

The collector never edits a catalog or gate.  It turns PreviewHost selections and
Visual Traceback receipts into a stable, reviewable proposal for the Owner.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

SCRIPT_PATH = Path(__file__).resolve()
SKILLS_ROOT = SCRIPT_PATH.parents[2]
JEV_CLIENT_PATH = SKILLS_ROOT / "team-lead" / "scripts" / "jev_client.py"
ISSUE_BRIEF_PATH = SKILLS_ROOT / "issue-brief" / "scripts" / "verify_issue_format.py"
LOOP_ID = "design-taste-evolution"
HANDOFF_ENV = "DESIGN_TASTE_HANDOFF_ROOT"
POSITIVE_KINDS = ("selected", "starred", "note")
CHOICES = ("PROMOTE_POSITIVE_ARCHETYPE", "PROMOTE_ANTI_PATTERN_GATE", "ONE_OFF_CONTEXT_NOTE")
POSITIVE_WORDS = re.compile(r"(?i)compact|hierarch|breath|clear|calm|glass|split|native|inline|focus|层次|留白|紧凑|清晰|原位|玻璃|分栏|克制")
ANTI_WORDS = re.compile(r"(?i)reject|rejection|anti[- ]?pattern|cramped|truncat|overflow|flat card|bottom capsule|clutter|不选|拒绝|打回|反模式|拥挤|截断|溢出|扁平|胶囊|杂乱")
REJECTION_WORDS = re.compile(r"(?i)reject(?:ed|ion)?|declin(?:e|ed)|not selected|do not use|anti[- ]?pattern|不选|拒绝|打回|不要|禁用|反模式")
TRACEBACK_RE = re.compile(r"\[(IMPL_BUG|SPEC_GAP)\]")
SELECTION_NAMES = re.compile(r"^preview-selection(?:-r\d+)?\.json$")
INTERNAL_CODE_RE = re.compile(r"(?<![A-Za-z0-9_-])(?:S(?:1[0-2]|[1-9])|M[1-5]|EV-\d{1,4}|DC-\d{1,4}|H[1-5]|R10|V[1-5])(?![A-Za-z0-9_-])")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
HEX_SHA_RE = re.compile(r"(?<![A-Za-z0-9_-])[0-9a-fA-F]{7,40}(?![A-Za-z0-9_-])")
CLI_COMMAND_RE = re.compile(r"(?:python3?|pytest|bash|sh|curl|gh|multica|git)\s+\S+|\./scripts/\S+|skills/\S+/scripts/\S+")
TAG_RE = re.compile(r"<[^>]*>")


def _sanitize_human_text(value: Any, fallback: str = "该证据") -> str:
    text = _clean(value)
    text = TAG_RE.sub(" ", text)
    text = UUID_RE.sub("标识符", text)
    text = HEX_SHA_RE.sub("提交标识", text)
    text = CLI_COMMAND_RE.sub("命令证据", text)
    text = INTERNAL_CODE_RE.sub("规则证据", text)
    text = text.replace("|", "／").replace("<", "‹").replace(">", "›")
    text = re.sub(r"\s+", " ", text).strip(" -:：")
    return (text or fallback)[:180]


def _sanitize_ai_text(value: Any) -> str:
    text = _clean(value)
    return text.replace("<", "&lt;").replace(">", "&gt;")[:240]


def _goal_and_round(path: Path, root: Path) -> tuple[str, str]:
    relative = Path(_relative(path, root))
    goal = relative.parts[0] if relative.parts else "unknown"
    match = re.search(r"preview-selection-r(\d+)\.json$", path.name)
    return goal, f"r{match.group(1)}" if match else "active"


def _default_handoff_root() -> Path:
    return Path(os.environ.get(HANDOFF_ENV, "~/Development/handoff")).expanduser()


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_jev() -> Any:
    return _load_module("taste_evolution_jev_client", JEV_CLIENT_PATH)


def _load_issue_brief() -> Any:
    return _load_module("taste_evolution_issue_brief", ISSUE_BRIEF_PATH)


def _json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value: Any) -> str:
    text = _clean(value).casefold()
    return re.sub(r"[^\w\u4e00-\u9fff]+", " ", text, flags=re.UNICODE).strip()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _as_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (tuple, list, set)):
        return sorted({_clean(item) for item in value if _clean(item)})
    return []


def _notes(value: Any) -> list[tuple[str, str]]:
    if isinstance(value, Mapping):
        return sorted((str(key), _clean(note)) for key, note in value.items() if _clean(note))
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, Mapping):
                vid = _clean(item.get("variantID") or item.get("variantId") or item.get("variant"))
                note = _clean(item.get("note") or item.get("text") or item.get("body"))
                if note:
                    result.append((vid, note))
        return sorted(result)
    return []


def _inventory(payload: Mapping[str, Any]) -> set[str]:
    found: set[str] = set()
    for key in ("variantIDs", "variantIds", "availableVariantIDs", "availableVariants"):
        found.update(_as_ids(payload.get(key)))
    variants = payload.get("variants")
    if isinstance(variants, Mapping):
        found.update(_clean(key) for key in variants if _clean(key))
    elif isinstance(variants, list):
        for item in variants:
            if isinstance(item, Mapping):
                vid = _clean(item.get("id") or item.get("variantID") or item.get("variantId"))
                if vid:
                    found.add(vid)
            elif _clean(item):
                found.add(_clean(item))
    return found


def _rejected(payload: Mapping[str, Any], inventory: set[str]) -> list[tuple[str, str]]:
    if not inventory:
        return []
    result: set[tuple[str, str]] = set()
    for key in ("rejectedVariantIDs", "rejectedVariants", "rejections"):
        raw = payload.get(key)
        if isinstance(raw, Mapping):
            for vid, reason in raw.items():
                if _clean(vid) in inventory:
                    result.add((_clean(vid), _clean(reason) or "explicit rejection"))
        else:
            for item in _as_ids(raw):
                if item in inventory:
                    result.add((item, "explicit rejection"))
    by_variant = payload.get("rejectionsByVariant")
    if isinstance(by_variant, Mapping):
        for vid, reason in by_variant.items():
            if _clean(vid) in inventory:
                result.add((_clean(vid), _clean(reason) or "explicit rejection"))
    variants = payload.get("variants")
    if isinstance(variants, Mapping):
        variants = [dict(value, id=key) if isinstance(value, Mapping) else {"id": key, "value": value} for key, value in variants.items()]
    if isinstance(variants, list):
        for item in variants:
            if not isinstance(item, Mapping):
                continue
            vid = _clean(item.get("id") or item.get("variantID") or item.get("variantId"))
            status = _clean(item.get("status") or item.get("decision") or item.get("selection")).casefold()
            if vid in inventory and (item.get("rejected") is True or status in {"rejected", "reject", "declined", "not_selected"}):
                result.add((vid, _clean(item.get("reason") or item.get("note")) or "explicit rejection"))
    return sorted(result)


def _add_evidence(store: dict[tuple[str, str, str], dict[str, Any]], *, kind: str, variant_id: str, text: str, source: str, line: int | None = None, classification: str | None = None, goal: str | None = None, round_id: str | None = None) -> None:
    variant_id, text = _clean(variant_id), _clean(text)
    key = (kind, variant_id, _norm(text) or kind)
    row = store.get(key)
    if row is None:
        row = {"kind": kind, "variant_id": variant_id, "text": text or kind, "count": 0, "sources": [], "classifications": [], "_rounds": {}}
        store[key] = row
    provenance = {"path": source}
    if line is not None:
        provenance["line"] = line
    if provenance not in row["sources"]:
        row["sources"].append(provenance)
    if classification and classification not in row["classifications"]:
        row["classifications"].append(classification)
    if goal is None or round_id is None:
        row["count"] += 1
        return
    rounds = row["_rounds"].setdefault(goal, set())
    if round_id not in rounds:
        duplicate_active = round_id == "active" and any(item != "active" for item in rounds)
        duplicate_archived = round_id != "active" and "active" in rounds
        if not (duplicate_active or duplicate_archived):
            row["count"] += 1
        rounds.add(round_id)


def _self_marker(path: Path, root: Path, content: str = "") -> bool:
    relative = _relative(path, root)
    return (
        relative.split("/", 1)[0] == LOOP_ID
        or "taste-evolution-harvest" in content
        or "taste-evolution-proposals" in content
        or "multica-cursor:design-taste-evolution" in content
        or "taste_evolution_loop.py" in content
    )


def _selection_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        (path for goal in root.iterdir() if goal.is_dir() and goal.name != LOOP_ID for path in (goal / "receipts").glob("preview-selection*.json") if path.is_file() and SELECTION_NAMES.fullmatch(path.name)),
        key=lambda p: _relative(p, root),
    )


def _review_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    result: list[Path] = []
    for goal in root.iterdir():
        if not goal.is_dir() or goal.name == LOOP_ID:
            continue
        receipts = goal / "receipts"
        candidates = list(receipts.glob("*review*.md")) + list(receipts.glob("*visual*.md")) + [goal / "design.md"]
        for path in candidates:
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if not _self_marker(path, root, content):
                    result.append(path)
    return sorted(set(result), key=lambda p: _relative(p, root))


def _noise_line(text: str) -> bool:
    lowered = text.casefold()
    if not text or text.startswith("#") or (text.startswith("|") and text.endswith("|")):
        return True
    if re.search(r"(?:^|\b)(?:no|none|zero|0)\s*(?:\[?(?:impl_bug|spec_gap)\]?|findings?)", lowered):
        return True
    if "reviewer instruction" in lowered or "review prompt" in lowered or ("reviewer" in lowered and ("should" in lowered or "must" in lowered or "不要" in text)) or "不要重新扫描" in text:
        return True
    return False


def _variant_entry(payload: Mapping[str, Any], variant_id: str) -> Mapping[str, Any]:
    variants = payload.get("variants")
    if isinstance(variants, Mapping) and isinstance(variants.get(variant_id), Mapping):
        return variants[variant_id]
    if isinstance(variants, list):
        for item in variants:
            if isinstance(item, Mapping) and _clean(item.get("id") or item.get("variantID") or item.get("variantId")) == variant_id:
                return item
    return {}


def _variant_context(payload: Mapping[str, Any], variant_id: str, entry: Mapping[str, Any], note: str = "") -> str:
    top = payload.get("variantSummaries") or payload.get("variantTitles") or {}
    candidates = [note, entry.get("note"), entry.get("notes"), entry.get("summary"), entry.get("title"), entry.get("name"), top.get(variant_id) if isinstance(top, Mapping) else ""]
    return next((_clean(value) for value in candidates if _clean(value)), "")


def harvest(handoff_root: Path | str | None = None) -> dict[str, Any]:
    """Harvest only scoped, concrete evidence and never invent cross-receipt inventory."""
    root = Path(handoff_root).expanduser() if handoff_root is not None else _default_handoff_root()
    root = root.resolve()
    positive: dict[tuple[str, str, str], dict[str, Any]] = {}
    rejections: dict[tuple[str, str, str], dict[str, Any]] = {}
    tracebacks: dict[tuple[str, str, str], dict[str, Any]] = {}
    inventory: set[str] = set()
    selection_count = 0
    malformed: list[str] = []
    for path in _selection_files(root):
        payload = _json_file(path)
        source = _relative(path, root)
        if not isinstance(payload, Mapping) or _self_marker(path, root, json.dumps(payload, ensure_ascii=False)):
            if payload is None:
                malformed.append(source)
            continue
        selection_count += 1
        goal, round_id = _goal_and_round(path, root)
        file_inventory = _inventory(payload)
        inventory.update(file_inventory)
        notes = dict(_notes(payload.get("notesByVariant")))
        variants = payload.get("variants")
        variant_items = variants.items() if isinstance(variants, Mapping) else []
        for vid, raw_entry in variant_items:
            entry = raw_entry if isinstance(raw_entry, Mapping) else {}
            variant_id = _clean(vid)
            context = _variant_context(payload, variant_id, entry, notes.get(variant_id, ""))
            if entry.get("isSelected") is True and context:
                _add_evidence(positive, kind="selected", variant_id=variant_id, text=context, source=source, goal=goal, round_id=round_id)
            if entry.get("isStarred") is True and context:
                _add_evidence(positive, kind="starred", variant_id=variant_id, text=context, source=source, goal=goal, round_id=round_id)
            entry_note = _variant_context(payload, variant_id, entry, "")
            if entry_note and _clean(entry.get("notes") or entry.get("note")):
                _add_evidence(positive, kind="note", variant_id=variant_id, text=entry_note, source=source, goal=goal, round_id=round_id)
        selected = _clean(payload.get("selectedVariantID") or payload.get("selectedVariantId"))
        if selected in file_inventory:
            context = _variant_context(payload, selected, _variant_entry(payload, selected), notes.get(selected, ""))
            if context:
                _add_evidence(positive, kind="selected", variant_id=selected, text=context, source=source, goal=goal, round_id=round_id)
        for variant in _as_ids(payload.get("starredVariantIDs") or payload.get("starredVariantIds")):
            if variant not in file_inventory:
                continue
            context = _variant_context(payload, variant, _variant_entry(payload, variant), notes.get(variant, ""))
            if context:
                _add_evidence(positive, kind="starred", variant_id=variant, text=context, source=source, goal=goal, round_id=round_id)
        for variant, note in _notes(payload.get("notesByVariant")):
            if variant in file_inventory:
                _add_evidence(positive, kind="note", variant_id=variant, text=note, source=source, goal=goal, round_id=round_id)
        for variant, reason in _rejected(payload, file_inventory):
            _add_evidence(rejections, kind="rejection", variant_id=variant, text=reason, source=source, goal=goal, round_id=round_id)
    for path in _review_files(root):
        source = _relative(path, root)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            malformed.append(source)
            continue
        for number, line in enumerate(lines, 1):
            text = _clean(line)
            if _noise_line(text):
                continue
            match = TRACEBACK_RE.search(text)
            if match:
                _add_evidence(tracebacks, kind="visual_traceback", variant_id="", text=text, source=source, line=number, classification=match.group(1))
            elif REJECTION_WORDS.search(text):
                _add_evidence(rejections, kind="review_rejection", variant_id="", text=text, source=source, line=number)
    def ordered(values: dict[tuple[str, str, str], dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for row in values.values():
            clean_row = {key: value for key, value in row.items() if not key.startswith("_")}
            rows.append(clean_row)
        return sorted(rows, key=lambda row: (row["kind"], row["variant_id"], _norm(row["text"])))
    return {
        "schema": "taste-evolution-harvest/v1",
        "handoff_root": root.as_posix(),
        "selection_files": selection_count,
        "inventory": sorted(inventory),
        "positive_preferences": ordered(positive),
        "rejections": ordered(rejections),
        "visual_tracebacks": ordered(tracebacks),
        "malformed_files": sorted(malformed),
    }


def _mock_value(mock: Any, candidate: Mapping[str, Any], index: int) -> Any:
    if isinstance(mock, (str, Path)):
        try:
            candidate_path = Path(str(mock)).expanduser()
            raw = candidate_path.read_text(encoding="utf-8") if candidate_path.is_file() else str(mock)
            mock = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid --mock-jev-json") from exc
    if isinstance(mock, list):
        if index >= len(mock):
            raise ValueError(f"missing mock Jev entry at index {index + 1}")
        return mock[index]
    if isinstance(mock, Mapping):
        candidate_id = _clean(candidate.get("candidate_id"))
        normalized = _norm(candidate.get("text"))
        query = _norm(candidate.get("query"))
        for key in (candidate_id, normalized, query, _clean(candidate.get("text")), _clean(candidate.get("query")), str(index + 1)):
            if key and key in mock:
                return mock[key]
        if "default" in mock:
            return mock["default"]
        if "answers" in mock:
            return mock
    raise ValueError("mock Jev JSON has no matching candidate entry or default")


def _number(value: Any, key: str) -> float | None:
    if isinstance(value, Mapping):
        value = value.get(key, value.get("value"))
    if isinstance(value, bool) or value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0 <= value <= 1 else None


def _fallback_choice(text: str, *, anti: bool, count: int) -> str:
    recurring = count >= 2
    if anti or ANTI_WORDS.search(text):
        return "PROMOTE_ANTI_PATTERN_GATE" if recurring else "ONE_OFF_CONTEXT_NOTE"
    if recurring and POSITIVE_WORDS.search(text):
        return "PROMOTE_POSITIVE_ARCHETYPE"
    return "ONE_OFF_CONTEXT_NOTE"


def _decide(candidate: Mapping[str, Any], *, index: int, mock_jev_json: Any = None, jev_fn: Any = None) -> dict[str, Any]:
    text = _clean(candidate.get("text"))
    anti = candidate.get("kind") in {"rejection", "review_rejection", "visual_traceback"}
    count = int(candidate.get("count") or 0)
    fallback = _fallback_choice(text, anti=anti, count=count)
    supplied = mock_jev_json is not None
    questions = {
        "promotion": {"type": "choice", "instructions": "Classify this taste evidence.", "criteria": {choice: choice for choice in CHOICES}},
        "confidence": {"type": "noul", "instructions": "How confidently does this evidence support the classification?"},
        "score": {"type": "score", "instructions": "Score recurrence and usefulness from 0.0 to 1.0."},
    }
    if supplied:
        response = _mock_value(mock_jev_json, candidate, index)
    else:
        decide = jev_fn or _load_jev().jev_decide
        response = decide(
            state={"candidate": {"id": candidate.get("candidate_id"), "kind": candidate.get("kind"), "text": text, "count": count}},
            questions=questions,
            agent_role="design-studio",
            goal_id=LOOP_ID,
            timeout_sec=1.5,
            falcon_only=True,
            mock_response=None,
        )
    if not isinstance(response, Mapping):
        if supplied:
            raise ValueError("mock Jev entry must be an object")
        return {"choice": fallback, "noul": None, "score": None, "source": "deterministic_fallback_unavailable"}
    answers = response.get("answers", response)
    if not isinstance(answers, Mapping):
        if supplied:
            raise ValueError("mock Jev entry answers must be an object")
        return {"choice": fallback, "noul": None, "score": None, "source": "deterministic_fallback_malformed"}
    raw_choice = answers.get("promotion") or answers.get("classification") or answers.get("choice")
    if isinstance(raw_choice, Mapping):
        raw_choice = raw_choice.get("choice")
    confidence = _number(answers.get("confidence") or answers.get("noul"), "noul")
    score = _number(answers.get("score"), "score")
    if raw_choice not in CHOICES or confidence is None or score is None:
        if supplied:
            raise ValueError("mock Jev entry requires valid promotion choice, noul, and score in [0,1]")
        return {"choice": fallback, "noul": None, "score": None, "source": "deterministic_fallback_incomplete"}
    if confidence < 0.30:
        choice, source = "ONE_OFF_CONTEXT_NOTE", "jev_low_confidence_reject"
    elif confidence <= 0.70:
        choice, source = fallback, "deterministic_fallback_grey_zone"
    elif count < 2 and raw_choice in {"PROMOTE_POSITIVE_ARCHETYPE", "PROMOTE_ANTI_PATTERN_GATE"}:
        choice, source = "ONE_OFF_CONTEXT_NOTE", "jev_nonrecurring_reject"
    else:
        choice, source = str(raw_choice), "jev"
    return {"choice": choice, "noul": confidence, "score": score, "source": source}


def cluster(harvested: Mapping[str, Any], *, mock_jev_json: Any = None, jev_fn: Any = None) -> dict[str, Any]:
    """Cluster deduplicated evidence and classify it through Jev or deterministic fallback."""
    rows = list(harvested.get("positive_preferences") or []) + list(harvested.get("rejections") or []) + list(harvested.get("visual_tracebacks") or [])
    candidates = []
    for index, row in enumerate(sorted(rows, key=lambda item: (str(item.get("kind")), str(item.get("variant_id")), _norm(item.get("text"))))):
        candidate = dict(row)
        candidate["candidate_id"] = "taste-" + hashlib.sha256(f"{candidate.get('kind')}|{candidate.get('variant_id')}|{_norm(candidate.get('text'))}".encode()).hexdigest()[:12]
        candidate["decision"] = _decide(candidate, index=index, mock_jev_json=mock_jev_json, jev_fn=jev_fn)
        candidates.append(candidate)
    return {"schema": "taste-evolution-clusters/v1", "candidates": candidates}


def _candidate_id(prefix: str, text: str) -> str:
    return prefix + "-" + hashlib.sha256(_norm(text).encode()).hexdigest()[:10].upper()


def propose_gates(harvested: Mapping[str, Any], clustered: Mapping[str, Any] | None = None, *, mock_jev_json: Any = None, jev_fn: Any = None) -> dict[str, Any]:
    if clustered is None:
        clustered = cluster(harvested, mock_jev_json=mock_jev_json, jev_fn=jev_fn)
    positives, anti, notes = [], [], []
    for item in clustered.get("candidates", []):
        decision = item.get("decision") or {}
        choice = decision.get("choice")
        text = _clean(item.get("text"))
        base = {"candidate_id": item.get("candidate_id"), "evidence_kind": item.get("kind"), "text": text, "count": item.get("count", 0), "sources": item.get("sources", []), "decision": decision}
        if choice == "PROMOTE_POSITIVE_ARCHETYPE":
            base.update({"rule_id": _candidate_id("IP", text), "rule_type": "positive_preference"})
            positives.append(base)
        elif choice == "PROMOTE_ANTI_PATTERN_GATE":
            base.update({"gate_id": _candidate_id("DC", text), "rule_type": "anti_pattern_gate"})
            anti.append(base)
        else:
            notes.append(base)
    positives.sort(key=lambda item: (item["rule_id"], item["text"]))
    anti.sort(key=lambda item: (item["gate_id"], item["text"]))
    notes.sort(key=lambda item: (item["candidate_id"], item["text"]))
    signature = hashlib.sha256(json.dumps({"positive": positives, "anti": anti, "notes": notes}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return {"schema": "taste-evolution-proposals/v1", "positive_preferences": positives, "anti_pattern_gates": anti, "one_off_context_notes": notes, "gate_candidates": positives + anti, "has_actionable": bool(positives or anti), "signature": signature, "watermark": f"{LOOP_ID}:{signature}"}


def _issue_markdown(proposal: Mapping[str, Any], harvested: Mapping[str, Any], *, target_date: date | str | None = None, cursor: str | None = None) -> str:
    issue = _load_issue_brief()
    items = []
    seen_whats: set[str] = set()
    for row in list(proposal.get("positive_preferences") or []):
        label = _sanitize_human_text(row.get("text"), "稳定的界面偏好")
        vid = _sanitize_human_text(row.get("variant_id"), "")
        what = f"保留偏好：{label}" + (f"（`{vid}`）" if vid and label not in seen_whats else "")
        if what in seen_whats:
            continue
        seen_whats.add(what)
        items.append({"what": what, "why": f"连续 {max(1, int(row.get('count') or 1))} 轮选型证据支持沉淀为审美规则"})
        if len(items) >= 4:
            break
    for row in list(proposal.get("anti_pattern_gates") or []):
        label = _sanitize_human_text(row.get("text"), "重复出现的界面问题")
        what = f"加入反模式门禁：{label}"
        if what in seen_whats:
            continue
        seen_whats.add(what)
        items.append({"what": what, "why": f"视觉回溯或拒绝记录（{max(1, int(row.get('count') or 1))} 次）显示需要拦截"})
        if len(items) >= 8:
            break
    if not items:
        items = [{"what": "暂不新增审美门禁", "why": "当前证据不足以形成稳定偏好或反模式"}]
    date_text = str(target_date or datetime.now(timezone.utc).date())
    clean_cursor = cursor or f"{LOOP_ID}-{proposal.get('signature')}"
    compact_candidates = [
        {"kind": row.get("evidence_kind"), "text": _sanitize_ai_text(row.get("text")), "count": row.get("count", 0), "decision": (row.get("decision") or {}).get("choice")}
        for row in (list(proposal.get("positive_preferences") or [])[:4] + list(proposal.get("anti_pattern_gates") or [])[:4])
    ]
    ai_payload = {
        "loop_id": LOOP_ID,
        "date": date_text,
        "cursor": clean_cursor,
        "watermark": _sanitize_ai_text(proposal.get("watermark")),
        "signature": _sanitize_ai_text(proposal.get("signature")),
        "counts": {"positive": len(harvested.get("positive_preferences", [])), "rejections": len(harvested.get("rejections", [])), "visual_tracebacks": len(harvested.get("visual_tracebacks", []))},
        "candidates": compact_candidates,
    }
    markdown = issue.render_dual_zone_issue(
        tldr=f"品味回溯汇总了 {ai_payload['counts']['positive']} 条正向选择与 {ai_payload['counts']['visual_tracebacks']} 条视觉回溯证据，请 Owner 决定是否沉淀。",
        items=items,
        cursor=clean_cursor,
        ai_payload=ai_payload,
    )
    check = issue.verify_dual_zone_issue(markdown)
    if not check.get("ok"):
        raise ValueError(f"Dual-Zone verification failed: {check.get('violations')}")
    return markdown


def loop_check(handoff_root: Path | str | None = None, *, target_date: date | str | None = None, mock_jev_json: Any = None, jev_fn: Any = None, harvested: Mapping[str, Any] | None = None, clustered: Mapping[str, Any] | None = None, proposal: Mapping[str, Any] | None = None) -> dict[str, Any]:
    harvested = harvested if harvested is not None else harvest(handoff_root)
    clustered = clustered if clustered is not None else cluster(harvested, mock_jev_json=mock_jev_json, jev_fn=jev_fn)
    proposal = proposal if proposal is not None else propose_gates(harvested, clustered)
    actionable_count = len(proposal["gate_candidates"])
    cursor = f"{LOOP_ID}-{proposal['signature']}"
    result = {"ok": True, "loop_id": LOOP_ID, "date": str(target_date or datetime.now(timezone.utc).date()), "has_delta": actionable_count > 0, "actionable": proposal["has_actionable"], "delta_count": actionable_count, "severity": "medium" if actionable_count else "low", "watermark": proposal["watermark"], "cursor": cursor, "title": f"[P3 / Priority 3][Taste Evolution] {str(target_date or datetime.now(timezone.utc).date())} UI 品味偏好与反模式候选", "summary": "检测到可供 Owner 审阅的 UI 品味规则或反模式候选。" if actionable_count else "当前 UI 品味证据不足以形成新的 Owner 提案。", "items": [{"what": f"审阅 {_sanitize_human_text(row.get('text'), '候选证据')}", "why": "确认是否进入后续设计门禁"} for row in proposal["gate_candidates"][:8]], "dual_zone_markdown": _issue_markdown(proposal, harvested, target_date=target_date, cursor=cursor) if actionable_count else "", "positive_preferences": proposal["positive_preferences"], "anti_pattern_gates": proposal["anti_pattern_gates"], "proposal": proposal, "harvest": harvested, "clustered": clustered, "raw": {"evidence_count": sum(len(harvested.get(key, [])) for key in ("positive_preferences", "rejections", "visual_tracebacks")), "inventory": harvested.get("inventory", [])}}
    return result


# Public aliases used by other loop collectors and focused tests.
harvest_evidence = harvest
cluster_preferences = cluster
build_gate_proposals = propose_gates
run_loop_check = loop_check


def _read_mock(value: str | None) -> Any:
    if not value:
        return None
    try:
        path = Path(value).expanduser()
        raw = path.read_text(encoding="utf-8") if path.is_file() else value
        return json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid --mock-jev-json") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Loop 5 deterministic UI taste evidence collector")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("harvest", "cluster", "propose-gates", "loop-check"):
        subparser = sub.add_parser(command)
        subparser.add_argument("--handoff-root", type=Path, default=None)
        subparser.add_argument("--mock-jev-json", default=None)
        subparser.add_argument("--date", default=None)
        subparser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        harvested = harvest(args.handoff_root)
        mock = _read_mock(args.mock_jev_json)
        if args.command == "harvest":
            result = harvested
        else:
            clustered = cluster(harvested, mock_jev_json=mock)
            if args.command == "cluster":
                result = clustered
            else:
                proposal = propose_gates(harvested, clustered)
                result = proposal if args.command == "propose-gates" else loop_check(args.handoff_root, target_date=args.date, mock_jev_json=mock, harvested=harvested, clustered=clustered, proposal=proposal)
        rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    except ValueError as exc:
        print(f"taste-evolution: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
