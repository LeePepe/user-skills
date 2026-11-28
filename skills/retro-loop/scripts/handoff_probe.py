"""Retro-loop adapter for the bounded agent-retro handoff probe."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


def _probe_module():
    path = Path(__file__).resolve().parents[2] / "agent-retro" / "scripts" / "agent_retro_core" / "handoff_probe.py"
    spec = importlib.util.spec_from_file_location("retro_loop_handoff_probe", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def detect_handoff_trace_signals(
    trace_export_path: Path | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    records = [dict(event) for event in (events or []) if isinstance(event, Mapping)]
    if trace_export_path is not None and trace_export_path.is_file():
        try:
            for line in trace_export_path.read_text(encoding="utf-8").splitlines()[:100_000]:
                if line.strip():
                    value = json.loads(line)
                    if isinstance(value, dict):
                        records.append(value)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
    probe = _probe_module()
    return probe.detect_handoff_signals(records) if probe is not None else []


def build_handoff_trace_proposals(signals: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    proposals = []
    for signal in signals:
        code = str(signal.get("code") or "").strip()
        if code not in {"HANDOFF_INSTRUCTION_DROP", "M2_ROUTER_PARAM_TRAP"}:
            continue
        proposals.append({
            "id": f"retro-{code.lower()}",
            "ref": f"retro://handoff/{code.lower()}",
            "metric_id": code,
            "layer": "H2/H4",
            "category": code.lower(),
            "skill": str(signal.get("upstream_skill") or "personal-m2-assistant"),
            "priority": "P1",
            "title": f"[Handoff] {code} detected in dispatched trace",
            "detail": (
                f"root_cause={signal.get('root_cause', 'handoff parameter trap')} | "
                f"evidence={' ; '.join(str(item) for item in signal.get('evidence') or [])} | "
                "physical_lock=L2 CLI schema/completeness lock in m2-router.py"
            ),
            "plain_reason": "Repair dropped target context and reject unmapped domain/unspecified space fallbacks before dispatch.",
            "action": "Add an L2 payload-completeness/schema lock and preserve target instruction/sub_prompt plus mapped domain space.",
            "scope": "personal",
        })
    return proposals


def collect_handoff_trace_proposals(
    trace_export_path: Path | None,
    auto_propose: bool,
    proposals_path: Path | None,
    route: Callable[..., dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    signals = detect_handoff_trace_signals(trace_export_path)
    proposals = build_handoff_trace_proposals(signals)
    if not auto_propose or proposals_path is None:
        return signals, proposals
    queued = [
        route(item, mode="observe", proposals_path=proposals_path, deduplicate_pending=True)["proposal"]
        for item in proposals
    ]
    return signals, queued
