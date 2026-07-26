/**
 * R-F3098 — placement is a claim: context printed as a compliance finding.
 *
 * LIVE DEFECT (Mitie, 2026-07-26). Under "Compliance and sanctions" — the section a
 * reader scans to decide whether they may transact — the PDF printed:
 *
 *   INFO  Sovereign macro context: central-govt debt 130.7% of GDP
 *         "...Country-level context ... - not a finding against this entity."
 *   INFO  US federal contracts: 4 award(s), $3,409,511
 *
 * R-F3000 had already cut the first to `info` while leaving it in the compliance
 * findings list. Severity was never the problem: POSITION asserted what the wording
 * denied. Nothing is dropped here — context moves to its own labelled block, after
 * the findings that are actually about the subject.
 *
 * Asserted against `ddReportSections` (the pure selection function), not the PDF
 * bytes: pdfkit compresses its streams, so grepping output is a proxy, not the
 * property.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { ddReportSections, generateDueDiligencePDF } from '../lib/reports/pdf_generator.mjs';

const REPORT = {
  identity: { meta: { status: 'ok' }, entity_name: 'MITIE FACILITIES MANAGEMENT LIMITED' },
  compliance: {
    meta: { status: 'ok' },
    country_risk: 'GREEN',
    findings: [
      { severity: 'amber', title: 'Export licence required for this end-use',
        detail: 'dual-use' },
      { severity: 'info', context_only: true, context_kind: 'Country & market context',
        title: 'Sovereign macro context: central-govt debt 130.7% of GDP',
        detail: 'Country-level context - not a finding against this entity.' },
      { severity: 'info', context_only: true, context_kind: 'Commercial footprint',
        title: 'US federal contracts: 4 award(s), $3,409,511',
        detail: 'Top awarding agencies: Department of Defense, Department of State.' },
    ],
  },
};

const compliance = (r) => ddReportSections(r).find(s => s.title === 'Compliance and sanctions');

test('R-F3098: context leaves the decision-driving findings list', () => {
  const comp = compliance(REPORT);
  assert.deepEqual(comp.findings.map(f => f.title), ['Export licence required for this end-use']);
  assert.ok(!comp.findings.some(f => /Sovereign macro/.test(f.title)),
    'R-F3098 REGRESSION: a country statistic is back among compliance findings');
  assert.ok(!comp.findings.some(f => /US federal contracts/.test(f.title)));
});

test('R-F3098: nothing is dropped — it is moved, not removed', () => {
  const comp = compliance(REPORT);
  assert.equal(comp.contextFindings.length, 2);
  assert.ok(comp.contextFindings.some(f => /Sovereign macro/.test(f.title)));
  assert.ok(comp.contextFindings.some(f => /US federal contracts/.test(f.title)));
});

test('R-F3098: context is grouped by kind, never severity-ranked with findings', () => {
  const kinds = compliance(REPORT).contextFindings.map(f => f.context_kind);
  assert.deepEqual(kinds, [...kinds].sort(),
    'severity-ranking a country statistic against a sanctions hit is the conflation '
    + 'this split exists to end');
});

test('R-F3098: an unflagged report is completely unchanged', () => {
  const comp = compliance({ compliance: { meta: { status: 'ok' }, findings: [
    { severity: 'red', title: 'A' }, { severity: 'info', title: 'B' }] } });
  assert.deepEqual(comp.findings.map(f => f.title), ['A', 'B']);
  assert.deepEqual(comp.contextFindings, []);
});

test('R-F3098: a section carrying ONLY context is not dropped as empty', () => {
  const comp = compliance({ compliance: { meta: {}, findings: [
    { severity: 'info', context_only: true, context_kind: 'Country & market context',
      title: 'Sovereign macro context: debt 130.7% of GDP' }] } });
  assert.ok(comp, 'a context-only section still has something to show');
  assert.equal(comp.contextFindings.length, 1);
});

test('R-F3098: the PDF renders end-to-end with context findings', async () => {
  const buf = await generateDueDiligencePDF(REPORT);
  assert.ok(Buffer.isBuffer(buf) && buf.length > 1000, 'PDF must render');
});

test('R-F3098: malformed context flags never break the renderer', async () => {
  const buf = await generateDueDiligencePDF({ compliance: { meta: { status: 'ok' }, findings: [
    { severity: 'info', context_only: true, title: 'no kind supplied' },
    { severity: 'info', context_only: 'yes', title: 'truthy non-boolean' }] } });
  assert.ok(Buffer.isBuffer(buf) && buf.length > 1000);
});
