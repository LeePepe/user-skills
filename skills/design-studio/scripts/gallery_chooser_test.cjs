const assert = require('node:assert/strict');
const {readState, entryKey, updateState, selectionSummary, frameMetrics} = require('../assets/gallery-chooser.js');
function main() {
const values = new Map();
const storage = {getItem: k => values.get(k), setItem: (k,v) => values.set(k,v)};
const card = (category, round, id) => ({category, round, id});
function edit(goal, item, field, value) {
  const state = updateState(readState(storage, goal), item, field, value);
  storage.setItem(`design-gallery:${goal}`, JSON.stringify(state));
}
const A = {...card('A',1,'v01'), title:'Alpha'}, B = {...card('B',1,'v01'), title:'Beta'}, newer = {...card('B',2,'v02'), title:'Newer'};
edit('goal',A,'selected',true); edit('goal',B,'selected',true);
edit('goal',B,'starred',true); edit('goal',B,'notes','portal note');
// Round and portal use exactly the same read/write transaction, not stale snapshots.
edit('goal',B,'notes','changed in round');
let state = readState(storage,'goal');
assert.equal(state[entryKey(B)].notes,'changed in round');
assert.equal(state[entryKey(B)].selected,true); assert.equal(state[entryKey(B)].starred,true);
assert.equal(state[entryKey(A)].selected,true); assert.equal(state[entryKey(A)].starred,undefined);
edit('goal',newer,'selected',true);
state = readState(storage,'goal');
assert.equal(state[entryKey(B)].selected,false); assert.equal(state[entryKey(newer)].selected,true);
assert.equal(state[entryKey(A)].selected,true);
const activeSummary = selectionSummary('goal', state, undefined, [A, newer]);
assert.match(activeSummary,/Category: A[\s\S]*A\/v01/);
assert.doesNotMatch(activeSummary,/B\/v01|changed in round/);
assert.match(activeSummary,/B\/v02/);
assert.match(selectionSummary('goal',state),/A\/v01/);
const domInput = (checked, value) => ({checked, value,});
const domNode = {__galleryCard: B, querySelector: selector => selector === '[data-select]' ? domInput(true, '') : selector === '[data-star]' ? domInput(true, '') : domInput(false, '  dom note  ')};
const domRoot = {querySelectorAll: selector => selector === '.variant-card' ? [domNode] : []};
const mergedSummary = selectionSummary('goal', state, domRoot, [B]);
assert.match(mergedSummary,/B\/v01/);
assert.match(mergedSummary,/  - B\/v01 \(Beta\): dom note/);
assert.doesNotMatch(mergedSummary,/NOTES:\n.*NOTES:/s);
assert.equal(selectionSummary('goal', state, undefined, []).split('\n').filter(line => line.includes('Category:')).length, 0, 'explicit empty cards means zero');
assert.deepEqual(readState(null,'goal'),{});
assert.deepEqual(readState({getItem: () => 'null'},'goal'),{});
assert.deepEqual(readState(storage,'another-goal'),{});
values.set('design-gallery:invalid','{broken'); assert.deepEqual(readState(storage,'invalid'),{});
for (const mode of ['mobile','macos','web']) for (const available of [328, 390, 600]) {
  const m = frameMetrics(mode,false,available);
  assert.ok(m.width*m.scale+m.padding*2<=available-24+.01);
  assert.equal(m.width,mode==='mobile'?390:1280);
  assert.equal(m.height,mode==='mobile'?844:800);
  assert.equal(m.chrome,mode==='mobile'?0:28);
}
assert.deepEqual([frameMetrics('web',true,390).width,frameMetrics('web',true,390).height],[390,844]);
assert.deepEqual([frameMetrics('web',false,390).width,frameMetrics('web',false,390).height],[1280,800]);
const source = require('node:fs').readFileSync(require.resolve('../assets/gallery-chooser.js'), 'utf8');
verifyRuntime(source);
assert.doesNotMatch(selectionSummary('goal', state), /B\/v01|changed in round/);
const arbitraryKeys = {alpha: {...A, selected: true, notes: 'alpha note'}, beta: {...B, starred: true, notes: 'beta note'}, newer: {...newer, selected: true, notes: 'new note'}};
assert.match(selectionSummary('goal', arbitraryKeys), /SELECT: A\/v01/);
assert.match(selectionSummary('goal', arbitraryKeys), /Alpha\): alpha note/);
assert.doesNotMatch(selectionSummary('goal', arbitraryKeys), /B\/v01|beta note/);
for (const mutated of [
  source.replace('root, config.cards)', 'root)'),
  source.replace("return lines.join('\\n');", "return lines.join('\\n') + '\\nNOTES:\\nflattened';"),
  source.replace('const scoped = all.filter(s => s.category === category)', 'const scoped = all'),
  source.replace('const explicitCards = activeCards !== undefined', 'const explicitCards = false'),
  source.replace("return lines.join('\\n');", "const exports = lines; exports.push('NOTES:', 'flat'); return exports.join('\\n');"),
  source.replace('const text = selectionSummary(config.goal, currentState(), root, config.cards)', "const text = (false ? selectionSummary(config.goal, currentState(), root, config.cards) : 'legacy flat summary')"),
  source.replace("input.hasAttribute('data-note') ? 'notes'", "input.hasAttribute('data-note') ? 'starred'"),
]) assert.throws(() => verifyRuntime(mutated), 'embedded runtime mutation must fail');
console.log('PASS: shared storage, active inventories, embedded controls/telemetry/summary mutations and viewport fit');
}

function verifyRuntime(source) {
  const vm = require('node:vm');
  const A = {category: 'A', round: 2, id: 'v01', title: 'Alpha'}, B = {category: 'B', round: 2, id: 'v01', title: 'Beta'};
  const reference = {category: 'A', round: 2, id: 'current', title: 'Reference', reference: true};
  const stale = {category: 'B', round: 4, id: 'v04', title: 'Stale'};
  const inventory = [A, B, reference];
  const saved = Object.fromEntries([...inventory, stale].map(c => [entryKey(c), {...c, selected: true, starred: true, notes: `${c.title} stored`}]));
  class Node {
    constructor(attrs = {}) { this.attrs = attrs; this.dataset = {}; this.children = []; this.listeners = {}; this.value = ''; this.checked = false; this.clientWidth = 600; this.style = {setProperty() {}}; }
    hasAttribute(k) { return Object.hasOwn(this.attrs, k); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    addEventListener(k, fn) { this.listeners[k] = fn; }
    querySelector(selector) { return this.children.find(n => selector === 'iframe' ? n.attrs.tag === 'iframe' : n.hasAttribute(selector.slice(1, -1))); }
    querySelectorAll(selector) { return selector === '.variant-card' ? this.children : []; }
    closest() { return this.parent; }
    focus() {} select() {}
  }
  const nodes = inventory.map(c => {
    const n = new Node(); n.dataset = {group: c.category, platform: 'web'};
    n.children = ['data-select', 'data-star', 'data-note', 'iframe'].map(attr => {const input = new Node(attr === 'iframe' ? {tag: attr} : {[attr]: `${c.category}/${c.id}`}); input.parent = n; return input;});
    return n;
  });
  const root = new Node(); root.children = nodes;
  const ids = Object.fromEntries(['status', 'selection-telemetry', 'empty-state', 'copy-summary', 'summary-text'].map(id => [id, new Node()]));
  ids['portal-grid'] = root;
  const config = {goal: 'probe', portal: true, firstGroup: 'A', cards: inventory};
  ids['gallery-config'] = {textContent: JSON.stringify(config)};
  const store = new Map([['design-gallery:probe', JSON.stringify(saved)]]);
  const context = {document: {getElementById: id => ids[id], querySelectorAll: () => [], body: {dataset: {}}}, window: {localStorage: {getItem: k => store.get(k), setItem: (k,v) => store.set(k,v)}, addEventListener() {}}, navigator: {clipboard: {writeText: text => {context.copied = text;}}}, ResizeObserver: class {observe() {}}, module: {exports: {}}, console};
  vm.createContext(context);
  vm.runInContext(source, context, {timeout: 1000});
  context.click = ids['copy-summary'].listeners.click;
  const click = () => {vm.runInContext('click()', context, {timeout: 1000}); return ids['summary-text'].value;};
  const expected = (a, b) => `Goal: probe\nCategory: A\nSELECT: A/v01\nSTAR: A/v01\nNOTES:\n  - A/v01 (Alpha): ${a}\nCategory: B\nSELECT: B/v01\nSTAR: B/v01\nNOTES:\n  - B/v01 (Beta): ${b}`;
  assert.equal(ids['selection-telemetry'].textContent, '已记录 2 条观察 · 2 个收藏 · 2 个选择');
  assert.equal(click(), expected('Alpha stored', 'Beta stored'));
  assert.equal(context.copied, ids['summary-text'].value);
  // An extra mounted stale node must not become inventory when config.cards is explicit.
  const extra = new Node(); extra.__galleryCard = stale; root.children.push(extra);
  assert.equal(click(), expected('Alpha stored', 'Beta stored'));
  nodes[0].querySelector('[data-note]').value = '  Alpha DOM  ';
  assert.equal(click(), expected('Alpha DOM', 'Beta stored'));
  const note = nodes[1].querySelector('[data-note]'); note.value = 'Beta edited';
  context.persistEvent = root.listeners.input; context.event = {target: note};
  vm.runInContext('persistEvent(event)', context, {timeout: 1000});
  const updated = JSON.parse(store.get('design-gallery:probe'));
  assert.equal(updated[entryKey(A)].notes, 'Alpha stored');
  assert.equal(updated[entryKey(B)].notes, 'Beta edited');
  assert.equal(updated[entryKey(stale)].notes, 'Stale stored', 'historical state is retained');
  assert.equal(click(), expected('Alpha stored', 'Beta edited'));
  assert.equal(ids['selection-telemetry'].textContent, '已记录 2 条观察 · 2 个收藏 · 2 个选择');
  const star = nodes[1].querySelector('[data-star]'); star.checked = false;
  context.event = {target: star}; vm.runInContext('persistEvent(event)', context, {timeout: 1000});
  assert.equal(JSON.parse(store.get('design-gallery:probe'))[entryKey(A)].starred, true);
  assert.equal(JSON.parse(store.get('design-gallery:probe'))[entryKey(B)].starred, false);
  assert.equal(ids['selection-telemetry'].textContent, '已记录 2 条观察 · 1 个收藏 · 2 个选择');
  const select = nodes[0].querySelector('[data-select]'); select.checked = false;
  context.event = {target: select}; vm.runInContext('persistEvent(event)', context, {timeout: 1000});
  assert.equal(JSON.parse(store.get('design-gallery:probe'))[entryKey(B)].selected, true);
  assert.equal(ids['selection-telemetry'].textContent, '已记录 2 条观察 · 1 个收藏 · 1 个选择');
  context.probeSummary = context.module.exports.selectionSummary;
  context.saved = saved;
  assert.equal(vm.runInContext("probeSummary('probe', saved, undefined, [])", context, {timeout: 1000}), 'Goal: probe\nSELECT: none\nSTAR: none\nNOTES: none');
  // Test explicit empty inventory through the real copy handler, not just the export.
  vm.runInContext("JSON.parse = (() => { const parse = JSON.parse; return text => { const value = parse(text); if (value.goal === 'probe') value.cards = []; return value; }; })();", context, {timeout: 1000});
  root.children = [];
  vm.runInContext(source, context, {timeout: 1000});
  context.click = ids['copy-summary'].listeners.click;
  assert.equal(click(), 'Goal: probe\nSELECT: none\nSTAR: none\nNOTES: none');
  assert.equal(ids['selection-telemetry'].textContent, '已记录 0 条观察 · 0 个收藏 · 0 个选择');
}

module.exports = {verifyRuntime};
if (require.main === module) {
  if (process.argv.includes('--verify-runtime')) {
    try { verifyRuntime(JSON.parse(require('node:fs').readFileSync(0, 'utf8')).source); console.log('PASS: embedded runtime behavior'); }
    catch (error) { console.error(error.stack); process.exitCode = 1; }
  } else main();
}
