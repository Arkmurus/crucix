import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import nextConfig from '../aria-app/next.config.mjs';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

function matchesRewrite(source, pathname) {
  const pattern = source.replace('/:path(', '^/(').replace(/\)$/, ')$');
  return new RegExp(pattern).test(pathname);
}

test('R-F4184: the real strangler rewrite owns sign-in but preserves rollback', async () => {
  const rewrites = await nextConfig.rewrites();
  const proxy = rewrites.beforeFiles[0];

  assert.equal(matchesRewrite(proxy.source, '/signin'), false);
  assert.equal(matchesRewrite(proxy.source, '/api/session'), false);
  assert.equal(matchesRewrite(proxy.source, '/health/app'), false);
  assert.equal(matchesRewrite(proxy.source, '/preview'), false);
  assert.equal(matchesRewrite(proxy.source, '/preview/bd'), false);

  assert.equal(matchesRewrite(proxy.source, '/dashboard'), true);
  assert.equal(matchesRewrite(proxy.source, '/api/auth/login'), true);
  assert.equal(matchesRewrite(proxy.source, '/api/aria/health'), true);
  assert.equal(matchesRewrite(proxy.source, '/legal/privacy'), true);
});

test('R-F4184: promoted sign-in is identifiable and session failures stay honest', () => {
  const signin = read('aria-app/app/signin/page.tsx');
  const session = read('aria-app/app/api/session/route.ts');

  assert.match(signin, /data-aria-surface="next-signin"/);
  assert.match(session, /error: 'bad_request'.*status: 400/s);
  assert.match(session, /error: 'missing_token'.*status: 400/s);
  assert.match(session, /httpOnly: true/);
  assert.match(session, /sameSite: 'lax'/);
  assert.match(session, /secure: process\.env\.NODE_ENV === 'production'/);
});
