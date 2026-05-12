// test/wa-rate-limit-bypass-rf390.test.mjs
//
// Capability test for R-F390 — internal-token bypass for the user-facing
// rate limiters. The WA listener calls /api/aria/chat from inside the same
// Node process; Express sees 127.0.0.1, which previously shared the IP
// bucket with every sweep-side cross-call from the same machine.
//
// Live evidence 2026-05-12 22:11:15 / 22:11:48 (logs):
//   [WA Listener] Streaming failed, falling back to /chat: ARIA 429:
//   [WA Listener] ARIA chat failed (both paths):
//                 ARIA fallback 429: Too many requests. Please wait 15 minutes.
//
// Two checks:
//   1. Smoke — 200 internal-token requests against /api/aria/chat through
//      the real applyRateLimiting wiring produce zero 429s. Without the
//      skip, TIERS.standard (150/15min) would 429 the last 50.
//   2. Source — confirm skip: _internalTokenBypass is wired into all five
//      user-facing rate limit definitions (TIERS.standard, ariaChat,
//      ariaThin, admin, speedLimiter). A forgotten skip wiring is exactly
//      how the bug was introduced in the first place.
//
// Run: node test/wa-rate-limit-bypass-rf390.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import express from 'express';
import { applyRateLimiting } from '../middleware/rateLimiter.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const RL_SRC = readFileSync(join(__dirname, '..', 'middleware', 'rateLimiter.mjs'), 'utf8');

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F390 capability tests\n');

// ── 1. Smoke: bypass lets 200 internal-token requests through ─────────
console.log('1. 200 requests with valid internal token → zero 429s');
process.env.ARIA_INTERNAL_TOKEN = 'rf390-' + Math.random().toString(36).slice(2);
const TOKEN = process.env.ARIA_INTERNAL_TOKEN;

const app = express();
app.use(express.json());
applyRateLimiting(app);
app.post('/api/aria/chat', (req, res) => res.json({ ok: true }));
const server = app.listen(0);
const url = `http://127.0.0.1:${server.address().port}/api/aria/chat`;

{
  const statuses = await Promise.all(Array.from({ length: 200 }, () =>
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
      body: JSON.stringify({ message: 'test' }),
    }).then(r => r.status),
  ));
  const capped = statuses.filter(s => s === 429).length;
  // Without R-F390 the global TIERS.standard cap (150/15min, IP-keyed)
  // would have 429'd at least 50 of these.
  check(`zero 429s out of 200 (got ${capped})`, capped === 0);
}

server.close();

// ── 2. Source: every user-facing limiter has skip wired ───────────────
//
// Defends against the original failure mode: the wiring was simply
// forgotten on one limiter, so internal traffic still hit the IP bucket.
// A grep-style assertion is more robust here than e2e testing each tier
// because express-rate-limit's behavior on localhost varies by version.
//
// Five wirings expected: TIERS.standard, ariaThin, ariaChat, admin, and
// the speedLimiter. We assert by occurrence count: exactly five
// `skip:\s+_internalTokenBypass` lines in the file.
console.log('\n2. All five user-facing limiters have skip: _internalTokenBypass');
const skipMatches = RL_SRC.match(/skip:\s+_internalTokenBypass/g) || [];
check(`exactly 5 skip wirings (got ${skipMatches.length})`, skipMatches.length === 5);

// Each named tier mentions the bypass within the next 8 lines after its
// declaration. Catches a wiring drift where someone refactors but the
// total count stays at 5.
const tiersBlock = RL_SRC.split('};')[0]; // TIERS object body
for (const name of ['standard', 'ariaThin', 'ariaChat', 'admin']) {
  const i = tiersBlock.indexOf(`${name}: {`);
  const slice = i >= 0 ? tiersBlock.slice(i, i + 600) : '';
  check(`${name} block contains _internalTokenBypass`,
    slice.includes('_internalTokenBypass'));
}
// speedLimiter is defined outside TIERS — check the full source.
const sdIdx = RL_SRC.indexOf('const speedLimiter');
const sdSlice = sdIdx >= 0 ? RL_SRC.slice(sdIdx, sdIdx + 400) : '';
check('speedLimiter block contains _internalTokenBypass',
  sdSlice.includes('_internalTokenBypass'));

// Helper itself is defined and reads ARIA_INTERNAL_TOKEN from env.
check(
  '_internalTokenBypass helper defined and reads ARIA_INTERNAL_TOKEN',
  /function\s+_internalTokenBypass\s*\([^)]*\)\s*\{[\s\S]*?ARIA_INTERNAL_TOKEN[\s\S]*?\}/.test(RL_SRC),
);

// Empty/unset token must fail closed — verify the early-return is present.
check(
  'helper fail-closes on empty ARIA_INTERNAL_TOKEN',
  /if\s*\(\s*!expected\s*\)\s*return\s+false/.test(RL_SRC),
);

console.log(`\n${failures === 0 ? '✓ ALL PASS' : `✗ ${failures} FAILURES`}`);
process.exitCode = failures === 0 ? 0 : 1;
