// test/brain-banner-403-honest-rf2876.test.mjs
//
// R-F2876 — the brain page told the operator to do something that CANNOT work.
//
// /aria-brain lumped 401 and 403 into one `_authGated` set, then GUESSED the
// message from whether a token was present:
//
//     hasToken  -> "N operator panels require re-authentication. Sign in again."
//     !hasToken -> "N operator panels require sign-in to display."
//
// Four panels (/autonomy/composite, /autonomy/surface, /autonomous/status,
// /autonomous/dlq) return 403, and NO sign-in can ever change that. Verified
// live 2026-07-22 against the brain itself:
//
//     403 {"detail":"Operator-tier token required for this control/destructive endpoint."}
//     ARIA_OPERATOR_TOKEN on the web tier = false
//
// That is R-F2139 token scoping working AS DESIGNED: control-plane routes
// (/autonomous/*, /autonomy/*, self-deploy, cost-cap, purge, credentials,
// restore) demand the OPERATOR tier, while the web tier deliberately holds only
// the shared service token. The gate is on the WEB TIER's token, not on the
// user's identity — so the admin is already as authorised as a browser can be.
//
// THE CLASS THIS CLOSES — the same defect shape found four times today:
//   R-F2867  /api/health hid 3 of 5 source buckets and invented a total
//   R-F2869  the dashboard called not_configured sources "degraded"
//   R-F2873  a nav fetch sent no auth header and hid every gated tab
//   R-F2876  this: a remedy asserted that cannot possibly work
// Every one is the UI stating something it has not verified. The structural
// answer is the same each time: DERIVE the claim from evidence (here, the actual
// HTTP status) and never infer a cause from an absence.
//
// Run: node --test test/brain-banner-403-honest-rf2876.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const HTML = readFileSync(new URL('../public/aria-brain.html', import.meta.url), 'utf8');

/** Source with comments stripped — a guard must never fire on its own docs. */
const CODE = HTML
  // Only remove standalone block comments. Route documentation contains text
  // such as `/autonomous/*`; a generic block-comment regex treats that path as
  // an opener and erases live code through the next unrelated terminator.
  .replace(/^\s*\/\*[\s\S]*?^\s*\*\/\s*$/gm, '')
  .split(/\r?\n/)
  .filter((l) => !l.trim().startsWith('//'))
  .join('\n');

test('R-F2876: 401 and 403 are tracked SEPARATELY', () => {
  assert.match(CODE, /_forbidden\s*=\s*new Set\(\)/,
    'a 403 is not a 401 — they need different sets to get different messages');
  assert.match(CODE, /_authGated\s*=\s*new Set\(\)/, '401 tracking must remain');
});

test('R-F2876: the status decides the bucket — no guessing', () => {
  assert.match(CODE, /res\.status === 401/, '401 must route to the sign-in bucket');
  assert.match(CODE, /res\.status === 403/, '403 must route to the forbidden bucket');
  // Scope to fetchJson's probe branch. The AGGREGATE loader may legitimately
  // treat 401/403 alike — it only invalidates the cache and falls through to
  // per-path probing, which is where the classification actually matters.
  const probe = CODE.slice(CODE.indexOf("const res = await API.probe('/api/aria' + path)"));
  assert.ok(!/res\.status === 401 \|\| res\.status === 403/.test(probe.slice(0, 800)),
    'THE BUG: lumping them together in fetchJson forced a guessed message');
});

test('R-F2876: the 403 message never promises that signing in helps', () => {
  const i = CODE.indexOf('control-plane');
  assert.ok(i > 0, 'a 403 message branch must exist');
  const seg = CODE.slice(i, i + 600);
  assert.ok(!/[Ss]ign in|[Ss]ign-in|re-authenticat/.test(seg),
    'no sign-in remedy may be offered for a 403 — it cannot work');
  assert.match(seg, /operator/i, 'it must name the real reason: operator-tier');
});

test('R-F2876: the 401 message DOES still offer sign-in', () => {
  // Tightening 403 must not remove the correct remedy for a genuine 401.
  const i = CODE.indexOf('to display. The endpoints are');
  assert.ok(i > 0, 'a 401 message branch must exist');
  const seg = CODE.slice(i - 200, i + 200);
  assert.match(seg, /[Ss]ign-in|[Ss]ign in/, 'a real 401 IS fixed by signing in — keep saying so');
});

test('R-F2876: the hasToken GUESS is gone', () => {
  // The old branch inferred the cause from token presence rather than from the
  // response. That inference is what produced an impossible instruction, and it
  // was also the R-F2873 window.API bug site.
  assert.ok(!/const hasToken/.test(CODE),
    'the cause must come from the HTTP status, never from a token-presence guess');
});

test('R-F2876: both buckets are cleared on success', () => {
  // A panel that starts working must drop out of BOTH sets, or the banner
  // becomes a permanent false alarm.
  const seg = CODE.slice(CODE.indexOf('function _trackFetchSuccess'),
                         CODE.indexOf('function _trackFetchSuccess') + 500);
  assert.match(seg, /_authGated\.delete\(path\)/, '401 bucket must clear');
  assert.match(seg, /_forbidden\.delete\(path\)/, '403 bucket must clear');
});

test('R-F2876: the banner hides only when ALL buckets are empty', () => {
  assert.match(CODE, /_fetchFailures\.size === 0 && _authGated\.size === 0 && _forbidden\.size === 0/,
    'a forbidden panel must keep the banner visible, not be silently dropped');
});

test('R-F2876: a 403 is still NOT reported as "down"', () => {
  // The original R-F2390 property: auth-gated is reachable, not unreachable.
  // Splitting the buckets must not let 403 leak into the red DATA-UNAVAILABLE path.
  const probe = CODE.slice(CODE.indexOf('const res = await API.probe'),
                           CODE.indexOf('const res = await API.probe') + 700);
  assert.ok(!/_trackFetchFailure\(path[^)]*403/.test(probe),
    'a 403 must never be counted as a fetch failure');
});
