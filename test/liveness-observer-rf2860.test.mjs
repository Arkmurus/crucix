// R-F2860 — external liveness observer: aria-web watches aria-intel.
//
// WHY. web_integrity_agent runs INSIDE aria-intel and polls localhost — so when
// aria-intel dies or crash-loops, the agent dies WITH it and can never observe or
// record its own death ("9 passed" is guaranteed whenever the log line appears at
// all — certification-by-absence, the gate-#3/#4/#6 class). An in-process monitor
// measures SLOW, never DEAD. Only an observer in a DIFFERENT process (aria-web) that
// records to a sink OUTLIVING aria-intel can close this gap.
//
// The two properties under test are equal and opposite:
//   1. It must PROVABLY FIRE on a real outage (record + alert + report-to-brain) —
//      a monitor never seen to fail is not a monitor (the negative control).
//   2. It must NOT cry wolf during the legit ~10-min cold boot / a rolling deploy —
//      a false alarm every deploy destroys the alert's trust (the false-positive
//      control). This is the R-F1380/R-F1381 discipline, now external.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { classifyProbe, createLivenessObserver } from '../lib/observability/livenessObserver.mjs';

// Small thresholds keep the tests concise; production defaults are larger.
const CFG = {
  sustainedFailuresToConfirm: 3,
  flapWindowProbes: 8,
  flapFailuresToConfirm: 4,
  recoveryConsecutiveAlive: 2,
  maxOutages: 5,
};

function harness(states, config = CFG) {
  const q = [...states];
  const store = {
    _data: [],
    read() { return this._data; },
    async write(d) { this._data = d.map((x) => ({ ...x })); },
  };
  const notes = [];
  const brain = [];
  let t = 1_000_000;
  const obs = createLivenessObserver({
    serviceUrl: 'http://aria-intel.internal:8000',
    probeFn: async () => {
      const item = q.length ? q.shift() : 'online';
      if (item && typeof item === 'object') return item;               // explicit probe object
      const http_status = (item === 'offline' || item === 'unknown') ? 0 : 200;
      return { state: item, http_status, build_rev: 'sha_x' };
    },
    store,
    notifyFn: async (m) => { notes.push(m); },
    brainPostFn: async (p) => { brain.push(p); },
    now: () => (t += 30_000),   // 30s per tick
    logger: { warn() {}, info() {} },
    config,
  });
  return { obs, store, notes, brain };
}

async function run(obs, n) { for (let i = 0; i < n; i++) await obs.tick(); }

// ── the classifier ───────────────────────────────────────────────────────────

test('classifyProbe: offline/unknown/5xx are failures; a 2xx/4xx answer is alive', () => {
  assert.equal(classifyProbe({ state: 'offline' }), 'failure');                      // transport refusal
  assert.equal(classifyProbe({ state: 'unknown' }), 'failure');                      // timeout
  assert.equal(classifyProbe({ state: 'online', http_status: 200 }), 'alive');
  assert.equal(classifyProbe({ state: 'degraded_slow', http_status: 200 }), 'alive'); // warming/slow — answered
  assert.equal(classifyProbe({ state: 'degraded', http_status: 200 }), 'alive');      // 2xx-unhealthy — app answered
  // THE public-url down signal: fly-proxy returns 5xx while the machine is dead/booting.
  assert.equal(classifyProbe({ state: 'degraded', http_status: 502 }), 'failure');
  assert.equal(classifyProbe({ state: 'degraded', http_status: 503 }), 'failure');
});

test('a sustained fly-proxy 5xx (app down BEHIND the proxy) is confirmed DOWN', async () => {
  // probeFlyHealth calls a 502 "degraded" because SOMETHING answered — but it is the
  // proxy, not the app. Without the http_status rule this reads as alive = false negative.
  const { obs, notes, store } = harness(Array(4).fill({ state: 'degraded', http_status: 502, build_rev: 'x' }));
  await run(obs, 4);
  assert.equal(obs.snapshot().status, 'down', 'a 502 storm must confirm DOWN, not read as alive');
  assert.ok(notes.some((m) => /DOWN/.test(m)));
  assert.equal(store._data.length, 1);
});

// ── POSITIVE control: a healthy brain must never alarm ────────────────────────

test('a continuously healthy brain NEVER alarms and records nothing', async () => {
  const { obs, store, notes, brain } = harness(Array(20).fill('online'));
  await run(obs, 20);
  assert.equal(obs.snapshot().status, 'up');
  assert.equal(notes.length, 0, 'no operator alert on a healthy brain');
  assert.equal(store._data.length, 0, 'no outage recorded on a healthy brain');
  assert.equal(brain.length, 0);
});

// ── FALSE-POSITIVE control: a boot/deploy blip must NOT be flagged ────────────

test('a short boot/deploy blip (below sustained threshold) does NOT alarm', async () => {
  // 2 offline (a ~1min bind window) then healthy — below sustainedFailuresToConfirm=3
  const { obs, store, notes } = harness(['offline', 'offline', 'online', 'online', 'online']);
  await run(obs, 5);
  assert.equal(obs.snapshot().status, 'up', 'a 2-probe blip must not confirm DOWN');
  assert.equal(notes.length, 0, 'no cry-wolf on a boot/deploy blip');
  assert.equal(store._data.length, 0);
});

// ── NEGATIVE control: a real outage MUST fire (record + alert + brain report) ──

test('a SUSTAINED outage is confirmed, alerted, recorded, and reported to the brain on recovery', async () => {
  const { obs, store, notes, brain } = harness([
    'offline', 'offline', 'offline',     // 3 consecutive → confirm DOWN
    'offline',                           // still down (ongoing update)
    'online', 'online',                  // 2 consecutive alive → recovery
  ]);
  await run(obs, 6);

  assert.equal(obs.snapshot().status, 'up', 'must return to up after recovery');
  // it FIRED — the whole point:
  assert.ok(notes.some((m) => /DOWN/.test(m)), 'a DOWN alert must reach the operator');
  assert.ok(notes.some((m) => /RECOVER/i.test(m)), 'a RECOVERY alert must reach the operator');
  // durable record survives aria-intel death:
  assert.equal(store._data.length, 1, 'exactly one outage recorded');
  const o = store._data[0];
  assert.equal(o.status, 'closed', 'outage closed on recovery');
  assert.ok(o.duration_s >= 0 && Number.isFinite(o.duration_s), 'outage has a duration');
  assert.ok(o.down_from && o.recovered_at, 'outage has both endpoints');
  // §25 proprioception — the brain is told about the death it could not see itself:
  assert.equal(brain.length, 1, 'exactly one brain report on recovery');
  assert.match(brain[0].signal_type, /unavailable/, 'signal_type routes to capability_gaps');
  assert.match(brain[0].source, /external_observer/);
});

// ── crash-loop (flapping) must also be caught, not just a clean death ─────────

test('a flapping crash-loop (interspersed failures) is caught by the window rule', async () => {
  // offline/online alternating: 4 failures within an 8-probe window → flap-confirm
  const { obs, store, notes } = harness([
    'offline', 'online', 'offline', 'online', 'offline', 'online', 'offline', 'online',
  ]);
  await run(obs, 8);
  assert.equal(obs.snapshot().status, 'down', 'a crash-loop must be confirmed DOWN');
  assert.ok(notes.some((m) => /DOWN/.test(m)));
  assert.equal(store._data.length, 1);
  // down_from must be the START of the trouble, not the latest failure:
  assert.ok(store._data[0].down_from <= store._data[0].confirmed_at);
});

// ── bulletproof: a throwing sink must NEVER break the observer loop ───────────

test('a throwing notify/store/brain sink does not break tick or lose the state machine', async () => {
  const q = ['offline', 'offline', 'offline', 'online', 'online'];
  let t = 0;
  const obs = createLivenessObserver({
    serviceUrl: 'http://x',
    probeFn: async () => ({ state: q.length ? q.shift() : 'online' }),
    store: { read() { return []; }, async write() { throw new Error('disk full'); } },
    notifyFn: async () => { throw new Error('telegram down'); },
    brainPostFn: async () => { throw new Error('brain unreachable'); },
    now: () => (t += 1000),
    logger: { warn() {}, info() {} },
    config: CFG,
  });
  // must not throw despite every sink failing:
  for (let i = 0; i < 5; i++) await obs.tick();
  assert.equal(obs.snapshot().status, 'up', 'state machine still advances to recovery even when sinks throw');
});

// ── a probe that THROWS is treated as a failure, not a crash ──────────────────

test('a probe that throws counts as a failure (unknown), never crashes the loop', async () => {
  let t = 0;
  const obs = createLivenessObserver({
    serviceUrl: 'http://x',
    probeFn: async () => { throw new Error('DNS boom'); },
    store: { _d: [], read() { return this._d; }, async write(d) { this._d = d; } },
    notifyFn: async () => {},
    brainPostFn: async () => {},
    now: () => (t += 30000),
    logger: { warn() {}, info() {} },
    config: CFG,
  });
  await run(obs, 3);
  assert.equal(obs.snapshot().status, 'down', '3 throwing probes = sustained failure = DOWN');
});

// ── disabled when unconfigured ────────────────────────────────────────────────

test('no serviceUrl → the observer is a no-op (never probes, never alarms)', async () => {
  const obs = createLivenessObserver({
    serviceUrl: '', probeFn: async () => { throw new Error('should not be called'); },
    store: { read() { return []; }, async write() {} },
    notifyFn: async () => {}, brainPostFn: async () => {},
    logger: { warn() {}, info() {} }, config: CFG,
  });
  const r = await obs.tick();
  assert.equal(r.disabled, true);
});
