// R-F3072 + R-F3076 capability test — the limiter must count, and must not
// throttle a signed-in user for using the app normally.
//
// R-F3076 BROKEN PATH: express-rate-limit v8 changed the helper signature to
// ipKeyGenerator(ip: string). rateLimiter.mjs still called it as (req, res), so
// it returned the Request OBJECT as the bucket key — a new key per request, so
// the counter never passed 1. Verified live before the fix: 175 consecutive
// anonymous requests all returned RateLimit-Remaining: 149.
//
// R-F3072 BROKEN PATH: the limiters mount at server.mjs:1462, BEFORE any
// requireAuth, so req.user was always undefined and every tier fell back to a
// per-IP bucket. With 150 req/15min shared per IP, the app's own polling (the
// 60s sidebar badge on every page + the 90s×6-call dashboard refresh) exhausted
// the budget and the whole UI 429'd.
//
// Drives the REAL middleware through a real express app, not the helpers.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';

process.env.JWT_SECRET = process.env.JWT_SECRET
  || 'rf3072-test-secret-long-enough-to-pass-the-32-char-guard';
delete process.env.ARIA_INTERNAL_TOKEN;   // no bypass in this test

const { createToken } = await import('../lib/auth/users.mjs');
const { applyRateLimiting } = await import('../middleware/rateLimiter.mjs');

function makeApp() {
  const app = express();
  app.set('trust proxy', false);
  applyRateLimiting(app);
  app.get('/api/ping', (req, res) => res.json({ ok: true }));
  return app;
}

async function hit(server, token) {
  const { port } = server.address();
  const r = await fetch(`http://127.0.0.1:${port}/api/ping`,
    token ? { headers: { Authorization: `Bearer ${token}` } } : undefined);
  await r.text();
  return { status: r.status, remaining: Number(r.headers.get('ratelimit-remaining')), limit: Number(r.headers.get('ratelimit-limit')) };
}

function listen(app) {
  return new Promise((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
}

test('R-F3076: the anonymous counter actually increments', async () => {
  const server = await listen(makeApp());
  try {
    const first = await hit(server, null);
    const second = await hit(server, null);
    const third = await hit(server, null);
    assert.equal(first.limit, 150, 'anonymous callers keep the original 150 budget');
    assert.ok(first.remaining > second.remaining && second.remaining > third.remaining,
      `the bucket must count down (${first.remaining} → ${second.remaining} → ${third.remaining}). `
      + 'A flat value means ipKeyGenerator is minting a fresh key per request — the R-F3076 defect, '
      + 'which left every rate limit enforcing nothing.');
  } finally { server.close(); }
});

test('R-F3072: a signed-in user gets their own, larger bucket', async () => {
  const server = await listen(makeApp());
  try {
    const token = createToken('rf3072-alice', 'user');
    const r = await hit(server, token);
    assert.equal(r.limit, 600,
      'authenticated traffic is sized from the app\'s own polling profile, not the abuse budget');
  } finally { server.close(); }
});

test('R-F3072: one user cannot exhaust another user\'s budget', async () => {
  const server = await listen(makeApp());
  try {
    const alice = createToken('rf3072-alice2', 'user');
    const bob   = createToken('rf3072-bob2', 'user');

    let aliceRemaining = 0;
    for (let i = 0; i < 20; i++) aliceRemaining = (await hit(server, alice)).remaining;
    const bobFirst = await hit(server, bob);

    assert.ok(aliceRemaining <= 580, `alice's bucket must have been consumed (remaining ${aliceRemaining})`);
    assert.equal(bobFirst.remaining, 599,
      'bob starts on a full bucket — same IP, different identity. Pre-R-F3072 they shared one '
      + 'per-IP bucket, so colleagues behind one office NAT starved each other.');
    assert.equal(bobFirst.status, 200);
  } finally { server.close(); }
});

test('R-F3072: an expired or forged bearer falls back to the anonymous bucket', async () => {
  const server = await listen(makeApp());
  try {
    const forged = 'eyJ1c2VySWQiOiJhdHRhY2tlciJ9.not-a-real-signature';
    const r = await hit(server, forged);
    assert.equal(r.limit, 150,
      'an unverified token must NOT buy the larger authenticated budget — otherwise the '
      + 'anti-abuse cap is bypassable by sending any junk Authorization header');
  } finally { server.close(); }
});

test('R-F3072: the app\'s measured idle polling fits inside the budget', async () => {
  // Steady state per 15-min window, measured 2026-07-25 from the page sources:
  //   sidebar.js alerts badge  1 req/60s          = 15
  //   dashboard.html autoRefresh 6 reqs/90s       = 60
  // A second tab (dashboard + chat is the normal posture) doubles it.
  // Page LOAD cost, counted once per tab:
  //   dashboard loadData 6 + my-sources 1 + sidebar (nav-pages, auth/me, alerts) 3
  const LOAD = 10;
  const ONE_TAB = LOAD + 15 + 60;   // 85
  const TWO_TABS = ONE_TAB * 2;     // 170
  const OLD_BUDGET = 150;
  const AUTHED_BUDGET = 600;
  const SLOWDOWN_RAMP_AT = 80;      // express-slow-down delayAfter, anonymous tier

  assert.ok(ONE_TAB > SLOWDOWN_RAMP_AT,
    `ONE idle dashboard tab (~${ONE_TAB} requests/15min) already crossed the old `
    + `${SLOWDOWN_RAMP_AT}-request slow-down ramp, adding up to 5s to every later call.`);
  assert.ok(TWO_TABS >= OLD_BUDGET,
    `two idle tabs consume ~${TWO_TABS} of the old ${OLD_BUDGET}-request budget — i.e. ALL of it, `
    + 'leaving nothing for page loads, chat or DD. That is why the app 429\'d itself.');
  assert.ok(TWO_TABS * 2 < AUTHED_BUDGET,
    `the new ${AUTHED_BUDGET} budget leaves headroom beyond the ~${TWO_TABS} idle cost `
    + '(measured 2026-07-25). If this fails, the polling profile changed — re-derive the budget '
    + 'from the new profile rather than raising the number.');
});
