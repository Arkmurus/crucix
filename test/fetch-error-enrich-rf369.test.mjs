// R-F369 — fetch error enrichment helper tests.
//
// Mirrors the R-F353 unwrap pattern from apis/sources/intel-feeds.mjs
// (Comtrade) which already produces `fetch_ENOTFOUND` etc. in production.
// The helper is the shared version so 7+ other source catches don't have
// to duplicate the unwrap chain.

import test from 'node:test';
import assert from 'node:assert/strict';
import { enrichFetchError } from '../apis/utils/fetch_error.mjs';

test('unwraps undici TypeError(fetch failed) → cause.code', () => {
  const cause = new Error('getaddrinfo ENOTFOUND example.com');
  cause.code = 'ENOTFOUND';
  cause.errno = -3008;
  const err = new TypeError('fetch failed');
  err.cause = cause;
  assert.equal(enrichFetchError(err), 'ENOTFOUND');
});

test('unwraps connect timeout → UND_ERR_CONNECT_TIMEOUT', () => {
  const cause = new Error('Connect Timeout Error');
  cause.code = 'UND_ERR_CONNECT_TIMEOUT';
  const err = new TypeError('fetch failed');
  err.cause = cause;
  assert.equal(enrichFetchError(err), 'UND_ERR_CONNECT_TIMEOUT');
});

test('falls through to err.code when no cause', () => {
  const err = new Error('connection reset');
  err.code = 'ECONNRESET';
  assert.equal(enrichFetchError(err), 'ECONNRESET');
});

test('falls through to err.name when no code', () => {
  const err = new TypeError('something');
  assert.equal(enrichFetchError(err), 'TypeError');
});

test('falls through to err.message when no name+code', () => {
  // Plain Error with custom name unset is just "Error" — message wins
  // only if name is falsy. The chain order is intentional: name > message
  // because TypeError/AbortError etc. are more diagnostic than the
  // accompanying message text.
  const err = { message: 'HTTP 500' };  // not an Error instance
  assert.equal(enrichFetchError(err), 'HTTP 500');
});

test('R-F553: plain Error("...") prefers message over the bare "Error" name', () => {
  // Pre-R-F553 regression: afdb.mjs throws `new Error("All AfDB endpoints
  // unreachable: rss2json=429 | ...")`. err.name === "Error" sat ahead of
  // err.message in the precedence chain so the helper returned the literal
  // string "Error", producing seenode log noise like `[AfDB] Error: Error`
  // every sweep when all fallbacks failed simultaneously.
  const err = new Error('All AfDB endpoints unreachable: rss2json=429 | jina=451');
  assert.equal(
    enrichFetchError(err),
    'All AfDB endpoints unreachable: rss2json=429 | jina=451',
    'plain Error with descriptive message must surface the message, not the class name',
  );
});

test('R-F553: TypeError still prefers its name (the class IS the diagnostic)', () => {
  // R-F369 rationale: TypeError/AbortError class names are more
  // informative than their generic message text. R-F553 must NOT regress
  // this — only the bare "Error" sentinel is demoted.
  const err = new TypeError('Cannot read property');
  assert.equal(enrichFetchError(err), 'TypeError');
});

test('handles null/undefined gracefully', () => {
  assert.equal(enrichFetchError(null), 'unknown');
  assert.equal(enrichFetchError(undefined), 'unknown');
});

test('truncates very long messages', () => {
  const err = { message: 'x'.repeat(500) };
  const result = enrichFetchError(err);
  assert.ok(result.length <= 80, `expected ≤80 chars, got ${result.length}`);
});

test('handles err.cause that is not an object', () => {
  // Defensive: some libraries set err.cause to a string.
  const err = new Error('outer');
  err.cause = 'inner cause string';
  // Should NOT crash; falls through to err.name ("Error") or message.
  const result = enrichFetchError(err);
  assert.ok(['Error', 'outer'].includes(result));
});
