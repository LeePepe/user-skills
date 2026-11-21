"""Validated app journeys and operative chrome for the offline navigation sandbox.

Custom model names are labels; architecture is the executable contract. This module
is shared by the generator and evidence gate, which accepts canonical artifacts only.
"""
import argparse
from copy import deepcopy
from html import escape
import json
from pathlib import Path
import re
import sys

ASSETS = Path(__file__).resolve().parents[1] / 'assets'
KINDS = {'stack', 'tabs', 'lens', 'deck'}
OPERATIONS = {'pushScreen', 'popScreen', 'switchTab', 'toggleSheet', 'togglePopover', 'dismissOverlay'}


def json_script(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).replace('<', '\\u003c')


def starter_schemes(context='Target App'):
    screens = [
        {'id': 'capture', 'name': 'Capture', 'p0_summary': f'Capture an item in {context}',
         'actions': [{'label': 'Start capture', 'frequency': 'P0', 'transition': {'type': 'pushScreen', 'to': 'todo'}}]},
        {'id': 'todo', 'name': 'Todo', 'p0_summary': 'Keep the next task in focus',
         'actions': [{'label': 'Save task', 'frequency': 'P0', 'commit': 'saved', 'transition': {'type': 'switchTab', 'to': 'capture'}}],
         'transitions': [{'type': 'popScreen'}]},
        {'id': 'settings', 'name': 'Settings', 'role': 'sheet', 'p0_summary': 'Adjust capture preferences',
         'actions': [{'label': 'Save settings', 'frequency': 'P1', 'commit': 'configured', 'transition': {'type': 'dismissOverlay'}}]},
    ]
    schemes = []
    for kind in ('stack', 'tabs', 'lens', 'deck'):
        schemes.append({'title': f'{context} · {kind} journey', 'navigation_model': f'{context} {kind}',
                        'architecture': {'kind': kind, 'root': 'capture', 'destinations': ['capture', 'todo'],
                                         'primary': {'screen': 'capture', 'action': 0}, 'sheet': 'settings',
                                         'overflow': [], 'collapse_title': True},
                        'screens': deepcopy(screens)})
    return schemes


def _edges(page):
    return [a['transition'] for a in page['actions'] if a.get('transition')] + page['transitions']


def canonical_walk(scheme, seeds=None):
    """Ordered chrome and edges assign IDs identically for graph and state signatures."""
    a = scheme['architecture']
    pages = {p['id']: p for p in scheme['screens']}
    if seeds is None:
        seeds = [a['root'], *a['destinations'], a['primary']['screen'], a.get('sheet'),
                 *(r['screen'] for r in a['overflow'])]
    order = list(dict.fromkeys(s for s in seeds if s in pages))
    seen = set(order)
    for screen in order:
        for edge in _edges(pages[screen]):
            target = edge.get('to')
            if target in pages and target not in seen:
                seen.add(target)
                order.append(target)
    return order


def _reachable_pages(architecture, pages):
    page_map = {p['id']: p for p in pages}
    refs = [architecture['primary'], *architecture['overflow']]
    targets = [(page_map[r['screen']]['actions'][r['action']].get('transition') or {}).get('to') for r in refs]
    return canonical_walk({'architecture': architecture, 'screens': pages},
                          [*architecture['destinations'], architecture.get('sheet'), *targets])


def _popover_targets(architecture, pages, active):
    page_map = {p['id']: p for p in pages}
    sources = list(dict.fromkeys(s for s in [active, architecture.get('sheet')] if s is not None))
    edges = [page_map[r['screen']]['actions'][r['action']].get('transition')
             for r in [architecture['primary'], *architecture['overflow']]]
    targets = set()
    for source in sources:
        edges.extend(_edges(page_map[source]))
        for edge in edges:
            if edge and edge['type'] == 'togglePopover' and edge['to'] not in targets:
                targets.add(edge['to'])
                sources.append(edge['to'])
        edges = []
    return targets


def _content_paths(architecture, pages):
    page_map = {p['id']: p for p in pages}
    def visit(path):
        yield path
        for edge in _edges(page_map[path[-1]]):
            target = edge.get('to')
            if edge['type'] == 'pushScreen' and target not in path:
                yield from visit([*path, target])
    for destination in architecture['destinations']:
        yield from visit([destination])


def _validate_initial(architecture, pages, explicit):
    if not isinstance(explicit, dict):
        raise ValueError('initial_state must be an object')
    state = {'active_screen': architecture['root'], 'history': [], 'overlay': None,
             'detent': None, 'overflow_open': False, 'title_collapsed': False}
    if set(explicit) - set(state):
        raise ValueError('initial_state contains unsupported fields')
    state.update(deepcopy(explicit))
    page_map = {p['id']: p for p in pages}
    content = {p['id'] for p in pages if p['role'] not in {'sheet', 'popover'}}
    if state['active_screen'] not in content:
        raise ValueError('initial_state active_screen must be a declared content screen')
    history = state['history']
    if not isinstance(history, list) or any(s not in content for s in history) or len(set(history)) != len(history):
        raise ValueError('initial_state history must contain distinct declared content screens')
    if state['active_screen'] in history:
        raise ValueError('initial_state history cannot contain active_screen')
    sequence = history + [state['active_screen']]
    if sequence[0] not in architecture['destinations']:
        raise ValueError('initial_state history/active screen must start at a visible destination')
    for source, target in zip(sequence, sequence[1:]):
        if not any(e['type'] == 'pushScreen' and e.get('to') == target for e in _edges(page_map[source])):
            raise ValueError('initial_state history requires ordered pushScreen edges')
    overlay = state['overlay']
    if overlay is not None and (overlay not in page_map or page_map[overlay]['role'] not in {'sheet', 'popover'}):
        raise ValueError('initial_state overlay must reference a declared sheet or popover')
    if overlay and page_map[overlay]['role'] == 'sheet' and overlay != architecture.get('sheet'):
        raise ValueError('initial_state sheet must equal architecture.sheet')
    if overlay and page_map[overlay]['role'] == 'popover':
        if overlay not in _popover_targets(architecture, pages, state['active_screen']):
            raise ValueError('initial_state popover must be reachable by togglePopover without changing active content')
    if state['detent'] not in {None, 'medium', 'large'} or (state['detent'] is not None and overlay is None):
        raise ValueError('initial_state detent requires an overlay and must be medium or large')
    if overlay and page_map[overlay]['role'] == 'sheet' and state['detent'] is None:
        state['detent'] = 'medium'
    if type(state['overflow_open']) is not bool or (state['overflow_open'] and not architecture['overflow']):
        raise ValueError('initial_state overflow_open requires overflow actions')
    if state['overflow_open'] and overlay:
        raise ValueError('initial_state overflow and overlay cannot be open together')
    if type(state['title_collapsed']) is not bool or (state['title_collapsed'] and not architecture['collapse_title']):
        raise ValueError('initial_state title_collapsed requires collapse_title support')
    if (state['active_screen'] == architecture['root'] and not history and overlay is None
            and not state['overflow_open'] and not state['title_collapsed']):
        raise ValueError('app-nav initial_state cannot be a closed root baseline')
    return state


def _showcase_states(architecture, pages):
    root, sheet = architecture['root'], architecture.get('sheet')
    paths = list(_content_paths(architecture, pages))
    secondary = next((d for d in reversed(architecture['destinations']) if d != root), root)
    pushed = next((p for p in paths if len(p) > 1 and p[0] == root), None)
    preferred = {'active_screen': secondary}
    if architecture['kind'] == 'stack':
        preferred = {'active_screen': pushed[-1], 'history': pushed[:-1]} if pushed else {'overlay': sheet, 'detent': 'medium'} if sheet else preferred
    elif architecture['kind'] == 'tabs' and sheet and secondary == root:
        preferred = {'overlay': sheet, 'detent': 'large'}
    elif architecture['kind'] == 'lens':
        preferred.update(title_collapsed=architecture['collapse_title'], overflow_open=bool(architecture['overflow']))
    elif architecture['kind'] == 'deck':
        preferred = {'active_screen': root, 'title_collapsed': architecture['collapse_title']}
        if sheet:
            preferred.update(overlay=sheet, detent='medium')
    states = []
    def add(value):
        try:
            state = _validate_initial(architecture, pages, value)
            if state not in states:
                states.append(state)
        except ValueError:
            pass
    add(preferred)
    popovers = list(dict.fromkeys(e['to'] for s in _reachable_pages(architecture, pages)
                                 for e in _edges(next(p for p in pages if p['id'] == s)) if e['type'] == 'togglePopover'))
    overlays = [(None, None)] + ([(sheet, 'medium'), (sheet, 'large')] if sheet else []) + [(p, None) for p in popovers]
    for path in paths:
        for overlay, detent in overlays:
            for collapsed in ([False, True] if architecture['collapse_title'] else [False]):
                for overflow in ([False, True] if architecture['overflow'] else [False]):
                    add({'active_screen': path[-1], 'history': path[:-1], 'overlay': overlay,
                         'detent': detent, 'title_collapsed': collapsed, 'overflow_open': overflow})
    return states


def _initial_state(architecture, pages, index, explicit=None):
    if explicit is not None:
        return _validate_initial(architecture, pages, explicit)
    states = _showcase_states(architecture, pages)
    if not states:
        raise ValueError('insufficient declared capabilities for a non-baseline initial state')
    return states[0]


def _assign_showcases(schemes, explicit):
    choices = [[s['architecture']['initial_state']] if fixed else _showcase_states(s['architecture'], s['screens'])
               for s, fixed in zip(schemes, explicit)]
    def assign(index, seen):
        if index == len(schemes):
            return True
        for state in choices[index]:
            schemes[index]['architecture']['initial_state'] = state
            signature = initial_state_signature(schemes[index])
            if signature not in seen and assign(index + 1, seen | {signature}):
                return True
        return False
    if not assign(0, set()):
        raise ValueError('insufficient declared capabilities for four distinct non-baseline initial states')


def _edge(value, ids, roles):
    if not isinstance(value, dict) or value.get('type') not in OPERATIONS:
        raise ValueError('invalid transition type')
    kind, target = value['type'], value.get('to')
    if kind not in {'dismissOverlay', 'popScreen'} and target not in ids:
        raise ValueError('transition target is not declared')
    if kind in {'pushScreen', 'switchTab'} and roles.get(target) in {'sheet', 'popover'}:
        raise ValueError('overlay screens require overlay transitions')
    if kind == 'toggleSheet' and roles.get(target) != 'sheet':
        raise ValueError('toggleSheet target must have role sheet')
    if kind == 'togglePopover' and roles.get(target) != 'popover':
        raise ValueError('togglePopover target must have role popover')
    return {'type': kind, **({'to': target} if target is not None else {})}


def validate_scheme(scheme, index, context):
    if not isinstance(scheme, dict):
        raise ValueError('scheme must be an object')
    pages = scheme.get('screens')
    if not isinstance(pages, list) or len(pages) < 2 or not all(isinstance(p, dict) for p in pages):
        raise ValueError('scheme needs at least two screens')
    ids = [p.get('id') for p in pages]
    if any(not isinstance(i, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', i) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('invalid or duplicate screen id')
    roles = {p['id']: p.get('role', 'screen') for p in pages}
    clean = []
    for p in pages:
        transitions = p.get('transitions', p.get('edges', []))
        if not isinstance(transitions, list):
            raise ValueError('transitions must be an array')
        transitions = [_edge(t, ids, roles) for t in transitions]
        actions = p.get('actions', [])
        if not isinstance(actions, list):
            raise ValueError('actions must be an array')
        normalized = []
        for i, action in enumerate(actions):
            a = {'label': action} if isinstance(action, str) else deepcopy(action)
            if not isinstance(a, dict) or not isinstance(a.get('label'), str) or not a['label'].strip():
                raise ValueError('action requires a label')
            default_frequency = 'P3' if roles[p['id']] in {'sheet', 'popover'} else 'P0' if i == 0 else 'P2'
            frequency = a.get('frequency', default_frequency)
            if frequency not in {'P0', 'P1', 'P2', 'P3'}:
                raise ValueError('action frequency must be P0..P3')
            commit = a.get('commit')
            if commit is not None and (not isinstance(commit, str) or not commit.strip()):
                raise ValueError('commit must be a nonempty domain key')
            t = a.get('transition', transitions[i] if isinstance(action, str) and i < len(transitions) else None)
            normalized.append({'label': a['label'], 'frequency': frequency, 'commit': commit,
                               'transition': _edge(t, ids, roles) if t is not None else None})
        clean.append({'id': p['id'], 'name': str(p.get('name', p.get('title', p['id']))),
                      'role': roles[p['id']], 'p0_summary': str(p.get('p0_summary', p.get('role', ''))),
                      'actions': normalized, 'transitions': transitions})
    # Validate graph before architecture so malformed edges have useful errors.
    model = scheme.get('navigation_model')
    architecture = scheme.get('architecture')
    if not isinstance(model, str) or not model.strip():
        raise ValueError('navigation_model requires a nonempty label')
    if not isinstance(architecture, dict) or architecture.get('kind') not in KINDS:
        raise ValueError('architecture requires an operative kind: stack, tabs, lens or deck')
    a = deepcopy(architecture)
    root = a.get('root')
    destinations = a.get('destinations')
    if root not in ids or roles[root] in {'sheet', 'popover'}:
        raise ValueError('architecture root must be a content screen')
    if (not isinstance(destinations, list) or not destinations or len(set(destinations)) != len(destinations)
            or any(d not in ids or roles[d] in {'sheet', 'popover'} for d in destinations)):
        raise ValueError('architecture destinations must be distinct content screens')
    if root not in destinations:
        raise ValueError('architecture destinations must include root')
    page_map = {p['id']: p for p in clean}
    def reference(ref):
        if (not isinstance(ref, dict) or ref.get('screen') not in ids or type(ref.get('action')) is not int
                or not 0 <= ref['action'] < len(page_map[ref['screen']]['actions'])):
            raise ValueError('chrome action must reference a declared screen/action index')
        return {'screen': ref['screen'], 'action': ref['action']}
    primary = reference(a.get('primary'))
    if page_map[primary['screen']]['actions'][primary['action']]['frequency'] != 'P0':
        raise ValueError('prominent primary flow must reference a P0 action')
    overflow = a.get('overflow', [])
    if not isinstance(overflow, list):
        raise ValueError('overflow must be an action-reference array')
    sheet = a.get('sheet')
    if sheet is not None and roles.get(sheet) != 'sheet':
        raise ValueError('architecture sheet must reference a sheet screen')
    collapse = a.get('collapse_title', True)
    if type(collapse) is not bool:
        raise ValueError('collapse_title must be boolean')
    overflow = [reference(r) for r in overflow]
    for p in clean:
        if p['role'] not in {'sheet', 'popover'}:
            for i, action in enumerate(p['actions']):
                if action['frequency'] == 'P3' and {'screen': p['id'], 'action': i} not in overflow:
                    raise ValueError('P3 content actions must be routed through architecture.overflow')
    a = {'kind': a['kind'], 'root': root, 'destinations': destinations, 'primary': primary,
         'sheet': sheet, 'overflow': overflow, 'collapse_title': collapse}
    a['initial_state'] = _initial_state(a, clean, index, architecture.get('initial_state'))
    return {'id': f'app{index:02d}', 'title': str(scheme.get('title', f'{context} · {index}')),
            'navigation_model': model, 'architecture': a, 'screens': clean, 'context': context}


def initial_state_signature(scheme):
    """Compare showcase states by canonical graph positions, not screen labels."""
    a, pages = scheme['architecture'], {p['id']: p for p in scheme['screens']}
    order = canonical_walk(scheme)
    positions = {screen: index for index, screen in enumerate(order)}
    state = a['initial_state']; overlay = state['overlay']
    role = pages[overlay]['role'] if overlay else None
    detent = state['detent'] if role == 'sheet' else None
    return (positions[state['active_screen']], tuple(positions[s] for s in state['history']),
            (role, positions.get(overlay)) if overlay else None, detent,
            bool(state['overflow_open']), bool(state['title_collapsed']))


def architecture_signature(scheme):
    a = scheme['architecture']
    pages = {p['id']: p for p in scheme['screens']}
    def graph(order):
        positions = {screen: i for i, screen in enumerate(order)}
        def edge(t):
            return (t['type'], positions.get(t.get('to'))) if t else None
        return tuple((pages[s]['role'] if pages[s]['role'] in {'sheet', 'popover'} else 'content',
                      tuple((action['frequency'], bool(action['commit']), edge(action['transition'])) for action in pages[s]['actions']),
                      tuple(edge(t) for t in pages[s]['transitions'])) for s in order)
    # Ordered chrome and edges, not backing record order, assign canonical IDs.
    order = canonical_walk(scheme)
    positions = {screen: i for i, screen in enumerate(order)}
    def reference(r):
        return (positions[r['screen']], r['action'])
    # Retain unreferenced graph components as an order-independent multiset of
    # rooted, ordered traversals; no label-based sorting or permutation search.
    detached = tuple(sorted((graph(canonical_walk(scheme, [*order, s])) for s in pages if s not in positions), key=json.dumps))
    return (a['kind'], tuple(positions[d] for d in a['destinations']), positions[a['root']],
            a['collapse_title'], reference(a['primary']), tuple(reference(r) for r in a['overflow']),
            positions.get(a['sheet']), graph(order), detached)


def load_schemes(args):
    raw = getattr(args, 'app_flows_json', None)
    context = getattr(args, 'app_context', '') or 'Target App'
    if raw:
        raw = str(raw)
        if raw.lstrip().startswith(('{', '[')):
            raw = json.loads(raw)
        else:
            raw = json.loads(Path(raw).expanduser().read_text(encoding='utf-8'))
        if isinstance(raw, list):
            raw = {'schemes': raw}
        if not isinstance(raw, dict):
            raise ValueError('app flows must be an object or four-scheme array')
        context = str(raw.get('app_context', context))
        schemes = raw.get('schemes', raw.get('flows'))
    elif getattr(args, 'app_screens', '') or getattr(args, 'app_context', ''):
        raise ValueError('domain screens/context require --app-flows-json with four operative architectures; no guessed templates')
    else:
        schemes = starter_schemes(context)
    if not isinstance(schemes, list) or len(schemes) != 4:
        raise ValueError('--app-flows-json must define exactly four schemes')
    normalized = [validate_scheme(s, i, context) for i, s in enumerate(schemes, 1)]
    if len({architecture_signature(s) for s in normalized}) != 4:
        raise ValueError('four operative architectures must differ, not just model labels')
    _assign_showcases(normalized, [s['architecture'].get('initial_state') is not None for s in schemes])
    return normalized


def session_contract(anti_patterns, overrides):
    supported = {'no glass on content', 'no content glass', 'no top capsule', 'no top glass'}
    if any(a.casefold().strip() not in supported for a in anti_patterns):
        raise ValueError('unsupported session anti-pattern; supported: no glass on content, no top capsule/top glass')
    if any(k not in {'surface', 'density', 'note'} for k in overrides):
        raise ValueError('unsupported style override; supported: surface, density, inert note')
    return not any(a.casefold().strip() in {'no top capsule', 'no top glass'} for a in anti_patterns)


def presentation(overrides):
    surface = overrides.get('surface', 'solid')
    density = overrides.get('density', 'regular')
    if surface not in {'solid', 'warm', 'dark'} or density not in {'regular', 'compact', 'airy'}:
        raise ValueError('supported styles: surface=solid|warm|dark, density=regular|compact|airy')
    return {'surface': {'solid': '#edf2fb', 'warm': '#fffaf2', 'dark': '#111827'}[surface],
            'ink': '#f2f4ff' if surface == 'dark' else '#172033',
            'density': {'compact': '8px', 'regular': '14px', 'airy': '20px'}[density]}


def app_html(scheme, app_index, platform, optical_svg, anti_patterns=None, overrides=None):
    platform = {'macos': 'desktop'}.get(platform, platform)
    if platform not in {'mobile', 'desktop', 'web'}:
        raise ValueError('app-nav platform must be mobile, desktop/macos, or web')
    anti_patterns, overrides = anti_patterns or [], overrides or {}
    tokens = presentation(overrides)
    a = scheme['architecture']
    def button(ref, extra=''):
        page = next(p for p in scheme['screens'] if p['id'] == ref['screen'])
        label = page['actions'][ref['action']]['label']
        return f'<button {extra} data-action-screen="{escape(ref["screen"])}" data-action-index="{ref["action"]}">{escape(label)}</button>'
    destinations = ''.join(f'<button class="tab" data-screen="{escape(i)}">{escape(next(p["name"] for p in scheme["screens"] if p["id"] == i))}</button>' for i in a['destinations'])
    primary = button(a['primary'], 'id="toggle-tab" class="primary"')
    overflow = ''.join(button(r, 'data-overflow-action="true"') for r in a['overflow'])
    sheet_control = '<button id="open-sheet">Tools</button>' if a['sheet'] else ''
    more = '<button id="overflow" aria-label="More actions" aria-expanded="false">⋯</button>'
    back = '<button id="back">Back</button>'
    if a['kind'] == 'stack':
        chrome = f'<nav class="floating stack-toolbar">{back}<span id="compact-title"></span>{more}</nav><nav class="floating safe-dock">{primary}{sheet_control}</nav>'
        overflow += f'<nav aria-label="Journey destinations">{destinations}</nav>'
    elif a['kind'] == 'tabs':
        chrome = f'<nav class="stack-toolbar">{back}{more}{sheet_control}</nav><nav class="tab-chrome"><div class="floating tab-pill">{destinations}</div><div class="floating prominent-pill">{primary}</div></nav>'
    elif a['kind'] == 'lens':
        chrome = f'<nav class="floating lens-toolbar">{back}<div class="lens-tabs">{destinations}</div>{more}</nav><nav class="floating safe-dock">{primary}{sheet_control}</nav>'
    else:
        chrome = f'<nav class="deck-nav" aria-label="Journey deck">{destinations}</nav><nav class="floating inspector-toolbar">{back}{sheet_control}{more}</nav><nav class="floating safe-dock">{primary}</nav>'
    if platform == 'desktop':
        chrome = f'<header class="desktop-toolbar"><span>Toolbar</span>{more}</header><aside class="desktop-sidebar" aria-label="NavigationSplitView">{destinations}</aside><aside class="desktop-inspector" aria-label="Inspector">Inspector · MenuBarExtra{sheet_control}</aside><nav class="desktop-actions">{back}{primary}</nav>'
    elif platform == 'web':
        chrome = f'<header class="web-header"><span>Web navigation</span>{more}</header><aside class="web-toc" aria-label="Table of contents">{destinations}</aside><nav class="web-actions">{back}{primary}</nav>'
    if not session_contract(anti_patterns, overrides):
        chrome = chrome.replace('class="floating stack-toolbar"', 'class="stack-toolbar"').replace('class="floating lens-toolbar"', 'class="lens-toolbar"').replace('class="floating inspector-toolbar"', 'class="inspector-toolbar"')
    initial = {'active_screen': a['root'], 'history': [], 'overlay': None, 'detent': None,
               'overflow_open': False, 'title_collapsed': False} if app_index == 0 else a['initial_state']
    config = {'schema': 'navigation-page/v3', 'id': 'current' if app_index == 0 else scheme['id'],
              'platform': platform, 'scheme': scheme, 'initial_state': initial,
              'session_anti_patterns': anti_patterns, 'style_overrides': overrides}
    css = (ASSETS / 'navigation-demo.css').read_text(encoding='utf-8')
    runtime = (ASSETS / 'navigation-demo.js').read_text(encoding='utf-8')
    style = ';'.join(f'--{k}:{v}' for k, v in tokens.items())
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(scheme['title'])}</title><link rel="stylesheet" href="tokens.css"><style>{css}</style></head>
<body><main class="app architecture-{a['kind']}" style="{style}" data-platform="{escape(platform)}" data-navigation-model="{escape(scheme['navigation_model'])}" data-initial-screen="{escape(initial['active_screen'])}" data-initial-overlay="{escape(initial['overlay'] or '')}" data-initial-detent="{escape(initial['detent'] or '')}" data-initial-overflow="{str(initial['overflow_open']).lower()}" data-session-anti-patterns="{escape(json.dumps(anti_patterns, ensure_ascii=False))}" data-style-overrides="{escape(json.dumps(overrides, ensure_ascii=False))}">
{optical_svg}
<header class="large-title"><p>{escape(scheme['context'])}</p><h1 id="large-title">{escape(scheme['title'])}</h1></header>
{chrome}
<section id="screen" class="content-solid" aria-live="polite"></section>
<output id="action-state" role="status"></output>
<section id="sheet" class="floating overlay" role="dialog" aria-modal="true" aria-label="Tools" hidden><div class="grabber" aria-hidden="true"></div><div class="detents"><button id="close-sheet">Close</button><button id="medium-sheet">Medium</button><button id="large-sheet">Large</button></div><div id="sheet-content"></div></section>
<section id="overflow-menu" class="floating" aria-label="More actions" hidden>{overflow}<div id="popover-content"></div></section>
</main><script id="page-config" type="application/json">{json_script(config)}</script><script>{runtime}</script></body></html>'''


class _NavigationDemoParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(
            f"navigation_demo.py error: {message}\n"
            "  Fix: run `python3 skills/design-studio/scripts/navigation_demo.py --self-check` or use `design_catalog.py scaffold-gallery --mode app-nav`.\n"
            "  References: skills/design-studio/references/toolchain-20-matrix.md · skills/design-studio/references/eval-cases.json\n"
        )
        raise SystemExit(2)


def main(argv=None) -> int:
    parser = _NavigationDemoParser(description="Validated app navigation sandbox and operative graph helper.")
    parser.add_argument("--self-check", action="store_true", help="Run fast navigation graph self-check (default)")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary (default)")
    parser.parse_args(sys.argv[1:] if argv is None else argv)
    schemes = [validate_scheme(s, idx, "SelfCheck") for idx, s in enumerate(starter_schemes("SelfCheck"), 1)]
    distinct_archs = len({architecture_signature(s) for s in schemes})
    ok = distinct_archs == 4 and (ASSETS / "navigation-demo.js").is_file() and (ASSETS / "navigation-demo.css").is_file()
    print(json.dumps({
        "ok": ok,
        "module": "navigation_demo",
        "schemes_checked": len(schemes),
        "distinct_architectures": distinct_archs,
        "kinds": sorted(KINDS),
        "references": [
            "skills/design-studio/references/toolchain-20-matrix.md",
            "skills/design-studio/references/eval-cases.json",
        ],
    }, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
