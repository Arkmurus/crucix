import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import nextConfig from '../aria-app/next.config.mjs';
import { submitDD } from '../aria-app/lib/dd-submission.ts';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

function matchesRewrite(source, pathname) {
  const pattern = source.replace('/:path(', '^/(').replace(/\)$/, ')$');
  return new RegExp(pattern).test(pathname);
}

test('R-F4188: report list and detail are native while unrelated routes retain rollback', async () => {
  const rewrites = await nextConfig.rewrites();
  const proxy = rewrites.beforeFiles[0];

  assert.equal(matchesRewrite(proxy.source, '/reports'), false);
  assert.equal(matchesRewrite(proxy.source, '/reports/dd_123'), false);
  assert.equal(matchesRewrite(proxy.source, '/opportunities'), true);
  assert.equal(matchesRewrite(proxy.source, '/api/aria/dd/reports'), true);
});

test('R-F4188: the real DD submitter requests async work and reports only verified outcomes', async () => {
  let observedBody;
  const started = await submitDD(async (_path, init) => {
    observedBody = JSON.parse(String(init?.body));
    return { run_id: 'dd_rf4188', status: 'running', async_mode: true };
  }, { name: 'Acme Ltd', jurisdiction: 'GB', mode: 'deep' });

  assert.deepEqual(observedBody, {
    name: 'Acme Ltd', jurisdiction: 'GB', mode: 'deep', type: 'company', async_mode: true,
  });
  assert.deepEqual(started, {
    status: 'started',
    message: 'Due diligence started for Acme Ltd.',
    runId: 'dd_rf4188',
  });

  const malformed = await submitDD(async () => ({ status: 'running' }), {
    name: 'Acme Ltd', jurisdiction: '', mode: 'standard',
  });
  assert.equal(malformed.status, 'error');

  const failed = await submitDD(async () => { throw new Error('backend down'); }, {
    name: 'Acme Ltd', jurisdiction: '', mode: 'standard',
  });
  assert.equal(failed.status, 'error');
  assert.doesNotMatch(failed.message, /backend down/);
});

test('R-F4188: reports are role-gated and surface mutation and placeholder states', () => {
  const middleware = read('aria-app/middleware.ts');
  const reports = read('aria-app/app/(customer)/reports/page.tsx');
  const detail = read('aria-app/app/(customer)/reports/[runId]/page.tsx');
  const form = read('aria-app/components/dd-run-form.tsx');
  const health = read('aria-app/app/health/app/route.ts');

  assert.match(middleware, /CUSTOMER_PREFIXES[^;]*['"]\/reports['"]/s);
  assert.match(reports, /data-aria-surface="next-customer-reports"/);
  assert.match(detail, /data-aria-surface="next-customer-report-detail"/);
  assert.match(form, /useActionState\(runDD/);
  assert.match(form, /role="status"/);
  assert.match(reports, /status === 'running'/);
  assert.match(reports, /status === 'failed'/);
  assert.match(health, /['"]\/reports['"]/);
});
