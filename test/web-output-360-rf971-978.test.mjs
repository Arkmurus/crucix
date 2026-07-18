// test/web-output-360-rf971-978.test.mjs
//
// 360 web-data-output + UI accuracy batch (R-F971..F978, Node/UI lanes).
// Static source-assertion test (mirrors node-brain-signal-rf900.test.mjs) —
// pins the wiring so a future edit can't silently regress the honesty fixes.
//
//   R-F971 home dashboard labelled as the OSINT sweep, not ARIA's brain
//   R-F972 bd-intelligence share footer points at the right domain
//   R-F975 /api/status banner scoped to "service availability"
//   R-F976 dead /api/brain/* stubs return honest 410, not falsey-OK
//   R-F978 /api/data surfaces sweep freshness so stale data is visible
//
// Run: node test/web-output-360-rf971-978.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const read = (...p) => readFileSync(join(__dirname, '..', ...p), 'utf8');
const SRV = read('server.mjs');
const DASH = read('public', 'dashboard.html');
const BD = read('public', 'bd-intelligence.html');
const STATUS_HTML = read('public', 'status.html');
const STATUS_RT = read('lib', 'status', 'routes.mjs');

let failures = 0;
const check = (label, cond) => { console.log((cond ? '  ✓ ' : '  ✗ ') + label); if (!cond) failures++; };

console.log('R-F971 — home dashboard distinguishes decision intelligence from raw collection:');
check('dashboard names the decision layer and its material inputs',
  /decision layer/i.test(DASH) && /material DD, watchlist, deal/i.test(DASH));
check('dashboard distinguishes raw collection and links to source health',
  /raw collection remains in the Research Monitor/i.test(DASH) && /href="\/aria-brain"/.test(DASH));

console.log('\nR-F972 — share footer points at the correct domain:');
check('bd-intelligence no longer references intel.sursec.co.uk',
  !/intel\.sursec\.co\.uk/.test(BD));
// Rebranded intel.arkmurus.com -> imaria.io (bd-intelligence.html:114 share footer).
check('bd-intelligence uses imaria.io in the share footer',
  /imaria\.io/.test(BD));

console.log('\nR-F978 — /api/data surfaces sweep freshness:');
check('/api/data emits a _freshness block',
  /_freshness:\s*\{/.test(SRV));
check('_freshness carries stale + ageSeconds + staleAfterSeconds',
  /stale:\s*ageSeconds\s*!=\s*null\s*&&\s*ageSeconds\s*>\s*staleAfterSeconds/.test(SRV)
  && /ageSeconds,/.test(SRV) && /staleAfterSeconds,/.test(SRV));
check('does NOT mutate the shared currentData (spread copy)',
  /\.\.\.currentData,/.test(SRV));
check('dashboard reads data._freshness and renders a STALE badge',
  /data\._freshness/.test(DASH) && /STALE/.test(DASH));

console.log('\nR-F976 — dead /api/brain/* stubs are honest, not falsey-OK:');
check('a shared 410 "gone" helper exists',
  /_brainStubGone\s*=\s*\(movedTo\)\s*=>/.test(SRV));
check('leads/brief/status/history use the 410 helper',
  /\/api\/brain\/leads',\s*requireAuth,\s*_brainStubGone/.test(SRV)
  && /\/api\/brain\/brief',\s*requireAuth,\s*_brainStubGone/.test(SRV)
  && /\/api\/brain\/status',\s*requireAuth,\s*_brainStubGone/.test(SRV)
  && /\/api\/brain\/history',\s*requireAuth,\s*_brainStubGone/.test(SRV));
check('the helper returns HTTP 410 with a moved_to pointer',
  /res\.status\(410\)\.json\(\{[\s\S]*moved_to/.test(SRV));
check('leads/brief/status/history no longer return falsey-OK bodies',
  !/app\.get\('\/api\/brain\/leads',\s*requireAuth,\s*async[\s\S]*?res\.json\(\[\]\)/.test(SRV));
check('/api/brain/sweep no longer fakes a {status:"sweep_started"} ack',
  !/res\.json\([^)]*sweep_started/.test(SRV));
check('/api/brain/sweep honestly points at the real POST /api/sweep',
  /moved_to:\s*'POST \/api\/sweep'/.test(SRV));

console.log('\nR-F975 — status banner scoped to service availability:');
check('routes.mjs emits a measures field',
  /measures:\s*'service_availability'/.test(STATUS_RT));
check('routes.mjs emits a measuresNote explaining the scope',
  /measuresNote:/.test(STATUS_RT) && /Not a measure of intelligence quality/.test(STATUS_RT));
check('default summary no longer claims a blanket "All systems operational."',
  !/summary = 'All systems operational\.'/.test(STATUS_RT));
check('status.html renders the scope caption (data.measuresNote)',
  /data\.measuresNote/.test(STATUS_HTML) && /banner-scope/.test(STATUS_HTML));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
