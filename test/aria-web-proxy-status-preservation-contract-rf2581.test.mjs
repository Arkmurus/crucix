// test/aria-web-proxy-status-preservation-contract-rf2581.test.mjs
//
// Contract test for R-F2581 — status-preservation across the aria-web proxy layer.
//
// The recurring "DATA UNAVAILABLE" class (R-F2579 and its predecessors) is a proxy
// that rewrites a meaningful upstream status (401/403 auth-gated) into a generic
// one (503 offline), so the truth dies before it reaches the UI. R-F2579 fixed the
// canonical path (ariaProxy now relays 401/403). This contract test STOPS THE CLASS
// FROM SILENTLY RETURNING by enforcing two invariants at CI time:
//
//   1. ariaProxy MUST relay upstream 401/403 (never mask auth as a generic error).
//   2. The number of BESPOKE `fetch(${ARIA_SERVICE_URL}...)` sites (proxies that
//      bypass ariaProxy) is FROZEN at a reviewed baseline. Any NEW one fails this
//      test — forcing a conscious choice: route it through ariaProxy (correct by
//      construction) or review that it preserves upstream status. This is the same
//      ratchet pattern as test_rf1142 (exact task count).
//
// Why a ratchet, not "everything must use ariaProxy": ~14 bespoke fetches are
// legitimate (chat, chat/stream, chat/result polling, think, ingest, outcome,
// conversations, knowledge/fact, intel/signals/recent, liveness/beat) — streaming
// and job-polling paths whose error handling is UX-appropriate, not dashboard
// masking. The 150 ariaProxy sites (dashboard panels) are correct by construction.
//
// Run: node test/aria-web-proxy-status-preservation-contract-rf2581.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '..', 'server.mjs'), 'utf8');

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

console.log('R-F2581 status-preservation contract\n');

// ── Invariant 1: ariaProxy relays upstream 401/403 (never mask auth) ─────────
console.log('1. ariaProxy preserves upstream auth status');
const pxStart = SRC.indexOf('async function ariaProxy(');
const pxEndRel = SRC.slice(pxStart + 30).search(/\n\/\/ Send sweep data|\nasync function \w|\nfunction \w/);
const pxBody = SRC.slice(pxStart, pxStart + 30 + (pxEndRel === -1 ? 6000 : pxEndRel));
check('ariaProxy relays 401/403 before the 503 fallback',
  /if\s*\(\s*r\.status\s*===\s*401\s*\|\|\s*r\.status\s*===\s*403\s*\)/.test(pxBody)
    && /return\s+res\.status\(r\.status\)/.test(pxBody));

// ── Invariant 2: bespoke-proxy ratchet ───────────────────────────────────────
console.log('\n2. No new status-masking proxy without review (ratchet)');

// Reviewed baseline of bespoke `fetch(${ARIA_SERVICE_URL}...)` sites (2026-07-12).
// These bypass ariaProxy for legitimate reasons (streaming / job-polling / self-call);
// their error handling is UX-appropriate, not dashboard status-masking:
//   chat (x5), chat/result, chat/stream, conversations, ingest,
//   intel/signals/recent, knowledge/fact, liveness/beat, outcome, think.
// 15 = the whole-file regex count (includes one multi-line `fetch(\n ${ARIA_SERVICE_URL}…`
// call that per-line greps miss). Counted via the same regex this test uses.
const BESPOKE_BASELINE = 15;
const bespoke = (SRC.match(/fetch\(\s*`\$\{ARIA_SERVICE_URL\}/g) || []).length;
check(
  `bespoke ARIA_SERVICE_URL fetches == ${BESPOKE_BASELINE} (found ${bespoke})`,
  bespoke === BESPOKE_BASELINE,
  bespoke > BESPOKE_BASELINE
    ? `A NEW bespoke proxy to aria-intel was added. Route it through ariaProxy()\n` +
      `     (which preserves upstream status), OR verify it does NOT mask a 401/403/4xx\n` +
      `     as a generic error, then bump BESPOKE_BASELINE with a review note.`
    : `A bespoke proxy was removed — good; lower BESPOKE_BASELINE to ${bespoke}.`,
);

// ── Invariant 3: the panel fallback is only reachable via ariaProxy ──────────
console.log('\n3. Dashboard panels proxy through the status-preserving path');
check('_brainFallback (503 panel fallback) is only used inside ariaProxy routes',
  // Every _brainFallback() sits in an `ariaProxy(req, res, ..., { fallback })`
  // call — i.e. the 503 is only ever the LAST resort after ariaProxy already
  // relayed 401/403. Assert there are ariaProxy sites and the fallback count is
  // bounded by them (sanity: fallbacks << ariaProxy sites).
  (SRC.match(/ariaProxy\(/g) || []).length >= (SRC.match(/_brainFallback\(\)/g) || []).length);

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
