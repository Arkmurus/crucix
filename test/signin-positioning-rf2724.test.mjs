import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const signinHtml = readFileSync(new URL('../public/signin.html', import.meta.url), 'utf8');

test('R-F2724 sign-in hero uses the security and defence positioning', () => {
  assert.match(
    signinHtml,
    /<h2 class="auth-tagline">Security and Defence Intelligence\.<br><span>Precision\. Edge\.<\/span><\/h2>/,
  );
  assert.doesNotMatch(signinHtml, /Defense Intelligence\./);
});
