// lib/auth/roles.mjs
// R-F2170: canonical role model, shared by server.mjs (requireRole gate) and tests.
// Backend roles:
//   - 'viewer' : regular customer-tier user (default at registration). The aria-app
//                frontend maps `viewer` -> the "customer" panel.
//   - 'support': customer-support staff (scoped console).
//   - 'admin'  : power user (coders + designers) — full admin panel.
// Billing TIER (free/pro/proIntel) is orthogonal and gates customer features only.

export const ROLES = Object.freeze(['viewer', 'support', 'admin']);

/**
 * Does `userRole` satisfy a route that allows one of `allowed`?
 * admin is a superset of support (admin can use support consoles); support is NOT admin.
 */
export function roleSatisfies(userRole, allowed) {
  if (!Array.isArray(allowed) || allowed.length === 0) return false;
  if (allowed.includes(userRole)) return true;
  if (userRole === 'admin' && allowed.includes('support')) return true;
  return false;
}

/** True iff `role` is a recognised, assignable role. */
export function isValidRole(role) {
  return ROLES.includes(role);
}
