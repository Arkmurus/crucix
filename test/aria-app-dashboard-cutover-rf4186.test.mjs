import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import nextConfig from '../aria-app/next.config.mjs';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

function matchesRewrite(source, pathname) {
  const pattern = source.replace('/:path(', '^/(').replace(/\)$/, ')$');
  return new RegExp(pattern).test(pathname);
}

test('R-F4186: dashboard is native while every unpromoted route retains rollback', async () => {
  const rewrites = await nextConfig.rewrites();
  const proxy = rewrites.beforeFiles[0];

  assert.equal(matchesRewrite(proxy.source, '/dashboard'), false);
  assert.equal(matchesRewrite(proxy.source, '/watchlist'), true);
  assert.equal(matchesRewrite(proxy.source, '/api/aria/dd/reports'), true);
  assert.equal(matchesRewrite(proxy.source, '/api/opportunities'), true);
});

test('R-F4186: dashboard ownership is role-gated and customer-safe', () => {
  const middleware = read('aria-app/middleware.ts');
  const dashboard = read('aria-app/app/(customer)/dashboard/page.tsx');
  const health = read('aria-app/app/health/app/route.ts');

  assert.match(middleware, /CUSTOMER_PREFIXES[^;]*['"]\/dashboard['"]/s);
  assert.doesNotMatch(dashboard, /\/api\/bd-intelligence\/pipeline/);
  assert.doesNotMatch(dashboard, /Active Deals/);
  assert.match(dashboard, /data-aria-surface="next-customer-dashboard"/);
  assert.match(dashboard, /<UnavailableState/);
  assert.match(health, /['"]\/dashboard['"]/);
});
