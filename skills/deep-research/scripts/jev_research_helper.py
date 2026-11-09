#!/usr/bin/env python3
"""R09 reranking and R06 citation verification adapters for Jev System One."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import urllib.request
from typing import Any


SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TIERS_FILE = SKILL_DIR / "references" / "source-tiers.json"
TOP_K_TIERS = 3
L1_REF_MAX_LINES = 160


def normalize_quote(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"}))).strip()


def check_citation_step1(verbatim_quote: str | None, source_text: str | None) -> dict[str, Any]:
    if source_text is None:
        return {"status": "source_unavailable", "verdict": "SOURCE_UNAVAILABLE"}
    if not verbatim_quote:
        return {"status": "section_only"}
    found = normalize_quote(verbatim_quote) in normalize_quote(source_text)
    return {"status": "quote_found" if found else "missing",
            "verdict": None if found else "FABRICATED_QUOTE"}


def build_r09_request(research_goal: str, sub_question: str, candidates: list[dict[str, Any]], model: str = "jev-latest") -> dict[str, Any]:
    state = {"research_goal": research_goal, "sub_question": sub_question,
             "candidates": {f"cand_{i}": item for i, item in enumerate(candidates)}}
    questions: dict[str, Any] = {}
    for i in range(len(candidates)):
        key = f"cand_{i}"
        questions[f"{key}_direct"] = {"type": "noul", "instructions": f"Does {key} directly answer the sub-question?",
                                      "criteria": {"true": "The excerpt provides specific evidence answering the question.",
                                                   "false": "The excerpt is merely related or off-topic."}}
        questions[f"{key}_tier"] = {"type": "score", "instructions": f"Rate the evidence tier for {key}.",
                                    "criteria": ["Off-topic.", "Keyword mention.", "Relevant secondary or partial evidence.",
                                                 "Direct primary evidence."]}
    return {"model": model, "state": state, "questions": questions}


def _answer(answers: dict[str, Any], key: str) -> dict[str, Any]:
    value = answers.get(key, {})
    return value if isinstance(value, dict) else {}


def classify_r09(p_direct: float, s_tier: float, p0: float = 0.0, p23: float = 1.0) -> str:
    if p_direct < 0.30 or (s_tier < 1.0 and p0 >= 0.60):
        return "DROP_NOISE"
    if p_direct >= 0.70 and s_tier >= 2.0 and p0 < 0.25:
        return "KEEP_PRIMARY"
    if p_direct >= 0.70:
        return "EXPAND_SNIPPET"
    if 0.35 <= p_direct < 0.70 and s_tier >= 1.8 and p23 >= 0.60:
        return "KEEP_SUPPORTING"
    if 0.30 <= p_direct < 0.70 and 1.0 <= s_tier < 1.8:
        return "LOW_PRIORITY_BUFFER"
    return "DROP_NOISE"


def evaluate_r09_truth_table(answers: dict[str, Any], candidates: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = []
    count = len(candidates) if candidates is not None else len({key.rsplit("_", 1)[0] for key in answers if key.endswith("_direct")})
    for i in range(count):
        direct = _answer(answers, f"cand_{i}_direct")
        tier = _answer(answers, f"cand_{i}_tier")
        p_direct = float(direct.get("noul", direct.get("probability", 0.0)))
        score = float(tier.get("score", 0.0))
        probabilities = tier.get("probabilities", {})
        p0 = float(probabilities.get("0", probabilities.get(0, 0.0)))
        p23 = float(probabilities.get("2", probabilities.get(2, 0.0))) + float(probabilities.get("3", probabilities.get(3, 0.0)))
        rows.append({"index": i, "composite_score": round(0.65 * p_direct + 0.35 * (score / 3.0), 6),
                     "action": classify_r09(p_direct, score, p0, p23), "p_direct": p_direct, "s_tier": score})
    return sorted(rows, key=lambda row: (-row["composite_score"], row["index"]))


def build_r06_request(claim: str, source_section: str, model: str = "jev-latest") -> dict[str, Any]:
    return {"model": model, "state": {"claim": claim, "source_section": source_section},
            "questions": {
                "relation": {"type": "choice", "instructions": "How does source_section relate to claim?",
                             "criteria": {"supports": "It supports the claim.", "contradicts": "It refutes the claim.",
                                          "says_nothing": "It does not address the claim."}},
                "addresses_claim": {"type": "noul", "instructions": "Does the source address the specific subject?", "criteria": {"true": "Yes.", "false": "No."}},
                "quant_and_scope_intact": {"type": "noul", "instructions": "Are numbers, dates, and scope preserved?", "criteria": {"true": "Yes.", "false": "No."}},
            }}


def check_citation(claim: str, verbatim_quote: str | None, source_text: str | None,
                   source_section: str | None = None, endpoint: str | None = None) -> dict[str, Any]:
    step1 = check_citation_step1(verbatim_quote, source_text)
    if step1["status"] in {"missing", "source_unavailable"}:
        return evaluate_r06_truth_table(step1)
    payload = build_r06_request(claim, source_section or source_text or "")
    response = call_systemone(payload, endpoint)
    return evaluate_r06_truth_table(step1, response.get("answers", {}))


def evaluate_r06_truth_table(step1: dict[str, Any], answers: dict[str, Any] | None = None) -> dict[str, Any]:
    status = step1.get("status")
    if status == "missing":
        return {"verdict": "FABRICATED_QUOTE", "confidence_cap": "REFUTED"}
    if status == "source_unavailable":
        return {"verdict": "SOURCE_UNAVAILABLE", "confidence_cap": "MEDIUM"}
    answers = answers or {}
    relation = _answer(answers, "relation")
    rel = relation.get("choice", relation.get("value", "says_nothing"))
    probs = relation.get("probabilities", {})
    p_support = float(probs.get("supports", 0.0)); p_contradict = float(probs.get("contradicts", 0.0))
    addressed = float(_answer(answers, "addresses_claim").get("noul", 0.0))
    scope = float(_answer(answers, "quant_and_scope_intact").get("noul", 0.0))
    if rel == "contradicts" and p_contradict >= 0.70 and addressed >= 0.50:
        return {"verdict": "CONTRADICTED", "confidence_cap": "REFUTED"}
    if rel == "supports" and p_support >= 0.75 and addressed >= 0.70 and scope >= 0.70:
        return {"verdict": "VERIFIED", "confidence_cap": "HIGH"}
    if rel == "supports" and p_support >= 0.60 and addressed >= 0.70 and scope < 0.35:
        return {"verdict": "SCOPE_OR_NUMBER_DISTORTED", "confidence_cap": "LOW"}
    if rel == "says_nothing" and addressed < 0.35:
        return {"verdict": "UNSUPPORTED", "confidence_cap": "LOW"}
    return {"verdict": "INSUFFICIENT_EVIDENCE_ESCALATE", "confidence_cap": "CONTESTED"}


def call_systemone(payload: dict[str, Any], endpoint: str | None = None, source_key: str | None = None) -> dict[str, Any]:
    url = endpoint or os.environ.get("FALCON_BASE_URL", "http://127.0.0.1:19823")
    if not url.rstrip("/").endswith("/v1/systemone"):
        url = url.rstrip("/") + "/v1/systemone"
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {source_key}"} if source_key else {})})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def load_source_tiers(tiers_file: Path = DEFAULT_TIERS_FILE) -> dict[str, Any]:
    if not tiers_file.is_file():
        return {"schema": "deep-research-source-tiers/v1", "version": 1, "topic_routes": {}, "high_signal_tiers": []}
    return json.loads(tiers_file.read_text(encoding="utf-8"))


def route_research_topic(topic_type: str = "general", depth: str = "standard", tiers_file: Path = DEFAULT_TIERS_FILE) -> dict[str, Any]:
    doc = load_source_tiers(tiers_file)
    routes = doc.get("topic_routes", {})
    route_cfg = routes.get(topic_type) or routes.get("general", {})
    tiers = [
        item
        for item in doc.get("high_signal_tiers", [])
        if isinstance(item, dict) and topic_type in item.get("topics", ["general"])
    ][:TOP_K_TIERS]
    active_channels = list(route_cfg.get("active_channels", ["web", "github"]))
    if depth == "quick":
        active_channels = [ch for ch in active_channels if ch in {"web", "github"}] or ["web", "github"]
    return {
        "ok": True,
        "topic_type": topic_type,
        "depth": depth,
        "active_channels": active_channels,
        "skipped_channels": route_cfg.get("skipped_channels", []),
        "primary_playbook_sections": route_cfg.get("primary_playbook_sections", []),
        "matched_source_tiers": tiers,
        "skip_phase2_adversarial": depth == "quick",
    }


def build_flow_map(tiers_file: Path = DEFAULT_TIERS_FILE) -> dict[str, Any]:
    doc = load_source_tiers(tiers_file)
    ref_dir = SKILL_DIR / "references"
    ref_line_counts = {
        path.relative_to(SKILL_DIR).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(ref_dir.glob("*.md"))
    }
    all_within_budget = all(lines <= L1_REF_MAX_LINES for lines in ref_line_counts.values())
    return {
        "ok": all_within_budget,
        "skill": "deep-research",
        "architecture_ref": "skills/deep-research/SKILL.md",
        "entry_a_ingest": {
            "A1_source_tiers_drawer": "skills/deep-research/references/source-tiers.json",
        },
        "shared_tiers": {
            "tier_1_json_drawer": {
                "file": "skills/deep-research/references/source-tiers.json",
                "topic_types": sorted(doc.get("topic_routes", {}).keys()),
                "tier_rule_ids": [t.get("id") for t in doc.get("high_signal_tiers", []) if isinstance(t, dict)],
            },
            "tier_2_l1_docs_line_counts": ref_line_counts,
            "tier_3_executable_gates": "skills/deep-research/scripts/jev_research_helper.py (R09 rerank + R06 check-citations)",
        },
        "entry_b_consume": {
            "B1_route_channels": "python3 skills/deep-research/scripts/jev_research_helper.py route --topic-type <tooling|scientific|market|general>",
            "B2_rerank_r09": "python3 skills/deep-research/scripts/jev_research_helper.py rerank --input <candidates.json>",
            "B3_verify_citations_r06": "python3 skills/deep-research/scripts/jev_research_helper.py check-citations --input <claim.json>",
        },
        "anti_explosion_valves": {
            "valve_1_zero_token_quote_and_score_gates": True,
            "valve_2_topic_channel_slicing": sorted(doc.get("topic_routes", {}).keys()),
            "valve_3_top_k_and_line_caps": {
                "source_tiers_top_k": TOP_K_TIERS,
                "l1_doc_max_lines": L1_REF_MAX_LINES,
            },
            "valve_4_one_in_one_out_compaction": True,
            "all_l1_docs_within_budget": all_within_budget,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["rerank", "check-citations", "flow-map", "route"])
    parser.add_argument("--endpoint")
    parser.add_argument("--input", default="-")
    parser.add_argument("--topic-type", default="general", choices=["tooling", "scientific", "market", "general"])
    parser.add_argument("--depth", default="standard", choices=["quick", "standard", "deep"])
    parser.add_argument("--tiers-file", default=str(DEFAULT_TIERS_FILE))
    args = parser.parse_args(argv)
    tiers_path = Path(args.tiers_file).resolve()
    if args.command == "flow-map":
        payload = build_flow_map(tiers_path)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload.get("ok") else 1
    if args.command == "route":
        print(json.dumps(route_research_topic(args.topic_type, args.depth, tiers_path), indent=2, ensure_ascii=False))
        return 0
    if hasattr(args.input, "read"):
        data = json.load(args.input)
    elif args.input == "-":
        data = json.load(sys.stdin)
    else:
        with Path(args.input).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    if args.command == "rerank":
        payload = build_r09_request(data.get("research_goal", ""), data.get("sub_question", ""), data["candidates"])
        response = call_systemone(payload, args.endpoint, os.environ.get("FALCON_SOURCE_KEY"))
        print(json.dumps(evaluate_r09_truth_table(response.get("answers", {}), data["candidates"])))
    else:
        step1 = check_citation_step1(data.get("verbatim_quote"), data.get("source_text"))
        result = evaluate_r06_truth_table(step1, data.get("answers"))
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
