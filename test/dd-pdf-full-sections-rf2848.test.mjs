// R-F2848 — the DD PDF must carry the full per-layer findings, honestly.
//
// These test ddReportSections(), the PURE selection function the renderer draws
// from. Asserting on the rendered PDF bytes would be a proxy — pdfkit compresses
// its streams, so a substring match on output is exactly the kind of "measure a
// correlate" trap this codebase keeps finding. The selection LOGIC (ordering,
// what is shown, what is omitted, error carry-through) is the real contract; the
// drawing is verified end-to-end by rendering a live report to a real PDF.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ddReportSections, generateDueDiligencePDF } from '../lib/reports/pdf_generator.mjs';

const REPORT = {
  identity: {
    meta: { status: 'ok' }, registration_status: 'active', incorporation_date: '2011-02-10',
    findings: [{ severity: 'info', title: 'GLEIF LEI issued', source: 'gleif',
      url: 'https://search.gleif.org/x' }],
  },
  compliance: { meta: { status: 'error', error: 'sanctions source down' }, findings: [] },
  network: {
    meta: { status: 'ok' }, ubo_chain: [{ x: 1 }],
    findings: [{ severity: 'amber', title: 'Nominee pattern', confidence: 'PROBABLE',
      source: 'network_walker' }],
  },
  synthesis: {
    key_findings: [
      { severity: 'info', title: 'INFO finding' },
      { severity: 'red', title: 'RED finding' },
      { severity: 'amber', title: 'AMBER finding' },
    ],
  },
};

test('sections appear in priority order, key findings first', () => {
  const titles = ddReportSections(REPORT).map((s) => s.title);
  assert.deepEqual(titles, [
    'Key findings', 'Identity', 'Compliance and sanctions', 'Ownership and control network',
  ]);
});

test('findings within a section are severity-sorted, none dropped', () => {
  const kf = ddReportSections(REPORT)[0].findings.map((f) => f.title);
  assert.deepEqual(kf, ['RED finding', 'AMBER finding', 'INFO finding']);
  assert.equal(kf.length, 3, 'no finding may be dropped by sorting');
});

test('an errored layer carries its error through — never renders clean', () => {
  const comp = ddReportSections(REPORT).find((s) => s.title === 'Compliance and sanctions');
  assert.ok(comp, 'an errored layer with a status must still be shown');
  assert.equal(comp.status, 'error');
  assert.equal(comp.error, 'sanctions source down');
});

test('a source URL on a finding is preserved for traceability', () => {
  const id = ddReportSections(REPORT).find((s) => s.title === 'Identity');
  assert.equal(id.findings[0].url, 'https://search.gleif.org/x');
});

test('nested structures are summarised, not dumped', () => {
  // R-F3049 — formatting moved INTO ddReportSections so what the PDF will say is
  // testable without rendering (the R-F2848 principle: the renderer computes
  // nothing). This assertion used to pin the raw TYPE, which enforced nothing about
  // the output; the intent — summarise, never dump a JSON blob — is asserted on the
  // display string now. It is also stricter than before: a chain is NAMED rather
  // than counted, because "1 item" told a reader nothing (see R-F3049).
  const net = ddReportSections(REPORT).find((s) => s.title === 'Ownership and control network');
  const ubo = net.facts.find(([k]) => k === 'ubo_chain');
  assert.ok(ubo, 'ubo_chain should surface as a fact');
  const shown = String(ubo[1]);
  assert.equal(typeof ubo[1], 'string', 'the fact carries its display string');
  assert.ok(!shown.includes('{') && !shown.includes('['), 'never dump raw structure');
  assert.ok(shown.length < 400, 'summarised, not the whole chain');
});

test('nothing is invented — an empty report yields no findings sections', () => {
  assert.deepEqual(ddReportSections({}), []);
  assert.deepEqual(ddReportSections({ identity: { meta: {} } }), [],
    'a layer with no status, facts or findings is omitted, not shown empty');
});

test('a layer present but findings-empty with a status is still shown', () => {
  const secs = ddReportSections({ identity: { meta: { status: 'ok' }, registration_status: 'active' } });
  assert.equal(secs.length, 1);
  assert.equal(secs[0].title, 'Identity');
});

test('findings with no severity still appear (default rank, not dropped)', () => {
  const secs = ddReportSections({ identity: { meta: { status: 'ok' }, findings: [{ title: 'No-sev' }] } });
  assert.equal(secs[0].findings[0].title, 'No-sev');
});

test('end-to-end: the findings layers make the PDF materially larger', async () => {
  const base = {
    run_id: 'e2e', target: { name: 'Test Co plc' }, risk_classification: 'GREEN',
    decision_readiness: { status: 'NOT_CLEARED', clearance_ready: false, answered: 1, required: 5,
      questions: {} },
  };
  const withFindings = await generateDueDiligencePDF({ ...base, ...REPORT }, { docRef: 'e2e' });
  const withoutFindings = await generateDueDiligencePDF(base, { docRef: 'e2e' });

  assert.equal(withFindings.slice(0, 5).toString(), '%PDF-', 'must be a valid PDF');
  // Differential, not an absolute threshold: PDF size tracks content, so the
  // honest signal is that adding the layers ADDS output, not a magic byte count.
  assert.ok(withFindings.length > withoutFindings.length + 500,
    `findings layers should add substantial output — with=${withFindings.length} `
    + `without=${withoutFindings.length}`);
});

test('the USP rule is structural: GREEN in, but readiness carried alongside', () => {
  // ddReportSections never sees the verdict — it cannot upgrade or hide it. This
  // guards that the findings path did not accidentally take over verdict rendering.
  const secs = ddReportSections({ risk_classification: 'GREEN' });
  assert.deepEqual(secs, [], 'a bare verdict produces no findings sections and no verdict text');
});
