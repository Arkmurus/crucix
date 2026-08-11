// test/password-change-revokes-sessions-rf3835.test.mjs
//
// R-F3835 — changing or resetting a password must end every other live session.
//
// ── THE DEFECT ───────────────────────────────────────────────────────────────
// PUT /api/auth/password (server.mjs) and POST /api/auth/reset-password both
// wrote a new passwordHash WITHOUT bumping tokenVersion. requireAuth authorises
// on the JWT's `ver` matching the stored tokenVersion, so a token stolen before
// the change stayed valid for the rest of its SEVEN-DAY life. "I think someone
// has my password, I'll change it" did not evict the someone.
//
// The mechanism already existed and was already used on the paths an
// ADMINISTRATOR drives — recovery-reset bumps it inline (server.mjs:6728), and
// force-logout / suspend call revokeTokens (:7133, :7205). Only the two paths a
// USER drives for their own account were missed.
//
// ── THE THING THAT MAKES THIS NON-TRIVIAL ────────────────────────────────────
// Bumping the version invalidates the CALLER's own token too. public/set-
// password.html is the R-F3332 rotation flow: it PUTs the new password and then
// navigates to /dashboard.html reusing the token already in localStorage. A
// naive bump logs that user straight back out to /signin.html — turning a
// security fix into a lockout. So the change path must ALSO mint a replacement
// and the client must adopt it. That round trip is what these tests pin.
//
// Run: node --test test/password-change-revokes-sessions-rf3835.test.mjs

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

import { createToken, verifyToken } from '../lib/auth/users.mjs';

function repoRoot() {
  return path.resolve(
    path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
    '..',
  );
}
const read = (f) => fs.readFileSync(path.join(repoRoot(), f), 'utf8');

/** The check requireAuth performs (server.mjs:5641-5645), in one place. */
function sessionStillValid(token, liveUser) {
  const payload = verifyToken(token);
  if (payload.ver === undefined) return true;
  return !(liveUser && (liveUser.tokenVersion || 0) !== payload.ver);
}

describe('R-F3835 the revocation mechanism does what the fix relies on', () => {
  it('a token minted at v0 dies once the stored tokenVersion moves to v1', () => {
    const stolen = createToken('rf3835-user', 'user', '7d', 0);
    assert.equal(sessionStillValid(stolen, { tokenVersion: 0 }), true, 'valid before');
    assert.equal(sessionStillValid(stolen, { tokenVersion: 1 }), false,
      'a password change must strand the stolen token');
  });

  it('a replacement minted at the NEW version survives', () => {
    const replacement = createToken('rf3835-user', 'user', '7d', 1);
    assert.equal(sessionStillValid(replacement, { tokenVersion: 1 }), true,
      'the caller who just changed their own password must stay signed in');
  });

  it('the stolen token and the replacement are genuinely different', () => {
    const stolen = createToken('rf3835-user', 'user', '7d', 0);
    const replacement = createToken('rf3835-user', 'user', '7d', 1);
    assert.notEqual(stolen, replacement);
    assert.equal(verifyToken(stolen).ver, 0);
    assert.equal(verifyToken(replacement).ver, 1);
  });
});

describe('R-F3835 PUT /api/auth/password revokes and re-issues', () => {
  const handler = () => {
    const src = read('server.mjs');
    const at = src.indexOf("app.put('/api/auth/password'");
    assert.ok(at > -1, 'handler not found');
    return src.slice(at, at + 2200);
  };

  it('bumps tokenVersion on the password write', () => {
    const body = handler();
    // Accept either the inline bump or a named next-version const — the property
    // is that the write carries a tokenVersion derived from the stored one + 1,
    // not that it is spelled a particular way.
    const derives = /\(user\.tokenVersion \|\| 0\)\s*\+\s*1/.test(body);
    const writes = /tokenVersion:\s*(nextVersion|\(user\.tokenVersion \|\| 0\) \+ 1)/.test(body);
    assert.ok(derives && writes,
      'the write must invalidate every existing session');
  });

  it('mints a replacement token at the NEW version', () => {
    const body = handler();
    assert.ok(/createToken\(/.test(body),
      'without a replacement the caller is logged out of their own account');
    assert.ok(/nextVersion|tokenVersion \|\| 0\) \+ 1/.test(body));
  });

  it('returns the replacement to the caller AND refreshes the cookie', () => {
    const body = handler();
    assert.ok(/res\.json\(\{[^}]*token/s.test(body),
      'the client must receive the new token or it cannot keep the session');
    assert.ok(body.includes('_setAuthCookie'),
      'requirePageRole authenticates from the cookie — a stale cookie bounces '
      + 'the user to /signin.html on the very next page navigation');
  });

  it('still clears the R-F3332 rotation flag', () => {
    assert.ok(handler().includes('rotationClearedFields()'),
      'a gate whose clear path is dropped locks the account out permanently');
  });
});

describe('R-F3835 POST /api/auth/reset-password revokes', () => {
  it('bumps tokenVersion', () => {
    const src = read('server.mjs');
    const at = src.indexOf("app.post('/api/auth/reset-password'");
    assert.ok(at > -1, 'handler not found');
    const body = src.slice(at, at + 3600);
    assert.ok(/tokenVersion:\s*\(user\.tokenVersion \|\| 0\) \+ 1/.test(body),
      'a forgotten-password reset is the strongest signal the account is '
      + 'compromised — it MUST evict live sessions');
    assert.ok(body.includes('resetCode: null'), 'the used code must still be consumed');
  });

  it('does NOT re-issue a token — this path ends at the sign-in page', () => {
    const src = read('server.mjs');
    const at = src.indexOf("app.post('/api/auth/reset-password'");
    const body = src.slice(at, at + 3600);
    const upToResponse = body.slice(0, body.indexOf('You can now log in'));
    assert.ok(!upToResponse.includes('_setAuthCookie'),
      'reset-password is unauthenticated — issuing a session here would let '
      + 'anyone holding a reset code skip the sign-in step');
  });
});

describe('R-F3835 the rotation flow is not broken by the revocation', () => {
  it('set-password.html adopts the replacement token', () => {
    const page = read('public/set-password.html');
    assert.ok(/crucix_token/.test(page),
      'the rotation page must store the returned token, otherwise the very next '
      + 'navigation to /dashboard.html redirects to /signin.html');
    const at = page.indexOf("API.put('/api/auth/password'");
    assert.ok(at > -1);
    const after = page.slice(at, at + 900);
    assert.ok(after.includes('crucix_token'),
      'the token must be adopted in the success branch of the PUT');
  });
});
