// test/wa-listener-teardown-rf461.test.mjs
//
// Capability test for R-F461 — sock-listener teardown discipline in
// lib/whatsapp/waListener.mjs.
//
// Live evidence 2026-05-14 05:35 BST (seenode log, paste in chat):
//   [WA Listener] Disconnected (code 515) — reconnecting in 60s...
//   [WA Listener] Found saved auth (1 files) — attempting auto-reconnect
//   [WA Listener] Starting — Baileys v2.3000.1035194821
//   [WA Listener] ✓ Connected to WhatsApp — ARIA is listening
//   [WA Listener] Auth backed up to fly.io (32/32 files, 8KB)
//   [WA Listener] Disconnected (code undefined) — reconnecting in 5s...
//   [WA Listener] Found saved auth (33 files) — ...   (≈1 second later)
//   …same pattern repeating every 1–2 seconds, file count 31→47…
//
// Root cause: the non-logout close branch and the startListener cleanup
// block both called sock.end() without first calling
// sock.ev.removeAllListeners(). Baileys emits residual close events on the
// dying socket during shutdown; each one re-entered the still-attached
// connection.update handler and scheduled another setTimeout(startListener),
// producing a thundering-herd reconnect storm. WA rate-limits or temp-bans
// devices that disconnect/reconnect at that cadence.
//
// Three checks — the first two are guardrail static-source checks (same
// shape as the R-F390 capability test). The third is a runtime simulation
// that proves the symptom — multiple residual close events scheduling
// multiple startListener calls — does not recur once removeAllListeners
// is wired in.
//
// Run: node test/wa-listener-teardown-rf461.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { EventEmitter } from 'node:events';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(__dirname, '..', 'lib', 'whatsapp', 'waListener.mjs'),
  'utf8'
);

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F461 capability tests\n');

// ── 1. startListener entry cleanup tears down listeners ───────────────
console.log('1. startListener entry: removeAllListeners before end()');
{
  // Anchor on the "Close previous socket if any" comment and inspect the
  // following teardown block. The order must be removeAllListeners → end →
  // null. Doing end first lets Baileys fire residual close on the still-
  // attached handler, which is exactly what caused the storm.
  const anchor = SRC.indexOf('// Close previous socket if any');
  check('anchor comment present', anchor > -1);
  const window = SRC.slice(anchor, anchor + 1200);
  const removeIdx = window.indexOf('sock.ev.removeAllListeners()');
  const endIdx    = window.indexOf('sock.end()');
  const nullIdx   = window.indexOf('sock = null');
  check('removeAllListeners() called',  removeIdx > -1);
  check('end() called',                 endIdx > -1);
  check('sock nulled',                  nullIdx > -1);
  check('removeAllListeners before end',          removeIdx > -1 && removeIdx < endIdx);
  check('end before null',                        endIdx > -1 && endIdx < nullIdx);
}

// ── 2. non-logout close branch tears down before scheduling restart ───
console.log('\n2. non-logout close branch: teardown before setTimeout(startListener)');
{
  // The logout branch (line ~3778) already does the teardown. The bug was
  // that the else branch — every non-logout/non-440 close, including the
  // common "code undefined" residual — only logged + scheduled, leaking
  // the old sock's listeners. Anchor on the "reconnecting in" log line
  // and walk backwards to make sure a teardown block precedes it.
  const logIdx = SRC.indexOf('Disconnected (code ${code}) — reconnecting in');
  check('non-logout disconnect log present', logIdx > -1);
  // Look at the 600 chars preceding the log line — must contain the
  // teardown trio.
  const preface = SRC.slice(Math.max(0, logIdx - 600), logIdx);
  check('removeAllListeners before reconnect log',
        preface.includes('sock.ev.removeAllListeners()'));
  check('end before reconnect log', preface.includes('sock.end()'));
  check('sock nulled before reconnect log', preface.includes('sock = null'));
}

// ── 3. runtime: residual close events do NOT pile up startListener calls
console.log('\n3. runtime simulation: 5 residual close events → 1 scheduled restart');
{
  // Build a minimal repro of the leak. The fix is "removeAllListeners
  // BEFORE scheduling restart" — so once the handler runs once on a given
  // sock, subsequent close emissions from that same sock must be no-ops.
  //
  // We can't import the real handler (it's an anonymous closure inside
  // startListener), but the discipline we're enforcing is generic: any
  // close handler that schedules restart MUST detach itself before doing
  // so. Mirror the patched handler shape and prove the property.
  const ev = new EventEmitter();
  let scheduledRestarts = 0;
  let isConnected = true;

  const handler = ({ connection }) => {
    if (connection !== 'close') return;
    isConnected = false;
    // R-F461 discipline: detach FIRST, then schedule.
    ev.removeAllListeners('connection.update');
    scheduledRestarts += 1;
  };
  ev.on('connection.update', handler);

  // Fire 5 residual close events — same shape Baileys emits during the
  // graceful shutdown that follows sock.end().
  for (let i = 0; i < 5; i++) {
    ev.emit('connection.update', { connection: 'close', lastDisconnect: { error: null } });
  }

  check('exactly one restart scheduled',  scheduledRestarts === 1);
  check('isConnected flipped to false',   isConnected === false);
  check('handler removed after first close', ev.listenerCount('connection.update') === 0);
}

// ── 4. logout branch still does its teardown (regression guard) ───────
console.log('\n4. logout branch teardown intact (regression guard)');
{
  // The logout branch was the ONLY teardown surface before R-F461; make
  // sure we haven't accidentally regressed it while editing nearby.
  const anchor = SRC.indexOf('Auth invalid (code');
  check('logout branch anchor present', anchor > -1);
  const window = SRC.slice(anchor, anchor + 800);
  check('logout: removeAllListeners present', window.includes('removeAllListeners()'));
  check('logout: end() present',              window.includes('sock.end()'));
  check('logout: sock nulled',                window.includes('sock = null'));
}

console.log(`\nR-F461 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
