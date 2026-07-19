// test/cross-health-false-offline-rf2776.test.mjs
//
// CAPABILITY test for R-F2776 — GET /api/health/cross must never report a
// slow-but-healthy brain as OFFLINE, and must never render a verdict from a
// timeout.
//
// It drives the REAL production functions (lib/health/crossHealth.mjs, which
// server.mjs calls) with an injected fetch, not a replica of the logic (§3c).
//
// THE DEFECT this locks out: a single 4s AbortSignal.timeout decided `ok`, so
// aria-intel — whose documented boot takes ~10 minutes to go green (§11c) —
// was reported flatly offline while it was alive and healthy. Absence of a
// timely answer was rendered as a measured negative, the same fabrication class
// CLAUDE.md gate #3 was rewritten to kill.
//
// Run: node test/cross-health-false-offline-rf2776.test.mjs

import { probeFlyHealth, combineCrossOk, isTimeoutError } from '../lib/health/crossHealth.mjs';

let failures = 0;
function check(name, cond) {
  console.log(`${cond ? 'ok  ' : 'FAIL'} - ${name}`);
  if (!cond) failures++;
}

const FLY = 'https://aria-intel.test';
const jsonRes = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
  text: async () => JSON.stringify(body),
});
const timeoutErr = () => Object.assign(new Error('The operation was aborted due to timeout'), { name: 'TimeoutError' });
const transportErr = () => Object.assign(new Error('connect ECONNREFUSED 10.0.0.1:443'), { name: 'TypeError' });

// ── 1. Healthy + fast → online ───────────────────────────────────────────────
{
  const fly = await probeFlyHealth({
    flyUrl: FLY,
    fetchImpl: async () => jsonRes(200, { status: 'alive', build_rev: 'abc1234' }),
  });
  check('fast healthy → ok true', fly.ok === true);
  check('fast healthy → state online', fly.state === 'online');
  check('build_rev surfaced', fly.build_rev === 'abc1234');
  check('combine → true', combineCrossOk(true, fly.ok) === true);
}

// ── 2. THE REGRESSION: healthy but SLOW → online-degraded, NEVER offline ─────
// First budget times out (the old code's verdict point), retry succeeds. This is
// the live slow-boot case that used to be reported as down.
{
  let call = 0;
  const fly = await probeFlyHealth({
    flyUrl: FLY,
    budgets: [10, 5000],
    fetchImpl: async () => {
      call++;
      if (call === 1) throw timeoutErr();
      await new Promise((r) => setTimeout(r, 25));
      return jsonRes(200, { status: 'alive', build_rev: 'slowboot' });
    },
  });
  check('slow-but-healthy → retried once', call === 2);
  check('slow-but-healthy → ok TRUE (not offline)', fly.ok === true);
  check('slow-but-healthy → state degraded_slow', fly.state === 'degraded_slow');
  check('slow-but-healthy → NOT state offline', fly.state !== 'offline');
  check('slow-but-healthy → combine true', combineCrossOk(true, fly.ok) === true);
}

// ── 2b. The DEFAULT budget must itself retry ────────────────────────────────
// Tests 2/3 pass explicit budgets, so they would not notice the default
// collapsing back to a single probe — which is exactly the pre-R-F2776 shape.
// Assert the production default gives the slow-boot case a second chance.
{
  let call = 0;
  const fly = await probeFlyHealth({
    flyUrl: FLY,
    fetchImpl: async () => {           // no `budgets` → production default
      call++;
      if (call === 1) throw timeoutErr();
      return jsonRes(200, { status: 'alive' });
    },
  });
  check('DEFAULT budgets retry after a timeout', call === 2);
  check('DEFAULT budgets → slow brain reads healthy', fly.ok === true);
}

// ── 3. Total timeout → NO VERDICT (tri-state null), never false ──────────────
{
  let call = 0;
  const fly = await probeFlyHealth({
    flyUrl: FLY,
    budgets: [10, 20],
    fetchImpl: async () => { call++; throw timeoutErr(); },
  });
  check('timeout → both budgets attempted', call === 2);
  check('timeout → ok is NULL (could not measure)', fly.ok === null);
  check('timeout → ok is NOT false', fly.ok !== false);
  check('timeout → state unknown', fly.state === 'unknown');
  check('timeout → state is NOT offline', fly.state !== 'offline');
  check('timeout → reason probe_timeout', fly.reason === 'probe_timeout');
  check('timeout → error disclaims proof of down', /NOT proof/.test(fly.error || ''));
  check('timeout → combine stays null (unknown, not disconnected)', combineCrossOk(true, fly.ok) === null);
}

// ── 4. Transport refusal → offline IS the honest verdict, and NOT retried ────
{
  let call = 0;
  const fly = await probeFlyHealth({
    flyUrl: FLY,
    budgets: [10, 5000],
    fetchImpl: async () => { call++; throw transportErr(); },
  });
  check('transport error → NOT retried (real evidence)', call === 1);
  check('transport error → ok false', fly.ok === false);
  check('transport error → state offline', fly.state === 'offline');
  check('transport error → combine false', combineCrossOk(true, fly.ok) === false);
}

// ── 5. Answered non-2xx → degraded, not offline (it responded) ───────────────
{
  const fly = await probeFlyHealth({ flyUrl: FLY, fetchImpl: async () => jsonRes(503, {}) });
  check('HTTP 503 → ok false', fly.ok === false);
  check('HTTP 503 → state degraded (it ANSWERED)', fly.state === 'degraded');
  check('HTTP 503 → state is NOT offline', fly.state !== 'offline');
  check('HTTP 503 → http_status surfaced', fly.http_status === 503);
}

// ── 6. Answered 2xx with an unhealthy body → degraded ────────────────────────
{
  const fly = await probeFlyHealth({ flyUrl: FLY, fetchImpl: async () => jsonRes(200, { status: 'critical' }) });
  check('unhealthy body → ok false', fly.ok === false);
  check('unhealthy body → state degraded', fly.state === 'degraded');
}

// ── 7. Answered 2xx with non-JSON body → up, body surfaced ──────────────────
{
  const fly = await probeFlyHealth({
    flyUrl: FLY,
    fetchImpl: async () => ({
      ok: true, status: 200,
      json: async () => { throw new Error('not json'); },
      text: async () => 'OK',
    }),
  });
  check('non-JSON 2xx → ok true', fly.ok === true);
  check('non-JSON 2xx → body_text surfaced', fly.body_text === 'OK');
}

// ── 8. Error-class discrimination is what the whole fix rests on ─────────────
check('TimeoutError classified as timeout', isTimeoutError(timeoutErr()) === true);
check('AbortError classified as timeout', isTimeoutError(Object.assign(new Error('x'), { name: 'AbortError' })) === true);
check('TypeError NOT classified as timeout', isTimeoutError(transportErr()) === false);

// ── 9. combineCrossOk never collapses null to false ─────────────────────────
check('combine(null fly) → null even when node ok', combineCrossOk(true, null) === null);
check('combine(node down, fly ok) → false', combineCrossOk(false, true) === false);

console.log(failures === 0 ? '\nPASS — all checks green' : `\nFAIL — ${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);
