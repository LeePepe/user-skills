#!/usr/bin/env python3
"""UI Task closeout verification helpers for team-lead and m2-dispatcher."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Sequence

PLAYBOOK_GUIDANCE = "skills/team-lead/references/playbook.md"
M2_GUIDANCE = "skills/m2-dispatcher/references/pr-dag-auto-heal.md"

_UI_FILE_SUFFIX_RE = re.compile(r"\.(?:swift|tsx|jsx|vue|svelte)$", re.IGNORECASE)
_UI_PATH_HINT_RE = re.compile(
    r"(?:^|/)(?:views?|screens?|ui|components?|widgets?|previewhost|swiftui)(?:/|$)|view\.swift$",
    re.IGNORECASE,
)
_PNG_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_./~-]+/)?[A-Za-z0-9_.-]+\.png\b",
    re.IGNORECASE,
)
_WHITESPACE_CHECK_RE = re.compile(
    r"(?:check-whitespace-ratio|check-whitespace|blank_center_collapse|"
    r"whitespace[-_\s]*(?:ratio|check|audit|pass|ok|gate)|collapse[-_\s]*check)",
    re.IGNORECASE,
)
_DESIGNER_ROLE_RE = re.compile(
    r"(?:magpie/group/designer|\bgroup/designer\b|\bdesigner\b[^\n]{0,60}\b(?:visual\s+traceback|vision-audit|review|pass)\b|"
    r"\b(?:visual\s+traceback|vision-audit)\b[^\n]{0,60}\bdesigner\b)",
    re.IGNORECASE,
)
_VISUAL_TRACEBACK_RE = re.compile(
    r"(?:visual\s+traceback|vision-audit)",
    re.IGNORECASE,
)
_IMPL_BUG_TOKEN_RE = re.compile(r"\[\s*IMPL_BUG\s*\]", re.IGNORECASE)
_RESOLVED_IMPL_BUG_LINE_RE = re.compile(
    r"(?:"
    r"\b(?:0|zero|no|none|without)\b[^\n]{0,40}\[\s*IMPL_BUG\s*\]"
    r"|无[^\n]{0,20}\[\s*IMPL_BUG\s*\]"
    r"|零[^\n]{0,20}\[\s*IMPL_BUG\s*\]"
    r"|\[\s*IMPL_BUG\s*\][^\n]{0,40}(?:=\s*0|:\s*0|\b0\b|\bnone\b|\bresolved\b|\bfixed\b|\bclosed\b|已修复|已闭环|无未闭环)"
    r"|\b(?:resolved|fixed|closed|cleared)\b[^\n]{0,30}\[\s*IMPL_BUG\s*\]"
    r")",
    re.IGNORECASE,
)
_WHITESPACE_FAIL_LINE_RE = re.compile(
    r"(?:check-whitespace(?:-ratio)?|blank_center_collapse|whitespace[-_\s]*(?:ratio|check))[^\n]{0,60}"
    r"\b(?:fail|failed|violation|collapsed)\b",
    re.IGNORECASE,
)
_M1_DISPATCH_EVIDENCE_RE = re.compile(
    r"(?:herdr-dispatch\.sh|goal-tl(?:-r\d+)?|EVT\s*=\s*(?:DISPATCHED|READY)|\brole[=\s]+team_lead\b)",
    re.IGNORECASE,
)


def infer_handoff_dir(brief_path: Path | None = None, receipt_path: Path | None = None) -> Path | None:
    """Infer `<handoff>/<goal>` directory from `briefs/<task>.md` or `receipts/<task>.md`."""
    for candidate in (brief_path, receipt_path):
        if candidate is None:
            continue
        resolved = candidate.expanduser().resolve()
        parent = resolved.parent
        if parent.name in {"briefs", "receipts"} and parent.parent.is_dir():
            return parent.parent
        if (parent / "ledger.md").is_file() or (parent / "design.md").is_file() or (parent / "spec.md").is_file():
            return parent
    return None


def _paths_touch_ui(paths: Sequence[str]) -> bool:
    for raw in paths:
        cleaned = str(raw or "").strip().strip("`'\"")
        if not cleaned:
            continue
        norm = cleaned.replace("\\", "/")
        base = norm.rstrip("/*")
        if _UI_FILE_SUFFIX_RE.search(norm) or ".swift" in norm.lower():
            return True
        if _UI_PATH_HINT_RE.search(base):
            return True
    return False


def should_enforce_ui_closeout_in_pre_review(
    *,
    brief_path: Path,
    brief_text: str,
    owned_paths: Sequence[str],
    changed_files: Sequence[str],
    receipt_path: Path | None,
    receipt_text: str = "",
    require_ui_traceback: bool = False,
    design_spec: Path | None = None,
) -> bool:
    """Determine whether `feedback_guard.py pre-review-check` must enforce the UI closeout gate."""
    if require_ui_traceback or design_spec is not None:
        return True
    if receipt_path is None:
        return False

    handoff_dir = infer_handoff_dir(brief_path, receipt_path)
    has_design_md = bool(handoff_dir and (handoff_dir / "design.md").is_file())
    if has_design_md:
        return True

    ui_paths = _paths_touch_ui(owned_paths) or _paths_touch_ui(changed_files)
    corpus = f"{brief_text}\n{receipt_text}"
    explicit_ui_contract = bool(
        re.search(
            r"(?:\bdesign\.md\b|Visual\s+Traceback|check-whitespace(?:-ratio)?|snapshots/[A-Za-z0-9_.-]+\.png)",
            corpus,
            re.IGNORECASE,
        )
    )
    if ui_paths and explicit_ui_contract:
        return True
    return False


def has_unresolved_impl_bug(receipt_text: str) -> bool:
    """Return True when receipt mentions `[IMPL_BUG]` on any line not explicitly marked zero/resolved."""
    for raw_line in receipt_text.splitlines():
        line = raw_line.strip()
        if not line or not _IMPL_BUG_TOKEN_RE.search(line):
            continue
        if _RESOLVED_IMPL_BUG_LINE_RE.search(line):
            continue
        return True
    return False


def inspect_ui_receipt_evidence(receipt_text: str) -> dict[str, Any]:
    """Inspect an R10 receipt for rendered PNG, whitespace check, Designer Visual Traceback, and `[IMPL_BUG]`."""
    png_matches = [m.group(0) for m in _PNG_PATH_RE.finditer(receipt_text)]
    has_png = len(png_matches) > 0
    has_whitespace = bool(_WHITESPACE_CHECK_RE.search(receipt_text)) and not bool(
        _WHITESPACE_FAIL_LINE_RE.search(receipt_text)
    )
    has_designer_traceback = bool(_DESIGNER_ROLE_RE.search(receipt_text)) and bool(
        _VISUAL_TRACEBACK_RE.search(receipt_text)
    )
    unresolved_impl_bug = has_unresolved_impl_bug(receipt_text)

    violations: list[dict[str, str]] = []
    if not has_png:
        violations.append(
            {
                "code": "ui_snapshot_png_missing",
                "detail": "UI Task R10 receipt must record rendered PNG snapshot path(s) (e.g. snapshots/*.png)",
                "guidance": PLAYBOOK_GUIDANCE,
            }
        )
    if not has_whitespace:
        violations.append(
            {
                "code": "ui_whitespace_check_missing",
                "detail": "UI Task R10 receipt must record passing blank/collapse whitespace check (e.g. check-whitespace-ratio)",
                "guidance": PLAYBOOK_GUIDANCE,
            }
        )
    if not has_designer_traceback:
        violations.append(
            {
                "code": "ui_designer_visual_traceback_missing",
                "detail": "UI Task R10 receipt must record magpie/group/designer Visual Traceback review conclusion",
                "guidance": PLAYBOOK_GUIDANCE,
            }
        )
    if unresolved_impl_bug:
        violations.append(
            {
                "code": "ui_unresolved_impl_bug",
                "detail": "UI Task R10 receipt contains unresolved [IMPL_BUG] finding(s)",
                "guidance": PLAYBOOK_GUIDANCE,
            }
        )
    return {
        "ok": len(violations) == 0,
        "snapshots": png_matches,
        "has_snapshot_png": has_png,
        "has_whitespace_check": has_whitespace,
        "has_designer_visual_traceback": has_designer_traceback,
        "has_unresolved_impl_bug": unresolved_impl_bug,
        "violations": violations,
    }


def evaluate_pre_review_ui_gate(
    *,
    brief_path: Path,
    brief_text: str,
    owned_paths: Sequence[str],
    changed_files: Sequence[str],
    receipt_path: Path | None,
    receipt_text: str,
    require_ui_traceback: bool = False,
    design_spec: Path | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    """Run UI closeout checks for `feedback_guard.py pre-review-check` when applicable."""
    if design_spec is not None and not design_spec.expanduser().resolve().is_file():
        return True, [
            {
                "code": "missing_design_spec",
                "detail": f"Explicit --design-spec file does not exist: {design_spec}",
                "guidance": PLAYBOOK_GUIDANCE,
            }
        ]
    enforced = should_enforce_ui_closeout_in_pre_review(
        brief_path=brief_path,
        brief_text=brief_text,
        owned_paths=owned_paths,
        changed_files=changed_files,
        receipt_path=receipt_path,
        receipt_text=receipt_text,
        require_ui_traceback=require_ui_traceback,
        design_spec=design_spec,
    )
    if not enforced:
        return False, []
    if receipt_path is None or not receipt_path.expanduser().resolve().is_file():
        return True, [
            {
                "code": "ui_receipt_required",
                "detail": "UI Task pre-review check requires an R10 --receipt recording PNG snapshots, whitespace check, and Designer Visual Traceback",
                "guidance": PLAYBOOK_GUIDANCE,
            }
        ]
    inspected = inspect_ui_receipt_evidence(receipt_text)
    return True, list(inspected["violations"])


def check_m2_ui_closeout(
    *,
    handoff_dir: Path,
    receipt_path: Path | None = None,
    brief_path: Path | None = None,
) -> dict[str, Any]:
    """Validate M2 -> M1 dispatch evidence and UI R10 closeout evidence for a Goal handoff directory."""
    handoff = handoff_dir.expanduser().resolve()
    ledger_path = handoff / "ledger.md"
    queue_path = handoff / "queue.json"
    design_path = handoff / "design.md"
    spec_path = handoff / "spec.md"

    ledger_text = ledger_path.read_text(encoding="utf-8", errors="replace") if ledger_path.is_file() else ""
    queue_text = queue_path.read_text(encoding="utf-8", errors="replace") if queue_path.is_file() else ""
    spec_text = spec_path.read_text(encoding="utf-8", errors="replace") if spec_path.is_file() else ""

    brief_files: list[Path] = []
    if brief_path is not None:
        bp = brief_path.expanduser().resolve()
        if bp.is_file():
            brief_files.append(bp)
    elif (handoff / "briefs").is_dir():
        brief_files.extend(sorted(p for p in (handoff / "briefs").glob("*.md") if p.is_file()))
    brief_corpus = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in brief_files)

    is_ui_scope = (
        design_path.is_file()
        or bool(re.search(r"\.swift\b|SwiftUI|UIKit|AppKit|Visual\s+Traceback|\bdesign\.md\b", f"{spec_text}\n{brief_corpus}\n{queue_text}", re.IGNORECASE))
    )
    if not is_ui_scope:
        return {
            "ok": True,
            "goal": handoff.name,
            "ui_scope": False,
            "skipped_reason": "non_ui_goal",
            "guidance": M2_GUIDANCE,
        }

    missing: list[str] = []
    dispatch_corpus = f"{ledger_text}\n{queue_text}"
    if not _M1_DISPATCH_EVIDENCE_RE.search(dispatch_corpus):
        missing.append("m1_herdr_dispatch_missing")

    candidate_receipts: list[Path] = []
    if receipt_path is not None:
        rp = receipt_path.expanduser().resolve()
        if rp.is_file():
            candidate_receipts.append(rp)
        else:
            missing.append("r10_receipt_missing")
    elif (handoff / "receipts").is_dir():
        candidate_receipts.extend(
            sorted(
                p
                for p in (handoff / "receipts").glob("*.md")
                if p.is_file() and not p.name.startswith("preview-selection")
            )
        )
        if not candidate_receipts:
            missing.append("r10_receipt_missing")
    else:
        missing.append("r10_receipt_missing")

    snapshots: list[str] = []
    if candidate_receipts:
        combined_receipt = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in candidate_receipts)
        inspected = inspect_ui_receipt_evidence(combined_receipt)
        snapshots = list(inspected["snapshots"])
        for viol in inspected["violations"]:
            if viol["code"] not in missing:
                missing.append(viol["code"])

    if missing:
        return {
            "ok": False,
            "goal": handoff.name,
            "ui_scope": True,
            "error_code": "UI_CLOSEOUT_GATE_FAILED",
            "missing": missing,
            "guidance": M2_GUIDANCE,
            "reference": M2_GUIDANCE,
        }
    return {
        "ok": True,
        "goal": handoff.name,
        "ui_scope": True,
        "receipts_checked": [p.name for p in candidate_receipts],
        "snapshots": snapshots,
        "guidance": M2_GUIDANCE,
        "reference": M2_GUIDANCE,
    }
