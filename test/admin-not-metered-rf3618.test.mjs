// test/admin-not-metered-rf3618.test.mjs
//
// R-F3618 — the admin role must grant full access; billing tier gates CUSTOMERS.
//
// `lib/auth/roles.mjs` states the model: "Billing TIER (free/pro/proIntel) is
// orthogonal and gates customer features only." R-F2981 enforced that on the DD
// orchestrate route and the brain-side consume route — after the operator's own admin
// account (no `tier` field → defaults to FREE) was blocked mid-demo by 'ddRun cap 5/5'.
//
// It missed `_quotaBlock` in server.mjs, the shared helper behind the chat/message
// lane, whose own comment claims it "keeps the load-bearing exemption in one place".
// So an admin was still metered at the free tier's messagesPerDay: 50.
//
// Surfaced 2026-08-01: a second admin (SPENCER ANUM, spencerodai@gmail.com) was created
// with an EXPLICIT `tier: "free"`, and the operator asked whether the admin role means
// full access to the web content. On the chat lane it did not.
//
// These tests assert the PROPERTY (an admin is never customer-metered) at the two
// layers that decide it, rather than re-testing one route's wiring.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const { isPrivileged } = await import('../lib/auth/proxyPin.mjs');
const { roleSatisfies, ROLES } = await import('../lib/auth/roles.mjs');
const { TIERS, DEFAULT_TIER } = await import('../lib/billing/tiers.mjs');

const serverSrc = fs.readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');

describe('R-F3618 — an admin is not a metered customer', () => {
  it('the shared _quotaBlock helper exempts privileged callers', () => {
    // Asserted on the SOURCE because _quotaBlock is a module-private helper and
    // server.mjs boots a live app on import. The property under test is that the
    // exemption lives in the shared helper, not only at individual call sites.
    const start = serverSrc.indexOf('async function _quotaBlock(');
    assert.ok(start > 0, '_quotaBlock should exist');
    const body = serverSrc.slice(start, serverSrc.indexOf('\n}', start));
    assert.match(body, /isPrivileged\(/,
      'an admin on the default free tier would otherwise be capped at messagesPerDay');
    assert.match(body, /enforceQuota\(/, 'non-privileged users must still be metered');
  });

  it('every enforceQuota call site is privilege-aware', () => {
    // The defect was one site out of four. Count them, so adding a metered route
    // without the exemption fails here instead of in front of a customer.
    const sites = [...serverSrc.matchAll(/enforceQuota\(/g)].map(m => m.index);
    assert.ok(sites.length >= 2, `expected enforceQuota call sites, found ${sites.length}`);
    for (const idx of sites) {
      const window = serverSrc.slice(Math.max(0, idx - 1200), idx);
      assert.match(window, /isPrivileged\(|_ddPrivileged/,
        `an enforceQuota call at offset ${idx} has no privilege exemption above it`);
    }
  });

  it('isPrivileged is true for admin and false for every other role', () => {
    assert.equal(isPrivileged({ role: 'admin' }), true);
    for (const role of ROLES.filter(r => r !== 'admin')) {
      assert.equal(isPrivileged({ role }), false, `${role} must stay metered`);
    }
  });

  it('an explicit tier:free does not weaken an admin', () => {
    // SPENCER ANUM's record shape: role admin, status active, tier free. The tier
    // must be irrelevant to privilege — that is the whole point of "orthogonal".
    assert.equal(isPrivileged({ role: 'admin', status: 'active', tier: 'free' }), true);
    assert.equal(isPrivileged({ role: 'viewer', status: 'active', tier: 'free' }), false);
  });

  it('the free tier really does cap messages — so the exemption is load-bearing', () => {
    // If this ever became unlimited the tests above would pass vacuously.
    const free = TIERS[DEFAULT_TIER] || TIERS.free;
    assert.ok(Number(free?.messagesPerDay) > 0,
      'free tier must have a finite message cap for the admin exemption to matter');
  });

  it('admin satisfies every lower-privilege route gate', () => {
    for (const role of ROLES) {
      assert.equal(roleSatisfies('admin', [role]), true,
        `admin must satisfy a route gated on '${role}' — full access is the role's definition`);
    }
    assert.equal(roleSatisfies('viewer', ['admin']), false, 'and the reverse must not hold');
    assert.equal(roleSatisfies('poweruser', ['admin']), false);
  });
});
