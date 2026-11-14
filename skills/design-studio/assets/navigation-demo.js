'use strict';
const CONFIG = JSON.parse(document.getElementById('page-config').textContent);
const S = CONFIG.scheme;
const A = S.architecture;
const P = Object.fromEntries(S.screens.map(p => [p.id, p]));
const state = {
  activeTab: A.root,
  tabs: Object.fromEntries(A.destinations.map(id => [id, {current: id, history: []}])),
  overlay: null, detent: 'medium', commits: {}, feedback: ''
};
const screen = document.getElementById('screen');
const sheet = document.getElementById('sheet');
const menu = document.getElementById('overflow-menu');
function tabState() { return state.tabs[state.activeTab]; }
function currentScreen() { return tabState().current; }
function node(tag, text, attributes = {}) {
  const e = document.createElement(tag);
  if (text !== null) e.textContent = text;
  Object.entries(attributes).forEach(([k, v]) => e.setAttribute(k, v));
  return e;
}
function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); }
function feedback(message) {
  state.feedback = message;
  document.getElementById('action-state').textContent = message;
}
function renderPage(host, id) {
  const p = P[id];
  clear(host);
  host.dataset.screen = id;
  host.append(node('h2', p.name), node('p', p.p0_summary));
  const actions = node('div', null, {class: 'actions'});
  const overlay = ['sheet', 'popover'].includes(p.role);
  const actionButton = (a, i) => node('button', a.label, {
    'data-action-screen': id, 'data-action-index': i, 'data-frequency': a.frequency
  });
  p.actions.forEach((a, i) => { if (overlay || ['P0', 'P1'].includes(a.frequency)) actions.append(actionButton(a, i)); });
  host.append(actions);
  if (!overlay && p.actions.some(a => a.frequency === 'P2')) {
    const disclosure = node('details', null, {class: 'secondary-actions'});
    disclosure.append(node('summary', 'More in this view'));
    p.actions.forEach((a, i) => { if (a.frequency === 'P2') disclosure.append(actionButton(a, i)); });
    host.append(disclosure);
  }
  // P3 content actions are validated into architecture.overflow, never tiled inline.
  const edges = node('div', null, {class: 'edges'});
  p.transitions.forEach((t, i) => edges.append(node('button', `${t.type} · ${P[t.to]?.name || 'Close'}`, {
    'data-transition-screen': id, 'data-transition-index': i
  })));
  host.append(edges);
  const saved = node('ul', null, {class: 'commit-list', 'aria-label': 'Committed domain state'});
  Object.entries(state.commits).forEach(([key, value]) => saved.append(node('li', `${value.label} · ${value.count}`, {'data-commit': key})));
  host.append(saved);
}
function render() {
  renderPage(screen, currentScreen());
  document.getElementById('large-title').textContent = P[currentScreen()].name;
  const compact = document.getElementById('compact-title');
  if (compact) compact.textContent = P[currentScreen()].name;
  document.querySelectorAll('.tab').forEach(b => {
    const selected = b.dataset.screen === state.activeTab;
    b.setAttribute('aria-current', selected ? 'page' : 'false');
    b.setAttribute('aria-selected', selected);
  });
  const back = document.getElementById('back');
  back.disabled = !state.overlay && !tabState().history.length;
  if (state.overlay) {
    const host = state.overlay.kind === 'sheet' ? document.getElementById('sheet-content') : document.getElementById('popover-content');
    renderPage(host, state.overlay.screen);
  }
  bindActions();
}
function pushScreen(id) {
  if (!P[id] || ['sheet', 'popover'].includes(P[id].role)) throw new Error('Invalid push target');
  closeOverlays();
  if (id !== currentScreen()) tabState().history.push(currentScreen());
  tabState().current = id;
  render();
}
function popScreen() {
  if (state.overlay) { closeOverlays(); render(); return; }
  if (tabState().history.length) tabState().current = tabState().history.pop();
  else feedback('At the journey root');
  render();
}
function switchTab(id) {
  if (!P[id] || ['sheet', 'popover'].includes(P[id].role)) throw new Error('Invalid tab target');
  closeOverlays();
  // A graph edge may enter a content root not present in visible chrome.
  if (!state.tabs[id]) state.tabs[id] = {current: id, history: []};
  state.activeTab = id;
  render();
}
function closeOverlays() {
  state.overlay = null;
  sheet.hidden = true;
  menu.hidden = true;
  screen.removeAttribute('inert');
  document.getElementById('overflow').setAttribute('aria-expanded', 'false');
}
function setDetent(value) {
  if (!['medium', 'large'].includes(value)) throw new Error('Unknown detent');
  state.detent = value;
  sheet.dataset.detent = value;
}
function toggleSheet(id = A.sheet, value = state.detent) {
  if (!P[id] || P[id].role !== 'sheet') throw new Error('Invalid sheet target');
  if (state.overlay?.kind === 'sheet' && state.overlay.screen === id && state.detent === value) {
    closeOverlays(); render(); return;
  }
  closeOverlays();
  state.overlay = {kind: 'sheet', screen: id};
  setDetent(value);
  sheet.dataset.screen = id;
  sheet.hidden = false;
  screen.setAttribute('inert', '');
  render();
}
function togglePopover(id) {
  if (!P[id] || P[id].role !== 'popover') throw new Error('Invalid popover target');
  closeOverlays();
  state.overlay = {kind: 'popover', screen: id};
  menu.hidden = false;
  render();
}
function dispatch(t) {
  if (!t) return;
  const operations = {pushScreen, popScreen, switchTab, toggleSheet, togglePopover, dismissOverlay: () => {closeOverlays(); render();}};
  if (!operations[t.type]) throw new Error('Invalid transition');
  operations[t.type](t.to);
}
function performAction(id, index) {
  const action = P[id]?.actions[index];
  if (!action) throw new Error('Invalid action');
  if (action.commit) {
    const old = state.commits[action.commit];
    state.commits[action.commit] = {label: action.label, count: (old?.count || 0) + 1};
  }
  feedback(action.label + (action.commit ? ' committed' : ' started'));
  dispatch(action.transition);
  // Feedback and domain state survive push/pop/tab and all overlay actions.
  render();
}
function bindActions() {
  document.querySelectorAll('[data-action-screen]').forEach(b => {
    b.onclick = () => {
      if (b.hasAttribute('data-overflow-action')) closeOverlays();
      performAction(b.dataset.actionScreen, Number(b.dataset.actionIndex));
    };
  });
  document.querySelectorAll('[data-transition-screen]').forEach(b => {
    b.onclick = () => dispatch(P[b.dataset.transitionScreen].transitions[Number(b.dataset.transitionIndex)]);
  });
}
document.querySelectorAll('.tab').forEach(b => b.onclick = () => switchTab(b.dataset.screen));
document.getElementById('back').onclick = popScreen;
const openSheet = document.getElementById('open-sheet');
if (openSheet) openSheet.onclick = () => toggleSheet();
document.getElementById('close-sheet').onclick = () => {closeOverlays(); render();};
document.getElementById('medium-sheet').onclick = () => setDetent('medium');
document.getElementById('large-sheet').onclick = () => setDetent('large');
document.getElementById('overflow').onclick = () => {
  const open = menu.hidden;
  closeOverlays();
  menu.hidden = !open;
  document.getElementById('overflow').setAttribute('aria-expanded', open);
};
document.addEventListener('keydown', e => { if (e.key === 'Escape') {closeOverlays(); render();} });
window.addEventListener('scroll', () => {
  document.querySelector('.app').classList.toggle('title-collapsed', A.collapse_title && window.scrollY > 32);
});
// Blink refracts the real backdrop; WebKit retains the ambient veil and rim.
const blink = /(?:Chrome|Chromium|Edg|OPR)\//.test(navigator.userAgent) && !/(?:CriOS|EdgiOS)/.test(navigator.userAgent);
document.querySelectorAll('.floating').forEach(e => {
  e.dataset.opticalMode = blink ? 'backdrop-refraction' : 'webkit-ambient-fallback';
  if (blink) e.style.setProperty('--optical-backdrop', 'url(#apple-liquid-glass) blur(2.5px) saturate(135%)');
});
render();
window.navigationDemo = {state, pushScreen, popScreen, switchTab, toggleSheet, performAction, dispatch, currentScreen};
