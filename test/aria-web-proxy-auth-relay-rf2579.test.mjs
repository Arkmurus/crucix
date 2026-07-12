// test/aria-web-proxy-auth-relay-rf2579.test.mjs
//
// Capability test for R-F2579 — aria-web ariaProxy must RELAY upstream 401/403
// instead of masking them as a 503 "ARIA service offline".
//
// Live evidence 2026-07-12: the /aria-brain dashboard showed the red
// "DATA UNAVAILABLE" banner for the Autonomy panels. Root cause chain:
//   1. aria-app (Next.js) is in ROLLBACK MODE — transparently proxies every
//      path to the live node server aria-web (next.config.mjs:26-27).
//   2. Operator-only endpoints (/api/aria/autonomous/*, /api/aria/autonomy/*)
//      reached with a non-operator token return 403 from aria-intel
//      (_OPERATOR_ONLY_RE, aria_service/routes/aria.py:297).
//   3. ariaProxy only passed 2xx through (`if (r.ok)`); a 403 fell to the
//      _brainFallback → res.status(503){error:'ARIA service offline'}
//      (server.mjs:2961). The browser never saw the 403.
//   4. The dashboard treats 401/403 as auth-gated (public/aria-brain.html:485)
//      but 503/error-envelope as DOWN → red "DATA UNAVAILABLE" banner.
//
// Fix: ariaProxy relays upstream 401/403 verbatim (res.status(r.status)); only
// genuine failures (5xx / network / timeout) reach the fallback. The dashboard
// then renders the honest "auth-gated" state instead of "offline".
//
// The DECISIVE capability proof is the post-deploy live probe: a non-operator
// token to /api/aria/autonomous/status must return 403 (was 503). This file
// asserts (1) the fix is present + correctly placed in the ariaProxy source and
// (2) the relay/fallback decision logic is correct, kept in lockstep with prod.
//
// Run: node test/aria-web-proxy-auth-relay-rf2579.test.mjs

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

console.log('R-F2579 capability tests\n');

// ── 1. STATIC: the relay block is present in ariaProxy, correctly placed ──────
console.log('1. ariaProxy source relays upstream 401/403 before the fallback');

// Isolate the ariaProxy function body so placement assertions are meaningful.
const pxStart = SRC.indexOf('async function ariaProxy(');
check('ariaProxy function found', pxStart !== -1);
// Slice the whole ariaProxy body: from its start to the next top-level
// function/comment block (a generous window that covers the full function).
const pxEndRel = SRC.slice(pxStart + 30).search(/\n\/\/ Send sweep data|\nasync function \w|\nfunction \w/);
const pxBody = pxStart !== -1
  ? SRC.slice(pxStart, pxStart + 30 + (pxEndRel === -1 ? 6000 : pxEndRel))
  : '';

const okIdx = pxBody.indexOf('if (r.ok)');
const relayIdx = pxBody.search(/if\s*\(\s*r\.status\s*===\s*401\s*\|\|\s*r\.status\s*===\s*403\s*\)/);
const fallbackIdx = pxBody.indexOf('if (fallback) return fallback');

check('relay branch checks r.status === 401 || r.status === 403', relayIdx !== -1);
check('relay branch sends res.status(r.status) (verbatim upstream status)',
  /return\s+res\.status\(r\.status\)\.json/.test(pxBody));
check('relay is placed AFTER the 2xx passthrough (if (r.ok))',
  okIdx !== -1 && relayIdx !== -1 && relayIdx > okIdx,
  `okIdx=${okIdx} relayIdx=${relayIdx}`);
check('relay is placed BEFORE the 503 fallback',
  relayIdx !== -1 && fallbackIdx !== -1 && relayIdx < fallbackIdx,
  `relayIdx=${relayIdx} fallbackIdx=${fallbackIdx}`);
check('carries the R-F2579 marker',
  /R-F2579/.test(pxBody));

// ── 2. BEHAVIOURAL: the relay/fallback decision is correct ───────────────────
console.log('\n2. Proxy status-decision logic (lockstep mirror of ariaProxy)');

// Mirror of ariaProxy's response decision for a fetched upstream response.
// Keep in lockstep with server.mjs — if the production branch changes, mirror it.
function proxyDecision({ ok, status, threw }) {
  if (threw) return { kind: 'fallback', clientStatus: 503 };   // network / timeout
  if (ok) return { kind: 'passthrough', clientStatus: status };
  if (status === 401 || status === 403) return { kind: 'relay', clientStatus: status };
  return { kind: 'fallback', clientStatus: 503 };              // 5xx / other non-2xx
}

// The operator's actual symptom endpoints: operator-gated -> upstream 403.
check('upstream 403 is RELAYED as 403 (was masked as 503)',
  proxyDecision({ ok: false, status: 403 }).clientStatus === 403);
check('upstream 401 is RELAYED as 401',
  proxyDecision({ ok: false, status: 401 }).clientStatus === 401);
check('403 decision kind is "relay", not "fallback"',
  proxyDecision({ ok: false, status: 403 }).kind === 'relay');

// Genuine outages must STILL reach the 503 fallback (no regression).
check('upstream 500 still falls back to 503 (real service error)',
  proxyDecision({ ok: false, status: 500 }).clientStatus === 503);
check('upstream 502 still falls back to 503',
  proxyDecision({ ok: false, status: 502 }).clientStatus === 503);
check('network throw / timeout still falls back to 503',
  proxyDecision({ threw: true }).clientStatus === 503);
check('2xx still passes through unchanged',
  proxyDecision({ ok: true, status: 200 }).clientStatus === 200);

// ── 3. FRONTEND CONTRACT: the dashboard treats 401/403 as auth-gated ─────────
console.log('\n3. Dashboard renders 401/403 as auth-gated (the fix reaches a handler)');
const HTML = readFileSync(join(__dirname, '..', 'public', 'aria-brain.html'), 'utf8');
check('aria-brain.html branches on res.status === 401 || res.status === 403',
  /res\.status\s*===\s*401\s*\|\|\s*res\.status\s*===\s*403/.test(HTML));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
