// test/dashboard-banner-rf466.test.mjs
//
// Capability test for R-F466 — dashboard.html fetch-failure banner.
//
// Web/UI audit P0 (next_session_pickup_2026_05_15): dashboard.html is
// the second-highest-traffic page (after /aria-brain) and pre-R-F466
// had NO loud-fail UX. When /api/data failed, panels showed loading
// spinners forever; when /api/health failed during sweep polling,
// the spinner just stuck. With R-F464 turning silent error envelopes
// into honest null, dashboard.html now also needs a banner to expose
// those failures.
//
// Mirrors the R-F391 pattern from /aria-brain.html.
//
// Run: node test/dashboard-banner-rf466.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(
  join(__dirname, '..', 'public', 'dashboard.html'),
  'utf8'
);

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F466 capability tests\n');

// ── 1. #fetch-failure-banner element present ─────────────────────────
console.log('1. banner DOM element');
{
  check('#fetch-failure-banner div exists',
        HTML.includes('id="fetch-failure-banner"'));
  check('starts hidden (display:none)',
        /id="fetch-failure-banner"[\s\S]{0,400}display:none/.test(HTML));
  check('role="alert" for screen readers',
        /id="fetch-failure-banner"[\s\S]{0,200}role="alert"/.test(HTML));
}

// ── 2. tracker functions defined ─────────────────────────────────────
console.log('\n2. tracker functions');
{
  check('_fetchFailures Map declared',     HTML.includes('_fetchFailures = new Map()'));
  check('_trackFetchFailure defined',      HTML.includes('function _trackFetchFailure'));
  check('_trackFetchSuccess defined',      HTML.includes('function _trackFetchSuccess'));
  check('_renderFetchFailureBanner defined', HTML.includes('function _renderFetchFailureBanner'));
}

// ── 3. loadData wires success/failure into the tracker ──────────────
console.log('\n3. loadData → tracker');
{
  const idx = HTML.indexOf('async function loadData()');
  check('loadData defined', idx > -1);
  const fn = HTML.slice(idx, idx + 600);
  check('loadData calls _trackFetchFailure on null',
        /if\s*\(\s*!data\s*\)[\s\S]+?_trackFetchFailure\(['"]\/api\/data['"]/.test(fn));
  check('loadData calls _trackFetchSuccess on data',
        fn.includes("_trackFetchSuccess('/api/data')"));
}

// ── 4. Banner copy is honest about stale-data risk ───────────────────
console.log('\n4. honest copy');
{
  const idx = HTML.indexOf('function _renderFetchFailureBanner');
  const fn = HTML.slice(idx, idx + 1500);
  check('says "DATA UNAVAILABLE"',           fn.includes('DATA UNAVAILABLE'));
  check('warns about stale/fallback values', /stale|fallback/.test(fn));
  check('names plausible causes',            /timeout|auth|overload/i.test(fn));
}

console.log(`\nR-F466 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
