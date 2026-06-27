// test/email-smtp-cred-resolution-rf2032.test.mjs
//
// R-F2032 — lib/auth/email.mjs must read the SMTP_USER / SMTP_PASS namespace.
// Live aria-web held the working mailbox creds under SMTP_USER/SMTP_PASS, but
// the module only read EMAIL_* → ARIA_EMAIL_*, so auth failed ("Invalid login:
// wrong user/password") while the right creds sat unread.
//
// Drives the REAL module (it resolves creds at import time + logs an [EMAIL]
// boot line with the resolved user) in an isolated subprocess per case, so we
// test the actual resolution, not a replica.
//
// Run: node --test test/email-smtp-cred-resolution-rf2032.test.mjs

import test from 'node:test';
import assert from 'node:assert';
import { spawnSync } from 'node:child_process';

// file:// URL — required for dynamic import() of an absolute path on Windows.
const EMAIL_MJS = new URL('../lib/auth/email.mjs', import.meta.url).href;

// Start from a clean copy of the parent env with ALL mail vars stripped, so the
// host test environment can never leak EMAIL_*/SMTP_*/ARIA_* into a case.
function _baseEnv() {
  const e = { ...process.env };
  for (const k of Object.keys(e)) {
    if (/^(EMAIL_|SMTP_|ARIA_EMAIL_|ARIA_SMTP_)/.test(k)) delete e[k];
  }
  return e;
}

function bootLog(caseVars) {
  const r = spawnSync(
    process.execPath,
    ['--input-type=module', '-e', `await import(${JSON.stringify(EMAIL_MJS)})`],
    { env: { ..._baseEnv(), ...caseVars }, encoding: 'utf8' },
  );
  return (r.stderr || '') + (r.stdout || '');
}

test('SMTP_USER/SMTP_PASS are used when EMAIL_USER is unset (the live bug)', () => {
  const log = bootLog({
    EMAIL_HOST: 'ox.livemail.co.uk',
    SMTP_USER: 'smtpbox@arkmurus.com',
    SMTP_PASS: 'secret-smtp-pass',
  });
  assert.match(log, /SMTP configured/, 'should be configured, not log-relay mode');
  assert.match(log, /user=smtpbox@arkmurus\.com/, 'must resolve EMAIL_USER from SMTP_USER');
});

test('dedicated EMAIL_USER still wins over SMTP_USER (additive, never overrides)', () => {
  const log = bootLog({
    EMAIL_HOST: 'mail.dedicated.com',
    EMAIL_USER: 'dedicated@arkmurus.com',
    EMAIL_PASS: 'p',
    SMTP_USER: 'smtpbox@arkmurus.com',
    SMTP_PASS: 'q',
  });
  assert.match(log, /user=dedicated@arkmurus\.com/, 'EMAIL_* must keep precedence over SMTP_*');
});

test('ARIA fallback still works when neither EMAIL_* nor SMTP_* is set', () => {
  const log = bootLog({
    ARIA_EMAIL_HOST: 'ox.livemail.co.uk',
    ARIA_EMAIL_USER: 'aria@arkmurus.com',
    ARIA_EMAIL_PASS: 'p',
  });
  assert.match(log, /SMTP configured/, 'ARIA inbound-bridge fallback must still configure SMTP');
  assert.match(log, /user=aria@arkmurus\.com/, 'must fall back to ARIA_EMAIL_USER');
});

test('still log-relay mode when no credentials anywhere', () => {
  const log = bootLog({ EMAIL_HOST: 'ox.livemail.co.uk' });  // host but no user/pass
  assert.match(log, /SMTP NOT configured/, 'no creds anywhere → log-relay, not a broken send');
});
