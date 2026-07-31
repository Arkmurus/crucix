// test/wa-integrity-rf1870.test.mjs
//
// Capability test for R-F1870 (audit DD-15/18/19/24/27) — WhatsApp listener
// integrity hardening. aria_wa_listener.mjs boots a WhatsApp socket on import,
// so (like the other listener tests) this combines runtime simulations of the
// exact logic with static-source guards that lock the real wiring.
//
// Run: node test/wa-integrity-rf1870.test.mjs

import { readFileSync } from 'node:fs';
import assert from 'node:assert';

let failures = 0;
async function check(name, fn) {
  try { await fn(); console.log(`  ok - ${name}`); }
  catch (e) { failures++; console.error(`  FAIL - ${name}\n     ${e.message}`); }
}

const SRC = readFileSync(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');

// ── DD-15 runtime: capped collector throws past the cap, returns under it ────
// Mirror of collectMediaBuffer (the static guard below proves the real code
// matches this shape).
async function collectMediaBuffer(stream, hardCap) {
  if (Buffer.isBuffer(stream)) {
    if (stream.length > hardCap) throw new Error('cap');
    return stream;
  }
  const chunks = []; let total = 0;
  for await (const c of stream) { total += c.length; if (total > hardCap) throw new Error('cap'); chunks.push(c); }
  return Buffer.concat(chunks);
}

await check('DD-15: oversized media stream is rejected before OOM', async () => {
  async function* huge() { for (let i = 0; i < 100; i++) yield Buffer.alloc(1024); } // 100KB
  await assert.rejects(() => collectMediaBuffer(huge(), 10 * 1024), /cap/); // 10KB cap
});

await check('DD-15: in-cap media stream concatenates correctly', async () => {
  async function* small() { yield Buffer.from('ab'); yield Buffer.from('cd'); }
  const buf = await collectMediaBuffer(small(), 1024);
  assert.strictEqual(buf.toString(), 'abcd');
});

await check('DD-15 static: all 3 media sites use collectMediaBuffer + helper exists', () => {
  assert.ok(/async function collectMediaBuffer\(/.test(SRC), 'collectMediaBuffer helper missing');
  const uses = (SRC.match(/await collectMediaBuffer\(stream\)/g) || []).length;
  assert.strictEqual(uses, 3, `expected 3 media sites using collectMediaBuffer, found ${uses}`);
  assert.ok(!/Buffer\.isBuffer\(stream\) \? stream : Buffer\.concat\(await/.test(SRC),
    'old unbounded Buffer.concat pattern still present');
});

// ── DD-18 runtime: token mismatch is rejected, match is accepted ─────────────
await check('DD-18: callback token mismatch rejected, match accepted', () => {
  const mapping = { callbackToken: 'secret123' };
  const verify = (presented) => !mapping.callbackToken || presented === mapping.callbackToken;
  assert.strictEqual(verify('wrong'), false);
  assert.strictEqual(verify('secret123'), true);
  // backward-compat: a legacy mapping with no token is not blocked
  assert.strictEqual((m => !m.callbackToken || 'x' === m.callbackToken)({}), true);
});

await check('DD-18 static: token generated, appended to callback_url, stored, verified', () => {
  assert.ok(/const callbackToken = randomBytes\(24\)\.toString\('hex'\)/.test(SRC), 'token not generated');
  assert.ok(/'ct=' \+ (encodeURIComponent\()?callbackToken/.test(SRC), 'token not appended to callback_url');
  assert.ok(/deliveredViaCallback: false, callbackToken/.test(SRC), 'token not stored in mapping');
  assert.ok(/if \(mapping\.callbackToken\)\s*\{/.test(SRC) && /invalid callback token/.test(SRC),
    'token not verified in callback handler');
});

// ── DD-24 static: deliveredViaCallback set AFTER the send loop ───────────────
await check('DD-24: deliveredViaCallback set only after the send completes', () => {
  // The flag assignment must come AFTER the send loop, inside the try.
  // R-F1930/R-F2069: the raw sock.sendMessage was replaced by _sendChunkWithRetry
  // (re-resolves the socket per retry so a mid-delivery reconnect doesn't drop the
  // result) — the DD-24 ordering property (flag set only after the loop) is unchanged.
  const sendIdx = SRC.indexOf('await _sendChunkWithRetry(chatId, { text: chunks[i] }, _resolveDsock)');
  const flagIdx = SRC.indexOf('mapping.deliveredViaCallback = true', sendIdx);
  assert.ok(sendIdx >= 0 && flagIdx > sendIdx, 'deliveredViaCallback is not set after the send loop');
  // And it must NOT be set in the pre-send guard region anymore. R-F1884 renamed
  // the double-delivery guard reason to 'already_delivered_or_in_progress'.
  const guardRegion = SRC.slice(SRC.indexOf("already_delivered_or_in_progress"), sendIdx);
  assert.ok(!/mapping\.deliveredViaCallback = true/.test(guardRegion), 'flag still set before send');
});

// ── DD-19 static: auto-response keyed on sender, not group chatId ────────────
await check('DD-19: auto-response session namespaced by senderJid, not chatId', () => {
  assert.ok(/askARIA\(prompt, `auto_\$\{senderJid\}`/.test(SRC), 'auto-response not keyed on senderJid');
  assert.ok(!/askARIA\(prompt, `auto_\$\{chatId\}`/.test(SRC), 'auto-response still keyed on group chatId');
});

// ── DD-27 static: auto-response gated by the sender allow-list ───────────────
// R-F3586 — assert the PROPERTY, not the argument list. This pinned the exact
// call `_waSenderAllowed(senderJid)`, so passing the full message for LID-aware
// identity matching failed it while the gating it guards got STRICTER. The rule
// is "the auto-response is gated by the sender allow-list", however the sender
// is identified.
await check('DD-27: auto-response gated by the sender allow-list', () => {
  assert.ok(/trigger\.triggered && _waSenderAllowed\(senderJid(,\s*msg)?\) &&/.test(SRC),
    'auto-response not gated by the sender allow-list');
});

if (failures) { console.error(`\n${failures} check(s) FAILED`); process.exit(1); }
console.log('\nAll R-F1870 WA-integrity checks passed');
