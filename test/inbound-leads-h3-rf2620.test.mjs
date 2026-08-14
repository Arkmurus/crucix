// test/inbound-leads-h3-rf2620.test.mjs
//
// Capability test for R-F2620 (H3) — the public landing form must actually send
// the lead somewhere, and the operator must be able to view it. Before this, the
// form (index.html) was client-side only: it showed "Thanks, we'll be in touch"
// and POSTed nowhere — every sign-up was dropped. Now:
//   index.html  → POST /api/leads (honest states; no fake success on failure §22)
//   server.mjs  → POST /api/leads (public, forwards to aria-intel with service token)
//               → GET  /api/leads (requireAdmin — leads are PII)
//   leads.html  → operator viewing surface with status-aware empty-vs-error
//
// Run: node test/inbound-leads-h3-rf2620.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const rd = (p) => readFileSync(join(__dirname, '..', p), 'utf8');
const INDEX = rd('public/index.html');
const LANDING_JS = rd('public/pelican/assets/js/custom.js');
const SERVER = rd('server.mjs');
const LEADS = rd('public/leads.html');

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

console.log('R-F2620 H3 — inbound lead capture (landing → brain → operator view)\n');

// ── 1. index.html: the form POSTs, and only claims success honestly ──────────
check('landing form POSTs to /api/leads',
  /action="\/api\/leads"/.test(INDEX) && /url:\s*'\/api\/leads'[\s\S]{0,80}method:\s*'POST'/.test(LANDING_JS));
check('sends name + email + use_case',
  /JSON\.stringify\(\{[\s\S]{0,100}name:\s*name,[\s\S]{0,50}email:\s*email,[\s\S]{0,50}use_case:/.test(LANDING_JS));
check('collects a bounded supported use case instead of fabricating a generic one',
  /id="lead-use-case"[\s\S]{0,700}Compliance advisory/.test(INDEX) &&
  /var useCase =/.test(LANDING_JS) && /use_case:\s*useCase/.test(LANDING_JS) &&
  !/use_case:\s*'ARIA landing page'/.test(LANDING_JS));
// R-F3531 — was `.done(function()`; the handler now takes the response body so
// it can report whether the confirmation email actually went out. Pinning the
// empty argument list asserted the WORDING, not the property, and went red on a
// change that preserved the property exactly.
check('only shows success after the request resolves',
  /\.done\(function\([a-z]*\)[\s\S]{0,420}request has been recorded/.test(LANDING_JS));
check('surfaces a failure branch (retry) instead of faking success',
  /\.fail\(function\(xhr\)[\s\S]{0,260}(try again|try again shortly)/i.test(LANDING_JS));
// the old fire-and-forget "Thanks" (unconditional) must be gone
check('no unconditional success (Thanks not shown before the fetch resolves)',
  !/\.on\('submit'[\s\S]{0,450}request has been recorded[\s\S]{0,100}\$\.ajax/.test(LANDING_JS));

// ── 2. server.mjs: public POST + admin GET, forwarding to aria-intel ─────────
check('POST /api/leads is registered', /app\.post\('\/api\/leads'/.test(SERVER));
check('POST /api/leads is PUBLIC (no requireAuth/requireAdmin on the POST)',
  /app\.post\('\/api\/leads',\s*async/.test(SERVER));
check('POST forwards to aria-intel /api/aria/leads/inbound with service headers',
  /\/api\/aria\/leads\/inbound`,\s*\{[\s\S]{0,120}headers: _ariaHeaders\(\)/.test(SERVER));
// R-F3531 — this check used to scan the WHOLE FILE for `r.ok ? { ok: true } : …`.
// When the leads handler stopped using that exact expression the check stayed
// GREEN, because an unrelated route (the application form, ~line 3084) still
// contained it. A guard that can be satisfied by code it is not guarding proves
// nothing, so it is now scoped to the leads handler and asserts the property:
// an upstream failure is relayed, never converted into a success.
const LEADS_POST = SERVER.slice(
  SERVER.indexOf("app.post('/api/leads'"),
  SERVER.indexOf('async function _mailLeadVerification'));
check('the leads POST block was located (guard is not scanning an empty slice)',
  LEADS_POST.length > 200 && LEADS_POST.includes('/api/aria/leads/inbound'));
check('POST relays the brain verdict — no fake success on upstream failure',
  /if \(!r\.ok\) return res\.status\(r\.status \|\| 502\)[\s\S]{0,120}ok: false/.test(LEADS_POST));
// R-F3999 — anchored to the UPSTREAM CALL, was a first-occurrence comparison.
//
// The contract is unchanged and still worth having: once we have called the
// brain, the `!r.ok` branch must return before any success reply, so an upstream
// failure can never be reported as a recorded lead.
//
// The old form compared the FIRST `res.status(200).json({ ok: true` anywhere in
// the route. C-80 added a deliberate early 200 BEFORE the fetch — the silent
// refusal for a tripped honeypot or a mail-bombed destination, which must look
// exactly like a success so a bot learns nothing — and that made this guard fail
// on correct code.
//
// Deleting or loosening it was the wrong repair: it is the guard that stops a
// fake "Thanks" when the brain is down, which is the defect R-F2581 exists for.
// Searching from the upstream call forward ignores anything that legitimately
// short-circuits BEFORE it, while still catching a fake success inserted after.
const _upstreamAt = LEADS_POST.indexOf('/api/aria/leads/inbound');
const _failAt = LEADS_POST.indexOf('if (!r.ok) return res.status', _upstreamAt);
const _okAt = LEADS_POST.indexOf('return res.status(200).json({ ok: true', _upstreamAt);
check('the success reply is reached only AFTER the failure branch has returned',
  _upstreamAt > -1 && _failAt > -1 && _okAt > -1 && _failAt < _okAt);
check('GET /api/leads is admin-gated (requireAdmin)',
  /app\.get\('\/api\/leads',\s*requireAdmin/.test(SERVER));
check('DELETE /api/leads/:leadId is admin-gated and forwarded as DELETE',
  /app\.delete\('\/api\/leads\/:leadId',\s*requireAdmin/.test(SERVER) &&
  /leads\/inbound\/' \+ encodeURIComponent\(req\.params\.leadId\)[\s\S]{0,100}method:\s*'DELETE'/.test(SERVER));

// ── 3. leads.html: honest status-aware states ────────────────────────────────
check('leads.html fetches /api/leads via status-aware probe', /API\.probe\('\/api\/leads'\)/.test(LEADS));
check('distinguishes 403 (not admin) from a load failure',
  /res\.status === 403/.test(LEADS) && /admin-only/.test(LEADS));
check('distinguishes a load failure from a real empty list',
  /Couldn\\?'t load leads/.test(LEADS) && /No access requests yet/.test(LEADS));
check('states the operator job and renders contactability, gaps and next action',
  /Access Requests/.test(LEADS) && /owned, auditable access decision/.test(LEADS) &&
  /trust_state/.test(LEADS) && /readiness/.test(LEADS) &&
  /a\.gaps/.test(LEADS) && /next_best_action/.test(LEADS));
check('offers a structured details action that can clear assessed gaps',
  /data-action="update_details"/.test(LEADS) &&
  /applyAction\(leadId, 'update_details', details\)/.test(LEADS));
check('does not overstate mailbox confirmation as identity verification',
  /mailbox confirmed/i.test(LEADS) && !/confirmed identity/i.test(LEADS) && !/\/100/.test(LEADS));
check('erasure UI requires a verified erasure receipt',
  /API\.del\('\/api\/leads\/'/.test(LEADS) &&
  /erasure_complete !== true/.test(LEADS) &&
  /Erase access request/.test(LEADS));

// ── 4. BEHAVIOURAL: mirror index.html's success gate ─────────────────────────
console.log('\nSuccess-gate mirror — "Thanks" only on a real recorded lead:');
function outcome(resp) {
  // mirror: Thanks only when r.ok && data.ok; else retry
  if (resp.ok && resp.data && resp.data.ok) return 'THANKS';
  return 'RETRY';
}
check('brain recorded it -> Thanks', outcome({ ok: true, data: { ok: true } }) === 'THANKS');
check('brain 503 (not recorded) -> Retry, not Thanks', outcome({ ok: false, data: { ok: false } }) === 'RETRY');
check('network error (no data) -> Retry', outcome({ ok: false, data: null }) === 'RETRY');
check('200 but ok:false -> Retry (never a false Thanks)', outcome({ ok: true, data: { ok: false } }) === 'RETRY');

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
