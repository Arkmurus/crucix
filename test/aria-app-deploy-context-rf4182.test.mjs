import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

test('R-F4182: deploy contract uses aria-app context and exposes the exact build', () => {
  const readme = read('aria-app/README.md');
  const fly = read('aria-app/fly.app.toml');
  const dockerfile = read('aria-app/Dockerfile.app');
  const rewrites = read('aria-app/next.config.mjs');
  const health = read('aria-app/app/health/app/route.ts');

  assert.match(readme, /flyctl deploy aria-app --config aria-app\/fly\.app\.toml/);
  assert.match(fly, /Deploy: flyctl deploy aria-app --config aria-app\/fly\.app\.toml/);
  assert.match(dockerfile, /ARG ARIA_BUILD_GIT_SHA/);
  assert.match(dockerfile, /ARIA_BUILD_GIT_SHA=\$ARIA_BUILD_GIT_SHA/);
  assert.match(rewrites, /health\/app/);
  assert.match(health, /process\.env\.ARIA_BUILD_GIT_SHA/);
  assert.match(health, /build_rev/);
});
