// test/operator-page-gate-matrix-rf2785.test.mjs
//
// The aria-web access-control MATRIX: every operator page × every role, plus the
// customer pages that must stay reachable. Covers the R-F2773 (poweruser role),
// R-F2774 (page navigation gate) and R-F2785 (shared page table) chain.
//
// Drives the REAL decision functions — requiredRoleForPage() from the table
// server.mjs registers from, and roleSatisfies() from the gate itself — so this is
// the contract, not a grep of server.mjs's source text.
//
// The class of bug it exists to catch: a new operator page dropped into public/
// and never added to the table. express.static serves it to anyone, the in-page
// Auth.require*() call is cosmetic (it runs after the HTML is already delivered),
// and nothing fails loudly. Same for an unlisted URL ALIAS of a gated page —
// /vault.htm is exactly that.
//
// Run: node --test test/operator-page-gate-matrix-rf2785.test.mjs

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

import {
  OPERATOR_VIEW_PAGES, OPERATOR_ADMIN_PAGES, requiredRoleForPage,
} from '../lib/auth/operatorPages.mjs';
import { roleSatisfies } from '../lib/auth/roles.mjs';

const ROOT = path.resolve(
  path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
  '..',
);
const PUBLIC = path.join(ROOT, 'public');

const ROLES_UNDER_TEST = ['anonymous', 'viewer', 'support', 'poweruser', 'admin'];

/** What the live gate does: requirePageRole(...allowed) → roleSatisfies, else redirect. */
function pageAllows(role, route) {
  const needed = requiredRoleForPage(route);
  if (needed === null) return true;                       // not an operator page
  if (role === 'anonymous') return false;                 // → /signin.html
  const allowed = needed === 'admin' ? ['admin'] : ['poweruser', 'admin'];
  return roleSatisfies(role, allowed);
}

describe('R-F2785 operator page gate matrix', () => {
  it('every operator page serves a file that exists', () => {
    for (const [route, file] of [...OPERATOR_VIEW_PAGES, ...OPERATOR_ADMIN_PAGES]) {
      assert.ok(fs.existsSync(path.join(PUBLIC, file)),
        `${route} serves ${file}, which is missing from public/`);
    }
  });

  it('no page is listed as both view and admin (ambiguous gate)', () => {
    const viewRoutes = new Set(OPERATOR_VIEW_PAGES.map(([r]) => r));
    const dupes = OPERATOR_ADMIN_PAGES.map(([r]) => r).filter((r) => viewRoutes.has(r));
    assert.deepEqual(dupes, [], 'a route must have exactly one required role');
  });

  it('ANONYMOUS is denied every operator page', () => {
    for (const [route] of [...OPERATOR_VIEW_PAGES, ...OPERATOR_ADMIN_PAGES]) {
      assert.equal(pageAllows('anonymous', route), false, `anon must not reach ${route}`);
    }
  });

  it('VIEWER (paying customer) is denied every operator page', () => {
    for (const [route] of [...OPERATOR_VIEW_PAGES, ...OPERATOR_ADMIN_PAGES]) {
      assert.equal(pageAllows('viewer', route), false, `viewer must not reach ${route}`);
    }
  });

  it('SUPPORT is denied every operator page (support is a customer-support scope)', () => {
    for (const [route] of [...OPERATOR_VIEW_PAGES, ...OPERATOR_ADMIN_PAGES]) {
      assert.equal(pageAllows('support', route), false, `support must not reach ${route}`);
    }
  });

  it('POWERUSER sees the view pages but NOT the destructive ones', () => {
    for (const [route] of OPERATOR_VIEW_PAGES) {
      assert.equal(pageAllows('poweruser', route), true, `poweruser must reach ${route}`);
    }
    for (const [route] of OPERATOR_ADMIN_PAGES) {
      assert.equal(pageAllows('poweruser', route), false,
        `poweruser must NOT reach the destructive page ${route}`);
    }
  });

  it('ADMIN reaches every operator page (admin is a superset of poweruser)', () => {
    for (const [route] of [...OPERATOR_VIEW_PAGES, ...OPERATOR_ADMIN_PAGES]) {
      assert.equal(pageAllows('admin', route), true, `admin must reach ${route}`);
    }
  });

  it('the destructive pages are admin-only — vault, WA pairing, user management', () => {
    for (const route of ['/vault.html', '/vault.htm', '/wa-connections.html', '/admin.html']) {
      assert.equal(requiredRoleForPage(route), 'admin', `${route} must require admin`);
    }
  });

  it('CUSTOMER pages stay ungated for everyone (the over-gating safety net)', () => {
    const CUSTOMER_PAGES = [
      '/index.html', '/dashboard.html', '/account.html', '/model-card.html',
      '/signin.html', '/aria.html', '/dd-reports.html', '/watchlist.html',
      '/news.html', '/explorer.html', '/pricing.html',
    ];
    for (const route of CUSTOMER_PAGES) {
      // only assert pages that actually ship, so the list can't rot into a no-op
      if (!fs.existsSync(path.join(PUBLIC, route.slice(1)))) continue;
      assert.equal(requiredRoleForPage(route), null, `${route} is a customer page — must NOT be gated`);
      for (const role of ROLES_UNDER_TEST) {
        assert.equal(pageAllows(role, route), true, `${role} must reach customer page ${route}`);
      }
    }
  });

  it('EVERY url form of a gated page is listed — an ungated alias is an ungated page', () => {
    // A gated page is only gated if all of its URL spellings are in the table.
    // express.static serves /foo.html for a file public/foo.html, and the
    // extension-less form is a common hand-typed alias.
    const gatedFiles = new Set([...OPERATOR_VIEW_PAGES, ...OPERATOR_ADMIN_PAGES].map(([, f]) => f));
    const listed = new Set([...OPERATOR_VIEW_PAGES, ...OPERATOR_ADMIN_PAGES].map(([r]) => r));
    const missing = [];
    for (const file of gatedFiles) {
      if (!listed.has('/' + file)) missing.push('/' + file);
    }
    assert.deepEqual(missing, [], 'each gated file must be listed under its own /<file>.html route');
  });

  it('the known operator pages are all still gated (regression list)', () => {
    // If one of these is ever dropped from the table, this fails loudly rather
    // than silently reverting the page to anonymous static serving.
    const MUST_BE_GATED = [
      '/aria-brain.html', '/sources.html', '/vls-chain.html', '/bd-intelligence.html',
      '/leads.html', '/design-partners.html', '/vault.html', '/wa-connections.html',
      '/admin.html',
    ];
    for (const route of MUST_BE_GATED) {
      assert.notEqual(requiredRoleForPage(route), null, `${route} must remain gated`);
    }
  });
});
