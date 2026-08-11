// test/auth-enumeration-rf3836.test.mjs
//
// R-F3836 — the verification endpoints must not reveal whether an email is
// registered, or whether a registered account is already verified.
//
// ── THE DEFECT ───────────────────────────────────────────────────────────────
// Registration is hardened against enumeration (server.mjs:5777-5825) and
// /forgot-password is correct (":If that email is registered..."). The two
// verification endpoints were the side door:
//
//   POST /api/auth/verify-email
//     unknown email      -> 404 {"error":"User not found"}
//     known, wrong code  -> 400 {"error":"Invalid verification code"}
//     known, ALREADY ok  -> 200 {"message":"Email already verified..."}
//   POST /api/auth/resend-verification
//     unknown email      -> 200 {"message":"If that email exists..."}
//     known, ALREADY ok  -> 400 {"error":"Account already verified"}
//
// Three distinguishable outcomes on the first, two on the second. An attacker
// walks a breach list and learns which addresses hold live, verified accounts —
// which is precisely what the hardened registration flow refuses to tell them.
//
// ── THE TRADE, STATED ────────────────────────────────────────────────────────
// Closing this costs a small piece of UX: someone re-clicking a stale
// verification link now reads "Invalid or expired verification code" instead of
// "already verified — please log in", and an already-verified user asking for a
// resend gets the generic acknowledgement and no email. Both then sign in
// successfully, which is the action they wanted. The alternative — telling the
// truth about account state to an unauthenticated caller — is the leak itself.
//
// Run: node --test test/auth-enumeration-rf3836.test.mjs

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

function repoRoot() {
  return path.resolve(
    path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
    '..',
  );
}
const src = () => fs.readFileSync(path.join(repoRoot(), 'server.mjs'), 'utf8');

function handler(marker, len = 3000) {
  const s = src();
  const at = s.indexOf(marker);
  assert.ok(at > -1, `handler not found: ${marker}`);
  return s.slice(at, at + len);
}

/**
 * Handler source with comments removed.
 *
 * These assertions are about what the endpoint SAYS to a caller, and the fix's
 * own explanatory comments necessarily quote the strings being removed. Matching
 * raw source made the test fail on its own documentation — a false positive that
 * would have been "fixed" by deleting the explanation.
 */
function code(marker, len = 3000) {
  return handler(marker, len)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

describe('R-F3836 POST /api/auth/verify-email is uniform', () => {
  const body = () => handler("app.post('/api/auth/verify-email'");

  it('an unknown email no longer returns a distinguishable 404', () => {
    assert.ok(!/if \(!user\) return res\.status\(404\)\.json\(\{ error: 'User not found' \}\);/.test(body()),
      'the enumeration oracle is back: unknown email 404s while a wrong code 400s');
  });

  it('every failure path returns the SAME status and message', () => {
    const b = body();
    // Collect every non-2xx response this handler can emit, excluding the
    // 400 "Email and code required" argument check (which reveals nothing about
    // an account) and the 429 lockout (which is state the caller created).
    const statuses = [...b.matchAll(/res\.status\((\d{3})\)/g)].map((m) => m[1]);
    const informative = statuses.filter((s) => s !== '400' && s !== '429' && s !== '500');
    assert.deepEqual(informative, [],
      `these statuses distinguish account state: ${informative.join(', ')}`);
  });

  it('the shared refusal message names neither the account nor its status', () => {
    const b = code("app.post('/api/auth/verify-email'");
    assert.ok(!/'User not found'/.test(b), 'must not confirm non-existence');
    assert.ok(!/already verified/i.test(b),
      'must not confirm that a given address holds a verified account');
    // All three refusals must be the SAME string, not merely three safe ones.
    const messages = [...b.matchAll(/error:\s*(INVALID_CODE|'([^']+)')/g)]
      .map((m) => m[2] || m[1]);
    const distinct = new Set(messages.filter((m) => (
      m !== 'Email and code required'
      // Reachable only AFTER a correct code, so it is not an existence oracle.
      && m !== 'Verification code expired. Request a new one.'
    )));
    assert.deepEqual([...distinct], ['INVALID_CODE'],
      `verify-email emits ${distinct.size} distinct refusals: ${[...distinct].join(' | ')} `
      + '— the unknown, wrong-code, already-verified and locked-out paths must be identical');
  });

  it('the lockout is keyed by EMAIL, so it fires for unregistered addresses too', () => {
    const b = code("app.post('/api/auth/verify-email'", 4000);
    assert.ok(b.includes('_verifyThrottleCheck'),
      'R-F2035 counts attempts on the USER RECORD, so it can only ever fire for an '
      + 'email that exists — that made the lockout itself the oracle');
    const throttleAt = b.indexOf('_verifyThrottleCheck');
    const lookupAt = b.indexOf('findUserByEmail');
    assert.ok(throttleAt > -1 && lookupAt > -1 && throttleAt < lookupAt,
      'the throttle must be checked BEFORE the user lookup (R-F609 rationale)');
    assert.ok(/if \(!user\) \{\s*_verifyThrottleRecordFailure/.test(b),
      'an attempt against an unknown email must count toward the same lockout');
  });

  it('no path returns 429 any more — only the existing-account path could', () => {
    const b = code("app.post('/api/auth/verify-email'", 4000);
    assert.ok(!/res\.status\(429\)/.test(b),
      'a 429 here means "this address is a live pending account"');
  });

  it('the R-F2035 code burn survives — the response changed, not the defence', () => {
    const b = code("app.post('/api/auth/verify-email'", 4000);
    assert.ok(/verificationCode: null, verificationExpiry: null, verificationAttempts: 0/.test(b),
      'the code must still be burned at the attempt cap');
  });

  it('a correct code clears the email throttle', () => {
    const b = code("app.post('/api/auth/verify-email'", 5000);
    assert.ok(b.includes('_verifyThrottleClear'),
      'a legitimate user who mistyped earlier must not stay locked out');
  });

  it('the brute-force lockout and timing-safe compare are untouched', () => {
    const b = body();
    assert.ok(b.includes('MAX_VERIFY_ATTEMPTS'), 'R-F2035 lockout must survive');
    assert.ok(b.includes('timingSafeEqual'), 'R-F2383 timing-safe compare must survive');
  });

  it('a correct code still verifies the account', () => {
    assert.ok(handler("app.post('/api/auth/verify-email'", 5000).includes('evaluateAutoApproval'),
      'the R-F2034 self-serve approval path must still be reachable');
  });
});

describe('R-F3836 POST /api/auth/resend-verification is uniform', () => {
  const body = () => handler("app.post('/api/auth/resend-verification'");

  it('an already-verified account no longer returns a distinguishable 400', () => {
    assert.ok(!/return res\.status\(400\)\.json\(\{ error: 'Account already verified' \}\)/.test(body()),
      'unknown email 200s while a verified one 400s — that is the oracle');
  });

  it('unknown, pending and verified all return the same acknowledgement', () => {
    const b = code("app.post('/api/auth/resend-verification'");
    // Both early-return branches must emit the same message — whether that is
    // the literal twice or a shared const referenced twice.
    const acks = [...b.matchAll(/message:\s*(RESEND_ACK|'([^']+)')/g)].map((m) => m[2] || m[1]);
    assert.ok(acks.length >= 2,
      `expected the unknown and already-verified branches to both acknowledge, got ${acks.length}`);
    assert.equal(new Set(acks.slice(0, 2)).size, 1,
      `the two branches return different messages: ${acks.slice(0, 2).join(' | ')}`);
  });

  it('the 60-second resend throttle still applies to real pending accounts', () => {
    assert.ok(/60 \* 1000/.test(body()), 'the resend rate limit must survive');
  });

  it('no verification email is sent to an account that does not need one', () => {
    const b = body();
    const sendAt = b.indexOf('sendVerificationEmail');
    assert.ok(sendAt > -1, 'the real resend path must still send');
    // The send must sit AFTER the status guard, not before it.
    const guardAt = b.indexOf("status === 'active'");
    assert.ok(guardAt > -1 && guardAt < sendAt,
      'the already-verified guard must run before the send');
  });
});

describe('R-F3836 the flows that were already correct stay correct', () => {
  it('/forgot-password still returns its uniform acknowledgement', () => {
    const b = handler("app.post('/api/auth/forgot-password'");
    assert.ok(b.includes('If that email is registered'),
      'this endpoint was already right — do not regress it');
  });

  it('registration still refuses to confirm an existing address', () => {
    // R-F2033: registration answers identically whether or not the email is taken.
    const s = src();
    const at = s.indexOf("app.post('/api/auth/register'");
    assert.ok(at > -1);
    assert.ok(!/'Email already registered'/.test(s.slice(at, at + 3000)),
      'registration hardening must not be undone');
  });
});
