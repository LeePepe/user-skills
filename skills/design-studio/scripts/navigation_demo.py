"""Validated app journeys and operative chrome for the offline navigation sandbox.

Custom model names are labels; architecture is the executable contract. This module
is shared by the generator and evidence gate, which accepts canonical artifacts only.
"""
from copy import deepcopy
from html import escape
import json
from pathlib import Path
import re

ASSETS = Path(__file__).resolve().parents[1] / 'assets'
KINDS = {'stack', 'tabs', 'lens', 'deck'}
OPERATIONS = {'pushScreen', 'popScreen', 'switchTab', 'toggleSheet', 'togglePopover', 'dismissOverlay'}


def json_script(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).replace('<', '\\u003c')


def starter_schemes(context='Target App'):
    screens = [
        {'id': 'capture', 'name': 'Capture', 'p0_summary': f'Capture an item in {context}',
         'actions': [{'label': 'Start capture', 'frequency': 'P0', 'transition': {'type': 'pushScreen', 'to': 'review'}}]},
        {'id': 'review', 'name': 'Review', 'p0_summary': 'Review before saving',
         'actions': [{'label': 'Save item', 'frequency': 'P0', 'commit': 'saved', 'transition': {'type': 'pushScreen', 'to': 'library'}}]},
        {'id': 'library', 'name': 'Library', 'p0_summary': 'Your saved items',
         'actions': [{'label': 'New item', 'frequency': 'P1', 'transition': {'type': 'switchTab', 'to': 'capture'}}],
         'transitions': [{'type': 'popScreen', 'to': 'review'}, {'type': 'toggleSheet', 'to': 'tools'}]},
        {'id': 'tools', 'name': 'Tools', 'role': 'sheet', 'p0_summary': 'Export the selected item',
         'actions': [{'label': 'Export item', 'frequency': 'P3', 'commit': 'exported', 'transition': {'type': 'dismissOverlay'}}]},
    ]
    return [{'title': f'{context} · {kind} journey', 'navigation_model': f'{context} {kind}',
             'architecture': {'kind': kind, 'root': 'capture', 'destinations': ['capture', 'library'],
                              'primary': {'screen': 'capture', 'action': 0}, 'sheet': 'tools',
                              'overflow': [{'screen': 'tools', 'action': 0}], 'collapse_title': True},
             'screens': deepcopy(screens)} for kind in ('stack', 'tabs', 'lens', 'deck')]


def _edge(value, ids, roles):
    if not isinstance(value, dict) or value.get('type') not in OPERATIONS:
        raise ValueError('invalid transition type')
    kind, target = value['type'], value.get('to')
    if kind != 'dismissOverlay' and target not in ids:
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
    if root not in destinations or (a['kind'] != 'stack' and len(destinations) < 2):
        raise ValueError('architecture requires root and at least two non-stack destinations')
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
    return {'id': f'app{index:02d}', 'title': str(scheme.get('title', f'{context} · {index}')),
            'navigation_model': model, 'architecture': a, 'screens': clean, 'context': context}


def architecture_signature(scheme):
    a = scheme['architecture']
    pages = {p['id']: p for p in scheme['screens']}
    def walk(seeds):
        order = list(dict.fromkeys(s for s in seeds if s is not None))
        seen = set(order)
        for screen in order:
            p = pages[screen]
            for t in [action['transition'] for action in p['actions']] + p['transitions']:
                target = t.get('to') if t else None
                if target is not None and target not in seen:
                    seen.add(target)
                    order.append(target)
        return order
    def graph(order):
        positions = {screen: i for i, screen in enumerate(order)}
        def edge(t):
            return (t['type'], positions.get(t.get('to'))) if t else None
        return tuple((pages[s]['role'] if pages[s]['role'] in {'sheet', 'popover'} else 'content',
                      tuple((action['frequency'], bool(action['commit']), edge(action['transition'])) for action in pages[s]['actions']),
                      tuple(edge(t) for t in pages[s]['transitions'])) for s in order)
    # Ordered chrome and edges, not backing record order, assign canonical IDs.
    order = walk([a['root'], *a['destinations'], a['primary']['screen'], a['sheet'],
                  *(r['screen'] for r in a['overflow'])])
    positions = {screen: i for i, screen in enumerate(order)}
    def reference(r):
        return (positions[r['screen']], r['action'])
    # Retain unreferenced graph components as an order-independent multiset of
    # rooted, ordered traversals; no label-based sorting or permutation search.
    detached = tuple(sorted((graph(walk([*order, s])) for s in pages if s not in positions), key=json.dumps))
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
    responsive_toggle = '<button id="responsive-toggle" aria-pressed="false">Desktop 1280×800 ⇄ Mobile 390×844</button>' if platform == 'web' else ''
    config = {'schema': 'navigation-page/v3', 'id': 'current' if app_index == 0 else scheme['id'],
              'platform': platform, 'scheme': scheme, 'session_anti_patterns': anti_patterns, 'style_overrides': overrides}
    css = (ASSETS / 'navigation-demo.css').read_text(encoding='utf-8')
    runtime = (ASSETS / 'navigation-demo.js').read_text(encoding='utf-8')
    style = ';'.join(f'--{k}:{v}' for k, v in tokens.items())
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(scheme['title'])}</title><style>{css}</style></head>
<body><main class="app architecture-{a['kind']}" style="{style}" data-platform="{escape(platform)}" data-navigation-model="{escape(scheme['navigation_model'])}" data-session-anti-patterns="{escape(json.dumps(anti_patterns, ensure_ascii=False))}" data-style-overrides="{escape(json.dumps(overrides, ensure_ascii=False))}">
{optical_svg}
<header class="large-title"><p>{escape(scheme['context'])}</p><h1 id="large-title">{escape(scheme['title'])}</h1></header>
{chrome}{responsive_toggle}
<section id="screen" class="content-solid" aria-live="polite"></section>
<output id="action-state" role="status"></output>
<section id="sheet" class="floating overlay" role="dialog" aria-modal="true" aria-label="Tools" hidden><div class="grabber" aria-hidden="true"></div><div class="detents"><button id="close-sheet">Close</button><button id="medium-sheet">Medium</button><button id="large-sheet">Large</button></div><div id="sheet-content"></div></section>
<section id="overflow-menu" class="floating" aria-label="More actions" hidden>{overflow}<div id="popover-content"></div></section>
</main><script id="page-config" type="application/json">{json_script(config)}</script><script>{runtime}</script></body></html>'''
