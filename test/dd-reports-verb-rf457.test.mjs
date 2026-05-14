// test/dd-reports-verb-rf457.test.mjs
//
// Capability test for R-F457 — TBML / Benford HTTP-verb regression guard.
//
// Session B audit flagged "Currently render 405 Method Not Allowed; both
// endpoints accept POST only" for /api/aria/tbml/classify and
// /api/aria/forensic/benford. Live source check (2026-05-14): both panels
// in dd-reports.html already configured with `method: 'POST'`. The Session
// B note was either stale or referenced a panel that was already fixed.
//
// Rather than spend a slot on a no-op patch, this test pins the correct
// verb at both call sites so a future copy-paste accident can't silently
// regress to GET (which would 405 because fly-side both endpoints are
// @router.post only).
//
// Run: node test/dd-reports-verb-rf457.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(
  join(__dirname, '..', 'public', 'dd-reports.html'),
  'utf8'
);
const ARIA_PY = readFileSync(
  join(__dirname, '..', 'aria_service', 'routes', 'aria.py'),
  'utf8'
);

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F457 capability tests\n');

// ── 1. dd-reports.html uses POST for tbml/classify ─────────────────
console.log('1. /api/aria/tbml/classify panel uses POST');
{
  const idx = HTML.indexOf('/api/aria/tbml/classify');
  check('tbml/classify endpoint referenced', idx > -1);
  const block = HTML.slice(idx, idx + 400);
  check("method is 'POST'", /method:\s*['"]POST['"]/i.test(block));
  check('no GET on this panel', !/method:\s*['"]GET['"]/i.test(block));
}

// ── 2. dd-reports.html uses POST for forensic/benford ──────────────
console.log('\n2. /api/aria/forensic/benford panel uses POST');
{
  const idx = HTML.indexOf('/api/aria/forensic/benford');
  check('forensic/benford endpoint referenced', idx > -1);
  const block = HTML.slice(idx, idx + 400);
  check("method is 'POST'", /method:\s*['"]POST['"]/i.test(block));
  check('no GET on this panel', !/method:\s*['"]GET['"]/i.test(block));
}

// ── 3. fly-side endpoints are POST-only (asserts the contract) ─────
console.log('\n3. fly endpoints expose POST verb');
{
  check('/tbml/classify is @router.post',
        /@router\.post\(\s*['"]\/tbml\/classify['"]/.test(ARIA_PY));
  check('/forensic/benford is @router.post',
        /@router\.post\(\s*['"]\/forensic\/benford['"]/.test(ARIA_PY));
}

console.log(`\nR-F457 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
