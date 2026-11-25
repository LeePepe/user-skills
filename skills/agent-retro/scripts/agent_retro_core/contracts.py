from __future__ import annotations

from . import base as _base
globals().update({k: v for k, v in vars(_base).items() if not k.startswith("__")})

def _git_output(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _git_exists(object_name: str) -> bool:
    try:
        return subprocess.run(["git", "cat-file", "-e", object_name], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0
    except OSError:
        return False


def resolve_target_skill_watermarks(skill_names: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
    """Resolve per-skill commit/tree watermarks without inventing fixture history."""
    global _TARGET_WATERMARK_CACHE
    if skill_names is None and _TARGET_WATERMARK_CACHE is not None:
        return _TARGET_WATERMARK_CACHE
    names = sorted(set(skill_names or [p.name for p in (ROOT / "skills").iterdir() if p.is_dir()]))
    result: dict[str, dict[str, Any]] = {}
    for skill in names:
        path = ROOT / "skills" / skill
        if not path.is_dir():
            continue
        commits = _git_output(["git", "rev-list", "--count", "HEAD", "--", f"skills/{skill}"])
        tree_sha = _git_output(["git", "rev-parse", "--short", f"HEAD:skills/{skill}"])
        modified = _git_output(["git", "log", "-1", "--format=%ct", "--", f"skills/{skill}"])
        result[skill] = {"version": f"v{int(commits or 0)}@{tree_sha or 'unknown'}",
                         "commits": int(commits or 0), "tree_sha": tree_sha or "unknown",
                         "last_modified_epoch": int(modified or 0)}
    if skill_names is None:
        _TARGET_WATERMARK_CACHE = result
    return result


def _literal_strings(node: ast.AST | None) -> set[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return {str(item.value).strip() for item in node.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value.strip()}
    if isinstance(node, ast.Dict):
        return {str(item.value).strip() for item in node.keys
                if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value.strip()}
    return set()


def _placeholder_token(token: str) -> bool:
    value = token.strip("'\\\"`.,;:()[]{}")
    return not value or value in {"*", "...", "etc", "and", "or"} or bool(
        re.fullmatch(r"(?:<[^>]*>|\{[^}]*\}|\[[^]]*\]|\*+|\.{2,})", value)
    )


def _argparse_contract(script: Path) -> tuple[set[str], set[str], bool]:
    try:
        source = script.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(script))
    except (OSError, SyntaxError):
        return {"-h", "--help"}, set(), True
    if "parse_known_args" in source or "_CANONICAL" in source or re.search(r"\bexec\s*\(", source):
        return {"-h", "--help"}, set(), True
    flags: set[str] = {"-h", "--help"}
    subcommands: set[str] = set()
    top_constants: dict[str, set[str]] = {}
    for statement in tree.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id.upper() in {"SUBCOMMANDS", "CHOICES"}:
                    top_constants[target.id] = _literal_strings(statement.value)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "add_argument":
                option_args = [str(arg.value).strip() for arg in node.args
                               if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
                for value in option_args:
                    if value.startswith("-"):
                        flags.add(value.split("=", 1)[0])
                if not any(value.startswith("-") for value in option_args):
                    for keyword in node.keywords:
                        if keyword.arg == "choices":
                            subcommands.update(_literal_strings(keyword.value))
                            if isinstance(keyword.value, ast.Name):
                                subcommands.update(top_constants.get(keyword.value.id, set()))
            elif node.func.attr == "add_parser" and node.args:
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    subcommands.add(node.args[0].value.strip())
                for keyword in node.keywords:
                    if keyword.arg == "aliases":
                        subcommands.update(_literal_strings(keyword.value))
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            values = _literal_strings(node.iter)
            if values:
                for child in ast.walk(node):
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "add_parser":
                        if child.args and isinstance(child.args[0], ast.Name) and child.args[0].id == node.target.id:
                            subcommands.update(values)
    subcommands.update(top_constants.get("SUBCOMMANDS", set()))
    subcommands = {value for value in subcommands if not _placeholder_token(value)}
    return flags, subcommands, False


def _runtime_model_ids() -> set[str]:
    profile = ROOT / "skills" / "team-lead" / "references" / "runtime-profiles.yaml"
    text = _safe_text(profile, 200_000)
    ids = set(re.findall(r"(?m)^\s{2}([A-Za-z0-9][A-Za-z0-9._/-]*):", text[text.find("vendors:"):]))
    ids.update(re.findall(r"[\"'](group/[A-Za-z0-9_-]+)[\"']", text))
    return ids


def _explicit_model_ids(text: str) -> set[str]:
    candidates = set(re.findall(
        r"(?<![A-Za-z0-9_])(?:group/[A-Za-z0-9_-]+|opus|kimi|gpt-[A-Za-z0-9._-]+|gemini-[A-Za-z0-9._-]+|mai-code-[A-Za-z0-9._-]+|grok-[A-Za-z0-9._-]+|claude-[A-Za-z0-9._-]+)(?![A-Za-z0-9_])",
        text, re.I,
    ))
    candidates.update(re.findall(
        r"(?:--model(?:=|\s+)|(?:selected_)?model(?:_canon)?\s*[:=]\s*)([^\s`'\",;()\[\]{}]+)",
        text, re.I,
    ))
    ignored = {"str", "string", "model", "name", "id", "value", "none", "null", "for", "or", "and"}
    return {candidate.strip("'\\\".,") for candidate in candidates
            if candidate.strip("'\\\".,").lower() not in ignored
            and not _placeholder_token(candidate) and "\\\\" not in candidate and not re.search(r"[\\[\\]{}*]", candidate)}


def _cross_skill_contract_check(skills_root: Path | None = None) -> dict[str, Any]:
    """Static zero-token check of cross-skill calls, CLI contracts, and model IDs."""
    global _CROSS_SKILL_CACHE
    root = Path(skills_root).expanduser() if skills_root is not None else ROOT / "skills"
    cacheable = skills_root is None
    cache_key = str(root.resolve())
    if cacheable and isinstance(_CROSS_SKILL_CACHE, dict) and _CROSS_SKILL_CACHE.get("root") == cache_key:
        return _CROSS_SKILL_CACHE
    findings: list[str] = []
    checked = 0
    allowed_models = _runtime_model_ids()
    invocation = re.compile(r"skills/([A-Za-z0-9_-]+)/scripts/([A-Za-z0-9_.-]+\.py)(?P<args>[^\n`]*)")
    callers = sorted(root.glob("*/SKILL.md")) + sorted(root.glob("*/references/*.md")) + sorted(root.glob("*/scripts/*.py"))
    for caller in callers:
        caller_skill = caller.relative_to(root).parts[0]
        text = _safe_text(caller, 100_000)
        for match in invocation.finditer(text):
            callee, script_name, tail = match.group(1), match.group(2), match.group("args")
            if caller_skill == callee:
                continue
            line = text[match.start():text.find("\n", match.start()) if text.find("\n", match.start()) >= 0 else len(text)]
            if caller.suffix == ".py" and not re.search(r"(?:subprocess|os\.system|check_call|check_output|Popen|run\s*\()", line):
                continue
            tail = re.split(r"[`\"']", tail, maxsplit=1)[0]
            callee_root = root / callee
            if not callee_root.is_dir():
                continue
            script = callee_root / "scripts" / script_name
            checked += 1
            if not script.is_file():
                findings.append(f"{caller.relative_to(root)}: missing {callee}/{script_name}")
                continue
            flags, subcommands, skipped = _argparse_contract(script)
            if not skipped:
                raw_tokens = re.findall(r"(?:--[^\s`]*|-[A-Za-z][^\s`]*|[A-Za-z][A-Za-z0-9_.:/@+{}\[\]<>*-]*)", tail)
                tokens: list[str] = []
                for raw in raw_tokens:
                    token = raw.strip("'\\\"`.,;:()")
                    if token == "--":
                        break
                    tokens.append(token)
                for token in tokens:
                    if token.startswith("-") and token.split("=", 1)[0] not in flags:
                        findings.append(f"{caller.relative_to(root)}: {callee}/{script_name} unknown flag {token.split('=', 1)[0]}")
                if subcommands:
                    first = next((token for token in tokens if not token.startswith("-") and not _placeholder_token(token)), "")
                    if first and first not in subcommands and first not in {"python", "python3"}:
                        findings.append(f"{caller.relative_to(root)}: {callee}/{script_name} unknown subcommand {first}")
            for model in _explicit_model_ids(tail):
                if model not in allowed_models:
                    findings.append(f"{caller.relative_to(root)}: unknown model ID {model}")
        for model in _explicit_model_ids(text) if caller_skill == "agent-retro" else ():
            if model not in allowed_models:
                findings.append(f"{caller.relative_to(root)}: unknown model ID {model}")
    result = {"root": cache_key, "checked": checked, "findings": list(dict.fromkeys(findings))[:40], "ok": not findings}
    if cacheable:
        _CROSS_SKILL_CACHE = result
    return result


def _warehouse_signals(warehouse: Path | None) -> dict[str, Any]:
    if warehouse is None or not _external_path_safe(warehouse):
        return {"available": False, "signals": {}}
    signals: dict[str, int | float] = {}
    try:
        with closing(sqlite3.connect(f"file:{warehouse}?mode=ro", uri=True, timeout=0.2)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            columns = {table: {row[1] for row in db.execute(f"PRAGMA table_info({table})")} for table in tables}
            if "dim_goal" in tables and "is_status_drifted" in columns["dim_goal"]:
                signals["status_drifted_goals"] = int(db.execute("SELECT COUNT(*) FROM dim_goal WHERE is_status_drifted = 1").fetchone()[0])
            if "fact_gtt_event" in tables and "evt_type" in columns["fact_gtt_event"]:
                for name, evt_type in (("review_fail_events", "REVIEW_FAIL"), ("input_blocked", "INPUT_BLOCKED"), ("unblock", "UNBLOCK")):
                    signals[name] = int(db.execute("SELECT COUNT(*) FROM fact_gtt_event WHERE evt_type = ?", (evt_type,)).fetchone()[0])
            if "fact_gtt_task_run" in tables and {"round_no", "v5_verdict"}.issubset(columns["fact_gtt_task_run"]):
                signals["task_runs_total"] = int(db.execute("SELECT COUNT(*) FROM fact_gtt_task_run").fetchone()[0])
                signals["task_runs_round3_plus"] = int(db.execute("SELECT COUNT(*) FROM fact_gtt_task_run WHERE round_no >= 3").fetchone()[0])
                signals["review_fail_r3_plus"] = int(db.execute("SELECT COUNT(*) FROM fact_gtt_task_run WHERE round_no >= 3 AND UPPER(v5_verdict) = 'FAIL'").fetchone()[0])
            if "fact_request" in tables and {"model", "model_canon", "total_tokens"}.issubset(columns["fact_request"]):
                steering, total = db.execute(
                    "SELECT COALESCE(SUM(CASE WHEN lower(COALESCE(model, '')) LIKE '%group/steering%' OR lower(COALESCE(model_canon, '')) LIKE '%group/steering%' OR lower(COALESCE(model_canon, '')) IN ('opus', 'gemini-4-carbon') THEN COALESCE(total_tokens, 0) ELSE 0 END), 0), COALESCE(SUM(COALESCE(total_tokens, 0)), 0) FROM fact_request"
                ).fetchone()
                signals["steering_tokens"] = int(steering or 0)
                signals["request_tokens_total"] = int(total or 0)
                signals["steering_token_share"] = (float(steering) / float(total)) if total else 0.0
    except (OSError, sqlite3.Error):
        return {"available": False, "signals": {}}
    return {"available": True, "signals": signals}
