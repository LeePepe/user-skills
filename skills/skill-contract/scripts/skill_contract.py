#!/usr/bin/env python3
"""Scaffold and audit progressive-disclosure skill packages."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import math
import os
import re
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
PRIVATE = re.compile(r"\b(?:progressRail|isUnilateral|DashboardL10n)\b")
BUDGET_EXEMPTIONS: set[str] = set()
COORDINATE_KEYS = ("platforms", "dimensions", "containers", "tags", "routes", "layers")
DRAWERS = {
    "defect-cases/index.json": ("active_cases", ("platforms", "dimensions", "containers")),
    "eval-cases.json": ("active_cases", None),
    "review-smells.json": ("active_smells", ("languages", "layers")),
    "build-cases.json": ("active_cases", ("routes",)),
    "source-tiers.json": ("high_signal_tiers", ("topics", "patterns")),
    "card-templates.json": ("templates", ("tags",)),
}
EVAL_DRAWERS = (
    "references/eval-cases.json",
    "references/defect-cases/index.json",
)


def package_roots(repo: str | Path | None = None) -> list[Path]:
    if repo is not None:
        repo_path = Path(repo).expanduser().resolve()
        return [
            candidate
            for candidate in (repo_path / "skills", repo_path / ".agents" / "skills")
            if candidate.is_dir()
        ]
    roots = [ROOT / "skills"]
    for candidate in (
        ROOT / "Skills" / "skills" / "meta",
        ROOT / "Skills" / "skills" / "repo",
        ROOT.parent / "Skills" / "skills" / "meta",
        ROOT.parent / "Skills" / "skills" / "repo",
        ROOT.parent.parent / "Skills" / "skills" / "meta",
        ROOT.parent.parent / "Skills" / "skills" / "repo",
    ):
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return roots


def _resolve_skill_dir(skill: str, repo: str | Path | None = None, create: bool = False) -> Path:
    if repo is None:
        for root in package_roots():
            if (root / skill / "SKILL.md").is_file() or (root / skill).is_dir():
                return root / skill
        return ROOT / "skills" / skill
    repo_path = Path(repo).expanduser().resolve()
    candidates = [repo_path / "skills" / skill, repo_path / ".agents" / "skills" / skill]
    for base in (repo_path / "skills", repo_path / ".agents" / "skills"):
        if base.is_dir():
            candidates.extend(sub / skill for sub in sorted(base.iterdir()) if sub.is_dir())
    for candidate in candidates:
        if (candidate / "SKILL.md").is_file() or candidate.is_dir():
            return candidate
    if create:
        if (repo_path / ".agents" / "skills").is_dir() and not (repo_path / "skills").is_dir():
            return repo_path / ".agents" / "skills" / skill
        return repo_path / "skills" / skill
    return candidates[0]


def _discover_packages(skill: str | None = None, repo: str | Path | None = None) -> list[Path]:
    if skill:
        return [_resolve_skill_dir(skill, repo=repo, create=False)]
    seen: set[Path] = set()
    packages: list[Path] = []
    for root in package_roots(repo=repo):
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if p.is_dir() and (p / "SKILL.md").exists():
                resolved = p.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    packages.append(p)
    return packages


def init_skill(args: argparse.Namespace) -> int:
    target = _resolve_skill_dir(args.skill, repo=getattr(args, "repo", None), create=True)
    target.mkdir(parents=True, exist_ok=True)
    for dirname in (".claude-plugin", "references", "scripts"):
        (target / dirname).mkdir(exist_ok=True)
    design = " Design skills use an 80-line L0 budget." if args.design else ""
    (target / "SKILL.md").write_text(
        f'---\nname: {args.skill}\ndescription: "{args.skill} skill."\n---\n\n'
        f"# {args.skill}\n\nProgressive disclosure entry point for the {args.category} category.{design}\n\n"
        "Read references on demand; keep deterministic checks in scripts.\n",
        encoding="utf-8",
    )
    manifest = {"name": args.skill, "description": f"{args.skill} skill", "version": "1.0.0", "category": args.category}
    (target / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "skill": args.skill, "path": str(target)}))
    return 0


def _csv(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _target_path(target: Path, value: str | None, default: str) -> Path:
    if not value:
        return target / default
    p = Path(value)
    return p if p.is_absolute() or (ROOT / p).exists() or value.startswith("skills/") else target / p


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


def evaluate_case(case: dict[str, Any], skill_dir: Path, repo_root: Path = ROOT) -> list[str]:
    reasons: list[str] = []
    cid = str(case.get("id", "<unidentified>"))
    if _is_golden(case):
        reasons.extend(f"{cid}: {reason}" for reason in _snapshot_failures(case))
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
        print(json.dumps({"ok": False, "error": "project-private identifiers are forbidden in cases"}))
        return 1

    if args.forbidden_regex and (not args.bad_snippet or not args.good_snippet):
        print(json.dumps({"ok": False, "error": "forbidden_regex requires both --bad-snippet and --good-snippet"}))
        return 1

    if not args.forbidden_regex and not args.cmd and not args.file and not args.negative_cmd:
        print(json.dumps({"ok": False, "error": "add-case requires at least one executable assertion (--forbidden-regex, --cmd, --negative-cmd, or --file)"}))
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
        print(json.dumps({"ok": False, "id": ident, "failures": failures}, ensure_ascii=False, indent=2))
        return 1

    entries = [item for item in entries if not (isinstance(item, dict) and item.get("id") == ident)]
    entries.append(entry)
    doc["active_cases"] = entries
    doc["updated"] = date.today().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "skill": args.skill, "drawer": drawer_rel, "id": ident, "entry": entry}, ensure_ascii=False))
    return 0


def run_eval(args: argparse.Namespace) -> int:
    if not getattr(args, "all", False) and not getattr(args, "skill", None) and not getattr(args, "repo", None):
        raise SystemExit("eval requires one of --all, --skill, or --repo")
    repo_arg = getattr(args, "repo", None)
    repo_root = Path(repo_arg).expanduser().resolve() if repo_arg else ROOT
    packages = _discover_packages(skill=getattr(args, "skill", None), repo=repo_arg)
    seen_drawers: set[Path] = set()
    drawers_evaluated = 0
    cases_run = 0
    passed = 0
    failures: list[dict[str, Any]] = []

    for pkg in packages:
        for rel_drawer in EVAL_DRAWERS:
            drawer_path = pkg / rel_drawer
            if not drawer_path.is_file():
                continue
            resolved = drawer_path.resolve()
            if resolved in seen_drawers:
                continue
            seen_drawers.add(resolved)
            drawers_evaluated += 1
            try:
                doc = json.loads(resolved.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                failures.append({"skill": pkg.name, "drawer": rel_drawer, "id": "<drawer>", "reasons": [str(exc)]})
                continue
            for case in doc.get("active_cases", []):
                if not isinstance(case, dict):
                    continue
                has_assertion = bool(
                    (case.get("forbidden_regex") or case.get("regex")) and (case.get("bad_snippet") is not None or case.get("good_snippet") is not None)
                    or case.get("cmd")
                    or case.get("negative_cmd")
                    or case.get("file")
                )
                if not has_assertion:
                    if rel_drawer.endswith("eval-cases.json"):
                        cases_run += 1
                        failures.append({"skill": pkg.name, "drawer": rel_drawer, "id": case.get("id"), "reasons": ["eval case has no executable assertion"]})
                    continue
                cases_run += 1
                reasons = evaluate_case(case, pkg, repo_root)
                if reasons:
                    failures.append({"skill": pkg.name, "drawer": rel_drawer, "id": case.get("id"), "reasons": reasons})
                else:
                    passed += 1

    payload = {
        "ok": len(failures) == 0,
        "drawers_evaluated": drawers_evaluated,
        "cases_run": cases_run,
        "passed": passed,
        "failures": failures,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failures else 0


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
        path = _target_path(target, args.target_file, "references/defect-cases/index.json")
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
    print(json.dumps({"ok": True, "skill": args.skill, "kind": args.kind}))
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


def _frontmatter_description(md: Path) -> str:
    value = _frontmatter_fields(md).get("description", "")
    return value if isinstance(value, str) else ""


def _thin_wrapper(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return len(text.splitlines()) <= 25 and ("_CANONICAL_PATH" in text or "CANONICAL" in text)


def audit_package(path: Path, design: bool = False, repo_bound: bool = False, sibling_corpus: str = "") -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    skill = path.name
    md = path / "SKILL.md"
    if not md.exists():
        return [{"skill": skill, "code": "missing-skill"}]
    text = md.read_text(encoding="utf-8")
    desc = _frontmatter_description(md)
    if not desc or len(desc) > 320:
        findings.append({"skill": skill, "code": "description-length", "value": len(desc), "cap": 320})
    manifest = path / ".claude-plugin" / "plugin.json"
    if manifest.is_file():
        try:
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_desc = manifest_data.get("description") if isinstance(manifest_data, dict) else None
            if not isinstance(manifest_desc, str) or not manifest_desc or len(manifest_desc) > 320:
                findings.append({"skill": skill, "code": "description-length", "value": len(manifest_desc) if isinstance(manifest_desc, str) else 0, "cap": 320, "file": str(manifest)})
        except (OSError, UnicodeError, json.JSONDecodeError):
            findings.append({"skill": skill, "code": "invalid-manifest", "file": str(manifest)})
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
        if ref.is_dir() or not ref.is_file() or ref.suffix not in {".md", ".json"}:
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
        _audit_drawer(skill, ref, findings, pkg_dir=path)
        local_link = re.search(rf"(?<![A-Za-z0-9_-])(?:references/)?{re.escape(ref.name)}(?![A-Za-z0-9_.-])", all_md)
        canonical_link = re.search(rf"(?:\.\./)?{re.escape(skill)}/references/{re.escape(ref.name)}", sibling_corpus)
        if not local_link and not canonical_link:
            findings.append({"skill": skill, "code": "orphan-reference", "file": str(ref)})
    return findings


def audit(args: argparse.Namespace) -> int:
    if not getattr(args, "all", False) and not getattr(args, "skill", None) and not getattr(args, "repo", None):
        raise SystemExit("audit requires one of --all, --skill, or --repo")
    repo_arg = getattr(args, "repo", None)
    selected = _discover_packages(skill=getattr(args, "skill", None), repo=repo_arg)
    all_packages = _discover_packages(repo=repo_arg)
    selected_set = {p.resolve() for p in selected}
    findings: list[dict[str, Any]] = []
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
        names: dict[str, list[str]] = {}
        for package in group:
            names.setdefault(package.name, []).append(str(package))
        findings.extend({"code": "duplicate-skill", "name": name, "paths": paths} for name, paths in names.items() if len(paths) > 1)
        scripts: dict[str, list[Path]] = {}
        for package in group:
            for script in (package / "scripts").glob("*.py") if (package / "scripts").is_dir() else []:
                if not script.is_symlink():
                    scripts.setdefault(script.name, []).append(script)
        for name, paths in scripts.items():
            real = [path for path in paths if not _thin_wrapper(path)]
            if len(real) >= 2:
                findings.append({"code": "duplicate-script-fork", "script": name, "paths": [str(path) for path in real]})
    result = {"ok": not findings, "skills": len(selected), "findings": findings}
    print(json.dumps(result, indent=2))
    return 1 if findings and args.strict else 0


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
    if isinstance(choice, dict):
        if "confidence" in choice and not _valid_applicability(choice["confidence"]):
            return False, "invalid choice confidence"
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
    if "choice" in value and choice.lower() in {"no", "false"}:
        return False, None
    if scores:
        score = scores[score_keys[0]]
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
                "type": "choice", "instructions": "Should this skill handle the query, based only on its description and exclusions? Choose yes or no.",
                "criteria": {"yes": "The query falls within the described skill scope.", "no": "The query is excluded or outside the described skill scope."},
            },
            "noul": {"type": "noul", "instructions": "Score applicability of this skill to the query from 0 (not applicable) to 1 (fully applicable), respecting exclusions."},
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
    print(json.dumps(payload, ensure_ascii=False, indent=2))
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
        print(json.dumps({"ok": False, "error": f"database not found: {db_path}"})); return 1
    try:
        con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True); con.row_factory = sqlite3.Row
        try:
            rows = con.execute("SELECT r.*, s.name AS source_name, rv.state AS review_state_joined FROM requests r LEFT JOIN sources s ON r.source_id=s.id LEFT JOIN reviews rv ON rv.request_id=r.id WHERE json_extract(CAST(r.summary_json AS TEXT), '$.starredAt') IS NOT NULL OR rv.state='starred' OR r.review_state='starred' ORDER BY r.id").fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        print(json.dumps({"ok": False, "error": f"database query failed: {exc}"})); return 1
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
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 1 if malformed else 0


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--skill", required=True)
    init.add_argument("--category", required=True)
    init.add_argument("--design", action="store_true")
    init.add_argument("--repo", default=None)

    sink = sub.add_parser("sink-rule")
    sink.add_argument("--skill", required=True)
    sink.add_argument("--kind", choices=["regex-gate", "case-card", "mental-model"], required=True)
    sink.add_argument("--id")
    sink.add_argument("--target-file")
    sink.add_argument("--replace-row")
    sink.add_argument("--section")
    sink.add_argument("--value", default="")
    sink.add_argument("--platforms", default="desktop,mobile,web")
    sink.add_argument("--dimensions", default="")
    sink.add_argument("--containers", default="")
    sink.add_argument("--title", default="")
    sink.add_argument("--severity", default="Medium")
    sink.add_argument("--regex", default="")
    sink.add_argument("--remediation", default="")
    sink.add_argument("--bad-snippet", default=None)
    sink.add_argument("--good-snippet", default=None)

    add_p = sub.add_parser("add-case")
    add_p.add_argument("--skill", required=True)
    add_p.add_argument("--repo", default=None)
    add_p.add_argument("--drawer", default="references/eval-cases.json")
    add_p.add_argument("--id", default="auto")
    add_p.add_argument("--title", required=True)
    add_p.add_argument("--tags", default="")
    add_p.add_argument("--routes", default="")
    add_p.add_argument("--layers", default="")
    add_p.add_argument("--platforms", default="")
    add_p.add_argument("--dimensions", default="")
    add_p.add_argument("--containers", default="")
    add_p.add_argument("--symptom", default="")
    add_p.add_argument("--fix-rule", default="")
    add_p.add_argument("--forbidden-regex", default="")
    add_p.add_argument("--bad-snippet", default=None)
    add_p.add_argument("--good-snippet", default=None)
    add_p.add_argument("--cmd", default="")
    add_p.add_argument("--expect-exit", type=int, default=0)
    add_p.add_argument("--expect-contains", default="")
    add_p.add_argument("--expect-json-subset", default="")
    add_p.add_argument("--file", default="")
    add_p.add_argument("--must-contain", default="")
    add_p.add_argument("--must-not-contain", default="")
    add_p.add_argument("--negative-cmd", default="", help="Standalone command self-test requiring a genuine nonzero exit")
    add_p.add_argument("--expect-fail-exit", type=int, default=None, help="Require this exact nonzero exit; otherwise any nonzero exit passes")
    add_p.add_argument("--negative-stdout-contains", default="")
    add_p.add_argument("--negative-stderr-contains", default="")

    eval_p = sub.add_parser("eval")
    eval_group = eval_p.add_mutually_exclusive_group(required=False)
    eval_group.add_argument("--all", action="store_true")
    eval_group.add_argument("--skill")
    eval_p.add_argument("--repo", default=None)
    eval_p.add_argument("--json", action="store_true")
    eval_p.add_argument("--strict", action="store_true")

    desc_p = sub.add_parser("description-eval")
    desc_p.add_argument("--skill", required=True)
    desc_p.add_argument("--repo", default=None)
    desc_p.add_argument("--mock-jev-json", default=None)
    desc_p.add_argument("--strict", action="store_true")

    star_p = sub.add_parser("sync-jev-stars")
    star_p.add_argument("--skill", required=True)
    star_p.add_argument("--repo", default=None)
    star_p.add_argument("--db-path", default=None)
    star_p.add_argument("--source-name", default=None)
    star_p.add_argument("--cmd-template", default=None)
    star_p.add_argument("--dry-run", action="store_true")

    audit_parser = sub.add_parser("audit")
    group = audit_parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--all", action="store_true")
    group.add_argument("--skill")
    audit_parser.add_argument("--repo", default=None)
    audit_parser.add_argument("--json", action="store_true")
    audit_parser.add_argument("--strict", action="store_true")
    return parser


def main() -> int:
    args = parser().parse_args()
    return {
        "init": init_skill,
        "sink-rule": sink_rule,
        "add-case": add_case,
        "eval": run_eval,
        "description-eval": description_eval,
        "sync-jev-stars": sync_jev_stars,
        "audit": audit,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
