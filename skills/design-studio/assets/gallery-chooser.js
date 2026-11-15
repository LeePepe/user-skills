/* Shared round/portal storage and DOM behavior. */
(function () {
  'use strict';
  function readState(storage, goal) {
    if (!storage) return {};
    try { const state = JSON.parse(storage.getItem(`design-gallery:${goal}`) || '{}'); return state && typeof state === 'object' && !Array.isArray(state) ? state : {}; } catch { return {}; }
  }
  const entryKey = card => JSON.stringify([card.category, card.round, card.id]);
  function updateState(state, card, field, value) {
    const next = Object.assign({}, state);
    if (field === 'selected' && value) Object.entries(next).forEach(([key, old]) => { if (old.category === card.category) next[key] = {...old, selected: false}; });
    const key = entryKey(card); next[key] = {...next[key], category: card.category, round: card.round, id: card.id, [field]: value}; return next;
  }
  function selectionSummary(goal, state, root) {
    const entries = Object.values(state || {}), label = s => `r${s.round}/${s.id}`;
    const selected = entries.filter(s => s.selected === true), starred = entries.filter(s => s.starred === true);
    if (root) root.querySelectorAll('.variant-card').forEach(node => { const card = node.__galleryCard; if (!card || card.reference) return; const key = entryKey(card), value = state[key] || {}; const note = node.querySelector('[data-note]')?.value.trim(); if (node.querySelector('[data-select]')?.checked) value.selected = true; if (node.querySelector('[data-star]')?.checked) value.starred = true; if (note) value.notes = note; if (value.selected || value.starred || value.notes) entries.push({...value, category: card.category, round: card.round, id: card.id}); });
    const all = entries.length ? entries : [], categories = [...new Set(all.map(s => s.category).filter(Boolean))].sort(); const lines = [`Goal: ${goal}`];
    categories.forEach(category => { const scoped = all.filter(s => s.category === category); lines.push(`Category: ${category}`, `SELECT: ${scoped.filter(s => s.selected === true).map(label).join(', ') || 'none'}`, `STAR: ${scoped.filter(s => s.starred === true).map(label).join(', ') || 'none'}`); });
    if (!categories.length) lines.push('SELECT: none', 'STAR: none');
    lines.push('NOTES:'); all.filter(s => typeof s.notes === 'string' && s.notes.trim()).forEach(s => lines.push(`${label(s)}: ${s.notes.trim()}`)); return lines.join('\n');
  }
  function frameMetrics(mode, mobileWeb, cardWidth) { const compact = mode === 'mobile' || (mobileWeb && mode === 'web'); const width = compact ? 390 : 1280, height = compact ? 844 : 800, padding = mode === 'mobile' ? 12 : 10; return {width, height, padding, scale: Math.max(.05, Math.min(compact ? .8 : .46, (cardWidth - 24 - padding * 2) / width)), chrome: mode === 'mobile' ? 0 : 28}; }
  if (typeof module !== 'undefined') module.exports = {readState, entryKey, updateState, selectionSummary, frameMetrics};
  if (typeof document === 'undefined') return;
  const config = JSON.parse(document.getElementById('gallery-config').textContent), root = document.getElementById(config.portal ? 'portal-grid' : 'variants');
  const articles = [...root.children], cards = new Map(articles.map((node, i) => { node.__galleryCard = config.cards[i]; return [node, config.cards[i]]; }));
  const status = document.getElementById('status'), telemetry = document.getElementById('selection-telemetry'); let group = config.portal ? config.firstGroup : 'all', platform = 'all', theme = 'light', mobileWeb = false;
  function storage() { try { return window.localStorage; } catch { return null; } }
  function currentState() { return readState(storage(), config.goal); }
  function updateTelemetry(state) { if (!telemetry) return; const values = Object.values(state || {}); telemetry.textContent = `已记录 ${values.filter(s => typeof s.notes === 'string' && s.notes.trim()).length} 条观察 · ${values.filter(s => s.starred === true).length} 个收藏 · ${values.filter(s => s.selected === true).length} 个选择`; }
  function restoreControls() { const state = currentState(); cards.forEach((card, node) => { if (card.reference) return; const value = state[entryKey(card)] || {}; node.querySelector('[data-select]').checked = !!value.selected; node.querySelector('[data-star]').checked = !!value.starred; node.querySelector('[data-note]').value = value.notes || ''; }); updateTelemetry(state); filter(); }
  root.addEventListener('input', persist); root.addEventListener('change', persist);
  function persist(event) { const node = event.target.closest('.variant-card'), card = cards.get(node), input = event.target; if (!card || card.reference) return; const field = input.hasAttribute('data-select') ? 'selected' : input.hasAttribute('data-star') ? 'starred' : input.hasAttribute('data-note') ? 'notes' : null; if (!field) return; const state = updateState(currentState(), card, field, field === 'notes' ? input.value : input.checked); try { const store = storage(); if (!store) throw new Error('Storage unavailable'); store.setItem(`design-gallery:${config.goal}`, JSON.stringify(state)); restoreControls(); } catch { updateTelemetry(state); status.textContent = 'Local storage unavailable — changes cannot persist in this browser context.'; } }
  function applyFrame(frame) { try { const doc = frame.contentDocument; if (!doc?.body) return; doc.documentElement.dataset.theme = theme; doc.body.dataset.theme = theme; } catch { status.textContent = 'Preview appearance unavailable across origins; open via a same-origin HTTP server.'; } }
  function applyTheme(value) { theme = value; document.body.dataset.theme = theme; root.querySelectorAll('iframe').forEach(applyFrame); }
  function fitFrames() { root.querySelectorAll('.device-bezel').forEach(bezel => { const card = bezel.closest('.variant-card'), m = frameMetrics(bezel.dataset.frameMode, mobileWeb, card.clientWidth); bezel.style.setProperty('--frame-width', `${m.width}px`); bezel.style.setProperty('--frame-height', `${m.height}px`); bezel.style.setProperty('--preview-scale', m.scale); }); }
  function filter() { let visible = 0; articles.forEach(node => { const matches = (group === 'all' || node.dataset.group === group) && (platform === 'all' || node.dataset.platform === platform); node.hidden = !matches; node.setAttribute('aria-hidden', String(!matches)); if (matches) visible++; }); document.getElementById('empty-state').hidden = visible > 0; updateTelemetry(currentState()); fitFrames(); applyTheme(theme); }
  function bindFilter(selector, update) { const buttons = [...document.querySelectorAll(selector)]; buttons.forEach(button => button.addEventListener('click', () => { buttons.forEach(b => b.setAttribute('aria-pressed', String(b === button))); update(button); filter(); })); }
  bindFilter('[data-group-filter]', b => { group = b.dataset.groupFilter; }); bindFilter('[data-portal-platform]', b => { platform = b.dataset.portalPlatform; });
  document.querySelectorAll('button[data-theme],button[data-portal-theme]').forEach(button => button.addEventListener('click', () => applyTheme(button.dataset.portalTheme || button.dataset.theme)));
  document.getElementById('responsive-toggle')?.addEventListener('click', event => { mobileWeb = !mobileWeb; event.currentTarget.setAttribute('aria-pressed', String(mobileWeb)); fitFrames(); });
  document.getElementById('copy-summary').addEventListener('click', async () => { const text = selectionSummary(config.goal, currentState(), root), area = document.getElementById('summary-text'); area.value = text; try { await navigator.clipboard.writeText(text); area.hidden = true; status.textContent = 'Summary copied'; } catch { area.hidden = false; area.focus(); area.select(); status.textContent = 'Clipboard unavailable — copy the summary below.'; } });
  articles.forEach(node => node.querySelector('iframe').addEventListener('load', event => applyFrame(event.target)));
  window.addEventListener('storage', restoreControls); window.addEventListener('pageshow', restoreControls); window.addEventListener('resize', fitFrames); const observer = new ResizeObserver(fitFrames); observer.observe(root); restoreControls(); filter();
})();
