// test/bd-honest-leads-mapping-rf2011.test.mjs
//
// R-F2011 (completes ARIA's R-F2009) — the web-tier _mergeBrainLeads must map the
// brain's HONEST lead shape {market, signal_summary, signal_count} and must NOT
// reference the removed fabricated fields (win_probability, buyer, window, angle/
// oemRecommendation, first_action, compliance_flags). Source-read assertion (the
// web tier auto-connects on import, so we verify the shipped source).
//
// Run: node test/bd-honest-leads-mapping-rf2011.test.mjs

import { readFileSync } from 'node:fs';
import assert from 'node:assert';

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok - ${name}`); }
  catch (e) { failures++; console.error(`  FAIL - ${name}\n     ${e.message}`); }
}

const SRC = readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
// isolate the _mergeBrainLeads function body
const start = SRC.indexOf('async function _mergeBrainLeads');
assert.ok(start > 0, '_mergeBrainLeads must exist');
const body = SRC.slice(start, start + 1600);

check('maps the honest signal fields', () => {
  assert.ok(/signalSummary:\s*l\.signal_summary/.test(body));
  assert.ok(/signalCount:\s*l\.signal_count/.test(body));
});

check('no longer references the fabricated brain fields', () => {
  for (const f of ['win_probability', 'l.buyer', 'l.window', 'l.angle',
                   'l.first_action', 'l.compliance_flags']) {
    assert.ok(!body.includes(f), `_mergeBrainLeads must not reference ${f}`);
  }
});

check('urgency/type are neutral signal labels, not fabricated HOT/WARM tiers', () => {
  assert.ok(/urgency:\s*'SIGNAL'/.test(body));
  assert.ok(/type:\s*'INTEL'/.test(body));
});

if (failures) { console.error(`\n${failures} test(s) FAILED`); process.exit(1); }
console.log('\nAll R-F2011 honest-leads-mapping tests passed.');
