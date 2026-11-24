#!/usr/bin/env python3
"""Validation helpers for structured original-feedback queue metadata."""

from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence


FEEDBACK_SCHEMA_VERSION = 1
FEEDBACK_VALIDATION_VERIFIED = "verified"
FEEDBACK_VALIDATION_LEGACY = "unverified_legacy"

_MANIFEST_KEYS = {"schema_version", "entries", "task_mappings"}
_ENTRY_KEYS = {"feedback_id", "acceptance"}
_ACCEPTANCE_KEYS = {"acceptance_id", "text"}
_MAPPING_KEYS = {
    "task_id",
    "feedback_ids",
    "acceptance_mapping",
    "common_root_cause_evidence",
}
_EVIDENCE_KEYS = {"summary", "references"}
_ACCEPTANCE_MAPPING_KEYS = {"feedback_id", "acceptance_ids"}
_COMPLETION_KEYS = {
    "feedback_id",
    "acceptance_id",
    "status",
    "evidence",
    "replacement_task_id",
    "authorization",
}
_COMPLETION_STATUSES = {"passed", "replaced", "cancelled"}
TRACKED_TASK_FIELDS = frozenset(
    {
        "feedback_ids",
        "acceptance_mapping",
        "acceptance_evidence",
        "superseded_by",
        "cancellation_authorization",
        "replacement_of",
    }
)


def parse_json_argument(raw: str, option_name: str) -> Any:
    """Parse an inline JSON value or a safe repository/handoff JSON file."""
    stripped = raw.strip()
    if not stripped:
        raise ValueError(f"{option_name} cannot be empty")
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{option_name} contains malformed JSON: {exc.msg}") from exc

    path = Path(stripped).expanduser()
    if ".." in path.parts or path.is_symlink():
        raise ValueError(f"Refusing unsafe {option_name} path: {raw}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{option_name} file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{option_name} file contains malformed JSON: {exc.msg}") from exc


def _require_keys(value: Any, allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unsupported metadata: {', '.join(unknown)}")
    return value


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, label: str, *, unique: bool = True) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty JSON array")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _required_string(item, f"{label}[{index}]")
        if unique and text in result:
            raise ValueError(f"{label} contains duplicate value '{text}'")
        result.append(text)
    return result


def _acceptance_pairs(
    manifest: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], str]]:
    entries = manifest["entries"]
    feedback_entries: dict[str, dict[str, Any]] = {}
    acceptance_by_pair: dict[tuple[str, str], str] = {}
    for entry_index, raw_entry in enumerate(entries):
        entry = _require_keys(raw_entry, _ENTRY_KEYS, f"entries[{entry_index}]")
        feedback_id = _required_string(entry.get("feedback_id"), f"entries[{entry_index}].feedback_id")
        if feedback_id in feedback_entries:
            raise ValueError(f"duplicate feedback_id '{feedback_id}'")
        acceptance = entry.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance:
            raise ValueError(f"entries[{entry_index}].acceptance must be a non-empty JSON array")
        normalized_acceptance: list[dict[str, str]] = []
        seen_acceptance: set[str] = set()
        for acceptance_index, raw_acceptance in enumerate(acceptance):
            item = _require_keys(
                raw_acceptance,
                _ACCEPTANCE_KEYS,
                f"entries[{entry_index}].acceptance[{acceptance_index}]",
            )
            acceptance_id = _required_string(
                item.get("acceptance_id"),
                f"entries[{entry_index}].acceptance[{acceptance_index}].acceptance_id",
            )
            text = _required_string(
                item.get("text"),
                f"entries[{entry_index}].acceptance[{acceptance_index}].text",
            )
            if acceptance_id in seen_acceptance:
                raise ValueError(
                    f"feedback_id '{feedback_id}' contains duplicate acceptance_id '{acceptance_id}'"
                )
            seen_acceptance.add(acceptance_id)
            normalized_acceptance.append({"acceptance_id": acceptance_id, "text": text})
            acceptance_by_pair[(feedback_id, acceptance_id)] = text
        feedback_entries[feedback_id] = {
            "feedback_id": feedback_id,
            "acceptance": normalized_acceptance,
        }
    return feedback_entries, acceptance_by_pair


def validate_feedback_manifest(
    manifest: Any,
    tasks: Sequence[dict[str, Any]],
    *,
    pending_replacements: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Validate coverage, ownership, grouping evidence, and acceptance mapping."""
    data = _require_keys(manifest, _MANIFEST_KEYS, "feedback manifest")
    if data.get("schema_version") != FEEDBACK_SCHEMA_VERSION:
        raise ValueError(f"feedback manifest schema_version must be {FEEDBACK_SCHEMA_VERSION}")
    entries = data.get("entries")
    mappings = data.get("task_mappings")
    if not isinstance(entries, list) or not entries:
        raise ValueError("feedback manifest entries must be a non-empty JSON array")
    if not isinstance(mappings, list) or not mappings:
        raise ValueError("feedback manifest task_mappings must be a non-empty JSON array")

    feedback_entries, acceptance_by_pair = _acceptance_pairs(data)
    task_by_id = {str(task.get("task_id")): task for task in tasks}
    if len(task_by_id) != len(tasks):
        raise ValueError("queue contains duplicate task_id values")

    mapping_by_task: dict[str, dict[str, Any]] = {}
    for mapping_index, raw_mapping in enumerate(mappings):
        mapping = _require_keys(raw_mapping, _MAPPING_KEYS, f"task_mappings[{mapping_index}]")
        task_id = _required_string(mapping.get("task_id"), f"task_mappings[{mapping_index}].task_id")
        if task_id not in task_by_id:
            raise ValueError(f"task mapping references unknown task_id '{task_id}'")
        if task_id in mapping_by_task:
            raise ValueError(f"duplicate task mapping for task_id '{task_id}'")
        feedback_ids = _string_list(
            mapping.get("feedback_ids"),
            f"task_mappings[{mapping_index}].feedback_ids",
        )
        unknown_feedback = sorted(set(feedback_ids) - set(feedback_entries))
        if unknown_feedback:
            raise ValueError(
                f"task mapping '{task_id}' references unknown feedback_id(s): {', '.join(unknown_feedback)}"
            )
        if len(feedback_ids) > 1:
            root_cause = _require_keys(
                mapping.get("common_root_cause_evidence"),
                _EVIDENCE_KEYS,
                f"task_mappings[{mapping_index}].common_root_cause_evidence",
            )
            _required_string(root_cause.get("summary"), "common_root_cause_evidence.summary")
            _string_list(root_cause.get("references"), "common_root_cause_evidence.references")

        raw_acceptance_mapping = mapping.get("acceptance_mapping")
        if not isinstance(raw_acceptance_mapping, list) or not raw_acceptance_mapping:
            raise ValueError(f"task mapping '{task_id}' requires acceptance_mapping")
        mapped_feedback: set[str] = set()
        mapped_pairs: set[tuple[str, str]] = set()
        for acceptance_index, raw_item in enumerate(raw_acceptance_mapping):
            item = _require_keys(
                raw_item,
                _ACCEPTANCE_MAPPING_KEYS,
                f"task_mappings[{mapping_index}].acceptance_mapping[{acceptance_index}]",
            )
            feedback_id = _required_string(
                item.get("feedback_id"),
                f"acceptance_mapping[{acceptance_index}].feedback_id",
            )
            if feedback_id not in feedback_entries:
                raise ValueError(f"acceptance mapping references unknown feedback_id '{feedback_id}'")
            if feedback_id in mapped_feedback:
                raise ValueError(f"task mapping '{task_id}' repeats feedback_id '{feedback_id}'")
            mapped_feedback.add(feedback_id)
            acceptance_ids = _string_list(
                item.get("acceptance_ids"),
                f"acceptance_mapping[{acceptance_index}].acceptance_ids",
            )
            expected_ids = {
                acceptance["acceptance_id"]
                for acceptance in feedback_entries[feedback_id]["acceptance"]
            }
            if set(acceptance_ids) != expected_ids:
                raise ValueError(
                    f"task mapping '{task_id}' does not preserve all acceptance criteria for '{feedback_id}'"
                )
            for acceptance_id in acceptance_ids:
                pair = (feedback_id, acceptance_id)
                if pair in mapped_pairs:
                    raise ValueError(f"task mapping '{task_id}' repeats acceptance pair {pair!r}")
                mapped_pairs.add(pair)

        if mapped_feedback != set(feedback_ids):
            raise ValueError(f"task mapping '{task_id}' feedback_ids and acceptance_mapping disagree")
        mapping_by_task[task_id] = mapping

    expected_task_ids = set(task_by_id)
    if set(mapping_by_task) != expected_task_ids:
        missing = sorted(expected_task_ids - set(mapping_by_task))
        extra = sorted(set(mapping_by_task) - expected_task_ids)
        details = []
        if missing:
            details.append(f"missing task mappings: {', '.join(missing)}")
        if extra:
            details.append(f"unknown task mappings: {', '.join(extra)}")
        raise ValueError("feedback manifest coverage is incomplete (" + "; ".join(details) + ")")
    expected_pairs = set(acceptance_by_pair)
    mapped_pairs = set().union(*(mapping_pairs(mapping) for mapping in mapping_by_task.values()))
    if mapped_pairs != expected_pairs:
        missing_pairs = sorted(expected_pairs - mapped_pairs)
        extra_pairs = sorted(mapped_pairs - expected_pairs)
        details = []
        if missing_pairs:
            details.append(
                "missing "
                + ", ".join(f"{feedback_id}/{acceptance_id}" for feedback_id, acceptance_id in missing_pairs)
            )
        if extra_pairs:
            details.append(
                "unknown "
                + ", ".join(f"{feedback_id}/{acceptance_id}" for feedback_id, acceptance_id in extra_pairs)
            )
        raise ValueError(
            "feedback manifest acceptance coverage is incomplete (" + "; ".join(details) + ")"
        )

    pending_replacements = pending_replacements or {}
    for replacement_task_id, original_task_id in pending_replacements.items():
        if replacement_task_id not in task_by_id or original_task_id not in task_by_id:
            raise ValueError("replacement_of must reference an existing task")
        replacement_task = task_by_id[replacement_task_id]
        original_task = task_by_id[original_task_id]
        if str(replacement_task.get("replacement_of") or "") != original_task_id:
            raise ValueError(
                f"task '{replacement_task_id}' replacement_of does not match the requested original task"
            )
        if str(original_task.get("status")) in {"done", "cancelled"}:
            raise ValueError("replacement_of cannot target a terminal task")
        if mapping_pairs(mapping_by_task[replacement_task_id]) != mapping_pairs(
            mapping_by_task[original_task_id]
        ):
            raise ValueError("a new replacement task must preserve exactly the original acceptance mapping")

    for task_id, task in task_by_id.items():
        superseded_by = str(task.get("superseded_by") or "").strip()
        if str(task.get("status")) == "cancelled" and superseded_by:
            if superseded_by not in task_by_id or superseded_by == task_id:
                raise ValueError(f"task '{task_id}' has an invalid superseded_by reference")
            if not mapping_pairs(mapping_by_task[task_id]).issubset(
                mapping_pairs(mapping_by_task[superseded_by])
            ):
                raise ValueError(
                    f"task '{task_id}' replacement does not preserve every original acceptance"
                )

    owners_by_pair: dict[tuple[str, str], set[str]] = {}
    authorized_cancellations_by_pair: dict[tuple[str, str], bool] = {}
    for task_id, mapping in mapping_by_task.items():
        terminal_owner = _resolve_terminal_owner(task_id, task_by_id)
        pairs = mapping_pairs(mapping)
        if terminal_owner is None:
            for pair in pairs:
                authorized_cancellations_by_pair[pair] = True
            continue
        if _is_pending_replacement(task_id, task_by_id):
            continue
        for pair in pairs:
            owners_by_pair.setdefault(pair, set()).add(terminal_owner)

    for pair in expected_pairs:
        owners = owners_by_pair.get(pair, set())
        if len(owners) > 1:
            raise ValueError(f"acceptance pair {pair!r} has duplicate ownership")
        if not owners and not authorized_cancellations_by_pair.get(pair):
            raise ValueError(f"acceptance pair {pair!r} has no active terminal owner")
    return mapping_by_task


def _resolve_terminal_owner(
    task_id: str,
    task_by_id: Mapping[str, dict[str, Any]],
) -> str | None:
    current = task_id
    visited: set[str] = set()
    while True:
        if current in visited:
            raise ValueError(f"supersession cycle detected at task '{current}'")
        visited.add(current)
        task = task_by_id[current]
        status = str(task.get("status"))
        superseded_by = str(task.get("superseded_by") or "").strip()
        authorization = str(task.get("cancellation_authorization") or "").strip()
        if status != "cancelled":
            if superseded_by or authorization:
                raise ValueError(f"task '{current}' has stale terminal metadata")
            return current
        if superseded_by and authorization:
            raise ValueError(
                f"cancelled task '{current}' cannot have both superseded_by and authorization"
            )
        if superseded_by:
            if superseded_by not in task_by_id or superseded_by == current:
                raise ValueError(f"task '{current}' has an invalid superseded_by reference")
            current = superseded_by
            continue
        if authorization:
            return None
        raise ValueError(
            f"cancelled task '{current}' requires superseded_by or cancellation_authorization"
        )


def _is_pending_replacement(
    task_id: str,
    task_by_id: Mapping[str, dict[str, Any]],
) -> bool:
    # TODO(deferred): reject dangling replacement_of tasks if original_task_id transitions
    # to done or is superseded by a different task after enqueue.
    task = task_by_id[task_id]
    original_task_id = str(task.get("replacement_of") or "").strip()
    if not original_task_id:
        return False
    if original_task_id not in task_by_id:
        raise ValueError(f"task '{task_id}' replacement_of references an unknown task")
    return str(task.get("status")) != "cancelled" and str(
        task_by_id[original_task_id].get("superseded_by") or ""
    ) != task_id


def validate_manifest_preserves_original_entries(
    original_manifest: Any,
    replacement_manifest: Any,
) -> None:
    """Reject removal or rewriting of existing feedback acceptance text and IDs."""
    original_entries, _ = _acceptance_pairs(
        _require_keys(original_manifest, _MANIFEST_KEYS, "original feedback manifest")
    )
    replacement_entries, _ = _acceptance_pairs(
        _require_keys(replacement_manifest, _MANIFEST_KEYS, "replacement feedback manifest")
    )
    for feedback_id, original_entry in original_entries.items():
        replacement_entry = replacement_entries.get(feedback_id)
        if replacement_entry is None:
            raise ValueError(f"replacement manifest must preserve feedback_id '{feedback_id}'")
        replacement_acceptance = {
            item["acceptance_id"]: item["text"] for item in replacement_entry["acceptance"]
        }
        for item in original_entry["acceptance"]:
            acceptance_id = item["acceptance_id"]
            if acceptance_id not in replacement_acceptance:
                raise ValueError(
                    f"replacement manifest must preserve acceptance '{feedback_id}/{acceptance_id}'"
                )
            if replacement_acceptance[acceptance_id] != item["text"]:
                raise ValueError(
                    f"replacement manifest cannot rewrite acceptance text "
                    f"'{feedback_id}/{acceptance_id}'"
                )


def mapping_pairs(
    mapping: dict[str, Any],
) -> set[tuple[str, str]]:
    """Return acceptance pairs from a validated task mapping."""
    return {
        (str(item["feedback_id"]), str(acceptance_id))
        for item in mapping["acceptance_mapping"]
        for acceptance_id in item["acceptance_ids"]
    }


def validate_completion_evidence(
    manifest: Any,
    tasks: Sequence[dict[str, Any]],
    target_task: dict[str, Any],
    status: str,
    evidence: Any,
    *,
    superseded_by: str | None,
    authorization: str | None,
    replacement_manifest: Any | None = None,
    replacement_tasks: Sequence[dict[str, Any]] | None = None,
) -> None:
    """Require structured evidence for every mapped acceptance at terminal states."""
    mappings = validate_feedback_manifest(manifest, tasks)
    candidate_tasks = replacement_tasks if replacement_tasks is not None else tasks
    replacement_mappings = (
        validate_feedback_manifest(replacement_manifest, candidate_tasks)
        if replacement_manifest is not None
        else validate_feedback_manifest(manifest, candidate_tasks)
        if replacement_tasks is not None
        else mappings
    )
    if replacement_manifest is not None:
        validate_manifest_preserves_original_entries(manifest, replacement_manifest)
    task_id = str(target_task["task_id"])
    expected_pairs = mapping_pairs(mappings[task_id])
    evidence_object = _require_keys(evidence, {"acceptance"}, "completion evidence")
    raw_items = evidence_object.get("acceptance")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("completion evidence acceptance must be a non-empty JSON array")
    seen_pairs: set[tuple[str, str]] = set()
    normalized_items: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items):
        item = _require_keys(raw_item, _COMPLETION_KEYS, f"completion evidence acceptance[{index}]")
        feedback_id = _required_string(item.get("feedback_id"), f"acceptance[{index}].feedback_id")
        acceptance_id = _required_string(item.get("acceptance_id"), f"acceptance[{index}].acceptance_id")
        pair = (feedback_id, acceptance_id)
        if pair in seen_pairs:
            raise ValueError(f"completion evidence repeats acceptance pair {pair!r}")
        seen_pairs.add(pair)
        if pair not in expected_pairs:
            raise ValueError(f"completion evidence contains unmapped acceptance pair {pair!r}")
        evidence_status = _required_string(item.get("status"), f"acceptance[{index}].status")
        if evidence_status not in _COMPLETION_STATUSES:
            raise ValueError(f"unsupported completion evidence status '{evidence_status}'")
        evidence_values = _string_list(item.get("evidence"), f"acceptance[{index}].evidence")
        normalized_items.append(
            {
                "feedback_id": feedback_id,
                "acceptance_id": acceptance_id,
                "status": evidence_status,
                "evidence": evidence_values,
                **(
                    {"replacement_task_id": _required_string(
                        item.get("replacement_task_id"),
                        f"acceptance[{index}].replacement_task_id",
                    )}
                    if "replacement_task_id" in item
                    else {}
                ),
                **(
                    {"authorization": _required_string(
                        item.get("authorization"),
                        f"acceptance[{index}].authorization",
                    )}
                    if "authorization" in item
                    else {}
                ),
            }
        )
    if seen_pairs != expected_pairs:
        missing = sorted(expected_pairs - seen_pairs)
        raise ValueError(
            "completion evidence must cover every original acceptance: "
            + ", ".join(f"{feedback_id}/{acceptance_id}" for feedback_id, acceptance_id in missing)
        )

    if status == "done":
        if superseded_by or authorization:
            raise ValueError("done cannot carry supersession or cancellation metadata")
        if any(item["status"] != "passed" for item in normalized_items):
            raise ValueError("done requires status=passed evidence for every acceptance")
        return

    if status != "cancelled":
        raise ValueError(f"completion evidence is only valid for terminal status, not '{status}'")
    note_values = [str(item.get("evidence", "")) for item in normalized_items]
    if any(_looks_like_ci_failure(value) for value in note_values):
        raise ValueError("CI/build/check failures cannot be used as cancellation evidence")

    task_by_id = {str(task["task_id"]): task for task in candidate_tasks}
    if superseded_by:
        if superseded_by == task_id or superseded_by not in task_by_id:
            raise ValueError("superseded_by must reference a different existing task")
        replacement_pairs = mapping_pairs(replacement_mappings[superseded_by])
        if not expected_pairs.issubset(replacement_pairs):
            raise ValueError("replacement task does not preserve every original acceptance")
        for item in normalized_items:
            if item["status"] != "replaced":
                raise ValueError("supersession requires status=replaced for every acceptance")
            if item.get("replacement_task_id") != superseded_by:
                raise ValueError("supersession evidence requires matching replacement_task_id")
        if task_by_id[superseded_by].get("status") == "cancelled":
            raise ValueError("superseded_by task cannot already be cancelled")
        return

    if not authorization:
        raise ValueError("authorized cancellation requires --authorization")
    for item in normalized_items:
        if item["status"] != "cancelled":
            raise ValueError("authorized cancellation requires status=cancelled evidence")
        item_authorization = str(item.get("authorization") or authorization)
        if not item_authorization.strip():
            raise ValueError("authorized cancellation requires non-empty authorization")


def _looks_like_ci_failure(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in ("ci failure", "ci failed", "build failed", "pipeline failed", "check failed")
    )


def attach_task_mapping(task: dict[str, Any], mapping: dict[str, Any]) -> None:
    """Copy validated mapping identifiers without normalizing away source metadata."""
    task["feedback_ids"] = list(mapping["feedback_ids"])
    task["acceptance_mapping"] = [
        {
            "feedback_id": str(item["feedback_id"]),
            "acceptance_ids": list(item["acceptance_ids"]),
        }
        for item in mapping["acceptance_mapping"]
    ]


_PERSONAL_HOME_RE = re.compile(r"(?:/" + r"Users|/home)/[A-Za-z0-9._-]+")
_TOKEN_LEAK_RE = re.compile(
    r"(?:ghp_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|"
    r"lin_api_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{16,})"
)
_BRIEF_OWNED_HDR_RE = re.compile(r"(?i)^(?:#{1,6}\s*|[-*+]\s*(?:\*\*)?)owned\s+paths(?:\*\*)?\b(.*)$")
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")


def extract_owned_paths_from_brief(brief_text: str) -> list[str]:
    """Extract repo-relative Owned Paths patterns from a Markdown task brief."""
    owned: list[str] = []
    lines = brief_text.splitlines()
    in_owned = False
    base_indent = 0
    for line in lines:
        stripped = line.strip()
        hdr_match = _BRIEF_OWNED_HDR_RE.match(stripped)
        if hdr_match:
            in_owned = True
            base_indent = len(line) - len(line.lstrip(" "))
            for token in _BACKTICK_RE.findall(hdr_match.group(1)):
                norm = _normalize_owned_token(token)
                if norm and norm not in owned:
                    owned.append(norm)
            continue
        if not in_owned:
            continue
        if stripped.startswith("#"):
            break
        indent = len(line) - len(line.lstrip(" "))
        if stripped and re.match(r"^[-*+]\s+\*\*[^*]+\*\*\s*:", stripped) and indent <= base_indent:
            break
        if not stripped:
            continue
        tokens = _BACKTICK_RE.findall(stripped)
        if not tokens and re.match(r"^[-*+]\s+\S+", stripped):
            raw_item = re.sub(r"^[-*+]\s+", "", stripped).split()[0].strip(",;:")
            tokens = [raw_item]
        for token in tokens:
            norm = _normalize_owned_token(token)
            if norm and norm not in owned:
                owned.append(norm)
    return owned


def _normalize_owned_token(raw: str) -> str:
    token = raw.strip().strip("\"'").lstrip("./")
    if not token or " " in token or token.startswith(("-", "http://", "https://", "~/")):
        return ""
    # Skip GitHub owner/repo slugs like `LeePepe/user-skills` in Owned Paths headers.
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", token) and "." not in token.split("/", 1)[0]:
        first_seg = token.split("/", 1)[0]
        if first_seg[0].isupper() or "-" in token.split("/", 1)[1] and "." not in token.split("/", 1)[1]:
            if first_seg not in {"skills", "scripts", "references", "tests", "docs", "src", "apps", "packages"}:
                return ""
    if "/" not in token and "." not in token and "*" not in token:
        return ""
    return token.rstrip("/") if not token.endswith("/**") else token


def _matches_owned_path(rel_path: str, owned_patterns: Sequence[str]) -> bool:
    norm_file = PurePosixPath(rel_path.lstrip("./")).as_posix()
    for raw_pat in owned_patterns:
        pat = raw_pat.strip().lstrip("./")
        if not pat:
            continue
        if pat.endswith("/**"):
            prefix = pat[:-3].rstrip("/")
            if norm_file == prefix or norm_file.startswith(prefix + "/"):
                return True
        if pat.endswith("/*"):
            prefix = pat[:-2].rstrip("/")
            if PurePosixPath(norm_file).parent.as_posix() == prefix:
                return True
        if norm_file == pat or norm_file.startswith(pat.rstrip("/") + "/"):
            return True
        if fnmatch.fnmatchcase(norm_file, pat):
            return True
    return False


def _clean_receipt_value(raw: str) -> str:
    return raw.strip().strip("*`'\"").strip()


_PLACEHOLDER_VALS = {"n/a", "na", "none", "null", "unknown", "tbd", "todo", "-", "—"}


def _is_populated_token(val: str) -> bool:
    cleaned = _clean_receipt_value(val)
    return bool(cleaned) and cleaned.lower() not in _PLACEHOLDER_VALS


def validate_r10_receipt(receipt_text: str) -> list[str]:
    """Check required R10 receipt sections/fields (real Commit SHA, actual Role + Model, non-empty Verification/V5)."""
    violations: list[str] = []
    sha_match = re.search(
        r"(?im)^[\s>*-]*(?:\*\*)?(?:commit(?:\s+sha)?|head(?:\s+sha)?)(?:\*\*)?[\s*`]*[:|=-][ \t]*(.+)$",
        receipt_text,
    )
    sha_val = _clean_receipt_value(sha_match.group(1)) if sha_match else ""
    if not re.search(r"\b[0-9a-f]{7,40}\b", sha_val, re.IGNORECASE):
        violations.append("missing_receipt_commit_sha: R10 receipt must record a real 7-40 hex Commit SHA")

    has_role = False
    has_model = False
    combined_rm = re.search(
        r"(?im)^[\s>*-]*(?:\*\*)?role\s*/\s*model(?:\*\*)?[\s*`]*[:|=-][ \t]*(.+)$",
        receipt_text,
    )
    if combined_rm:
        raw_rm = combined_rm.group(1).strip()
        if " / " in raw_rm:
            role_part, model_part = re.split(r"\s+/\s+", raw_rm, maxsplit=1)
        elif "/" in raw_rm:
            role_part, model_part = raw_rm.rsplit("/", 1)
        else:
            role_part, model_part = "", ""
        if _is_populated_token(role_part):
            has_role = True
        if _is_populated_token(model_part):
            has_model = True
    if not has_role:
        role_only = re.search(
            r"(?im)^[\s>*-]*(?:\*\*)?(?:role|agent)(?:\*\*)?[\s*`]*[:|=-][ \t]*(.+)$",
            receipt_text,
        )
        if role_only and _is_populated_token(role_only.group(1)):
            has_role = True
    if not has_model:
        model_only = re.search(
            r"(?im)^[\s>*-]*(?:\*\*)?(?:resolved_model|model)(?:\*\*)?[\s*`]*[:|=-][ \t]*(.+)$",
            receipt_text,
        )
        if model_only and _is_populated_token(model_only.group(1)):
            has_model = True
    if not (has_role and has_model):
        violations.append(
            "missing_receipt_role_or_model: R10 receipt must record both a non-empty Role and an actual Model value"
        )

    has_verif_value = False
    v5_line = re.search(
        r"(?im)^[\s>*-]*(?:\*\*)?(?:v5(?:\s+verdict)?|verdict|verification(?:\s+commands?)?)(?:\*\*)?[\s*`]*[:|=-][ \t]*(.+)$",
        receipt_text,
    )
    if v5_line:
        v5_val = _clean_receipt_value(v5_line.group(1))
        if v5_val and v5_val.lower() not in {"n/a", "na", "none", "tbd", "todo", "-", "—"}:
            has_verif_value = True
    if not has_verif_value:
        sec_match = re.search(
            r"(?ims)^#{1,4}\s+[^\n]*(?:verification|verify|v5)[^\n]*\n(.+?)(?=^#{1,4}\s+|\Z)",
            receipt_text,
        )
        if sec_match:
            sec_lines = [
                ln.strip()
                for ln in sec_match.group(1).splitlines()
                if ln.strip() and _clean_receipt_value(ln.lstrip("-*0123456789. "))
            ]
            if sec_lines:
                has_verif_value = True
    if not has_verif_value:
        violations.append(
            "missing_receipt_verification: R10 receipt must record a non-empty V5 verdict or verification command result"
        )
    return violations


def _mask_swift_source(text: str) -> str:
    pattern = re.compile(r'("(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/)')
    return pattern.sub(lambda match: ''.join('\n' if char == '\n' else ' ' for char in match.group()), text)


def _strip_swift_comments_preserve_strings(text: str) -> str:
    pattern = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/')
    return pattern.sub(lambda match: ''.join('\n' if char == '\n' else ' ' for char in match.group()), text)


def check_swiftui_and_error_retry_guards(
    worktree: Path,
    changed_files: Sequence[str],
    diff_text: str = "",
) -> list[dict[str, Any]]:
    """Check changed Swift files for platform-safe environment and error recovery contracts."""
    violations: list[dict[str, Any]] = []
    wt = Path(worktree).expanduser().resolve()
    for relative in changed_files:
        if not str(relative).lower().endswith('.swift'):
            continue
        path = wt / str(relative)
        try:
            source = path.read_text(encoding='utf-8', errors='ignore') if path.is_file() else diff_text
        except OSError:
            source = diff_text
        masked = _mask_swift_source(source)
        if re.search(r'@Environment\s*\(\s*AppNavigation\.self\s*\)', masked) and re.search(r'@EnvironmentObject\b', masked):
            violations.append({'code': 'swiftui_environment_or_macos_incompat', 'file': str(relative), 'detail': 'Observation @Environment(AppNavigation.self) is mixed with @EnvironmentObject'})
        elif re.search(r'@EnvironmentObject\b[^\n]*\bAppNavigation\b', masked):
            violations.append({'code': 'swiftui_environment_or_macos_incompat', 'file': str(relative), 'detail': 'AppNavigation must not be bound through @EnvironmentObject'})
        ios_only = r'\b(?:UIColor|UIImage|UIScreen|UIApplication|UIImpactFeedbackGenerator)\b|\.(?:navigationBarTitleDisplayMode|fullScreenCover)\s*\('
        if re.search(ios_only, masked) and not re.search(r'(?m)^\s*#if\s+(?:os\(iOS\)|canImport\(UIKit\))', source):
            violations.append({'code': 'swiftui_environment_or_macos_incompat', 'file': str(relative), 'detail': 'unguarded iOS-only UIKit/SwiftUI symbol'})
        error_markers = list(re.finditer(r'case\s+\.(?:error|failed)\b|\berrorMessage\b|ContentUnavailableView\s*\([^)]*(?:error|failed|错误|失败)|\bstruct\s+\w*Error\w*View\s*:\s*View', masked, re.IGNORECASE))
        if error_markers:
            retry_source = _strip_swift_comments_preserve_strings(source)
            has_retry = bool(re.search(r'\b(?:retry|onRetry|reload|tryAgain)\b|"(?:Retry|重试|Try Again|Reload)"', retry_source, re.IGNORECASE))
            for marker in error_markers:
                region_end = masked.find('case .', marker.end())
                region = source[marker.start(): region_end if region_end >= 0 else len(source)]
                if re.search(r'\bButton\b', _mask_swift_source(region)):
                    has_retry = True
                    break
            if not has_retry:
                violations.append({'code': 'missing_error_state_retry_action', 'file': str(relative), 'detail': 'explicit error UI branch has no retry/recovery action'})
    return violations


M1_STEERING_ROLES = {
    "m1",
    "m1_tl",
    "m1-tl",
    "tl",
    "team-lead",
    "team_lead",
    "steering",
    "group/steering",
    "magpie/group/steering",
}


def _receipt_role(receipt_text: str) -> str | None:
    match = re.search(
        r"(?im)^\s*[>*-]*\s*(?:\*\*)?role\s*/\s*model(?:\*\*)?\s*[:|=-]\s*([^\n]+)",
        receipt_text,
    )
    if match:
        raw_rm = match.group(1).strip()
        if re.search(r"\s+/\s+", raw_rm):
            role_part, _ = re.split(r"\s+/\s+", raw_rm, maxsplit=1)
        elif "/" in raw_rm:
            role_part, _ = raw_rm.rsplit("/", 1)
        else:
            role_part = raw_rm
        return _clean_receipt_value(role_part).lower()
    match = re.search(r"(?im)^\s*[>*-]*\s*(?:\*\*)?role(?:\*\*)?\s*[:|=-]\s*([^\n]+)", receipt_text)
    return _clean_receipt_value(match.group(1)).lower() if match else None


def _normalize_shell_command(command: str) -> str:
    normalized = re.sub(r"\s+", " ", command.strip())
    while True:
        previous = normalized
        normalized = re.sub(r"\s*(?:\(\s*)?exit\s*=\s*0\s*\)?\s*$", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*(?:—|--|:)\s*(?:PASS|OK)\b\s*$", "", normalized, flags=re.IGNORECASE)
        normalized = normalized.strip().strip("`").strip()
        if normalized == previous:
            return normalized


def _receipt_verification_commands(receipt_text: str) -> list[str]:
    commands: list[str] = []
    pattern = re.compile(
        r"(?im)^\s*(?:(?:[-*]|\d+[.)])\s*)?`?((?:python3|pytest|unittest|swiftlint)\b[^\n]*|(?:\./)?scripts/verify\b[^\n]*)"
    )
    for match in pattern.finditer(receipt_text):
        command = _normalize_shell_command(match.group(1))
        if command and command not in commands:
            commands.append(command)
    return commands


def _event_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        values = [value]
        for nested in value.values():
            values.extend(_event_values(nested))
        return values
    if isinstance(value, list):
        values: list[Any] = []
        for item in value:
            values.extend(_event_values(item))
        return values
    return [value]


def _event_command(event: Any) -> str:
    candidates: list[str] = []
    for item in _event_values(event):
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if isinstance(value, str) and ("command" in key.lower() or key.lower() in {"cmd", "shell"}):
                candidates.append(value)
    return max(candidates, key=len, default="").strip()


def _event_exit_code(event: Any) -> int | None:
    nested_values = _event_values(event)
    for item in nested_values:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key.lower() in {"exitcode", "exit_code"} and not isinstance(value, bool):
                try:
                    return int(value)
                except (TypeError, ValueError):
                    pass

    for item in nested_values:
        if isinstance(item, str):
            match = re.search(r"completed with exit code\s+(\d+)", item, re.IGNORECASE)
            if match:
                return int(match.group(1))

    for item in nested_values:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key.lower() == "success" and value is True:
                return 0
            if key.lower() == "success" and value is False:
                return 1
    return None


def _successful_event_commands(events_path: Path) -> list[str]:
    records: dict[str, dict[str, Any]] = {}
    standalone: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        call_id = ""
        for item in _event_values(event):
            if isinstance(item, dict):
                call_id = str(item.get("toolCallId") or item.get("tool_call_id") or item.get("toolCallID") or "")
                if call_id:
                    break
        if call_id:
            record = records.setdefault(call_id, {})
            record["command"] = record.get("command") or _event_command(event)
            if _event_command(event):
                record["command"] = _event_command(event)
            exit_code = _event_exit_code(event)
            if exit_code is not None:
                record["exit_code"] = exit_code
        else:
            standalone.append({"command": _event_command(event), "exit_code": _event_exit_code(event)})
    successful = [str(record["command"]) for record in [*records.values(), *standalone] if record.get("command") and record.get("exit_code") == 0]
    return successful


def _command_was_successful(claim: str, executed: list[str]) -> bool:
    normalized_claim = _normalize_shell_command(claim)
    for command in executed:
        normalized_command = _normalize_shell_command(command)
        if normalized_command == normalized_claim or normalized_claim in normalized_command:
            return True
        for part in re.split(r"&&|;", normalized_command):
            if normalized_claim == part.strip() or normalized_claim in part.strip():
                return True
    return False


def run_pre_review_check(
    *,
    worktree: Path,
    brief_path: Path,
    receipt_path: Path | None = None,
    base_ref: str | None = None,
    head_ref: str = "HEAD",
    allow_dirty: bool = False,
    actor_role: str | None = None,
    max_inline_files: int = 3,
    events_jsonl: Path | None = None,
) -> dict[str, Any]:
    """Deterministic pre-review gate catching recurring R1 Writer failures before Reviewer dispatch."""
    wt = worktree.expanduser().resolve()
    brief = brief_path.expanduser().resolve()
    target_head = (head_ref or "HEAD").strip() or "HEAD"
    violations: list[dict[str, Any]] = []

    if not wt.is_dir():
        return {"ok": False, "violations": [{"code": "missing_worktree", "detail": str(wt)}]}
    if not brief.is_file():
        return {"ok": False, "violations": [{"code": "missing_brief", "detail": str(brief)}]}

    owned_paths = extract_owned_paths_from_brief(brief.read_text(encoding="utf-8"))
    if not owned_paths:
        violations.append({
            "code": "empty_owned_paths",
            "detail": f"No Owned Paths parsed from {brief}",
        })

    status_proc = subprocess.run(
        ["git", "-C", str(wt), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        check=False,
    )
    if status_proc.returncode != 0:
        return {
            "ok": False,
            "violations": [{"code": "invalid_git_worktree", "detail": status_proc.stderr.strip() or str(wt)}],
        }
    dirty_tracked = [
        line[3:].strip()
        for line in status_proc.stdout.splitlines()
        if line.strip()
    ]
    if dirty_tracked and not allow_dirty:
        violations.append({
            "code": "dirty_tracked_worktree",
            "files": dirty_tracked,
            "detail": "Tracked modifications must be committed before Reviewer dispatch",
        })

    head_probe = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--verify", f"{target_head}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if head_probe.returncode != 0:
        violations.append({
            "code": "unverified_head_commit",
            "detail": head_probe.stderr.strip() or f"Worktree has no verifiable commit for head_ref '{target_head}'",
        })

    resolved_base: str | None = None
    if base_ref:
        explicit_probe = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "--verify", f"{base_ref}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if explicit_probe.returncode != 0:
            violations.append({
                "code": "invalid_base_ref",
                "base_ref": base_ref,
                "detail": explicit_probe.stderr.strip() or f"Explicit --base-ref '{base_ref}' cannot be resolved",
            })
        else:
            mb = subprocess.run(
                ["git", "-C", str(wt), "merge-base", base_ref, target_head],
                capture_output=True,
                text=True,
                check=False,
            )
            if mb.returncode != 0 or not mb.stdout.strip():
                violations.append({
                    "code": "invalid_base_ref",
                    "base_ref": base_ref,
                    "detail": mb.stderr.strip() or f"Cannot compute merge-base between '{base_ref}' and {target_head}",
                })
            else:
                resolved_base = mb.stdout.strip()
    else:
        head_sha = head_probe.stdout.strip() if head_probe.returncode == 0 else ""
        for candidate in ("origin/main", "main", f"{target_head}~1"):
            probe = subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "--verify", f"{candidate}^{{commit}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.returncode != 0:
                continue
            cand_sha = probe.stdout.strip()
            if candidate in {"origin/main", "main"} and cand_sha == head_sha:
                continue
            mb = subprocess.run(
                ["git", "-C", str(wt), "merge-base", candidate, target_head],
                capture_output=True,
                text=True,
                check=False,
            )
            if mb.returncode == 0 and mb.stdout.strip():
                resolved_base = mb.stdout.strip()
                break
        if not resolved_base:
            violations.append({
                "code": "unresolvable_base_ref",
                "detail": f"Could not resolve any valid git comparison base (origin/main, main, or {target_head}~1)",
            })

    changed_files: list[str] = []
    combined_diff_parts: list[str] = []
    if resolved_base and head_probe.returncode == 0:
        diff_names = subprocess.run(
            ["git", "-C", str(wt), "--no-pager", "diff", "--name-only", f"{resolved_base}..{target_head}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if diff_names.returncode != 0:
            violations.append({
                "code": "git_diff_failed",
                "detail": diff_names.stderr.strip() or f"git diff --name-only {resolved_base}..{target_head} failed",
            })
        else:
            changed_files.extend(
                f.strip() for f in diff_names.stdout.splitlines() if f.strip()
            )

        diff_proc = subprocess.run(
            ["git", "-C", str(wt), "--no-pager", "diff", "--unified=0", f"{resolved_base}..{target_head}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if diff_proc.returncode != 0:
            violations.append({
                "code": "git_diff_failed",
                "detail": diff_proc.stderr.strip() or f"git diff --unified=0 {resolved_base}..{target_head} failed",
            })
        else:
            combined_diff_parts.append(diff_proc.stdout or "")

    for df in dirty_tracked:
        if df not in changed_files:
            changed_files.append(df)

    if owned_paths and changed_files:
        outside = [f for f in changed_files if not _matches_owned_path(f, owned_paths)]
        if outside:
            violations.append({
                "code": "outside_owned_paths",
                "files": outside,
                "owned_paths": owned_paths,
                "detail": "Modified tracked files fall outside brief Owned Paths",
            })

    if head_probe.returncode == 0 and target_head == "HEAD":
        working_diff_proc = subprocess.run(
            ["git", "-C", str(wt), "--no-pager", "diff", "--unified=0", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if working_diff_proc.returncode != 0:
            violations.append({
                "code": "git_diff_failed",
                "detail": working_diff_proc.stderr.strip() or "git diff --unified=0 HEAD failed",
            })
        else:
            combined_diff_parts.append(working_diff_proc.stdout or "")

    combined_diff = "\n".join(combined_diff_parts)
    violations.extend(check_swiftui_and_error_retry_guards(wt, changed_files, combined_diff))
    current_file = ""
    leaked_paths: list[str] = []
    leaked_tokens: list[str] = []
    for raw_line in combined_diff.splitlines():
        if raw_line.startswith("+++ b/"):
            current_file = raw_line[6:].strip()
            continue
        if not raw_line.startswith("+") or raw_line.startswith("+++"):
            continue
        added_line = raw_line[1:]
        if _PERSONAL_HOME_RE.search(added_line):
            leaked_paths.append(f"{current_file}: {added_line.strip()[:120]}")
        if _TOKEN_LEAK_RE.search(added_line):
            leaked_tokens.append(f"{current_file}: [REDACTED_SECRET_PATTERN]")

    if leaked_paths:
        violations.append({
            "code": "hardcoded_personal_path",
            "matches": leaked_paths[:20],
            "detail": "Hardcoded personal home directory paths found in added lines",
        })
    if leaked_tokens:
        violations.append({
            "code": "leaked_secret_token",
            "matches": leaked_tokens[:20],
            "detail": "Secret/token pattern detected in added lines",
        })

    receipt_text = ""
    if receipt_path is not None:
        rcpt = receipt_path.expanduser().resolve()
        if not rcpt.is_file():
            violations.append({
                "code": "missing_receipt_file",
                "detail": f"R10 receipt file does not exist: {rcpt}",
            })
        else:
            receipt_text = rcpt.read_text(encoding="utf-8")
            for msg in validate_r10_receipt(receipt_text):
                code, _, detail = msg.partition(": ")
                violations.append({"code": code, "detail": detail or msg})

    effective_role = (actor_role or _receipt_role(receipt_text) or "").strip().lower()
    if effective_role in M1_STEERING_ROLES and len(changed_files) > max_inline_files:
        violations.append({
            "code": "m1_inline_file_budget_exceeded",
            "changed_file_count": len(changed_files),
            "max_inline_files": max_inline_files,
            "files": changed_files,
            "detail": f"M1/Steering inline edit touched {len(changed_files)} files (max {max_inline_files}); dispatch a leaf Writer instead",
        })

    if receipt_path is not None and events_jsonl is not None:
        events_file = events_jsonl.expanduser().resolve()
        if not events_file.is_file():
            violations.append({
                "code": "missing_events_jsonl",
                "detail": f"Trace events JSONL file does not exist: {events_file}",
            })
        else:
            claims = _receipt_verification_commands(receipt_text)
            executed = _successful_event_commands(events_file)
            missing = [claim for claim in claims if not _command_was_successful(claim, executed)]
            if missing:
                violations.append({
                    "code": "ungrounded_receipt_verification",
                    "missing_commands": missing,
                    "detail": "R10 receipt claims verification commands with zero matching exit=0 executions in events.jsonl",
                })

    return {
        "ok": len(violations) == 0,
        "worktree": str(wt),
        "brief": str(brief),
        "receipt": str(receipt_path.expanduser().resolve()) if receipt_path is not None else None,
        "base_ref": resolved_base,
        "head_ref": target_head,
        "owned_paths": owned_paths,
        "changed_files": changed_files,
        "violations": violations,
    }


def _feedback_cli_error(message: str, reference: str) -> int:
    sys.stderr.write(
        f"[feedback_guard] CLI error: {message}\n"
        "  Fix: check the command arguments and rerun the subcommand with --help.\n"
        f"  Reference: {reference}\n"
    )
    return 2


def _run_diff_check(worktree: Path, base_ref: str | None, head_ref: str | None) -> tuple[dict[str, Any] | None, int]:
    wt = worktree.expanduser()
    if not wt.is_dir() or not (wt / ".git").exists():
        return None, _feedback_cli_error(f"invalid git worktree: {wt}", "skills/team-lead/references/playbook.md")
    command = ["git", "-C", str(wt), "--no-pager", "diff", "--check"]
    if base_ref:
        command.append(f"{base_ref}..{head_ref or 'HEAD'}")
    elif head_ref and head_ref != "HEAD":
        command.append(head_ref)
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    output = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0 and (
        not output or any(line.lower().startswith(("fatal:", "error:", "usage:")) for line in output.splitlines())
    ):
        return None, _feedback_cli_error(output or "git diff --check failed", "skills/team-lead/references/playbook.md")
    issues = [line for line in output.splitlines() if line.strip()]
    payload = {
        "ok": True,
        "gate_tier": "soft_advisory_exit_0",
        "clean": proc.returncode == 0,
        "issue_count": len(issues),
        "issues": issues,
        "reference": "skills/team-lead/references/playbook.md",
    }
    return payload, 0


def _receipt_summary(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    data: dict[str, Any] = {}
    if path.suffix.lower() == ".json":
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                data = parsed
        except json.JSONDecodeError:
            data = {}
    sha_value = data.get("commit_sha") or data.get("head_sha") or data.get("sha")
    role_value = data.get("role") or data.get("agent_role")
    model_value = data.get("model") or data.get("resolved_model")
    status_value = data.get("status")
    sha_match = re.search(
        r"(?im)^\s*[>*-]*(?:\*\*)?(?:commit(?:\s+sha)?|head(?:\s+sha)?)(?:\*\*)?\s*[:|=-]\s*([0-9a-f]{7,40})\b",
        text,
    )
    status_match = re.search(r"(?im)^\s*[>*-]*(?:\*\*)?status(?:\*\*)?\s*[:|=-]\s*([^\n]+)", text)
    model_match = re.search(r"(?im)^\s*[>*-]*(?:\*\*)?(?:resolved_model|model)(?:\*\*)?\s*[:|=-]\s*([^\n]+)", text)
    sha = _clean_receipt_value(str(sha_value)) if sha_value else (sha_match.group(1) if sha_match else None)
    role = _clean_receipt_value(str(role_value)).lower() if role_value else _receipt_role(text)
    model = _clean_receipt_value(str(model_value)) if model_value else (_clean_receipt_value(model_match.group(1)) if model_match else None)
    status = _clean_receipt_value(str(status_value)) if status_value else (_clean_receipt_value(status_match.group(1)) if status_match else None)
    violations = validate_r10_receipt(text) if not data else []
    if data:
        if not sha or not re.fullmatch(r"[0-9a-f]{7,40}", sha, re.IGNORECASE):
            violations.append("missing_receipt_commit_sha")
        if not _is_populated_token(role or "") or not _is_populated_token(model or ""):
            violations.append("missing_receipt_role_or_model")
        if not _is_populated_token(status or ""):
            violations.append("missing_receipt_status")
    return {
        "name": path.name,
        "path": str(path),
        "status": status,
        "commit_sha": sha,
        "role": role,
        "model": model,
        "valid": not violations,
        "violations": violations,
    }


def _list_receipts(target: Path) -> tuple[dict[str, Any] | None, int]:
    directory = target.expanduser()
    if directory.is_dir() and directory.name != "receipts" and (directory / "receipts").is_dir():
        directory = directory / "receipts"
    if not directory.is_dir():
        return None, _feedback_cli_error(f"receipt directory does not exist: {directory}", "skills/team-lead/references/ledger.md")
    files = sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".md", ".json"}
    )
    receipts: list[dict[str, Any]] = []
    for path in files:
        try:
            receipts.append(_receipt_summary(path))
        except OSError as exc:
            receipts.append({"name": path.name, "path": str(path), "valid": False, "violations": [str(exc)]})
    return {
        "ok": True,
        "gate_tier": "soft_advisory_exit_0",
        "receipt_count": len(receipts),
        "receipts": receipts,
        "reference": "skills/team-lead/references/ledger.md",
    }, 0


class _SelfDisclosingFeedbackGuardParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        reference = getattr(self, "feedback_reference", "skills/team-lead/references/eval-cases.json | skills/team-lead/references/playbook.md")
        if "list-receipts" in self.prog or "inspect-receipts" in self.prog or "list-receipts" in sys.argv or "inspect-receipts" in sys.argv:
            reference = "skills/team-lead/references/ledger.md"
        elif "diff-check" in self.prog or "whitespace-check" in self.prog or "diff-check" in sys.argv or "whitespace-check" in sys.argv:
            reference = "skills/team-lead/references/playbook.md"
        sys.stderr.write(
            f"[feedback_guard] CLI error: {message}\n"
            "  Fix: check the command arguments and rerun the subcommand with --help.\n"
            f"  Reference: {reference}\n"
        )
        raise SystemExit(2)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = _SelfDisclosingFeedbackGuardParser(
        prog="feedback_guard.py",
        description="Feedback manifest & pre-review deterministic guardrails",
    )
    parser.add_argument("--json", action="store_true", default=True)
    sub = parser.add_subparsers(dest="command", required=True)

    p_pre = sub.add_parser(
        "pre-review-check",
        help="Validate Owned Paths, clean git status, personal path/token leaks, and R10 receipt headers",
    )
    p_pre.add_argument("--worktree", "--cwd", "--dir", dest="worktree", required=True, type=Path, help="Target git worktree directory")
    p_pre.add_argument("--brief", "--brief-file", dest="brief", required=True, type=Path, help="Task brief markdown declaring Owned Paths")
    p_pre.add_argument("--receipt", "--receipt-file", dest="receipt", type=Path, default=None, help="Optional R10 receipt markdown file")
    p_pre.add_argument("--base-ref", default=None, help="Optional git base ref (defaults to merge-base with origin/main)")
    p_pre.add_argument("--head-ref", "--head", dest="head_ref", default="HEAD", help="Optional git head ref (defaults to HEAD)")
    p_pre.add_argument("--allow-dirty", action="store_true", help="Allow uncommitted tracked changes during local self-check")
    p_pre.add_argument("--actor-role", default=None, help="Effective actor role for inline edit budget checks")
    p_pre.add_argument("--max-inline-files", type=int, default=3, help="Maximum files an M1/Steering actor may edit inline")
    p_pre.add_argument("--events-jsonl", "--trace-jsonl", dest="events_jsonl", type=Path, default=None, help="Optional events.jsonl trace for R10 verification grounding")
    p_pre.add_argument("--json", action="store_true", default=True, help="Emit JSON output (default True)")

    p_diff = sub.add_parser("diff-check", aliases=["whitespace-check"], help="Report git whitespace/conflict issues as a soft advisory")
    p_diff.feedback_reference = "skills/team-lead/references/playbook.md"
    p_diff.add_argument("--worktree", "--cwd", "--dir", dest="worktree", type=Path, default=Path("."))
    p_diff.add_argument("--base-ref", default=None)
    p_diff.add_argument("--head-ref", "--head", dest="head_ref", default="HEAD")
    p_diff.add_argument("--strict", action="store_true", help="Treat reported issues as exit 1")
    p_diff.add_argument("--json", action="store_true", default=True)

    p_receipts = sub.add_parser("list-receipts", aliases=["inspect-receipts"], help="List top-level R10 receipts as soft facts")
    p_receipts.feedback_reference = "skills/team-lead/references/ledger.md"
    p_receipts.add_argument("target", nargs="?", type=Path, default=None)
    p_receipts.add_argument("--handoff-dir", "--handoff", "--dir", dest="handoff_dir", type=Path, default=None)
    p_receipts.add_argument("--json", action="store_true", default=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_cli_parser().parse_args(argv)
    if args.command == "pre-review-check":
        report = run_pre_review_check(
            worktree=args.worktree,
            brief_path=args.brief,
            receipt_path=args.receipt,
            base_ref=args.base_ref,
            head_ref=args.head_ref,
            allow_dirty=args.allow_dirty,
            actor_role=args.actor_role,
            max_inline_files=args.max_inline_files,
            events_jsonl=args.events_jsonl,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if args.command in {"diff-check", "whitespace-check"}:
        payload, code = _run_diff_check(args.worktree, args.base_ref, args.head_ref)
        if payload is None:
            return code
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if args.strict and not payload["clean"] else 0
    if args.command in {"list-receipts", "inspect-receipts"}:
        target = args.handoff_dir or args.target
        if target is None:
            return _feedback_cli_error("a receipt directory or --handoff-dir is required", "skills/team-lead/references/ledger.md")
        payload, code = _list_receipts(target)
        if payload is None:
            return code
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
