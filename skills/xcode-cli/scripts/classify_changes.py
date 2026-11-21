#!/usr/bin/env python3
"""Classify changed paths as SwiftPM-only, Xcode, or mixed."""

from __future__ import annotations

import argparse
from datetime import date
import json
import re
import shlex
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


class _SelfDisclosingXcodeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(f"classify_changes: {' '.join(message.split())}", file=sys.stderr)
        print("Fix: classify_changes.py --repo <repo> [--base <ref> --head <ref> | --range <a..b>] [--json]", file=sys.stderr)
        print("Reference: skills/xcode-cli/references/command-routing.md · skills/xcode-cli/references/build-cases.json", file=sys.stderr)
        raise SystemExit(2)


def commit_ref(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()


def default_merge_base(repo: Path, head: str) -> str | None:
    for ref in ("origin/main", "main"):
        try:
            return git(repo, "merge-base", commit_ref(repo, ref), head).decode().strip()
        except subprocess.CalledProcessError:
            continue
    return None


def changed_paths(
    repo: Path,
    base: str | None,
    head: str = "HEAD",
    diff_range: str | None = None,
    committed_only: bool = False,
) -> list[str]:
    head_commit = commit_ref(repo, head)
    paths: set[str] = set()
    if not committed_only:
        paths |= nul_paths(git(repo, "diff", "--name-only", "-z", "HEAD"))
        paths |= nul_paths(git(repo, "ls-files", "--others", "--exclude-standard", "-z"))
    start: str | None = None
    if diff_range:
        match = re.fullmatch(r"(.+?)(\.{2,3})([^.].*)", diff_range)
        if not match:
            raise ValueError("--range requires <base>..<head> or <base>...<head>")
        left, separator, right = match.groups()
        start, head_commit = commit_ref(repo, left), commit_ref(repo, right)
        if separator == "...":
            start = git(repo, "merge-base", start, head_commit).decode().strip()
    elif base:
        start = git(repo, "merge-base", commit_ref(repo, base), head_commit).decode().strip()
    elif not paths or head != "HEAD":
        start = default_merge_base(repo, head_commit)
    if start:
        paths |= nul_paths(git(repo, "diff", "--name-only", "-z", start, head_commit))
    return sorted(paths)


def _heavy_condition(tokens: list[str]) -> int:
    """Return the branch requiring heavy opt-in: then=1, else=-1, unknown=0."""
    negated = tokens[:1] == ["!"]
    condition_tokens = tokens[1:] if negated else tokens
    if condition_tokens[:1] not in (["["], ["[["], ["test"]):
        return 0
    text = " ".join(condition_tokens)
    match = re.search(
        r"(?:\$\{?(?:RUN_XCODEBUILD|RUN_HEAVY|mode|MODE)\b[^\s]*|mode)\s*"
        r"(==|=|!=|-eq|-ne)\s*(1|0|true|false|ci)\b", text,
    )
    if not match:
        return 0
    operator, value = match.groups()
    positive = (operator in {"==", "=", "-eq"}) == (value in {"1", "true", "ci"})
    positive ^= negated
    # OR, or the complement of AND, may admit a different non-opt-in branch.
    if ("||" in tokens and positive) or ("&&" in tokens and not positive):
        return 0
    return 1 if positive else -1


def _shell_commands(text: str) -> dict[str, list[tuple[list[str], bool]]]:
    """Inspect literal shell commands and function calls without executing repo code."""
    text = re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"|#[^\n]*",
                  lambda match: "" if match.group().startswith("#") else match.group(), text)
    lexer = shlex.shlex(text.replace("\\\n", " "), posix=True, punctuation_chars=";&|()")
    lexer.whitespace = " \t\r"
    lexer.wordchars += "!"
    lexer.commenters = ""
    statements: list[tuple[list[str], str]] = []
    current: list[str] = []
    try:
        for token in lexer:
            if token and all(char in ";&|\n" for char in token):
                in_test = current.count("[[") > current.count("]]") or current.count("[") > current.count("]")
                if token in {"&&", "||"} and (in_test or current[:1] in (["if"], ["elif"])):
                    current.append(token)
                    continue
                statements.append((current, token.strip()))
                current = []
            else:
                current.append(token)
    except ValueError:
        # Keep preceding evidence when a non-shell verify script contains quotes.
        pass
    statements.append((current, ""))
    commands: dict[str, list[tuple[list[str], bool]]] = {"": []}
    owner = ""
    branches: list[int] = []
    short_guard = False
    for tokens, connector in statements:
        if not tokens:
            short_guard = False
            continue
        if len(tokens) >= 2 and tokens[1] == "()":
            owner = tokens[0]
            commands.setdefault(owner, [])
            tokens = tokens[2:]
            if tokens[:1] == ["{"]:
                tokens = tokens[1:]
        if tokens[:1] == ["if"]:
            branches.append(_heavy_condition(tokens[1:]))
            tokens = []
        elif tokens[:1] == ["elif"]:
            if branches:
                branches[-1] = _heavy_condition(tokens[1:])
            tokens = []
        elif tokens[:1] == ["else"]:
            if branches:
                branches[-1] *= -1
            tokens = tokens[1:]
        elif tokens[:1] == ["fi"]:
            if branches:
                branches.pop()
            tokens = tokens[1:]
        elif tokens == ["}"]:
            owner = ""
            tokens = []
        if tokens[:1] == ["then"]:
            tokens = tokens[1:]
        if tokens:
            commands[owner].append((tokens, 1 in branches or short_guard))
        condition = _heavy_condition(tokens)
        short_guard = (connector == "&&" and condition == 1) or (connector == "||" and condition == -1)
    return commands


def _command_name(tokens: list[str]) -> str:
    for token in tokens:
        if re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", token) or token in {"env", "exec", "command", "time", "bash", "sh", "xcrun"}:
            continue
        return token
    return ""


def verify_gate_health(repo: Path) -> dict[str, object]:
    verify_path = repo / "scripts" / "verify"
    files = [verify_path, repo / ".githooks" / "pre-push", repo / "scripts" / "hooks" / "pre-push"]
    parsed = {
        path: _shell_commands(path.read_text(encoding="utf-8", errors="replace"))
        for path in files if path.is_file()
    }

    def blocks(path: Path, owner: str = "", guarded: bool = False, forced: bool = False,
               seen: frozenset[tuple[Path, str, bool, bool]] = frozenset()) -> bool:
        key = (path, owner, guarded, forced)
        if key in seen:
            return False
        functions = parsed.get(path, {})
        for tokens, local_guard in functions.get(owner, []):
            protected = guarded or (local_guard and not forced)
            name = _command_name(tokens)
            opt_in = any(re.fullmatch(r"(?:RUN_(?:XCODEBUILD|HEAVY)=(?:1|true)|(?:mode|MODE)=ci)", token) for token in tokens)
            opt_in |= "--mode=ci" in tokens or any(tokens[i:i + 2] == ["--mode", "ci"] for i in range(len(tokens)))
            if name in {"", "export"} and opt_in and not protected:
                forced = True
            elif Path(name).name == "xcodebuild" and any(action in tokens for action in ("test", "test-without-building")):
                if not protected:
                    return True
            elif name in functions:
                if blocks(path, name, protected, forced or opt_in, seen | {key}):
                    return True
            elif re.search(r"(?:^|/)scripts/verify$", name):
                if blocks(verify_path, guarded=protected, forced=forced or opt_in, seen=seen | {key}):
                    return True
        return False

    blocking = any(blocks(path) for path in files[1:] if path in parsed)
    executable = "\n".join(
        " ".join(tokens) for functions in parsed.values() for commands in functions.values()
        for tokens, _ in commands if _command_name(tokens) not in {"echo", "printf"}
    )
    incremental = bool(re.search(r"--(?:changed|layers?|affected|incremental)\b|classify_changes\.py|"
                                 r"\b(?:select_layers|changed_layers|detect_layers)\b|git diff[^\n]*--name-only", executable))
    isolation = bool(re.search(r"-derivedDataPath\b|DerivedData|DERIVED_DATA|\b(?:flock|lockf)\b|"
                               r"BUILD_LOCK|build[_.-]lock|with_build_lock|acquire_lock", executable))
    fast_spm = any(
        Path(_command_name(tokens)).name == "swift" and any(action in tokens for action in ("build", "test"))
        for commands in parsed.get(verify_path, {}).values() for tokens, _ in commands
    )
    return {
        "has_verify_script": verify_path.is_file(),
        "prepush_blocks_on_unconditional_xcodebuild": blocking,
        "uses_incremental_layer_selection": incremental,
        "has_derived_data_or_build_lock": isolation,
        "tier": "blocking_prepush_xcodebuild" if blocking else "local_fast_spm" if fast_spm else "not_detected",
        "note": "Static literal-shell indicators only; external scripts and dynamic commands require manual review.",
    }


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
        "verifyGateHealth": verify_gate_health(repo),
        "note": "Confirm custom Package.swift target paths before executing commands.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = _SelfDisclosingXcodeParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Explicit repo-relative paths, or a single repository directory")
    parser.add_argument("--repo", "--repo-dir", "--worktree", "--cwd", help="Git repository root or subdirectory")
    parser.add_argument("--base", "--base-ref", help="Base ref; defaults to origin/main or main on a clean branch")
    parser.add_argument("--head", "--head-ref", help="Head ref for committed changes (default HEAD)")
    parser.add_argument("--range", "--diff-range", dest="diff_range", help="Explicit a..b snapshot diff or a...b merge-base diff")
    parser.add_argument("--committed-only", action="store_true", help="Exclude staged, unstaged, and untracked changes")
    parser.add_argument("--json", action="store_true", help="Compatibility flag; output is always JSON")
    parser.add_argument("--check-verify-gate", action="store_true", help="Inspect local/pre-push Apple gate health without running builds")
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
    if args.diff_range is not None and (args.base is not None or args.head is not None):
        parser.error("--range cannot be combined with --base or --head")
    if args.base == "" or args.head == "" or args.diff_range == "":
        parser.error("git refs and ranges must not be empty")
    if args.repo is None and len(args.paths) == 1:
        candidate = Path(args.paths[0])
        if candidate.is_dir() and (candidate / ".git").exists():
            args.repo, args.paths = str(candidate), []

    cases_path = Path(args.cases_file).resolve()
    if args.flow_map or args.paths == ["flow-map"]:
        payload = build_flow_map(cases_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("ok") else 1

    if args.register_case:
        if not args.id or not args.title or not args.fix_rule:
            parser.error("--register-case requires --id, --title, and --fix-rule")
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
        repo = Path(git(Path(args.repo or ".").resolve(), "rev-parse", "--show-toplevel").decode().strip())
        if args.check_verify_gate:
            payload = {"verifyGateHealth": verify_gate_health(repo)}
        else:
            paths = sorted(set(args.paths)) if args.paths else changed_paths(
                repo, args.base, args.head or "HEAD", args.diff_range, args.committed_only,
            )
            payload = classify(repo, paths, cases_file=cases_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        detail = error.stderr.decode(errors="replace") if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        parser.error(detail)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
