#!/usr/bin/env python3
"""Deterministic Phase 0 proposals, checkpoints, and spec/design CAS locks."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

EXCLUDED_HASH_KEYS = {"spec_sha", "design_sha", "updated_at"}
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        if value in {"null", "Null", "NULL", "~"}:
            return None
        if value in {"true", "True"}:
            return True
        if value in {"false", "False"}:
            return False
        if value.startswith("[") and value.endswith("]"):
            return [item.strip().strip("\"'") for item in value[1:-1].split(",") if item.strip()]
        return value.strip("\"'")


def parse_document(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text.replace("\r\n", "\n").replace("\r", "\n"))
    if not match:
        return {}, text
    fields: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = _scalar(value)
    return fields, match.group(2)


def _canonical_body(body: str) -> str:
    return body.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"


def canonical_spec_digest(markdown_text: str) -> str:
    fields, body = parse_document(markdown_text)
    filtered = {key: value for key, value in fields.items() if key not in EXCLUDED_HASH_KEYS}
    header = json.dumps(filtered, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    payload = header + "\n---\n" + _canonical_body(body)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str) and (not value or re.search(r"[:#{}\[\],\"]", value)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_document(fields: dict[str, Any], body: str) -> str:
    header = "\n".join(f"{key}: {_yaml_scalar(fields[key])}" for key in fields)
    return f"---\n{header}\n---\n{_canonical_body(body)}"


@contextlib.contextmanager
def locked(handoff_dir: Path) -> Iterator[None]:
    handoff_dir.mkdir(parents=True, exist_ok=True)
    lock_path = handoff_dir / ".queue.lock"
    with lock_path.open("a+") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _proposal_sha(data: dict[str, Any]) -> str:
    proposals = data.get("proposals", [])
    payload = json.dumps(proposals, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _topic(data: dict[str, Any], handoff_dir: Path) -> dict[str, Any]:
    if data:
        return data
    return {
        "schema": "phase0-proposals/v1",
        "goal": handoff_dir.name,
        "topic_id": handoff_dir.name,
        "round": 1,
        "target_artifact": "spec.md",
        "base_version": 1,
        "proposals": [],
        "proposals_sha": _proposal_sha({"proposals": []}),
    }


def command_push(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    handoff = Path(args.handoff_dir)
    with locked(handoff):
        path = handoff / "proposals.json"
        data = _topic(_read_json(path, {}), handoff)
        proposals = data.setdefault("proposals", [])
        proposal = {
            "id": args.proposal_id,
            "label": args.label,
            "agent_name": args.agent,
            "model": args.model,
            "interface_summary": args.interface_summary,
            "seam_placement": args.seam_placement,
            "dependency_category": args.dependency_category,
            "recommended": bool(args.recommended),
            "content": args.content or "",
        }
        existing = next((p for p in proposals if p.get("id") == args.proposal_id), None)
        if existing is None:
            proposals.append(proposal)
        else:
            existing.update(proposal)
        old_sha = data.get("proposals_sha")
        data["proposals_sha"] = _proposal_sha(data)
        if old_sha != data["proposals_sha"]:
            checkpoint = data.get("latest_checkpoint")
            if checkpoint and checkpoint.get("proposals_sha") != data["proposals_sha"]:
                data["latest_checkpoint_status"] = "stale_checkpoint"
        _write_json(path, data)
        return 0, {"ok": True, "proposal_id": args.proposal_id, "proposals_sha": data["proposals_sha"]}


def command_checkpoint(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    handoff = Path(args.handoff_dir)
    with locked(handoff):
        path = handoff / "proposals.json"
        data = _read_json(path, {})
        if not data:
            return 1, {"ok": False, "status": "no_topic"}
        proposal = next((p for p in data.get("proposals", [])
                         if p.get("id") == args.proposal_id and
                         p.get("topic_id", data.get("topic_id", handoff.name)) == data.get("topic_id", handoff.name)), None)
        if proposal is None:
            return 1, {"ok": False, "code": "INVALID_PROPOSAL_TOPIC", "status": 409}
        checkpoint = {
            "id": f"chk-{hashlib.sha256((args.proposal_id + args.decided_by).encode()).hexdigest()[:16]}",
            "topic_id": data.get("topic_id", handoff.name),
            "selected_proposal_id": args.proposal_id,
            "proposals_sha": data.get("proposals_sha", _proposal_sha(data)),
            "target_spec_version": data.get("target_spec_version", _current_spec_version(handoff)),
            "decided_by": args.decided_by,
            "decision": args.decision,
            "reasoning": args.reasoning or "",
            "actions": args.action or [],
        }
        data["latest_checkpoint"] = checkpoint
        data["latest_checkpoint_status"] = "current"
        _write_json(path, data)
        return 0, {"ok": True, "checkpoint_id": checkpoint["id"], "proposals_sha": checkpoint["proposals_sha"]}


def command_pop(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    handoff = Path(args.handoff_dir)
    with locked(handoff):
        data = _read_json(handoff / "proposals.json", {})
        if not data:
            return 1, {"ok": False, "status": "no_topic"}
        checkpoint = data.get("latest_checkpoint")
        if not checkpoint:
            return 1, {"ok": False, "status": "pending"}
        if checkpoint.get("proposals_sha") != data.get("proposals_sha"):
            return 1, {"ok": False, "status": "stale_checkpoint", "checkpoint": checkpoint}
        return 0, {"ok": True, "checkpoint": checkpoint}


def command_status(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    data = _read_json(Path(args.handoff_dir) / "proposals.json", {})
    if not data:
        return 0, {"ok": True, "status": "no_topic", "proposals": [], "proposals_sha": None}
    checkpoint = data.get("latest_checkpoint")
    status = "pending" if checkpoint is None else (
        "decided" if checkpoint.get("proposals_sha") == data.get("proposals_sha") else "stale_checkpoint"
    )
    return 0, {"ok": True, "status": status, "proposals": data.get("proposals", []),
               "proposals_sha": data.get("proposals_sha"), "latest_checkpoint": checkpoint}


def _file_fields(path: Path) -> tuple[dict[str, Any], str, str]:
    text = path.read_text(encoding="utf-8")
    fields, body = parse_document(text)
    return fields, body, canonical_spec_digest(text)


def _current_spec_version(handoff: Path) -> int:
    spec_path = handoff / "spec.md"
    if not spec_path.exists():
        return 1
    fields, _, _ = _file_fields(spec_path)
    return int(fields.get("version", 1))


def command_stamp(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    path = Path(args.file)
    handoff = path.parent
    with locked(handoff):
        fields, body, current_sha = _file_fields(path)
        actual_version = int(fields.get("version", 1))
        if args.expected_version is not None and actual_version != args.expected_version:
            return 1, {"ok": False, "code": "SPEC_VERSION_CONFLICT", "status": 409,
                       "expected_version": args.expected_version, "actual_version": actual_version}
        if args.expected_sha is not None and current_sha != args.expected_sha:
            return 1, {"ok": False, "code": "SPEC_VERSION_CONFLICT", "status": 409,
                       "expected_sha": args.expected_sha, "actual_sha": current_sha}
        if args.bump_version:
            fields["version"] = actual_version + 1
        sha_key = "design_sha" if "design" in path.name else "spec_sha"
        fields.pop(sha_key, None)
        stamped = render_document(fields, body)
        fields[sha_key] = canonical_spec_digest(stamped)
        final = render_document(fields, body)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(final)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return 0, {"ok": True, "file": str(path), "version": fields.get("version"), sha_key: fields[sha_key]}


def command_verify_lock(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    handoff = Path(args.handoff_dir)
    spec_path = handoff / "spec.md"
    if not spec_path.exists():
        return 1, {"ok": False, "status": "NO_SPEC"}
    spec_fields, _, spec_actual = _file_fields(spec_path)
    issues: list[str] = []
    if spec_fields.get("spec_sha") != spec_actual:
        issues.append("STALE_SPEC")
    design_path = handoff / "design.md"
    design_fields: dict[str, Any] = {}
    if design_path.exists():
        design_fields, _, _ = _file_fields(design_path)
        if design_fields.get("parent_spec_version") != spec_fields.get("version") or design_fields.get("parent_spec_sha") != spec_fields.get("spec_sha"):
            issues.append("STALE_DESIGN")
    mandate: Any = args.mandate_json
    if mandate:
        if Path(str(mandate)).exists():
            mandate = Path(str(mandate)).read_text(encoding="utf-8")
        try:
            mandate = json.loads(mandate)
        except (TypeError, json.JSONDecodeError):
            mandate = {}
        if mandate.get("spec_sha") not in (None, spec_fields.get("spec_sha")) or (
            design_fields and mandate.get("design_sha") not in (None, design_fields.get("design_sha"))
        ):
            issues.append("STALE_MANDATE")
    return (1 if issues else 0), {"ok": not issues, "issues": issues, "spec_sha": spec_fields.get("spec_sha"),
                                  "version": spec_fields.get("version")}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    push = sub.add_parser("push")
    push.add_argument("--handoff-dir", required=True); push.add_argument("--agent", required=True)
    push.add_argument("--model", required=True); push.add_argument("--proposal-id", required=True)
    push.add_argument("--label", required=True); push.add_argument("--interface-summary", required=True)
    push.add_argument("--seam-placement", required=True)
    push.add_argument("--dependency-category", required=True, choices=["in_process", "local_substitutable", "remote_owned", "true_external"])
    push.add_argument("--recommended", action="store_true"); push.add_argument("--content")
    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("--handoff-dir", required=True); checkpoint.add_argument("--proposal-id", required=True)
    checkpoint.add_argument("--decision", required=True); checkpoint.add_argument("--decided-by", required=True)
    checkpoint.add_argument("--reasoning"); checkpoint.add_argument("--action", action="append")
    for name in ("pop", "status"):
        command = sub.add_parser(name); command.add_argument("--handoff-dir", required=True)
    stamp = sub.add_parser("stamp"); stamp.add_argument("--file", required=True)
    stamp.add_argument("--expected-version", type=int); stamp.add_argument("--expected-sha")
    stamp.add_argument("--bump-version", action="store_true")
    verify = sub.add_parser("verify-lock"); verify.add_argument("--handoff-dir", required=True)
    verify.add_argument("--mandate-json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {
        "push": command_push, "checkpoint": command_checkpoint, "pop": command_pop,
        "status": command_status, "stamp": command_stamp, "verify-lock": command_verify_lock,
    }[args.command]
    code, result = handler(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
