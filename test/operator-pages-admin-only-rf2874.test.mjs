// test/operator-pages-admin-only-rf2874.test.mjs
//
// R-F2874 — ARIA Brain and Source Health are ADMIN-ONLY.
//
// Operator direction 2026-07-22: "ensure it is only the admin that have access to
// those tabs and no other user". Brain, Source Health and Vault expose the
// platform's own internals — brain/knowledge state, per-source reliability and
// the signup vault. Vault was already admin-only; Brain and Source Health sat in
// OPERATOR_VIEW_PAGES, which roleSatisfies() grants to `poweruser` as well.
//
// No user currently holds `poweruser` (live /data/users.json: one admin, three
// viewers), so this changes nothing operationally today — it closes the path
// before a poweruser is ever created, rather than after.
//
// This moves BOTH the page gate and the nav entitlement at once, because both
// read these same tables (server.mjs requirePageRole + navPagesForRole). That is
// the property R-F2822 introduced and this ticket must not break: one table, so
// the nav cannot drift from the gate.
//
// Run: node --test test/operator-pages-admin-only-rf2874.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  OPERATOR_VIEW_PAGES,
  OPERATOR_ADMIN_PAGES,
  navPagesForRole,
  operatorPageFor,
} from '../lib/auth/operatorPages.mjs';

const ADMIN_ONLY = ['/aria-brain', '/aria-brain.html', '/sources.html',
                    '/vault.html', '/vault.htm', '/admin.html',
                    '/wa-connections.html', '/leads.html'];

test('R-F2874: brain, source health and vault are in the ADMIN table', () => {
  const adminRoutes = OPERATOR_ADMIN_PAGES.map(([r]) => r);
  for (const route of ['/aria-brain', '/aria-brain.html', '/sources.html']) {
    assert.ok(adminRoutes.includes(route), `${route} must be admin-only`);
  }
});

test('R-F2874: they are NOT in the poweruser-visible table', () => {
  const viewRoutes = OPERATOR_VIEW_PAGES.map(([r]) => r);
  for (const route of ['/aria-brain', '/aria-brain.html', '/sources.html']) {
    assert.ok(!viewRoutes.includes(route),
      `${route} must not remain in OPERATOR_VIEW_PAGES`);
  }
});

test('R-F2874: a poweruser can no longer reach any of them', () => {
  const allowed = navPagesForRole('poweruser');
  for (const route of ADMIN_ONLY) {
    assert.ok(!allowed.includes(route),
      `poweruser must NOT be entitled to ${route}, got: ${allowed.join(',')}`);
  }
});

test('R-F2874: the admin still gets everything', () => {
  const allowed = navPagesForRole('admin');
  for (const route of ADMIN_ONLY) {
    assert.ok(allowed.includes(route), `admin must still reach ${route}`);
  }
  // and the pages that legitimately stay poweruser-visible
  for (const route of ['/vls-chain.html', '/bd-intelligence.html',
                       '/design-partners.html']) {
    assert.ok(allowed.includes(route), `admin must still reach ${route}`);
  }
});

test('R-F2874: a viewer gets nothing (unchanged)', () => {
  assert.deepEqual(navPagesForRole('viewer'), []);
  assert.deepEqual(navPagesForRole(undefined), []);
});

test('R-F2874: the GATE agrees with the nav — one table, no drift', () => {
  // R-F2822's whole point. If these two ever disagree the nav starts lying again.
  for (const route of ['/aria-brain', '/sources.html', '/vault.html']) {
    const page = operatorPageFor(route);
    assert.ok(page, `${route} must still resolve to a page`);
    assert.deepEqual(page.roles, ['admin'],
      `${route} must demand admin at the GATE, not just in the nav`);
  }
});

test('R-F2874: NEGATIVE CONTROL — poweruser keeps the pages it should have', () => {
  // Tightening must be surgical: it must not silently strip a poweruser's
  // legitimate access to the other operator pages.
  const allowed = navPagesForRole('poweruser');
  for (const route of ['/vls-chain.html', '/bd-intelligence.html',
                       '/design-partners.html']) {
    assert.ok(allowed.includes(route),
      `poweruser must KEEP ${route} — this ticket only moves brain/sources`);
  }
});

test('R-F2874: every page still resolves to a real file', () => {
  for (const [route, file] of [...OPERATOR_VIEW_PAGES, ...OPERATOR_ADMIN_PAGES]) {
    assert.ok(typeof file === 'string' && file.endsWith('.html'),
      `${route} must map to an .html file, got ${file}`);
  }
});
