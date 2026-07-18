// R-F2773 — the `poweruser` role: view-only operator/infra access, NOT admin.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ROLES, roleSatisfies, isValidRole } from '../lib/auth/roles.mjs';

test('poweruser is a real, assignable role', () => {
  assert.ok(ROLES.includes('poweruser'));
  assert.ok(isValidRole('poweruser'));
  assert.equal(isValidRole('analyst'), false); // the old stale dropdown value is NOT valid
});

test('poweruser satisfies a poweruser gate but NOT an admin-only gate', () => {
  assert.equal(roleSatisfies('poweruser', ['poweruser', 'admin']), true);  // view infra page
  assert.equal(roleSatisfies('poweruser', ['admin']), false);              // admin-only action (vault write, WA pairing)
  assert.equal(roleSatisfies('poweruser', ['support']), false);            // not a support console
});

test('admin is a superset — satisfies poweruser AND support gates (operator never locked out)', () => {
  assert.equal(roleSatisfies('admin', ['poweruser']), true);
  assert.equal(roleSatisfies('admin', ['poweruser', 'admin']), true);
  assert.equal(roleSatisfies('admin', ['support']), true);
  assert.equal(roleSatisfies('admin', ['admin']), true);
});

test('viewer / support do NOT satisfy a poweruser gate', () => {
  assert.equal(roleSatisfies('viewer', ['poweruser', 'admin']), false);
  assert.equal(roleSatisfies('support', ['poweruser', 'admin']), false);
  assert.equal(roleSatisfies(undefined, ['poweruser', 'admin']), false);   // localhost-bypass (no req.user)
});
