// test/stream-selfheal-rf1748.test.mjs
//
// Capability test for R-F1748 — web chat UI self-heals on mid-stream
// connectivity drops instead of dead-ending on "[!stream cut: fetch failed]".
//
// Operator symptom 2026-06-20: "/screen modirum gespi brazil" returned
// "⚠️ [!stream cut: fetch failed]" because the query hit DURING the R-F1744
// deploy (brain restarting). The mid-stream `evt.type==='error'` branch only
// printed the scary marker — it did NOT trigger the R-F1697 reconnect that the
// outer catch already had. Now a connectivity-class error event flips to the
// reconnect banner + a one-shot auto-retry once the brain is back; app-level
// errors (Python 500 / 429) still show the marker (no silent retry of a real
// failure).
//
// This test BEHAVIOURALLY exercises the connectivity matcher (CONN_RE) by
// extracting it from the page, plus structural checks on the self-heal wiring.
//
// Run: node test/stream-selfheal-rf1748.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ARIA_HTML = readFileSync(join(__dirname, '..', 'public', 'aria.html'), 'utf8');

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F1748 self-heal capability tests\n');

// ── 1. BEHAVIOURAL: extract CONN_RE and test the actual decision ──────────
console.log('1. CONN_RE classifies connectivity vs app errors correctly');
{
  const m = ARIA_HTML.match(/var CONN_RE = (\/.*\/[a-z]*);/);
  check('CONN_RE literal found in page', !!m);
  if (m) {
    // Rebuild the regex from its source so we test the EXACT shipped pattern.
    const body = m[1].slice(1, m[1].lastIndexOf('/'));
    const flags = m[1].slice(m[1].lastIndexOf('/') + 1);
    const CONN_RE = new RegExp(body, flags);

    // Connectivity-class → MUST self-heal (match).
    for (const s of ['fetch failed', 'Failed to fetch', 'terminated',
                     'ECONNRESET', 'ENOTFOUND', 'Python 502: bad gateway',
                     'request timed out', 'NetworkError when attempting']) {
      check(`connectivity reason matches: "${s}"`, CONN_RE.test(s));
    }
    // App-level errors → MUST NOT auto-retry (no match).
    for (const s of ['Python 500: KeyError in build_report',
                     'Python 429: rate limit exceeded',
                     'Python 400: message required', '']) {
      check(`app/non-conn reason does NOT match: "${s}"`, !CONN_RE.test(s));
    }
  }
}

// ── 2. error-event branch routes connectivity drops to reconnect ──────────
console.log('\n2. mid-stream error branch self-heals on connectivity class');
{
  const eIdx = ARIA_HTML.indexOf("evt.type === 'error'");
  check('error branch present', eIdx > -1);
  const block = ARIA_HTML.slice(eIdx, eIdx + 3000);
  check('routes via ConnectionMonitor.isConnReason',
        block.includes('ConnectionMonitor.isConnReason'));
  check('triggers reconnect (ConnectionMonitor.lost) on conn error',
        /ConnectionMonitor\.lost\(\)/.test(block));
  check('still shows [!stream cut] marker for non-conn errors',
        block.includes('[!stream cut:'));
  check('sets the _streamConnError flag', block.includes('_streamConnError'));
}

// ── 3. one-shot auto-retry wired in finally + health-gated helper ─────────
console.log('\n3. one-shot auto-retry after reconnect');
{
  check('finally guards retry with _autoRetried (fires at most once)',
        /_streamConnError\s*&&\s*!fullText\s*&&\s*!opts\._autoRetried/.test(ARIA_HTML));
  check('_awaitHealthThenRetry helper defined',
        /function _awaitHealthThenRetry\s*\(/.test(ARIA_HTML));
  check('retry waits on /healthz before resending',
        /_awaitHealthThenRetry[\s\S]{0,400}\/healthz/.test(ARIA_HTML));
  check('isConnReason exported from ConnectionMonitor',
        /return\s*\{[^}]*isConnReason\s*:\s*isConnReason/.test(ARIA_HTML));
}

console.log(`\nR-F1748 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
