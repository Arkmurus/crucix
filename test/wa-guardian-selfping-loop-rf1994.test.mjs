// test/wa-guardian-selfping-loop-rf1994.test.mjs
//
// R-F1994 — Guardian check-in self-ping was re-ingested as a new command.
//
// Live repro 2026-06-27: operator armed "check on me in one minute…". ARIA's
// stage-1 self-ping ("⏰ ARIA safety check-in — are you safe? … (Your note:
// check on me in one minute if I am safe)") is sent server-side via the brain's
// /api/wa-listener/send. On a linked account that send echoes back as `fromMe`;
// its id was NOT tracked, so it passed the loop guard, passed the mention gate
// (it contains "ARIA"), and the ECHOED note re-matched the arm regex → a new
// check-in armed every cycle. Worse: the echo's sender is ARIA's own jid, so the
// spurious check-in keyed under ARIA's (empty) circle → stage-2 reported
// "your trusted circle is empty, so I couldn't alert anyone."
//
// Fix (two layers):
//   1. /send registers each sent message id via _markAriaSent → id-based guard.
//   2. _isAriaOwnGuardianEcho() drops ARIA's own guardian templates (fromMe).
//
// Run: node test/wa-guardian-selfping-loop-rf1994.test.mjs

import { readFileSync } from 'node:fs';
import assert from 'node:assert';

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok - ${name}`); }
  catch (e) { failures++; console.error(`  FAIL - ${name}\n     ${e.message}`); }
}

const SRC = readFileSync(new URL('../services/wa-listener/aria_wa_listener.mjs', import.meta.url), 'utf8');

// Mirror of the shipped helper (same regex source).
function _isAriaOwnGuardianEcho(text) {
  return /(ARIA safety check-in|Check-in armed for|Check-in active|Glad you'?re safe|SOS sent|Guardian PAUSED|Guardian resumed)/i.test(text || '');
}

// The exact self-ping checkin.py emits (stage 1), with the note echoed in.
const SELF_PING =
  '⏰ ARIA safety check-in — are you safe? Reply "all clear" to stand me down.\n'
  + '(Your note: check on me in one minute if I am safe)\n'
  + "If I don't hear back in ~2 min I'll alert your trusted circle.";
const ARM_CONFIRM = '🛡️ Check-in armed for 1 min. At the deadline I\'ll message you…';
const CLEAR_CONFIRM = '✅ Glad you\'re safe — check-in cleared.';

// ── the bug: ARIA's own self-ping is recognised as her own output ────────────
check('self-ping (with echoed note) is detected as ARIA\'s own output', () => {
  assert.ok(_isAriaOwnGuardianEcho(SELF_PING),
    'the looping self-ping MUST be recognised so it is skipped');
});
check('arm + clear confirmations are detected as ARIA\'s own output', () => {
  assert.ok(_isAriaOwnGuardianEcho(ARM_CONFIRM));
  assert.ok(_isAriaOwnGuardianEcho(CLEAR_CONFIRM));
});

// ── no false positives: real user safety commands still flow through ─────────
check('a real user "check on me in 5 min" is NOT treated as ARIA\'s echo', () => {
  assert.ok(!_isAriaOwnGuardianEcho('Aria check on me in 5 min'));
});
check('a real user "all clear" / "I am safe" is NOT treated as ARIA\'s echo', () => {
  assert.ok(!_isAriaOwnGuardianEcho('all clear'));
  assert.ok(!_isAriaOwnGuardianEcho('Aria I am safe'));
});
check('a real user circle-add is NOT treated as ARIA\'s echo', () => {
  assert.ok(!_isAriaOwnGuardianEcho('add Antonio +351932015591 to my trusted circle'));
});

// ── the wiring is actually present in the shipped source ─────────────────────
check('the loop guard is wired into the message pipeline (fromMe + echo)', () => {
  assert.ok(/_isFromMe\s*&&\s*_isAriaOwnGuardianEcho\(text\)\)\s*continue/.test(SRC),
    'expected: if (_isFromMe && _isAriaOwnGuardianEcho(text)) continue;');
});
check('/send registers sent message ids so server-sends are skipped on echo', () => {
  // both the text and image branches must mark their sent id
  const marks = (SRC.match(/if\s*\(_(?:txt|img)Sent\?\.key\?\.id\)\s*_markAriaSent/g) || []);
  assert.ok(marks.length >= 2,
    `expected text+image /send branches to call _markAriaSent (found ${marks.length})`);
});

if (failures) { console.error(`\n${failures} test(s) FAILED`); process.exit(1); }
console.log('\nAll R-F1994 guardian self-ping loop tests passed.');
