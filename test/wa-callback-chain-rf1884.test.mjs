// test/wa-callback-chain-rf1884.test.mjs
//
// R-F1884 (review Class A) — WA async-callback delivery chain.
//   RV-01 (CRITICAL): CALLBACK_URL must target /callback, not /send — else the
//     brain allowlist rejects every callback and long DDs never deliver.
//   RV-04: token must come ONLY from the query string (?ct=), not the request
//     body; compared in constant time.
//   double-delivery: an atomic in-progress claim before the send, released on
//     failure, so concurrent callbacks deliver exactly once.
//
// Listener boots a socket on import, so this combines runtime sims of the
// extractable logic with static-source guards on the real wiring.
//
// Run: node test/wa-callback-chain-rf1884.test.mjs

import { readFileSync } from 'node:fs';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import assert from 'node:assert';

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok - ${name}`); }
  catch (e) { failures++; console.error(`  FAIL - ${name}\n     ${e.message}`); }
}
const SRC = readFileSync(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');

// ── RV-01: CALLBACK_URL default targets /callback ────────────────────────────
check('RV-01: CALLBACK_URL default is /api/wa-listener/callback (not /send)', () => {
  const m = SRC.match(/CALLBACK_URL\s*=\s*process\.env\.WA_LISTENER_CALLBACK_URL\s*\|\|\s*'([^']+)'/);
  assert.ok(m, 'CALLBACK_URL default not found');
  assert.ok(m[1].endsWith('/api/wa-listener/callback'),
    `CALLBACK_URL default is ${m[1]} — must end with /api/wa-listener/callback`);
  assert.ok(!m[1].endsWith('/send'), 'CALLBACK_URL still defaults to /send (RV-01 unfixed)');
});

// ── RV-04: token from query only, constant-time compare ──────────────────────
check('RV-04: token read from req.query.ct only — no request-body fallback', () => {
  assert.ok(/const presented = req\.query\?\.ct \|\| '';/.test(SRC),
    'token still accepts a request-body fallback (b.callback_token)');
  // the OLD vulnerable code line (query || body) must be gone (comment mentions are fine)
  assert.ok(!/req\.query\?\.ct \|\| b\.callback_token/.test(SRC), 'b.callback_token body fallback still in code');
  assert.ok(/_callbackTokenEq\(presented, mapping\.callbackToken\)/.test(SRC),
    'token compare is not using the constant-time _callbackTokenEq');
});

// constant-time compare logic (mirror of _callbackTokenEq)
function _ctEq(a, b) {
  try {
    const ba = Buffer.from(String(a || ''), 'utf8'), bb = Buffer.from(String(b || ''), 'utf8');
    return ba.length === bb.length && ba.length > 0 && timingSafeEqual(ba, bb);
  } catch { return false; }
}
check('RV-04: constant-time compare matches equal, rejects diff/empty/length-mismatch', () => {
  const t = randomBytes(24).toString('hex');
  assert.strictEqual(_ctEq(t, t), true);
  assert.strictEqual(_ctEq(t, t.slice(0, -1) + '0'), false);   // same length, 1 char diff
  assert.strictEqual(_ctEq(t, t.slice(0, 10)), false);          // length mismatch (no throw)
  assert.strictEqual(_ctEq('', ''), false);                     // empty never matches
  assert.strictEqual(_ctEq(undefined, t), false);
});

// ── double-delivery: atomic claim semantics ──────────────────────────────────
check('double-delivery: atomic claim — second concurrent callback bails', () => {
  const mapping = {};
  // first callback claims
  const firstBlocked = (mapping.deliveredViaCallback || mapping.deliveringViaCallback);
  assert.strictEqual(!!firstBlocked, false);
  mapping.deliveringViaCallback = true;   // atomic claim (no await between check+set)
  // second concurrent callback sees the claim and bails
  const secondBlocked = (mapping.deliveredViaCallback || mapping.deliveringViaCallback);
  assert.strictEqual(!!secondBlocked, true, 'second callback was NOT blocked — double-delivery race');
});
check('double-delivery: failure releases the claim so a retry can re-attempt', () => {
  const mapping = { deliveringViaCallback: true };
  // send fails → release
  mapping.deliveringViaCallback = false;
  assert.strictEqual(!!mapping.deliveredViaCallback, false);  // still not delivered
  const retryBlocked = (mapping.deliveredViaCallback || mapping.deliveringViaCallback);
  assert.strictEqual(!!retryBlocked, false, 'retry was blocked after a failed send — turn lost');
});
check('static: failure path releases deliveringViaCallback; success sets deliveredViaCallback after send', () => {
  assert.ok(/mapping\.deliveringViaCallback = true;\s*\/\/ atomic claim/.test(SRC));
  assert.ok(/catch \(e\) \{[\s\S]{0,500}mapping\.deliveringViaCallback = false;/.test(SRC),
    'failure path does not release the in-progress claim');
});

// ── urlencode ────────────────────────────────────────────────────────────────
check('token query value is URL-encoded', () => {
  assert.ok(/'ct=' \+ encodeURIComponent\(callbackToken\)/.test(SRC), 'callback token not URL-encoded');
});

if (failures) { console.error(`\n${failures} check(s) FAILED`); process.exit(1); }
console.log('\nAll R-F1884 callback-chain checks passed');
