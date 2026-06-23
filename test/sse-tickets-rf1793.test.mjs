// test/sse-tickets-rf1793.test.mjs
//
// Capability test for R-F1793 — short-lived single-use SSE tickets replace the
// long-lived-JWT-in-?token= credential leak (aria-web audit #9, 2026-06-23).
//
// Security invariants:
//   - a ticket redeems AT MOST ONCE (single use)
//   - a ticket expires after its 60s TTL
//   - unknown / malformed / non-string tickets return null (no crash, no auth)
//   - tickets are unique + unguessable (256-bit random)
//
// Run: node test/sse-tickets-rf1793.test.mjs
import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import {
  issueSseTicket, redeemSseTicket, sweepExpiredTickets, _ticketCount,
} from '../lib/auth/sseTickets.mjs';

test('ticket redeems exactly once (single-use)', () => {
  const t = issueSseTicket({ userId: 'u1', role: 'admin' });
  const first = redeemSseTicket(t);
  assert.equal(first.userId, 'u1');
  assert.equal(first.role, 'admin');
  assert.equal(redeemSseTicket(t), null, 'a ticket must not redeem a second time');
});

test('unknown / malformed / non-string tickets return null (never authenticate)', () => {
  assert.equal(redeemSseTicket('does-not-exist'), null);
  assert.equal(redeemSseTicket(undefined), null);
  assert.equal(redeemSseTicket(''), null);
  assert.equal(redeemSseTicket(12345), null);
  assert.equal(redeemSseTicket(null), null);
});

test('expired ticket returns null after its 60s TTL', () => {
  mock.timers.enable({ apis: ['Date'] });
  try {
    const t = issueSseTicket({ userId: 'u2' });
    mock.timers.tick(61_000); // advance past the 60s TTL
    assert.equal(redeemSseTicket(t), null, 'an expired ticket must not redeem');
  } finally {
    mock.timers.reset();
  }
});

test('sweepExpiredTickets reclaims only expired entries', () => {
  mock.timers.enable({ apis: ['Date'] });
  try {
    issueSseTicket({ userId: 'old' });
    mock.timers.tick(61_000);
    const fresh = issueSseTicket({ userId: 'new' });
    const removed = sweepExpiredTickets();
    assert.ok(removed >= 1, 'the expired ticket should be swept');
    assert.equal(redeemSseTicket(fresh).userId, 'new', 'a fresh ticket survives the sweep');
  } finally {
    mock.timers.reset();
  }
});

test('tickets are unique and unguessable (256-bit)', () => {
  const a = issueSseTicket({ userId: 'x' });
  const b = issueSseTicket({ userId: 'x' });
  assert.notEqual(a, b, 'two issued tickets must differ');
  assert.ok(a.length >= 40, '256-bit base64url ticket is ~43 chars');
  redeemSseTicket(a); redeemSseTicket(b);
  assert.equal(typeof _ticketCount(), 'number');
});
