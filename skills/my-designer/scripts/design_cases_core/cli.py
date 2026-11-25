from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import shared
from . import audit
from . import route
from .shared import *
from .audit import *
from .route import *

for _module in (shared, audit, route):
    for _name in dir(_module):
        if not _name.startswith("__"):
            globals().setdefault(_name, getattr(_module, _name))

def _jev_response(args, payload, platform):
    return JEV.response(args, payload, platform, TAXONOMY)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _platform_errors(platform, text):
    return EVIDENCE.platform_errors(platform, text)


def _gallery_errors(root, manifest):
    return EVIDENCE.gallery_errors(root, manifest)


def command_jev_gate(args):
    plat = getattr(args, 'platform', None) or getattr(args, 'top_platform', None) or 'desktop'
    args.platform = _normalize_platform(plat)
    errors, evidence = [], []
    if args.stage == 'gallery':
        if not args.gallery_dir:
            errors.append('gallery stage requires --gallery-dir')
        else:
            root = Path(args.gallery_dir)
            manifest = _read_json(root / 'manifest.json')
            if not isinstance(manifest, dict):
                errors.append('missing or malformed manifest.json')
            else:
                if manifest.get('platform') != args.platform:
                    errors.append('manifest platform does not match gate platform')
                found, evidence = _gallery_errors(root, manifest)
                errors.extend(found)
    else:
        try:
            if not args.spec_file:
                raise ValueError('spec/traceback stage requires --spec-file')
            text = Path(args.spec_file).read_text(encoding='utf-8')
            if args.stage == 'spec':
                for token in ('Axis X', 'Axis Y', 'Axis Z'):
                    if token not in text:
                        errors.append(f'missing {token}')
                if not re.search(r'(?i)(single\s+P0|one\s+P0|唯一\s*P0|P0[^\n]{0,40}(?:primary|per viewport|唯一))', text):
                    errors.append('missing single P0 focal evidence')
                for sub_err in audit_subtractive_spec_text(text, str(args.spec_file)):
                    errors.append(f"{sub_err['rule']}: {sub_err['message']}")
                errors.extend(_platform_errors(args.platform, text))
                evidence = [{'spec_text': text, 'platform': args.platform}]
            else:
                found, evidence = EVIDENCE.traceback_errors(text, base_dir=Path(args.spec_file).resolve().parent, platform=args.platform)
                errors.extend(found)
        except (OSError, ValueError) as error:
            errors.append(str(error))
    try:
        taxonomy = _load_taxonomy(args.taxonomy)
        if not isinstance(taxonomy, dict):
            raise ValueError('taxonomy root must be an object')
    except (OSError, ValueError) as error:
        errors.append(f'invalid taxonomy: {error}')
        taxonomy = {}
    payload = {'platform': args.platform, 'stage': args.stage, 'evidence': evidence, 'deterministic_errors': errors}
    jev = JEV.response(args, payload, args.platform, taxonomy)
    ok = not errors and jev['status'] == 'PASS'
    result: dict[str, Any] = {'ok': ok, 'platform': args.platform, 'stage': args.stage,
              'status': 'PASS' if ok else 'REJECT_REVISE', 'errors': errors, 'evidence': evidence, 'jev': jev}
    if not ok:
        stage_drawers = ['architecture', 'appearance'] if args.stage == 'gallery' else (
            ['navigation', 'breathing', 'modernization'] if args.stage == 'spec' else ['architecture', 'navigation']
        )
        drawers, guidance = _build_self_disclosing_guidance(['eval', *stage_drawers])
        result['reference_drawers'] = drawers
        result['guidance'] = guidance
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def command_inspect_json(args):
    path = Path(args.inspect_path).expanduser()
    try:
        data = _load_json(path)
    except (OSError, ValueError) as error:
        drawers, guidance = _build_self_disclosing_guidance(['routing', 'eval'])
        print(json.dumps({'ok': False, 'mode': 'soft_advisory', 'error': f'invalid JSON artifact: {error}',
                          'reference_drawers': drawers, 'guidance': guidance}, ensure_ascii=False))
        sys.stderr.write('\n'.join(guidance) + '\n')
        return 1
    kind = 'generic-json'
    if isinstance(data, dict):
        summary = {'keys': sorted(data), 'key_count': len(data),
                   'counts': {k: len(v) for k, v in data.items() if isinstance(v, (dict, list))}}
        if path.name.startswith('preview-selection') or 'selectedVariantID' in data:
            kind = 'preview-selection'
            summary.update({k: data.get(k) for k in ('selectedVariantID', 'starredVariantIDs', 'notesByVariant')})
        elif data.get('schema') == 'visual-traceback/v1':
            kind = 'visual-traceback/v1'
            summary.update({'findings': data.get('findings', []), 'rendered_evidence': data.get('rendered_evidence', [])})
        elif data.get('schema') == 'skill-eval-cases/v1' or 'active_cases' in data:
            kind = data.get('schema') or 'defect-cases/index'
            summary['active_cases'] = data.get('active_cases', [])
    else:
        summary = {'type': type(data).__name__, 'count': len(data) if isinstance(data, list) else 1}
    print(json.dumps({'ok': True, 'mode': 'soft_advisory', 'artifact_kind': kind, 'summary': summary}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = SelfDisclosingArgumentParser(add_help=False)
    common.add_argument("--cases-file", default=str(DEFAULT_CASES))
    common.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    common.add_argument("--json", action="store_true", help="Emit JSON output (default)")
    parser = SelfDisclosingArgumentParser(description=__doc__, parents=[common])
    parser.add_argument("--check-whitespace-ratio", dest="top_whitespace_path", nargs="?", const="__PATH_REQUIRED__", default=None, metavar="PNG_OR_DIR")
    parser.add_argument("--path", "--dir", dest="top_path_alias", default=None, metavar="PATH")
    parser.add_argument("--max-dead-space", "--max-ratio", dest="top_max_dead_space", type=float, default=None)
    parser.add_argument("--persona", dest="top_persona", default=None, help="Tier 2 Product Persona scope (compact|default|breathing)")
    parser.add_argument("--audit-geometry", dest="top_geometry_path", default=None, metavar="GEOMETRY_JSON")
    parser.add_argument("--check-section-interactivity", dest="top_interactivity_path", nargs="?", const="__SNIPPET_ONLY__", default=None, metavar="SWIFT_OR_DIR")
    parser.add_argument("--audit-code", dest="top_audit_code", nargs="*", action="extend", default=None, metavar="PATH")
    parser.add_argument("--audit-spec", dest="top_audit_spec", nargs="*", action="extend", default=None, metavar="SPEC_MD")
    parser.add_argument("--strict-spec-gaps", dest="top_strict_spec_gaps", action="store_true")
    parser.add_argument("--snippet", dest="top_snippet", default=None)
    parser.add_argument("--lookup-defect", dest="top_lookup_defect", default=None, metavar="KEYWORD")
    parser.add_argument("--inspect-json", dest="top_inspect_json", default=None, metavar="JSON_PATH")
    parser.add_argument("--collision-threshold", type=float, default=2.0)
    parser.add_argument("--platform", "--surface", dest="top_platform", choices=["desktop", "macos", "mobile", "ios", "web"], default=None)
    sub = parser.add_subparsers(dest="command", required=False)

    sub.add_parser("flow-map", parents=[common])
    inspect = sub.add_parser("inspect-json", parents=[common], help="Read-only JSON fact collector; never certifies evidence")
    inspect.add_argument("inspect_path", nargs="?", metavar="JSON_PATH")
    inspect.add_argument("--path", "--dir", dest="inspect_path_flag", default=None, metavar="JSON_PATH")

    route = sub.add_parser("route", parents=[common])
    route.add_argument("--platform", choices=["desktop", "macos", "mobile", "ios", "web"], default=None)
    route.add_argument("--dimensions", "--dimension", default="")
    route.add_argument("--container", "--containers", default=None)
    route.add_argument("--persona", default=None, help="Tier 2 Product Persona scope (compact|default|breathing)")
    route.add_argument("--archetype", default=None, help="Alias for --persona / style archetype hint")
    route.add_argument("--surface", default=None, help="Platform alias; with --platform, may be a UI surface hint")
    route.add_argument("--path", default=None, help="Optional target source path hint")

    audit = sub.add_parser(
        "audit-code",
        aliases=["audit-swift", "check-code", "audit"],
        parents=[common],
        help="Scan Swift/Web source against active defect cases and instructional clutter",
    )
    audit.add_argument("positional_paths", nargs="*", default=[], metavar="PATH")
    audit.add_argument("--platform", "--surface", choices=["desktop", "macos", "mobile", "ios", "web"], default=None)
    audit.add_argument("--path", "--paths", "--files", "--dir", dest="path", nargs="+", action="extend", default=None)
    audit.add_argument("--root", dest="root_dir", default=None)
    audit.add_argument("--ui-path", dest="ui_path", default=None)
    audit.add_argument("--snippet", default=None)
    audit.add_argument("--spec-file", "--spec", dest="spec_files_list", action="append", default=None, metavar="SPEC_MD")
    audit.add_argument("--check-whitespace-ratio", default=None, metavar="PNG_OR_DIR")
    audit.add_argument("--max-dead-space", "--max-ratio", dest="max_dead_space", type=float, default=None)
    audit.add_argument("--persona", default=None)
    audit.add_argument("--audit-geometry", default=None, metavar="GEOMETRY_JSON")
    audit.add_argument("--collision-threshold", type=float, default=2.0)

    spec_audit = sub.add_parser("audit-spec", parents=[common], help="Audit design.md/spec.md for subtractive Negative DOM/View Assertions and concrete Obsolete Test Manifest")
    spec_audit.add_argument("positional_specs", nargs="*", default=[], metavar="SPEC_MD")
    spec_audit.add_argument("--spec-file", "--spec", "--file", "--path", "--dir", dest="spec_files_list", action="append", default=None, metavar="SPEC_MD")
    spec_audit.add_argument("--strict-spec-gaps", action="store_true", help="Fail when pre-implementation [SPEC_GAP] warnings are present")

    ws = sub.add_parser("check-whitespace-ratio", parents=[common], help="Reject full-viewport screenshots with excessive bottom contiguous dead space")
    ws.add_argument("positional_paths", nargs="*", default=[], metavar="PNG_OR_DIR")
    ws.add_argument("--platform", "--surface", choices=["desktop", "macos", "mobile", "ios", "web"], default=None)
    ws.add_argument("--path", "--dir", "--file", "--image", "--png-or-dir", "--check-whitespace-ratio", dest="path", action="append", default=None, metavar="PNG_OR_DIR")
    ws.add_argument("--max-dead-space", "--max-ratio", dest="max_dead_space", type=float, default=None)
    ws.add_argument("--persona", default=None, help="Context-scoped Tier 2 persona: compact (0.25), default (0.40), breathing (0.55 + >=0.12 advisory)")

    geom = sub.add_parser("audit-geometry", parents=[common], help="Audit subtree leaf AABB collisions (>2pt^2 -> GEOM_TEXT_COLLISION) and layout bounds")
    geom.add_argument("positional_paths", nargs="*", default=[], metavar="GEOMETRY_JSON")
    geom.add_argument("--path", "--file", "--audit-geometry", dest="path", action="append", default=None, metavar="GEOMETRY_JSON")
    geom.add_argument("--platform", "--surface", choices=["desktop", "macos", "mobile", "ios", "web"], default=None)
    geom.add_argument("--collision-threshold", type=float, default=2.0)

    interactivity = sub.add_parser("check-section-interactivity", parents=[common], help="Reject static SwiftUI section/preview placeholders")
    interactivity.add_argument("positional_paths", nargs="*", default=[], metavar="SWIFT_OR_DIR")
    interactivity.add_argument("--path", "--file", "--dir", dest="interactivity_path", default=None, metavar="SWIFT_OR_DIR")
    interactivity.add_argument("--snippet", default=None)

    lookup = sub.add_parser("lookup-defect", parents=[common], help="Query the deterministic defect-case index")
    lookup.add_argument("positional_query", nargs="*", default=[], metavar="KEYWORD")
    lookup.add_argument("--query", "--keyword", dest="query", default=None)
    lookup.set_defaults(lookup_defect=None)

    gate = sub.add_parser("jev-gate", parents=[common], help="Fail-closed structural and Jev truth-table gate")
    gate.add_argument("--platform", "--surface", choices=["desktop", "macos", "mobile", "ios", "web"], default=None)
    gate.add_argument("--stage", choices=["gallery", "spec", "traceback"], required=True)
    gate.add_argument("--gallery-dir", "--dir", dest="gallery_dir", default=None)
    gate.add_argument("--spec-file", "--spec", "--file", dest="spec_file", default=None)
    gate.add_argument("--no-jev", action="store_true")
    gate.add_argument("--mock-jev-json")
    gate.add_argument("--jev-url", default="http://127.0.0.1:19823/v1/systemone")
    gate.add_argument("--jev-timeout", type=float, default=1.5)

    reg = sub.add_parser("register-case", parents=[common])
    reg.add_argument("--id", default="auto", help="Case ID (e.g. DC-005) or 'auto' to assign the next sequential DC-00N")
    reg.add_argument("--auto-id", action="store_true", help="Automatically assign the next sequential DC-00N ID")
    reg.add_argument("--title", required=True)
    reg.add_argument("--platforms", default="desktop,mobile")
    reg.add_argument("--dimensions", default="")
    reg.add_argument("--containers", default="")
    reg.add_argument("--symptom", required=True)
    reg.add_argument("--fix-rule", required=True)
    reg.add_argument("--forbidden-regex", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else list(sys.argv[1:])
    # argparse's nargs=+ otherwise treats an immediately following strict flag as a missing path.
    if raw_argv is not None and "--audit-spec" in raw_argv and "--strict-spec-gaps" in raw_argv:
        audit_index = raw_argv.index("--audit-spec")
        strict_index = raw_argv.index("--strict-spec-gaps")
        if strict_index == audit_index + 1:
            raw_argv[audit_index:audit_index + 2] = ["--strict-spec-gaps", "--audit-spec"]
    args = parser.parse_args(raw_argv)
    platform = getattr(args, 'platform', None) or args.top_platform
    if args.command == 'route' and args.surface and not platform:
        if args.surface not in {'ios', 'macos', 'mobile', 'desktop', 'web'}:
            parser.error('--surface UI hints require --platform; otherwise use ios|macos|mobile|desktop|web')
        platform = args.surface
    args.platform = _normalize_platform(platform or 'desktop')
    if args.command == 'inspect-json' or args.top_inspect_json:
        if args.top_inspect_json:
            args.inspect_path = args.top_inspect_json
        elif getattr(args, 'inspect_path_flag', None):
            args.inspect_path = args.inspect_path_flag
        if not getattr(args, 'inspect_path', None):
            parser.error('inspect-json requires a JSON_PATH or --path/--dir JSON_PATH')
        return command_inspect_json(args)
    if args.command == "flow-map":
        return command_flow_map(args)
    if args.command == "route":
        return command_route(args)
    if args.command in {"audit-code", "audit-swift", "check-code", "audit"}:
        if args.snippet is None and getattr(args, "top_snippet", None) is not None:
            args.snippet = args.top_snippet
        if args.max_dead_space is None and getattr(args, "top_max_dead_space", None) is not None:
            args.max_dead_space = args.top_max_dead_space
        if args.persona is None and getattr(args, "top_persona", None) is not None:
            args.persona = args.top_persona
        return command_audit_code(args)
    if args.command == "audit-spec":
        return command_audit_spec(args)
    if args.command == "check-whitespace-ratio":
        if args.max_dead_space is None and getattr(args, "top_max_dead_space", None) is not None:
            args.max_dead_space = args.top_max_dead_space
        if args.persona is None and getattr(args, "top_persona", None) is not None:
            args.persona = args.top_persona
        return command_check_whitespace_ratio(args)
    if args.command == "audit-geometry":
        return command_audit_geometry(args)
    if args.command == "check-section-interactivity":
        if args.snippet is None and getattr(args, "top_snippet", None) is not None:
            args.snippet = args.top_snippet
        return command_check_section_interactivity(args)
    if args.command == "lookup-defect":
        return command_lookup_defect(args)
    if args.command == "register-case":
        return command_register_case(args)
    if args.command == "jev-gate":
        return command_jev_gate(args)
    if args.top_audit_code is not None:
        args.path = list(args.top_audit_code or [])
        if getattr(args, 'top_path_alias', None):
            args.path.append(args.top_path_alias)
        args.positional_paths = []
        args.root_dir = None
        args.ui_path = None
        args.snippet = args.top_snippet
        args.spec_files_list = args.top_audit_spec
        args.check_whitespace_ratio = args.top_whitespace_path
        args.max_dead_space = args.top_max_dead_space
        args.persona = args.top_persona
        args.audit_geometry = args.top_geometry_path
        return command_audit_code(args)
    if args.top_audit_spec is not None:
        args.spec_files_list = list(args.top_audit_spec or [])
        if getattr(args, 'top_path_alias', None):
            args.spec_files_list.append(args.top_path_alias)
        args.spec_file = None
        args.positional_specs = []
        args.strict_spec_gaps = bool(getattr(args, 'top_strict_spec_gaps', False))
        return command_audit_spec(args)
    if args.top_interactivity_path is not None:
        args.interactivity_path = None if args.top_interactivity_path == "__SNIPPET_ONLY__" else args.top_interactivity_path
        if getattr(args, 'top_path_alias', None):
            args.interactivity_path = args.top_path_alias
        args.positional_paths = []
        args.snippet = args.top_snippet
        return command_check_section_interactivity(args)
    if args.top_lookup_defect:
        args.lookup_defect = args.top_lookup_defect
        args.query = None
        args.positional_query = []
        return command_lookup_defect(args)
    if args.top_whitespace_path or args.top_geometry_path:
        if args.top_whitespace_path == "__PATH_REQUIRED__":
            args.top_whitespace_path = args.top_path_alias
            if not args.top_whitespace_path:
                parser.error("--check-whitespace-ratio requires a PNG_OR_DIR or --path/--dir PNG_OR_DIR")
        ws_errors, ws_metrics, ws_advisories = ([], [], [])
        geom_errors, geom_details = ([], [])
        drawer_keys: list[str] = []
        persona_name, max_dead, min_breath, profile = _resolve_persona_whitespace(args.top_persona, args.top_max_dead_space)
        if args.top_whitespace_path:
            ws_errors, ws_metrics = EVIDENCE.check_whitespace_ratio(args.top_whitespace_path, max_dead_space_ratio=max_dead)
            if min_breath > 0.0:
                for m in ws_metrics:
                    ratio = float(m.get('dead_space_ratio', 0.0))
                    if ratio < min_breath:
                        ws_advisories.append(
                            f"{Path(str(m.get('file', 'image'))).name}: SOFT_ADVISORY_CRAMPED_BREATHING "
                            f"(whitespace ratio {ratio:.1%} < {min_breath:.0%} floor for --persona breathing)"
                        )
            if ws_errors or ws_advisories:
                drawer_keys.extend(['breathing', 'appearance'])
        if args.top_geometry_path:
            geom_errors, geom_details = EVIDENCE.audit_geometry(args.top_geometry_path, platform=args.platform, collision_threshold_pt2=float(args.collision_threshold))
            if geom_errors:
                drawer_keys.extend(['navigation', 'interaction'])
        errors = [*ws_errors, *geom_errors]
        out_payload: dict[str, Any] = {
            'ok': not errors,
            'platform': args.platform,
            'persona': persona_name,
            'max_dead_space_ratio': max_dead,
            'errors': errors,
            'whitespace_metrics': ws_metrics,
            'geometry_details': geom_details,
        }
        if ws_advisories:
            out_payload['advisories'] = ws_advisories
        if errors or ws_advisories:
            drawers, guidance = _build_self_disclosing_guidance(drawer_keys or ['breathing'])
            out_payload['reference_drawers'] = drawers
            out_payload['guidance'] = guidance
        print(json.dumps(out_payload, ensure_ascii=False, indent=2))
        return 0 if not errors else 1
    parser.error("a subcommand or --audit-code / --audit-spec / --check-whitespace-ratio / --audit-geometry / --check-section-interactivity / --lookup-defect is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
