#!/usr/bin/env python3
"""Deterministic bidirectional spec/code drift patrol (Loop 4).

The scanner reads repository metadata and tracked ``main`` snapshots only.  It
never imports or executes anything found in a specification, and Jev receives
summaries rather than source contents.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence

SCRIPT_PATH = Path(__file__).resolve()
SKILLS_ROOT = SCRIPT_PATH.parents[2]
TEAM_LEAD_SCRIPTS = SKILLS_ROOT / "team-lead" / "scripts"
JEV_PATH = TEAM_LEAD_SCRIPTS / "jev_client.py"
ISSUE_BRIEF_PATH = SKILLS_ROOT / "issue-brief" / "scripts" / "verify_issue_format.py"
ARCH_AUDIT_PATH = SKILLS_ROOT / "ios-architecture-testing" / "scripts" / "audit_arch_testing.py"
LOOP_ID = "spec-code-drift-patrol"
DEFAULT_REPOS = ("Financial", "VoxPocket", "VitalStride", "AIDash")
GIT_TIMEOUT_SEC = 3.0
MAX_TRACKED_PATHS = 20_000
MAX_TEXT_BLOB_BYTES = 2_000_000
TEXT_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".css", ".csv", ".go", ".h", ".hpp", ".html", ".java", ".js", ".json",
    ".jsx", ".kt", ".md", ".m", ".mm", ".py", ".rb", ".rs", ".sh", ".sql", ".swift", ".tds",
    ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
})
PATH_RE = re.compile(r"(?<![A-Za-z0-9_./-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_-]+)?")
BACKTICK_RE = re.compile(r"`([^`]+)`")
REPO_HEADER_RE = re.compile(
    r"(?im)^\s*(?:[-*+]\s*)?(?:>\s*)?(?:\*\*)?"
    r"(?:repository|repo|repos|target\s+repos?(?:itory)?|目标仓库)"
    r"(?:\*\*)?\s*[:：]\s*([^\n#]+)"
)
REPO_SLUGS = (("fin-", "Financial"), ("vox-", "VoxPocket"), ("vital-", "VitalStride"), ("vs-", "VitalStride"), ("aidash-", "AIDash"))
KNOWN_PATH_ROOTS = frozenset({"Apps", "Features", "Models", "Packages", "Resources", "Sources", "Tests", "Views", "aidata", "docs", "scripts", "src", "test", "tests"})
SEVERITY_HIGH = {"AT-011", "missing_contract_test", "missing_negative_assertion"}


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _parse_repos(raw: str | Sequence[str] | None) -> tuple[str, ...]:
    if raw is None:
        return DEFAULT_REPOS
    if isinstance(raw, str):
        return tuple(x.strip() for x in raw.split(",") if x.strip()) or DEFAULT_REPOS
    return tuple(str(x).strip() for x in raw if str(x).strip()) or DEFAULT_REPOS


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    path = Path(str(value)).expanduser()
    raw = path.read_text(encoding="utf-8") if path.is_file() else str(value)
    return json.loads(raw)


def _run_git(
    repo: Path,
    args: Sequence[str],
    *,
    text: bool = True,
    timeout: float = GIT_TIMEOUT_SEC,
) -> subprocess.CompletedProcess[Any]:
    command = ["git", "-C", str(repo), *args]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=text,
            check=False,
            timeout=timeout,
            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        )
    except subprocess.TimeoutExpired:
        empty: str | bytes = "" if text else b""
        return subprocess.CompletedProcess(command, 124, empty, "git command timed out")
    except OSError as exc:
        empty = "" if text else b""
        return subprocess.CompletedProcess(command, 127, empty, str(exc))


def _main_ref(repo: Path) -> str | None:
    for ref in ("main", "origin/main", "master", "origin/master"):
        if _run_git(repo, ["rev-parse", "--verify", ref]).returncode == 0:
            return ref
    return None


def _is_text_path(name: str) -> bool:
    suffix = Path(name).suffix.lower()
    return not suffix or suffix in TEXT_SUFFIXES


def _tracked_snapshot(repo: Path) -> tuple[str | None, dict[str, str], dict[str, int]]:
    """Return tracked main paths and bounded UTF-8 text, tolerating binary blobs."""
    ref = _main_ref(repo)
    listed = _run_git(repo, ["ls-tree", "-r", "--name-only", ref] if ref else ["ls-files"])
    raw_names = listed.stdout.splitlines() if listed.returncode == 0 else []
    names = [str(x).strip() for x in raw_names if str(x).strip()]
    files: dict[str, str] = {name: "" for name in names}
    for name in names[:MAX_TRACKED_PATHS]:
        if not _is_text_path(name):
            continue
        shown = _run_git(repo, ["show", f"{ref}:{name}"] if ref else ["show", ":" + name], text=False)
        if shown.returncode != 0 or not isinstance(shown.stdout, (bytes, bytearray)):
            continue
        blob = bytes(shown.stdout)
        if len(blob) > MAX_TEXT_BLOB_BYTES:
            continue
        files[name] = blob.decode("utf-8", errors="replace")
    return ref, files, {name: text.count("\n") + 1 for name, text in files.items()}


def _line_number(text: str, needle: str) -> int:
    pos = text.find(needle)
    return text.count("\n", 0, max(0, pos)) + 1 if pos >= 0 else 1


def _declared_repositories(text: str, repo_names: Sequence[str]) -> tuple[set[str], bool]:
    matches = list(REPO_HEADER_RE.finditer(text))
    if not matches:
        return set(), False
    declared: set[str] = set()
    for match in matches:
        value = match.group(1).replace("\\", "/")
        for name in repo_names:
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", value, re.I) or f"/{name}/" in f"/{value}/":
                declared.add(name)
    return declared, True


def _doc_records(handoff_root: Path, repo_names: Sequence[str]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {name: [] for name in repo_names}
    unsupported: list[dict[str, Any]] = []
    if not handoff_root.is_dir():
        return records, [{"kind": "missing_handoff_root", "path": str(handoff_root), "reason": "handoff root is not a directory"}]
    for goal in sorted(handoff_root.iterdir()):
        if not goal.is_dir() or goal.name.startswith("_"):
            continue
        ledger = goal / "ledger.md"
        ledger_text = ledger.read_text(encoding="utf-8", errors="replace") if ledger.is_file() else ""
        if re.search(r"\b(?:cancelled|canceled|abandoned|superseded)\b", ledger_text, re.I) or re.search(r"\bEVT=GOAL_DONE\b", ledger_text):
            unsupported.append({"kind": "historical_handoff_skipped", "path": str(goal), "reason": "ledger marks completed or superseded goal"})
            continue
        docs: list[dict[str, Any]] = []
        for filename in ("spec.md", "design.md", "CONTEXT.md"):
            path = goal / filename
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                unsupported.append({"kind": "unreadable_spec", "path": str(path), "reason": str(exc)})
                continue
            docs.append({"path": path, "text": text, "filename": filename})
        if not docs:
            continue
        joined = "\n".join(d["text"] for d in docs)
        candidates, explicit = _declared_repositories(joined, repo_names)
        if not explicit:
            if goal.name in repo_names:
                candidates.add(goal.name)
            else:
                for prefix, name in REPO_SLUGS:
                    if goal.name.lower().startswith(prefix) and name in repo_names:
                        candidates.add(name)
                        break
        if not candidates:
            reason = "declared repository is external or not in scan set" if explicit else "no unique repository association"
            unsupported.append({"kind": "unassociated_handoff", "path": str(goal), "reason": reason})
            continue
        for repo in sorted(candidates):
            records[repo].extend(docs)
    return records, unsupported


def _candidate_paths(text: str, *, repo: Path, tracked: Mapping[str, str]) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    seen: set[str] = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        values = BACKTICK_RE.findall(line) + PATH_RE.findall(line)
        for raw in values:
            value = raw.strip().strip(".,:;()[]{}<>\"'").replace("\\", "/")
            value = re.sub(r":\d+(?:-\d+)?$", "", value)
            if value.startswith("~/Development/"):
                value = value[len("~/Development/"):]
                if not value.startswith(repo.name + "/"):
                    continue
                value = value[len(repo.name) + 1:]
            if not value or "*" in value or value.startswith(("http://", "https://", "--", "/api/", "/howto/")) or " " in value:
                continue
            if value.startswith("/"):
                marker = f"/{repo.name}/"
                if marker not in value:
                    continue
                value = value.split(marker, 1)[1]
            elif f"/{repo.name}/" in value:
                value = value.split(f"/{repo.name}/", 1)[1]
            if value.startswith(("skills/", "shared-ci/")) and repo.name != "user-skills":
                continue
            parts = value.lstrip("./").split("/")
            if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
                continue
            if parts[0] not in KNOWN_PATH_ROOTS and parts[0] not in {Path(name).parts[0] for name in tracked if Path(name).parts}:
                continue
            if value.endswith("/") or ("." not in parts[-1] and value not in tracked and parts[0] not in KNOWN_PATH_ROOTS):
                continue
            if value in {"spec.md", "design.md", "CONTEXT.md", "README.md"}:
                continue
            if value not in seen:
                seen.add(value)
                found.append((value, line_no))
    return found


def _resolve_reference(repo: Path, value: str, tracked: Mapping[str, str]) -> str | None:
    normalized = value.replace("\\", "/").lstrip("./")
    if normalized.startswith(repo.name + "/"):
        normalized = normalized[len(repo.name) + 1 :]
    if normalized in tracked:
        return normalized
    if any(name.startswith(normalized.rstrip("/") + "/") for name in tracked):
        return normalized
    return None


def _finding(repo: str, direction: str, code: str, title: str, *, file: str, line: int, evidence: str, remediation: str, action_type: str) -> dict[str, Any]:
    stable = f"{repo}|{direction}|{code}|{file}|{line}|{evidence}"
    return {
        "id": f"{direction[:3].upper()}-{hashlib.sha256(stable.encode()).hexdigest()[:10]}",
        "repo": repo,
        "direction": direction,
        "code": code,
        "title": title,
        "file": file,
        "line": int(line or 1),
        "evidence": evidence[:400],
        "remediation": remediation,
        "action_type": action_type,
    }


def _scan_repo(repo_name: str, repo: Path, docs: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    stale: list[dict[str, Any]] = []
    compliance: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    ref, tracked, _ = _tracked_snapshot(repo)
    if not tracked:
        unsupported.append({"kind": "missing_main_snapshot", "repo": repo_name, "path": str(repo), "reason": "no readable git main snapshot"})
        return stale, compliance, unsupported
    source_text = "\n".join(tracked.values())
    test_files = {name: text for name, text in tracked.items() if re.search(r"(?:test|tests|spec)", name, re.I)}
    test_text = "\n".join(test_files.values())
    docs_to_scan = list(docs)
    for root_doc in ("CONTEXT.md", "spec.md", "design.md"):
        if root_doc in tracked and not any(Path(str(item["path"])).name == root_doc for item in docs_to_scan):
            docs_to_scan.append({"path": repo / root_doc, "text": tracked[root_doc], "filename": root_doc})
    for name, text in tracked.items():
        if name.startswith("docs/adr/") and name.endswith(".md") and not any(str(item["path"]).endswith(name) for item in docs_to_scan):
            docs_to_scan.append({"path": repo / name, "text": text, "filename": name})
    for doc in docs_to_scan:
        path = Path(str(doc["path"]))
        text = str(doc["text"])
        for value, line in _candidate_paths(text, repo=repo, tracked=tracked):
            if value.startswith(("http", "python", "git", "bash")):
                continue
            if _resolve_reference(repo, value, tracked) is None:
                stale.append(_finding(repo_name, "spec_staleness", "missing_referenced_path", "Specification references a path absent from the main snapshot", file=str(path), line=line, evidence=value, remediation="Update the specification to the renamed path or restore the documented module.", action_type="SYNC_SPEC_DOC"))
        lines = text.splitlines()
        in_deprecated = False
        for idx, raw in enumerate(lines, 1):
            if re.search(r"deprecated test clauses? to (?:remove|rewrite)", raw, re.I):
                in_deprecated = True
                continue
            if in_deprecated and re.match(r"^\s*#{1,6}\s+", raw):
                in_deprecated = False
            if in_deprecated and raw.strip() and not raw.strip().startswith(("<!--", "---")):
                stale.append(_finding(repo_name, "spec_staleness", "deprecated_test_clause", "Deprecated test clause remains in the handoff", file=str(path), line=idx, evidence=raw.strip(), remediation="Remove or rewrite the clause after confirming the migrated main test contract.", action_type="SYNC_SPEC_DOC"))
                break
        for idx, raw in enumerate(lines, 1):
            if not re.search(r"\b(route|model|dependency|depends on)\b\s*[:：]", raw, re.I):
                continue
            declared = re.split(r"[:：]", raw, maxsplit=1)[-1].strip().strip("` ")
            if not declared or len(declared) > 160:
                continue
            path_hit = _resolve_reference(repo, declared, tracked)
            symbol = Path(declared).stem if "/" in declared else declared.split()[0]
            if path_hit is None and not re.search(rf"\b{re.escape(symbol)}\b", source_text):
                stale.append(_finding(repo_name, "spec_staleness", "stale_route_or_model", "Route or model declaration is absent from the main snapshot", file=str(path), line=idx, evidence=raw.strip(), remediation="Synchronize the route/model declaration with the tracked implementation.", action_type="SYNC_SPEC_DOC"))
        contract_lines = [(idx, raw) for idx, raw in enumerate(lines, 1) if re.search(r"contract\s+tests?|tests?\s+contract|regression\s+gate", raw, re.I)]
        for idx, raw in contract_lines:
            paths = _candidate_paths(raw, repo=repo, tracked=tracked)
            if paths and all(_resolve_reference(repo, value, tracked) is None for value, _ in paths):
                compliance.append(_finding(repo_name, "code_compliance", "missing_contract_test", "Declared contract test is absent from the main snapshot", file=str(path), line=idx, evidence=raw.strip(), remediation="Add the named contract test to the main test tree or correct the specification.", action_type="FIX_CODE_BUG"))
            elif not paths and re.search(r"(?:required|must|missing|add)", raw, re.I) and not test_files:
                compliance.append(_finding(repo_name, "code_compliance", "missing_contract_test", "Specification requires contract tests but no tracked test file exists", file=str(path), line=idx, evidence=raw.strip(), remediation="Add a tracked contract test covering the declared behavior.", action_type="FIX_CODE_BUG"))
        if re.search(r"negative\s+assert|negative\s+path|invalid\s+input|permission[- ]denied|rejection\s+case", text, re.I):
            if not test_text or not re.search(r"assert(?:Raises|Throws|False|Not)|#expect\s*\(\s*throws|negative|invalid|reject|error", test_text, re.I):
                line = next((idx for idx, raw in enumerate(lines, 1) if re.search(r"negative\s+assert|negative\s+path|invalid\s+input|permission[- ]denied|rejection\s+case", raw, re.I)), 1)
                compliance.append(_finding(repo_name, "code_compliance", "missing_negative_assertion", "Declared negative behavior has no tracked negative assertion", file=str(path), line=line, evidence=lines[line - 1].strip(), remediation="Add an assertion for rejection, invalid input, permission denial, or the documented error contract.", action_type="FIX_CODE_BUG"))
        if re.search(r"geometry|screenshot|pixel|design[_ -]?contract|clipping", text, re.I):
            if not re.search(r"geometry|screenshot|pixel|design[_ -]?contract|clipping", test_text, re.I):
                line = next((idx for idx, raw in enumerate(lines, 1) if re.search(r"geometry|screenshot|pixel|design[_ -]?contract|clipping", raw, re.I)), 1)
                compliance.append(_finding(repo_name, "code_compliance", "missing_ui_geometry_contract", "UI geometry or screenshot gate is declared but absent from tracked tests", file=str(path), line=line, evidence=lines[line - 1].strip(), remediation="Add a deterministic geometry/screenshot contract check and keep it in the main test tree.", action_type="FIX_CODE_BUG"))
    swift_files = {name: text for name, text in tracked.items() if name.endswith(".swift")}
    if swift_files and any(re.search(r"macOS|iOS|Navigation|bottom.?dock|root.?shell|AT-011", str(d["text"]), re.I) for d in docs_to_scan):
        if ARCH_AUDIT_PATH.is_file():
            try:
                auditor = _load_module("spec_drift_arch_audit", ARCH_AUDIT_PATH)
                for name, text in swift_files.items():
                    for issue in auditor.audit_source(text, name):
                        if issue.get("rule_id") == "AT-011":
                            compliance.append(_finding(repo_name, "code_compliance", "AT-011", "macOS and iOS navigation are not divergent in the main snapshot", file=name, line=int(issue.get("line") or 1), evidence=str(issue.get("title") or "AT-011"), remediation=str(issue.get("remediation") or "Split macOS and iOS navigation chrome."), action_type="FIX_CODE_BUG"))
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                unsupported.append({"kind": "architecture_audit_unavailable", "repo": repo_name, "reason": str(exc)})
        else:
            unsupported.append({"kind": "architecture_audit_unavailable", "repo": repo_name, "reason": "audit_arch_testing.py is not present"})
    return stale, compliance, unsupported


def scan(*, dev_root: Path | str | None = None, handoff_root: Path | str | None = None, repos: Sequence[str] | str | None = None, fixture_root: Path | str | None = None) -> dict[str, Any]:
    if fixture_root is not None:
        base = Path(fixture_root).expanduser()
        dev = Path(dev_root).expanduser() if dev_root else base
        handoff = Path(handoff_root).expanduser() if handoff_root else base / "handoff"
    else:
        dev = Path(dev_root or (Path.home() / "Development")).expanduser()
        handoff = Path(handoff_root or (dev / "handoff")).expanduser()
    repo_names = _parse_repos(repos)
    docs_by_repo, unsupported = _doc_records(handoff, repo_names)
    stale: list[dict[str, Any]] = []
    compliance: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for name in repo_names:
        repo = dev / name
        if not repo.is_dir():
            missing.append({"repo": name, "path": str(repo), "reason": "repository directory is missing"})
            continue
        old_stale, old_compliance, old_unsupported = _scan_repo(name, repo, docs_by_repo.get(name, ()))
        stale.extend(old_stale)
        compliance.extend(old_compliance)
        unsupported.extend(old_unsupported)
    findings = stale + compliance
    fingerprint_rows = [
        (f["id"], f["repo"], f["direction"], f["code"], f["file"], f["line"], f["evidence"])
        for f in findings
    ]
    fingerprint = hashlib.sha256(json.dumps(fingerprint_rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return {
        "ok": True,
        "loop_id": LOOP_ID,
        "dev_root": str(dev),
        "handoff_root": str(handoff),
        "repos": list(repo_names),
        "spec_stale_findings": stale,
        "code_compliance_findings": compliance,
        "findings": findings,
        "missing_repositories": missing,
        "unsupported": unsupported,
        "scan_status": "degraded" if missing or unsupported else "clean" if not findings else "drift",
        "clean": not findings and not missing and not unsupported,
        "has_delta": bool(findings),
        "delta_count": len(findings),
        "watermark": f"{LOOP_ID}:{fingerprint}",
        "fingerprint": fingerprint,
    }


def _prob(value: Any, field: str = "noul") -> float | None:
    if isinstance(value, Mapping):
        value = value.get(field, value.get("value"))
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and 0 <= result <= 1 else None


def _fallback_for(finding: Mapping[str, Any]) -> tuple[bool, str, str]:
    action = str(finding.get("action_type") or "FIX_CODE_BUG")
    return True, "SPEC_OUTDATED_SYNC_DOC" if action == "SYNC_SPEC_DOC" else "CODE_VIOLATES_SPEC_BUG", action


def _mock_for(mock: Any, finding: Mapping[str, Any], index: int) -> Any:
    if isinstance(mock, list):
        return mock[index] if index < len(mock) else None
    if not isinstance(mock, Mapping) or "answers" in mock:
        return mock
    for key in (str(finding.get("id")), str(index + 1), str(finding.get("code")), str(finding.get("repo"))):
        if key in mock:
            return mock[key]
    return mock if any(k in mock for k in ("drift_classification", "severity_confidence", "confidence")) else None


def _mock_entries(mock: Any, findings: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if isinstance(mock, Mapping) and ("answers" in mock or any(key in mock for key in ("drift_classification", "classification", "choice"))):
        if len(findings) != 1:
            raise ValueError("mock_jev_json requires one keyed answer per finding")
        entries = [mock]
    elif isinstance(mock, list):
        if len(mock) != len(findings):
            raise ValueError("mock_jev_json list must contain exactly one answer per finding")
        entries = mock
    elif isinstance(mock, Mapping):
        expected = [str(finding.get("id")) for finding in findings]
        if set(mock) != set(expected):
            raise ValueError("mock_jev_json keys must exactly match finding ids")
        entries = [mock[key] for key in expected]
    else:
        raise ValueError("mock_jev_json must be a JSON object or list")
    validated: list[Mapping[str, Any]] = []
    choices = {"SPEC_OUTDATED_SYNC_DOC", "CODE_VIOLATES_SPEC_BUG", "BENIGN_NOISE"}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("each Jev mock answer must be an object")
        answers = entry.get("answers", entry)
        if not isinstance(answers, Mapping):
            raise ValueError("each Jev mock answer requires an answers object")
        raw_choice = answers.get("drift_classification", answers.get("classification", answers.get("choice")))
        if isinstance(raw_choice, Mapping):
            raw_choice = raw_choice.get("choice")
        confidence = _prob(answers.get("severity_confidence"))
        if confidence is None:
            confidence = _prob(answers.get("severity", answers.get("confidence", answers.get("noul"))))
        if str(raw_choice) not in choices or confidence is None:
            raise ValueError("each Jev mock answer requires a valid choice and companion noul confidence")
        validated.append({"choice": str(raw_choice), "confidence": confidence})
    return validated


def triage_findings(findings: Sequence[Mapping[str, Any]], *, mock_jev_json: Any = None, jev_fn: Callable[..., Any] | None = None) -> dict[str, Any]:
    if not findings:
        return {"actionable": False, "actionable_findings": [], "decisions": [], "decision_source": "layer0_clean"}
    mock_supplied = mock_jev_json is not None
    supplied_mock: Any = None
    mock_answers: list[Mapping[str, Any]] | None = None
    if mock_supplied:
        try:
            supplied_mock = _json_value(mock_jev_json)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("mock_jev_json is invalid JSON") from exc
        mock_answers = _mock_entries(supplied_mock, findings)
    questions = {
        "drift_classification": {"type": "choice", "instructions": "Classify this deterministic drift finding.", "criteria": {"SPEC_OUTDATED_SYNC_DOC": "The handoff is stale and should be synchronized.", "CODE_VIOLATES_SPEC_BUG": "Main code or tests violate the declared contract.", "BENIGN_NOISE": "This is harmless noise and should not wake delivery."}},
        "severity_confidence": {"type": "noul", "instructions": "Confidence from 0.0 to 1.0 in the classification."},
    }
    decide = jev_fn or _load_module("spec_drift_jev", JEV_PATH).jev_decide
    decisions: list[dict[str, Any]] = []
    actionable: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        fallback_actionable, fallback_class, fallback_action = _fallback_for(finding)
        source = "deterministic_fallback_unavailable"
        choice = fallback_class
        confidence: float | None = None
        if mock_answers is not None:
            choice = str(mock_answers[index]["choice"])
            confidence = float(mock_answers[index]["confidence"])
            source = "mock_jev"
        else:
            response = decide(state={"finding": {k: finding.get(k) for k in ("id", "repo", "direction", "code", "file", "line")}}, questions=questions, agent_role="team-lead", goal_id=f"{LOOP_ID}-{finding.get('id')}", timeout_sec=1.5, falcon_only=True, mock_response=None)
            if isinstance(response, Mapping) and not response.get("fallback_reason"):
                answers = response.get("answers", response)
                if isinstance(answers, Mapping):
                    raw_choice = answers.get("drift_classification", answers.get("classification", answers.get("choice")))
                    if isinstance(raw_choice, Mapping):
                        raw_choice = raw_choice.get("choice")
                    confidence = _prob(answers.get("severity_confidence"))
                    if confidence is None:
                        confidence = _prob(answers.get("severity", answers.get("confidence", answers.get("noul"))))
                    if str(raw_choice) in {"SPEC_OUTDATED_SYNC_DOC", "CODE_VIOLATES_SPEC_BUG", "BENIGN_NOISE"} and confidence is not None:
                        choice = str(raw_choice)
                        source = "jev_high_confidence" if confidence > 0.70 else "jev_grey_zone_fallback" if confidence >= 0.30 else "jev_low_confidence_reject"
        if choice == "BENIGN_NOISE":
            is_actionable = False if mock_answers is not None or (confidence is not None and confidence >= 0.40) else fallback_actionable
            action = "NONE" if not is_actionable else fallback_action
        elif confidence is not None and confidence < 0.30:
            is_actionable, choice, action = False, "BENIGN_NOISE", "NONE"
        elif confidence is not None and confidence > 0.70:
            is_actionable = True
            action = "SYNC_SPEC_DOC" if choice == "SPEC_OUTDATED_SYNC_DOC" else "FIX_CODE_BUG"
        else:
            is_actionable, choice, action = fallback_actionable, fallback_class, fallback_action
            source = "deterministic_fallback_grey" if confidence is not None else source
        decision = {"finding_id": finding.get("id"), "classification": choice, "confidence": confidence, "action_type": action, "actionable": is_actionable, "decision_source": source}
        decisions.append(decision)
        if is_actionable:
            actionable.append({**dict(finding), "classification": choice, "confidence": confidence, "action_type": action})
    return {"actionable": bool(actionable), "actionable_findings": actionable, "decisions": decisions, "decision_source": "mock" if mock_answers is not None else "mixed"}


def _issue_payload(report: Mapping[str, Any], verdict: Mapping[str, Any], *, target_date: date) -> dict[str, Any]:
    issue_brief = _load_module("spec_drift_issue_brief", ISSUE_BRIEF_PATH)
    findings = list(verdict.get("actionable_findings") or [])
    actionable_stale = [finding for finding in findings if finding.get("action_type") == "SYNC_SPEC_DOC"]
    actionable_compliance = [finding for finding in findings if finding.get("action_type") == "FIX_CODE_BUG"]
    items = []
    for finding in findings[:6]:
        verb = "同步规格文档" if finding.get("action_type") == "SYNC_SPEC_DOC" else "修复主线契约实现"
        items.append({"what": f"{verb}（{finding.get('repo')}）", "why": str(finding.get("title") or "消除规格与代码漂移")[:70]})
    if not items:
        items = [{"what": "复核本次规格与代码巡检结果", "why": "保持产品契约与主线实现一致"}]
    summary = f"检测到 {len(findings)} 项规格与主线实现漂移，已按文档同步或代码修复分流。" if findings else "本次规格与主线实现巡检未发现可交付漂移。"
    actionable_ids = {str(finding.get("id")) for finding in findings}
    markdown = issue_brief.render_dual_zone_issue(tldr=summary, items=items, cursor=f"{LOOP_ID}-{report.get('fingerprint')}", ai_payload={"loop_id": LOOP_ID, "watermark": report.get("watermark"), "spec_stale_findings": actionable_stale, "code_compliance_findings": actionable_compliance, "triage": [decision for decision in verdict.get("decisions", []) if str(decision.get("finding_id")) in actionable_ids], "missing_repositories": report.get("missing_repositories"), "unsupported": report.get("unsupported")})
    checked = issue_brief.verify_dual_zone_issue(markdown)
    if not checked.get("ok"):
        raise ValueError(f"issue-brief verification failed: {checked.get('violations')}")
    date_str = target_date.isoformat()
    return {"title": f"[P3 / Priority 3][Spec-Code Drift] {date_str} 规格与代码一致性巡检清单", "priority": "medium", "status": "todo", "assignee_email": "lee@multica.local", "cursor": f"{LOOP_ID}-{report.get('fingerprint')}", "markdown": markdown, "dual_zone_ok": True}


def loop_check(*, dev_root: Path | str | None = None, handoff_root: Path | str | None = None, repos: Sequence[str] | str | None = None, fixture_root: Path | str | None = None, mock_jev_json: Any = None, jev_fn: Callable[..., Any] | None = None, target_date: date | None = None, dry_run: bool = False) -> dict[str, Any]:
    report = scan(dev_root=dev_root, handoff_root=handoff_root, repos=repos, fixture_root=fixture_root)
    verdict = triage_findings(report["findings"], mock_jev_json=mock_jev_json, jev_fn=jev_fn)
    severity = "high" if any(str(f.get("code")) in SEVERITY_HIGH for f in verdict.get("actionable_findings", [])) else "medium" if verdict.get("actionable_findings") else "low"
    date_str = (target_date or date.today()).isoformat()
    result = {
        **report,
        "date": date_str,
        "cursor": f"{LOOP_ID}-{report.get('fingerprint')}",
        "title": f"[P3 / Priority 3][Spec-Code Drift] {date_str} 规格与代码一致性巡检清单",
        "actionable": bool(verdict["actionable"]),
        "severity": severity,
        "jev": verdict,
        "summary": (
            f"巡检不完整：存在 {len(report['missing_repositories']) + len(report['unsupported'])} 个仓库或工具诊断，未将缺口判定为干净。"
            if report.get("scan_status") == "degraded"
            else f"发现 {len(report['findings'])} 项规格与代码漂移。" if report["findings"] else "未发现规格与代码漂移。"
        ),
        "items": [],
        "dual_zone_markdown": "",
        "alert_issue": None,
    }
    result["items"] = [
        {"what": "同步规格文档" if item.get("action_type") == "SYNC_SPEC_DOC" else "修复主线契约实现", "why": str(item.get("title") or "消除规格与代码漂移")[:70]}
        for item in verdict.get("actionable_findings", [])[:6]
    ]
    if verdict["actionable"]:
        issue = _issue_payload(report, verdict, target_date=target_date or date.today())
        result["dual_zone_markdown"] = issue["markdown"]
        result["alert_issue"] = issue
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Loop 4 deterministic spec/code drift patrol")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "loop-check"):
        p = sub.add_parser(name)
        p.add_argument("--dev-root", type=Path, default=None)
        p.add_argument("--handoff-root", type=Path, default=None)
        p.add_argument("--fixture-root", type=Path, default=None)
        p.add_argument("--repos", default=None, help="comma-separated repository names")
        p.add_argument("--mock-jev-json", default=None)
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--json", action="store_true", help="emit JSON (the CLI is JSON by default)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "scan":
        result = scan(dev_root=args.dev_root, handoff_root=args.handoff_root, repos=args.repos, fixture_root=args.fixture_root)
    else:
        result = loop_check(dev_root=args.dev_root, handoff_root=args.handoff_root, repos=args.repos, fixture_root=args.fixture_root, mock_jev_json=args.mock_jev_json, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
