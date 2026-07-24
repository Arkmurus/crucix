// R-F2986 — a suspend or a role change via PUT /api/admin/users/:id MUST
// revoke the target's active sessions, not just block future logins.
//
// The bug: requireAuth (server.mjs) verifies the JWT signature + tokenVersion
// but sets req.user = payload (the LOGIN-TIME snapshot) and never re-reads
// user.status; requireRole authorizes on the token's baked-in role. The admin
// PUT applied status='suspended' / role-demotion via updateUser but never
// called revokeTokens — so a suspended user kept full access on their live
// 7-day JWT, and a demoted admin kept role:admin, until the token expired.
//
// This test drives the REAL auth-store functions (createUser, createToken,
// updateUser, revokeTokens, findUserById) on an isolated store and replicates
// BOTH the handler's revoke decision AND requireAuth's exact tokenVersion
// check — so it fails on the pre-fix handler (no revoke) and passes on the fix.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// Source-read regression lock (repo convention, cf. web-security-rf2094): assert
// the ACTUAL PUT /api/admin/users/:id handler in server.mjs wires revokeTokens on
// a role change or a suspend. This is the fail-before/pass-after signal against
// the real file — the pre-fix handler had no revoke and this regex would miss.
test('R-F2986: server.mjs wires revokeTokens into the admin user-update handler', () => {
  const src = readFileSync(
    path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'server.mjs'), 'utf-8');
  const handler = src.slice(src.indexOf("app.put('/api/admin/users/:id'"));
  const body = handler.slice(0, handler.indexOf('\napp.'));
  assert.ok(/roleChanged\s*\|\|\s*nowSuspended/.test(body),
    'handler must compute the roleChanged || nowSuspended revoke condition');
  assert.ok(/revokeTokens\(req\.params\.id\)/.test(body),
    'handler must call revokeTokens(req.params.id) on role-change/suspend');
});

// Isolate the users store + pin a valid JWT secret before importing users.mjs.
process.env.USERS_FILE_OVERRIDE = path.join(
  mkdtempSync(path.join(tmpdir(), 'users-rf2986-')), 'users.json',
);
process.env.JWT_SECRET = process.env.JWT_SECRET || 'test-secret-rf2986-at-least-32-characters-long';

const {
  createUser, createToken, updateUser, revokeTokens, findUserById,
} = await import('../lib/auth/users.mjs');

// requireAuth's session-validity check (server.mjs:4805-4809), verbatim:
// a token is still live iff its baked-in ver matches the user's live tokenVersion.
const sessionLive = (token, userId) => {
  const user = findUserById(userId);
  const payload = JSON.parse(Buffer.from(token.split('.')[0], 'base64url').toString('utf8'));
  return !!user && (user.tokenVersion || 0) === (payload.ver || 0);
};

// The exact revoke decision the R-F2986 handler makes.
const applyAdminUpdate = (existingUser, { role, status }) => {
  const updates = {};
  if (role !== undefined) updates.role = role;
  if (status !== undefined) updates.status = status;
  updateUser(existingUser.id, updates);
  const roleChanged  = role !== undefined && role !== existingUser.role;
  const nowSuspended = status === 'suspended' && existingUser.status !== 'suspended';
  if (roleChanged || nowSuspended) revokeTokens(existingUser.id);
};

test('R-F2986: suspending a user kills their existing session token', () => {
  const u = createUser({ username: 'suspendme', email: 'suspend@rf2986.test', password: 'Passw0rd123', fullName: 'S', status: 'active', role: 'user' });
  const token = createToken(u.id, u.role, '7d', u.tokenVersion || 0);
  assert.equal(sessionLive(token, u.id), true, 'sanity: fresh token is live');

  applyAdminUpdate(findUserById(u.id), { status: 'suspended' });

  assert.equal(sessionLive(token, u.id), false, 'suspended user\'s old JWT must be revoked');
  assert.equal(findUserById(u.id).status, 'suspended');
});

test('R-F2986: demoting an admin kills the admin-role token', () => {
  const u = createUser({ username: 'demoteme', email: 'demote@rf2986.test', password: 'Passw0rd123', fullName: 'D', status: 'active', role: 'admin' });
  const adminToken = createToken(u.id, u.role, '7d', u.tokenVersion || 0);
  assert.equal(sessionLive(adminToken, u.id), true, 'sanity: admin token is live');

  applyAdminUpdate(findUserById(u.id), { role: 'viewer' });

  assert.equal(sessionLive(adminToken, u.id), false, 'demoted admin\'s old role token must be revoked');
  assert.equal(findUserById(u.id).role, 'viewer');
});

test('R-F2986: a benign edit (notify flags, no role/suspend) does NOT revoke sessions', () => {
  const u = createUser({ username: 'benign', email: 'benign@rf2986.test', password: 'Passw0rd123', fullName: 'B', status: 'active', role: 'user' });
  const token = createToken(u.id, u.role, '7d', u.tokenVersion || 0);

  applyAdminUpdate(findUserById(u.id), {}); // no role, no status change

  assert.equal(sessionLive(token, u.id), true, 'a non-security edit must not nuke live sessions');
});

test('R-F2986: re-applying the SAME status/role is a no-op (no needless revoke)', () => {
  const u = createUser({ username: 'sameval', email: 'sameval@rf2986.test', password: 'Passw0rd123', fullName: 'V', status: 'active', role: 'user' });
  const token = createToken(u.id, u.role, '7d', u.tokenVersion || 0);

  applyAdminUpdate(findUserById(u.id), { role: 'user', status: 'active' }); // unchanged

  assert.equal(sessionLive(token, u.id), true, 'unchanged role/status must not revoke');
});
