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
import tempfile
from pathlib import Path, PurePosixPath


SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES_FILE = SKILL_DIR / "references" / "build-cases.json"
TOP_K_CASES = 3
MAX_ACTIVE_CASES = 12
L1_REF_MAX_LINES = 160
IGNORED_PARTS = {".build", ".git", "DerivedData", "node_modules"}
XCODE_EXACT_NAMES = {"project.yml", "project.yaml", "Project.swift", "Workspace.swift", "Tuist.swift", "Podfile", "Cartfile"}
NEUTRAL_NAMES = {"AGENTS.md", "CLAUDE.md", "LICENSE", "LICENSE.md", "README", "README.md"}
NEUTRAL_SUFFIXES = {".md", ".markdown", ".rst", ".adoc"}


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def nul_paths(output: bytes) -> set[str]:
    return {v.decode("utf-8", errors="surrogateescape") for v in output.split(b"\0") if v}


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
    repo: Path, base: str | None, head: str = "HEAD", diff_range: str | None = None, committed_only: bool = False,
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
    match = re.search(
        r"(?:\$\{?(?:RUN_XCODEBUILD|RUN_HEAVY|mode|MODE)\b[^\s]*|mode)\s*(==|=|!=|-eq|-ne)\s*(1|0|true|false|ci)\b",
        " ".join(condition_tokens),
    )
    if not match:
        return 0
    operator, value = match.groups()
    positive = ((operator in {"==", "=", "-eq"}) == (value in {"1", "true", "ci"})) ^ negated
    if ("||" in tokens and positive) or ("&&" in tokens and not positive):
        return 0
    return 1 if positive else -1


def _shell_commands(text: str) -> dict[str, list[tuple[list[str], bool]]]:
    """Inspect literal shell commands and function calls without executing repo code."""
    text = re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"|#[^\n]*",
                  lambda m: "" if m.group().startswith("#") else m.group(), text)
    lexer = shlex.shlex(text.replace("\\\n", " "), posix=True, punctuation_chars=";&|()")
    lexer.whitespace, lexer.wordchars, lexer.commenters = " \t\r", lexer.wordchars + "!", ""
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
        pass
    statements.append((current, ""))
    commands: dict[str, list[tuple[list[str], bool]]] = {"": []}
    owner, branches, short_guard = "", [], False
    for tokens, connector in statements:
        if not tokens:
            short_guard = False
            continue
        if len(tokens) >= 2 and tokens[1] == "()":
            owner = tokens[0]
            commands.setdefault(owner, [])
            tokens = tokens[3:] if tokens[2:3] == ["{"] else tokens[2:]
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
            owner, tokens = "", []
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


def _extract_top_level_target_args(call_body: str) -> str:
    """Keep only top-level target argument text outside nested (...) and [...]."""
    out, depth, in_str, escaped = [], 0, False, False
    for ch in call_body:
        if in_str:
            out.append(ch if depth == 0 else " ")
            escaped = (not escaped) and (ch == "\\")
            in_str = not (ch == '"' and not escaped)
        elif ch == '"':
            in_str = True
            out.append(ch if depth == 0 else " ")
        elif ch in "([])":
            depth = max(0, depth + (1 if ch in "([" else -1))
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _parse_package_manifest_targets(manifest: Path) -> tuple[set[str], set[PurePosixPath], set[PurePosixPath]]:
    """Return (declared_target_names, declared_target_roots, explicit_custom_paths) from Package.swift."""
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set(), set(), set()
    names, roots, explicit_paths = set(), set(), set()
    for match in re.finditer(r"\.\s*(target|executableTarget|testTarget|plugin|macro|systemLibrary|binaryTarget)\s*\(", text):
        kind, idx, depth, in_str, escaped = match.group(1), match.end(), 1, False, False
        while idx < len(text) and depth > 0:
            ch = text[idx]
            if in_str:
                escaped = (not escaped) and (ch == "\\")
                in_str = not (ch == '"' and not escaped)
            elif ch == '"':
                in_str = True
            elif ch in "()":
                depth += 1 if ch == "(" else -1
            idx += 1
        top_args = _extract_top_level_target_args(text[match.end():max(match.end(), idx - 1)])
        name_m, path_m = re.search(r'\bname\s*:\s*"([^"]+)"', top_args), re.search(r'\bpath\s*:\s*"([^"]+)"', top_args)
        if name_m:
            names.add(name_m.group(1).strip())
        if path_m and (norm := PurePosixPath(path_m.group(1).strip().strip("/"))).parts and norm != PurePosixPath("."):
            explicit_paths.add(norm)
            roots.add(norm)
        elif name_m:
            base = "Tests" if kind == "testTarget" else "Plugins" if kind == "plugin" else "Sources"
            roots.add(PurePosixPath(base) / name_m.group(1).strip())
    return names, roots, explicit_paths


def _has_xcode_or_app_structure(repo: Path) -> bool:
    return any((repo / n).is_file() for n in XCODE_EXACT_NAMES) or any((repo / d).is_dir() for d in ("App", "Apps")) or any(repo.glob("*.xcodeproj")) or any(repo.glob("*.xcworkspace"))


def _is_under_prefix(path: PurePosixPath, prefix: PurePosixPath) -> bool:
    return path == prefix or path.parts[:len(prefix.parts)] == prefix.parts


def _root_package_owns_path(repo: Path, relative: PurePosixPath) -> bool:
    if relative.as_posix() in {"Package.swift", "Package.resolved"}:
        return True
    names, declared_roots, explicit_paths = _parse_package_manifest_targets(repo / "Package.swift")
    if any(_is_under_prefix(relative, custom) for custom in explicit_paths):
        return True
    if not relative.parts or relative.parts[0] not in {"Sources", "Tests", "Plugins", "Fixtures", "Resources"}:
        return False
    is_app_entry = relative.name in {"App.swift", "AppDelegate.swift", "SceneDelegate.swift"} or relative.name.endswith("App.swift")
    if len(relative.parts) >= 3:
        subdir = relative.parts[1]
        if declared_roots:
            return any(_is_under_prefix(relative, root) for root in declared_roots)
        if subdir in {"App", "Apps"} and subdir not in names:
            return False
        if is_app_entry and _has_xcode_or_app_structure(repo) and subdir not in names:
            return False
        return True
    if is_app_entry and (declared_roots or _has_xcode_or_app_structure(repo)) and relative.stem not in names:
        return False
    return True


def _extract_yaml_target_section(cfg_text: str, target: str) -> str:
    targets_indent = target_indent = settings_indent = None
    found_targets, collected, top_settings = False, [], []
    for line in cfg_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if settings_indent is not None:
            if indent > settings_indent:
                top_settings.append(line)
            else:
                settings_indent = None
        if target_indent is not None:
            if indent > target_indent:
                collected.append(line)
                continue
            break
        if targets_indent is not None and indent <= targets_indent:
            targets_indent = None
        if indent == 0 and re.match(r"^settings\s*:", stripped):
            settings_indent = indent
        elif re.match(r"^targets\s*:", stripped):
            targets_indent, found_targets = indent, True
        elif targets_indent is not None and indent > targets_indent and re.match(rf"^[\"']?{re.escape(target)}[\"']?\s*:", stripped):
            target_indent = indent
            collected.append(line)
    if collected:
        target_text = "\n".join(collected)
        keys = ("ENABLE_DEBUG_DYLIB", "CODE_SIGN_STYLE", "DEVELOPMENT_TEAM", "CODE_SIGN_IDENTITY", "CODE_SIGNING_ALLOWED")
        extra = [ln for ln in top_settings if not any(k in target_text for k in keys if k in ln)]
        return ("\n".join(extra) + "\n" + target_text) if extra else target_text
    return "" if found_targets else cfg_text


def _extract_pbxproj_target_section(cfg_text: str, target: str) -> str:
    esc = re.escape(target)
    list_ids = {
        m.group(1)
        for m in re.finditer(
            rf"([A-Za-z0-9_]+)\s*/\*\s*Build configuration list for PBXNativeTarget [\"']?{esc}[\"']?\s*\*/\s*=\s*\{{([^}}]*)\}}",
            cfg_text,
            re.DOTALL,
        )
    }
    for match in re.finditer(r"([A-Za-z0-9_]+)(?:\s*/\*([^*]*)\*/)?\s*=\s*\{([^{}]*?isa\s*=\s*PBXNativeTarget;[^}]*)\}", cfg_text, re.DOTALL):
        comment, body = match.group(2) or "", match.group(3)
        if re.search(rf"\b{esc}\b", comment) or re.search(rf"\b(?:name|productName)\s*=\s*[\"']?{esc}[\"']?\s*;", body):
            if cfg_list := re.search(r"\bbuildConfigurationList\s*=\s*([A-Za-z0-9_]+)", body):
                list_ids.add(cfg_list.group(1))
    config_ids: set[str] = set()
    for list_id in list_ids:
        for match in re.finditer(rf"\b{re.escape(list_id)}(?:\s*/\*[^*]*\*/)?\s*=\s*\{{([^}}]*)\}}", cfg_text, re.DOTALL):
            if cfgs := re.search(r"\bbuildConfigurations\s*=\s*\(([^)]*)\)", match.group(1), re.DOTALL):
                config_ids.update(re.findall(r"\b([A-Za-z0-9_]+)\b", re.sub(r"/\*[^*]*\*/", "", cfgs.group(1))))
    debug_sections, other_sections = [], []
    for match in re.finditer(
        r"([A-Za-z0-9_]+)(?:\s*/\*([^*]*)\*/)?\s*=\s*\{[^{}]*?buildSettings\s*=\s*\{([^}]*)\}([^{}]*)\}",
        cfg_text,
        re.DOTALL,
    ):
        cid, comment, settings_body, tail = match.group(1), (match.group(2) or "").strip(), match.group(3), match.group(4) or ""
        if cid not in config_ids and not (
            re.search(rf"\b{esc}\b", comment)
            or re.search(rf"\b(?:PRODUCT_NAME|TARGET_NAME|name)\s*=\s*[\"']?{esc}[\"']?\s*;", settings_body + "\n" + tail)
        ):
            continue
        is_release = bool(re.search(r"\bRelease\b", comment) or re.search(r"\bname\s*=\s*[\"']?Release[\"']?\s*;", tail))
        (other_sections if is_release else debug_sections).append(settings_body)
    if debug_sections or other_sections:
        return "\n".join(debug_sections or other_sections)
    has_other_targets = bool(
        re.search(r"\bisa\s*=\s*PBXNativeTarget\b|\bBuild configuration list for PBXNativeTarget\b|\b(?:PRODUCT_NAME|TARGET_NAME)\s*=", cfg_text)
    )
    return "" if has_other_targets else cfg_text


def _has_valid_local_signing(cfg_text: str) -> bool:
    """Validate local signing inside a target's scoped configuration block."""
    if not cfg_text.strip():
        return False
    has_manual_adhoc = bool(
        re.search(r"CODE_SIGN_STYLE\s*[:=]\s*[\"']?Manual\b", cfg_text, re.IGNORECASE)
        and re.search(r"CODE_SIGN_IDENTITY\s*[:=]\s*[\"']-\"?", cfg_text)
    )
    if has_manual_adhoc:
        return True
    if re.search(r"CODE_SIGNING_ALLOWED\s*[:=]\s*[\"']?(?:NO|false)\b", cfg_text, re.IGNORECASE):
        return False
    non_empty_teams = [
        m.group(1).strip()
        for m in re.finditer(r"DEVELOPMENT_TEAM\s*[:=]\s*[\"']?([A-Za-z0-9_]+)[\"']?", cfg_text)
        if m.group(1).strip() and m.group(1).strip().lower() not in {"null", "none"}
    ]
    has_automatic = bool(re.search(r"CODE_SIGN_STYLE\s*[:=]\s*[\"']?Automatic\b", cfg_text, re.IGNORECASE))
    has_empty_team = bool(re.search(r"DEVELOPMENT_TEAM\s*[:=]\s*(?:[\"']\s*[\"']|(?:\r?\n|$|;))", cfg_text))
    if has_automatic and (has_empty_team or not non_empty_teams):
        return False
    if has_empty_team and not non_empty_teams:
        return False
    return True


def get_build_case(case_id: str, cases_file: Path = DEFAULT_CASES_FILE) -> dict[str, object] | None:
    """Look up a single build case dictionary by ID from build-cases.json."""
    doc = load_build_cases(cases_file)
    for key in ("active_cases", "archived_cases"):
        for entry in doc.get(key, []) if isinstance(doc.get(key), list) else []:
            if isinstance(entry, dict) and entry.get("id") == case_id:
                return dict(entry)
    return None


def _inspect_preview_host_health(repo: Path, cases_file: Path = DEFAULT_CASES_FILE) -> dict[str, object]:
    """Target-scoped inspection of *PreviewHost ENABLE_DEBUG_DYLIB = YES and local signing."""
    manifests = sorted(p for p in repo.rglob("Package.swift") if p.is_file() and not (IGNORED_PARTS & set(p.relative_to(repo).parts)))
    all_targets, config_files, missing_packages, invalid_signing_packages = [], [], [], []
    for manifest in manifests:
        try:
            pkg_text = manifest.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        targets = list(dict.fromkeys(re.findall(r"\b\w*PreviewHost\b", pkg_text)))
        if not targets:
            continue
        all_targets.extend(t for t in targets if t not in all_targets)
        pkg_dir = manifest.parent
        rel_pkg = "." if pkg_dir == repo else pkg_dir.relative_to(repo).as_posix()
        candidates: list[Path] = []
        for base_dir in ([pkg_dir] if pkg_dir == repo else [pkg_dir, repo]):
            for cand in (base_dir / "project.yml", base_dir / "project.yaml", *sorted(base_dir.glob("*.xcodeproj/project.pbxproj"))):
                if cand.is_file() and cand not in candidates:
                    candidates.append(cand)
        config_files.extend(c.relative_to(repo).as_posix() for c in candidates if c.relative_to(repo).as_posix() not in config_files)
        pkg_dylib_missing = pkg_signing_bad = False
        for target in targets:
            target_sections: list[str] = []
            for cand in candidates:
                try:
                    cfg_text = cand.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                sec = _extract_pbxproj_target_section(cfg_text, target) if cand.suffix == ".pbxproj" else _extract_yaml_target_section(cfg_text, target)
                if sec.strip():
                    target_sections.append(sec)
            combined = "\n".join(target_sections)
            has_no = bool(re.search(r"ENABLE_DEBUG_DYLIB\s*[:=]\s*[\"']?(?:NO|false)\b", combined, re.IGNORECASE))
            has_yes = bool(re.search(r"ENABLE_DEBUG_DYLIB\s*[:=]\s*[\"']?(?:YES|true)\b", combined, re.IGNORECASE))
            if not target_sections or not has_yes or has_no:
                pkg_dylib_missing = True
            if not target_sections or any(not _has_valid_local_signing(sec) for sec in target_sections):
                pkg_signing_bad = True
        if pkg_dylib_missing:
            missing_packages.append(rel_pkg)
        if pkg_signing_bad:
            invalid_signing_packages.append(rel_pkg)

    has_preview_host = bool(all_targets)
    dylib_ok, signing_ok = has_preview_host and not missing_packages, has_preview_host and not invalid_signing_packages
    status = "not_applicable" if not has_preview_host else "missing_enable_debug_dylib" if not dylib_ok else "invalid_local_signing" if not signing_ok else "ok"
    xc005 = get_build_case("XC-005", cases_file=cases_file)
    triggered_id = str(xc005["id"]) if xc005 and "id" in xc005 else "XC-005"
    return {
        "has_preview_host_target": has_preview_host, "preview_host_targets": all_targets, "config_files": config_files,
        "missing_packages": missing_packages, "invalid_signing_packages": invalid_signing_packages,
        "enable_debug_dylib_yes": dylib_ok, "local_signing_valid": signing_ok, "status": status,
        "triggered_case": triggered_id if (missing_packages or invalid_signing_packages) else None,
    }


def preview_host_health(repo: Path, cases_file: Path = DEFAULT_CASES_FILE) -> dict[str, object]:
    """Check whether SwiftPM *PreviewHost targets have ENABLE_DEBUG_DYLIB = YES and valid local signing in project.yml / .xcodeproj."""
    return _inspect_preview_host_health(repo, cases_file=cases_file)


def verify_gate_health(repo: Path, cases_file: Path = DEFAULT_CASES_FILE) -> dict[str, object]:
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
        "previewHostHealth": preview_host_health(repo, cases_file=cases_file),
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
            if candidate == repo and not _root_package_owns_path(repo, relative):
                return None
            return candidate
        if candidate == repo:
            break
        candidate = candidate.parent
    return None


def load_build_cases(cases_file: Path = DEFAULT_CASES_FILE) -> dict[str, object]:
    if not cases_file.is_file():
        return {"schema": "apple-build-cases/v1", "version": 1, "active_cases": [], "archived_cases": []}
    return json.loads(cases_file.read_text(encoding="utf-8"))


def _signals_preview_dylib(
    paths: list[str] | None = None, repo: Path | None = None, cases_file: Path = DEFAULT_CASES_FILE,
) -> bool:
    if repo is not None and preview_host_health(repo, cases_file=cases_file).get("triggered_case") is not None:
        return True
    for raw_path in paths or []:
        lowered, name = raw_path.lower(), PurePosixPath(raw_path).name.lower()
        if name in {"project.yml", "project.yaml"} or "previewhost" in lowered or "enable_debug_dylib" in lowered:
            return True
        if repo is not None and (candidate := repo / Path(*PurePosixPath(raw_path).parts)).is_file():
            try:
                snippet = candidate.read_text(encoding="utf-8", errors="replace")[:65536]
            except OSError:
                continue
            if "ENABLE_DEBUG_DYLIB" in snippet or "PreviewHost" in snippet:
                return True
    return False


_CONCURRENCY_CONTENT_RE = re.compile(
    r"(?:@MainActor\b|@Sendable\b|@preconcurrency\b|@globalActor\b|\bSendable\b|"
    r"\bactor\s+[A-Za-z_]|\bnonisolated\b|\bStrictConcurrency\b|"
    r"SWIFT_STRICT_CONCURRENCY|\bswiftSettings\b|\benableExperimentalFeature\s*\(\s*\"StrictConcurrency\")"
)


def _signals_concurrency(paths: list[str] | None = None, repo: Path | None = None) -> bool:
    for raw_path in paths or []:
        lowered = raw_path.lower()
        if any(tok in lowered for tok in ("concurrency", "sendable", "mainactor", "actor")):
            return True
        if repo is not None and (candidate := repo / Path(*PurePosixPath(raw_path).parts)).is_file():
            try:
                snippet = candidate.read_text(encoding="utf-8", errors="replace")[:65536]
            except OSError:
                continue
            if _CONCURRENCY_CONTENT_RE.search(snippet):
                return True
    return False


def match_build_cases(
    route: str, cases_file: Path = DEFAULT_CASES_FILE, top_k: int = TOP_K_CASES,
    paths: list[str] | None = None, repo: Path | None = None,
) -> list[dict[str, object]]:
    active = load_build_cases(cases_file).get("active_cases", [])
    if not isinstance(active, list):
        return []
    matched = [dict(c) for c in active if isinstance(c, dict) and (route in c.get("routes", []) or route == "none")]
    preview_case = get_build_case("XC-005", cases_file=cases_file)
    concurrency_case = get_build_case("XC-004", cases_file=cases_file)
    preview_id = str(preview_case["id"]) if preview_case and "id" in preview_case else "XC-005"
    concurrency_id = str(concurrency_case["id"]) if concurrency_case and "id" in concurrency_case else "XC-004"
    priority_ids: list[str] = []
    if _signals_preview_dylib(paths=paths, repo=repo, cases_file=cases_file):
        priority_ids.append(preview_id)
    if _signals_concurrency(paths=paths, repo=repo):
        priority_ids.append(concurrency_id)
    if priority_ids:
        matched.sort(key=lambda c: priority_ids.index(str(c.get("id"))) if str(c.get("id")) in priority_ids else len(priority_ids))
    return matched[:max(top_k, len(priority_ids))]


def build_flow_map(cases_file: Path = DEFAULT_CASES_FILE) -> dict[str, object]:
    doc = load_build_cases(cases_file)
    active = doc.get("active_cases", []) if isinstance(doc.get("active_cases"), list) else []
    ref_line_counts = {p.relative_to(SKILL_DIR).as_posix(): len(p.read_text(encoding="utf-8").splitlines()) for p in sorted((SKILL_DIR / "references").glob("*.md"))}
    ok = all(n <= L1_REF_MAX_LINES for n in ref_line_counts.values()) and len(active) <= MAX_ACTIVE_CASES
    return {
        "ok": ok, "skill": "xcode-cli", "architecture_ref": "skills/xcode-cli/SKILL.md",
        "entry_a_ingest": {"A1_register_build_case": "python3 skills/xcode-cli/scripts/classify_changes.py --register-case --id XC-xxx --title '...' --routes swiftpm,xcode --symptom '...' --fix-command '...' --fix-rule '...'"},
        "shared_tiers": {
            "tier_1_json_drawer": {"file": "skills/xcode-cli/references/build-cases.json", "active_case_count": len(active), "active_case_ids": [c.get("id") for c in active if isinstance(c, dict)]},
            "tier_2_l1_docs_line_counts": ref_line_counts,
            "tier_3_deterministic_classifier": "skills/xcode-cli/scripts/classify_changes.py",
        },
        "entry_b_consume": {"B1_classify_and_slice": "python3 skills/xcode-cli/scripts/classify_changes.py --repo <repo> [--base <base>]", "B2_route_reference": "skills/xcode-cli/references/command-routing.md"},
        "anti_explosion_valves": {
            "valve_1_zero_token_path_classifier": True,
            "valve_2_route_scoped_slicing": ["swiftpm", "xcode", "mixed", "none"],
            "valve_3_top_k_and_line_caps": {"matched_cases_top_k": TOP_K_CASES, "l1_doc_max_lines": L1_REF_MAX_LINES, "active_cases_cap": MAX_ACTIVE_CASES},
            "valve_4_one_in_one_out_compaction": True,
            "all_l1_docs_within_budget": ok,
        },
    }


def register_build_case(
    cases_file: Path, case_id: str, title: str, routes: list[str], symptom: str, fix_command: str, fix_rule: str,
) -> dict[str, object]:
    doc = load_build_cases(cases_file)
    active = [c for c in (doc.get("active_cases") or []) if isinstance(c, dict) and c.get("id") != case_id]
    archived = list(doc.get("archived_cases") or [])
    new_entry = {
        "id": case_id, "title": title, "routes": routes, "symptom": symptom,
        "fix_command": fix_command, "fix_rule": fix_rule, "registered_at": date.today().isoformat(),
    }
    active.append(new_entry)
    while len(active) > MAX_ACTIVE_CASES:
        archived.append(active.pop(0))
    doc.update({"active_cases": active, "archived_cases": archived, "updated": date.today().isoformat()})
    cases_file.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"ok": True, "registered": new_entry, "active_count": len(active)}


def classify(repo: Path, paths: list[str], cases_file: Path = DEFAULT_CASES_FILE) -> dict[str, object]:
    package_roots, package_files, neutral_files, xcode_inputs = set(), [], [], []
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
            xcode_inputs.append({"path": raw_path, "reason": "path is not owned by a discovered Swift package"})
    route = (
        "none" if not paths
        else "mixed" if (xcode_inputs and package_roots)
        else "xcode" if xcode_inputs
        else "swiftpm" if package_roots
        else "none"
    )
    return {
        "route": route, "changedFiles": paths, "packageFiles": package_files, "packageRoots": sorted(package_roots),
        "neutralFiles": neutral_files, "xcodeInputs": xcode_inputs,
        "matchedBuildCases": match_build_cases(route, cases_file=cases_file, paths=paths, repo=repo),
        "previewHostHealth": preview_host_health(repo, cases_file=cases_file),
        "verifyGateHealth": verify_gate_health(repo, cases_file=cases_file),
        "note": "Confirm custom Package.swift target paths before executing commands.",
    }


def self_test_preview_host(cases_file: Path = DEFAULT_CASES_FILE) -> dict[str, object]:
    """Deterministic self-test for *PreviewHost ENABLE_DEBUG_DYLIB = YES & local signing (XC-005) enforcement."""
    with tempfile.TemporaryDirectory(prefix="xcode-cli-xc005-") as tmp_dir:
        repo = Path(tmp_dir)
        (repo / "Sources").mkdir(parents=True, exist_ok=True)
        (repo / "Sources" / "Core.swift").write_text("let x = 1\n", encoding="utf-8")
        (repo / "Package.swift").write_text("// swift-tools-version: 6.0\n", encoding="utf-8")
        baseline = classify(repo, ["Sources/Core.swift"], cases_file=cases_file)
        assert baseline["previewHostHealth"]["status"] == "not_applicable"
        assert baseline["matchedBuildCases"][0]["id"] == "XC-001"

        (repo / "Package.swift").write_text(
            '// swift-tools-version: 6.0\nlet package = Package(targets: [.executableTarget(name: "AppPreviewHost")])\n',
            encoding="utf-8",
        )
        missing = classify(repo, ["Sources/Core.swift"], cases_file=cases_file)
        assert missing["previewHostHealth"]["status"] == "missing_enable_debug_dylib"
        assert missing["previewHostHealth"]["triggered_case"] == "XC-005"
        assert missing["matchedBuildCases"][0]["id"] == "XC-005"

        (repo / "project.yml").write_text("targets:\n  AppPreviewHost:\n    settings:\n      ENABLE_DEBUG_DYLIB: NO\n", encoding="utf-8")
        disabled = classify(repo, ["Sources/Core.swift"], cases_file=cases_file)
        assert disabled["previewHostHealth"]["triggered_case"] == "XC-005"
        assert disabled["matchedBuildCases"][0]["id"] == "XC-005"

        (repo / "project.yml").write_text(
            'targets:\n  AppPreviewHost:\n    settings:\n      ENABLE_DEBUG_DYLIB: YES\n      CODE_SIGN_STYLE: Automatic\n      DEVELOPMENT_TEAM: ""\n',
            encoding="utf-8",
        )
        invalid_signing = classify(repo, ["Sources/Core.swift"], cases_file=cases_file)
        assert invalid_signing["previewHostHealth"]["status"] == "invalid_local_signing"
        assert invalid_signing["previewHostHealth"]["local_signing_valid"] is False
        assert invalid_signing["previewHostHealth"]["triggered_case"] == "XC-005"
        assert invalid_signing["matchedBuildCases"][0]["id"] == "XC-005"

        (repo / "project.yml").write_text(
            'targets:\n  AppPreviewHost:\n    settings:\n      ENABLE_DEBUG_DYLIB: YES\n      CODE_SIGN_STYLE: Manual\n      CODE_SIGN_IDENTITY: "-"\n'
            '  HelperCLI:\n    settings:\n      ENABLE_DEBUG_DYLIB: NO\n      CODE_SIGNING_ALLOWED: NO\n',
            encoding="utf-8",
        )
        configured = classify(repo, ["Sources/Core.swift"], cases_file=cases_file)
        assert configured["previewHostHealth"]["status"] == "ok"
        assert configured["previewHostHealth"]["local_signing_valid"] is True
        assert configured["previewHostHealth"]["triggered_case"] is None
        assert configured["matchedBuildCases"][0]["id"] == "XC-001"
        touched = classify(repo, ["Sources/AppPreviewHost/main.swift"], cases_file=cases_file)
        assert touched["matchedBuildCases"][0]["id"] == "XC-005"
        return {
            "ok": True,
            "missing_dylib_case": missing["matchedBuildCases"][0]["id"],
            "missing_dylib_status": missing["previewHostHealth"]["status"],
            "invalid_signing_case": invalid_signing["matchedBuildCases"][0]["id"],
            "invalid_signing_status": invalid_signing["previewHostHealth"]["status"],
            "configured_dylib_status": configured["previewHostHealth"]["status"],
            "touched_preview_host_case": touched["matchedBuildCases"][0]["id"],
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
    parser.add_argument("--self-test-preview-host", action="store_true", help="Run deterministic XC-005 previewHostHealth self-test")
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
    if args.repo is None and len(args.paths) == 1 and Path(args.paths[0]).is_dir() and (Path(args.paths[0]) / ".git").exists():
        args.repo, args.paths = str(Path(args.paths[0])), []

    cases_path = Path(args.cases_file).resolve()
    if args.flow_map or args.paths == ["flow-map"]:
        payload = build_flow_map(cases_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("ok") else 1
    if args.self_test_preview_host:
        payload = self_test_preview_host(cases_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload.get("ok") else 1
    if args.register_case:
        if not args.id or not args.title or not args.fix_rule:
            parser.error("--register-case requires --id, --title, and --fix-rule")
        routes = [item.strip() for item in args.routes.split(",") if item.strip()]
        result = register_build_case(cases_path, args.id, args.title, routes, args.symptom, args.fix_command, args.fix_rule)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    try:
        repo = Path(git(Path(args.repo or ".").resolve(), "rev-parse", "--show-toplevel").decode().strip())
        if args.check_verify_gate:
            payload = {"verifyGateHealth": verify_gate_health(repo, cases_file=cases_path)}
        else:
            paths = sorted(set(args.paths)) if args.paths else changed_paths(repo, args.base, args.head or "HEAD", args.diff_range, args.committed_only)
            payload = classify(repo, paths, cases_file=cases_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        detail = error.stderr.decode(errors="replace") if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        parser.error(detail)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
