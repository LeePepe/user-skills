"""Domain modules for the my-designer compatibility facade."""
from __future__ import annotations

from . import audit as _audit
from . import cli as _cli
from . import route as _route
from . import semiotics_gates as _semiotics_gates
from . import shared as _shared
from . import vision_audit as _vision_audit
from .audit import (
    audit_subtractive_spec_text,
    command_audit_code,
    command_audit_geometry,
    command_audit_spec,
    command_check_section_interactivity,
    command_check_whitespace_ratio,
    command_lookup_defect,
    detect_spec_gap_warnings,
)
from .cli import (
    _gallery_errors,
    _jev_response,
    _merge_targets,
    _platform_errors,
    build_parser,
    command_inspect_json,
    command_jev_gate,
    main,
)
from .route import (
    command_flow_map,
    command_register_case,
    command_route,
    match_active_cases,
)
from .semiotics_gates import (
    INSTRUCTIONAL_MICRO_COPY_TEXT_RE,
    PHYSICAL_TELEMETRY_TOKEN_RE,
    SCROLL_EXPAND_PROMPT_TEXT_RE,
    SPEC_GAP_GUIDANCE,
    SPEC_SCROLL_PROMPT_NEGATION_RE,
    SPEC_SCROLL_PROMPT_PROPOSAL_RE,
    UI_CROSS_LAYER_METRIC_ECHO_FIX_RULE,
    UI_INSTRUCTIONAL_CLUTTER_RE,
    UI_LARGE_FONT_RE,
    UI_MAP_COMPONENT_CALL_RE,
    UI_PRIMARY_NAV_CTA_RE,
    UI_REPEATED_DETAIL_LABEL_RE,
    UI_SATURATED_COLOR_RE,
    UI_SCROLL_PROMPT_CLUTTER_FIX_RULE,
    UI_SCROLL_PROMPT_CLUTTER_RE,
    UI_SPEC_ID_DEBUG_LEAK_RE,
)
from .shared import (
    ALWAYS_LOAD_HIG,
    CONCRETE_TEST_REF_RE,
    DEFAULT_CASES,
    DEFAULT_TAXONOMY,
    DRAWER_GUIDANCE,
    EVIDENCE,
    EXTERNAL_SKILL_SOURCES,
    JEV,
    NEGATIVE_ASSERTION_EVIDENCE_RE,
    NEGATIVE_ASSERTION_HEADING_RE,
    OBSOLETE_TEST_HEADING_RE,
    PERSONA_WHITESPACE_PROFILES,
    PLAN_A_LENSES,
    ROOT,
    RUNTIME_SF_DATALESS,
    SF_DATALESS,
    SPEC_CUSTOM_THEME_NEGATION_RE,
    SPEC_CUSTOM_THEME_TOGGLE_PROPOSAL_RE,
    SUBTRACTIVE_MANDATE_RE,
    SelfDisclosingArgumentParser,
    TAXONOMY,
    UI_CUSTOM_THEME_PLUMBING_RE,
    UI_EXPAND_STATE_MUTATION_RE,
    UI_HEADER_INTERLEAVED_INSERTION_RE,
    UI_UNCLIPPED_PANEL_OVERLAP_RE,
    VAGUE_OBSOLETE_TEST_RE,
    WEB_DIMENSION_SLICES,
    _FALLBACK_TAXONOMY,
    _audit_visual_semiotics_swift_gates,
    _balanced_brace_end,
    _build_self_disclosing_guidance,
    _csv,
    _drawer_keys_for_case,
    _has_enclosing_clipped_reveal_container,
    _interactivity_result,
    _load_defect_entries,
    _load_json,
    _load_taxonomy,
    _mask_swift,
    _normalize_platform,
    _read_json,
    _resolve_persona_whitespace,
    _save_json,
    compact_json_dumps,
    redact_payload,
    safe_read_text,
)
from .vision_audit import (
    VISION_AUDIT_SCHEMA,
    command_vision_audit,
    discover_pngs,
    run_vision_audit,
    validate_vision_response,
)

audit = _audit
cli = _cli
route = _route
semiotics_gates = _semiotics_gates
shared = _shared
vision_audit = _vision_audit

_MODULES = [_shared, _semiotics_gates, _audit, _route, _vision_audit, _cli]
_ORIGINAL_SYMBOLS = {
    name: value
    for name, value in globals().items()
    if not name.startswith("__") and name not in {"_MODULES", "_ORIGINAL_SYMBOLS"}
}
__all__ = [name for name in _ORIGINAL_SYMBOLS]
