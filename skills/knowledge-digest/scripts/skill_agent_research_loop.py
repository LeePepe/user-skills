#!/usr/bin/env python3
"""Workflow Skill/Agent architecture discovery, R09 reranking, and daily digest loop."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
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
JEV_CLIENT_PATH = REPO_ROOT / "skills" / "team-lead" / "scripts" / "jev_client.py"
JEV_HELPER_PATH = REPO_ROOT / "skills" / "deep-research" / "scripts" / "jev_research_helper.py"
CST = timezone(timedelta(hours=8))
DEFAULT_VAULT_DIR = Path.home() / "Development" / "Personal" / "knowledge-vault"
DEFAULT_COLLECTED_TOOLS_DIR = Path.home() / "Development" / "Personal" / "collected-tools"
DEFAULT_RADAR_PATH = Path.home() / "Development" / "AIDash" / "aidata"
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
    raw = str(value or "").strip()
    if raw.startswith("git@github.com:"):
        raw = "https://github.com/" + raw.split(":", 1)[1]
    try:
        parts = urlsplit(raw)
        if parts.scheme and parts.netloc:
            path = parts.path.rstrip("/")
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


def _ledger_covered(ledger_path: Path) -> tuple[set[str], set[str], set[str], set[str], set[str], str | None]:
    if not ledger_path.is_file():
        return set(), set(), set(), set(), set(), f"missing topic ledger: {ledger_path}"
    doc = _read_json(ledger_path)
    if not isinstance(doc, dict) or not isinstance(doc.get("topics"), list):
        return set(), set(), set(), set(), set(), f"invalid topic ledger: {ledger_path}"
    keys: set[str] = set()
    aliases: set[str] = set()
    uris: set[str] = set()
    fingerprints: set[str] = set()
    questions: set[str] = set()
    for item in doc["topics"]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("topic_key") or "").strip().lower()
        if key:
            keys.add(key)
        aliases.update(str(x).strip().lower() for x in item.get("aliases", []) if str(x).strip())
        uri = _norm_uri(str(item.get("source_uri") or ""))
        if uri:
            uris.add(uri)
        fingerprint = str(item.get("source_fingerprint") or "").strip()
        if fingerprint:
            fingerprints.add(fingerprint)
        question = _identity(str(item.get("question") or ""))
        if question:
            questions.add(question)
    return keys, aliases, uris, fingerprints, questions, None


def _candidate(*, key: str, title: str, uri: str, source_ref: str, excerpt: str, dimension: str = "") -> dict[str, Any]:
    text = " ".join((key, title, uri, excerpt))
    dim = dimension or next((name for name, pattern in DIMENSION_TERMS.items() if pattern.search(text)), "CONTEXT_ENGINEERING")
    digest = hashlib.sha256(_norm_uri(uri).encode("utf-8")).hexdigest()[:16]
    return {
        "topic_key": key,
        "slug": key,
        "title": title[:180],
        "question": f"如何把 {title[:100]} 转化为可验证的工作流能力？",
        "source_uri": _norm_uri(uri),
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


def _walk_radar(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    files = [path] if path.is_file() else sorted(path.rglob("*.json"))
    for file in files[:500]:
        data = _read_json(file)
        if isinstance(data, dict):
            data = data.get("fact_repo_snapshot") or data.get("repos") or data.get("items") or [data]
        if not isinstance(data, list):
            continue
        for row in data:
            if isinstance(row, dict):
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
    keys, aliases, uris, fingerprints, questions, ledger_error = _ledger_covered(ledger_path)
    diagnostics: list[str] = []
    if ledger_error:
        return {"ok": False, "status": "health_error", "error": ledger_error, "candidates": [], "diagnostics": [ledger_error]}
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(row: dict[str, Any]) -> None:
        key = str(row.get("topic_key") or row.get("slug") or "").strip().lower()
        uri = _norm_uri(str(row.get("source_uri") or ""))
        question = _identity(str(row.get("question") or row.get("title") or ""))
        fingerprint = str(row.get("source_fingerprint") or "")
        if not key or not WORKFLOW_TERMS.search(" ".join(str(row.get(x) or "") for x in ("topic_key", "title", "excerpt", "source_uri"))):
            return
        if key in keys or key in aliases or uri in uris or fingerprint in fingerprints or question in questions:
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

    for row in _walk_radar(radar_path or DEFAULT_RADAR_PATH):
        uri = str(row.get("html_url") or row.get("url") or row.get("source_uri") or row.get("repo_url") or "")
        key = str(row.get("topic_key") or row.get("full_name") or row.get("name") or "").strip().lower().replace("/", "-")
        title = str(row.get("title") or row.get("name") or key)
        excerpt = str(row.get("description") or row.get("summary") or row.get("excerpt") or "")
        add(_candidate(key=key, title=title, uri=uri, source_ref=str(row.get("_source_file") or radar_path), excerpt=excerpt))

    if fetch_public:
        for key, title, uri, dim in CURATED_PUBLIC_SOURCES:
            try:
                request = Request(uri, headers={"User-Agent": "knowledge-digest-workflow/1"})
                with urlopen(request, timeout=3) as response:
                    body = response.read(4000).decode("utf-8", errors="replace")
                add(_candidate(key=key, title=title, uri=uri, source_ref=f"public:{key}", excerpt=body, dimension=dim))
            except (OSError, ValueError) as exc:
                diagnostics.append(f"public source unavailable {uri}: {type(exc).__name__}")
    else:
        diagnostics.append("public fetch disabled; use --fetch-public for bounded curated-source probes")

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
        questions[f"cand_{index}_dimension"] = {"type": "choice", "instructions": "Classify architecture dimension.", "choices": list(DIMENSION_TERMS)}
    state = {"track": "workflow", "candidates": {f"cand_{i}": c for i, c in enumerate(candidates)}}
    if client is not None and (not mock_supplied or mock is not None):
        decision = client.jev_decide(state, questions, agent_role="knowledge-digest", mock_response=mock)
        if isinstance(decision, dict):
            answers = decision.get("answers", decision)
    if not isinstance(answers, dict):
        diagnostics.append("Jev unavailable, malformed, or grey; deterministic R09 fallback used")
        answers = {}
    for index, candidate in enumerate(candidates):
        direct = answers.get(f"cand_{index}_direct")
        tier = answers.get(f"cand_{index}_tier")
        if not isinstance(direct, dict) or "noul" not in direct and "probability" not in direct:
            answers[f"cand_{index}_direct"] = {"noul": float(candidate.get("heuristic_direct", 0.6))}
        if not isinstance(tier, dict) or "score" not in tier:
            answers[f"cand_{index}_tier"] = {
                "score": float(candidate.get("heuristic_tier", 1.5)),
                "probabilities": {"0": 0.0, "2": 0.5, "3": 0.5},
            }
    if helper is not None and hasattr(helper, "evaluate_r09_truth_table"):
        score_rows = helper.evaluate_r09_truth_table(answers, candidates)
    else:
        score_rows = []
        for index, candidate in enumerate(candidates):
            direct = answers[f"cand_{index}_direct"]
            tier = answers[f"cand_{index}_tier"]
            p_direct = float(direct.get("noul", direct.get("probability", 0.0)))
            s_tier = float(tier.get("score", 0.0))
            score_rows.append({"index": index, "p_direct": p_direct, "s_tier": s_tier, "composite_score": round(0.65 * p_direct + 0.35 * (s_tier / 3.0), 6), "action": "KEEP_PRIMARY" if p_direct >= 0.7 and s_tier >= 2.0 else "KEEP_SUPPORTING"})
        score_rows.sort(key=lambda row: (-row["composite_score"], row["index"]))
    rows: list[dict[str, Any]] = []
    for score_row in score_rows:
        index = int(score_row["index"])
        candidate = candidates[index]
        dimension = answers.get(f"cand_{index}_dimension", {})
        choice = dimension.get("choice") if isinstance(dimension, dict) else None
        arch = choice if choice in DIMENSION_TERMS else str(candidate.get("architecture_dimension") or "CONTEXT_ENGINEERING")
        rows.append({**candidate, "r09": {"p_direct": score_row["p_direct"], "s_tier": score_row["s_tier"], "composite_score": score_row["composite_score"], "action": score_row["action"]}, "architecture_dimension": arch})
    return {"ok": True, "status": "ranked", "ranked": rows, "diagnostics": diagnostics, "formula": "0.65 * p_direct + 0.35 * (s_tier / 3.0)"}


def digest_dual_zone(day: str, ranked: list[dict[str, Any]], *, top: int = 3) -> str:
    issue_brief = _load_module("skill_agent_research_issue_brief", ISSUE_BRIEF_PATH)
    selected = [row for row in ranked if row.get("r09", {}).get("action") != "DROP_NOISE" and float(row.get("r09", {}).get("composite_score", 0)) >= 0.65][:max(1, top)]
    items = [{"what": f"[{row['topic_key']}] {row['title']}", "why": "勾选后由 Owner 批准展开验证"} for row in selected]
    if not items:
        items = [{"what": "保持候选池清零并等待下一轮来源变化", "why": "当前没有达到行动阈值的新候选"}]
    context = ["- track: workflow", "- candidate-only: true", "- source evidence is bounded and public-only", ""]
    for row in selected:
        score = row["r09"]
        context.extend([
            f"### {row['topic_key']} · {row['architecture_dimension']}",
            f"- composite: {score['composite_score']} (p_direct={score['p_direct']}, s_tier={score['s_tier']})",
            f"- source: {row.get('source_uri') or 'unavailable'}",
            f"- file patterns: {', '.join(row.get('file_patterns') or [])}",
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
    report = digest_dual_zone(day, ranked.get("ranked", []), top=int(kwargs.get("top") or 3)) if selected else ""
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
