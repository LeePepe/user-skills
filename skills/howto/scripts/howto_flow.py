#!/usr/bin/env python3
"""Deterministic default CLI entry for /howto (`howto_flow.py`).

Supports:
1. `--list-nodes [--group A|B|C] [--json]`: Parses `workflow-hub/references/how-to-nodes.md`
   and outputs the canonical How-To nodes (`E1..E12`, `Q1..Q3`, `I1..I7`, `G1..G2`,
   `P1..P9`, `O1..O6`) plus `TK-01..TK-16` broken-edge registry.
2. `--lookup <query> [--json]`: Deterministic keyword/node/skill matcher returning
   matching How-To nodes, target skills, guidance, and exact CLI entry commands.
3. `--scaffold-tuple <task> [--json]`: Generates a validated 5-tuple (`Inbound`,
   `Internal`, `Calls`, `PointsTo`, `Trigger & Notify`) scaffold verified by `howto_guard.py`.
4. `--graph` / `--pyramid` `[--json]`: Renders the L4–L1 4-Layer Cybernetic Pyramid (Harness Engineering)
   above the 5-Stage Macro Lifecycle Pipeline and extracted adjacency summary.
5. `--neighbors <NODE_ID> [--json]`: Renders a 1-page Local Neighborhood Card (including `cybernetic_layer`) for `<NODE_ID>`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SKILLS_ROOT = SKILL_DIR.parent
DEFAULT_NODES_DOC = SKILLS_ROOT / "workflow-hub" / "references" / "how-to-nodes.md"
GUARD_PATH = SCRIPT_DIR / "howto_guard.py"

_CYBERNETIC_PYRAMID: tuple[dict[str, Any], ...] = (
    {
        "layer": "L4",
        "title": "L4 人类掌舵层 (Human Helmsman — 4 Irreplaceable Decisions)",
        "role": "Human Helmsman",
        "responsibilities": [
            "Product Direction & Kill-Switch（定做什么与砍伪需求）",
            "Taste（原生 120Hz 预览审美选型与品味编码化）",
            "Oracle & Boundaries（定确定性验收标准与 Owned Paths，保护裁判不被写码 Agent 篡改）",
            "Evidence Acceptance（看截图/指标/R10 证据回执，而非逐行读代码 Diff）",
        ],
        "human_gate_nodes": ["I5", "G1"],
        "node_ids": [],
        "skills": [],
        "summary": "人类独掌方向/品味/裁判保护/证据终验，经 I5 拍板与 G1 Goal 门禁向 L3–L1 下达不可篡改契约",
    },
    {
        "layer": "L3",
        "title": "L3 熵减与自演进层 (Meta-Evolution — Agent Helping Agent Improve)",
        "role": "Agent Helping Agent Improve",
        "node_ids": ["P2", "P3", "P7", "P8"],
        "skills": ["agent-retro", "skill-retro", "retro-loop", "skill-contract", "repo-kit", "aidash-metrics"],
        "summary": "P2 aidata -> P3 retro-loop / agent-retro（6 层首错归因）/ skill-retro -> P7 skill-contract（3 把物理锁下沉 CLI 门禁）/ P8 repo-kit，将人类拍过的 Taste 与踩过的坑永久固化为确定性护栏",
    },
    {
        "layer": "L2",
        "title": "L2 协同编排层 (Orchestration — Agent Guiding Agent)",
        "role": "Agent Guiding Agent",
        "node_ids": ["I7", "E6", "E10", "E11"],
        "skills": ["personal-m2-assistant", "m2-dispatcher", "team-lead", "code-review"],
        "summary": "I7 personal-m2-assistant（M3 认知分流）-> m2-dispatcher（M2 冻结 spec.md、盯 PR DAG）-> team-lead（M1 波次调度、独立 Worktree + 跨厂商 Reviewer 独立裁判 + V3 两轮熔断）",
    },
    {
        "layer": "L1",
        "title": "L1 专项执行层 (Execution — Agent Doing the Work)",
        "role": "Agent Doing the Work",
        "node_ids": [
            "E1", "E2", "E3", "E4", "E4a", "E5", "E7", "E8", "E9", "E12",
            "Q1", "Q2", "Q3",
            "I1", "I2", "I3", "I4", "I5", "I6", "G1", "G2",
            "P1", "P4", "P5", "P6", "P9",
            "O1", "O2", "O3", "O4", "O5", "O6",
        ],
        "skills": [
            "howto", "workflow-hub", "my-designer", "design-studio", "codebase-design",
            "linear-board", "issue-brief", "knowledge-digest", "save-tool", "story-craft",
            "layered-data-warehouse", "nas-smart-home", "xcode-cli",
        ],
        "summary": "标准业务节点执行（研发交付 E1–E5/E7–E9/E12/Q1–Q3、信息与看板 I1–I6/G1–G2、个人知识与云/家居底座 P1/P4–P6/P9/O1–O6），其中 I5/G1 桥接 L4 人类门禁",
    },
)

_MACRO_PIPELINE_STAGES: tuple[dict[str, Any], ...] = (
    {
        "stage": 1,
        "title": "1. 信号与入口",
        "flow": "I1/I2/I7 -> I3/I4 -> I5/G1",
        "node_ids": ["I1", "I2", "I7", "I3", "I4", "I5", "I6", "G1"],
        "summary": "Teams/Email/统一 M2 入口采集 -> Jev 快思考分桶与脱敏摘要 -> Owner 拍板或落 Linear Goal",
    },
    {
        "stage": 2,
        "title": "2. 共创与拆解",
        "flow": "G1 -> E5/E6/E7",
        "node_ids": ["G1", "E5", "E6", "E7"],
        "summary": "Pre-Flight 边界探查、四层深模块拆解与 Apple OS 原生动态外观 UI 契约冻结",
    },
    {
        "stage": 3,
        "title": "3. 编码与修Bug",
        "flow": "E1/E2/E3 -> E4a -> E4 -> E9",
        "node_ids": ["E1", "E2", "E3", "E4a", "E4", "E9"],
        "summary": "Feature/Bug List 先经 E4a 预筛 -> 独立 Worktree 编码切片 -> 6DQ 渐进验证",
    },
    {
        "stage": 4,
        "title": "4. 审查与发布",
        "flow": "E10 <-> E11 -> E12 -> G2/Q1",
        "node_ids": ["E10", "E11", "E12", "G2", "Q1", "Q2", "Q3", "E8"],
        "summary": "PR 盯梢与双轴异构审查闭环 -> 签名公证发布与线上真实读回 -> FlightBook/质量监控",
    },
    {
        "stage": 5,
        "title": "5. 个人数据飞轮",
        "flow": "P9 -> P2 -> P1/P3 -> P7/P8/P6",
        "node_ids": ["P9", "P2", "P1", "P3", "P7", "P8", "P6", "P4", "P5", "O1", "O2", "O3", "O4", "O5", "O6"],
        "summary": "分层数仓与 aidata 汇聚 AIDash/retro-loop -> 沉淀 Skill/Repo 门禁、知识库与公开叙事",
    },
)

_SKILL_CLI_ROUTES: tuple[dict[str, Any], ...] = (
    {
        "keywords": (
            "cybernetic pyramid", "harness engineering", "human helmsman", "meta-evolution",
            "四层控制论金字塔", "控制论金字塔", "人类掌舵", "熵减与自演进", "自演进层", "协同编排层", "专项执行层",
        ),
        "nodes": ["P7", "P3", "I7", "E6", "E4", "E9", "G1", "I5"],
        "skills": ["howto", "workflow-hub", "skill-contract", "team-lead"],
        "guidance": (
            "L4–L1 4-Layer Cybernetic Pyramid (Harness Engineering): "
            "L4 Human Helmsman (Direction/Taste/Oracle/Evidence via I5/G1) -> "
            "L3 Meta-Evolution (P2/P3/P7/P8 Agent Helping Agent Improve) -> "
            "L2 Orchestration (I7/E6/E10/E11 Agent Guiding Agent) -> "
            "L1 Execution (Agent Doing the Work)."
        ),
        "cli_commands": [
            "python3 skills/howto/scripts/howto_flow.py --pyramid --json",
            "python3 skills/howto/scripts/howto_flow.py --neighbors P3 --json",
        ],
    },
    {
        "keywords": (
            "system theme", "dark mode", "light mode", "designtheme", ".designtheme", "colorscheme",
            "nsappearance", "uitraitcollection", "system appearance", "theme switcher", "theme toggle",
            "暗黑模式", "深色模式", "浅色模式", "系统外观", "系统主题", "主题切换", "平行主题",
        ),
        "nodes": ["E7", "P7", "E4", "E9"],
        "skills": ["my-designer", "skill-contract", "howto"],
        "guidance": (
            "Apple OS-Native Dynamic System Appearance (Zero Custom Theme Engine): E7 -> P7 -> E4 -> E9 — "
            "UI light/dark appearance MUST bind strictly to Apple OS system appearance "
            "(NSAppearance / UITraitCollection / @Environment(\\.colorScheme) + semantic dynamic tokens); "
            "forbid custom .designTheme, in-app theme switchers, and Web-CSS-style parallel theme engines."
        ),
        "cli_commands": [
            "python3 skills/howto/scripts/howto_guard.py validate-recipe --file <recipe.md>",
            "python3 skills/my-designer/scripts/verify_design_cases.py route --platform desktop --persona default",
            "python3 skills/skill-contract/scripts/skill_contract.py audit --skill howto --strict",
        ],
    },
    {
        "keywords": ("ai metric", "aidash", "aidata", "l1", "l2", "l3", "l4", "l5", "指标", "看板", "roi", "telemetry"),
        "nodes": ["P9", "P2", "P1", "E7", "E4"],
        "skills": ["aidash-metrics", "layered-data-warehouse", "retro-loop"],
        "cli_commands": [
            "python3 skills/retro-loop/scripts/multica_skills_autopilot.py collect --output /tmp/skills-roi.json",
            "python3 skills/skill-contract/scripts/skill_contract.py audit --skill aidash-metrics --strict",
        ],
    },
    {
        "keywords": ("bug list", "fix bug", "bug", "triage", "devsync", "缺陷", "排障"),
        "nodes": ["E2", "E4a", "E3", "E4", "E9", "E10"],
        "skills": ["team-lead", "m2-dispatcher", "code-review"],
        "cli_commands": [
            "python3 skills/team-lead/scripts/goal-queue.py next-wave --handoff-dir ~/Development/handoff/<goal>",
            "python3 skills/team-lead/scripts/feedback_guard.py pre-review-check --worktree <wt> --brief <brief.md>",
        ],
    },
    {
        "keywords": ("feature", "build", "spec", "architecture", "需求", "开发", "模块"),
        "nodes": ["E1", "E5", "E6", "E4", "E9", "E10"],
        "skills": ["codebase-design", "team-lead", "m2-dispatcher"],
        "cli_commands": [
            "python3 skills/codebase-design/scripts/spec_code_drift_patrol.py collect --json",
            "skills/team-lead/scripts/herdr-dispatch.sh --new-workspace --role team_lead --name <goal>-goal-tl-r1 --kind claude --cwd <repo> --prompt '<path>'",
        ],
    },
    {
        "keywords": ("jev", "falcon", "system one", "systemone", "noul", "choice", "快思考"),
        "nodes": ["I3"],
        "skills": ["howto", "team-lead", "skill-contract"],
        "cli_commands": [
            "python3 skills/team-lead/scripts/jev_client.py --state '{}' --questions '{}'",
            "python3 skills/skill-contract/scripts/skill_contract.py sync-jev-stars --skill <skill> --dry-run",
        ],
    },
    {
        "keywords": ("skill", "make a skill", "contract", "audit", "eval", "技能"),
        "nodes": ["P7", "E9"],
        "skills": ["skill-contract", "howto"],
        "cli_commands": [
            "python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict",
            "python3 skills/skill-contract/scripts/skill_contract.py eval --all --strict",
        ],
    },
    {
        "keywords": ("teams", "reply", "inbox", "群聊", "消息"),
        "nodes": ["I1", "I3", "I4", "I5", "I6"],
        "skills": ["howto", "personal-m2-assistant"],
        "cli_commands": [
            "python3 skills/howto/scripts/howto_guard.py validate-recipe --file skills/workflow-hub/references/how-to-nodes.md",
        ],
    },
    {
        "keywords": ("email", "workiq", "邮件", "分类"),
        "nodes": ["I2", "I3", "I4", "I5"],
        "skills": ["howto", "linear-board"],
        "cli_commands": [
            "python3 skills/linear-board/scripts/daily-brief.py --date today --dry-run --dual-zone",
        ],
    },
    {
        "keywords": ("ui", "design", "visual", "prototype", "设计", "界面", "色板"),
        "nodes": ["E7", "E9"],
        "skills": ["my-designer", "design-studio"],
        "cli_commands": [
            "python3 skills/design-studio/scripts/taste_evolution_loop.py scan",
        ],
    },
    {
        "keywords": ("learn", "knowledge", "digest", "vault", "知识", "拆解", "收藏", "tool"),
        "nodes": ["P4", "P5", "P6"],
        "skills": ["knowledge-digest", "save-tool", "story-craft"],
        "cli_commands": [
            "python3 skills/knowledge-digest/scripts/digest_vault.py discover-topics --top 4",
        ],
    },
    {
        "keywords": ("daily brief", "linear", "multica", "patrol", "hygiene", "巡检", "日报"),
        "nodes": ["G1", "I5", "P1", "P3"],
        "skills": ["linear-board", "issue-brief", "team-lead"],
        "cli_commands": [
            "python3 skills/team-lead/scripts/local_hygiene_patrol.py collect --json",
            "python3 skills/issue-brief/scripts/verify_issue_format.py verify - --json",
        ],
    },
)


def _load_guard() -> Any:
    spec = importlib.util.spec_from_file_location("howto_guard_mod", GUARD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load howto_guard from {GUARD_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["howto_guard_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


def _expand_node_ids(cell: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for rng in re.finditer(r"\b([EQIGPO])(\d+)[–-](\1)?(\d+)\b", cell):
        prefix = rng.group(1)
        start_n, end_n = int(rng.group(2)), int(rng.group(4))
        for num in range(min(start_n, end_n), max(start_n, end_n) + 1):
            nid = f"{prefix}{num}"
            if nid not in seen:
                seen.add(nid)
                found.append(nid)
    for m_node in re.finditer(r"\b([EQIGPO]\d+[a-z]?)\b", cell):
        nid = m_node.group(1)
        if nid not in seen:
            seen.add(nid)
            found.append(nid)
    return found


def _resolve_cybernetic_layer(node_id: str) -> dict[str, Any]:
    human_gates = {nid for e in _CYBERNETIC_PYRAMID for nid in e.get("human_gate_nodes", ())}
    matched = next((e for e in _CYBERNETIC_PYRAMID if e["layer"] != "L4" and node_id in e["node_ids"]), _CYBERNETIC_PYRAMID[-1])
    bridges_l4 = node_id in human_gates
    label = f"{matched['layer']} ({matched['role']})" + (" [bridges L4 Human Helmsman gate]" if bridges_l4 else "")
    return {
        "cybernetic_layer": matched["layer"],
        "cybernetic_layer_title": matched["title"],
        "cybernetic_role": matched["role"],
        "cybernetic_label": label,
        "bridges_l4_human_gate": bridges_l4,
    }


def parse_howto_catalog(nodes_doc: Path = DEFAULT_NODES_DOC) -> dict[str, Any]:
    resolved = nodes_doc.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"How-To catalog not found: {resolved}")

    text = resolved.read_text(encoding="utf-8")
    nodes: list[dict[str, Any]] = []
    broken_edges: list[dict[str, Any]] = []
    current_group = ""
    section_mode = ""

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        m_group = re.match(r"^###\s+Group\s+([ABC])\s*[：:]\s*(.+)$", stripped)
        if m_group:
            current_group = m_group.group(1).upper()
            section_mode = "nodes"
            continue
        if stripped.startswith("## §4"):
            section_mode = "tk"
            current_group = ""
            continue
        if stripped.startswith("## §5"):
            section_mode = ""
            continue
        if not stripped.startswith("|"):
            continue
        cols = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cols) < 5 or all(not c or set(c) <= {"-", ":"} for c in cols):
            continue
        if cols[0] in {"节点", "ID", "层", "Repo 版图"}:
            continue

        if section_mode == "nodes" and len(cols) >= 6:
            node_ids = _expand_node_ids(cols[0])
            if not node_ids:
                continue
            label_parts = cols[0].split(None, 1)
            title = label_parts[1] if len(label_parts) > 1 else cols[0]
            for idx, nid in enumerate(node_ids):
                layer_meta = _resolve_cybernetic_layer(nid)
                nodes.append(
                    {
                        "id": nid,
                        "raw_cell": cols[0],
                        "title": title if len(node_ids) == 1 else f"{title} ({nid})",
                        "group": current_group,
                        "cybernetic_layer": layer_meta["cybernetic_layer"],
                        "cybernetic_layer_title": layer_meta["cybernetic_layer_title"],
                        "cybernetic_label": layer_meta["cybernetic_label"],
                        "bridges_l4_human_gate": layer_meta["bridges_l4_human_gate"],
                        "trigger": cols[1],
                        "inbound_internal": cols[2],
                        "calls": cols[3],
                        "points_to": cols[4],
                        "broken_edge": cols[5],
                        "expanded_from_range": len(node_ids) > 1 and idx >= 0,
                    }
                )
        elif section_mode == "tk" and len(cols) >= 5 and re.fullmatch(r"TK-\d{2}", cols[0]):
            broken_edges.append(
                {
                    "id": cols[0],
                    "edge": cols[1],
                    "trigger_notify": cols[2],
                    "owned_paths": cols[3],
                    "acceptance_gate": cols[4],
                }
            )

    return {
        "ok": True,
        "catalog_path": str(resolved),
        "node_count": len(nodes),
        "broken_edge_count": len(broken_edges),
        "nodes": nodes,
        "broken_edges": broken_edges,
    }


def list_nodes(
    *,
    group: str | None = None,
    nodes_doc: Path = DEFAULT_NODES_DOC,
) -> dict[str, Any]:
    catalog = parse_howto_catalog(nodes_doc)
    selected = catalog["nodes"]
    norm_group = str(group or "").strip().upper()
    if norm_group:
        if norm_group not in {"A", "B", "C"}:
            raise ValueError(f"Unsupported group '{group}'; expected A, B, or C")
        selected = [n for n in selected if n["group"] == norm_group]
    return {
        "ok": True,
        "group_filter": norm_group or None,
        "node_count": len(selected),
        "node_ids": [n["id"] for n in selected],
        "nodes": selected,
        "broken_edge_count": catalog["broken_edge_count"],
        "broken_edges": catalog["broken_edges"] if not norm_group else [
            tk for tk in catalog["broken_edges"]
            if any(nid in _expand_node_ids(tk["edge"]) for nid in {n["id"] for n in selected})
        ],
    }


def _extract_edge_targets(cell: str, valid_ids: set[str]) -> list[str]:
    return [nid for nid in _expand_node_ids(cell) if nid in valid_ids]


def _format_graph_text(
    *,
    node_count: int,
    calls_edge_count: int,
    points_to_edge_count: int,
    broken_edge_count: int,
    cybernetic_pyramid: dict[str, dict[str, Any]],
    stages: Sequence[dict[str, Any]],
) -> str:
    lines = [
        "# L4–L1 四层控制论金字塔 (4-Layer Cybernetic Pyramid — Harness Engineering)",
        f"Nodes: {node_count} | Calls edges: {calls_edge_count} | PointsTo edges: {points_to_edge_count} | Broken edges: {broken_edge_count}",
        "",
    ]
    for layer_key in ("L4", "L3", "L2", "L1"):
        layer_info = cybernetic_pyramid[layer_key]
        lines.append(f"## {layer_info['title']}")
        lines.append(f"  - 核心定位：{layer_info['summary']}")
        if layer_info.get("responsibilities"):
            lines.append(f"  - 四项不可替代职责：{'；'.join(layer_info['responsibilities'])}")
        if layer_info.get("human_gate_nodes"):
            lines.append(f"  - 人类门禁桥接节点：{', '.join(layer_info['human_gate_nodes'])}")
        if layer_info.get("nodes"):
            lines.append(f"  - 归属节点：{', '.join(layer_info['nodes'])}")
        if layer_info.get("skills"):
            lines.append(f"  - 代表技能：{', '.join(layer_info['skills'])}")
        lines.append("")

    lines.append("# How-To 5-Stage Macro Lifecycle Pipeline")
    lines.append("")
    for st in stages:
        lines.append(f"## {st['title']}  [{st['flow']}]")
        lines.append(f"  - 核心职责：{st['summary']}")
        lines.append(f"  - 覆盖节点：{', '.join(st['nodes'])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_graph(*, nodes_doc: Path = DEFAULT_NODES_DOC) -> dict[str, Any]:
    catalog = parse_howto_catalog(nodes_doc)
    by_id = {n["id"]: n for n in catalog["nodes"]}
    valid_ids = set(by_id)
    calls_edges: list[dict[str, str]] = []
    points_to_edges: list[dict[str, str]] = []

    for node in catalog["nodes"]:
        src = node["id"]
        for dst in _extract_edge_targets(node["calls"], valid_ids):
            calls_edges.append({"from": src, "to": dst, "kind": "Calls"})
        for dst in _extract_edge_targets(node["points_to"], valid_ids):
            points_to_edges.append({"from": src, "to": dst, "kind": "PointsTo"})

    pyramid_payload: dict[str, dict[str, Any]] = {}
    for entry in _CYBERNETIC_PYRAMID:
        layer_key = entry["layer"]
        formatted_nodes = [
            f"{nid} {by_id[nid]['title']}" if by_id[nid]["title"] != nid else nid
            for nid in entry.get("node_ids", [])
            if nid in by_id
        ]
        formatted_gate_nodes = [
            f"{nid} {by_id[nid]['title']}" if by_id[nid]["title"] != nid else nid
            for nid in entry.get("human_gate_nodes", [])
            if nid in by_id
        ]
        item: dict[str, Any] = {
            "layer": layer_key,
            "title": entry["title"],
            "role": entry["role"],
            "summary": entry["summary"],
            "node_ids": [nid for nid in entry.get("node_ids", []) if nid in by_id],
            "nodes": formatted_nodes,
            "skills": list(entry.get("skills", [])),
        }
        if "responsibilities" in entry:
            item["responsibilities"] = list(entry["responsibilities"])
        if "human_gate_nodes" in entry:
            item["human_gate_node_ids"] = [nid for nid in entry["human_gate_nodes"] if nid in by_id]
            item["human_gate_nodes"] = formatted_gate_nodes
        pyramid_payload[layer_key] = item

    stages_payload: list[dict[str, Any]] = []
    for st in _MACRO_PIPELINE_STAGES:
        stage_nodes = [
            f"{nid} {by_id[nid]['title']}" if by_id[nid]["title"] != nid else nid
            for nid in st["node_ids"]
            if nid in by_id
        ]
        stages_payload.append(
            {
                "stage": st["stage"],
                "title": st["title"],
                "flow": st["flow"],
                "summary": st["summary"],
                "nodes": stage_nodes,
            }
        )

    text = _format_graph_text(
        node_count=catalog["node_count"],
        calls_edge_count=len(calls_edges),
        points_to_edge_count=len(points_to_edges),
        broken_edge_count=catalog["broken_edge_count"],
        cybernetic_pyramid=pyramid_payload,
        stages=stages_payload,
    )
    return {
        "ok": True,
        "node_count": catalog["node_count"],
        "calls_edge_count": len(calls_edges),
        "points_to_edge_count": len(points_to_edges),
        "total_edge_count": len(calls_edges) + len(points_to_edges),
        "broken_edge_count": catalog["broken_edge_count"],
        "cybernetic_pyramid": pyramid_payload,
        "stages": stages_payload,
        "adjacency": {
            "calls": calls_edges,
            "points_to": points_to_edges,
        },
        "text": text,
    }


def render_macro_graph(*, nodes_doc: Path = DEFAULT_NODES_DOC) -> dict[str, Any]:
    return build_graph(nodes_doc=nodes_doc)


def build_neighbors(node_id: str, *, nodes_doc: Path = DEFAULT_NODES_DOC) -> dict[str, Any]:
    clean_id = str(node_id or "").strip()
    catalog = parse_howto_catalog(nodes_doc)
    by_id = {n["id"]: n for n in catalog["nodes"]}
    canonical_map = {nid.upper(): nid for nid in by_id}
    resolved_id = by_id.get(clean_id, {}).get("id") or canonical_map.get(clean_id.upper(), "")
    if not resolved_id or resolved_id not in by_id:
        raise ValueError(f"Unknown How-To node '{node_id}'; run --list-nodes to inspect valid IDs")

    target = by_id[resolved_id]
    valid_ids = set(by_id)
    downstream_calls_ids = _extract_edge_targets(target["calls"], valid_ids)
    downstream_points_ids = _extract_edge_targets(target["points_to"], valid_ids)

    upstream_callers: list[dict[str, str]] = []
    upstream_pointers: list[dict[str, str]] = []
    for other in catalog["nodes"]:
        oid = other["id"]
        if oid == resolved_id:
            continue
        if resolved_id in _extract_edge_targets(other["calls"], valid_ids):
            upstream_callers.append({"id": oid, "title": other["title"], "via": "Calls"})
        if resolved_id in _extract_edge_targets(other["points_to"], valid_ids):
            upstream_pointers.append({"id": oid, "title": other["title"], "via": "PointsTo"})

    node_tk_refs = set(re.findall(r"\bTK-\d{2}\b", target["broken_edge"]))
    related_tks = [
        tk for tk in catalog["broken_edges"]
        if tk["id"] in node_tk_refs or resolved_id in _expand_node_ids(tk["edge"])
    ]

    def _fmt_nodes(items: Sequence[dict[str, str]]) -> str:
        if not items:
            return "—（顶层入口或按需触发）"
        return ", ".join(f"{it['id']} {it['title']} ({it['via']})" if "via" in it else f"{it['id']} {it['title']}" for it in items)

    calls_entries = [{"id": nid, "title": by_id[nid]["title"]} for nid in downstream_calls_ids]
    points_entries = [{"id": nid, "title": by_id[nid]["title"]} for nid in downstream_points_ids]
    tk_summary = (
        "; ".join(f"{tk['id']} [{tk['edge']}] -> {tk['acceptance_gate']}" for tk in related_tks)
        if related_tks
        else (target["broken_edge"] or "—")
    )

    card_lines = [
        f"# Local Neighborhood Card: {target['id']} {target['title']}",
        f"- **Node & Role**: `{target['id']}` ({target['title']}) · Group {target['group']} · Trigger: `{target['trigger']}`",
        f"- **Cybernetic Layer (控制论层级)**: `{target['cybernetic_layer']}` — {target['cybernetic_layer_title']}"
        + (" · 桥接 `L4` 人类掌舵门禁" if target["bridges_l4_human_gate"] else ""),
        f"- **Upstream (被谁调用/流转自)**: {_fmt_nodes([*upstream_callers, *upstream_pointers])}",
        f"- **Internal (本节点只干什么)**: {target['inbound_internal']}",
        f"- **Downstream Calls (调用哪些子步骤，跑完返回)**: {_fmt_nodes(calls_entries) if calls_entries else '—（叶节点，无子步骤调用）'}",
        f"- **Downstream PointsTo (做完交接给谁)**: {_fmt_nodes(points_entries) if points_entries else target['points_to']}",
        f"- **Broken Edges (关联 TK-xx)**: {tk_summary}",
    ]
    return {
        "ok": True,
        "cybernetic_layer": target["cybernetic_layer"],
        "cybernetic_layer_title": target["cybernetic_layer_title"],
        "cybernetic_label": target["cybernetic_label"],
        "bridges_l4_human_gate": target["bridges_l4_human_gate"],
        "node": target,
        "upstream": [*upstream_callers, *upstream_pointers],
        "downstream_calls": calls_entries,
        "downstream_points_to": points_entries,
        "broken_edges": related_tks,
        "text": "\n".join(card_lines) + "\n",
    }


def inspect_neighbors(node_id: str, *, nodes_doc: Path = DEFAULT_NODES_DOC) -> dict[str, Any]:
    return build_neighbors(node_id, nodes_doc=nodes_doc)


def lookup_howto(
    query: str,
    *,
    nodes_doc: Path = DEFAULT_NODES_DOC,
) -> dict[str, Any]:
    clean_query = str(query or "").strip()
    if not clean_query:
        raise ValueError("lookup query must be non-empty")

    catalog = parse_howto_catalog(nodes_doc)
    by_id = {n["id"]: n for n in catalog["nodes"]}
    q_lower = clean_query.lower()
    tokens = [tok for tok in re.split(r"[\s,;/|｜→-]+", q_lower) if len(tok) >= 2]

    matched_routes: list[dict[str, Any]] = []
    matched_node_ids: list[str] = []
    target_skills: list[str] = []
    guidance_notes: list[str] = []
    cli_commands: list[str] = [
        "python3 skills/howto/scripts/howto_guard.py validate-recipe --file <recipe.md>",
    ]

    for route in _SKILL_CLI_ROUTES:
        hits = [kw for kw in route["keywords"] if kw in q_lower or any(tok == kw for tok in tokens)]
        if hits:
            matched_routes.append({"matched_keywords": hits, **route})
            for nid in route["nodes"]:
                if nid in by_id and nid not in matched_node_ids:
                    matched_node_ids.append(nid)
            for sk in route["skills"]:
                if sk not in target_skills:
                    target_skills.append(sk)
            route_guidance = str(route.get("guidance") or "").strip()
            if route_guidance and route_guidance not in guidance_notes:
                guidance_notes.append(route_guidance)
            for cmd in route["cli_commands"]:
                if cmd not in cli_commands:
                    cli_commands.append(cmd)

    # Also match direct node IDs (e.g. E4a, P9, I3) or substring hits in node table
    for m_tok in re.finditer(r"\b([EQIGPO]\d+[a-z]?)\b", clean_query):
        nid = m_tok.group(1)
        if nid in by_id and nid not in matched_node_ids:
            matched_node_ids.append(nid)

    for node in catalog["nodes"]:
        haystack = " ".join(
            str(node.get(k) or "") for k in ("id", "raw_cell", "title", "inbound_internal", "calls", "points_to", "broken_edge")
        ).lower()
        if q_lower in haystack or any(tok in haystack for tok in tokens):
            if node["id"] not in matched_node_ids:
                matched_node_ids.append(node["id"])

    matched_tks = [
        tk for tk in catalog["broken_edges"]
        if q_lower in " ".join(tk.values()).lower()
        or any(nid in _expand_node_ids(tk["edge"]) for nid in matched_node_ids[:6])
    ]

    if not matched_node_ids:
        matched_node_ids = ["E1", "E6", "E4", "E9"]
    if not target_skills:
        target_skills = ["howto", "workflow-hub", "team-lead"]

    result: dict[str, Any] = {
        "ok": True,
        "query": clean_query,
        "matched_nodes": [by_id[nid] for nid in matched_node_ids[:10] if nid in by_id],
        "matched_broken_edges": matched_tks[:8],
        "target_skills": target_skills,
        "cli_entry_commands": cli_commands,
    }
    if guidance_notes:
        result["guidance"] = guidance_notes[0] if len(guidance_notes) == 1 else " | ".join(guidance_notes)
    return result


def scaffold_tuple(
    task: str,
    *,
    nodes_doc: Path = DEFAULT_NODES_DOC,
) -> dict[str, Any]:
    clean_task = str(task or "").strip()
    if not clean_task:
        raise ValueError("task description must be non-empty")

    lookup = lookup_howto(clean_task, nodes_doc=nodes_doc)
    node_ids = [n["id"] for n in lookup["matched_nodes"][:4]] or ["E1", "E6", "E4", "E9"]
    primary_nodes = " → ".join(node_ids)
    calls_str = ", ".join(n for n in node_ids if n not in {"G1", "I5"}) or "E4, E9"
    skills_str = ", ".join(f"`{s}`" for s in lookup["target_skills"][:3])
    tk_ids = [tk["id"] for tk in lookup["matched_broken_edges"][:2]]
    tk_note = f"（关联断链：{', '.join(tk_ids)}）" if tk_ids else ""

    sanitized_task = re.sub(
        r"(?i)(?:\.designTheme\b|\bdesignTheme\b|(?:custom|in-app|app-private|manual|web[-_\s]?css(?:[-_\s]?style)?)\s+(?:light/dark\s+|dark[- ]mode\s+|parallel[-_\s]+)?theme\s+(?:toggle|toggles|switcher|switchers|state\s+machine|state\s+machines|engine|engines|manager))",
        "Apple OS-native dynamic system appearance",
        clean_task,
    )
    tuple_dict = {
        "Inbound": f"Owner / Pulse 触发「{sanitized_task}」需求输入；复用技能 {skills_str}。",
        "Internal": (
            f"M2 仅拆解全局节点链路（{primary_nodes}）与边界，不翻业务代码；"
            f"凡含亮/暗外观必须 100% 绑定 Apple OS 原生动态系统外观（NSAppearance / UITraitCollection / @Environment(\\.colorScheme) + 语义动态 Token，严禁自建 .designTheme 或平行主题状态机）；"
            f"技术细节交右侧 M1 独立工作树调查{tk_note}。"
        ),
        "Calls": f"{calls_str}（按 Layer 0 脚本预检 → Layer 1 Jev 判断 → Layer 2 M1 实现漏斗编排）。",
        "PointsTo": "G1 Task / Linear TIA 状态读回与 I5 Notify Owner 验收结果（禁止跨素材库自动同步，save-tool 保持零执行）。",
        "Trigger & Notify": "Manual / Scheduled Pulse 触发；范围或口径未决走 P0 拍板，PR 合入与读回走 P1 回执。",
    }
    markdown = (
        f"### `/howto` 五元组拆解：{sanitized_task}\n\n"
        f"- **Inbound**：{tuple_dict['Inbound']}\n"
        f"- **Internal**：{tuple_dict['Internal']}\n"
        f"- **Calls**：{tuple_dict['Calls']}\n"
        f"- **PointsTo**：{tuple_dict['PointsTo']}\n"
        f"- **Trigger & Notify**：{tuple_dict['Trigger & Notify']}\n"
    )
    guard = _load_guard()
    validation = guard.validate_recipe(markdown, nodes_doc=nodes_doc)
    return {
        "ok": bool(validation["ok"]),
        "task": clean_task,
        "five_tuple": tuple_dict,
        "target_skills": lookup["target_skills"],
        "cli_entry_commands": lookup["cli_entry_commands"],
        "markdown": markdown,
        "guard_validation": validation,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="howto_flow.py",
        description="Deterministic /howto default entry CLI (--list-nodes, --graph/--pyramid, --neighbors, --lookup, --scaffold-tuple).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list-nodes", action="store_true", help="List canonical 30+ How-To nodes and TK-01..TK-16 registry.")
    mode.add_argument("--graph", "--pyramid", dest="graph", action="store_true", help="Render the L4–L1 Cybernetic Pyramid above the 5-Stage Pipeline.")
    mode.add_argument("--neighbors", metavar="NODE_ID", help="Render a 1-page Local Neighborhood Card for a canonical node ID (e.g. E2).")
    mode.add_argument("--lookup", metavar="QUERY", help="Match a task/scenario query to How-To nodes, skills, and CLI commands.")
    mode.add_argument("--scaffold-tuple", metavar="TASK", help="Generate a howto_guard-validated 5-tuple scaffold.")
    parser.add_argument("--group", choices=("A", "B", "C", "a", "b", "c"), default=None, help="Optional group filter for --list-nodes.")
    parser.add_argument("--nodes-doc", type=Path, default=DEFAULT_NODES_DOC, help="Path to workflow-hub/references/how-to-nodes.md.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output (human-readable text is default for --graph, --neighbors, and --scaffold-tuple).")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        if ns.list_nodes:
            payload = list_nodes(group=ns.group, nodes_doc=ns.nodes_doc)
        elif ns.graph:
            payload = render_macro_graph(nodes_doc=ns.nodes_doc)
        elif ns.neighbors is not None:
            payload = inspect_neighbors(ns.neighbors, nodes_doc=ns.nodes_doc)
        elif ns.lookup is not None:
            payload = lookup_howto(ns.lookup, nodes_doc=ns.nodes_doc)
        else:
            payload = scaffold_tuple(ns.scaffold_tuple, nodes_doc=ns.nodes_doc)
    except (FileNotFoundError, ValueError) as exc:
        err = {"ok": False, "error": str(exc)}
        print(json.dumps(err, ensure_ascii=False, indent=2))
        return 1

    if ns.json or (not ns.scaffold_tuple and not ns.graph and ns.neighbors is None):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif ns.scaffold_tuple:
        sys.stdout.write(payload["markdown"])
    else:
        sys.stdout.write(payload["text"])
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
