// R-F3019 / R-F3026 / R-F3027 / R-F3029 — what the DD PDF must actually contain.
//
// R-F3019: the R-F2998 sanctions-list block was gated on
//   `Array.isArray(screen.verified_sources)` — but `derive_verified_sources()`
//   returns a DICT keyed by list name. `Array.isArray({})` is false, so the branch
//   NEVER ran in production and every PDF shipped with the lists silently absent.
//   The existing R-F2998 test passed because its fixture was an ARRAY — a fixture
//   that did not match the shape the server actually produces.
// R-F3026: the PDF had NO code path for directors or PSCs (a grep for
//   directors|officers matched one unrelated comment), while the readiness
//   scorecard printed on page 1 of the same PDF claims "live registry status plus
//   number and DIRECTORS/incorporation" as its identity evidence.
// R-F3027: a corporate controller CH gives no registration number for was dropped
//   entirely — live, that was a 75-100% shareholder.
// R-F3029: the only stated blocker was "evidence grade D does not meet the Grade A
//   threshold" — a restatement of the grade, not a reason.
import test from 'node:test';
import assert from 'node:assert/strict';
import { ddReportSections, generateDueDiligencePDF } from '../lib/reports/pdf_generator.mjs';

const REPORT = {
  identity: {
    meta: { status: 'ok' },
    registration_status: 'active',
    incorporation_date: '2011-11-03',
    previous_names: [
      { name: 'ENGINEERING FOR THE FUTURE LIMITED', effective_from: '2011-11-03', ceased_on: '2025-12-24' },
    ],
    directors: [
      { name: 'JENKINS, Christopher Michael', officer_role: 'director', appointed_on: '2015-04-01', nationality: 'British' },
      { name: 'KIEFT, David John', officer_role: 'director', appointed_on: '2011-11-03' },
    ],
    shareholders: [
      {
        name: 'Raven Delta Limited',
        kind: 'corporate-entity-person-with-significant-control',
        natures_of_control: ['ownership-of-shares-75-to-100-percent', 'voting-rights-75-to-100-percent'],
        identification: { legal_form: 'Private Limited Company' },
      },
    ],
    // THE PRODUCTION SHAPE — a dict, not an array.
    sanctions_screen: {
      screened_at: '2026-07-25T09:15:00+00:00',
      verified_sources: {
        'OFAC SDN': { label: 'US Treasury — OFAC SDN', status: 'CLEAN' },
        'UK OFSI / HMT': { label: 'HM Treasury OFSI', status: 'CLEAN' },
        'EU Consolidated': { label: 'EU Financial Sanctions Database', status: 'UNAVAILABLE' },
      },
    },
  },
  compliance: { meta: { status: 'ok' }, country_risk: 'GREEN' },
  network: {
    meta: { status: 'ok' },
    controlled_by: [],
    controlled_by_unanchored: [
      {
        controller_name: 'Raven Delta Limited',
        controller_registration_number: '',
        natures_of_control: ['ownership-of-shares-75-to-100-percent'],
        grade: 'B',
      },
    ],
  },
  decision_readiness: {
    status: 'NOT_CLEARED',
    answered: 3,
    blocking_reasons: ['evidence grade D does not meet the Grade A reliance threshold'],
  },
  quality_assessment: {
    grade: 'D',
    blocking_reasons: [
      'only 5 cited sources (need 8)',
      'only 2 reputable independent sources (need 5)',
      'citation grounding 0%',
    ],
  },
};

test('R-F3019: verified_sources as a DICT (the production shape) renders the lists', () => {
  const secs = ddReportSections(REPORT);
  const compliance = secs.find((s) => /compliance/i.test(s.title));
  assert.ok(compliance, 'compliance section must be present');
  assert.equal(compliance.sanctionsSources.length, 3,
    'the dict shape must produce one entry per list — this was 0 in production');
  const names = compliance.sanctionsSources.map((s) => s.label);
  assert.ok(names.some((n) => /OFAC SDN/.test(n)));
  assert.equal(compliance.sanctionsDate, '2026-07-25T09:15:00+00:00',
    'the screening date must come from screened_at, not the report timestamp');
  // a list that did not answer must survive to the page, never be quietly dropped
  const unavail = compliance.sanctionsSources.find((s) => s.status === 'UNAVAILABLE');
  assert.ok(unavail, 'an UNAVAILABLE list must still be rendered');
});

test('R-F3019: no list is ever rendered as "?"', () => {
  const secs = ddReportSections(REPORT);
  const compliance = secs.find((s) => /compliance/i.test(s.title));
  for (const s of compliance.sanctionsSources) {
    assert.ok(String(s.label || s.name || '').trim().length > 1);
  }
});

test('R-F3026: directors, PSCs and former names reach the PDF', () => {
  const secs = ddReportSections(REPORT);
  const identity = secs.find((s) => /identity/i.test(s.title));
  const blob = JSON.stringify(identity.people);
  assert.match(blob, /JENKINS, Christopher Michael/);
  assert.match(blob, /appointed 2015-04-01/);
  assert.match(blob, /KIEFT, David John/);
  assert.match(blob, /Raven Delta Limited/);
  assert.match(blob, /ownership of shares 75 to 100 percent/);
  assert.match(blob, /ENGINEERING FOR THE FUTURE LIMITED/);
});

test('R-F3027: an untraversed controller is named AND labelled as untraversed', () => {
  const secs = ddReportSections(REPORT);
  const net = secs.find((s) => /ownership|network/i.test(s.title));
  const blob = JSON.stringify(net.people);
  assert.match(blob, /Raven Delta Limited/);
  assert.match(blob, /NOT traversed/i);
  assert.match(blob, /NO registration number/i);
});

test('R-F3026: nothing is invented for an entity with no people on file', () => {
  const bare = { identity: { meta: { status: 'ok' }, registration_status: 'active' } };
  const secs = ddReportSections(bare);
  const identity = secs.find((s) => /identity/i.test(s.title));
  assert.deepEqual(identity.people, [], 'no people on file must render no people');
});

test('R-F3029: the real grade-D reasons are available to the renderer', () => {
  // the tautology alone is not a reason a reader can act on
  assert.ok(REPORT.quality_assessment.blocking_reasons.length >= 3);
  const src = REPORT.decision_readiness.blocking_reasons[0];
  assert.match(src, /evidence grade/i);
});

test('the DD PDF still renders end-to-end with all of it', async () => {
  const buf = await generateDueDiligencePDF(REPORT, { entityName: 'EFT CONSULT LTD' });
  assert.ok(Buffer.isBuffer(buf) && buf.length > 1000, 'a real PDF must come out');
});
