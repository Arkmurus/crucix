// R-F3977 / C-66 — every email failure was silent, so account recovery could
// fail 100% with no signal anywhere.
//
//   server.mjs:6761
//     await sendPasswordResetEmail(email, user.fullName, resetCode).catch(() => {});
//   server.mjs:6763
//     res.json({ message: 'If that email is registered, a reset code has been sent.' });
//
// The user is told a code was sent regardless. One layer down, `sendMail` —
// the ONE point all fourteen senders go through — swallowed both of its failure
// modes into a console line and a `{sent:false}` nobody inspects:
//
//   lib/auth/email.mjs:151   transport missing -> console.log, return {sent:false}
//   lib/auth/email.mjs:165   send threw        -> console.warn, return {sent:false}
//
// §21b is explicit that "logged to console" is DARK, not wired, and §25 requires
// every output surface to report its delivery outcome. Signup verification,
// resend, welcome, password reset and the vetting invite all ride this path, so
// a broken SMTP credential silently breaks signup AND account recovery while
// every endpoint keeps answering success.
//
// The fix wires `sendMail` itself rather than the fourteen callers: one decision
// point, and a fifteenth sender added later inherits it. Same reasoning as C-43
// (mark crashes at the gather, not in each wrapper) and C-40 (a purpose, not a
// route list).
//
// The two failure modes are reported DIFFERENTLY on purpose. "SMTP not
// configured" is a standing platform state — announce-once, or a busy signup
// hour floods the ledger, which is the C-59 flood this repo has already paid
// for. A send EXCEPTION is a per-event incident and is reported each time.

import { test } from 'node:test';
import assert from 'node:assert/strict';

const MOD = '../lib/auth/email.mjs';

async function loadFresh() {
  // Fresh module instance so the announce-once latch starts clean.
  const mod = await import(`${MOD}?t=${Date.now()}${Math.random()}`);
  return mod;
}

function captureBrain() {
  const calls = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    calls.push({ url: String(url), body: JSON.parse(opts?.body || '{}') });
    return { ok: true, status: 200, json: async () => ({}) };
  };
  return {
    calls,
    restore() { globalThis.fetch = realFetch; },
  };
}

test('an unconfigured SMTP reports to the brain once, not per email', async () => {
  const brain = captureBrain();
  const prevHost = process.env.SMTP_HOST;
  delete process.env.SMTP_HOST;
  try {
    const { sendPasswordResetEmail, sendWelcomeEmail } = await loadFresh();
    await sendPasswordResetEmail('a@example.com', 'A', '123456');
    await sendWelcomeEmail('b@example.com', 'B');
    await sendPasswordResetEmail('c@example.com', 'C', '654321');

    const signals = brain.calls.filter(c => c.url.includes('/api/aria/brain/signal'));
    assert.equal(
      signals.length, 1,
      `expected ONE announce for a standing config state, got ${signals.length} — `
      + 'a per-email signal is the C-59 ledger flood in a different sink',
    );
    assert.match(JSON.stringify(signals[0].body).toLowerCase(), /smtp|not configured/);
  } finally {
    if (prevHost !== undefined) process.env.SMTP_HOST = prevHost;
    brain.restore();
  }
});

test('sendMail failures are reported, and the send result still says sent:false', async () => {
  const brain = captureBrain();
  const prevHost = process.env.SMTP_HOST;
  delete process.env.SMTP_HOST;
  try {
    const { sendPasswordResetEmail } = await loadFresh();
    const res = await sendPasswordResetEmail('a@example.com', 'A', '123456');
    assert.equal(res.sent, false, 'the caller contract must not change');
    assert.ok(res.reason, 'a reason must still be returned');
  } finally {
    if (prevHost !== undefined) process.env.SMTP_HOST = prevHost;
    brain.restore();
  }
});

test('a brain outage never breaks or blocks the email path', async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error('brain unreachable'); };
  const prevHost = process.env.SMTP_HOST;
  delete process.env.SMTP_HOST;
  try {
    const { sendPasswordResetEmail } = await loadFresh();
    const res = await sendPasswordResetEmail('a@example.com', 'A', '123456');
    assert.equal(res.sent, false);
  } finally {
    if (prevHost !== undefined) process.env.SMTP_HOST = prevHost;
    globalThis.fetch = realFetch;
  }
});

test('the wire lives in sendMail, not in the individual senders', async () => {
  const { readFile } = await import('node:fs/promises');
  const src = await readFile(new URL('../lib/auth/email.mjs', import.meta.url), 'utf8');

  const sendMailStart = src.indexOf('async function sendMail(');
  assert.ok(sendMailStart > -1, 'sendMail not found');
  const sendMailEnd = src.indexOf('\n}', sendMailStart);
  const body = src.slice(sendMailStart, sendMailEnd);

  assert.match(
    body, /reportEmailFailure|brain\/signal/,
    'sendMail does not report its failures — a broken SMTP credential still '
    + 'breaks signup and account recovery silently',
  );

  // One decision point: the individual senders must NOT each carry their own wire.
  const senderRegion = src.slice(src.indexOf('export async function sendVerificationEmail'));
  const perSender = (senderRegion.match(/reportEmailFailure\(/g) || []).length;
  assert.equal(
    perSender, 0,
    `${perSender} sender(s) carry their own failure wire — that is the route-list `
    + 'shape C-40 warns about; the fifteenth sender would be added dark',
  );
});
