// scripts/monitor/dashboard_honesty_probe.mjs
//
// R-F2582 — synthetic DASHBOARD HONESTY monitor (§25 proprioception for the web tier).
//
// The recurring "DATA UNAVAILABLE" class: the dashboard reports panels as OFFLINE
// when the brain (aria-intel) is actually healthy — because a proxy masks a real
// status (auth-gated 403, or a transient) as a generic 503. R-F2579 fixed the
// relay and R-F2581 CI-guards it. This monitor is the RUNTIME layer: it continuously
// asserts the honesty invariant against the LIVE system and alerts on violation, so
// a recurrence (or aria-intel returning healthy-but-5xx) is caught by the system,
// not discovered by the operator.
//
// The invariant (evaluateHonesty, pure + testable):
//   brain healthy  ⟹  (a) the web tier's /api/health/cross AGREES the brain is up,
//                  AND  (b) no dashboard-feeding endpoint returns a 5xx (which would
//                       render as "offline"). Operator-gated endpoints returning 401/403
//                       are CORRECT (auth-gated, not a violation).
//
// Feasible without a browser JWT: it probes aria-intel directly (internal token) for
// ground truth + the web tier's UNAUTH /health/cross, so it needs no user session.
//
// Run:  node scripts/monitor/dashboard_honesty_probe.mjs
// Env:  ARIA_SERVICE_URL (aria-intel base), ARIA_WEB_URL (aria-web base),
//       ARIA_INTERNAL_TOKEN (for direct aria-intel probes),
//       ARIA_HONESTY_SIGNAL=1 to POST a brain signal on violation.

const INTEL = (process.env.ARIA_SERVICE_URL || 'https://aria-intel.fly.dev').replace(/\/$/, '');
const WEB   = (process.env.ARIA_WEB_URL || 'https://aria-web.fly.dev').replace(/\/$/, '');
const TOKEN = (process.env.ARIA_INTERNAL_TOKEN || process.env.ARIA_API_TOKEN || '').trim();

// Dashboard-feeding endpoints on aria-intel + their expected auth tier.
// operator: gated by _OPERATOR_ONLY_RE (403 with a non-operator token = CORRECT).
// public:   should return 200 with the internal/service token.
const ENDPOINTS = [
  { path: '/api/aria/autonomous/status', tier: 'operator' },
  { path: '/api/aria/autonomy/surface', tier: 'operator' },
  { path: '/api/aria/health', tier: 'public' },
  { path: '/api/aria/intel/signals/recent?limit=1', tier: 'public' },
];

// ── PURE invariant evaluation (testable; no I/O) ─────────────────────────────
export function evaluateHonesty({ brainHealthy, crossFlyOk, endpoints }) {
  const violations = [];
  if (brainHealthy && crossFlyOk === false) {
    violations.push({
      kind: 'false_offline',
      detail: 'brain /health/live is GREEN but web /api/health/cross reports the brain unreachable (fly.ok=false) — the web tier would render panels as OFFLINE while the brain is up.',
    });
  }
  for (const e of (endpoints || [])) {
    if (brainHealthy && typeof e.status === 'number' && e.status >= 500) {
      violations.push({
        kind: 'healthy_but_5xx',
        detail: `${e.path} returned ${e.status} while the brain is healthy — a 5xx renders as "offline"/"DATA UNAVAILABLE" on the dashboard.`,
      });
    }
    // An operator-gated endpoint MUST resolve to an auth status (401/403) or 200,
    // never a masked/opaque error, so the dashboard can render "auth-gated".
    if (e.tier === 'operator' && typeof e.status === 'number'
        && ![200, 401, 403].includes(e.status)) {
      violations.push({
        kind: 'operator_gate_not_auth_status',
        detail: `${e.path} (operator-only) returned ${e.status} — expected 200/401/403 so the dashboard renders "auth-gated", not offline.`,
      });
    }
  }
  return { ok: violations.length === 0, violations };
}

// ── live probing ─────────────────────────────────────────────────────────────
async function probe(url, { headers = {}, timeoutMs = 8000 } = {}) {
  try {
    const r = await fetch(url, { headers, signal: AbortSignal.timeout(timeoutMs) });
    let body = '';
    try { body = await r.text(); } catch { /* ignore */ }
    return { status: r.status, body };
  } catch (e) {
    return { status: 0, error: e && e.message ? e.message : String(e) };
  }
}

async function main() {
  // 1. Ground truth: is the brain healthy?
  const live = await probe(`${INTEL}/health/live`);
  const brainHealthy = live.status === 200;

  // 2. Does the web tier AGREE the brain is up?
  const cross = await probe(`${WEB}/api/health/cross`);
  let crossFlyOk = null;
  try { crossFlyOk = JSON.parse(cross.body || '{}')?.fly?.ok ?? null; } catch { crossFlyOk = null; }

  // 3. True status of dashboard-feeding endpoints (direct aria-intel, internal token).
  const authHeader = TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {};
  const endpoints = [];
  for (const e of ENDPOINTS) {
    const r = await probe(`${INTEL}${e.path}`, { headers: authHeader });
    endpoints.push({ path: e.path, tier: e.tier, status: r.status });
  }

  const result = evaluateHonesty({ brainHealthy, crossFlyOk, endpoints });

  console.log(`[honesty-probe] brain=${brainHealthy ? 'GREEN' : 'DOWN(' + live.status + ')'} cross.fly.ok=${crossFlyOk}`);
  for (const e of endpoints) console.log(`  ${e.path} → ${e.status} (${e.tier})`);
  if (result.ok) {
    console.log('PASS — dashboard honesty invariant holds.');
  } else {
    console.error(`FAIL — ${result.violations.length} honesty violation(s):`);
    for (const v of result.violations) console.error(`  [${v.kind}] ${v.detail}`);
    // §25/§21 — wire the violation to the brain so it is not dark (best-effort).
    if (process.env.ARIA_HONESTY_SIGNAL === '1' && TOKEN) {
      await probe(`${INTEL}/api/aria/brain/signal`, {
        headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
      }).catch(() => {});
    }
  }
  process.exit(result.ok ? 0 : 1);
}

// Run only when invoked directly (so tests can import evaluateHonesty cleanly).
if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith('dashboard_honesty_probe.mjs')) {
  main();
}
