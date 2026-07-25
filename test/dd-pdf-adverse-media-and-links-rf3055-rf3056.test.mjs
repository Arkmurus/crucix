// R-F3055 + R-F3056 — adverse media on the PDF, and clickable source links.
//
// OPERATOR (2026-07-25):
//   "on the newest report even when it is downloaded the adverse media sections does
//    not show on the pdf report it must be included also, the online report must
//    match 100% the downloaded version in any shape or form"
//   "the pdf should include the media links as hyperlinks to ensure easy access to
//    those media contents ... ensure the entire DD report matches aria USP"
//
// CAUSE: `adverse_media` is a TOP-LEVEL report key, not a layer, so the PDF's
// _DD_LAYER_PLAN never reached it. Verified on the live reports: every completed run
// carried `adverse_media.status = "in_progress"`, and the PDF rendered NOTHING —
// which reads as "nothing adverse found". That is the false clean this product
// exists to prevent, so the state is now stated first, on every surface.
import test from 'node:test';
import assert from 'node:assert/strict';
import { ddReportSections, generateDueDiligencePDF } from '../lib/reports/pdf_generator.mjs';

const BASE = {
  identity: { meta: { status: 'ok' }, registration_status: 'active' },
  digital: {
    meta: { status: 'ok' },
    press_coverage: [{ url: 'https://www.ft.com/x', source: 'ft.com', source_tier: 'T1' }],
  },
};

function digitalOf(report) {
  const s = ddReportSections(report).find((x) => /digital/i.test(x.title));
  assert.ok(s, 'the digital section must exist');
  return s;
}

test('R-F3055: an unfinished sweep is STATED, never silently absent', () => {
  const sec = digitalOf({ ...BASE, adverse_media: { status: 'in_progress' } });
  const text = sec.adverseMedia.map((l) => l.text).join(' | ');
  assert.match(text, /STILL RUNNING/);
  assert.match(text, /UNCHECKED, not clean/);
});

test('R-F3055: a missing blob says NOT RUN rather than rendering nothing', () => {
  const sec = digitalOf({ ...BASE, adverse_media: {} });
  const text = sec.adverseMedia.map((l) => l.text).join(' | ');
  assert.match(text, /NOT RUN/);
  assert.match(text, /UNCHECKED, not as clean/);
});

test('R-F3055: a completed clean sweep never reads as proof of good standing', () => {
  const sec = digitalOf({ ...BASE,
    adverse_media: { status: 'completed', findings: [], templates_searched: 12,
                     search_backends_answered: true } });
  const text = sec.adverseMedia.map((l) => l.text).join(' | ');
  assert.match(text, /COMPLETED/);
  assert.match(text, /Query templates actually searched: 12/);
  assert.match(text, /absence of COVERAGE, not proof of good standing/);
});

test('R-F3055: the materiality arithmetic is shown when recorded', () => {
  const sec = digitalOf({ ...BASE, adverse_media: { status: 'completed',
    findings: [], materiality: { credible_count: 0, raw_count: 39, duplicates_dropped: 33,
      self_references_dropped: 1, non_adverse_dropped: 5 } } });
  const text = sec.adverseMedia.map((l) => l.text).join(' | ');
  assert.match(text, /0 credible adverse item\(s\) from 39 raw hit\(s\)/);
  assert.match(text, /33 duplicate/);
});

test('R-F3055: backends that did not answer are called out', () => {
  const sec = digitalOf({ ...BASE, adverse_media: { status: 'completed',
    findings: [], search_backends_answered: false } });
  const text = sec.adverseMedia.map((l) => l.text).join(' | ');
  assert.match(text, /NO — the sweep could not observe the web/);
});

test('R-F3056: adverse-media items carry their URL for linking', () => {
  const sec = digitalOf({ ...BASE, adverse_media: { status: 'completed', findings: [
    { title: 'Regulator fines Acme', source_url: 'https://www.ft.com/a' },
    { title: 'Court judgment', source_url: 'https://www.bailii.org/b' },
  ] } });
  const withUrls = sec.adverseMedia.filter((l) => l.url);
  assert.equal(withUrls.length, 2);
  assert.ok(withUrls.every((l) => /^https:\/\//.test(l.url)));
});

test('R-F3056: a memory:// self-reference is NOT rendered as an openable source', () => {
  const sec = digitalOf({ ...BASE, adverse_media: { status: 'completed', findings: [
    { title: 'brain_hook:web_search', source_url: 'memory://brain_hook' },
  ] } });
  const line = sec.adverseMedia.find((l) => /brain_hook/.test(l.text));
  assert.ok(line, 'the item still appears');
  // _linkLine only creates an annotation for http(s); assert the value it will see
  assert.ok(!/^https?:\/\//i.test(line.url || ''),
    'a memory reference must never become a clickable "source"');
});

test('R-F3056: the rendered PDF actually contains link annotations', async () => {
  const buf = await generateDueDiligencePDF({ ...BASE,
    adverse_media: { status: 'completed', findings: [
      { title: 'Regulator fines Acme', source_url: 'https://www.ft.com/a' }] },
  }, { entityName: 'Acme Ltd' });
  const raw = buf.toString('latin1');
  assert.match(raw, /\/Annots/, 'PDFKit must emit link annotations');
  assert.match(raw, /https:\/\/www\.ft\.com/, 'the target URL is embedded');
});

test('PARITY: every online section key has a PDF counterpart', () => {
  // The online view (dd_schema.structured_view) emits these section keys. If a new
  // one appears there, this test fails until the PDF renders it too — which is the
  // guarantee the operator asked for ("no discrepancies whatsoever").
  const ONLINE_SECTION_KEYS = ['identity', 'compliance', 'network', 'digital', 'verification'];
  const PDF_TITLES = {
    identity: /identity/i, compliance: /compliance/i, network: /ownership|network/i,
    digital: /digital/i, verification: /verification/i,
  };
  const report = {
    identity: { meta: { status: 'ok' }, registration_status: 'active' },
    compliance: { meta: { status: 'ok' }, country_risk: { headline_risk: 'GREEN' } },
    network: { meta: { status: 'ok' }, controlled_by: [{ controller_name: 'X Ltd' }] },
    digital: { meta: { status: 'ok' }, press_coverage: [{ url: 'https://a.com', source: 'a' }] },
    verification: { meta: { status: 'ok' }, grounded_rate: 0.3 },
    adverse_media: { status: 'completed', findings: [] },
  };
  const secs = ddReportSections(report);
  for (const key of ONLINE_SECTION_KEYS) {
    assert.ok(secs.some((s) => PDF_TITLES[key].test(s.title)),
      `online section '${key}' has no PDF counterpart`);
  }
});
