#!/usr/bin/env python3
"""Deterministic default CLI entry for /howto (`howto_flow.py`).

Supports:
1. `--list-nodes [--group A|B|C] [--json]`: Parses `workflow-hub/references/how-to-nodes.md`
   and outputs the canonical How-To nodes (`E1..E12`, `Q1..Q3`, `I1..I7`, `G1..G2`,
   `P1..P9`, `O1..O6`) plus `TK-01..TK-16` broken-edge registry.
2. `--lookup <query> [--json]`: Deterministic keyword/node/skill matcher returning
   matching How-To nodes, target skills, and exact CLI entry commands.
3. `--scaffold-tuple <task> [--json]`: Generates a validated 5-tuple (`Inbound`,
   `Internal`, `Calls`, `PointsTo`, `Trigger & Notify`) scaffold verified by `howto_guard.py`.
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

_SKILL_CLI_ROUTES: tuple[dict[str, Any], ...] = (
    {
        "id": "ai-metrics",
        "keywords": ("ai metric", "ai metrics", "aidash", "aidata", "l1", "l2", "l3", "l4", "l5", "指标", "看板", "roi", "telemetry"),
        "nodes": ["P9", "P2", "P1", "E7", "E4"],
        "skills": ["aidash-metrics", "layered-data-warehouse", "retro-loop"],
        "cli_commands": [
            "python3 skills/retro-loop/scripts/multica_skills_autopilot.py collect --output /tmp/skills-roi.json",
            "python3 skills/skill-contract/scripts/skill_contract.py audit --skill aidash-metrics --strict",
        ],
    },
    {
        "id": "bug-fix",
        "keywords": ("bug list", "fix bug", "bug", "triage", "devsync", "缺陷", "排障"),
        "nodes": ["E2", "E4a", "E3", "E4", "E9", "E10"],
        "skills": ["team-lead", "m2-dispatcher", "code-review"],
        "cli_commands": [
            "python3 skills/team-lead/scripts/goal-queue.py next-wave --handoff-dir ~/Development/handoff/<goal>",
            "python3 skills/team-lead/scripts/feedback_guard.py pre-review-check --worktree <wt> --brief <brief.md>",
        ],
    },
    {
        "id": "feature",
        "keywords": ("feature", "build", "spec", "architecture", "需求", "开发", "模块"),
        "nodes": ["E1", "E5", "E6", "E4", "E9", "E10"],
        "skills": ["codebase-design", "team-lead", "m2-dispatcher"],
        "cli_commands": [
            "python3 skills/codebase-design/scripts/spec_code_drift_patrol.py collect --json",
            "skills/team-lead/scripts/herdr-dispatch.sh --new-workspace --role team_lead --name <goal>-goal-tl-r1 --kind claude --cwd <repo> --prompt '<path>'",
        ],
    },
    {
        "id": "jev",
        "keywords": ("jev", "falcon", "system one", "systemone", "noul", "choice", "快思考"),
        "nodes": ["I3"],
        "skills": ["howto", "team-lead", "skill-contract"],
        "cli_commands": [
            "python3 skills/team-lead/scripts/jev_client.py --state '{}' --questions '{}'",
            "python3 skills/skill-contract/scripts/skill_contract.py sync-jev-stars --skill <skill> --dry-run",
        ],
    },
    {
        "id": "skill-contract",
        "keywords": ("skill", "make a skill", "contract", "audit", "eval", "技能"),
        "nodes": ["P7", "E9"],
        "skills": ["skill-contract", "howto"],
        "cli_commands": [
            "python3 skills/skill-contract/scripts/skill_contract.py audit --all --strict",
            "python3 skills/skill-contract/scripts/skill_contract.py eval --all --strict",
        ],
    },
    {
        "id": "teams",
        "keywords": ("teams", "reply", "inbox", "群聊", "消息"),
        "nodes": ["I1", "I3", "I4", "I5", "I6"],
        "skills": ["howto", "personal-m2-assistant"],
        "cli_commands": [
            "python3 skills/howto/scripts/howto_guard.py validate-recipe --file skills/workflow-hub/references/how-to-nodes.md",
        ],
    },
    {
        "id": "email",
        "keywords": ("email", "workiq", "邮件", "分类"),
        "nodes": ["I2", "I3", "I4", "I5"],
        "skills": ["howto", "linear-board"],
        "cli_commands": [
            "python3 skills/linear-board/scripts/daily-brief.py --date today --dry-run --dual-zone",
        ],
    },
    {
        "id": "ui-design",
        "keywords": ("ui", "design", "visual", "prototype", "设计", "界面", "色板"),
        "nodes": ["E7", "E9"],
        "skills": ["my-designer", "design-studio"],
        "cli_commands": [
            "python3 skills/design-studio/scripts/taste_evolution_loop.py loop-check",
        ],
    },
    {
        "id": "knowledge",
        "keywords": ("learn", "knowledge", "digest", "vault", "知识", "拆解", "收藏", "tool"),
        "nodes": ["P4", "P5", "P6"],
        "skills": ["knowledge-digest", "save-tool", "story-craft"],
        "cli_commands": [
            "python3 skills/knowledge-digest/scripts/digest_vault.py discover-topics --top 4",
        ],
    },
    {
        "id": "daily-brief",
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
                nodes.append(
                    {
                        "id": nid,
                        "raw_cell": cols[0],
                        "title": title if len(node_ids) == 1 else f"{title} ({nid})",
                        "group": current_group,
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

    if not nodes:
        raise ValueError(f"How-To catalog contains zero node definitions: {resolved}")

    return {
        "ok": True,
        "catalog_path": str(resolved),
        "node_count": len(nodes),
        "broken_edge_count": len(broken_edges),
        "nodes": nodes,
        "broken_edges": broken_edges,
    }


def _node_id_matches(nid: str, text: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_-]){re.escape(nid)}(?![A-Za-z0-9_-])", text, re.IGNORECASE) is not None


def _keyword_matches(keyword: str, text: str) -> bool:
    if keyword.isascii():
        if re.fullmatch(r"[eqigpo]\d+[a-z]?", keyword, re.IGNORECASE):
            return _node_id_matches(keyword, text)
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text, re.IGNORECASE) is not None
    return keyword.lower() in text.lower()


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
    node_ids = [n["id"] for n in selected]
    filtered_edges = [
        tk for tk in catalog["broken_edges"]
        if not norm_group or any(_node_id_matches(nid, tk["edge"]) for nid in node_ids)
    ]
    return {
        "ok": True,
        "group_filter": norm_group or None,
        "node_count": len(selected),
        "node_ids": node_ids,
        "nodes": selected,
        "broken_edge_count": len(filtered_edges),
        "broken_edges": filtered_edges,
    }


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
    cli_commands: list[str] = [
        "python3 skills/howto/scripts/howto_guard.py validate-recipe --file <recipe.md>",
    ]

    for route in _SKILL_CLI_ROUTES:
        hits = [kw for kw in route["keywords"] if _keyword_matches(kw, q_lower)]
        if hits:
            matched_routes.append({"matched_keywords": hits, **route})
            for nid in route["nodes"]:
                if nid in by_id and nid not in matched_node_ids:
                    matched_node_ids.append(nid)
            for sk in route["skills"]:
                if sk not in target_skills:
                    target_skills.append(sk)
            for cmd in route["cli_commands"]:
                if cmd not in cli_commands:
                    cli_commands.append(cmd)

    # Explicit IDs select definitions, not every node whose contract references them.
    for nid in by_id:
        if _node_id_matches(nid, clean_query) and nid not in matched_node_ids:
            matched_node_ids.append(nid)

    text_queries = [
        term for term in [q_lower, *tokens]
        if not re.fullmatch(r"[eqigpo]\d+[a-z]?", term, re.IGNORECASE)
    ]
    for node in catalog["nodes"]:
        haystack = " ".join(
            str(node.get(k) or "") for k in ("id", "raw_cell", "title", "inbound_internal", "calls", "points_to", "broken_edge")
        )
        if any(_keyword_matches(term, haystack) for term in text_queries):
            if node["id"] not in matched_node_ids:
                matched_node_ids.append(node["id"])

    matched_tks = [
        tk for tk in catalog["broken_edges"]
        if any(_keyword_matches(term, " ".join(tk.values())) for term in text_queries)
        or any(_node_id_matches(nid, tk["edge"]) for nid in matched_node_ids[:6])
    ]

    unresolved = not matched_routes and not matched_node_ids
    if not target_skills:
        target_skills = ["howto", "workflow-hub", "team-lead"]

    return {
        "ok": True,
        "query": clean_query,
        "unresolved": unresolved,
        "matched_scenario": matched_routes[0]["id"] if matched_routes else ("direct_node_match" if matched_node_ids else None),
        "matched_nodes": [by_id[nid] for nid in matched_node_ids[:10] if nid in by_id],
        "fallback_nodes": [by_id[nid] for nid in ("E1", "E6", "E4", "E9") if nid in by_id] if unresolved else [],
        "matched_broken_edges": matched_tks[:8],
        "target_skills": target_skills,
        "cli_entry_commands": cli_commands,
    }


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

    tuple_dict = {
        "Inbound": f"Owner / Pulse 触发「{clean_task}」需求输入；复用技能 {skills_str}。",
        "Internal": f"M2 仅拆解全局节点链路（{primary_nodes}）与边界，不翻业务代码；技术细节交右侧 M1 独立工作树调查{tk_note}。",
        "Calls": f"{calls_str}（按 Layer 0 脚本预检 → Layer 1 Jev 判断 → Layer 2 M1 实现漏斗编排）。",
        "PointsTo": "G1 Task / Linear TIA 状态读回与 I5 Notify Owner 验收结果（禁止跨素材库自动同步，save-tool 保持零执行）。",
        "Trigger & Notify": "Manual / Scheduled Pulse 触发；范围或口径未决走 P0 拍板，PR 合入与读回走 P1 回执。",
    }
    markdown = (
        f"### `/howto` 五元组拆解：{clean_task}\n\n"
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
        description="Deterministic /howto default entry CLI (--list-nodes, --lookup, --scaffold-tuple).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list-nodes", action="store_true", help="List canonical 30+ How-To nodes and TK-01..TK-16 registry.")
    mode.add_argument("--lookup", metavar="QUERY", help="Match a task/scenario query to How-To nodes, skills, and CLI commands.")
    mode.add_argument("--scaffold-tuple", metavar="TASK", help="Generate a howto_guard-validated 5-tuple scaffold.")
    parser.add_argument("--group", choices=("A", "B", "C", "a", "b", "c"), default=None, help="Optional group filter for --list-nodes.")
    parser.add_argument("--nodes-doc", type=Path, default=DEFAULT_NODES_DOC, help="Path to workflow-hub/references/how-to-nodes.md.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output (default when --json is passed; human-readable otherwise).")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        if ns.list_nodes:
            payload = list_nodes(group=ns.group, nodes_doc=ns.nodes_doc)
        elif ns.lookup is not None:
            payload = lookup_howto(ns.lookup, nodes_doc=ns.nodes_doc)
        else:
            payload = scaffold_tuple(ns.scaffold_tuple, nodes_doc=ns.nodes_doc)
    except (FileNotFoundError, ValueError) as exc:
        err = {"ok": False, "error": str(exc)}
        print(json.dumps(err, ensure_ascii=False, indent=2))
        return 1

    if ns.json or not ns.scaffold_tuple:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(payload["markdown"])
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
