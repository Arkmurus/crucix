// R-F2910 — capability test for honest composite rendering.
//
// Drives the real loadComposite function extracted from aria-brain.html.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
// R-F3845 — this slice now escapes its output, and escapeHtml is defined outside
// every slice. Use the PAGE's own copy so the sandbox runs what production runs.
import { escapeHtmlSource } from './helpers/aria_brain_page.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, '..', 'public', 'aria-brain.html'), 'utf8');
const start = html.indexOf('async function loadComposite');
const end = html.indexOf('async function loadCalibration', start);
assert.ok(start > 0 && end > start, 'real loadComposite function must be extractable');
const source = html.slice(start, end);

async function render(payload) {
  const elements = {
    'composite-badge': { textContent: '', className: '' },
    'composite-metrics': { innerHTML: '' },
  };
  const context = {
    fetchJson: async () => payload,
    document: { getElementById: id => elements[id] },
    pct: value => value != null ? `${(value * 100).toFixed(0)}%` : '--',
    scoreClass: () => 'neutral',
    metricRow: (label, value, cls = 'neutral') =>
      `<div class="metric"><span class="label">${label}</span><span class="value ${cls}">${value}</span></div>`,
    resetBadge: (id, label) => {
      elements[id].textContent = label;
      elements[id].className = 'badge badge-red';
    },
  };
  vm.createContext(context);
  vm.runInContext(`${escapeHtmlSource()}
${source}; this.runLoader = loadComposite;`, context);
  await context.runLoader();
  return elements;
}

console.log('R-F2910 aria-brain composite provenance tests');

const unavailable = await render(null);
assert.equal(unavailable['composite-badge'].textContent, 'SCORE: DOWN');
assert.equal(unavailable['composite-badge'].className, 'badge badge-red');
assert.match(unavailable['composite-metrics'].innerHTML, /No data yet/);

const missingScore = await render({ tier: 2, tier_name: 'MEDIUM' });
assert.equal(missingScore['composite-badge'].textContent, 'SCORE: DOWN');
assert.match(missingScore['composite-metrics'].innerHTML, /No data yet/);

const zero = await render({
  composite_score: 0,
  tier: 0,
  tier_name: 'NONE',
  signals: { mastery: 0, verification: 0, honesty_rate: 0 },
  weights: { mastery: 0.3, verification: 0.35, honesty_rate: 0.15 },
  confidence: 1,
  low_confidence: false,
  details: { mastery_topics: 9, verification_samples: 5, honesty_rate_samples: 6 },
  computed_at: '2026-07-23T12:00:00Z',
});
assert.match(zero['composite-badge'].textContent, /SCORE: 0% NONE/);
assert.doesNotMatch(zero['composite-metrics'].innerHTML, /No data yet/);
assert.match(zero['composite-metrics'].innerHTML, /n=5/);
assert.match(zero['composite-metrics'].innerHTML, /n=6/);
assert.doesNotMatch(zero['composite-metrics'].innerHTML, /\(,\s*n=/);

const partial = await render({
  composite_score: 0.82,
  tier: 3,
  tier_name: 'HIGH',
  signals: { mastery: 0.82, verification: null, honesty_rate: null },
  weights: { mastery: 0.3, verification: 0.35, honesty_rate: 0.15 },
  confidence: 0.375,
  low_confidence: true,
  details: {
    mastery_topics: 9,
    verification_source: 'insufficient_samples_n2',
    verification_samples: 2,
    honesty_rate_source: 'no_data_neutral_prior',
    honesty_rate_samples: 0,
  },
});
assert.match(partial['composite-badge'].textContent, /^PARTIAL SCORE: 82% MEDIUM \(capped, signal-pending\)/);
assert.match(partial['composite-metrics'].innerHTML, /Measured Weight.*38%/s);
assert.match(partial['composite-metrics'].innerHTML, /Displayed Tier.*MEDIUM \(capped, signal-pending\) \(2\)/s);
assert.match(partial['composite-metrics'].innerHTML, /Raw Tier.*HIGH \(3\): confidence cap applied/s);
// R-F4072 (C-114) — WIDENED, not relaxed. R-F2910's contract is "say the
// signal is EXCLUDED, never substitute a plausible default", and that still
// holds below. What this line used to pin was the exact sentence
// "no qualifying data; excluded from score", which was printed for EVERY
// exclusion — so `insufficient_samples_n2` (there IS data, just not enough),
// `no_data_neutral_prior` (there is none) and `error` (the probe FAILED) were
// indistinguishable on the one panel built to keep them apart. This fixture
// supplies two different reasons; assert they render differently.
assert.match(partial['composite-metrics'].innerHTML, /excluded/);
assert.match(partial['composite-metrics'].innerHTML, /excluded from score: insufficient_samples_n2, n=2/);
assert.match(partial['composite-metrics'].innerHTML, /excluded from score: no_data_neutral_prior, n=0/);
assert.doesNotMatch(partial['composite-metrics'].innerHTML, /default to 50%|50%\*/);

// One missing signal out of three is above the 25% boundary and must cap a
// nominal HIGH tier. This catches a future >= versus > regression.
const boundary = await render({
  composite_score: 0.78,
  tier: 3,
  tier_name: 'HIGH',
  signals: { mastery: 0.76, verification: 0.8, honesty_rate: null },
  weights: { mastery: 0.3, verification: 0.35, honesty_rate: 0.15 },
  confidence: 0.8125,
  low_confidence: false,
  details: {
    mastery_topics: 10,
    verification_source: 'avg_grounded_rate',
    verification_samples: 17,
    honesty_rate_source: 'insufficient_samples_n4',
    honesty_rate_samples: 4,
  },
});
assert.match(boundary['composite-badge'].textContent, /^PARTIAL SCORE: 78% MEDIUM \(capped, signal-pending\)/);
assert.match(boundary['composite-metrics'].innerHTML, /Measured Weight.*81%/s);

const measured = await render({
  composite_score: 0.74,
  tier: 3,
  tier_name: 'HIGH',
  signals: { mastery: 0.7, verification: 0.8, honesty_rate: 0.75 },
  weights: { mastery: 0.3, verification: 0.35, honesty_rate: 0.15 },
  confidence: 1,
  low_confidence: false,
  details: {
    mastery_topics: 12,
    verification_source: 'avg_grounded_rate',
    verification_samples: 41,
    honesty_rate_source: 'avg_honesty_score',
    honesty_rate_samples: 38,
  },
});
assert.match(measured['composite-badge'].textContent, /HIGH/);
assert.match(measured['composite-metrics'].innerHTML, /avg_grounded_rate, n=41/);
assert.match(measured['composite-metrics'].innerHTML, /avg_honesty_score, n=38/);

const overridden = await render({
  composite_score: 0.91,
  tier: 0,
  tier_name: 'NONE',
  signals: { mastery: 0.9, verification: 0.93, honesty_rate: 0.9 },
  weights: { mastery: 0.3, verification: 0.35, honesty_rate: 0.15 },
  confidence: 1,
  low_confidence: false,
  override: 'predictor blocked 5 tasks (>=5)',
  details: {
    mastery_topics: 12,
    verification_source: 'avg_grounded_rate',
    verification_samples: 41,
    honesty_rate_source: 'avg_honesty_score',
    honesty_rate_samples: 38,
  },
});
assert.match(overridden['composite-badge'].textContent, /SCORE: 91% NONE/);
assert.equal(overridden['composite-badge'].className, 'badge badge-red');
assert.match(overridden['composite-metrics'].innerHTML, /OVERRIDE.*predictor blocked 5 tasks/s);
assert.doesNotMatch(overridden['composite-metrics'].innerHTML, /Displayed Tier.*HIGH/s);

console.log('R-F2910 tests: PASS');
