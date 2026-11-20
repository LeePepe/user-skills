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
import struct
import subprocess
from urllib.parse import unquote, urlsplit
import zlib

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


def _css_blocks(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return [(selector.strip(), re.sub(r'\s+', ' ', body).strip()) for selector, body in re.findall(r'([^{}]+)\{([^{}]*)\}', text) if selector.strip() and body.strip()]


def _hex(value, variables, seen=None):
    seen = set() if seen is None else seen
    value = value.strip().lower()
    if value.startswith('var('):
        name = re.match(r'var\(\s*(--[\w-]+)', value)
        if name and name.group(1) not in seen and name.group(1) in variables:
            return _hex(variables[name.group(1)], variables, seen | {name.group(1)})
    value = value.split()[0].rstrip(';,')
    if re.fullmatch(r'#[0-9a-f]{3}', value):
        return '#' + ''.join(c * 2 for c in value[1:])
    return value if re.fullmatch(r'#[0-9a-f]{6}(?:[0-9a-f]{2})?', value) else None


def anti_clone_css_and_layout_errors(root):
    """Reject copied screen variants while ignoring shared gallery chrome."""
    root = Path(root).resolve()
    counts = [count for count in (4, 10) if all((root / f'v{i:02d}.html').is_file() and not (root / f'v{i:02d}.html').is_symlink() for i in range(1, count + 1))]
    if not counts:
        return []
    count = 10 if 10 in counts else 4
    paths = [root / f'v{i:02d}.html' for i in range(1, count + 1)]
    errors, records, all_variant_classes = [], [], set()
    for path in paths:
        html = path.read_text(encoding='utf-8')
        surface = RenderedSurface(html)
        mains = [node for node in surface.tree.get('children', []) if node.get('tag') == 'html']
        main_nodes = [node for node in surface.tree.get('children', []) if node.get('tag') == 'main']
        main = main_nodes[0] if main_nodes else next((n for n in surface.nodes if n[0] == 'main'), (None, {}))[1]
        classes = set(main.get('class', '').split()) if isinstance(main, dict) else set()
        for _, attrs in surface.nodes:
            classes.update(attrs.get('class', '').split())
        all_variant_classes.update(classes)
        records.append({'path': path, 'html': html, 'surface': surface, 'main': main, 'classes': classes})
    css_cache = {}
    def local_css(record):
        texts = list(record['surface'].styles)
        for href in record['surface'].links:
            url = urlsplit(href)
            if url.scheme or url.netloc or not url.path:
                continue
            candidate = (record['path'].parent / unquote(url.path)).resolve()
            if candidate.is_relative_to(root) and candidate.is_file() and not candidate.is_symlink():
                if candidate not in css_cache:
                    css_cache[candidate] = candidate.read_text(encoding='utf-8')
                texts.append(css_cache[candidate])
        return texts
    class_pattern = re.compile(r'\.([A-Za-z_][\w-]*)')
    morphology_name = re.compile(r'(?:bento|timeline|pulse|inset|glass[-_]?row|ledger|pro[-_]?table|morph|variant|layout|stage|grid|rail|stack|panel|card)', re.I)
    class_counts = {}
    def is_variant_alias(selector):
        parts = [part.strip() for part in selector.split(',')]
        names = {next(iter(class_pattern.findall(part)), '') for part in parts if class_pattern.findall(part)}
        unique_names = {name for name in names if class_counts.get(name) == 1}
        return len(names) >= 2 and (len(unique_names) >= 2 or sum(bool(morphology_name.search(name)) for name in names) >= 3)
    for record in records:
        for cls in record['classes']:
            class_counts[cls] = class_counts.get(cls, 0) + 1
    geometry_keys = ('display:', 'grid-template-', 'grid-auto-', 'flex-', 'flex-direction:', 'order:', 'place-', 'position:', 'inset:', 'justify-', 'align-')
    for record in records:
        unique_classes = {cls for cls in record['classes'] if class_counts.get(cls) == 1}
        blocks, variables = [], {}
        for text in local_css(record) + [record['html']]:
            for name, value in re.findall(r'(--[\w-]+)\s*:\s*([^;{}]+)', text):
                variables[name] = value.strip()
            if text == record['html']:
                styles = re.findall(r'<style[^>]*>(.*?)</style>', text, re.S | re.I)
                parsed = [block for style in styles for block in _css_blocks(style)]
            else:
                parsed = _css_blocks(text)
            for selector, body in parsed:
                selector_classes = set(class_pattern.findall(selector))
                if is_variant_alias(selector):
                    errors.append(f"{record['path'].name}: comma-aliased variant selector shell")
                variant_classes = selector_classes & unique_classes
                if not variant_classes:
                    continue
                if any(c.startswith(('topology-', 'archetype-', 'layout-', 'fold-', 'whitespace-', 'glass-', 'bento-', 'timeline-', 'pulse-', 'ring-', 'master-', 'split-', 'floating-', 'pocket-', 'command-', 'editorial-')) for c in variant_classes) or any(key in body for key in geometry_keys):
                    blocks.append(re.sub(r'\s+', '', body).lower())
        inline = re.search(r'<main\b[^>]*\bstyle=[\"\x27]([^\"\x27]*)', record['html'], re.I)
        inline_style = inline.group(1) if inline else ''
        for name, value in re.findall(r'(--[\w-]+)\s*:\s*([^;]+)', inline_style):
            variables[name] = value.strip()
        accent = _hex(variables.get('--variant-accent', variables.get('--accent', '')), variables)
        surface_hex = _hex(variables.get('--content-surface', variables.get('--surface', variables.get('--bg', variables.get('--paper', '')))), variables)
        geometry = []
        for block in blocks:
            if any(key in block for key in geometry_keys):
                geometry.append(block)
        records[paths.index(record['path'])].update({'blocks': set(blocks), 'palette': (surface_hex, accent), 'accent': accent, 'geometry': tuple(sorted(set(geometry)))})
    for selector, body in [block for record in records for text in local_css(record) for block in _css_blocks(text)]:
        if is_variant_alias(selector):
            errors.append('linked stylesheet: comma-aliased variant selector shell')
    for left, right in itertools.combinations(records, 2):
        union = left['blocks'] | right['blocks']
        overlap = len(left['blocks'] & right['blocks']) / len(union) if union else 0
        if overlap > 0.35:
            errors.append(f"{left['path'].name}/{right['path'].name}: normalized CSS overlap {overlap:.2f} > 0.35")
    palettes, accents = {}, {}
    for record in records:
        palettes.setdefault(record['palette'], []).append(record['path'].name)
        accents.setdefault(record['accent'], []).append(record['path'].name)
        if record['palette'] == (None, None):
            errors.append(f"{record['path'].name}: missing effective surface/accent palette")
    for palette, names in palettes.items():
        if palette != (None, None) and len(names) > 2:
            errors.append(f"palette {palette} reused by {', '.join(names)}")
    for accent, names in accents.items():
        if accent is not None and len(names) > 2:
            errors.append(f"accent {accent} reused by {', '.join(names)}")
    signatures = {record['geometry'] for record in records}
    minimum = 6 if count == 10 else 4
    if len(signatures) < minimum:
        errors.append(f'layout diversity requires {minimum} spatial signatures, found {len(signatures)}')
    return list(dict.fromkeys(errors))


def layout_diversity_errors(root):
    return [error for error in anti_clone_css_and_layout_errors(root) if 'layout diversity' in error or 'spatial signatures' in error]


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
        if href in {'/assets/colors.css', '/assets/strata-theme.css', '/assets/blog.css'}:
            return
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
    if any(all((root / f'v{i:02d}.html').is_file() for i in range(1, count + 1)) for count in (4, 10)):
        errors.extend(anti_clone_css_and_layout_errors(root))
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
        'gallery-breadcrumbs': re.compile(r'class="[^"]*\bgallery-breadcrumbs\b[^"]*"'),
        'demos breadcrumb': 'href="/demos/"',
        'selection telemetry': 'id="selection-telemetry"',
        'copy summary': 'id="copy-summary"',
        'select controls': 'data-select',
        'star controls': 'data-star',
        'note controls': 'data-note',
    }
    errors = [f'index.html: missing {label}' for label, token in required.items() if not (token.search(html) if hasattr(token, 'search') else token in html)]
    if 'aria-hidden="true"' in html and not re.search(r'(?:\.variant-card\[hidden\]|\.variant-card\[aria-hidden=["\']true["\']\])\s*\{[^}]*display\s*:\s*none', html, re.I | re.S):
        errors.append('index.html: aria-hidden card filtering must also hide cards with CSS')
    if not all(token in html for token in ('SELECT:', 'STAR:', 'NOTES:', '[data-star]', '[data-note]')):
        errors.append('index.html: executable summary must include SELECT, STAR, and NOTES from star/note controls')
    scripts = [body for attrs, body in re.findall(r'<script\b([^>]*)>(.*?)</script>', html, re.I | re.S)
               if not re.search(r'type=["\']application/json["\']', attrs, re.I)]
    runtime = '\n'.join(scripts)
    if not re.search(r'selectionSummary\(\s*config\.goal\s*,\s*currentState\(\)\s*,\s*root\s*,\s*config\.cards\s*\)', runtime):
        errors.append('index.html: runtime copy-summary must pass explicit config.cards')
    if not all(re.search(rf'function\s+{name}\s*\(', runtime) for name in ('selectionSummary', 'updateTelemetry')):
        errors.append('index.html: runtime summary/telemetry identifiers are missing')
    try:
        if not runtime or len(runtime) > 512_000:
            raise ValueError('missing or oversized embedded runtime')
        probe = subprocess.run(['node', str(CATALOG_PATH.with_name('gallery_chooser_test.cjs')), '--verify-runtime'],
                               input=json.dumps({'source': runtime}), text=True, capture_output=True, timeout=5)
        if probe.returncode:
            errors.append('index.html: runtime behavior failed active inventory, category notes or control/telemetry verification')
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        errors.append(f'index.html: runtime behavioral verification unavailable: {error}')
    try:
        config_match = re.search(r'<script id="gallery-config"[^>]*>(.*?)</script>', html, re.S)
        config_cards = json.loads(config_match.group(1)).get('cards', []) if config_match else []
        if not isinstance(config_cards, list):
            raise ValueError('cards must be an array')
    except (ValueError, TypeError, AttributeError):
        config_cards = []
        errors.append('index.html: invalid gallery-config inventory')
    cards = list(re.finditer(r'<article\b[^>]*class="variant-card[^\"]*"[^>]*>.*?</article>', html, re.I | re.S))
    summaries_by_category = {}
    for index, match in enumerate(cards):
        card = match.group(0)
        card_class = re.search(r'class="([^\"]*)"', card, re.I)
        category_match = re.search(r'data-group="([^\"]+)"', card, re.I)
        summary_matches = list(re.finditer(r'<p\b[^>]*class="[^"]*\bvariant-summary\b[^"]*"[^>]*>(.*?)</p>', card, re.I | re.S))
        summary_match = summary_matches[0] if summary_matches else None
        nodes = RenderedSurface(card).nodes
        if sum('variant-summary' in attrs.get('class', '').split() for _, attrs in nodes) > 1:
            errors.append('index.html: every variant-card may contain only one variant-summary')
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
            variant_match = re.search(r'data-variant="([^"]+)"', card, re.I)
            identity = config_cards[index] if index < len(config_cards) and isinstance(config_cards[index], dict) else {}
            expected = f'{identity.get("category", category)}/{identity.get("id", variant_match.group(1).split("/", 1)[-1] if variant_match else "")}'
            if not variant_match or variant_match.group(1) != expected:
                errors.append(f'index.html: data-variant must use category-prefixed id {expected}')
            select = [attrs for tag, attrs in nodes if tag == 'input' and 'data-select' in attrs]
            star = [attrs for tag, attrs in nodes if tag == 'input' and 'data-star' in attrs]
            note = [attrs for tag, attrs in nodes if tag == 'textarea' and 'data-note' in attrs]
            controls = {
                'data-star': [attrs.get('data-star') for attrs in star],
                'data-note': [attrs.get('data-note') for attrs in note],
                'select value': [attrs.get('value') for attrs in select],
                'aria-label': [attrs.get('aria-label') for attrs in note],
            }
            for label, values in controls.items():
                target = expected + ' Notes' if label == 'aria-label' else expected
                if values != [target]:
                    errors.append(f'index.html: {label} must use category-prefixed id {expected}')
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
            count = manifest.get('count', len(variants) if isinstance(variants, list) else 0)
            if not isinstance(variants, list) or count not in {4, 10} or len(variants) != count or {v.get('id') for v in variants if isinstance(v, dict)} != {f'v{i:02d}' for i in range(1, count + 1)}:
                return ['gallery must contain exactly contiguous v01..v04 or v01..v10'], []
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
            evidence.append({'variant_count': count, 'checked_pairs': len(distances), 'minimum_distance': min(distances)})
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
            if len(set(topology_names)) != count or len(set(topology_trees)) != count:
                errors.append(f'screen variants must have {count} distinct DOM topology/child trees independent of cosmetic IDs/classes')
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


def _unvalidated_marker(value):
    if isinstance(value, dict):
        return any(_unvalidated_marker(key) or _unvalidated_marker(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_unvalidated_marker(item) for item in value)
    return isinstance(value, str) and 'unvalidated' in value.casefold()


def _image_path(source, base_dir, platform=None):
    raw = re.split(r'[#]', source, maxsplit=1)[0]
    path = Path(raw)
    if not path.is_absolute():
        path = (base_dir / path) if base_dir else (Path.cwd() / path)
    path = path.resolve()
    if path.is_symlink() or not path.is_file() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f'screenshot artifact is missing or symlinked: {source}')
    data = path.read_bytes()
    width = height = None
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        if len(data) < 33 or data[12:16] != b'IHDR' or int.from_bytes(data[16:20], 'big') < 1 or int.from_bytes(data[20:24], 'big') < 1 or b'IEND' not in data:
            raise ValueError(f'screenshot PNG is truncated or has invalid dimensions: {source}')
        width, height = int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
    elif data.startswith(b'\xff\xd8\xff'):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if index + 2 > len(data):
                break
            length = int.from_bytes(data[index:index + 2], 'big')
            if length < 2 or index + length > len(data):
                break
            if marker in set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0)):
                if length >= 7:
                    height = int.from_bytes(data[index + 3:index + 5], 'big')
                    width = int.from_bytes(data[index + 5:index + 7], 'big')
                break
            index += length
        if not width or not height:
            raise ValueError(f'screenshot JPEG dimensions are unverifiable: {source}')
    else:
        raise ValueError(f'screenshot artifact is not PNG or JPEG: {source}')
    bounds = {'mobile': (390, 844), 'desktop': (1280, 800)}
    if platform in bounds:
        minimum_width, minimum_height = bounds[platform]
        if width < minimum_width or height < minimum_height:
            raise ValueError(f'screenshot is below {platform} viewport {minimum_width}x{minimum_height}: {source}')
    elif (width < 390 or height < 844) and (width < 1280 or height < 800):
        raise ValueError(f'screenshot is below supported viewport minimum: {source}')
    return path


def _decode_png(data):
    if len(data) < 33 or data[:8] != b'\x89PNG\r\n\x1a\n' or data[12:16] != b'IHDR':
        raise ValueError('not a valid PNG')
    width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack('>IIBBBBB', data[16:29])
    if width < 1 or height < 1 or bit_depth not in (8, 16) or color_type not in (0, 2, 4, 6) or comp != 0 or filt != 0 or interlace != 0:
        raise ValueError('unsupported PNG format (requires 8/16-bit non-interlaced Grayscale/RGB/RGBA)')
    samples_per_pixel = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    bytes_per_sample = 2 if bit_depth == 16 else 1
    bpp = samples_per_pixel * bytes_per_sample
    idat = bytearray()
    offset = 8
    seen_iend = False
    while offset + 12 <= len(data):
        length = struct.unpack_from('>I', data, offset)[0]
        chunk_type = data[offset + 4:offset + 8]
        chunk_end = offset + 8 + length
        if chunk_end + 4 > len(data):
            raise ValueError('truncated PNG chunk')
        if chunk_type == b'IDAT':
            idat.extend(data[offset + 8:chunk_end])
        elif chunk_type == b'IEND':
            seen_iend = True
            break
        offset = chunk_end + 4
    if not seen_iend or not idat:
        raise ValueError('incomplete PNG stream')
    stride = width * bpp
    expected = height * (1 + stride)
    raw = zlib.decompress(bytes(idat))
    if len(raw) != expected:
        raise ValueError('invalid PNG scanline byte count')
    rows = []
    cursor = 0
    prior = bytearray(stride)
    out_channels = 4 if color_type in (4, 6) else 3
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        row = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        if filter_type == 1:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 255
        elif filter_type == 2:
            for i in range(stride):
                row[i] = (row[i] + prior[i]) & 255
        elif filter_type == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prior[i]) >> 1)) & 255
        elif filter_type == 4:
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                b = prior[i]
                c = prior[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[i] = (row[i] + pr) & 255
        elif filter_type != 0:
            raise ValueError(f'invalid PNG filter type: {filter_type}')
        prior = row
        if bit_depth == 8 and color_type in (2, 6):
            rows.append(bytes(row))
        else:
            norm = bytearray(width * out_channels)
            for x in range(width):
                base = x * bpp
                if color_type in (0, 4):
                    g = row[base]
                    dst = x * out_channels
                    norm[dst] = g
                    norm[dst + 1] = g
                    norm[dst + 2] = g
                    if out_channels == 4:
                        norm[dst + 3] = row[base + bytes_per_sample]
                else:
                    dst = x * out_channels
                    norm[dst] = row[base]
                    norm[dst + 1] = row[base + bytes_per_sample]
                    norm[dst + 2] = row[base + 2 * bytes_per_sample]
                    if out_channels == 4:
                        norm[dst + 3] = row[base + 3 * bytes_per_sample]
            rows.append(bytes(norm))
    return width, height, rows, out_channels


def _region_bottom_dead_space_ratio(width, height, rows, channels, x_start_ratio, x_end_ratio):
    x0 = max(0, min(width - 1, int(width * x_start_ratio)))
    x1 = max(x0 + 1, min(width, int(width * x_end_ratio)))
    step = max(1, (x1 - x0) // 160)
    xs = list(range(x0, x1, step))
    if not xs:
        return 0.0

    def row_is_uniform(y, ref_rgb=None):
        row = rows[y]
        mid_x = xs[len(xs) // 2]
        base_r, base_g, base_b = row[mid_x * channels], row[mid_x * channels + 1], row[mid_x * channels + 2]
        target_r, target_g, target_b = ref_rgb if ref_rgb is not None else (base_r, base_g, base_b)
        matching = 0
        for x in xs:
            idx = x * channels
            r, g, b = row[idx], row[idx + 1], row[idx + 2]
            if max(abs(r - target_r), abs(g - target_g), abs(b - target_b)) <= 8 and max(abs(r - base_r), abs(g - base_g), abs(b - base_b)) <= 8:
                matching += 1
        return (matching / len(xs)) >= 0.98, (base_r, base_g, base_b)

    bezel_limit = max(1, int(height * 0.02))
    y = height - 1
    skipped_bezel = 0
    while y >= 0 and skipped_bezel < bezel_limit:
        uniform, _ = row_is_uniform(y)
        if uniform:
            break
        skipped_bezel += 1
        y -= 1
    if y < 0:
        return 0.0
    first_uniform, ref_rgb = row_is_uniform(y)
    if not first_uniform:
        return 0.0
    blank_rows = 0
    while y >= 0:
        uniform, _ = row_is_uniform(y, ref_rgb)
        if not uniform:
            break
        blank_rows += 1
        y -= 1
    return round(blank_rows / max(1, height), 4)


def check_whitespace_ratio(path, max_dead_space_ratio=0.40):
    target = Path(path)
    images = [target] if target.is_file() else sorted(target.glob('*.png')) if target.is_dir() else []
    if not images:
        return [f'whitespace check path not found: {path}'], []
    errors, metrics = [], []
    for image in images:
        try:
            if image.is_symlink():
                raise ValueError('refusing symlink PNG')
            width, height, rows, channels = _decode_png(image.read_bytes())
            full_ratio = _region_bottom_dead_space_ratio(width, height, rows, channels, 0.02, 0.98)
            detail_ratio = _region_bottom_dead_space_ratio(width, height, rows, channels, 0.20, 0.96) if width >= 600 else full_ratio
            ratio = max(full_ratio, detail_ratio)
            ok = ratio <= max_dead_space_ratio
            metrics.append({
                'file': str(image),
                'width': width,
                'height': height,
                'dead_space_ratio': ratio,
                'full_canvas_ratio': full_ratio,
                'detail_canvas_ratio': detail_ratio,
                'max_dead_space_ratio': max_dead_space_ratio,
                'ok': ok,
            })
            if not ok:
                errors.append(f'{image.name}: VISUAL_EXCESSIVE_DEAD_SPACE (bottom contiguous dead space {ratio:.1%} > {max_dead_space_ratio:.0%})')
        except (OSError, ValueError, struct.error, zlib.error) as error:
            errors.append(f'{image.name}: invalid PNG for whitespace check: {error}')
    return errors, metrics


def audit_geometry(path_or_data, platform='desktop', collision_threshold_pt2=2.0):
    if isinstance(path_or_data, dict):
        document = path_or_data
    else:
        try:
            document = json.loads(Path(path_or_data).read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            return [f'GEOM_INVALID: {error}'], []
    if not isinstance(document, dict):
        return ['GEOM_INVALID: geometry root must be an object'], []
    errors, details = [], []
    if document.get('fixedSizeVerticalTightCrop') is True or document.get('snapshotMode') == 'fixedSize':
        errors.append('FIXED_SIZE_PAGE_SNAPSHOT_CROP')
        details.append({'rule': 'FIXED_SIZE_PAGE_SNAPSHOT_CROP', 'reason': 'page-level .fixedSize(horizontal: false, vertical: true) tight-crop is forbidden'})
    viewport = float(document.get('viewportWidth', (document.get('viewport') or {}).get('width', 0) if isinstance(document.get('viewport'), dict) else 0) or 0)
    nodes = []

    def visit(value, parent=None, ancestors=(), scope='default'):
        if isinstance(value, dict):
            current_scope = scope
            raw_label = str(value.get('label', value.get('id', '')) or '')
            if parent is document and not isinstance(value.get('frame'), dict) and raw_label:
                current_scope = raw_label
            elif '/' in raw_label and current_scope == 'default':
                current_scope = raw_label.split('/', 1)[0]
            node_entry = None
            next_ancestors = ancestors
            if isinstance(value.get('frame'), dict):
                node_entry = dict(value)
                node_entry['_obj_id'] = id(value)
                node_entry['_parent'] = parent
                node_entry['_ancestors'] = set(ancestors)
                node_entry['_scope'] = current_scope
                node_entry['_has_framed_child'] = False
                nodes.append(node_entry)
                next_ancestors = (*ancestors, id(value))
            for key in ('children', 'nodes', 'samples', 'leafNodes'):
                children = value.get(key)
                if isinstance(children, list):
                    before_count = len(nodes)
                    for child in children:
                        visit(child, value, next_ancestors, current_scope)
                    if node_entry is not None and len(nodes) > before_count:
                        node_entry['_has_framed_child'] = True
        elif isinstance(value, list):
            for child in value:
                visit(child, parent, ancestors, scope)

    visit(document)
    min_target = 44.0 if platform == 'mobile' else 28.0
    for node in nodes:
        frame = node['frame']
        x = float(frame.get('minX', frame.get('x', 0)))
        y = float(frame.get('minY', frame.get('y', 0)))
        width = float(frame.get('width', 0))
        height = float(frame.get('height', 0))
        if viewport and (x < 0 or x + width > viewport):
            errors.append('GEOM_VIEWPORT_OVERFLOW')
        text = str(node.get('text', node.get('label', '')))
        if node.get('numeric') and ('…' in text or '...' in text):
            errors.append('GEOM_NUMERIC_TRUNCATION')
        if (node.get('interactive') or node.get('role') in {'button', 'AXButton'}) and (width < min_target or height < min_target):
            errors.append('GEOM_HIT_TARGET_TOO_SMALL')

    for parent in {id(node.get('_parent')): node.get('_parent') for node in nodes if node.get('_parent') is not None}.values():
        siblings = [node for node in nodes if node.get('_parent') is parent]
        for index, first in enumerate(siblings):
            a = first['frame']
            ax, ay = float(a.get('minX', a.get('x', 0))), float(a.get('minY', a.get('y', 0)))
            ar, ab = ax + float(a.get('width', 0)), ay + float(a.get('height', 0))
            for second in siblings[index + 1:]:
                b = second['frame']
                bx, by = float(b.get('minX', b.get('x', 0))), float(b.get('minY', b.get('y', 0)))
                br, bb = bx + float(b.get('width', 0)), by + float(b.get('height', 0))
                if min(ar, br) > max(ax, bx) and min(ab, bb) > max(ay, by):
                    errors.append('GEOM_SIBLING_OVERLAP')

    leaf_roles = {'text', 'label', 'staticText', 'AXStaticText', 'button', 'AXButton', 'heading', 'badge', 'pill', 'rowTitle', 'cadence', 'pulseStageTile'}
    leaf_nodes = [
        node for node in nodes
        if not node.get('_has_framed_child') or str(node.get('role', node.get('kind', ''))) in leaf_roles
    ]
    by_scope = {}
    for node in leaf_nodes:
        by_scope.setdefault(node.get('_scope', 'default'), []).append(node)
    for scope_key, scoped_leaves in by_scope.items():
        for index, first in enumerate(scoped_leaves):
            a = first['frame']
            ax, ay = float(a.get('minX', a.get('x', 0))), float(a.get('minY', a.get('y', 0)))
            ar, ab = ax + float(a.get('width', 0)), ay + float(a.get('height', 0))
            for second in scoped_leaves[index + 1:]:
                if first['_obj_id'] in second['_ancestors'] or second['_obj_id'] in first['_ancestors']:
                    continue
                b = second['frame']
                bx, by = float(b.get('minX', b.get('x', 0))), float(b.get('minY', b.get('y', 0)))
                br, bb = bx + float(b.get('width', 0)), by + float(b.get('height', 0))
                ix = max(0.0, min(ar, br) - max(ax, bx))
                iy = max(0.0, min(ab, bb) - max(ay, by))
                area = ix * iy
                if area > float(collision_threshold_pt2):
                    errors.append('GEOM_TEXT_COLLISION')
                    details.append({
                        'rule': 'GEOM_TEXT_COLLISION',
                        'scope': scope_key,
                        'first': str(first.get('label', first.get('id', first.get('text', 'node_a')))),
                        'second': str(second.get('label', second.get('id', second.get('text', 'node_b')))),
                        'intersection_pt2': round(area, 2),
                    })
    return list(dict.fromkeys(errors)), details


def traceback_errors(text, base_dir=None, platform=None):
    errors, evidence = [], []
    try:
        # JSON file or one fenced JSON record in a report. Prose is not evidence.
        fenced = re.findall(r'```json\s*\n(.*?)\n```', text, re.S)
        data = json.loads(fenced[0] if len(fenced) == 1 else text)
        if not isinstance(data, dict) or data.get('schema') != 'visual-traceback/v1' or not isinstance(data.get('findings'), list):
            raise ValueError('expected visual-traceback/v1 with findings array')
        if _unvalidated_marker(data):
            raise ValueError('UNVALIDATED visual traceback cannot PASS')
        rendered = data.get('rendered_evidence')
        if not isinstance(rendered, list) or not rendered:
            raise ValueError('real rendered screenshot/observation evidence is required')
        screenshots = []
        for item in rendered:
            if not isinstance(item, dict) or item.get('kind') not in {'screenshot', 'observation', 'geometry'} or not _source(item.get('source')) or not item.get('observation'):
                raise ValueError('rendered_evidence requires kind, source, and observation')
            if item.get('kind') == 'screenshot':
                if not re.search(r'\.(?:png|jpe?g)(?:#|$)', item['source'], re.I):
                    raise ValueError('screenshot evidence must cite a PNG or JPEG source')
                screenshots.append(_image_path(item['source'], Path(base_dir) if base_dir else None, platform))
            elif item.get('kind') == 'geometry':
                raw_geom = re.split(r'[#:]', item['source'], maxsplit=1)[0]
                geom_path = Path(raw_geom)
                if not geom_path.is_absolute():
                    geom_path = (Path(base_dir) / geom_path) if base_dir else (Path.cwd() / geom_path)
                geom_errors, _ = audit_geometry(geom_path.resolve(), platform=platform or 'desktop')
                if geom_errors:
                    raise ValueError(f"geometry audit failed ({', '.join(geom_errors)})")
        if not screenshots:
            raise ValueError('at least one real screenshot evidence entry is required')
        if data.get('check_whitespace_ratio') or data.get('max_dead_space_ratio') is not None:
            max_dead = float(data.get('max_dead_space_ratio', 0.40))
            for shot in screenshots:
                if shot.suffix.casefold() == '.png':
                    ws_errors, _ = check_whitespace_ratio(shot, max_dead_space_ratio=max_dead)
                    if ws_errors:
                        raise ValueError('; '.join(ws_errors))
        if data.get('geometry_source'):
            raw_geom = re.split(r'[#:]', str(data['geometry_source']), maxsplit=1)[0]
            geom_path = Path(raw_geom)
            if not geom_path.is_absolute():
                geom_path = (Path(base_dir) / geom_path) if base_dir else (Path.cwd() / geom_path)
            geom_errors, _ = audit_geometry(geom_path.resolve(), platform=platform or 'desktop')
            if geom_errors:
                raise ValueError(f"geometry audit failed ({', '.join(geom_errors)})")
        screenshot_sources = {str(path) for path in screenshots}
        def require_screenshot_source(source):
            if not isinstance(source, str) or not re.search(r'\.(?:png|jpe?g)(?:#|$)', source, re.I):
                raise ValueError('implementation evidence must cite a screenshot artifact')
            path = _image_path(source, Path(base_dir) if base_dir else None, platform)
            if str(path) not in screenshot_sources:
                raise ValueError('implementation screenshot is not listed in rendered_evidence')
        findings = data['findings']
        if not findings:
            clean = data.get('clean')
            if not isinstance(clean, dict) or not _source(clean.get('spec_source')) or not _source(clean.get('implementation_source')) or not clean.get('checks'):
                raise ValueError('clean result requires spec/implementation source references and checks')
            require_screenshot_source(clean['implementation_source'])
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
            require_screenshot_source(impl['source'])
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
