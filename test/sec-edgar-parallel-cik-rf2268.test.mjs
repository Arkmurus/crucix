// R-F2268 — SecEdgar's watched-company fetch looped 6 CIKs SEQUENTIALLY at 8s
// each (up to 48s), which alone blew the 30s SOURCE_TIMEOUT_MS parent cap in
// briefing.mjs → SecEdgar timed out at ~30s every sweep and contributed zero
// data to the /api/source-health panel. The loop is now Promise.allSettled over
// the CIKs (concurrent). This test stubs global.fetch to prove the CIK requests
// now overlap in time (max concurrency > 1), driving the real briefing() path.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SEC_PATH = resolve(__dirname, '..', 'apis', 'sources', 'sec_edgar.mjs');
const BRIEFING_PATH = resolve(__dirname, '..', 'apis', 'briefing.mjs');

test('R-F2268: SecEdgar fetches the 6 watched CIKs concurrently, not sequentially', async () => {
  const realFetch = global.fetch;
  let inflight = 0, maxInflight = 0, cikCalls = 0;

  global.fetch = async (url) => {
    const u = String(url);
    if (u.includes('data.sec.gov/submissions/CIK')) {
      cikCalls++;
      inflight++;
      maxInflight = Math.max(maxInflight, inflight);
      await new Promise(r => setTimeout(r, 80)); // simulate a slow SEC response
      inflight--;
      return { ok: true, status: 200, json: async () => ({ filings: { recent: {} } }) };
    }
    // RSS / rss2json legs → return quickly, empty
    return { ok: true, status: 200, text: async () => '', json: async () => ({ items: [] }) };
  };

  try {
    const { briefing } = await import('../apis/sources/sec_edgar.mjs');
    await briefing();
    assert.ok(cikCalls >= 2, `expected ≥2 CIK fetches, got ${cikCalls}`);
    // Sequential would keep maxInflight === 1; concurrent overlaps them.
    assert.ok(maxInflight >= 2, `CIK fetches did not overlap (maxInflight=${maxInflight}) — still sequential`);
  } finally {
    global.fetch = realFetch;
  }
});

test('R-F2268: the sequential await-in-for-loop is gone from the source', () => {
  const src = readFileSync(SEC_PATH, 'utf-8');
  assert.ok(src.includes('Promise.allSettled(ciks.map'), 'CIK fetch should use Promise.allSettled(ciks.map(...))');
  assert.ok(!/for\s*\(const cik of ciks\)/.test(src), 'the sequential `for (const cik of ciks)` loop must be gone');
});

test('R-F2268: ProcurementPortals & SecEdgar have parent-timeout overrides', () => {
  const src = readFileSync(BRIEFING_PATH, 'utf-8');
  const start = src.indexOf('SOURCE_TIMEOUT_OVERRIDES');
  const table = src.slice(start, src.indexOf('};', start) + 2);
  // ProcurementPortals must exceed its own 60s internal cap so partial-return can fire.
  assert.match(table, /ProcurementPortals:\s*6[0-9]_?[0-9]{3}/, 'ProcurementPortals override must be >60s');
  assert.match(table, /SecEdgar:\s*\d{2}_?\d{3}/, 'SecEdgar override must be present');
});
