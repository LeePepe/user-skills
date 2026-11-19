#!/usr/bin/env python3
"""Workflow Skill/Agent architecture discovery, R09 reranking, and daily digest loop."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REPO_ROOT = SKILL_DIR.parents[1]
ISSUE_BRIEF_PATH = REPO_ROOT / "skills" / "issue-brief" / "scripts" / "verify_issue_format.py"
DIGEST_VAULT_PATH = SCRIPT_DIR / "digest_vault.py"
JEV_CLIENT_PATH = REPO_ROOT / "skills" / "team-lead" / "scripts" / "jev_client.py"
JEV_HELPER_PATH = REPO_ROOT / "skills" / "deep-research" / "scripts" / "jev_research_helper.py"
CST = timezone(timedelta(hours=8))
DEFAULT_VAULT_DIR = Path.home() / "Development" / "Personal" / "knowledge-vault"
DEFAULT_COLLECTED_TOOLS_DIR = Path.home() / "Development" / "Personal" / "collected-tools"
DEFAULT_RADAR_PATH = Path.home() / "Development" / "AIDash" / "aidata" / "L1_collect" / "raw" / "github_repo"
_DIGEST_VAULT_MODULE: Any | None = None
WORKFLOW_TERMS = re.compile(
    r"(?:swarm|harness|gate|context[-_ ]engineering|multi[-_ ]agent|agent[-_ ]skill|skill[-_ ]contract|orchestration|workflow|autopilot|deterministic|eval|stop[-_ ]hook)",
    re.IGNORECASE,
)
DIMENSION_TERMS = {
    "SWARM_ORCHESTRATION": re.compile(r"swarm|multi[-_ ]agent|orchestrat|crew|parallel", re.I),
    "HARNESS_RUNTIME": re.compile(r"harness|runtime|agent[-_ ]loop|tool[-_ ]use|sandbox|executor", re.I),
    "DETERMINISTIC_GATE": re.compile(r"gate|eval|contract|lint|verif|guard|policy|stop[-_ ]hook", re.I),
    "CONTEXT_ENGINEERING": re.compile(r"context|prompt|retriev|memory|skill|progressive[-_ ]disclosure", re.I),
}
CURATED_PUBLIC_SOURCES = (
    ("openai-swarm", "OpenAI Swarm", "https://github.com/openai/swarm", "SWARM_ORCHESTRATION"),
    ("anthropic-claude-code", "Anthropic Claude Code", "https://github.com/anthropics/claude-code", "HARNESS_RUNTIME"),
    ("vercel-agent-skills", "Vercel Agent Skills", "https://github.com/vercel-labs/skills", "CONTEXT_ENGINEERING"),
    ("openai-model-spec", "OpenAI Model Spec", "https://github.com/openai/model_spec", "DETERMINISTIC_GATE"),
)


def _load_module(name: str, path: Path) -> Any | None:
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _norm_uri(value: str) -> str:
    """Use digest_vault's canonical GitHub/source URI normalizer."""
    global _DIGEST_VAULT_MODULE
    raw = str(value or "").strip()
    if _DIGEST_VAULT_MODULE is None:
        _DIGEST_VAULT_MODULE = _load_module("skill_agent_research_digest_vault", DIGEST_VAULT_PATH)
    if _DIGEST_VAULT_MODULE is not None and hasattr(_DIGEST_VAULT_MODULE, "_normalize_source_uri"):
        return str(_DIGEST_VAULT_MODULE._normalize_source_uri(raw))
    if raw.startswith("git@github.com:"):
        raw = "https://github.com/" + raw.split(":", 1)[1]
    try:
        parts = urlsplit(raw)
        if parts.scheme and parts.netloc:
            path = parts.path.rstrip("/")
            if parts.hostname and parts.hostname.casefold() == "github.com":
                path = path.removesuffix(".git").casefold()
                return f"https://github.com{path}"
            return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))
    except ValueError:
        pass
    return raw.rstrip("/").lower()


def _identity(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    result: dict[str, Any] = {}
    for line in text[3:end].splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip().strip("\"'")
        result[key.strip()] = value
    return result


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _ledger_covered(ledger_path: Path) -> tuple[set[str], set[str], set[str], set[str], set[str], set[str], str | None]:
    if not ledger_path.is_file():
        return set(), set(), set(), set(), set(), set(), f"missing topic ledger: {ledger_path}"
    doc = _read_json(ledger_path)
    if not isinstance(doc, dict) or not isinstance(doc.get("topics"), list):
        return set(), set(), set(), set(), set(), set(), f"invalid topic ledger: {ledger_path}"
    keys: set[str] = set()
    aliases: set[str] = set()
    source_ids: set[str] = set()
    uris: set[str] = set()
    fingerprints: set[str] = set()
    questions: set[str] = set()
    for item in doc["topics"]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("topic_key") or "").strip().lower()
        if key:
            keys.add(key)
        for alias in _strings(item.get("aliases")):
            aliases.add(alias.strip().lower())
        for source_id in _strings(item.get("source_id")):
            source_ids.add(source_id.strip().lower())
        for raw_uri in [*_strings(item.get("source_uri")), *_strings(item.get("source_uris"))]:
            uri = _norm_uri(raw_uri)
            if uri:
                uris.add(uri)
        for fingerprint in _strings(item.get("source_fingerprint")):
            value = fingerprint.strip().lower()
            if value:
                fingerprints.add(value)
                fingerprints.add(value[:12])
        question = _identity(str(item.get("question") or ""))
        if question:
            questions.add(question)
    return keys, aliases, source_ids, uris, fingerprints, questions, None


def _candidate(*, key: str, title: str, uri: str, source_ref: str, excerpt: str, dimension: str = "") -> dict[str, Any]:
    text = " ".join((key, title, uri, excerpt))
    dim = dimension or next((name for name, pattern in DIMENSION_TERMS.items() if pattern.search(text)), "CONTEXT_ENGINEERING")
    digest = hashlib.sha256(_norm_uri(uri).encode("utf-8")).hexdigest()[:12]
    source_id = key
    normalized_uri = _norm_uri(uri)
    if normalized_uri.startswith("https://github.com/"):
        source_id = normalized_uri.removeprefix("https://github.com/")
    return {
        "topic_key": key,
        "slug": key,
        "source_id": source_id,
        "title": title[:180],
        "question": f"如何把 {title[:100]} 转化为可验证的工作流能力？",
        "source_uri": normalized_uri,
        "source_ref": source_ref,
        "source_fingerprint": digest,
        "excerpt": excerpt[:500],
        "file_patterns": [source_ref] if source_ref else [],
        "track": "workflow",
        "architecture_dimension": dim,
        "heuristic_direct": 0.78 if uri.startswith(("https://github.com/", "http://", "https://")) else 0.68,
        "heuristic_tier": 2.4 if uri.startswith("https://github.com/") else 1.8,
    }


def _iter_source_files(collected_dir: Path) -> list[tuple[Path, str, str]]:
    if not collected_dir.is_dir():
        return []
    results: list[tuple[Path, str, str]] = []
    for path in sorted(collected_dir.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        results.append((path, path.stem, path.read_text(encoding="utf-8", errors="replace")))
    for repo in sorted((collected_dir / "repos").glob("*")):
        readme = next((p for p in (repo / "README.md", repo / "readme.md") if p.is_file()), None)
        if readme:
            results.append((readme, repo.name, readme.read_text(encoding="utf-8", errors="replace")))
    return results


def _extract_uri(path: Path, text: str) -> str:
    meta = _frontmatter(text)
    uri = str(meta.get("source_uri") or "")
    if not uri:
        config = path.parent / ".git" / "config"
        if config.is_file():
            match = re.search(r"(?m)^\s*url\s*=\s*(\S+)", config.read_text(encoding="utf-8", errors="replace"))
            uri = match.group(1) if match else ""
    if not uri:
        match = re.search(r"https?://[^\s<>\])]+|git@github\.com:[^\s<>\])]+", text)
        uri = match.group(0) if match else ""
    return _norm_uri(uri)


def _public_readme_url(uri: str) -> str:
    normalized = _norm_uri(uri)
    parsed = urlsplit(normalized)
    if parsed.hostname == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            return f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/HEAD/README.md"
    return normalized


def _fetch_public_evidence(uri: str) -> tuple[str | None, str | None]:
    url = _public_readme_url(uri)
    try:
        request = Request(url, headers={"User-Agent": "knowledge-digest-workflow/1", "Accept": "text/plain"})
        with urlopen(request, timeout=3) as response:
            body = response.read(12000).decode("utf-8", errors="replace")
    except (OSError, ValueError, UnicodeError) as exc:
        return None, f"public source unavailable {url}: {type(exc).__name__}"
    cleaned = body.strip()
    if not cleaned or re.match(r"(?is)^(?:<!doctype\s+html|<html\b|<head\b)", cleaned):
        return None, f"public source returned non-README content: {url}"
    if len(re.sub(r"\s+", "", cleaned)) < 80:
        return None, f"public source README too short to ground claims: {url}"
    return cleaned, None


def _radar_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        nested = data.get("fact_repo_snapshot")
        if nested is not None:
            return _radar_rows(nested)
        for key in ("repos", "items", "rows", "records"):
            if key in data:
                return _radar_rows(data[key])
        return [data]
    if isinstance(data, list):
        return [row for item in data for row in _radar_rows(item)]
    return []


def _walk_radar(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    files = [path] if path.is_file() else sorted([*path.rglob("*.json"), *path.rglob("*.jsonl")])
    for file in files[:500]:
        try:
            if file.suffix == ".jsonl":
                parsed = []
                for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(value, (dict, list)):
                        parsed.append(value)
            else:
                parsed = [_read_json(file)]
        except (OSError, UnicodeError):
            continue
        for data in parsed:
            for row in _radar_rows(data):
                rows.append({**row, "_source_file": str(file)})
    return rows


def discover(
    *,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    topic_ledger_path: Path | None = None,
    collected_tools_dir: Path = DEFAULT_COLLECTED_TOOLS_DIR,
    radar_path: Path | None = DEFAULT_RADAR_PATH,
    fetch_public: bool = False,
) -> dict[str, Any]:
    ledger_path = topic_ledger_path or vault_dir / "topic-ledger.json"
    keys, aliases, source_ids, uris, fingerprints, questions, ledger_error = _ledger_covered(ledger_path)
    diagnostics: list[str] = []
    source_status: dict[str, str] = {}
    if ledger_error:
        return {"ok": False, "status": "health_error", "error": ledger_error, "candidates": [], "diagnostics": [ledger_error]}
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(row: dict[str, Any]) -> None:
        key = str(row.get("topic_key") or row.get("slug") or "").strip().lower()
        source_id = str(row.get("source_id") or key).strip().lower()
        uri = _norm_uri(str(row.get("source_uri") or ""))
        question = _identity(str(row.get("question") or row.get("title") or ""))
        fingerprint = str(row.get("source_fingerprint") or "").strip().lower()
        fingerprint_prefix = fingerprint[:12]
        if not key or not WORKFLOW_TERMS.search(" ".join(str(row.get(x) or "") for x in ("topic_key", "source_id", "title", "excerpt", "source_uri"))):
            return
        if key in keys or key in aliases or source_id in source_ids or uri in uris or fingerprint in fingerprints or fingerprint_prefix in fingerprints or question in questions:
            return
        identity = (key, uri or question)
        if identity in seen:
            return
        seen.add(identity)
        row["track"] = "workflow"
        candidates.append(row)

    for path, key, text in _iter_source_files(collected_tools_dir):
        uri = _extract_uri(path, text)
        add(_candidate(key=key.lower(), title=key.replace("-", " ").title(), uri=uri or f"local://{key}", source_ref=str(path), excerpt=text[:500]))

    radar_target = radar_path or DEFAULT_RADAR_PATH
    if not radar_target.exists():
        source_status["radar"] = "missing"
        diagnostics.append(f"radar source missing: {radar_target} (expected fact_repo_snapshot/jsonl input)")
    else:
        radar_rows = _walk_radar(radar_target)
        source_status["radar"] = "ok" if radar_rows else "empty_or_unreadable"
        if not radar_rows:
            diagnostics.append(f"radar source has no readable fact_repo_snapshot rows: {radar_target}")
        for row in radar_rows:
            uri = str(row.get("html_url") or row.get("url") or row.get("source_uri") or row.get("repo_url") or "")
            key = str(row.get("topic_key") or row.get("repo") or row.get("full_name") or row.get("name") or "").strip().lower().replace("/", "-")
            title = str(row.get("title") or row.get("name") or row.get("repo") or key)
            excerpt = str(row.get("description") or row.get("summary") or row.get("excerpt") or "")
            add(_candidate(key=key, title=title, uri=uri, source_ref=str(row.get("_source_file") or radar_target), excerpt=excerpt))

    if fetch_public:
        source_status["curated_public"] = "partial"
        for key, title, uri, dim in CURATED_PUBLIC_SOURCES:
            body, error = _fetch_public_evidence(uri)
            if error:
                diagnostics.append(error)
                continue
            source_status["curated_public"] = "ok"
            add(_candidate(key=key, title=title, uri=uri, source_ref=f"public:{key}:README.md", excerpt=body or "", dimension=dim))
    else:
        source_status["curated_public"] = "disabled"
        diagnostics.append("public fetch disabled; scheduled collector enables bounded curated README probes")

    candidates.sort(key=lambda item: (-float(item.get("heuristic_direct", 0)), str(item.get("topic_key"))))
    for index, row in enumerate(candidates, 1):
        row["rank"] = index
    return {
        "ok": True,
        "status": "candidates_found" if candidates else "no_candidates",
        "track": "workflow",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "diagnostics": diagnostics,
        "source_status": source_status,
        "ledger_path": str(ledger_path),
    }


def _mock_value(value: Any) -> Any:
    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            path = Path(value).expanduser()
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
            return json.loads(value)
        except (OSError, ValueError, TypeError):
            return None
    return None


def _finite_probability(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _finite_tier(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 3.0 else None


def rerank_r09(candidates: list[dict[str, Any]], *, mock_jev_json: Any = None) -> dict[str, Any]:
    if not candidates:
        return {"ok": True, "status": "no_candidates", "ranked": [], "diagnostics": []}
    helper = _load_module("skill_agent_research_jev_helper", JEV_HELPER_PATH)
    client = _load_module("skill_agent_research_jev_client", JEV_CLIENT_PATH)
    diagnostics: list[str] = []
    answers: dict[str, Any] | None = None
    mock_supplied = mock_jev_json is not None
    mock = _mock_value(mock_jev_json)
    questions: dict[str, Any] = {}
    for index, row in enumerate(candidates):
        questions[f"cand_{index}_direct"] = {"type": "noul", "instructions": f"Does cand_{index} directly support workflow adoption?"}
        questions[f"cand_{index}_tier"] = {"type": "score", "instructions": f"Rate evidence tier for cand_{index}.", "criteria": ["off-topic", "keyword", "relevant", "primary"]}
        questions[f"cand_{index}_dimension"] = {
            "type": "choice",
            "instructions": "Classify architecture dimension using one supported option.",
            "options": list(DIMENSION_TERMS),
            "criteria": {option: f"Evidence supports {option}." for option in DIMENSION_TERMS},
        }
        questions[f"cand_{index}_dimension_noul"] = {
            "type": "noul",
            "instructions": "Is the selected architecture dimension grounded by the source excerpt?",
            "criteria": {"true": "The excerpt directly supports the selected dimension.", "false": "The dimension is only a guess."},
        }
    state = {"track": "workflow", "candidates": {f"cand_{i}": c for i, c in enumerate(candidates)}}
    if client is not None and (not mock_supplied or mock is not None):
        decision = client.jev_decide(state, questions, agent_role="knowledge-digest", mock_response=mock)
        if isinstance(decision, dict):
            answers = decision.get("answers", decision)
    if not isinstance(answers, dict):
        diagnostics.append("Jev unavailable, malformed, or grey; deterministic R09 fallback used")
        answers = {}
    validation_fallback = False
    for index, candidate in enumerate(candidates):
        direct = answers.get(f"cand_{index}_direct")
        tier = answers.get(f"cand_{index}_tier")
        p_direct = _finite_probability(direct.get("noul", direct.get("probability"))) if isinstance(direct, dict) else None
        s_tier = _finite_tier(tier.get("score")) if isinstance(tier, dict) else None
        if p_direct is None:
            p_direct = _finite_probability(candidate.get("heuristic_direct")) or 0.6
            validation_fallback = True
        if s_tier is None:
            s_tier = _finite_tier(candidate.get("heuristic_tier")) or 1.5
            validation_fallback = True
        answers[f"cand_{index}_direct"] = {"noul": p_direct}
        answers[f"cand_{index}_tier"] = {"score": s_tier, "probabilities": {"0": 0.0, "2": 0.5, "3": 0.5}}
    if validation_fallback:
        diagnostics.append("Invalid, incomplete, or non-finite Jev score answer; deterministic candidate fallback used")
    if helper is not None and hasattr(helper, "evaluate_r09_truth_table"):
        score_rows = helper.evaluate_r09_truth_table(answers, candidates)
    else:
        score_rows = []
        for index, candidate in enumerate(candidates):
            p_direct = answers[f"cand_{index}_direct"]["noul"]
            s_tier = answers[f"cand_{index}_tier"]["score"]
            score_rows.append({"index": index, "p_direct": p_direct, "s_tier": s_tier, "composite_score": round(0.65 * p_direct + 0.35 * (s_tier / 3.0), 6), "action": "KEEP_PRIMARY" if p_direct >= 0.7 and s_tier >= 2.0 else "KEEP_SUPPORTING"})
        score_rows.sort(key=lambda row: (-row["composite_score"], row["index"]))
    rows: list[dict[str, Any]] = []
    for score_row in score_rows:
        index = int(score_row["index"])
        candidate = candidates[index]
        dimension = answers.get(f"cand_{index}_dimension", {})
        choice = dimension.get("choice") if isinstance(dimension, dict) else None
        dimension_noul = _finite_probability(answers.get(f"cand_{index}_dimension_noul", {}).get("noul")) if isinstance(answers.get(f"cand_{index}_dimension_noul"), dict) else None
        if choice not in DIMENSION_TERMS or dimension_noul is None or dimension_noul < 0.5:
            arch = str(candidate.get("architecture_dimension") or "CONTEXT_ENGINEERING")
            dimension_confidence = dimension_noul if dimension_noul is not None else 0.0
        else:
            arch = choice
            dimension_confidence = dimension_noul
        row = {**candidate, "r09": {"p_direct": score_row["p_direct"], "s_tier": score_row["s_tier"], "composite_score": score_row["composite_score"], "action": score_row["action"]}, "architecture_dimension": arch, "architecture_dimension_noul": dimension_confidence}
        if row["r09"]["action"] != "DROP_NOISE" and row["r09"]["composite_score"] >= 0.65:
            row["breakdown_draft"] = _breakdown_draft(row)
        rows.append(row)
    return {"ok": True, "status": "ranked", "ranked": rows, "diagnostics": diagnostics, "formula": "0.65 * p_direct + 0.35 * (s_tier / 3.0)"}


def _breakdown_draft(row: Mapping[str, Any]) -> dict[str, Any]:
    excerpt = re.sub(r"\s+", " ", str(row.get("excerpt") or "").strip())
    grounded = excerpt[:240] if excerpt else "来源摘录不可用；不得把此候选当作已研究结论。"
    dimension = str(row.get("architecture_dimension") or "CONTEXT_ENGINEERING")
    return {
        "tldr": f"候选来源摘录（待核验）：{grounded}",
        "pain_point_faq": [
            {"question": "它声称解决什么工作流痛点？", "answer": f"仅依据来源摘录待核验：{grounded}"},
            {"question": "落地前最需要确认什么？", "answer": "确认真实仓库文档、适用边界与安全门禁；当前不是已验证结论。"},
        ],
        "actionable_checklist": [
            "[未验证提案] 先阅读来源 README 与许可证，再决定是否试用。",
            "[未验证提案] 在隔离样例中记录输入、输出与失败路径。",
            "[未验证提案] Owner 批准后再接入现有 Skill/Gate 流程。",
        ],
        "architecture_takeaway": f"候选架构维度：{dimension}；仅作为待核验分类，不代表来源已证明该设计。",
    }


def digest_dual_zone(day: str, ranked: list[dict[str, Any]], *, top: int = 3) -> str:
    issue_brief = _load_module("skill_agent_research_issue_brief", ISSUE_BRIEF_PATH)
    selected = [row for row in ranked if row.get("r09", {}).get("action") != "DROP_NOISE" and float(row.get("r09", {}).get("composite_score", 0)) >= 0.65][:max(1, top)]
    for row in selected:
        row.setdefault("breakdown_draft", _breakdown_draft(row))
    items = [{"what": f"[{row['topic_key']}] {row['title']}", "why": "勾选后由 Owner 批准展开验证"} for row in selected]
    if not items:
        items = [{"what": "保持候选池清零并等待下一轮来源变化", "why": "当前没有达到行动阈值的新候选"}]
    context = ["- track: workflow", "- candidate-only: true", "- source evidence is bounded and public-only", ""]
    for row in selected:
        score = row["r09"]
        draft = row["breakdown_draft"]
        context.extend([
            f"### {row['topic_key']} · {row['architecture_dimension']}",
            f"- composite: {score['composite_score']} (p_direct={score['p_direct']}, s_tier={score['s_tier']})",
            f"- source: {row.get('source_uri') or 'unavailable'}",
            f"- file patterns: {', '.join(row.get('file_patterns') or [])}",
            f"- DRAFT TL;DR (candidate-only): {draft['tldr']}",
            f"- DRAFT pain-point FAQ: {json.dumps(draft['pain_point_faq'], ensure_ascii=False)}",
            f"- DRAFT actionable checklist (unverified): {json.dumps(draft['actionable_checklist'], ensure_ascii=False)}",
            f"- DRAFT architecture takeaway: {draft['architecture_takeaway']}",
            "",
        ])
    rendered = issue_brief.render_dual_zone_issue(
        tldr=f"今日筛出 {len(selected)} 个可验证的 Workflow 与 Agent 架构候选，先勾选再展开。",
        items=items,
        cursor=f"skill-agent-research-{day}",
        ai_context_md="\n".join(context).strip(),
        extra_comments=["<!-- skill-agent-research-track:workflow -->"],
    )
    verification = issue_brief.verify_dual_zone_issue(rendered)
    if not verification.get("ok"):
        raise ValueError(f"Dual-Zone verification failed: {verification.get('violations')}")
    return rendered


def loop_check(**kwargs: Any) -> dict[str, Any]:
    discovered = discover(**{key: kwargs[key] for key in ("vault_dir", "topic_ledger_path", "collected_tools_dir", "radar_path", "fetch_public") if key in kwargs})
    if not discovered.get("ok"):
        return discovered | {"status": "health_error", "has_delta": False, "selected": [], "dual_zone_markdown": ""}
    ranked = rerank_r09(discovered.get("candidates", []), mock_jev_json=kwargs.get("mock_jev_json"))
    selected = [row for row in ranked.get("ranked", []) if row.get("r09", {}).get("action") != "DROP_NOISE" and float(row.get("r09", {}).get("composite_score", 0)) >= 0.65]
    day = str(kwargs.get("day") or datetime.now(CST).date().isoformat())
    selected = selected[:max(1, int(kwargs.get("top") or 3))]
    for row in selected:
        row.setdefault("breakdown_draft", _breakdown_draft(row))
    report = digest_dual_zone(day, selected, top=int(kwargs.get("top") or 3)) if selected else ""
    signature = hashlib.sha256(json.dumps([row.get("topic_key") for row in selected], sort_keys=True).encode()).hexdigest()[:16]
    return {
        "ok": True,
        "status": "ACTIONABLE" if selected else "SKIPPED_NO_HIGH_SIGNAL",
        "track": "workflow",
        "date": day,
        "has_delta": bool(selected),
        "delta_count": len(selected),
        "severity": "medium" if selected else "low",
        "watermark": f"skill-agent-research:{day}:{signature}",
        "cursor": f"skill-agent-research-{day}",
        "title": f"[Skill Agent Research {day}] Workflow 架构候选日报",
        "summary": f"发现 {len(selected)} 个达到行动阈值的 Workflow 架构候选。" if selected else "暂无达到行动阈值的新 Workflow 架构候选。",
        "selected": selected,
        "items": [{"what": f"研究候选：{row['title']}", "why": row["architecture_dimension"]} for row in selected[:3]],
        "dual_zone_markdown": report,
        "raw": {"discovery": discovered, "rerank": ranked},
        "diagnostics": [*discovered.get("diagnostics", []), *ranked.get("diagnostics", [])],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Workflow Skill/Agent architecture daily research loop")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("discover", "rerank-r09", "digest-dual-zone", "loop-check"):
        p = sub.add_parser(name)
        p.add_argument("--vault-dir", type=Path, default=DEFAULT_VAULT_DIR)
        p.add_argument("--topic-ledger-path", type=Path, default=None)
        p.add_argument("--collected-tools-dir", type=Path, default=DEFAULT_COLLECTED_TOOLS_DIR)
        p.add_argument("--radar-path", type=Path, default=DEFAULT_RADAR_PATH)
        p.add_argument("--mock-jev-json", default=None)
        p.add_argument("--day", default=None)
        p.add_argument("--top", type=int, default=3)
        p.add_argument("--fetch-public", action="store_true")
        p.add_argument("--input-json", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "discover":
        result = discover(vault_dir=args.vault_dir, topic_ledger_path=args.topic_ledger_path, collected_tools_dir=args.collected_tools_dir, radar_path=args.radar_path, fetch_public=args.fetch_public)
    elif args.command == "rerank-r09":
        data = _read_json(args.input_json) if args.input_json else json.load(sys.stdin)
        result = rerank_r09(list((data or {}).get("candidates", data if isinstance(data, list) else [])), mock_jev_json=args.mock_jev_json)
    elif args.command == "digest-dual-zone":
        data = _read_json(args.input_json) if args.input_json else json.load(sys.stdin)
        result = {"ok": True, "markdown": digest_dual_zone(args.day or datetime.now(CST).date().isoformat(), list((data or {}).get("ranked", data if isinstance(data, list) else [])), top=args.top)}
    else:
        result = loop_check(vault_dir=args.vault_dir, topic_ledger_path=args.topic_ledger_path, collected_tools_dir=args.collected_tools_dir, radar_path=args.radar_path, fetch_public=args.fetch_public, mock_jev_json=args.mock_jev_json, day=args.day, top=args.top)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
