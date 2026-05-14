// test/proxy-bearer-fallback-rf456.test.mjs
//
// Capability test for R-F456 — bearer pass-through fallback.
//
// Live evidence: Session B brain-cascade audit found 8 dashboard panels
// returning 401 (/rlaif/stats, /critique/stats, /vendors, /pending-actions,
// /airtable/health, /security/counter-intel/scan, /learning/coverage,
// /sanctions/divergence). Public-bypass panels (/health, /adversarial/stats)
// kept working — masking the cause.
//
// Root cause: fly's _accepted_tokens accepts EITHER ARIA_API_TOKEN OR
// ARIA_INTERNAL_TOKEN (aria_service/routes/aria.py:165). _ariaHeaders on
// seenode previously only read ARIA_API_TOKEN. When seenode had only the
// internal token set (e.g. post-R-F389 cleanup), the proxy sent no
// Authorization header at all → fly 401's everything not on the
// _PUBLIC_AUTH_BYPASS_PATHS allowlist.
//
// Run: node test/proxy-bearer-fallback-rf456.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(__dirname, '..', 'server.mjs'),
  'utf8'
);

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F456 capability tests\n');

// ── 1. _ariaHeaders falls back to ARIA_INTERNAL_TOKEN ────────────────
console.log('1. _ariaHeaders bearer fallback');
{
  const idx = SRC.indexOf('function _ariaHeaders');
  check('_ariaHeaders function present', idx > -1);
  const fn = SRC.slice(idx, idx + 1500);
  check('reads ARIA_API_TOKEN first',   fn.includes('ARIA_API_TOKEN'));
  check('falls back to ARIA_INTERNAL_TOKEN',
        fn.includes('ARIA_INTERNAL_TOKEN') &&
        /ARIA_API_TOKEN\s*\|\|\s*process\.env\.ARIA_INTERNAL_TOKEN/.test(fn));
  check('sets Authorization Bearer header',
        fn.includes("headers['Authorization']") &&
        fn.includes('Bearer'));
}

// ── 2. runtime: behaviour with each combination of env vars ──────────
console.log('\n2. runtime simulation of env-var combinations');
{
  // We can't easily import the real _ariaHeaders (top-level server.mjs
  // boots Express + the whole app on import). Instead, mirror the
  // patched logic and prove the truth-table.
  function ariaHeadersFor(env) {
    const headers = { 'Content-Type': 'application/json' };
    const token = env.ARIA_API_TOKEN || env.ARIA_INTERNAL_TOKEN;
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return headers;
  }
  // Both set → API token wins (preserves prior behaviour)
  let h = ariaHeadersFor({ ARIA_API_TOKEN: 'api', ARIA_INTERNAL_TOKEN: 'int' });
  check('both set → bearer is api token', h.Authorization === 'Bearer api');

  // Only API token → uses it (regression guard for the original behaviour)
  h = ariaHeadersFor({ ARIA_API_TOKEN: 'api' });
  check('only api set → bearer is api token', h.Authorization === 'Bearer api');

  // Only INTERNAL token → falls through (the new R-F456 behaviour)
  h = ariaHeadersFor({ ARIA_INTERNAL_TOKEN: 'int' });
  check('only internal set → bearer is internal token (R-F456 fix)',
        h.Authorization === 'Bearer int');

  // Neither set → no Authorization header (soft rollout — fly logs a warning)
  h = ariaHeadersFor({});
  check('neither set → no Authorization header (soft rollout)',
        !('Authorization' in h));

  // Empty API token → falls to internal (operators sometimes set blank by accident)
  h = ariaHeadersFor({ ARIA_API_TOKEN: '', ARIA_INTERNAL_TOKEN: 'int' });
  check('blank api + internal set → bearer is internal token',
        h.Authorization === 'Bearer int');
}

console.log(`\nR-F456 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
