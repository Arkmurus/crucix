// test/onboarding-self-serve-rf2034.test.mjs
//
// R-F2034/R-F2035/R-F2036 — automated self-serve onboarding.
// Operator policy (2026-06-27): signup → email-verify → INSTANT active (free
// tier), no manual admin approval; abuse controls do the gating.
//
// Tests the policy seam (lib/auth/onboarding.mjs) + the real users.mjs status /
// sanitisation layer. The HTTP wiring (mandatory-verify 503, lockout 429,
// verify→active) is thin glue over these; final e2e is a live signup (needs SMTP).
//
// Run: node --test test/onboarding-self-serve-rf2034.test.mjs

import test from 'node:test';
import assert from 'node:assert';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { isDisposableEmail, evaluateAutoApproval, MAX_VERIFY_ATTEMPTS } from '../lib/auth/onboarding.mjs';

// ── Policy module (R-F2034/R-F2035) ─────────────────────────────────────────

test('R-F2035: disposable email domains are detected', () => {
  assert.equal(isDisposableEmail('x@mailinator.com'), true);
  assert.equal(isDisposableEmail('x@10minutemail.com'), true);
  assert.equal(isDisposableEmail('a.b@guerrillamail.com'), true);
  assert.equal(isDisposableEmail('analyst@arkmurus.com'), false);
  assert.equal(isDisposableEmail('user@gmail.com'), false);
  assert.equal(isDisposableEmail('not-an-email'), false);
});

test('R-F2034: a verified non-disposable email is auto-approved', () => {
  const d = evaluateAutoApproval({ email: 'analyst@defencegroup.com', accountType: 'broker' });
  assert.equal(d.approve, true);
  assert.equal(d.reason, 'self_serve_email_verified');
  assert.equal(d.signals.email_verified, true);
  assert.equal(d.signals.disposable_email, false);
});

test('R-F2035: a disposable email is NOT auto-approved (held for review)', () => {
  const d = evaluateAutoApproval({ email: 'throwaway@mailinator.com' });
  assert.equal(d.approve, false);
  assert.equal(d.reason, 'disposable_email_needs_review');
});

test('R-F2035: brute-force cap is a sane low number', () => {
  assert.ok(Number.isInteger(MAX_VERIFY_ATTEMPTS) && MAX_VERIFY_ATTEMPTS >= 3 && MAX_VERIFY_ATTEMPTS <= 10);
});

// ── users.mjs status + sanitisation (real store via override) ────────────────

test('R-F2034: new user starts pending_verification; flips to active; counter never leaks', async () => {
  process.env.USERS_FILE_OVERRIDE = join(mkdtempSync(join(tmpdir(), 'onboard-rf2034-')), 'users.json');
  process.env.JWT_SECRET = process.env.JWT_SECRET || 'x'.repeat(40);
  const users = await import('../lib/auth/users.mjs');

  const created = users.createUser({
    username: 'newcustomer', email: 'new@defencegroup.com',
    password: 'sufficiently-long-pw', fullName: 'New Customer',
  });
  assert.equal(created.status, 'pending_verification', 'new signup must start unverified');
  assert.equal(created.passwordHash, undefined, 'clean user must not expose passwordHash');

  const raw = users.findUserByEmail('new@defencegroup.com');
  // simulate the brute-force counter incrementing, then a successful auto-approve
  users.updateUser(raw.id, { verificationAttempts: 3 });
  const active = users.updateUser(raw.id, {
    status: 'active', verificationCode: null, verificationExpiry: null, verificationAttempts: 0,
  });
  assert.equal(active.status, 'active', 'verified user must become active (self-serve)');
  assert.equal(active.verificationAttempts, undefined, 'internal brute-force counter must be stripped (R-F2035)');
  assert.equal(active.verificationCode, undefined, 'verification code must be stripped');
});
