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
BUDGET_EXEMPTIONS: set[str] = set()
DRAWERS = {
    "defect-cases/index.json": ("active_cases", ("platforms", "dimensions", "containers")),
    "review-smells.json": ("active_smells", ("languages", "layers")),
    "build-cases.json": ("active_cases", ("routes",)),
    "source-tiers.json": ("high_signal_tiers", ("topics", "patterns")),
}


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
    if md_rel not in BUDGET_EXEMPTIONS and len(md.read_text(encoding="utf-8").splitlines()) > (80 if skill in {"my-designer", "design-studio"} else 180):
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
        if ref.suffix == ".md" and skill in {"skill-contract", "my-designer", "design-studio"}:
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


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.add_argument("--skill", required=True); init.add_argument("--category", required=True); init.add_argument("--design", action="store_true")
    sink = sub.add_parser("sink-rule"); sink.add_argument("--skill", required=True); sink.add_argument("--kind", choices=["regex-gate", "case-card", "mental-model"], required=True); sink.add_argument("--id"); sink.add_argument("--target-file"); sink.add_argument("--replace-row"); sink.add_argument("--section"); sink.add_argument("--value", default=""); sink.add_argument("--platforms", default="desktop,mobile,web"); sink.add_argument("--dimensions", default=""); sink.add_argument("--containers", default=""); sink.add_argument("--title", default=""); sink.add_argument("--severity", default="Medium"); sink.add_argument("--regex", default=""); sink.add_argument("--remediation", default="")
    audit_parser = sub.add_parser("audit"); group = audit_parser.add_mutually_exclusive_group(required=True); group.add_argument("--all", action="store_true"); group.add_argument("--skill"); audit_parser.add_argument("--json", action="store_true"); audit_parser.add_argument("--strict", action="store_true")
    return parser


def main() -> int:
    args = parser().parse_args()
    return {"init": init_skill, "sink-rule": sink_rule, "audit": audit}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
