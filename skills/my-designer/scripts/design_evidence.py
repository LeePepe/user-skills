"""Fail-closed design evidence: scoped clauses, canonical galleries and attribution.

The deterministic gallery gate trusts a versioned, behavior-tested renderer, not
arbitrary JavaScript tokens. It parses embedded coordinates and then compares each
page and its executable assets with a fresh rendering of the validated manifest.
"""
from html.parser import HTMLParser
import importlib.util
import itertools
import json
from pathlib import Path
import re

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
                                        'application': 'floating backdrop (Blink) or filtered caustic veil (WebKit)'},
                             'content_material': 'solid' if actual == expected else 'unverified',
                             'declared_minimum_target_px': 44 if platform == 'mobile' else None,
                             'canonical_operations': actual == expected})
        except (ValueError, TypeError):
            errors.append(f'{name}: missing/invalid embedded coordinates or executable document')
    try:
        if manifest.get('schema') not in {'design-gallery/v2', 'tenfold-gallery/v1'}:
            raise ValueError('unsupported gallery schema')
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
            if manifest.get('variants') != expected_variants:
                errors.append('manifest variant summaries do not match operative schemes')
            if len({c.NAVIGATION.architecture_signature(s) for s in normalized}) != 4:
                errors.append('app-nav architectures differ only in labels')
            for i, scheme in enumerate(normalized, 1):
                compare(f'app{i:02d}.html', c._app_nav_html(scheme, i, platform, anti, overrides))
            compare('current.html', c._app_nav_html(normalized[0], 0, platform, anti, overrides))
            links = ''.join(f'<li><a href="app{i:02d}.html">{c.escape(s["title"])}</a></li>' for i, s in enumerate(normalized, 1))
            # App rounds publish a link index, not the screen comparison runtime.
            index = f'<!doctype html><meta charset="utf-8"><title>{c.escape(manifest["goal"])}</title><h1>Bespoke app navigation hypotheses</h1><p data-session-anti-patterns="{c.escape(json.dumps(anti, ensure_ascii=False))}" data-style-overrides="{c.escape(json.dumps(overrides, ensure_ascii=False))}">{c.escape(normalized[0]["context"])} · graph-driven screens and transitions</p><ul>{links}</ul>'
            compare('index.html', index, coordinates=False)
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
            for v in variants:
                compare(v['id'] + '.html', c._gallery_html(f'{manifest["goal"]} {v["id"]}', v['id'], platform, v, anti, overrides))
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
