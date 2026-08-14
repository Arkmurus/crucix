// lib/billing/uploadLimit.mjs
// R-F3988 (C-73) — the upload size a caller is ALLOWED is the one their tier sells.
//
// THE DEFECT. `tiers.mjs` defines `uploadBytesMax` per tier (free/pro 5 MB,
// proIntel 50 MB), `/api/billing/me` reports it, and the public pricing page
// sells it. The route enforcing it compared Content-Length against a single
// hardcoded literal:
//
//     server.mjs:1636   if (Number(req.headers['content-length']) > 25 * 1024 * 1024)
//
// Measured against the live tier table, that number was wrong for every tier:
//
//     free      sold  5 MB   enforced 25 MB   OVER-DELIVERS 20 MB
//     pro       sold  5 MB   enforced 25 MB   OVER-DELIVERS 20 MB
//     proIntel  sold 50 MB   enforced 25 MB   UNDER-DELIVERS 25 MB
//
// The under-delivery is the one that costs money: a £199/mo customer was refused
// at half the limit they were sold. Neither direction surfaces as a complaint —
// nobody reports a limit that is too generous, and the customer who hits the low
// ceiling assumes it is theirs.
//
// Factored out of server.mjs for the reason R-F2170/R-F2775/R-F2785 give: that
// file boots a live app on import, so anything left inline there can only be
// tested by grepping its source text, and a source-spelling assertion is not a
// contract test.
//
// ── THE CEILING ABOVE US IS REAL, AND IT IS NOT OURS ────────────────────────
// Raising this route's number alone would have moved the failure downstream, not
// fixed it. The brain caps every request body in `main.py::_limit_body_size` at
// ARIA_MAX_BODY_BYTES (default 50 MB; verified UNSET on aria-intel 2026-08-14, so
// the default governs). Content-Length measures the whole multipart REQUEST —
// boundary, headers, filename — which is strictly larger than the file, so a
// 50 MB FILE is a >50 MB REQUEST and the brain refuses it with a bare
// "request body too large".
//
// So proIntel's advertised 50 MB is NOT deliverable today. This module does not
// paper over that: it clamps to what the chain can actually carry and reports
// `constrainedByDownstream`, so the shortfall is a value someone can read rather
// than a silent under-delivery of exactly the kind being fixed here. Closing the
// remaining gap is an operator action — raise ARIA_MAX_BODY_BYTES on aria-intel
// above the tier limit plus envelope, or reduce the advertised figure.
import { getTier } from './tiers.mjs';

/**
 * What the brain will accept as a total request body.
 *
 * MUST mirror ARIA_MAX_BODY_BYTES on aria-intel. Read from the environment with
 * the same name and the same default so the two can be kept in step by setting
 * one variable on both apps; a divergence here only ever makes THIS side
 * stricter, which fails closed (a refusal we can explain) rather than open (an
 * opaque 413 from a service the user has never heard of).
 */
export const DOWNSTREAM_MAX_BODY_BYTES = (() => {
  const raw = parseInt(process.env.ARIA_MAX_BODY_BYTES || '', 10);
  return Number.isFinite(raw) && raw > 0 ? raw : 50 * 1024 * 1024;
})();

/**
 * Head-room reserved for the multipart envelope (boundary, part headers,
 * filename). Generous on purpose: this is subtracted from the FILE limit, so
 * over-reserving costs a customer a few KB of a multi-MB allowance, while
 * under-reserving refuses a document of exactly the advertised size — which is
 * the "sold N, delivers less than N" failure this change exists to end.
 */
export const MULTIPART_ENVELOPE_ALLOWANCE_BYTES = 64 * 1024;

/**
 * The FILE size `tierId` is sold. Unknown/missing/malformed tiers resolve to the
 * default (free) via getTier — never to the largest allowance.
 */
export function advertisedUploadBytesFor(tierId) {
  return getTier(tierId).uploadBytesMax;
}

/**
 * The FILE size we can actually carry for `tierId`, and whether that is less
 * than what the tier is sold.
 *
 * Returns { advertisedBytes, effectiveBytes, constrainedByDownstream }.
 */
export function effectiveUploadLimit(tierId) {
  const advertisedBytes = advertisedUploadBytesFor(tierId);
  const carryable = DOWNSTREAM_MAX_BODY_BYTES - MULTIPART_ENVELOPE_ALLOWANCE_BYTES;
  const effectiveBytes = Math.max(0, Math.min(advertisedBytes, carryable));
  return {
    advertisedBytes,
    effectiveBytes,
    constrainedByDownstream: effectiveBytes < advertisedBytes,
  };
}

/** The FILE limit enforced for `tierId` (clamped to what the chain can carry). */
export function uploadLimitBytesFor(tierId) {
  return effectiveUploadLimit(tierId).effectiveBytes;
}

/** The largest total REQUEST body accepted for `tierId`. Never exceeds the brain's cap. */
export function maxRequestBytesFor(tierId) {
  return Math.min(
    uploadLimitBytesFor(tierId) + MULTIPART_ENVELOPE_ALLOWANCE_BYTES,
    DOWNSTREAM_MAX_BODY_BYTES,
  );
}

/**
 * Verdict for one upload. Mirrors the enforceQuota convention in this directory:
 * null means ALLOWED, an object means refused and carries what to tell the user.
 *
 * `contentLength` is the raw header value (string | number | undefined).
 *
 * AN UNMEASURABLE BODY IS NOT AN EMPTY ONE. A chunked request carries no
 * Content-Length, and the guard this replaces did `Number(undefined) > limit`,
 * which is false — so an unmeasured body passed as though it had been checked.
 * That bypass is a separate defect (C-76) and is deliberately NOT fixed here;
 * what this function guarantees is narrower and load-bearing: it never CERTIFIES
 * an unmeasurable body as within the limit. It reports `length_unknown` and
 * leaves the decision to the caller, so the bypass cannot hide inside a helper
 * whose name says the size was checked.
 */
export function uploadTooLarge(contentLength, tierId) {
  const { advertisedBytes, effectiveBytes, constrainedByDownstream } = effectiveUploadLimit(tierId);
  const maxRequestBytes = maxRequestBytesFor(tierId);
  const base = {
    limitBytes: advertisedBytes,
    effectiveBytes,
    constrainedByDownstream,
    maxRequestBytes,
    limitMb: Math.floor(effectiveBytes / (1024 * 1024)),
  };

  const n = typeof contentLength === 'number' ? contentLength : parseInt(contentLength, 10);
  if (!Number.isFinite(n) || n < 0) {
    return { ...base, reason: 'length_unknown', contentLength: null };
  }
  if (n > maxRequestBytes) {
    return { ...base, reason: 'too_large', contentLength: n };
  }
  return null;
}

/** Human-facing refusal text. Quotes the tier's own limit, never a global constant. */
export function uploadTooLargeMessage(verdict) {
  if (!verdict) return '';
  return `Document too large (max ${verdict.limitMb}MB on your plan)`;
}
