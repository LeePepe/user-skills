"""Daily Linear digest markdown, Dual-Zone rendering, and issue merge/upsert."""

from __future__ import annotations

from .common import *

_DAILY_TASK = re.compile(r"^[-*+] \[([ xX])\] (.*)$")
_DAILY_NOTICE = "候选学习仅在 Owner Gate 批准后展开；本 issue 不自动归档或发布。"
DAILY_LINEAR_FOOTER = (
    "Owner 可评论 `@bot 1`、`@bot 都做`，或将 Assignee/Delegate 改为 Bot；"
    "Bot 会展开主题、在评论中发布完整知识卡，并将 issue 重新分配给 Owner (`@ltp19941128`)。"
)


def _learning_brief(item: dict[str, Any]) -> dict[str, Any]:
    key = str(item.get("topic_key") or item.get("slug") or "").strip()
    question = str(item.get("question") or item.get("title") or "").strip()
    source = str(item.get("source_id") or item.get("source_uri") or "").strip()
    guide = item.get("learning_guide")
    if isinstance(guide, dict) and len(guide.get("preview_questions", [])) == 3:
        return {
            "why_now": str(guide.get("why_now") or item.get("why_now") or item.get("excerpt") or "").strip(),
            "preview_questions": list(guide["preview_questions"]),
            "recommended_command": str(guide.get("recommended_command") or f"/learn {key}"),
        }
    brief = _legacy_automated_learning_brief(item)
    body = re.split(r"如何|怎样|怎么|为什么|为何", question, maxsplit=1)[-1]
    body = re.sub(r"^(?:通过|用|把)", "", body).strip("？?。 ")
    parts = re.split(r"转为|转换成|区分|而不|同时|但", body, maxsplit=1)
    mechanism = parts[0][:80] or source
    constraint = parts[-1][:80] if len(parts) > 1 else str(item.get("category") or "输入/输出边界")
    evidence = str(item.get("why_now") or item.get("source_ref") or source).split("（")[0][:100]
    brief["preview_questions"] = [
        f"「{mechanism}」依赖哪些最小状态与转换规则，才能满足「{constraint}」的输出契约？",
        f"为什么「{constraint}」局部验收通过，仍可能因输入约束变化而让「{mechanism}」整体失效？",
        f"针对 {evidence}，如何只改变一个输入或约束做对照实验，用失败用例界定「{mechanism}」的适用范围？",
    ]
    return brief


def _legacy_automated_learning_brief(item: dict[str, Any]) -> dict[str, Any]:
    key = str(item.get("topic_key") or item.get("slug") or "").strip()
    question = str(item.get("question") or item.get("title") or "").strip()
    source = str(item.get("source_id") or item.get("source_uri") or "").strip()
    why_now = str(item.get("why_now") or item.get("excerpt") or "").strip()
    return {
        "why_now": f"{why_now} 来源 {source}，待核对缺口是：{question}。",
        "preview_questions": [
            f"针对 {source}，{question} 的底层机制与输入/输出边界是什么？",
            f"为什么 {source} 的实现会在“{question}”这个场景出现反直觉结果？",
            f"如何用一个最小工程实验验证 {key} 的关键假设，并判断结果是否可迁移？",
        ],
        "recommended_command": f"/deep-research {key}" if item.get("source_kind") == "git_tech_stack" else f"/learn {key}",
    }


def _daily_line_keys(line: str) -> set[str]:
    keys = set(re.findall(r"learning-topic://([a-z0-9]+(?:-[a-z0-9]+)*)", line))
    if _DAILY_TASK.match(line) or re.match(r"^#{1,6} ", line):
        keys.update(re.findall(r"\(`([a-z0-9]+(?:-[a-z0-9]+)*)`\)\s*$", line))
    return keys


def _daily_description_keys(description: str) -> set[str]:
    structured = set().union(*(
        _daily_line_keys(line)
        for line in description.splitlines()
        if _DAILY_TASK.match(line) or re.match(r"^#{1,6} ", line)
    ))
    structured.update(re.findall(r"<!-- KD-TOPIC-START:([a-z0-9]+(?:-[a-z0-9]+)*) -->", description))
    return structured or set(re.findall(r"learning-topic://([a-z0-9]+(?:-[a-z0-9]+)*)", description))


def _daily_brief_lines(brief: dict[str, Any]) -> list[str]:
    return [
        f"  - 为什么现在值得学：{brief['why_now']}",
        "  - 3 个核心痛点追问预览：",
        *[f"    1. {q}" for q in brief["preview_questions"]],
        f"  - 推荐展开命令：`{brief['recommended_command']}`",
    ]


def _daily_topic_lines(item: dict[str, Any], *, with_brief: bool = True) -> list[str]:
    key = item["topic_key"]
    return [
        f"<!-- KD-TOPIC-START:{key} -->",
        f"- [ ] [{key}](learning-topic://{key}) {item['title']}",
        *(_daily_brief_lines(item["learning_brief"]) if with_brief else []),
        f"<!-- KD-TOPIC-END:{key} -->",
        "",
    ]


def _load_issue_brief_module() -> Any:
    path = SKILLS_ROOT / "issue-brief" / "scripts" / "verify_issue_format.py"
    mod = _load_helper_module("knowledge_digest_issue_brief", path)
    if mod is None:
        raise RuntimeError(f"Unable to load verify_issue_format from {path}")
    return mod


def _short_plain_why(item: dict[str, Any]) -> str:
    raw = str(item.get("why_now") or item.get("excerpt") or "").strip()
    cleaned = re.sub(r"[A-Za-z0-9_./-]+:\d+(?:-\d+)?", "", raw)
    cleaned = re.sub(r"(?:repos|collected-tools|knowledge-vault|skills)/[^\s（(]+", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"^[（(](.+)[）)]$", r"\1", cleaned.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ·—-，。;；()（）")
    if not cleaned:
        track = infer_topic_track(item)
        return (
            "补齐个人工作流、技能设计与多角色编排的确定性门禁缺口"
            if track == "workflow"
            else "补齐个人理财、运动健康或原生端交互质感的底层认知缺口"
        )
    return cleaned[:48].rstrip("，,；;（( ")


def render_dual_zone_digest_issue(day: str, topics: list[dict[str, Any]]) -> str:
    """Render a Dual-Zone compliant Daily Learn issue body grouped into Two Tracks via `issue-brief`."""
    issue_brief = _load_issue_brief_module()
    count = len(topics)
    tldr = (
        f"今日按两大板块筛出 {count} 个高价值学习主题，勾选对应项或留言即可展开拆解。"
        if count
        else "今日暂无新增高价值学习主题，候选池保持清零或冷却状态。"
    )
    items: list[dict[str, Any]] = []
    context_blocks: list[str] = [_DAILY_NOTICE, ""]

    workflow_topics = [t for t in topics if infer_topic_track(t) == "workflow"]
    personal_topics = [t for t in topics if infer_topic_track(t) == "personal"]

    for track_id, group_topics in (("workflow", workflow_topics), ("personal", personal_topics)):
        if not group_topics:
            continue
        items.append({"group_header": TRACK_SECTION_HEADERS[track_id]})
        for item in group_topics:
            key = str(item["topic_key"])
            title = str(item.get("title") or key)
            brief = item.get("learning_brief") or _learning_brief(item)
            items.append({
                "what": f"[{key}](learning-topic://{key}) {title}",
                "why": _short_plain_why(item),
            })
            context_blocks.extend([
                f"<!-- KD-TOPIC-START:{key} -->",
                f"### [{TRACK_LABELS[track_id]}] {title} (`{key}`)",
                *(_daily_brief_lines(brief)),
                f"<!-- KD-TOPIC-END:{key} -->",
                "",
            ])
    if not topics:
        items.append({"what": "保持现有知识库索引", "why": "今日无待审批新主题"})

    rendered = issue_brief.render_dual_zone_issue(
        tldr=tldr,
        items=items,
        cursor=f"daily-learn-{day}",
        extra_comments=[
            f"<!-- GTT-LINEAR-DEDUP:daily-learn-{day} -->",
            f"<!-- multica-knowledge-autopilot:daily-learn-{day} -->",
            f"<!-- KD-DAILY-START:{day} -->",
        ],
        footer=DAILY_LINEAR_FOOTER,
        ai_context_md="\n".join(context_blocks).strip(),
        trailing_comments=[f"<!-- KD-DAILY-END:{day} -->"],
    )
    report = issue_brief.verify_dual_zone_issue(rendered)
    if not report["ok"]:
        raise ValueError(f"Dual-Zone daily digest verification failed: {report['violations']}")
    return rendered


def _daily_digest_markdown(day: str, topics: list[dict[str, Any]], *, dual_zone: bool = False) -> str:
    if dual_zone:
        return render_dual_zone_digest_issue(day, topics)
    issue_brief = _load_issue_brief_module()
    context_lines = [
        f"- [`{infer_topic_track(item)}`] `{item['topic_key']}`: `{item['learning_brief']['recommended_command']}`"
        for item in topics
    ] or ["- `topics`: `none`"]
    ai_details_lines = [
        issue_brief.CANONICAL_DETAILS_OPEN,
        "",
        *context_lines,
        "",
        "</details>",
    ]
    lines = [
        f"<!-- GTT-LINEAR-DEDUP:daily-learn-{day} -->",
        f"<!-- multica-cursor:daily-learn-{day} -->",
        f"<!-- KD-DAILY-START:{day} -->",
        "",
        _DAILY_NOTICE,
        "",
    ]
    workflow_topics = [t for t in topics if infer_topic_track(t) == "workflow"]
    personal_topics = [t for t in topics if infer_topic_track(t) == "personal"]
    for track_id, group_topics in (("workflow", workflow_topics), ("personal", personal_topics)):
        if not group_topics:
            continue
        lines.extend([TRACK_STANDARD_HEADINGS[track_id], ""])
        for item in group_topics:
            lines.extend(_daily_topic_lines(item))
    lines.extend([DAILY_LINEAR_FOOTER, "", *ai_details_lines, ""])
    return "\n".join([*lines, f"<!-- KD-DAILY-END:{day} -->", ""])


def _daily_semantic_text(line: str) -> str:
    text = re.sub(r"^\s*(?:[-*+] |\d+[.)] )", "", line)
    text = re.sub(r"\\([\\`*{}\[\]()#+.!_>~-])", r"\1", text)
    text = re.sub(r"<([a-z][a-z0-9+.-]*://[^>]+)>", r"\1", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: match[2] if match[1] == match[2] else f"{match[1]} ({match[2]})",
        text,
    )
    return " ".join(text.strip().split())


def _automated_daily_fields(lines: list[str], start: int, end: int, item: dict[str, Any]) -> set[int] | None:
    for label in ("为什么现在值得学：", "3 个核心痛点追问预览：", "推荐展开命令："):
        if sum(_daily_semantic_text(line).startswith(label) for line in lines[start + 1:end]) != 1:
            return None
    variants = [_legacy_automated_learning_brief(item)]
    if not item.get("learning_guide"):
        variants.append(item["learning_brief"])
    for brief in variants:
        expected = [_daily_semantic_text(line) for line in _daily_brief_lines(brief)]
        matched: list[int] = []
        for index in range(start + 1, end):
            if lines[index].startswith("  ") and _daily_semantic_text(lines[index]) == expected[len(matched)]:
                matched.append(index)
                if len(matched) == len(expected):
                    return set(matched)
    return None


def _merge_daily_digest_description(existing: str, generated: str, topics: list[dict[str, Any]]) -> str:
    if not existing.strip():
        return generated
    day = re.search(r"KD-DAILY-START:(\d{4}-\d{2}-\d{2})", generated).group(1)
    marker = f"<!-- GTT-LINEAR-DEDUP:daily-learn-{day} -->"
    begin, finish = f"<!-- KD-DAILY-START:{day} -->", f"<!-- KD-DAILY-END:{day} -->"
    lines = existing.splitlines()
    bounded = begin in lines or finish in lines
    if bounded and (lines.count(begin) != 1 or lines.count(finish) != 1 or lines.index(begin) >= lines.index(finish)):
        raise ValueError("Ambiguous daily digest boundaries; refusing to alter Owner content")
    by_key = {str(item["topic_key"]): item for item in topics}
    tasks: dict[str, list[tuple[int, int, bool]]] = {key: [] for key in by_key}
    headings: dict[str, list[int]] = {key: [] for key in by_key}
    for index, line in enumerate(lines):
        task = _DAILY_TASK.match(line)
        heading = re.match(r"^#{1,6} ", line)
        if not task and not heading:
            continue
        keys = _daily_line_keys(line) & by_key.keys()
        if len(keys) > 1:
            raise ValueError("Ambiguous daily topic identity; refusing to alter Owner content")
        for key in keys:
            if heading:
                headings[key].append(index)
            else:
                end = index + 1
                while end < len(lines) and (not lines[end].strip() or lines[end].startswith("  ")):
                    end += 1
                tasks[key].append((index, end, task.group(1).lower() == "x"))
    removed: set[int] = set()
    replacements: dict[int, str] = {}
    additions: list[str] = []
    for key, item in by_key.items():
        rows = tasks[key]
        if len(headings[key]) > 1:
            raise ValueError(f"Multiple unowned learning briefs for {key}; manual reconciliation required")
        if headings[key]:
            heading_start = headings[key][0]
            heading_end = next((index for index in range(heading_start + 1, len(lines)) if re.match(r"^#{1,6} ", lines[index])), len(lines))
            body = lines[heading_start + 1:heading_end]
            if not any("为什么现在值得学" in line or "Why Now" in line for line in body) or not any("推荐展开命令" in line or "Recommended Command" in line for line in body):
                raise ValueError(f"Unrecognized learning guide for {key}; manual reconciliation required")
        row_fields = {start: any("为什么现在值得学：" in line or "3 个核心痛点追问预览：" in line for line in lines[start + 1:end]) for start, end, _ in rows}
        keeper = next((start for start, _, _ in rows if row_fields[start] and not headings[key]), rows[0][0] if rows else None)
        has_brief = bool(headings[key])
        for start, end, _ in rows:
            has_fields = row_fields[start]
            if has_fields and has_brief:
                fields = _automated_daily_fields(lines, start, end, item)
                if fields is None:
                    raise ValueError(f"Cannot safely remove an edited/unowned duplicate brief for {key}")
                removed.update(fields)
            elif has_fields:
                has_brief = True
            if start != keeper:
                label = _DAILY_TASK.match(lines[start]).group(2)
                generated_label = f"[{key}](learning-topic://{key}) {item['title']}"
                if _daily_semantic_text(label) == _daily_semantic_text(generated_label):
                    removed.add(start)
                else:
                    replacements[start] = label
        if keeper is not None and any(done for _, _, done in rows):
            replacements[keeper] = re.sub(r"\[[ xX]\]", "[x]", lines[keeper], count=1)
        if keeper is None:
            additions.extend(_daily_topic_lines(item, with_brief=not has_brief))
        elif not has_brief:
            _, end, _ = next(row for row in rows if row[0] == keeper)
            if any(line.strip() for line in lines[keeper + 1:end]):
                raise ValueError(f"Unrecognized learning guide for {key}; manual reconciliation required")
            replacements[keeper] = replacements.get(keeper, lines[keeper]) + "\n" + "\n".join(_daily_brief_lines(item["learning_brief"]))
    if not bounded:
        removed.update(index for index, line in enumerate(lines) if line == marker)
    result = [replacements.get(index, line) for index, line in enumerate(lines) if index not in removed]
    if bounded:
        if additions:
            result[result.index(finish):result.index(finish)] = additions
    else:
        result = [marker, begin, *result, *additions, finish]
    merged = "\n".join(result)
    return existing if merged == existing.rstrip("\n") else merged.rstrip() + "\n"


def _linear_transport_from_repo() -> Any:
    path = SKILLS_ROOT / "team-lead" / "scripts" / "linear-sync.py"
    mod = _load_helper_module("knowledge_digest_linear_sync", path)
    if mod is None or not getattr(mod, "load_api_key", None):
        raise RuntimeError("Linear transport is unavailable")
    token = mod.load_api_key()
    if not token:
        raise RuntimeError("Linear credentials are not configured; use the existing team-lead credential setup")
    client_cls = getattr(mod, "GraphQLClient", None)
    if client_cls is None:
        raise RuntimeError("Linear transport does not expose GraphQLClient")
    return client_cls(token)


def _upsert_linear_daily_issue(
    client: Any,
    day: str,
    topics: list[dict[str, Any]],
    *,
    existing_issue: dict[str, Any] | None = None,
    dual_zone: bool = False,
) -> dict[str, Any]:
    title = f"[Daily Learn {day}] 今日 3 个推荐学习主题"
    marker = f"GTT-LINEAR-DEDUP:daily-learn-{day}"
    issue = existing_issue
    if issue is None:
        issues = client.list_issues("TIA", query=title, limit=50)
        issue = next((x for x in issues if marker in str(x.get("description") or "") or x.get("title") == title), None)
    description = _daily_digest_markdown(day, topics, dual_zone=dual_zone)
    if issue:
        existing = str(issue.get("description") or "")
        desired = description
        description = _merge_daily_digest_description(existing, desired, topics)
        if DAILY_LINEAR_FOOTER not in description:
            description = description.rstrip() + "\n\n" + DAILY_LINEAR_FOOTER + "\n"
        assignee = issue.get("assignee")
        assignee_id = assignee.get("id") if isinstance(assignee, dict) else None
        bot_id = "6105e16e-700a-4ea5-8283-67aec10daf20"
        delegated_to_bot = (issue.get("delegate") or {}).get("id") == bot_id
        needs_owner = (
            "assignee" in issue
            and not delegated_to_bot
            and assignee_id not in {bot_id, "0680d5e4-720a-4724-a1d4-b9660c0d1495"}
        )
        if description == existing and title == issue.get("title") and not needs_owner:
            return issue
        kwargs = {"title": title, "description": description}
        if needs_owner:
            kwargs["assignee_id"] = "0680d5e4-720a-4724-a1d4-b9660c0d1495"
        return client.update_issue(issue.get("id") or issue.get("identifier"), **kwargs)
    team = client.team_metadata("TIA")
    state_id = next((s.get("id") for s in team.get("states", {}).get("nodes", []) if s.get("type") == "backlog"), None)
    return client.create_issue(
        team_id=team["id"],
        title=title,
        description=description,
        state_id=state_id,
        assignee_id="0680d5e4-720a-4724-a1d4-b9660c0d1495",
    )


def _update_proposal_external_ids(proposals_path: Path, selected: list[dict[str, Any]], external_id: str) -> None:
    if not proposals_path.is_file():
        return
    keys = {str(item.get("topic_key")) for item in selected}
    lines = proposals_path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    for record in records:
        if record.get("topic_key") in keys:
            record["external_id"] = external_id
    proposals_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
