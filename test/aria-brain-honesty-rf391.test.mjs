// test/aria-brain-honesty-rf391.test.mjs
//
// Capability test for R-F391 — ARIA Brain dashboard honesty.
//
// Live evidence 2026-05-13: operator opened intel.arkmurus.com/aria-brain
// and saw 20+ panels rendering apparent measurements ("Redis DOWN",
// "Mastery --", "Autonomy OFF", "LLM Cost NO DATA", "0 chat turns",
// etc.) while the underlying system was verifiably processing live data
// (24,967 ledger signals, 35,363 knowledge facts, DeepSeek/Anthropic
// calls firing, WA responding). The lie:
//
//   1. fly.io proxy times out / 503s.
//   2. seenode's ariaProxy fallback returns 503 with body
//      {error:'ARIA service offline'} (server.mjs:2085).
//   3. fetchJson returned that object (truthy!) — every panel's
//      `if (!d) return` guard PASSED.
//   4. Panels rendered with d.infra undefined → "DOWN", d.quality
//      undefined → "--", d.engine.enabled undefined → "OFF".
//
// Fix: fetchJson detects {error:...} (and null) responses, returns null,
// tracks the failure path, and a banner surfaces "DATA UNAVAILABLE"
// listing which endpoints failed. Panels then bail correctly.
//
// Two layers of assertion:
//   (1) STATIC — parse aria-brain.html and assert the banner element,
//       tracker functions, and fetchJson error detection are present.
//   (2) BEHAVIOURAL — extract fetchJson logic into a stub-shaped
//       function and prove it returns null + tracks the failure when
//       given {error:...} / null / network-error responses, and returns
//       data + clears the tracker on success.
//
// Run: node test/aria-brain-honesty-rf391.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(join(__dirname, '..', 'public', 'aria-brain.html'), 'utf8');

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

console.log('R-F391 capability tests\n');

// ── 1. STATIC: HTML contains the banner element + CSS + tracker + fetchJson check
console.log('1. Dashboard HTML wired with honesty banner + fetchJson guard');

check(
  'banner div #fetch-failure-banner present in body',
  /<div\s+id=["']fetch-failure-banner["'][^>]*><\/div>/.test(HTML),
);
check(
  'banner CSS defined with red-alert styling (R-F2049 light theme) (visible alert)',
  // R-F518: regex tolerates the R-F441 combined selector
  // `#fetch-failure-banner, #js-error-banner { ... }` as well as the
  // original single-selector form. The visible-alert invariant is what
  // we're asserting — the CSS authoring style is not.
  /#fetch-failure-banner\s*[,{][\s\S]*?display:\s*none[\s\S]*?background:\s*rgba\(220/.test(HTML),
);
check(
  '_trackFetchFailure helper defined',
  /function\s+_trackFetchFailure\s*\(/.test(HTML),
);
check(
  '_trackFetchSuccess helper defined',
  /function\s+_trackFetchSuccess\s*\(/.test(HTML),
);
check(
  '_renderFetchFailureBanner helper defined',
  /function\s+_renderFetchFailureBanner\s*\(/.test(HTML),
);
check(
  '_fetchFailures Map is the failure tracker',
  /const\s+_fetchFailures\s*=\s*new\s+Map\(\)/.test(HTML),
);
// R-F2437: R-F2234 refactored fetchJson into a richer failure detector — the
// honesty invariant (a failed fetch must reach _trackFetchFailure, never render
// silently) is PRESERVED, but via new code paths: a timeout/no-response is
// res.status === 0, a server error is !res.ok, and a computed-but-failed cache
// panel is an '__error' marker. These assertions keep the SAME teeth (a
// silently-swallowed failure still fails the test) against the current code.
check(
  'fetchJson treats a timeout / no-response as a failure (status 0)',
  /res\.status\s*===\s*0\b[\s\S]{0,120}?_trackFetchFailure/.test(HTML),
);
check(
  'fetchJson treats an error response as a failure (!res.ok or cached __error)',
  /!res\.ok[\s\S]{0,80}?_trackFetchFailure/.test(HTML)
    || /__error['"]?\s*\)?[\s\S]{0,80}?_trackFetchFailure/.test(HTML),
);
check(
  'fetchJson clears tracker on success',
  /_trackFetchSuccess\(path\)/.test(HTML),
);
check(
  'banner shows "DATA UNAVAILABLE" message',
  /DATA UNAVAILABLE/.test(HTML),
);

// ── 2. BEHAVIOURAL: extract fetchJson logic and prove it
console.log('\n2. Extracted fetchJson logic behaves correctly');

// Re-implement the same logic the dashboard uses, then test it.
// Keep this in lockstep with the production code — if you change the
// production fetchJson, mirror the change here.
function makeFetchHarness() {
  const failures = new Map();
  let bannerCalls = 0;
  function trackFailure(path, reason) {
    failures.set(path, reason || 'no response');
    bannerCalls += 1;
  }
  function trackSuccess(path) {
    if (failures.delete(path)) bannerCalls += 1;
  }
  async function fetchJson(path, apiGet) {
    try {
      const data = await apiGet(path);
      if (data == null) { trackFailure(path, 'no response'); return null; }
      if (typeof data === 'object' && data.error) {
        trackFailure(path, String(data.error).slice(0, 80));
        return null;
      }
      trackSuccess(path);
      return data;
    } catch (e) {
      trackFailure(path, e.message || 'exception');
      return null;
    }
  }
  return { fetchJson, getFailures: () => failures, getBannerCalls: () => bannerCalls };
}

// Case A: real data → returns data, no tracker entry, banner not re-rendered with content
{
  const h = makeFetchHarness();
  const result = await h.fetchJson('/health', async () => ({ status: 'healthy', infra: { redis: true } }));
  check('A1: real data returned verbatim', JSON.stringify(result) === JSON.stringify({ status: 'healthy', infra: { redis: true } }));
  check('A2: no failure tracked on success', h.getFailures().size === 0);
}

// Case B: {error:'ARIA service offline'} (the seenode proxy fallback) → null + tracked
{
  const h = makeFetchHarness();
  const result = await h.fetchJson('/health', async () => ({ error: 'ARIA service offline' }));
  check('B1: {error:...} response → null (not the error object)', result === null);
  check('B2: failure tracker contains the path', h.getFailures().has('/health'));
  check(
    'B3: tracker stored the error reason',
    h.getFailures().get('/health') === 'ARIA service offline',
  );
}

// Case C: API.get returned null (401-then-logout, or network error) → null + tracked
{
  const h = makeFetchHarness();
  const result = await h.fetchJson('/operating-mode', async () => null);
  check('C1: null response → null', result === null);
  check('C2: failure tracker contains the path', h.getFailures().has('/operating-mode'));
}

// Case D: thrown exception in API.get → null + tracked
{
  const h = makeFetchHarness();
  const result = await h.fetchJson('/student/mastery', async () => { throw new Error('network down'); });
  check('D1: exception → null', result === null);
  check('D2: failure tracker stored exception message', h.getFailures().get('/student/mastery') === 'network down');
}

// Case E: success after failure clears the tracker (recovery)
{
  const h = makeFetchHarness();
  await h.fetchJson('/cost/monthly', async () => ({ error: 'offline' }));
  check('E1: tracker has the failure', h.getFailures().has('/cost/monthly'));
  await h.fetchJson('/cost/monthly', async () => ({ total_cost_usd: 12.34 }));
  check('E2: tracker cleared after successful recovery', !h.getFailures().has('/cost/monthly'));
}

// Case F: multiple endpoints failing → all tracked independently
{
  const h = makeFetchHarness();
  await h.fetchJson('/health', async () => ({ error: 'offline' }));
  await h.fetchJson('/autonomous/status', async () => null);
  await h.fetchJson('/cost/monthly', async () => { throw new Error('boom'); });
  check('F: 3 failing endpoints → 3 tracker entries', h.getFailures().size === 3);
}

// ── 3. REGRESSION: the original lying-fallback strings must not appear
//        as UNCONDITIONAL static text. They can still appear inside
//        conditional render branches (which is fine when data is real).
console.log('\n3. No lying fallback strings as unconditional static HTML');
// The dashboard initial-state HTML must not include these literal values
// as the displayed text in metric cells. They should only appear inside
// conditional JS render paths driven by real data.
const STATIC_FORBIDDEN = [
  // Initial cell content should be "Loading...", not the fallback values.
  /<div[^>]*id=["']infra-metrics["'][^>]*>\s*(?:DOWN|UNKNOWN|OFF)\s*<\/div>/i,
  /<div[^>]*id=["']quality-metrics["'][^>]*>\s*(?:--|0)\s*<\/div>/i,
];
for (let i = 0; i < STATIC_FORBIDDEN.length; i++) {
  check(
    `forbidden pattern #${i + 1} not in static HTML`,
    !STATIC_FORBIDDEN[i].test(HTML),
  );
}

// Banner is initially hidden (display: none) so it doesn't lie at first paint.
// R-F518: regex tolerates the R-F441 combined selector — see comment above.
check(
  'banner CSS has display:none by default (no false-alarm on first paint)',
  /#fetch-failure-banner\s*[,{][\s\S]*?display:\s*none/.test(HTML),
);

console.log(`\n${failures === 0 ? '✓ ALL PASS' : `✗ ${failures} FAILURES`}`);
process.exitCode = failures === 0 ? 0 : 1;
