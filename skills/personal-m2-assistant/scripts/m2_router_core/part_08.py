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

_READONLY_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "rev-parse",
    "ls-files",
    "worktree",
    "branch",
}

_SAFE_GIT_BRANCH_FLAGS = {"--show-current", "-a", "--all", "-r", "--remotes", "--list"}

_FORBIDDEN_GIT_FLAGS = (
    "-c",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--work-tree",
    "--upload-pack",
    "--receive-pack",
    "--ext-diff",
    "--textconv",
    "--output",
    "--paginate",
    "-p",
)

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
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
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

def _extract_command_block_from_frame(frame_text: str) -> str | None:
    """Extract the full command block from an active command confirmation frame, or None if multiline/ambiguous."""
    lines = [ln.strip() for ln in frame_text.splitlines() if ln.strip()]
    cmd_lines: list[str] = []
    collecting = False
    for line in lines:
        if re.match(r"^(?:[>❯●◉▶]|\d+[.)])\s+", line):
            collecting = False
            continue
        if re.match(
            r"^(?:CMD_CONFIRM|Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command|"
            r"Do\s+you\s+want\s+to\s+run|Allow\s+running|Run\s+shell\s+command)\b",
            line,
            re.I,
        ):
            m_inline = re.match(
                r"^(?:CMD_CONFIRM|Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command)\s*:\s*(.+)$",
                line,
                re.I,
            )
            if m_inline:
                rest = m_inline.group(1).strip()
                if not re.match(r"^(?:Do\s+you|Allow|Confirm|Run\s+command|Execute\s+command)\b", rest, re.I):
                    cmd_lines.append(rest.strip("`"))
                    collecting = True
            continue
        m_cmd = re.match(r"^(?:Command|Run|Execute|Shell|bash|sh)\s*:\s*(.+)$", line, re.I)
        if m_cmd:
            cmd_lines.append(m_cmd.group(1).strip().strip("`"))
            collecting = True
            continue
        if line.startswith("$ "):
            cmd_lines.append(line[2:].strip().strip("`"))
            collecting = True
            continue
        if collecting:
            # Any extra non-choice line after Command: makes it a multiline command block -> reject
            cmd_lines.append(line)
    if len(cmd_lines) != 1:
        return None
    return cmd_lines[0]

def _is_whitelisted_command(cmd_text: str) -> bool:
    stripped = cmd_text.strip()
    if not stripped or "\n" in stripped or "\r" in stripped or "\\" in stripped:
        return False
    # Reject shell chaining, pipes, redirections, command/process substitutions, subshells, backgrounding, or globs
    if re.search(r"[`<>()$*{}\[\];|&]", stripped):
        return False
    try:
        return _is_whitelisted_command_segment(stripped)
    except ValueError:
        return False

_PROMPT_HEADER_RE = re.compile(
    r"(?:Allow\s+path\s+access|Allow\s+directory\s+access|Read\s+access\s+covers\s+this\s+directory|"
    r"Do\s+you\s+want\s+to\s+use\s+this\s+tool|"
    r"\bCurate\s+Memory\b|curate\s+this\s+memory|"
    r"\bstore_memory\b|\bvote_memory\b|Confirm\s+folder\s+trust|"
    r"Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|\bCMD_CONFIRM\b|"
    r"Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command|"
    r"Do\s+you\s+want\s+to\s+run|Allow\s+running|Run\s+shell\s+command|"
    r"\bask_user\b|NEED_GO|NEED_OWNER_DECISION|Owner\s+Gate|Owner\s+decision\s+required|"
    r"Proceed\s+with|What\s+would\s+you\s+like|Which\s+option\s+would\s+you\s+like|"
    r"请选择方案|请确认方案|Question\s+for\s+Owner)",
    re.I,
)

_BUSINESS_DECISION_RE = re.compile(
    r"(?:\bask_user\b|NEED_GO|NEED_OWNER_DECISION|Owner\s+Gate|Owner\s+decision\s+required|"
    r"Proceed\s+with|What\s+would\s+you\s+like|Which\s+option\s+would\s+you\s+like|"
    r"请选择方案|请确认方案|Question\s+for\s+Owner)",
    re.I,
)

_COMPLETION_BOUNDARY_RE = re.compile(
    r"^(?:Completed\.|Done\.|Finished\.|[✓✔✗✘]\s+|\bExit\s+code\b|---+$|═+$|━+$)",
    re.I,
)

_FORBIDDEN_MCP_TOOL_KEYWORDS_RE = re.compile(
    r"(?:^|_)(?:delete|remove|destroy|drop|purge|wipe|retire|share|unshare|merge|bypass|"
    r"send|post|reply|exec|execute|run_pipeline|create_pipeline|rename_pipeline|"
    r"update_build|upload|attachment|diff_review|diff_comment|update_diff)(?:_|$)",
    re.I,
)

_FORBIDDEN_READONLY_MCP_KEYWORDS_RE = re.compile(
    r"(?:^|_)(?:write|create|update|delete|remove|destroy|drop|upsert|upload|link|unlink|"
    r"add|set|send|post|reply|merge|submit|vote|approve|reject|close|reopen|cancel|retry|"
    r"run_pipeline|assign|reorder|bypass|fork|push|commit|patch|edit|save|share|unshare|retire)(?:_|$)",
    re.I,
)

def _normalize_mcp_server_and_tool(raw_server: str, raw_tool: str) -> tuple[str, str]:
    server = _strip_matching_quotes(_text(raw_server)).lower()
    tool = _strip_matching_quotes(_text(raw_tool))
    if not server and tool:
        lower_tool = tool.lower()
        for prefix in ("github-mcp-server-", "ado-edge-", "linear-", "github-", "ado-"):
            if lower_tool.startswith(prefix):
                server = prefix[:-1]
                tool = tool[len(prefix) :]
                break
        if not server:
            for sep in ("/", ":"):
                if sep in tool:
                    head, tail = tool.split(sep, 1)
                    if head.strip() and tail.strip():
                        server = head.strip().lower()
                        tool = tail.strip()
                        break
    if server in {"linear-mcp", "linear_mcp"}:
        server = "linear"
    tool_norm = re.sub(r"[\s_-]+", "_", tool.strip().lower()).strip("_")
    for prefix in ("github_mcp_server_", "ado_edge_", "linear_", "github_", "ado_"):
        if tool_norm.startswith(prefix) and not server:
            server = prefix[:-1].replace("_", "-")
            tool_norm = tool_norm[len(prefix) :]
            break
    if server in {"linear-mcp", "linear_mcp"}:
        server = "linear"
    return server, tool_norm

def _is_whitelisted_mcp_tool(server: str, tool: str) -> bool:
    server_norm, tool_norm = _normalize_mcp_server_and_tool(server, tool)
    if not server_norm or not tool_norm:
        return False
    if _FORBIDDEN_MCP_TOOL_KEYWORDS_RE.search(tool_norm):
        return False
    if server_norm == "linear":
        if tool_norm in WHITELISTED_LINEAR_MCP_TOOLS:
            return True
        if tool_norm.startswith(("get_", "list_", "search_")):
            return True
        return False
    if server_norm in WHITELISTED_READONLY_MCP_SERVERS:
        if _FORBIDDEN_READONLY_MCP_KEYWORDS_RE.search(tool_norm):
            return False
        if tool_norm in WHITELISTED_ADO_READONLY_MCP_TOOLS:
            return True
        if tool_norm.startswith(("get_", "list_", "search_", "show_", "read_", "find_")):
            return True
        return False
    return False

def _is_double_boxed_or_json_payload_line(raw_line: str, unboxed_line: str) -> bool:
    """Return True when a line inside an MCP tool confirmation dialog is part of the inner JSON argument preview box."""
    if re.match(r"^\s*[│┃]\s*[│┃╭╰]", raw_line):
        return True
    stripped = unboxed_line.strip()
    if not stripped:
        return True
    if stripped[0] in {'{', '}', '[', ']', '"'}:
        return True
    if re.match(r'^"[A-Za-z0-9_.-]+"\s*:', stripped):
        return True
    return False

def _extract_mcp_tool_prompt(visible_text: str, raw_active_frame: str) -> dict[str, Any] | None:
    """Extract active MCP tool confirmation details from a 'Do you want to use this tool?' bottom frame."""
    raw_lines = [ln for ln in _text(raw_active_frame).splitlines() if ln.strip()]
    if not raw_lines:
        return None
    unboxed_lines = [_strip_tui_box_chrome(ln) for ln in raw_lines]
    q_idx = -1
    for idx, uline in enumerate(unboxed_lines):
        if re.match(r"^Do\s+you\s+want\s+to\s+use\s+this\s+tool\s*\?\s*$", uline.strip(), re.I):
            q_idx = idx
    if q_idx < 0:
        return None

    post_lines = [ln.strip() for ln in unboxed_lines[q_idx + 1 :] if ln.strip()]
    # If any completion boundary, business decision, or another non-option question appears AFTER the tool question, it is stale/ambiguous.
    for pline in post_lines:
        if _COMPLETION_BOUNDARY_RE.search(pline) or _BUSINESS_DECISION_RE.search(pline):
            return None
        if pline.endswith("?") and not re.match(r"^(?:[>❯●◉▶]?\s*)?\d+[.)]\s+", pline):
            return None

    # Check non-JSON lines before 'Do you want to use this tool?' for conflicting dialog headers
    ambiguous = False
    header_candidates: list[str] = []
    for idx in range(q_idx):
        raw_ln = raw_lines[idx]
        unboxed_ln = unboxed_lines[idx].strip()
        if not unboxed_ln or _is_double_boxed_or_json_payload_line(raw_ln, unboxed_ln):
            continue
        header_candidates.append(unboxed_ln)
        if _BUSINESS_DECISION_RE.search(unboxed_ln):
            ambiguous = True
        elif re.search(
            r"(?:Allow\s+(?:path|directory)\s+access|Read\s+access\s+covers\s+this\s+directory|"
            r"Confirm\s+folder\s+trust|Do\s+you\s+trust\s+the\s+files\s+in\s+this\s+folder|"
            r"\bCurate\s+Memory\b|curate\s+this\s+memory|\bstore_memory\b|\bvote_memory\b|"
            r"\bCMD_CONFIRM\b|Do\s+you\s+want\s+to\s+run|Run\s+command|Execute\s+command|Allow\s+command|Confirm\s+command)",
            unboxed_ln,
            re.I,
        ):
            ambiguous = True
        elif unboxed_ln.endswith("?"):
            ambiguous = True

    post_joined = " ".join(post_lines)
    post_multiline = "\n".join(post_lines)
    server = ""
    tool = ""

    # 1. Authoritative extraction from Copilot CLI's Option 2:
    #    '2. Yes, and don't ask again for tool "Save issue" from "linear" in this repo (...)'
    m_opt2 = re.search(
        r"for\s+tool\s+(?:[\"']([^\"']+)[\"']|([A-Za-z0-9_:/ -]+?))\s+from\s+(?:MCP\s+server\s+)?(?:[\"']([^\"']+)[\"']|([A-Za-z0-9_.-]+))",
        post_joined,
        re.I,
    )
    if m_opt2:
        tool = (m_opt2.group(1) or m_opt2.group(2) or "").strip()
        server = (m_opt2.group(3) or m_opt2.group(4) or "").strip()
    else:
        m_opt2_tool_only = re.search(
            r"for\s+tool\s+(?:[\"']([^\"']+)[\"']|([A-Za-z0-9_:/ -]+?))(?:\s+in\s+this\s+repo|\s+for\s+this\s+session|\s*\(|\s+3[.)]|\s*$)",
            post_joined,
            re.I,
        )
        if m_opt2_tool_only:
            tool = (m_opt2_tool_only.group(1) or m_opt2_tool_only.group(2) or "").strip()

    # 2. Fallback / supplement from header lines above the JSON box (in frame and visible scrollback)
    if not server or not tool:
        all_raw_lines = [ln for ln in _text(visible_text).splitlines() if ln.strip()][-80:]
        extra_headers: list[str] = []
        for rln in reversed(all_raw_lines):
            uln = _strip_tui_box_chrome(rln).strip()
            if not uln or _is_double_boxed_or_json_payload_line(rln, uln):
                continue
            extra_headers.append(uln)
        for hline in [*header_candidates, *extra_headers]:
            m_mcp_paren = re.search(
                r"(?:^|[○●❯>\s]|Tool\s*:\s*)([A-Za-z0-9_ -]+?)\s*\(\s*MCP\s*:\s*([A-Za-z0-9_.-]+)\s*\)",
                hline,
                re.I,
            )
            if m_mcp_paren:
                tool = tool or m_mcp_paren.group(1).strip()
                server = server or m_mcp_paren.group(2).strip()
                break
            m_server_kv = re.match(r"^(?:MCP\s+)?Server\s*[:=]\s*[\"']?([A-Za-z0-9_.-]+)[\"']?\s*$", hline, re.I)
            if m_server_kv and not server:
                server = m_server_kv.group(1).strip()
                continue
            m_tool_kv = re.match(
                r"^Tool(?:\s+name)?\s*[:=]\s*[\"']?([A-Za-z0-9_:/ -]+?)[\"']?(?:\s*\(\s*(?:MCP\s*:\s*)?([A-Za-z0-9_.-]+)\s*\))?\s*$",
                hline,
                re.I,
            )
            if m_tool_kv:
                tool = tool or m_tool_kv.group(1).strip()
                if m_tool_kv.group(2) and not server:
                    server = m_tool_kv.group(2).strip()
                continue
            m_pair = re.match(
                r"^(linear|ado-edge|ado|github-mcp-server|github)\s*(?:/|:|\s+-\s+)\s*([A-Za-z0-9_ -]+?)(?:\s*\(MCP\))?\s*$",
                hline,
                re.I,
            )
            if m_pair:
                server = server or m_pair.group(1).strip()
                tool = tool or m_pair.group(2).strip()
                break

    server_norm, tool_norm = _normalize_mcp_server_and_tool(server, tool)
    has_remember_option = bool(
        re.search(
            r"(?m)^\s*(?:[>❯●◉▶]\s*)?2[.)]\s*(?:Yes,\s*and\s+(?:don't\s+ask\s+again|remember)|Always\s+allow)\b",
            post_multiline,
            re.I,
        )
    )
    return {
        "ambiguous": ambiguous,
        "mcp_server": server_norm or server,
        "mcp_tool": tool or tool_norm,
        "mcp_tool_norm": tool_norm,
        "has_remember_option": has_remember_option,
        "signature_text": f"mcp:{server_norm}:{tool_norm}:{post_joined}",
    }

def _extract_active_bottom_frame(visible_text: str) -> str:
    """Slice only the bottom-most active interactive frame after the last output/completion boundary."""
    lines = [ln.rstrip() for ln in _text(visible_text).splitlines() if ln.strip()]
    if not lines:
        return ""
    tail = lines[-20:]
    last_boundary_idx = -1
    for idx, raw in enumerate(tail):
        stripped = raw.strip()
        if _COMPLETION_BOUNDARY_RE.search(stripped):
            last_boundary_idx = idx
    if last_boundary_idx >= 0 and last_boundary_idx + 1 < len(tail):
        tail = tail[last_boundary_idx + 1 :]
    # Also if there are multiple prompt headers separated by non-choice output, slice from the last header/question
    header_indices = [
        idx for idx, raw in enumerate(tail)
        if _PROMPT_HEADER_RE.search(raw) or (raw.strip().endswith("?") and not re.match(r"^(?:[>❯●◉▶]|\d+[.)])\s+", raw.strip()))
    ]
    if len(header_indices) > 1:
        # Check if there are multiple distinct prompt headers -> if separated or conflicting, return full tail so ambiguity check rejects it
        pass
    return "\n".join(tail)

def _validate_apply_receipt_for_target(
    plan: dict[str, Any],
    apply_receipt: dict[str, Any] | None,
    *,
    target_ref: str = "",
    pane_id: str = "",
) -> bool:
    if not isinstance(apply_receipt, dict):
        return False
    if apply_receipt.get("ok") is not True or apply_receipt.get("approved") is not True:
        return False
    plan_id = _text(plan.get("plan_id"))
    receipt_plan_id = _text(apply_receipt.get("plan_id"))
    if not plan_id or receipt_plan_id != plan_id:
        return False
    receipts = apply_receipt.get("receipts")
    if not isinstance(receipts, list) or not receipts:
        return False
    for rec in receipts:
        if not isinstance(rec, dict) or rec.get("ok") is not True:
            continue
        method = _text(rec.get("method"))
        if method == "stdout":
            continue
        rec_target = _text(rec.get("target"))
        rec_pane = _text(
            rec.get("pane_id")
            or ((rec.get("receipt") or {}).get("pane_id") if isinstance(rec.get("receipt"), dict) else "")
        )
        if target_ref and rec_target and target_ref == rec_target:
            return True
        if pane_id and rec_pane and pane_id == rec_pane:
            return True
    return False
