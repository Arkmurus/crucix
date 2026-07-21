// test/errortracker-own-code-domain-rf2821.test.mjs
//
// R-F2821 — CAPABILITY test: own-code / process failures in the Node tier must
// actually REACH the brain, and the wire must know when it hasn't.
//
// THE DEFECT (verified against the pre-fix module):
//   classifyError() was written for OUTBOUND dependency failures, where a 500 or
//   a timeout genuinely is transient (R-F1016, CLAUDE.md §14). The same function
//   was reused for OWN-CODE failures, which match none of its keywords and fell to
//   `return SEVERITY.TRANSIENT`. _reportToBrain then dropped TRANSIENT. So:
//       uncaughtException      (server.mjs:7603) → transient → DROPPED
//       unhandledRejection     (server.mjs:7599) → transient → DROPPED
//       boot listen_error      (server.mjs:7302) → transient → DROPPED
//       brute-force lockout    (server.mjs:4983) → transient → DROPPED
//       every 500 via expressMiddleware          → transient → DROPPED
//   R-F2182 wired unhandledRejection to the brain in good faith; the wire was
//   severed one layer down at the severity filter, so the tier looked wired (§21a)
//   and emitted nothing.
//
//   Second strand: _reportToBrain never checked res.ok and swallowed everything in
//   a bare `catch {}` — a brain returning 401/404/500 was indistinguishable from a
//   successful delivery. §21a: a signal that silently fails is still dark.
//
// Run: node --test test/errortracker-own-code-domain-rf2821.test.mjs

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { classifyError, SEVERITY, OWN_CODE_SOURCES, ErrorTracker } from '../lib/observability/errorTracker.mjs';

/** Stand up a tracker with a controllable brain endpoint. */
function makeTracker({ respond } = {}) {
  process.env.ARIA_SERVICE_URL = 'http://brain.test';
  const sent = [];
  const tracker = new ErrorTracker();
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    sent.push({ url, body: JSON.parse(opts.body) });
    if (typeof respond === 'function') return respond(sent.length);
    return { ok: true, status: 200 };
  };
  return { tracker, sent, restore: () => { globalThis.fetch = realFetch; } };
}

describe('R-F2821 — the own-code failure domain', () => {
  test('every own-code source classifies as PROCESS, not transient', () => {
    // These are the exact shapes the live call sites produce.
    const cases = [
      ['web_process', new TypeError("Cannot read properties of undefined (reading 'x')")],
      ['web_process', new ReferenceError('foo is not defined')],
      ['boot', Object.assign(new Error('listen EADDRINUSE'), { code: 'EADDRINUSE' })],
      ['auth', null],                                  // login_throttle_lockout passes null
      ['express_route', Object.assign(new Error('boom'), { status: 500 })],
    ];
    for (const [source, err] of cases) {
      assert.equal(classifyError(err, source), SEVERITY.PROCESS,
        `${source} failure classified as something other than PROCESS — it would be dropped`);
    }
  });

  test('PROCESS is on the escalation list (the drop that caused this bug)', () => {
    assert.ok(ErrorTracker.ESCALATE.includes(SEVERITY.PROCESS),
      'PROCESS must escalate, or every own-code failure stays dark');
    // The guards that must NOT be undone:
    assert.ok(!ErrorTracker.ESCALATE.includes(SEVERITY.TRANSIENT),
      'R-F1016: dependency blips must stay off the escalation path');
    assert.ok(!ErrorTracker.ESCALATE.includes(SEVERITY.CLIENT_INPUT),
      'R-F2452: a client malformed body is not our defect');
  });

  test('ANTI-REGRESSION R-F1016 — outbound dependency failures stay transient', () => {
    // If this ever flips, the gap pipeline floods and the real signal is lost.
    assert.equal(classifyError({ status: 503, message: 'gateway timeout' }, 'gdelt'), SEVERITY.TRANSIENT);
    assert.equal(classifyError(new Error('socket hang up'), 'reliefweb'), SEVERITY.TRANSIENT);
    assert.equal(classifyError(new Error('network unreachable'), 'world_bank'), SEVERITY.TRANSIENT);
  });

  test('ANTI-REGRESSION R-F2452 — a client malformed body on express_route beats PROCESS', () => {
    // express_route IS an own-code source, so CLIENT_INPUT must be checked first
    // or we would start blaming ourselves for the client's bad JSON.
    const err = Object.assign(new SyntaxError('Unexpected token } in JSON'), {
      type: 'entity.parse.failed', status: 400,
    });
    assert.equal(classifyError(err, 'express_route'), SEVERITY.CLIENT_INPUT);
  });

  test('the domain is declared by SOURCE, so a new call site cannot silently go dark', () => {
    for (const s of ['web_process', 'boot', 'express_route', 'auth']) {
      assert.ok(OWN_CODE_SOURCES.has(s), `${s} must be registered as an own-code source`);
    }
  });
});

describe('R-F2821 — the wire reports its own delivery', () => {
  test('CAPABILITY: an uncaught-exception-shaped failure is actually SENT', async () => {
    const { tracker, sent, restore } = makeTracker();
    try {
      tracker.record('web_process', 'uncaught_exception',
        new TypeError("Cannot read properties of undefined (reading 'user')"));
      await new Promise((r) => setTimeout(r, 50));
      assert.equal(sent.length, 1, 'the crash never reached the brain');
      assert.match(sent[0].url, /\/api\/aria\/brain\/signal$/);
      assert.equal(sent[0].body.signal_type, 'node_tier_failure_process');
      assert.ok(sent[0].body.signal_type.includes('fail'),
        'signal_type must contain "fail" to route to capability_gaps (R-F887)');
      assert.equal(tracker.brainWireStats().delivered, 1);
    } finally { restore(); }
  });

  test('CAPABILITY: a brain returning 401 is recorded as a DROP, not a success', async () => {
    const { tracker, restore } = makeTracker({ respond: () => ({ ok: false, status: 401 }) });
    try {
      tracker.record('web_process', 'uncaught_exception', new Error('kaboom'));
      await new Promise((r) => setTimeout(r, 1200));  // allow the single retry
      const s = tracker.brainWireStats();
      assert.equal(s.delivered, 0, 'a 401 must never count as delivered');
      assert.equal(s.dropped, 1, 'an undelivered signal must be counted, not swallowed');
      assert.match(s.lastError, /401/);
    } finally { restore(); }
  });

  test('a transient brain failure is retried once and then succeeds', async () => {
    const { tracker, restore } = makeTracker({
      respond: (n) => (n === 1 ? { ok: false, status: 503 } : { ok: true, status: 200 }),
    });
    try {
      tracker.record('boot', 'listen_error', new Error('EADDRINUSE'));
      await new Promise((r) => setTimeout(r, 1200));
      assert.equal(tracker.brainWireStats().delivered, 1,
        'the brain’s ~10-min cold boot must not cost us the signal');
    } finally { restore(); }
  });

  test('a request storm collapses to one signal per class (no gap-pipeline flood)', async () => {
    const { tracker, sent, restore } = makeTracker();
    try {
      for (let i = 0; i < 40; i++) {
        tracker.record('express_route', 'UnhandledError', Object.assign(new Error('boom'), { status: 500 }));
      }
      await new Promise((r) => setTimeout(r, 80));
      // Count the FAILURE CLASS specifically. The circuit breaker legitimately
      // emits its own distinct `circuit_opened` signal at threshold
      // (errorTracker.mjs:150) — that is a different event, not a duplicate, and
      // suppressing it would lose real information. Assert on the class, not on
      // the raw total, or this test would push us to silence a good signal.
      const ofClass = (t) => sent.filter((s) => s.body.signal_type === t).length;
      assert.equal(ofClass('node_tier_failure_process'), 1,
        `40 identical failures produced ${ofClass('node_tier_failure_process')} process signals`);
      assert.ok(tracker.brainWireStats().throttled >= 39);
      const before = sent.length;
      // ...but a DIFFERENT failure during the same storm still gets through.
      tracker.record('web_process', 'uncaught_exception', new Error('different failure'));
      await new Promise((r) => setTimeout(r, 50));
      assert.equal(sent.length, before + 1, 'throttling must be per-class, not global');
    } finally { restore(); }
  });

  test('telemetry never throws into the app, even when the brain is unreachable', async () => {
    const { tracker, restore } = makeTracker({
      respond: () => { throw new Error('ECONNREFUSED'); },
    });
    try {
      assert.doesNotThrow(() => tracker.record('web_process', 'uncaught_exception', new Error('x')));
      await new Promise((r) => setTimeout(r, 1200));
      assert.equal(tracker.brainWireStats().dropped, 1);
    } finally { restore(); }
  });
});
