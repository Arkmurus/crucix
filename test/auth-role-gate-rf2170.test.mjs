// test/auth-role-gate-rf2170.test.mjs
//
// CAPABILITY test for R-F2170 — the customer/support/admin role model.
// It invokes the REAL decision functions used by the live code paths:
//   - server.mjs requireRole(...) gate  -> roleSatisfies()         (lib/auth/roles.mjs)
//   - server.mjs PUT /api/admin/users/:id role validation -> isValidRole()/ROLES
//   - aria-app middleware/layouts panel routing -> decodeToken()/roleAllows()/homeForRole()
//     (aria-app/lib/auth.ts — imported via node's TS type-stripping)
//
// Run: node test/auth-role-gate-rf2170.test.mjs

import { ROLES, roleSatisfies, isValidRole } from '../lib/auth/roles.mjs';

let failures = 0;
function check(name, cond) {
  console.log(`${cond ? 'ok  ' : 'FAIL'} - ${name}`);
  if (!cond) failures++;
}

// ── Backend gate decision (requireRole) ──────────────────────────────────────
check('admin satisfies admin-only', roleSatisfies('admin', ['admin']) === true);
check('viewer denied admin-only', roleSatisfies('viewer', ['admin']) === false);
check('support denied admin-only', roleSatisfies('support', ['admin']) === false);
check('support satisfies support route', roleSatisfies('support', ['support', 'admin']) === true);
check('admin is superset of support', roleSatisfies('admin', ['support']) === true);
check('viewer denied support route', roleSatisfies('viewer', ['support', 'admin']) === false);
check('empty allowed -> deny', roleSatisfies('admin', []) === false);
check('non-array allowed -> deny', roleSatisfies('admin', null) === false);
check('unknown role denied', roleSatisfies('hacker', ['admin']) === false);

// ── Backend role validation (PUT /api/admin/users/:id) ───────────────────────
check('ROLES is exactly [viewer,support,poweruser,admin]', JSON.stringify(ROLES) === JSON.stringify(['viewer', 'support', 'poweruser', 'admin']));  // R-F2773 added poweruser
check('ROLES frozen', Object.isFrozen(ROLES));
check('isValidRole(support) true', isValidRole('support') === true);
check('isValidRole(viewer) true', isValidRole('viewer') === true);
check('isValidRole(admin) true', isValidRole('admin') === true);
check('isValidRole(customer) false (backend uses viewer)', isValidRole('customer') === false);
check('isValidRole(garbage) false', isValidRole('superuser') === false);
check('isValidRole(undefined) false', isValidRole(undefined) === false);

// ── Frontend decoder (aria-app) against the REAL backend token shape ─────────
// server.mjs createToken() emits base64url(JSON({userId,role,ver,iat,exp})).'sig'
// where exp/iat are epoch MILLISECONDS. Build a matching token (sig irrelevant —
// decodeToken does NOT verify, the backend does).
function makeBackendToken(payload) {
  const data = Buffer.from(JSON.stringify(payload)).toString('base64url');
  return `${data}.signature-not-verified-by-frontend`;
}

const frontend = await import('../aria-app/lib/auth.ts');
const { decodeToken, roleAllows, homeForRole } = frontend;

const now = Date.now();
const viewerTok = makeBackendToken({ userId: 'abc123', role: 'viewer', ver: 0, iat: now, exp: now + 7 * 864e5 });
const adminTok = makeBackendToken({ userId: 'admin1', role: 'admin', ver: 1, iat: now, exp: now + 7 * 864e5 });
const supportTok = makeBackendToken({ userId: 'sup1', role: 'support', ver: 0, iat: now, exp: now + 7 * 864e5 });
const expiredTok = makeBackendToken({ userId: 'old1', role: 'viewer', ver: 0, iat: now - 9e8, exp: now - 1000 });

const v = decodeToken(viewerTok);
check('decode reads userId (not id/sub)', v?.id === 'abc123');
check('viewer role -> customer panel', v?.role === 'customer');
check('admin role preserved', decodeToken(adminTok)?.role === 'admin');
check('support role preserved', decodeToken(supportTok)?.role === 'support');
check('ms-exp NOT treated as seconds (valid token accepted)', decodeToken(viewerTok) !== null);
check('expired (ms) token rejected', decodeToken(expiredTok) === null);
check('malformed token rejected', decodeToken('garbage') === null);
check('empty token rejected', decodeToken('') === null);

// Panel routing.
check('homeForRole(customer) -> /dashboard', homeForRole('customer') === '/dashboard');
check('homeForRole(support) -> /support', homeForRole('support') === '/support');
check('homeForRole(admin) -> /admin', homeForRole('admin') === '/admin');
check('customer cannot reach admin area', roleAllows('customer', ['admin']) === false);
check('admin can reach support area', roleAllows('admin', ['support']) === true);
check('support cannot reach admin area', roleAllows('support', ['admin']) === false);

console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
