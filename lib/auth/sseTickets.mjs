// lib/auth/sseTickets.mjs
//
// R-F1793 (2026-06-23) — short-lived, single-use tickets for SSE auth.
//
// SECURITY: browser EventSource cannot send an Authorization header, so SSE
// endpoints historically accepted the long-lived (7-day) JWT in `?token=`.
// A JWT in a query string leaks the live credential into web-server / fly
// access logs, browser history, and the Referer header of any outbound link
// on the page — a credential-exposure gap (aria-web audit #9, 2026-06-23).
//
// Fix: an already-authenticated client (Authorization header) POSTs to get a
// one-time ticket — random 256-bit, 60s TTL, single redemption — and passes
// THAT in the query string instead. A leaked ticket is useless after one use
// or 60 seconds, and it carries no standing identity by itself.
import { randomBytes } from 'node:crypto';

const TTL_MS = 60_000;
const _tickets = new Map(); // ticket -> { payload, exp }

export function issueSseTicket(payload) {
  const ticket = randomBytes(32).toString('base64url');
  _tickets.set(ticket, { payload, exp: Date.now() + TTL_MS });
  return ticket;
}

export function redeemSseTicket(ticket) {
  if (!ticket || typeof ticket !== 'string') return null;
  const rec = _tickets.get(ticket);
  if (!rec) return null;
  _tickets.delete(ticket);            // single use — gone whether valid or expired
  if (rec.exp < Date.now()) return null;
  return rec.payload;
}

// Bounded, timer-free sweep of expired tickets (call opportunistically).
export function sweepExpiredTickets(now = Date.now()) {
  let removed = 0;
  for (const [k, v] of _tickets) {
    if (v.exp < now) { _tickets.delete(k); removed++; }
  }
  return removed;
}

// Test/introspection helper — current live ticket count.
export function _ticketCount() {
  return _tickets.size;
}
