// lib/http/upstreamSegment.mjs
//
// R-F3831 / R-F3832 — boundary checks for request-controlled path segments that
// aria-web interpolates into a TOKEN-BEARING upstream URL.
//
// This lives in its own module, rather than inline in server.mjs, for exactly the
// reason lib/vetting/portalPath.mjs states: the capability test must exercise the
// SHIPPED validator, not a copy pasted into the test. A duplicated regex is a test
// that passes while production is open — which is how R-F3682 survived, and how
// the two defects below survived R-F3682 itself.
//
// ── THE DEFECT CLASS ─────────────────────────────────────────────────────────
// Express percent-DECODES a route capture, so `%2f` arrives at the handler as a
// real `/`. If that value is then concatenated into an upstream URL template, the
// `..` segments survive and WHATWG URL parsing COLLAPSES them. Measured, not
// assumed (node 22, 2026-08-10):
//
//   sid = '..%2f..%2fdd%2freport%2fvictim'    // what the attacker sends
//   req.params.sessionId                       -> '../../dd/report/victim'
//   new URL(BASE + '/api/aria/conversations/' + sid + '/detail').href
//        -> http://brain.internal:8000/api/dd/report/victim/detail        ESCAPED
//   new URL(BASE + '/api/aria/conversations/' + encodeURIComponent(sid) + '/detail').href
//        -> .../api/aria/conversations/..%2F..%2Fdd%2Freport%2Fvictim/detail   CONTAINED
//
// R-F3682 fixed the REGEX-capture case on the unauthenticated vetting portal. The
// NAMED-param cases were missed:
//   server.mjs  /api/aria/conversations/:sessionId        GET / DELETE / PUT-title
//               -> carries _ariaHeaders(), the brain service token
//   server.mjs  /api/wa-listener/accounts/:id             GET / GET-qr / DELETE
//               -> carries Bearer ARIA_INTERNAL_TOKEN, which the listener's
//                  requireAuth accepts unconditionally
//
// Self-serve signup auto-approves to `active`, so "authenticated" is a free
// precondition for both.
//
// ── WHY TWO LAYERS, AND WHICH ONE IS THE GUARANTEE ───────────────────────────
// 1. `encodeURIComponent` at the call site is the CONTAINMENT GUARANTEE. It is
//    what the measurement above proves: no input can become a path separator,
//    because WHATWG does not re-decode `%2F` inside a path.
// 2. These validators are DEFENCE IN DEPTH. They exist so that a future edit
//    which drops the encode does not silently re-open the hole, and so obviously
//    hostile input is refused loudly at the edge instead of being forwarded in
//    escaped form. Both layers are asserted by the capability test.
//
// ── WHY THE SESSION-ID CHARSET IS WIDER THAN portalPath's ────────────────────
// A vetting-portal suffix has TWO legal shapes, so portalPath.mjs can enumerate
// them. A session id is an opaque, user-scoped handle minted in at least nine
// places, and an over-tight allowlist would 404 a user's own saved history —
// silently destroying access to real conversations to close a hole that layer 1
// already closes. The minters, read at source rather than assumed:
//
//   public/aria.html:783   `${USER_ID_SLUG}_${Date.now()}_${rand36}`  slug=[A-Za-z0-9]
//   lib/aria/emailReader.mjs:230   `email_compose_${Date.now()}`
//   lib/whatsapp/waListener.mjs    `wa_group_${name.replace(/[^a-zA-Z0-9]/g,'')}`
//   aria_service/routes/aria.py:3541   f"eval_{uuid4().hex[:10]}"
//   aria_service/intel/dd_orchestrator.py   f"dd_{uuid4().hex[:12]}"
//   aria_service/main.py:5050   f"client_{user}"      <- user may be an email
//   aria_service/.../aria_tui.py:446   f"tui_{...}"
//
// So the charset admits the separators an email or a composed id can contain
// (`.`, `@`, `:`, `-`, `_`) and refuses, exactly, the characters that can become
// or encode a path boundary: `/`, `\`, `%`, and any control byte. `..` is
// refused outright as a sequence — no minter above can produce it, and it is the
// only reason `.` would ever be dangerous.

/** Characters a legitimate session id may contain. No `/`, `\`, `%`, no controls. */
export const SESSION_ID_RE = /^[A-Za-z0-9._:@-]{1,200}$/;

/** WhatsApp account ids are minted `wa_<ts>_<6 base36>` (aria_wa_listener.mjs:4084). */
export const WA_ACCOUNT_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

/**
 * True iff `value` is safe to interpolate as ONE path segment of an upstream URL.
 *
 * Takes the value Express has ALREADY percent-decoded (`req.params.x`), so it
 * sees the same bytes the upstream fetch would — the only place the check means
 * anything.
 *
 * @param {unknown} value
 * @returns {boolean}
 */
export function isValidSessionId(value) {
  if (typeof value !== 'string' || value.length === 0) return false;
  // Explicit refusals ahead of the charset test. The regex below already excludes
  // every one of these; stating them keeps the intent readable if the charset is
  // ever widened, and `..` is the one the charset alone would NOT catch.
  if (/[\0-\x1f\x7f]/.test(value)) return false;   // NUL truncation, CRLF splitting
  if (value.includes('..')) return false;          // traversal, in any encoding
  return SESSION_ID_RE.test(value);
}

/**
 * True iff `value` is a legitimate WhatsApp account id.
 *
 * Tighter than isValidSessionId because this shape IS enumerable: the listener
 * mints `wa_<ts>_<6 base36>` and nothing else.
 *
 * @param {unknown} value
 * @returns {boolean}
 */
export function isValidWaAccountId(value) {
  if (typeof value !== 'string' || value.length === 0) return false;
  if (/[\0-\x1f\x7f]/.test(value)) return false;
  if (value.includes('..')) return false;
  return WA_ACCOUNT_ID_RE.test(value);
}
