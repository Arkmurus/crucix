// R-F554 — proxy-attempt tag formatter.
//
// Pre-R-F554 every proxy attempt logged just the HTTP status
// (`codetabs=200`). When the body was empty or an HTML rejection page
// that parseRSS couldn't extract from, the source still reported
// "failed" but the operator couldn't tell whether codetabs was up and
// serving junk, or down. The formatter below makes those two states
// grep-distinguishable.

import test from 'node:test';
import assert from 'node:assert/strict';
import { proxyAttemptTag } from '../apis/sources/lusophone.mjs';

test('R-F554: items present → bare status (healthy path)', () => {
  const items = [{ title: 'a' }, { title: 'b' }];
  assert.equal(proxyAttemptTag('codetabs', 200, items, 4096), 'codetabs=200');
});

test('R-F554: 2xx with tiny body → "(empty)" suffix', () => {
  // Live signal from 2026-05-16 — 9 Lusophone feeds were getting
  // codetabs=200 with bodies ≤64 bytes (proxy responded but the
  // upstream RSS endpoint was 451/403'd and codetabs forwarded the
  // empty error page). Operator must see "(empty)" so they can tell
  // codetabs's quota is fine but the underlying source is the problem.
  assert.equal(proxyAttemptTag('codetabs', 200, [], 12), 'codetabs=200(empty)');
  assert.equal(proxyAttemptTag('codetabs', 200, [], 0), 'codetabs=200(empty)');
});

test('R-F554: 2xx with body but parseRSS yielded nothing → "(0-items)"', () => {
  // Body present and large enough to be HTML or unrelated content —
  // proxy is up, upstream returned something, but it doesn't parse as
  // RSS/Atom. Different failure mode from (empty); distinct tag.
  assert.equal(proxyAttemptTag('codetabs', 200, [], 4096), 'codetabs=200(0-items)');
  assert.equal(proxyAttemptTag('jina', 200, undefined, 2048), 'jina=200(0-items)');
});

test('R-F554: non-numeric status passes through', () => {
  // Defensive — if a caller passes "err" or a string status, don't
  // synthesise a bogus "(empty)" suffix.
  assert.equal(proxyAttemptTag('jina', 'err', [], 0), 'jina=err');
});

test('R-F554: name composition is unambiguous', () => {
  // The grep pattern `name=` must always land on the proxy name. No
  // commas, semicolons, or `=` in the suffix that could break the
  // existing pipeline-delimited log line.
  const out = proxyAttemptTag('allorigins', 200, [], 1024);
  assert.match(out, /^allorigins=200/, 'name=status must lead the tag');
  assert.ok(!out.includes(','), 'no commas in suffix (would break delim)');
  assert.ok(!out.includes(';'), 'no semicolons in suffix');
});
