// test/export-control-promotion-rf3545.test.mjs
//
// R-F3545 — BIS export-control rules were fetched, rendered, and promoted nowhere.
//
// `pushPromotionsToBrain` pushed opportunities, OpenSanctions and CSL;
// `synthesized.exportControlActions` reached a dashboard widget and stopped. It is
// official primary evidence sitting one mapper away from Grade-A intelligence, and
// it is the one alarming class nothing else in ARIA covers: a designation lands in
// the sanctions diff, but "BIS rewrote drone export controls" appears in no lane.

import { _test, pushPromotionsToBrain } from '../apis/promotion_bridge.mjs';

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

console.log('R-F3545 — BIS export-control rules reach the graded feed\n');

const { _mapExportControlRule: map } = _test;

// Shape produced by apis/sources/_federal_register.mjs (verified against source).
const RULE = {
  source: 'Export Controls',
  title: '🚦 Streamlining Export Controls for Drone Exports',
  content: 'Rule · 2026-01-21 · https://www.federalregister.gov/documents/x',
  url: 'https://www.federalregister.gov/documents/x',
  documentNumber: '2026-01234',
  timestamp: Date.parse('2026-01-21'),
  priority: 'high',
};

const m = map(RULE);
check('a BIS rule maps to a finding', !!m);
check('the emoji prefix is stripped from the title', m.title === 'Streamlining Export Controls for Drone Exports',
  'got: ' + m.title);
check('typed as a compliance change, which is the decision it forces', m.signal_type === 'sanctions_change');
check('graded as official primary evidence', m.source_tier === 'tier_1a' && m.confidence === 'HIGH');
check('a recent rule is HIGH priority', m.priority === 'HIGH');
check('an older rule is not HIGH', map({ ...RULE, priority: 'normal' }).priority === 'MEDIUM');
check('carries a citable evidence URL', m.evidence_url === RULE.url);
check('names a specific artefact so the grader can see an entity',
  Array.isArray(m.entities.events) && m.entities.events[0] === '2026-01234',
  'the grader REJECTs a signal with no named entity');
check('extracts a country when the title names one',
  map({ ...RULE, title: '🚦 Enhanced Favorable Treatment for the United Arab Emirates' })
    .entities.countries.includes('United Arab Emirates'));
check('dedups on the document number, not the fetch time',
  m.ref === '2026-01234' && map(RULE).ref === m.ref);
check('states what actually changes for the reader',
  /licence|export/i.test(m.why_it_matters) && /licence position/i.test(m.recommended_action));

console.log('\nHonesty floor — an unlinkable rule is not publishable:');
check('a rule with no URL is dropped', map({ ...RULE, url: null }) === null,
  'the channel gate and the grader both require a real evidence URL');
check('a non-http URL is dropped', map({ ...RULE, url: 'javascript:alert(1)' }) === null);
check('a titleless rule is dropped', map({ ...RULE, title: '🚦   ' }) === null);

console.log('\nThe lane is actually wired into the push:');
const posted = [];
globalThis.fetch = async (url, opts) => {
  posted.push(JSON.parse(opts.body).source);
  return { ok: true, json: async () => ({ accepted: 1 }) };
};
process.env.ARIA_SERVICE_URL = 'http://brain.test';
const out = await pushPromotionsToBrain({
  opportunities: [], opensanctions: {}, csl: { recent: [] },
  exportControlActions: { updates: [RULE] },
});
check('bis_export_controls is one of the pushed sources', posted.includes('bis_export_controls'),
  'pushed: ' + JSON.stringify(posted));
check('the return value reports the new lane', out.exportControls === 1,
  'got: ' + JSON.stringify(out));
check('an empty synthesized object still reports the lane',
  pushPromotionsToBrain(null) instanceof Promise);

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
