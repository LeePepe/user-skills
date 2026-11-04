#!/usr/bin/env python3
"""Manage the motion-studio tool and interaction-pattern catalog."""

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
}


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
    for collection, prefix in (("tools", "T-"), ("patterns", "MP-")):
        if not isinstance(catalog.get(collection), list):
            raise ValueError(f"{collection} must be a list")
        for item in catalog[collection]:
            required = {"id", "name", "aliases", "category", "platforms", "surface_modes",
                        "motion_tier", "usage_count", "rating", "last_used_at", "usage_history"}
            missing = required - item.keys()
            if missing:
                raise ValueError(f"{item.get('id', '?')} missing {sorted(missing)}")
            if not str(item["id"]).startswith(prefix):
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


def render(catalog):
    lines = [
        "# Motion Studio toolchain matrix",
        "",
        "This matrix is generated from `catalog.json`; use `motion_catalog.py` to search,",
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
    lines += ["", "## Interactive Motion Pattern Catalog", "",
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
        'python3 skills/motion-studio/scripts/motion_catalog.py search --query "滑动确认"',
        "python3 skills/motion-studio/scripts/motion_catalog.py recommend --platform web --surface-mode Operate",
        'python3 skills/motion-studio/scripts/motion_catalog.py record-use --id MP-002 --context "checkout" --rating liked',
        "```", "",
    ]
    return "\n".join(lines)


def catalog_paths(args):
    return Path(args.catalog), Path(args.markdown)


def command(args):
    catalog_path, markdown_path = catalog_paths(args)
    catalog = validate(load(catalog_path))
    if args.command == "search":
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


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    common.add_argument("--markdown", default=str(DEFAULT_MARKDOWN))
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
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
