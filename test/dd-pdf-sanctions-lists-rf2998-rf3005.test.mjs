// R-F2998 + R-F3005 — DD PDF: name the sanctions lists screened (+ date) and
// relabel section status OK -> COMPLETED.
//
// DD-practitioner review: the "Compliance and Sanctions" section named no lists
// and no screening date (the per-list HIT/CLEAN/UNAVAILABLE breakdown already
// existed on identity.sanctions_screen.verified_sources but was never rendered),
// and every section header read "OK" while the evidence grade was D ("OK reads as
// a quality judgement even if you mean completed").
//
// Tests the PURE selector ddReportSections() + a source-lock on the drawer's label
// map + an end-to-end smoke render (pdfkit compresses streams, so byte-substring
// asserts are a proxy — the selection LOGIC is the real contract).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { ddReportSections, generateDueDiligencePDF } from '../lib/reports/pdf_generator.mjs';

const REPORT = {
  generated_at: '2026-07-24T12:00:00Z',
  identity: {
    meta: { status: 'ok' }, registration_status: 'active',
    sanctions_screen: {
      verified_sources: [
        { label: 'OFAC SDN', status: 'CLEAN' },
        { label: 'EU Consolidated', status: 'CLEAN' },
        { label: 'UK OFSI', status: 'HIT', match_count: 1 },
        { label: 'UN SC Consolidated', status: 'UNAVAILABLE' },
      ],
    },
    findings: [],
  },
  compliance: { meta: { status: 'ok' }, country_risk: { headline_risk: 'medium' }, findings: [] },
};

test('R-F2998: compliance section carries the NAMED sanctions lists + a screening date', () => {
  const comp = ddReportSections(REPORT).find((s) => s.title === 'Compliance and sanctions');
  assert.ok(comp, 'compliance section present');
  assert.deepEqual(
    comp.sanctionsSources.map((s) => `${s.label}:${s.status}`),
    ['OFAC SDN:CLEAN', 'EU Consolidated:CLEAN', 'UK OFSI:HIT', 'UN SC Consolidated:UNAVAILABLE'],
  );
  assert.equal(comp.sanctionsDate, '2026-07-24T12:00:00Z');   // falls back to report date
});

test('R-F2998: sanctions lists attach ONLY to compliance, not other sections', () => {
  const secs = ddReportSections(REPORT);
  const idn = secs.find((s) => s.title === 'Identity');
  assert.ok(!idn.sanctionsSources || idn.sanctionsSources.length === 0);
});

test('R-F2998: compliance renders even when the lists are its only content', () => {
  const rep = {
    generated_at: '2026-07-24T00:00:00Z',
    identity: { sanctions_screen: { verified_sources: [{ label: 'OFAC SDN', status: 'CLEAN' }] } },
    compliance: { meta: {} },
  };
  const comp = ddReportSections(rep).find((s) => s.title === 'Compliance and sanctions');
  assert.ok(comp && comp.sanctionsSources.length === 1, 'section not omitted when only sanctions lists present');
});

test('R-F3005: the section drawer maps ok -> COMPLETED (not the bare OK)', () => {
  const src = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), '..', 'lib', 'reports', 'pdf_generator.mjs'), 'utf-8');
  assert.ok(/sec\.status === 'ok' \? 'COMPLETED'/.test(src), "drawer must relabel ok -> COMPLETED");
});

test('R-F2998/R-F3005: the DD PDF still renders end-to-end without throwing', async () => {
  const buf = await generateDueDiligencePDF(REPORT, { docRef: 'test-rf2998' });
  assert.ok(Buffer.isBuffer(buf) && buf.length > 500, 'PDF buffer produced');
});
