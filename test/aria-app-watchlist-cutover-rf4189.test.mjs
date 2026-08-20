import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import nextConfig from '../aria-app/next.config.mjs';
import { performWatchlistMutation } from '../aria-app/lib/watchlist-mutation.ts';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');
const matches = (source, path) => new RegExp(source.replace('/:path(', '^/(').replace(/\)$/, ')$')).test(path);

test('R-F4189: all real watchlist mutation branches are explicit', async () => {
  const calls = [];
  const request = async (path, init) => { calls.push([path, init]); return { ok: true }; };
  assert.equal((await performWatchlistMutation(request, 'add', { name: 'Acme' })).status, 'success');
  assert.equal((await performWatchlistMutation(request, 'remove', { name: 'Acme' })).status, 'success');
  assert.equal((await performWatchlistMutation(request, 'rescreen')).status, 'success');
  assert.equal((await performWatchlistMutation(async () => { throw new Error('down'); }, 'add', { name: 'Acme' })).status, 'error');
  assert.deepEqual(await performWatchlistMutation(request, 'remove', { name: '  ' }), {
    status: 'error', message: 'An entity name is required.',
  });
  assert.equal(calls.length, 3);
  assert.deepEqual(calls, [
    ['/api/aria/dd/watchlist', { method: 'POST', body: '{"name":"Acme"}' }],
    ['/api/aria/dd/watchlist/Acme', { method: 'DELETE' }],
    ['/api/aria/dd/watchlist/rescreen', { method: 'POST', body: '{}' }],
  ]);
});

test('R-F4189: watchlist is native, role-gated, and visibly wired', async () => {
  const proxy = (await nextConfig.rewrites()).beforeFiles[0];
  assert.equal(matches(proxy.source, '/watchlist'), false);
  assert.equal(matches(proxy.source, '/chat'), true);
  assert.match(read('aria-app/middleware.ts'), /CUSTOMER_PREFIXES[^;]*['"]\/watchlist['"]/s);
  assert.match(read('aria-app/app/(customer)/watchlist/page.tsx'), /data-aria-surface="next-customer-watchlist"/);
  assert.match(read('aria-app/components/watchlist-actions.tsx'), /role="status"/);
  assert.match(read('aria-app/lib/actions.ts'), /result\.status === 'success'[^\n]*revalidatePath\('\/watchlist'\)/);
  assert.match(read('aria-app/app/health/app/route.ts'), /['"]\/watchlist['"]/);
});
