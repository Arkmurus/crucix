// test/wa-listener-440-teardown-rf1634.test.mjs
//
// Capability/guard test for R-F1634 — the 440 (connectionReplaced) reconnect
// storm in the CANONICAL WA listener.
//
// Root cause (verified live 2026-06-17): startListener() did
// `sock = makeWASocket(...)` WITHOUT closing the prior socket, so each
// reconnect spawned a new socket that REPLACED the old at WhatsApp → the old
// emitted 440 → another reconnect → 440 → a self-perpetuating storm (listener
// effectively dead; brain fetches timed out as the loop churned).
//
// R-F461 added this exact teardown — but to lib/whatsapp/waListener.mjs (the
// LEGACY file). The Dockerfile.wa entry point is
// services/wa-listener/aria_wa_listener.mjs, which never got it → the storm
// recurred. This test GUARDS THE CANONICAL FILE so the fix can't regress to
// the wrong-file mistake, and asserts the teardown precedes socket creation
// AND that the streak no longer resets unconditionally on 'open'.
//
// Run: node test/wa-listener-440-teardown-rf1634.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(__dirname, '..', 'services', 'wa-listener', 'aria_wa_listener.mjs'),
  'utf8',
);

let failures = 0;
function check(name, cond) {
  console.log(`${cond ? 'ok  ' : 'FAIL'} - ${name}`);
  if (!cond) failures++;
}

// 1. startListener tears down the old socket BEFORE creating a new one.
const startIdx = SRC.indexOf('async function startListener()');
const makeIdx  = SRC.indexOf('sock = makeWASocket(', startIdx);
const teardownWindow = SRC.slice(startIdx, makeIdx);
check('startListener found', startIdx > -1);
check('makeWASocket call found', makeIdx > -1);
check('removeAllListeners() before makeWASocket', teardownWindow.includes('removeAllListeners'));
check('socket close/end before makeWASocket',
  teardownWindow.includes('ws?.close') || teardownWindow.includes('sock.end'));
check('old socket nulled before recreate', teardownWindow.includes('sock = null'));

// 2. The disconnect streak is NOT reset unconditionally on 'open' (the flap-storm
//    bug). A delayed/identity-checked reset must gate it so a flap still climbs
//    the streak toward the self-heal exit.
const openIdx = SRC.indexOf("connection === 'open'");
const closeIdx = SRC.indexOf("connection === 'close'", openIdx);
const openWindow = SRC.slice(openIdx, closeIdx);
check("'open' block found", openIdx > -1 && closeIdx > openIdx);
const _stIdx = openWindow.indexOf('setTimeout(');
const _resetIdx = openWindow.indexOf('_disconnectStreak = 0');
check('streak reset is delay-gated (setTimeout present in open block)', _stIdx > -1);
check('streak reset lives INSIDE the delayed callback (after setTimeout), not bare',
  _stIdx > -1 && _resetIdx > _stIdx);

console.log(failures === 0 ? '\nR-F1634 tests: PASS' : `\nR-F1634 tests: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
