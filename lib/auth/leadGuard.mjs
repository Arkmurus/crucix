// lib/auth/leadGuard.mjs
// R-F3999 (C-80) — bot and mail-abuse bounds for the UNAUTHENTICATED lead form.
//
// `POST /api/leads` has to be open: a prospect requesting access has no account.
// It also sends an email to whatever address the body names, which makes it the
// one anonymous outbound-mail path in the app. It sat on the generic anonymous
// tier (150 requests / 15 min) with no bot defence, so a single IP could send 150
// emails per quarter-hour to an address of its choosing — from our domain and our
// SMTP reputation — and fill the operator's access-request queue with plausible
// entries.
//
// NO CAPTCHA, DELIBERATELY. CLAUDE.md §6 puts the burden of proof on any new
// third-party dependency, and a CAPTCHA is a third party watching the top of the
// funnel. It also taxes the one person this form exists for — a real prospect —
// to inconvenience a bot that can solve it for a fraction of a cent. A honeypot
// costs a legitimate user nothing because they never see the field.
//
// Extracted from server.mjs so the decisions are contract-testable: that file
// boots a live app on import, so anything left inline can only be grep-tested.

/**
 * Decoy field name.
 *
 * R-F4018 (C-93) — was `website_url`, chosen to "look worth filling". That was a
 * mistake, and the failure mode is the expensive direction: `website_url` with a
 * "Website" label is precisely the shape a browser's autofill heuristics target
 * for a URL/organisation field. `autocomplete="off"` is honoured inconsistently
 * for non-credential inputs, so a real prospect whose browser helpfully filled it
 * would have been silently discarded — and told their request succeeded.
 *
 * A plausible name buys almost nothing anyway. The bots this catches are the ones
 * that fill EVERY input, and they fill a meaningless name just as readily; the
 * smarter bots that skip obvious decoys also skip hidden fields. So the name is
 * now semantically inert — no address, url, name or organisation token for any
 * autofill vocabulary to match — while staying self-documenting to a maintainer.
 *
 * NOT `type="hidden"`. Bots routinely skip hidden inputs precisely because they
 * are a known honeypot shape; a CSS/aria-hidden field looks like a real one to a
 * parser and is invisible to a person and to a screen reader.
 */
export const LEAD_HONEYPOT_FIELD = 'lead_confirm_blank';

/**
 * True when the decoy was filled in.
 *
 * Whitespace is NOT a fill — an autofill that writes a space, or a stray
 * keystroke, must not silently discard a genuine request. A missing body is not
 * a bot either: callers may post an empty object and that is a validation
 * problem, handled elsewhere, not a bot signal.
 */
export function leadHoneypotTripped(body) {
  if (!body || typeof body !== 'object') return false;
  return String(body[LEAD_HONEYPOT_FIELD] ?? '').trim().length > 0;
}

// ── Per-destination bound ────────────────────────────────────────────────────
// A per-IP rate limit does not protect the VICTIM of a mail-bomb. The source
// address rotates trivially; the target is the constant. So the bound is keyed on
// the address we would MAIL, not on who asked.
//
// Deliberately per-address rather than global: a global counter would let one
// attacker deny access requests to every other prospect, turning an anti-abuse
// control into the abuse.
//
// In-memory and process-local, matching the existing rate limiters (this app runs
// single-machine by design — fly.web.toml pins one instance for the /data volume).
// A restart clears it, which is the correct failure direction for a control whose
// worst case is letting a few extra genuine emails through.

const MAX_MAILS_PER_ADDRESS = 3;
const WINDOW_MS = 60 * 60 * 1000;          // 1 hour
const MAX_TRACKED_ADDRESSES = 5000;        // bound the map; see _sweep below

/** address -> { count, first } */
const _destinations = new Map();

function _normalise(email) {
  return String(email || '').trim().toLowerCase();
}

function _sweep(now) {
  // Drop expired entries first; only if that is not enough, drop the oldest.
  // Without a bound this map is an unbounded memory sink on an endpoint anyone
  // can call — the anti-abuse control must not itself be the abuse vector.
  for (const [k, v] of _destinations) {
    if (now - v.first > WINDOW_MS) _destinations.delete(k);
  }
  if (_destinations.size <= MAX_TRACKED_ADDRESSES) return;
  const excess = _destinations.size - MAX_TRACKED_ADDRESSES;
  let i = 0;
  for (const k of _destinations.keys()) {
    if (i++ >= excess) break;
    _destinations.delete(k);
  }
}

/**
 * True when this address has already been mailed too often in the window.
 *
 * Counts on every call, so the caller must invoke it exactly once per intended
 * send. Returns false (allowed) for the first request to any address — a genuine
 * prospect must never be refused on their first attempt.
 */
export function leadDestinationBlocked(email, now = Date.now()) {
  const key = _normalise(email);
  if (!key) return false;            // no address → nothing to bomb; validation handles it
  _sweep(now);
  const entry = _destinations.get(key);
  if (!entry || now - entry.first > WINDOW_MS) {
    _destinations.set(key, { count: 1, first: now });
    return false;
  }
  entry.count += 1;
  return entry.count > MAX_MAILS_PER_ADDRESS;
}

/** Test-only reset. Exported so tests do not reach into module state. */
export function _resetLeadDestinations() {
  _destinations.clear();
}
