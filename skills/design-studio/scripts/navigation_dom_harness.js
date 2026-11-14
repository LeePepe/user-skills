// Dependency-free DOM/event harness for executing the actual generated scripts.
// Geometry and engine-specific CSS are checked separately in real WKWebView.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
function decode(s) { return s.replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&'); }
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.attrs = {}; this.children = []; this.parentNode = null; this.text = ''; this.listeners = {};
    this.style = {setProperty: (k, v) => {this.attrs.style = (this.attrs.style || '') + `${k}:${v};`;}};
    this.dataset = new Proxy({}, {get: (_, key) => this.getAttribute('data-' + String(key).replace(/[A-Z]/g, x => '-' + x.toLowerCase())),
      set: (_, key, value) => {this.setAttribute('data-' + String(key).replace(/[A-Z]/g, x => '-' + x.toLowerCase()), value); return true;}});
    this.classList = {contains: c => (this.attrs.class || '').split(/\s+/).includes(c),
      add: c => {if (!this.classList.contains(c)) this.attrs.class = ((this.attrs.class || '') + ' ' + c).trim();},
      remove: c => {this.attrs.class = (this.attrs.class || '').split(/\s+/).filter(x => x !== c).join(' ');},
      toggle: (c, force) => {const on = force === undefined ? !this.classList.contains(c) : force; on ? this.classList.add(c) : this.classList.remove(c); return on;}};
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  hasAttribute(k) { return Object.hasOwn(this.attrs, k); }
  get id() { return this.attrs.id || ''; }
  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(v) { v ? this.setAttribute('hidden', '') : this.removeAttribute('hidden'); }
  get open() { return this.hasAttribute('open'); }
  set open(v) { v ? this.setAttribute('open', '') : this.removeAttribute('open'); }
  get disabled() { return this.hasAttribute('disabled'); }
  set disabled(v) { v ? this.setAttribute('disabled', '') : this.removeAttribute('disabled'); }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this.text + this.children.map(c => c.textContent).join(''); }
  set textContent(v) { this.text = String(v); this.children = []; }
  append(...elements) { elements.forEach(e => {e.parentNode = this; this.children.push(e);}); }
  removeChild(e) { this.children.splice(this.children.indexOf(e), 1); e.parentNode = null; }
  appendChild(e) { this.append(e); return e; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatchEvent(event) { this.listeners[event.type]?.forEach(f => f(event)); this['on' + event.type]?.(event); }
  click() { if (!this.disabled) this.dispatchEvent({type: 'click', target: this}); }
  focus() {}
  descendants() { return this.children.flatMap(c => [c, ...c.descendants()]); }
  querySelectorAll(selector) { return this.descendants().filter(e => selector.split(',').some(s => match(e, s.trim()))); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function simple(e, selector) {
  const attributes = [...selector.matchAll(/\[([^=\]]+)(?:=["']?([^"'\]]+)["']?)?\]/g)];
  if (attributes.some(([, k, v]) => !e.hasAttribute(k) || (v !== undefined && e.getAttribute(k) !== v))) return false;
  const base = selector.replace(/\[[^\]]*\]/g, '');
  const id = base.match(/#([\w-]+)/)?.[1]; if (id && e.id !== id) return false;
  const classes = [...base.matchAll(/\.([\w-]+)/g)].map(m => m[1]); if (classes.some(c => !e.classList.contains(c))) return false;
  const tag = base.match(/^[\w-]+/)?.[0]; return !tag || e.tagName === tag.toUpperCase();
}
function match(e, selector) {
  const parts = selector.split(/\s+/); if (!simple(e, parts.pop())) return false;
  let parent = e.parentNode;
  while (parts.length) { const part = parts.pop(); while (parent && !simple(parent, part)) parent = parent.parentNode; if (!parent) return false; parent = parent.parentNode; }
  return true;
}
const document = new Element('document');
document.createElement = tag => new Element(tag);
document.getElementById = id => document.descendants().find(e => e.id === id) || null;
const stack = [document];
const voidTags = new Set(['meta', 'link', 'input', 'img', 'br', 'hr']);
for (const token of input.html.match(/<!--[\s\S]*?-->|<![^>]*>|<[^>]+>|[^<]+/g) || []) {
  if (/^<!/.test(token)) continue;
  if (/^<\//.test(token)) { const tag = token.slice(2, -1).trim().toUpperCase(); if (stack.at(-1).tagName === tag) stack.pop(); continue; }
  if (token[0] === '<') {
    const tag = token.match(/^<([\w-]+)/)?.[1]; if (!tag) continue;
    const e = new Element(tag);
    for (const m of token.slice(tag.length + 1, -1).matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) e.setAttribute(m[1], decode(m[2] ?? m[3] ?? m[4] ?? ''));
    stack.at(-1).append(e); if (!voidTags.has(tag) && !token.endsWith('/>')) stack.push(e);
  } else stack.at(-1).text += decode(token);
}
document.body = document.querySelector('body');
const window = {scrollY: 0, events: {}, addEventListener(type, fn) {this.events[type] = fn;}};
const context = {document, window, navigator: {userAgent: 'WebKit'}, console, assert, JSON, Object, Number, Error, String};
vm.createContext(context);
for (const script of document.querySelectorAll('script')) if (script.getAttribute('type') !== 'application/json') vm.runInContext(script.textContent, context, {timeout: 1000});
vm.runInContext('(function(){' + input.assertions + '\n})();', context, {timeout: 1000});
console.log(JSON.stringify({ok: true, state: window.navigationDemo?.state}));
