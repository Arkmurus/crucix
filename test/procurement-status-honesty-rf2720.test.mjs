// R-F2720 — Codex source-health audit #11: procurement_portals classified every market
// with `method !== 'none' ? 'ok' : 'failed'`, so an EXCEPTION (method='error') counted as
// 'ok', and a real method that returned ZERO items counted as 'ok' too (yield-blind).
// Markets that didn't resolve before the outer time budget also silently disappeared.
//
// This drives the exported classifier the module now uses + asserts the unresolved-market
// visibility. (procurement_tenders is intentionally NOT changed here: its sub-sources can't
// distinguish a 500-that-returned-[] from a genuine empty, so flipping 0→empty there would
// HIDE errors — that needs the deeper source contract.)

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const { classifyMarketResult } = await import('../apis/sources/procurement_portals.mjs');

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '..', 'apis', 'sources', 'procurement_portals.mjs'), 'utf-8');

describe('R-F2720 procurement market status honesty', () => {
  it('an ERROR is failed, not ok (the core bug: an exception was counted as success)', () => {
    assert.equal(classifyMarketResult('error', 0), 'failed');
    assert.equal(classifyMarketResult('error', 5), 'failed'); // error dominates defensively
  });

  it('no method produced anything → failed', () => {
    assert.equal(classifyMarketResult('none', 0), 'failed');
  });

  it('a real method with data → ok', () => {
    assert.equal(classifyMarketResult('portal_rss', 3), 'ok');
    assert.equal(classifyMarketResult('google_site', 1), 'ok');
  });

  it('a real method with ZERO items → empty (successful-empty: not ok, not failed)', () => {
    assert.equal(classifyMarketResult('portal_rss', 0), 'empty');
    assert.equal(classifyMarketResult('google_country', 0), 'empty');
  });

  it('STRUCTURAL: unresolved markets are named (not silently dropped); yield-blind line gone', () => {
    assert.match(SRC, /sourceStatus\[m\.name\] = 'unresolved'/, 'unresolved markets must be surfaced');
    // the yield-blind ASSIGNMENT is gone (a historical mention survives only in a comment)
    assert.doesNotMatch(SRC, /sourceStatus\[market\.name\] = method !== 'none'/, 'the yield-blind assignment must be gone');
    assert.match(SRC, /sourceStatus\[market\.name\] = classifyMarketResult\(method, items\.length\)/, 'the classifier must be wired at the call site');
  });
});
