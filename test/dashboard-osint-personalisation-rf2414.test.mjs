// test/dashboard-osint-personalisation-rf2414.test.mjs
//
// Capability test for R-F2414 — OSINT Market Sweep restructure R2:
// personalisation spine + cross-source provenance.
//
// R2 makes the global sweep answer "what changed that affects ME":
//   - live OSINT/Telegram signals that mention an entity on THE USER's
//     watchlist are flagged (⭐) and summarised;
//   - every correlation shows how well it is corroborated (✓ corroborated
//     · N sources vs single-source) — the honesty moat vs black-box feeds.
//
// This test unit-checks the two new helpers and drives the REAL extracted
// dashboard JS against a fixture where the watchlist entity "Acme
// Corporation" is mentioned in a live signal.
//
// Run: node test/dashboard-osint-personalisation-rf2414.test.mjs

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

// ── Static guard ─────────────────────────────────────────────────────────────
console.log('static guard:');
for (const s of ['id="wl-signal-match"', 'buildWatchlistMatcher', 'provenanceBadge', 'on your watchlist']) {
  ok(HTML.includes(s), `present: ${s}`);
}

// ── Build a DOM shim + run the real extracted JS ─────────────────────────────
const scripts = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).filter(s => s.trim());
const CODE = scripts[scripts.length - 1];

const SWEEP = {
  correlations: [
    { region: 'Eastern Europe', severity: 'critical', signalCount: 22, sourceCount: 4, totalScore: 115 },
    { region: 'Sahel', severity: 'warning', signalCount: 5, sourceCount: 1, totalScore: 30 },
  ],
  bdIntelligence: { counts: { activeTenders: 5 } },
  tg: { urgent: [
    { channel: 'OSINT', date: '2026-07-04T00:00:00Z', text: 'Report: Acme shipment seized at port amid sanctions probe' },
    { channel: 'GDELT', date: '2026-07-04T00:00:00Z', text: 'Unrelated regional conflict escalation continues' },
  ] },
  opportunities: [],
};
const HEALTH = { sourcesOk: 45, sourcesFailed: 3, sourcesTotal: 48 };
const WATCHLIST = { watchlist: [{ name: 'Acme Corporation', entity_type: 'company' }] };

const els = new Map();
const makeEl = () => ({ textContent: '', innerHTML: '', title: '', value: '', dataset: {}, style: {},
  addEventListener() {}, removeEventListener() {}, focus() {}, appendChild() {},
  querySelectorAll() { return []; }, querySelector() { return makeEl(); } });
const getEl = (id) => { if (!els.has(id)) els.set(id, makeEl()); return els.get(id); };

const bodyFor = (path) => {
  if (path.includes('/api/aria/dd/reports')) return { reports: [] };
  if (path.includes('/api/aria/dd/watchlist')) return WATCHLIST;
  if (path.includes('/api/bd-intelligence/pipeline')) return [];
  if (path.includes('/api/opportunities')) return { opportunities: [] };
  if (path.includes('/api/aria/user/sources')) return { sources: [] };
  return {};
};

const sandbox = {
  console, Date, Promise, Math, JSON, Array, Object, String, Number, RegExp, Set,
  encodeURIComponent, setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
  confirm: () => false, alert: () => {},
  Auth: { requireAuth() {} }, Sidebar: { init() {} }, Toast: { show() {} },
  escHtml: (s) => String(s == null ? '' : s),
  truncate: (s, n) => String(s == null ? '' : s).slice(0, n),
  API: { BASE: '', headers: () => ({}),
    get: async (p) => (p === '/api/data' ? SWEEP : p === '/api/health' ? HEALTH : null),
    post: async () => ({ ok: true, data: {} }) },
  fetch: async (url) => ({ ok: true, status: 200, json: async () => bodyFor(String(url)) }),
  document: { getElementById: getEl, querySelector: () => makeEl(), querySelectorAll: () => [], addEventListener() {}, hidden: false },
};
sandbox.window = sandbox;
const ctx = vm.createContext(sandbox);
try {
  vm.runInContext(CODE, ctx, { filename: 'dashboard-inline.js' });
  await ctx.loadData();
} catch (e) { ok(false, `extracted JS ran without throwing (got: ${e && e.message})`); }

// ── Unit: provenanceBadge ────────────────────────────────────────────────────
console.log('unit — provenanceBadge:');
ok(/corroborated/.test(ctx.provenanceBadge(4)) && ctx.provenanceBadge(4).includes('4 sources'), 'sourceCount 4 → corroborated · 4 sources');
ok(/single-source/.test(ctx.provenanceBadge(1)), 'sourceCount 1 → single-source');
ok(/single-source/.test(ctx.provenanceBadge(0)), 'sourceCount 0 → single-source');

// ── Unit: buildWatchlistMatcher ──────────────────────────────────────────────
console.log('unit — buildWatchlistMatcher:');
const mm = ctx.buildWatchlistMatcher({ watchlist: [{ name: 'Acme Corporation' }] });
ok(mm.size === 1, 'one usable watchlist entity');
ok(JSON.stringify(mm.find('acme shipment seized')) === JSON.stringify(['Acme Corporation']), 'matches "acme" (suffix stripped, word-boundary)');
ok(mm.find('the academy of arts').length === 0, 'no false match inside "academy"');
ok(mm.find('this is acmex corp').length === 0, 'no false match on "acmex" (trailing alnum)');
ok(ctx.buildWatchlistMatcher({ watchlist: [{ name: 'AB' }] }).size === 0, 'short name (<4 chars) ignored');
ok(ctx.buildWatchlistMatcher({ watchlist: [] }).size === 0, 'empty watchlist → size 0');

// ── Runtime render (real JS + fixture) ───────────────────────────────────────
console.log('runtime render:');
const summary = getEl('wl-signal-match').innerHTML;
ok(summary.includes('Acme Corporation') && /live signal/.test(summary), 'watchlist summary names the matched entity');
ok(getEl('wl-signal-match').style.display === '', 'watchlist summary is shown (display cleared)');
const osint = getEl('osint-list').innerHTML;
ok(osint.includes('⭐') && osint.includes('Acme Corporation'), 'the matching Telegram post is flagged ⭐');
ok((osint.match(/⭐/g) || []).length === 1, 'only the matching post is flagged (not the unrelated one)');
const corr = getEl('correlations-list').innerHTML;
ok(/corroborated/.test(corr) && corr.includes('4 sources'), 'the 4-source correlation shows corroborated');
ok(/single-source/.test(corr), 'the 1-source correlation shows single-source');

console.log(failures === 0 ? '\nPASS' : `\nFAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
