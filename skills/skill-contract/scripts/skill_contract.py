#!/usr/bin/env python3
"""Scaffold and audit progressive-disclosure skill packages."""
from __future__ import annotations

import argparse
from datetime import date
import json
import re
import shlex
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


def evaluate_case(case: dict[str, Any], skill_dir: Path, repo_root: Path = ROOT) -> list[str]:
    reasons: list[str] = []
    cid = str(case.get("id", "<unidentified>"))
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
        argv = list(cmd_raw) if isinstance(cmd_raw, list) else shlex.split(str(cmd_raw))
        if argv and argv[0] in {"python", "python3"}:
            argv[0] = sys.executable
        proc = subprocess.run(argv, cwd=repo_root, capture_output=True, text=True, check=False)
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
    ]
    if not repo_arg and any(PRIVATE.search(val or "") for val in text_fields):
        print(json.dumps({"ok": False, "error": "project-private identifiers are forbidden in cases"}))
        return 1

    if args.forbidden_regex and (not args.bad_snippet or not args.good_snippet):
        print(json.dumps({"ok": False, "error": "forbidden_regex requires both --bad-snippet and --good-snippet"}))
        return 1

    if not args.forbidden_regex and not args.cmd and not args.file:
        print(json.dumps({"ok": False, "error": "add-case requires at least one executable assertion (--forbidden-regex, --cmd, or --file)"}))
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
            if required is None:
                has_any = any(isinstance(entry.get(k), list) and len(entry.get(k)) > 0 for k in COORDINATE_KEYS)
                if not has_any:
                    findings.append({"skill": skill, "code": "missing-coordinate", "file": rel, "id": entry.get("id"), "fields": list(COORDINATE_KEYS)})
            else:
                missing = [field for field in required if field not in entry]
                if missing:
                    findings.append({"skill": skill, "code": "missing-coordinate", "file": rel, "id": entry.get("id"), "fields": missing})


def audit_package(path: Path, design: bool = False, repo_bound: bool = False) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    skill = path.name
    md = path / "SKILL.md"
    if not md.exists():
        return [{"skill": skill, "code": "missing-skill"}]
    text = md.read_text(encoding="utf-8")
    all_md = text
    refs = path / "references"
    if refs.is_dir():
        all_md += "\n" + "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in refs.glob("*.md"))
    if not repo_bound and PRIVATE.search(all_md):
        findings.append({"skill": skill, "code": "private-identifier"})
    raw_regex_lines = [line for line in all_md.splitlines() if "forbidden_regex" in line or "regex-gate" in line]
    if len(raw_regex_lines) > 1:
        findings.append({"skill": skill, "code": "raw-regex-gates-in-markdown"})
    md_rel = f"{skill}/SKILL.md"
    if md_rel not in BUDGET_EXEMPTIONS and len(md.read_text(encoding="utf-8").splitlines()) > (80 if (design or skill in {"my-designer", "design-studio"}) else 180):
        findings.append({"skill": skill, "code": "l0-lines"})
    if md_rel not in BUDGET_EXEMPTIONS and md.stat().st_size > 16 * 1024:
        findings.append({"skill": skill, "code": "l0-bytes"})
    for ref in refs.glob("*") if refs.is_dir() else []:
        if ref.suffix not in {".md", ".json"}:
            continue
        ref_rel = f"{skill}/references/{ref.name}"
        ref_text = ref.read_text(encoding="utf-8")
        if ref.suffix == ".md" and ref_rel not in BUDGET_EXEMPTIONS and len(ref_text.splitlines()) > 160:
            findings.append({"skill": skill, "code": "l1-lines", "file": str(ref)})
        if ref.suffix == ".md" and ref_rel not in BUDGET_EXEMPTIONS and ref.stat().st_size > 16 * 1024:
            findings.append({"skill": skill, "code": "l1-bytes", "file": str(ref)})
        if ref.suffix == ".md" and (repo_bound or skill in {"skill-contract", "my-designer", "design-studio", "repo-kit", "nas-smart-home", "knowledge-digest"}):
            for block in re.findall(r"```[^\n]*\n(.*?)```", ref_text, re.DOTALL):
                if len(block.splitlines()) > 25:
                    findings.append({"skill": skill, "code": "fenced-code-lines", "file": str(ref)})
                    break
        _audit_drawer(skill, ref, findings, pkg_dir=path)
        corpus = text + "\n" + "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in refs.glob("*.md"))
        corpus += "\n" + "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in (path / "scripts").glob("*.py")) if (path / "scripts").is_dir() else ""
        if ref.name not in corpus and ref.stem not in corpus:
            findings.append({"skill": skill, "code": "orphan-reference", "file": str(ref)})
    return findings


def audit(args: argparse.Namespace) -> int:
    if not getattr(args, "all", False) and not getattr(args, "skill", None) and not getattr(args, "repo", None):
        raise SystemExit("audit requires one of --all, --skill, or --repo")
    repo_arg = getattr(args, "repo", None)
    packages = _discover_packages(skill=getattr(args, "skill", None), repo=repo_arg)
    findings: list[dict[str, Any]] = []
    names: dict[str, list[str]] = {}
    for package in packages:
        names.setdefault(package.name, []).append(str(package))
        findings.extend(audit_package(package, repo_bound=bool(repo_arg)))
    findings.extend({"code": "duplicate-skill", "name": name, "paths": paths} for name, paths in names.items() if len(paths) > 1)
    result = {"ok": not findings, "skills": len(packages), "findings": findings}
    print(json.dumps(result, indent=2))
    return 1 if findings and args.strict else 0


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

    eval_p = sub.add_parser("eval")
    eval_group = eval_p.add_mutually_exclusive_group(required=False)
    eval_group.add_argument("--all", action="store_true")
    eval_group.add_argument("--skill")
    eval_p.add_argument("--repo", default=None)
    eval_p.add_argument("--json", action="store_true")
    eval_p.add_argument("--strict", action="store_true")

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
        "audit": audit,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
