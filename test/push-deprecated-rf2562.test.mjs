// R-F2562 (#9) — push is a deprecated no-op unless PUSH_ENABLED is set.
import { test } from 'node:test';
import assert from 'node:assert/strict';

test('push is a quiet no-op when PUSH_ENABLED is unset (deprecated)', async () => {
  delete process.env.PUSH_ENABLED;
  const { pushToAll, pushToUser, pushFlash, pushDigest } = await import('../lib/push/push.mjs');
  const a = await pushToAll({ title: 'x' }, null, { critical: true });
  assert.equal(a.reason, 'push_deprecated');   // no "CRITICAL NOT delivered" warning path
  assert.equal(a.sent, 0);
  const u = await pushToUser('u1', { title: 'x' });
  assert.equal(u.reason, 'push_deprecated');
  const f = await pushFlash('t', 'b');
  assert.equal(f.reason, 'push_deprecated');
  const d = await pushDigest('t', 'b');
  assert.equal(d.reason, 'push_deprecated');
});
