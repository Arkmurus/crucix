// R-F2385 — dashboard Golden Intel panel.
//
// Drives the real dashboard inline JavaScript with a promoted intel-signal
// fixture. This protects the business-facing contract: dashboard.html must show
// decision-grade signals clearly before the raw news/audit feed.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

for (const marker of [
  'id="golden-intel-card"',
  'id="golden-intel-list"',
  '/api/aria/intel/signals/recent?limit=20',
  'renderGoldenIntel',
  'why_it_matters',
  'recommended_action',
]) {
  assert.ok(HTML.includes(marker), `dashboard must include ${marker}`);
}

const scripts = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map((m) => m[1])
  .filter((s) => s.trim());
const CODE = scripts[scripts.length - 1];

const INTEL = {
  schema_version: 'rf2385.v1',
  signals: [{
    signal_type: 'active_tender',
    priority: 'HIGH',
    confidence: 'HIGH',
    decision_summary: 'Angola launches armoured vehicle tender',
    why_it_matters: 'Procurement activity may create a near-term commercial window.',
    recommended_action: 'Qualify opportunity',
    target: 'Angola',
    source: 'US DoD Daily Contracts',
    source_tier: 'tier_1b',
    url: 'https://example.com/angola-tender',
    detected_at: '2026-07-07T10:00:00Z',
  }],
};

const els = new Map();
const makeEl = () => ({
  textContent: '',
  innerHTML: '',
  title: '',
  value: '',
  dataset: {},
  style: {},
  addEventListener() {},
  removeEventListener() {},
  focus() {},
  appendChild() {},
  querySelectorAll() { return []; },
  querySelector() { return makeEl(); },
});
const getEl = (id) => {
  if (!els.has(id)) els.set(id, makeEl());
  return els.get(id);
};

const bodyFor = (path) => {
  if (path.includes('/api/aria/dd/reports')) return { reports: [] };
  if (path.includes('/api/aria/dd/watchlist')) return { watchlist: [] };
  if (path.includes('/api/bd-intelligence/pipeline')) return [];
  if (path.includes('/api/opportunities')) return { opportunities: [] };
  if (path.includes('/api/aria/intel/signals/recent')) return INTEL;
  if (path.includes('/api/aria/user/sources')) return { sources: [] };
  return {};
};

const sandbox = {
  console,
  Date,
  Promise,
  Math,
  JSON,
  Array,
  Object,
  String,
  Number,
  RegExp,
  Set,
  encodeURIComponent,
  setTimeout,
  clearTimeout,
  setInterval: () => 0,
  clearInterval: () => {},
  confirm: () => false,
  alert: () => {},
  Auth: { requireAuth() {} },
  Sidebar: { init() {} },
  Toast: { show() {} },
  escHtml: (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c])),
  truncate: (s, n) => String(s == null ? '' : s).slice(0, n),
  API: {
    BASE: '',
    headers: () => ({}),
    get: async (p) => (p === '/api/data'
      ? { correlations: [], bdIntelligence: { counts: {} }, tg: { urgent: [] } }
      : p === '/api/health'
        ? { sourcesOk: 1, sourcesTotal: 1 }
        : null),
    post: async () => ({ ok: true, data: {} }),
  },
  fetch: async (url) => ({ ok: true, status: 200, json: async () => bodyFor(String(url)) }),
  document: {
    getElementById: getEl,
    querySelector: () => makeEl(),
    querySelectorAll: () => [],
    addEventListener() {},
    hidden: false,
  },
};
sandbox.window = sandbox;

const ctx = vm.createContext(sandbox);
vm.runInContext(CODE, ctx, { filename: 'dashboard-inline.js' });
await ctx.loadData();

const html = getEl('golden-intel-list').innerHTML;
assert.match(html, /Angola launches armoured vehicle tender/);
assert.match(html, /HIGH/);
assert.match(html, /HIGH confidence/);
assert.match(html, /Procurement activity may create a near-term commercial window/);
assert.match(html, /Qualify opportunity/);
assert.match(html, /Evidence/);
assert.match(html, /tier_1b/);

console.log('PASS');
