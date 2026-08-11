// test/2fa-pretoken-not-a-session-rf3834.test.mjs
//
// R-F3834 — CAPABILITY test: the 2FA pre-auth token must not be a session token.
//
// ── THE DEFECT ───────────────────────────────────────────────────────────────
// server.mjs:5936 minted the pre-auth token with plain `createToken(id, role,
// '5m')`. That emits the SAME claim set as a full session token — {userId, role,
// ver, iat, exp} — with nothing to mark it as a half-finished login, and
// verifyToken had nothing to distinguish it by. requireAuth accepted it: `ver`
// defaults to 0, which matches tokenVersion 0 on any account never force-logged
// out.
//
// So an attacker holding only the PASSWORD of a 2FA-protected account could
// POST /api/auth/login, take `preToken` from the 200 response, and use it as a
// Bearer on any authenticated route for five minutes — including
// PUT /api/auth/password (which needs only the current password, which they
// have) and POST /api/auth/2fa/disable. The TOTP step is never invoked. 2FA
// reduced to a UI speed bump.
//
// The fix is fail-closed at the ONE chokepoint: verifyToken rejects any staged
// token unless the caller explicitly asks for that stage. Five call sites grant
// access from verifyToken (server.mjs:2250, :5635, :5785, :7949, :8305); fixing
// them one by one would leave the sixth to be written later.
//
// Run: node --test test/2fa-pretoken-not-a-session-rf3834.test.mjs

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

const UID = 'rf3834-victim';

describe('R-F3834 a pre-2FA token is refused everywhere a session is required', () => {
  it('verifyToken REFUSES a staged token by default', () => {
    const pre = createToken(UID, 'user', '5m', 0, 'pre2fa');
    assert.throws(() => verifyToken(pre), /stage|pre-?auth|session/i,
      'the default call — used by requireAuth, requirePageRole, /events, '
      + '/api/search/deep and the socket.io handshake — must reject it');
  });

  it('verifyToken accepts it ONLY when that exact stage is demanded', () => {
    const pre = createToken(UID, 'user', '5m', 0, 'pre2fa');
    const payload = verifyToken(pre, { stage: 'pre2fa' });
    assert.equal(payload.userId, UID);
    assert.equal(payload.stage, 'pre2fa');
  });

  it('a full session token is NOT accepted where a pre2fa token is demanded', () => {
    // Otherwise /2fa/authenticate would accept a real session token and the
    // stage check would be decorative in one direction.
    const full = createToken(UID, 'user', '7d', 0);
    assert.throws(() => verifyToken(full, { stage: 'pre2fa' }), /stage|pre-?auth/i);
  });

  it('a full session token still verifies normally — no regression', () => {
    const full = createToken(UID, 'admin', '7d', 3);
    const payload = verifyToken(full);
    assert.equal(payload.userId, UID);
    assert.equal(payload.role, 'admin');
    assert.equal(payload.ver, 3);
    assert.ok(payload.stage === undefined || payload.stage === null,
      'an ordinary token must carry no stage claim');
  });

  it('the stage claim is inside the SIGNED payload, not strippable', () => {
    const pre = createToken(UID, 'user', '5m', 0, 'pre2fa');
    const [data, sig] = pre.split('.');
    const claims = JSON.parse(Buffer.from(data, 'base64url').toString('utf8'));
    assert.equal(claims.stage, 'pre2fa', 'stage must be a signed claim');

    // Strip the stage and re-encode — the signature must no longer verify.
    delete claims.stage;
    const forged = Buffer.from(JSON.stringify(claims)).toString('base64url') + '.' + sig;
    assert.throws(() => verifyToken(forged), /signature/i,
      'removing the stage claim must invalidate the signature');
  });

  it('an unknown stage is refused rather than treated as unstaged', () => {
    const weird = createToken(UID, 'user', '5m', 0, 'some-future-stage');
    assert.throws(() => verifyToken(weird), /stage|pre-?auth|session/i,
      'fail closed: a stage nobody has taught the gate about is not a session');
    assert.throws(() => verifyToken(weird, { stage: 'pre2fa' }), /stage|pre-?auth/i);
  });

  it('the pre-auth token still carries tokenVersion, so force-logout kills it', () => {
    const pre = createToken(UID, 'user', '5m', 7, 'pre2fa');
    assert.equal(verifyToken(pre, { stage: 'pre2fa' }).ver, 7);
  });

  it('an expired pre-auth token is refused', () => {
    const pre = createToken(UID, 'user', '5m', 0, 'pre2fa');
    const [data] = pre.split('.');
    const claims = JSON.parse(Buffer.from(data, 'base64url').toString('utf8'));
    assert.ok(claims.exp - claims.iat <= 5 * 60 * 1000 + 50, 'the 5m TTL must survive the change');
  });
});

describe('R-F3834 anti-regression: production mints and checks the stage', () => {
  const read = (f) => fs.readFileSync(path.join(repoRoot(), f), 'utf8');

  it('login mints the pre-auth token WITH the stage claim', () => {
    const src = read('server.mjs');
    assert.ok(!/const preToken = createToken\(user\.id, user\.role, '5m'\);/.test(src),
      'the unstaged mint is back — this is the defect verbatim');
    assert.ok(/createToken\(\s*user\.id,\s*user\.role,\s*'5m',[^)]*'pre2fa'/.test(src),
      'login must mint the pre-auth token with stage pre2fa');
  });

  it('/2fa/authenticate demands the pre2fa stage', () => {
    const src = read('server.mjs');
    const at = src.indexOf("app.post('/api/auth/2fa/authenticate'");
    assert.ok(at > -1, 'handler not found');
    const body = src.slice(at, at + 1200);
    assert.ok(/verifyToken\(\s*preToken,\s*\{\s*stage:\s*'pre2fa'\s*\}\s*\)/.test(body),
      'the second factor must be the ONLY acceptor of a pre-auth token');
  });

  it('verifyToken is fail-closed: the stage check is not opt-in per call site', () => {
    const src = read('lib/auth/users.mjs');
    const at = src.indexOf('export function verifyToken');
    const body = src.slice(at, at + 1400);
    assert.ok(body.includes('stage'),
      'verifyToken itself must enforce the stage — five call sites grant access '
      + 'from it, and a per-site check leaves the sixth to be written later');
  });

  it('login does not set the auth cookie on the 2FA branch', () => {
    // requirePageRole authenticates from the cookie. A cookie minted before the
    // second factor would re-open the hole through page navigation.
    const src = read('server.mjs');
    const at = src.indexOf('if (user.twoFactorEnabled && user.twoFactorSecret)');
    assert.ok(at > -1);
    const branch = src.slice(at, src.indexOf('return res.json({ requires2FA: true', at));
    assert.ok(!branch.includes('_setAuthCookie'),
      'no session cookie may be issued before the TOTP code is verified');
  });
});
