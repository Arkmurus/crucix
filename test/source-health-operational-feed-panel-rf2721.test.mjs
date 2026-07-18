// R-F2721 — Codex source-health audit #1: sources.html OMITTED /api/source-health (the
// operational briefing-feed tracker), so the page couldn't show whether the ~50 feeds
// actually run reliably, and it OVERCLAIMED ("Real-time status of all…" + "Source for the
// Tier 1a/1b/2 credibility scoring across DD reports + chat retrieval" — untrue: web_atlas
// ingestion is unwired, coverage 0). This drives the real inline JS with a source-health
// fixture and asserts the operational-feed panel renders honest buckets (unconfigured is NOT
// healthy), plus source-asserts the overclaims are gone.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(join(__dirname, '..', 'public', 'sources.html'), 'utf8');

// ── source-level honesty assertions ──────────────────────────────────────────
assert.ok(HTML.includes('/api/source-health'), 'page must integrate /api/source-health (Codex #1)');
assert.ok(HTML.includes('async function loadOperationalFeeds'), 'operational-feed loader must exist');
assert.ok(!HTML.includes('Real-time status of all intelligence data sources'), 'the "all sources" overclaim must be gone');
assert.ok(!HTML.includes('Source for the Tier 1a / 1b / 2 credibility scoring across DD reports + chat retrieval'),
  'the unsupported catalogue-feeds-DD claim must be gone');
assert.ok(HTML.includes('Reference registry only'), 'the catalogue must be honestly labelled a reference registry');

// ── behavioural: drive the real loadOperationalFeeds via vm + mock DOM/fetch ──
const scripts = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]).filter((s) => s.trim());
const CODE = scripts.join('\n');

const els = new Map();
const makeEl = () => ({
  textContent: '', innerHTML: '', title: '', value: '', disabled: false,
  dataset: {}, style: {}, classList: { add() {}, remove() {} },
  addEventListener() {}, removeEventListener() {}, appendChild() {},
  querySelector() { return makeEl(); }, querySelectorAll() { return []; },
});
const getEl = (id) => { if (!els.has(id)) els.set(id, makeEl()); return els.get(id); };

const sourceHealth = {
  sources: [
    { name: 'OFAC', reliability: 100 },
    { name: 'Lusophone', reliability: 79 },
    { name: 'ProcurementTenders', reliability: 4 },
    { name: 'Comtrade', reliability: null },
    { name: 'CSL', reliability: null },
  ],
  degraded: ['Lusophone', 'ProcurementTenders'],
  unconfigured: ['Comtrade', 'CSL'],
  notChecked: [],
  healthyCount: 1, degradedCount: 2, unconfiguredCount: 2, notCheckedCount: 0,
};

const sandbox = {
  console, Date, Promise, Math, JSON, Array, Object, String, Number, RegExp, Set, URL,
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
  localStorage: { getItem: () => '' },
  Auth: { requireAuth() {} }, Sidebar: { init() {} }, Modal: { confirm: async () => false }, Toast: { show() {} },
  API: { get: async () => ({}), post: async () => ({ ok: true }) },
  fetch: async (url) => {
    if (String(url).includes('/api/source-health')) {
      return { ok: true, status: 200, json: async () => sourceHealth };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  },
  document: { getElementById: getEl, querySelector: () => makeEl(), querySelectorAll: () => [], addEventListener() {}, hidden: false },
};
sandbox.window = sandbox;

const ctx = vm.createContext(sandbox);
vm.runInContext(CODE, ctx, { filename: 'sources-inline.js' });
await ctx.loadOperationalFeeds();

// honest bucket counts
assert.equal(String(getEl('opfeed-healthy').textContent), '1', 'only reliability>=80 is healthy');
assert.equal(String(getEl('opfeed-degraded').textContent), '2');
assert.equal(String(getEl('opfeed-unconfigured').textContent), '2', 'Comtrade+CSL are unconfigured, not healthy');
assert.equal(String(getEl('opfeed-notchecked').textContent), '0');

const table = getEl('opfeed-body').innerHTML;
assert.match(table, /OFAC[\s\S]*?Healthy/, 'OFAC (100%) → Healthy');
assert.match(table, /Lusophone[\s\S]*?Degraded/, 'Lusophone (79%) → Degraded');
assert.match(table, /Comtrade[\s\S]*?Unconfigured/, 'Comtrade (null) → Unconfigured, NOT healthy');
// the unconfigured rows must never be labelled Healthy
const comtradeRow = table.split('</tr>').find((r) => r.includes('Comtrade')) || '';
assert.ok(!comtradeRow.includes('Healthy'), 'an unconfigured feed must never render as Healthy');

console.log('PASS');
