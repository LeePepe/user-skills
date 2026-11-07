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
        "platforms": ["mobile"],
        "citation": "designing-for-iphone-duo.md › Best practices",
        "pattern": re.compile(r"\bUIScreen\.main\.bounds\b"),
        "message": "Avoid UIScreen.main.bounds; use GeometryReader, window size, or horizontalSizeClass for adaptive layouts.",
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
    ext_root = "~/Development/Personal/collected-tools/repos"
    if args.platform in ("desktop", "mobile"):
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
                "files": [
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
                ],
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
                f"for r in apple-design-skill avdlee-swiftui-skill bama dimillian-skills "
                f"emilkowalski-skills expo-skills impeccable shipswift taste-skill "
                f"twostraws-swiftui-skill ui-ux-pro-max-skill; do "
                f"git -C {ext_root}/$r pull --ff-only; done"
            ),
            "hig_files_to_load": hig_files[:12],
            "apple_native_primitives": native_primitives[:12],
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


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cases-file", default=str(DEFAULT_CASES))
    common.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

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
