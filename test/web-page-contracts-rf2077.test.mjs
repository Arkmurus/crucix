/**
 * R-F2077 — aria-web page↔backend CONTRACT GUARD (anti-drift "anti-virus").
 * ═══════════════════════════════════════════════════════════════════════════
 * The recurring failure class found in the 2026-06-28 4-step DD audit: a page
 * reads a field the backend no longer returns (or calls a path that 404s), and
 * fails SILENTLY — empty tab, wrong count, or a false "clean"/"FAIL" verdict.
 * `node --check` passes (the JS is valid); only a human noticed the wrong number.
 *
 * This guard makes that class LOUD:
 *   PART A (live, anti-drift): probe each critical backend endpoint and assert
 *     it returns HTTP 200 AND every field the page depends on is present. If the
 *     backend renames/drops a field or a path 404s, THIS TEST FAILS. Skips
 *     gracefully when no token / brain unreachable (so offline CI doesn't break).
 *   PART B (static, deterministic): lock each of the 7 audit fixes against
 *     regression by asserting the corrected contract in the page source.
 *
 * Run: node --test test/web-page-contracts-rf2077.test.mjs
 */
import fs from 'fs';
import path from 'path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'), '..');
function read(rel) { return fs.readFileSync(path.join(ROOT, rel), 'utf-8'); }

// ── token + brain URL from .env (best-effort) ──────────────────────────────
function envVal(key) {
  try {
    for (const line of read('.env').split(/\r?\n/)) {
      if (line.startsWith(key + '=')) return line.slice(key.length + 1).trim().replace(/^["']|["']$/g, '');
    }
  } catch {}
  return process.env[key] || '';
}
const TOKEN = envVal('ARIA_INTERNAL_TOKEN');
const BRAIN = (envVal('ARIA_SERVICE_URL') || 'https://aria-intel.fly.dev').replace(/\/$/, '');

function hasPath(obj, dotted) {
  return dotted.split('.').reduce((o, k) => (o && typeof o === 'object' && k in o) ? o[k] : undefined, obj) !== undefined;
}

async function probe(p) {
  const r = await fetch(BRAIN + p, { headers: { Authorization: `Bearer ${TOKEN}` }, signal: AbortSignal.timeout(25000) });
  const body = await r.json();
  return { status: r.status, body };
}

// The page→backend contract. Each `fields` entry is a field the live page READS;
// if the backend stops returning it, the page breaks silently — so we assert it.
const CONTRACTS = [
  { page: 'dashboard.html / opportunities.html', path: '/api/aria/opportunities',
    fields: ['count', 'opportunities', 'market_signal_count'] },
  { page: 'dashboard.html / watchlist.html', path: '/api/aria/dd/watchlist',
    fields: ['watchlist'], itemOf: 'watchlist', itemFields: ['last_risk'] },
  { page: 'news.html', path: '/api/aria/news/stats',
    fields: ['total_sources', 'categories', 'recent_articles'] },
  { page: 'news.html', path: '/api/aria/news/recent?limit=5',
    fields: ['articles'] },
  { page: 'explorer.html', path: '/api/aria/security/counter-intel/scan?entity=ContractGuardProbe',
    fields: ['n_signals', 'narrative', 'patterns'] },
  { page: 'vault.html', path: '/api/aria/vault?limit=1',
    fields: ['stats.total', 'stats.by_status'] },
  { page: 'dd-reports.html / dashboard.html', path: '/api/aria/dd/reports?limit=1',
    fields: ['reports'] },
];

describe('R-F2077 PART A — live backend contract (anti-drift)', () => {
  for (const c of CONTRACTS) {
    it(`${c.path} returns the fields ${c.page} reads`, async (t) => {
      if (!TOKEN) { t.skip('no ARIA_INTERNAL_TOKEN — live probe skipped'); return; }
      let res;
      try { res = await probe(c.path); }
      catch (e) { t.skip(`brain unreachable (${e.message}) — live probe skipped`); return; }
      assert.equal(res.status, 200, `${c.path} must be a real endpoint (got HTTP ${res.status} — wrong path / 404 class)`);
      for (const f of c.fields) {
        assert.ok(hasPath(res.body, f), `${c.path} must return "${f}" (page ${c.page} reads it); got keys: ${Object.keys(res.body)}`);
      }
      if (c.itemOf && Array.isArray(res.body[c.itemOf]) && res.body[c.itemOf].length) {
        const item = res.body[c.itemOf][0];
        for (const f of c.itemFields) {
          assert.ok(f in item, `${c.path} items must carry "${f}" (got: ${Object.keys(item)})`);
        }
      }
    });
  }
});

describe('R-F2077 PART B — static regression locks (the 7 audit fixes)', () => {
  it('#1 DD-reports IDOR: server.mjs has an explicit user_id-pinned /dd/reports route', () => {
    const s = read('server.mjs');
    assert.ok(/app\.get\('\/api\/aria\/dd\/reports', requireAuth/.test(s),
      'explicit app.get(/api/aria/dd/reports) route must exist (not catch-all)');
    // params.set must appear within the route body (right after the route start).
    assert.ok(/app\.get\('\/api\/aria\/dd\/reports'[\s\S]{0,500}params\.set\('user_id', userId\)/.test(s),
      'the /dd/reports route must pin user_id from the JWT');
  });
  it('#2 explorer counter-intel reads n_signals (not the dead data.ok/composite_score)', () => {
    const s = read('public/explorer.html');
    assert.ok(/data\.n_signals/.test(s), 'renderCounterIntel must read n_signals');
    assert.ok(/counterSignals/.test(s) && /counterRan/.test(s), 'overview must use counterSignals/counterRan');
    assert.ok(!/data\.composite_score/.test(s), 'must not read the non-existent composite_score');
    assert.ok(/data\.corporate/.test(s), 'renderNetwork must read the real `corporate` array');
  });
  it('#3 dashboard Active Deals calls the real pipeline route, not the 404 path', () => {
    const s = read('public/dashboard.html');
    // Check the actual CALL (authed('...')), not comments that may mention the old path.
    assert.ok(!/authed\(\s*['"]\/api\/aria\/bd-intelligence\/pipeline/.test(s),
      'must NOT call the 404 path /api/aria/bd-intelligence/pipeline');
    assert.ok(/authed\(\s*['"]\/api\/bd-intelligence\/pipeline['"]/.test(s),
      'must call the real Node route /api/bd-intelligence/pipeline');
  });
  it('#4 vls-chain treats a 200 no-chain (error+empty results) as empty-state', () => {
    const s = read('public/vls-chain.html');
    assert.ok(/data\.error\s*&&\s*noEntries/.test(s), 'must handle the {verified:false,error} 200 response as empty-state');
  });
  it('#5 news shows the retained recent_articles count, not the 100-row page length', () => {
    const s = read('public/news.html');
    assert.ok(/stats\.recent_articles/.test(s), 'kpi-total must read stats.recent_articles');
  });
  it('#6 vault renders summary tiles FROM by_status, not a hardcoded vocabulary (R-F2934 supersedes)', () => {
    const s = read('public/vault.html');
    // R-F2934: the original lock asserted `bs.needs_operator` — a specific hardcoded
    // read that was itself the drift the page kept regressing on. The stronger contract
    // is that the tiles are DERIVED from whatever by_status contains, so a new status
    // cannot go invisible. Assert the derivation, and that the reconciliation guard
    // exists — both are what actually stop the regression.
    assert.ok(s.includes('STATUS_CONFIG'), 'vault must still know the real status labels/icons');
    assert.ok(/renderVaultSummary/.test(s), 'summary must be rendered from data, not fixed IDs');
    assert.ok(/by_status/.test(s) && /reduce\(/.test(s),
      'the tiles must be built from by_status with a reconciliation sum');
    // The exact drift-prone hardcoded IDs must be GONE (see the R-F2934 contract test).
    for (const id of ['vault-open-api', 'vault-declined', 'vault-needs-operator']) {
      assert.ok(!s.includes(`id="${id}"`), `#${id} hardcoded tile must be removed`);
    }
  });
  it('#7 watchlist surfaces last_risk and reads the real added_at timestamp', () => {
    const s = read('public/watchlist.html');
    assert.ok(/last_risk/.test(s), 'watchlist must surface last_risk');
    assert.ok(/added_at/.test(s), 'watchlist must read added_at for Last Checked');
  });
  it('#8 opportunities SINGLE SOURCE OF TRUTH: dashboard KPI + page read the same endpoint (R-F2079)', () => {
    const dash = read('public/dashboard.html');
    const page = read('public/opportunities.html');
    // The opportunities page is the canonical view; the dashboard KPI must read
    // the SAME engine so the two never show contradictory counts.
    assert.ok(/['"]\/api\/opportunities['"]/.test(page), 'opportunities.html must read /api/opportunities');
    assert.ok(/authed\(\s*['"]\/api\/opportunities['"]\s*\)/.test(dash),
      'dashboard Opportunities KPI must read the same /api/opportunities engine');
    assert.ok(!/authed\(\s*['"]\/api\/aria\/opportunities['"]/.test(dash),
      'dashboard must NOT read the divergent brain /api/aria/opportunities for the headline KPI');
  });
  it('#9 dashboard Golden Intel reads promoted decision signals, not raw news only', () => {
    const dash = read('public/dashboard.html');
    assert.ok(/id="golden-intel-card"/.test(dash), 'dashboard must render the Golden Intel card');
    assert.ok(/\/api\/aria\/intel\/signals\/recent\?limit=20/.test(dash),
      'dashboard must read promoted intel signals');
    assert.ok(/why_it_matters/.test(dash) && /recommended_action/.test(dash),
      'Golden Intel must show impact and action fields');
  });
});
