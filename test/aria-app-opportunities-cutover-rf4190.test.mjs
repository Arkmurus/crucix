import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import nextConfig from '../aria-app/next.config.mjs';
import { normalizeOpportunities } from '../aria-app/lib/opportunities.ts';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');
const matches = (source, path) => new RegExp(source.replace('/:path(', '^/(').replace(/\)$/, ')$')).test(path);

test('R-F4190: opportunities normalize untrusted backend data at one boundary', () => {
  const normalized = normalizeOpportunities({ opportunities: [{
    market: 'Kenya', score: 87, complianceStatus: 'NOT_SCREENED',
    sources: [
      { title: 'Trusted', url: 'https://example.test/tender' },
      { title: 'Script', url: 'javascript:alert(1)' },
      { title: 'Credentials', url: 'https://user:secret@example.test/private' },
      { title: 'Loopback', url: 'http://127.0.0.1:3000/admin' },
      { title: 'Private', url: 'https://192.168.1.5/internal' },
    ],
  }, null, 'bad'] });

  assert.equal(normalized.length, 1);
  assert.equal(normalized[0].market, 'Kenya');
  assert.equal(normalized[0].score, 87);
  assert.deepEqual(normalized[0].sources, [
    { title: 'Trusted', url: 'https://example.test/tender', type: undefined },
  ]);
  assert.deepEqual(normalizeOpportunities({ opportunities: 'not-an-array' }), []);
  assert.equal(normalizeOpportunities([{ market: 'Bad score', score: 999 }])[0].score, undefined);
});

test('R-F4190: opportunities are native, customer-gated, observable, and enabled', async () => {
  const proxy = (await nextConfig.rewrites()).beforeFiles[0];
  assert.equal(matches(proxy.source, '/opportunities'), false);
  assert.equal(matches(proxy.source, '/vault'), true);
  assert.equal(matches(proxy.source, '/api/opportunities'), true);

  const middleware = read('aria-app/middleware.ts');
  const page = read('aria-app/app/(customer)/opportunities/page.tsx');
  const health = read('aria-app/app/health/app/route.ts');
  assert.match(middleware, /CUSTOMER_PREFIXES[^;]*['"]\/opportunities['"]/s);
  assert.match(page, /data-aria-surface="next-customer-opportunities"/);
  assert.match(page, /<UnavailableState/);
  assert.match(page, /<EmptyState/);
  assert.match(health, /['"]\/opportunities['"]/);
});
