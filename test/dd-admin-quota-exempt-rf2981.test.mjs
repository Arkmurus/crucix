// R-F2981 — admin/operator accounts are EXEMPT from the ddRun quota.
//
// Live 2026-07-24: the operator's own admin account has no `tier` field, so it
// defaulted to `free` (5 DD-runs/month) and a Silverbrook demo dry-run failed
// with "ddRun cap reached (5/5)". Admins/operators run demos + ops and must not
// be customer-metered — the §17 $300/mo LLM cost cap remains the hard backstop.
// server.mjs now gates the DD quota as `isPrivileged(user) ? null : enforceQuota(...)`
// on BOTH the web path and the internal /quota/consume path.
//
// These tests drive the REAL decision (isPrivileged + the real checkAndConsume
// counter) and pin both directions: admins bypass the cap; regular users don't.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

// Isolate the durable counter file (R-F2858) so this suite can't poison the
// shared quota store or consume real customer quota on a PERSIST_DIR=/data box.
process.env.QUOTA_FILE_OVERRIDE = path.join(
  mkdtempSync(path.join(tmpdir(), 'quota-rf2981-')), 'quotas.json',
);
const { enforceQuota } = await import('../lib/billing/enforce.mjs');
const { isPrivileged } = await import('../lib/auth/proxyPin.mjs');

// The exact decision server.mjs makes (R-F2981) at both DD-quota gates.
const ddDecision = async (user, uid, tier) =>
  isPrivileged(user) ? null : await enforceQuota(uid, tier, 'ddRun');

test('R-F2981: isPrivileged flags admins + aria-internal, not regular users', () => {
  assert.equal(isPrivileged({ role: 'admin' }), true);
  assert.equal(isPrivileged({ id: 'aria-internal' }), true);
  assert.equal(isPrivileged({ role: 'user' }), false);
  assert.equal(isPrivileged({}), false);
  assert.equal(isPrivileged(null), false);
});

test('R-F2981: an admin bypasses the ddRun cap even after it is exhausted', async () => {
  const uid = 'test-admin-rf2981';
  const admin = { id: uid, role: 'admin', tier: null };  // no tier → would be free/5
  // A non-exempt caller on this uid would be capped after 5:
  for (let i = 0; i < 6; i++) await enforceQuota(uid, 'free', 'ddRun');
  const asRegular = await enforceQuota(uid, 'free', 'ddRun');
  assert.ok(asRegular && asRegular.allowed === false, 'sanity: the counter is past the free cap');
  // But the admin decision is EXEMPT (null = allowed) regardless of the counter:
  assert.equal(await ddDecision(admin, uid, null), null, 'admin must be exempt, not 429');
  assert.equal(await ddDecision(admin, uid, null), null, 'admin stays exempt on repeat');
});

test('R-F2981: a NON-privileged user is still capped (the exemption does not leak)', async () => {
  const uid = 'test-reguser-rf2981';
  const user = { id: uid, role: 'user', tier: null };
  for (let i = 0; i < 5; i++) {
    assert.equal(await ddDecision(user, uid, null), null, `dd ${i + 1} allowed`);
  }
  const sixth = await ddDecision(user, uid, null);
  assert.ok(sixth && sixth.allowed === false, 'the 6th DD for a regular user must still be blocked');
  assert.equal(sixth.cap, 5);
});
