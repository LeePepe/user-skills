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
        scripts = package / "scripts"
        if scripts.is_dir():
            for script in scripts.rglob("*.py"):
                if script.is_symlink():
                    continue
                relative = f"skills/{package.name}/scripts/{script.relative_to(scripts).as_posix()}"
                if relative in LEGACY_SCRIPT_LINE_CAPS and not any(item["script"] == relative for item in facts):
                    facts.append({"script": relative, "lines": len(script.read_text(encoding="utf-8", errors="replace").splitlines()), "cap": LEGACY_SCRIPT_LINE_CAPS[relative]})
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


def _thin_wrapper(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return len(text.splitlines()) <= 25 and ("_CANONICAL_PATH" in text or "CANONICAL" in text)


LEGACY_SCRIPT_LINE_CAPS: dict[str, int] = {
    "skills/agent-retro/scripts/agent_retro.py": 2173,
    "skills/asset-screener/scripts/asset_evaluator.py": 1850,
    "skills/code-review/scripts/discover_review_context.py": 1335,
    "skills/codebase-design/scripts/spec_code_drift_patrol.py": 1342,
    "skills/design-studio/scripts/design_catalog.py": 2330,
    "skills/design-studio/scripts/test_design_catalog.py": 1815,
    "skills/knowledge-digest/scripts/digest_vault.py": 3247,
    "skills/linear-board/scripts/backlog_groomer.py": 1521,
    "skills/linear-board/scripts/daily-brief.py": 1430,
    "skills/linear-board/scripts/linear-bot-bridge.py": 1130,
    "skills/linear-board/scripts/root_tracker.py": 2724,
    "skills/my-designer/scripts/design_evidence.py": 1323,
    "skills/my-designer/scripts/verify_design_cases.py": 1901,
    "skills/personal-m2-assistant/scripts/m2-router.py": 7526,
    "skills/repo-kit/scripts/repo_gate_auditor.py": 1192,
    "skills/retro-loop/scripts/multica_skills_autopilot.py": 1634,
    "skills/retro-loop/scripts/retro_story_engine.py": 1225,
    "skills/retro-loop/scripts/trace_backfill_exporter.py": 2094,
    "skills/skill-retro/scripts/skill_retro.py": 9707,
    "skills/team-lead/scripts/autopilot_watchdog.py": 1795,
    "skills/team-lead/scripts/feedback_guard.py": 1395,
    "skills/team-lead/scripts/goal-queue.py": 2078,
    "skills/team-lead/scripts/goal-supervisor.py": 1604,
    "skills/team-lead/scripts/icloud_skills_sync.py": 1586,
    "skills/team-lead/scripts/linear-sync.py": 2828,
    "skills/team-lead/scripts/local_hygiene_patrol.py": 1842,
    "skills/team-lead/scripts/multica_loop_runner.py": 2422,
    "skills/team-lead/scripts/multica_vpn_bridge.py": 1171,
}

def _script_budget_findings(path: Path) -> list[dict[str, Any]]:
    scripts = path / "scripts"
    if not scripts.is_dir():
        return []
    findings: list[dict[str, Any]] = []
    root_kind = "skills" if path.parent.name == "skills" else ".agents/skills"
    for script in scripts.rglob("*.py"):
        if script.is_symlink() or "__pycache__" in script.parts:
            continue
        try:
            lines = len(script.read_text(encoding="utf-8", errors="replace").splitlines())
            relative = f"{root_kind}/{path.name}/scripts/{script.relative_to(scripts).as_posix()}"
        except (OSError, ValueError):
            continue
        cap = LEGACY_SCRIPT_LINE_CAPS.get(relative)
        if cap is not None:
            if lines > cap:
                findings.append({"skill": path.name, "code": "script-monolith-growth", "script": relative, "lines": lines, "cap": cap})
        elif lines > 1000:
            findings.append({"skill": path.name, "code": "script-lines", "script": relative, "lines": lines, "cap": 1000})
    return findings

def audit_package(path: Path, design: bool = False, repo_bound: bool = False, sibling_corpus: str = "") -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = _script_budget_findings(path)
    skill = path.name
    md = path / "SKILL.md"
    if not md.exists():
        return [_enrich_finding({"skill": skill, "code": "missing-skill"})]
    text = md.read_text(encoding="utf-8")
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
    data_defect = path / "scripts" / "data" / "defect-cases" / "index.json"
    if data_defect.is_file():
        _audit_drawer(skill, data_defect, findings, pkg_dir=path)
    return [_enrich_finding(item) for item in findings]


def audit(args: argparse.Namespace, discover_packages=None) -> int:
    repo_arg = _normalize_repo_path(getattr(args, "repo", None))
    skill_names = _csv(getattr(args, "skills", None))
    if getattr(args, "skill", None):
        skill_names = _csv(getattr(args, "skill", None))
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
                if not script.is_symlink() and script.name != "__init__.py":
                    scripts.setdefault(script.name, []).append(script)
        for name, paths in scripts.items():
            real = [path for path in paths if not _thin_wrapper(path)]
            if len(real) >= 2:
                findings.append({"code": "duplicate-script-fork", "script": name, "paths": [str(path) for path in real]})
    findings = [_enrich_finding(item) for item in findings]
    result: dict[str, Any] = {"ok": not findings, "skills": len(selected), "findings": findings}
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
