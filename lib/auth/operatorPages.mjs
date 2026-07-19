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

/** Operator pages viewable by poweruser or admin: [route, file]. */
export const OPERATOR_VIEW_PAGES = Object.freeze([
  ['/aria-brain', 'aria-brain.html'],
  ['/aria-brain.html', 'aria-brain.html'],
  ['/sources.html', 'sources.html'],
  ['/vls-chain.html', 'vls-chain.html'],
  ['/bd-intelligence.html', 'bd-intelligence.html'],
  ['/leads.html', 'leads.html'],
  ['/design-partners.html', 'design-partners.html'],
].map(Object.freeze));

/** Operator pages restricted to admin (destructive controls): [route, file]. */
export const OPERATOR_ADMIN_PAGES = Object.freeze([
  ['/vault.html', 'vault.html'],
  ['/vault.htm', 'vault.html'],
  ['/wa-connections.html', 'wa-connections.html'],
  ['/admin.html', 'admin.html'],
].map(Object.freeze));

/**
 * Role required to NAVIGATE to `route`, or null if it is not an operator page
 * (i.e. express.static may serve it to anyone).
 */
export function requiredRoleForPage(route) {
  const clean = String(route || '').split('?')[0];
  if (OPERATOR_ADMIN_PAGES.some(([r]) => r === clean)) return 'admin';
  if (OPERATOR_VIEW_PAGES.some(([r]) => r === clean)) return 'poweruser';
  return null;
}
