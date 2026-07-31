// test/dashboard-osint-real-tiles-rf2411.test.mjs
//
// Capability test for R-F2411 — OSINT Market Sweep restructure R1.
//
// The sweep KPI row headlined VIX / Brent Crude / Market Direction —
// financial noise with zero decision value for a defence/security buyer,
// and it surfaced NO real intelligence. R-F2411 replaces those four tiles
// with populated brain fields: Critical Alerts + Conflict Signals (from
// ACLED-driven `correlations`), Active Tenders (from `bdIntelligence`,
// a real feed), and Source Health (from /api/health). The OFAC and
// export-control SWEEP feeds are stubs (ofac.mjs discards its fetch;
// export_controls.mjs never fetches) so they are deliberately NOT surfaced
// as "▲N new" deltas — shipping a delta over a hardcoded literal would be
// fabricated data on the flagship page.
//
// This test (1) statically guards the noise is gone + the real tiles exist,
// and (2) drives the ACTUAL extracted dashboard JS (not a reimplementation)
// against a realistic /api/data + /api/health fixture in a DOM shim, and
// asserts the four real tiles compute the correct values while the removed
// noise IDs are never queried.
//
// Run: node test/dashboard-osint-real-tiles-rf2411.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

let failures = 0;
function ok(cond, msg) {
  if (cond) { console.log(`  ✓ ${msg}`); }
  else { console.error(`  ✗ ${msg}`); failures++; }
}

// ── 1. Static guard — noise removed, real tiles present ──────────────────────
console.log('static guard:');
for (const gone of ['id="kpi-vix"', 'id="kpi-brent"', 'id="kpi-direction"', 'id="kpi-osint"',
                     'Market Direction', 'Brent Crude', 'data.markets', 'data.energy']) {
  ok(!HTML.includes(gone), `removed: ${gone}`);
}
for (const present of ['id="kpi-critical"', 'id="kpi-conflict"', 'id="kpi-tenders"', 'id="kpi-srchealth"',
                       // R-F3344 — 'Critical Alerts' became 'High Correlations', and that is a
                       // claim being withdrawn, not a rename. The tile is unchanged
                       // otherwise: same id="kpi-critical", same red octagon, same
                       // source (correlations with severity==='critical'). What changed
                       // is what it CLAIMS: dashboard.html:738 now reads "High
                       // correlations - early research indicators, not customer alerts",
                       // and the tooltip says "N high-scoring research correlation(s)".
                       // Labelling a research indicator a "Critical Alert" tells an
                       // operator to act on a signal that has not earned it.
                       'High Correlations', 'Conflict Signals', 'Active Tenders', 'Source Health']) {
  ok(HTML.includes(present), `present: ${present}`);
}

// ── 2. Runtime capability — drive the real extracted JS against fixtures ─────
console.log('runtime render (real JS, fixture data):');

// Pull the last inline <script> block (the page logic).
const scripts = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).filter(s => s.trim());
const CODE = scripts[scripts.length - 1];

const SWEEP = {
  correlations: [
    { region: 'Eastern Europe', severity: 'critical', signalCount: 22, sourceCount: 4, totalScore: 115 },
    { region: 'East Africa', severity: 'warning', signalCount: 8, sourceCount: 2, totalScore: 40 },
  ],
  bdIntelligence: { counts: { activeTenders: 5, strategicIdeas: 3, pipelineDeals: 2 } },
  tg: { urgent: [{ channel: 'OSINT', date: '2026-07-04T00:00:00Z', text: 'urgent signal text' }] },
  opportunities: [{ market: 'Angola', score: 80, type: 'TENDER', signal_count: 3 }],
  defenseNews: [],
};
const HEALTH = { sourcesOk: 45, sourcesFailed: 3, sourcesTotal: 48 };

// DOM shim: cache one stub per id; record which ids get queried.
const requestedIds = new Set();
const els = new Map();
function makeEl() {
  return { textContent: '', innerHTML: '', title: '', value: '', dataset: {}, style: {},
           addEventListener() {}, removeEventListener() {}, focus() {}, appendChild() {},
           querySelectorAll() { return []; }, querySelector() { return makeEl(); } };
}
function getEl(id) { requestedIds.add(id); if (!els.has(id)) els.set(id, makeEl()); return els.get(id); }

const bodyFor = (path) => {
  if (path.includes('/api/aria/dd/reports')) return { reports: [{ entity_name: 'Acme Corp', risk_classification: 'GREEN', generated_at: '2026-07-01T00:00:00Z' }] };
  if (path.includes('/api/aria/dd/watchlist')) return { watchlist: [{ name: 'Acme Corp', entity_type: 'company' }] };
  if (path.includes('/api/bd-intelligence/pipeline')) return [];
  if (path.includes('/api/opportunities')) return { opportunities: SWEEP.opportunities };
  if (path.includes('/api/aria/user/sources')) return { sources: [] };
  return {};
};

const sandbox = {
  console, Date, Promise, Math, JSON, Array, Object, String, Number, RegExp,
  encodeURIComponent, setTimeout, clearTimeout,
  setInterval: () => 0, clearInterval: () => {},
  confirm: () => false, alert: () => {},
  Auth: { requireAuth() {} },
  Sidebar: { init() {} },
  Toast: { show() {} },
  escHtml: (s) => String(s == null ? '' : s),
  truncate: (s, n) => String(s == null ? '' : s).slice(0, n),
  API: {
    BASE: '',
    headers: () => ({}),
    get: async (path) => (path === '/api/data' ? SWEEP : path === '/api/health' ? HEALTH : null),
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

try {
  vm.runInContext(CODE, ctx, { filename: 'dashboard-inline.js' });
  // The script defines loadData in the context; call + await for a deterministic snapshot.
  await ctx.loadData();
} catch (e) {
  ok(false, `extracted JS ran without throwing (got: ${e && e.message})`);
}

ok(String(getEl('kpi-critical').textContent) === '1', 'Critical Alerts = 1 (one critical correlation)');
ok(String(getEl('kpi-conflict').textContent) === '30', 'Conflict Signals = 30 (22 + 8 signalCount)');
// R-F3536 — the KPI now counts the tenders in the RENDERED research feed, not a
// different window. Live 2026-07-31 it read "Active Tenders 1" directly above a
// feed listing four, because bdIntelligence.counts and the feed are two
// different queries. This fixture supplies no feed signals, so the honest answer
// above an empty panel is 0 — a KPI must never contradict the list it captions.
ok(String(getEl('kpi-tenders').textContent) === '0',
   'Active Tenders counts the rendered feed (empty fixture feed → 0, not a stale 5)');
ok(/research feed/.test(getEl('kpi-tenders').title || ''),
   'the tile states which set it counted');
ok(String(getEl('kpi-srchealth').textContent) === '45/48', 'Source Health = 45/48 (/api/health)');

// The removed noise tiles must never be touched by the live code.
for (const noise of ['kpi-vix', 'kpi-brent', 'kpi-direction', 'kpi-osint']) {
  ok(!requestedIds.has(noise), `noise tile never queried: ${noise}`);
}

console.log(failures === 0 ? '\nPASS' : `\nFAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
