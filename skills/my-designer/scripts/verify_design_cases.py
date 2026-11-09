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

PLAN_A_CODE_RULES = [
    {
        "id": "HIG-LG-CONTENT",
        "severity": "High",
        "platforms": ["desktop", "mobile"],
        "citation": "liquid-glass.md › Where the material belongs",
        "pattern": re.compile(r"(?:List|Form|Table|ForEach)\s*\{[\s\S]{0,240}?\.glassEffect\("),
        "message": "Liquid Glass applied inside content list/table rows; keep glass in the floating functional layer.",
    },
    {
        "id": "HIG-IOS-SCREEN-BOUNDS",
        "severity": "High",
        "platforms": ["mobile", "desktop"],
        "citation": "WWDC26-278 & designing-for-iphone-duo.md › Resizable Windows & Size Classes",
        "pattern": re.compile(
            r"(?:\bUIScreen\.main\b|\buserInterfaceIdiom\s*==\s*\.phone\b|\binterfaceOrientation\.(?:isLandscape|isPortrait)\b)"
        ),
        "message": "Avoid UIScreen.main, userInterfaceIdiom == .phone, or interfaceOrientation layout branches; iPhone apps resize freely in iOS 27 / macOS 27 iPhone Mirroring where idiom stays .phone and orientation stays .portrait—use horizontalSizeClass, ViewThatFits, or effectiveGeometry.",
    },
    {
        "id": "HIG-COLOR-RAW-HEX",
        "severity": "Medium",
        "platforms": ["desktop", "mobile"],
        "citation": "color.md › Best practices",
        "pattern": re.compile(r"Color\(\s*red\s*:[^)]+green\s*:[^)]+blue\s*:"),
        "message": "Prefer Apple semantic system colors or Asset Catalog adaptive color sets over inline RGB literals.",
    },
    {
        "id": "HIG-COMPAT-FLAG",
        "severity": "High",
        "platforms": ["desktop", "mobile"],
        "citation": "liquid-glass.md › SwiftUI, UIKit, and AppKit",
        "pattern": re.compile(r"UIDesignRequiresCompatibility"),
        "message": "UIDesignRequiresCompatibility is temporary and ignored on modern SDKs; adopt native Liquid Glass chrome.",
    },
    {
        "id": "SLOP-PURPLE-GRADIENT",
        "severity": "High",
        "platforms": ["desktop", "mobile"],
        "citation": "native-slop.md › Tell #4 The Purple-Gradient Hero",
        "pattern": re.compile(r"LinearGradient\([^)]*colors\s*:\s*\[\s*(?:\.purple\s*,\s*\.blue|\.blue\s*,\s*\.purple|\.indigo\s*,\s*\.purple)"),
        "message": "Avoid generic AI purple-blue gradient heroes; use purposeful brand seed tokens or subtle surface luminance.",
    },
    {
        "id": "SLOP-LEFT-ACCENT-STRIPE",
        "severity": "Medium",
        "platforms": ["desktop", "mobile"],
        "citation": "anti-ai-slop.md › Left-border accent card",
        "pattern": re.compile(r"Rectangle\(\)\s*\.fill\([^)]+\)\s*\.frame\(\s*width\s*:\s*[3-6]\b"),
        "message": "Avoid Web-style left-border accent stripe cards; use native semantic badges, SF Symbols, or clean list hierarchy.",
    },
    {
        "id": "HIG-MACOS-HOVER-EFFECT",
        "severity": "High",
        "platforms": ["desktop"],
        "citation": "genjutsu/desktop-principles › macOS pointer conventions",
        "pattern": re.compile(r"\.hoverEffect\s*\("),
        "message": ".hoverEffect(_:) is unavailable on macOS; use .onHover / .pointerStyle on macOS or guard with #if !os(macOS).",
    },
    {
        "id": "SLOP-MUDDY-BLACK-GRADIENT",
        "severity": "Medium",
        "platforms": ["desktop", "mobile"],
        "citation": "CL-005 › Hue-Arc Luminous Gradient Rule",
        "pattern": re.compile(r"LinearGradient\([^)]*colors\s*:\s*\[[^\]]*\.black\b"),
        "message": "Avoid straight-to-black RGB gradients (muddy dead zone); step through adjacent high-luminance hues along the color wheel arc.",
    },
    {
        "id": "SHIPSWIFT-SWWALLET-FLATTENED",
        "severity": "High",
        "platforms": ["desktop", "mobile"],
        "citation": "DC-001 › ShipSwift Spatial Signature Preservation (SWWallet.swift)",
        "pattern": re.compile(r"struct\s+SWWallet\b[\s\S]{0,600}?VStack[^{]*\{\s*ForEach"),
        "message": "Do not flatten ShipSwift SWWallet into a VStack/ForEach list; preserve its ZStack + zIndex + offset(y:) 3D pocket stack geometry (~220pt collapsed height).",
    },
    {
        "id": "SLOP-DUPLICATE-DATA-REPRESENTATION",
        "severity": "High",
        "platforms": ["desktop", "mobile"],
        "citation": "DC-002 › No Duplicate Same-Source Data Representation",
        "pattern": re.compile(r"(?:SWDonutChart|SectorMark)[\s\S]{0,800}?ProgressView\(\s*value\s*:"),
        "message": "Never render both a Donut/Sector chart with legend AND a redundant ProgressView bar list for the same dataset in one pane; use a Segmented Lens or interactive chart slices.",
    },
    {
        "id": "HIG-LAZY-LEAF-CONDITIONAL",
        "severity": "High",
        "platforms": ["desktop", "mobile"],
        "citation": "WWDC26-321 › Subview loading 1:1 resolution",
        "pattern": re.compile(r"Lazy(?:V|H)Stack[^{]*\{[\s\S]{0,220}?ForEach\([^)]*\)\s*\{\s*\w+\s+in\s+if\b"),
        "message": "Avoid top-level 'if' filtering inside LazyStack ForEach closures (causes 0/1 dynamic subview count and offscreen memory retention); filter data in @Query(#Predicate) or the collection instead.",
    },
    {
        "id": "HIG-APPKIT-LEGACY-MOUSELOOP",
        "severity": "Medium",
        "platforms": ["desktop"],
        "citation": "WWDC26-289 › Modernize your AppKit app",
        "pattern": re.compile(r"override\s+func\s+mouseDown\b[\s\S]{0,300}?nextEvent\(\s*matching\s*:"),
        "message": "Avoid overriding mouseDown with blocking nextEvent tracking loops; use NSGestureRecognizer, NSControl.Events (.trackingEndedOutside), or NSTextSelectionManager.",
    },
    {
        "id": "HIG-DRAG-WILL-BEGIN-UI",
        "severity": "Medium",
        "platforms": ["mobile", "desktop"],
        "citation": "WWDC26-278 › Apple Intelligence & Drag Session",
        "pattern": re.compile(
            r"dragInteraction\([^)]*sessionWillBegin[\s\S]{0,240}?(?:present\(|UIAlertController|withAnimation)"
        ),
        "message": "Do not trigger modals or animations in sessionWillBegin (Siri / Apple Intelligence invokes drag sessions headlessly without touch); move UI feedback to sessionDidMove.",
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


def command_audit_code(args: argparse.Namespace) -> int:
    cases_doc = _load_json(Path(args.cases_file))
    target = Path(args.path)
    files = [target] if target.is_file() else sorted(target.rglob("*.swift"))
    dims = _csv(args.dimensions)
    active_plan_c = match_active_cases(cases_doc, args.platform, dims, args.container)

    findings: list[dict[str, Any]] = []
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for rule in PLAN_A_CODE_RULES:
            if args.platform not in rule["platforms"]:
                continue
            for match in rule["pattern"].finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                findings.append({
                    "source": "plan-a-hig",
                    "id": rule["id"],
                    "severity": rule["severity"],
                    "file": str(file_path),
                    "line": line_no,
                    "citation": rule["citation"],
                    "message": rule["message"],
                })
        for case in active_plan_c:
            regex_str = case.get("forbidden_regex")
            if not regex_str:
                continue
            pattern = re.compile(regex_str)
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                findings.append({
                    "source": "plan-c-defect-case",
                    "id": case["id"],
                    "severity": "High",
                    "file": str(file_path),
                    "line": line_no,
                    "citation": f"defect-cases/index.json › {case['id']}",
                    "message": f"{case['title']}: {case['fix_rule']}",
                })

    payload = {
        "ok": len(findings) == 0,
        "platform": args.platform,
        "scanned_files": len(files),
        "plan_c_cases_evaluated": [c["id"] for c in active_plan_c],
        "findings": findings,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not findings else 1


def _normalize_format_tokens(text: str) -> list[str]:
    stripped = text.replace("%%", "")
    raw = re.findall(r"%(?:\d+\$)?[-+#0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|h|ll|l|q|z|t|L)?[@dDuUxXoOfeEgGcCsSpaA]", stripped)
    normalized = []
    for tok in raw:
        clean = re.sub(r"^%\d+\$", "%", tok)
        normalized.append(clean)
    return sorted(normalized)


def command_audit_l10n(args: argparse.Namespace) -> int:
    target = Path(args.path).resolve()
    locales = _csv(args.locales) or ["en", "zh-Hans"]
    excluded_parts = {".build", "DerivedData", ".git", "node_modules"}

    def _is_excluded(p: Path) -> bool:
        return any(part in excluded_parts for part in p.parts)

    catalogs = [p for p in ( [target] if target.name.endswith(".xcstrings") else sorted(target.rglob("*.xcstrings")) ) if not _is_excluded(p)]
    swift_files = [] if target.is_file() else [p for p in sorted(target.rglob("*.swift")) if not _is_excluded(p) and "Tests" not in p.parts]
    info_plists = [] if target.is_file() else [p for p in sorted(target.rglob("Info.plist")) if not _is_excluded(p)]

    findings: list[dict[str, Any]] = []
    all_catalog_keys: set[str] = set()

    for cat_path in catalogs:
        try:
            doc = _load_json(cat_path)
        except Exception as exc:
            findings.append({"id": "L10N-PARSE-ERR", "severity": "Critical", "file": str(cat_path), "message": str(exc)})
            continue
        source_lang = doc.get("sourceLanguage", "en")
        strings = doc.get("strings", {})
        for key, entry in strings.items():
            all_catalog_keys.add(key)
            locs = entry.get("localizations", {})
            base_val = locs.get(source_lang, {}).get("stringUnit", {}).get("value", key)
            base_tokens = _normalize_format_tokens(base_val)
            for loc in locales:
                unit = locs.get(loc, {}).get("stringUnit", {})
                val = unit.get("value", "")
                if not val:
                    findings.append({
                        "id": "L10N-MISSING-LOCALE",
                        "severity": "High",
                        "file": str(cat_path),
                        "key": key,
                        "locale": loc,
                        "message": f"Missing '{loc}' translation for key '{key}'.",
                    })
                    continue
                if "." in key and val == key:
                    findings.append({
                        "id": "L10N-RAW-KEY-LEAK",
                        "severity": "High",
                        "file": str(cat_path),
                        "key": key,
                        "locale": loc,
                        "message": f"Translation for '{key}' in '{loc}' equals the raw dotted key.",
                    })
                loc_tokens = _normalize_format_tokens(val)
                if loc_tokens != base_tokens:
                    findings.append({
                        "id": "L10N-FORMAT-MISMATCH",
                        "severity": "Critical",
                        "file": str(cat_path),
                        "key": key,
                        "locale": loc,
                        "message": f"Format specifiers in '{loc}' ({loc_tokens}) do not match '{source_lang}' ({base_tokens}).",
                    })

    key_pattern = re.compile(
        r'(?:DashboardL10n\.(?:tr|format)|localized|String\(\s*localized)\s*\(\s*"([a-z0-9_]+(?:\.[a-z0-9_]+)+(?:\s*%[@lld]+)?)"'
    )
    if all_catalog_keys:
        for sf in swift_files:
            try:
                text = sf.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in key_pattern.finditer(text):
                k = match.group(1)
                if k not in all_catalog_keys:
                    line_no = text.count("\n", 0, match.start()) + 1
                    findings.append({
                        "id": "L10N-UNREGISTERED-KEY",
                        "severity": "High",
                        "file": str(sf),
                        "line": line_no,
                        "key": k,
                        "message": f"Key '{k}' referenced in source is missing from Localizable.xcstrings.",
                    })

    for plist_path in info_plists:
        try:
            plist_text = plist_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "CFBundleLocalizations" not in plist_text:
            findings.append({
                "id": "L10N-INFOPLIST-LOCALES",
                "severity": "Medium",
                "file": str(plist_path),
                "message": "Info.plist is missing CFBundleLocalizations array; non-default OS languages may fall back to English.",
            })

    payload = {
        "ok": len(findings) == 0,
        "locales_checked": locales,
        "catalogs_scanned": len(catalogs),
        "total_keys": len(all_catalog_keys),
        "swift_files_scanned": len(swift_files),
        "findings": findings,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not findings else 1


def command_register_case(args: argparse.Namespace) -> int:
    cases_path = Path(args.cases_file)
    cases_doc = _load_json(cases_path)
    active = cases_doc.setdefault("active_cases", [])
    if any(c.get("id") == args.id for c in active):
        raise ValueError(f"defect case already exists: {args.id}")
    entry = {
        "id": args.id,
        "title": args.title,
        "platforms": _csv(args.platforms) or ["desktop", "mobile"],
        "dimensions": _csv(args.dimensions),
        "containers": _csv(args.containers),
        "symptom": args.symptom,
        "fix_rule": args.fix_rule,
        "forbidden_regex": args.forbidden_regex,
        "created_at": dt.date.today().isoformat(),
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
            "A4_tier3_code_sinking": "Add regex to PLAN_A_CODE_RULES or run verify_design_cases.py register-case --id DC-xxx",
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
                "catalog_patterns": len(catalog.get("patterns", [])),
                "catalog_tools": len(catalog.get("tools", [])),
            },
            "tier_2_l1_docs_line_counts": l1_line_counts,
            "tier_3_executable_gates": {
                "plan_a_code_rules": len(PLAN_A_CODE_RULES),
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
                "python3 skills/my-designer/scripts/verify_design_cases.py audit-code --platform <p> --path <dir>",
                "python3 skills/my-designer/scripts/verify_design_cases.py audit-l10n --path <dir> --locales en,zh-Hans",
            ],
        },
        "anti_explosion_valves": {
            "valve_1_zero_token_code_sinking": len(PLAN_A_CODE_RULES) + active_cases_count,
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

    audit = sub.add_parser("audit-code", parents=[common])
    audit.add_argument("--platform", choices=["desktop", "mobile"], required=True)
    audit.add_argument("--path", required=True)
    audit.add_argument("--dimensions", default="")
    audit.add_argument("--container", default=None)

    l10n = sub.add_parser("audit-l10n", parents=[common])
    l10n.add_argument("--path", required=True, help="Repo root, app target directory, or .xcstrings file")
    l10n.add_argument("--locales", default="en,zh-Hans", help="Comma-separated required locales")

    reg = sub.add_parser("register-case", parents=[common])
    reg.add_argument("--id", required=True)
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
    if args.command == "audit-code":
        return command_audit_code(args)
    if args.command == "audit-l10n":
        return command_audit_l10n(args)
    if args.command == "register-case":
        return command_register_case(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
