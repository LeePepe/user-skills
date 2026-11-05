#!/usr/bin/env python3
"""Manage the design-studio tool and interaction-pattern catalog."""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "references" / "catalog.json"
DEFAULT_MARKDOWN = ROOT / "references" / "toolchain-20-matrix.md"
PLATFORMS = {"web", "mobile", "shared"}
SURFACE_MODES = {"Operate", "Read", "Persuade", "Experience", "Video/Offline"}
CATEGORIES = {
    "code-to-video", "core-ui-motion", "3d-physics", "audio-beat",
    "asset-export", "gesture-physical", "confirmation-safety",
    "spatial-navigation", "micro-feedback-social", "drag-drop-choreography",
    "data-linkage", "filter-sort-recovery", "master-detail-flow", "layout-craft",
}
INGEST_PROFILES = {
    "drag-drop-choreography": (
        ("drag and drop", "drag-drop", "drop target", "拖放", "拖入", "拖拽", "文件夹"),
        "idle -> drag -> target_preview -> drop_commit/cancel -> settle",
        "Motion pointer drag with hit testing and a keyboard move action.",
        "SwiftUI draggable/dropDestination with StrataMotion and an accessible move action.",
        "Keep source data and counts unchanged until a valid drop commits.",
    ),
    "confirmation-safety": (
        ("confirm", "confirmation", "destructive", "delete", "undo", "确认", "删除", "撤销"),
        "idle -> intent -> confirmation -> commit/cancel -> recoverable_result",
        "Explicit confirmation state with accessible buttons and pending/error recovery.",
        "SwiftUI confirmationDialog with pending/error state and SensoryFeedback on commit.",
        "Commit once after confirmation; never use motion as the sole safety signal.",
    ),
    "master-detail-flow": (
        ("master-detail", "detail pane", "detail panel", "详情", "主从"),
        "list_ready -> select_record -> detail_loading -> detail_ready/error -> list_ready",
        "Stable selectedId and keyed master list with in-place detail loading/error states.",
        "SwiftUI NavigationSplitView or compact detail sheet preserving selection and scroll.",
        "Preserve list order, filters, and scroll; expose the selected record clearly.",
    ),
    "data-linkage": (
        ("linked", "linkage", "cross-filter", "chart", "summary", "联动", "图表", "汇总", "统计"),
        "ready -> update_shared_state -> recompute_views_and_metrics -> synced -> reset",
        "Shared state with pure derived selectors for linked views and Basalt numeric readouts.",
        "SwiftUI observable state with computed summaries and numericText transitions.",
        "Use one source of truth; preserve independent query, sort, and selection context.",
    ),
    "filter-sort-recovery": (
        ("filter", "sort", "search", "empty state", "no results", "筛选", "排序", "搜索", "无结果", "恢复"),
        "ready -> filter_search_sort -> results/empty/error -> scoped_reset -> restored",
        "Independent query/filter/sort state, derived results, and scoped reset controls.",
        "SwiftUI searchable and sort descriptors with contextual ContentUnavailableView recovery.",
        "Reset only the requested constraint; preserve other filters and sort order.",
    ),
    "spatial-navigation": (
        ("navigation", "navigate", "toolbar", "scrub", "parallax", "导航", "工具栏", "滑选", "视差"),
        "idle -> focus_preview -> destination_commit/cancel -> transition -> settled",
        "Motion shared-layout focus and navigation transitions with keyboard navigation.",
        "SwiftUI navigation state with matchedGeometryEffect and StrataMotion.",
        "Keep focus and destination labels visible; preserve a clear return path.",
    ),
    "micro-feedback-social": (
        ("reaction", "feedback", "like", "badge", "反馈", "表情", "点赞", "气泡", "徽标"),
        "idle -> press -> feedback_preview -> commit/cancel -> acknowledged -> idle",
        "Motion press feedback and Basalt semantic badges with accessible status announcements.",
        "SwiftUI SensoryFeedback and StrataMotion with numericText for changing counts.",
        "Keep counts derived from committed state; feedback must not depend on color alone.",
    ),
    "gesture-physical": (
        ("gesture", "swipe", "peel", "spring", "drag", "pull", "手势", "滑动", "揭角", "拉绳", "弹簧", "拖动"),
        "idle -> gesture -> threshold -> commit/cancel -> spring_settle -> idle",
        "Motion pointer tracking with bounded transforms and tokenized spring settling.",
        "SwiftUI DragGesture with StrataMotion and SensoryFeedback at the commit threshold.",
        "Bound displacement and trigger once per gesture; provide a non-gesture alternative.",
    ),
    "layout-craft": (
        ("layout", "grid", "spacing", "responsive", "alignment", "布局", "网格", "间距", "响应式", "对齐"),
        "ready -> container_or_content_change -> measure -> reflow -> stable",
        "Container queries and CSS grid/flex with Basalt spacing and typography tokens.",
        "SwiftUI Layout/ViewThatFits with DesignTokens spacing and Dynamic Type support.",
        "Use container-driven width budgets; prevent overlap and numeric truncation.",
    ),
}
IP_CATEGORIES = {"data-linkage", "filter-sort-recovery", "master-detail-flow", "layout-craft"}


def load(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def words(value):
    return set(re.findall(r"[\w]+", str(value).casefold(), re.UNICODE))


def grams(value, size=3):
    value = re.sub(r"\s+", "", str(value).casefold())
    return {value[i:i + size] for i in range(max(0, len(value) - size + 1))}


def similarity(query, candidate):
    query = str(query).casefold().strip()
    candidate = str(candidate).casefold().strip()
    if not query or not candidate:
        return 0.0
    if query in candidate:
        return 1.0
    a, b = words(query), words(candidate)
    token_score = len(a & b) / max(1, len(a | b))
    ag, bg = grams(query), grams(candidate)
    gram_score = len(ag & bg) / max(1, len(ag | bg))
    return round(max(token_score, gram_score), 4)


def searchable(item):
    return " ".join(
        str(item.get(key, ""))
        for key in ("id", "name", "aliases", "source_prompt_zh", "canonical_prompt", "category")
    )


def validate(catalog):
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1:
        raise ValueError("catalog must be an object with schema_version 1")
    for collection, prefix in (("tools", "T-"), ("patterns", ("MP-", "IP-"))):
        if not isinstance(catalog.get(collection), list):
            raise ValueError(f"{collection} must be a list")
        for item in catalog[collection]:
            required = {"id", "name", "aliases", "category", "platforms", "surface_modes",
                        "motion_tier", "usage_count", "rating", "last_used_at", "usage_history"}
            missing = required - item.keys()
            if missing:
                raise ValueError(f"{item.get('id', '?')} missing {sorted(missing)}")
            valid_prefix = prefix if isinstance(prefix, tuple) else (prefix,)
            if not str(item["id"]).startswith(valid_prefix):
                raise ValueError(f"invalid {collection} id: {item['id']}")
            if not set(item["platforms"]) <= PLATFORMS:
                raise ValueError(f"invalid platforms for {item['id']}")
            if not set(item["surface_modes"]) <= SURFACE_MODES:
                raise ValueError(f"invalid surface modes for {item['id']}")
            if item["motion_tier"] not in {"M1", "M2", "M3", "M4"}:
                raise ValueError(f"invalid motion tier for {item['id']}")
            if item["rating"] not in {"liked", "neutral", "disliked", "unrated"}:
                raise ValueError(f"invalid rating for {item['id']}")
            if not isinstance(item["usage_count"], int) or item["usage_count"] < 0:
                raise ValueError(f"invalid usage count for {item['id']}")
    return catalog


def all_items(catalog, kind="all"):
    if kind == "tool":
        return catalog["tools"]
    if kind == "pattern":
        return catalog["patterns"]
    return catalog["tools"] + catalog["patterns"]


def matches(catalog, query, kind="all", platform=None, surface_mode=None, threshold=0.25):
    result = []
    for item in all_items(catalog, kind):
        if platform and platform not in item["platforms"]:
            continue
        if surface_mode and surface_mode not in item["surface_modes"]:
            continue
        score = similarity(query, searchable(item))
        if score >= threshold:
            result.append({"id": item["id"], "name": item["name"], "score": score,
                           "kind": "tool" if item["id"].startswith("T-") else "pattern"})
    result.sort(key=lambda item: (-item["score"], item["id"]))
    return {"matches": result, "has_duplicate": any(item["score"] >= threshold for item in result)}


def csv_values(value):
    return [part.strip() for part in value.split(",") if part.strip()]


def duplicate(catalog, candidate, collection, threshold=0.55):
    fields = ("name", "aliases", "source_prompt_zh", "canonical_prompt") if collection == "patterns" else (
        "name", "aliases", "category")
    for item in catalog[collection]:
        if item["id"] == candidate["id"]:
            continue
        for field in fields:
            candidate_value = candidate.get(field, "")
            existing_value = item.get(field, "")
            if candidate_value and existing_value and similarity(candidate_value, existing_value) >= threshold:
                return item
    return None


def classify_prompt(text):
    def score(profile):
        return sum(
            bool(re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", text.casefold()))
            if keyword.isascii() else keyword in text
            for keyword in profile[0]
        )

    category = max(INGEST_PROFILES, key=lambda key: score(INGEST_PROFILES[key]))
    return category if score(INGEST_PROFILES[category]) else "layout-craft"


def ingest(args, catalog, catalog_path, markdown_path):
    text = (args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")).strip()
    if not text:
        raise ValueError("input prompt must not be empty")
    category = args.category or classify_prompt(text)
    prefix = "IP" if category in IP_CATEGORIES else "MP"
    numbers = [
        int(match.group(1))
        for item in catalog["patterns"]
        if (match := re.fullmatch(rf"{prefix}-(\d+)", item["id"]))
    ]
    pattern_id = args.id or f"{prefix}-{max(numbers, default=0) + 1:03d}"
    if not pattern_id.startswith(prefix + "-"):
        raise ValueError(f"{category} requires an {prefix}- prefixed id")
    if any(item["id"] == pattern_id for item in all_items(catalog)):
        raise ValueError(f"catalog id already exists: {pattern_id}")
    _, state, web, mobile, guardrails = INGEST_PROFILES[category]
    normalized = " ".join(text.split())
    item = {
        "id": pattern_id, "name": normalized[:80], "aliases": [], "category": category,
        "platforms": args.platforms, "surface_modes": args.surface_modes,
        "motion_tier": args.motion_tier or ("M1" if prefix == "IP" else "M2"),
        "source_prompt_zh": text, "canonical_prompt": normalized,
        "state_machine": state, "web_implementation": web, "mobile_implementation": mobile,
        "token_guardrails": (
            f"{guardrails} Reuse DesignTokens/StrataMotion/Basalt tokens; support Light/Dark, "
            ">=44x44pt hit targets, and prefers-reduced-motion / Reduce Motion without spatial motion."
        ),
        "usage_count": 0, "rating": "unrated", "last_used_at": None, "usage_history": [],
    }
    found = [
        {"id": existing["id"], "name": existing["name"]}
        for existing in catalog["patterns"]
        if duplicate({"patterns": [existing]}, item, "patterns")
    ]
    blocked = bool(found) and not args.force
    result = {
        "pattern": item,
        "classification": {"prefix": prefix, "category": category,
                           "source": "override" if args.category else "automatic"},
        "duplicate_matches": found,
        "duplicate_decision": "blocked" if blocked else ("forced" if found else "unique"),
        "dry_run": args.dry_run,
        "created": False,
        "paths": {"catalog": str(catalog_path), "markdown": str(markdown_path)},
        "actions": [],
    }
    if not blocked:
        catalog["patterns"].append(item)
        validate(catalog)
        if not args.dry_run:
            save(catalog_path, catalog)
            markdown_path.write_text(render(catalog), encoding="utf-8")
            result["created"] = True
            result["actions"] = ["write_catalog", "render_markdown"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if blocked else 0


def render(catalog):
    lines = [
        "# Design Studio Reference Catalog & Toolchain Matrix",
        "",
        "This matrix is generated from `catalog.json`; use `design_catalog.py` to search,",
        "extend, rate, and re-render the catalog.",
        "",
        "## M1–M4 Expressiveness Decision Table",
        "",
        "| Tier | Intent | Typical surfaces | Guardrail |",
        "|---|---|---|---|",
        "| M1 | Micro-feedback | Operate, Read | 100–150ms press, numeric transition, reduced-motion fallback |",
        "| M2 | Spatial & gesture choreography | Operate, Read, Experience | Deterministic state machine, spring settle, 44pt hit targets |",
        "| M3 | Narrative / 3D hero | Persuade, Experience | Tokenized timeline, progressive fallback, no dense-form usage |",
        "| M4 | Code-to-video | Video/Offline, Persuade | Deterministic frame render and reproducible export |",
        "",
        "## Web vs. Mobile-Native vs. Shared",
        "",
        "| Boundary | Contract |",
        "|---|---|",
        "| Shared | Physical state machines, DesignTokens/StrataMotion timing, Lottie, Blender GLTF/USDZ, Remotion/FFmpeg promo outputs |",
        "| Web | Motion, Anime.js, Three.js/Postprocessing/Three.quarks/Jolt/GPU Pathtracer, DEV-only Tweakpane |",
        "| Mobile-Native | StrataMotion, matchedGeometryEffect, KeyframeAnimator, PhaseAnimator, Canvas, ShaderLibrary, SensoryFeedback, numericText |",
        "",
        "## Extensible Toolchain",
        "",
        "| ID | Tool | Category | Platforms | Surface modes | Tier | Automation |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in catalog["tools"]:
        lines.append("| {id} | {name} | {category} | {platforms} | {surface_modes} | {tier} | {automation} |".format(
            id=item["id"], name=item["name"], category=item["category"],
            platforms=", ".join(item["platforms"]), surface_modes=", ".join(item["surface_modes"]),
            tier=item["motion_tier"], automation=item["automation_mode"]))
    lines += ["", "## Interactive Pattern Catalog", "",
              "| ID | Pattern | Category | Platforms | Tier | State machine |",
              "|---|---|---|---|---|---|"]
    for item in catalog["patterns"]:
        lines.append("| {id} | {name} | {category} | {platforms} | {tier} | `{state}` |".format(
            id=item["id"], name=item["name"], category=item["category"],
            platforms=", ".join(item["platforms"]), tier=item["motion_tier"],
            state=item["state_machine"]))
    lines += ["", "### Pattern details", ""]
    for item in catalog["patterns"]:
        lines += [
            f"#### {item['id']} · {item['name']}",
            f"- Prompt: {item['source_prompt_zh']}",
            f"- Canonical: {item['canonical_prompt']}",
            f"- Web: {item['web_implementation']}",
            f"- Mobile: {item['mobile_implementation']}",
            f"- Guardrails: {item['token_guardrails']}",
            "",
        ]
    lines += ["## Usage & Preference Ledger", "",
              "| ID | Usage count | Rating | Last used |",
              "|---|---:|---|---|"]
    for item in all_items(catalog):
        lines.append(f"| {item['id']} | {item['usage_count']} | {item['rating']} | {item['last_used_at'] or '—'} |")
    lines += [
        "", "## Code / CLI Recipes", "",
        "```bash",
        'python3 skills/design-studio/scripts/design_catalog.py search --query "滑动确认"',
        "python3 skills/design-studio/scripts/design_catalog.py recommend --platform web --surface-mode Operate",
        'python3 skills/design-studio/scripts/design_catalog.py record-use --id MP-002 --context "checkout" --rating liked',
        "```", "",
    ]
    return "\n".join(lines)


def catalog_paths(args):
    return Path(args.catalog), Path(args.markdown)


def command(args):
    catalog_path, markdown_path = catalog_paths(args)
    catalog = validate(load(catalog_path))
    if args.command == "ingest":
        return ingest(args, catalog, catalog_path, markdown_path)
    elif args.command == "search":
        print(json.dumps(matches(catalog, args.query, args.kind, args.platform,
                                 args.surface_mode, args.threshold), ensure_ascii=False, indent=2))
    elif args.command == "render":
        rendered = render(catalog)
        if args.check:
            if markdown_path.read_text(encoding="utf-8") != rendered:
                raise ValueError(f"{markdown_path} is out of sync; run render")
        else:
            markdown_path.write_text(rendered, encoding="utf-8")
    elif args.command in {"add-pattern", "add-tool"}:
        collection = "patterns" if args.command == "add-pattern" else "tools"
        item = vars(args).copy()
        item.pop("command", None)
        item.pop("catalog", None)
        item.pop("markdown", None)
        item.pop("force", None)
        for key in ("platforms", "surface_modes", "aliases"):
            if key in item:
                item[key] = csv_values(item[key])
        item.update({
            "web_implementation": item.pop("web_impl", ""),
            "mobile_implementation": item.pop("mobile_impl", ""),
            "token_guardrails": item.pop("guardrails", ""),
        })
        item["usage_count"], item["rating"], item["last_used_at"], item["usage_history"] = 0, "unrated", None, []
        found = duplicate(catalog, item, collection)
        if found and not args.force:
            print(f"duplicate: {found['id']} {found['name']}", file=sys.stderr)
            return 2
        catalog[collection].append(item)
        save(catalog_path, catalog)
        markdown_path.write_text(render(catalog), encoding="utf-8")
    elif args.command == "record-use":
        found = next((item for item in all_items(catalog) if item["id"] == args.id), None)
        if not found:
            raise ValueError(f"unknown catalog id: {args.id}")
        found["usage_count"] += 1
        found["last_used_at"] = dt.date.today().isoformat()
        if args.rating:
            found["rating"] = args.rating
        found["usage_history"].append({"timestamp": dt.datetime.now().isoformat(timespec="seconds"),
                                        "context": args.context, "rating": args.rating or found["rating"],
                                        "notes": args.notes or ""})
        save(catalog_path, catalog)
        markdown_path.write_text(render(catalog), encoding="utf-8")
    elif args.command == "recommend":
        result = []
        for item in all_items(catalog, args.kind):
            if item["rating"] == "disliked":
                continue
            if args.platform and args.platform not in item["platforms"]:
                continue
            if args.surface_mode and args.surface_mode not in item["surface_modes"]:
                continue
            if args.motion_tier and item["motion_tier"] != args.motion_tier:
                continue
            result.append(item)
        result.sort(key=lambda item: (0 if item["rating"] == "liked" else 1, -item["usage_count"], item["id"]))
        print(json.dumps({"recommendations": result}, ensure_ascii=False, indent=2))
    return 0


def pattern_id(value):
    if not re.fullmatch(r"(IP|MP)-[0-9]+", value):
        raise argparse.ArgumentTypeError("id must be IP-<number> or MP-<number>")
    return value


def csv_choices(value, choices):
    values = csv_values(value)
    if not values or not set(values) <= choices:
        raise argparse.ArgumentTypeError(f"expected comma-separated values from {', '.join(sorted(choices))}")
    return values


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    common.add_argument("--markdown", default=str(DEFAULT_MARKDOWN))
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    intake = sub.add_parser("ingest", parents=[common])
    source = intake.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file")
    intake.add_argument("--id", type=pattern_id)
    intake.add_argument("--category", choices=sorted(INGEST_PROFILES))
    intake.add_argument("--platforms", type=lambda value: csv_choices(value, PLATFORMS),
                        default="web,mobile,shared")
    intake.add_argument("--surface-modes", type=lambda value: csv_choices(value, SURFACE_MODES),
                        default="Operate,Read")
    intake.add_argument("--motion-tier", choices=["M1", "M2", "M3", "M4"])
    intake.add_argument("--force", action="store_true")
    intake.add_argument("--dry-run", action="store_true")
    search = sub.add_parser("search", parents=[common])
    search.add_argument("--query", required=True)
    search.add_argument("--kind", choices=["all", "tool", "pattern"], default="all")
    search.add_argument("--platform", choices=sorted(PLATFORMS))
    search.add_argument("--surface-mode", choices=sorted(SURFACE_MODES))
    search.add_argument("--threshold", type=float, default=0.25)
    render_parser = sub.add_parser("render", parents=[common])
    render_parser.add_argument("--check", action="store_true")
    for name in ("add-pattern", "add-tool"):
        add = sub.add_parser(name, parents=[common])
        add.add_argument("--id", required=True)
        add.add_argument("--name", required=True)
        add.add_argument("--category", required=True, choices=sorted(CATEGORIES))
        add.add_argument("--platforms", required=True)
        add.add_argument("--surface-modes", required=True)
        add.add_argument("--motion-tier", required=True, choices=["M1", "M2", "M3", "M4"])
        add.add_argument("--aliases", default="")
        add.add_argument("--force", action="store_true")
        if name == "add-pattern":
            for flag in ("source-prompt-zh", "canonical-prompt", "state-machine", "web-impl", "mobile-impl", "guardrails"):
                add.add_argument("--" + flag, required=True)
        else:
            add.add_argument("--automation-mode", required=True,
                             choices=["direct-code", "mcp-cli", "dev-only", "human-gui-only"])
            add.add_argument("--token-integration", required=True)
    use = sub.add_parser("record-use", parents=[common])
    use.add_argument("--id", required=True)
    use.add_argument("--context", required=True)
    use.add_argument("--rating", choices=["liked", "neutral", "disliked"])
    use.add_argument("--notes")
    rec = sub.add_parser("recommend", parents=[common])
    rec.add_argument("--platform", choices=sorted(PLATFORMS))
    rec.add_argument("--surface-mode", choices=sorted(SURFACE_MODES))
    rec.add_argument("--motion-tier", choices=["M1", "M2", "M3", "M4"])
    rec.add_argument("--kind", choices=["all", "tool", "pattern"], default="all")
    return root


if __name__ == "__main__":
    try:
        raise SystemExit(command(parser().parse_args()))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
