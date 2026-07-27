import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

/**
 * R-F3289 — SMTP reported "configured" while structurally unable to authenticate.
 *
 * Diagnosed from the LIVE aria-web secrets, by digest (no value was read):
 *
 *     EMAIL_USER        49bb8a67b557e235
 *     EMAIL_PASS        49bb8a67b557e235   <- the same value
 *     ARIA_EMAIL_USER   cb26e8b79add1b2e
 *     ARIA_EMAIL_PASS   b8e84ce769c9b5e7   <- correctly distinct
 *
 * A username and a password are never legitimately the same string, so
 * EMAIL_USER/EMAIL_PASS are a mis-set pair. They also take PRECEDENCE over the
 * ARIA_EMAIL_* pair, which is the one that is correct. Every send therefore
 * authenticates with user === pass and gets 535, and has done since the
 * secrets were set.
 *
 * The defect this file fixes is not the secret. It is that `isConfigured` said
 * TRUE for a credential set that cannot possibly work, so the boot log printed
 * "SMTP configured" and the diagnostic surface agreed, while every password
 * reset silently failed. That is the same shape as every false clean in this
 * codebase: a check that reports the presence of a value rather than whether
 * the thing can actually do its job.
 *
 * "Present" is not "usable". A config that provably cannot authenticate is
 * reported as NOT configured, with the reason, so the codes fall back to the
 * stdout path a human can act on instead of vanishing into a 535.
 */

const src = readFileSync('lib/auth/email.mjs', 'utf8');

test('a credential pair where user equals pass is refused, not reported as ready', () => {
  // Compared TRIMMED, deliberately: R-F2039 found these very secrets carrying
  // a trailing "\r" from a Windows source, so an untrimmed compare would miss
  // "aria@imaria.io" vs "aria@imaria.io\r" — the same wrong value, undetected.
  assert.match(src, /String\(EMAIL_USER\)\.trim\(\) === String\(EMAIL_PASS\)\.trim\(\)/,
    'nothing detects the identical-user-and-pass case (trimmed)');
});

test('isConfigured is false when the credentials cannot authenticate', () => {
  // The whole point: `isConfigured` gates every send. If it stays true for an
  // unusable pair, the fix is a log line nobody reads.
  const line = src.match(/export const isConfigured = [^\n]*/)[0];
  assert.match(line, /_credsUsable|configError|!_configError/,
    `isConfigured still only checks presence: ${line}`);
});

test('the reason is stated, not just the refusal', () => {
  // "SMTP not configured" for a box with three SMTP secrets set would send the
  // operator hunting for a missing value that is not missing.
  assert.match(src, /export const configError|configError =/,
    'there is no machine-readable reason');
  assert.match(src, /identical/i,
    'the message does not say what is actually wrong');
});

test('the boot log names the specific problem', () => {
  assert.match(src, /\[EMAIL\][^\n]*R-F3289|R-F3289[^\n]*EMAIL/,
    'the boot diagnostic does not mention this failure mode');
});

test('a valid distinct pair is still accepted', () => {
  // The guard must not be so eager that it refuses a working mailbox.
  const fn = src.match(/function _credsUsable[\s\S]*?\n\}/);
  assert.ok(fn, 'no _credsUsable helper to reason about');
  const check = new Function('u', 'p', 'h',
    fn[0] + '; return _credsUsable(u, p, h);');
  assert.equal(check('aria@imaria.io', 'a-real-password', 'smtp.example.com'), true);
  assert.equal(check('aria@imaria.io', 'aria@imaria.io', 'smtp.example.com'), false,
    'the live defect is not detected');
  assert.equal(check('aria@imaria.io', '', 'smtp.example.com'), false);
  assert.equal(check('', 'pw', 'smtp.example.com'), false);
  assert.equal(check('aria@imaria.io', 'pw', ''), false, 'no host is not usable');
});

test('the host and the credentials must come from the same source set', () => {
  // The second half of the live misconfiguration: EMAIL_HOST is unset, so the
  // host resolves to ARIA_SMTP_HOST, while EMAIL_USER/PASS (set) win over
  // ARIA_EMAIL_USER/PASS. Host from one mailbox, credentials from another.
  // A mixed set is not necessarily wrong, but it is worth saying out loud.
  assert.match(src, /mixed|MIXED/,
    'nothing warns when host and credentials come from different variable sets');
});
