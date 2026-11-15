"""One card, bezel, and runtime contract for round choosers and goal portals."""
from html import escape
import json
from pathlib import Path
import re

ASSETS = Path(__file__).resolve().parents[1] / 'assets'


def frame_mode(manifest):
    return 'macos' if manifest['platform'] == 'desktop' else manifest['platform']


_CHINESE = re.compile(r'[\u4e00-\u9fff]')


def _usable_summary(value):
    text = str(value or '').strip()
    return text if len(text) >= 10 and _CHINESE.search(text) else ''


def _prompt_summary(root_dir, variant):
    if not root_dir:
        return ''
    prompt = Path(root_dir) / 'prompts' / f'{variant["id"]}-prompt.md'
    try:
        text = prompt.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return ''
    for line in text.splitlines():
        match = re.search(r'(?:一句话介绍|核心差异|摘要|summary)\\s*[：:]\\s*(.+)', line, re.I)
        candidate = _usable_summary(match.group(1) if match else line.lstrip('# -*'))
        if candidate:
            return candidate
    return ''


def _summary_for(manifest, variant, scheme=None, root_dir=None):
    if variant.get('id') == 'current':
        return _usable_summary(manifest.get('reference_summary')) or f'当前基线参照视图（{manifest.get("goal", "当前项目")} 的现有布局与交互对照基线）'
    for key in ('summary', 'one_liner', 'summary_zh'):
        candidate = _usable_summary(variant.get(key))
        if candidate:
            return candidate
    candidate = _prompt_summary(root_dir, variant)
    if candidate:
        return candidate
    if scheme is not None:
        architecture = scheme.get('architecture', {})
        kind = architecture.get('kind', '工作台')
        state = architecture.get('initial_state', {}).get('active_screen', '主屏')
        destinations = '、'.join(str(item) for item in architecture.get('destinations', [])[:2]) or '核心流程'
        sheet = architecture.get('sheet') or '无浮层'
        return f'{variant["id"]} 采用{kind}导航，以{state}为起点串联{destinations}，并用{sheet}保持操作路径清晰。'
    morphology = {'v01': 'Bento磁贴', 'v02': '时间轴轨', 'v03': '脉冲矩阵', 'v04': '环形仪表',
                  'v05': '主从折叠', 'v06': '双栏工作台', 'v07': '悬浮灵动岛', 'v08': '3D卡包',
                  'v09': '命令舞台', 'v10': '画报大字'}.get(variant.get('id'), '模块化布局')
    return (f'{variant["id"]} 以{morphology}聚焦{variant.get("p0", "主要任务")}，'
            f'通过{variant.get("axis_y", "分层折叠")}和{variant.get("layout", "主次布局")}组织信息，'
            f'呈现{variant.get("glass_geometry", "平衡")}的操作节奏。')


def cards_for(manifest, prefix='', reference=True, root_dir=None):
    category = manifest.get('category') or f'r{manifest["round"]}'
    common = {'category': category, 'round': manifest['round'], 'platform': frame_mode(manifest)}
    result = []
    if reference:
        result.append(dict(common, id='current', title='Current reference', pills=['Reference'],
                           src=prefix + 'current.html', reference=True,
                           summary=_summary_for(manifest, {'id': 'current'})))
    for variant in manifest['variants']:
        if variant.get('prompt_file') is not None and variant['prompt_file'] != f'prompts/{variant["id"]}-prompt.md':
            raise ValueError('unsafe variant prompt_file')
        scheme = None
        if manifest.get('mode') == 'app-nav':
            scheme = next(s for s in manifest['schemes'] if s['id'] == variant['id'])
            pills = [variant['navigation_model'], scheme['architecture']['kind']]
        else:
            pills = [variant['p0'], variant['axis_y'], variant['archetype']]
        result.append(dict(common, id=variant['id'], title=variant.get('title', variant['id']),
                           pills=pills, src=prefix + variant['id'] + '.html', reference=False,
                           prompt=prefix + variant['prompt_file'] if variant.get('prompt_file') else '',
                           summary=_summary_for(manifest, variant, scheme, root_dir)))
    return result


def card_html(card, hidden=False):
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
    hidden_attr = ' hidden aria-hidden="true"' if hidden else ''
    prompt_link = f'<a class="prompt-link" target="_blank" rel="noopener" href="{attr(card["prompt"])}">Design prompt</a>' if card.get('prompt') else ''
    return f'''<article class="variant-card{current_class}" data-group="{attr(card['category'])}" data-round="{card['round']}" data-variant="{attr(card['id'])}" data-platform="{card['platform']}"{hidden_attr}>
<header><span class="variant-badge">{attr(card['id'])}</span><strong>{attr(card['title'])}</strong>{pills}
<a target="_blank" rel="noopener" href="{attr(card['src'])}">↗ 全屏打开</a>{prompt_link}</header>
<p class="variant-summary">{escape(card['summary'])}</p>
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
        root_dir = Path(manifest.get('_root_dir', '')) if manifest.get('_root_dir') else None
        # Portals compare candidates; each round keeps its own non-selectable reference.
        cards.extend(cards_for(manifest, path, reference=not portal, root_dir=root_dir))
    tabs = ''
    if portal:
        total = sum(g['count'] for g in groups.values())
        buttons = []
        for index, group in enumerate(groups.values()):
            label = f'{group["title"]} ({group["count"]} · {"/".join(sorted(group["platforms"]))})'
            buttons.append(f'<button data-group-filter="{escape(group["id"], quote=True)}" aria-pressed="{str(index == 0).lower()}">{escape(label)}</button>')
        buttons.append(f'<button data-group-filter="all" aria-pressed="false">全部方案 ({total})</button>')
        tabs = '<nav class="group-tabs" aria-label="Categories">' + ''.join(buttons) + '</nav>'
    platforms = ''.join(f'<button data-portal-platform="{p}" aria-pressed="{str(p == "all").lower()}">{p.title()}</button>'
                        for p in ('all', 'mobile', 'macos', 'web')) if portal else ''
    theme_attr = 'data-portal-theme' if portal else 'data-theme'
    responsive = '<button id="responsive-toggle" aria-pressed="false">Desktop 1280×800 ⇄ Mobile 390×844</button>' if any(c['platform'] == 'web' for c in cards) else ''
    first_group = next(iter(groups), 'all')
    payload = {'goal': goal, 'portal': portal, 'firstGroup': first_group, 'cards': cards}
    encoded = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
    title = f'{goal} · Master Portal' if portal else f'{goal} · Round {manifests[0]["round"]}'
    css = (ASSETS / 'tenfold-gallery.css').read_text(encoding='utf-8')
    runtime = (ASSETS / 'gallery-chooser.js').read_text(encoding='utf-8')
    grid_id = 'portal-grid' if portal else 'variants'
    session = '; '.join(str(a) for m in manifests for a in m.get('session_anti_patterns', [])) or 'none'
    breadcrumbs = '<nav class="gallery-breadcrumbs" aria-label="Breadcrumb"><a href="/">← Blog</a><span>·</span><a href="/demos/">Demos 总目录</a>' + ('<span>·</span><a href="../index.html">Goal Portal (../)</a>' if not portal else '') + '</nav>'
    initial = ''.join(card_html(c, portal and c['category'] != first_group) for c in cards)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{tokens}\n{css}</style></head>
<body class="gallery-page"><main class="gallery{' portal' if portal else ''}">
{breadcrumbs}<h1>{escape(title)}</h1><p id="session">Session anti-patterns: {escape(session)}</p>
{tabs}<div class="portal-actions">{platforms}<button {theme_attr}="light">Light</button><button {theme_attr}="dark">Dark</button>{responsive}</div>
<div id="{grid_id}">{initial}</div>
<p id="empty-state" class="empty-state" hidden>No previews match these filters.</p>
<p id="selection-telemetry" class="selection-telemetry" aria-live="polite">已记录 0 条观察 · 0 个收藏 · 0 个选择</p>
<button id="copy-summary">Copy Selection Summary</button><output id="status" aria-live="polite"></output>
<textarea id="summary-text" class="manual-copy" aria-label="Selection summary" hidden readonly></textarea>
</main><script id="gallery-config" type="application/json">{encoded}</script><script>{runtime}</script></body></html>'''


def render_demos_hub(demos_root, tokens):
    root = Path(demos_root).resolve()
    goals = []
    if root.is_dir():
        for goal_dir in sorted(root.iterdir(), key=lambda p: p.name):
            if goal_dir.name.startswith('.') or goal_dir.is_symlink() or not goal_dir.is_dir():
                continue
            items = []
            for child in sorted(goal_dir.iterdir(), key=lambda p: p.name):
                manifest_path = child / 'manifest.json'
                if child.name.startswith('.') or child.is_symlink() or not child.is_dir() or manifest_path.is_symlink() or not manifest_path.is_file():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                    if not isinstance(manifest, dict):
                        continue
                    category = manifest.get('category') or child.name
                    if (manifest.get('goal') != goal_dir.name or manifest.get('schema') not in {'tenfold-gallery/v1', 'design-gallery/v2'}
                            or not isinstance(manifest.get('variants'), list) or not manifest['variants']
                            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', str(category))):
                        continue
                    items.append((manifest, category))
                except (OSError, UnicodeError, ValueError, TypeError):
                    continue
            if items:
                goals.append((goal_dir.name, items))
    cards = []
    for goal, items in goals:
        categories = ' · '.join(escape(category) for _, category in items)
        count = sum(len(manifest['variants']) for manifest, _ in items)
        platforms = '/'.join(sorted({frame_mode(manifest) for manifest, _ in items}))
        safe = escape(goal, quote=True)
        cards.append(f'<article class="demo-goal-card" data-demo-goal="{safe}"><h2>{escape(goal)}</h2><p>{categories} · {count} variants · {platforms}</p><p data-goal-telemetry="{safe}">已记录 0 条观察 · 0 个收藏 · 0 个选择</p><a href="{safe}/index.html">进入画廊 →</a> <button data-copy-goal="{safe}">复制该项目观察摘要</button></article>')
    config = json.dumps([goal for goal, _ in goals], ensure_ascii=False).replace('<', '\\u003c')
    runtime = r"""(() => { const goals = JSON.parse(document.getElementById('hub-config').textContent); const safeObject = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {}; const parse = goal => { const merged = {}; for (const key of [`design-gallery:${goal}`, `design-gallery-${goal}`]) { try { Object.assign(merged, safeObject(JSON.parse(localStorage.getItem(key) || '{}'))); } catch {} } if (goal === 'aidash-cards-redesign') { for (let i = 0; i < localStorage.length; i++) { const key = localStorage.key(i) || ''; if (!key.startsWith('aidash-cards-r2:')) continue; const parts = key.slice('aidash-cards-r2:'.length).split(':'); if (parts[0] === 'selection' && parts[1]) { const category = parts[1].replace(/^select-/, ''), id = String(localStorage.getItem(key) || ''); if (id) { const entry = JSON.stringify([category, 2, id]); merged[entry] = {...merged[entry], category, round: 2, id, selected: true}; } } else if ((parts[0] === 'star' || parts[0] === 'note') && parts.length >= 3) { const category = parts[1], id = parts.slice(2).join(':'), entry = JSON.stringify([category, 2, id]), item = {...merged[entry], category, round: 2, id}; if (parts[0] === 'star' && localStorage.getItem(key) === '1') item.starred = true; if (parts[0] === 'note') item.notes = String(localStorage.getItem(key) || ''); merged[entry] = item; } } } return merged; }; const summary = (goal, state) => { const values = Object.values(state), label = s => `r${s.round}/${s.id}`, lines = [`Goal: ${goal}`, `SELECT: ${values.filter(s => s.selected === true).map(label).join(', ') || 'none'}`, `STAR: ${values.filter(s => s.starred === true).map(label).join(', ') || 'none'}`, 'NOTES:']; values.filter(s => typeof s.notes === 'string' && s.notes.trim()).forEach(s => lines.push(`${label(s)}: ${s.notes.trim()}`)); return lines.join('\n'); }; const update = () => goals.forEach(goal => { let state = {}; try { state = parse(goal); } catch {} const values = Object.values(state), node = document.querySelector(`[data-goal-telemetry="${CSS.escape(goal)}"]`); if (node) node.textContent = `已记录 ${values.filter(s => typeof s.notes === 'string' && s.notes.trim()).length} 条观察 · ${values.filter(s => s.starred === true).length} 个收藏 · ${values.filter(s => s.selected === true).length} 个选择`; }); const copy = async text => { try { await navigator.clipboard.writeText(text); } catch { const area = document.getElementById('hub-summary-text'); area.hidden = false; area.value = text; area.focus(); area.select(); } }; document.addEventListener('click', event => { const button = event.target.closest('[data-copy-goal],#copy-all-demos-summary'); if (!button) return; copy(button.id ? goals.map(goal => summary(goal, parse(goal))).join('\n\n') : summary(button.dataset.copyGoal, parse(button.dataset.copyGoal))); }); window.addEventListener('storage', update); window.addEventListener('pageshow', update); update(); })();"""
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Design Demos 总目录 · Active Ephemeral Galleries</title><style>{tokens}\n.gallery-breadcrumbs{{display:flex;gap:8px;margin-bottom:18px}}.demo-goal-card{{padding:20px;margin:16px 0;border:1px solid var(--border);border-radius:18px;background:var(--white)}}.gallery-breadcrumbs a,.demo-goal-card a{{margin-right:12px;min-height:28px;display:inline-flex;align-items:center}}button{{padding:10px 14px}}@media(max-width:480px){{.gallery-breadcrumbs a,.demo-goal-card a{{min-height:44px}}}}</style></head><body class="gallery-page"><main class="gallery"><nav class="gallery-breadcrumbs" aria-label="Breadcrumb"><a href="/">← Blog</a></nav><h1>Design Demos 总目录 · Active Ephemeral Galleries</h1><div id="demo-goals">{"".join(cards)}</div><button id="copy-all-demos-summary">📋 复制全部项目观察摘要</button><textarea id="hub-summary-text" readonly hidden></textarea></main><script id="hub-config" type="application/json">{config}</script><script>{runtime}</script></body></html>'''
