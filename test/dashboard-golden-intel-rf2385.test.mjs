// R-F2385 — dashboard Golden Intel panel.
//
// Drives the real dashboard inline JavaScript with a promoted intel-signal
// fixture. This protects the business-facing contract: dashboard.html must show
// decision-grade signals clearly before the raw news/audit feed.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

for (const marker of [
  'id="golden-intel-card"',
  'id="golden-intel-list"',
  '/api/aria/intel/signals/recent?limit=20',
  'renderGoldenIntel',
  'why_it_matters',
  'recommended_action',
  // R-F3344 — was 'quality_label', a free-text muted badge ("decision-grade
  // single-source"). R-F2890..R-F2896 replaced it with a two-value grade whose
  // meaning is stated: GRADE A is official primary evidence or independent
  // corroboration; GRADE B is explicitly single-source awaiting corroboration.
  // Free text let a signal describe its own quality; the grade does not.
  'intelGradeBadge',
  'intel_grade',
  'confidence_rationale',
  'action_horizon',
  'corroboration',
  // R-F3344 — were 'customerValueScore' / 'customerValueHardRejections', a
  // numeric heuristic (>= 70 wins, a reject list loses). R-F2890..R-F2896
  // replaced it with a STRUCTURAL gate: an item is customer-visible only if it
  // has a grade, an allowed signal type, a decision summary, why_it_matters, a
  // recommended action and a real evidence URL, and is not backfilled. A score
  // can be met by an item missing the fields that make it actionable; requiring
  // the fields cannot.
  'isCustomerVisibleIntel',
  'GOLDEN_DISTRIBUTION_TYPES',
  'signalEvidenceUrl',
]) {
  assert.ok(HTML.includes(marker), `dashboard must include ${marker}`);
}

const scripts = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map((m) => m[1])
  .filter((s) => s.trim());
const CODE = scripts[scripts.length - 1];

const INTEL = {
  schema_version: 'rf2385.v1',
  // R-F3344 — the FEED-level verdict, required since R-F2554/R-F2896: a stale or
  // fully backfilled feed publishes nothing, so both grade columns render empty
  // regardless of the signals. Omitting it (as this fixture did) left the header
  // saying "Customer-visible changes: 1" beside two empty columns, which is what
  // made this look like a renderer regression.
  freshness: { publishable: true, stale: false, backfilled: false },
  signals: [{
    signal_type: 'active_tender',
    priority: 'HIGH',
    confidence: 'HIGH',
    // R-F3344 — the fixture now carries the shape the CURRENT gate reads.
    // `quality_label` (free text) became `intel_grade` A/B, and the evidence URL
    // moved into an `evidence` object that signalEvidenceUrl() validates as an
    // http(s) URL. A fixture describing a signal the pipeline no longer produces
    // tests nothing: isCustomerVisibleIntel() rejected it, so the renderer had
    // nothing to render and the assertion blamed the renderer.
    intel_grade: 'B',
    evidence: { url: 'https://example.com/angola-tender', source_tier: 'tier_1b' },
    confidence_rationale: 'high-trust source tier; actionable active tender pattern; named entity extracted; single-source',
    action_horizon: '0-72h',
    corroboration: 'single-source',
    evidence_count: 1,
    decision_summary: 'Angola launches armoured vehicle tender',
    why_it_matters: 'Procurement activity may create a near-term commercial window.',
    recommended_action: 'Qualify opportunity',
    target: 'Angola',
    source: 'US DoD Daily Contracts',
    source_tier: 'tier_1b',
    url: 'https://example.com/angola-tender',
    detected_at: '2026-07-07T10:00:00Z',
    customer_value: {
      score: 75,
      segments: ['procurement_team'],
      problems: ['bid_opportunity'],
      aria_added: ['procurement_implication'],
      rejection_reasons: ['customer_value_below_telegram_threshold'],
      distribution_ready: true,
      telegram_ready: false,
    },
  }],
};

const els = new Map();
const makeEl = () => ({
  textContent: '',
  innerHTML: '',
  title: '',
  value: '',
  dataset: {},
  style: {},
  addEventListener() {},
  removeEventListener() {},
  focus() {},
  appendChild() {},
  querySelectorAll() { return []; },
  querySelector() { return makeEl(); },
});
const getEl = (id) => {
  if (!els.has(id)) els.set(id, makeEl());
  return els.get(id);
};

const bodyFor = (path) => {
  if (path.includes('/api/aria/dd/reports')) return { reports: [] };
  if (path.includes('/api/aria/dd/watchlist')) return { watchlist: [] };
  if (path.includes('/api/bd-intelligence/pipeline')) return [];
  if (path.includes('/api/opportunities')) return { opportunities: [] };
  if (path.includes('/api/aria/intel/signals/recent')) return INTEL;
  if (path.includes('/api/aria/user/sources')) return { sources: [] };
  return {};
};

const sandbox = {
  console,
  Date,
  Promise,
  Math,
  JSON,
  Array,
  Object,
  String,
  Number,
  RegExp,
  Set,
  encodeURIComponent,
  setTimeout,
  clearTimeout,
  setInterval: () => 0,
  clearInterval: () => {},
  confirm: () => false,
  alert: () => {},
  Auth: { requireAuth() {} },
  Sidebar: { init() {} },
  Toast: { show() {} },
  escHtml: (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c])),
  truncate: (s, n) => String(s == null ? '' : s).slice(0, n),
  API: {
    BASE: '',
    headers: () => ({}),
    get: async (p) => (p === '/api/data'
      ? { correlations: [], bdIntelligence: { counts: {} }, tg: { urgent: [] } }
      : p === '/api/health'
        ? { sourcesOk: 1, sourcesTotal: 1 }
        : null),
    post: async () => ({ ok: true, data: {} }),
  },
  fetch: async (url) => ({ ok: true, status: 200, json: async () => bodyFor(String(url)) }),
  document: {
    getElementById: getEl,
    querySelector: () => makeEl(),
    querySelectorAll: () => [],
    addEventListener() {},
    hidden: false,
  },
};
sandbox.window = sandbox;

const ctx = vm.createContext(sandbox);
vm.runInContext(CODE, ctx, { filename: 'dashboard-inline.js' });
await ctx.loadData();

const html = getEl('golden-intel-list').innerHTML;
assert.match(html, /Angola launches armoured vehicle tender/);
assert.match(html, /HIGH/);
assert.match(html, /HIGH confidence/);
assert.match(html, /Procurement activity may create a near-term commercial window/);
assert.match(html, /Qualify opportunity/);
// R-F3344 — was /decision-grade single-source/, the free-text quality_label.
// The two-value grade replaced it: a signal can no longer describe its own
// quality in prose.
assert.match(html, /GRADE B · SINGLE SOURCE/);
assert.match(html, /Horizon:/);
assert.match(html, /0-72h/);
assert.match(html, /Evidence:/);
assert.match(html, /single-source/);
assert.match(html, /high-trust source tier/);
assert.match(html, /Evidence/);
assert.match(html, /tier_1b/);
// R-F3344 — was /Customer value 75/. The numeric customer-value score was
// removed with the heuristic that produced it (R-F2890..R-F2896); the gate is
// now structural. Asserted ABSENT so the score cannot quietly return as a
// user-facing number without the gate behind it.
assert.ok(!/Customer value \d/.test(html),
  'the removed customer-value score must not reappear in the rendered item');

console.log('PASS');
