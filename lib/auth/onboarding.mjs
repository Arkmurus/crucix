// lib/auth/onboarding.mjs
//
// R-F2034/R-F2035 — automated self-serve onboarding policy.
//
// The platform's approval is now self-serve (operator decision 2026-06-27):
// signup → email-verify → INSTANT active (free tier). This module is the single
// seam that decides auto-approval + screens signups, so the policy lives in one
// testable place instead of being scattered through server.mjs. If the policy
// ever changes (e.g. risk-tiered approval, trusted-domain allow-lists, a Stripe-
// card gate), it changes HERE — the route handlers just call evaluateAutoApproval.

// ── Disposable / throwaway email domains (R-F2035) ──────────────────────────
// A self-serve signup path is an open door; throwaway addresses are the #1
// abuse vector (one human, unlimited accounts). This is a high-signal core
// list, not exhaustive — extend as abuse is observed. Matching is exact on the
// lowercased domain.
export const DISPOSABLE_DOMAINS = new Set([
  'mailinator.com', 'guerrillamail.com', 'guerrillamail.info', 'sharklasers.com',
  '10minutemail.com', '10minutemail.net', 'tempmail.com', 'temp-mail.org',
  'throwawaymail.com', 'yopmail.com', 'getnada.com', 'trashmail.com',
  'maildrop.cc', 'dispostable.com', 'fakeinbox.com', 'mintemail.com',
  'mohmal.com', 'mytemp.email', 'tempmailo.com', 'emailondeck.com',
  'spamgourmet.com', 'mailcatch.com', 'inboxbear.com', 'tmpmail.org',
  'tempinbox.com', 'discard.email', 'mailnesia.com', 'spam4.me',
]);

/**
 * R-F2035 — is this a disposable / throwaway email domain?
 * @param {string} email
 * @returns {boolean}
 */
export function isDisposableEmail(email) {
  const at = String(email || '').lastIndexOf('@');
  if (at < 0) return false;
  const domain = String(email).slice(at + 1).trim().toLowerCase();
  return DISPOSABLE_DOMAINS.has(domain);
}

// ── Verify-code brute-force guard (R-F2035) ─────────────────────────────────
// A 6-digit code is 1e6 space; without a cap an attacker can grind it. After
// MAX_VERIFY_ATTEMPTS wrong codes the code is burned (caller clears it) and the
// user must request a fresh one — turning brute force into a resend-rate problem.
export const MAX_VERIFY_ATTEMPTS = 5;

/**
 * R-F2034 — decide whether a user who just passed email verification should be
 * auto-approved to `active`. Self-serve-instant policy: a verified email IS the
 * approval. Returns a structured decision so the caller can audit WHY (and so a
 * future risk-tiered policy can return approve:false with a reason without any
 * route changes).
 *
 * @param {object} user  raw user record (post email-verify)
 * @returns {{approve: boolean, reason: string, signals: object}}
 */
export function evaluateAutoApproval(user) {
  const signals = {
    email_verified: true,           // caller only invokes this after a correct code
    disposable_email: isDisposableEmail(user?.email),
    account_type: user?.accountType || 'unknown',
  };
  // Disposable addresses are blocked at registration; if one somehow reaches
  // here, do not auto-approve — leave it for admin review.
  if (signals.disposable_email) {
    return { approve: false, reason: 'disposable_email_needs_review', signals };
  }
  return { approve: true, reason: 'self_serve_email_verified', signals };
}
