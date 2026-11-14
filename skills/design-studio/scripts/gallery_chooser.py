"""One card, bezel, and runtime contract for round choosers and goal portals."""
from html import escape
import json
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / 'assets'


def frame_mode(manifest):
    return 'macos' if manifest['platform'] == 'desktop' else manifest['platform']


def cards_for(manifest, prefix='', reference=True):
    category = manifest.get('category') or f'r{manifest["round"]}'
    common = {'category': category, 'round': manifest['round'], 'platform': frame_mode(manifest)}
    result = []
    if reference:
        result.append(dict(common, id='current', title='Current reference', pills=['Reference'],
                           src=prefix + 'current.html', reference=True))
    for variant in manifest['variants']:
        if manifest.get('mode') == 'app-nav':
            scheme = next(s for s in manifest['schemes'] if s['id'] == variant['id'])
            pills = [variant['navigation_model'], scheme['architecture']['kind']]
        else:
            pills = [variant['p0'], variant['axis_y'], variant['archetype']]
        result.append(dict(common, id=variant['id'], title=variant.get('title', variant['id']),
                           pills=pills, src=prefix + variant['id'] + '.html', reference=False))
    return result


def card_html(card):
    attr = lambda value: escape(str(value), quote=True)
    width, height = (390, 844) if card['platform'] == 'mobile' else (1280, 800)
    scale = .8 if card['platform'] == 'mobile' else .46
    pills = ''.join(f'<span class="coordinate-pill">{attr(p)}</span>' for p in card['pills'])
    if card['platform'] == 'mobile':
        chrome = '<div class="device-island" aria-hidden="true"></div>'
        home = '<div class="device-home" aria-hidden="true"></div>'
    elif card['platform'] == 'macos':
        chrome = '<div class="device-titlebar" aria-hidden="true"><i></i><i></i><i></i><span>macOS preview</span></div>'
        home = ''
    else:
        chrome = '<div class="device-titlebar browser-titlebar" aria-hidden="true"><span>https://app.local/preview</span></div>'
        home = ''
    if card['reference']:
        controls = '<p class="reference-label">Reference only · not a candidate</p>'
    else:
        controls = f'''<div class="controls">
<label><input type="radio" name="primary-{attr(card['category'])}" value="{attr(card['id'])}" data-select> SELECT</label>
<label><input type="checkbox" data-star="{attr(card['id'])}"> STAR</label></div>
<textarea data-note="{attr(card['id'])}" aria-label="{attr(card['id'])} Notes" placeholder="Notes"></textarea>'''
    current_class = ' current-card' if card['reference'] else ''
    return f'''<article class="variant-card{current_class}" data-group="{attr(card['category'])}" data-round="{card['round']}" data-variant="{attr(card['id'])}" data-platform="{card['platform']}">
<header><span class="variant-badge">{attr(card['id'])}</span><strong>{attr(card['title'])}</strong>{pills}
<a target="_blank" rel="noopener" href="{attr(card['src'])}">↗ 全屏打开</a></header>
<div class="device-bezel" data-frame-mode="{card['platform']}" style="--frame-width:{width}px;--frame-height:{height}px;--preview-scale:{scale}">
<div class="device-window">{chrome}<iframe loading="eager" src="{attr(card['src'])}" title="{attr(card['title'])}"></iframe>{home}</div></div>
{controls}</article>'''


def render(goal, manifests, tokens, portal=False, prefix=''):
    cards, groups = [], {}
    for manifest in manifests:
        category = manifest.get('category') or f'r{manifest["round"]}'
        group = groups.setdefault(category, {'id': category, 'title': manifest.get('category_title') or category,
                                              'count': 0, 'platforms': set()})
        group['count'] += len(manifest['variants'])
        group['platforms'].add(frame_mode(manifest))
        path = manifest['_path'] + '/' if portal else prefix
        # Portals compare candidates; each round keeps its own non-selectable reference.
        cards.extend(cards_for(manifest, path, reference=not portal))
    tabs = ''
    if portal:
        total = sum(g['count'] for g in groups.values())
        buttons = [f'<button data-group-filter="all" aria-pressed="true">全部方案 ({total})</button>']
        for group in groups.values():
            label = f'{group["title"]} ({group["count"]} · {"/".join(sorted(group["platforms"]))})'
            buttons.append(f'<button data-group-filter="{escape(group["id"], quote=True)}" aria-pressed="false">{escape(label)}</button>')
        tabs = '<nav class="group-tabs" aria-label="Categories">' + ''.join(buttons) + '</nav>'
    platforms = ''.join(f'<button data-portal-platform="{p}" aria-pressed="{str(p == "all").lower()}">{p.title()}</button>'
                        for p in ('all', 'mobile', 'macos', 'web')) if portal else ''
    theme_attr = 'data-portal-theme' if portal else 'data-theme'
    responsive = '<button id="responsive-toggle" aria-pressed="false">Desktop 1280×800 ⇄ Mobile 390×844</button>' if any(c['platform'] == 'web' for c in cards) else ''
    payload = {'goal': goal, 'portal': portal, 'cards': cards}
    encoded = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
    title = f'{goal} · Master Portal' if portal else f'{goal} · Round {manifests[0]["round"]}'
    css = (ASSETS / 'tenfold-gallery.css').read_text(encoding='utf-8')
    runtime = (ASSETS / 'gallery-chooser.js').read_text(encoding='utf-8')
    grid_id = 'portal-grid' if portal else 'variants'
    session = '; '.join(str(a) for m in manifests for a in m.get('session_anti_patterns', [])) or 'none'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{tokens}\n{css}</style></head>
<body class="gallery-page"><main class="gallery{' portal' if portal else ''}">
<h1>{escape(title)}</h1><p id="session">Session anti-patterns: {escape(session)}</p>
{tabs}<div class="portal-actions">{platforms}<button {theme_attr}="light">Light</button><button {theme_attr}="dark">Dark</button>{responsive}</div>
<div id="{grid_id}">{''.join(card_html(c) for c in cards)}</div>
<p id="empty-state" class="empty-state" hidden>No previews match these filters.</p>
<button id="copy-summary">Copy Selection Summary</button><output id="status" aria-live="polite"></output>
<textarea id="summary-text" class="manual-copy" aria-label="Selection summary" hidden readonly></textarea>
</main><script id="gallery-config" type="application/json">{encoded}</script><script>{runtime}</script></body></html>'''
