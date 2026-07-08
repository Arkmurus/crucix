// test/test-email-button-rf744.test.mjs
//
// Capability test for R-F744 — admin.html "Test Email alerter" button.
//
// User-visible promise: clicking the button on intel.arkmurus.com/admin.html
// shows a GREEN success bar when the SMTP transport actually delivered the
// message, NOT the red "Email alerter returned OK but nothing sent" that
// pre-R-F744 always fired even on success.
//
// Pre-R-F744 bug: lib/auth/email.mjs sendMail() returned { sent: true }
// (no messageId). public/admin.html checked `d.adminEmail.messageId` —
// always undefined — so every successful send was rendered as failure.
//
// Strategy (source + behavioral; ESM resolution makes mocking nodemailer
// from a sibling test brittle, so we verify the contract directly):
//   1. Source-level: lib/auth/email.mjs sendMail() success return must
//      include `messageId` from the nodemailer info object.
//   2. Behavioral: when SMTP is unconfigured, sendMail returns the
//      existing { sent: false, reason } shape so the failure-side
//      contract is preserved (no regression).
//   3. Frontend: public/admin.html success check must accept
//      `sent === true` (the new permissive contract).
//
// Run: node test/test-email-button-rf744.test.mjs

import { spawnSync } from 'node:child_process';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');

let failures = 0;
function check(label, cond, detail) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? ' — ' + detail : ''}`); failures += 1; }
}

console.log('R-F744 capability tests — Test Email button contract\n');

// ── 1. Backend source contract ────────────────────────────────────────────
console.log('1. lib/auth/email.mjs sendMail() success path returns messageId');
{
  const src = readFileSync(join(ROOT, 'lib', 'auth', 'email.mjs'), 'utf8');

  // Find the sendMail function body.
  const sendMailMatch = src.match(/async function sendMail\([\s\S]*?\n\}/);
  check('sendMail function present', !!sendMailMatch);

  if (sendMailMatch) {
    const body = sendMailMatch[0];
    check(
      'captures nodemailer info object',
      /const\s+info\s*=\s*await\s+transport\.sendMail/.test(body),
      'sendMail must capture the info return so messageId is available'
    );
    check(
      'success return includes messageId',
      /return\s*\{[^}]*sent:\s*true[^}]*messageId/.test(body),
      'success return must be { sent: true, messageId: info.messageId, ... }'
    );
    check(
      'failure return still uses { sent: false, reason }',
      /return\s*\{\s*sent:\s*false,\s*reason:/.test(body),
      'failure-side contract preserved for non-regression'
    );
  }
}

// ── 2. Behavioral — unconfigured SMTP returns reason (non-regression) ────
console.log('\n2. unconfigured SMTP still returns { sent: false, reason }');
{
  const probeDir = mkdtempSync(join(tmpdir(), 'rf744-behav-'));
  const probe = join(probeDir, 'probe.mjs');
  const emailModUrl = pathToFileURL(join(ROOT, 'lib', 'auth', 'email.mjs')).href;
  writeFileSync(probe, `
    const mod = await import('${emailModUrl}');
    const result = await mod.sendAdminNotification('R-F744 unconfigured', '<p>probe</p>');
    console.log('RESULT_JSON=' + JSON.stringify(result));
  `);

  // Force all SMTP env vars unset so isConfigured = false.
  const env = { ...process.env };
  for (const k of ['EMAIL_HOST','EMAIL_PORT','EMAIL_USER','EMAIL_PASS','EMAIL_FROM','EMAIL_SECURE',
                   'ARIA_EMAIL_HOST','ARIA_EMAIL_PORT','ARIA_EMAIL_USER','ARIA_EMAIL_PASS',
                   'ARIA_SMTP_HOST','ARIA_SMTP_PORT']) {
    delete env[k];
  }

  const r = spawnSync(process.execPath, [probe], { env, encoding: 'utf8' });
  const line = (r.stdout || '').split(/\r?\n/).find(l => l.startsWith('RESULT_JSON='));
  let out = null;
  try { out = JSON.parse(line.slice('RESULT_JSON='.length)); } catch {}

  if (!out) {
    check('parseable result', false, 'stdout=' + JSON.stringify(r.stdout));
  } else {
    check('sent === false when SMTP unconfigured', out.sent === false);
    check('reason field present', typeof out.reason === 'string' && out.reason.length > 0,
          'got: ' + JSON.stringify(out));
    check('reason mentions SMTP not configured', /SMTP not configured/i.test(out.reason || ''));
  }

  try { rmSync(probeDir, { recursive: true, force: true }); } catch {}
}

// ── 3. Frontend contract — admin.html accepts sent:true ──────────────────
console.log('\n3. public/admin.html test-email success check accepts sent:true');
{
  const html = readFileSync(join(ROOT, 'public', 'admin.html'), 'utf8');

  check(
    'success check accepts d.adminEmail.sent === true',
    /d\.adminEmail\s*&&\s*\(\s*d\.adminEmail\.sent\s*===\s*true/.test(html),
    'must accept sent===true, not only messageId'
  );
  check(
    'success check accepts d.testEmail.sent === true',
    /d\.testEmail\s*&&\s*\(\s*d\.testEmail\.sent\s*===\s*true/.test(html),
    'must accept sent===true, not only messageId'
  );
  check(
    'success bar text says "Email alerter OK"',
    /✓ Email alerter OK\./.test(html),
    'differentiates new success from misleading old copy'
  );
  check(
    'failure branch surfaces backend reason',
    /Email send failed\. Admin:/.test(html),
    'error message should show backend reason (admin + test), not generic'
  );
}

console.log(`\n${failures === 0 ? '✓ All R-F744 capability checks passed' : `✗ ${failures} check(s) failed`}`);
process.exit(failures === 0 ? 0 : 1);
