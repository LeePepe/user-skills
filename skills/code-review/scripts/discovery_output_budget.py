from __future__ import annotations

import json


MAX_STDOUT_BYTES = 16_000
DISCOVERY_PREVIEW_LIMIT = 20
DISCOVERY_MIN_PREVIEW_LIMIT = 1
REQUIRED_DISCOVERY_KEYS = (
    "repository",
    "branch",
    "base_ref",
    "fixed_point",
    "mode",
    "empty_surface",
    "commits",
    "diff_command",
    "changed_files",
    "context",
    "spec_kit",
    "layers",
    "unmapped_files",
    "coverage_gaps",
    "matched_review_smells",
)


def _render_json(payload: dict[str, object], *, compact: bool) -> str:
    if compact:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _limit_list(payload: dict[str, object], key: str, limit: int) -> None:
    values = payload.get(key)
    if not isinstance(values, list):
        return
    payload[f"{key}_total"] = len(values)
    payload[key] = values[: max(DISCOVERY_MIN_PREVIEW_LIMIT, limit)]


def _summarize_layer(
    layer: dict[str, object],
    *,
    changed_limit: int,
    include_untouched_details: bool,
    include_gates: bool,
    include_red_lines: bool,
) -> dict[str, object]:
    changed = layer.get("changed_files")
    changed_files = changed if isinstance(changed, list) else []
    touched = bool(changed_files)
    summarized: dict[str, object] = {
        "name": layer.get("name", ""),
        "root": layer.get("root", ""),
        "context": layer.get("context", ""),
        "depends_on": layer.get("depends_on", []),
        "depended_by": layer.get("depended_by", []),
        "changed_files_count": len(changed_files),
        "changed_files": changed_files[: max(DISCOVERY_MIN_PREVIEW_LIMIT, changed_limit)],
    }
    if touched or include_untouched_details:
        summarized["test"] = layer.get("test")
        if include_red_lines:
            summarized["red_lines"] = layer.get("red_lines", [])
        if include_gates and isinstance(layer.get("gates"), list):
            summarized["gates"] = layer.get("gates", [])
    return summarized


def _compact_context(context: object) -> dict[str, object]:
    if not isinstance(context, dict):
        return {"routing_indexes": [], "constitutions": [], "top_technical_context": [], "context_compacted": True}
    routing = context.get("routing_indexes")
    constitutions = context.get("constitutions")
    top_context = context.get("top_technical_context")
    return {
        "routing_indexes": routing[:1] if isinstance(routing, list) else [],
        "routing_indexes_total": len(routing) if isinstance(routing, list) else 0,
        "constitutions": constitutions[:1] if isinstance(constitutions, list) else [],
        "constitutions_total": len(constitutions) if isinstance(constitutions, list) else 0,
        "top_technical_context": top_context[:1] if isinstance(top_context, list) else [],
        "top_technical_context_total": len(top_context) if isinstance(top_context, list) else 0,
        "context_compacted": True,
    }


def _compact_discovery_result(
    result: dict[str, object],
    *,
    list_limit: int,
    layer_changed_limit: int,
    include_untouched_layers: bool,
    include_untouched_layer_details: bool,
    include_gates: bool,
    include_red_lines: bool,
    compact_context: bool = False,
) -> dict[str, object]:
    compacted = dict(result)
    for key in ("changed_files", "unmapped_files", "commits", "matched_review_smells"):
        _limit_list(compacted, key, list_limit)

    raw_layers = result.get("layers")
    layers = raw_layers if isinstance(raw_layers, list) else []
    touched: list[dict[str, object]] = []
    untouched: list[dict[str, object]] = []
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        changed_files = layer.get("changed_files")
        if isinstance(changed_files, list) and changed_files:
            touched.append(layer)
        else:
            untouched.append(layer)

    selected_layers = touched + (untouched if include_untouched_layers else [])
    compacted["layers"] = [
        _summarize_layer(
            layer,
            changed_limit=layer_changed_limit,
            include_untouched_details=include_untouched_layer_details,
            include_gates=include_gates,
            include_red_lines=include_red_lines,
        )
        for layer in selected_layers
    ]
    compacted["layers_total"] = len(layers)
    compacted["layers_touched_total"] = len(touched)
    if untouched:
        compacted["untouched_layers_total"] = len(untouched)
        if not include_untouched_layers:
            compacted["layers_omitted_count"] = len(untouched)
    if compact_context:
        compacted["context"] = _compact_context(result.get("context"))

    compacted["output_truncated"] = True
    compacted["full_hint"] = "run with --full for uncompacted output, or scope with --range/--base"
    return compacted


def _ensure_discovery_keys(payload: dict[str, object], full: dict[str, object]) -> dict[str, object]:
    ensured = dict(payload)
    defaults: dict[str, object] = {
        "commits": [],
        "changed_files": [],
        "context": {"routing_indexes": [], "constitutions": [], "top_technical_context": []},
        "spec_kit": {},
        "layers": [],
        "unmapped_files": [],
        "coverage_gaps": [],
        "matched_review_smells": [],
    }
    for key in REQUIRED_DISCOVERY_KEYS:
        if key not in ensured:
            ensured[key] = full.get(key, defaults.get(key))
    return ensured


def render_discovery_result(result: dict[str, object], *, summary: bool = False, full: bool = False) -> str:
    full_rendered = _render_json(result, compact=False)
    if full:
        return full_rendered
    if not summary and len(full_rendered.encode("utf-8")) <= MAX_STDOUT_BYTES:
        return full_rendered

    profiles = [
        {
            "list_limit": DISCOVERY_PREVIEW_LIMIT,
            "layer_changed_limit": DISCOVERY_PREVIEW_LIMIT,
            "include_untouched_layers": True,
            "include_untouched_layer_details": True,
            "include_gates": True,
            "include_red_lines": True,
            "compact_context": False,
            "compact_json": False,
        },
        {
            "list_limit": 12,
            "layer_changed_limit": 12,
            "include_untouched_layers": False,
            "include_untouched_layer_details": False,
            "include_gates": False,
            "include_red_lines": True,
            "compact_context": False,
            "compact_json": False,
        },
        {
            "list_limit": 6,
            "layer_changed_limit": 6,
            "include_untouched_layers": False,
            "include_untouched_layer_details": False,
            "include_gates": False,
            "include_red_lines": False,
            "compact_context": False,
            "compact_json": False,
        },
        {
            "list_limit": 3,
            "layer_changed_limit": 3,
            "include_untouched_layers": False,
            "include_untouched_layer_details": False,
            "include_gates": False,
            "include_red_lines": False,
            "compact_context": True,
            "compact_json": True,
        },
        {
            "list_limit": 1,
            "layer_changed_limit": 1,
            "include_untouched_layers": False,
            "include_untouched_layer_details": False,
            "include_gates": False,
            "include_red_lines": False,
            "compact_context": True,
            "compact_json": True,
        },
    ]
    compacted: dict[str, object] = {}
    for profile in profiles:
        compacted = _compact_discovery_result(result, **{k: v for k, v in profile.items() if k != "compact_json"})
        rendered = _render_json(_ensure_discovery_keys(compacted, result), compact=bool(profile["compact_json"]))
        if len(rendered.encode("utf-8")) <= MAX_STDOUT_BYTES:
            return rendered
    return _render_json(_ensure_discovery_keys(compacted, result), compact=True)
