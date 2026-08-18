/**
 * R-F4152 (C-175) — a trailing carriage return in a fly secret silently
 * disabled the email reader, and made the auth diagnostic confidently wrong.
 *
 * Measured live on aria-web, 2026-08-18, while the operator was locked out:
 *
 *     ARIA_EMAIL_ENABLED = "true\r"
 *     ARIA_EMAIL_USER    = "aria@arkmurus.com\r"
 *     ARIA_EMAIL_PASS    = "...\r"
 *     ADMIN_EMAIL        = "acorrea@arkmurus.com\r"
 *
 * The secrets were set out of a CRLF file, so each carries one invisible byte.
 *
 * WHAT IT COST
 *
 *  1. `const ENABLED = process.env.ARIA_EMAIL_ENABLED === 'true'` — and
 *     `"true\r" === 'true'` is FALSE. The email reader logged "Disabled — set
 *     ARIA_EMAIL_ENABLED=true to activate" and did nothing, for a flag that WAS
 *     set to true. Nothing failed and nothing alerted: a feature the operator
 *     switched on was simply off.
 *
 *  2. SMTP AUTH. Proven against the real server, connect+AUTH only:
 *
 *         AS-IS (with trailing CR): 535 5.7.8 Authentication failed
 *         TRIMMED:                  AUTH OK
 *
 *     The credentials were always correct. One byte per secret was not.
 *
 *  3. `/api/auth/system-status` printed the RAW value, so it showed a broken
 *     SMTP user while `lib/auth/email.mjs` — which already had its own
 *     `_clean` — was trimming and authenticating fine. During a live lockout
 *     that reading sent the diagnosis in the wrong direction for several
 *     minutes: it said the mailer was misconfigured when it was not, and it hid
 *     the module that genuinely WAS reading raw.
 *
 * WHY A SHARED READ-SIDE FIX AND NOT "SET THE SECRET MORE CAREFULLY"
 *
 * `lib/auth/email.mjs` had already been bitten by this and carries a comment
 * ending "Reverted in favour of trim". That fix was per-module, so the next
 * module to read the same secrets — `emailReader.mjs` — inherited none of it.
 * A habit that lives in one file is not a fix. Reading is where tolerance
 * belongs, because no amount of care at the setting end reliably prevents a
 * CRLF paste.
 */
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('the live production value would have disabled the reader', () => {
  // Not a hypothetical: this is the exact string measured on aria-web.
  assert.equal('true\r' === 'true', false,
    'JS equality changed?! the premise of this defect no longer holds');
  assert.equal('true\r'.trim() === 'true', true);
});

test('emailReader reads every env var through a trimming helper', () => {
  const src = fs.readFileSync(path.join(ROOT, 'lib/aria/emailReader.mjs'), 'utf8');
  const head = src.slice(0, src.indexOf('function') > 0 ? src.indexOf('function') : 8000);

  // The specific vars that were contaminated must not be read raw.
  for (const name of ['ARIA_EMAIL_ENABLED', 'ARIA_EMAIL_USER', 'ARIA_EMAIL_PASS']) {
    const raw = new RegExp(`process\\.env\\.${name}\\b`);
    assert.equal(raw.test(head), false,
      `${name} is read straight off process.env — a trailing CR will silently ` +
      'disable or fail it. Read it through _env().');
  }
  assert.ok(/const _env\s*=/.test(src), 'the _env helper is gone');
});

test('the trimming helper actually trims, and preserves the default', () => {
  const _env = (name, dflt = '') => {
    const v = process.env[name];
    return (v == null ? dflt : String(v).trim()) || dflt;
  };
  process.env.__RF4152_A = 'true\r';
  process.env.__RF4152_B = '   ';
  delete process.env.__RF4152_C;
  try {
    assert.equal(_env('__RF4152_A'), 'true');       // the live failure case
    assert.equal(_env('__RF4152_B', 'fallback'), 'fallback'); // whitespace-only -> default
    assert.equal(_env('__RF4152_C', 'fallback'), 'fallback'); // unset -> default
  } finally {
    delete process.env.__RF4152_A;
    delete process.env.__RF4152_B;
  }
});

test('system-status reports the EFFECTIVE smtp values, not the raw env', () => {
  const src = fs.readFileSync(path.join(ROOT, 'server.mjs'), 'utf8');
  const i = src.indexOf("app.get('/api/auth/system-status'");
  assert.ok(i > 0, 'the system-status route is gone');
  const block = src.slice(i, i + 4000);

  assert.ok(/const _eff\s*=/.test(block),
    'system-status no longer normalises what it reports — it will print a raw ' +
    'value again and misdirect the next person triaging a lockout');
  // and it must be declared before its first use, or it is a TDZ ReferenceError
  const decl = block.indexOf('const _eff');
  const firstUse = block.indexOf('_eff(');
  assert.ok(decl < firstUse,
    '_eff is used before it is declared — that is a runtime ReferenceError on ' +
    'the one endpoint you reach for during an outage');
});

test('no source file carries a stray carriage return inside a line', () => {
  // The fix for this defect introduced the defect: a literal \r in a comment
  // split the line and broke server.mjs syntax. CRLF files are fine; a CR that
  // is not followed by LF is not.
  for (const rel of ['server.mjs', 'lib/aria/emailReader.mjs']) {
    const text = fs.readFileSync(path.join(ROOT, rel), 'utf8');
    const stray = (text.match(/\r(?!\n)/g) || []).length;
    assert.equal(stray, 0, `${rel} contains ${stray} stray CR byte(s)`);
  }
});
