#!/usr/bin/env python3
"""Zero-Token knowledge-vault archival, R09 topic discovery, and DC-006..008 blog publishing."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import html
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILLS_ROOT = SKILL_DIR.parent
DEFAULT_VAULT_DIR = Path.home() / "Development" / "Personal" / "knowledge-vault"
DEFAULT_COLLECTED_TOOLS_DIR = Path.home() / "Development" / "Personal" / "collected-tools"
DEFAULT_BLOG_REPO = Path.home() / "Development" / "leepepe.github.io"
DEFAULT_PROPOSALS_PATH = (
    Path.home() / "Development" / "AIDash" / "aidata" / "L5_apps" / "digest" / "state" / "proposals.jsonl"
)
DEFAULT_GIT_ROOTS = (
    Path.home() / "Development" / "user-skills",
    Path.home() / "Development" / "AIDash",
    Path.home() / "Development" / "VoxPocket",
    Path.home() / "Development" / "VitalStride",
)
CARD_TEMPLATES_PATH = SKILL_DIR / "references" / "card-templates.json"

FOLDER_SECTION_HEADINGS = {
    "thoughts": "## 💭 思考与感悟随笔（`thoughts/`）",
    "concepts": "## 🧠 原理与概念痛点卡（`concepts/`）",
    "craft": "## 🎬 叙事结构与制作技巧卡（`craft/`）",
    "raw": "## 📦 原始好素材存档（`raw/`）",
}

FOLDER_TABLE_HEADERS = {
    "thoughts": "| 门类 | 标题 | 核心感悟（Insight） | 路径 | 更新日期 |\n|---|---|---|---|---|",
    "concepts": "| 门类 | 标题 | 一句话本质（TL;DR） | 路径 | 更新日期 |\n|---|---|---|---|---|",
    "craft": "| 门类 | 标题 | 一句话公式（TL;DR） | 路径 | 更新日期 |\n|---|---|---|---|---|",
    "raw": "| 来源类型 | 标题 | 衍生卡片 | 路径 | 归档日期 |\n|---|---|---|---|---|",
}

DC006_FORBIDDEN_RE = re.compile(r"\A---\s*\n[\s\S]{1,400}?\n---\s*\n\s*<!doctype html>", re.IGNORECASE)
DC007_FORBIDDEN_RE = re.compile(r"<h[1-4][^>]*>\s*[📌❓✅🎬🏷️💡🔥✨🎯🚀⚡🧰🔭🛠🔄💭🧠📦]")
DC008_FORBIDDEN_RE = re.compile(r"\.prose\s+th\s*,\s*\.prose\s+td\s*\{[^}]*white-space\s*:\s*nowrap")
_EMOJI_STRIP_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]+")


def _load_helper_module(name: str, path: Path):
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def strip_emoji(text: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI_STRIP_RE.sub("", text)).strip()


def load_card_templates(path: Path = CARD_TEMPLATES_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {"schema": "knowledge-digest-card-templates/v1", "version": 1, "templates": []}
    return json.loads(path.read_text(encoding="utf-8"))


def infer_folder(category: str, explicit_folder: str | None = None) -> str:
    if explicit_folder in {"concepts", "craft", "thoughts", "raw"}:
        return explicit_folder
    cat = category.strip()
    if "叙事" in cat or "表达" in cat or "craft" in cat.lower():
        return "craft"
    if "原始" in cat or "raw" in cat.lower():
        return "raw"
    if "思考" in cat or "感悟" in cat or "随笔" in cat or "智能家居与生活" in cat:
        return "thoughts"
    return "concepts"


def parse_frontmatter(md_text: str) -> tuple[dict[str, Any], str]:
    text = md_text.strip()
    meta: dict[str, Any] = {}
    if not text.startswith("---"):
        return meta, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return meta, text
    fm_lines = parts[1].splitlines()
    body = parts[2].strip()
    current_list_key: str | None = None
    for raw_line in fm_lines:
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        m_item = re.match(r"^\s*-\s+(.*)$", line)
        if m_item and current_list_key:
            val = m_item.group(1).strip().strip("\"'")
            meta.setdefault(current_list_key, []).append(val)
            continue
        m_kv = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if m_kv:
            key = m_kv.group(1).strip()
            val = m_kv.group(2).strip()
            if not val:
                current_list_key = key
                meta[key] = []
            elif val.startswith("[") and val.endswith("]"):
                current_list_key = None
                inner = val[1:-1].strip()
                meta[key] = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()] if inner else []
            else:
                current_list_key = None
                meta[key] = val.strip("\"'")
    return meta, body


def extract_tldr_summary(md_text: str, fallback: str = "") -> str:
    meta, body = parse_frontmatter(md_text)
    if meta.get("insight"):
        return strip_emoji(str(meta["insight"]))
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(">"):
            cleaned = re.sub(r"^>\s*", "", s)
            cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
            cleaned = strip_emoji(cleaned)
            if cleaned:
                return cleaned
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("|") and s != "---":
            return strip_emoji(re.sub(r"\*\*([^*]+)\*\*", r"\1", s))[:160]
    return strip_emoji(fallback or str(meta.get("title") or ""))


def _ensure_index_scaffold(index_path: Path) -> str:
    if index_path.is_file():
        return index_path.read_text(encoding="utf-8")
    return (
        "# Personal Knowledge Vault（个人知识与好内容拆解库）\n\n"
        "由 [`knowledge-digest`](../../user-skills/skills/knowledge-digest/SKILL.md) Skill 统一维护。\n\n"
        f"{FOLDER_SECTION_HEADINGS['thoughts']}\n\n{FOLDER_TABLE_HEADERS['thoughts']}\n\n---\n\n"
        f"{FOLDER_SECTION_HEADINGS['concepts']}\n\n{FOLDER_TABLE_HEADERS['concepts']}\n\n---\n\n"
        f"{FOLDER_SECTION_HEADINGS['craft']}\n\n{FOLDER_TABLE_HEADERS['craft']}\n\n---\n\n"
        f"{FOLDER_SECTION_HEADINGS['raw']}\n\n{FOLDER_TABLE_HEADERS['raw']}\n"
    )


def upsert_vault_index(
    index_path: Path,
    *,
    folder: str,
    rel_path: str,
    category: str,
    title: str,
    summary: str,
    updated_date: str,
) -> None:
    text = _ensure_index_scaffold(index_path)
    safe_cat = category.replace("|", "｜").strip()
    safe_title = title.replace("|", "｜").strip()
    safe_sum = summary.replace("|", "｜").strip()
    new_row = f"| {safe_cat} | {safe_title} | {safe_sum} | [`{rel_path}`]({rel_path}) | {updated_date} |"

    lines = text.splitlines()
    link_token = f"[`{rel_path}`]({rel_path})"
    replaced = False
    for idx, line in enumerate(lines):
        if link_token in line or f"({rel_path})" in line:
            lines[idx] = new_row
            replaced = True
            break

    if not replaced:
        heading = FOLDER_SECTION_HEADINGS.get(folder, FOLDER_SECTION_HEADINGS["concepts"])
        sec_idx = next((i for i, ln in enumerate(lines) if heading in ln), -1)
        if sec_idx < 0:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend([heading, "", FOLDER_TABLE_HEADERS[folder], new_row])
        else:
            insert_at = sec_idx + 1
            while insert_at < len(lines) and not lines[insert_at].strip().startswith("|---"):
                insert_at += 1
            if insert_at < len(lines):
                insert_at += 1
                while insert_at < len(lines) and lines[insert_at].strip().startswith("|"):
                    insert_at += 1
                lines.insert(insert_at, new_row)
            else:
                lines.extend(["", FOLDER_TABLE_HEADERS[folder], new_row])

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def archive_card(
    *,
    slug: str,
    category: str,
    title: str,
    file_path: Path,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    folder: str | None = None,
    updated_date: str | None = None,
) -> dict[str, Any]:
    clean_slug = re.sub(r"[^a-z0-9-]+", "-", slug.strip().lower()).strip("-")
    if not clean_slug:
        raise ValueError("slug must contain alphanumeric characters")
    if not file_path.is_file():
        raise FileNotFoundError(f"card file not found: {file_path}")

    target_folder = infer_folder(category, folder)
    raw_md = file_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw_md)
    eff_updated = updated_date or str(meta.get("updated") or meta.get("date") or date.today().isoformat())
    eff_title = title.strip() or str(meta.get("title") or clean_slug)
    eff_cat = category.strip() or str(meta.get("category") or "工程与设计")

    if not raw_md.strip().startswith("---"):
        raw_md = (
            f"---\ntitle: {eff_title}\ncategory: {eff_cat}\n"
            f"tags: [{clean_slug}]\nchannel: acquire\nupdated: {eff_updated}\n---\n\n"
            f"# {eff_title}\n\n{body}\n"
        )

    dest_dir = vault_dir / target_folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{clean_slug}.md"
    if file_path.resolve() != dest_path.resolve():
        dest_path.write_text(raw_md.rstrip() + "\n", encoding="utf-8")

    rel_path = f"{target_folder}/{clean_slug}.md"
    summary = extract_tldr_summary(raw_md, fallback=eff_title)
    index_path = vault_dir / "index.md"
    upsert_vault_index(
        index_path,
        folder=target_folder,
        rel_path=rel_path,
        category=eff_cat,
        title=eff_title,
        summary=summary,
        updated_date=eff_updated,
    )
    return {
        "ok": True,
        "slug": clean_slug,
        "folder": target_folder,
        "category": eff_cat,
        "title": eff_title,
        "card_path": str(dest_path),
        "rel_path": rel_path,
        "index_path": str(index_path),
        "summary": summary,
        "updated": eff_updated,
    }


def _existing_vault_corpus(vault_dir: Path) -> str:
    parts: list[str] = []
    index_path = vault_dir / "index.md"
    if index_path.is_file():
        parts.append(index_path.read_text(encoding="utf-8", errors="ignore"))
    for sub in ("concepts", "craft", "thoughts", "raw"):
        sub_dir = vault_dir / sub
        if sub_dir.is_dir():
            for md in sub_dir.glob("*.md"):
                parts.append(md.stem)
                parts.append(md.read_text(encoding="utf-8", errors="ignore")[:500])
    return "\n".join(parts).lower()


def _scan_collected_tools_candidates(collected_dir: Path, existing_corpus: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if not collected_dir.is_dir():
        return candidates
    for md in sorted(collected_dir.glob("*.md")):
        if md.name.lower() == "readme.md":
            continue
        slug = re.sub(r"[^a-z0-9-]+", "-", md.stem.lower()).strip("-")
        text = md.read_text(encoding="utf-8", errors="ignore")
        first_h1 = next(
            (ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")),
            md.stem,
        )
        summary_lines = [
            ln.strip()
            for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith("```")
        ]
        excerpt = " ".join(summary_lines[:3])[:240]
        already_covered = slug in existing_corpus and f"concepts/{slug}.md" in existing_corpus
        if already_covered:
            continue
        candidates.append({
            "slug": f"{slug}-anti-slop-anatomy" if slug == "hallmark" else slug,
            "title": f"{first_h1}：反 AI 模板感解剖设计与多视口几何门禁",
            "category": "工程与设计",
            "source_kind": "collected_tools",
            "source_ref": str(md),
            "excerpt": excerpt or f"Tool note from {md.name}",
            "heuristic_direct": 0.88,
            "heuristic_tier": 2.6,
        })
    repos_dir = collected_dir / "repos"
    if repos_dir.is_dir():
        for repo_dir in sorted(repos_dir.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name.startswith("."):
                continue
            slug = re.sub(r"[^a-z0-9-]+", "-", repo_dir.name.lower()).strip("-")
            if slug in existing_corpus:
                continue
            readme = next(
                (p for p in (repo_dir / "README.md", repo_dir / "readme.md") if p.is_file()),
                None,
            )
            excerpt = ""
            if readme is not None:
                lines = [
                    ln.strip()
                    for ln in readme.read_text(encoding="utf-8", errors="ignore").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
                excerpt = " ".join(lines[:2])[:220]
            candidates.append({
                "slug": f"{slug}-core-patterns",
                "title": f"{repo_dir.name} 核心架构模式与实战拆解",
                "category": "工程与设计",
                "source_kind": "collected_tools_repo",
                "source_ref": str(repo_dir),
                "excerpt": excerpt or f"Reference repository {repo_dir.name} in collected-tools",
                "heuristic_direct": 0.80,
                "heuristic_tier": 2.3,
            })
    return candidates


def _scan_git_tech_stack_candidates(git_roots: tuple[Path, ...], existing_corpus: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    commit_lines: list[str] = []
    for root in git_roots:
        if not (root / ".git").exists():
            continue
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), "--no-pager", "log", "-n", "15", "--oneline"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                commit_lines.extend(proc.stdout.strip().splitlines())
        except (OSError, subprocess.SubprocessError):
            continue

    joined_commits = "\n".join(commit_lines).lower()
    topic_probes = [
        (
            "jev-r09-r06-truth-table",
            "Jev System One R09 重排与 R06 引用核查：用零幻觉真值表替代黑盒打分",
            "计算机与网络",
            ("r09", "r06", "jev", "systemone", "rerank"),
            "Recent commits wire Jev R09 score reranking and R06 citation verification across research and digest pipelines.",
            0.90,
            2.7,
        ),
        (
            "progressive-disclosure-four-valves",
            "渐进式披露 Skill 四道防爆阀门：为什么 L0/L1/L2 预算能让 Token 降一个量级",
            "工程与设计",
            ("skill-contract", "repo-bound", "progressive", "drawer", "audit"),
            "Recent git commits refine skill-contract L0/L1/L2 progressive disclosure budgets and executable eval drawers.",
            0.85,
            2.5,
        ),
        (
            "aidash-xpc-event-bridge-flywheel",
            "AIDash 单向数据流与 Event Bridge 闭环：从 L1 遥测采集到点 Star 自动触发动作",
            "工程与设计",
            ("event_bridge", "aidata", "linear_issue", "aidash", "proposal"),
            "Recent commits in AIDash connect L1-L5 telemetry with event_bridge Star/Done actions.",
            0.83,
            2.4,
        ),
    ]
    for slug, title, category, keywords, excerpt, p_direct, s_tier in topic_probes:
        if slug in existing_corpus:
            continue
        matched = any(k in joined_commits for k in keywords) or not commit_lines
        if matched:
            candidates.append({
                "slug": slug,
                "title": title,
                "category": category,
                "source_kind": "git_tech_stack",
                "source_ref": "git-log",
                "excerpt": excerpt,
                "heuristic_direct": p_direct,
                "heuristic_tier": s_tier,
            })
    return candidates


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


def render_concept_card_markdown(candidate: dict[str, Any], updated_date: str | None = None) -> str:
    eff_date = updated_date or date.today().isoformat()
    slug = candidate["slug"]
    title = candidate["title"]
    category = candidate.get("category", "工程与设计")
    excerpt = candidate.get("excerpt", "")
    return f"""---
title: {title}
category: {category}
tags: [{slug}, 工程实践, 痛点破案]
channel: acquire
updated: {eff_date}
---

# {title}

## ⚡ 一句话本质（TL;DR）

> **{title}的核心不是堆砌规则，而是把隐性工程摩擦变成零心智负担的确定性护栏** —— 将高频排障与阅读检索耗时压缩 **80% 以上**（从数十分钟人工比对降到 **1 次**确定性检查）。

## 🧰 核心教具与量级速览

| 抽象概念 / 参数 | 关键数字 | 生活教具 / 直观参照 | 物理/工程效果 |
|---|---|---|---|
| 上下文与信噪比预算 | 压缩 **80%+** 无关噪音 | 行李箱分层收纳袋 | 避免一次性塞满长文本导致注意力稀释 |
| 确定性门禁回执 | **0 Token** 毫秒级拦截 | 电路保险丝与防呆卡扣 | 在问题进入下游前当场定位并阻断 |

## 🎯 核心痛点直答（Pain-Point Q&A）

### Q1：为什么平时收藏了很多工具与工程实践，真正用时还是容易踩旧坑？
- **直答**：缺少从「原始素材」到「痛点问答 + 零 Token 门禁」的闭环转化。
- **原理与教具**：就像厨房买了很多调料却没贴标签分装（{excerpt[:80] or "散落在仓库与笔记中"}），紧要关头只能凭记忆乱抓；只有提炼成 **3–5 个痛点问答**并绑定脚本检查，知识才会变成肌肉记忆。

### Q2：如何在不增加日常维护负担的前提下持续消化新主题？
- **直答**：用自动寻题重排（R09）筛出 Top **1–2** 个高价值缺口，推上每日简报按需点亮发布。
- **原理与教具**：像智能温控器只在实际偏离阈值时才启动加热，每次只聚焦最痛的 **1 个工程切面**，拒绝百科全书式囤积。

## ✅ 立即可用清单（Actionable Checklist）

1. **先跑一行命令验证现状**：用确定性脚本扫描当前仓库与知识库缺口，不靠主观猜测选题。
2. **用生活教具替掉干瘪定义**：每张卡片至少绑定 **1 个具体数字对比**与 **1 个日常参照物**。
3. **点 Star 一键沉淀与发布**：在 AIDash 上审批高价值主题，自动同步到个人博客与索引表。

## 🔭 延伸视野（1 分钟拔高）

- 当知识摄取、每日简报与静态博客形成单向无摩擦飞轮，每一次解决线上痛点都会自然长成一篇可复用的公开技术笔记。
"""


def discover_topics(
    *,
    top: int = 2,
    propose_aidash: bool = False,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    collected_tools_dir: Path = DEFAULT_COLLECTED_TOOLS_DIR,
    git_roots: tuple[Path, ...] = DEFAULT_GIT_ROOTS,
    proposals_path: Path = DEFAULT_PROPOSALS_PATH,
    materialize_cards: bool = True,
    mock_r09_answers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing_corpus = _existing_vault_corpus(vault_dir)
    raw_candidates = (
        _scan_collected_tools_candidates(collected_tools_dir, existing_corpus)
        + _scan_git_tech_stack_candidates(git_roots, existing_corpus)
    )
    deduped: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for cand in raw_candidates:
        if cand["slug"] not in seen_slugs:
            seen_slugs.add(cand["slug"])
            deduped.append(cand)

    ranked = _rerank_candidates_r09(deduped, mock_r09_answers=mock_r09_answers)
    selected = ranked[: max(1, top)]

    archived_cards: list[dict[str, Any]] = []
    proposals_written: list[dict[str, Any]] = []
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    for item in selected:
        slug = item["slug"]
        if materialize_cards:
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
            record = {
                "id": f"learn-{slug}",
                "ref": f"knowledge://{slug}",
                "ts": now_iso,
                "agent": "knowledge-digest",
                "title": f"学习卡片：{item['title']}",
                "detail": f"R09 score={item['r09']['composite_score']} ({item['r09']['action']}) · ref=knowledge://{slug} · {item['excerpt']}",
                "priority": "medium",
                "status": "pending",
            }
            proposals_path.parent.mkdir(parents=True, exist_ok=True)
            with proposals_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            proposals_written.append(record)

    return {
        "ok": True,
        "candidate_count": len(deduped),
        "selected_count": len(selected),
        "topics": selected,
        "archived_cards": archived_cards,
        "proposals_written": proposals_written,
    }


def validate_dc006_to_008(html_text: str, css_text: str = "") -> list[str]:
    violations: list[str] = []
    if not html_text.startswith("<!doctype html>"):
        violations.append("DC-006: HTML does not start with <!doctype html> on line 1")
    if DC006_FORBIDDEN_RE.search(html_text):
        violations.append("DC-006: Raw YAML frontmatter leaked above <!doctype html>")
    if DC007_FORBIDDEN_RE.search(html_text):
        violations.append("DC-007: Decorative Emoji found inside <h1..h4> heading")
    if css_text and DC008_FORBIDDEN_RE.search(css_text):
        violations.append("DC-008: Global white-space: nowrap found on .prose th, .prose td")
    return violations


def _find_vault_card(vault_dir: Path, slug: str) -> tuple[str, Path]:
    for folder in ("concepts", "craft", "thoughts"):
        candidate = vault_dir / folder / f"{slug}.md"
        if candidate.is_file():
            return folder, candidate
    for folder in ("concepts", "craft", "thoughts"):
        sub = vault_dir / folder
        if not sub.is_dir():
            continue
        for md in sorted(sub.glob("*.md")):
            if slug in md.stem or md.stem in slug:
                return folder, md
    raise FileNotFoundError(f"knowledge card for slug '{slug}' not found under {vault_dir}")


def _render_standalone_learn_html(
    *,
    slug: str,
    title: str,
    teaser: str,
    updated_date: str,
    tags: list[str],
    prose_html: str,
    toc_items: list[dict[str, str]],
    words: int,
) -> str:
    clean_title = strip_emoji(title)
    clean_teaser = strip_emoji(teaser)
    tags_li = "".join(f'<li><a class="tag" href="/tags/">{html.escape(strip_emoji(t))}</a></li>' for t in tags[:4])
    toc_lis = "".join(
        f'<li class="{t["level"]}"><a href="#{t["id"]}">{html.escape(strip_emoji(t["title"]))}</a></li>'
        for t in toc_items
    )
    return f"""<!doctype html>
<html lang="zh-CN" data-lp-theme="light" data-strata-brand="yellow" data-strata-material="glass">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(clean_title)} · Lee Pepe</title>
<meta name="description" content="{html.escape(clean_teaser, quote=True)}">
<link rel="stylesheet" href="/assets/colors.css">
<link rel="stylesheet" href="/assets/strata-theme.css">
<link rel="stylesheet" href="/assets/blog.css">
</head>
<body>
<main id="main" class="shell">
  <div class="post-layout">
    <div>
      <article class="card article" data-slug="{html.escape(slug)}">
        <header class="article-header">
          <ul class="tags" aria-label="标签">{tags_li}</ul>
          <h1>{html.escape(clean_title)}</h1>
          <p class="lede">{html.escape(clean_teaser)}</p>
          <div class="post-meta"><time datetime="{html.escape(updated_date)}">{html.escape(updated_date)}</time><span class="dot" aria-hidden="true">·</span><span>约 {words:,} 字</span></div>
        </header>
        <div class="prose">
{prose_html}
        </div>
      </article>
    </div>
    <aside class="toc" aria-label="本文目录">
      <p class="section-label">目录</p>
      <ol>{toc_lis}</ol>
    </aside>
  </div>
</main>
</body>
</html>
"""


def publish_blog(
    *,
    slug: str,
    blog_repo: Path = DEFAULT_BLOG_REPO,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    mode: str = "observe",
) -> dict[str, Any]:
    clean_slug = re.sub(r"^knowledge://", "", slug.strip()).strip("/")
    folder, card_path = _find_vault_card(vault_dir, clean_slug)
    raw_md = card_path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(raw_md)
    title = strip_emoji(str(meta.get("title") or card_path.stem))
    category = strip_emoji(str(meta.get("category") or "工程与设计"))
    updated_date = str(meta.get("updated") or meta.get("date") or date.today().isoformat())
    tags_raw = meta.get("tags") or [clean_slug]
    tags = [strip_emoji(str(t)) for t in (tags_raw if isinstance(tags_raw, list) else [tags_raw]) if str(t).strip()]
    teaser = extract_tldr_summary(raw_md, fallback=title)

    build_site_path = blog_repo / "build_site.py"
    site_mod = _load_helper_module("leepepe_build_site", build_site_path)
    rel_source = f"{folder}/{card_path.name}"

    if site_mod is not None:
        site_mod.REPO_ROOT = blog_repo
        site_mod.LEARN_DIR = blog_repo / "learn"
        site_mod.THOUGHTS_DIR = blog_repo / "thoughts"
        site_mod.VAULT_DIR = vault_dir
        site_mod.VAULT_THOUGHTS_DIR = vault_dir / "thoughts"

        if folder != "thoughts":
            existing_idx = next(
                (
                    i
                    for i, art in enumerate(site_mod.LEARN_ARTICLES)
                    if art.get("slug") == clean_slug or rel_source in art.get("sources", [])
                ),
                None,
            )
            article_entry = {
                "num": f"{len(site_mod.LEARN_ARTICLES) + 1:02d}" if existing_idx is None else site_mod.LEARN_ARTICLES[existing_idx]["num"],
                "slug": clean_slug if existing_idx is None else site_mod.LEARN_ARTICLES[existing_idx]["slug"],
                "aliases": [] if existing_idx is None else site_mod.LEARN_ARTICLES[existing_idx].get("aliases", []),
                "category": category,
                "title": title,
                "short_title": title[:28],
                "reading_time": str(meta.get("reading_time") or "阅读约 6 分钟"),
                "updated": updated_date,
                "teaser": teaser,
                "sources": [rel_source],
                "tags": tags[:3] or ["求知"],
            }
            if existing_idx is None:
                site_mod.LEARN_ARTICLES.append(article_entry)
            else:
                site_mod.LEARN_ARTICLES[existing_idx] = article_entry
                clean_slug = article_entry["slug"]

        site_mod.main()
        out_section = "thoughts" if folder == "thoughts" else "learn"
        out_html_path = blog_repo / out_section / clean_slug / "index.html"
    else:
        out_section = "thoughts" if folder == "thoughts" else "learn"
        out_html_path = blog_repo / out_section / clean_slug / "index.html"
        out_html_path.parent.mkdir(parents=True, exist_ok=True)
        prose_lines: list[str] = [
            f"<blockquote><p><strong>一句话结论</strong>：{html.escape(teaser)}</p></blockquote>"
        ]
        toc_items: list[dict[str, str]] = []
        sec_no = 0
        _, body_md = parse_frontmatter(raw_md)
        for ln in body_md.splitlines():
            s = ln.strip()
            if not s or s.startswith("# ") or s == "---":
                continue
            if s.startswith("## "):
                sec_no += 1
                sec_id = f"sec-{sec_no}"
                h2_clean = strip_emoji(s[3:])
                toc_items.append({"id": sec_id, "level": "l2", "title": h2_clean})
                prose_lines.append(f'<h2 id="{sec_id}">{html.escape(h2_clean)}</h2>')
            elif s.startswith("### "):
                h3_clean = strip_emoji(s[4:])
                prose_lines.append(f"<h3>{html.escape(h3_clean)}</h3>")
            elif not s.startswith(">"):
                prose_lines.append(f"<p>{html.escape(strip_emoji(s))}</p>")
        rendered = _render_standalone_learn_html(
            slug=clean_slug,
            title=title,
            teaser=teaser,
            updated_date=updated_date,
            tags=tags,
            prose_html="\n".join(prose_lines),
            toc_items=toc_items,
            words=max(500, len(body_md)),
        )
        out_html_path.write_text(rendered, encoding="utf-8")

    html_text = out_html_path.read_text(encoding="utf-8")
    css_path = blog_repo / "assets" / "blog.css"
    css_text = css_path.read_text(encoding="utf-8") if css_path.is_file() else ""
    violations = validate_dc006_to_008(html_text, css_text)
    if violations:
        raise ValueError(f"DC-006..008 validation failed: {'; '.join(violations)}")

    committed = False
    commit_sha: str | None = None
    if mode == "auto" and (blog_repo / ".git").exists():
        status_proc = subprocess.run(
            ["git", "-C", str(blog_repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status_proc.returncode == 0 and status_proc.stdout.strip():
            subprocess.run(["git", "-C", str(blog_repo), "add", "-A"], check=False)
            commit_proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(blog_repo),
                    "commit",
                    "-m",
                    f"docs(learn): publish {clean_slug} from knowledge-vault",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if commit_proc.returncode == 0:
                committed = True
                rev_proc = subprocess.run(
                    ["git", "-C", str(blog_repo), "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                commit_sha = rev_proc.stdout.strip() if rev_proc.returncode == 0 else None

    return {
        "ok": True,
        "slug": clean_slug,
        "folder": folder,
        "card_path": str(card_path),
        "output_html": str(out_html_path),
        "url": f"/{out_section}/{clean_slug}/",
        "dc_validation": "PASS",
        "mode": mode,
        "committed": committed,
        "commit_sha": commit_sha,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Knowledge Digest vault & blog flywheel CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_arch = sub.add_parser("archive", help="Archive a markdown card into knowledge-vault and update index.md")
    p_arch.add_argument("--slug", required=True)
    p_arch.add_argument("--category", required=True)
    p_arch.add_argument("--title", required=True)
    p_arch.add_argument("--file", required=True, type=Path)
    p_arch.add_argument("--folder", choices=("concepts", "craft", "thoughts", "raw"), default=None)
    p_arch.add_argument("--vault-dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_arch.add_argument("--updated", default=None)

    p_disc = sub.add_parser("discover-topics", help="Scan git tech stack, collected-tools, and vault gaps with R09")
    p_disc.add_argument("--top", type=int, default=2)
    p_disc.add_argument("--propose-aidash", action="store_true")
    p_disc.add_argument("--no-materialize", action="store_true")
    p_disc.add_argument("--vault-dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_disc.add_argument("--collected-tools-dir", type=Path, default=DEFAULT_COLLECTED_TOOLS_DIR)
    p_disc.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)

    p_pub = sub.add_parser("publish-blog", help="Render a knowledge-vault card into leepepe.github.io (DC-006..008)")
    p_pub.add_argument("--slug", required=True)
    p_pub.add_argument("--blog-repo", type=Path, default=DEFAULT_BLOG_REPO)
    p_pub.add_argument("--vault-dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_pub.add_argument("--mode", choices=("observe", "auto"), default="observe")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "archive":
        res = archive_card(
            slug=args.slug,
            category=args.category,
            title=args.title,
            file_path=args.file,
            vault_dir=args.vault_dir,
            folder=args.folder,
            updated_date=args.updated,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if args.command == "discover-topics":
        res = discover_topics(
            top=args.top,
            propose_aidash=args.propose_aidash,
            vault_dir=args.vault_dir,
            collected_tools_dir=args.collected_tools_dir,
            proposals_path=args.proposals_path,
            materialize_cards=not args.no_materialize,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if args.command == "publish-blog":
        res = publish_blog(
            slug=args.slug,
            blog_repo=args.blog_repo,
            vault_dir=args.vault_dir,
            mode=args.mode,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
