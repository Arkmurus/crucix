// R-F2934 — the vault page must render FROM the backend's data, not a hardcoded copy
// of its vocabulary. This test is the anti-regression mechanism, not just a checker.
//
// WHY it exists: the page has drifted from the backend's status vocabulary TWICE.
// R-F2076 fixed the table icons after 42 of 43 rows fell to a default because
// STATUS_CONFIG only knew a stale set. That patch left the SUMMARY TILES hardcoded to
// yet another fixed set, so `deferred` (had data) had no tile and `open_api` (no data)
// held a permanent-zero tile, and the tiles no longer summed to Total. Same class,
// one layer over. Adding the missing tiles by hand would just set up the third
// regression. Deriving the tiles from the data removes the class — and this test
// keeps it removed.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const HTML = readFileSync(new URL('../public/vault.html', import.meta.url), 'utf8');
const SCRIPT = HTML.split('<script>').pop().split('</script>')[0];

// Extract a top-level `function name(...) { ... }` by brace-matching.
function fn(name) {
  const start = SCRIPT.indexOf('function ' + name);
  assert.ok(start > -1, `missing function ${name}`);
  let depth = 0, i = SCRIPT.indexOf('{', start);
  const open = i;
  do { if (SCRIPT[i] === '{') depth++; else if (SCRIPT[i] === '}') depth--; i++; } while (depth > 0 && i < SCRIPT.length);
  return SCRIPT.slice(start, i);
}

// A DOM + helper shim just rich enough to run the render functions.
function harness() {
  const els = {};
  const doc = { getElementById: id => (els[id] = els[id] || { innerHTML: '', textContent: '', title: '', style: {} }) };
  const escHtml = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const STATUS_CONFIG = {
    needs_operator: { icon: 'bi-hourglass-split', color: '#c2410c', label: 'Needs Operator' },
    open_api: { icon: 'bi-plug', color: '#1d4ed8', label: 'Open API' },
    verified: { icon: 'bi-check-circle', color: '#15803d', label: 'Verified' },
    declined: { icon: 'bi-x-circle', color: '#b91c1c', label: 'Declined' },
    deferred: { icon: 'bi-clock-history', color: '#6b6862', label: 'Deferred' },
  };
  const src = [
    'const VAULT_STATUS_ORDER = ' + JSON.stringify(['verified', 'needs_operator', 'open_api', 'deferred', 'declined']) + ';',
    'const EXPLORE_STATUS_LABEL = ' + JSON.stringify({
      unprobed: 'not yet examined', credentials_required: 'needs credentials',
      no_api: 'no machine API', third_party_only: 'third-party proxy only',
      bulk_only: 'bulk data only (no per-entity lookup)',
    }) + ';',
    fn('vaultTile'), fn('renderVaultSummary'), fn('renderExplorationBreakdown'),
    'globalThis.__rvs = renderVaultSummary; globalThis.__reb = renderExplorationBreakdown;',
  ].join('\n');
  const run = new Function('document', 'escHtml', 'STATUS_CONFIG', src + '\nreturn {rvs: __rvs, reb: __reb};');
  const api = run(doc, escHtml, STATUS_CONFIG);
  return { els, ...api };
}

// ── tiles derive from the data ─────────────────────────────────────────────

test('R-F2934: a status present in the data ALWAYS gets a tile', () => {
  const h = harness();
  h.rvs({ total: 22, by_status: { verified: 2, needs_operator: 15, declined: 4, deferred: 1 }, stale_unverified: 0 });
  const html = h.els['vault-summary'].innerHTML;
  // deferred had data but no hardcoded tile before — the exact regression.
  assert.match(html, /Deferred/);
  assert.match(html, /Verified/);
  assert.match(html, /Needs Operator/);
  assert.match(html, /Declined/);
});

test('R-F2934: a status with NO data gets NO tile (no permanent-zero phantom)', () => {
  const h = harness();
  h.rvs({ total: 22, by_status: { verified: 2, needs_operator: 15, declined: 4, deferred: 1 }, stale_unverified: 0 });
  // open_api is not in by_status, so it must not appear as a 0 tile.
  assert.doesNotMatch(h.els['vault-summary'].innerHTML, /Open API/);
});

test('R-F2934: an UNKNOWN status the backend adds still gets a tile', () => {
  const h = harness();
  h.rvs({ total: 5, by_status: { verified: 2, quarantined: 3 }, stale_unverified: 0 });
  const html = h.els['vault-summary'].innerHTML;
  // A vocabulary the UI has never seen must not vanish — that was the R-F2076 bug.
  assert.match(html, /Quarantined/, 'an unknown status was dropped instead of shown');
});

// ── the reconciliation invariant, made visible ─────────────────────────────

test('R-F2934: when tiles sum to Total, the page says so', () => {
  const h = harness();
  h.rvs({ total: 22, by_status: { verified: 2, needs_operator: 15, declined: 4, deferred: 1 }, stale_unverified: 0 });
  assert.match(h.els['vault-recon'].innerHTML, /reconcile/);
  assert.doesNotMatch(h.els['vault-recon'].innerHTML, /exclamation-triangle/);
});

test('R-F2934: when tiles do NOT sum to Total, the page SHOWS the mismatch', () => {
  const h = harness();
  h.rvs({ total: 99, by_status: { verified: 2, needs_operator: 15 }, stale_unverified: 0 });
  const recon = h.els['vault-recon'].innerHTML;
  assert.match(recon, /exclamation-triangle-fill/, 'a non-reconciling total was hidden');
  assert.match(recon, /17.*99|sum to 17/);   // 2+15 = 17 vs 99
});

// ── exploration reasons are surfaced, not just counted ─────────────────────

test('R-F2934: the explored-but-uncovered breakdown shows WHY, not a bare count', () => {
  const h = harness();
  h.reb({ a: { status: 'unprobed' }, b: { status: 'no_api' }, c: { status: 'credentials_required' }, d: { status: 'no_api' } });
  const note = h.els['coverage-explore-note'].innerHTML;
  assert.match(note, /no machine API/);
  assert.match(note, /needs credentials/);
  assert.match(note, /not yet examined/);
  assert.match(note, /not ruled out/, 'unprobed must be distinguished from ruled-out');
});

// ── the page no longer carries the hardcoded per-status element IDs ─────────

test('R-F2934: the drift-prone hardcoded status tile IDs are gone', () => {
  for (const id of ['vault-verified', 'vault-needs-operator', 'vault-open-api', 'vault-declined']) {
    assert.doesNotMatch(HTML, new RegExp('id="' + id + '"'),
      `#${id} is a hardcoded per-status tile — reintroducing it reopens the drift class`);
  }
});

test('R-F2934: the Explored tile reads PROBED, not the manual_only total', () => {
  assert.match(SCRIPT, /cov-explored[\s\S]{0,120}sum\.probed/,
    'the Explored tile must show probed count, not manual_only (which counts 44 never-examined)');
  assert.doesNotMatch(SCRIPT, /cov-explored'\)\.textContent = sum\.manual_only/);
});
