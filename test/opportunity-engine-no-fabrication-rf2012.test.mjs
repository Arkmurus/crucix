// test/opportunity-engine-no-fabrication-rf2012.test.mjs
//
// R-F2012 — de-fabricate the web-tier opportunity engine so the Opportunities
// page matches REAL data. The engine mixed real signals (ACLED conflict counts,
// news sources, intel signals, outcome-learned multiplier) with FABRICATED ones:
// an editorial score base (priority/lusophone/riskLevel constants), a
// strategicNeeds fallback shown as detected need, an "always show HIGH-priority
// markets" floor (non-existent opportunities), a fake complianceStatus "CLEAR"
// (no real screen ran), and a stratContext note fallback (hand-authored prose).
//
// Source-read assertions (the engine pulls live data on import).
// Run: node test/opportunity-engine-no-fabrication-rf2012.test.mjs

import { readFileSync } from 'node:fs';
import assert from 'node:assert';

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok - ${name}`); }
  catch (e) { failures++; console.error(`  FAIL - ${name}\n     ${e.message}`); }
}

const SRC = readFileSync(new URL('../lib/self/opportunity_engine.mjs', import.meta.url), 'utf8');

check('no editorial priority base in the score', () => {
  assert.ok(!/score \+= market\.priority === 'HIGH' \? 10/.test(SRC));
});
check('no editorial Lusophone score bonus', () => {
  assert.ok(!/if \(market\.lusophone\) score \+= 8/.test(SRC));
});
check('no editorial riskLevel score penalty', () => {
  assert.ok(!/score -= market\.riskLevel \* 3/.test(SRC));
});
check('no "always show HIGH-priority markets" floor', () => {
  assert.ok(!/market\.priority !== 'HIGH'\) continue/.test(SRC));
});
check('no strategicNeeds fallback (templated needs)', () => {
  assert.ok(!/if \(market\?\.strategicNeeds\)/.test(SRC));
});
check('no stratContext note fallback (hand-authored prose)', () => {
  assert.ok(!/market\.stratContext \|\|/.test(SRC));
});
check('compliance is honest (NOT_SCREENED, not fake CLEAR)', () => {
  assert.ok(!/'CLEAR' : 'REVIEW_REQUIRED'/.test(SRC));
  assert.ok(/'NOT_SCREENED' : 'REVIEW_REQUIRED'/.test(SRC));
});
check('REAL signal scoring is preserved', () => {
  assert.ok(/conflictData\.total \* 2/.test(SRC), 'conflict intensity kept');
  assert.ok(/procSources\.length \* 8/.test(SRC), 'real procurement-signal scoring kept');
  assert.ok(/blockers\.length \* 15/.test(SRC), 'real sanctions/blocker penalty kept');
});
check('the real-signal threshold gate remains', () => {
  assert.ok(/if \(score < 5\) continue/.test(SRC));
});
check('R-F2014: matchOEMs is fed REAL needs only (no fabricated fallback)', () => {
  assert.ok(!/market\.strategicNeeds \|\| \['small arms'/.test(SRC),
    'matchOEMs must not fall back to strategicNeeds/hardcoded needs');
  assert.ok(/const matchedOEMs = matchOEMs\(procurementNeeds\);/.test(SRC),
    'matchOEMs must take the real detected procurementNeeds directly');
});
check('R-F2015: defence-relevance filter gates signal counting', () => {
  assert.ok(/const DEFENCE_RELEVANCE_KW =/.test(SRC));
  assert.ok(/function _isDefenceRelevant/.test(SRC));
  assert.ok(/if \(!_isDefenceRelevant\(combined\)\) continue;/.test(SRC),
    'non-defence (sports/noise) items must be skipped before counting');
});
check('R-F2015: min-evidence gate excludes zero-signal markets', () => {
  assert.ok(/const hasRealSignal =/.test(SRC));
  assert.ok(/if \(!hasRealSignal\) continue;/.test(SRC),
    'a market with no conflict/signal/dev-finance/source must be excluded');
  // the gate must sit BEFORE correlationBoost so boosts cannot inflate zero-signal markets
  assert.ok(SRC.indexOf('if (!hasRealSignal) continue;') <
            SRC.indexOf('const correlationBoost'),
    'min-evidence gate must run before the correlation boost');
});

if (failures) { console.error(`\n${failures} test(s) FAILED`); process.exit(1); }
console.log('\nAll R-F2012 opportunity-engine no-fabrication tests passed.');
