// lib/billing/uploadLimit.mjs
// R-F3988 (C-73) — the upload size a caller is ALLOWED is the one their tier sells.
//
// THE DEFECT, AS IT WAS IN AUGUST 2026. `tiers.mjs` defined `uploadBytesMax` per
// tier — free/pro 5 MB, proIntel 50 MB AT THAT TIME — `/api/billing/me` reported
// it, and the public pricing page sold it. The route enforcing it compared
// Content-Length against a single hardcoded literal:
// hardcoded literal:
//
//     server.mjs:1636   if (Number(req.headers['content-length']) > 25 * 1024 * 1024)
//
// Measured against the tier table AS IT THEN STOOD, that number was wrong for
// every tier:
//
//     free      sold  5 MB   enforced 25 MB   OVER-DELIVERS 20 MB
//     pro       sold  5 MB   enforced 25 MB   OVER-DELIVERS 20 MB
//     proIntel  sold 50 MB   enforced 25 MB   UNDER-DELIVERS 25 MB
//
// R-F4020 (C-94) UPDATE: free and pro are now sold 25 MB, which is what the flat
// literal had been serving them all along. The table ABOVE is the state that
// motivated this module, kept because it is the evidence for why the cap is
// tier-resolved; it is NOT a description of the current tiers. Read tiers.mjs for
// those — this comment must never become the place someone looks up a live value.
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
import { Transform } from 'node:stream';
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
 * That bypass is a separate defect (C-78) and is deliberately NOT fixed here;
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

/**
 * R-F3997 (C-78) — count the bytes that actually arrive.
 *
 * `uploadTooLarge` above can only read the Content-Length HEADER, and a chunked
 * request has none: the guard it replaced did `Number(undefined) > limit`, which
 * is false, so an unmeasured body streamed straight through. Refusing every
 * request without a Content-Length would bound the body and break legitimate
 * streaming clients — and it would still be measuring a CLAIM. The header is what
 * the client says; these bytes are what it sent. Counting catches both the absent
 * header and the lying one, which the header check never could.
 *
 * Returns `{ stream, exceeded, bytes }`:
 *   stream    — a Transform to pipe the request through before it is forwarded.
 *               It passes chunks along untouched until the running total exceeds
 *               `maxRequestBytes`, then errors, which aborts the upstream request
 *               mid-flight rather than after the whole payload has been paid for.
 *   exceeded() — true when it aborted for size. The caller needs this to answer
 *               413 rather than a generic proxy error: an oversized upload and a
 *               dead upstream must not look the same to the user.
 *   bytes()   — the running total, for logging.
 *
 * Deliberately THROWS on a falsy or non-positive budget rather than defaulting to
 * unlimited. A meter that silently allows everything is the defect being fixed,
 * wearing the name of the fix.
 */
export function createUploadMeter(maxRequestBytes) {
  if (!Number.isFinite(maxRequestBytes) || maxRequestBytes <= 0) {
    throw new TypeError(
      `createUploadMeter: a positive byte limit is required, got ${String(maxRequestBytes)}`,
    );
  }
  let seen = 0;
  let over = false;
  const stream = new Transform({
    transform(chunk, _enc, cb) {
      seen += chunk.length;
      if (seen > maxRequestBytes) {
        over = true;
        // An Error (not a silent end) so the pipeline rejects and the caller
        // cannot mistake a truncated upload for a complete one.
        cb(new UploadTooLargeError(maxRequestBytes, seen));
        return;
      }
      cb(null, chunk);
    },
  });
  return { stream, exceeded: () => over, bytes: () => seen };
}

/** Distinct type so the route can tell "too big" from "upstream broke". */
export class UploadTooLargeError extends Error {
  constructor(limitBytes, seenBytes) {
    super(`upload exceeded ${limitBytes} bytes`);
    this.name = 'UploadTooLargeError';
    this.limitBytes = limitBytes;
    this.seenBytes = seenBytes;
  }
}

/** Human-facing refusal text. Quotes the tier's own limit, never a global constant. */
export function uploadTooLargeMessage(verdict) {
  if (!verdict) return '';
  return `Document too large (max ${verdict.limitMb}MB on your plan)`;
}
