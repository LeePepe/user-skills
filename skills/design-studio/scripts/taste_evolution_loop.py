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
SELECTION_NAMES = re.compile(r"^preview-selection(?:-r\d+(?:-archived)?)?\.json$")


def _default_handoff_root() -> Path:
    return Path(__import__("os").environ.get(HANDOFF_ENV, "~/Development/handoff")).expanduser()


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
    result: set[tuple[str, str]] = set()
    for key in ("rejectedVariantIDs", "rejectedVariants", "rejections"):
        raw = payload.get(key)
        if isinstance(raw, Mapping):
            for vid, reason in raw.items():
                if _clean(vid) and (not inventory or _clean(vid) in inventory):
                    result.add((_clean(vid), _clean(reason) or "explicit rejection"))
        else:
            for item in _as_ids(raw):
                if not inventory or item in inventory:
                    result.add((item, "explicit rejection"))
    by_variant = payload.get("rejectionsByVariant")
    if isinstance(by_variant, Mapping):
        for vid, reason in by_variant.items():
            if _clean(vid) and (not inventory or _clean(vid) in inventory):
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
            if vid and (item.get("rejected") is True or status in {"rejected", "reject", "declined", "not_selected"}):
                if not inventory or vid in inventory:
                    result.add((vid, _clean(item.get("reason") or item.get("note")) or "explicit rejection"))
    return sorted(result)


def _add_evidence(store: dict[tuple[str, str, str], dict[str, Any]], *, kind: str, variant_id: str, text: str, source: str, line: int | None = None, classification: str | None = None) -> None:
    variant_id, text = _clean(variant_id), _clean(text)
    key = (kind, variant_id, _norm(text) or kind)
    row = store.get(key)
    if row is None:
        row = {"kind": kind, "variant_id": variant_id, "text": text or kind, "count": 0, "sources": [], "classifications": []}
        store[key] = row
    row["count"] += 1
    provenance = {"path": source}
    if line is not None:
        provenance["line"] = line
    if provenance not in row["sources"]:
        row["sources"].append(provenance)
    if classification and classification not in row["classifications"]:
        row["classifications"].append(classification)


def _selection_files(root: Path) -> list[Path]:
    files = [p for p in root.rglob("*.json") if p.is_file() and SELECTION_NAMES.fullmatch(p.name)] if root.is_dir() else []
    return sorted(files, key=lambda p: _relative(p, root))


def _review_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    result = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".md", ".markdown", ".txt"}:
            continue
        name = path.name.casefold()
        if "review" in name or "visual" in name or name == "design.md":
            result.append(path)
    return sorted(result, key=lambda p: _relative(p, root))


def harvest(handoff_root: Path | str | None = None) -> dict[str, Any]:
    """Harvest selection and traceback evidence without inventing inventory or rejections."""
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
        if not isinstance(payload, Mapping):
            malformed.append(source)
            continue
        selection_count += 1
        inventory.update(_inventory(payload))
        selected = _clean(payload.get("selectedVariantID") or payload.get("selectedVariantId"))
        stars = _as_ids(payload.get("starredVariantIDs") or payload.get("starredVariantIds"))
        if selected:
            _add_evidence(positive, kind="selected", variant_id=selected, text=f"Owner selected {selected}", source=source)
        for variant in stars:
            _add_evidence(positive, kind="starred", variant_id=variant, text=f"Owner starred {variant}", source=source)
        for variant, note in _notes(payload.get("notesByVariant")):
            _add_evidence(positive, kind="note", variant_id=variant, text=note, source=source)
        for variant, reason in _rejected(payload, inventory):
            _add_evidence(rejections, kind="rejection", variant_id=variant, text=reason, source=source)
    for path in _review_files(root):
        source = _relative(path, root)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            malformed.append(source)
            continue
        for number, line in enumerate(lines, 1):
            text = _clean(line)
            match = TRACEBACK_RE.search(text)
            if match:
                _add_evidence(tracebacks, kind="visual_traceback", variant_id="", text=text, source=source, line=number, classification=match.group(1))
            elif REJECTION_WORDS.search(text):
                _add_evidence(rejections, kind="review_rejection", variant_id="", text=text, source=source, line=number)
    def ordered(values: dict[tuple[str, str, str], dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(values.values(), key=lambda row: (row["kind"], row["variant_id"], _norm(row["text"])))
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


def _mock_value(mock: Any, candidate_id: str, index: int) -> Any:
    if mock is None:
        return None
    if isinstance(mock, (str, Path)):
        try:
            candidate_path = Path(str(mock)).expanduser()
            raw = candidate_path.read_text(encoding="utf-8") if candidate_path.is_file() else str(mock)
            mock = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"__invalid_mock__": True}
    if isinstance(mock, list):
        return mock[index] if index < len(mock) else {"__invalid_mock__": True}
    if isinstance(mock, Mapping):
        if candidate_id in mock:
            return mock[candidate_id]
        if "default" in mock:
            return mock["default"]
        if "answers" in mock:
            return mock
    return {"__invalid_mock__": True}


def _number(value: Any, key: str) -> float | None:
    if isinstance(value, Mapping):
        value = value.get(key, value.get("value"))
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 1 else None


def _fallback_choice(text: str, *, anti: bool, count: int) -> str:
    if anti or ANTI_WORDS.search(text):
        return "PROMOTE_ANTI_PATTERN_GATE" if count >= 1 else "ONE_OFF_CONTEXT_NOTE"
    if count >= 2 or POSITIVE_WORDS.search(text):
        return "PROMOTE_POSITIVE_ARCHETYPE"
    return "ONE_OFF_CONTEXT_NOTE"


def _decide(candidate: Mapping[str, Any], *, index: int, mock_jev_json: Any = None, jev_fn: Any = None) -> dict[str, Any]:
    text = _clean(candidate.get("text"))
    anti = candidate.get("kind") in {"rejection", "review_rejection", "visual_traceback"}
    fallback = _fallback_choice(text, anti=anti, count=int(candidate.get("count") or 0))
    supplied = mock_jev_json is not None
    mock = _mock_value(mock_jev_json, str(candidate.get("candidate_id") or ""), index) if supplied else None
    if supplied and isinstance(mock, Mapping) and mock.get("__invalid_mock__"):
        return {"choice": fallback, "noul": None, "score": None, "source": "deterministic_fallback_invalid_mock"}
    questions = {
        "promotion": {"type": "choice", "instructions": "Classify this taste evidence.", "criteria": {choice: choice for choice in CHOICES}},
        "confidence": {"type": "noul", "instructions": "How confidently does this evidence support the classification?"},
        "score": {"type": "score", "instructions": "Score recurrence and usefulness from 0.0 to 1.0."},
    }
    decide = jev_fn or _load_jev().jev_decide
    response = decide(
        state={"candidate": {"id": candidate.get("candidate_id"), "kind": candidate.get("kind"), "text": text, "count": candidate.get("count")}},
        questions=questions,
        agent_role="design-studio",
        goal_id=LOOP_ID,
        timeout_sec=1.5,
        falcon_only=True,
        mock_response=mock if supplied else None,
    )
    if not isinstance(response, Mapping):
        return {"choice": fallback, "noul": None, "score": None, "source": "deterministic_fallback_unavailable"}
    answers = response.get("answers", response)
    if not isinstance(answers, Mapping):
        return {"choice": fallback, "noul": None, "score": None, "source": "deterministic_fallback_malformed"}
    raw_choice = answers.get("promotion") or answers.get("classification") or answers.get("choice")
    if isinstance(raw_choice, Mapping):
        raw_choice = raw_choice.get("choice")
    choice = str(raw_choice) if raw_choice in CHOICES else None
    confidence = _number(answers.get("confidence") or answers.get("noul"), "noul")
    score = _number(answers.get("score"), "score")
    if choice is None or confidence is None or score is None:
        source = "deterministic_fallback_invalid_mock" if supplied else "deterministic_fallback_incomplete"
        return {"choice": fallback, "noul": None, "score": None, "source": source}
    return {"choice": choice, "noul": confidence, "score": score, "source": "jev"}


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


def _issue_markdown(proposal: Mapping[str, Any], harvested: Mapping[str, Any], *, target_date: date | str | None = None) -> str:
    issue = _load_issue_brief()
    items = []
    for row in list(proposal.get("positive_preferences") or [])[:4]:
        items.append({"what": f"保留偏好：{row['text']}", "why": "多轮选择或评分支持沉淀为可复用审美规则"})
    for row in list(proposal.get("anti_pattern_gates") or [])[:4]:
        items.append({"what": f"加入反模式门禁：{row['text']}", "why": "视觉回溯或明确拒绝记录显示需要拦截"})
    if not items:
        items = [{"what": "暂不新增审美门禁", "why": "当前证据不足以形成稳定偏好或反模式"}]
    date_text = str(target_date or datetime.now(timezone.utc).date())
    markdown = issue.render_dual_zone_issue(
        tldr=f"品味回溯汇总了 {len(harvested.get('positive_preferences', []))} 条正向选择与 {len(harvested.get('visual_tracebacks', []))} 条视觉回溯证据，请 Owner 决定是否沉淀。",
        items=items,
        cursor=f"{LOOP_ID}-{date_text}-{proposal.get('signature')}",
        ai_payload={"loop_id": LOOP_ID, "date": date_text, "watermark": proposal.get("watermark"), "proposal": proposal, "harvest": harvested},
    )
    check = issue.verify_dual_zone_issue(markdown)
    if not check.get("ok"):
        raise ValueError(f"Dual-Zone verification failed: {check.get('violations')}")
    return markdown


def loop_check(handoff_root: Path | str | None = None, *, target_date: date | str | None = None, mock_jev_json: Any = None, jev_fn: Any = None) -> dict[str, Any]:
    harvested = harvest(handoff_root)
    clustered = cluster(harvested, mock_jev_json=mock_jev_json, jev_fn=jev_fn)
    proposal = propose_gates(harvested, clustered)
    actionable_count = len(proposal["gate_candidates"])
    result = {"ok": True, "loop_id": LOOP_ID, "date": str(target_date or datetime.now(timezone.utc).date()), "has_delta": actionable_count > 0, "actionable": proposal["has_actionable"], "delta_count": actionable_count, "severity": "medium" if actionable_count else "low", "watermark": proposal["watermark"], "cursor": f"{LOOP_ID}-{proposal['signature']}", "title": f"[P3 / Priority 3][Taste Evolution] {str(target_date or datetime.now(timezone.utc).date())} UI 品味偏好与反模式候选", "summary": "检测到可供 Owner 审阅的 UI 品味规则或反模式候选。" if actionable_count else "当前 UI 品味证据不足以形成新的 Owner 提案。", "items": [{"what": f"审阅 {row['text']}", "why": "确认是否进入后续设计门禁"} for row in proposal["gate_candidates"][:8]], "dual_zone_markdown": _issue_markdown(proposal, harvested, target_date=target_date) if actionable_count else "", "positive_preferences": proposal["positive_preferences"], "anti_pattern_gates": proposal["anti_pattern_gates"], "proposal": proposal, "harvest": harvested, "clustered": clustered, "raw": {"evidence_count": sum(len(harvested.get(key, [])) for key in ("positive_preferences", "rejections", "visual_tracebacks")), "inventory": harvested.get("inventory", [])}}
    return result


# Public aliases used by other loop collectors and focused tests.
harvest_evidence = harvest
cluster_preferences = cluster
build_gate_proposals = propose_gates
run_loop_check = loop_check


def _read_mock(value: str | None) -> Any:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


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
    harvested = harvest(args.handoff_root)
    if args.command == "harvest":
        result = harvested
    else:
        clustered = cluster(harvested, mock_jev_json=_read_mock(args.mock_jev_json))
        if args.command == "cluster":
            result = clustered
        else:
            proposal = propose_gates(harvested, clustered)
            result = proposal if args.command == "propose-gates" else loop_check(args.handoff_root, target_date=args.date, mock_jev_json=_read_mock(args.mock_jev_json))
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
