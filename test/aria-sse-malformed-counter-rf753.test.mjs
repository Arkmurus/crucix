// test/aria-sse-malformed-counter-rf753.test.mjs
//
// R-F753 — public/aria.html no longer silently drops malformed SSE
// events from /api/aria/chat/stream. Pre-R-F753 the parser had a
// bare `catch(e) { /* skip malformed SSE */ }` which made backend
// regressions emitting bad JSON invisible. R-F753 adds: a session
// counter, a per-stream counter, console.warn with the raw event
// head, and a "degraded stream" badge once the per-stream count ≥5.
//
// Source-level test (no DOM env required) — verifies the four
// contracts are present so a future edit cannot accidentally revert
// to the silent catch.
//
// Run: node test/aria-sse-malformed-counter-rf753.test.mjs

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');

let failures = 0;
function check(label, cond, detail) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? ' — ' + detail : ''}`); failures += 1; }
}

console.log('R-F753 capability tests — SSE malformed counter\n');

const html = readFileSync(resolve(ROOT, 'public/aria.html'), 'utf8');

// 1. The silent catch must be gone.
check(
  'silent `catch(e) { /* skip malformed SSE */ }` removed',
  !/catch\(e\)\s*\{\s*\/\*\s*skip malformed SSE\s*\*\/\s*\}/.test(html),
  'R-F753 regression: the pre-fix silent catch came back'
);

// 2. The new catch surfaces via _ariaSseMalformedTotal counter.
check(
  'session-level counter _ariaSseMalformedTotal exists',
  /_ariaSseMalformedTotal\s*=\s*\(\s*window\._ariaSseMalformedTotal\s*\|\|\s*0\s*\)\s*\+\s*1/.test(html),
  'must increment a window-scoped session total'
);

// 3. Per-stream counter resets at stream start AND increments inside the catch.
check(
  'per-stream counter increments on each drop',
  /_ariaSseMalformedThisStream\s*=\s*\(\s*window\._ariaSseMalformedThisStream\s*\|\|\s*0\s*\)\s*\+\s*1/.test(html),
  'must increment _ariaSseMalformedThisStream inside the catch'
);
check(
  'per-stream counter resets at stream start',
  /_ariaSseMalformedThisStream\s*=\s*0/.test(html),
  'must reset the per-stream counter before reader.read() loop'
);

// 4. Diagnostic console.warn with R-F753 marker and a raw-event head.
check(
  'console.warn carries R-F753 marker',
  /console\.warn\(\s*\n?\s*'\[R-F753\] SSE event drop/.test(html),
  'browser console must see the drop with a discoverable R-tag'
);
check(
  'console.warn includes raw event head',
  /\(raw\s*\|\|\s*''\)\.slice\(0,\s*200\)/.test(html),
  'browser console must surface the raw head (truncated) for triage'
);

// 5. Degraded-stream badge appears once per-stream ≥5.
check(
  'degraded-stream badge threshold at 5',
  /_ariaSseMalformedThisStream\s*>=\s*5/.test(html),
  'cliff threshold must be 5 (kept aligned with R-F467 stream-cut marker UX)'
);
check(
  'degraded-stream badge sets dataset.degradedShown',
  /streamBadge\.dataset\.degradedShown\s*=\s*'1'/.test(html),
  'badge must mark itself shown so it doesn\'t flicker on every subsequent drop'
);
check(
  'degraded-stream badge surfaces count text',
  /degraded stream — '\s*\+\s*window\._ariaSseMalformedThisStream/.test(html),
  'badge copy must include the live count'
);

console.log(`\n${failures === 0 ? '✓ All R-F753 capability checks passed' : `✗ ${failures} check(s) failed`}`);
process.exit(failures === 0 ? 0 : 1);
