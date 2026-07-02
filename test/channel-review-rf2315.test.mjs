// R-F2315 — 4-step-review correctness batch: flash scoring + parseReply whole-word
// country matching (no false briefs from chatter).
import { test } from 'node:test';
import assert from 'node:assert';
import { scoreSignal } from '../lib/telegram/channelPublisher.mjs';
import { parseReply } from '../lib/telegram/replyKeywordRouter.mjs';

test('flash severity now earns the urgency boost (was dropped below threshold)', () => {
  const r = scoreSignal({ severity: 'flash', title: 'rf2315-flash-unique' }, { noMark: true });
  // base 0.5 + flash 0.2 = 0.7 ≥ MIN_QUALITY_SCORE (0.55) → passes → won't be dropped
  assert.ok(r.score >= 0.7, `flash should get +0.2, got ${r.score}`);
  assert.equal(r.pass, true);
});

test('parseReply: real country keyword still resolves', () => {
  assert.equal(parseReply('angola').type, 'country');            // exact
  assert.equal(parseReply('nigeria please').type, 'country');    // whole word inside a phrase
  assert.equal(parseReply('tell me about poland').type, 'country'); // whole word mid-phrase
});

test('parseReply: chatter containing a country substring does NOT trigger a brief', () => {
  assert.notEqual(parseReply('ukulele').type, 'country');  // 'uk' must not match inside "ukulele"
  assert.notEqual(parseReply('drcongo-ish rambling').type, 'country'); // no whole-word 'drc'
});

test('parseReply: explicit command keywords unaffected', () => {
  assert.equal(parseReply('SCREEN Rosoboronexport').action, 'screen');
  assert.equal(parseReply('PRO').action, 'pro');
  assert.equal(parseReply('HELP').action, 'help');
});
