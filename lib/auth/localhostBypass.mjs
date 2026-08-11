// lib/auth/localhostBypass.mjs
//
// R-F3833 — ONE implementation of the "is this caller the same process?" test.
//
// ── THE DEFECT ───────────────────────────────────────────────────────────────
// Five gates decided the localhost bypass from `req.ip`. server.mjs sets
// `trust proxy: 1`, so req.ip is DERIVED FROM `X-Forwarded-For` — a caller-
// supplied header. aria-web listens on 0.0.0.0 and is reachable as
// aria-web.internal:3117 over Fly's 6PN, so any peer on the private network
// connecting DIRECTLY (no proxy hop) can send `X-Forwarded-For: 127.0.0.1` and
// make req.ip read as loopback:
//
//     requireAuth      -> next() with req.user never set
//     requirePageRole  -> operator/infra PAGES render for an unauthenticated peer
//     /events          -> the full sweep-payload SSE stream
//     _waRequireAuth   -> WhatsApp message injection / ARIA queries
//     _waQrAuthOK      -> the WhatsApp LINKING QR
//
// Not reachable from the public internet: fly-proxy appends the real client IP,
// so req.ip resolves to the client. Reachable from a compromised aria-wa or
// aria-intel, or any machine in the org.
//
// requireInfraRole (R-F2775) already got this right and documented the vector in
// a comment 100 lines below requireAuth — the sibling gate was simply never
// updated. The whole point of this module is that there is now nowhere for a
// sixth copy to drift to.
//
// ── WHY THE SOCKET PEER IS THE HONEST SOURCE ─────────────────────────────────
// `req.socket.remoteAddress` is the REAL TCP peer. It is set by the kernel from
// the accepted connection and no header can move it. Genuine same-process
// callers — the embedded Telegram bot hitting /api/data, the WA listener's
// cross-calls — are unaffected, because their real peer address IS 127.0.0.1.
// That is asserted over a real socket by the capability test, not assumed.

/** Loopback peer addresses, including the IPv4-mapped IPv6 form Node reports. */
const LOOPBACK = new Set(['127.0.0.1', '::1', '::ffff:127.0.0.1']);

/**
 * True iff the request's REAL TCP peer is loopback.
 *
 * Deliberately does NOT consult `req.ip`, `X-Forwarded-For`, `X-Real-IP` or any
 * other header. `req.connection` is the deprecated alias of `req.socket` and is
 * read only as a fallback for the older call sites.
 *
 * @param {{socket?: {remoteAddress?: string}, connection?: {remoteAddress?: string}}} req
 * @returns {boolean}
 */
export function isSameProcessPeer(req) {
  const peer = req?.socket?.remoteAddress || req?.connection?.remoteAddress || '';
  return LOOPBACK.has(peer);
}

/**
 * True iff the localhost bypass should be granted for this request.
 *
 * Combines the peer test with the operator kill switch. The env var is read at
 * CALL time, not at module load, so flipping it takes effect without a restart
 * and a test can toggle it.
 *
 * @param {object} req
 * @returns {boolean}
 */
export function localhostBypassAllowed(req) {
  if ((process.env.ARIA_DISABLE_LOCALHOST_BYPASS || '').toLowerCase() === '1') return false;
  return isSameProcessPeer(req);
}
