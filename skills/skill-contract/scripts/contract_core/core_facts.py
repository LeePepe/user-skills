from __future__ import annotations

from .core_common import *
from .core_eval import *

def _diff_facts(repo_root: Path) -> list[str]:
    issues: list[str] = []
    for label, command in (("working", ["git", "diff", "--check"]), ("cached", ["git", "diff", "--cached", "--check"])):
        try:
            proc = subprocess.run(command, cwd=repo_root, capture_output=True, text=True, check=False, timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            issues.append(f"{label}: {exc}")
            continue
        for line in (proc.stdout + "\n" + proc.stderr).splitlines():
            if line.strip():
                issues.append(f"{label}: {line.strip()}")
    try:
        diff = subprocess.run(["git", "diff", "--no-ext-diff", "--unified=0"], cwd=repo_root, capture_output=True, text=True, check=False, timeout=5)
        for line in diff.stdout.splitlines():
            if re.match(r"^[+ -]?(?:<<<<<<<|=======|>>>>>>>)", line):
                issues.append(f"conflict-marker: {line.strip()}")
    except (OSError, subprocess.SubprocessError) as exc:
        issues.append(f"working: {exc}")
    return issues


def _json_inspection(value: Any, source: str) -> dict[str, Any]:
    top = value.keys() if isinstance(value, dict) else range(len(value)) if isinstance(value, list) else []
    counts = {"dicts": 0, "lists": 0}
    def walk(item: Any) -> None:
        if isinstance(item, dict):
            counts["dicts"] += 1
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            counts["lists"] += 1
            for child in item:
                walk(child)
    walk(value)
    preview = json.dumps(value, ensure_ascii=False, indent=2)
    if len(preview) > 4096:
        preview = preview[:4093] + "..."
    return {
        "source": source,
        "top_level_type": type(value).__name__,
        "top_level_keys": [str(key) for key in top],
        "counts": counts,
        "preview": preview,
    }


def _legacy_monolith_facts(repo_root: Path) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for package in _discover_packages(repo=repo_root):
        for finding in _script_budget_findings(package):
            if finding["code"] == "script-monolith-growth":
                facts.append({key: finding[key] for key in ("script", "lines", "cap")})
        for _script, relative, lines in _script_records(package):
            if relative in LEGACY_SCRIPT_LINE_CAPS and not any(item["script"] == relative for item in facts):
                facts.append({"script": relative, "lines": lines, "cap": LEGACY_SCRIPT_LINE_CAPS[relative]})
    return sorted(facts, key=lambda item: item["script"])


def hygiene_facts(args: argparse.Namespace) -> int:
    repo_root = Path(getattr(args, "repo", None) or ROOT).expanduser().resolve()
    diff_issues = _diff_facts(repo_root) if getattr(args, "check_diff", True) else []
    json_summary: dict[str, Any] | None = None
    json_error: str | None = None
    json_file = getattr(args, "json_file", None)
    if json_file:
        try:
            raw = sys.stdin.read() if json_file == "-" else Path(json_file).expanduser().read_text(encoding="utf-8")
            json_summary = _json_inspection(json.loads(raw), "stdin" if json_file == "-" else str(Path(json_file).expanduser().resolve()))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            json_error = f"json-file: {exc}"
    problems = list(diff_issues)
    if json_error:
        problems.append(json_error)
    payload = {
        "ok": not problems,
        "schema": "skill-hygiene-facts/v1",
        "gate_tier": "soft_advisory_exit_0",
        "diff_clean": not diff_issues,
        "diff_issues": diff_issues,
        "json_summary": json_summary,
        "legacy_monoliths": _legacy_monolith_facts(repo_root),
        "advisories": _repo_advisories(repo_root),
        "reference": VALVE_REFERENCE_BASE,
    }
    _emit_json(payload, args)
    if problems and getattr(args, "strict", False):
        sys.stderr.write("skill-contract: hygiene facts found an issue; inspect the reported facts.\n")
        sys.stderr.write(f"Reference: {VALVE_REFERENCE_BASE}\n")
        return 1
    return 0


def sink_rule(args: argparse.Namespace) -> int:
    target = ROOT / "skills" / args.skill
    if not (target / "SKILL.md").is_file():
        raise SystemExit(f"skill not found: {args.skill}")
    if args.kind == "mental-model":
        path = _target_path(target, args.target_file, "SKILL.md")
        text = path.read_text(encoding="utf-8")
        replacement = args.replace_row
        if args.section:
            start = text.find(args.section)
            if start < 0:
                raise SystemExit(f"section not found: {args.section}")
            end = text.find("\n## ", start + len(args.section))
            end = len(text) if end < 0 else end
            if replacement is None:
                candidates = re.findall(r"(?m)^(?:\|[^\n]+\||\s*[-*]\s+[^\n]+)$", text[start:end])
                replacement = candidates[-1] if candidates else None
            if replacement is None or replacement not in text[start:end]:
                raise SystemExit("replace-row is outside requested section")
        if not replacement or replacement not in text:
            raise SystemExit(f"row not found in {path}")
        path.write_text(text.replace(replacement, args.value, 1), encoding="utf-8")
    elif args.kind == "case-card":
        path = _defect_drawer_path(target, args.target_file)
        if any(PRIVATE.search(value or "") for value in (args.regex, args.title, args.remediation, args.bad_snippet, args.good_snippet)):
            raise SystemExit("project-private identifiers are forbidden in case cards")
        doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"active_cases": []}
        entries = doc.setdefault("active_cases", doc.setdefault("cases", []))
        numbers = [int(m.group(1)) for item in entries if (m := re.fullmatch(r"DC-(\d+)", str(item.get("id", ""))))]
        ident = args.id if args.id and args.id != "auto" else f"DC-{(max(numbers) if numbers else 0) + 1:03d}"
        entry: dict[str, Any] = {
            "id": ident,
            "platforms": _csv(args.platforms),
            "dimensions": _csv(args.dimensions),
            "containers": _csv(args.containers),
            "title": args.title,
            "severity": args.severity,
            "regex": args.regex,
            "remediation": args.remediation,
        }
        if args.bad_snippet is not None:
            entry["bad_snippet"] = args.bad_snippet
        if args.good_snippet is not None:
            entry["good_snippet"] = args.good_snippet
        if args.regex and (args.bad_snippet is not None or args.good_snippet is not None):
            reasons = evaluate_case(entry, target, ROOT)
            if reasons:
                raise SystemExit("; ".join(reasons))
        entries.append(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        path = _target_path(target, args.target_file, "references/regex-gates.json")
        if any(PRIVATE.search(value or "") for value in (args.regex, args.title, args.remediation)):
            raise SystemExit("project-private identifiers are forbidden in regex gates")
        doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"rules": []}
        rule = {"id": args.id or f"RG-{len(doc['rules']) + 1:03d}", "title": args.title, "regex": args.regex, "remediation": args.remediation, "target_file": args.target_file or "shared-ci/scripts/context/design_contract.py"}
        doc.setdefault("rules", []).append(rule)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _emit_json({"ok": True, "skill": args.skill, "kind": args.kind}, args)
    return 0


def _audit_drawer(skill: str, path: Path, findings: list[dict[str, Any]], pkg_dir: Path | None = None) -> None:
    try:
        rel = str(path.relative_to(ROOT / "skills"))
    except ValueError:
        base = pkg_dir.parent if pkg_dir is not None else path.parents[1]
        try:
            rel = str(path.relative_to(base))
        except ValueError:
            rel = path.name
    for suffix, (key, required) in DRAWERS.items():
        if not rel.endswith(suffix):
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            findings.append({"skill": skill, "code": "invalid-drawer", "file": rel})
            return
        entries = doc.get(key, [])
        if not isinstance(entries, list):
            findings.append({"skill": skill, "code": "drawer-not-list", "file": rel})
            return
        if len(entries) > 15:
            findings.append({"skill": skill, "code": "top-3-disclosure-cap", "file": rel, "value": len(entries), "cap": 15})
        for entry in entries:
            if not isinstance(entry, dict):
                findings.append({"skill": skill, "code": "invalid-case", "file": rel})
                continue
            if required is None:
                has_any = any(isinstance(entry.get(k), list) and len(entry.get(k)) > 0 for k in COORDINATE_KEYS)
                if not has_any:
                    findings.append({"skill": skill, "code": "missing-coordinate", "file": rel, "id": entry.get("id"), "fields": list(COORDINATE_KEYS)})
            else:
                missing = [field for field in required if field not in entry]
                if missing:
                    findings.append({"skill": skill, "code": "missing-coordinate", "file": rel, "id": entry.get("id"), "fields": missing})
            if any(k in entry for k in ("verify_lines", "verify_count", "assertions")):
                scored = score_case_assertions(entry, completed=bool(entry.get("completed", True)))
                for viol in scored["discipline_violations"]:
                    code_name = "soft-assertion-missing-evidence" if "soft-assertion-missing-evidence" in viol else "assertion-count-mismatch"
                    findings.append({"skill": skill, "code": code_name, "file": rel, "id": entry.get("id"), "detail": viol})


def _frontmatter_description(md: Path) -> str:
    value = _frontmatter_fields(md).get("description", "")
    return value if isinstance(value, str) else ""


def _frontmatter_errors(md: Path) -> list[str]:
    """Validate strict YAML 1.2 frontmatter scalar syntax without external dependencies."""
    try:
        lines = md.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [f"unreadable SKILL.md: {exc}"]
    if not lines or lines[0].strip() != "---":
        return ["missing opening '---' YAML frontmatter delimiter"]
    closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if closing is None:
        return ["missing closing '---' YAML frontmatter delimiter"]
    errors: list[str] = []
    index = 1
    while index < closing:
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        match = re.match(r"^[ \t]*([A-Za-z0-9_-]+):(?:[ \t]*(.*))?$", line)
        if not match:
            if line[0].isspace() and stripped.startswith("-"):
                index += 1
                continue
            errors.append(f"line {index + 1}: invalid YAML frontmatter entry")
            index += 1
            continue
        key, raw = match.group(1), (match.group(2) or "").strip()
        if re.fullmatch(r"[|>][+-]?\d*", raw):
            index += 1
            while index < closing and (not lines[index].strip() or lines[index][0].isspace()):
                index += 1
            continue
        if raw.startswith('"'):
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, str):
                    errors.append(f"line {index + 1} ({key}): malformed double-quoted YAML scalar")
            except json.JSONDecodeError:
                errors.append(f"line {index + 1} ({key}): invalid double-quoted YAML scalar")
        elif raw.startswith("'"):
            if len(raw) < 2 or not raw.endswith("'") or "'" in raw[1:-1].replace("''", ""):
                errors.append(f"line {index + 1} ({key}): invalid single-quoted YAML scalar")
        elif raw and not raw.startswith(("[", "{")):
            if ": " in raw or ":\t" in raw or raw.endswith(":"):
                errors.append(f"line {index + 1} ({key}): unquoted ':' in YAML scalar; wrap value in double quotes")
            elif " #" in raw or "\t#" in raw:
                errors.append(f"line {index + 1} ({key}): unquoted ' #' starts a YAML comment; wrap value in double quotes")
            elif raw[0] in "*&!%@`?,:":
                errors.append(f"line {index + 1} ({key}): unquoted leading YAML indicator '{raw[0]}'; wrap value in double quotes")
        index += 1
    return errors


def _thin_wrapper(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return len(text.splitlines()) <= 25 and ("_CANONICAL_PATH" in text or "CANONICAL" in text)


LEGACY_SCRIPT_LINE_CAPS: dict[str, int] = {
    "skills/team-lead/scripts/herdr-dispatch.sh": 1269,
    "skills/m2-dispatcher/scripts/skill-slash-expander.ts": 1058,
    "skills/asset-screener/scripts/asset_evaluator.py": 1850,
    "skills/code-review/scripts/discover_review_context.py": 1335,
    "skills/codebase-design/scripts/spec_code_drift_patrol.py": 1342,
    "skills/design-studio/scripts/design_catalog.py": 2330,
    "skills/design-studio/scripts/test_design_catalog.py": 1815,
    "skills/linear-board/scripts/backlog_groomer.py": 1521,
    "skills/linear-board/scripts/daily-brief.py": 1430,
    "skills/linear-board/scripts/linear-bot-bridge.py": 1130,
    "skills/my-designer/scripts/design_evidence.py": 1323,
    "skills/repo-kit/scripts/repo_gate_auditor.py": 1192,
    "skills/retro-loop/scripts/multica_skills_autopilot.py": 1634,
    "skills/retro-loop/scripts/retro_story_engine.py": 1225,
    "skills/team-lead/scripts/autopilot_watchdog.py": 1795,
    "skills/team-lead/scripts/feedback_guard.py": 1395,
    "skills/team-lead/scripts/goal-queue.py": 2078,
    "skills/team-lead/scripts/goal-supervisor.py": 1604,
    "skills/team-lead/scripts/icloud_skills_sync.py": 1586,
    "skills/team-lead/scripts/local_hygiene_patrol.py": 1842,
    "skills/team-lead/scripts/multica_loop_runner.py": 2422,
    "skills/team-lead/scripts/multica_vpn_bridge.py": 1171,
}

def _script_records(path: Path) -> list[tuple[Path, str, int]]:
    scripts = path / "scripts"
    if not scripts.is_dir():
        return []
    root_kind = "skills" if path.parent.name == "skills" else ".agents/skills"
    records: list[tuple[Path, str, int]] = []
    for script in scripts.rglob("*"):
        if script.is_symlink() or not script.is_file() or "__pycache__" in script.parts or script.suffix not in SCRIPT_SUFFIXES:
            continue
        try:
            lines = len(script.read_text(encoding="utf-8", errors="replace").splitlines())
            relative = f"{root_kind}/{path.name}/scripts/{script.relative_to(scripts).as_posix()}"
        except (OSError, ValueError):
            continue
        records.append((script, relative, lines))
    return records


def _script_budget_findings(path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for _script, relative, lines in _script_records(path):
        cap = LEGACY_SCRIPT_LINE_CAPS.get(relative)
        if cap is not None:
            if lines > cap:
                findings.append({"skill": path.name, "code": "script-monolith-growth", "script": relative, "lines": lines, "cap": cap})
        elif lines > 1000:
            findings.append({"skill": path.name, "code": "script-lines", "script": relative, "lines": lines, "cap": 1000})
    return findings


def _advisory(skill: str, code: str, file: str, **values: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"skill": skill, "code": code, "file": file, "tier": "soft_advisory_exit_0"}
    item.update(values)
    return _enrich_finding(item)


def _package_advisories(path: Path) -> list[dict[str, Any]]:
    skill = path.name
    advisories: list[dict[str, Any]] = []
    md = path / "SKILL.md"
    md_rel = f"{skill}/SKILL.md"
    if md.is_file():
        size = md.stat().st_size
        if 12 * 1024 < size <= 16 * 1024 and md_rel not in BUDGET_EXEMPTIONS:
            advisories.append(_advisory(skill, "l0-bytes-soft", md_rel, bytes=size, threshold=12 * 1024, cap=16 * 1024))
    refs = path / "references"
    if refs.is_dir():
        for ref in sorted(refs.iterdir()):
            if not ref.is_file() or ref.is_symlink():
                continue
            size = ref.stat().st_size
            ref_rel = f"{skill}/references/{ref.name}"
            if ref.suffix == ".md" and 12 * 1024 < size <= 16 * 1024 and ref_rel not in BUDGET_EXEMPTIONS:
                advisories.append(_advisory(skill, "l1-bytes-soft", ref_rel, bytes=size, threshold=12 * 1024, cap=16 * 1024))
            elif ref.suffix in {".json", ".yaml", ".yml"} and size > 16 * 1024:
                advisories.append(_advisory(skill, "data-drawer-bytes-soft", ref_rel, bytes=size, threshold=16 * 1024, hint="Script-Only Data Drawer: query via L2 CLI, never full-file view."))
    for _script, relative, lines in _script_records(path):
        cap = LEGACY_SCRIPT_LINE_CAPS.get(relative)
        if cap is not None:
            if lines <= 800:
                advisories.append(_advisory(skill, "stale-legacy-cap", relative, lines=lines, threshold=800, cap=cap))
        elif 800 < lines <= 1000:
            advisories.append(_advisory(skill, "script-lines-soft", relative, lines=lines, threshold=800, cap=1000))
    return advisories


def audit_package_advisories(path: Path, design: bool = False, repo_bound: bool = False, sibling_corpus: str = "") -> list[dict[str, Any]]:
    return _package_advisories(path)


DESIGN_THEME_CALL_RE = re.compile(r"\.designTheme\s*\(")
ENV_THEME_INIT_RE = re.compile(r"\.environment\s*\(\s*\\\.theme\s*,\s*Theme\s*\(")
ISDARK_LABEL_RE = re.compile(r"\bisDark\s*:")
APP_SWIFT_ROOTS = ("Apps", "Sources", "Packages")
APP_SWIFT_IGNORE_PARTS = {
    "Tests",
    "UITests",
    "TestSupport",
    ".build",
    "DerivedData",
    "Carthage",
    "Pods",
    "node_modules",
}


SWIFT_OP_HEAD_CHARS = frozenset("+-*%<>&|^~!=?.")
SWIFT_REGEX_KEYWORDS = frozenset(
    {
        "return",
        "case",
        "in",
        "where",
        "throw",
        "try",
        "await",
        "if",
        "guard",
        "while",
        "switch",
        "for",
        "as",
        "is",
        "else",
        "repeat",
        "consume",
        "copy",
    }
)


def _scan_swift_regex_end(text: str, start: int, hashes: int) -> int | None:
    """Return index after closing `/#...` if `start` opens a valid Swift regex literal, else None."""
    n = len(text)
    slash_idx = start + hashes
    if slash_idx >= n or text[slash_idx] != "/":
        return None
    closing = "/" + ("#" * hashes)
    multiline = hashes > 0 and slash_idx + 1 < n and text[slash_idx + 1] in {" ", "\t", "\n", "\r"}
    if hashes == 0:
        # SE-0354: bare `/.../` cannot start or end with space/tab, cannot span lines, and cannot be `//` or `/*`.
        if slash_idx + 1 >= n or text[slash_idx + 1] in {"/", "*", " ", "\t", "\n", "\r"}:
            return None
    i = slash_idx + 1
    class_depth = 0
    prev_escaped = False
    while i < n:
        ch = text[i]
        if ch in {"\n", "\r"} and not multiline:
            return None
        if ch == "\\" and i + 1 < n:
            prev_escaped = True
            i += 2
            continue
        if ch == "[":
            class_depth += 1
            prev_escaped = False
            i += 1
            continue
        if ch == "]" and class_depth > 0:
            class_depth -= 1
            prev_escaped = False
            i += 1
            continue
        if class_depth == 0 and text.startswith(closing, i):
            if hashes == 0 and i > slash_idx + 1 and text[i - 1] in {" ", "\t"} and not prev_escaped:
                return None
            after_idx = i + len(closing)
            if hashes == 0 and after_idx < n and (text[after_idx].isalnum() or text[after_idx] == "_"):
                return None
            return after_idx
        prev_escaped = False
        i += 1
    return None


def _scan_swift_code(text: str, i: int, out: list[str], *, stop_at_close_paren: bool = False) -> int:
    """Scan Swift code from index `i`, masking comments, strings, and regex literals while preserving offsets."""
    n = len(text)
    paren_depth = 1 if stop_at_close_paren else 0
    prev_sig = ""
    prev_word = ""
    prev_was_space = True
    while i < n:
        ch = text[i]
        if ch in {" ", "\t", "\n", "\r"}:
            out.append(ch)
            prev_was_space = True
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            out.append(word)
            prev_sig = word[-1]
            prev_word = word
            prev_was_space = False
            i = j
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            prev_was_space = True
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            depth = 1
            out.extend((" ", " "))
            i += 2
            while i < n and depth > 0:
                if text[i] == "/" and i + 1 < n and text[i + 1] == "*":
                    depth += 1
                    out.extend((" ", " "))
                    i += 2
                elif text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                    depth -= 1
                    out.extend((" ", " "))
                    i += 2
                else:
                    out.append("\n" if text[i] == "\n" else " ")
                    i += 1
            prev_was_space = True
            continue
        hashes = 0
        if ch == "#":
            j = i
            while j < n and text[j] == "#":
                j += 1
            if j < n and text[j] == "/":
                regex_end = _scan_swift_regex_end(text, i, j - i)
                if regex_end is not None:
                    for k in range(i, regex_end):
                        out.append("\n" if text[k] == "\n" else " ")
                    i = regex_end
                    prev_sig = "/"
                    prev_word = ""
                    prev_was_space = False
                    continue
            if j < n and text[j] == '"':
                hashes = j - i
        elif ch == "/":
            # SE-0354: bare `/.../` cannot have whitespace immediately after `/`, so `a / b` is never a bare regex.
            # Unspaced `a/b/c` is division when preceded without whitespace by an expression-ending token.
            can_start_bare_regex = (
                prev_was_space
                or prev_sig in {"", "(", "[", "{", ",", ":", ";"}
                or prev_sig in SWIFT_OP_HEAD_CHARS
                or prev_word in SWIFT_REGEX_KEYWORDS
            )
            if can_start_bare_regex:
                regex_end = _scan_swift_regex_end(text, i, 0)
                if regex_end is not None:
                    for k in range(i, regex_end):
                        out.append("\n" if text[k] == "\n" else " ")
                    i = regex_end
                    prev_sig = "/"
                    prev_word = ""
                    prev_was_space = False
                    continue
        if ch == '"' or hashes > 0:
            q_start = i + hashes
            is_multiline = text.startswith('"""', q_start)
            delim_quotes = '"""' if is_multiline else '"'
            closing = delim_quotes + ("#" * hashes)
            escape_seq = "\\" + ("#" * hashes)
            interp_seq = escape_seq + "("
            prefix_len = hashes + len(delim_quotes)
            out.extend(" " for _ in range(prefix_len))
            i = q_start + len(delim_quotes)
            while i < n:
                if text.startswith(closing, i):
                    out.extend(" " for _ in range(len(closing)))
                    i += len(closing)
                    break
                if text.startswith(interp_seq, i):
                    out.extend(" " for _ in range(len(interp_seq)))
                    i = _scan_swift_code(text, i + len(interp_seq), out, stop_at_close_paren=True)
                    continue
                if text.startswith(escape_seq, i) and i + len(escape_seq) < n:
                    step = len(escape_seq) + 1
                    for k in range(step):
                        out.append("\n" if text[i + k] == "\n" else " ")
                    i += step
                    continue
                if not is_multiline and hashes == 0 and text[i] == "\n":
                    out.append("\n")
                    i += 1
                    break
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            prev_sig = '"'
            prev_word = ""
            prev_was_space = False
            continue
        if stop_at_close_paren:
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth -= 1
                if paren_depth == 0:
                    out.append(" ")
                    return i + 1
        out.append(ch)
        prev_sig = ch
        prev_word = ""
        prev_was_space = False
        i += 1
    return i


def _mask_swift_non_code(text: str) -> str:
    """Mask Swift line comments, nested block comments, and single/multiline/raw string literals."""
    out: list[str] = []
    _scan_swift_code(text, 0, out, stop_at_close_paren=False)
    return "".join(out)


def _custom_theme_plumbing_spans(masked: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = [(m.start(), m.end()) for m in DESIGN_THEME_CALL_RE.finditer(masked)]
    n = len(masked)
    for match in ENV_THEME_INIT_RE.finditer(masked):
        depth = 1
        cursor = match.end()
        while cursor < n and depth > 0:
            if masked[cursor] == "(":
                depth += 1
            elif masked[cursor] == ")":
                depth -= 1
            cursor += 1
        theme_args = masked[match.end() : max(match.end(), cursor - 1)]
        if ISDARK_LABEL_RE.search(theme_args):
            env_end = cursor
            while env_end < n and masked[env_end].isspace():
                env_end += 1
            if env_end < n and masked[env_end] == ")":
                env_end += 1
            spans.append((match.start(), env_end))
    return sorted(spans, key=lambda item: (item[0], item[1]))


def check_repo_appearance_hygiene(repo_root: str | Path) -> list[dict[str, Any]]:
    """Scan non-test Apple Swift sources under Apps/, Sources/, and Packages/ for custom theme plumbing."""
    base = Path(repo_root).expanduser().resolve()
    if not base.is_dir():
        return []
    findings: list[dict[str, Any]] = []
    seen_files: set[Path] = set()
    for folder in APP_SWIFT_ROOTS:
        scan_root = base / folder
        if not scan_root.is_dir():
            continue
        for swift_path in sorted(scan_root.rglob("*.swift")):
            if not swift_path.is_file():
                continue
            resolved = swift_path.resolve()
            if resolved in seen_files:
                continue
            try:
                rel_parts = swift_path.relative_to(base).parts
            except ValueError:
                rel_parts = swift_path.parts
            if any(part in APP_SWIFT_IGNORE_PARTS or part.endswith("Tests") for part in rel_parts[:-1]):
                continue
            seen_files.add(resolved)
            try:
                raw_text = swift_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = raw_text.splitlines()
            masked = _mask_swift_non_code(raw_text)
            rel_file = swift_path.relative_to(base).as_posix() if swift_path.is_relative_to(base) else str(swift_path)
            for start, end in _custom_theme_plumbing_spans(masked):
                start_line = masked.count("\n", 0, start) + 1
                end_line = masked.count("\n", 0, max(start, end - 1)) + 1
                spanned_raw = "\n".join(lines[start_line - 1 : end_line])
                if "design-contract-allow" in spanned_raw:
                    continue
                findings.append(
                    _enrich_finding(
                        {
                            "code": "custom-theme-plumbing",
                            "file": rel_file,
                            "line": start_line,
                            "match": raw_text[start:end].replace("\n", " ").strip()[:200],
                            "guidance_drawer": "skills/my-designer/references/appearance-ledger.md",
                        }
                    )
                )
    return findings


def _repo_advisories(repo_root: Path) -> list[dict[str, Any]]:
    advisories: list[dict[str, Any]] = []
    for package in _discover_packages(repo=repo_root):
        advisories.extend(_package_advisories(package))
    return advisories


def audit_package(path: Path, design: bool = False, repo_bound: bool = False, sibling_corpus: str = "") -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = _script_budget_findings(path)
    skill = path.name
    md = path / "SKILL.md"
    if not md.exists():
        return [_enrich_finding({"skill": skill, "code": "missing-skill"})]
    text = md.read_text(encoding="utf-8")
    for err in _frontmatter_errors(md):
        findings.append({"skill": skill, "code": "invalid-frontmatter", "file": str(md), "detail": err})
    desc = _frontmatter_description(md)
    if not desc or len(desc) > 320:
        findings.append({"skill": skill, "code": "description-length", "value": len(desc), "cap": 320})
    manifest = path / ".claude-plugin" / "plugin.json"
    require_manifest = not repo_bound and (
        path.parent.resolve() == (ROOT / "skills").resolve()
        or (
            path.parent.is_dir()
            and any(
                sib != path and sib.is_dir() and (sib / ".claude-plugin" / "plugin.json").is_file()
                for sib in path.parent.iterdir()
            )
        )
    )
    if manifest.is_file():
        try:
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_desc = manifest_data.get("description") if isinstance(manifest_data, dict) else None
            if not isinstance(manifest_desc, str) or not manifest_desc or len(manifest_desc) > 320:
                findings.append({"skill": skill, "code": "description-length", "value": len(manifest_desc) if isinstance(manifest_desc, str) else 0, "cap": 320, "file": str(manifest)})
            manifest_ver = manifest_data.get("version") if isinstance(manifest_data, dict) else None
            if not isinstance(manifest_ver, str) or not SEMVER_RE.fullmatch(manifest_ver.strip()):
                findings.append({"skill": skill, "code": "missing-version", "file": str(manifest)})
        except (OSError, UnicodeError, json.JSONDecodeError):
            findings.append({"skill": skill, "code": "invalid-manifest", "file": str(manifest)})
    elif require_manifest:
        findings.append({"skill": skill, "code": "missing-version", "file": str(manifest)})
    refs = path / "references"
    ref_docs = [p for p in refs.glob("*.md") if p.is_file() and not p.is_symlink()] if refs.is_dir() else []
    def safe_text(file: Path) -> str:
        try:
            return file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ""
    all_md = text + "\n" + "\n".join(safe_text(p) for p in ref_docs)
    if not repo_bound and PRIVATE.search(all_md):
        findings.append({"skill": skill, "code": "private-identifier"})
    raw_regex_lines = [line for line in all_md.splitlines() if "forbidden_regex" in line or "regex-gate" in line]
    if len(raw_regex_lines) > 1:
        findings.append({"skill": skill, "code": "raw-regex-gates-in-markdown"})
    md_rel = f"{skill}/SKILL.md"
    if md_rel not in BUDGET_EXEMPTIONS and len(text.splitlines()) > (80 if (design or skill in {"my-designer", "design-studio"}) else 180):
        findings.append({"skill": skill, "code": "l0-lines"})
    if md_rel not in BUDGET_EXEMPTIONS and md.stat().st_size > 16 * 1024:
        findings.append({"skill": skill, "code": "l0-bytes"})
    for base in (refs, path / "scripts"):
        if base.is_dir():
            for link in base.rglob("*"):
                if link.is_symlink():
                    try:
                        target = os.readlink(link)
                        if target.startswith("/") or not link.exists():
                            findings.append({"skill": skill, "code": "broken-symlink", "file": str(link)})
                    except OSError:
                        findings.append({"skill": skill, "code": "broken-symlink", "file": str(link)})
    corpus = all_md + "\n" + sibling_corpus
    if (path / "scripts").is_dir():
        corpus += "\n" + "\n".join(safe_text(p) for p in (path / "scripts").glob("*.py") if p.is_file())
    for ref in (refs.glob("*") if refs.is_dir() else []):
        if ref.is_symlink() and not ref.exists():
            continue
        if ref.is_dir() or not ref.is_file() or ref.suffix not in REFERENCE_SUFFIXES:
            continue
        ref_rel = f"{skill}/references/{ref.name}"
        ref_text = safe_text(ref)
        if ref.suffix == ".md" and ref_rel not in BUDGET_EXEMPTIONS and len(ref_text.splitlines()) > 160:
            findings.append({"skill": skill, "code": "l1-lines", "file": str(ref)})
        if ref.suffix == ".md" and ref_rel not in BUDGET_EXEMPTIONS and ref.stat().st_size > 16 * 1024:
            findings.append({"skill": skill, "code": "l1-bytes", "file": str(ref)})
        if ref.suffix == ".md" and (repo_bound or skill in {"skill-contract", "my-designer", "design-studio", "repo-kit", "nas-smart-home", "knowledge-digest", "codebase-design"}):
            for block in re.findall(r"```[^\n]*\n(.*?)```", ref_text, re.DOTALL):
                if len(block.splitlines()) > 25:
                    findings.append({"skill": skill, "code": "fenced-code-lines", "file": str(ref)})
                    break
        if ref.suffix == ".json":
            _audit_drawer(skill, ref, findings, pkg_dir=path)
        local_link = re.search(rf"(?<![A-Za-z0-9_-])(?:references/)?{re.escape(ref.name)}(?![A-Za-z0-9_.-])", all_md)
        canonical_link = re.search(rf"(?:\.\./)?{re.escape(skill)}/references/{re.escape(ref.name)}", sibling_corpus)
        if not local_link and not canonical_link:
            findings.append({"skill": skill, "code": "orphan-reference", "file": str(ref)})
    data_defect = path / "scripts" / "data" / "defect-cases" / "index.json"
    if data_defect.is_file():
        _audit_drawer(skill, data_defect, findings, pkg_dir=path)
    return [_enrich_finding(item) for item in findings]


def audit(args: argparse.Namespace, discover_packages=None) -> int:
    repo_arg = _normalize_repo_path(getattr(args, "repo", None))
    skill_names = _merge_skill_names(getattr(args, "skills", None), getattr(args, "skill", None))
    if not skill_names and not getattr(args, "all", False) and not repo_arg:
        args.all = True
    args_key = {"skills": skill_names, "all": bool(getattr(args, "all", False)), "repo": str(repo_arg or "")}
    cached = _cache_read("audit", repo_arg, args_key, no_cache=bool(getattr(args, "no_cache", False)))
    if isinstance(cached, dict):
        _emit_json(cached, args)
        return 1 if not cached.get("ok") and getattr(args, "strict", False) else 0
    discover = discover_packages or _discover_packages
    if skill_names:
        selected = []
        for skill_name in skill_names:
            selected.extend(discover(skill=skill_name, repo=repo_arg))
    else:
        selected = discover(skill=None, repo=repo_arg)
    all_packages = discover(repo=repo_arg)
    selected_set = {p.resolve() for p in selected}
    findings: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
    groups: dict[Path, list[Path]] = {}
    for package in all_packages:
        groups.setdefault(package.parent.resolve(), []).append(package)
    for group in groups.values():
        group_corpus = "\n".join(
            (p / "SKILL.md").read_text(encoding="utf-8", errors="replace")
            + "\n" + "\n".join(q.read_text(encoding="utf-8", errors="replace") for q in (p / "references").glob("*.md") if q.is_file() and not q.is_symlink())
            for p in group
        )
        for package in group:
            if package.resolve() in selected_set:
                findings.extend(audit_package(package, repo_bound=bool(repo_arg), sibling_corpus=group_corpus))
                advisories.extend(audit_package_advisories(package, sibling_corpus=group_corpus))
        names: dict[str, list[str]] = {}
        for package in group:
            names.setdefault(package.name, []).append(str(package))
        findings.extend({"code": "duplicate-skill", "name": name, "paths": paths} for name, paths in names.items() if len(paths) > 1)
        scripts: dict[str, list[Path]] = {}
        for package in group:
            for script in (package / "scripts").glob("*.py") if (package / "scripts").is_dir() else []:
                if not script.is_symlink() and script.name != "__init__.py":
                    scripts.setdefault(script.name, []).append(script)
        for name, paths in scripts.items():
            real = [path for path in paths if not _thin_wrapper(path)]
            if len(real) >= 2:
                findings.append({"code": "duplicate-script-fork", "script": name, "paths": [str(path) for path in real]})
    if repo_arg and not skill_names:
        findings.extend(check_repo_appearance_hygiene(repo_arg))
    findings = [_enrich_finding(item) for item in findings]
    result: dict[str, Any] = {"ok": not findings, "skills": len(selected), "findings": findings, "advisories": advisories}
    if findings:
        result["guidance_drawer"] = VALVE_REFERENCE_BASE
        refs = sorted({str(item["reference"]) for item in findings if item.get("reference")})
        sys.stderr.write(
            f"[skill-contract] {len(findings)} valve finding(s) detected. "
            f"Read {', '.join(refs[:3])} for remediation rules.\n"
        )
    _cache_write("audit", repo_arg, args_key, result)
    _emit_json(result, args)
    return 1 if findings and args.strict else 0



__all__ = [name for name in globals() if not name.startswith("__")]
