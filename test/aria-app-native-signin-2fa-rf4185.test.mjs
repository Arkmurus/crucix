import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const src = readFileSync(new URL('../aria-app/components/signin-form.tsx', import.meta.url), 'utf8');

test('R-F4185: native sign-in completes the real backend two-factor flow', () => {
  assert.match(src, /data\.requires2FA/);
  assert.match(src, /data\.preToken/);
  assert.match(src, /\/api\/auth\/2fa\/authenticate/);
  assert.match(src, /preToken.*code/s);
  assert.match(src, /autoComplete="one-time-code"/);
  assert.match(src, /inputMode="numeric"/);
  assert.match(src, /Back to sign in/);
});

test('R-F4185: neither auth branch can redirect before cookie parking succeeds', () => {
  assert.match(src, /const sessionRes = await fetch\('\/api\/session'/);
  assert.match(src, /if \(!sessionRes\.ok\) throw new Error\('session_not_persisted'\)/);
  assert.match(src, /mustChangePassword/);
  assert.match(src, /\/set-password\.html/);

  const park = src.indexOf("const sessionRes = await fetch('/api/session'");
  const redirect = src.indexOf('router.push(');
  assert.ok(park >= 0 && redirect > park, 'session cookie must be parked before redirect');
});
