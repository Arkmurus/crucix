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
 * admin is a superset of EVERY lower role. support and poweruser satisfy ONLY
 * routes that explicitly allow them — they are not supersets of each other.
 *
 * R-F3619 — the admin superset used to enumerate `support` and `poweruser` only, so
 * `roleSatisfies('admin', ['viewer'])` was FALSE: a route gated on the customer role
 * would have 403'd the operator. LATENT, not live — nothing gates on 'viewer' today
 * (customer surfaces use requireAuth, which any signed-in user satisfies), so no
 * behaviour changes here. It is fixed because the guarantee has to be structural: the
 * operator's requirement is that assigning the admin role grants full access, and
 * under the old form that held only for as long as nobody wrote requireRole('viewer').
 * A property that depends on a route never being written is not a property.
 *
 * Derived from ROLES rather than re-enumerated, so a role added to the canonical list
 * cannot silently fall outside the admin superset the way 'viewer' did.
 *
 * This does NOT touch tenant isolation, which is a separate mechanism: proxyPin still
 * pins user_id for everyone, admin included, on the R-F3167 paths.
 */
export function roleSatisfies(userRole, allowed) {
  if (!Array.isArray(allowed) || allowed.length === 0) return false;
  if (allowed.includes(userRole)) return true;
  if (userRole === 'admin' && allowed.some(r => ROLES.includes(r))) return true;
  return false;
}

/** True iff `role` is a recognised, assignable role. */
export function isValidRole(role) {
  return ROLES.includes(role);
}
