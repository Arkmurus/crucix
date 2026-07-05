// test/osint-sweep-hardening-stress-rf2422.test.mjs
//
// Adversarial STRESS/hardening test for the OSINT Market Sweep restructure
// (R-F2411/2414/2416/2419) + the R-F2422 defensive hardening.
//
// Hammers the REAL code — the feed briefing() functions and the extracted
// dashboard JS — with malformed / XSS / huge / failure-mode inputs. fetch is
// mocked per case (no live network), so this is deterministic and CI-safe.
//
// R-F2422 hardening under test:
//   - Array.isArray guards on data.correlations / data.tg.urgent so a
//     non-array /api/data payload can never crash the render.
//   - Number() coercion on numeric interpolations into innerHTML
//     (provenanceBadge sourceCount, correlation signalCount/totalScore).
//
// Run: node test/osint-sweep-hardening-stress-rf2422.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import { briefing as ofac } from '../apis/sources/ofac.mjs';
import { briefing as exp } from '../apis/sources/export_controls.mjs';
import { briefing as sanc } from '../apis/sources/sanctions.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
let fail = 0, n = 0;
const ok = (c, m) => { n++; if (c) console.log(`  ✓ ${m}`); else { console.error(`  ✗ ${m}`); fail++; } };
const realFetch = globalThis.fetch;
const mock = (fn) => { globalThis.fetch = fn; };
const okResp = (payload) => async () => ({ ok: true, status: 200, json: async () => payload });

// ══ A. FEED ROBUSTNESS ══
console.log('A. Feed robustness (real briefing() under adversarial responses):');
try {
  mock(okResp({ results: null }));
  let r = await ofac(); ok(Array.isArray(r.updates) && r.updates.length === 0 && r.status === 'degraded', 'null results → honest-empty, no crash');
  mock(okResp({ results: 'not-an-array' }));
  r = await ofac(); ok(Array.isArray(r.updates) && r.updates.length === 0, 'non-array results → empty, no crash');
  mock(okResp({ results: [{}, {}, {}] }));
  r = await ofac(); ok(r.updates.length === 3 && r.status === 'active', 'field-less items → mapped, no crash');
  mock(okResp({ results: [{ title: '<img src=x onerror=alert(1)>', publication_date: '2026-01-01', html_url: 'https://x/y' }] }));
  r = await ofac(); ok(r.updates[0].title.includes('<img'), 'XSS-in-title carried as inert data');
  mock(okResp({ results: [{ title: 'A'.repeat(200000), publication_date: '2026-01-01' }] }));
  r = await ofac(); ok(r.updates.length === 1, '200k-char title handled');
  mock(okResp({ results: Array.from({ length: 5000 }, (_, i) => ({ title: 't' + i, publication_date: '2026-01-01' })) }));
  r = await ofac(); ok(r.updates.length === 5000 && r.signals.length === 6, '5000 results mapped; signals capped at 6');
  for (const st of [403, 429, 500, 503]) { mock(async () => ({ ok: false, status: st, json: async () => ({}) })); r = await exp(); ok(r.status === 'error' && r.updates.length === 0, `HTTP ${st} → honest-empty error`); }
  mock(async () => { throw new Error('ECONNRESET'); });
  r = await sanc(); ok(r.status === 'error' && r.updates.length === 0, 'network throw → honest-empty error');
  mock(async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError('bad'); } }));
  r = await ofac(); ok(r.status === 'error' && r.updates.length === 0, 'malformed JSON → honest-empty error');
  mock(async () => { const e = new Error('aborted'); e.name = 'AbortError'; throw e; });
  r = await exp(); ok(r.status === 'error', 'abort/timeout → honest-empty error');
} finally { globalThis.fetch = realFetch; }

// ══ B. DASHBOARD RENDER ROBUSTNESS ══
console.log('B. Dashboard render robustness (real JS, adversarial payloads):');
const HTML = readFileSync(join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
const CODE = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).filter(s => s.trim()).pop();
async function render(sweep, watchlist) {
  const els = new Map();
  const makeEl = () => ({ textContent: '', innerHTML: '', title: '', value: '', dataset: {}, style: {}, addEventListener() {}, removeEventListener() {}, focus() {}, appendChild() {}, querySelectorAll() { return []; }, querySelector() { return makeEl(); } });
  const getEl = (id) => { if (!els.has(id)) els.set(id, makeEl()); return els.get(id); };
  const body = (p) => p.includes('watchlist') ? { watchlist: watchlist || [] } : p.includes('opportunities') ? { opportunities: [] } : p.includes('pipeline') ? [] : {};
  const sb = { console, Date, Promise, Math, JSON, Array, Object, String, Number, RegExp, Set, encodeURIComponent, setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {}, confirm: () => false, alert: () => {}, Auth: { requireAuth() {} }, Sidebar: { init() {} }, Toast: { show() {} },
    escHtml: (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    truncate: (s, k) => String(s == null ? '' : s).slice(0, k),
    API: { BASE: '', headers: () => ({}), get: async (p) => (p === '/api/data' ? sweep : p === '/api/health' ? { sourcesOk: 45, sourcesTotal: 48 } : null), post: async () => ({ ok: true, data: {} }) },
    fetch: async (u) => ({ ok: true, status: 200, json: async () => body(String(u)) }),
    document: { getElementById: getEl, querySelector: () => makeEl(), querySelectorAll: () => [], addEventListener() {}, hidden: false } };
  sb.window = sb; const ctx = vm.createContext(sb);
  vm.runInContext(CODE, ctx, { filename: 'd.js' }); await ctx.loadData(); return els;
}
const XSS = '<script>alert(1)</script><img src=x onerror=alert(2)>';
async function safe(fn, msg) { try { await fn(); } catch (e) { ok(false, `${msg} (threw: ${e && e.message})`); } }
try {
  let els = await render({ correlations: [], bdIntelligence: { counts: {} }, tg: { urgent: [] }, ofacActions: { updates: [{ title: XSS, url: 'javascript:alert(3)', timestamp: 1783000000000 }] }, exportControlActions: { updates: [] } }, []);
  let h = els.get('ofac-actions-list').innerHTML;
  ok(!h.includes('<script>') && !h.includes('<img src=x onerror') && h.includes('&lt;'), 'B1 widget: XSS title escaped (raw tags gone)');
  ok(!/href="javascript:/i.test(h), 'B1 widget: javascript: URL dropped by https guard');
  els = await render({ correlations: [], bdIntelligence: { counts: {} }, tg: { urgent: [{ channel: XSS, text: XSS, date: '2026-01-01' }] } }, []);
  h = els.get('osint-list').innerHTML;
  ok(!h.includes('<script>') && !h.includes('<img src=x onerror') && h.includes('&lt;'), 'B2 telegram: XSS channel/text escaped');
  els = await render({ correlations: [], bdIntelligence: { counts: {} }, tg: { urgent: [{ channel: 'x', text: 'mentions evilcorp today', date: '2026-01-01' }] } }, [{ name: 'evilcorp<img src=x onerror=alert(9)>', entity_type: 'c' }]);
  h = els.get('osint-list').innerHTML + els.get('wl-signal-match').innerHTML;
  ok(!h.includes('onerror=alert(9)>') && !h.includes('<img src=x onerror'), 'B3 watchlist: XSS entity name escaped');
  await safe(async () => { await render({ correlations: [], bdIntelligence: { counts: {} }, tg: { urgent: [{ channel: 'x', text: 'a.*b [t] (g)', date: '2026-01-01' }] } }, [{ name: '.*+[](){}^$\\', entity_type: 'c' }, { name: 'a.*b', entity_type: 'c' }]); ok(true, 'B4 regex-metachar watchlist names → no crash / no ReDoS'); }, 'B4');
  await safe(async () => { const e = await render({ correlations: 'oops', tg: null, bdIntelligence: null, ofacActions: 42, exportControlActions: undefined }, []); ok(e.get('kpi-conflict').textContent !== undefined, 'B5 malformed field types → no crash (Array.isArray guard)'); }, 'B5');
  const big = Array.from({ length: 2000 }, (_, i) => ({ region: 'R' + i, severity: i % 2 ? 'critical' : 'x', signalCount: i, sourceCount: 2, totalScore: i }));
  const bigPosts = Array.from({ length: 2000 }, (_, i) => ({ channel: 'c', text: 'post ' + i, date: '2026-01-01' }));
  els = await render({ correlations: big, tg: { urgent: bigPosts }, bdIntelligence: { counts: { activeTenders: 3 } } }, []);
  ok(els.get('kpi-critical').textContent === 1000 && els.get('correlations-list').innerHTML.length > 0, 'B6 2000+2000 items → rendered (sliced), correct count');
  els = await render({ correlations: [], bdIntelligence: { counts: {} }, tg: { urgent: [] }, ofacActions: { updates: [] }, exportControlActions: { updates: [] } }, []);
  ok(els.get('sanctions-export-block').style.display === 'none', 'B7 empty feeds → widget hidden');
  els = await render(JSON.parse('{"correlations":[],"bdIntelligence":{"counts":{}},"tg":{"urgent":[]},"ofacActions":{"updates":[{"title":"ok","url":"https://x","timestamp":1783000000000,"__proto__":{"polluted":true}}]},"exportControlActions":{"updates":[]}}'), []);
  ok(({}).polluted === undefined, 'B8 no prototype pollution from feed payload');
  // B9 — numeric coercion: string signalCount must not concat / not inject
  els = await render({ correlations: [{ region: 'R', severity: 'critical', signalCount: '5', sourceCount: '3', totalScore: '9' }], bdIntelligence: { counts: {} }, tg: { urgent: [] } }, []);
  ok(els.get('kpi-conflict').textContent === 5 && els.get('correlations-list').innerHTML.includes('5 signals'), 'B9 string numerics coerced (no string-concat, no injection)');
} finally { globalThis.fetch = realFetch; }

console.log(`\n${fail === 0 ? 'PASS' : 'FAIL'} — ${n - fail}/${n} checks`);
process.exit(fail === 0 ? 0 : 1);
