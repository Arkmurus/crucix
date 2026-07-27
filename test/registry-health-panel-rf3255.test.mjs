/**
 * R-F3255 — the Sources page must RENDER the registry reliability EMA, and must
 * keep "never measured" visually distinct from both healthy and failing.
 *
 * /api/aria/source_validator/health has been accumulating a per-family EMA that no
 * page ever read — producer with no consumer, the R-F2693 / R-F3177 class.
 *
 * These tests EXECUTE the real loader out of the shipped HTML against a mock API
 * and assert the DOM it produces. A regex over the source would only prove the
 * markup exists, not that the path renders it — that is the UI-unverified-claim
 * failure this repo has been bitten by before.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const page = readFileSync('public/sources.html', 'utf8');

// ── extract the real function from the shipped page ──────────────────────────
const START = 'async function loadRegistryHealth()';
const END = '\nloadBridge();';
const startIdx = page.indexOf(START);
assert.ok(startIdx > -1, 'loadRegistryHealth() is not in the shipped page');
const fnSrc = page.slice(startIdx, page.indexOf(END, startIdx));

function harness(apiResponse, { throwIt = false } = {}) {
  const els = new Map();
  const document = {
    getElementById(id) {
      if (!els.has(id)) els.set(id, { id, textContent: '', innerHTML: '' });
      return els.get(id);
    },
  };
  const API = {
    async get() {
      if (throwIt) throw new Error('boom');
      return apiResponse;
    },
  };
  const escHtml = (s) =>
    String(s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // eslint-disable-next-line no-new-func
  const make = new Function('document', 'API', 'escHtml',
    `${fnSrc}; return loadRegistryHealth;`);
  return { run: make(document, API, escHtml), els };
}

const UNMEASURED_ONLY = {
  total_sources: 1,
  healthy_count: 0, degraded_count: 0, failing_count: 0, dead_count: 0,
  unmeasured_count: 1,
  top_performers: [], degraded: [], failing: [], dead: [],
  unmeasured: [{ family: 'never.example', tier: '1b', overall_health: null, samples: 0, topics: 2 }],
};

test('R-F3255 an unmeasured family renders as Unmeasured — not healthy, not failing', async () => {
  const { run, els } = harness(UNMEASURED_ONLY);
  await run();
  const rows = els.get('reghealth-body').innerHTML;

  assert.match(rows, /never\.example/);
  assert.match(rows, />Unmeasured</, 'the row must be labelled Unmeasured');
  assert.match(rows, /not measured/, 'it must not display a fabricated score');
  assert.doesNotMatch(rows, /pysrc-badge-ok/, 'unmeasured must never render as healthy');
  assert.doesNotMatch(rows, /pysrc-badge-err/, 'unmeasured must never render as failing/dead');
  assert.doesNotMatch(rows, /0\.50/, 'the 0.5 prior must never be shown as a measurement');
  assert.equal(els.get('reghealth-unmeasured').textContent, 1);
  assert.equal(els.get('reghealth-failing').textContent, 0);
});

test('R-F3255 a measured family still renders its score and a healthy badge', async () => {
  const { run, els } = harness({
    total_sources: 1,
    healthy_count: 1, degraded_count: 0, failing_count: 0, dead_count: 0,
    unmeasured_count: 0,
    top_performers: [{ family: 'gov.uk', tier: '1a', overall_health: 0.93, samples: 4, topics: 3 }],
    degraded: [], failing: [], dead: [], unmeasured: [],
  });
  await run();
  const rows = els.get('reghealth-body').innerHTML;

  assert.match(rows, /gov\.uk/);
  assert.match(rows, /pysrc-badge-ok/);
  assert.match(rows, /0\.93/);
  assert.equal(els.get('reghealth-total').textContent, '1 families');
});

test('R-F3255 an older server with no unmeasured bucket shows ? — never 0', async () => {
  // Deploy skew is real: aria-web ships independently of aria-intel. Rendering 0
  // would assert "no source is unmeasured" on a build that cannot know.
  const { run, els } = harness({
    total_sources: 2,
    healthy_count: 1, degraded_count: 0, failing_count: 1, dead_count: 0,
    top_performers: [{ family: 'gov.uk', tier: '1a', overall_health: 0.93, samples: 4 }],
    degraded: [], failing: [], dead: [],
  });
  await run();
  assert.equal(els.get('reghealth-unmeasured').textContent, '?');
});

test('R-F3255 an unreachable endpoint reports unknown, never a clean reading', async () => {
  for (const opts of [[null, {}], [undefined, { throwIt: true }]]) {
    const { run, els } = harness(opts[0], opts[1]);
    await run();
    assert.equal(els.get('reghealth-healthy').textContent, '?');
    assert.equal(els.get('reghealth-unmeasured').textContent, '?');
    assert.equal(els.get('reghealth-total').textContent, 'unavailable');
    assert.match(els.get('reghealth-body').innerHTML, /NOT a clean reading|unreachable/);
  }
});

test('R-F3255 the loader is actually wired into page start-up and refresh', () => {
  const after = page.slice(page.indexOf(END));
  assert.match(after, /loadRegistryHealth\(\);/, 'never called at start-up');
  const refresh = page.slice(page.indexOf('setInterval(() => {'));
  assert.match(refresh, /loadRegistryHealth\(\);/, 'not refreshed with the other panels');
});

test('R-F3255 the panel exists with all five buckets, unmeasured included', () => {
  for (const id of ['healthy', 'degraded', 'failing', 'dead', 'unmeasured']) {
    assert.match(page, new RegExp(`id="reghealth-${id}"`), `missing bucket: ${id}`);
  }
  assert.match(page, /\/api\/aria\/source_validator\/health/);
  assert.match(page, /it is <strong>not<\/strong> healthy and <strong>not<\/strong> failing/);
});
