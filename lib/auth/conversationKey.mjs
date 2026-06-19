// lib/auth/conversationKey.mjs
// R-F1687 (2026-06-19) — single source of truth for the conversation-history
// bucket key.
//
// The empty-sidebar bug was a WRITE/READ key mismatch: the chat write path sent
// `user_id: req.user?.id` (undefined → '' → per-session bucket) while the read
// path used `req.user?.userId` (the volatile hex id) — and the browser computed
// yet a third value (email-slug). Three different keys → conversations never
// listed under the key the sidebar queried.
//
// This module is the ONE place the bucket key is derived. server.mjs uses it for
// BOTH the chat write proxy and the conversation list/detail/delete/rename
// proxies, so they can never diverge again. The value MUST equal the browser's
//   USER_ID_SLUG = (user.email || user.username || user.id).replace(/[^A-Za-z0-9]/g,'')
// (aria.html) so a chat WRITE and a sidebar LIST address the same key.
//
// Email is the preferred component because it is STABLE across deploys and
// account-store rebuilds (the user logs in by it; a future phone-number login
// maps to the same account record). The volatile per-row user.id is the last
// resort only.

/** Strip everything that isn't [A-Za-z0-9] — identical transform to aria.html. */
export function slugifyIdentity(s) {
  return String(s == null ? '' : s).replace(/[^A-Za-z0-9]/g, '');
}

/**
 * Canonical conversation bucket key for an authenticated account record.
 * Prefers email (stable) → username → id, mirroring the browser slug.
 * Returns '' when no usable identity is present (caller treats as unauthenticated).
 */
export function conversationKeyForUser(user) {
  if (!user) return '';
  return slugifyIdentity(user.email || user.username || user.id || '');
}
