from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .shared import *

def build_root_markdown(
    root: Mapping[str, Any],
    snapshot_root: Mapping[str, Any] | None = None,
    confirm_teams_timeline: bool = False,
    workstream_links: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """Render a Work [Root] issue in a strict three-layer layout."""
    name = str(root.get("name") or root.get("root_id") or "Root")
    root_id = str(root.get("root_id") or _slug(name) or "root")
    description = " ".join(str(root.get("description") or "").split())
    snap: Mapping[str, Any] = snapshot_root if isinstance(snapshot_root, Mapping) else {}
    workstreams = snap.get("workstreams") if isinstance(snap.get("workstreams"), Mapping) else {}
    ws_links: dict[str, dict[str, Any]] = {}
    raw_ws_links = workstream_links or (snap.get("workstream_links") if isinstance(snap.get("workstream_links"), Mapping) else {}) or {}
    for stream in WORKSTREAMS:
        stream_data = workstreams.get(stream) if isinstance(workstreams.get(stream), Mapping) else {}
        lin = (raw_ws_links.get(stream) if isinstance(raw_ws_links, Mapping) else None) or stream_data.get("linear") or {}
        if isinstance(lin, Mapping):
            ws_links[stream] = {
                "id": str(lin.get("id") or ""),
                "identifier": str(lin.get("identifier") or ""),
                "url": str(lin.get("url") or ""),
            }
        else:
            ws_links[stream] = {"id": "", "identifier": "", "url": ""}

    pr_items = [
        item for item in (workstreams.get("PR & PoP") or {}).get("items", [])
        if isinstance(item, Mapping) and item.get("type") == "pr"
    ]
    decision_items = [
        item for item in (workstreams.get("PR & PoP") or {}).get("items", [])
        if isinstance(item, Mapping) and item.get("type") == "decision"
    ]
    if not decision_items:
        decision_items = [
            item for item in pr_items
            if str(item.get("signal") or "") == "P0_BLOCKING_DECISION"
        ]

    def signal_count(predicate) -> int:
        return sum(1 for item in pr_items if predicate(item))

    merged = signal_count(
        lambda item: str(item.get("state") or "").upper() != "CLOSED"
        and (
            str(item.get("signal") or "").upper() == "MERGED"
            or str(item.get("state") or "").upper() == "MERGED"
        )
    )
    pop = signal_count(
        lambda item: str(item.get("signal") or "") == "WAIT_POP_OR_MERGE"
        and str(item.get("state") or "").upper() not in {"MERGED", "CLOSED"}
    )
    on_hold = signal_count(
        lambda item: str(item.get("signal") or "").upper() == "ON_HOLD"
        and str(item.get("state") or "").upper() not in {"MERGED", "CLOSED"}
    )
    auto = signal_count(
        lambda item: (
            "AUTO_HEAL" in str(item.get("classification") or item.get("signal") or "")
            or str(item.get("signal") or "").startswith("S")
        )
        and str(item.get("state") or "").upper() not in {"MERGED", "CLOSED"}
    )
    waiting = signal_count(
        lambda item: str(item.get("signal") or "") == "OBSERVE"
        and str(item.get("state") or "").upper() not in {"MERGED", "CLOSED"}
    )
    in_flight_total = pop + on_hold + auto + waiting

    bugs = [
        item for item in (workstreams.get("Bugs & Devsync") or {}).get("items", [])
        if isinstance(item, Mapping) and item.get("type") == "bug"
    ]

    def bug_bucket(item: Mapping[str, Any]) -> str:
        st = str(item.get("status") or "").casefold()
        if "remote config" in st:
            return "Remote Config"
        if "by design" in st or "not fixing" in st or "cannot reproduce" in st:
            return "By Design & Not Fixing"
        if "external" in st:
            return "External Non-Blocker"
        if "blocking" in st or "on hold" in st:
            return "Blocking"
        return "Fixed" if "fixed" in st or "resolved" in st else "Blocking"

    bug_counts = Counter(bug_bucket(item) for item in bugs)
    ship_blocker_open = [
        item for item in bugs
        if item.get("ship_blocker")
        and str(item.get("ado_state") or "") not in {"Resolved", "Closed"}
        and bug_bucket(item) in {"Blocking", "Remote Config"}
    ]
    non_pr_bugs = [
        b for b in bugs
        if str(b.get("ado_state") or "") not in {"Resolved", "Closed"}
        and not str(b.get("status") or "").startswith("RESOLVED")
        and not (
            b.get("pr_number")
            and not b.get("pr_closed")
            and "ON HOLD" not in str(b.get("raw_status") or b.get("status") or "")
        )
    ]
    unassigned_non_pr_count = sum(1 for b in non_pr_bugs if not str(b.get("assigned_to") or "").strip())
    sinks = len((workstreams.get("Repo & Skill Sinks") or {}).get("items", []))
    failing = [
        str(item.get("pr_number"))
        for item in pr_items
        if ("S3" in str(item.get("signal") or "") or "CI_FAILURE" in str(item.get("classification") or ""))
        and item.get("pr_number")
    ]
    on_hold_prs = [
        str(item.get("pr_number"))
        for item in pr_items
        if str(item.get("signal") or "").upper() == "ON_HOLD" and item.get("pr_number")
    ]
    pop_prs = [
        str(item.get("pr_number"))
        for item in pr_items
        if str(item.get("signal") or "") == "WAIT_POP_OR_MERGE" and item.get("pr_number")
    ]
    waiting_prs = [
        str(item.get("pr_number"))
        for item in pr_items
        if str(item.get("signal") or "") == "OBSERVE"
        and str(item.get("state") or "").upper() not in {"MERGED", "CLOSED"}
        and item.get("pr_number")
    ]
    blockers = [f"PoP PR #{pr}" for pr in pop_prs]
    blockers += [str(item.get("id") or "阻塞决策") for item in decision_items]

    timeline = snap.get("teams_timeline") or root.get("teams_timeline")
    has_tl = _timeline_is_confirmed(timeline, confirm_teams_timeline) and isinstance(timeline, Mapping)
    ver = str(timeline.get("version") or "CN App").strip() if has_tl else name
    tdate = str(timeline.get("target_date") or "TBD").strip() if has_tl else "TBD"
    int_branch = ", ".join(f"`{b}`" for b in (root.get("integration_branches") or [])[:1]) or "`main`"

    pop_links = ", ".join(_md_link(f"#{pr}", f"https://msft.ghe.com/san/bing-growth-sapphire-app/pull/{pr}") for pr in pop_prs[:6])
    failing_links = ", ".join(_md_link(f"#{pr}", f"https://msft.ghe.com/san/bing-growth-sapphire-app/pull/{pr}") for pr in failing[:6]) if failing else "无"
    on_hold_links = ", ".join(_md_link(f"#{pr}", f"https://msft.ghe.com/san/bing-growth-sapphire-app/pull/{pr}") for pr in on_hold_prs[:4]) if on_hold_prs else "无"
    waiting_links = ", ".join(_md_link(f"#{pr}", f"https://msft.ghe.com/san/bing-growth-sapphire-app/pull/{pr}") for pr in waiting_prs[:8]) if waiting_prs else "无"

    sb_count = len(ship_blocker_open) or bug_counts["Blocking"]
    ext_count = bug_counts["External Non-Blocker"]
    non_pr_total = len(non_pr_bugs)
    non_pr_is_relevant = root_id == "china-release" or non_pr_total > 0 or bug_counts["Blocking"] > 0

    hold_suffix = f" · ON HOLD **{on_hold}**" if on_hold else ""
    if has_tl:
        tldr_status = (
            f"当前推进 **{ver}**（目标发版日 **{tdate}**），代码侧已合入 **{merged}** 个 PR、"
            f"在途 **{in_flight_total}** 个 PR（待催审 **{waiting}** · 必需 CI 待恢复/重跑 **{auto}**{hold_suffix} · 待点合入 **{pop}**）。"
        )
    else:
        lead = description.rstrip(".") if description else f"{name} 业务根追踪"
        tldr_status = (
            f"{lead} — 代码侧已合入 **{merged}** 个 PR、在途 **{in_flight_total}** 个 PR"
            f"（待催审 **{waiting}** · 必需 CI 待恢复/重跑 **{auto}**{hold_suffix} · 待点合入 **{pop}**）。"
        )

    if pop > 0 or decision_items:
        tldr_blocker = (
            f"当前最大卡点为 **{pop}** 个已就绪 PR 待你点 PoP 合入、**{len(decision_items)}** 项阻塞方案待你拍板"
            + (f"，另有 **{non_pr_total or bug_counts['Blocking']}** 条非 PR/阻塞 Bug 待推进。" if non_pr_is_relevant else "；当前无非 PR 可修阻塞或外部配置卡点（0 条）。")
        )
    elif non_pr_is_relevant:
        tldr_blocker = (
            f"非 PR 可修项共 **{non_pr_total or (sb_count + ext_count)}** 条（含 **{sb_count}** 条发版阻塞远端配置/服务端开关及 ON HOLD 裁决 + "
            f"**{unassigned_non_pr_count or max((non_pr_total or (sb_count + ext_count)) - sb_count, 0)}** 条 ADO 尚无 Assignee 待指派责任人/定本版取舍项）；代码侧有 **{waiting}** 个已清零评论的 PR 等待催审。"
        )
    elif waiting > 0 or auto > 0:
        tldr_blocker = f"当前无非 PR 可修阻塞或外部配置卡点（0 条）；主要关注 **{waiting}** 个待审 PR 审批与 **{auto}** 个在途 PR CI 状态。"
    else:
        tldr_blocker = "当前无非 PR 可修阻塞或外部配置卡点（0 条），在途 PR 与阻塞项均已清零。"

    tldr_autopilot = (
        f"必需 CI 失败/取消待恢复/重跑 **{auto}** 项"
        + (f"，ON HOLD 待裁决 **{on_hold}** 项" if on_hold else "")
        + f"，并已沉淀 **{sinks}** 项高价值工程与评测规则。"
    )

    next_actions: list[str] = []
    if pop > 0:
        next_actions.append(
            f"- [ ] **[点 PoP 合入]** 完成 **{pop}** 个已就绪 PR 的 PoP 合入（{pop_links or '详见子单 PR & PoP'}）"
        )
    if decision_items:
        dec_links = ", ".join(
            _md_link(str(item.get("id") or "决策单"), item.get("url"))
            for item in decision_items[:4]
        )
        next_actions.append(
            f"- [ ] **[拍板阻塞决策]** 回复 **{len(decision_items)}** 项待决方案（{dec_links or '详见子单 PR & PoP'}）"
        )
    if waiting > 0:
        next_actions.append(
            f"- [ ] **[催审在途 PR]** 推动 Reviewer 审批评论已清零的 **{waiting}** 个在途 PR（{waiting_links}，详见子单 PR & PoP）"
        )
    if failing:
        next_actions.append(
            f"- [ ] **[恢复/重跑必需 CI]** 跟进或重跑 **{len(failing)}** 个必需检查失败/取消的 PR（{failing_links}，当前无活跃自愈进程，详见子单 PR & PoP）"
        )
    if on_hold > 0:
        next_actions.append(
            f"- [ ] **[裁决 ON HOLD 挂起项]** 对 {on_hold_links} 二选一拍板：摘除 Ship Blocker 本版豁免 vs 解除 Hold 推进合入（详见子单 PR & PoP / Bugs & Devsync）"
        )
    if non_pr_is_relevant and (sb_count > 0 or non_pr_total > 0):
        next_actions.append(
            f"- [ ] **[推进远端配置与待指派外部依赖]** 推动 **{sb_count}** 条发版阻塞配置/服务端开关收口，并为 **{unassigned_non_pr_count or max(non_pr_total - sb_count, 0)}** 条尚无 Assignee 的外部卡点指派责任人或定本版取舍（详见子单 Bugs & Devsync）"
        )
    if has_tl:
        ms_list = [m for m in (timeline.get("milestones") or []) if isinstance(m, Mapping)]
        qa_ms = next((m for m in ms_list if "qa" in str(m.get("name") or "").casefold() and str(m.get("status") or "").casefold() != "completed"), None)
        rel_ms = next((m for m in reversed(ms_list) if str(m.get("status") or "").casefold() != "completed"), None)
        if qa_ms:
            qlink = f" ([Teams]({qa_ms.get('deep_link')}))" if qa_ms.get("deep_link") else ""
            next_actions.append(f"- [ ] **[QA 验收跟进 ({qa_ms.get('date', '11-18')})]** {qa_ms.get('summary') or qa_ms.get('name')}{qlink}")
        if rel_ms and rel_ms is not qa_ms:
            rlink = f" ([Teams]({rel_ms.get('deep_link')}))" if rel_ms.get("deep_link") else ""
            next_actions.append(f"- [ ] **[上架提审准备 ({rel_ms.get('date', tdate)})]** {rel_ms.get('summary') or rel_ms.get('name')}{rlink}")
    if not next_actions:
        next_actions.append("- 当前无需你立即操作")

    def _ws_badge(stream: str) -> str:
        info = ws_links.get(stream) or {}
        ident = str(info.get("identifier") or "").strip()
        url = str(info.get("url") or "").strip()
        if ident and url:
            return f" ({_md_link(ident, url)})"
        if ident:
            return f" (`{ident}`)"
        return ""

    pr_todo_parts: list[str] = []
    if pop > 0 and pop_links:
        pr_todo_parts.append(f"待点合入: {pop_links}")
    if waiting > 0 and waiting_links != "无":
        pr_todo_parts.append(f"待催审: {waiting_links}")
    if failing:
        pr_todo_parts.append(f"必需 CI 待恢复/重跑: {failing_links}")
    if on_hold > 0 and on_hold_links != "无":
        pr_todo_parts.append(f"ON HOLD 待裁决: {on_hold_links}")
    pr_todo_cell = " · ".join(pr_todo_parts) if pr_todo_parts else "当前无在途阻塞 PR"
    hold_col = f" · ON HOLD `{on_hold}`" if on_hold else ""
    ext_col = f" · 外部待指派 `{bug_counts['External Non-Blocker']}`" if bug_counts["External Non-Blocker"] else ""

    lines: list[str] = [
        "## ⚡ TL;DR & 下一步需要你做的",
        f"- **TL;DR（核心结论）**：{tldr_status}",
        f"- **TL;DR（最大卡点）**：{tldr_blocker}",
        f"- **TL;DR（CI 与规则沉淀）**：{tldr_autopilot}",
        "### 👇 下一步做的（Next Actions）",
        *next_actions,
        "",
        "## 📋 总体信息",
        "### 📊 三大工作流进度 (Milestone Readiness Scoreboard)",
        "| 维度 | 当前进度 | 当前阻塞 / 待办 |",
        "| --- | --- | --- |",
        f"| **🔀 PR & PoP**{_ws_badge('PR & PoP')} | 待合入 PoP `{pop}` · 待拍板 `{len(decision_items)}` · 必需 CI 待恢复 `{auto}`{hold_col} · 待审 `{waiting}` · 已合入 `{merged}` | {pr_todo_cell} |",
        f"| **🐛 Bugs & Devsync**{_ws_badge('Bugs & Devsync')} | 已修 `{bug_counts['Fixed']}` · 远端配置 `{bug_counts['Remote Config']}` · 设计如此/不修 `{bug_counts['By Design & Not Fixing']}`{ext_col} · 阻塞/挂起 `{bug_counts['Blocking']}` | 阻塞 Bug: **{bug_counts['Blocking']}**（非 PR 卡点 `{non_pr_total}` 条） |",
        f"| **🧬 Repo & Skill Sinks**{_ws_badge('Repo & Skill Sinks')} | 高价值规则沉淀候选 `{sinks}` 项 | 待处理 PoP/决策: {', '.join(blockers) if blockers else '无'} |",
    ]

    if has_tl:
        ms_list = [m for m in (timeline.get("milestones") or []) if isinstance(m, Mapping)]
        lines += [
            "",
            "### 🗓️ 发版与 QA 关键时间线 (Release Timeline & Teams Context)",
            f"- **版本与目标日**：`{ver}` · **🎯 Target Release**：**`{tdate}`** · **集成分支**：{int_branch}",
        ]
        for ms in ms_list[:8]:
            mdate = str(ms.get("date") or "")[5:] if len(str(ms.get("date") or "")) >= 10 else str(ms.get("date") or "")
            status_norm = str(ms.get("status") or "").casefold()
            chk = "x" if status_norm == "completed" else " "
            dlink = f" ([Teams]({ms.get('deep_link')}))" if ms.get("deep_link") else ""
            summary_txt = str(ms.get("summary") or "").strip()
            detail_part = f" — {summary_txt}" if summary_txt else ""
            lines.append(f"- [{chk}] `{mdate or 'TBD'}` **{ms.get('name')}**{detail_part}{dlink}")
        ctx_items = [c for c in (timeline.get("key_context") or []) if isinstance(c, Mapping)]
        for ctx in ctx_items[:3]:
            cdate = str(ctx.get("date") or "")[5:] if len(str(ctx.get("date") or "")) >= 10 else str(ctx.get("date") or "")
            cowner = str(ctx.get("owner") or "").split("/")[0].strip()
            csum = str(ctx.get("summary") or ctx.get("text") or "").strip()
            curl = ctx.get("deep_link") or ctx.get("url")
            clink = f" ([Teams]({curl}))" if curl else ""
            prefix = f"**`{cdate}` ({cowner})**" if cdate and cowner else (f"**`{cdate or cowner}`**" if (cdate or cowner) else "**Teams 要点**")
            lines.append(f"- {prefix}：{csum}{clink}")
        top_links = [
            _md_link(d.get("title") or "Teams thread", d.get("url") or d.get("deep_link"))
            for d in (timeline.get("deep_links") or [])
            if isinstance(d, Mapping) and (d.get("url") or d.get("deep_link"))
        ]
        if top_links:
            lines.append(f"- **🔗 核心讨论深链**：{' · '.join(top_links[:4])}")

    sub_issue_bindings = [
        f"{stream}={ws_links[stream]['identifier'] or 'unbound'}"
        for stream in WORKSTREAMS
    ]
    pr_sha_entries = [
        f"PR#{item.get('pr_number') or item.get('id')}@{str(item.get('head_sha') or item.get('headRefOid') or 'none')[:9]}:{item.get('signal') or 'OBSERVE'}"
        for item in pr_items
    ]
    signal_sources = {
        "repos": list(root.get("repos") or []),
        "integration_branches": list(root.get("integration_branches") or []),
        "handoff_files": list(root.get("handoff_files") or []),
        "bug_list_paths": list(root.get("bug_list_paths") or []),
    }
    meta_payload = {
        "pr_heads": [
            {
                "head_sha": str(item.get("head_sha") or item.get("headRefOid") or ""),
                "pr": item.get("pr_number") or item.get("id"),
                "signal": str(item.get("signal") or ""),
                "state": str(item.get("state") or "OPEN"),
            }
            for item in pr_items
        ],
        "root_id": root_id,
        "signal_sources": signal_sources,
        "spec_sha": "prt-spec-v2-3layer",
        "workstream_issues": {
            stream: {
                "id": ws_links[stream]["id"],
                "identifier": ws_links[stream]["identifier"],
            }
            for stream in WORKSTREAMS
        },
    }
    lines += [
        "",
        "<details><summary>🤖 AI 知道的信息</summary>",
        "",
        f"- `root_id`: `{root_id}`",
        f"- `sub_issues`: {', '.join(sub_issue_bindings)} (three sub-issues)",
        f"- `pr_sha_manifest`: {', '.join(pr_sha_entries) if pr_sha_entries else 'none'}",
        f"- `signal_sources`: repos={','.join(signal_sources['repos']) or 'none'} · branches={','.join(signal_sources['integration_branches']) or 'none'}",
        "",
        "<!-- Current Milestone Blockers: Details: see the three sub-issues -->",
        "<!-- ROOT-META:v1 " + json.dumps(meta_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + " -->",
        manifest_block(root),
        "</details>",
    ]
    return "\n".join(line for line in lines if line is not None).strip()


def _workstream_title(root_name: str, stream: str) -> str:
    if stream == "PR & PoP":
        return f"[{root_name}] PR 进度与待办"
    suffixes = {
        "Bugs & Devsync": "非 PR 可修卡点、远端配置与缺失信息清单",
        "Repo & Skill Sinks": "评测提取 (≥0.70) 与规则沉淀候选 Link",
    }
    return f"[{root_name} · {stream}] {suffixes[stream]}"


def _md_link(label: Any, url: Any) -> str:
    text = str(label or "").replace("|", "\\|").replace("\n", " ")
    return f"[{text}]({url})" if url else f"`{text}`"


def _item_pr_url(item: Mapping[str, Any]) -> str:
    url = item.get("url") or item.get("pr_url")
    if url:
        return str(url)
    number = item.get("pr_number")
    return f"https://msft.ghe.com/san/bing-growth-sapphire-app/pull/{number}" if number else ""


def _pending_entry(item: Mapping[str, Any], kind: str) -> Mapping[str, Any] | None:
    entries = [
        entry for entry in item.get("pending_decisions", [])
        if isinstance(entry, Mapping) and entry.get("kind") == kind
    ]
    return entries[0] if entries else None


def _decision_identifier(item: Mapping[str, Any], kind: str | None = None) -> str:
    entry = _pending_entry(item, kind) if kind else None
    if entry:
        return str(entry.get("decision_id") or entry.get("linear_identifier") or "")
    for value in item.get("pending_decisions", []) or []:
        if isinstance(value, Mapping):
            identifier = value.get("decision_id") or value.get("linear_identifier")
            if identifier:
                return str(identifier)
    return str(item.get("decision_id") or "")


def _pr_state_value(item: Mapping[str, Any], key: str, default: Any = "") -> Any:
    value = item.get(key)
    if value in (None, ""):
        aliases = {"head_sha": "headRefOid", "unresolved": "unresolvedCount"}
        value = item.get(aliases.get(key, key), default)
    return default if value is None else value


def _unresolved_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _human_pr_status(signal: str, item: Mapping[str, Any] | None = None) -> str:
    code = signal.upper()
    auto_active = bool((item or {}).get("auto_healing_active"))
    if code == "ON_HOLD" or "ON_HOLD" in code:
        return "ON HOLD（待裁决本版豁免或解封）"
    if "S1" in code or "CONFLICT" in code:
        return "冲突自动处理中" if auto_active else "存在合并冲突（待处理）"
    if "S2" in code or "COMMENT" in code:
        return "评论自动修复中" if auto_active else "存在未决评论（待处理）"
    if "S3" in code or "CI_FAILURE" in code or "CI" in code and "FAIL" in code:
        return "CI 自动修复中" if auto_active else "必需 CI 失败/取消（待恢复/重跑）"
    return "评论已清零，等 Review"


def _agent_pr_state(
    root: Mapping[str, Any],
    pops: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    in_flight: Sequence[Mapping[str, Any]],
    merged: Sequence[str],
) -> str:
    def pr_value(item: Mapping[str, Any]) -> int | str:
        value = item.get("pr_number") or item.get("number") or item.get("id") or ""
        try:
            return int(value)
        except (TypeError, ValueError):
            return str(value)

    def decision_value(item: Mapping[str, Any], kind: str | None = None) -> str:
        return _decision_identifier(item, kind)

    payload = {
        "decisions": [
            {
                "pr": pr_value(item),
                "decision_id": decision_value(item, "owner_decision"),
                "head_sha": str(_pr_state_value(item, "head_sha", "") or ""),
            }
            for item in decisions
        ],
        "in_flight": [
            {
                "pr": pr_value(item),
                "head_sha": str(_pr_state_value(item, "head_sha", "") or ""),
                "stage_code": str(item.get("signal") or item.get("classification") or "OBSERVE"),
                "decision_id": decision_value(item),
                "unresolved": _unresolved_count(_pr_state_value(item, "unresolved", 0)),
                "is_draft": bool(item.get("isDraft", item.get("is_draft", False))),
            }
            for item in in_flight
        ],
        "merged": [
            int(value[1:]) if str(value).startswith("#") and str(value)[1:].isdigit() else (int(value) if str(value).isdigit() else str(value))
            for value in merged
        ],
        "pop_ready": [
            {
                "pr": pr_value(item),
                "head_sha": str(_pr_state_value(item, "head_sha", "") or ""),
                "decision_id": decision_value(item, "owner_action"),
            }
            for item in pops
        ],
        "root_id": str(root.get("root_id") or ""),
    }
    return "<!-- AGENT-PR-STATE:v1 " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + " -->"


def _workstream_markdown(root: Mapping[str, Any], stream: str, items: Sequence[Mapping[str, Any]]) -> str:
    rows = [dict(item) for item in items if isinstance(item, Mapping)]
    lines: list[str] = []
    if stream == "PR & PoP":
        pops = [
            item for item in rows
            if item.get("type") == "pr"
            and str(item.get("state") or "").upper() not in {"MERGED", "CLOSED"}
            and (
                item.get("signal") == "WAIT_POP_OR_MERGE"
                or any(
                    entry.get("kind") == "owner_action"
                    and str(entry.get("status") or "").casefold() == "awaiting_owner"
                    for entry in item.get("pending_decisions", [])
                    if isinstance(entry, Mapping)
                )
            )
        ]
        decisions = [item for item in rows if item.get("type") == "decision"]
        if not decisions:
            decisions = [
                item for item in rows
                if item.get("type") == "pr" and item.get("signal") == "P0_BLOCKING_DECISION"
            ]
        action_pr_nums: set[str] = set()
        for item in pops + decisions:
            pr_num = str(item.get("pr_number") or "")
            if pr_num:
                action_pr_nums.add(pr_num)

        in_flight: list[dict[str, Any]] = []
        merged_prs: list[dict[str, Any]] = []
        for item in rows:
            if item.get("type") != "pr":
                continue
            pr_num = str(item.get("pr_number") or item.get("id") or "")
            if not pr_num or pr_num in action_pr_nums:
                continue
            signal = str(item.get("signal") or item.get("classification") or "OBSERVE")
            state = str(item.get("state") or "").upper()
            if signal == "CLOSED" or (state == "CLOSED" and signal != "MERGED"):
                continue
            if signal == "MERGED" or state == "MERGED":
                merged_prs.append(item)
            elif state not in {"CLOSED", "MERGED"}:
                in_flight.append(item)
        merged_numbers = [str(item.get("pr_number") or item.get("id") or "") for item in merged_prs]
        action_count = len(pops) + len(decisions)
        on_hold_items = [
            item for item in in_flight
            if str(item.get("signal") or item.get("classification") or "").upper() == "ON_HOLD"
        ]
        ci_attention_items = [
            item for item in in_flight
            if any(
                token in str(item.get("signal") or item.get("classification") or "").upper()
                for token in ("S1", "S2", "S3", "CI_FAILURE", "CONFLICT")
            )
            and not item.get("auto_healing_active")
        ]
        waiting_review_items = [
            item for item in in_flight
            if item not in on_hold_items and item not in ci_attention_items
        ]
        attention_count = len(ci_attention_items) + len(on_hold_items)
        if action_count:
            callout_line = (
                f"> 🔴 **待你操作 {action_count}**（PoP **{len(pops)}** · 决策 **{len(decisions)}**） · "
                f"在途 **{len(in_flight)}** · 已合入 **{len(merged_numbers)}**"
            )
        elif attention_count:
            callout_line = (
                f"> 🟡 **待你推进 / 裁决 {attention_count}**（必需 CI 待恢复/重跑 **{len(ci_attention_items)}** · "
                f"ON HOLD 待裁决 **{len(on_hold_items)}** · 待催审 **{len(waiting_review_items)}**） · "
                f"在途 **{len(in_flight)}** · 已合入 **{len(merged_numbers)}**"
            )
        else:
            callout_line = f"> 🟢 **无需你操作** · 在途 **{len(in_flight)}** · 已合入 **{len(merged_numbers)}**"
        lines.extend([
            "<!-- PRT-T3-ACTIVE-CALLOUT:v1 -->",
            callout_line,
        ])
        if action_count:
            lines.extend(["", f"### 🔴 待你操作 ({action_count})"])
            for item in pops:
                pending = _pending_entry(item, "owner_action") or {}
                pr_num = str(item.get("pr_number") or "")
                pr_link = _md_link(f"#{pr_num}", _item_pr_url(item)) if pr_num else _md_link(item.get("id"), item.get("url"))
                identifier = pending.get("linear_identifier") or item.get("id") or ""
                linear_link = _md_link(identifier, pending.get("url")) if pending.get("url") else f"`{identifier}`"
                title = str(item.get("title") or "").strip()
                lines.append(f"- **{pr_link}** ({linear_link}) — {title} — *待点 PoP 合入*")
            for item in decisions:
                pending = _pending_entry(item, "owner_decision") or {}
                identifier = pending.get("linear_identifier") or item.get("id") or ""
                linear_link = _md_link(identifier, pending.get("url")) if pending.get("url") else f"`{identifier}`"
                pr_num = str(item.get("pr_number") or "")
                pr_link = f"({_md_link(f'#{pr_num}', _item_pr_url(item))})" if pr_num else ""
                summary = str(item.get("title") or item.get("rationale") or "Owner decision required").replace("|", r"\|")
                lines.append(f"- **{linear_link}** {pr_link} — {summary} — *待拍板回复 A/B*")

        if in_flight:
            lines.extend(["", f"### ⏳ 在途 PR ({len(in_flight)})"])
            for item in in_flight:
                pr_num = str(item.get("pr_number") or item.get("id") or "")
                link = _md_link(f"#{pr_num}", _item_pr_url(item))
                title = str(item.get("title") or "").strip()
                draft_badge = " `DRAFT`" if item.get("isDraft", item.get("is_draft", False)) else ""
                resolved = [
                    str(entry.get("linear_identifier"))
                    for entry in item.get("pending_decisions", []) or []
                    if isinstance(entry, Mapping)
                    and str(entry.get("status") or "").casefold() == "completed"
                    and entry.get("linear_identifier")
                ]
                dec_badge = f" *(已修完 Resolve: {', '.join(resolved)})*" if resolved else ""
                signal = str(item.get("signal") or item.get("classification") or "OBSERVE")
                lines.append(f"- **{link}** {title}{draft_badge} — *{_human_pr_status(signal, item)}*{dec_badge}")

        if merged_numbers:
            lines.extend(["", f"### ✅ 已合入 ({len(merged_numbers)})", "", " · ".join(
                _md_link(f"#{number}", _item_pr_url(item)) for number, item in zip(merged_numbers, merged_prs)
            )])
        lines.extend(["", _agent_pr_state(root, pops, decisions, in_flight, merged_numbers)])
    elif stream == "Bugs & Devsync":
        bug_rows = [item for item in rows if item.get("type") == "bug"]
        if not bug_rows:
            bug_rows = [item for item in rows if str(item.get("id") or "").startswith("AB#")]
        seen_bug_ids: set[str] = set()
        unique_bugs: list[dict[str, Any]] = []
        for item in bug_rows:
            bid = str(item.get("id") or "")
            if bid and bid in seen_bug_ids:
                continue
            if bid:
                seen_bug_ids.add(bid)
            unique_bugs.append(item)

        def _fmt_non_pr_row(b: Mapping[str, Any]) -> str:
            bid = str(b.get("id") or "")
            num = bid.removeprefix("AB#")
            link = _md_link(bid, f"https://msasg.visualstudio.com/SAN%20SA/_workitems/edit/{num}") if bid.startswith("AB#") else f"`{bid}`"
            sb = " 🚨`Ship Blocker`" if b.get("ship_blocker") else ""
            owner_dom = str(b.get("owner_domain") or b.get("raw_status") or "外部/配置").replace("`", "").replace("|", r"\|")
            assignee = str(b.get("assigned_to") or "").strip().replace("|", r"\|")
            assignee_tag = f"👤 `{assignee}`" if assignee else "⚠️ `待指派责任人/定本版取舍`"
            title_txt = str(b.get("title") or "").replace("|", r"\|")
            res_txt = str(b.get("resolution") or "—").replace("|", r"\|")
            raw_st = str(b.get("raw_status") or b.get("status") or "").upper()
            if bid == "AB#11452501" or "ON HOLD" in raw_st:
                res_txt += " **【需二选一裁决：摘除 Ship Blocker 本版豁免 vs 解除 #566 Hold】**"
            elif not assignee:
                res_txt += " **【ADO 尚无 Assignee：待指派责任人/定本版取舍】**"
            return f"| {link}{sb} | `{owner_dom}` · {assignee_tag} | {title_txt} | {res_txt} |"

        c1_remote_config = []
        c2_missing_info_or_policy = []
        c3_external_web_h5 = []
        pr_tracked_count = 0
        ado_closed_count = 0

        for b in unique_bugs:
            ado_st = str(b.get("ado_state") or "")
            st = str(b.get("status") or "")
            raw_st = str(b.get("raw_status") or st)
            dom = str(b.get("owner_domain") or "")
            res = str(b.get("resolution") or "")
            if ado_st in {"Resolved", "Closed"} or st.startswith("RESOLVED"):
                ado_closed_count += 1
            elif b.get("pr_number") and not b.get("pr_closed") and "ON HOLD" not in raw_st:
                pr_tracked_count += 1
            elif "REMOTE CONFIG" in raw_st or "配置" in dom or "AASA" in dom:
                c1_remote_config.append(b)
            elif any(k in f"{dom} {res} {raw_st}" for k in ("短链", "Lottie", "贴图", "架构特性缺失", "合规策略", "ON HOLD")):
                c2_missing_info_or_policy.append(b)
            else:
                c3_external_web_h5.append(b)

        non_pr_total = len(c1_remote_config) + len(c2_missing_info_or_policy) + len(c3_external_web_h5)
        lines.extend([
            f"> **本单仅聚焦「非客户端 PR 可直接修复 / 缺少外部信息或配置」的 `{non_pr_total}` 条卡点**（其余已由 PR 追踪的 `{pr_tracked_count}` 条见子单 `PR & PoP`，已在 ADO 关单的 `{ado_closed_count}` 条不再重复列出）。",
            "",
            f"## 1. ⚙️ 需改远端配置 / 服务端开关 (`start-china` / `ManageApps` / `AASA`，共 {len(c1_remote_config)} 条)",
            "",
            "| Bug ID | 卡点类型 | 现象 | 为什么非 PR 可修 & 下一步怎么推 |",
            "| --- | --- | --- | --- |",
            *(_fmt_non_pr_row(b) for b in c1_remote_config),
            *(["| — | — | — | None |"] if not c1_remote_config else []),
            "",
            f"## 2. ❓ 缺少外部信息 / 资产 / 需 PM・合规拍板 (共 {len(c2_missing_info_or_policy)} 条)",
            "",
            "| Bug ID | 缺失项 / 状态 | 现象 | 缺什么信息 & 下一步找谁 |",
            "| --- | --- | --- | --- |",
            *(_fmt_non_pr_row(b) for b in c2_missing_info_or_policy),
            *(["| — | — | — | None |"] if not c2_missing_info_or_policy else []),
            "",
            f"## 3. 🌐 纯外部网页 / 远端 H5 团队职责 (非客户端代码，共 {len(c3_external_web_h5)} 条)",
            "",
            "| Bug ID | 外部责任方 | 现象 | 根因证据 & 责任团队 |",
            "| --- | --- | --- | --- |",
            *(_fmt_non_pr_row(b) for b in c3_external_web_h5),
            *(["| — | — | — | None |"] if not c3_external_web_h5 else []),
        ])
    else:
        lines.extend(["## Eval Sinks (score ≥ 0.70)", "", "| Sink | Score | PR | Landing options |", "| --- | ---: | --- | --- |"])
        for item in rows:
            score = item.get("score")
            score_text = f"{float(score):.2f}" if isinstance(score, (int, float)) else "—"
            sink_id = str(item.get("id") or "sink")
            companions = item.get("companion_prs") or []
            companion_links = []
            for companion in companions:
                number = companion.get("pr_number") or "?"
                label = f"PR #{number}"
                identifiers = companion.get("linear_identifiers") or []
                if identifiers:
                    label += f" ({', '.join(str(value) for value in identifiers)})"
                companion_links.append(_md_link(label, companion.get("url")))
            if companion_links:
                merged = all(str(companion.get("state") or "").upper() == "MERGED" for companion in companions)
                marker = "✅ **跨会话落地**" if merged else "🔗 **跨会话关联**"
                landed_note = f" {marker}: " + " · ".join(companion_links)
            else:
                landed_note = ""
            lines.append(f"| `{sink_id}` | `{score_text}` | {_md_link(item.get('pr_number') or '—', _item_pr_url(item) if item.get('pr_number') else '')} | {', '.join(f'`{option}`' for option in item.get('landing_options') or LANDING_OPTIONS)}{landed_note} |")
        if not rows:
            lines.append("| — | — | — | No current eval sink |")
    return "\n".join(lines).strip()
