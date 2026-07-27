import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const server = readFileSync('server.mjs', 'utf8');
const mailer = readFileSync('lib/auth/email.mjs', 'utf8');

// R-F3253 — one question, one implementation.
//
// server.mjs re-derived "is SMTP configured?" from bare EMAIL_HOST/USER/PASS
// and knew nothing about the ARIA_SMTP_* fallback the mailer resolves through.
// The live log carried both answers within the same second, and the wrong one
// claimed mail was disabled while it was sending. One of the duplicates was a
// SEND GATE, so the duplicate-registration warning to a legitimate account
// owner was silently never delivered.

test('the mailer is the only place that decides whether SMTP is configured', () => {
  assert.match(mailer, /export const isConfigured/,
    'lib/auth/email.mjs must export the single answer');

  // No file outside the mailer may reconstruct the check from raw env vars.
  const reDerived = /process\.env\.EMAIL_HOST\s*&&[\s\S]{0,80}?process\.env\.EMAIL_PASS/;
  assert.doesNotMatch(server, reDerived,
    'server.mjs re-derives SMTP configuration instead of importing isConfigured');
});

test('server.mjs imports the single source', () => {
  assert.match(server, /isConfigured as smtpIsConfigured/,
    'server.mjs must import the mailer\'s own answer');
});

test('the send gate uses the shared answer, not raw env vars', () => {
  // The duplicate-registration notice is a security path: it tells a real
  // account owner that someone tried to register with their address.
  assert.match(server, /emailExists && smtpIsConfigured/,
    'the duplicate-registration notice is gated on raw env vars again — it '
    + 'will silently not send whenever credentials come via the fallback');
});

test('the mailer honours the ARIA fallback the duplicates did not know about', () => {
  assert.match(mailer, /ARIA_SMTP_|_ARIA_FALLBACK/,
    'the fallback that made the two answers disagree must still exist — this '
    + 'test is meaningless if it was removed rather than respected');
});
