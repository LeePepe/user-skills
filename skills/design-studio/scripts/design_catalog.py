#!/usr/bin/env python3
"""Manage the design-studio tool and interaction-pattern catalog."""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
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


ITEM_START = re.compile(r"^\s*(?:[-*+•]\s+|(?:\d{1,3}[.)、]|[①②③④⑤⑥⑦⑧⑨⑩]|\(\d{1,3}\))\s*)(.+)$")
BOLD_ITEM = re.compile(r"^\s*\*\*(.+?)\*\*\s*[:：]\s*(.+?)\s*$", re.DOTALL)


def split_segments(text):
    """Split itemized prompts while dropping prose before and after the list."""
    lines = text.splitlines()
    segments = []
    current = None
    for line in lines:
        match = ITEM_START.match(line)
        bold = BOLD_ITEM.match(line)
        if match:
            if current:
                segments.append(" ".join(current).strip())
            current = [match.group(1).strip()]
        elif bold:
            if current:
                segments.append(" ".join(current).strip())
            current = [f"{bold.group(1).strip()}: {bold.group(2).strip()}"]
        elif current and line[:1].isspace() and line.strip():
            current.append(line.strip())
    if current:
        segments.append(" ".join(current).strip())
    return [segment for segment in segments if segment]


def jev_result(text, args):
    if args.no_jev:
        return {}
    payload = json.dumps({"text": text, "task": "design_catalog_ingest"}).encode("utf-8")
    request = urllib.request.Request(args.jev_url, data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            value = json.loads(response.read().decode("utf-8"))
        if isinstance(value, dict):
            value = value.get("result", value.get("data", value))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return {}


def classify_segment(text, args):
    result = jev_result(text, args)
    noul = result.get("noul", 1.0)
    try:
        if float(noul) < 0.40 or result.get("is_design_pattern") is False:
            return None
    except (TypeError, ValueError):
        pass
    category = args.category or classify_prompt(text)
    category_confidence = result.get("category_confidence", result.get("categoryConfidence", 0))
    motion_confidence = result.get("motion_confidence", result.get("motionConfidence", 0))
    try:
        category_confidence, motion_confidence = float(category_confidence), float(motion_confidence)
    except (TypeError, ValueError):
        category_confidence = motion_confidence = 0
    if (not args.category and category_confidence >= 0.55
            and result.get("category") in INGEST_PROFILES):
        category = result["category"]
    motion = args.motion_tier or ("M1" if category in IP_CATEGORIES else "M2")
    if not args.motion_tier and motion_confidence >= 0.60 and result.get("motion_tier") in {"M1", "M2", "M3", "M4"}:
        motion = result["motion_tier"]
    return category, motion, result


def next_pattern_id(catalog, category, reserved=None):
    prefix = "IP" if category in IP_CATEGORIES else "MP"
    reserved = reserved or set()
    numbers = [int(match.group(1)) for item in catalog["patterns"]
               if (match := re.fullmatch(rf"{prefix}-(\d+)", item["id"]))]
    number = max(numbers, default=0) + 1
    while f"{prefix}-{number:03d}" in reserved:
        number += 1
    return f"{prefix}-{number:03d}"


def make_pattern(text, category, motion, pattern_id, args):
    _, state, web, mobile, guardrails = INGEST_PROFILES[category]
    normalized = " ".join(text.split())
    prefix = pattern_id.split("-", 1)[0]
    return {
        "id": pattern_id, "name": normalized[:80], "aliases": [], "category": category,
        "platforms": args.platforms, "surface_modes": args.surface_modes, "motion_tier": motion,
        "source_prompt_zh": text, "canonical_prompt": normalized, "state_machine": state,
        "web_implementation": web, "mobile_implementation": mobile,
        "token_guardrails": (f"{guardrails} Reuse DesignTokens/StrataMotion/Basalt tokens; support Light/Dark, "
                              ">=44x44pt hit targets, and prefers-reduced-motion / Reduce Motion without spatial motion."),
        "usage_count": 0, "rating": "unrated", "last_used_at": None, "usage_history": [],
    }


def compact_line(kind, item):
    return f"{kind}: {item['id']} [{item['category']}/{item['motion_tier']}] {item['name']}"


def ocr_image(path, runner=None):
    """Extract Chinese/English text locally with macOS Vision; runner is injectable."""
    runner = runner or subprocess.run
    script = r'''import Foundation
import Vision
import ImageIO
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil), let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { exit(2) }
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
let handler = VNImageRequestHandler(cgImage: image, options: [:])
try handler.perform([request])
for observation in request.results ?? [] { if let candidate = observation.topCandidates(1).first { print(candidate.string) } }
'''
    try:
        completed = runner(["swift", "-e", script, str(path)], check=True,
                           capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"OCR failed for image: {path}") from error
    extracted = completed.stdout.strip()
    if not extracted:
        raise ValueError(f"OCR returned no text for image: {path}")
    return extracted


def ingest(args, catalog, catalog_path, markdown_path):
    if args.text is not None:
        text = args.text.strip()
    elif args.file is not None:
        text = Path(args.file).read_text(encoding="utf-8").strip()
    else:
        text = ocr_image(args.image).strip()
    if not text:
        raise ValueError("input prompt must not be empty")
    segments = [text] if args.no_split else (split_segments(text) or [text])
    if args.id and len(segments) != 1:
        raise ValueError("--id requires a single ingest segment")
    created_items, duplicate_items, skipped_items, reserved = [], [], [], set()
    forced_matches = []
    working = {"patterns": list(catalog["patterns"])}
    for segment in segments:
        classified = classify_segment(segment, args)
        if classified is None:
            skipped_items.append({"text": segment, "reason": "jev_noise"})
            continue
        category, motion, jev = classified
        prefix = "IP" if category in IP_CATEGORIES else "MP"
        candidate_id = args.id or next_pattern_id(catalog, category, reserved)
        if not candidate_id.startswith(prefix + "-"):
            raise ValueError(f"{category} requires an {prefix}- prefixed id")
        if any(item["id"] == candidate_id for item in all_items(catalog)):
            raise ValueError(f"catalog id already exists: {candidate_id}")
        item = make_pattern(segment, category, motion, candidate_id, args)
        found = [{"id": existing["id"], "name": existing["name"]}
                 for existing in working["patterns"] if duplicate({"patterns": [existing]}, item, "patterns")]
        if found and not args.force:
            duplicate_items.append({"id": found[0]["id"], "name": found[0]["name"],
                                     "category": category, "motion_tier": motion,
                                     "duplicate_matches": found, "jev": jev})
        else:
            reserved.add(candidate_id)
            created_items.append(item)
            working["patterns"].append(item)
            if found:
                forced_matches = found
    if created_items and not args.dry_run:
        catalog["patterns"].extend(created_items)
        validate(catalog)
        save(catalog_path, catalog)
        markdown_path.write_text(render(catalog), encoding="utf-8")
    single = len(segments) == 1 and not skipped_items
    if single:
        item = created_items[0] if created_items else None
        dup = duplicate_items[0] if duplicate_items else None
        result = {"pattern": item or {"id": dup["id"], "name": dup["name"], "category": dup["category"], "motion_tier": dup["motion_tier"]},
                  "classification": {"prefix": (item or dup)["id"].split("-", 1)[0], "category": (item or dup)["category"],
                                     "source": "override" if args.category else "automatic"},
                  "duplicate_matches": (dup or {}).get("duplicate_matches", forced_matches),
                  "duplicate_decision": "blocked" if dup else ("forced" if forced_matches else "unique"),
                  "dry_run": args.dry_run, "created": bool(item) and not args.dry_run,
                  "paths": {"catalog": str(catalog_path), "markdown": str(markdown_path)},
                  "actions": (["write_catalog", "render_markdown"] if item and not args.dry_run else [])}
    else:
        result = {"created_items": [{"id": item["id"], "name": item["name"], "category": item["category"], "motion_tier": item["motion_tier"]} for item in created_items],
                  "duplicate_items": duplicate_items, "skipped_items": skipped_items, "dry_run": args.dry_run,
                  "paths": {"catalog": str(catalog_path), "markdown": str(markdown_path)},
                  "actions": (["write_catalog", "render_markdown"] if created_items and not args.dry_run else [])}
    if args.compact:
        for item in result.get("created_items", []): print(compact_line("created", item))
        for item in result.get("duplicate_items", []): print(compact_line("duplicate", item))
        if single: print(compact_line("created" if created_items else "duplicate", item or dup))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if duplicate_items and not created_items else 0


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
    source.add_argument("--image")
    intake.add_argument("--id", type=pattern_id)
    intake.add_argument("--category", choices=sorted(INGEST_PROFILES))
    intake.add_argument("--platforms", type=lambda value: csv_choices(value, PLATFORMS),
                        default="web,mobile,shared")
    intake.add_argument("--surface-modes", type=lambda value: csv_choices(value, SURFACE_MODES),
                        default="Operate,Read")
    intake.add_argument("--motion-tier", choices=["M1", "M2", "M3", "M4"])
    intake.add_argument("--force", action="store_true")
    intake.add_argument("--dry-run", action="store_true")
    intake.add_argument("--no-split", action="store_true")
    intake.add_argument("--jev-url", default="http://127.0.0.1:19823/v1/systemone")
    intake.add_argument("--no-jev", action="store_true")
    intake.add_argument("--compact", action="store_true")
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
