// test/aria-brain-badge-reset-rf465.test.mjs
//
// Capability test for R-F465 — aria-brain status badges reset on loader
// failure.
//
// Web/UI audit P0 (next_session_pickup_2026_05_15): the four status-bar
// badges on /aria-brain (health-badge, mode-badge, autonomy-badge,
// composite-badge) don't reset when their loader fails. After a successful
// tick followed by a failed tick, the badges keep yesterday's value while
// the R-F391 banner below says "ARIA service offline" — top of page
// contradicts the banner. Operator can't tell which is true.
//
// R-F465 introduces a resetBadge helper and stamps `DOWN` (badge-red)
// when fetchJson returns null in loadHealth / loadMode / loadEngine /
// loadComposite.
//
// Run: node test/aria-brain-badge-reset-rf465.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(
  join(__dirname, '..', 'public', 'aria-brain.html'),
  'utf8'
);

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F465 capability tests\n');

// ── 1. resetBadge helper is defined ──────────────────────────────────
console.log('1. resetBadge helper defined');
{
  check('resetBadge function present', HTML.includes('function resetBadge'));
  const fnIdx = HTML.indexOf('function resetBadge');
  const fn = HTML.slice(fnIdx, fnIdx + 400);
  check('sets badge-red class for DOWN state', fn.includes('badge-red'));
  check('writes textContent (label)',          fn.includes('textContent'));
  check('writes className',                    fn.includes('className'));
}

// ── 2. Each loader resets its badge on null/failure ─────────────────
console.log('\n2. badge reset wired into each loader');
{
  // loadHealth → health-badge
  const lhIdx = HTML.indexOf('async function loadHealth');
  const lh = HTML.slice(lhIdx, lhIdx + 800);
  check('loadHealth resets health-badge on null',
        /if\s*\(\s*!d\s*\)[\s\S]+?resetBadge\(['"]health-badge/.test(lh));

  // loadMode → mode-badge
  const lmIdx = HTML.indexOf('async function loadMode');
  const lm = HTML.slice(lmIdx, lmIdx + 800);
  check('loadMode resets mode-badge on null',
        /if\s*\(\s*!d\s*\)[\s\S]+?resetBadge\(['"]mode-badge/.test(lm));

  // loadEngine → autonomy-badge
  const leIdx = HTML.indexOf('async function loadEngine');
  const le = HTML.slice(leIdx, leIdx + 800);
  check('loadEngine resets autonomy-badge on null',
        /if\s*\(\s*!d\s*\)[\s\S]+?resetBadge\(['"]autonomy-badge/.test(le));

  // loadComposite → composite-badge
  const lcIdx = HTML.indexOf('async function loadComposite');
  const lc = HTML.slice(lcIdx, lcIdx + 800);
  check('loadComposite resets composite-badge on null/missing score',
        /(\!d\s*\|\|\s*\!d\.composite_score)[\s\S]+?resetBadge\(['"]composite-badge/.test(lc));
}

// ── 3. Reset labels are honest (not "OK" / "GREEN") ─────────────────
console.log('\n3. reset labels signal DOWN, not happy-path');
{
  const lhIdx = HTML.indexOf('async function loadHealth');
  const lh = HTML.slice(lhIdx, lhIdx + 800);
  check("health resets to 'DOWN'", lh.includes("resetBadge('health-badge', 'DOWN')"));

  const lmIdx = HTML.indexOf('async function loadMode');
  const lm = HTML.slice(lmIdx, lmIdx + 800);
  check("mode resets to 'MODE: DOWN'", lm.includes("resetBadge('mode-badge', 'MODE: DOWN')"));

  const leIdx = HTML.indexOf('async function loadEngine');
  const le = HTML.slice(leIdx, leIdx + 800);
  check("autonomy resets to 'AUTONOMY: DOWN'", le.includes("resetBadge('autonomy-badge', 'AUTONOMY: DOWN')"));

  const lcIdx = HTML.indexOf('async function loadComposite');
  const lc = HTML.slice(lcIdx, lcIdx + 800);
  check("composite resets to 'SCORE: DOWN'", lc.includes("resetBadge('composite-badge', 'SCORE: DOWN')"));
}

console.log(`\nR-F465 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
