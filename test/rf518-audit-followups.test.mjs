// R-F518 — test-regex tolerance for combined-selector CSS refactors.
//
// Live failure: R-F441 (a8afae6, 2026-05-13) merged the
// `#fetch-failure-banner` and `#js-error-banner` CSS selectors in
// `public/aria-brain.html` into a single rule:
//
//   #fetch-failure-banner, #js-error-banner { display: none; ... background: #3d1418; ... }
//
// The R-F391 capability test at `test/aria-brain-honesty-rf391.test.mjs`
// asserted the visible-alert + display:none invariant with regexes that
// expected the un-merged form `#fetch-failure-banner {`. Both assertions
// silently broke even though the underlying CSS still holds the
// invariant. Honest test discipline says the regression-guard test must
// re-pass — R-F518 widens the bracket from `{` to `[,{]` so it matches
// both single-selector and comma-list forms.
//
// This file locks the bracket-tolerance in so that a future cleanup
// can't silently re-narrow it.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));

// The two regex patterns that R-F518 widened. Keep these literally in
// sync with `aria-brain-honesty-rf391.test.mjs:58,203`.
const BANNER_VISIBLE_REGEX =
  /#fetch-failure-banner\s*[,{][\s\S]*?display:\s*none[\s\S]*?background:\s*#3d1418/;
const BANNER_HIDDEN_DEFAULT_REGEX =
  /#fetch-failure-banner\s*[,{][\s\S]*?display:\s*none/;

test('R-F518: regex matches the un-merged single-selector form (pre-R-F441 shape)', () => {
  const css = `
    #fetch-failure-banner {
      display: none;
      margin-bottom: 16px;
      padding: 12px 16px;
      background: #3d1418;
    }
  `;
  assert.match(css, BANNER_VISIBLE_REGEX, 'must match pre-R-F441 single-selector form');
  assert.match(css, BANNER_HIDDEN_DEFAULT_REGEX);
});

test('R-F518: regex matches the merged comma-list form (post-R-F441 shape)', () => {
  const css = `
    #fetch-failure-banner, #js-error-banner {
      display: none;
      margin-bottom: 16px;
      padding: 12px 16px;
      background: #3d1418;
    }
  `;
  assert.match(css, BANNER_VISIBLE_REGEX, 'must match post-R-F441 multi-selector form');
  assert.match(css, BANNER_HIDDEN_DEFAULT_REGEX);
});

test('R-F518: regex still rejects the busted shape — banner without display:none', () => {
  // Honesty regression: if a future refactor accidentally removed
  // display:none, the assertion must FAIL (no false positives on the
  // wider bracket).
  const broken = `
    #fetch-failure-banner, #js-error-banner {
      background: #3d1418;
      margin-bottom: 16px;
    }
  `;
  assert.doesNotMatch(broken, BANNER_HIDDEN_DEFAULT_REGEX);
});

test('R-F518: regex still rejects styling that omits the red-on-dark background', () => {
  const wrongColor = `
    #fetch-failure-banner, #js-error-banner {
      display: none;
      background: #ffffff;
    }
  `;
  assert.doesNotMatch(wrongColor, BANNER_VISIBLE_REGEX);
});

test('R-F518: production aria-brain.html still satisfies the banner invariant', () => {
  // End-to-end pin: the live file must hold the invariant. This is the
  // same assertion the R-F391 test makes — duplicated here so R-F518
  // remains anchored even if the R-F391 test is refactored.
  const html = readFileSync(
    join(__dirname, '..', 'public', 'aria-brain.html'),
    'utf8',
  );
  assert.match(html, BANNER_VISIBLE_REGEX);
  assert.match(html, BANNER_HIDDEN_DEFAULT_REGEX);
});
