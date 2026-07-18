// R-F2722 — Codex source-health audit #11: procurement_tenders classified `fulfilled && 0
// items` as 'failed', mislabelling a legitimately-empty result as a failure. But it could NOT
// safely be relabelled 'empty', because withTimeout + the sub-source fetches SWALLOWED errors
// (a 500 that returned [] looked identical to a genuine empty) — so calling it 'empty' would
// HIDE the 500 (a Codex #5 band-aid).
//
// The fix is a fetch-status CONTRACT: withTimeout tags the resolved array with _fetchStatus
// ('ok'/'timeout'/'error'), and every fetch THROWS on total failure (genuine empty stays 'ok'
// with 0 items). Then error/empty/timeout/data are all distinguishable. This drives the real
// exported classifier across every path + structurally asserts the throw-on-failure wiring.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const { classifyTenderSource, fetchStatusOf } = await import('../apis/sources/procurement_tenders.mjs');

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '..', 'apis', 'sources', 'procurement_tenders.mjs'), 'utf-8');

// mirror withTimeout's tagging: a real Array carrying a non-enumerable _fetchStatus
function tagged(items, status) {
  const a = [...items];
  if (status) Object.defineProperty(a, '_fetchStatus', { value: status, enumerable: false });
  return a;
}
const fulfilled = (value) => ({ status: 'fulfilled', value });

describe('R-F2722 tenders fetch-status contract', () => {
  it('data → ok', () => {
    assert.equal(classifyTenderSource(fulfilled(tagged([{ title: 't' }], 'ok'))), 'ok');
  });

  it('a GENUINE empty → empty, NOT failed (Codex #11: zero tenders can be legitimate)', () => {
    assert.equal(classifyTenderSource(fulfilled(tagged([], 'ok'))), 'empty');
  });

  it('an ERROR → failed, NOT empty (no band-aid: a 500 must not hide as empty)', () => {
    assert.equal(classifyTenderSource(fulfilled(tagged([], 'error'))), 'failed');
  });

  it('a TIMEOUT → timeout (distinct state)', () => {
    assert.equal(classifyTenderSource(fulfilled(tagged([], 'timeout'))), 'timeout');
  });

  it('a rejected settle → failed', () => {
    assert.equal(classifyTenderSource({ status: 'rejected', reason: new Error('x') }), 'failed');
  });

  it('fetchStatusOf reads the tag, defaults to ok for an untagged array', () => {
    assert.equal(fetchStatusOf(tagged([], 'error')), 'error');
    assert.equal(fetchStatusOf(tagged([], 'timeout')), 'timeout');
    assert.equal(fetchStatusOf([]), 'ok');
    assert.equal(fetchStatusOf(undefined), 'ok');
  });

  it('STRUCTURAL: withTimeout tags outcomes + fetches THROW on total failure (not swallow to [])', () => {
    assert.match(SRC, /_tagStatus\(\[\], 'timeout'\)/, 'withTimeout must tag timeouts');
    assert.match(SRC, /_tagStatus\(\[\], 'error'\)/, 'withTimeout must tag fetch throws as error');
    // the clean-catch fetches re-throw instead of returning []
    assert.ok((SRC.match(/throw e;  \/\/ R-F2722/g) || []).length >= 3, 'clean-catch fetches must re-throw');
    // the multi-attempt fetches throw only when EVERY attempt errored
    assert.match(SRC, /failures > 0 && failures === attempts\) throw/, 'DSCA/Africa: throw only if all attempts errored');
    assert.match(SRC, /allFeedsErrored && gnErrored\) throw/, 'DefenceWeb: throw only if all feeds + fallback errored');
    // healthy set now includes 'empty' (safe — errors are 'failed'), excludes 'failed'/'timeout'
    assert.match(SRC, /HEALTHY_STATES = new Set\(\['ok', 'empty', 'disabled_no_key'\]\)/);
  });
});
