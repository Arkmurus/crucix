// test/auth-cookie-mint-on-bearer-rf2871.test.mjs
//
// R-F2871 — a valid session silently lost page access, with no way to recover
// except logging out.
//
// R-F2774 (2026-07-18) added a SERVER-SIDE operator-page gate. A page navigation
// carries no Authorization header, so `requirePageRole` reads an httpOnly cookie:
//
//     const token = _cookieToken(req);
//     if (!token) return res.redirect(302, '/signin.html');
//
// That cookie was written in exactly TWO places — both login handlers. There was
// no refresh path. So every session that existed when R-F2774 shipped, and every
// session that crosses the 7-day cookie TTL, keeps a perfectly valid admin JWT in
// localStorage — APIs work, the dashboard renders, the nav shows the links — while
// every gated page bounces to /signin.html.
//
// Reported live by the admin (acorrea@arkmurus.com) on 2026-07-22: no access to
// brain / source health / vault. The account was never the problem —
// /api/auth/system-status confirmed admins=1, matchesEnv=true, anomaly="ok", and
// roleSatisfies already grants admin ⊇ poweruser.
//
// FIX: mint the cookie whenever a request presents a VALID Bearer. The session
// self-heals on the next API call the page makes, with no user action.
//
// SECURITY — why this grants nothing new:
//   * it runs only AFTER verifyToken() and the tokenVersion (force-logout) check,
//     so the cookie can only ever carry a token the caller already holds and that
//     already authenticates them;
//   * it is skipped for the localhost bypass (no token, no identity) and for the
//     ARIA_INTERNAL_TOKEN service path (a machine caller has no browser session,
//     and minting one would hand a page cookie to every internal integration);
//   * flags are identical to login (httpOnly, secure, sameSite=lax), and per
//     R-F2774 this cookie gates GET page reads only — it never authenticates a
//     mutating API, so it adds no CSRF surface.
//
// Run: node --test test/auth-cookie-mint-on-bearer-rf2871.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SRC = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');

/** The body of requireAuth, so assertions cannot match unrelated code. */
const REQUIRE_AUTH = (() => {
  const start = SRC.indexOf('function requireAuth(req, res, next)');
  assert.ok(start > 0, 'requireAuth must exist');
  const end = SRC.indexOf('\nfunction ', start + 10);
  assert.ok(end > start, 'requireAuth must be bounded');
  return SRC.slice(start, end);
})();

test('R-F2871: a valid Bearer mints the page-gate cookie', () => {
  assert.match(REQUIRE_AUTH, /_mintPageCookie\(/,
    'THE FIX: the verified-JWT path must refresh the cookie so sessions self-heal');
});

test('R-F2871: minting happens only AFTER verification and the tokenVersion check', () => {
  const verifyAt = REQUIRE_AUTH.indexOf('verifyToken(token)');
  const versionAt = REQUIRE_AUTH.indexOf('Session revoked');
  const mintAt = REQUIRE_AUTH.indexOf('_mintPageCookie(');
  assert.ok(verifyAt > 0 && versionAt > 0 && mintAt > 0, 'all three must be present');
  assert.ok(mintAt > verifyAt,
    'never mint from an unverified token');
  assert.ok(mintAt > versionAt,
    'never mint for a force-logged-out session — that would resurrect it');
});

test('R-F2871: NEGATIVE CONTROL — the internal service token must NOT get a cookie', () => {
  // A machine caller has no browser session. Minting one would hand a page-gate
  // cookie to every internal integration holding ARIA_INTERNAL_TOKEN.
  const internalBranch = REQUIRE_AUTH.slice(
    REQUIRE_AUTH.indexOf('internalToken && token === internalToken'),
    REQUIRE_AUTH.indexOf('try {'),
  );
  assert.ok(internalBranch.length > 0, 'the internal-token branch must exist');
  assert.ok(!/_mintPageCookie\(/.test(internalBranch),
    'the ARIA_INTERNAL_TOKEN path must never mint a browser cookie');
});

test('R-F2871: NEGATIVE CONTROL — the localhost bypass must NOT get a cookie', () => {
  // The bypass calls next() with NO req.user and no token — there is no identity
  // to mint a cookie for.
  const bypassBranch = REQUIRE_AUTH.slice(0, REQUIRE_AUTH.indexOf('const token ='));
  assert.ok(/return next\(\)/.test(bypassBranch), 'the bypass must still exist');
  assert.ok(!/_mintPageCookie\(/.test(bypassBranch),
    'the localhost bypass has no identity — nothing to mint');
});

test('R-F2871: the minted cookie uses the SAME flags as login', () => {
  const helper = SRC.slice(SRC.indexOf('function _mintPageCookie'),
                           SRC.indexOf('function _mintPageCookie') + 900);
  assert.ok(helper.length > 0, '_mintPageCookie must exist');
  assert.match(helper, /_setAuthCookie\(/,
    'it must reuse the login cookie writer rather than duplicating the flags');
});

test('R-F2871: minting never throws into the request', () => {
  const helper = SRC.slice(SRC.indexOf('function _mintPageCookie'),
                           SRC.indexOf('function _mintPageCookie') + 900);
  assert.match(helper, /try\s*\{/, 'a cookie refresh must never break an API call');
  assert.match(helper, /catch/, 'failures must be swallowed, not propagated');
});

test('R-F2871: does not re-send Set-Cookie when the cookie already matches', () => {
  // Every authenticated API call would otherwise carry a redundant Set-Cookie.
  const helper = SRC.slice(SRC.indexOf('function _mintPageCookie'),
                           SRC.indexOf('function _mintPageCookie') + 900);
  assert.match(helper, /_cookieToken\(req\)/,
    'it must compare against the existing cookie before writing');
});

test('R-F2871: the login set-points are untouched', () => {
  // R-F2774 minted at login; this ticket ADDS a refresh path, it does not move
  // or remove the original one.
  const setCalls = SRC.match(/_setAuthCookie\(res, token\)/g) || [];
  assert.ok(setCalls.length >= 2,
    'both login handlers must still mint the cookie directly');
});
