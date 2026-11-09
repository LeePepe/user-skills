#!/usr/bin/env python3
"""Progressive disclosure router, Plan A Apple HIG code auditor, and Plan C defect-case registry."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "references" / "defect-cases" / "index.json"
DEFAULT_TAXONOMY = ROOT.parent / "design-studio" / "references" / "taxonomy-v2.json"

ALWAYS_LOAD_HIG = {
    "desktop": ["designing-for-macos.md", "accessibility.md", "layout.md", "color.md", "typography.md"],
    "mobile": ["designing-for-ios.md", "designing-for-ipados.md", "accessibility.md", "layout.md", "color.md", "typography.md"],
    "web": [],
}

PLAN_A_LENSES = [
    {
        "id": "lens-1-accessibility",
        "severity": "Critical",
        "summary": "Dynamic Type (>=11pt mobile / >=10pt desktop), >=4.5:1 contrast, >=44x44pt mobile / >=28x28pt desktop targets, Reduce Motion & Reduce Transparency.",
    },
    {
        "id": "lens-2-platform-conventions",
        "severity": "High",
        "summary": "Native TabView/NavigationSplitView/.toolbar/MenuBarExtra; no bottom bars on macOS; Size Classes over fixed device widths on iOS/iPadOS/iPhone Duo.",
    },
    {
        "id": "lens-3-liquid-glass-and-craft",
        "severity": "High",
        "summary": "Liquid Glass restricted to Functional Layer (bars/floating controls); Content Layer stays solid/standard material; max 1-2 .glassProminent actions.",
    },
    {
        "id": "lens-4-interaction-and-states",
        "severity": "Medium",
        "summary": "Non-blocking loading, ContentUnavailableView empty states, inline error recovery, confirmation for destructive actions.",
    },
    {
        "id": "lens-5-typography-and-copy",
        "severity": "Medium",
        "summary": "Platform capitalization, .monospacedDigit() + .contentTransition(.numericText()) for live numbers, clear verb labels.",
    },
]

def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def match_active_cases(cases_doc: dict[str, Any], platform: str, dimensions: list[str], container: str | None = None) -> list[dict[str, Any]]:
    matched = []
    dim_set = set(dimensions)
    for case in cases_doc.get("active_cases", []):
        plats = case.get("platforms", [])
        if plats and platform not in plats:
            continue
        case_dims = set(case.get("dimensions", []))
        if dim_set and case_dims and not (dim_set & case_dims):
            continue
        containers = case.get("containers", [])
        if container and containers and container not in containers:
            continue
        matched.append(case)
    return matched


def command_route(args: argparse.Namespace) -> int:
    cases_doc = _load_json(Path(args.cases_file))
    taxonomy = _load_json(Path(args.taxonomy)) if Path(args.taxonomy).is_file() else {"dimensions": []}
    dims = _csv(args.dimensions)
    distilled_base = list(ALWAYS_LOAD_HIG.get(args.platform, []))
    hig_files = list(distilled_base)
    on_demand_external: list[str] = []
    native_primitives: list[str] = []

    for dim in taxonomy.get("dimensions", []):
        if not dims or dim["id"] in dims:
            for h in dim.get("hig_routes", []):
                if h not in hig_files:
                    hig_files.append(h)
                if h not in distilled_base and h not in on_demand_external:
                    on_demand_external.append(h)
            for p in dim.get("apple_native_primitives", []):
                if p not in native_primitives:
                    native_primitives.append(p)

    active_plan_c = match_active_cases(cases_doc, args.platform, dims, args.container)
    layering_dims = {"layout-navigation", "component", "chart-dataviz", "motion-interaction"}
    breathing_dims = {"visual-style", "color-palette", "layout-navigation", "typography-copy"}
    disclose_layering = (not dims) or bool(set(dims) & layering_dims) or bool(active_plan_c)
    disclose_breathing = (not dims) or bool(set(dims) & breathing_dims)
    dim_set = set(dims)
    ext_root = "~/Development/Personal/collected-tools/repos"
    if args.platform in ("desktop", "mobile"):
        shipswift_pkgs = {
            "animation": f"{ext_root}/shipswift/ShipSwift/SWPackage/SWAnimation",
            "metal_shaders": f"{ext_root}/shipswift/ShipSwift/SWPackage/SWAnimation/SWMetal",
            "charts": f"{ext_root}/shipswift/ShipSwift/SWPackage/SWChart",
            "components": f"{ext_root}/shipswift/ShipSwift/SWPackage/SWComponent",
            "modules": f"{ext_root}/shipswift/ShipSwift/SWPackage/SWModule",
        }
        focused_shipswift: dict[str, str] = {}
        if not dim_set or (dim_set & {"motion-interaction", "glass-material", "visual-style"}):
            focused_shipswift["animation"] = shipswift_pkgs["animation"]
        if not dim_set or (dim_set & {"glass-material", "visual-style", "motion-interaction"}):
            focused_shipswift["metal_shaders"] = shipswift_pkgs["metal_shaders"]
        if not dim_set or ("chart-dataviz" in dim_set):
            focused_shipswift["charts"] = shipswift_pkgs["charts"]
        if not dim_set or (dim_set & {"component", "layout-navigation", "typography-copy"}):
            focused_shipswift["components"] = shipswift_pkgs["components"]
        if not dim_set or ("layout-navigation" in dim_set):
            focused_shipswift["modules"] = shipswift_pkgs["modules"]
        if not focused_shipswift:
            focused_shipswift["components"] = shipswift_pkgs["components"]

        avdlee_all_files = [
            f"{ext_root}/avdlee-swiftui-skill/skills/swiftui-expert-skill/references/liquid-glass.md",
            f"{ext_root}/avdlee-swiftui-skill/skills/swiftui-expert-skill/references/charts.md",
            f"{ext_root}/avdlee-swiftui-skill/skills/swiftui-expert-skill/references/animation-advanced.md",
            *(
                [
                    f"{ext_root}/avdlee-swiftui-skill/skills/swiftui-expert-skill/references/macos-window-styling.md",
                    f"{ext_root}/avdlee-swiftui-skill/skills/swiftui-expert-skill/references/macos-scenes.md",
                ]
                if args.platform == "desktop"
                else [
                    f"{ext_root}/avdlee-swiftui-skill/skills/swiftui-expert-skill/references/iphone-duo.md",
                ]
            ),
        ]
        avdlee_focused: list[str] = []
        if not dim_set or (dim_set & {"glass-material", "visual-style", "color-palette"}):
            avdlee_focused.append(avdlee_all_files[0])
        if not dim_set or ("chart-dataviz" in dim_set):
            avdlee_focused.append(avdlee_all_files[1])
        if not dim_set or ("motion-interaction" in dim_set):
            avdlee_focused.append(avdlee_all_files[2])
        if not dim_set or (dim_set & {"layout-navigation", "component"}):
            avdlee_focused.extend(avdlee_all_files[3:])
        if not avdlee_focused:
            avdlee_focused = list(avdlee_all_files[:1])

        genjutsu_all_files = [
            f"{ext_root}/genjutsu/skills/_jutsu/swiftui-graphics/SKILL.md",
            f"{ext_root}/genjutsu/skills/_jutsu/swiftui-motion/SKILL.md",
            f"{ext_root}/genjutsu/skills/_jutsu/motion-principles/SKILL.md",
            *(
                [f"{ext_root}/genjutsu/skills/_jutsu/desktop-principles/SKILL.md"]
                if args.platform == "desktop"
                else [f"{ext_root}/genjutsu/skills/_jutsu/mobile-principles/SKILL.md"]
            ),
        ]
        genjutsu_focused: list[str] = []
        if not dim_set or (dim_set & {"glass-material", "visual-style", "color-palette"}):
            genjutsu_focused.append(genjutsu_all_files[0])
        if not dim_set or ("motion-interaction" in dim_set):
            genjutsu_focused.extend(genjutsu_all_files[1:3])
        if not dim_set or (dim_set & {"layout-navigation", "component"}):
            genjutsu_focused.append(genjutsu_all_files[3])
        if not genjutsu_focused:
            genjutsu_focused = [genjutsu_all_files[3]]

        allowed_native = {
            "apple-design-skill": {
                "hig_dir": f"{ext_root}/apple-design-skill/references/hig",
                "code_audit": f"{ext_root}/apple-design-skill/references/code-audit.md",
                "on_demand_files": on_demand_external[:8],
            },
            "twostraws-swiftui-skill": {
                "files": [
                    f"{ext_root}/twostraws-swiftui-skill/swiftui-pro/references/design.md",
                    f"{ext_root}/twostraws-swiftui-skill/swiftui-pro/references/resizability.md",
                    f"{ext_root}/twostraws-swiftui-skill/swiftui-pro/references/views.md",
                ],
            },
            "avdlee-swiftui-skill": {
                "files": avdlee_all_files,
                "focused_files": avdlee_focused,
            },
            "dimillian-skills": {
                "files": [
                    f"{ext_root}/dimillian-skills/swiftui-liquid-glass/references/liquid-glass.md",
                    f"{ext_root}/dimillian-skills/swiftui-ui-patterns/references/scroll-reveal.md",
                    *(
                        [
                            f"{ext_root}/dimillian-skills/swiftui-ui-patterns/references/menu-bar.md",
                            f"{ext_root}/dimillian-skills/swiftui-ui-patterns/references/macos-settings.md",
                        ]
                        if args.platform == "desktop"
                        else [
                            f"{ext_root}/dimillian-skills/swiftui-ui-patterns/references/sheets.md",
                            f"{ext_root}/dimillian-skills/swiftui-ui-patterns/references/navigationstack.md",
                        ]
                    ),
                ],
            },
            "emilkowalski-skills_native": {
                "files": [
                    f"{ext_root}/emilkowalski-skills/skills/apple-design/SKILL.md",
                    f"{ext_root}/emilkowalski-skills/skills/mobile-native/SKILL.md",
                    f"{ext_root}/emilkowalski-skills/skills/remove-animations/SKILL.md",
                    f"{ext_root}/emilkowalski-skills/skills/break-ui/SKILL.md",
                ],
            },
            "expo-skills_anti_slop_only": {
                "files": [
                    f"{ext_root}/expo-skills/plugins/expo/skills/expo-design-system/references/native-slop.md",
                    f"{ext_root}/expo-skills/.claude/skills/expo-skill-eval/references/design-rubric.md",
                    f"{ext_root}/expo-skills/plugins/expo/skills/expo-web-to-native/references/false-friends.md",
                ],
                "forbidden_expo_features": ["expo-router", "eas-*", "react-native JS components"],
            },
            "shipswift": {
                "catalog": f"{ext_root}/shipswift/skills/catalog.md",
                "packages": shipswift_pkgs,
                "focused_packages": focused_shipswift,
            },
            "awesome-ios-design-md": {
                "root": f"{ext_root}/awesome-ios-design-md/design-md",
                "categories": [
                    "finance", "fitness", "music", "productivity", "social",
                    "travel", "messaging", "video", "food", "dating", "misc",
                ],
                "note": "200 real iOS apps with DESIGN-swiftui.md brand palettes, typography scales, and ButtonStyle springs.",
            },
            "genjutsu_native_only": {
                "files": genjutsu_all_files,
                "focused_files": genjutsu_focused,
                "forbidden_web_compose": ["css-native", "framer-motion", "gsap", "threejs-r3f", "compose-*"],
            },
            "claude-code-apple-skills": {
                "files": [
                    f"{ext_root}/claude-code-apple-skills/skills/design/game-feel/SKILL.md",
                    f"{ext_root}/claude-code-apple-skills/skills/design/design-system/SKILL.md",
                    f"{ext_root}/claude-code-apple-skills/skills/design/liquid-glass/SKILL.md",
                    f"{ext_root}/claude-code-apple-skills/skills/design/animation-patterns/SKILL.md",
                    f"{ext_root}/claude-code-apple-skills/skills/design/typography/SKILL.md",
                ],
            },
            "impeccable_native_only": {
                "ref_dir": f"{ext_root}/impeccable/.agents/skills/impeccable/reference",
                "files": [
                    "ios.md",
                    "audit.native.md",
                    "adapt.native.md",
                    "craft-floor.md",
                    "operate.md",
                ],
                "forbidden_web_features": ["impeccable detect", "impeccable live", "audit.md", "adapt.md"],
            },
            "ui-ux-pro-max_swiftui_only": {
                "allowed_cmd": f"python3 {ext_root}/ui-ux-pro-max-skill/.claude/skills/ui-ux-pro-max/scripts/search.py '<query>' --stack swiftui",
                "forbidden_domains": ["gsap", "google-fonts", "landing", "shadcn", "html-tailwind", "--design-system"],
            },
        }
        if args.platform == "desktop":
            allowed_native["bama_macos_only"] = {
                "files": [
                    f"{ext_root}/bama/skills/design/mac-design/SKILL.md",
                    f"{ext_root}/bama/skills/design/mac-design/design-rules.md",
                    f"{ext_root}/bama/skills/design/mac-design/toolbars.md",
                    f"{ext_root}/bama/skills/design/layout-grid/SKILL.md",
                    f"{ext_root}/bama/skills/design/design-from-app/measuring.md",
                ],
            }
        external_skill_routing = {
            "allowed_native_sources": allowed_native,
            "blocked_web_only_sources": [
                "taste-skill (design-taste-frontend: 100% Web HTML/CSS/Shader/Chrome Stop-hook)",
                "ui-ux-pro-max-skill Web sub-skills (ui-styling, banner-design, slides, design-system CSS tokens)",
                "impeccable Web detector/live overlay",
                "expo-skills React Native / JS router skills",
                *(["bama (macOS desktop only)"] if args.platform == "mobile" else []),
            ],
        }
    else:
        external_skill_routing = {
            "allowed_web_sources": {
                "taste-skill": f"{ext_root}/taste-skill/skills/taste-skill/SKILL.md",
                "impeccable_web": f"{ext_root}/impeccable/.agents/skills/impeccable/SKILL.md",
                "ui-ux-pro-max_web": f"{ext_root}/ui-ux-pro-max-skill/.claude/skills/ui-ux-pro-max/SKILL.md",
                "emilkowalski_web": f"{ext_root}/emilkowalski-skills/skills/emil-design-eng/SKILL.md",
            },
            "blocked_native_only_sources": [
                "apple-design-skill / twostraws / avdlee / dimillian / bama / shipswift SwiftUI native APIs",
                "impeccable ios.md / audit.native.md",
            ],
        }

    payload = {
        "ok": True,
        "platform": args.platform,
        "dimensions": dims,
        "container": args.container,
        "baseline_mode": cases_doc.get("baseline_mode", "plan-a-apple-hig"),
        "progressive_disclosure": {
            "distilled_in_plan_a": distilled_base,
            "distilled_note": "Already distilled in Plan A 5 Lenses; skip reading raw files by default.",
            "on_demand_external_refs": on_demand_external[:8],
            "external_hig_root": f"{ext_root}/apple-design-skill/references/hig",
            "external_skill_routing": external_skill_routing,
            "external_repos_sync_cmd": (
                f"for r in apple-design-skill avdlee-swiftui-skill awesome-ios-design-md bama "
                f"claude-code-apple-skills dimillian-skills emilkowalski-skills expo-skills "
                f"genjutsu impeccable shipswift taste-skill twostraws-swiftui-skill "
                f"ui-ux-pro-max-skill; do "
                f"git -C {ext_root}/$r pull --ff-only; done"
            ),
            "appearance_ledger": "skills/my-designer/references/appearance-ledger.md",
            "interaction_layering_ref": (
                "skills/my-designer/references/interaction-layering.md" if disclose_layering else None
            ),
            "breathing_and_clarity_ref": (
                "skills/my-designer/references/breathing-and-clarity.md" if disclose_breathing else None
            ),
            "hig_files_to_load": hig_files[:12],
            "apple_native_primitives": native_primitives[:12],
            "context_budget": {
                "vault_search_top_k": 3,
                "l1_doc_max_lines": 130,
                "active_plan_c_cap": 15,
            },
        },
        "plan_a_lenses": PLAN_A_LENSES,
        "plan_c_active_cases": active_plan_c,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _next_defect_case_id(cases_doc: dict[str, Any]) -> str:
    numbers = [int(match.group(1)) for c in cases_doc.get("active_cases", [])
               if (match := re.fullmatch(r"DC-(\d+)", str(c.get("id", ""))))]
    return f"DC-{(max(numbers) if numbers else 0) + 1:03d}"


def command_register_case(args: argparse.Namespace) -> int:
    cases_path = Path(args.cases_file)
    cases_doc = _load_json(cases_path)
    active = cases_doc.setdefault("active_cases", [])
    raw_id = (args.id or "").strip()
    if getattr(args, "auto_id", False) or not raw_id or raw_id.lower() == "auto":
        case_id = _next_defect_case_id(cases_doc)
    else:
        case_id = raw_id
    if any(c.get("id") == case_id for c in active):
        raise ValueError(f"defect case already exists: {case_id}")
    entry = {
        "id": case_id,
        "title": args.title,
        "platforms": _csv(args.platforms) or ["desktop", "mobile"],
        "dimensions": _csv(args.dimensions),
        "containers": _csv(args.containers),
        "symptom": args.symptom,
        "fix_rule": args.fix_rule,
        "forbidden_regex": args.forbidden_regex,
        "registered_at": dt.date.today().isoformat(),
    }
    active.append(entry)
    cases_doc["updated"] = dt.date.today().isoformat()
    _save_json(cases_path, cases_doc)
    print(json.dumps({"ok": True, "registered": entry, "active_count": len(active)}, ensure_ascii=False, indent=2))
    return 0


def command_flow_map(args: argparse.Namespace) -> int:
    cases_doc = _load_json(Path(args.cases_file))
    taxonomy = _load_json(Path(args.taxonomy)) if Path(args.taxonomy).is_file() else {"dimensions": []}
    ds_refs = ROOT.parent / "design-studio" / "references"
    desktop_vault = _load_json(ds_refs / "vault" / "desktop-macos.json")
    mobile_vault = _load_json(ds_refs / "vault" / "mobile-ios.json")
    web_vault = _load_json(ds_refs / "vault" / "web.json")
    catalog = _load_json(ds_refs / "catalog.json")

    l1_md_files = sorted((ROOT / "references").glob("*.md"))
    l1_line_counts = {
        f"skills/my-designer/references/{p.name}": len(p.read_text(encoding="utf-8").splitlines())
        for p in l1_md_files
    }
    max_l1_lines = 130
    active_cases_count = len(cases_doc.get("active_cases", []))
    all_within_budget = all(cnt <= max_l1_lines for cnt in l1_line_counts.values()) and active_cases_count <= 15

    payload = {
        "ok": all_within_budget,
        "architecture_doc": "skills/my-designer/references/design-flow-architecture.md",
        "entry_a_ingest": {
            "A0_raw_digest": "skills/knowledge-digest/SKILL.md -> ~/Development/Personal/knowledge-vault/",
            "A1_classify_learn": "python3 skills/design-studio/scripts/design_catalog.py classify --text '<raw>' --learn",
            "A2_vault_and_catalog_ingest": [
                "python3 skills/design-studio/scripts/design_catalog.py vault-ingest --text '<raw>'",
                "python3 skills/design-studio/scripts/design_catalog.py ingest --text '<pattern>'",
            ],
            "A3_tier2_distillation": "Update <=130-line L1 tables in skills/my-designer/references/*.md (One-In, One-Out)",
            "A4_tier3_code_sinking": "Register a generic case with verify_design_cases.py register-case --id auto",
        },
        "shared_tiers": {
            "tier_0_raw_archives": [
                "~/Development/Personal/knowledge-vault/",
                "~/Development/Personal/collected-tools/repos/",
            ],
            "tier_1_indexed_vault": {
                "taxonomy_dimensions": len(taxonomy.get("dimensions", [])),
                "desktop_macos_cards": len(desktop_vault.get("inspirations", [])),
                "mobile_ios_cards": len(mobile_vault.get("inspirations", [])),
                "web_cards": len(web_vault.get("inspirations", [])),
                "catalog_patterns": len(catalog.get("patterns", [])),
                "catalog_tools": len(catalog.get("tools", [])),
            },
            "tier_2_l1_docs_line_counts": l1_line_counts,
            "tier_3_executable_gates": {
                "design_time_rules": "shared-ci/scripts/context/design_contract.py",
                "plan_c_active_cases": active_cases_count,
                "plan_c_case_ids": [c["id"] for c in cases_doc.get("active_cases", [])],
            },
        },
        "entry_b_design": {
            "B0_lock_coordinates": "--platform (desktop|mobile|web) --dimensions <1..4> [--container <host>]",
            "B1_drawer_retrieval": "python3 skills/design-studio/scripts/design_catalog.py vault-search --platform <p> --dimension <d> --query '<q>' --top 3",
            "B2_sliced_route": "python3 skills/my-designer/scripts/verify_design_cases.py route --platform <p> --dimensions <d>",
            "B3_direct_impl": "Author design.md/spec.md (AppTheme.swift + X-Y-Z layering) -> direct product implementation (Zero Demo)",
            "B4_static_and_visual_audit": [
                "python3 shared-ci/scripts/context/design_contract.py --audit-code --platform <p> <path>",
                "python3 shared-ci/scripts/context/design_contract.py --audit-l10n --locales en,zh-Hans <path>",
            ],
        },
        "anti_explosion_valves": {
            "valve_1_zero_token_code_sinking": "shared-ci/scripts/context/design_contract.py",
            "valve_2_3d_coordinate_slicing": True,
            "valve_3_top_k_and_line_caps": {
                "vault_search_top_k": 3,
                "l1_doc_max_lines": max_l1_lines,
                "active_plan_c_cap": 15,
            },
            "valve_4_one_in_one_out_compaction": True,
            "all_l1_docs_within_budget": all_within_budget,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all_within_budget else 1


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cases-file", default=str(DEFAULT_CASES))
    common.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("flow-map", parents=[common])

    route = sub.add_parser("route", parents=[common])
    route.add_argument("--platform", choices=["desktop", "mobile", "web"], required=True)
    route.add_argument("--dimensions", default="")
    route.add_argument("--container", default=None)

    reg = sub.add_parser("register-case", parents=[common])
    reg.add_argument("--id", default="auto", help="Case ID (e.g. DC-005) or 'auto' to assign the next sequential DC-00N")
    reg.add_argument("--auto-id", action="store_true", help="Automatically assign the next sequential DC-00N ID")
    reg.add_argument("--title", required=True)
    reg.add_argument("--platforms", default="desktop,mobile")
    reg.add_argument("--dimensions", default="")
    reg.add_argument("--containers", default="")
    reg.add_argument("--symptom", required=True)
    reg.add_argument("--fix-rule", required=True)
    reg.add_argument("--forbidden-regex", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "flow-map":
        return command_flow_map(args)
    if args.command == "route":
        return command_route(args)
    if args.command == "register-case":
        return command_register_case(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
