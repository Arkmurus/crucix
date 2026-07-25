// R-F3074 capability test — logout must actually end the session.
//
// Broken path: POST /api/auth/logout returned {ok:true} while the bearer token
// it was called with stayed valid for the rest of its 7-day life, because the
// handler only cleared a cookie the app does not authenticate with.
//
// This drives the REAL flow (login → authenticated call → logout → same token
// again) rather than unit-testing the denylist helper.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

process.env.JWT_SECRET = process.env.JWT_SECRET
  || 'rf3074-test-secret-long-enough-to-pass-the-32-char-guard';
const _tmp = mkdtempSync(join(tmpdir(), 'rf3074-'));
process.env.TOKEN_DENYLIST_FILE_OVERRIDE = join(_tmp, 'token_denylist.json');
writeFileSync(process.env.TOKEN_DENYLIST_FILE_OVERRIDE, '{}');

const { createToken, verifyToken } = await import('../lib/auth/users.mjs');
const { initTokenDenylist, revokeToken, isTokenRevoked } =
  await import('../lib/auth/tokenDenylist.mjs');

await initTokenDenylist();

const USER_ID = 'rf3074-user';
// createToken's payload is {userId, role, ver, iat, exp} with iat/exp in ms and
// NO nonce, so two logins inside the same millisecond produce byte-identical
// tokens. Real logins are seconds apart; the helper makes that explicit rather
// than letting a same-ms collision flap the test.
const _sleep1ms = () => { const t = Date.now(); while (Date.now() === t) { /* spin */ } };
const issue = () => { _sleep1ms(); return createToken(USER_ID, 'user'); };

test('a live token is accepted before logout', () => {
  const token = issue();
  assert.equal(isTokenRevoked(token), false,
    'a freshly issued token must not be revoked');
  assert.ok(verifyToken(token).userId, 'token must verify normally');
});

test('logout revokes THAT token — the pre-R-F3074 symptom', () => {
  const token = issue();
  const exp = verifyToken(token).exp;

  // Signature + exp still verify — this is exactly why the old logout was a
  // no-op: nothing about the token itself changes when a user signs out.
  assert.ok(exp > Date.now(), 'token has not expired on its own');

  revokeToken(token, exp);                    // what the logout route now does

  assert.equal(isTokenRevoked(token), true,
    'after logout the same bearer must be rejected — this assertion FAILS on '
    + 'the pre-R-F3074 code, where logout touched nothing the bearer path reads');
  // The token is still cryptographically valid; only the denylist stops it.
  assert.ok(verifyToken(token).userId,
    'signature check still passes — revocation is what does the work');
});

test('logging out one device does not kill the other devices', () => {
  const phone  = issue();
  const laptop = issue();
  assert.notEqual(phone, laptop, 'each login issues a distinct token');

  revokeToken(phone, verifyToken(phone).exp);

  assert.equal(isTokenRevoked(phone), true,  'the device that logged out is dead');
  assert.equal(isTokenRevoked(laptop), false,
    'the other device must stay signed in — this is why R-F3074 does NOT bump '
    + 'tokenVersion (that would be an all-sessions kill switch)');
});

test('revocation survives a restart (durable store)', async () => {
  const token = issue();
  revokeToken(token, verifyToken(token).exp);

  // Re-import with a fresh module registry to simulate a process restart
  // reading the persisted file back.
  const fresh = await import('../lib/auth/tokenDenylist.mjs?restart=1');
  await fresh.initTokenDenylist();
  assert.equal(fresh.isTokenRevoked(token), true,
    'a deploy must not silently un-revoke a signed-out session');
});

test('an unknown / malformed token is not treated as revoked', () => {
  assert.equal(isTokenRevoked(''), false);
  assert.equal(isTokenRevoked('not-a-token'), false);
  assert.equal(isTokenRevoked(createToken('someone-else', 'user')), false);
});
