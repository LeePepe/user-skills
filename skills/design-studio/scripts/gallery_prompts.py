"""Bind independent Designer prompt files without pretending to interpret free text."""
import hashlib
import re
from pathlib import Path


def _paragraphs(text):
    return {p.strip() for p in re.split(r'\n\s*\n', text) if len(p.strip()) >= 20 and not p.lstrip().startswith('#')}


def _overlap_ratio(left, right):
    return len(left & right) / min(len(left), len(right)) if left and right else 0.0


def _check_overlap(items):
    names = list(items)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            a, b = _paragraphs(items[left]), _paragraphs(items[right])
            if not a or not b:
                continue
            overlap = _overlap_ratio(a, b)
            if overlap > 0.20:
                raise ValueError(f'prompt duplicate boilerplate exceeds 20%: {left}/{right} ({overlap:.2f})')

SCREEN_METAPHORS = [
    'Signal observatory: a quiet instrument with one reading in focus',
    'Recovery harbor: a protected landing area for the next safe step',
    'Pocket notebook: one actionable page with supporting context tucked away',
    'Journey map: one current waypoint and a folded trail of earlier moments',
    'Editorial calendar: a single moment framed by restrained typographic rhythm',
    'Worktable: one ready tool above an expandable context drawer',
    'Rescue card: a clear reset point with reversible supporting actions',
    'Memory lantern: one illuminated moment above a layered history pocket',
    'Launch pad: one committed next action with an alternate detail lens',
    'Context compass: one orienting signal within a navigable spatial stack',
]
APP_METAPHORS = {
    'stack': 'Guided trail: each committed step reveals the next destination',
    'tabs': 'Workspaces: independent rooms retain their own unfinished journeys',
    'lens': 'Observatory: switch perspectives without duplicating the content stage',
    'deck': 'Card atlas: reveal destinations spatially and keep tools in an inspector',
}
PALETTES = {
    'VS-005': 'cool paper, dark editorial ink, a restrained blue focal accent',
    'VS-006': 'warm paper, charcoal monospace ink, a restrained terracotta accent',
    'VS-007': 'neutral canvas, high-contrast ink, an emerald directional accent',
    'VS-008': 'cool solid panels, high-contrast ink, a violet analytical accent',
}


def starter(manifest, variant, index):
    platform = manifest['platform']
    if manifest.get('mode') == 'app-nav':
        scheme = manifest['schemes'][index]
        a = scheme['architecture']
        primary = next(p for p in scheme['screens'] if p['id'] == a['primary']['screen'])
        p0 = primary['actions'][a['primary']['action']]['label']
        domain = scheme['context']
        metaphor = APP_METAPHORS[a['kind']]
        folding = f"{a['kind']} journey; destinations {', '.join(a['destinations'])}; sheet {a['sheet'] or 'none'}"
        coordinates = f"{scheme['navigation_model']} / {a['kind']} / root {a['root']}"
        palette = 'solid neutral content, semantic light/dark ink, one restrained action accent'
        layout = 'Use the supplied graph and platform-native chrome; retain tab-local history and reversible push/pop.'
        icon = 'a connected path with one highlighted destination'
        topology = {'stack': 'a vertical journey rail with a persistent Back affordance', 'tabs': 'a tab-local workspace strip with an isolated active room', 'lens': 'a comparison lens with two synchronized reading panes', 'deck': 'a horizontal card deck with a revealed inspector'}[a['kind']]
        surface_detail = {'stack': 'use a quiet paper surface and a single lifted journey rail', 'tabs': 'separate the selected workspace from the prominent action pill with solid surfaces', 'lens': 'use a cool analytical canvas with a crisp lens toolbar and no nested glass', 'deck': 'use broad solid cards with a restrained inspector edge and clear depth order'}[a['kind']]
        anti_focus = {'stack': 'do not hide Back or turn the journey rail into decorative chrome', 'tabs': 'do not merge tab history or make every room look like the root', 'lens': 'do not fabricate a comparison source or use an inert lens switcher', 'deck': 'do not imply depth with striped effects or duplicate cards'}[a['kind']]
    else:
        domain = variant['content']
        metaphor = SCREEN_METAPHORS[index]
        p0 = variant['p0']
        folding = variant['axis_y']
        coordinates = f"{variant['archetype']} / {variant['layout']} / {variant['whitespace']} / {variant['glass_geometry']}"
        palette = PALETTES[variant['archetype']]
        layout = f"Place the hero using {variant['layout']}; spacing is {variant['whitespace']}; functional chrome geometry is {variant['glass_geometry']}."
        icon = {'next-action': 'a forward arrow inside an open path', 'timeline': 'three points on a time rail',
                'context': 'a compass needle with an open circle', 'recovery': 'a returning arrow with an unclosed loop'}[p0]
        topology = ['a bento signal grid', 'a timeline rail with milestone nodes', 'a pulse matrix of live checkpoints', 'a ring gauge surrounded by evidence chips', 'a master-detail item rail', 'a split workbench with input and result panes', 'a floating action island over solid content', 'a stacked pocket deck', 'a command stage with keyboard focus', 'an editorial poster with a single headline'][index]
        surface_detail = ['cool paper with blue signal ink', 'warm paper with terracotta milestone marks', 'neutral canvas with emerald pulse accents', 'dark canvas with violet gauge highlights', 'solid ivory with a focused detail column', 'cool solid panels with a green workbench cue', 'warm solid paper and one lifted island', 'dark solid cards with restrained depth', 'neutral solid canvas with a command accent', 'editorial paper with high-contrast type'][index]
        anti_focus = ['avoid equal-weight tiles', 'avoid an unbounded scroll timeline', 'avoid decorative heartbeat animation', 'avoid a gauge without a readable value', 'avoid a detail pane detached from selection', 'avoid nested glass work surfaces', 'avoid floating chrome covering the P0', 'avoid fake card depth and clipped pockets', 'avoid a command palette with no real action', 'avoid poster copy that hides the next action'][index]
    body = f'''# {variant['id']} — independent prototype prompt

Status: scaffold starter, NOT Designer-approved. Stage 1 Designer must refine and approve this individual brief; Stage 2 Coding implements the approved brief.
Target: {platform}; domain: {domain}.
Coordinates: {coordinates}.

## Visual metaphor
{metaphor}. Build {topology}; translate the metaphor into a coherent hierarchy with one readable focal state, not decorative imagery repeated across every candidate.

## Single P0 focal point
Make “{p0}” the only primary focal object/action per viewport. Anchor it to the real domain “{domain}”; retain visible intent, commit feedback and a reversible return path.

## P1/P2 folding
Keep one P1 companion status inline; fold P2 context using {folding}. P3 configuration stays behind a declared tools/overflow affordance only when the graph declares one. Shape the content around {topology}; do not stack all secondary modules vertically.
{layout}

## Color and surface polarity
Use {palette}; the candidate surface treatment is {surface_detail}. Light uses solid paper with dark ink; dark uses a deep solid canvas and bright ink. Preserve semantic contrast across both appearances; glass is functional floating chrome only, never content-on-glass.

## Liquid Glass optical parameters
Use a signed squircle Snell/Fresnel backdrop map (IOR 1.5, 12px bevel, 24px displacement with chromatic channels), blur 2.5px and saturation 135–155%. Blink may use SVG backdrop-filter refraction; provide a soft radial ambient/specular rim fallback, reduced transparency and reduced motion. Glass never samples another glass layer.

## Inline SVG iconography
Author a small stroke-based inline SVG depicting {icon}, with a 24×24 viewBox, rounded joins, semantic currentColor and accessible text where meaningful. Do not use emoji or external icon fonts as UI substitutes.

## Explicit anti-patterns
No .caustic-veil repeating-linear-gradient zebra stripes; never distort a striped overlay with url(#apple-liquid-glass). No .ruler 28×28 dashed boxes, Active / Matte debug bars, or duplicate inner platform/theme/viewport/selection controls. Candidate-specific guardrail: {anti_focus}. Keep legitimate navigation and product actions working. Outer choosers own gallery controls; prevent numeric truncation, clipped targets and raw localization keys.

## Stage 2 handoff
Designer owns the decisions above. Coding implements the approved concrete HTML/CSS/JS and, after Owner selection, production code. The deterministic scaffold binds this Markdown as evidence; it does not semantically implement arbitrary supplied prose or certify design approval.
'''
    return body


def prepare(manifest, source_dir=None):
    """Validate/read every source before any destination or blog directory is created."""
    source = Path(source_dir).expanduser() if source_dir else None
    if source and (source.is_symlink() or not source.is_dir()):
        raise ValueError('--prompts-dir must be a real directory, not a symlink')
    payload = {}
    for index, variant in enumerate(manifest['variants']):
        name = f'{variant["id"]}-prompt.md'
        if source:
            path = source / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f'missing or unsafe Designer prompt: {name}')
            data = path.read_bytes()
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError as error:
                raise ValueError(f'Designer prompt must be UTF-8: {name}') from error
            if not text.strip():
                raise ValueError(f'Designer prompt must not be empty: {name}')
        else:
            data = starter(manifest, variant, index).encode('utf-8')
        relative = f'prompts/{name}'
        payload[relative] = data
        variant['prompt_file'] = relative
        variant['prompt_sha256'] = hashlib.sha256(data).hexdigest()
    _check_overlap({name: data.decode('utf-8') for name, data in payload.items()})
    manifest['prompt_contract'] = {'schema': 'design-prompts/v1', 'source': 'provided' if source else 'starter',
                                   'count': len(payload)}
    return payload


def write(destination, payload):
    (destination / 'prompts').mkdir()
    for relative, data in payload.items():
        (destination / relative).write_bytes(data)
