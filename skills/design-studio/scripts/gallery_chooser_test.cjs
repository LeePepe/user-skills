const assert = require('node:assert/strict');
const {readState, entryKey, updateState, selectionSummary, frameMetrics} = require('../assets/gallery-chooser.js');
const values = new Map();
const storage = {getItem: k => values.get(k), setItem: (k,v) => values.set(k,v)};
const card = (category, round, id) => ({category, round, id});
function edit(goal, item, field, value) {
  const state = updateState(readState(storage, goal), item, field, value);
  storage.setItem(`design-gallery:${goal}`, JSON.stringify(state));
}
const A = card('A',1,'v01'), B = card('B',1,'v01'), newer = card('B',2,'v02');
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
assert.match(selectionSummary('goal',state),/Category: A[\s\S]*r1\/v01/);
assert.match(selectionSummary('goal',state),/changed in round/);
assert.match(selectionSummary('goal',state),/r2\/v02/);
const domInput = (checked, value) => ({checked, value,});
const domNode = {__galleryCard: B, querySelector: selector => selector === '[data-select]' ? domInput(true, '') : selector === '[data-star]' ? domInput(true, '') : domInput(false, '  dom note  ')};
const domRoot = {querySelectorAll: selector => selector === '.variant-card' ? [domNode] : []};
const mergedSummary = selectionSummary('goal', state, domRoot);
assert.equal((mergedSummary.match(/r1\/v01/g) || []).length, 4, 'stored entry is merged once across category/select/star/note');
assert.equal((mergedSummary.match(/r1\/v01: dom note/g) || []).length, 1, 'DOM note is not duplicated');
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
console.log('PASS: shared storage updates/isolation/winners/summary and viewport-fit equations');
