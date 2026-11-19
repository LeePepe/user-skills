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


def validate_r10_receipt(receipt_text: str) -> list[str]:
    """Check required R10 receipt sections/fields (non-placeholder Commit SHA, non-empty Role/Model, non-empty Verification/V5)."""
    violations: list[str] = []
    sha_match = re.search(
        r"(?im)^[\s>*-]*(?:\*\*)?(?:commit(?:\s+sha)?|head(?:\s+sha)?)(?:\*\*)?[\s*`]*[:|=-][ \t]*(.+)$",
        receipt_text,
    )
    sha_val = _clean_receipt_value(sha_match.group(1)) if sha_match else ""
    if not re.search(r"\b[0-9a-f]{7,40}\b", sha_val, re.IGNORECASE):
        violations.append("missing_receipt_commit_sha: R10 receipt must record a real 7-40 hex Commit SHA")

    role_match = re.search(
        r"(?im)^[\s>*-]*(?:\*\*)?(?:role(?:\s*/\s*model)?|model|resolved_model|agent)(?:\*\*)?[\s*`]*[:|=-][ \t]*(.+)$",
        receipt_text,
    )
    role_val = _clean_receipt_value(role_match.group(1)) if role_match else ""
    if not role_val or role_val.lower() in {"n/a", "na", "none", "tbd", "todo", "-", "—"}:
        violations.append("missing_receipt_role_or_model: R10 receipt must record a non-empty Role / Model value")

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


def run_pre_review_check(
    *,
    worktree: Path,
    brief_path: Path,
    receipt_path: Path | None = None,
    base_ref: str | None = None,
    allow_dirty: bool = False,
) -> dict[str, Any]:
    """Deterministic pre-review gate catching recurring R1 Writer failures before Reviewer dispatch."""
    wt = worktree.expanduser().resolve()
    brief = brief_path.expanduser().resolve()
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
        ["git", "-C", str(wt), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if head_probe.returncode != 0:
        violations.append({
            "code": "unverified_head_commit",
            "detail": head_probe.stderr.strip() or "Worktree has no verifiable HEAD commit",
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
                ["git", "-C", str(wt), "merge-base", base_ref, "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            if mb.returncode != 0 or not mb.stdout.strip():
                violations.append({
                    "code": "invalid_base_ref",
                    "base_ref": base_ref,
                    "detail": mb.stderr.strip() or f"Cannot compute merge-base between '{base_ref}' and HEAD",
                })
            else:
                resolved_base = mb.stdout.strip()
    else:
        head_sha = head_probe.stdout.strip() if head_probe.returncode == 0 else ""
        for candidate in ("origin/main", "main", "HEAD~1"):
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
                ["git", "-C", str(wt), "merge-base", candidate, "HEAD"],
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
                "detail": "Could not resolve any valid git comparison base (origin/main, main, or HEAD~1)",
            })

    changed_files: list[str] = []
    combined_diff_parts: list[str] = []
    if resolved_base and head_probe.returncode == 0:
        diff_names = subprocess.run(
            ["git", "-C", str(wt), "--no-pager", "diff", "--name-only", f"{resolved_base}..HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if diff_names.returncode != 0:
            violations.append({
                "code": "git_diff_failed",
                "detail": diff_names.stderr.strip() or f"git diff --name-only {resolved_base}..HEAD failed",
            })
        else:
            changed_files.extend(
                f.strip() for f in diff_names.stdout.splitlines() if f.strip()
            )

        diff_proc = subprocess.run(
            ["git", "-C", str(wt), "--no-pager", "diff", "--unified=0", f"{resolved_base}..HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if diff_proc.returncode != 0:
            violations.append({
                "code": "git_diff_failed",
                "detail": diff_proc.stderr.strip() or f"git diff --unified=0 {resolved_base}..HEAD failed",
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

    if head_probe.returncode == 0:
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

    if receipt_path is not None:
        rcpt = receipt_path.expanduser().resolve()
        if not rcpt.is_file():
            violations.append({
                "code": "missing_receipt_file",
                "detail": f"R10 receipt file does not exist: {rcpt}",
            })
        else:
            for msg in validate_r10_receipt(rcpt.read_text(encoding="utf-8")):
                code, _, detail = msg.partition(": ")
                violations.append({"code": code, "detail": detail or msg})

    return {
        "ok": len(violations) == 0,
        "worktree": str(wt),
        "brief": str(brief),
        "receipt": str(receipt_path.expanduser().resolve()) if receipt_path is not None else None,
        "base_ref": resolved_base,
        "owned_paths": owned_paths,
        "changed_files": changed_files,
        "violations": violations,
    }


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Feedback manifest & pre-review deterministic guardrails")
    sub = parser.add_subparsers(dest="command", required=True)

    p_pre = sub.add_parser(
        "pre-review-check",
        help="Validate Owned Paths, clean git status, personal path/token leaks, and R10 receipt headers",
    )
    p_pre.add_argument("--worktree", required=True, type=Path, help="Target git worktree directory")
    p_pre.add_argument("--brief", required=True, type=Path, help="Task brief markdown declaring Owned Paths")
    p_pre.add_argument("--receipt", type=Path, default=None, help="Optional R10 receipt markdown file")
    p_pre.add_argument("--base-ref", default=None, help="Optional git base ref (defaults to merge-base with origin/main)")
    p_pre.add_argument("--allow-dirty", action="store_true", help="Allow uncommitted tracked changes during local self-check")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_cli_parser().parse_args(argv)
    if args.command == "pre-review-check":
        report = run_pre_review_check(
            worktree=args.worktree,
            brief_path=args.brief,
            receipt_path=args.receipt,
            base_ref=args.base_ref,
            allow_dirty=args.allow_dirty,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
