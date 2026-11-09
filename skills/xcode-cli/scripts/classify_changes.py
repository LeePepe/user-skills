#!/usr/bin/env python3
"""Classify changed paths as SwiftPM-only, Xcode, or mixed."""

from __future__ import annotations

import argparse
from datetime import date
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath


SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES_FILE = SKILL_DIR / "references" / "build-cases.json"
TOP_K_CASES = 3
MAX_ACTIVE_CASES = 12
L1_REF_MAX_LINES = 160
IGNORED_PARTS = {".build", ".git", "DerivedData", "node_modules"}
XCODE_EXACT_NAMES = {
    "project.yml",
    "project.yaml",
    "Project.swift",
    "Workspace.swift",
    "Tuist.swift",
    "Podfile",
    "Cartfile",
}
NEUTRAL_NAMES = {"AGENTS.md", "CLAUDE.md", "LICENSE", "LICENSE.md", "README", "README.md"}
NEUTRAL_SUFFIXES = {".md", ".markdown", ".rst", ".adoc"}


def git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def nul_paths(output: bytes) -> set[str]:
    return {
        value.decode("utf-8", errors="surrogateescape")
        for value in output.split(b"\0")
        if value
    }


def changed_paths(repo: Path, base: str | None) -> list[str]:
    paths: set[str] = set()
    if base:
        merge_base = git(repo, "merge-base", base, "HEAD").decode().strip()
        paths |= nul_paths(git(repo, "diff", "--name-only", "-z", f"{merge_base}..HEAD"))
    paths |= nul_paths(git(repo, "diff", "--name-only", "-z", "HEAD"))
    paths |= nul_paths(git(repo, "ls-files", "--others", "--exclude-standard", "-z"))
    return sorted(paths)


def xcode_reason(path: PurePosixPath, package_owned: bool) -> str | None:
    if path.name in XCODE_EXACT_NAMES:
        return "project generator or dependency integration metadata"
    if any(part.endswith((".xcodeproj", ".xcworkspace")) for part in path.parts):
        return "Xcode project or workspace metadata"
    if path.suffix in (".xcconfig", ".entitlements"):
        return "Xcode build settings, signing, or interface metadata"
    if package_owned:
        return None
    if path.name == "Info.plist" or path.suffix in (".storyboard", ".xib"):
        return "Xcode build settings or interface metadata"
    for part in path.parts:
        if part.endswith((".xcassets", ".xcdatamodeld")):
            return "Xcode model or asset container"
    return None


def is_neutral(path: PurePosixPath) -> bool:
    return (
        path.name in NEUTRAL_NAMES
        or path.suffix.lower() in NEUTRAL_SUFFIXES
        or (path.parts and path.parts[0] in {"docs", ".github"})
    )


def package_root(repo: Path, relative: PurePosixPath) -> Path | None:
    if any(part in IGNORED_PARTS for part in relative.parts):
        return None
    candidate = (repo / Path(*relative.parts)).parent
    if relative.name == "Package.swift":
        candidate = repo / Path(*relative.parent.parts)
    while candidate == repo or repo in candidate.parents:
        if (candidate / "Package.swift").is_file():
            return candidate
        if candidate == repo:
            break
        candidate = candidate.parent
    return None


def load_build_cases(cases_file: Path = DEFAULT_CASES_FILE) -> dict[str, object]:
    if not cases_file.is_file():
        return {"schema": "apple-build-cases/v1", "version": 1, "active_cases": [], "archived_cases": []}
    return json.loads(cases_file.read_text(encoding="utf-8"))


def match_build_cases(route: str, cases_file: Path = DEFAULT_CASES_FILE, top_k: int = TOP_K_CASES) -> list[dict[str, object]]:
    doc = load_build_cases(cases_file)
    active = doc.get("active_cases", [])
    if not isinstance(active, list):
        return []
    matched = [
        case
        for case in active
        if isinstance(case, dict) and (route in case.get("routes", []) or route == "none")
    ]
    return matched[:top_k]


def build_flow_map(cases_file: Path = DEFAULT_CASES_FILE) -> dict[str, object]:
    doc = load_build_cases(cases_file)
    active = doc.get("active_cases", []) if isinstance(doc.get("active_cases"), list) else []
    ref_dir = SKILL_DIR / "references"
    ref_line_counts = {
        path.relative_to(SKILL_DIR).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(ref_dir.glob("*.md"))
    }
    all_within_budget = all(lines <= L1_REF_MAX_LINES for lines in ref_line_counts.values()) and (
        len(active) <= MAX_ACTIVE_CASES
    )
    return {
        "ok": all_within_budget,
        "skill": "xcode-cli",
        "architecture_ref": "skills/xcode-cli/SKILL.md",
        "entry_a_ingest": {
            "A1_register_build_case": (
                "python3 skills/xcode-cli/scripts/classify_changes.py --register-case "
                "--id XC-xxx --title '...' --routes swiftpm,xcode --symptom '...' --fix-command '...' --fix-rule '...'"
            ),
        },
        "shared_tiers": {
            "tier_1_json_drawer": {
                "file": "skills/xcode-cli/references/build-cases.json",
                "active_case_count": len(active),
                "active_case_ids": [c.get("id") for c in active if isinstance(c, dict)],
            },
            "tier_2_l1_docs_line_counts": ref_line_counts,
            "tier_3_deterministic_classifier": "skills/xcode-cli/scripts/classify_changes.py",
        },
        "entry_b_consume": {
            "B1_classify_and_slice": "python3 skills/xcode-cli/scripts/classify_changes.py --repo <repo> [--base <base>]",
            "B2_route_reference": "skills/xcode-cli/references/command-routing.md",
        },
        "anti_explosion_valves": {
            "valve_1_zero_token_path_classifier": True,
            "valve_2_route_scoped_slicing": ["swiftpm", "xcode", "mixed", "none"],
            "valve_3_top_k_and_line_caps": {
                "matched_cases_top_k": TOP_K_CASES,
                "l1_doc_max_lines": L1_REF_MAX_LINES,
                "active_cases_cap": MAX_ACTIVE_CASES,
            },
            "valve_4_one_in_one_out_compaction": True,
            "all_l1_docs_within_budget": all_within_budget,
        },
    }


def register_build_case(
    cases_file: Path,
    case_id: str,
    title: str,
    routes: list[str],
    symptom: str,
    fix_command: str,
    fix_rule: str,
) -> dict[str, object]:
    doc = load_build_cases(cases_file)
    active = list(doc.get("active_cases", [])) if isinstance(doc.get("active_cases"), list) else []
    archived = list(doc.get("archived_cases", [])) if isinstance(doc.get("archived_cases"), list) else []
    new_entry = {
        "id": case_id,
        "title": title,
        "routes": routes,
        "symptom": symptom,
        "fix_command": fix_command,
        "fix_rule": fix_rule,
        "registered_at": date.today().isoformat(),
    }
    active = [c for c in active if isinstance(c, dict) and c.get("id") != case_id]
    active.append(new_entry)
    while len(active) > MAX_ACTIVE_CASES:
        archived.append(active.pop(0))
    doc["active_cases"] = active
    doc["archived_cases"] = archived
    doc["updated"] = date.today().isoformat()
    cases_file.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"ok": True, "registered": new_entry, "active_count": len(active)}


def classify(repo: Path, paths: list[str], cases_file: Path = DEFAULT_CASES_FILE) -> dict[str, object]:
    package_roots: set[str] = set()
    package_files: list[str] = []
    neutral_files: list[str] = []
    xcode_inputs: list[dict[str, str]] = []

    for raw_path in paths:
        path = PurePosixPath(raw_path)
        root = package_root(repo, path)
        if root is None and is_neutral(path):
            neutral_files.append(raw_path)
            continue
        reason = xcode_reason(path, package_owned=root is not None)
        if reason is not None:
            xcode_inputs.append({"path": raw_path, "reason": reason})
        elif root is not None:
            package_roots.add("." if root == repo else root.relative_to(repo).as_posix())
            package_files.append(raw_path)
        else:
            xcode_inputs.append({
                "path": raw_path,
                "reason": "path is not owned by a discovered Swift package",
            })

    if not paths:
        route = "none"
    elif xcode_inputs and package_roots:
        route = "mixed"
    elif xcode_inputs:
        route = "xcode"
    elif package_roots:
        route = "swiftpm"
    else:
        route = "none"

    return {
        "route": route,
        "changedFiles": paths,
        "packageFiles": package_files,
        "packageRoots": sorted(package_roots),
        "neutralFiles": neutral_files,
        "xcodeInputs": xcode_inputs,
        "matchedBuildCases": match_build_cases(route, cases_file=cases_file),
        "note": "Confirm custom Package.swift target paths before executing commands.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Explicit repo-relative paths; otherwise inspect git changes")
    parser.add_argument("--repo", default=".", help="Git repository root or subdirectory")
    parser.add_argument("--base", help="Base ref for committed branch changes, for example origin/main")
    parser.add_argument("--cases-file", default=str(DEFAULT_CASES_FILE), help="Path to build-cases.json drawer")
    parser.add_argument("--flow-map", action="store_true", help="Print Dual-Entry flow-map JSON and budget health")
    parser.add_argument("--register-case", action="store_true", help="Register or update a build case in build-cases.json")
    parser.add_argument("--id", help="Build case ID (e.g. XC-005) when using --register-case")
    parser.add_argument("--title", help="Build case title when using --register-case")
    parser.add_argument("--routes", default="swiftpm,xcode,mixed", help="Comma-separated routes for --register-case")
    parser.add_argument("--symptom", default="", help="Symptom description for --register-case")
    parser.add_argument("--fix-command", default="", help="Canonical CLI command for --register-case")
    parser.add_argument("--fix-rule", default="", help="Fix rule for --register-case")
    args = parser.parse_args(argv)

    cases_path = Path(args.cases_file).resolve()
    if args.flow_map or args.paths == ["flow-map"]:
        payload = build_flow_map(cases_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("ok") else 1

    if args.register_case:
        if not args.id or not args.title or not args.fix_rule:
            print("classify_changes: --register-case requires --id, --title, and --fix-rule", file=sys.stderr)
            return 2
        routes = [item.strip() for item in args.routes.split(",") if item.strip()]
        result = register_build_case(
            cases_path,
            case_id=args.id,
            title=args.title,
            routes=routes,
            symptom=args.symptom,
            fix_command=args.fix_command,
            fix_rule=args.fix_rule,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    try:
        repo = Path(git(Path(args.repo).resolve(), "rev-parse", "--show-toplevel").decode().strip())
        paths = sorted(set(args.paths)) if args.paths else changed_paths(repo, args.base)
        print(json.dumps(classify(repo, paths, cases_file=cases_path), indent=2, sort_keys=True))
        return 0
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        print(f"classify_changes: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
