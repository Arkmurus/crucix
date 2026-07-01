// lib/auth/proxyPin.mjs
// R-F2211 (2026-07-01) — central IDOR guard for the aria-web → aria-intel
// catch-all proxy (server.mjs `app.use('/api/aria', …)`).
//
// The catch-all forwarded the client's query string VERBATIM. The brain
// owner-scopes results on `user_id` (and treats an EMPTY user_id as
// admin/see-all), so a normal authenticated user could read another tenant's
// data simply by appending `?user_id=<victim>` (or an empty one). This was
// plugged endpoint-by-endpoint (dd/reports R-F2075, vault/case R-F2097,
// document/extraction R-F1826, watchlist alerts, …) — whack-a-mole: any NEW
// brain endpoint that scopes on user_id and lacks its own pin route was a fresh
// latent leak. This module pins user_id centrally, once, for every non-admin
// request that reaches the catch-all.
//
// Kept as a standalone, side-effect-free module so it is unit/capability
// testable without booting server.mjs (which starts the whole platform on
// import).

// A caller is privileged (keeps its query verbatim, incl. empty/other user_id
// for legitimate see-all) when it is an admin JWT or the internal service token
// (requireAuth sets req.user = { id: 'aria-internal', role: 'admin' } for that).
export function isPrivileged(user) {
  const u = user || {};
  return u.role === 'admin' || u.id === 'aria-internal';
}

// Rewrite `originalUrl` so a non-admin caller can never forge `?user_id`.
// - Non-admin: force user_id to the caller's own JWT userId, discarding any
//   client-supplied value (present, empty, or victim's).
// - Admin/internal: returned unchanged (see-all preserved — matches the
//   per-route pins, which never gated admins).
// Never throws: on any parse failure the original path is returned unchanged and
// the brain-side auth remains the backstop.
export function pinNonAdminUserId(originalUrl, user) {
  if (isPrivileged(user)) return originalUrl;
  const uid = (user && user.userId) || '';
  try {
    const qi = originalUrl.indexOf('?');
    const base = qi === -1 ? originalUrl : originalUrl.slice(0, qi);
    const qs = new URLSearchParams(qi === -1 ? '' : originalUrl.slice(qi + 1));
    qs.set('user_id', uid); // override/insert — client value discarded
    const s = qs.toString();
    return s ? `${base}?${s}` : base;
  } catch {
    return originalUrl;
  }
}
