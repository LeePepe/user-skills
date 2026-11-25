from __future__ import annotations

from .core_common import *
from .core_eval import *
from .core_facts import *

def _meaningful_tokens(text: str) -> set[str]:
    stop = {"a", "an", "and", "are", "for", "from", "how", "into", "not", "the", "this", "with", "when", "use", "your", "skill"}
    tokens: set[str] = set()
    for token in re.findall(r"[\w-]+", text.lower()):
        if len(token) <= 2 or token in stop:
            continue
        tokens.add(token)
        for suffix in ("ing", "ed", "es", "s"):
            if token.endswith(suffix) and len(token) - len(suffix) > 2:
                tokens.add(token[:-len(suffix)])
                break
    return tokens


def _fallback_trigger(description: str, query: str) -> bool:
    positive = description.split("Not for", 1)[0].split("not for", 1)[0]
    query_tokens = _meaningful_tokens(query)
    desc_tokens = _meaningful_tokens(positive)
    return len(query_tokens & desc_tokens) >= (2 if len(query_tokens) > 1 else 1)


def _trigger_decision(value: Any) -> tuple[bool | None, str | None]:
    if isinstance(value, bool):
        return value, None
    if not isinstance(value, dict):
        return False, "malformed mock decision"
    if "answers" in value:
        value = value["answers"]
        if not isinstance(value, dict):
            return False, "malformed mock answers"
    choice = value.get("choice")
    choice_confidence = None
    if isinstance(choice, dict):
        if "confidence" in choice and not _valid_applicability(choice["confidence"]):
            return False, "invalid choice confidence"
        choice_confidence = choice.get("confidence")
        choice = choice.get("choice")
    if "choice" in value:
        if not isinstance(choice, str) or choice.lower() not in {"yes", "no", "true", "false"}:
            return False, "malformed mock choice"
        if "noul" not in value:
            return False, "choice requires companion noul"
    score_keys = [key for key in ("noul", "score", "confidence") if key in value]
    scores: dict[str, float] = {}
    for key in score_keys:
        score = value[key]
        if isinstance(score, dict):
            if "confidence" in score and not _valid_applicability(score["confidence"]):
                return False, f"invalid mock {key} confidence"
            score = score.get(key)
        if not _valid_applicability(score):
            return False, f"invalid mock {key}: expected finite number in [0, 1]"
        scores[key] = float(score)
    if scores:
        score = scores[score_keys[0]]
        if "choice" in value and choice.lower() in {"no", "false"}:
            if choice_confidence is None:
                choice_confidence = scores.get("confidence")
            if 0.30 <= score <= 0.70 and (choice_confidence is None or choice_confidence < 0.40):
                return None, None
            return False, None
        if score > 0.70:
            return True, None
        if score < 0.30:
            return False, None
        return None, None
    for key in ("should_trigger", "trigger"):
        if isinstance(value.get(key), bool):
            return value[key], None
    return False, "malformed mock decision"


def _valid_applicability(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1 and math.isfinite(value)


def _mock_trigger(mock: Any, index: int, case: dict[str, Any]) -> tuple[bool | None, str | None]:
    keys = [str(case[key]) for key in ("id", "query") if case.get(key)] + [str(index)]
    if isinstance(mock, dict):
        key = next((key for key in keys if key in mock), None)
        if key is None:
            return False, "incomplete mock truth table"
        value = mock[key]
    elif isinstance(mock, list):
        if index - 1 >= len(mock):
            return False, "incomplete mock truth table"
        value = mock[index - 1]
    else:
        return False, "malformed mock truth table"
    return _trigger_decision(value)


def _jev_trigger(skill: str, description: str, query: str) -> tuple[bool | None, str | None]:
    client_path = ROOT / "skills" / "team-lead" / "scripts" / "jev_client.py"
    if not client_path.is_file():
        return None, "Jev client unavailable"
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("skill_contract_jev_client", client_path)
        if spec is None or spec.loader is None:
            return None, "Jev client unavailable"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        state = {"skill": skill, "description": description, "query": query}
        questions = {
            "choice": {
                "type": "choice", "instructions": "Does the query request a capability permitted by this skill description?",
                "criteria": {"yes": "The query falls within the permitted skill scope.", "no": "The query falls outside the permitted skill scope."},
            },
            "noul": {"type": "noul", "instructions": "Is the query within the skill's permitted scope?"},
        }
        result = module.jev_decide(state, questions, timeout_sec=1.5)
        if not isinstance(result, dict):
            return None, "Jev unavailable or missing answers"
        answers = result.get("answers", result)
        if not isinstance(answers, dict) or "choice" not in answers or "noul" not in answers:
            return None, "Jev missing choice or companion noul"
        matched, error = _trigger_decision(answers)
        return (None, error) if error else (matched, "grey applicability" if matched is None else None)
    except (OSError, ImportError, TypeError, ValueError, AttributeError) as exc:
        return None, f"Jev unavailable: {type(exc).__name__}"


def description_eval(args: argparse.Namespace) -> int:
    target = _resolve_skill_dir(args.skill, repo=getattr(args, "repo", None), create=False)
    desc = _frontmatter_description(target / "SKILL.md")
    drawer = target / "references" / "eval-cases.json"
    doc = json.loads(drawer.read_text(encoding="utf-8")) if drawer.is_file() else {}
    cases = doc.get("trigger_evals", [])
    failures: list[dict[str, Any]] = []
    mock = None
    supplied_mock = args.mock_jev_json is not None
    if supplied_mock:
        try:
            mock = _load_json_argument(args.mock_jev_json)
        except ValueError as exc:
            failures.append({"error": str(exc)})
    if supplied_mock and not isinstance(mock, (dict, list)):
        failures.append({"error": "malformed mock truth table"})
    if isinstance(mock, dict):
        valid_keys = {str(i) for i in range(1, len(cases) + 1)} | {str(case[key]) for case in cases if isinstance(case, dict) for key in ("id", "query") if case.get(key)}
        unknown = sorted(set(mock) - valid_keys)
        if unknown:
            failures.append({"error": f"unknown mock truth-table keys: {unknown}"})
    if isinstance(mock, list) and len(mock) > len(cases):
        failures.append({"error": "extra mock truth-table entries"})
    predicted: list[bool] = []
    expected_values: list[bool] = []
    decisions: list[dict[str, Any]] = []
    passed = 0
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict) or not isinstance(case.get("query"), str) or not isinstance(case.get("should_trigger"), bool):
            failures.append({"index": index, "error": "malformed trigger_eval"})
            continue
        query = case["query"]
        source = "mock" if supplied_mock else "jev"
        error = None
        fallback_reason = None
        if supplied_mock:
            matched, error = _mock_trigger(mock, index, case)
            if error:
                failures.append({"index": index, "query": query, "error": error})
            elif matched is None:
                fallback_reason = "grey applicability"
        else:
            matched, fallback_reason = _jev_trigger(args.skill, desc, query)
        if matched is None:
            matched = _fallback_trigger(desc, query)
            source = "deterministic-fallback"
        decision = {"index": index, "query": query, "matched": matched, "source": source}
        if fallback_reason:
            decision["fallback_reason"] = fallback_reason
        if error:
            decision["error"] = error
        decisions.append(decision)
        predicted.append(matched)
        expected_values.append(case["should_trigger"])
        if matched != case["should_trigger"]:
            failures.append({"index": index, "query": query, "expected": case["should_trigger"], "matched": matched, "near_miss_skill": case.get("near_miss_skill")})
        elif not error:
            passed += 1
    tp = sum(p and e for p, e in zip(predicted, expected_values)); fp = sum(p and not e for p, e in zip(predicted, expected_values)); fn = sum((not p) and e for p, e in zip(predicted, expected_values))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    payload = {"ok": not failures and 0 < len(desc) <= 320, "skill": args.skill, "description_length": len(desc), "total": len(cases), "passed": passed, "precision": precision, "recall": recall, "failures": failures, "decisions": decisions}
    _emit_json(payload, args)
    return 1 if args.strict and not payload["ok"] else 0


def _json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def sync_jev_stars(args: argparse.Namespace) -> int:
    target = _resolve_skill_dir(args.skill, repo=getattr(args, "repo", None), create=False)
    drawer = target / "references" / "eval-cases.json"
    original = drawer.read_text(encoding="utf-8") if drawer.is_file() else None
    doc = json.loads(original) if original else {"schema": "skill-eval-cases/v1", "version": 1, "active_cases": []}
    entries = [item for item in doc.get("active_cases", []) if isinstance(item, dict)]
    db_path = Path(args.db_path).expanduser() if args.db_path else Path.home() / "Library/Application Support/Falcon/falcon.sqlite"
    if not db_path.is_file():
        _emit_json({"ok": False, "error": f"database not found: {db_path}"}, args); return 1
    try:
        con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True); con.row_factory = sqlite3.Row
        try:
            rows = con.execute("SELECT r.*, s.name AS source_name, rv.state AS review_state_joined FROM requests r LEFT JOIN sources s ON r.source_id=s.id LEFT JOIN reviews rv ON rv.request_id=r.id WHERE json_extract(CAST(r.summary_json AS TEXT), '$.starredAt') IS NOT NULL OR rv.state='starred' OR r.review_state='starred' ORDER BY r.id").fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        _emit_json({"ok": False, "error": f"database query failed: {exc}"}, args); return 1
    fallbacks = {"team-lead": {"goal-task"}, "personal-m2-assistant": {"team-lead", "goal-task"}, "m2-dispatcher": {"team-lead", "goal-task"}, "writer": {"team-lead", "goal-task"}, "reviewer": {"team-lead", "goal-task"}, "explorer": {"team-lead", "goal-task"}, "knowledge-digest": {"deep-research"}, "design-studio": {"my-designer"}, "retro-loop": {"shared-ci", "team-lead", "goal-task"}, "howto": {"goal-task", "team-lead"}}
    requested = args.source_name
    allowed = {requested} if requested else {args.skill} | fallbacks.get(args.skill, set())
    selected: dict[str, sqlite3.Row] = {}
    malformed: list[str] = []
    for row in rows:
        if row["source_name"] not in allowed:
            continue
        sid = str(row["id"])
        selected[sid] = row
    repo_root = Path(args.repo).expanduser().resolve() if getattr(args, "repo", None) else ROOT
    try:
        default_file = str((target / "SKILL.md").relative_to(repo_root))
    except ValueError:
        default_file = "SKILL.md"
    existing = {str(item.get("jev_starred_id")): item for item in entries if item.get("jev_starred_id") is not None}
    planned: list[dict[str, Any]] = []
    skips: list[str] = []
    archived = list(doc.get("archived_cases", []))
    evicted: list[str] = []
    added = 0
    for sid, row in selected.items():
        try:
            received = _json_value(row["received_request"])
            response = _json_value(row["upstream_response"])
            if not isinstance(received, dict) or not isinstance(response, dict):
                raise ValueError("request/response is not an object")
            questions = received.get("questions", {})
            state = received.get("state")
            answers = response.get("answers", response)
            previous = existing.get(sid)
            entry = dict(previous) if previous is not None else {
                "id": _next_case_id(entries + archived + planned, "EV"),
                "title": f"Falcon starred decision {sid}", "registered_at": date.today().isoformat(),
            }
            entry.update({"jev_starred_id": row["id"], "jev_state": state, "jev_questions": questions, "mock_jev_response": answers})
            tags = list(entry.get("tags") or [])
            if "jev-star" not in tags:
                tags.append("jev-star")
            entry["tags"] = tags
            if args.cmd_template:
                mock_json = json.dumps(answers, ensure_ascii=False, separators=(",", ":"))
                entry["cmd"] = [arg.replace("{mock_json}", mock_json) for arg in shlex.split(args.cmd_template)]
                entry.setdefault("expect_exit", 0)
            elif not any(entry.get(key) for key in ("cmd", "negative_cmd", "file", "forbidden_regex", "regex")):
                entry["file"] = default_file
                entry["must_contain"] = [f"name: {args.skill}"]
            reasons = evaluate_case(entry, target, repo_root)
            if reasons:
                malformed.append(f"{sid}: {'; '.join(reasons)}")
                continue
            if previous is not None:
                position = next(i for i, item in enumerate(entries) if str(item.get("jev_starred_id")) == sid)
                entries[position] = entry
            else:
                if len(entries) >= 15:
                    ordinary = [(i, item) for i, item in enumerate(entries) if not _is_golden(item)]
                    if not ordinary:
                        skips.append(sid)
                        continue
                    position, evicted_item = min(ordinary, key=lambda pair: (str(pair[1].get("registered_at", pair[1].get("date", ""))), str(pair[1].get("id", ""))))
                    entries.pop(position)
                    archived.append(evicted_item)
                    evicted.append(str(evicted_item.get("id")))
                entries.append(entry)
                added += 1
            planned.append(entry)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            malformed.append(f"{sid}: {exc}")
    payload = {"ok": not malformed, "dry_run": bool(args.dry_run), "skill": args.skill, "planned": len(planned), "added": added, "evicted": evicted, "skipped": skips, "malformed": malformed, "active": len(entries)}
    if not args.dry_run and not malformed:
        doc["active_cases"] = entries; doc["archived_cases"] = archived; doc["updated"] = date.today().isoformat(); drawer.parent.mkdir(parents=True, exist_ok=True); drawer.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _emit_json(payload, args); return 1 if malformed else 0


def uptake_eval(args: argparse.Namespace) -> int:
    retro_script = ROOT / "skills" / "skill-retro" / "scripts" / "skill_retro.py"
    spec = importlib.util.spec_from_file_location("skill_retro_uptake", retro_script)
    if spec is None or spec.loader is None:
        _emit_json({"ok": False, "error": f"skill_retro.py not found at {retro_script}"}, args)
        return 1
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    target_dir = Path(args.target_dir).expanduser().resolve() if getattr(args, "target_dir", None) else ROOT
    uptake_map_path = Path(args.uptake_map_path).expanduser().resolve() if getattr(args, "uptake_map_path", None) else None
    merged_skills = _merge_skill_names(getattr(args, "skill", None), getattr(args, "skills", None))
    skills_arg = ",".join(merged_skills) if merged_skills else None
    out = mod.evaluate_skill_uptake(
        skills=skills_arg,
        triggered_skills=args.triggered_skills,
        target_dir=target_dir,
        scenario=args.scenario,
        uptake_map_path=uptake_map_path,
        baseline_evaluator_pct=args.baseline_pct,
        skill_evaluator_pct=args.skill_pct,
        build_success=args.build_success,
    )
    _emit_json(out, args)
    return 1 if (getattr(args, "strict", False) and not out["ok"]) else 0



__all__ = [name for name in globals() if not name.startswith("__")]
