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

console.log(failures === 0 ? '\nR-F2211 tests: PASS' : `\nR-F2211 tests: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
