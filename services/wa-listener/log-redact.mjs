// R-F2705 — recursive secret redaction for the aria-wa structured logger.
// ═══════════════════════════════════════════════════════════════════════════
// WHY THIS EXISTS
// The listener routes every console.* call through pino via `_waLogFields`
// (aria_wa_listener.mjs). That shim had TWO unredacted paths:
//   1. a lone object arg was handed to pino VERBATIM, and
//   2. any object among multiple args was `JSON.stringify`d in full.
// pino was configured with no `redact`, so a Baileys signal-session /
// libsignal session object — `privKey`, `rootKey`, `chainKey`, ratchet
// state, identity/noise keys, all raw Buffers — could reach the log stream
// intact (observed on aria-wa at 07:27:53 UTC 2026-07-18, most plausibly via
// the process-level `unhandledRejection`/`uncaughtException` handlers, which
// pass a raw reason/error object straight to console.error).
//
// This module is the ROOT-CAUSE fix (§1): a single recursive scrubber applied
// on BOTH branches before anything reaches pino, so EVERY current and future
// caller is covered — not just the one that leaked. Two independent nets:
//   • key-name net  — values under credential-shaped keys are replaced wholesale.
//   • byte net      — ANY Buffer / TypedArray value is replaced, regardless of
//                     its key name, since binary blobs in logs are almost always
//                     key material and never useful debug context.
//
// CONTRACT (kept bulletproof on purpose):
//   • NON-MUTATING — returns a fresh structure; never touches the live creds
//     object Baileys is actively using.
//   • CIRCULAR-SAFE — a WeakSet breaks cycles (Baileys objects can self-ref).
//   • DEPTH-CAPPED  — bounded recursion so a pathological graph can't hang boot.
//   • DEPENDENCY-FREE — Node built-ins only, so it is importable at the repo
//     root for the capability test AND cherry-pick-copyable into the aria-wa
//     image (see the COPY line in Dockerfile.wa — a MISSING copy crash-loops
//     the app on boot, exactly like send-retry.mjs / doc-resubmit.mjs).

const REDACTED = '[REDACTED]';
const REDACTED_BYTES = '[REDACTED:bytes]';
const MAX_DEPTH = 8;

// Credential-shaped key names. Case-insensitive substring match so both
// libsignal session shapes (privKey, pubKey, rootKey, chainKey, ephemeralKeyPair)
// and Baileys creds shapes (noiseKey, signedIdentityKey, signedPreKey,
// advSecretKey, pairing*) are caught, plus the usual token/secret/password/cookie
// family and the Baileys `keys` keystore itself.
const SECRET_KEY_RE = /(?:priv|secret|password|passwd|\bpwd\b|token|credential|creds?|api[_-]?key|apikey|bearer|cookie|mnemonic|seed|salt|signature|nonce|\biv\b|\bmac\b|cipher|rootkey|chainkey|ratchet|identitykey|signedidentity|prekey|signedkey|noisekey|ephemeral|advsecret|pairing|sessionkey|\bkeys?\b|signalident|\bauth\b)/i;

function isBufferLike(v) {
  if (v == null || typeof v !== 'object') return false;
  if (typeof Buffer !== 'undefined' && typeof Buffer.isBuffer === 'function' && Buffer.isBuffer(v)) return true;
  if (v instanceof Uint8Array || ArrayBuffer.isView(v)) return true;
  // JSON-serialized Buffer: { type: 'Buffer', data: [ ...bytes ] }
  if (v.type === 'Buffer' && Array.isArray(v.data)) return true;
  return false;
}

/**
 * Return a redacted deep copy of `value` safe to serialize into a log line.
 * Scalars pass through unchanged; secret-named keys and binary values are
 * replaced. Never mutates the input.
 */
export function redactSecrets(value, _depth = 0, _seen = new WeakSet()) {
  // BigInt is not JSON-serializable and would throw downstream — stringify it.
  if (typeof value === 'bigint') return `${value}`;
  if (value === null || typeof value !== 'object') return value;
  if (isBufferLike(value)) return REDACTED_BYTES;
  if (_seen.has(value)) return '[Circular]';
  if (_depth >= MAX_DEPTH) return '[Truncated]';
  _seen.add(value);

  // This helper runs inside every log call, so it must NEVER throw — a hostile
  // getter or an exotic Proxy trap fails CLOSED to a redacted marker, never
  // propagates out of a console.* call.
  try {
    if (Array.isArray(value)) {
      return value.map((v) => redactSecrets(v, _depth + 1, _seen));
    }
    const out = {};
    for (const k of Object.keys(value)) {
      if (SECRET_KEY_RE.test(k)) { out[k] = REDACTED; continue; }
      let v;
      try { v = value[k]; } catch { out[k] = '[unreadable]'; continue; }
      if (isBufferLike(v)) { out[k] = REDACTED_BYTES; continue; }
      out[k] = redactSecrets(v, _depth + 1, _seen);
    }
    return out;
  } catch {
    return '[REDACTED:unserializable]';
  }
}

export { SECRET_KEY_RE, REDACTED, REDACTED_BYTES };
