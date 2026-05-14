// test/proxy-timeout-rf455.test.mjs
//
// Capability test for R-F455 — seenode→fly proxy timeout headroom.
//
// Session B brain-cascade diagnosis (next_session_pickup_2026_05_15):
//   - sentence-transformer batches at 12-35s under email-reader bursts
//   - full /health on fly takes 5-15s legitimately
//   - 30s default proxy timeout aborted slow-but-healthy panel fetches
//   - dashboard banner reported "ARIA service offline" for any panel
//     whose fly side ran longer than 30s
//
// R-F455 makes the default tunable via ARIA_PROXY_TIMEOUT_MS and bumps
// the floor to 45000ms so legitimate slow ticks no longer trip the
// dashboard's loud-fail banner. Explicit per-route timeouts override
// (the existing 300s for /adversarial/run_weekly still wins).
//
// Run: node test/proxy-timeout-rf455.test.mjs

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

console.log('R-F455 capability tests\n');

// ── 1. ariaProxy default reads ARIA_PROXY_TIMEOUT_MS ─────────────────
console.log('1. ariaProxy default timeout is env-var tunable');
{
  const idx = SRC.indexOf('async function ariaProxy');
  check('ariaProxy function present', idx > -1);
  const fn = SRC.slice(idx, idx + 3000);
  check('reads ARIA_PROXY_TIMEOUT_MS env var',
        fn.includes('ARIA_PROXY_TIMEOUT_MS'));
  check('default floor is 45000ms (not 30000)',
        fn.includes("'45000'") && !fn.includes('30000'));
  check('per-route timeoutMs still overrides',
        fn.includes("typeof timeoutMs === 'number'") &&
        fn.includes('timeoutMs > 0'));
}

// ── 2. Explicit per-route long timeouts still in place (regression) ──
console.log('\n2. existing per-route timeouts intact (regression guard)');
{
  // R-F57 — adversarial/run_weekly is 5-min
  check('/adversarial/run_weekly keeps 300000ms timeoutMs',
        SRC.includes('timeoutMs: 300000'));
}

// ── 3. Catch-all proxy uses ariaProxy (gets the new default) ─────────
console.log('\n3. catch-all proxy inherits the bumped default');
{
  // Anchor on the R-F110 catch-all comment (which precedes the real
  // handler) — there are multiple app.use('/api/aria', ...) mounts
  // earlier in the file for express.json, so we need the right one.
  const idx = SRC.indexOf('catch-all /api/aria/* proxy');
  check('catch-all mount comment present', idx > -1);
  const block = SRC.slice(idx, idx + 3000);
  check('catch-all calls ariaProxy',  block.includes('ariaProxy('));
  check('catch-all sets method but no timeoutMs override',
        block.includes('method: req.method') && !/timeoutMs:\s*\d/.test(block));
}

console.log(`\nR-F455 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
