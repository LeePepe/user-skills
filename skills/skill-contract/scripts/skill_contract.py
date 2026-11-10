#!/usr/bin/env python3
"""Scaffold and audit progressive-disclosure skill packages."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
PRIVATE = re.compile(r"\b(?:progressRail|isUnilateral|DashboardL10n)\b")
BUDGET_EXEMPTIONS = {
    "m2-dispatcher/SKILL.md", "team-lead/SKILL.md",
    "m2-dispatcher/references/ledger.md", "m2-dispatcher/references/bots-and-agents.md",
    "team-lead/references/ledger.md", "team-lead/references/bots-and-agents.md",
    "design-studio/references/toolchain-20-matrix.md",
    "workflow-hub/references/his-fm-nocoo-study-and-roadmap.md",
}
DRAWERS = {
    "defect-cases/index.json": ("active_cases", ("platforms", "dimensions", "containers")),
    "review-smells.json": ("active_smells", ("languages", "layers")),
    "build-cases.json": ("active_cases", ("routes",)),
    "source-tiers.json": ("high_signal_tiers", ("topics", "patterns")),
}
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "to", "of", "for", "in", "on", "with", "when",
    "use", "this", "that", "skill", "skills", "user", "users", "from", "into",
    "as", "by", "is", "be", "it", "its", "at", "if", "not", "do", "does",
    "how", "what", "any", "all",
})
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", re.I)
DEFAULT_TRIGGER_THRESHOLD = 0.2
DESCRIPTION_CUE_CAP = 8
DESCRIPTION_CHAR_CAP = 600


def package_roots() -> list[Path]:
    roots = [ROOT / "skills"]
    for candidate in (ROOT / "Skills" / "skills" / "meta", ROOT / "Skills" / "skills" / "repo"):
        if candidate.is_dir():
            roots.append(candidate)
    return roots


def init_skill(args: argparse.Namespace) -> int:
    target = ROOT / "skills" / args.skill
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
    return Path(value) if value else target / default


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
        doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"active_cases": []}
        entries = doc.setdefault("active_cases", doc.setdefault("cases", []))
        numbers = [int(m.group(1)) for item in entries if (m := re.fullmatch(r"DC-(\d+)", str(item.get("id", ""))))]
        ident = args.id if args.id and args.id != "auto" else f"DC-{(max(numbers) if numbers else 0) + 1:03d}"
        entry = {"id": ident, "platforms": _csv(args.platforms), "dimensions": _csv(args.dimensions), "containers": _csv(args.containers), "title": args.title, "severity": args.severity, "regex": args.regex, "remediation": args.remediation}
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


def _audit_drawer(skill: str, path: Path, findings: list[dict[str, Any]]) -> None:
    rel = str(path.relative_to(ROOT / "skills"))
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
            missing = [field for field in required if field not in entry]
            if missing:
                findings.append({"skill": skill, "code": "missing-coordinate", "file": rel, "id": entry.get("id"), "fields": missing})


def audit_package(path: Path, design: bool = False) -> list[dict[str, Any]]:
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
    if PRIVATE.search(all_md):
        findings.append({"skill": skill, "code": "private-identifier"})
    raw_regex_lines = [line for line in all_md.splitlines() if "forbidden_regex" in line or "regex-gate" in line]
    if len(raw_regex_lines) > 1:
        findings.append({"skill": skill, "code": "raw-regex-gates-in-markdown"})
    md_rel = f"{skill}/SKILL.md"
    if md_rel not in BUDGET_EXEMPTIONS and len(md.read_text(encoding="utf-8").splitlines()) > (80 if skill in {"my-designer", "design-studio", "visual-design-modernization"} else 180):
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
        if ref.suffix == ".md" and skill in {"skill-contract", "my-designer", "design-studio", "visual-design-modernization"}:
            for block in re.findall(r"```[^\n]*\n(.*?)```", ref_text, re.DOTALL):
                if len(block.splitlines()) > 25:
                    findings.append({"skill": skill, "code": "fenced-code-lines", "file": str(ref)})
                    break
        _audit_drawer(skill, ref, findings)
        corpus = text + "\n" + "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in refs.glob("*.md"))
        corpus += "\n" + "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in (path / "scripts").glob("*.py")) if (path / "scripts").is_dir() else ""
        if ref.name not in corpus and ref.stem not in corpus:
            findings.append({"skill": skill, "code": "orphan-reference", "file": str(ref)})
    return findings


def audit(args: argparse.Namespace) -> int:
    packages = [ROOT / "skills" / args.skill] if args.skill else [p for root in package_roots() if root.is_dir() for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").exists()]
    findings: list[dict[str, Any]] = []
    names: dict[str, list[str]] = {}
    for package in packages:
        names.setdefault(package.name, []).append(str(package))
        findings.extend(audit_package(package))
    findings.extend({"code": "duplicate-skill", "name": name, "paths": paths} for name, paths in names.items() if len(paths) > 1)
    result = {"ok": not findings, "skills": len(packages), "findings": findings}
    print(json.dumps(result, indent=2))
    return 1 if findings and args.strict else 0


def tokens(text: str) -> list[str]:
    found: list[str] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group(0).lower()
        if token not in STOPWORDS:
            found.append(token)
    return found


def overlap_ratio(prompt: str, description: str) -> float:
    prompt_tokens = set(tokens(prompt))
    if not prompt_tokens:
        return 0.0
    return len(prompt_tokens & set(tokens(description))) / len(prompt_tokens)


def parse_description(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing frontmatter")
    end = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if end is None:
        raise ValueError("missing frontmatter close")
    for line in lines[1:end]:
        if not re.match(r"description\s*:", line):
            continue
        raw = line.split(":", 1)[1].strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] == '"':
            return str(json.loads(raw))
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] == "'":
            return raw[1:-1]
        return raw
    raise ValueError("missing description")


def replace_description(text: str, description: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing frontmatter")
    end = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if end is None:
        raise ValueError("missing frontmatter close")
    quoted = json.dumps(description, ensure_ascii=False)
    for index in range(1, end):
        if re.match(r"description\s*:", lines[index]):
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = f"description: {quoted}{newline}"
            return "".join(lines)
    lines.insert(1, f"description: {quoted}\n")
    return "".join(lines)


def score_evals(description: str, evals: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for item in evals:
        prompt = str(item["prompt"])
        should_trigger = bool(item["should_trigger"])
        ratio = overlap_ratio(prompt, description)
        triggered = ratio >= threshold
        cases.append({
            "id": item.get("id"),
            "prompt": prompt,
            "should_trigger": should_trigger,
            "ratio": round(ratio, 4),
            "triggered": triggered,
            "pass": triggered == should_trigger,
        })
    return {
        "description": description,
        "passed": sum(1 for case in cases if case["pass"]),
        "total": len(cases),
        "cases": cases,
    }


def _with_cues(description: str, cues: list[str]) -> str:
    if not cues:
        return description
    base = description.rstrip().rstrip(".")
    proposed = f"{base}. Use when: {', '.join(cues)}."
    return proposed if len(proposed) <= DESCRIPTION_CHAR_CAP else description


def propose_description(description: str, evals: list[dict[str, Any]], threshold: float) -> str:
    baseline = score_evals(description, evals, threshold)
    if baseline["passed"] == baseline["total"]:
        return description
    negative_tokens = {token for item in evals if not item.get("should_trigger") for token in tokens(str(item["prompt"]))}
    present = set(tokens(description))
    cues: list[str] = []
    base_neg_fail = sum(1 for case in baseline["cases"] if not case["should_trigger"] and not case["pass"])
    for case in baseline["cases"]:
        if case["should_trigger"] and case["pass"]:
            continue
        if not case["should_trigger"]:
            continue
        for token in tokens(case["prompt"]):
            if token in present or token in negative_tokens or token in cues:
                continue
            trial = _with_cues(description, cues + [token])
            if trial == description:
                continue
            trial_score = score_evals(trial, evals, threshold)
            trial_neg_fail = sum(1 for row in trial_score["cases"] if not row["should_trigger"] and not row["pass"])
            if trial_neg_fail > base_neg_fail:
                continue
            cues.append(token)
            present.add(token)
            if len(cues) >= DESCRIPTION_CUE_CAP:
                break
        if len(cues) >= DESCRIPTION_CUE_CAP:
            break
    proposed = _with_cues(description, cues)
    scored = score_evals(proposed, evals, threshold)
    return proposed if scored["passed"] > baseline["passed"] else description


def run_description_eval(
    skill: str,
    eval_file: str | None = None,
    candidate: str | None = None,
    apply: bool = False,
    threshold: float | None = None,
) -> tuple[int, dict[str, Any]]:
    package = ROOT / "skills" / skill
    markdown = package / "SKILL.md"
    if not markdown.is_file():
        raise SystemExit(f"skill not found: {skill}")
    eval_path = Path(eval_file) if eval_file else package / "evals" / "description-eval.json"
    if not eval_path.is_file():
        return 2, {"ok": False, "error": "missing-eval-file", "path": str(eval_path)}
    doc = json.loads(eval_path.read_text(encoding="utf-8"))
    evals = doc.get("evals")
    if not isinstance(evals, list) or not evals:
        return 2, {"ok": False, "error": "empty-evals", "path": str(eval_path)}
    gate = DEFAULT_TRIGGER_THRESHOLD if threshold is None else threshold
    if threshold is None and isinstance(doc.get("threshold"), (int, float)):
        gate = float(doc["threshold"])
    original = markdown.read_text(encoding="utf-8")
    current = parse_description(original)
    baseline = score_evals(current, evals, gate)
    proposed_text = candidate if candidate is not None else propose_description(current, evals, gate)
    proposed = score_evals(proposed_text, evals, gate)
    improved = proposed["passed"] > baseline["passed"]
    result: dict[str, Any] = {
        "ok": proposed["passed"] == proposed["total"],
        "skill": skill,
        "eval_file": str(eval_path),
        "threshold": gate,
        "baseline": baseline,
        "proposed": proposed,
        "improved": improved,
        "applied": False,
        "rolled_back": False,
        "audit": None,
    }
    if not apply:
        result["ok"] = baseline["passed"] == baseline["total"]
        return (0 if result["ok"] else 1), result
    if proposed_text == current:
        result["apply_skipped"] = "unchanged"
        result["ok"] = baseline["passed"] == baseline["total"]
        return (0 if result["ok"] else 1), result
    if proposed["passed"] < baseline["passed"]:
        result["apply_skipped"] = "score-regressed"
        result["ok"] = False
        return 1, result
    if candidate is None and not improved:
        result["apply_skipped"] = "not-improved"
        return 1, result
    markdown.write_text(replace_description(original, proposed_text), encoding="utf-8")
    findings = audit_package(package)
    result["audit"] = {"ok": not findings, "findings": findings}
    if findings:
        markdown.write_text(original, encoding="utf-8")
        result["applied"] = False
        result["rolled_back"] = True
        result["ok"] = False
        return 1, result
    result["applied"] = True
    result["ok"] = proposed["passed"] == proposed["total"]
    return (0 if result["ok"] else 1), result


def description_eval(args: argparse.Namespace) -> int:
    code, result = run_description_eval(
        skill=args.skill,
        eval_file=args.eval_file,
        candidate=args.candidate,
        apply=args.apply,
        threshold=args.threshold,
    )
    print(json.dumps(result, indent=2))
    return code


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.add_argument("--skill", required=True); init.add_argument("--category", required=True); init.add_argument("--design", action="store_true")
    sink = sub.add_parser("sink-rule"); sink.add_argument("--skill", required=True); sink.add_argument("--kind", choices=["regex-gate", "case-card", "mental-model"], required=True); sink.add_argument("--id"); sink.add_argument("--target-file"); sink.add_argument("--replace-row"); sink.add_argument("--section"); sink.add_argument("--value", default=""); sink.add_argument("--platforms", default="desktop,mobile,web"); sink.add_argument("--dimensions", default=""); sink.add_argument("--containers", default=""); sink.add_argument("--title", default=""); sink.add_argument("--severity", default="Medium"); sink.add_argument("--regex", default=""); sink.add_argument("--remediation", default="")
    audit_parser = sub.add_parser("audit"); group = audit_parser.add_mutually_exclusive_group(required=True); group.add_argument("--all", action="store_true"); group.add_argument("--skill"); audit_parser.add_argument("--json", action="store_true"); audit_parser.add_argument("--strict", action="store_true")
    desc = sub.add_parser("description-eval")
    desc.add_argument("--skill", required=True)
    desc.add_argument("--eval-file")
    desc.add_argument("--candidate")
    desc.add_argument("--apply", action="store_true")
    desc.add_argument("--threshold", type=float, default=None)
    return parser


def main() -> int:
    args = parser().parse_args()
    return {"init": init_skill, "sink-rule": sink_rule, "audit": audit, "description-eval": description_eval}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
