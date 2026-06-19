// test/conversation-key-rf1687.test.mjs
// R-F1687 — proves the conversation bucket key is (a) stable, (b) identical to
// the browser's USER_ID_SLUG, and (c) the SAME for the write path and the read
// path. The empty-sidebar bug was a write/read key mismatch; this test fails on
// the pre-fix behaviour and passes on the fix.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { conversationKeyForUser, slugifyIdentity } from '../lib/auth/conversationKey.mjs';

// Mirror of aria.html's slug computation (the browser/read-query side).
function browserUserIdSlug(user) {
  return (user.email || user.username || user.id || 'anon').toString().replace(/[^A-Za-z0-9]/g, '');
}

test('R-F1687: bucket key prefers email and matches the browser slug', () => {
  const user = { id: '07ea659385d1', username: 'admin', email: 'acorrea@arkmurus.com' };
  const serverKey = conversationKeyForUser(user);
  assert.equal(serverKey, 'acorreaarkmuruscom');
  // Write-side (server) MUST equal read-side (browser query) — else empty sidebar.
  assert.equal(serverKey, browserUserIdSlug(user));
});

test('R-F1687: key is STABLE when the volatile user.id churns (deploy rebuild)', () => {
  // Same human, two different random ids across deploys — email is unchanged.
  const before = { id: '07ea659385d1', email: 'acorrea@arkmurus.com' };
  const after  = { id: '588d5ca52108', email: 'acorrea@arkmurus.com' };
  assert.equal(conversationKeyForUser(before), conversationKeyForUser(after));
});

test('R-F1687: a per-session id never becomes the bucket key', () => {
  // The pre-fix Python fallback bucketed under session_id.rsplit("_",1)[0] =
  // `{slug}_{ts}`. The key for a real account must be the bare slug, so a write
  // and a list converge. Guard: slug never contains the timestamp separator.
  const key = conversationKeyForUser({ email: 'acorrea@arkmurus.com' });
  assert.ok(!key.includes('_'));
  assert.equal(key, 'acorreaarkmuruscom');
});

test('R-F1687: no identity → empty key (treated as unauthenticated, not a junk bucket)', () => {
  assert.equal(conversationKeyForUser(null), '');
  assert.equal(conversationKeyForUser({}), '');
});

test('R-F1687: slugifyIdentity strips exactly the browser charset', () => {
  assert.equal(slugifyIdentity('a.b+c@x-y.com'), 'abcxycom');
  assert.equal(slugifyIdentity(null), '');
  assert.equal(slugifyIdentity('aria-internal'), 'ariainternal');
});
