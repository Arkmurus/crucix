// lib/reports/sign.mjs
// HMAC signing for audit-grade PDF reports.
//
// Distinct from ARIA_AUDIT_SIGNING_KEY (the Python audit-log chain).
// REPORT_SIGNING_KEY is the seenode-side report-signing root. They COULD
// be the same key, but keeping them split means a key compromise on one
// surface doesn't invalidate the other.
//
// Soft-rollout: when REPORT_SIGNING_KEY is unset we still generate the
// report — but with an explicit "unsigned" footer warning the recipient
// it has no audit value. This keeps the feature usable in dev without
// silently producing reports the operator would mistake for signed.

import { createHash, createHmac, timingSafeEqual } from 'node:crypto';

export function signingConfigured() {
  return !!(process.env.REPORT_SIGNING_KEY || '').trim();
}

function _key() {
  const k = (process.env.REPORT_SIGNING_KEY || '').trim();
  if (!k) throw new Error('REPORT_SIGNING_KEY not set');
  return k;
}

// SHA-256 of the canonical content. Used both as a stable identifier of
// the report (so a verifier can confirm "this PDF embeds content X") and
// as an input to the HMAC.
export function contentHash(content) {
  return createHash('sha256').update(String(content || ''), 'utf8').digest('hex');
}

// Canonical-string we sign:
//   v1|<contentSha256>|<userId>|<sessionId>|<messageIndex>|<generatedAtIso>
// Versioned so we can rotate the format later without breaking verifies.
export function canonicalize({ contentSha256, userId, sessionId, messageIndex, generatedAt }) {
  return [
    'v1',
    contentSha256,
    String(userId || ''),
    String(sessionId || ''),
    String(messageIndex ?? ''),
    String(generatedAt || ''),
  ].join('|');
}

export function sign(parts) {
  const canonical = canonicalize(parts);
  const sig = createHmac('sha256', _key()).update(canonical, 'utf8').digest('hex');
  return sig;
}

export function verify(parts, presentedSig) {
  if (!signingConfigured()) return { valid: false, reason: 'signing not configured on this server' };
  if (!presentedSig || typeof presentedSig !== 'string') return { valid: false, reason: 'no signature presented' };
  let expected;
  try {
    expected = sign(parts);
  } catch (e) {
    return { valid: false, reason: e.message };
  }
  // Constant-time compare via Buffer + timingSafeEqual.
  const a = Buffer.from(expected, 'hex');
  const b = Buffer.from(presentedSig, 'hex');
  if (a.length !== b.length) return { valid: false, reason: 'signature length mismatch' };
  const ok = timingSafeEqual(a, b);
  return { valid: ok, reason: ok ? '' : 'signature mismatch' };
}
