/* Shared round/portal storage and DOM behavior. No HTML is built from saved text. */
(function () {
  'use strict';
  function readState(storage, goal) {
    try {
      const state = JSON.parse(storage.getItem(`design-gallery:${goal}`) || '{}');
      return state && typeof state === 'object' && !Array.isArray(state) ? state : {};
    } catch { return {}; }
  }
  const entryKey = card => JSON.stringify([card.category, card.round, card.id]);
  function updateState(state, card, field, value) {
    const next = Object.assign({}, state);
    if (field === 'selected' && value) {
      Object.entries(next).forEach(([key, old]) => {
        if (old.category === card.category) next[key] = {...old, selected: false};
      });
    }
    const key = entryKey(card);
    next[key] = {...next[key], category: card.category, round: card.round, id: card.id, [field]: value};
    return next;
  }
  function selectionSummary(goal, state) {
    const lines = [`Goal: ${goal}`];
    const categories = [...new Set(Object.values(state).map(s => s.category))].sort();
    categories.forEach(category => {
      const entries = Object.values(state).filter(s => s.category === category);
      const label = s => `r${s.round}/${s.id}`;
      lines.push(`Category: ${category}`, `SELECT: ${entries.filter(s => s.selected).map(label).join(', ') || 'none'}`,
        `STAR: ${entries.filter(s => s.starred).map(label).join(', ') || 'none'}`);
      entries.filter(s => s.notes).forEach(s => lines.push(`${label(s)}: ${s.notes}`));
    });
    return lines.join('\n');
  }
  function frameMetrics(mode, mobileWeb, cardWidth) {
    const compact = mode === 'mobile' || (mobileWeb && mode === 'web');
    const width = compact ? 390 : 1280, height = compact ? 844 : 800;
    const padding = mode === 'mobile' ? 12 : 10;
    const scale = Math.max(.05, Math.min(compact ? .8 : .46, (cardWidth - 24 - padding * 2) / width));
    return {width, height, padding, scale, chrome: mode === 'mobile' ? 0 : 28};
  }
  // Pure helpers are executable by the dependency-free Node regression runner.
  if (typeof module !== 'undefined') module.exports = {readState, entryKey, updateState, selectionSummary, frameMetrics};
  if (typeof document === 'undefined') return;

  const config = JSON.parse(document.getElementById('gallery-config').textContent);
  const root = document.getElementById(config.portal ? 'portal-grid' : 'variants');
  const articles = [...root.children];
  const cards = new Map(articles.map((node, i) => [node, config.cards[i]]));
  const status = document.getElementById('status');
  let group = 'all', platform = 'all', theme = 'light', mobileWeb = false;
  function storage() { try { return window.localStorage; } catch { return null; } }
  function currentState() { return readState(storage(), config.goal); }
  function restoreControls() {
    const state = currentState();
    cards.forEach((card, node) => {
      if (card.reference) return;
      const value = state[entryKey(card)] || {};
      node.querySelector('[data-select]').checked = !!value.selected;
      node.querySelector('[data-star]').checked = !!value.starred;
      node.querySelector('[data-note]').value = value.notes || '';
    });
  }
  root.addEventListener('input', persist);
  root.addEventListener('change', persist);
  function persist(event) {
    const node = event.target.closest('.variant-card');
    const card = cards.get(node);
    if (!card || card.reference) return;
    const input = event.target;
    const field = input.hasAttribute('data-select') ? 'selected' : input.hasAttribute('data-star') ? 'starred' : input.hasAttribute('data-note') ? 'notes' : null;
    if (!field) return;
    const value = field === 'notes' ? input.value : input.checked;
    const state = updateState(currentState(), card, field, value);
    try {
      if (!storage()) throw new Error('Storage unavailable');
      storage().setItem(`design-gallery:${config.goal}`, JSON.stringify(state));
      restoreControls();
    } catch { status.textContent = 'Local storage unavailable — changes cannot persist in this browser context.'; }
  }
  function applyFrame(frame) {
    try {
      const doc = frame.contentDocument;
      if (!doc?.body) return;
      doc.documentElement.dataset.theme = theme;
      doc.body.dataset.theme = theme;
    } catch { status.textContent = 'Preview appearance unavailable across origins; open via a same-origin HTTP server.'; }
  }
  articles.forEach(node => node.querySelector('iframe').addEventListener('load', event => applyFrame(event.target)));
  function applyTheme(value) {
    theme = value;
    document.body.dataset.theme = theme;
    root.querySelectorAll('iframe').forEach(applyFrame);
  }
  function fitFrames() {
    root.querySelectorAll('.device-bezel').forEach(bezel => {
      const card = bezel.closest('.variant-card');
      const {width, height, scale} = frameMetrics(bezel.dataset.frameMode, mobileWeb, card.clientWidth);
      bezel.style.setProperty('--frame-width', `${width}px`);
      bezel.style.setProperty('--frame-height', `${height}px`);
      bezel.style.setProperty('--preview-scale', scale);
    });
  }
  function filter() {
    const visible = articles.filter(node => (group === 'all' || node.dataset.group === group) &&
      (platform === 'all' || node.dataset.platform === platform));
    root.replaceChildren(...visible);
    document.getElementById('empty-state').hidden = visible.length > 0;
    restoreControls();
    fitFrames();
    applyTheme(theme);
  }
  function bindFilter(selector, update) {
    const buttons = [...document.querySelectorAll(selector)];
    buttons.forEach(button => button.addEventListener('click', () => {
      buttons.forEach(b => b.setAttribute('aria-pressed', String(b === button)));
      update(button); filter();
    }));
  }
  bindFilter('[data-group-filter]', b => { group = b.dataset.groupFilter; });
  bindFilter('[data-portal-platform]', b => { platform = b.dataset.portalPlatform; });
  document.querySelectorAll('button[data-theme],button[data-portal-theme]').forEach(button => {
    button.addEventListener('click', () => applyTheme(button.dataset.portalTheme || button.dataset.theme));
  });
  document.getElementById('responsive-toggle')?.addEventListener('click', event => {
    mobileWeb = !mobileWeb;
    event.currentTarget.setAttribute('aria-pressed', String(mobileWeb));
    fitFrames();
  });
  document.getElementById('copy-summary').addEventListener('click', async () => {
    const text = selectionSummary(config.goal, currentState());
    const area = document.getElementById('summary-text');
    area.value = text;
    try {
      await navigator.clipboard.writeText(text);
      area.hidden = true;
      status.textContent = 'Summary copied';
    } catch {
      area.hidden = false;
      area.focus(); area.select();
      status.textContent = 'Clipboard unavailable — copy the summary below.';
    }
  });
  window.addEventListener('storage', restoreControls);
  window.addEventListener('pageshow', restoreControls);
  window.addEventListener('resize', fitFrames);
  const observer = new ResizeObserver(fitFrames);
  observer.observe(root);
  restoreControls(); fitFrames(); applyTheme('light');
})();
