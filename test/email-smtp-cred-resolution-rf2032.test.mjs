// test/email-smtp-cred-resolution-rf2032.test.mjs
//
// R-F2039 (supersedes R-F2032) — lib/auth/email.mjs credential resolution.
//
// ACTUAL root cause of the live SMTP failure (verified on aria-web 2026-06-27):
// CRLF contamination — every secret carried a trailing "\r" (e.g.
// "ox.livemail.co.uk\r", "aria@arkmurus.com\r"), which broke DNS (ENOTFOUND)
// and SMTP AUTH (535). The same creds authenticate the moment they're trimmed.
// So the fix is to TRIM creds, and the sender is the ARIA mailbox (aria@) per
// operator direction — NOT the SMTP_* namespace R-F2032 added (that points to a
// different mailbox, acorrea@).
//
// Drives the REAL module (it resolves creds at import + logs an [EMAIL] boot
// line with the resolved user) in isolated subprocesses.
//
// Run: node --test test/email-smtp-cred-resolution-rf2032.test.mjs

import test from 'node:test';
import assert from 'node:assert';
import { spawnSync } from 'node:child_process';

const EMAIL_MJS = new URL('../lib/auth/email.mjs', import.meta.url).href;

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

test('R-F2039: CRLF-contaminated creds are TRIMMED and still configure SMTP (the real bug)', () => {
  const log = bootLog({
    EMAIL_HOST: 'ox.livemail.co.uk\r',
    EMAIL_USER: 'aria@arkmurus.com\r',
    EMAIL_PASS: 'secret\r',
  });
  assert.match(log, /SMTP configured/, 'trailing \\r must not break configuration');
  assert.match(log, /host=ox\.livemail\.co\.uk(?:\s|$)/m, 'host must be trimmed (no trailing \\r)');
  assert.match(log, /user=aria@arkmurus\.com(?:\s|$)/m, 'user must be trimmed (no trailing \\r)');
});

test('R-F2039: sender falls back to the ARIA mailbox (aria@) when EMAIL_* is empty', () => {
  const log = bootLog({
    EMAIL_USER: '',                              // empty (live state)
    ARIA_SMTP_HOST: 'ox.livemail.co.uk\r',
    ARIA_EMAIL_USER: 'aria@arkmurus.com\r',
    ARIA_EMAIL_PASS: 'p\r',
    ARIA_SMTP_PORT: '465\r',
  });
  assert.match(log, /SMTP configured/);
  assert.match(log, /user=aria@arkmurus\.com(?:\s|$)/m, 'sender must be the ARIA mailbox');
});

test('R-F2039: SMTP_* (a different mailbox) is NOT used as the sender', () => {
  const log = bootLog({
    EMAIL_USER: '',
    SMTP_USER: 'acorrea@arkmurus.com',          // present, but must be ignored
    SMTP_PASS: 'q',
    ARIA_SMTP_HOST: 'ox.livemail.co.uk',
    ARIA_EMAIL_USER: 'aria@arkmurus.com',
    ARIA_EMAIL_PASS: 'p',
  });
  assert.match(log, /user=aria@arkmurus\.com(?:\s|$)/m, 'must resolve aria@, not the SMTP_* mailbox');
  assert.doesNotMatch(log, /user=acorrea@arkmurus\.com/, 'SMTP_* must NOT win (R-F2032 reverted)');
});

test('R-F2039: dedicated EMAIL_USER keeps precedence over the ARIA fallback', () => {
  const log = bootLog({
    EMAIL_HOST: 'mail.dedicated.com',
    EMAIL_USER: 'dedicated@arkmurus.com',
    EMAIL_PASS: 'p',
    ARIA_EMAIL_USER: 'aria@arkmurus.com',
    ARIA_EMAIL_PASS: 'p',
  });
  assert.match(log, /user=dedicated@arkmurus\.com/, 'explicit EMAIL_* override must win');
});

test('R-F2039: still log-relay mode when no credentials anywhere', () => {
  const log = bootLog({ ARIA_SMTP_HOST: 'ox.livemail.co.uk' });  // host but no user/pass
  assert.match(log, /SMTP NOT configured/);
});
