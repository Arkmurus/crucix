// R-F2317 — 4-step-review batch 2: honest welcome, real PRO checkout, discovery
// loop footer. (Proprioception + SCREEN daily cap are network/stateful and covered
// by manual/live smoke.)
import { test } from 'node:test';
import assert from 'node:assert';
import { handlePro } from '../lib/telegram/replyKeywordRouter.mjs';
import { buildWelcomePost, buildMorningSignal } from '../lib/telegram/channelScheduler.mjs';

test('PRO routes to a real checkout, not the "DM @arkmurus" dead-end', () => {
  const r = handlePro();
  assert.doesNotMatch(r.text, /DM @arkmurus/i);
  assert.match(r.text, /imaria\.io|Upgrade to Pro/);  // real link
});

test('PRO honors STRIPE_PAYMENT_LINK_PROINTEL when set', () => {
  process.env.STRIPE_PAYMENT_LINK_PROINTEL = 'https://buy.stripe.com/test_xyz';
  const r = handlePro();
  delete process.env.STRIPE_PAYMENT_LINK_PROINTEL;
  assert.match(r.text, /buy\.stripe\.com\/test_xyz/);
});

test('welcome is honest — no disabled-pillar schedules promised', () => {
  const w = buildWelcomePost();
  assert.doesNotMatch(w, /Mon\/Wed\/Fri|Tue\/Thu|Mon\/Thu|Tue\/Fri/);  // dead cron schedules
  assert.doesNotMatch(w, /Case File|Know Your Rights|Opportunity Signal/); // disabled pillars
  assert.match(w, /Daily Signal/);   // only what actually fires
  assert.match(w, /Breaking Alerts/);
  assert.match(w, /SCREEN/);         // the working consult
});

test('daily post carries the discovery loop — consult CTA + forward ask', () => {
  const post = buildMorningSignal({ signals: [{ title: 'Test', text: 'body' }] });
  assert.match(post, /SCREEN \[company\]|ArkmurusIntelBot/); // free-consult CTA
  assert.match(post, /Forward to a colleague/i);             // the organic growth lever
  assert.doesNotMatch(post, /Reply with `morning`/);         // removed hollow keyword
});
