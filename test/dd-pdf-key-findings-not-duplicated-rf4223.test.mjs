// R-F4223 / C-203 — the DD PDF printed every key finding TWICE.
//
// Found by reading a delivered report: ARIA_DD_Penfold_Savings_Limited_dd_9b3bc17a15f4.pdf.
// Nine findings — GLEIF LEI, the sanctions screen, charges, insolvency, the
// disqualified-directors check, The Gazette, employment tribunals, the FCA
// register and the shell screen — appear in full on page 2 under KEY FINDINGS
// and again, verbatim, on page 3. Verified by extraction: each distinctive line
// occurs exactly twice, on pages 2 and 3.
//
// THE CONTRACT WAS ALREADY WRITTEN, on the Python side that produces the data.
// `_rollup_key_findings` (dd_orchestrator.py) says:
//
//     "NOTHING IS HIDDEN. This re-orders a 10-item view of a list that stays
//      complete in its own section; a deferred finding is still rendered under
//      `network`."
//
// So `synthesis.key_findings` is a SUMMARY VIEW of findings that also render in
// their own layer. `ddReportSections` pushed the view as a section and then
// pushed every layer's full list, with no dedup — so the body of each key
// finding printed twice, adding a page to a customer-facing report.
//
// WHY THE EXISTING SUITE MISSED IT: dd-pdf-full-sections-rf2848's fixture uses
// key_findings ("INFO finding", "RED finding") that appear in NO layer, so the
// overlap that exists in every real report was never exercised. The fixture, not
// the assertion, was the gap.
//
// THE FIX MUST NOT DROP ANYTHING. A key finding keeps its place in the summary
// and its full body in its layer; only the DUPLICATED body is suppressed, and
// only when the finding genuinely appears in a layer section.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ddReportSections } from '../lib/reports/pdf_generator.mjs';

// Realistic: key_findings is a rollup OF the layer findings, as production builds it.
const GLEIF = {
  severity: 'info', title: 'GLEIF: LEI 254900184SNIZJY19F60',
  detail: 'Authoritative GLEIF record: legal name PENFOLD SAVINGS LIMITED, status ACTIVE.',
  confidence: 'PROBABLE', source: 'gleif.search_lei',
};
const CHARGES = {
  severity: 'info', title: 'No outstanding charges registered',
  detail: 'The Companies House charges register was consulted and records 0 charge(s).',
  confidence: 'CONFIRMED', source: 'companies_house.charges',
};
const IDENTITY_ONLY = {
  severity: 'info', title: 'director EASTWOOD — sanctions screen CLEAN',
  detail: 'No matches across the sanctions/PEP datasets reached by this screen.',
  confidence: 'CONFIRMED', source: 'sanctions.director_screen',
};

const REPORT = {
  identity: {
    meta: { status: 'ok' }, registration_status: 'active',
    findings: [GLEIF, CHARGES, IDENTITY_ONLY],
  },
  synthesis: { key_findings: [GLEIF, CHARGES] },
};

function bodies(sections) {
  const out = [];
  for (const s of sections) for (const f of s.findings || []) {
    if (f.detail) out.push(f.detail);
  }
  return out;
}

test('a key finding body is not printed twice', () => {
  const b = bodies(ddReportSections(REPORT));
  const dupes = b.filter((x, i) => b.indexOf(x) !== i);
  assert.deepEqual(dupes, [], `these finding bodies render twice in one report: ${dupes}`);
});

test('the summary still lists the key findings', () => {
  const kf = ddReportSections(REPORT).find((s) => s.title === 'Key findings');
  assert.ok(kf, 'the executive summary must survive');
  assert.deepEqual(kf.findings.map((f) => f.title),
    ['GLEIF: LEI 254900184SNIZJY19F60', 'No outstanding charges registered']);
});

test('the layer keeps the FULL body — nothing is dropped', () => {
  const id = ddReportSections(REPORT).find((s) => s.title === 'Identity');
  const byTitle = Object.fromEntries(id.findings.map((f) => [f.title, f]));
  assert.equal(byTitle['GLEIF: LEI 254900184SNIZJY19F60'].detail, GLEIF.detail,
    'the layer section is where the complete list lives (dd_orchestrator contract)');
  assert.equal(byTitle['No outstanding charges registered'].detail, CHARGES.detail);
  assert.equal(byTitle['director EASTWOOD — sanctions screen CLEAN'].detail, IDENTITY_ONLY.detail);
});

test('a key finding with NO layer home keeps its body in the summary', () => {
  // Otherwise suppressing the duplicate would silently delete the only copy.
  const orphan = { severity: 'red', title: 'Orphan finding', detail: 'Only copy of this text.' };
  const sections = ddReportSections({
    identity: { meta: { status: 'ok' }, findings: [GLEIF] },
    synthesis: { key_findings: [GLEIF, orphan] },
  });
  const kf = sections.find((s) => s.title === 'Key findings');
  const got = kf.findings.find((f) => f.title === 'Orphan finding');
  assert.equal(got.detail, 'Only copy of this text.',
    'a finding that appears nowhere else must keep its detail in the summary');
});

test('the original finding objects are not mutated', () => {
  ddReportSections(REPORT);
  assert.equal(GLEIF.detail,
    'Authoritative GLEIF record: legal name PENFOLD SAVINGS LIMITED, status ACTIVE.',
    'ddReportSections is documented as PURE — it must not edit the report it reads');
});
