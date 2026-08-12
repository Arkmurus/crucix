// C-36 follow-through / R-F3933 — the honesty fields had no reader.
//
// C-36 added `signal_direction` and `metric_note` to
// GET /api/aria/source_validator/health, because `web_atlas.record_correction` has no
// caller (C-32) and the EMA can therefore only RISE — a quantity that cannot decrease
// is not a reliability score. The API became honest and **nothing rendered it**:
// `grep -c 'signal_direction\|metric_note' public/sources.html` returned 0.
//
// So the operator — who reads the page, not the JSON — still saw
// "Registry reliability (measured observations)" with healthy / degraded / failing /
// dead bands over a one-way counter, and a 0.995 still read as "verified not to fail".
//
// That is C-27's producer-with-no-consumer shape ("an instrument nobody can read is
// indistinguishable from health"), occurring inside the fix written to cure exactly
// that class. Caught by self-audit, not by the test suite, because every assertion
// C-36 made about the API was true.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'fs';

const PAGE = readFileSync(new URL('../public/sources.html', import.meta.url), 'utf8');

test('C-36: the page reads signal_direction', () => {
  assert.ok(
    /signal_direction/.test(PAGE),
    'sources.html never reads signal_direction — the API states the number cannot ' +
      'fall and the panel that renders it does not say so',
  );
});

test('C-36: the page renders metric_note rather than discarding it', () => {
  assert.ok(
    /metric_note/.test(PAGE),
    'metric_note explains what the number actually counts; dropping it leaves the ' +
      '"reliability" heading unqualified',
  );
});

test('C-36: the note is escaped like every other server-supplied string', () => {
  // Whole-file match on purpose: the first textual occurrence of `metric_note` is
  // inside the explanatory HTML comment, so a window around it would test the
  // comment rather than the code.
  assert.ok(
    /escHtml\(\s*d\.metric_note/.test(PAGE),
    'metric_note is server-supplied text heading for innerHTML and must go through ' +
      'escHtml, per the R-F3845 XSS guard',
  );
});

test('C-36: the banner asserts nothing when the API states no direction', () => {
  // An older aria-intel build predates C-36 and sends neither field. The panel must
  // stay silent rather than defaulting to a claim — the same reasoning that makes
  // the counts render '?' instead of 0 when the key is absent.
  assert.ok(
    PAGE.includes("getElementById('reghealth-direction')"),
    'the direction banner is never populated by the renderer',
  );
  assert.ok(
    /dirEl\.style\.display\s*=\s*'none'/.test(PAGE),
    'the banner must be hidden unless the API supplies a recognised direction',
  );
  assert.ok(
    /'positive_only'/.test(PAGE) && /'bidirectional'/.test(PAGE),
    'it must render only the two directions the API defines, never an inferred one',
  );
});
