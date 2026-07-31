// lib/auth/roles.mjs
// R-F2170: canonical role model, shared by server.mjs (requireRole gate) and tests.
// Backend roles:
//   - 'viewer'   : regular customer-tier user (default at registration). aria-app
//                  maps `viewer` -> the "customer" panel.
//   - 'support'  : customer-support staff (scoped console).
//   - 'poweruser': R-F2773 — trusted operator/infra team member. VIEW-only access to
//                  the operator/infra pages (aria-brain, sources, vls-chain, etc.) and
//                  their READ APIs. NO destructive powers (no vault writes, DD reset,
//                  self-code and user management — those stay 'admin'. WhatsApp
//                  pairing is owner-scoped and available to every signed-in user.
//   - 'admin'    : full power user (operator) — full admin panel + every gate.
// Billing TIER (free/pro/proIntel) is orthogonal and gates customer features only.

export const ROLES = Object.freeze(['viewer', 'support', 'poweruser', 'admin']);

/**
 * Does `userRole` satisfy a route that allows one of `allowed`?
 * admin is a superset of every lower role (it satisfies support- and poweruser-
 * gated routes). support and poweruser satisfy ONLY routes that explicitly allow
 * them — they are not supersets of each other.
 */
export function roleSatisfies(userRole, allowed) {
  if (!Array.isArray(allowed) || allowed.length === 0) return false;
  if (allowed.includes(userRole)) return true;
  if (userRole === 'admin' && (allowed.includes('support') || allowed.includes('poweruser'))) return true;
  return false;
}

/** True iff `role` is a recognised, assignable role. */
export function isValidRole(role) {
  return ROLES.includes(role);
}
