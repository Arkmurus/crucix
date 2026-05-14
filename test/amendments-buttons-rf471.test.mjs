// test/amendments-buttons-rf471.test.mjs
//
// Capability test for R-F471 — Pending Amendments Approve/Reject buttons.
//
// Operator report 2026-05-14 chat:
//   "buttons not working to approve or to reject"
//   Queue had 11 amendments staged.
//
// The endpoint side (POST /api/aria/adversarial/amendments/approve +
// /reject) is reachable — live probe confirmed 401 (auth-gated, exists).
// The bug is on the UI side: the original handlers swallowed every
// failure mode (modal didn't open, API non-standard error shape, exception
// in click handler) without ever reaching the operator. R-F471 wraps both
// handlers with try/catch + step-by-step toast feedback, and adds a
// DOMContentLoaded self-check that paints the JS error banner if any
// handler is missing.
//
// This test is a static-source guardrail (the only deterministic check
// that works without a browser). Verifies:
//   1. Both handlers have try/catch around the modal call
//   2. Both handlers have try/catch around the API.post call
//   3. Both handlers surface the actual HTTP status + body in the toast
//      on failure (not just "network error")
//   4. The DOMContentLoaded self-check is wired in and references the
//      handler names + #op-modal + #js-error-banner
//   5. The R-F441 #js-error-banner element is still present (regression)
//
// Run: node test/amendments-buttons-rf471.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(
  join(__dirname, '..', 'public', 'aria-brain.html'),
  'utf8'
);

let failures = 0;
function check(label, cond, extra) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${extra ? ' — ' + extra : ''}`); failures += 1; }
}

console.log('R-F471 capability tests\n');

// ── 1. rejectAmendment is defensive ─────────────────────────────────────
console.log('1. rejectAmendment: defensive wrap');
{
  const fnStart = HTML.indexOf('async function rejectAmendment');
  check('rejectAmendment defined', fnStart > -1);
  const fnEnd = HTML.indexOf('\n}', fnStart);
  const body = HTML.slice(fnStart, fnEnd);
  check('try around showOperatorModal',
        /try\s*\{[\s\S]+?await showOperatorModal[\s\S]+?\}\s*catch/.test(body));
  check('try around API.post',
        /try\s*\{[\s\S]+?await API\.post[\s\S]+?\}\s*catch/.test(body));
  check('toast surfaces HTTP status',
        body.includes('HTTP ${resp ? resp.status'));
  check('toast falls back to JSON body when no detail',
        body.includes('JSON.stringify(resp.data)'));
}

// ── 2. approveAmendment is defensive ────────────────────────────────────
console.log('\n2. approveAmendment: defensive wrap');
{
  const fnStart = HTML.indexOf('async function approveAmendment');
  check('approveAmendment defined', fnStart > -1);
  const fnEnd = HTML.indexOf('\n}', fnStart);
  const body = HTML.slice(fnStart, fnEnd);
  check('try around showOperatorModal',
        /try\s*\{[\s\S]+?await showOperatorModal[\s\S]+?\}\s*catch/.test(body));
  check('try around API.post',
        /try\s*\{[\s\S]+?await API\.post[\s\S]+?\}\s*catch/.test(body));
  check('toast surfaces HTTP status',
        body.includes('HTTP ${resp ? resp.status'));
  check('toast falls back to JSON body when no detail',
        body.includes('JSON.stringify(resp.data)'));
  check('phrase APPROVE still required',
        body.includes("phrase: 'APPROVE'"));
}

// ── 3. DOMContentLoaded self-check exists ───────────────────────────────
console.log('\n3. DOMContentLoaded self-check (handler-presence probe)');
{
  const idx = HTML.indexOf("addEventListener('DOMContentLoaded'");
  check('DOMContentLoaded listener registered', idx > -1);
  const window2k = HTML.slice(idx, idx + 2000);
  check('self-check names approveAmendment',  window2k.includes('approveAmendment'));
  check('self-check names rejectAmendment',   window2k.includes('rejectAmendment'));
  check('self-check names bulkRejectStale',   window2k.includes('bulkRejectStale'));
  check('self-check names showOperatorModal', window2k.includes('showOperatorModal'));
  check('self-check checks #op-modal element', window2k.includes('op-modal'));
  check('self-check paints #js-error-banner',  window2k.includes('js-error-banner'));
  check('self-check prompts hard-refresh',     window2k.toLowerCase().includes('hard-refresh'));
}

// ── 4. R-F441 #js-error-banner + boot listeners still present (regression)
console.log('\n4. R-F441 boot-time JS-error capture intact (regression guard)');
{
  check('#js-error-banner element present', HTML.includes('id="js-error-banner"'));
  check("window.addEventListener('error'", HTML.includes("window.addEventListener('error'"));
  check("window.addEventListener('unhandledrejection'",
        HTML.includes("window.addEventListener('unhandledrejection'"));
}

// ── 5. R-F433 inline modal still present (regression) ──────────────────
console.log('\n5. R-F433 inline operator modal intact (regression guard)');
{
  check('#op-modal exists',           HTML.includes('id="op-modal"'));
  check('#op-modal-submit exists',    HTML.includes('id="op-modal-submit"'));
  check('#op-modal-cancel exists',    HTML.includes('id="op-modal-cancel"'));
  check('showOperatorModal defined',  HTML.includes('function showOperatorModal'));
}

console.log(`\nR-F471 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
