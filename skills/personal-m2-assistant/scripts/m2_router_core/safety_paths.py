from __future__ import annotations

from ._imports import *

from .constants_and_paths import WHITELISTED_HERDR_ENV_VARS

def _safe_roots_for_target(target_cwd: str = "", *, include_target_cwd: bool = True) -> list[str]:
    baseline_roots = [_expand_cwd(root).rstrip("/") for root in SAFE_BASELINE_ROOTS if _expand_cwd(root)]
    if not include_target_cwd:
        return baseline_roots
    expanded_target = _expand_cwd(target_cwd).rstrip("/")
    if expanded_target and _path_is_under_safe_roots(expanded_target, baseline_roots):
        if expanded_target not in baseline_roots:
            baseline_roots.append(expanded_target)
    return baseline_roots

def _lexical_within(path_str: str, roots: list[str]) -> bool:
    norm = os.path.abspath(path_str).rstrip("/")
    for root in roots:
        norm_root = os.path.abspath(root).rstrip("/")
        if norm == norm_root or norm.startswith(norm_root + "/"):
            return True
    return False

def _resolved_within(path_obj: Path, resolved_roots: list[str]) -> bool:
    try:
        resolved_str = str(path_obj.resolve(strict=False)).rstrip("/")
    except OSError:
        return False
    for r_root in resolved_roots:
        if resolved_str == r_root or resolved_str.startswith(r_root + "/"):
            return True
    return False

def _strip_matching_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"', "`"}:
        return stripped[1:-1].strip()
    return stripped

def _is_clean_single_path_token(token: str, *, allow_relative: bool = False) -> bool:
    if not token or "\n" in token or "\r" in token or "\x00" in token:
        return False
    if any(ch in token for ch in _FORBIDDEN_SINGLE_PATH_CHARS):
        return False
    if re.search(r"\s+(?:~|/)[A-Za-z0-9_.-]", token):
        return False
    if allow_relative:
        return True
    return token.startswith("/") or token.startswith("~/") or token == "~"

def _split_symlink_arrow_path(raw_text: str) -> tuple[str, str] | None:
    """Parse '<symlink_path> -> <target_path>' into (source_path, resolved_target_path) or None."""
    candidate = _strip_matching_quotes(_text(raw_text).rstrip("?").strip())
    if "->" not in candidate and "→" not in candidate:
        return None
    parts = re.split(r"\s*(?:->|→)\s*", candidate)
    if len(parts) != 2:
        return None
    src = _strip_matching_quotes(parts[0].strip())
    dst = _strip_matching_quotes(parts[1].rstrip("?").strip())
    if not _is_clean_single_path_token(src, allow_relative=False):
        return None
    src_parts = [p for p in re.split(r"[/\\]+", src) if p]
    if ".." in src_parts or "." in src_parts:
        return None
    if not _is_clean_single_path_token(dst, allow_relative=True):
        return None
    if dst.startswith("/") or dst.startswith("~/") or dst == "~":
        dst_parts = [p for p in re.split(r"[/\\]+", dst) if p]
        if ".." in dst_parts or "." in dst_parts:
            return None
        resolved_dst = dst
    else:
        expanded_src = _expand_cwd(src)
        if not expanded_src or not os.path.isabs(expanded_src):
            return None
        resolved_dst = os.path.normpath(os.path.join(os.path.dirname(expanded_src), dst))
    return src, resolved_dst

def _is_forbidden_redline_path(expanded_path: str) -> bool:
    norm_abs = os.path.abspath(expanded_path).rstrip("/") or "/"
    home_obj = Path.home().resolve(strict=False)
    home_abs = str(home_obj).rstrip("/")
    users_root = str(home_obj.parent).rstrip("/")
    if norm_abs in {"/", "/Volumes", "/System", "/Library", "/etc", "/var", "/private", home_abs, users_root}:
        return True
    if str(Path(norm_abs).parent).rstrip("/") == users_root:
        return True
    return False

def _path_is_under_safe_roots(candidate_path: str, safe_roots: list[str]) -> bool:
    raw = _text(candidate_path)
    if not raw or "\n" in raw or "\r" in raw or "\x00" in raw:
        return False
    if "->" in raw or "→" in raw:
        arrow_pair = _split_symlink_arrow_path(raw)
        if arrow_pair is None:
            return False
        src_path, dst_path = arrow_pair
        if not _path_is_under_safe_roots(src_path, safe_roots):
            return False
        if not _path_is_under_safe_roots(dst_path, safe_roots):
            return False
        expanded_src = os.path.abspath(_expand_cwd(src_path))
        pkg_roots = [os.path.abspath(r).rstrip("/") for r in READONLY_PACKAGE_MANAGER_ROOTS]
        if _lexical_within(expanded_src, pkg_roots):
            expanded_dst = os.path.abspath(_expand_cwd(dst_path))
            if not _lexical_within(expanded_dst, pkg_roots):
                return False
        return True

    if re.search(r"[*?\[\]{}$`<>|;]", raw):
        return False
    parts = [p for p in re.split(r"[/\\]+", raw) if p]
    if ".." in parts or "." in parts:
        return False
    expanded = _expand_cwd(raw)
    if not expanded or not os.path.isabs(expanded):
        return False
    if _is_forbidden_redline_path(expanded):
        return False

    expanded_roots = [
        os.path.abspath(_expand_cwd(r)).rstrip("/")
        for r in safe_roots
        if _expand_cwd(r) and not _is_forbidden_redline_path(_expand_cwd(r))
    ]
    if not expanded_roots:
        return False
    if not _lexical_within(expanded, expanded_roots):
        return False

    resolved_roots: list[str] = []
    for r in expanded_roots:
        try:
            resolved_roots.append(str(Path(r).resolve(strict=False)).rstrip("/"))
        except OSError:
            continue
    if not resolved_roots:
        return False

    cand_obj = Path(os.path.abspath(expanded))
    if not _resolved_within(cand_obj, resolved_roots):
        return False

    # Walk every prefix from deepest allowed lexical root down to candidate to catch symlinks escaping safe roots.
    matching_lex_roots = [
        r for r in expanded_roots
        if os.path.abspath(expanded).rstrip("/") == r or os.path.abspath(expanded).startswith(r + "/")
    ]
    base_lex = max(matching_lex_roots, key=len)
    base_path = Path(base_lex)
    try:
        rel_parts = cand_obj.relative_to(base_path).parts
    except ValueError:
        return False

    cur = base_path
    if cur.is_symlink() and not _resolved_within(cur, resolved_roots):
        return False
    for part in rel_parts:
        cur = cur / part
        try:
            is_link = cur.is_symlink()
            exists = cur.exists()
        except OSError:
            return False
        if is_link or exists:
            if not _resolved_within(cur, resolved_roots):
                return False
            if is_link:
                try:
                    link_target = os.readlink(cur)
                except OSError:
                    return False
                resolved_target = (cur.parent / link_target) if not os.path.isabs(link_target) else Path(link_target)
                if not _resolved_within(resolved_target, resolved_roots):
                    return False
    return True

def _extract_single_dialog_path(frame_text: str) -> str | None:
    """Extract the exact single path from a path/folder permission prompt, or None if ambiguous."""
    patterns = (
        re.compile(r"(?:Allow\s+(?:path|directory)\s+access\s+to|Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|Confirm\s+folder\s+trust\s*:?|Trust\s+folder\s*:?|access\s+to)\s+([^\n?]+?)\??\s*$", re.I),
        re.compile(r"^(?:Path|Directory|Folder)\s*:\s*([^\n]+?)\s*$", re.I),
    )
    extracted: list[str] = []
    for raw_line in frame_text.splitlines():
        line = raw_line.strip()
        if not line or re.match(r"^(?:[>❯●◉▶]|\d+[.)])\s+", line):
            continue
        for pat in patterns:
            m = pat.search(line)
            if m:
                candidate = m.group(1).strip()
                if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"', "`"}:
                    candidate = candidate[1:-1].strip()
                else:
                    candidate = candidate.rstrip("?").strip()
                    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"', "`"}:
                        candidate = candidate[1:-1].strip()
                if candidate and candidate not in extracted:
                    extracted.append(candidate)
                break
    if len(extracted) != 1:
        return None
    single = extracted[0]
    if "->" in single or "→" in single:
        arrow_pair = _split_symlink_arrow_path(single)
        if arrow_pair is None:
            return None
        return f"{arrow_pair[0]} -> {arrow_pair[1]}"
    if not (single.startswith("/") or single.startswith("~/") or single == "~"):
        return None
    # Reject multiple unquoted path tokens in the same captured span
    if re.search(r"\s+(?:~|/)[A-Za-z0-9_.-]", single):
        return None
    if any(ch in single for ch in _FORBIDDEN_SINGLE_PATH_CHARS):
        return None
    return single

def _strip_tui_box_chrome(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in _text(text).splitlines():
        stripped = re.sub(r"^[\s│┃╭╮╰╯─━┌┐└┘├┤┬┴┼]+|[\s│┃╭╮╰╯─━┌┐└┘├┤┬┴┼]+$", "", raw_line)
        if stripped:
            cleaned_lines.append(stripped)
    return "\n".join(cleaned_lines)

def _extract_dialog_paths(frame_text: str) -> list[str]:
    unboxed = _strip_tui_box_chrome(frame_text)
    single = _extract_single_dialog_path(unboxed)
    if single is not None:
        return [single]
    # If inline header pattern had multiple paths on one line, do not fall through
    for line in unboxed.splitlines():
        if re.search(r"(?:Allow\s+(?:path|directory)\s+access\s+to|access\s+to)\s+", line, re.I):
            return []
    raw_lines = [ln.strip() for ln in unboxed.splitlines() if ln.strip()]
    merged_lines: list[str] = []
    idx = 0
    while idx < len(raw_lines):
        cur = raw_lines[idx]
        if (cur.endswith("->") or cur.endswith("→")) and idx + 1 < len(raw_lines):
            merged_lines.append(f"{cur} {raw_lines[idx + 1]}")
            idx += 2
            continue
        merged_lines.append(cur)
        idx += 1

    paths: list[str] = []
    for line in merged_lines:
        if not line or re.match(r"^(?:[>❯●◉▶]|\d+[.)])\s+", line):
            continue
        if "->" in line or "→" in line:
            arrow_pair = _split_symlink_arrow_path(line)
            if arrow_pair is None:
                return []
            normalized_arrow = f"{arrow_pair[0]} -> {arrow_pair[1]}"
            if normalized_arrow not in paths:
                paths.append(normalized_arrow)
            continue
        if not (line.startswith("/") or line.startswith("~/") or line == "~"):
            continue
        if re.search(r"\s+(?:~|/)[A-Za-z0-9_.-]", line):
            return []
        if any(ch in line for ch in _FORBIDDEN_SINGLE_PATH_CHARS):
            return []
        if line not in paths:
            paths.append(line)
    return paths

def _is_safe_git_command(args: list[str]) -> bool:
    idx = 0
    while idx < len(args):
        tok = args[idx]
        if tok in {"--no-pager", "-P", "--no-optional-locks"}:
            idx += 1
            continue
        if tok == "-C":
            if idx + 1 >= len(args):
                return False
            if not _path_is_under_safe_roots(args[idx + 1], _safe_roots_for_target(include_target_cwd=False)):
                return False
            idx += 2
            continue
        if tok.startswith("-"):
            return False
        break
    if idx >= len(args):
        return False
    subcmd = args[idx]
    rest = args[idx + 1 :]
    if subcmd not in _READONLY_GIT_SUBCOMMANDS:
        return False
    for flag in rest:
        if any(flag == p or flag.startswith(p + "=") for p in _FORBIDDEN_GIT_FLAGS):
            return False
    if subcmd == "worktree":
        return rest == ["list"] or rest == ["list", "--porcelain"]
    if subcmd == "branch":
        if not rest:
            return False
        return all(tok in _SAFE_GIT_BRANCH_FLAGS for tok in rest)
    return True

def _is_safe_gh_command(args: list[str]) -> bool:
    if len(args) < 2:
        return False
    group, action = args[0], args[1]
    rest = args[2:]
    allowed = {
        "pr": {"view", "list", "status", "checks"},
        "run": {"view", "list"},
        "issue": {"view", "list"},
    }
    if action not in allowed.get(group, set()):
        return False
    for flag in rest:
        if flag in {"-w", "--web"} or flag.startswith("--output"):
            return False
    return True

def _is_safe_herdr_command(args: list[str]) -> bool:
    if len(args) < 2:
        return False
    group, action = args[0], args[1]
    if group == "agent" and action in {"list", "get", "read", "wait"}:
        return True
    if group == "pane" and action == "list" and len(args) == 2:
        return True
    if group == "workspace" and action == "list" and len(args) == 2:
        return True
    return False

def _is_safe_python_command(args: list[str]) -> bool:
    if len(args) == 1 and args[0] in {"--version", "-V", "--help", "-h"}:
        return True
    if len(args) >= 2 and args[0] == "-m" and args[1] == "unittest":
        for tok in args[2:]:
            if tok in {"-c", "-k"}:
                continue
            if tok.startswith("-"):
                if tok not in {"-v", "--verbose", "-q", "--quiet", "-f", "--failfast", "-b", "--buffer", "discover", "-s", "-p", "-t"}:
                    return False
            elif ".." in tok or tok.startswith("/") or tok.startswith("~"):
                return False
        return True
    return False

def _is_whitelisted_command_segment(segment: str) -> bool:
    tokens = shlex.split(segment.strip()) if segment.strip() else []
    if not tokens:
        return False
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        name, value = tokens[0].split("=", 1)
        if name not in WHITELISTED_HERDR_ENV_VARS or not re.fullmatch(r"[A-Za-z0-9_.:/-]*", value):
            return False
        tokens.pop(0)
    if not tokens:
        return False
    if "/" in tokens[0] and Path(tokens[0]).parent not in {Path("/bin"), Path("/usr/bin"), Path("/usr/local/bin"), Path("/opt/homebrew/bin")}:
        return False
    binary = Path(tokens[0]).name
    if binary not in WHITELISTED_CMD_BINARIES:
        return False
    args = tokens[1:]
    if binary == "env":
        return len(args) == 0
    if binary == "git":
        return _is_safe_git_command(args)
    if binary == "gh":
        return _is_safe_gh_command(args)
    if binary == "herdr":
        return _is_safe_herdr_command(args)
    if binary == "python3":
        return _is_safe_python_command(args)
    if binary == "find":
        forbidden_find = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
        return not any(tok.lower() in forbidden_find or tok.lower().startswith("-fprint") or tok.lower().startswith("-fls") for tok in args)
    if binary == "jq":
        return not any(tok in {"-f", "--from-file", "-L"} for tok in args)
    if binary in {"rg", "grep"}:
        return not any(tok == "--pre" or tok.startswith("--pre=") or tok == "--pre-glob" or tok.startswith("--pre-glob=") for tok in args)
    if binary == "sleep":
        return len(args) == 1 and bool(re.match(r"^\d+(?:\.\d+)?$", args[0]))
    if binary in {"date", "shasum", "ls", "cat", "head", "tail"}:
        return not any(tok in {"-s", "--set"} for tok in args)
    return False
