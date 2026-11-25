"""Card archival, frontmatter extraction, vault coverage, precheck, and blog publishing."""

from __future__ import annotations

from .common import *

_KD_AUTO_DESIGN_SYNC_RE = re.compile(
    r"(?i)(?:自动同步|auto[-_\s]?sync)[^\n。；;!?！？]{0,60}design-studio|"
    r"design-studio[^\n。；;!?！？]{0,60}(?:自动同步|auto[-_\s]?sync)"
)
_KD_NEGATION_RE = re.compile(
    r"(?i)(?:永不|禁止|严禁|不自动|不得|不会自动|不授权自动|never|must\s+not|do\s+not|don't|cannot|not\s+auto)"
)
_KD_CLAUSE_SPLIT_RE = re.compile(
    r"[。；;！!？?\n]+|(?:\.(?=\s|$))|(?:\s*[|｜]\s*)|(?:[,，]\s*(?=(?:design-studio|knowledge-vault|自动同步|auto[-_\s]?sync|并且|而且|同时|and\b|but\b|then\b)))",
    re.IGNORECASE,
)


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
                parts.append(f"{sub}/{md.name}")
                parts.append(md.stem)
                parts.append(md.read_text(encoding="utf-8"))
    return "\n".join(parts).lower()


def _inspect_vault_coverage(vault_dir: Path) -> dict[str, Any]:
    parts: list[str] = []
    rel_paths: set[str] = set()
    stems: set[str] = set()
    topic_keys: set[str] = set()
    aliases: set[str] = set()
    covered_raw_files: set[str] = set()
    uncovered_raw_items: list[dict[str, Any]] = []

    index_path = vault_dir / "index.md"
    if index_path.is_file():
        idx_text = index_path.read_text(encoding="utf-8", errors="ignore")
        parts.append(idx_text)
        for m in re.finditer(r"\(((?:concepts|craft|thoughts|raw)/[^)]+\.md)\)", idx_text):
            rel_paths.add(_normalize_rel_vault_ref(m.group(1)))

    for sub in ("concepts", "craft", "thoughts", "raw"):
        sub_dir = vault_dir / sub
        if not sub_dir.is_dir():
            continue
        for md in sorted(sub_dir.glob("*.md")):
            rel_p = f"{sub}/{md.name}".lower()
            rel_paths.add(rel_p)
            stem_l = md.stem.lower()
            stems.add(stem_l)
            text = md.read_text(encoding="utf-8", errors="ignore")
            meta, body = parse_frontmatter(text)
            parts.append(stem_l)
            parts.append(text)

            tk = str(meta.get("topic_key") or "").strip().lower()
            if tk:
                topic_keys.add(tk)
            for al_key in ("aliases", "tags"):
                raw_al = meta.get(al_key)
                if isinstance(raw_al, list):
                    for item in raw_al:
                        if str(item).strip():
                            aliases.add(str(item).strip().lower())
                elif isinstance(raw_al, str) and raw_al.strip():
                    aliases.add(raw_al.strip().lower())

            for cov_key in ("coverage_refs",):
                raw_cov = meta.get(cov_key)
                if isinstance(raw_cov, list):
                    for item in raw_cov:
                        if str(item).strip():
                            rel_paths.add(_normalize_rel_vault_ref(str(item)))

            if sub == "raw":
                derived_ptrs: list[str] = []
                for d_key in ("derived_concept", "derived_craft", "derived_cards", "derived"):
                    d_val = meta.get(d_key)
                    if isinstance(d_val, list):
                        derived_ptrs.extend(str(x) for x in d_val if str(x).strip())
                    elif isinstance(d_val, str) and d_val.strip():
                        derived_ptrs.append(d_val.strip())
                for m_ptr in re.finditer(r"\(\.\./((?:concepts|craft|thoughts)/[^)]+\.md)\)", body):
                    derived_ptrs.append(m_ptr.group(1))
                norm_ptrs = [_normalize_rel_vault_ref(p) for p in derived_ptrs if _normalize_rel_vault_ref(p)]
                for np in norm_ptrs:
                    rel_paths.add(np)
                    stems.add(Path(np).stem.lower())
                if norm_ptrs:
                    covered_raw_files.add(rel_p)
                else:
                    first_h1 = next(
                        (ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")),
                        str(meta.get("title") or md.stem),
                    )
                    summary_lines = [
                        ln.strip()
                        for ln in body.splitlines()
                        if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith("---")
                    ]
                    uncovered_raw_items.append({
                        "slug": stem_l,
                        "topic_key": tk or stem_l,
                        "title": first_h1,
                        "category": str(meta.get("category") or "工程与设计"),
                        "source_kind": "vault_raw",
                        "source_ref": str(md),
                        "source_uri": f"vault://{rel_p}",
                        "aliases": [stem_l],
                        "coverage_refs": [],
                        "excerpt": " ".join(summary_lines[:2])[:220] or f"Raw material {md.name}",
                        "heuristic_direct": 0.86,
                        "heuristic_tier": 2.5,
                    })

    return {
        "corpus_text": "\n".join(parts).lower(),
        "rel_paths": rel_paths,
        "stems": stems,
        "topic_keys": topic_keys,
        "aliases": aliases,
        "covered_raw_files": covered_raw_files,
        "uncovered_raw_items": uncovered_raw_items,
    }


def _vault_coverage(vault_dir: Path) -> tuple[set[str], set[str]]:
    identities: set[str] = set()
    questions: set[str] = set()
    for folder in ("concepts", "craft", "thoughts", "raw"):
        for path in sorted((vault_dir / folder).glob("*.md")):
            meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            derived = [value for key, value in meta.items() if key.startswith("derived_")]
            if folder == "raw" and not derived:
                continue
            identities.add(path.stem)
            for key in ("topic_key", "aliases", "topic_aliases"):
                values = meta.get(key, [])
                identities.update(str(v).strip() for v in (values if isinstance(values, list) else [values]) if v)
            if meta.get("question"):
                questions.add(_question_identity(str(meta["question"])))
            for value in derived:
                for ref in (value if isinstance(value, list) else [value]):
                    identities.add(Path(str(ref)).stem)
    return identities, questions


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
    raw_slug = slug.strip()
    if raw_slug.startswith("learning-topic://"):
        raise ValueError(
            "learning-topic:// candidates cannot trigger publish-blog before Owner exploration and card archival"
        )
    clean_slug = re.sub(r"^knowledge://", "", raw_slug).strip("/")
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


def precheck_card(
    *,
    file_path: Path,
    folder: str | None = None,
    vault_dir: Path | None = None,
    require_index: bool = False,
) -> dict[str, Any]:
    """Deterministically validate required card sections, frontmatter, index registration, and no-auto-sync-design-studio."""
    resolved = file_path.expanduser().resolve()
    if not resolved.is_file():
        return {"ok": False, "file": str(resolved), "violations": [{"code": "missing_card_file", "detail": str(resolved)}]}

    raw_md = resolved.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw_md)
    violations: list[dict[str, str]] = []

    eff_folder = folder
    if eff_folder is None:
        parent_name = resolved.parent.name
        if parent_name in {"concepts", "craft", "thoughts", "raw"}:
            eff_folder = parent_name
        else:
            eff_folder = infer_folder(str(meta.get("category") or ""), None)

    for req_fm in ("title", "category", "updated"):
        if not str(meta.get(req_fm) or "").strip():
            violations.append({
                "code": "missing_frontmatter_field",
                "field": req_fm,
                "detail": f"Card frontmatter is missing required field '{req_fm}'",
            })

    if eff_folder == "concepts":
        section_checks = (
            ("tldr_section", re.compile(r"(?im)^##\s+.*(?:一句话本质|TL;DR)")),
            ("pain_point_qa_section", re.compile(r"(?im)^##\s+.*(?:核心痛点(?:直答|问答)|Pain-Point\s+Q&A|FAQ)")),
            ("actionable_checklist_section", re.compile(r"(?im)^##\s+.*(?:立即可用清单|Actionable\s+Checklist)")),
        )
        for sec_code, sec_re in section_checks:
            if not sec_re.search(body):
                violations.append({
                    "code": "missing_card_section",
                    "section": sec_code,
                    "detail": f"Concept card is missing required section '{sec_code}'",
                })
    elif eff_folder == "craft":
        if not re.compile(r"(?im)^##\s+.*(?:一句话公式|TL;DR)").search(body):
            violations.append({
                "code": "missing_card_section",
                "section": "tldr_formula_section",
                "detail": "Craft card is missing TL;DR formula section",
            })

    if str(meta.get("auto_sync_design_studio") or "").strip().lower() in {"true", "1", "yes"}:
        violations.append({
            "code": "design_studio_auto_sync_violation",
            "detail": "auto_sync_design_studio=true violates knowledge-vault -> design-studio isolation boundary",
        })
    for line_no, line in enumerate(raw_md.splitlines(), 1):
        for raw_clause in _KD_CLAUSE_SPLIT_RE.split(line):
            clause = raw_clause.strip()
            if not clause:
                continue
            m_sync = _KD_AUTO_DESIGN_SYNC_RE.search(clause)
            if m_sync:
                neg_window = clause[max(0, m_sync.start() - 24):m_sync.end()]
                if not _KD_NEGATION_RE.search(neg_window):
                    violations.append({
                        "code": "design_studio_auto_sync_violation",
                        "line": str(line_no),
                        "detail": "Card claims automatic sync to design-studio without explicit Owner promotion gate",
                    })

    rel_path = f"{eff_folder}/{resolved.name}"
    eff_vault = vault_dir.expanduser().resolve() if vault_dir is not None else None
    if eff_vault is None and resolved.parent.name in {"concepts", "craft", "thoughts", "raw"}:
        eff_vault = resolved.parent.parent
    indexed = False
    if eff_vault is not None and (eff_vault / "index.md").is_file():
        idx_text = (eff_vault / "index.md").read_text(encoding="utf-8", errors="ignore")
        indexed = f"({rel_path})" in idx_text
    if require_index and not indexed:
        violations.append({
            "code": "missing_index_registration",
            "rel_path": rel_path,
            "detail": f"Card '{rel_path}' is not registered in index.md",
        })

    return {
        "ok": len(violations) == 0,
        "file": str(resolved),
        "folder": eff_folder,
        "rel_path": rel_path,
        "indexed": indexed,
        "violations": violations,
    }
