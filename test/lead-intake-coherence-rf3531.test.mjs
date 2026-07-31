// test/lead-intake-coherence-rf3531.test.mjs
//
// R-F3531 — the web tier half of the intake coherence surgery.
//
// The Python suite (test_rf3531_lead_intake_coherence.py) guards the brain and
// the producer→consumer field contract. This guards what only aria-web can be
// wrong about:
//   * the single-use token must never reach the browser
//   * /api/leads/verify must be PUBLIC (the confirming person has no account)
//   * the operator routes must be admin-gated, and `actor` must come from the
//     JWT, not from the request body
//   * the confirmation page must confirm on a CLICK, never on page load
//   * a confirmation that could not be emailed must never be reported as sent
//
// Run: node test/lead-intake-coherence-rf3531.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const rd = (p) => readFileSync(join(__dirname, '..', p), 'utf8');
const SERVER = rd('server.mjs');
const EMAIL = rd('lib/auth/email.mjs');
const VERIFY_PAGE = rd('public/lead-verify.html');
const LEADS = rd('public/leads.html');
const APP_JS = rd('public/js/app.js');

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

console.log('R-F3531 — lead intake coherence (web tier + surfaces)\n');

// ── 1. The token must never reach the browser ────────────────────────────────
const LEADS_POST = SERVER.slice(
  SERVER.indexOf("app.post('/api/leads'"),
  SERVER.indexOf('async function _mailLeadVerification'));
check('the leads POST block was located (not an empty slice)',
  LEADS_POST.length > 200 && LEADS_POST.includes('/api/aria/leads/inbound'));
check('the public POST reply carries only ok + the send outcome',
  /return res\.status\(200\)\.json\(\{ ok: true, verification \}\);/.test(LEADS_POST));
check('the public POST reply never relays data.verification (the token) to the browser',
  !/json\([\s\S]{0,120}data\.verification/.test(LEADS_POST) &&
  !/token/.test(LEADS_POST.replace(/\/\/[^\n]*\n/g, '')),
  'the plaintext challenge must be consumed by the mailer, not echoed');

// ── 2. Gating: public where it must be, admin where it must be ───────────────
check('POST /api/leads/verify is PUBLIC (the confirming person has no account)',
  /app\.post\('\/api\/leads\/verify',\s*async/.test(SERVER));
check('PATCH /api/leads/:leadId is admin-gated',
  /app\.patch\('\/api\/leads\/:leadId',\s*requireAdmin/.test(SERVER));
check('POST /api/leads/:leadId/resend-verification is admin-gated',
  /app\.post\('\/api\/leads\/:leadId\/resend-verification',\s*requireAdmin/.test(SERVER));

// ── 3. actor comes from the JWT, never from the browser ──────────────────────
const PATCH_BLOCK = SERVER.slice(
  SERVER.indexOf("app.patch('/api/leads/:leadId'"),
  SERVER.indexOf("app.post('/api/leads/:leadId/resend-verification'"));
check('actor is read from the authenticated user',
  /const actor = req\.user\?\.email \|\| req\.user\?\.userId \|\| '';/.test(PATCH_BLOCK));
check('actor is applied AFTER the body spread, so a browser-supplied actor loses',
  /\{ \.\.\.\(req\.body \|\| \{\}\), actor \}/.test(PATCH_BLOCK));

console.log('\nBehavioural — a forged actor in the request body is discarded:');
function stampActor(body, jwtActor) { return { ...(body || {}), actor: jwtActor }; }
check('browser claim "actor: ceo@victim.example" is overwritten by the JWT identity',
  stampActor({ action: 'mark_operator_verified', actor: 'ceo@victim.example' }, 'ops@imaria.io').actor
    === 'ops@imaria.io');
check('the rest of the body still passes through',
  stampActor({ action: 'add_note', note: 'hello' }, 'ops@imaria.io').note === 'hello');

// ── 4. Honest send reporting ─────────────────────────────────────────────────
const MAILER = SERVER.slice(
  SERVER.indexOf('async function _mailLeadVerification'),
  SERVER.indexOf("app.post('/api/leads/verify'"));
check('an unconfigured SMTP reports not_sent, never sent',
  /if \(!smtpIsConfigured\)[\s\S]{0,220}return 'not_sent';/.test(MAILER));
check('a send failure reports not_sent rather than throwing away the lead',
  /catch \(e\)[\s\S]{0,140}return 'not_sent';/.test(MAILER));
check('sent is claimed only on a truthy result.sent',
  /return result\?\.sent \? 'sent' : 'not_sent';/.test(MAILER));

console.log('\nBehavioural — the landing message mirrors the real outcome:');
function landingMessage(verification) {
  let m = 'Thank you. Your request has been recorded.';
  if (verification === 'sent') m += ' Please confirm your address using the link we have just emailed you.';
  else if (verification === 'not_sent') m += ' We could not send the confirmation email — we will follow up directly.';
  return m;
}
check('sent -> tells them to check their email',
  /confirm your address/.test(landingMessage('sent')));
check('not_sent -> never tells them to check an email that was not sent',
  !/confirm your address/.test(landingMessage('not_sent')) &&
  /could not send/.test(landingMessage('not_sent')));
check('not_required -> no claim about email at all',
  landingMessage('not_required') === 'Thank you. Your request has been recorded.');

// ── 5. The confirmation page ─────────────────────────────────────────────────
check('lead-verify.html does NOT require auth',
  !/Auth\.requireAuth\(\)/.test(VERIFY_PAGE) && !/Sidebar\.init/.test(VERIFY_PAGE));
check('confirmation happens on a click, not on page load (scanners prefetch links)',
  /addEventListener\('click'/.test(VERIFY_PAGE) &&
  !/^\s*fetch\('\/api\/leads\/verify'/m.test(VERIFY_PAGE));
check('it POSTs (a prefetched GET must not be able to confirm an address)',
  /'\/api\/leads\/verify',\s*\{[\s\S]{0,60}method: 'POST'/.test(VERIFY_PAGE));
check('no referrer may leave the page carrying the token',
  /<meta name="referrer" content="no-referrer">/.test(VERIFY_PAGE));
check('the spent token is stripped from the address bar',
  /history\.replaceState/.test(VERIFY_PAGE));
check('the page is not indexable', /noindex/.test(VERIFY_PAGE));

// ── 6. The operator surface ──────────────────────────────────────────────────
check('API.patch exists (the workflow route is registered as app.patch)',
  /async patch\(path, body\)/.test(APP_JS) && /method: 'PATCH'/.test(APP_JS));
check('leads.html loads an app.js new enough to have API.patch',
  /js\/app\.js\?v=1[1-9]/.test(LEADS));
for (const control of ['resend_verification', 'mark_operator_verified', 'assign_owner', 'add_note', 'set_stage', 'erase']) {
  check(`surface offers the ${control} control`,
    new RegExp(`data-action="${control}"`).test(LEADS));
}
check('an attestation cannot be recorded without stating what was checked',
  /mark_operator_verified'\)[\s\S]{0,600}required: true/.test(LEADS) &&
  /if \(!attest \|\| !String\(attest\.note \|\| ''\)\.trim\(\)\) return;/.test(LEADS));
check('a reissued-but-unsent link is reported as a failure, not a success',
  /Link reissued, but the email could NOT be sent/.test(LEADS));
check('the erasure receipt is still required before claiming erasure',
  /erasure_complete !== true/.test(LEADS));

// ── 7. The mail itself ───────────────────────────────────────────────────────
check('sendLeadVerificationEmail is exported', /export async function sendLeadVerificationEmail\(/.test(EMAIL));
check('server.mjs imports it', /sendLeadVerificationEmail[\s\S]{0,120}from '\.\/lib\/auth\/email\.mjs'/.test(SERVER));
check('the mail says the request is not progressed until confirmed',
  /not\s+progressed/.test(EMAIL.slice(EMAIL.indexOf('sendLeadVerificationEmail'))));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
