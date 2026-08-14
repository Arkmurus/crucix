// R-F3086 capability test — 2FA must actually work, and must not accept a
// wrong code.
//
// BROKEN PATH (reproduced live 2026-07-25 against a running server): otplib v13
// (package.json pins ^13.4.0) removed the `TOTP.verify()` static and changed
// `generateURI` to a single options object. Four call sites still used the v12
// shapes, so the whole subsystem was dead:
//   POST /api/auth/2fa/setup        → 500 {"error":"2FA setup failed"}
//     ([Auth] 2FA setup error: Cannot read properties of undefined (reading 'split'))
//   POST /api/auth/2fa/enable       → 500  (TOTP.verify is not a function)
//   POST /api/auth/2fa/authenticate → 500
//   POST /api/auth/2fa/disable      → 500
//
// Scope, precisely: 2FA has NEVER worked. The feature commit (7a5e29d3) added no
// otplib dependency; 045ffdae added it the same day at ^13.4.0, so the v12 shapes
// this code targets were never installed. Nobody is locked out (twoFactorEnabled
// can only be set by /2fa/enable, which always 500'd) — but the security feature
// has been dead since it shipped, and signin.html:174 already handles requires2FA,
// so it WOULD have locked out anyone who got it enabled.
//
// The trap this test exists to hold: the v13 replacement `verifySync()` returns
// an OBJECT `{valid, delta, ...}`, not a boolean. A naive swap leaves
// `if (!valid)` compiling while `{valid:false}` is TRUTHY — every code accepted,
// a silent auth BYPASS strictly worse than the outage. So this asserts the
// NEGATIVE case as hard as the positive one.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SERVER = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');

test('the installed otplib really does reject the v12 call shapes', async () => {
  // Pins the PREMISE. If a future otplib restores these, the fix should be
  // revisited rather than this test deleted.
  const otplib = await import('otplib');
  assert.equal(typeof otplib.TOTP?.verify, 'undefined',
    'TOTP.verify() is a v12 API — if it came back, re-check the call sites');
  assert.throws(() => otplib.generateURI('TOTP', { label: 'a@b.com', secret: otplib.generateSecret(), issuer: 'X' }),
    'the v12 generateURI(strategy, opts) form must still throw — that is the bug');
});

test('a correct code verifies and a wrong one does NOT (the bypass trap)', async () => {
  const { generateSecret, generateSync, verifySync } = await import('otplib');
  const secret = generateSecret();

  // Mirror the server helper exactly: read `.valid`, never the object.
  const check = (code) => verifySync({ token: String(code).replace(/\s/g, ''), secret })?.valid === true;

  const good = generateSync({ secret });
  assert.equal(check(good), true, 'the current TOTP code must verify');

  const bad = String((Number(good) + 111111) % 1000000).padStart(6, '0');
  assert.equal(check(bad), false, 'a wrong code must be REJECTED');

  // The exact mistake this guards against:
  const raw = verifySync({ token: bad, secret });
  assert.equal(typeof raw, 'object', 'verifySync returns an object, not a boolean');
  assert.ok(raw, 'and that object is TRUTHY even when the code is wrong — which is '
    + 'why `if (!verifySync(...))` would accept every code');
});

test('server.mjs uses the v13 API through one helper, on every route', () => {
  assert.ok(!/TOTP\.verify\s*\(/.test(SERVER.replace(/^\s*\/\/.*$/gm, '')),
    'no route may call the removed v12 TOTP.verify()');
  assert.ok(!/generateURI\(\s*['"]TOTP['"]/.test(SERVER.replace(/^\s*\/\/.*$/gm, '')),
    'no route may call the v12 generateURI(strategy, opts) form');

  const helper = SERVER.slice(SERVER.indexOf('async function verifyTotpCode'),
                              SERVER.indexOf('// ── 2FA: verify TOTP code after password'));
  assert.match(helper, /verifySync/, 'the helper must use the v13 verifySync');
  assert.match(helper, /\.valid\s*===\s*true/,
    'the helper MUST compare .valid explicitly — a truthy check on the result '
    + 'object accepts every code (auth bypass)');

  // R-F4012 (C-89) — was `assert.equal(calls.length, 3)`, naming authenticate +
  // enable + disable. A FOURTH code-checking route was added since (the WhatsApp
  // linked-device governance step-up, server.mjs ~1451) and correctly uses the
  // shared helper — so the guard went red on a correct addition while the property
  // it exists to protect was never violated.
  //
  // The property is that NO route verifies a TOTP itself. That is now asserted
  // directly: `verifySync` may appear only inside the helper. The count is kept as
  // a review tripwire on top, so a fifth route still has to be looked at rather
  // than appearing silently — but it can no longer fail for the mere fact of
  // growing.
  // Comments stripped first, exactly as the two assertions above already do: the
  // note explaining that "the v13 replacement is `verifySync()`" contains a
  // literal call and would otherwise be counted as one. Prose must never fail a
  // test about behaviour.
  const stripComments = (s) => s.replace(/^\s*\/\/.*$/gm, '');
  const directVerify = (stripComments(SERVER).match(/verifySync\s*\(/g) || []).length;
  const inHelper = (stripComments(helper).match(/verifySync\s*\(/g) || []).length;
  assert.equal(directVerify, inHelper,
    'verifySync() must be called ONLY inside verifyTotpCode — a route that checks '
    + 'a code itself bypasses the .valid===true comparison and accepts every code');

  const calls = SERVER.match(/await verifyTotpCode\(/g) || [];
  assert.equal(calls.length, 4,
    'reviewed code-checking routes: 2FA authenticate, enable, disable, and the '
    + `WhatsApp linked-device step-up. Found ${calls.length} — if a route was added, `
    + 'confirm it uses the helper and update this count deliberately');
});

test('2FA setup builds a scannable otpauth URI for this issuer', async () => {
  const { generateSecret, generateURI } = await import('otplib');
  const secret = generateSecret();
  const uri = generateURI({
    strategy: 'totp',
    issuer: 'Arkmurus Intelligence',
    label: 'analyst@example.com',
    secret,
  });
  assert.match(uri, /^otpauth:\/\/totp\//, 'authenticator apps need an otpauth://totp URI');
  assert.match(uri, /Arkmurus%20Intelligence/, 'the issuer must survive into the URI');
  assert.match(uri, /secret=/, 'the URI must carry the secret');
});
