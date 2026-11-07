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
DEFAULT_TAXONOMY = ROOT / "references" / "taxonomy-v2.json"
DEFAULT_VAULT_DIR = ROOT / "references" / "vault"
PLATFORMS = {"web", "mobile", "shared"}
VAULT_PLATFORMS = {"desktop", "mobile", "web"}
DIMENSIONS = {
    "color-palette", "visual-style", "component", "chart-dataviz",
    "glass-material", "motion-interaction", "layout-navigation", "typography-copy",
}
DIMENSION_PREFIXES = {
    "color-palette": "CL",
    "visual-style": "VS",
    "component": "CP",
    "chart-dataviz": "DV",
    "glass-material": "GM",
    "motion-interaction": "MI",
    "layout-navigation": "LN",
    "typography-copy": "TC",
}
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


def _kw_match(text, keyword):
    kw = str(keyword).strip()
    if not kw:
        return False
    if kw.isascii():
        return bool(re.search(r"(?<!\w)" + re.escape(kw.casefold()) + r"(?!\w)", text.casefold()))
    return kw.casefold() in text.casefold()


def _jev_classify_v2_request(text, taxonomy, args):
    if getattr(args, "mock_jev_json", None):
        return json.loads(args.mock_jev_json)
    if getattr(args, "no_jev", False):
        return {}
    questions = {
        "is_design_inspiration": {
            "type": "noul",
            "instructions": "Does this text describe a UI/UX design inspiration, color palette, visual style, component, chart, material, motion, layout, or interface copy?",
            "criteria": {"true": "Design or UI/UX related.", "false": "Unrelated noise."},
        }
    }
    for plat, rule in taxonomy.get("platform_rules", {}).items():
        q = rule.get("jev_question", {})
        if q.get("id"):
            questions[q["id"]] = {
                "type": "noul",
                "instructions": q.get("instructions", f"Is this for {plat}?"),
                "criteria": q.get("criteria", {"true": "Yes", "false": "No"}),
            }
    for dim in taxonomy.get("dimensions", []):
        q = dim.get("jev_question", {})
        if q.get("id"):
            questions[q["id"]] = {
                "type": "noul",
                "instructions": q.get("instructions", f"Is this {dim['id']}?"),
                "criteria": q.get("criteria", {"true": "Yes", "false": "No"}),
            }
    payload = json.dumps({
        "text": text,
        "task": "design_taxonomy_v2_classify",
        "questions": questions,
    }).encode("utf-8")
    req = urllib.request.Request(
        getattr(args, "jev_url", "http://127.0.0.1:19823/v1/systemone"),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            val = json.loads(resp.read().decode("utf-8"))
        if isinstance(val, dict):
            val = val.get("answers", val.get("result", val.get("data", val)))
        return val if isinstance(val, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return {}


def _extract_noul(jev_data, key):
    if not isinstance(jev_data, dict):
        return None
    entry = jev_data.get(key)
    if isinstance(entry, dict) and "noul" in entry:
        try:
            return float(entry["noul"])
        except (TypeError, ValueError):
            return None
    if isinstance(entry, (int, float)):
        return float(entry)
    return None


def classify_v2_segment(text, taxonomy, args):
    jev = _jev_classify_v2_request(text, taxonomy, args)
    noise_noul = _extract_noul(jev, "is_design_inspiration")
    if noise_noul is None and "noul" in jev:
        try:
            noise_noul = float(jev["noul"])
        except (TypeError, ValueError):
            noise_noul = None
    if (noise_noul is not None and noise_noul < 0.40) or jev.get("is_design_pattern") is False:
        return {"status": "noise_skipped", "text": text, "noul": noise_noul or 0.0}

    learned_plat = taxonomy.get("learned_keywords", {}).get("platforms", {})
    plat_scores = {}
    for plat in ("desktop", "mobile", "web"):
        rule = taxonomy.get("platform_rules", {}).get(plat, {})
        pos_kws = list(rule.get("positive_keywords_zh_en", [])) + list(learned_plat.get(plat, []))
        neg_kws = list(rule.get("negative_keywords_zh_en", []))
        pos_hits = [kw for kw in pos_kws if _kw_match(text, kw)]
        neg_hits = [kw for kw in neg_kws if _kw_match(text, kw)]
        kw_conf = min(0.95, 0.45 + 0.18 * len(pos_hits) - 0.12 * len(neg_hits)) if pos_hits else 0.0
        q_id = rule.get("jev_question", {}).get("id", f"q_platform_{plat}")
        jev_noul = _extract_noul(jev, q_id)
        if jev_noul is not None:
            score = round(max(kw_conf, jev_noul) if pos_hits else jev_noul, 3)
        else:
            score = round(max(0.0, kw_conf), 3)
        plat_scores[plat] = {"score": score, "matched_keywords": pos_hits}

    active_plats = [p for p, info in plat_scores.items() if info["score"] >= 0.60]
    if not active_plats:
        best_plat = max(plat_scores, key=lambda p: plat_scores[p]["score"])
        if plat_scores[best_plat]["score"] > 0.0:
            active_plats = [best_plat]
            plat_confidence = plat_scores[best_plat]["score"]
        else:
            # Default Apple-First dual platform when no explicit platform cue is stated
            active_plats = ["desktop", "mobile"]
            plat_confidence = 0.55
    else:
        plat_confidence = max(plat_scores[p]["score"] for p in active_plats)

    learned_dim = taxonomy.get("learned_keywords", {}).get("dimensions", {})
    dim_scores = {}
    dim_by_id = {d["id"]: d for d in taxonomy.get("dimensions", [])}
    for dim in taxonomy.get("dimensions", []):
        did = dim["id"]
        kws = list(dim.get("keywords_zh_en", [])) + list(learned_dim.get(did, []))
        hits = [kw for kw in kws if _kw_match(text, kw)]
        kw_conf = min(0.96, 0.52 + 0.16 * len(hits)) if hits else 0.0
        q_id = dim.get("jev_question", {}).get("id", f"q_dim_{did.replace('-', '_')}")
        jev_noul = _extract_noul(jev, q_id)
        if jev_noul is not None:
            score = round(max(kw_conf, jev_noul) if hits else jev_noul, 3)
        else:
            score = round(kw_conf, 3)
        sub_slug = dim["sub_categories"][0]["slug"] if dim.get("sub_categories") else "general"
        for sub in dim.get("sub_categories", []):
            for token in (sub.get("slug", ""), sub.get("label_zh", ""), sub.get("label_en", "")):
                if token and _kw_match(text, token):
                    sub_slug = sub["slug"]
                    break
        dim_scores[did] = {"score": score, "matched_keywords": hits, "sub_category": sub_slug}

    ranked_dims = sorted(dim_scores.items(), key=lambda kv: (-kv[1]["score"], kv[0]))
    strong_dims = [did for did, info in ranked_dims if info["score"] >= 0.65]
    primary_dim = ranked_dims[0][0] if ranked_dims and ranked_dims[0][1]["score"] > 0 else "visual-style"
    dim_confidence = ranked_dims[0][1]["score"] if ranked_dims else 0.0
    secondary_dims = [did for did in strong_dims if did != primary_dim][:2]

    is_compound = len(strong_dims) >= 2
    low_confidence = plat_confidence < 0.70 or dim_confidence < 0.70
    needs_llm_fallback = (not getattr(args, "no_llm_fallback", False)) and (
        getattr(args, "llm_fallback", False) or low_confidence or is_compound
    )

    fallback_reasons = []
    if plat_confidence < 0.70:
        fallback_reasons.append("low_platform_confidence")
    if dim_confidence < 0.70:
        fallback_reasons.append("low_dimension_confidence")
    if is_compound:
        fallback_reasons.append("compound_multi_dimension_prompt")

    cards = []
    learned_updates = {"platforms": {}, "dimensions": {}}
    if needs_llm_fallback:
        cards, learned_updates = llm_decompose_inspiration(
            text, taxonomy, active_plats, strong_dims or [primary_dim], dim_scores, args
        )
        route_stage = "stage2_llm_fallback"
    else:
        d_meta = dim_by_id.get(primary_dim, {})
        cards = [
            _build_atomic_card(
                text=text,
                platforms=active_plats,
                primary_dim=primary_dim,
                secondary_dims=secondary_dims,
                sub_category=dim_scores[primary_dim]["sub_category"],
                d_meta=d_meta,
            )
        ]
        route_stage = "stage1_jev_fast_path"

    return {
        "status": "classified",
        "route_stage": route_stage,
        "needs_llm_fallback": needs_llm_fallback,
        "fallback_reasons": fallback_reasons,
        "platforms": active_plats,
        "platform_confidence": round(plat_confidence, 3),
        "primary_dimension": primary_dim,
        "secondary_dimensions": secondary_dims,
        "dimension_confidence": round(dim_confidence, 3),
        "cards": cards,
        "learned_keywords_candidate": learned_updates,
    }


def _build_atomic_card(text, platforms, primary_dim, secondary_dims, sub_category, d_meta):
    normalized = " ".join(text.split())
    primitives = d_meta.get("apple_native_primitives", ["SwiftUI", "SF Symbols"])
    hig_routes = d_meta.get("hig_routes", ["design-principles.md", "layout.md"])
    return {
        "name": normalized[:80],
        "platforms": platforms,
        "primary_dimension": primary_dim,
        "secondary_dimensions": secondary_dims,
        "sub_category": sub_category,
        "prefix": DIMENSION_PREFIXES.get(primary_dim, "VS"),
        "source_prompt_zh": text,
        "canonical_prompt": normalized,
        "apple_native_primitives": primitives[:5],
        "hig_routes": hig_routes,
        "desktop_macos_implementation": (
            f"macOS Native ({', '.join(primitives[:3])}): pointer/hover feedback, keyboard shortcut support, "
            f"and standard window/popover materials per {', '.join(hig_routes[:2])}."
        ),
        "mobile_ios_implementation": (
            f"iOS/iPadOS Native ({', '.join(primitives[:3])}): >=44x44pt touch targets, Size Class adaptive layout, "
            f"and Dynamic Type scaling per {', '.join(hig_routes[:2])}."
        ),
        "native_guardrails": (
            "Apple HIG baseline: semantic Light/Dark colors (>=4.5:1 contrast), Liquid Glass restricted to "
            "floating functional chrome, Reduce Motion & Reduce Transparency respected."
        ),
    }


def llm_decompose_inspiration(text, taxonomy, active_plats, target_dims, dim_scores, args):
    if getattr(args, "mock_llm_json", None):
        mock = json.loads(args.mock_llm_json)
        return mock.get("cards", []), mock.get("new_keywords_to_learn", {"platforms": {}, "dimensions": {}})

    dim_by_id = {d["id"]: d for d in taxonomy.get("dimensions", [])}
    clauses = [
        c.strip()
        for c in re.split(r"[；;。！？\n]+|(?:\s+并且\s+|\s+同时\s+|\s+以及\s+|\s+配\s+|\s+with\s+|\s+and\s+)", text)
        if c.strip()
    ]
    cards = []
    if len(target_dims) >= 2:
        for idx, did in enumerate(target_dims):
            d_meta = dim_by_id.get(did, {})
            sub_cat = dim_scores.get(did, {}).get("sub_category", "general")
            clause_text = clauses[idx] if idx < len(clauses) else text
            others = [other for other in target_dims if other != did][:2]
            cards.append(_build_atomic_card(clause_text, active_plats, did, others, sub_cat, d_meta))
    else:
        did = target_dims[0] if target_dims else "visual-style"
        d_meta = dim_by_id.get(did, {})
        sub_cat = dim_scores.get(did, {}).get("sub_category", "general")
        cards.append(_build_atomic_card(text, active_plats, did, [], sub_cat, d_meta))

    # Extract concise candidate domain tokens for self-learning writeback
    candidate_tokens = [
        tok for tok in re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z][A-Za-z0-9_-]{3,20}", text)
        if tok.casefold() not in {"这个", "一个", "需要", "可以", "设计", "支持", "with", "that", "from", "this"}
    ][:3]
    primary_dim = target_dims[0] if target_dims else "visual-style"
    learned = {
        "platforms": {p: candidate_tokens[:1] for p in active_plats if len(active_plats) == 1},
        "dimensions": {primary_dim: candidate_tokens[:2]} if candidate_tokens else {},
    }
    return cards, learned


def _apply_learned_keywords(taxonomy, taxonomy_path, learned_updates):
    changed = False
    lk = taxonomy.setdefault("learned_keywords", {"platforms": {}, "dimensions": {}})
    for plat, toks in learned_updates.get("platforms", {}).items():
        bucket = lk.setdefault("platforms", {}).setdefault(plat, [])
        existing = set(bucket) | set(taxonomy.get("platform_rules", {}).get(plat, {}).get("positive_keywords_zh_en", []))
        for tok in toks:
            if tok and tok not in existing:
                bucket.append(tok)
                existing.add(tok)
                changed = True
    for dim_id, toks in learned_updates.get("dimensions", {}).items():
        bucket = lk.setdefault("dimensions", {}).setdefault(dim_id, [])
        dim_obj = next((d for d in taxonomy.get("dimensions", []) if d["id"] == dim_id), {})
        existing = set(bucket) | set(dim_obj.get("keywords_zh_en", []))
        for tok in toks:
            if tok and tok not in existing:
                bucket.append(tok)
                existing.add(tok)
                changed = True
    if changed:
        save(taxonomy_path, taxonomy)
    return changed


def _next_vault_id(vault_dir, prefix, reserved):
    max_num = 0
    for vf in ("desktop-macos.json", "mobile-ios.json", "web-backlog.json"):
        vpath = vault_dir / vf
        if vpath.is_file():
            vdata = load(vpath)
            for item in vdata.get("inspirations", []):
                m = re.fullmatch(rf"{re.escape(prefix)}-(\d+)", str(item.get("id", "")))
                if m:
                    max_num = max(max_num, int(m.group(1)))
    num = max_num + 1
    while f"{prefix}-{num:03d}" in reserved:
        num += 1
    return f"{prefix}-{num:03d}"


def command_classify(args):
    taxonomy_path = Path(args.taxonomy)
    taxonomy = load(taxonomy_path)
    text = _read_ingest_source(args)
    segments = [text] if args.no_split else (split_segments(text) or [text])
    results = []
    learned_written = False
    for seg in segments:
        res = classify_v2_segment(seg, taxonomy, args)
        if args.learn and res.get("status") == "classified":
            if _apply_learned_keywords(taxonomy, taxonomy_path, res.get("learned_keywords_candidate", {})):
                learned_written = True
        results.append(res)
    payload = {
        "schema": "design-classify-result/v2",
        "segment_count": len(segments),
        "learned_keywords_updated": learned_written,
        "results": results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_vault_ingest(args):
    taxonomy_path = Path(args.taxonomy)
    vault_dir = Path(args.vault_dir)
    taxonomy = load(taxonomy_path)
    text = _read_ingest_source(args)
    segments = [text] if args.no_split else (split_segments(text) or [text])

    vault_files = {
        "desktop": vault_dir / "desktop-macos.json",
        "mobile": vault_dir / "mobile-ios.json",
        "web": vault_dir / "web-backlog.json",
    }
    vaults = {}
    for plat, vpath in vault_files.items():
        vaults[plat] = load(vpath) if vpath.is_file() else {
            "schema": "design-vault-platform/v2",
            "platform": plat,
            "status": "backlog-frozen" if plat == "web" else "active-primary",
            "tools": [],
            "inspirations": [],
        }

    created_cards = []
    skipped_segments = []
    reserved = set()
    learned_written = False

    for seg in segments:
        res = classify_v2_segment(seg, taxonomy, args)
        if res.get("status") != "classified":
            skipped_segments.append(res)
            continue
        for card in res["cards"]:
            cid = _next_vault_id(vault_dir, card["prefix"], reserved)
            reserved.add(cid)
            entry = {
                "id": cid,
                **card,
                "route_stage": res["route_stage"],
                "usage_count": 0,
                "rating": "unrated",
                "last_used_at": None,
            }
            created_cards.append(entry)
            if not args.dry_run:
                for plat in card["platforms"]:
                    if plat in vaults:
                        vaults[plat]["inspirations"].append(entry)
        if not args.dry_run and res.get("needs_llm_fallback"):
            if _apply_learned_keywords(taxonomy, taxonomy_path, res.get("learned_keywords_candidate", {})):
                learned_written = True

    if created_cards and not args.dry_run:
        for plat, vpath in vault_files.items():
            save(vpath, vaults[plat])

    print(json.dumps({
        "schema": "design-vault-ingest/v2",
        "dry_run": args.dry_run,
        "created_count": len(created_cards),
        "created_cards": created_cards,
        "skipped_segments": skipped_segments,
        "learned_keywords_updated": learned_written,
    }, ensure_ascii=False, indent=2))
    return 0


def command_vault_search(args):
    vault_dir = Path(args.vault_dir)
    taxonomy = load(Path(args.taxonomy))
    platforms = [args.platform] if args.platform else ["desktop", "mobile"]
    vault_files = {
        "desktop": vault_dir / "desktop-macos.json",
        "mobile": vault_dir / "mobile-ios.json",
        "web": vault_dir / "web-backlog.json",
    }
    seen = {}
    for plat in platforms:
        vpath = vault_files.get(plat)
        if not vpath or not vpath.is_file():
            continue
        vdata = load(vpath)
        for item in vdata.get("inspirations", []):
            if args.dimension and item.get("primary_dimension") != args.dimension and args.dimension not in item.get("secondary_dimensions", []):
                continue
            blob = " ".join(str(item.get(k, "")) for k in ("id", "name", "source_prompt_zh", "canonical_prompt", "primary_dimension", "sub_category"))
            score = similarity(args.query, blob) if args.query else 1.0
            if score >= args.threshold:
                existing = seen.get(item["id"])
                if not existing or score > existing["score"]:
                    seen[item["id"]] = {
                        "id": item["id"],
                        "name": item["name"],
                        "platforms": item.get("platforms_v2", item.get("platforms", [plat])),
                        "primary_dimension": item.get("primary_dimension"),
                        "sub_category": item.get("sub_category"),
                        "score": score,
                        "desktop_macos_implementation": item.get("desktop_macos_implementation", ""),
                        "mobile_ios_implementation": item.get("mobile_ios_implementation", item.get("mobile_implementation", "")),
                        "native_guardrails": item.get("native_guardrails", item.get("token_guardrails", "")),
                    }
    ranked = sorted(seen.values(), key=lambda x: (-x["score"], x["id"]))[: args.top]
    active_dims = {r["primary_dimension"] for r in ranked if r.get("primary_dimension")}
    if args.dimension:
        active_dims.add(args.dimension)
    hig_routes = []
    native_primitives = []
    for dim in taxonomy.get("dimensions", []):
        if dim["id"] in active_dims:
            for h in dim.get("hig_routes", []):
                if h not in hig_routes:
                    hig_routes.append(h)
            for p in dim.get("apple_native_primitives", []):
                if p not in native_primitives:
                    native_primitives.append(p)
    print(json.dumps({
        "schema": "design-vault-search/v2",
        "platforms_queried": platforms,
        "dimension_filter": args.dimension,
        "progressive_disclosure": {
            "hig_files_to_load": hig_routes[:8],
            "apple_native_primitives": native_primitives[:10],
        },
        "matches": ranked,
    }, ensure_ascii=False, indent=2))
    return 0


def _read_ingest_source(args):
    if args.text is not None:
        text = args.text.strip()
    elif args.file is not None:
        text = Path(args.file).read_text(encoding="utf-8").strip()
    else:
        text = ocr_image(args.image).strip()
    if not text:
        raise ValueError("input prompt must not be empty")
    return text


def command(args):
    if args.command == "classify":
        return command_classify(args)
    if args.command == "vault-ingest":
        return command_vault_ingest(args)
    if args.command == "vault-search":
        return command_vault_search(args)
    catalog_path, markdown_path = catalog_paths(args)
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

    v2_common = argparse.ArgumentParser(add_help=False)
    v2_common.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    v2_common.add_argument("--vault-dir", default=str(DEFAULT_VAULT_DIR))
    v2_common.add_argument("--jev-url", default="http://127.0.0.1:19823/v1/systemone")
    v2_common.add_argument("--no-jev", action="store_true")
    v2_common.add_argument("--mock-jev-json")
    v2_common.add_argument("--mock-llm-json")
    v2_common.add_argument("--llm-fallback", action="store_true")
    v2_common.add_argument("--no-llm-fallback", action="store_true")

    cls_parser = sub.add_parser("classify", parents=[v2_common])
    cls_source = cls_parser.add_mutually_exclusive_group(required=True)
    cls_source.add_argument("--text")
    cls_source.add_argument("--file")
    cls_source.add_argument("--image")
    cls_parser.add_argument("--no-split", action="store_true")
    cls_parser.add_argument("--learn", action="store_true")

    vingest = sub.add_parser("vault-ingest", parents=[v2_common])
    v_source = vingest.add_mutually_exclusive_group(required=True)
    v_source.add_argument("--text")
    v_source.add_argument("--file")
    v_source.add_argument("--image")
    vingest.add_argument("--no-split", action="store_true")
    vingest.add_argument("--dry-run", action="store_true")

    vsearch = sub.add_parser("vault-search", parents=[v2_common])
    vsearch.add_argument("--query", default="")
    vsearch.add_argument("--platform", choices=sorted(VAULT_PLATFORMS))
    vsearch.add_argument("--dimension", choices=sorted(DIMENSIONS))
    vsearch.add_argument("--threshold", type=float, default=0.15)
    vsearch.add_argument("--top", type=int, default=5)
    return root


if __name__ == "__main__":
    try:
        raise SystemExit(command(parser().parse_args()))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
