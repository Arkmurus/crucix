// lib/vetting/portalPath.mjs
//
// R-F3682 — the boundary check for the UNAUTHENTICATED vetting-portal proxy.
//
// This lives in its own module, rather than inline in server.mjs, for the same
// reason lib/auth/infraRoutes.mjs does: the capability test must exercise the
// SHIPPED validator over a real socket, not a copy of it pasted into the test.
// A duplicated regex is a test that passes while production is open — which is
// precisely how the defect below survived.
//
// ── THE DEFECT (proven live 2026-08-04, unauthenticated, public internet) ────
//   GET /api/aria/cost/monthly/status                                      -> 401
//   GET /api/vetting-portal/..%2f..%2fapi%2faria%2fcost%2fmonthly%2fstatus  -> 200
//   {"month":"2026-08","spent_usd":21.48,"cap_usd":300,...}
//
// server.mjs proxies this route with `_ariaHeaders()`, which attaches the brain
// service token, and mounts it BEFORE the authenticated /api/aria catch-all —
// both correct in isolation, because an applicant or referee reaching a portal
// link has no account and requireAuth would make the feature impossible.
//
// The hole is that Express percent-DECODES a regex route's capture group, so
// `%2f` arrives as `/`, the `..` segments survive into the upstream URL
// template, and WHATWG URL parsing collapses them. One request promotes an
// anonymous caller to an authenticated brain caller.
//
// ── WHY AN ALLOWLIST, NOT A SANITISER ───────────────────────────────────────
// Rejecting `%` and `..` is a guess about which encodings Express and WHATWG
// will collapse; the next encoding that normalises to `/` re-opens it. The
// brain exposes exactly TWO portal routes (aria_service/routes/vetting_portal.py):
//     GET  /api/vetting-portal/{token}            (:81)
//     POST /api/vetting-portal/{token}/documents  (:160)
// so the set of legitimate suffixes is enumerable, and enumerating it refuses
// every traversal shape at once — including ones nobody has thought of.
//
// `token` is secrets.token_urlsafe(32) (aria_service/vetting/invites.py:133),
// i.e. 43 characters of [A-Za-z0-9_-]. The LENGTH bounds are deliberately loose
// so a future token-size change cannot silently 404 every applicant; the
// CHARSET is exact, and it contains no `%`, no `.`, and no `/` other than the
// single literal `/documents` segment.
//
// Adding a portal route to the brain now requires adding it here too. That cost
// is intended: this list failing closed is the entire point.

/** The only suffix shapes the unauthenticated portal proxy will forward. */
export const VETTING_PORTAL_SUFFIX_RE =
  /^[A-Za-z0-9_-]{16,128}(?:\/documents)?$/;

/**
 * True iff `suffix` is a legitimate vetting-portal path suffix.
 *
 * Takes the value Express has ALREADY percent-decoded (`req.params[0]`), so it
 * sees the same bytes the upstream fetch would, which is the only place the
 * check is meaningful.
 *
 * @param {unknown} suffix
 * @returns {boolean}
 */
export function isValidVettingPortalSuffix(suffix) {
  if (typeof suffix !== 'string' || suffix.length === 0) return false;
  // Defence in depth: a NUL or newline can truncate or split a downstream
  // request even when the visible prefix looks well-formed. The regex below
  // already excludes them, but an explicit refusal keeps that intent readable
  // if the charset is ever widened.
  if (/[\0-\x1f\x7f]/.test(suffix)) return false;
  return VETTING_PORTAL_SUFFIX_RE.test(suffix);
}
