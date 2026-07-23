// R-F2910 — capability test for honest composite rendering.
//
// Drives the real loadComposite function extracted from aria-brain.html.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

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
  vm.runInContext(`${source}; this.runLoader = loadComposite;`, context);
  await context.runLoader();
  return elements;
}

console.log('R-F2910 aria-brain composite provenance tests');

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
assert.match(partial['composite-badge'].textContent, /MEDIUM \(capped, signal-pending\)/);
assert.match(partial['composite-metrics'].innerHTML, /Measured Weight.*38%/s);
assert.match(partial['composite-metrics'].innerHTML, /excluded.*no qualifying data; excluded from score/s);
assert.doesNotMatch(partial['composite-metrics'].innerHTML, /default to 50%|50%\*/);

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

console.log('R-F2910 tests: PASS');
