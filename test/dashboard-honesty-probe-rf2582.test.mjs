// test/dashboard-honesty-probe-rf2582.test.mjs
//
// Capability test for R-F2582 — the dashboard honesty monitor's invariant.
// Exercises the REAL evaluateHonesty() (imported from the monitor) across the
// masking scenarios it exists to catch.
//
// Run: node test/dashboard-honesty-probe-rf2582.test.mjs

import { evaluateHonesty } from '../scripts/monitor/dashboard_honesty_probe.mjs';

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

console.log('R-F2582 dashboard honesty monitor tests\n');

const OK_ENDPOINTS = [
  { path: '/api/aria/autonomous/status', tier: 'operator', status: 403 }, // auth-gated = correct
  { path: '/api/aria/health', tier: 'public', status: 200 },
];

// Healthy + web agrees + clean statuses → PASS
let r = evaluateHonesty({ brainHealthy: true, crossFlyOk: true, endpoints: OK_ENDPOINTS });
check('healthy brain + agreeing web + auth-gated operator route → PASS', r.ok && r.violations.length === 0);

// The core masking scenario: brain green but web says offline → violation
r = evaluateHonesty({ brainHealthy: true, crossFlyOk: false, endpoints: OK_ENDPOINTS });
check('brain GREEN but web /health/cross says offline → false_offline violation',
  !r.ok && r.violations.some(v => v.kind === 'false_offline'));

// Endpoint 5xx while healthy → violation (would render "offline")
r = evaluateHonesty({ brainHealthy: true, crossFlyOk: true, endpoints: [
  { path: '/api/aria/health', tier: 'public', status: 503 },
] });
check('endpoint 5xx while brain healthy → healthy_but_5xx violation',
  !r.ok && r.violations.some(v => v.kind === 'healthy_but_5xx'));

// Operator route returns a masked/opaque status (not 200/401/403) → violation
r = evaluateHonesty({ brainHealthy: true, crossFlyOk: true, endpoints: [
  { path: '/api/aria/autonomous/status', tier: 'operator', status: 503 },
] });
check('operator route → 503 (masked, not auth-gated) → operator_gate_not_auth_status violation',
  !r.ok && r.violations.some(v => v.kind === 'operator_gate_not_auth_status'));

// Operator route 403 (correctly auth-gated) is NOT a violation
r = evaluateHonesty({ brainHealthy: true, crossFlyOk: true, endpoints: [
  { path: '/api/aria/autonomy/surface', tier: 'operator', status: 403 },
] });
check('operator route → 403 (honest auth-gate) is NOT flagged', r.ok);

// Brain DOWN: the invariant is conditioned on health — do not cry-wolf when the
// brain is genuinely down (a 5xx then is honest, not masking).
r = evaluateHonesty({ brainHealthy: false, crossFlyOk: false, endpoints: [
  { path: '/api/aria/health', tier: 'public', status: 503 },
] });
check('brain DOWN + 5xx → NO violation (honest outage, not a lie)', r.ok);

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
