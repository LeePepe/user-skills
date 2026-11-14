import base64
import contextlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
import zlib
from unittest import mock
from pathlib import Path


SCRIPT = Path(__file__).with_name("design_catalog.py")
SPEC = importlib.util.spec_from_file_location("design_catalog", SCRIPT)
DESIGN_CATALOG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DESIGN_CATALOG)
CATALOG = SCRIPT.parents[1] / "references" / "catalog.json"
MARKDOWN = SCRIPT.parents[1] / "references" / "toolchain-20-matrix.md"
TEMP_DIR_ROOT = SCRIPT.parents[3]


def run(*args, **kwargs):
    return subprocess.run([sys.executable, str(SCRIPT), *args], text=True,
                          capture_output=True, **kwargs)


class DesignCatalogTests(unittest.TestCase):
    def test_repository_schema_and_render(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertIsInstance(catalog, dict)
        self.assertEqual(len(catalog["tools"]), 20)
        self.assertEqual(len(catalog["patterns"]), 17)
        self.assertEqual(run("render", "--check").returncode, 0)

    def test_search_prompts(self):
        for query, expected in (
            ("揭角", "MP-001"),
            ("滑动确认", "MP-002"),
            ("拉绳", "MP-003"),
            ("图表联动筛选", "IP-001"),
            ("无结果恢复", "IP-004"),
        ):
            result = json.loads(run("search", "--query", query).stdout)
            self.assertTrue(result["has_duplicate"])
            self.assertEqual(result["matches"][0]["id"], expected)

    def test_duplicate_and_temp_add(self):
        with tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT) as directory:
            catalog = Path(directory) / "catalog.json"
            markdown = Path(directory) / "matrix.md"
            catalog.write_text(CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
            markdown.write_text(MARKDOWN.read_text(encoding="utf-8"), encoding="utf-8")
            args = ["add-pattern", "--id", "MP-NEW", "--name", "卡片揭角", "--category",
                    "gesture-physical", "--platforms", "web,mobile,shared", "--surface-modes", "Operate",
                    "--motion-tier", "M2", "--source-prompt-zh", "卡片揭角", "--canonical-prompt",
                    "Peel a card corner.", "--state-machine", "idle -> drag -> settle", "--web-impl",
                    "Motion", "--mobile-impl", "SwiftUI", "--guardrails", "Reduce Motion", "--catalog",
                    str(catalog), "--markdown", str(markdown)]
            self.assertEqual(run(*args).returncode, 2)
            args[4] = "全新拖拽反馈"
            self.assertEqual(run(*args).returncode, 0)
            ip_args = [
                "add-pattern", "--id", "IP-NEW", "--name", "卡片联动统计", "--category",
                "data-linkage", "--platforms", "web,mobile,shared", "--surface-modes", "Operate,Read",
                "--motion-tier", "M1", "--source-prompt-zh", "点卡片后联动统计",
                "--canonical-prompt", "Click a card to update linked metrics.",
                "--state-machine", "idle -> select -> sync", "--web-impl", "shared state",
                "--mobile-impl", "SwiftUI", "--guardrails", "derived metrics", "--catalog",
                str(catalog), "--markdown", str(markdown),
            ]
            self.assertEqual(run(*ip_args).returncode, 0)
            self.assertIn("MP-NEW", markdown.read_text(encoding="utf-8"))
            self.assertIn("IP-NEW", markdown.read_text(encoding="utf-8"))

    def test_new_categories_and_prefix_validation(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(
            {item["category"] for item in catalog["patterns"] if item["id"].startswith("IP-")},
            {"data-linkage", "filter-sort-recovery", "master-detail-flow", "layout-craft"},
        )
        self.assertTrue(all(item["id"].startswith(("MP-", "IP-")) for item in catalog["patterns"]))

    def test_usage_and_recommendation(self):
        with tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT) as directory:
            catalog = Path(directory) / "catalog.json"
            markdown = Path(directory) / "matrix.md"
            catalog.write_text(CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
            markdown.write_text(MARKDOWN.read_text(encoding="utf-8"), encoding="utf-8")
            common = ["--catalog", str(catalog), "--markdown", str(markdown)]
            self.assertEqual(run("record-use", "--id", "IP-001", "--context", "test",
                                 "--rating", "liked", *common).returncode, 0)
            self.assertEqual(run("record-use", "--id", "MP-002", "--context", "test",
                                 "--rating", "disliked", *common).returncode, 0)
            result = json.loads(run("recommend", "--kind", "pattern", *common).stdout)
            ids = [item["id"] for item in result["recommendations"]]
            self.assertEqual(ids[0], "IP-001")
            self.assertNotIn("MP-002", ids)


class AppNavigationGraphTests(unittest.TestCase):
    def test_screen_declaration_order_and_renaming_do_not_create_architectures(self):
        import copy
        import itertools
        navigation = DESIGN_CATALOG.NAVIGATION
        original = navigation.starter_schemes('Voice notes')[0]
        original['screens'].append({'id': 'unused', 'name': 'Unused', 'actions': []})
        expected = navigation.architecture_signature(navigation.validate_scheme(original, 1, 'Voice notes'))
        clones = []
        for order in itertools.permutations(range(len(original['screens']))):
            scheme = copy.deepcopy(original)
            scheme['screens'] = [scheme['screens'][i] for i in order]
            ids = {p['id']: 'renamed-' + p['id'] for p in scheme['screens']}
            def rename(value):
                if isinstance(value, dict): return {k: rename(v) for k, v in value.items()}
                if isinstance(value, list): return [rename(v) for v in value]
                return ids.get(value, value) if isinstance(value, str) else value
            scheme = rename(scheme)
            self.assertEqual(navigation.architecture_signature(navigation.validate_scheme(scheme, 1, 'Voice notes')), expected)
            if len(clones) < 4: clones.append(scheme)
        with self.assertRaisesRegex(ValueError, 'four operative architectures must differ'):
            DESIGN_CATALOG._load_app_flows(types.SimpleNamespace(app_flows_json=json.dumps({'schemes': clones})))
        for change in ('destination-order', 'action-order', 'operation'):
            scheme = copy.deepcopy(original)
            if change == 'destination-order': scheme['architecture']['destinations'].reverse()
            elif change == 'action-order':
                scheme['screens'][0]['actions'].append({'label': 'Capture directly', 'frequency': 'P0', 'commit': 'captured'})
                before = navigation.architecture_signature(navigation.validate_scheme(scheme, 1, 'Voice notes'))
                scheme['screens'][0]['actions'].reverse()
            else: scheme['screens'][0]['actions'][0]['transition']['type'] = 'switchTab'
            with self.subTest(change=change):
                self.assertNotEqual(navigation.architecture_signature(navigation.validate_scheme(scheme, 1, 'Voice notes')), before if change == 'action-order' else expected)

    def test_custom_architecture_is_required_not_inferred_from_names(self):
        schemes = DESIGN_CATALOG.NAVIGATION.starter_schemes('Voice notes')
        for i, scheme in enumerate(schemes):
            scheme['navigation_model'] = 'bespoke-journey-%d' % i
        args = types.SimpleNamespace(app_flows_json=json.dumps({'schemes': schemes}), app_context='', app_screens='')
        self.assertEqual(len(DESIGN_CATALOG._load_app_flows(args)), 4)
        for invalid in ('missing', 'empty', 'unknown', 'same-chrome', 'renamed-graph'):
            data = json.loads(args.app_flows_json)
            if invalid == 'missing': data['schemes'][0].pop('architecture')
            elif invalid == 'empty': data['schemes'][0]['navigation_model'] = ''
            elif invalid == 'unknown': data['schemes'][0]['architecture']['kind'] = 'unimplemented'
            else:
                for i, scheme in enumerate(data['schemes']):
                    scheme['architecture']['kind'] = 'tabs'
                    if invalid == 'renamed-graph':
                        ids = {p['id']: 'renamed%d-%s' % (i, p['id']) for p in scheme['screens']}
                        def rename(value):
                            if isinstance(value, dict): return {k: rename(v) for k, v in value.items()}
                            if isinstance(value, list): return [rename(v) for v in value]
                            return ids.get(value, value) if isinstance(value, str) else value
                        data['schemes'][i] = rename(scheme)
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                DESIGN_CATALOG._load_app_flows(types.SimpleNamespace(app_flows_json=json.dumps(data)))
        # Four app-tailored graph hypotheses can share a chrome primitive; labels
        # do not force the four starter kinds, but the actual operations must vary.
        tailored = DESIGN_CATALOG.NAVIGATION.starter_schemes('Voice notes')
        for i, scheme in enumerate(tailored):
            scheme['architecture']['kind'] = 'stack'
            action = scheme['screens'][0]['actions'][0]
            if i == 1: action['transition'] = {'type': 'switchTab', 'to': 'review'}
            if i == 2: action['transition'] = {'type': 'toggleSheet', 'to': 'tools'}
            if i == 3: action.pop('transition'); action['commit'] = 'captured'
        self.assertEqual(len(DESIGN_CATALOG._load_app_flows(types.SimpleNamespace(app_flows_json=json.dumps({'schemes': tailored})))), 4)


class AppNavigationBehaviorTests(unittest.TestCase):
    def gate(self, gallery):
        script = SCRIPT.parents[2] / 'my-designer/scripts/verify_design_cases.py'
        return subprocess.run([sys.executable, str(script), 'jev-gate', '--platform', 'mobile', '--stage', 'gallery', '--gallery-dir', str(gallery), '--no-jev'], text=True, capture_output=True)

    def node(self, page, assertions):
        result = subprocess.run(['node', str(SCRIPT.with_name('navigation_dom_harness.js'))],
                                input=json.dumps({'html': page.read_text(), 'assertions': assertions}), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])

    def test_bespoke_runtime_primary_commit_all_surfaces_and_tab_owned_history(self):
        with tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT) as directory:
            schemes = DESIGN_CATALOG.NAVIGATION.starter_schemes('Voice notes')
            for i, scheme in enumerate(schemes):
                scheme['navigation_model'] = 'capture-hypothesis-%d' % i
                scheme['screens'][0]['actions'].extend([
                    {'label': 'Recording options', 'frequency': 'P2', 'commit': 'configured'},
                    {'label': 'Archive note', 'frequency': 'P3', 'commit': 'archived'}])
                scheme['architecture']['overflow'].append({'screen': 'capture', 'action': 2})
                scheme['screens'].append({'id': 'organize', 'name': 'Organize notes', 'role': 'popover',
                    'p0_summary': 'Change the note collection', 'actions': [{'label': 'Organize', 'frequency': 'P3', 'commit': 'organized', 'transition': {'type': 'dismissOverlay'}}]})
                scheme['screens'][2]['transitions'].append({'type': 'togglePopover', 'to': 'organize'})
            result = run('scaffold-gallery', '--goal', 'runtime', '--round', '1', '--platform', 'mobile', '--mode', 'app-nav',
                         '--app-flows-json', json.dumps({'app_context': 'Voice capture', 'schemes': schemes}),
                         '--session-anti-patterns', '["no glass on content"]', '--style-overrides', '{"surface":"dark","density":"compact"}', env=dict(os.environ, HOME=directory))
            self.assertEqual(result.returncode, 0, result.stderr)
            gallery = Path(json.loads(result.stdout)['gallery'])
            accepted = self.gate(gallery)
            self.assertEqual(accepted.returncode, 0, accepted.stdout)
            assertions = r'''const n=window.navigationDemo;
const click=s=>{const e=document.querySelector(s);assert.ok(e,s);e.click();};
const current=()=>n.currentScreen();
assert.equal(document.querySelector('#screen .secondary-actions').open,false);
assert.equal(document.querySelector('#screen [data-frequency="P3"]'),null);
document.querySelector('#screen .secondary-actions').open=true;
click('#screen .secondary-actions [data-action-index="1"]');assert.equal(n.state.commits.configured.count,1);
click('#toggle-tab'); assert.equal(current(),'review');
click('#screen [data-action-index="0"]'); assert.equal(current(),'library');
assert.equal(n.state.commits.saved.count,1);
assert.match(document.querySelector('#action-state').textContent,/Save item committed/);
assert.match(document.querySelector('#screen .commit-list').textContent,/Save item/);
click('#screen [data-transition-index="0"]'); assert.equal(current(),'review');
click('#back'); assert.equal(current(),'capture');
click('#toggle-tab'); assert.equal(current(),'review');
click('#overflow'); click('.tab[data-screen="library"]'); assert.equal(current(),'library');
click('#back'); assert.equal(current(),'library');
click('.tab[data-screen="capture"]'); assert.equal(current(),'review');
click('#back'); assert.equal(current(),'capture');
n.switchTab('library'); click('#screen [data-transition-index="1"]');
assert.equal(current(),'library'); assert.equal(n.state.overlay.screen,'tools');
assert.equal(document.querySelector('#sheet').hidden,false);
click('#large-sheet'); assert.equal(document.querySelector('#sheet').dataset.detent,'large');
click('#medium-sheet'); assert.equal(document.querySelector('#sheet').dataset.detent,'medium');
click('#sheet [data-action-index="0"]'); assert.equal(n.state.commits.exported.count,1);
assert.equal(n.state.overlay,null); assert.equal(current(),'library');
click('#overflow'); click('[data-overflow-action]'); assert.equal(n.state.commits.exported.count,2);
assert.equal(document.querySelector('#overflow-menu').hidden,true);
click('#overflow');click('[data-overflow-action][data-action-screen="capture"]');assert.equal(n.state.commits.archived.count,1);
click('#screen [data-transition-index="2"]'); assert.equal(n.state.overlay.screen,'organize');
click('#popover-content [data-action-index="0"]'); assert.equal(n.state.commits.organized.count,1);
assert.equal(n.state.overlay,null); assert.equal(current(),'library');
'''
            for i in range(1, 5):
                self.node(gallery / f'app{i:02d}.html', assertions)
            original = (gallery / 'app02.html').read_text()
            (gallery / 'app02.html').write_text((gallery / 'app01.html').read_text())
            self.assertNotEqual(self.gate(gallery).returncode, 0)
            (gallery / 'app02.html').write_text(original)
            page = gallery / 'app01.html'
            text = page.read_text()
            runtime = DESIGN_CATALOG.NAVIGATION.ASSETS / 'navigation-demo.js'
            page.write_text(text.replace(runtime.read_text(), '/* .onclick= addEventListener */'))
            self.assertNotEqual(self.gate(gallery).returncode, 0)

    def test_known_and_open_gallery_rounds_gate_runtime_and_correspondence(self):
        with tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT) as directory:
            for known in (False, True):
                options = ['--direction', 'VS-007', '--screen-content', 'Transcript timeline'] if known else []
                result = run('scaffold-gallery', '--goal', 'known' if known else 'open', '--round', '1', '--platform', 'mobile', *options, env=dict(os.environ, HOME=directory))
                self.assertEqual(result.returncode, 0, result.stderr)
                gallery = Path(json.loads(result.stdout)['gallery'])
                accepted = self.gate(gallery)
                self.assertEqual(accepted.returncode, 0, accepted.stdout)
                evidence = json.loads(accepted.stdout)['evidence'][0]
                self.assertEqual(evidence['checked_pairs'], 45)
                self.assertGreaterEqual(evidence['minimum_distance'], 2)
                manifest = json.loads((gallery / 'manifest.json').read_text())
                if known: self.assertEqual({v['archetype'] for v in manifest['variants']}, {'VS-007'})
                for v in manifest['variants']:
                    extra = 'assert.match(document.querySelector(".hero p").textContent,/Transcript timeline/);' if known else ''
                    self.node(gallery / (v['id'] + '.html'), extra + r'''const tabs=[...document.querySelectorAll('[role=tab]')];
if(tabs.length){tabs[1].click();assert.equal(tabs[1].getAttribute('aria-selected'),'true');assert.equal(document.getElementById('panel-detail').hidden,false);}
const next=document.getElementById('next-pocket');if(next){const before=document.querySelector('.stack button').dataset.depth;next.click();assert.notEqual(document.querySelector('.stack button').dataset.depth,before);}
document.getElementById('continue').click();assert.equal(document.getElementById('action-state').textContent,'Direction selected');
document.getElementById('reset-view').click();assert.equal(document.getElementById('action-state').textContent,'View reset');''')
                for i in range(2, 11): (gallery / f'v{i:02d}.html').write_text((gallery / 'v01.html').read_text())
                self.assertNotEqual(self.gate(gallery).returncode, 0)

    def test_object_commit_only_and_legacy_edge_fallback_execute_as_authored(self):
        schemes = DESIGN_CATALOG.NAVIGATION.starter_schemes('Voice notes')
        for scheme in schemes:
            scheme['screens'][0]['transitions'] = [{'type': 'pushScreen', 'to': 'review'}]
        schemes[0]['screens'][0]['actions'] = [{'label': 'Record note', 'frequency': 'P0', 'commit': 'captured-only'}]
        schemes[1]['screens'][0]['actions'] = ['Legacy capture']
        with tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT) as directory:
            result = run('scaffold-gallery', '--goal', 'optional-action', '--round', '1', '--platform', 'mobile', '--mode', 'app-nav',
                         '--app-flows-json', json.dumps({'schemes': schemes}), env=dict(os.environ, HOME=directory))
            self.assertEqual(result.returncode, 0, result.stderr)
            gallery = Path(json.loads(result.stdout)['gallery'])
            self.assertEqual(self.gate(gallery).returncode, 0)
            self.node(gallery / 'app01.html', r'''const n=window.navigationDemo;
document.getElementById('toggle-tab').click();
assert.equal(n.currentScreen(),'capture');assert.equal(n.state.tabs.capture.history.length,0);
assert.equal(n.state.commits['captured-only'].count,1);
document.querySelector('#screen [data-transition-index="0"]').click();
assert.equal(n.currentScreen(),'review');assert.equal(n.state.tabs.capture.history.join(','),'capture');
document.getElementById('back').click();assert.equal(n.currentScreen(),'capture');
assert.equal(n.state.commits['captured-only'].count,1);''')
            for i in (2, 3, 4):
                self.node(gallery / f'app{i:02d}.html', r'''const n=window.navigationDemo;
document.getElementById('toggle-tab').click();assert.equal(n.currentScreen(),'review');
assert.equal(n.state.tabs.capture.history.join(','),'capture');''')

    def test_both_gallery_indexes_require_canonical_round_bindings(self):
        with tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT) as directory:
            for mode in ('screen', 'app-nav'):
                result = run('scaffold-gallery', '--goal', mode, '--round', '2', '--platform', 'mobile', '--mode', mode, env=dict(os.environ, HOME=directory))
                self.assertEqual(result.returncode, 0, result.stderr)
                gallery = Path(json.loads(result.stdout)['gallery'])
                index = gallery / 'index.html'
                canonical = index.read_text()
                self.assertEqual(self.gate(gallery).returncode, 0)
                replacements = ['<!doctype html><p>No variants or selection controls available.</p>',
                                canonical.replace('screen', 'forged-screen') if mode == 'screen' else canonical.replace('app01.html', 'app99.html')]
                if mode == 'screen':
                    replacements.extend([canonical[:canonical.index('<script>')] + '</body></html>',
                                         canonical.replace('Round: ${manifest.round}', 'Round: 99')])
                for html in replacements:
                    with self.subTest(mode=mode, html=html[:80]):
                        index.write_text(html)
                        rejected = self.gate(gallery)
                        self.assertNotEqual(rejected.returncode, 0, rejected.stdout)
                        self.assertTrue(any('index.html' in error for error in json.loads(rejected.stdout)['errors']))
                index.write_text(canonical)
                self.assertEqual(self.gate(gallery).returncode, 0)

    def test_invalid_graph_rejects_before_artifact_creation(self):
        schemes = DESIGN_CATALOG.NAVIGATION.starter_schemes()
        schemes[0]['screens'][0]['transitions'] = [{'type': 'bogus', 'to': 'missing'}]
        with self.assertRaisesRegex(ValueError, 'invalid transition'):
            DESIGN_CATALOG._load_app_flows(types.SimpleNamespace(app_flows_json=json.dumps({'schemes': schemes})))


class IngestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT)
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.catalog = self.directory / "catalog.json"
        self.markdown = self.directory / "matrix.md"
        self.catalog.write_bytes(CATALOG.read_bytes())
        self.markdown.write_bytes(MARKDOWN.read_bytes())

    def ingest(self, *args):
        return run("ingest", *args, "--catalog", str(self.catalog),
                   "--markdown", str(self.markdown))

    def snapshot(self):
        return self.catalog.read_bytes(), self.markdown.read_bytes()

    def test_classification_and_dry_run(self):
        before = self.snapshot()
        for text, category, prefix in (
            ("Link chart segments with a summary", "data-linkage", "IP"),
            ("图表联动统计", "data-linkage", "IP"),
            ("Search and sort with scoped recovery", "filter-sort-recovery", "IP"),
            ("搜索无结果时保留排序", "filter-sort-recovery", "IP"),
            ("Select a record for the detail pane", "master-detail-flow", "IP"),
            ("主从详情保持位置", "master-detail-flow", "IP"),
            ("Responsive grid spacing and alignment", "layout-craft", "IP"),
            ("容器布局间距对齐", "layout-craft", "IP"),
            ("Swipe a spring-loaded card", "gesture-physical", "MP"),
            ("揭角手势带弹簧回位", "gesture-physical", "MP"),
            ("Confirm destructive changes with undo", "confirmation-safety", "MP"),
            ("删除前确认并可撤销", "confirmation-safety", "MP"),
            ("Scrub the navigation toolbar", "spatial-navigation", "MP"),
            ("工具栏滑选导航", "spatial-navigation", "MP"),
            ("Like reaction badge feedback", "micro-feedback-social", "MP"),
            ("点赞表情反馈", "micro-feedback-social", "MP"),
            ("Drag and drop files on a drop target", "drag-drop-choreography", "MP"),
            ("拖入文件夹时显示拖放落点", "drag-drop-choreography", "MP"),
            ("An unrecognized visual idea", "layout-craft", "IP"),
        ):
            with self.subTest(text=text):
                result = self.ingest("--text", text, "--dry-run", "--force")
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(data["classification"]["category"], category)
                self.assertEqual(data["classification"]["prefix"], prefix)
                self.assertEqual(data["pattern"]["id"], "IP-009" if prefix == "IP" else "MP-010")
                self.assertEqual(data["pattern"]["motion_tier"], "M1" if prefix == "IP" else "M2")
                self.assertTrue(data["dry_run"])
                self.assertFalse(data["created"])
                self.assertEqual(data["actions"], [])
        self.assertEqual(self.snapshot(), before)

    def test_numbering_uses_maximum_per_prefix_and_persists_render(self):
        catalog = json.loads(self.catalog.read_text(encoding="utf-8"))
        extra = dict(catalog["patterns"][-1], id="IP-042")
        catalog["patterns"].insert(0, extra)
        self.catalog.write_text(json.dumps(catalog), encoding="utf-8")
        for text, expected in (("Responsive editorial grid", "IP-043"),
                               ("Swipe an elastic paper edge", "MP-010"),
                               ("Align a compact calendar layout", "IP-044")):
            with self.subTest(expected=expected):
                result = self.ingest("--text", text)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertTrue(data["created"])
                self.assertFalse(data["dry_run"])
                self.assertEqual(data["duplicate_decision"], "unique")
                self.assertEqual(data["actions"], ["write_catalog", "render_markdown"])
                stored = json.loads(self.catalog.read_text(encoding="utf-8"))["patterns"][-1]
                self.assertEqual(stored, data["pattern"])
                self.assertEqual(stored["id"], expected)
                for field in ("source_prompt_zh", "canonical_prompt", "state_machine",
                              "web_implementation", "mobile_implementation", "token_guardrails"):
                    self.assertTrue(stored[field], field)
                self.assertIn(expected, self.markdown.read_text(encoding="utf-8"))
                check = run("render", "--check", "--catalog", str(self.catalog),
                            "--markdown", str(self.markdown))
                self.assertEqual(check.returncode, 0, check.stderr)

    def test_duplicate_lists_all_matches_and_force(self):
        catalog = json.loads(self.catalog.read_text(encoding="utf-8"))
        existing = next(item for item in catalog["patterns"] if item["id"] == "IP-001")
        catalog["patterns"].append(dict(existing, id="IP-099"))
        self.catalog.write_text(json.dumps(catalog), encoding="utf-8")
        before = self.snapshot()
        for flags in ([], ["--dry-run"]):
            blocked = self.ingest("--text", existing["source_prompt_zh"], *flags)
            self.assertEqual(blocked.returncode, 2, blocked.stderr)
            data = json.loads(blocked.stdout)
            self.assertEqual(data["duplicate_decision"], "blocked")
            self.assertEqual({item["id"] for item in data["duplicate_matches"]}, {"IP-001", "IP-099"})
            self.assertFalse(data["created"])
            self.assertEqual(self.snapshot(), before)
        preview = self.ingest("--text", existing["source_prompt_zh"], "--force", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(json.loads(preview.stdout)["duplicate_decision"], "forced")
        self.assertEqual(self.snapshot(), before)
        forced = self.ingest("--text", existing["source_prompt_zh"], "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        data = json.loads(forced.stdout)
        self.assertEqual(data["duplicate_decision"], "forced")
        self.assertEqual(data["pattern"]["id"], "IP-100")
        self.assertTrue(data["created"])

    def test_duplicate_similarity_threshold(self):
        catalog = json.loads(self.catalog.read_text(encoding="utf-8"))
        base = catalog["patterns"][0]
        catalog["patterns"] = [dict(base, name="different name", aliases=[],
                                    source_prompt_zh="alpha beta gamma delta epsilon zeta",
                                    canonical_prompt="different canonical prompt")]
        self.catalog.write_text(json.dumps(catalog), encoding="utf-8")
        before = self.snapshot()
        for text, expected in (("alpha beta gamma delta epsilon theta", 2),
                               ("alpha eta beta theta gamma delta", 0)):
            result = self.ingest("--text", text, "--dry-run")
            self.assertEqual(result.returncode, expected, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_mixed_and_all_duplicate_batch_codes(self):
        existing = json.loads(self.catalog.read_text(encoding="utf-8"))["patterns"][0]
        mixed = self.ingest("--text", f"- {existing['source_prompt_zh']}\n- brand-new mixed batch", "--no-jev")
        self.assertEqual(mixed.returncode, 0, mixed.stderr)
        mixed_data = json.loads(mixed.stdout)
        self.assertEqual(len(mixed_data["created_items"]), 1)
        self.assertEqual(len(mixed_data["duplicate_items"]), 1)
        self.assertEqual(mixed_data["duplicate_items"][0]["id"], existing["id"])
        all_duplicate = self.ingest("--text", f"- {existing['source_prompt_zh']}\n- {existing['source_prompt_zh']}", "--no-jev")
        self.assertEqual(all_duplicate.returncode, 2, all_duplicate.stderr)
        self.assertEqual(len(json.loads(all_duplicate.stdout)["created_items"]), 0)

    def test_image_cli_routing_and_input_validation(self):
        args = DESIGN_CATALOG.parser().parse_args([
            "ingest", "--image", "mock.png", "--no-jev", "--catalog", str(self.catalog),
            "--markdown", str(self.markdown), "--dry-run"])
        original = DESIGN_CATALOG.ocr_image
        try:
            DESIGN_CATALOG.ocr_image = lambda path: "OCR routed prompt"
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(DESIGN_CATALOG.command(args), 0)
            self.assertIn("OCR routed prompt", output.getvalue())
        finally:
            DESIGN_CATALOG.ocr_image = original
        self.assertEqual(self.ingest("--text", "text", "--image", "image.png").returncode, 2)
        self.assertEqual(run("ingest").returncode, 2)

    def test_split_and_no_split_batch_compact(self):
        prompt = "Context before\n• first linked chart\n2、second filter\n③ third detail\n(4) fourth layout\n**Fifth**: fifth gesture\nContext after"
        result = self.ingest("--text", prompt, "--no-jev", "--compact")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 5)
        self.assertEqual(len(json.loads(self.catalog.read_text(encoding="utf-8"))["patterns"]), 22)
        single = self.ingest("--text", "- first\n- second", "--no-split", "--no-jev", "--dry-run")
        self.assertEqual(single.returncode, 0, single.stderr)
        self.assertEqual(json.loads(single.stdout)["pattern"]["source_prompt_zh"], "- first\n- second")

    def test_mocked_jev_filter_category_and_motion(self):
        args = types.SimpleNamespace(no_jev=False, jev_url="http://unused", category=None, motion_tier=None)
        original = DESIGN_CATALOG.jev_result
        try:
            DESIGN_CATALOG.jev_result = lambda text, args: {
                "noul": 0.9, "is_design_pattern": True, "category": "data-linkage",
                "category_confidence": 0.9, "motion_tier": "M3", "motion_confidence": 0.9,
            }
            result = DESIGN_CATALOG.classify_segment("chart linkage", args)
            self.assertEqual(result[:2], ("data-linkage", "M3"))
            DESIGN_CATALOG.jev_result = lambda text, args: {"noul": 0.1, "is_design_pattern": True}
            self.assertIsNone(DESIGN_CATALOG.classify_segment("prose", args))
        finally:
            DESIGN_CATALOG.jev_result = original

    def test_image_ocr_injection_and_empty_error(self):
        completed = type("Completed", (), {"stdout": "中文 heading\nEnglish prompt\n"})()
        self.assertEqual(DESIGN_CATALOG.ocr_image("image.png", lambda *a, **k: completed),
                         "中文 heading\nEnglish prompt")
        empty = type("Completed", (), {"stdout": "  "})()
        with self.assertRaises(ValueError):
            DESIGN_CATALOG.ocr_image("image.png", lambda *a, **k: empty)

    def test_file_input_and_overrides(self):
        prompt = self.directory / "prompt.txt"
        text = "  面板布局\n  保留完整的需求与层级。  \n"
        prompt.write_text(text, encoding="utf-8")
        result = self.ingest("--file", str(prompt), "--id", "IP-123", "--category", "layout-craft",
                             "--platforms", " web, mobile ", "--surface-modes", "Read,Experience",
                             "--motion-tier", "M3")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["classification"]["source"], "override")
        item = data["pattern"]
        self.assertEqual(item["id"], "IP-123")
        self.assertEqual(item["source_prompt_zh"], text.strip())
        self.assertEqual(item["canonical_prompt"], "面板布局 保留完整的需求与层级。")
        self.assertEqual(item["platforms"], ["web", "mobile"])
        self.assertEqual(item["surface_modes"], ["Read", "Experience"])
        self.assertEqual(item["motion_tier"], "M3")
        self.assertEqual(data["paths"], {"catalog": str(self.catalog), "markdown": str(self.markdown)})
        self.assertTrue(data["created"])

    def test_invalid_input_and_overrides_never_write(self):
        before = self.snapshot()
        for args in (
            [], ["--text", "one", "--file", "other"],
            ["--text", "grid", "--category", "code-to-video"],
            ["--text", "grid", "--platforms", "tablet"],
            ["--text", "grid", "--platforms", ""],
            ["--text", "grid", "--surface-modes", "Unknown"],
            ["--text", "grid", "--motion-tier", "M5"],
            ["--text", "grid", "--id", "T-001"],
            ["--text", "grid", "--id", "IP-not-numeric"],
        ):
            with self.subTest(args=args):
                result = self.ingest(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("error:", result.stderr)
        for args in (
            ["--text", " \n "],
            ["--file", str(self.directory / "missing.txt")],
            ["--text", "grid", "--id", "MP-123"],
            ["--text", "grid", "--id", "IP-001", "--force"],
        ):
            with self.subTest(args=args):
                result = self.ingest(*args)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("error:", result.stderr)
        empty = self.directory / "empty.txt"
        empty.write_text("", encoding="utf-8")
        self.assertEqual(self.ingest("--file", str(empty)).returncode, 1)
        self.assertEqual(self.snapshot(), before)


class TaxonomyV2AndVaultTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT)
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.taxonomy = self.directory / "taxonomy-v2.json"
        self.vault_dir = self.directory / "vault"
        self.vault_dir.mkdir(parents=True)
        src_refs = SCRIPT.parents[1] / "references"
        self.taxonomy.write_bytes((src_refs / "taxonomy-v2.json").read_bytes())
        for vf in ("desktop-macos.json", "mobile-ios.json", "web.json"):
            (self.vault_dir / vf).write_bytes((src_refs / "vault" / vf).read_bytes())

    def test_jev_fast_path_and_llm_fallback_and_learn(self):
        # 1. Fast path (single dimension, high confidence desktop + chart-dataviz)
        fast = run(
            "classify",
            "--text", "macOS 菜单栏 NSPopover 里的折线图 Swift Charts，hover 时高亮数据",
            "--no-jev",
            "--taxonomy", str(self.taxonomy),
            "--vault-dir", str(self.vault_dir),
        )
        self.assertEqual(fast.returncode, 0, fast.stderr)
        fast_data = json.loads(fast.stdout)["results"][0]
        self.assertEqual(fast_data["route_stage"], "stage1_jev_fast_path")
        self.assertEqual(fast_data["platforms"], ["desktop"])
        self.assertEqual(fast_data["primary_dimension"], "chart-dataviz")

        # 2. Compound multi-dimension prompt triggers Stage 2 LLM fallback & decomposes into atomic cards
        compound = run(
            "classify",
            "--text", "iOS TabView 液态玻璃 Liquid Glass 导航栏配深色模式 P3 主题配色方案与折线图统计",
            "--no-jev",
            "--learn",
            "--taxonomy", str(self.taxonomy),
            "--vault-dir", str(self.vault_dir),
        )
        self.assertEqual(compound.returncode, 0, compound.stderr)
        comp_payload = json.loads(compound.stdout)
        comp_res = comp_payload["results"][0]
        self.assertEqual(comp_res["route_stage"], "stage2_llm_fallback")
        self.assertTrue(comp_res["needs_llm_fallback"])
        self.assertGreaterEqual(len(comp_res["cards"]), 2)
        self.assertTrue(comp_payload["learned_keywords_updated"])

        # 3. Noise rejection via Jev noul < 0.40
        noise = run(
            "classify",
            "--text", "unrelated backend sql migration log",
            "--mock-jev-json", json.dumps({"is_design_inspiration": {"noul": 0.12}}),
            "--taxonomy", str(self.taxonomy),
            "--vault-dir", str(self.vault_dir),
        )
        self.assertEqual(noise.returncode, 0, noise.stderr)
        self.assertEqual(json.loads(noise.stdout)["results"][0]["status"], "noise_skipped")

    def test_vault_ingest_and_progressive_disclosure_search(self):
        ing = run(
            "vault-ingest",
            "--text", "macOS NavigationSplitView 侧边栏使用 Liquid Glass 悬浮材质并支持快捷键收起",
            "--no-jev",
            "--taxonomy", str(self.taxonomy),
            "--vault-dir", str(self.vault_dir),
        )
        self.assertEqual(ing.returncode, 0, ing.stderr)
        ing_data = json.loads(ing.stdout)
        self.assertGreaterEqual(ing_data["created_count"], 1)

        search = run(
            "vault-search",
            "--platform", "desktop",
            "--dimension", "glass-material",
            "--query", "Liquid Glass 侧边栏",
            "--taxonomy", str(self.taxonomy),
            "--vault-dir", str(self.vault_dir),
        )
        self.assertEqual(search.returncode, 0, search.stderr)
        s_data = json.loads(search.stdout)
        self.assertIn("liquid-glass.md", s_data["progressive_disclosure"]["hig_files_to_load"])
        self.assertGreaterEqual(len(s_data["matches"]), 1)

    def test_cross_domain_archetype_remixes_and_screenshot_palette_ingest(self):
        import struct
        import zlib

        for vf in ("mobile-ios.json", "desktop-macos.json"):
            vault_doc = json.loads((self.vault_dir / vf).read_text(encoding="utf-8"))
            ids = {item["id"] for item in vault_doc["inspirations"]}
            for expected_id in ("VS-005", "VS-006", "VS-007", "VS-008", "CL-005", "CL-006", "CL-007", "CL-008", "GM-003", "LN-002", "MI-008"):
                self.assertIn(expected_id, ids, f"Missing {expected_id} in {vf}")

        # Build a deterministic 4x4 PNG in #4F97FF (Things 3 Sky Blue)
        raw = b"".join(b"\0" + bytes((0x4F, 0x97, 0xFF)) * 4 for _ in range(4))
        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        shot = self.directory / "shot.png"
        shot.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b"")
        )
        palette = DESIGN_CATALOG._parse_png_palette(shot, top=3)
        self.assertEqual(len(palette), 1)
        self.assertEqual(palette[0]["hex"], "#4C94FC")

        ing = run(
            "vault-ingest",
            "--text", "Things 3 纸感留白圆圈打勾跨界到平铺训练计划表",
            "--measure-palette", str(shot),
            "--archetype-ref", "VS-005",
            "--no-jev",
            "--taxonomy", str(self.taxonomy),
            "--vault-dir", str(self.vault_dir),
        )
        self.assertEqual(ing.returncode, 0, ing.stderr)
        payload = json.loads(ing.stdout)
        self.assertEqual(payload["archetype_ref"], "VS-005")
        self.assertTrue(payload["measured_palette"])
        self.assertEqual(payload["created_cards"][0]["archetype_ref"], "VS-005")
        self.assertTrue(payload["created_cards"][0]["measured_palette"])

    def test_vault_ingest_multimodal_metadata_and_url_only(self):
        image = self.directory / "shot.png"
        image.write_bytes(b"not-an-image")
        result = run("vault-ingest", "--text", "Liquid Glass mobile card", "--image", str(image),
                     "--measure-palette", str(image), "--url", "https://example.com/a,https://example.com/a",
                     "--source-url", "https://example.com/b", "--html-recipe-ref", "recipe-v1",
                     "--archetype-ref", "VS-005", "--no-jev", "--taxonomy", str(self.taxonomy),
                     "--vault-dir", str(self.vault_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        card = payload["created_cards"][0]
        self.assertEqual(card["source_urls"], ["https://example.com/a", "https://example.com/b"])
        self.assertEqual(card["html_recipe_ref"], "recipe-v1")
        self.assertEqual(card["image_assets"], [str(image)])
        url_only = run("vault-ingest", "--url", "https://example.com/only", "--no-jev",
                       "--taxonomy", str(self.taxonomy), "--vault-dir", str(self.vault_dir), "--dry-run")
        self.assertEqual(url_only.returncode, 0, url_only.stderr)
        self.assertEqual(json.loads(url_only.stdout)["created_count"], 1)

    def test_curated_text_skips_ocr_and_missing_image_writes_nothing(self):
        image = self.directory / "existing.png"
        image.write_bytes(b"valid-path-placeholder")
        args = DESIGN_CATALOG.parser().parse_args(["vault-ingest", "--text", "curated visual note", "--image", str(image), "--no-jev", "--taxonomy", str(self.taxonomy), "--vault-dir", str(self.vault_dir)])
        with mock.patch.object(DESIGN_CATALOG, "ocr_image", side_effect=AssertionError("OCR must not run")):
            self.assertEqual(DESIGN_CATALOG.command(args), 0)
        stored = json.loads((self.vault_dir / "mobile-ios.json").read_text())
        self.assertTrue(any(card.get("image_assets") == [str(image)] for card in stored["inspirations"]))
        before = {path.name: path.read_bytes() for path in self.vault_dir.glob("*.json")}
        missing = run("vault-ingest", "--text", "note", "--image", str(self.directory / "missing.png"), "--no-jev", "--taxonomy", str(self.taxonomy), "--vault-dir", str(self.vault_dir))
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.vault_dir.glob("*.json")})
        self.assertNotEqual(missing.returncode, 0)

    def test_scaffold_gallery_writes_safe_interactive_bundle(self):
        with tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT) as directory:
            env = os.environ.copy()
            env["HOME"] = directory
            result = run("scaffold-gallery", "--goal", "goal-a", "--round", "1", "--platform", "mobile",
                         "--session-anti-patterns", "[\"no top capsule\"]", "--style-overrides", "{\"density\":\"airy\"}", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            gallery = Path(directory) / "Development/handoff/goal-a/r1"
            self.assertTrue((gallery / "index.html").is_file())
            self.assertTrue((gallery / "screen.css").is_file())
            self.assertIn("feDisplacementMap", (gallery / "screen.css").read_text())
            self.assertIn("feDisplacementMap", (gallery / "v01.html").read_text())
            manifest = json.loads((gallery / "manifest.json").read_text())
            self.assertEqual(len(manifest["variants"]), 10)
            self.assertEqual(manifest["session_anti_patterns"], ["no top capsule"])
            self.assertEqual(manifest["style_overrides"], {"density": "airy"})
            duplicate = run("scaffold-gallery", "--goal", "goal-a", "--round", "1", "--platform", "mobile", env=env)
            self.assertNotEqual(duplicate.returncode, 0)

    def test_web_optical_recipe_ingest_cannot_contaminate_native_vaults(self):
        recipe = "~/Development/handoff/apple-liquid-glass-html-recipe/"
        taxonomy_before = self.taxonomy.read_bytes()
        inputs = [("iOS SwiftUI liquid glass control", ["--html-recipe-ref", recipe]),
                  ("iOS SwiftUI liquid glass control", ["--source-url", "https://example.org/apple-liquid-glass-html-recipe/"]),
                  ("iOS SwiftUI feDisplacementMap liquid glass control", [])]
        for text, extra in inputs:
            with self.subTest(extra=extra, text=text):
                result = run("vault-ingest", "--text", text, *extra, "--no-jev",
                             "--taxonomy", str(self.taxonomy), "--vault-dir", str(self.vault_dir))
                self.assertEqual(result.returncode, 0, result.stderr)
                cards = json.loads(result.stdout)["created_cards"]
                self.assertTrue(cards)
                for card in cards:
                    self.assertEqual(card["platforms"], ["web"])
                    self.assertIn("web_implementation", card)
                    self.assertNotIn("apple_native_primitives", card)
                for name in ("mobile-ios.json", "desktop-macos.json"):
                    native = (self.vault_dir / name).read_text()
                    self.assertNotIn("apple-liquid-glass-html-recipe", native)
                    self.assertNotIn("feDisplacementMap", native)
                self.assertEqual(self.taxonomy.read_bytes(), taxonomy_before)

    def test_gallery_optical_map_has_inward_normals_and_clear_center(self):
        svg = ET.fromstring(DESIGN_CATALOG._gallery_svg(192, 96))
        ns = {"s": "http://www.w3.org/2000/svg"}
        image = svg.find(".//s:feImage", ns)
        png = base64.b64decode(image.attrib["href"].split(",", 1)[1])
        width, height = struct.unpack("!II", png[16:24])
        compressed = bytearray()
        offset = 8
        while offset < len(png):
            size = struct.unpack("!I", png[offset:offset + 4])[0]
            if png[offset + 4:offset + 8] == b"IDAT":
                compressed.extend(png[offset + 8:offset + 8 + size])
            offset += size + 12
        raw = zlib.decompress(compressed)
        stride = width * 4 + 1

        def pixel(x, y):
            self.assertEqual(raw[y * stride], 0)
            start = y * stride + 1 + x * 4
            return tuple(raw[start:start + 4])

        self.assertEqual(pixel(width // 2, height // 2), (128, 128, 128, 255))
        left, right = pixel(2, height // 2), pixel(width - 3, height // 2)
        top, bottom = pixel(width // 2, 2), pixel(width // 2, height - 3)
        self.assertGreater(left[0], 128)
        self.assertLess(right[0], 128)
        self.assertEqual((left[1], right[1]), (128, 128))
        self.assertGreater(top[1], 128)
        self.assertLess(bottom[1], 128)
        self.assertEqual((top[0], bottom[0]), (128, 128))
        self.assertTrue(all(value > 128 for value in pixel(10, 10)[:2]))
        self.assertGreater(len({pixel(x, height // 2)[0] for x in range(1, 12)}), 5)
        light = [pixel(x, y)[2] for y in range(height) for x in range(width)]
        self.assertLess(min(light), 128)
        self.assertGreater(max(light), 128)
        scales = [float(node.attrib["scale"]) for node in svg.findall(".//s:feDisplacementMap", ns)]
        self.assertEqual(len(scales), 3)
        self.assertAlmostEqual(scales[0] + scales[2], 2 * scales[1])

    def test_gallery_rejects_unsafe_session_data_and_parent_symlink_before_writing(self):
        with tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT) as directory:
            env = dict(os.environ, HOME=directory)
            base = ["scaffold-gallery", "--goal", "safe-goal", "--round", "1", "--platform", "web"]
            for option, value in (("--style-overrides", "{bad"),
                                  ("--style-overrides", '{"density":{}}'),
                                  ("--session-anti-patterns", "[1]")):
                rejected = run(*base, option, value, env=env)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertFalse((Path(directory) / "Development/handoff/safe-goal").exists())
            root = Path(directory) / "Development/handoff"
            root.mkdir(parents=True)
            outside = Path(directory) / "product"
            outside.mkdir()
            (root / "safe-goal").symlink_to(outside, target_is_directory=True)
            rejected = run(*base, env=env)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(list(outside.iterdir()), [])

    def test_gallery_keeps_session_text_inert_in_html_and_inline_script(self):
        payload = '</script><img src=x onerror="window.injected=true">'
        manifest = {"goal": "safe-goal", "round": 1, "platform": "mobile",
                    "frame": {"width": 390, "height": 844}, "variants": [],
                    "session_anti_patterns": [payload], "style_overrides": {"note": payload}}
        index = DESIGN_CATALOG._gallery_index(manifest)
        self.assertNotIn(payload, index)
        self.assertEqual(index.count("</script>"), 1)
        screen = DESIGN_CATALOG._gallery_html("title", "v01", "mobile",
                    {"p0": "next-action", "axis_y": "accordion", "archetype": "VS-005"},
                    [payload], {})
        self.assertNotIn(payload, screen)
        self.assertIn("&lt;/script&gt;", screen)


if __name__ == "__main__":
    unittest.main()
