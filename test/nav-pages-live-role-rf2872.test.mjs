// test/nav-pages-live-role-rf2872.test.mjs
//
// R-F2872 — the nav entitlement answered from a STALE role snapshot.
//
// R-F2822 introduced GET /api/auth/nav-pages so the sidebar stops hand-maintaining
// which links each role may see. Its own comment states the intent:
//
//     "It is deliberately fetched fresh rather than read from Auth.me(), which
//      returns a login-time localStorage snapshot ... and would go stale on a
//      role change."
//
// But the handler then resolved the role as:
//
//     const role = req.user?.role || 'analyst';
//
// `req.user` is the decoded JWT, and the JWT's `role` is baked in at LOGIN
// (users.mjs createToken(userId, role, ...)). So it goes stale on a role change
// in exactly the way the comment says it is avoiding — the fetch is fresh, the
// DATA inside it is not. Elevating a user to admin has no effect on their nav
// until they happen to log in again, and nothing tells them that.
//
// Live on 2026-07-22: acorrea@arkmurus.com is `role: admin, status: active` in
// /data/users.json, the deployed table returns all 11 routes for admin, and the
// deployed sidebar.js carries all 8 data-gated links with exactly matching
// strings — yet the operator could not see the brain / source-health / vault
// tabs. Every server-side component was correct; the role travelling in the
// token was not.
//
// FIX: resolve from the live user record, falling back to the token.
//
// This is a nav-decoration endpoint, NOT a security boundary — requirePageRole()
// is (server.mjs). Reading the live role here can only make the nav agree with
// the gate sooner; it cannot grant access the gate would refuse.
//
// Run: node --test test/nav-pages-live-role-rf2872.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SRC = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');

/** The nav-pages handler body only. */
const HANDLER = (() => {
  const start = SRC.indexOf("app.get('/api/auth/nav-pages'");
  assert.ok(start > 0, 'the nav-pages endpoint must exist');
  const end = SRC.indexOf('app.put(', start);
  assert.ok(end > start, 'handler must be bounded');
  return SRC.slice(start, end);
})();

test('R-F2872: the role is resolved from the LIVE user record', () => {
  assert.match(HANDLER, /findUserById\(/,
    'THE FIX: a role change must reach the nav without waiting for a re-login');
});

test('R-F2872: the stale token role is only a FALLBACK, never the primary', () => {
  const liveAt = HANDLER.indexOf('findUserById(');
  const tokenAt = HANDLER.indexOf('req.user?.role');
  assert.ok(liveAt > 0 && tokenAt > 0, 'both sources must be present');
  assert.ok(liveAt < tokenAt,
    'the live record must be consulted BEFORE falling back to the token snapshot');
});

test('R-F2872: NEGATIVE CONTROL — a missing user does not crash or escalate', () => {
  // findUserById returns undefined for the ARIA_INTERNAL_TOKEN pseudo-user
  // (id 'aria-internal'), which has no row. That must fall back cleanly, not throw.
  assert.match(HANDLER, /\?\.role|\|\|/,
    'the lookup must be optional-chained or defaulted');
  assert.match(HANDLER, /try\s*\{/, 'the handler must keep its try/catch');
});

test('R-F2872: still FAILS CLOSED on error', () => {
  // R-F2822 made an error return an empty allow-list so the nav hides links
  // rather than showing ones that would bounce. That must survive.
  assert.match(HANDLER, /allowed:\s*\[\]/,
    'an error must yield an empty allow-list, never a permissive default');
  assert.match(HANDLER, /nav_entitlement_unavailable/,
    'the failure must stay labelled for diagnosis');
});

test('R-F2872: the endpoint remains authenticated', () => {
  assert.match(HANDLER, /'\/api\/auth\/nav-pages',\s*requireAuth/,
    'nav-pages must stay behind requireAuth');
});

test('R-F2872: entitlement is still computed by the shared table, not inline', () => {
  // The whole point of R-F2822 is that the browser performs no authorization
  // reasoning and the server uses the SAME table as the gate.
  assert.match(HANDLER, /navPagesForRole\(/,
    'must delegate to navPagesForRole so nav and gate cannot drift');
});
