// test/proactive-hypotheses-rf773.test.mjs
//
// Capability test for R-F773 — proactive daily-engagement hypothesis surfacing.
//
// Two-part regression guard for the bug fixed in lib/aria/proactive.mjs:
//
//   1. Path: caller hit /api/aria/research/hypotheses; backend mounts at
//      /api/aria/hypotheses (routes/aria.py:8926, @router.get("/hypotheses")
//      with router prefix /api/aria). The mismatch returned 404, which was
//      swallowed by `.catch(() => null)` + outer try/catch + `|| []` fallback,
//      so daily engagement silently fell back to the rotating compliance
//      question and never surfaced an unvalidated hypothesis.
//
//   2. Status filter: caller filtered `h.status === 'pending' || 'unvalidated'`,
//      but backend hypothesis rows carry one of OPEN / STALE / VALIDATED /
//      REJECTED (see researcher.py:1859 + main.py:908 + routes/aria.py:10204).
//      Even if the path had been correct, the filter would never match — same
//      silent fall-through.
//
// Both branches of the bug were hidden by the error-tolerance triple. This
// test grep-pins both sides so a future copy-paste accident can't silently
// regress.
//
// Run: node test/proactive-hypotheses-rf773.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROACTIVE = readFileSync(
  join(__dirname, '..', 'lib', 'aria', 'proactive.mjs'),
  'utf8'
);
const ARIA_PY = readFileSync(
  join(__dirname, '..', 'aria_service', 'routes', 'aria.py'),
  'utf8'
);
const RESEARCHER_PY = readFileSync(
  join(__dirname, '..', 'aria_service', 'intel', 'researcher.py'),
  'utf8'
);

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F773 capability tests\n');

// ── 1. Caller targets the correct path ──────────────────────────────
check(
  'proactive.mjs calls /api/aria/hypotheses (the mounted path)',
  PROACTIVE.includes("brainGet('/api/aria/hypotheses')")
);
check(
  'proactive.mjs does NOT call /api/aria/research/hypotheses (404 regression)',
  !PROACTIVE.includes('/api/aria/research/hypotheses')
);

// ── 2. Backend mounts /hypotheses under the router prefix ───────────
check(
  'routes/aria.py mounts @router.get("/hypotheses")',
  /@router\.get\(\s*["']\/hypotheses["']\s*\)/.test(ARIA_PY)
);

// ── 3. Caller filter matches backend status casing ──────────────────
check(
  'proactive.mjs filters on h.status === \'OPEN\'',
  /h\.status\s*===\s*['"]OPEN['"]/.test(PROACTIVE)
);
check(
  'proactive.mjs does NOT filter on \'pending\' or \'unvalidated\' (always-empty regression)',
  !/h\.status\s*===\s*['"](pending|unvalidated)['"]/.test(PROACTIVE)
);

// ── 4. Backend creates hypotheses with status="OPEN" ────────────────
check(
  'researcher.py writes new hypotheses with status="OPEN"',
  /"status":\s*"OPEN"/.test(RESEARCHER_PY)
);

if (failures === 0) {
  console.log('\nAll R-F773 checks passed.');
  process.exit(0);
} else {
  console.error(`\n${failures} check(s) failed.`);
  process.exit(1);
}
