// test/proxy-idor-pin-rf2211.test.mjs
//
// CAPABILITY test for R-F2211 — central IDOR guard on the aria-web → aria-intel
// catch-all proxy. It invokes the ACTUAL functions the catch-all uses
// (pinNonAdminUserId / isPrivileged from lib/auth/proxyPin.mjs) and asserts the
// user-visible security outcome: a non-admin can never forge ?user_id, while an
// admin keeps see-all. Plus a source-contract lock that the catch-all in
// server.mjs actually calls the guard (so a future edit can't drop it).
//
// Run: node test/proxy-idor-pin-rf2211.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { pinNonAdminUserId, isPrivileged } from '../lib/auth/proxyPin.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
let failures = 0;
function check(name, cond) {
  console.log(`${cond ? 'ok  ' : 'FAIL'} - ${name}`);
  if (!cond) failures++;
}
// extract the user_id query value from a rewritten path
function uid(path) {
  const qi = path.indexOf('?');
  if (qi === -1) return null;
  return new URLSearchParams(path.slice(qi + 1)).get('user_id');
}

const viewer = { userId: 'u_alice', role: 'viewer' };
const admin = { userId: 'u_boss', role: 'admin' };
const internal = { id: 'aria-internal', role: 'admin' };

// ── The exploit: non-admin forging another tenant's user_id ───────────────────
check('non-admin forging ?user_id=victim is overridden to their own id',
  uid(pinNonAdminUserId('/api/aria/dd/reports?user_id=u_victim', viewer)) === 'u_alice');
check('non-admin passing NO user_id gets their own injected (not empty=see-all)',
  uid(pinNonAdminUserId('/api/aria/dd/reports', viewer)) === 'u_alice');
check('non-admin empty ?user_id= is overridden to their own id (not see-all)',
  uid(pinNonAdminUserId('/api/aria/dd/reports?user_id=', viewer)) === 'u_alice');
check('other query params are preserved when pinning',
  pinNonAdminUserId('/api/aria/dd/reports?since_hours=24&user_id=u_victim', viewer)
    .includes('since_hours=24'));

// ── Admin / internal keep see-all (must NOT be clamped) ───────────────────────
check('admin query is returned verbatim (see-all preserved)',
  pinNonAdminUserId('/api/aria/dd/reports?user_id=u_target', admin) === '/api/aria/dd/reports?user_id=u_target');
check('internal service token is privileged (see-all)',
  isPrivileged(internal) === true);
check('admin is privileged', isPrivileged(admin) === true);
check('viewer is NOT privileged', isPrivileged(viewer) === false);
check('undefined user is NOT privileged (fails closed)', isPrivileged(undefined) === false);

// ── Never throws on malformed input ───────────────────────────────────────────
check('missing userId → empty string injected, no throw',
  uid(pinNonAdminUserId('/api/aria/x?user_id=v', { role: 'viewer' })) === '');

// ── Source-contract: the catch-all actually uses the guard ────────────────────
const SERVER = readFileSync(join(__dirname, '..', 'server.mjs'), 'utf8');
check('server.mjs imports the guard', /from '\.\/lib\/auth\/proxyPin\.mjs'/.test(SERVER));
const catchAllIdx = SERVER.indexOf("app.use('/api/aria', requireAuth");
const catchAllBody = SERVER.slice(catchAllIdx, catchAllIdx + 1600);
check('catch-all rewrites path via pinNonAdminUserId(req.originalUrl, req.user)',
  catchAllBody.includes('pinNonAdminUserId(req.originalUrl, req.user)'));
check('catch-all pins body.user_id for non-admin POST/PUT/PATCH',
  catchAllBody.includes('req.body.user_id = (req.user && req.user.userId)'));
check('catch-all no longer forwards the raw req.originalUrl verbatim',
  !/const fullPath = req\.originalUrl;/.test(catchAllBody));

// ── R-F3167: vetting is pinned for EVERYONE, admin included ──────────────
//
// Live symptom: /vetting.html showed "Could not load cases (HTTP 400)" for an
// admin. pinNonAdminUserId returns the URL unchanged for privileged users —
// correct for DD, where an operator legitimately reviews every report — so the
// request carried NO user_id and the brain's strict tenant check refused it.
//
// Vetting has no admin see-all BY DESIGN: a case holds criminal-conviction data
// about a named individual. Admin is still an identity, so pinning gives them
// their own tenant. That is the correct answer, not a lesser one.
const ADMIN = { role: 'admin', userId: 'admin-1' };
const VIEWER = { role: 'viewer', userId: 'u-9' };
const forged = pinNonAdminUserId('/api/aria/vetting/cases?user_id=someone-else', ADMIN);

check('R-F3167 admin IS pinned on /api/aria/vetting',
  /user_id=admin-1/.test(pinNonAdminUserId('/api/aria/vetting/cases', ADMIN)));
check('R-F3167 admin cannot forge user_id on vetting',
  forged.includes('user_id=admin-1') && !forged.includes('someone-else'));
check('R-F3167 DD see-all for admins is UNCHANGED (no collateral damage)',
  pinNonAdminUserId('/api/aria/dd/reports', ADMIN) === '/api/aria/dd/reports');
check('R-F3167 a prefix lookalike is not a vetting path',
  pinNonAdminUserId('/api/aria/vettingsomething', ADMIN) === '/api/aria/vettingsomething');
check('R-F3167 non-admins are still pinned on vetting',
  /user_id=u-9/.test(pinNonAdminUserId('/api/aria/vetting/cases?user_id=victim', VIEWER)));

console.log(failures === 0 ? '\nR-F2211 tests: PASS' : `\nR-F2211 tests: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
