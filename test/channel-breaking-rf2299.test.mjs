// R-F2299 — wireBreakingAlertsToChannel: FLASH/critical signals (score >=
// BREAKING_SCORE) post to the channel IMMEDIATELY, bypassing the routine cadence
// cap. Plus the side-effect-free `noMark` classifier (so classifying a signal
// doesn't consume its dedup slot) and the broadened (global, not Africa-only)
// relevance keywords.
import { test } from 'node:test';
import assert from 'node:assert';
import { scoreSignal, isBreakingSignal, BREAKING_SCORE } from '../lib/telegram/channelPublisher.mjs';
import { publishBreakingSignals } from '../lib/telegram/channelServerHooks.mjs';

const fresh = () => new Date().toISOString();

test('BREAKING_SCORE defaults to 0.85', () => {
  assert.equal(BREAKING_SCORE, 0.85);
});

test('isBreakingSignal: true for a critical, fresh, relevant signal', () => {
  const s = { severity: 'critical', title: 'rf2299-brk-a', summary: 'sanctions procurement defence Poland tender', timestamp: fresh() };
  assert.equal(isBreakingSignal(s), true);
});

test('isBreakingSignal: false for a low-severity generic signal', () => {
  const s = { severity: 'low', title: 'rf2299-low-a', summary: 'a generic note about nothing in particular' };
  assert.equal(isBreakingSignal(s), false);
});

test('noMark: classifying does NOT consume the dedup slot', () => {
  const s = { severity: 'critical', title: 'rf2299-nomark-unique', summary: 'sanctions procurement defence', timestamp: fresh() };
  const a = scoreSignal(s, { noMark: true });
  assert.equal(a.pass, true);
  // A second noMark classification still passes — it was never marked posted.
  const b = scoreSignal(s, { noMark: true });
  assert.notEqual(b.reason, 'already posted (dedup)');
});

test('broadened keywords: a global (NATO/Poland) signal gets the relevance boost', () => {
  // scoreSignal checks (text || title || summary); keywords go in the title here.
  const s = { severity: 'low', title: 'NATO Poland defence procurement briefing rf2299global' };
  const r = scoreSignal(s, { noMark: true });
  // base 0.5 + relevance (>=3 of nato/poland/defence/procurement) 0.15 = 0.65
  assert.ok(r.score >= 0.65, `expected relevance boost for global keywords, got ${r.score}`);
});

test('publishBreakingSignals: classifies breaking, excludes non-breaking (no token → no network)', async () => {
  const breaking = { severity: 'critical', title: 'rf2299-pub-brk', summary: 'sanctions procurement defence Poland', timestamp: fresh() };
  const normal = { severity: 'low', title: 'rf2299-pub-normal', summary: 'a generic note' };
  const r = await publishBreakingSignals([breaking, normal], { /* no botToken → publishSignal returns ok:false, no fetch */ });
  assert.equal(r.handled.has(breaking), true, 'breaking signal must be handled');
  assert.equal(r.handled.has(normal), false, 'non-breaking signal must NOT be handled');
});
