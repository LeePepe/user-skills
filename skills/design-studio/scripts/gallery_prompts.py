"""Bind independent Designer prompt files without pretending to interpret free text."""
import hashlib
from pathlib import Path

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
    return f'''# {variant['id']} — independent prototype prompt

Status: scaffold starter, NOT Designer-approved. Stage 1 Designer must refine and approve this individual brief; Stage 2 Coding implements the approved brief.
Target: {platform}; domain: {domain}.
Coordinates: {coordinates}.

## Visual metaphor
{metaphor}. Translate this metaphor into a coherent hierarchy, not decorative imagery repeated across every candidate.

## Single P0 focal point
Make “{p0}” the only primary focal object/action per viewport. Anchor it to the real domain “{domain}”; retain visible intent, commit feedback and a reversible return path.

## P1/P2 folding
Keep one P1 companion status inline; fold P2 context using {folding}. P3 configuration stays behind the declared tools/overflow affordance. Do not stack all secondary modules vertically.
{layout}

## Color and surface polarity
Use {palette}. Light uses solid paper with dark ink; dark uses a deep solid canvas and bright ink. Preserve semantic contrast across both appearances; glass is functional floating chrome only, never content-on-glass.

## Liquid Glass optical parameters
Use a signed squircle Snell/Fresnel backdrop map (IOR 1.5, 12px bevel, 24px displacement with chromatic channels), blur 2.5px and saturation 135–155%. Blink may use SVG backdrop-filter refraction; provide a soft radial ambient/specular rim fallback, reduced transparency and reduced motion. Glass never samples another glass layer.

## Inline SVG iconography
Author a small stroke-based inline SVG depicting {icon}, with a 24×24 viewBox, rounded joins, semantic currentColor and accessible text where meaningful. Do not use emoji or external icon fonts as UI substitutes.

## Explicit anti-patterns
No .caustic-veil repeating-linear-gradient zebra stripes; never distort a striped overlay with url(#apple-liquid-glass). No .ruler 28×28 dashed boxes, Active / Matte debug bars, or duplicate inner platform/theme/viewport/selection controls. Keep legitimate navigation and product actions working. Outer choosers own gallery controls; prevent numeric truncation, clipped targets and raw localization keys.

## Stage 2 handoff
Designer owns the decisions above. Coding implements the approved concrete HTML/CSS/JS and, after Owner selection, production code. The deterministic scaffold binds this Markdown as evidence; it does not semantically implement arbitrary supplied prose or certify design approval.
'''


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
    manifest['prompt_contract'] = {'schema': 'design-prompts/v1', 'source': 'provided' if source else 'starter',
                                   'count': len(payload)}
    return payload


def write(destination, payload):
    (destination / 'prompts').mkdir()
    for relative, data in payload.items():
        (destination / relative).write_bytes(data)
