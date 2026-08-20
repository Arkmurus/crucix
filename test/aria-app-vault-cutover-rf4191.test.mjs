import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import nextConfig from '../aria-app/next.config.mjs';
import { normalizeUserSources, performSourceMutation } from '../aria-app/lib/source-vault.ts';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');
const matches = (source, path) => new RegExp(source.replace('/:path(', '^/(').replace(/\)$/, ')$')).test(path);

test('R-F4191: customer source mutations report verified semantic outcomes', async () => {
  const calls = [];
  const request = async (path, init) => {
    calls.push([path, init]);
    return path.endsWith('/source-1')
      ? { success: true, deleted: 'source-1' }
      : { success: true, entry: { site_id: 'source-1' }, verified: false };
  };
  assert.equal((await performSourceMutation(request, 'add', {
    name: 'Reuters', url: 'https://reuters.com/feed', siteType: 'rss', notes: '',
  })).status, 'success');
  assert.equal((await performSourceMutation(request, 'remove', { siteId: 'source-1' })).status, 'success');
  assert.equal((await performSourceMutation(async () => ({ success: false, error: 'limit reached' }), 'add', {
    name: 'Reuters', url: 'https://reuters.com/feed',
  })).status, 'error');
  assert.equal((await performSourceMutation(async () => { throw new Error('secret'); }, 'remove', {
    siteId: 'source-1',
  })).status, 'error');
  assert.equal((await performSourceMutation(request, 'add', {
    name: 'X'.repeat(161), url: 'https://example.test/feed',
  })).status, 'error');
  assert.deepEqual(calls.map(([path]) => path), [
    '/api/aria/user/sources', '/api/aria/user/sources/source-1',
  ]);
});

test('R-F4191: customer source records are bounded and safe to render', () => {
  const sources = normalizeUserSources({ sources: [{
    site_id: 'source-1', site_name: 'Reuters', site_url: 'https://reuters.com/feed',
    status: 'verified', agent_id: 'user:other-user',
  }, { site_id: 'bad', site_url: 'javascript:alert(1)' }, null] });
  assert.deepEqual(sources, [{
    siteId: 'source-1', name: 'Reuters', url: 'https://reuters.com/feed',
    siteType: undefined, status: 'verified', createdAt: undefined,
    updatedAt: undefined, lastVerifiedAt: undefined,
  }]);
});

test('R-F4191: vault is native, tenant-scoped, observable, and enabled', async () => {
  const proxy = (await nextConfig.rewrites()).beforeFiles[0];
  assert.equal(matches(proxy.source, '/vault'), false);
  assert.equal(matches(proxy.source, '/chat'), true);
  assert.equal(matches(proxy.source, '/api/aria/user/sources'), true);
  const page = read('aria-app/app/(customer)/vault/page.tsx');
  assert.match(read('aria-app/middleware.ts'), /CUSTOMER_PREFIXES[^;]*['"]\/vault['"]/s);
  assert.match(page, /['"]\/api\/aria\/user\/sources['"]/);
  assert.doesNotMatch(page, /['"]\/api\/aria\/vault/);
  assert.match(page, /data-aria-surface="next-customer-vault"/);
  assert.match(read('aria-app/components/source-vault-actions.tsx'), /role="status"/);
  assert.match(read('aria-app/app/health/app/route.ts'), /['"]\/vault['"]/);
});
