/* Shared round/portal storage and DOM behavior. */
(function () {
  'use strict';
  function readState(storage, goal) {
    if (!storage) return {};
    try {
      const state = JSON.parse(storage.getItem(`design-gallery:${goal}`) || '{}');
      if (!state || typeof state !== 'object' || Array.isArray(state)) return {};
      return Object.fromEntries(Object.entries(state).filter(([, value]) => value && typeof value === 'object' && !Array.isArray(value)));
    } catch { return {}; }
  }
  const entryKey = card => JSON.stringify([card.category, card.round, card.id]);
  function updateState(state, card, field, value) {
    const next = Object.fromEntries(Object.entries(state || {}).filter(([, item]) => item && typeof item === 'object'));
    if (field === 'selected' && value) Object.entries(next).forEach(([key, old]) => { if (old.category === card.category) next[key] = {...old, selected: false}; });
    const key = entryKey(card); next[key] = {...next[key], category: card.category, round: card.round, id: card.id, [field]: value}; return next;
  }
  function activeItems(state, root, activeCards) {
    const stored = new Map(Object.entries(state || {}).filter(([, item]) => item && typeof item === 'object').map(([key, item]) => [key, {...item}]));
    const explicitCards = activeCards !== undefined;
    const cards = explicitCards ? (activeCards || []) : root ? [...root.querySelectorAll('.variant-card')].map(node => node.__galleryCard).filter(Boolean) : null;
    let active;
    if (cards) {
      active = cards.filter(card => card && !card.reference);
    } else {
      const rounds = new Map(), latest = new Map();
      stored.forEach(item => { if (item.category && item.id && !item.reference) rounds.set(item.category, Math.max(rounds.get(item.category) ?? -Infinity, Number(item.round) || 0)); });
      stored.forEach(item => { if (!item.category || !item.id || item.reference || (Number(item.round) || 0) !== rounds.get(item.category)) return; latest.set(`${item.category}/${item.id}`, item); });
      active = [...latest.values()];
    }
    return active.map(card => {
      const key = entryKey(card), item = {...card, ...(stored.get(key) || {}), category: card.category, round: card.round, id: card.id,
        title: card.title || stored.get(key)?.title || card.id};
      if (root) {
        const node = [...root.querySelectorAll('.variant-card')].find(candidate => candidate.__galleryCard && entryKey(candidate.__galleryCard) === key);
        if (node) { const select = node.querySelector('[data-select]'), star = node.querySelector('[data-star]'), note = node.querySelector('[data-note]');
          if (select) item.selected = !!select.checked; if (star) item.starred = !!star.checked; if (note) item.notes = note.value.trim(); }
      }
      return item;
    });
  }
  function telemetryText(items) { return `已记录 ${items.filter(s => typeof s.notes === 'string' && s.notes.trim()).length} 条观察 · ${items.filter(s => s.starred === true).length} 个收藏 · ${items.filter(s => s.selected === true).length} 个选择`; }
  function selectionSummary(goal, state, root, activeCards) {
    const all = activeItems(state, root, activeCards);
    const label = s => `${s.category}/${s.id}`, categories = [...new Set(all.map(s => s.category).filter(Boolean))].sort();
    const lines = [`Goal: ${goal}`];
    categories.forEach(category => { const scoped = all.filter(s => s.category === category), notes = scoped.filter(s => typeof s.notes === 'string' && s.notes.trim()); lines.push(`Category: ${category}`, `SELECT: ${scoped.filter(s => s.selected === true).map(label).join(', ') || 'none'}`, `STAR: ${scoped.filter(s => s.starred === true).map(label).join(', ') || 'none'}`, notes.length ? 'NOTES:' : 'NOTES: none');
      notes.forEach(s => lines.push(`  - ${label(s)} (${s.title || s.id}): ${s.notes.trim()}`)); });
    if (!categories.length) lines.push('SELECT: none', 'STAR: none', 'NOTES: none');
    return lines.join('\n');
  }
  function frameMetrics(mode, mobileWeb, cardWidth) { const compact = mode === 'mobile' || (mobileWeb && mode === 'web'); const width = compact ? 390 : 1280, height = compact ? 844 : 800, padding = mode === 'mobile' ? 12 : 10; return {width, height, padding, scale: Math.max(.05, Math.min(compact ? .8 : .46, (cardWidth - 24 - padding * 2) / width)), chrome: mode === 'mobile' ? 0 : 28}; }
  const api = {readState, entryKey, updateState, activeItems, telemetryText, selectionSummary, frameMetrics};
  if (typeof module !== 'undefined') module.exports = api;
  if (typeof window !== 'undefined') window.galleryChooser = api;
  if (typeof document === 'undefined' || !document.getElementById('gallery-config')) return;
  const config = JSON.parse(document.getElementById('gallery-config').textContent), root = document.getElementById(config.portal ? 'portal-grid' : 'variants');
  const articles = [...root.children], cards = new Map(articles.map((node, i) => { node.__galleryCard = config.cards[i]; return [node, config.cards[i]]; }));
  const status = document.getElementById('status'), telemetry = document.getElementById('selection-telemetry'); let group = config.portal ? config.firstGroup : 'all', platform = 'all', theme = 'light', mobileWeb = false, memoryState = {};
  function storage() { try { return window.localStorage; } catch { return null; } }
  function currentState() { const stored = readState(storage(), config.goal); return Object.keys(stored).length ? {...memoryState, ...stored} : {...memoryState}; }
  function updateTelemetry(state) { if (telemetry) telemetry.textContent = telemetryText(activeItems(state, root, config.cards)); }
  function restoreControls() { const state = currentState(); memoryState = {...state}; cards.forEach((card, node) => { if (card.reference) return; const value = state[entryKey(card)] || {}; node.querySelector('[data-select]').checked = !!value.selected; node.querySelector('[data-star]').checked = !!value.starred; node.querySelector('[data-note]').value = value.notes || ''; }); updateTelemetry(state); filter(); }
  root.addEventListener('input', persist); root.addEventListener('change', persist);
  function persist(event) { const node = event.target.closest('.variant-card'), card = cards.get(node), input = event.target; if (!card || card.reference) return; const field = input.hasAttribute('data-select') ? 'selected' : input.hasAttribute('data-star') ? 'starred' : input.hasAttribute('data-note') ? 'notes' : null; if (!field) return; const state = updateState(currentState(), card, field, field === 'notes' ? input.value : input.checked); memoryState = state; try { const store = storage(); if (!store) throw new Error('Storage unavailable'); store.setItem(`design-gallery:${config.goal}`, JSON.stringify(state)); } catch { status.textContent = 'Local storage unavailable — changes cannot persist in this browser context.'; } restoreControls(); }
  function applyFrame(frame) { try { const doc = frame.contentDocument; if (!doc?.body) return; doc.documentElement.dataset.theme = theme; doc.body.dataset.theme = theme; } catch { status.textContent = 'Preview appearance unavailable across origins; open via a same-origin HTTP server.'; } }
  function applyTheme(value) { theme = value; document.body.dataset.theme = theme; root.querySelectorAll('iframe').forEach(applyFrame); }
  function fitFrames() { root.querySelectorAll('.device-bezel').forEach(bezel => { const card = bezel.closest('.variant-card'), m = frameMetrics(bezel.dataset.frameMode, mobileWeb, card.clientWidth); bezel.style.setProperty('--frame-width', `${m.width}px`); bezel.style.setProperty('--frame-height', `${m.height}px`); bezel.style.setProperty('--preview-scale', m.scale); }); }
  function filter() { let visible = 0; articles.forEach(node => { const matches = (group === 'all' || node.dataset.group === group) && (platform === 'all' || node.dataset.platform === platform); node.hidden = !matches; node.setAttribute('aria-hidden', String(!matches)); if (matches) visible++; }); document.getElementById('empty-state').hidden = visible > 0; updateTelemetry(currentState()); fitFrames(); applyTheme(theme); }
  function bindFilter(selector, update) { const buttons = [...document.querySelectorAll(selector)]; buttons.forEach(button => button.addEventListener('click', () => { buttons.forEach(b => b.setAttribute('aria-pressed', String(b === button))); update(button); filter(); })); }
  bindFilter('[data-group-filter]', b => { group = b.dataset.groupFilter; }); bindFilter('[data-portal-platform]', b => { platform = b.dataset.portalPlatform; });
  document.querySelectorAll('button[data-theme],button[data-portal-theme]').forEach(button => button.addEventListener('click', () => applyTheme(button.dataset.portalTheme || button.dataset.theme)));
  document.getElementById('responsive-toggle')?.addEventListener('click', event => { mobileWeb = !mobileWeb; event.currentTarget.setAttribute('aria-pressed', String(mobileWeb)); fitFrames(); });
  document.getElementById('copy-summary').addEventListener('click', async () => { const text = selectionSummary(config.goal, currentState(), root, config.cards), area = document.getElementById('summary-text'); area.value = text; try { await navigator.clipboard.writeText(text); area.hidden = true; status.textContent = 'Summary copied'; } catch { area.hidden = false; area.focus(); area.select(); status.textContent = 'Clipboard unavailable — copy the summary below.'; } });
  articles.forEach(node => node.querySelector('iframe').addEventListener('load', event => applyFrame(event.target)));
  window.addEventListener('storage', restoreControls); window.addEventListener('pageshow', restoreControls); window.addEventListener('resize', fitFrames); const observer = new ResizeObserver(fitFrames); observer.observe(root); restoreControls(); filter();
})();
