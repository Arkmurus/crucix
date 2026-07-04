// test/dashboard-sanctions-export-widget-rf2416.test.mjs
//
// Capability test for R-F2416 R3b — the dashboard "Sanctions & Export Actions"
// widget. server.mjs re-attaches the now-REAL OFAC + BIS sweep feeds onto
// currentData (data.ofacActions / data.exportControlActions); the dashboard
// renders them with real titles, dates and federalregister.gov links. When the
// feed is honest-empty (source failed, updates []), the whole block is HIDDEN —
// never a fabricated placeholder.
//
// Drives the REAL extracted dashboard JS in a DOM shim.
//
// Run: node test/dashboard-sanctions-export-widget-rf2416.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
const CODE = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).filter(s => s.trim()).pop();

let failures = 0;
function ok(cond, msg) { if (cond) console.log(`  ✓ ${msg}`); else { console.error(`  ✗ ${msg}`); failures++; } }

// static guard
ok(HTML.includes('id="sanctions-export-block"') && HTML.includes('id="ofac-actions-list"') && HTML.includes('id="export-actions-list"'), 'widget elements present in HTML');

async function run(sweep) {
  const els = new Map();
  const makeEl = () => ({ textContent: '', innerHTML: '', title: '', value: '', dataset: {}, style: {},
    addEventListener() {}, removeEventListener() {}, focus() {}, appendChild() {}, querySelectorAll() { return []; }, querySelector() { return makeEl(); } });
  const getEl = (id) => { if (!els.has(id)) els.set(id, makeEl()); return els.get(id); };
  const bodyFor = (p) => p.includes('/api/aria/dd/watchlist') ? { watchlist: [] }
    : p.includes('/api/opportunities') ? { opportunities: [] }
    : p.includes('/api/bd-intelligence/pipeline') ? [] : {};
  const sandbox = {
    console, Date, Promise, Math, JSON, Array, Object, String, Number, RegExp, Set,
    encodeURIComponent, setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
    confirm: () => false, alert: () => {},
    Auth: { requireAuth() {} }, Sidebar: { init() {} }, Toast: { show() {} },
    escHtml: (s) => String(s == null ? '' : s), truncate: (s, n) => String(s == null ? '' : s).slice(0, n),
    API: { BASE: '', headers: () => ({}), get: async (p) => (p === '/api/data' ? sweep : p === '/api/health' ? { sourcesOk: 45, sourcesTotal: 48 } : null), post: async () => ({ ok: true, data: {} }) },
    fetch: async (url) => ({ ok: true, status: 200, json: async () => bodyFor(String(url)) }),
    document: { getElementById: getEl, querySelector: () => makeEl(), querySelectorAll: () => [], addEventListener() {}, hidden: false },
  };
  sandbox.window = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(CODE, ctx, { filename: 'dashboard-inline.js' });
  await ctx.loadData();
  return els;
}

const TS = 1783000000000;
const WITH_DATA = {
  correlations: [], bdIntelligence: { counts: { activeTenders: 0 } }, tg: { urgent: [] }, opportunities: [],
  ofacActions: { status: 'active', updates: [
    { title: '🛡️ Notice of OFAC Sanctions Actions', url: 'https://www.federalregister.gov/documents/abc', timestamp: TS },
  ] },
  exportControlActions: { status: 'active', updates: [
    { title: '🚦 Streamlining Export Controls for Drone Exports', url: 'https://www.federalregister.gov/documents/drone', timestamp: TS },
  ] },
};

console.log('with real feed data:');
const a = await run(WITH_DATA);
ok(a.get('sanctions-export-block').style.display === '', 'block is shown when feeds have data');
const ofacHtml = a.get('ofac-actions-list').innerHTML;
ok(ofacHtml.includes('Notice of OFAC Sanctions Actions'), 'OFAC list shows the real action title');
ok(ofacHtml.includes('federalregister.gov'), 'OFAC item links to the real source');
ok(a.get('export-actions-list').innerHTML.includes('Drone Exports'), 'export list shows the real rule title');

console.log('honest-empty (feeds failed / no updates):');
const b = await run({ correlations: [], bdIntelligence: { counts: {} }, tg: { urgent: [] }, opportunities: [],
  ofacActions: { status: 'error', updates: [] }, exportControlActions: { status: 'error', updates: [] } });
ok(b.get('sanctions-export-block').style.display === 'none', 'block is HIDDEN when both feeds are empty (no fabrication)');

console.log('feeds absent entirely (older sweep):');
const c = await run({ correlations: [], bdIntelligence: { counts: {} }, tg: { urgent: [] }, opportunities: [] });
ok(c.get('sanctions-export-block').style.display === 'none', 'block hidden when fields absent');

console.log(failures === 0 ? '\nPASS' : `\nFAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
