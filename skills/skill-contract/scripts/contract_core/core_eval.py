from __future__ import annotations

from .core_common import *

def _csv(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, (list, tuple)) else [value]
    seen: list[str] = []
    for raw in raw_items:
        for part in str(raw or "").split(","):
            token = part.strip()
            if token and token not in seen:
                seen.append(token)
    return seen


def _merge_skill_names(*values: str | list[str] | tuple[str, ...] | None) -> list[str]:
    merged: list[str] = []
    for item in values:
        for token in _csv(item):
            if token not in merged:
                merged.append(token)
    return merged


def _target_path(target: Path, value: str | None, default: str) -> Path:
    if not value:
        return target / default
    p = Path(value)
    return p if p.is_absolute() or (ROOT / p).exists() or value.startswith("skills/") else target / p


def _defect_drawer_path(target: Path, explicit: str | None = None) -> Path:
    if explicit:
        return _target_path(target, explicit, explicit)
    canonical = target / "scripts" / "data" / "defect-cases" / "index.json"
    legacy = target / "references" / "defect-cases" / "index.json"
    return canonical if canonical.exists() or not legacy.exists() else legacy


def _lookup_dot_path(data: Any, dot_path: str) -> tuple[bool, Any]:
    cur = data
    for part in dot_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False, None
    return True, cur


def _run_command(command: Any, repo_root: Path) -> subprocess.CompletedProcess[str]:
    argv: list[str] = []
    try:
        argv = list(command) if isinstance(command, list) else shlex.split(str(command))
        if not argv:
            raise ValueError("empty command")
        if argv[0] in {"python", "python3"}:
            argv[0] = sys.executable
        return subprocess.run(argv, cwd=repo_root, capture_output=True, text=True, check=False)
    except (OSError, TypeError, ValueError) as exc:
        result = subprocess.CompletedProcess(argv, 127, "", str(exc))
        result.launch_error = str(exc)
        return result


def _frontmatter_fields(md: Path) -> dict[str, Any]:
    """Parse the small YAML subset used by skill frontmatter without dependencies."""
    try:
        lines = md.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, Any] = {}
    index = 1
    while index < len(lines) and lines[index].strip() != "---":
        line = lines[index]
        match = re.match(r"^([A-Za-z0-9_-]+):(?:[ \t]*(.*))?$", line)
        if not match:
            index += 1
            continue
        key, raw = match.group(1), match.group(2) or ""
        if raw.startswith(("|", ">")):
            folded = raw.startswith(">")
            chunks: list[str] = []
            index += 1
            while index < len(lines) and lines[index].strip() != "---" and (not lines[index] or lines[index][0].isspace()):
                chunks.append(lines[index].strip())
                index += 1
            value = (" ".join(chunks) if folded else "\n".join(chunks))
            fields[key] = value.rstrip() + ("\n" if raw.endswith("+") else "")
            continue
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            if value[0] == '"':
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = value[1:-1]
            else:
                value = value[1:-1]
        fields[key] = value
        index += 1
    return fields


def _load_json_argument(value: Any) -> Any:
    if value is None:
        return None
    path = Path(str(value)).expanduser()
    try:
        raw = path.read_text(encoding="utf-8") if path.is_file() else str(value)
        return json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        raise ValueError("mock JSON must be valid inline JSON or a readable JSON file")


def _is_golden(case: dict[str, Any]) -> bool:
    return case.get("jev_starred_id") is not None or "jev-star" in (case.get("tags") or [])


def _snapshot_failures(case: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    questions = case.get("jev_questions")
    answers = case.get("mock_jev_response")
    if not isinstance(questions, (dict, list)) or not questions:
        failures.append("jev_questions must be a nonempty dict/list")
    if not isinstance(answers, dict) or not answers:
        failures.append("mock_jev_response must be a nonempty answers dict")
    if isinstance(questions, dict):
        question_ids = list(questions)
    elif isinstance(questions, list):
        question_ids = [question["id"] for question in questions if isinstance(question, dict) and "id" in question]
    else:
        question_ids = []
    if isinstance(answers, dict):
        missing = [qid for qid in question_ids if not isinstance(qid, (str, int)) or str(qid) not in answers]
        if missing:
            failures.append(f"question IDs missing from mock_jev_response: {missing}")
    try:
        if "jev_state" not in case:
            failures.append("jev_state is missing")
        json.dumps({key: case.get(key) for key in ("jev_state", "jev_questions", "mock_jev_response")}, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        failures.append("golden snapshot state/questions/answers must be JSON-compatible")
    return failures


def score_case_assertions(
    case: Mapping[str, Any],
    *,
    completed: bool = True,
) -> dict[str, Any]:
    """Score Hard vs Soft assertions under `1-Verify-1-Assertion` & fatal zeroing discipline (`expo/eval-harness` parity)."""
    cid = str(case.get("id", "<unidentified>"))
    verify_lines_raw = case.get("verify_lines")
    verify_lines = [str(v).strip() for v in verify_lines_raw if str(v).strip()] if isinstance(verify_lines_raw, list) else []
    verify_count = int(case["verify_count"]) if case.get("verify_count") is not None else len(verify_lines)
    raw_assertions = case.get("assertions")
    assertions = [dict(a) for a in raw_assertions if isinstance(a, Mapping)] if isinstance(raw_assertions, list) else []

    max_points = int(case.get("points") if case.get("points") is not None else (verify_count or len(assertions) or 1))
    discipline_violations: list[str] = []

    if (verify_lines or case.get("verify_count") is not None) and len(assertions) != verify_count:
        discipline_violations.append(
            f"{cid}: 1-Verify-1-Assertion violation (assertion-count-mismatch): recorded {len(assertions)} assertion(s) for {verify_count} Verify line(s)"
        )

    hard_assertions: list[dict[str, Any]] = []
    soft_assertions: list[dict[str, Any]] = []
    for idx, item in enumerate(assertions):
        v_line = verify_lines[idx] if idx < len(verify_lines) else str(item.get("verify") or item.get("check") or f"verify_{idx + 1}")
        non_fatal_marker = "(non-fatal)" in v_line.lower()
        sev = str(item.get("severity") or item.get("kind") or ("soft" if "evidence" in item or "why_soft" in item else "hard")).strip().lower()
        is_soft = sev in {"soft", "soft_advisory"}
        default_fatal = not (non_fatal_marker or sev == "soft_advisory")
        fatal = bool(item["fatal"]) if "fatal" in item and item["fatal"] is not None else default_fatal
        passed_val = bool(item.get("passed", True))
        if is_soft:
            ev = str(item.get("evidence") or "").strip()
            why_soft = str(item.get("why_soft") or "").strip()
            if not ev or not why_soft:
                discipline_violations.append(
                    f"{cid}: soft-assertion-missing-evidence on assertion #{idx + 1} ({v_line!r}): soft assertions require non-empty 'evidence' and 'why_soft'"
                )
            soft_assertions.append({
                "check": str(item.get("check") or v_line),
                "fatal": fatal,
                "passed": passed_val,
                "evidence": ev,
                "why_soft": why_soft,
            })
        else:
            hard_assertions.append({
                "check": str(item.get("check") or item.get("id") or v_line),
                "fatal": fatal,
                "passed": passed_val,
            })

    all_checks = hard_assertions + soft_assertions
    fatal_hard_failed = any(not a["passed"] for a in hard_assertions if a["fatal"])
    fatal_soft_failed = any(not a["passed"] for a in soft_assertions if a["fatal"])
    any_fatal_failed = fatal_hard_failed or fatal_soft_failed or bool(discipline_violations)

    if any_fatal_failed or not completed:
        earned_points = 0
        passed = False
    elif not all_checks:
        earned_points = max_points
        passed = True
    else:
        passed_count = sum(1 for a in all_checks if a["passed"])
        earned_points = round(max_points * passed_count / len(all_checks))
        passed = earned_points == max_points

    return {
        "id": cid,
        "max_points": max_points,
        "earned_points": earned_points,
        "passed": passed,
        "completed": completed,
        "verify_count": verify_count,
        "assertion_count": len(all_checks),
        "hard_assertions": len(hard_assertions),
        "soft_assertions": len(soft_assertions),
        "fatal_failed": any_fatal_failed,
        "discipline_violations": discipline_violations,
    }


def evaluate_case(case: dict[str, Any], skill_dir: Path, repo_root: Path = ROOT) -> list[str]:
    reasons: list[str] = []
    cid = str(case.get("id", "<unidentified>"))
    if _is_golden(case):
        reasons.extend(f"{cid}: {reason}" for reason in _snapshot_failures(case))
    if any(k in case for k in ("verify_lines", "verify_count", "assertions")):
        scored = score_case_assertions(case, completed=bool(case.get("completed", True)))
        reasons.extend(scored["discipline_violations"])
        if scored["fatal_failed"] and not scored["discipline_violations"]:
            reasons.append(f"{cid}: fatal assertion failed (earned_points=0/{scored['max_points']})")
    pattern_raw = case.get("forbidden_regex") or case.get("regex")
    bad_snippet = case.get("bad_snippet")
    good_snippet = case.get("good_snippet")

    if pattern_raw:
        try:
            compiled = re.compile(str(pattern_raw))
        except re.error as exc:
            reasons.append(f"{cid}: invalid regex {pattern_raw!r}: {exc}")
            compiled = None
        if compiled is not None:
            if bad_snippet is not None and compiled.search(str(bad_snippet)) is None:
                reasons.append(f"{cid}: bad_snippet did not match forbidden_regex/regex")
            if good_snippet is not None and compiled.search(str(good_snippet)) is not None:
                reasons.append(f"{cid}: good_snippet falsely matched forbidden_regex/regex")

    cmd_raw = case.get("cmd")
    if cmd_raw:
        proc = _run_command(cmd_raw, repo_root)
        expect_exit = int(case.get("expect_exit", 0))
        if proc.returncode != expect_exit:
            reasons.append(f"{cid}: cmd exit {proc.returncode} != expect_exit {expect_exit} ({proc.stderr.strip()})")
        combined_out = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
        for token in case.get("expect_stdout_contains") or []:
            if str(token) not in combined_out:
                reasons.append(f"{cid}: stdout missing expected substring {token!r}")
        json_subset = case.get("expect_json_subset")
        if isinstance(json_subset, dict) and json_subset:
            try:
                parsed_json = json.loads(proc.stdout)
            except json.JSONDecodeError as exc:
                reasons.append(f"{cid}: stdout is not valid JSON for expect_json_subset: {exc}")
            else:
                for dot_key, expected_val in json_subset.items():
                    found, actual_val = _lookup_dot_path(parsed_json, str(dot_key))
                    if not found or actual_val != expected_val:
                        reasons.append(f"{cid}: JSON {dot_key!r} expected {expected_val!r}, got {actual_val!r}")

    negative_cmd = case.get("negative_cmd")
    if "negative_cmd" in case:
        proc = _run_command(negative_cmd if negative_cmd is not None else "", repo_root)
        expected = case.get("expect_fail_exit")
        if getattr(proc, "launch_error", None):
            reasons.append(f"{cid}: negative_cmd launch failed: {proc.stderr.strip()}")
        elif proc.returncode == 0:
            reasons.append(f"{cid}: negative_cmd unexpectedly succeeded")
        elif expected is not None:
            try:
                if proc.returncode != int(expected):
                    reasons.append(f"{cid}: negative_cmd exit {proc.returncode} != expect_fail_exit {expected}")
            except (TypeError, ValueError):
                reasons.append(f"{cid}: invalid expect_fail_exit {expected!r}")
        for token in case.get("negative_stdout_contains") or []:
            if str(token) not in proc.stdout:
                reasons.append(f"{cid}: negative stdout missing expected substring {token!r}")
        for token in case.get("negative_stderr_contains") or []:
            if str(token) not in proc.stderr:
                reasons.append(f"{cid}: negative stderr missing expected substring {token!r}")

    file_ref = case.get("file")
    if file_ref:
        candidate = repo_root / str(file_ref)
        if not candidate.is_file():
            candidate = skill_dir / str(file_ref)
        if not candidate.is_file():
            reasons.append(f"{cid}: contract file not found: {file_ref}")
        else:
            file_text = candidate.read_text(encoding="utf-8", errors="replace")
            for token in case.get("must_contain") or []:
                if str(token) not in file_text:
                    reasons.append(f"{cid}: file {file_ref} missing must_contain {token!r}")
            for token in case.get("must_not_contain") or []:
                if str(token) in file_text:
                    reasons.append(f"{cid}: file {file_ref} contains forbidden must_not_contain {token!r}")

    return reasons


def _next_case_id(entries: list[dict[str, Any]], prefix: str) -> str:
    numbers = [
        int(m.group(1))
        for item in entries
        if isinstance(item, dict) and (m := re.fullmatch(rf"{re.escape(prefix)}-(\d+)", str(item.get("id", ""))))
    ]
    return f"{prefix}-{(max(numbers) if numbers else 0) + 1:03d}"


def add_case(args: argparse.Namespace) -> int:
    repo_arg = getattr(args, "repo", None)
    repo_root = Path(repo_arg).expanduser().resolve() if repo_arg else ROOT
    target = _resolve_skill_dir(args.skill, repo=repo_arg, create=False)
    if not (target / "SKILL.md").is_file():
        raise SystemExit(f"skill not found: {args.skill}")
    drawer_rel = args.drawer or "references/eval-cases.json"
    path = _target_path(target, drawer_rel, "references/eval-cases.json")
    is_defect_drawer = path.as_posix().endswith("defect-cases/index.json")
    prefix = "DC" if is_defect_drawer else "EV"

    text_fields = [
        args.title,
        args.symptom,
        args.fix_rule,
        args.forbidden_regex,
        args.bad_snippet,
        args.good_snippet,
        args.cmd,
        args.file,
        args.must_contain,
        args.must_not_contain,
        args.expect_contains,
        args.negative_cmd,
        args.negative_stdout_contains,
        args.negative_stderr_contains,
    ]
    if not repo_arg and any(PRIVATE.search(val or "") for val in text_fields):
        _emit_json({"ok": False, "error": "project-private identifiers are forbidden in cases"}, args)
        return 1

    if args.forbidden_regex and (not args.bad_snippet or not args.good_snippet):
        _emit_json({"ok": False, "error": "forbidden_regex requires both --bad-snippet and --good-snippet"}, args)
        return 1

    if not args.forbidden_regex and not args.cmd and not args.file and not args.negative_cmd:
        _emit_json({"ok": False, "error": "add-case requires at least one executable assertion (--forbidden-regex, --cmd, --negative-cmd, or --file)"}, args)
        return 1

    default_schema = "apple-design-defect-cases/v1" if is_defect_drawer else "skill-eval-cases/v1"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema": default_schema, "version": 1, "active_cases": []}
    entries = doc.setdefault("active_cases", [])
    raw_id = (args.id or "auto").strip()
    ident = _next_case_id(entries, prefix) if raw_id.lower() == "auto" else raw_id

    entry: dict[str, Any] = {"id": ident, "title": args.title}
    for coord_key, raw_val in (
        ("platforms", args.platforms),
        ("dimensions", args.dimensions),
        ("containers", args.containers),
        ("tags", args.tags),
        ("routes", args.routes),
        ("layers", args.layers),
    ):
        vals = _csv(raw_val)
        if vals:
            entry[coord_key] = vals
    if not any(k in entry for k in COORDINATE_KEYS):
        entry["tags"] = ["general"]

    if args.symptom:
        entry["symptom"] = args.symptom
    if args.fix_rule:
        entry["fix_rule"] = args.fix_rule
    if args.forbidden_regex:
        entry["forbidden_regex"] = args.forbidden_regex
    if args.bad_snippet is not None:
        entry["bad_snippet"] = args.bad_snippet
    if args.good_snippet is not None:
        entry["good_snippet"] = args.good_snippet
    if args.cmd:
        entry["cmd"] = args.cmd
        entry["expect_exit"] = args.expect_exit
    if args.expect_contains:
        entry["expect_stdout_contains"] = _csv(args.expect_contains)
    if args.expect_json_subset:
        entry["expect_json_subset"] = json.loads(args.expect_json_subset)
    if args.file:
        entry["file"] = args.file
    if args.must_contain:
        entry["must_contain"] = _csv(args.must_contain)
    if args.must_not_contain:
        entry["must_not_contain"] = _csv(args.must_not_contain)
    if args.negative_cmd:
        entry["negative_cmd"] = args.negative_cmd
        if args.expect_fail_exit is not None:
            entry["expect_fail_exit"] = args.expect_fail_exit
    if args.negative_stdout_contains:
        entry["negative_stdout_contains"] = _csv(args.negative_stdout_contains)
    if args.negative_stderr_contains:
        entry["negative_stderr_contains"] = _csv(args.negative_stderr_contains)
    entry["registered_at"] = date.today().isoformat()

    failures = evaluate_case(entry, target, repo_root)
    if failures:
        _emit_json({"ok": False, "id": ident, "failures": failures}, args)
        return 1

    entries = [item for item in entries if not (isinstance(item, dict) and item.get("id") == ident)]
    entries.append(entry)
    doc["active_cases"] = entries
    doc["updated"] = date.today().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _emit_json({"ok": True, "skill": args.skill, "drawer": drawer_rel, "id": ident, "entry": entry}, args)
    return 0


def _detect_target_skills(
    handoff_dir: Path | None,
    worktree: Path | None,
    repo_root: Path = ROOT,
) -> list[str]:
    all_pkgs = {pkg.name for pkg in _discover_packages(repo=repo_root)}
    detected: list[str] = []

    def add_skill(name: str) -> None:
        if name in all_pkgs and name not in detected:
            detected.append(name)

    corpus_parts: list[str] = []
    if handoff_dir is not None and handoff_dir.is_dir():
        if (handoff_dir / "design.md").is_file():
            add_skill("my-designer")
        for doc_name in ("spec.md", "design.md", "ledger.md", "queue.json"):
            doc_path = handoff_dir / doc_name
            if doc_path.is_file():
                try:
                    corpus_parts.append(doc_path.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    pass
    corpus = "\n".join(corpus_parts)
    for skill_name in sorted(all_pkgs):
        if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(skill_name)}(?![A-Za-z0-9_-])", corpus):
            add_skill(skill_name)
    if re.search(r"\b(?:SwiftUI|UIKit|AppKit|Liquid\s+Glass|design\.md|Visual\s+Traceback)\b", corpus, re.I):
        add_skill("my-designer")

    if worktree is not None and worktree.is_dir():
        ui_suffixes = {".swift", ".html", ".htm", ".css", ".scss", ".tsx", ".jsx"}
        try:
            git_proc = subprocess.run(
                ["git", "-C", str(worktree), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            changed_lines = git_proc.stdout.splitlines() if git_proc.returncode == 0 else []
            if any(Path(line[3:].strip()).suffix.lower() in ui_suffixes for line in changed_lines if len(line) > 3):
                add_skill("my-designer")
        except (OSError, subprocess.TimeoutExpired):
            pass
        if "my-designer" not in detected:
            for ext in ("*.swift", "*.html", "*.tsx", "*.jsx"):
                if next(worktree.rglob(ext), None) is not None:
                    add_skill("my-designer")
                    break
    return detected


def _expand_target_cmd(
    template: str | list[str],
    *,
    worktree: Path | None,
    handoff_dir: Path | None,
) -> tuple[list[str] | None, str | None]:
    tokens = [str(item) for item in template] if isinstance(template, list) else shlex.split(str(template))
    if not tokens:
        return None, "empty target_cmd"
    raw_joined = " ".join(tokens)
    if "{HANDOFF_DIR}" in raw_joined:
        if handoff_dir is None or not handoff_dir.is_dir():
            return None, "missing_handoff_dir"
        if "{HANDOFF_DIR}/design.md" in raw_joined and not (handoff_dir / "design.md").is_file():
            return None, "design.md_absent"
        if "{HANDOFF_DIR}/spec.md" in raw_joined and not (handoff_dir / "spec.md").is_file():
            return None, "spec.md_absent"
    if "{WORKTREE}" in raw_joined and (worktree is None or not worktree.is_dir()):
        return None, "missing_worktree"

    expanded: list[str] = []
    for token in tokens:
        val = token
        if handoff_dir is not None:
            val = val.replace("{HANDOFF_DIR}", str(handoff_dir.resolve()))
        if worktree is not None:
            val = val.replace("{WORKTREE}", str(worktree.resolve()))
        val = val.replace("{SKILL_ROOT}", str(ROOT.resolve()))
        expanded.append(val)
    return expanded, None


def run_eval_target(args: argparse.Namespace) -> int:
    repo_arg = getattr(args, "repo", None)
    repo_root = Path(repo_arg).expanduser().resolve() if repo_arg else ROOT
    handoff_dir = Path(args.handoff_dir).expanduser().resolve() if getattr(args, "handoff_dir", None) else None
    worktree = Path(args.worktree).expanduser().resolve() if getattr(args, "worktree", None) else None
    phase = getattr(args, "phase", "all") or "all"
    allowed_phases = {"spec", "code"} if phase == "all" else {phase}

    skill_names = _merge_skill_names(getattr(args, "skills", None), getattr(args, "skill", None))
    if not skill_names or skill_names == ["auto"]:
        skill_names = _detect_target_skills(handoff_dir, worktree, repo_root=repo_root)
    else:
        skill_names = [name for name in skill_names if name.lower() != "auto"]

    triggered_skills: list[str] = []
    executed_cases: list[str] = []
    skipped_cases: list[dict[str, str]] = []
    case_results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    passed = 0

    for skill_name in skill_names:
        pkg = _resolve_skill_dir(skill_name, repo=repo_arg, create=False)
        drawer_path = pkg / "references" / "eval-cases.json"
        if not drawer_path.is_file():
            continue
        try:
            doc = json.loads(drawer_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append({"skill": skill_name, "id": "<drawer>", "phase": phase, "reasons": [str(exc)]})
            continue

        for case in doc.get("active_cases", []):
            if not isinstance(case, dict):
                continue
            raw_assertions = case.get("target_assertions")
            target_Specs: list[dict[str, Any]] = []
            if isinstance(raw_assertions, list) and raw_assertions:
                for item in raw_assertions:
                    if isinstance(item, dict) and item.get("cmd"):
                        target_Specs.append(item)
            elif case.get("target_cmd"):
                target_Specs.append({
                    "phase": case.get("target_phase") or "code",
                    "cmd": case["target_cmd"],
                    "expect_exit": case.get("target_expect_exit", 0),
                    "expect_stdout_contains": case.get("target_expect_stdout_contains") or [],
                })
            if not target_Specs:
                continue

            case_id = str(case.get("id", "<unidentified>"))
            matched_specs = [
                spec for spec in target_Specs
                if str(spec.get("phase") or case.get("target_phase") or "code").lower() in allowed_phases
            ]
            if not matched_specs:
                continue

            expanded_runs: list[tuple[str, list[str], dict[str, Any]]] = []
            first_skip_reason: str | None = None
            for spec in matched_specs:
                spec_phase = str(spec.get("phase") or case.get("target_phase") or "code").lower()
                argv, skip_reason = _expand_target_cmd(
                    spec["cmd"],
                    worktree=worktree,
                    handoff_dir=handoff_dir,
                )
                if skip_reason is not None or argv is None:
                    first_skip_reason = first_skip_reason or skip_reason or "skipped"
                    continue
                expanded_runs.append((spec_phase, argv, spec))

            if not expanded_runs:
                skipped_cases.append({
                    "skill": skill_name,
                    "id": case_id,
                    "phase": str(matched_specs[0].get("phase") or case.get("target_phase") or "code").lower(),
                    "reason": first_skip_reason or "skipped",
                })
                continue

            if skill_name not in triggered_skills:
                triggered_skills.append(skill_name)
            executed_cases.append(f"{skill_name}:{case_id}")
            reasons: list[str] = []
            last_exit = 0
            case_phase = expanded_runs[0][0]
            for spec_phase, argv, spec in expanded_runs:
                proc = _run_command(argv, repo_root)
                last_exit = proc.returncode
                expect_exit = int(spec.get("expect_exit", case.get("target_expect_exit", 0)))
                if proc.returncode != expect_exit:
                    snippet = (proc.stdout.strip() or proc.stderr.strip() or f"exit {proc.returncode}")[:2400]
                    reasons.append(f"{case_id}: target_cmd exit {proc.returncode} != {expect_exit}: {snippet}")
                combined_out = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
                for token in spec.get("expect_stdout_contains") or case.get("target_expect_stdout_contains") or []:
                    if str(token) not in combined_out:
                        reasons.append(f"{case_id}: target output missing {token!r}")

            ok_case = len(reasons) == 0
            if ok_case:
                passed += 1
            else:
                failures.append({
                    "skill": skill_name,
                    "id": case_id,
                    "phase": case_phase,
                    "reasons": reasons,
                })
            case_results.append({
                "skill": skill_name,
                "id": case_id,
                "title": case.get("title", ""),
                "phase": case_phase,
                "ok": ok_case,
                "exit_code": last_exit,
                "reasons": reasons,
            })

    summary = {
        "triggered_skills": triggered_skills,
        "executed_case_ids": executed_cases,
        "cases_run": len(executed_cases),
        "passed": passed,
        "failed": len(failures),
        "caught": len(failures),
        "skipped": len(skipped_cases),
    }
    payload = {
        "ok": len(failures) == 0,
        "phase": phase,
        "handoff_dir": str(handoff_dir) if handoff_dir else None,
        "worktree": str(worktree) if worktree else None,
        "skill_eval_summary": summary,
        "results": case_results,
        "skipped_cases": skipped_cases,
        "failures": failures,
    }
    receipt_out = getattr(args, "receipt_out", None)
    if receipt_out:
        out_path = Path(receipt_out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _emit_json(payload, args)
    return 1 if failures and getattr(args, "strict", False) else 0


def run_eval(args: argparse.Namespace) -> int:
    repo_arg = _normalize_repo_path(getattr(args, "repo", None))
    skill_names = _merge_skill_names(getattr(args, "skills", None), getattr(args, "skill", None))
    if not skill_names and not getattr(args, "all", False) and not repo_arg:
        args.all = True
    repo_root = Path(repo_arg).expanduser().resolve() if repo_arg else ROOT
    packages = []
    if skill_names:
        for skill_name in skill_names:
            packages.extend(_discover_packages(skill=skill_name, repo=repo_arg))
    else:
        packages = _discover_packages(skill=None, repo=repo_arg)
    seen_drawers: set[Path] = set()
    drawers_evaluated = 0
    cases_run = 0
    passed = 0
    failures: list[dict[str, Any]] = []

    drawer_arg = getattr(args, "drawer", None)
    drawer_specs: list[tuple[str, Path, Path]] = []
    if drawer_arg:
        resolved = Path(drawer_arg).expanduser().resolve()
        inferred_skill = ROOT / "skills" / "skill-contract"
        if len(resolved.parents) > 1 and resolved.parent.name == "references":
            inferred_skill = resolved.parent.parent
        elif len(resolved.parents) > 2 and resolved.parent.name == "data" and resolved.parent.parent.name == "scripts":
            inferred_skill = resolved.parent.parent.parent
        drawer_specs.append((inferred_skill.name, resolved, inferred_skill))
    else:
        for pkg in packages:
            for rel_drawer in EVAL_DRAWERS:
                drawer_specs.append((pkg.name, pkg / rel_drawer, pkg))

    pending_evals: list[tuple[str, str, dict[str, Any], Path]] = []
    for skill_name, drawer_path, pkg in drawer_specs:
        if not drawer_path.is_file():
            continue
        resolved = drawer_path.resolve()
        if resolved in seen_drawers:
            continue
        seen_drawers.add(resolved)
        drawers_evaluated += 1
        rel_drawer = str(drawer_path.relative_to(pkg)) if drawer_path.is_relative_to(pkg) else drawer_path.name
        try:
            doc = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append({
                "skill": skill_name,
                "drawer": rel_drawer,
                "id": "<drawer>",
                "reasons": [str(exc)],
                "reference": f"{VALVE_REFERENCE_BASE}#valve-2--coordinate-and-eval-drawers",
            })
            continue
        for case in doc.get("active_cases", []):
                if not isinstance(case, dict):
                    continue
                has_assertion = bool(
                    (case.get("forbidden_regex") or case.get("regex")) and (case.get("bad_snippet") is not None or case.get("good_snippet") is not None)
                    or case.get("cmd")
                    or case.get("negative_cmd")
                    or case.get("file")
                    or case.get("assertions")
                )
                if not has_assertion:
                    if rel_drawer.endswith("eval-cases.json"):
                        cases_run += 1
                        failures.append({
                            "skill": pkg.name,
                            "drawer": rel_drawer,
                            "id": case.get("id"),
                            "reasons": ["eval case has no executable assertion"],
                            "reference": f"{VALVE_REFERENCE_BASE}#valve-2--coordinate-and-eval-drawers",
                        })
                    continue
                cases_run += 1
                pending_evals.append((pkg.name, rel_drawer, case, pkg))

    requested_jobs = getattr(args, "jobs", None)
    max_workers = max(1, int(requested_jobs)) if requested_jobs else min(8, max(1, os.cpu_count() or 4))
    if max_workers == 1 or len(pending_evals) <= 1:
        eval_outcomes = [evaluate_case(case, pkg, repo_root) for _, _, case, pkg in pending_evals]
    else:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(pending_evals))) as pool:
            eval_outcomes = list(pool.map(lambda item: evaluate_case(item[2], item[3], repo_root), pending_evals))

    for (skill_name, rel_drawer, case, _), reasons in zip(pending_evals, eval_outcomes):
        if reasons:
            failures.append({
                "skill": skill_name,
                "drawer": rel_drawer,
                "id": case.get("id"),
                "reasons": reasons,
                "reference": f"{VALVE_REFERENCE_BASE}#valve-2--coordinate-and-eval-drawers",
            })
        else:
            passed += 1

    payload: dict[str, Any] = {
        "ok": len(failures) == 0,
        "drawers_evaluated": drawers_evaluated,
        "cases_run": cases_run,
        "passed": passed,
        "failures": failures,
    }
    if failures:
        payload["guidance_drawer"] = f"{VALVE_REFERENCE_BASE}#valve-2--coordinate-and-eval-drawers"
    _emit_json(payload, args)
    return 1 if failures else 0



__all__ = [name for name in globals() if not name.startswith("__")]
