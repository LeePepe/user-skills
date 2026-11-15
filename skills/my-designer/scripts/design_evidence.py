"""Fail-closed design evidence: scoped clauses, canonical galleries and attribution.

The deterministic gallery gate trusts a versioned, behavior-tested renderer, not
arbitrary JavaScript tokens. It parses embedded coordinates and then compares each
page and its executable assets with a fresh rendering of the validated manifest.
"""
from html.parser import HTMLParser
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

CATALOG_PATH = Path(__file__).resolve().parents[2] / 'design-studio' / 'scripts' / 'design_catalog.py'


def catalog():
    spec = importlib.util.spec_from_file_location('evidence_catalog', CATALOG_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PageParser(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.config_text = ''
        self.in_config = False
        self.nodes = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.nodes.append((tag, attrs))
        if tag == 'script' and attrs.get('id') == 'page-config':
            if attrs.get('type') != 'application/json' or self.config_text:
                raise ValueError('exactly one application/json page-config is required')
            self.in_config = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_config = False

    def handle_data(self, data):
        if self.in_config:
            self.config_text += data

    def coordinates(self):
        result = json.loads(self.config_text)
        if not isinstance(result, dict):
            raise ValueError('page-config must be an object')
        return result


class RenderedSurface(HTMLParser):
    """Keep independent block/label text runs and a cosmetic-ID-free DOM tree."""
    BLOCKS = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'div', 'section', 'header',
              'footer', 'summary', 'figcaption', 'article', 'aside', 'nav', 'label', 'td',
              'th', 'output', 'main', 'ol', 'ul', 'figure', 'button', 'textarea', 'select', 'option', 'br', 'hr'}
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, text):
        super().__init__()
        self.nodes, self.styles, self.links, self.labels = [], [], [], []
        self.visible_blocks, self.option_tokens, self.text_run = [], [], []
        self.tree = {'tag': 'document', 'attrs': {}, 'children': []}
        self.stack = [self.tree]
        self.in_style = False
        self.ignored_depth = 0
        self.control_stack = []
        self.feed(text)
        self._flush_text()

    def _flush_text(self):
        text = ' '.join(''.join(self.text_run).split()).casefold()
        if text:
            self.visible_blocks.append(text)
        self.text_run = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.nodes.append((tag, attrs))
        node = {'tag': tag, 'attrs': attrs, 'children': []}
        self.stack[-1]['children'].append(node)
        if tag not in self.VOID:
            self.stack.append(node)
        if tag == 'link' and 'stylesheet' in attrs.get('rel', '').lower().split():
            self.links.append(attrs.get('href', ''))
        if attrs.get('style'):
            self.styles.append('*{' + attrs['style'] + '}')
        if tag in {'script', 'style'}:
            self._flush_text()
            self.ignored_depth += 1
            self.in_style = tag == 'style'
        if self.ignored_depth:
            return
        if tag in self.BLOCKS or tag == 'input':
            self._flush_text()
        for key in ('aria-label', 'title', 'placeholder'):
            if attrs.get(key):
                self.labels.append(' '.join(attrs[key].split()).casefold())
        if tag == 'input' and attrs.get('value'):
            self.labels.append(' '.join(attrs['value'].split()).casefold())
        if tag in {'button', 'textarea'} or (tag == 'option' and any(n['tag'] == 'select' for n in self.stack)):
            self.control_stack.append((tag, attrs.get('value', ''), []))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_data(self, text):
        if self.in_style:
            self.styles.append(text)
        if self.ignored_depth:
            return
        self.text_run.append(text)
        for _, _, chunks in self.control_stack:
            chunks.append(text)

    def handle_endtag(self, tag):
        if tag in self.BLOCKS:
            self._flush_text()
        if self.control_stack and self.control_stack[-1][0] == tag:
            _, value, chunks = self.control_stack.pop()
            text = ' '.join(''.join(chunks).split()).casefold()
            if tag == 'option':
                self.option_tokens.append((text, ' '.join(value.split()).casefold()))
            elif text:
                self.labels.append(text)
        if tag in {'script', 'style'}:
            self.in_style = False
            self.ignored_depth = max(0, self.ignored_depth - 1)
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]['tag'] == tag:
                del self.stack[i:]
                break


def topology_signature(text):
    surface = RenderedSurface(text)
    def descendants(node):
        yield node
        for child in node['children']:
            yield from descendants(child)
    mains = [n for n in descendants(surface.tree) if n['tag'] == 'main']
    if len(mains) != 1:
        raise ValueError('topology requires one main viewport')
    main = mains[0]
    tokens = [t for t in main['attrs'].get('class', '').split() if t.startswith('topology-')]
    content = [n for n in main['children'] if n['tag'] == 'section' and 'content' in n['attrs'].get('class', '').split()]
    if len(tokens) != 1 or len(content) != 1:
        raise ValueError('topology requires a labelled main and an inner content component')
    children = [n for n in content[0]['children'] if not set(n['attrs'].get('class', '').split()) & {'hero', 'fold-panel', 'secondary'}]
    if not children:
        raise ValueError('topology inner child tree is empty')
    def shape(node):
        return (node['tag'], node['attrs'].get('role', ''), tuple(shape(n) for n in node['children']))
    return tokens[0], tuple(shape(n) for n in children)


def disclaimer_copy(text):
    banned = re.compile(r'演示数据|非实时|模拟|示意|非已证实失败|非成功/失败证明|synthetic|demo\s+only|mock\s+data|placeholder\s+stage', re.I)
    parts = re.split(r'[;；。!?！？\n]|\.(?:\s|$)|\b(?:but|however|yet|whereas)\b|但是|但|然而|不过|\band\s+(?=(?:display|render|show|use|present|confirm)\b)|[,，]\s*(?=(?:now|this|we|display|render|show)\b|现在|本页|显示)', text, flags=re.I)
    for part in parts:
        for match in banned.finditer(part):
            prefix = part[:match.start()]
            if not re.search(r'禁止|严禁|不得|不要|\b(?:never|must not|do not)\s+(?:use|show|display|render|include|generate|confirm|present|allow)\b|\bno\s+(?:use\s+(?:of\s+)?)?$', prefix, re.I):
                return True
    return False


def rendered_gallery_errors(root):
    root = root.resolve()
    errors, visited = [], set()

    def css_rules(text, label):
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
        variables = dict(re.findall(r'(--[\w-]+)\s*:\s*([^;{}]+)', text))
        for match in re.finditer(r'([^{}]+)\{([^{}]*)\}', text):
            selector, body = match.groups()
            for _ in range(8):
                expanded = re.sub(r'var\(\s*(--[\w-]+)\s*\)', lambda m: variables.get(m.group(1), m.group(0)), body)
                if expanded == body:
                    break
                body = expanded
            repeated = 'repeating-linear-gradient' in body.casefold()
            if re.search(r'\.ruler\b|\.(?:debug-bar|debug-controls|optical-debug)\b', selector, re.I):
                errors.append(f'{label}: forbidden debug ruler/bar CSS')
            if re.search(r'\.caustic-veil\b', selector, re.I) and repeated:
                errors.append(f'{label}: forbidden caustic-veil repeating-linear-gradient stripes')
            if 'repeating-linear-gradient' in body.casefold() and re.search(r'url\([\s\"\x27]*#apple-liquid-glass', body, re.I):
                errors.append(f'{label}: forbidden refracted striped overlay')
        return text

    def stylesheet(href, base):
        url = urlsplit(href)
        if url.scheme or url.netloc or not url.path:
            errors.append(f'unsafe local stylesheet: {href}')
            return
        raw = base / unquote(url.path)
        path = raw.resolve()
        if not path.is_relative_to(root) or any(p.is_symlink() for p in (raw, *raw.parents) if p.is_relative_to(root)):
            errors.append(f'unsafe local stylesheet: {href}')
            return
        if path in visited:
            return
        visited.add(path)
        try:
            text = css_rules(path.read_text(encoding='utf-8'), str(path.relative_to(root)))
            for match in re.finditer(r'@import\s+(?:url\(\s*)?[\"\x27]([^\"\x27]+)[\"\x27]|@import\s+url\(\s*([^\s)]+)\s*\)', text, re.I):
                stylesheet(match.group(1) or match.group(2), path.parent)
        except (OSError, UnicodeError) as error:
            errors.append(f'missing/invalid local stylesheet {href}: {error}')

    for path in sorted(root.glob('*.html')):
        if path.is_symlink():
            errors.append(f'unsafe rendered HTML: {path.name}')
            continue
        try:
            html = path.read_text(encoding='utf-8')
            surface = RenderedSurface(html)
            if any(disclaimer_copy(block) for block in [*surface.visible_blocks, *surface.labels]):
                errors.append(f'{path.name}: sandbox/meta-disclaimer copy is forbidden')
            internal = {'ready', 'empty', 'stale', 'failed', 'loading', 'error', 'simulated'}
            if any(text in internal or value in internal for text, value in surface.option_tokens):
                errors.append(f'{path.name}: inline test-state select is forbidden')
            for tag, attrs in surface.nodes:
                classes = set(attrs.get('class', '').split())
                if classes & {'ruler', 'debug-bar', 'debug-controls', 'optical-debug'} or attrs.get('id') in {'ruler', 'debug-bar', 'debug-controls', 'optical-debug'}:
                    errors.append(f'{path.name}: forbidden debug ruler/bar node')
                if 'caustic-veil' in classes and 'repeating-linear-gradient' in attrs.get('style', '').casefold():
                    errors.append(f'{path.name}: forbidden caustic-veil repeating-linear-gradient stripes')
                if path.name != 'index.html' and tag in {'button', 'input', 'textarea', 'select', 'nav'}:
                    if attrs.get('id') in {'responsive-toggle', 'copy-summary'} or any(k in attrs for k in ('data-theme', 'data-portal-theme', 'data-portal-platform', 'data-group-filter', 'data-select', 'data-star', 'data-note')):
                        errors.append(f'{path.name}: duplicate inner gallery control')
            if {'active', 'matte'} <= set(surface.labels) or 'active / matte' in surface.labels:
                errors.append(f'{path.name}: forbidden Active / Matte debug controls')
            css_rules('\n'.join(surface.styles), path.name)
            for href in surface.links:
                stylesheet(href, path.parent)
        except (OSError, UnicodeError, ValueError) as error:
            errors.append(f'{path.name}: invalid rendered surface: {error}')
    errors.extend(portal_errors(root))
    manifest_path = root / 'manifest.json'
    if manifest_path.is_file() and not manifest_path.is_symlink():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            parent = root.parent
            if parent != root and (parent / 'index.html').is_file() and not (parent / 'index.html').is_symlink():
                sibling_manifests = catalog()._portal_manifests(parent, manifest.get('goal'))
                if len(sibling_manifests) > 1:
                    errors.extend(f'portal: {error}' for error in portal_errors(parent))
        except (OSError, UnicodeError, ValueError, TypeError):
            pass
    return errors


def portal_errors(goal_root):
    """Fail closed on chooser controls and true hidden-card filtering."""
    index = Path(goal_root) / 'index.html'
    if not index.is_file() or index.is_symlink():
        return []
    try:
        html = index.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as error:
        return [f'index.html: unreadable portal surface: {error}']
    required = {
        'gallery-breadcrumbs': 'class="gallery-breadcrumbs"',
        'demos breadcrumb': 'href="/demos/"',
        'selection telemetry': 'id="selection-telemetry"',
        'copy summary': 'id="copy-summary"',
        'select controls': 'data-select',
        'star controls': 'data-star',
        'note controls': 'data-note',
    }
    errors = [f'index.html: missing {label}' for label, token in required.items() if token not in html]
    if 'aria-hidden="true"' in html and not re.search(r'(?:\.variant-card\[hidden\]|\.variant-card\[aria-hidden=["\']true["\']\])\s*\{[^}]*display\s*:\s*none', html, re.I | re.S):
        errors.append('index.html: aria-hidden card filtering must also hide cards with CSS')
    if not all(token in html for token in ('SELECT:', 'STAR:', 'NOTES:', '[data-star]', '[data-note]')):
        errors.append('index.html: executable summary must include SELECT, STAR, and NOTES from star/note controls')
    cards = list(re.finditer(r'<article\b[^>]*class="variant-card[^\"]*"[^>]*>.*?</article>', html, re.I | re.S))
    summaries_by_category = {}
    for match in cards:
        card = match.group(0)
        card_class = re.search(r'class="([^\"]*)"', card, re.I)
        category_match = re.search(r'data-group="([^\"]+)"', card, re.I)
        summary_match = re.search(r'<p\b[^>]*class="variant-summary"[^>]*>(.*?)</p>', card, re.I | re.S)
        header_end = card.lower().find('</header>')
        bezel_start = card.lower().find('<div class="device-bezel"')
        summary_start = summary_match.start() if summary_match else -1
        summary_end = summary_match.end() if summary_match else -1
        if not summary_match or header_end < 0 or bezel_start < 0 or not (header_end < summary_start < summary_end <= bezel_start):
            errors.append('index.html: every variant-card requires a correctly placed variant-summary')
            continue
        summary = re.sub(r'<[^>]+>', '', summary_match.group(1)).strip()
        if len(summary) < 10:
            errors.append('index.html: variant-summary must contain at least 10 characters')
        if not re.search(r'[\u4e00-\u9fff]', summary):
            errors.append('index.html: variant-summary must contain Chinese text')
        if card_class and 'current-card' not in card_class.group(1).split():
            category = category_match.group(1) if category_match else ''
            summaries_by_category.setdefault(category, []).append(summary)
    for category, summaries in summaries_by_category.items():
        if len(summaries) != len(set(summaries)):
            errors.append(f'index.html: duplicate candidate variant-summary in category {category}')

    groups = re.findall(r'data-group-filter="([^"]+)"[^>]*aria-pressed="([^"]+)"', html)
    categories = re.findall(r'class="variant-card[^\"]*"[^>]*data-group="([^"]+)"', html)
    unique_categories = list(dict.fromkeys(categories))
    if len(unique_categories) > 1:
        if not groups or groups[0][0] != unique_categories[0] or groups[0][1] != 'true' or not any(group == 'all' and pressed == 'false' for group, pressed in groups):
            errors.append('index.html: multi-category portal must activate first category and deactivate all tab')
        for category in unique_categories[1:]:
            if not re.search(rf'data-group="{re.escape(category)}"[^>]*\bhidden\b[^>]*aria-hidden="true"', html):
                errors.append(f'index.html: initial cards for category {category} are not hidden')
                break
    if '"portal": true' in html or '"portal":true' in html:
        try:
            evidence_catalog = catalog()
            config_match = re.search(r'<script id="gallery-config"[^>]*>(.*?)</script>', html, re.S)
            config = json.loads(config_match.group(1)) if config_match else {}
            goal = config.get('goal')
            manifests = evidence_catalog._portal_manifests(Path(goal_root), goal) if goal else []
            expected = evidence_catalog._render_goal_portal_index(goal, manifests)
            if manifests and html != expected:
                errors.append('index.html: portal DOM/CSS/runtime drifted from canonical renderer')
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            errors.append('index.html: invalid portal canonical evidence')
    return errors


_prompts_spec = importlib.util.spec_from_file_location('evidence_prompts', CATALOG_PATH.with_name('gallery_prompts.py'))
_PROMPTS = importlib.util.module_from_spec(_prompts_spec)
_prompts_spec.loader.exec_module(_PROMPTS)
_prompt_paragraphs = _PROMPTS._paragraphs
_overlap_ratio = _PROMPTS._overlap_ratio


def prompt_errors(root, manifest):
    contract = manifest.get('prompt_contract')
    variants = manifest.get('variants', [])
    if contract is None:
        return ['prompt metadata requires prompt_contract'] if any('prompt_file' in v or 'prompt_sha256' in v for v in variants if isinstance(v, dict)) else []
    if not isinstance(contract, dict) or contract.get('schema') != 'design-prompts/v1' or contract.get('source') not in {'starter', 'provided'} or contract.get('count') != len(variants):
        return ['invalid prompt_contract']
    errors = []
    prompt_texts = {}
    for variant in variants:
        identifier = variant.get('id', '')
        expected = f'prompts/{identifier}-prompt.md'
        if not re.fullmatch(r'(?:v|app)\d{2}', identifier) or variant.get('prompt_file') != expected:
            errors.append(f'{identifier}: missing/unsafe prompt_file binding')
            continue
        path = root / expected
        try:
            if (root / 'prompts').is_symlink() or path.is_symlink() or not path.is_file():
                raise ValueError('missing or unsafe prompt file')
            data = path.read_bytes()
            text = data.decode('utf-8')
            if not text.strip() or hashlib.sha256(data).hexdigest() != variant.get('prompt_sha256'):
                raise ValueError('empty or modified prompt file')
            prompt_texts[identifier] = text
        except (OSError, UnicodeError, ValueError) as error:
            errors.append(f'{identifier}: prompt binding failed: {error}')
    for left, right in itertools.combinations(prompt_texts, 2):
        a, b = _prompt_paragraphs(prompt_texts[left]), _prompt_paragraphs(prompt_texts[right])
        if a and b:
            overlap = _overlap_ratio(a, b)
            if overlap > 0.20:
                errors.append(f'{left}/{right}: prompt duplicate boilerplate exceeds 20% ({overlap:.2f})')
    return errors


def gallery_errors(root, manifest):
    errors, evidence = [], []
    c = catalog()
    platform = manifest.get('platform')
    anti, overrides = manifest.get('session_anti_patterns', []), manifest.get('style_overrides', {})
    if platform not in {'mobile', 'desktop', 'web'} or not isinstance(anti, list) or not all(isinstance(a, str) for a in anti) or not isinstance(overrides, dict):
        return ['invalid platform/session configuration'], []
    def compare(name, expected, coordinates=True):
        path = root / name
        if path.is_symlink() or not path.is_file():
            errors.append(f'missing or unsafe {name}')
            return
        actual = path.read_text(encoding='utf-8')
        try:
            parsed, canonical = PageParser(actual), PageParser(expected)
            if coordinates and parsed.coordinates() != canonical.coordinates():
                errors.append(f'{name}: embedded graph/coordinates do not match manifest')
            if actual != expected:
                errors.append(f'{name}: DOM/CSS/executable bindings differ from canonical renderer; regenerate the round')
            if not coordinates:
                evidence.append({'page': name, 'canonical_round_bindings': actual == expected,
                                 'variant_ids': [v['id'] for v in manifest['variants']], 'round': manifest['round']})
                return
            evidence.append({'page': name, 'coordinates': parsed.coordinates(),
                             'controls': sum(t == 'button' for t, _ in parsed.nodes),
                             'optics': {'displacement_channels': sum(t == 'fedisplacementmap' for t, _ in parsed.nodes),
                                        'recipe': 'signed squircle Snell/chromatic/Fresnel map',
                                        'application': 'floating backdrop (Blink) or ambient veil/specular rim (WebKit)'},
                             'content_material': 'solid' if actual == expected else 'unverified',
                             'declared_minimum_target_px': 44 if platform == 'mobile' else None,
                             'canonical_operations': actual == expected})
        except (ValueError, TypeError):
            errors.append(f'{name}: missing/invalid embedded coordinates or executable document')
    try:
        if manifest.get('schema') not in {'design-gallery/v2', 'tenfold-gallery/v1'}:
            raise ValueError('unsupported gallery schema')
        errors.extend(rendered_gallery_errors(root))
        errors.extend(prompt_errors(root, manifest))
        c.NAVIGATION.session_contract(anti, overrides)
        if manifest.get('mode') == 'app-nav':
            schemes = manifest.get('schemes')
            if not isinstance(schemes, list) or len(schemes) != 4:
                return ['app-nav requires exactly four validated schemes'], []
            c.NAVIGATION.presentation(overrides)
            normalized = [c.NAVIGATION.validate_scheme(s, i, manifest.get('app_context', '')) for i, s in enumerate(schemes, 1)]
            if normalized != schemes:
                errors.append('manifest schemes are not normalized operative graphs')
            expected_variants = [{'id': s['id'], 'title': s['title'], 'navigation_model': s['navigation_model'],
                                  'screens': [p['id'] for p in s['screens']]} for s in normalized]
            actual_variants = [{k: v for k, v in variant.items() if k not in {'prompt_file', 'prompt_sha256', 'summary', 'one_liner', 'summary_zh'}} for variant in manifest.get('variants', [])]
            if actual_variants != expected_variants:
                errors.append('manifest variant summaries do not match operative schemes')
            if len({c.NAVIGATION.architecture_signature(s) for s in normalized}) != 4:
                errors.append('app-nav architectures differ only in labels')
            initial_signatures = {c.NAVIGATION.initial_state_signature(s) for s in normalized}
            if len(initial_signatures) != 4:
                errors.append('app-nav schemes need exactly four distinct initial states')
            for i, scheme in enumerate(normalized, 1):
                compare(f'app{i:02d}.html', c._app_nav_html(scheme, i, platform, anti, overrides))
            compare('current.html', c._app_nav_html(normalized[0], 0, platform, anti, overrides))
            for name, expected in [('tokens.css', c._gallery_tokens()), ('screen.css', (c.ROOT / 'assets' / 'tenfold-gallery.css').read_text())]:
                path = root / name
                if path.is_symlink() or not path.is_file() or path.read_text() != expected:
                    errors.append(f'{name}: rendering assets differ from canonical renderer')
            compare('index.html', c._gallery_index(manifest), coordinates=False)
        else:
            variants = manifest.get('variants')
            if not isinstance(variants, list) or len(variants) != 10 or {v.get('id') for v in variants if isinstance(v, dict)} != {f'v{i:02d}' for i in range(1, 11)}:
                return ['gallery must contain exactly v01..v10'], []
            axes = ('p0', 'axis_y', 'archetype', 'layout', 'whitespace', 'glass_geometry')
            allowed = {'p0': {'next-action', 'timeline', 'context', 'recovery'},
                       'axis_y': {'accordion', 'segmented-lens', 'swwallet'},
                       'archetype': {'VS-005', 'VS-006', 'VS-007', 'VS-008'},
                       'layout': {'hero-top', 'hero-inline', 'hero-bottom', 'hero-center'},
                       'whitespace': {'tight', 'balanced', 'open'},
                       'glass_geometry': {'wide', 'compact', 'rail', 'lifted'}}
            for v in variants:
                if any(v.get(a) not in allowed[a] for a in axes):
                    raise ValueError(f'{v.get("id")}: missing/invalid operative depth axes')
            distances = []
            for a, b in itertools.combinations(variants, 2):
                distance = sum(a[k] != b[k] for k in axes)
                distances.append(distance)
                if distance < 2:
                    errors.append(f'{a["id"]}/{b["id"]}: need two substantive axis differences')
            evidence.append({'variant_count': 10, 'checked_pairs': len(distances), 'minimum_distance': min(distances)})
            topology_names, topology_trees = [], []
            for v in variants:
                name = v['id'] + '.html'
                expected = c._gallery_html(f'{manifest["goal"]} {v["id"]}', v['id'], platform, v, anti, overrides)
                compare(name, expected)
                try:
                    topology, children = topology_signature((root / name).read_text(encoding='utf-8'))
                    topology_names.append(topology)
                    topology_trees.append(children)
                    if (topology, children) != topology_signature(expected):
                        errors.append(f'{name}: inner DOM topology/child tree does not match the declared morphology')
                except (OSError, ValueError) as error:
                    errors.append(f'{name}: invalid DOM topology: {error}')
            if len(set(topology_names)) != 10 or len(set(topology_trees)) != 10:
                errors.append('screen variants must have ten distinct DOM topology/child trees independent of cosmetic IDs/classes')
            current_meta = {'p0': 'current', 'axis_y': 'accordion', 'archetype': variants[0]['archetype'],
                            'content': manifest.get('screen_content') or f'{manifest["goal"]} key screen'}
            # The first breadth variant is not necessarily the current archetype.
            current_meta['archetype'] = manifest.get('direction') or 'VS-005'
            compare('current.html', c._gallery_html(f'{manifest["goal"]} current', 'current', platform, current_meta, anti, overrides))
            for name, expected in [('tokens.css', c._gallery_tokens()), ('screen.css', (c.ROOT / 'assets' / 'tenfold-gallery.css').read_text())]:
                if not (root / name).is_file() or (root / name).read_text() != expected:
                    errors.append(f'{name}: rendering assets do not implement the declared axes/optics')
            compare('index.html', c._gallery_index(manifest), coordinates=False)
    except (KeyError, ValueError, TypeError, OSError) as e:
        errors.append(f'invalid gallery contract: {e}')
    return errors, evidence


def clauses(text):
    """Clause-local polarity; prohibitions never suppress a later directive."""
    scope = 'implementation'
    for line in text.splitlines():
        if line.startswith('#'):
            scope = 'example' if re.search(r'(?i)\b(?:web demo|html sandbox|counterexample|forbidden example)\b', line) else 'implementation'
            continue
        if scope == 'example':
            continue
        # Independent contrast clauses reset polarity; temporal gerunds do not.
        for clause in re.split(r'[;。!?\n]|\.(?:\s|$)|\b(?:but|however|yet|whereas)\b|\bwhile\b(?!\s+\w+ing\b)(?=\s+\S)|,\s*(?=(?:implement|use|render|build|add)\b)|\band\s+(?=(?:implement|use|render|build|add)\b)', line, flags=re.I):
            clause = clause.strip().casefold()
            if clause:
                yield clause


def _matches(text, pattern, positive=True):
    for clause in clauses(text):
        for match in re.finditer(pattern, clause):
            prefix = clause[:match.start()]
            negated = bool(re.search(r'\b(?:no|never|without|zero|avoid|prohibit|prohibited|forbid|forbidden)\b|\b(?:must|do|does|should|shall)\s+not\b|\b(?:don\x27t|doesn\x27t|shouldn\x27t)\b|禁止|不得|不要', prefix))
            if positive != negated:
                yield match


def platform_errors(platform, text):
    errors = []
    def has(pattern): return bool(list(_matches(text, pattern)))
    def require(pattern, message):
        if not has(pattern): errors.append(message)
    if platform in {'mobile', 'desktop'}:
        if has(r'react\s+dom|css\s+html|fedisplacementmap|\bhtml\b|\bsvg\b|\bcss\b'):
            errors.append(f'{platform} native implementation leaks Web primitives (scope the offline HTML sandbox separately)')
        if has(r'glass\s+(?:on|in)\s+content|content\s+(?:rows?\s+)?glass'):
            errors.append('content must remain solid, not glass')
    if platform == 'mobile':
        require(r'navigationstack|tabview|\bsheet\b|safearea(?:inset|_inset)', 'mobile native navigation topology missing')
        minimum = 44
    elif platform == 'desktop':
        if has(r'mobile\s+bottom\s+tabs|bottom\s+tabview|tabview\s+bar'):
            errors.append('desktop uses mobile bottom tabs')
        require(r'keyboard|⌘|\bcmd\b', 'desktop keyboard parity missing')
        require(r'inactive|appearsactive|dimming', 'desktop inactive-window dimming missing')
        if list(_matches(text, r'keyboard|⌘|\bcmd\b', positive=False)):
            errors.append('desktop forbids required keyboard parity')
        if list(_matches(text, r'inactive|appearsactive|dimming', positive=False)):
            errors.append('desktop forbids required inactive-window dimming')
        require(r'navigationsplitview|inspector|menubarextra|nspopover', 'desktop split/inspector topology missing')
        minimum = 28
    else:
        if has(r'box-in-box|nested\s+cages?|decorative[^;]{0,25}emojis?|tracked[^;]{0,25}(?:eyebrows?|capitals?)'):
            errors.append('web anti-slop requirement violated')
        measures = [int(m.group(1)) for m in _matches(text, r'(\d+)\s*ch')]
        if any(m > 72 or m < 60 for m in measures):
            errors.append('web prose measure must be 60–72ch')
        require(r'60\s*[–-]\s*72\s*ch|(?:6\d|7[0-2])\s*ch', 'web editorial measure missing')
        require(r'cjk[^;]{0,40}wrap', 'web natural CJK wrapping missing')
        if list(_matches(text, r'cjk[^;]{0,40}wrap', positive=False)):
            errors.append('web forbids required CJK wrapping')
        require(r'fedisplacementmap', 'web optical refraction missing')
        if list(_matches(text, r'fedisplacementmap', positive=False)):
            errors.append('web forbids required optical refraction')
        return errors
    targets = []
    for m in _matches(text, r'(\d+)\s*[x×]\s*(\d+)\s*(?:pt|px)?'):
        targets.append(min(int(m.group(1)), int(m.group(2))))
    for m in _matches(text, r'(\d+)\s*pt\s*(?:hit\s*)?targets?|targets?[^;]{0,20}?(\d+)\s*pt'):
        targets.append(int(m.group(1) or m.group(2)))
    if not targets or max(targets) < minimum:
        errors.append(f'{platform} {minimum}pt target evidence missing')
    if any(t < minimum for t in targets):
        errors.append(f'{platform} hit target below {minimum}pt')
    return errors


def _source(value):
    return isinstance(value, str) and bool(re.search(r'\S+(?::\d+|#\S+)', value))


def _assertion(rule):
    if not isinstance(rule, dict) or rule.get('operator') not in {'minimum', 'maximum', 'equal', 'absent'}:
        raise ValueError('assertion requires minimum|maximum|equal|absent operator and observed/required values')
    required, observed = rule.get('required'), rule.get('observed')
    if rule['operator'] in {'minimum', 'maximum'}:
        if type(required) not in {int, float} or type(observed) not in {int, float}:
            raise ValueError('numeric assertion values must be numbers, not bool/string')
        return observed >= required if rule['operator'] == 'minimum' else observed <= required
    if 'required' not in rule or 'observed' not in rule:
        raise ValueError('assertion requires required and observed values')
    return observed == required if rule['operator'] == 'equal' else not observed


def traceback_errors(text):
    errors, evidence = [], []
    try:
        # JSON file or one fenced JSON record in a report. Prose is not evidence.
        fenced = re.findall(r'```json\s*\n(.*?)\n```', text, re.S)
        data = json.loads(fenced[0] if len(fenced) == 1 else text)
        if not isinstance(data, dict) or data.get('schema') != 'visual-traceback/v1' or not isinstance(data.get('findings'), list):
            raise ValueError('expected visual-traceback/v1 with findings array')
        findings = data['findings']
        if not findings:
            clean = data.get('clean')
            if not isinstance(clean, dict) or not _source(clean.get('spec_source')) or not _source(clean.get('implementation_source')) or not clean.get('checks'):
                raise ValueError('clean result requires spec/implementation source references and checks')
            for check in clean['checks']:
                if not check.get('requirement') or not _assertion(check.get('assertion')):
                    raise ValueError('clean result contains missing/failed evidence check')
            return [], [{'clean': clean}]
        if data.get('clean'):
            raise ValueError('cannot mix a clean result with findings')
        for i, f in enumerate(findings, 1):
            constraint, impl, route = f.get('constraint', {}), f.get('implementation', {}), f.get('route', {})
            if type(constraint.get('declared')) is not bool or type(impl.get('conforms')) is not bool:
                raise ValueError(f'finding {i}: declared and conforms must be explicit booleans')
            if not _source(constraint.get('source')) or not constraint.get('requirement') or not _source(impl.get('source')) or not impl.get('observation'):
                raise ValueError(f'finding {i}: source, requirement and observed implementation evidence required')
            declared, conforms = constraint['declared'], impl['conforms']
            if declared:
                conforms_actual = _assertion(impl.get('assertion'))
                if conforms_actual != conforms:
                    raise ValueError(f'finding {i}: assertion contradicts conforms')
            else:
                if not constraint.get('missing_requirement') or not conforms:
                    raise ValueError(f'finding {i}: missing requirement needs compliant declared implementation evidence')
            expected = 'IMPL_BUG' if declared and not conforms else 'SPEC_GAP' if not declared and conforms else None
            if not expected or f.get('classification') != expected:
                raise ValueError(f'finding {i}: evidence requires {expected or "no defect"}, not {f.get("classification")}')
            owner, amend = ('Writer', False) if expected == 'IMPL_BUG' else ('Steering', True)
            if route.get('owner') != owner or route.get('amend_spec_first') is not amend:
                raise ValueError(f'finding {i}: route must be {owner}, amend_spec_first={amend}')
            evidence.append(f)
    except (ValueError, TypeError, AttributeError) as e:
        errors.append(f'traceback: {e}; supply structured evidence, not classification vocabulary')
    return errors, evidence
