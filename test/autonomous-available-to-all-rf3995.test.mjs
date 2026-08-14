// test/autonomous-available-to-all-rf3995.test.mjs
//
// R-F3995 (C-76) — autonomous is available ACROSS ALL USERS (operator, 2026-08-14).
//
// WHAT WAS WRONG. `autonomousEnabled` was true for proIntel and false for free
// and pro, and `tiers.mjs` called it "the paid moat". It gated nothing:
// R-F3990 established that the only enforced capability flag in the tree is
// `publicApiEnabled`. This one was read solely to be DISPLAYED — by
// `/api/billing/me` and rendered in account.html. So the entitlement matrix was
// wrong in BOTH directions at once: free and pro users were shown a restriction
// that did not exist, and proIntel customers were shown a differentiator they
// were not actually being given. Same shape as the C-73 upload cap.
//
// WHY IT COULD NOT HAVE BEEN ENFORCED AS WRITTEN. The autonomous engine is one
// GLOBAL loop — verified live 2026-08-14: enabled, running, autonomy level 3, 98
// tasks loaded — not a per-account subscription. There is no per-user unit to
// gate. Honouring the label would have meant either building per-account
// autonomy or degrading the shared loop for some users; the second limits what
// ARIA can do for everyone in order to keep a word true.
//
// So it was levelled UP. Nobody loses a capability, the displayed matrix becomes
// true, and a uniform flag cannot misdescribe what a customer bought.
//
// This file exists so that resolution cannot quietly reverse. A future edit that
// re-gates autonomy by tier has to fail here and say why, rather than reappear as
// a one-word diff in a config object nobody diffs against the display layer.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const { TIERS } = await import('../lib/billing/tiers.mjs');
const serverSrc = fs.readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
const keysSrc = fs.readFileSync(new URL('../lib/api_keys/routes.mjs', import.meta.url), 'utf8');
const billingSrc = fs.readFileSync(new URL('../lib/billing/routes.mjs', import.meta.url), 'utf8');

describe('R-F3995 — autonomous is available to every tier', () => {

  it('every tier has autonomousEnabled true', () => {
    for (const [id, tier] of Object.entries(TIERS)) {
      assert.equal(tier.autonomousEnabled, true,
        `${id} must have autonomous — operator direction is that it is available across all users`);
    }
  });

  it('the free tier is not excluded — the acquisition funnel keeps full capability', () => {
    // Called out separately because free is the tier a reviewer is most tempted
    // to trim when looking for something to charge for. Trimming it here would
    // restore a restriction that was never enforced in the first place.
    assert.equal(TIERS.free.autonomousEnabled, true);
  });

  it('no tier is left as the sole holder of a capability nothing enforces', () => {
    // The general property. A flag that VARIES between tiers is a promise; a
    // promise no code reads is decoration. Uniform is honest precisely because
    // there is no enforcement call site to make it real.
    const values = new Set(Object.values(TIERS).map(t => t.autonomousEnabled));
    assert.equal(values.size, 1,
      'autonomousEnabled must not vary between tiers while it remains unenforced');
    const enforced = new RegExp("tierAllows\\([^)]*['\"]autonomousEnabled['\"]")
      .test(serverSrc + keysSrc);
    assert.equal(enforced, false,
      'if autonomousEnabled has gained real enforcement, this test should be replaced by one '
      + 'that exercises the gate rather than asserting the flag is uniform');
  });

  it('the capability is still REPORTED to the customer, not silently dropped', () => {
    // Levelling the value must not turn into deleting the row. The customer
    // should be able to see that they have it.
    assert.match(billingSrc, /autonomous:\s*tier\.autonomousEnabled/,
      '/api/billing/me must keep reporting the autonomous capability');
  });

  it('spend is still bounded by the controls that ARE enforced', () => {
    // The reason widening this flag is safe: cost is bounded by the per-tier
    // counters and the monthly cap, none of which this change touches.
    for (const [id, tier] of Object.entries(TIERS)) {
      assert.ok(Number.isFinite(tier.messagesPerDay) && tier.messagesPerDay > 0,
        `${id} must keep a finite per-day message cap`);
      assert.ok(Number.isFinite(tier.ddRunsPerMonth) && tier.ddRunsPerMonth > 0,
        `${id} must keep a finite monthly DD cap`);
      assert.ok(Number.isFinite(tier.uploadsPerDay) && tier.uploadsPerDay > 0,
        `${id} must keep a finite per-day upload cap`);
    }
    // And those counters are genuinely wired (R-F2765 / R-F3989).
    const code = serverSrc.split(/\r?\n/).filter(l => !l.trim().startsWith('//')).join('\n');
    for (const kind of ['message', 'ddRun', 'upload']) {
      const called = new RegExp(`_quotaBlock\\(\\s*req\\s*,\\s*['"]${kind}['"]`).test(code)
        || new RegExp(`enforceQuota\\([^)]*['"]${kind}['"]`).test(code);
      assert.ok(called, `'${kind}' must keep its enforcement call site`);
    }
  });
});
