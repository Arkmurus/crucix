// R-F4180 capability guard: the Next.js customer surface must never render a
// failed API read as a genuine zero/empty business result.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

const dashboard = read('aria-app/app/(customer)/dashboard/page.tsx');
const reports = read('aria-app/app/(customer)/reports/page.tsx');
const watchlist = read('aria-app/app/(customer)/watchlist/page.tsx');
const opportunities = read('aria-app/app/(customer)/opportunities/page.tsx');
const vault = read('aria-app/app/(customer)/vault/page.tsx');
const reportDetail = read('aria-app/app/(customer)/reports/[runId]/page.tsx');
const states = read('aria-app/components/page-header.tsx');

assert.match(states, /export function UnavailableState/,
  'customer pages need one shared, visibly distinct unavailable state');
assert.match(states, /could not be verified|couldn.t be loaded/i,
  'the unavailable state must say that ARIA could not verify the data');

for (const [name, source] of Object.entries({ reports, watchlist, opportunities, vault, reportDetail })) {
  assert.match(source, /\berror\b/, `${name} must retain the API error signal`);
  assert.match(source, /<UnavailableState/, `${name} must render failure separately from empty`);
  assert.match(source, /<EmptyState/, `${name} must preserve the genuine successful-empty state`);
}

assert.match(dashboard, /reportsR\.error/,
  'dashboard report count must retain its independent API error');
assert.match(dashboard, /watchR\.error/,
  'dashboard watchlist count must retain its independent API error');
assert.match(dashboard, /oppsR\.error/,
  'dashboard opportunities count must retain its independent API error');
assert.match(dashboard, /value:\s*[^,]+,\s*unavailable:/,
  'dashboard KPI cards must carry availability beside their value');
assert.match(dashboard, /k\.unavailable\s*\?\s*['"][—-]['"]/,
  'an unavailable KPI must render a dash, never a fabricated zero');
assert.match(dashboard, /<UnavailableState/,
  'recent reports must distinguish unavailable from genuinely empty');

console.log('R-F4180 capability: failed reads cannot masquerade as empty customer data');
