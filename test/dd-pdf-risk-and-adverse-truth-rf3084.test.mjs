// R-F3084 — the PDF must distinguish raw search output from filtered adverse items.
import test from 'node:test';
import assert from 'node:assert/strict';

import { ddReportSections } from '../lib/reports/pdf_generator.mjs';

const BASE = {
  identity: { meta: { status: 'ok' }, registration_status: 'active' },
  digital: { meta: { status: 'ok' }, press_coverage: [] },
};

function adverseText(adverseMedia) {
  const section = ddReportSections({ ...BASE, adverse_media: adverseMedia })
    .find((candidate) => /digital/i.test(candidate.title));
  assert.ok(section, 'the customer-facing digital section must exist');
  return {
    headline: section.adverseMedia[0].text,
    concern: section.adverseMedia[1].text,
    headlineWarns: section.adverseMedia[0].warn,
    lines: section.adverseMedia.map((line) => line.text).join(' | '),
  };
}

test('R-F3084: persisted materiality renders only the filtered review set', () => {
  const rendered = adverseText({
    status: 'completed',
    findings: [
      { title: 'raw registry result', source_url: 'https://example.test/raw' },
      { title: 'raw memory result', source_url: 'memory://search' },
    ],
    findings_for_review: [],
    materiality: {
      credible_count: 0,
      raw_count: 2,
      duplicates_dropped: 0,
      self_references_dropped: 1,
      non_adverse_dropped: 1,
    },
  });

  assert.doesNotMatch(rendered.headline, /2 item\(s\) require review/);
  assert.match(rendered.lines, /Raw search results returned: 2/);
  assert.match(rendered.lines, /0 item\(s\) require human review after filtering/);
  assert.doesNotMatch(rendered.lines, /raw registry result|raw memory result/);
});

test('R-F3084: legacy raw hits are labelled unfiltered, never subject-attributed', () => {
  const rendered = adverseText({
    status: 'completed',
    findings: [
      { title: 'company registry overview', source_url: 'https://example.test/raw' },
    ],
  });

  assert.equal(rendered.headlineWarns, true);
  assert.match(rendered.headline, /RAW SEARCH RESULTS REQUIRE FILTERING/);
  assert.match(rendered.concern, /subject attribution has not been verified/);
  assert.match(rendered.lines, /Raw, unfiltered search results returned: 1/);
  assert.match(rendered.lines, /\[RAW\/UNFILTERED\] company registry overview/);
  assert.doesNotMatch(rendered.lines, /Subject-named items returned/);
});

test('R-F3084: a completed zero-hit sweep keeps the coverage warning', () => {
  const rendered = adverseText({
    status: 'completed',
    findings: [],
    templates_searched: 12,
    search_backends_answered: true,
  });

  assert.match(rendered.lines, /absence of COVERAGE, not proof of good standing/);
});
