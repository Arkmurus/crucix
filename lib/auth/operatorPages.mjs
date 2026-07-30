// lib/auth/operatorPages.mjs
// R-F2785: the operator/infra PAGE table behind the R-F2774 navigation gate.
//
// Extracted from server.mjs for the same reason as lib/auth/roles.mjs (R-F2170)
// and lib/auth/infraRoutes.mjs (R-F2775): server.mjs boots on import, so anything
// left inline there can only be tested by grepping its source text — and a
// source-spelling assertion is not a contract test. R-F2774 replaced the old
// literal `app.get('/vault.htm', ...)` registrations with a table-driven loop,
// which silently broke test/web-full-ui-smoke-rf2389 even though the route still
// existed AND had gained an admin gate: production got safer while the test went
// red on a spelling change. With the table here, tests assert what actually
// matters — which pages exist, which file each serves, and what role each demands.
//
// ROLE SPLIT (R-F2773):
//   VIEW  pages → poweruser or admin. Read-only operator surface.
//   ADMIN pages → admin only. These expose destructive controls (vault writes and
//                 clear-all, WhatsApp device pairing, user management).
//
// Every URL form of each page must be listed — a page reachable by an ungated
// alias is an ungated page. `/vault.htm` (no trailing 'l') is a real alias that
// express.static would otherwise never serve but the gate must still cover.

import { normalisePath } from './infraRoutes.mjs';  // R-F2818: ONE normaliser, shared
import { roleSatisfies } from './roles.mjs';        // R-F2822: nav entitlement

/** Operator pages viewable by poweruser or admin: [route, file]. */
export const OPERATOR_VIEW_PAGES = Object.freeze([
  ['/vls-chain.html', 'vls-chain.html'],
  ['/bd-intelligence.html', 'bd-intelligence.html'],
  ['/design-partners.html', 'design-partners.html'],
].map(Object.freeze));

/** Operator pages restricted to admin (destructive controls): [route, file]. */
export const OPERATOR_ADMIN_PAGES = Object.freeze([
  // R-F2874 — operator direction 2026-07-22: brain + source health are
  // ADMIN-ONLY. They expose the platform's own internals (knowledge/brain
  // state, per-source reliability), so they move out of the poweruser-visible
  // table. No user currently holds `poweruser`, so this closes the path before
  // one exists rather than after. Both the page GATE and the nav entitlement
  // read this same table, so they cannot drift (the R-F2822 property).
  ['/aria-brain', 'aria-brain.html'],
  ['/aria-brain.html', 'aria-brain.html'],
  ['/sources.html', 'sources.html'],
  // Access requests contain contact details and the API is requireAdmin.
  // Keep page visibility and data authorization on the same contract.
  ['/leads.html', 'leads.html'],
  ['/vault.html', 'vault.html'],
  ['/vault.htm', 'vault.html'],
  ['/wa-connections.html', 'wa-connections.html'],
  ['/admin.html', 'admin.html'],
].map(Object.freeze));

/**
 * R-F2818 — PATH NORMALISATION IS A SECURITY CONTROL HERE TOO.
 *
 * This gate previously matched on `String(route).split('?')[0]` and nothing else,
 * while the sibling /api/aria gate had been hardened by R-F2802 with decode +
 * slash-collapse + case-fold. That asymmetry was the whole bug: Express's
 * `req.path` is NOT percent-decoded, so `app.get('/admin.html')` never matched
 * `/%61dmin.html` and the gate never fired — but express.static hands off to
 * `send`, which DOES decode, and served the file.
 *
 * REPRODUCED live on imaria.io before this fix:
 *   GET /admin.html            → 302 /signin.html   (gated)
 *   GET /%61dmin.html          → 200 <title>Admin | ARIA</title>
 *   GET /%76ault.html          → 200
 *   GET /%77a-connections.html → 200   ← the R-F2705 WhatsApp pairing surface
 *   GET //admin.html           → 200   (duplicate slash, same class)
 *
 * The fix is to share ONE normaliser with infraRoutes.mjs rather than reimplement
 * it, so a future hardening of either gate cannot leave the other behind again —
 * which is exactly how this defect was born.
 */
function pageKey(route) {
  return normalisePath(route);
}

/**
 * The operator page `route` resolves to, or null if it is not an operator page
 * (i.e. express.static may serve it to anyone).
 * Returns `{ file, roles }` so the caller has one lookup, not two.
 */
export function operatorPageFor(route) {
  const clean = pageKey(route);
  const admin = OPERATOR_ADMIN_PAGES.find(([r]) => pageKey(r) === clean);
  if (admin) return { file: admin[1], roles: ['admin'] };
  const view = OPERATOR_VIEW_PAGES.find(([r]) => pageKey(r) === clean);
  if (view) return { file: view[1], roles: ['poweruser', 'admin'] };
  return null;
}

/**
 * R-F2822 — which gated routes may a caller with `role` navigate to?
 *
 * Pure, and derived from the SAME tables the gate enforces, so the sidebar can
 * ask the server instead of hand-maintaining its own copy (which had drifted in
 * both directions — see server.mjs /api/auth/nav-pages). Uses roleSatisfies()
 * rather than an equality check, which is precisely what the sidebar got wrong:
 * it revealed on `role === 'admin'`, hiding poweruser-entitled pages.
 *
 * Not a security boundary — requirePageRole() is. This decides what the nav SHOWS.
 */
export function navPagesForRole(role) {
  const allowed = [];
  if (roleSatisfies(role, ['poweruser', 'admin'])) {
    for (const [route] of OPERATOR_VIEW_PAGES) allowed.push(route);
  }
  if (roleSatisfies(role, ['admin'])) {
    for (const [route] of OPERATOR_ADMIN_PAGES) allowed.push(route);
  }
  return allowed;
}

/**
 * Role required to NAVIGATE to `route`, or null if it is not an operator page.
 */
export function requiredRoleForPage(route) {
  const page = operatorPageFor(route);
  return page ? page.roles[0] : null;
}
