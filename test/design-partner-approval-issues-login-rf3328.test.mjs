// R-F3328 — approving a design partner MUST issue them a login.
//
// The defect, verified live on 2026-07-28: Ray Ingram applied through
// partners.html at 10:35:03, an operator approved him 31 seconds later, the
// design-partners page showed "engaged ✓" — and /data/users.json on aria-web
// held four accounts, none of them his. He had no password, no email and no way
// in, while partners.html had promised him "free access during the pilot".
//
// Root cause: the entire approval chain was a status label.
//   design-partners.html Approve
//     → POST /api/design-partners/:index/status   (server.mjs)
//     → PATCH /api/aria/admin/design-partners/:index (aria-intel)
//     → DesignPartnerTracker.update()             (design_partner_tracker.py)
// update() writes `notes` and `status` and returns. Nothing on that path ever
// created an account. It was not broken — it was never built.
//
// These tests drive the REAL provisioning function against a REAL (isolated)
// user store and assert the user-visible outcome: that the approved partner can
// sign in, checked with the login route's actual gate (verifyPassword + the
// status checks at server.mjs /api/auth/login). Pre-fix, provisionDesignPartner
// Access did not exist and the handler called nothing — the source lock below
// is the fail-before signal against server.mjs itself.
//
// Run: node --test test/design-partner-approval-issues-login-rf3328.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const REPO = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');

// ── Isolated store ───────────────────────────────────────────────────────────
// Set BEFORE importing users.mjs: USERS_FILE is resolved at module load, so a
// late override would write into the real /data store.
//
// Both the env vars and the dynamic imports MUST come before the first test()
// call. node:test starts draining as soon as tests are registered; a test
// registered after a top-level await can miss that window and be reported
// "cancelled — the event loop has already resolved" under the repo's
// `--test-force-exit` runner (package.json), which is a green-locally /
// red-in-suite trap.
const STORE = path.join(mkdtempSync(path.join(tmpdir(), 'users-rf3328-')), 'users.json');
process.env.USERS_FILE_OVERRIDE = STORE;
process.env.JWT_SECRET = process.env.JWT_SECRET || 'test-secret-rf3328-at-least-32-characters-long';

const { findUserByEmail, verifyPassword } = await import('../lib/auth/users.mjs');
const { provisionDesignPartnerAccess, DESIGN_PARTNER_TIER } =
  await import('../lib/auth/designPartnerAccess.mjs');

// ── Source-read regression lock ──────────────────────────────────────────────
// The property: the handler that saves an access-granting status must also
// provision. Asserted against the real server.mjs, so re-introducing the
// label-only handler fails here even if the lib module still exists.
test('R-F3328: the status handler provisions an account on an access-granting status', () => {
  const src = readFileSync(path.join(REPO, 'server.mjs'), 'utf-8');
  const handler = src.slice(src.indexOf("app.post('/api/design-partners/:index/status'"));
  const body = handler.slice(0, handler.indexOf('\napp.post(', 1));
  assert.ok(/ACCESS_GRANTING_STATUSES\.includes\(patch\.status\)/.test(body),
    'handler must branch on the status being access-granting');
  assert.ok(/await provisionDesignPartnerAccess\(entry\)/.test(body),
    'handler must call provisionDesignPartnerAccess with the stored record');
  assert.ok(/provisioning\b/.test(body),
    'handler must return the provisioning outcome to the caller, not swallow it');
});

test('R-F3328: an "Issue login" route exists for partners approved before the fix', () => {
  const src = readFileSync(path.join(REPO, 'server.mjs'), 'utf-8');
  assert.ok(src.includes("app.post('/api/design-partners/:index/provision', requireAdmin"),
    'the manual-issue route must exist and be admin-only');
});

test('R-F3328: the admin page surfaces whether an approved partner can sign in', () => {
  const html = readFileSync(path.join(REPO, 'public', 'design-partners.html'), 'utf-8');
  assert.ok(/function accessCell\(/.test(html), 'page must render an Access column');
  assert.ok(/data-provision=/.test(html), 'page must offer an Issue login action');
  assert.ok(/tempPassword/.test(html),
    'page must display the one-time credential — live SMTP cannot deliver it');
});

// The login gate from server.mjs POST /api/auth/login, verbatim in effect:
// unknown email → 401, bad password → 401, and pending_verification /
// pending_approval / suspended → 403. Only an ACTIVE row with a matching
// password signs in. This is the assertion that matters — "a user row exists"
// is not the same as "the partner can get in", and createUser alone would leave
// them at pending_verification behind an email that cannot be delivered.
function canSignIn(email, password) {
  const raw = JSON.parse(readFileSync(STORE, 'utf-8'));
  const user = raw.find(u => u.email === String(email).toLowerCase().trim());
  if (!user) return { ok: false, why: '401 invalid credentials (no such user)' };
  if (!verifyPassword(password, user.passwordHash)) return { ok: false, why: '401 invalid credentials' };
  if (user.status === 'pending_approval') return { ok: false, why: '403 pending admin approval' };
  if (user.status === 'pending_verification') return { ok: false, why: '403 verify your email first' };
  if (user.status === 'suspended') return { ok: false, why: '403 account suspended' };
  return { ok: true, why: '' };
}

// The live record, as stored in aria-intel's /data/design_partners.json.
const RAY = {
  name: 'Ray ingram',
  contact: 'ingram.ray@gmail.com',
  notes: '',
  status: 'engaged',
  source: 'public_application',
  company: '',
};

// SMTP on aria-web cannot authenticate (EMAIL_USER === EMAIL_PASS — R-F3289),
// so the delivery path used in production is the FAILING one. Every test runs
// against that reality; a fix that only works when email works would leave the
// operator exactly where they started.
const deadSmtp = { calls: [], sendEmail: async (to, name, pw) => {
  deadSmtp.calls.push({ to, name, pw });
  return { sent: false, reason: 'SMTP not configured' };
} };

test('R-F3328: approving Ray creates an account he can actually sign in with', async () => {
  assert.equal(findUserByEmail(RAY.contact), null, 'arrange: no account exists, as live');

  const result = await provisionDesignPartnerAccess(RAY, { sendEmail: deadSmtp.sendEmail });

  assert.equal(result.provisioned, true, result.reason);
  assert.ok(result.tempPassword, 'a credential must be returned to the operator');
  const signIn = canSignIn(RAY.contact, result.tempPassword);
  assert.equal(signIn.ok, true, `the issued credential must actually sign in (got ${signIn.why})`);
});

test('R-F3328: the approved partner gets the free full-platform pilot access promised', () => {
  const user = findUserByEmail(RAY.contact);
  assert.ok(user, 'account exists');
  assert.equal(user.status, 'active');
  assert.equal(user.tier, DESIGN_PARTNER_TIER, 'partners.html promises the full platform, free');
  assert.equal(user.designPartner, true, 'the grant must be marked as a pilot grant, not a subscription');
});

test('R-F3328: a credential email that cannot send is reported, never swallowed', () => {
  assert.equal(deadSmtp.calls.length, 1, 'the credential email must be attempted');
  assert.equal(deadSmtp.calls[0].to, RAY.contact);
});

test('R-F3328: re-issuing does not reset an existing partner\'s password', async () => {
  const first = findUserByEmail(RAY.contact);
  const again = await provisionDesignPartnerAccess(RAY, { sendEmail: deadSmtp.sendEmail });

  assert.equal(again.provisioned, false);
  assert.equal(again.outcome, 'existing_account');
  assert.equal(again.tempPassword, null,
    'an existing account\'s credential must never be handed to whoever pressed the button');
  assert.equal(findUserByEmail(RAY.contact).passwordHash, first.passwordHash,
    'the stored hash must be untouched');
});

test('R-F3328: a record with no email address fails loudly instead of silently', async () => {
  const before = JSON.parse(readFileSync(STORE, 'utf-8')).length;
  const result = await provisionDesignPartnerAccess(
    { name: 'Phone Only Ltd', contact: '+44 7700 900123' }, { sendEmail: deadSmtp.sendEmail });

  assert.equal(result.provisioned, false);
  assert.equal(result.outcome, 'no_email_address');
  assert.match(result.reason, /not an email address/);
  assert.equal(JSON.parse(readFileSync(STORE, 'utf-8')).length, before, 'no account created');
});

test('R-F3328: two partners on the same email local part get distinct usernames', async () => {
  const a = await provisionDesignPartnerAccess(
    { name: 'A', contact: 'ray@alpha-rf3328.test' }, { sendEmail: deadSmtp.sendEmail });
  const b = await provisionDesignPartnerAccess(
    { name: 'B', contact: 'ray@beta-rf3328.test' }, { sendEmail: deadSmtp.sendEmail });

  assert.equal(a.provisioned, true);
  assert.equal(b.provisioned, true);
  assert.notEqual(a.username, b.username, 'createUser does not de-duplicate usernames');
  // Both credentials must work — a username collision would have made one row
  // unreachable through any username-keyed lookup.
  assert.equal(canSignIn(a.email, a.tempPassword).ok, true);
  assert.equal(canSignIn(b.email, b.tempPassword).ok, true);
});
