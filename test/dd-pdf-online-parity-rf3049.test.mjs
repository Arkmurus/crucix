// R-F3049 / R-F3050 — the PDF and the online view must not disagree.
//
// OPERATOR REPORT (dd_f4a7635c6efa, SUPACAT LIMITED): "severe discrepancies from the
// downloaded report and the online version". Diffing the two renderings of that ONE
// report:
//
//   data                    online view                    downloaded PDF
//   ----------------------  -----------------------------  ---------------------
//   Directors / officers    6 NAMED                        absent
//   PSC / beneficial owners 3 NAMED                        absent
//   Ghost score             0/28 GREEN                     absent
//   Controllers             "Sc Group-Global Limited"      "Controlled By 1 item"
//   Country risk            "GREEN"                        "(present)"
//   Export control          "civilian or unclassified"     "(present)"
//   Press coverage          tier split + 8 cited URLs      "8 items"
//   Grounded rate           "30%"                          "0.3"
//
// Two causes: (1) the R-F3026 people block was committed but aria-web was never
// deployed, and (2) the PDF flattened every nested object to "(present)" and every
// list to a COUNT, while the online view pulls the meaningful scalar out of each.
// A count is not a finding and "(present)" tells a reader nothing — and the PDF is
// the artefact a client actually files.
import test from 'node:test';
import assert from 'node:assert/strict';
import { ddReportSections } from '../lib/reports/pdf_generator.mjs';

// Shaped exactly like the live dd_f4a7635c6efa payload.
const REPORT = {
  identity: {
    meta: { status: 'ok' },
    registration_status: 'active',
    incorporation_date: '1980-08-26',
    registered_address: 'The Airfield, Dunkeswell, Devon, EX14 4LF',
    declared_activity: '30400, 30990, 33170, 71129',
    ghost_score: { total: 0, max_total: 28, classification: 'GREEN' },
    directors: [
      { name: 'MITCHELL, Alan Shaun', officer_role: 'secretary', appointed_on: '2004-01-08' },
      { name: 'AMES, Roger Simon Nicholas', officer_role: 'director', appointed_on: '2003-09-01' },
    ],
    shareholders: [
      { name: 'Miss Elizabeth Mary Jones', kind: 'individual-person-with-significant-control',
        natures_of_control: ['significant-influence-or-control'] },
      { name: 'Sc Group-Global Limited', kind: 'corporate-entity-person-with-significant-control',
        natures_of_control: ['ownership-of-shares-75-to-100-percent'],
        identification: { registration_number: '08020542' } },
    ],
  },
  compliance: {
    meta: { status: 'ok' },
    country_risk: { headline_risk: 'GREEN', risk_level: 'low' },
    export_control: { recommendation: 'civilian or unclassified', confidence: 0.4 },
    financial_health: { health_verdict: 'STRONG', data_available: true },
  },
  network: {
    meta: { status: 'ok' },
    controlled_by: [{ controller_name: 'Sc Group-Global Limited',
                      controller_registration_number: '08020542' }],
    ubo_chain: [{ name: 'SUPACAT LIMITED' }, { name: 'Sc Group-Global Limited' }],
  },
  verification: { meta: { status: 'ok' }, grounded_rate: 0.3, unverified_claim_count: 7 },
  digital: {
    meta: { status: 'error', error: 'timeout after 180s' },
    press_coverage: [
      { url: 'https://www.supacat.com/news/a', source: 'supacat.com', source_tier: 'ENTITY_SITE' },
      { url: 'https://www.ft.com/x', source: 'ft.com', source_tier: 'T1' },
    ],
    source_tier_breakdown: { T1: 1, UNVERIFIED: 2, ENTITY_SITE: 5 },
  },
};

function factsOf(sec) {
  return Object.fromEntries(sec.facts.map(([k, v]) => [k, v]));
}
function sectionOf(secs, re) {
  const s = secs.find((x) => re.test(x.title));
  assert.ok(s, `section ${re} must exist`);
  return s;
}

test('R-F3049: nested layer objects render their VALUE, never "(present)"', () => {
  const secs = ddReportSections(REPORT);
  const blob = JSON.stringify(secs);
  assert.ok(!/\(present\)/.test(blob), '"(present)" must never reach the page');
  const compliance = sectionOf(secs, /compliance/i);
  const f = factsOf(compliance);
  assert.equal(f.country_risk, 'GREEN');
  assert.equal(f.export_control, 'civilian or unclassified');
  assert.equal(f.financial_health, 'STRONG', 'the online view shows this; so must the PDF');
});

test('R-F3049: controllers and UBO nodes are NAMED, not counted', () => {
  const secs = ddReportSections(REPORT);
  const f = factsOf(sectionOf(secs, /ownership|network/i));
  assert.equal(f.controlled_by, 'Sc Group-Global Limited',
    'the online view names the controller; "1 item" is not a finding');
  assert.match(String(f.ubo_chain), /SUPACAT LIMITED/);
  assert.ok(!/^\d+ items?$/.test(String(f.controlled_by)));
});

test('R-F3049: ghost score appears, in the same form the online view uses', () => {
  const f = factsOf(sectionOf(ddReportSections(REPORT), /identity/i));
  assert.equal(f.ghost_score, '0/28 GREEN');
});

test('R-F3049: grounded rate is a percentage, matching the online view', () => {
  const f = factsOf(sectionOf(ddReportSections(REPORT), /verification/i));
  assert.equal(f.grounded_rate, '30%');
});

test('R-F3049: the source-tier breakdown is a breakdown, not one number', () => {
  const f = factsOf(sectionOf(ddReportSections(REPORT), /digital/i));
  assert.match(String(f.source_tier_breakdown), /T1:1/);
  assert.match(String(f.source_tier_breakdown), /UNVERIFIED:2/);
  assert.match(String(f.source_tier_breakdown), /ENTITY_SITE:5/);
});

test('R-F3049: cited press URLs reach the PDF', () => {
  const digital = sectionOf(ddReportSections(REPORT), /digital/i);
  assert.equal(digital.evidence.length, 2);
  const urls = digital.evidence.map((e) => e.url);
  assert.ok(urls.includes('https://www.ft.com/x'));
  assert.equal(digital.evidence.find((e) => e.tier === 'T1').source, 'ft.com');
});

test('R-F3026 parity: the people the online view names are in the PDF too', () => {
  const identity = sectionOf(ddReportSections(REPORT), /identity/i);
  const blob = JSON.stringify(identity.people);
  assert.match(blob, /MITCHELL, Alan Shaun/);
  assert.match(blob, /AMES, Roger Simon Nicholas/);
  assert.match(blob, /Miss Elizabeth Mary Jones/);
  assert.match(blob, /Sc Group-Global Limited/);
});

test('R-F3049: an errored layer still carries its error (no silent clean)', () => {
  const digital = sectionOf(ddReportSections(REPORT), /digital/i);
  assert.equal(digital.status, 'error');
  assert.match(digital.error, /timeout after 180s/);
});

test('R-F3049: nothing is invented for an empty report', () => {
  const secs = ddReportSections({ identity: { meta: { status: 'ok' } } });
  const blob = JSON.stringify(secs);
  assert.ok(!/GREEN|STRONG|Sc Group/.test(blob), 'no value may appear without data');
});
