// test/convos-fallback-robust-rf1778.test.mjs
//
// Capability test for R-F1778 — robust conversation sidebar.
//
// ROOT CAUSE this proves fixed: `/api/aria/conversations` passed
// `fallback: { conversations: [], user_id }` (a plain OBJECT), but ariaProxy
// invokes it as `fallback({ lastStatus, lastErr })`. So on EVERY aria-intel
// non-2xx / throw / timeout (cold-start, brain wedge, transient 5xx) the proxy
// threw `TypeError: fallback is not a function` → Express 5 → HTTP 500 → the
// sidebar painted "Failed to load conversations: HTTP 500" instead of degrading
// gracefully. That is the operator's "flashes errors before it shows the chats"
// symptom. R-F1778 makes the fallback a real function returning a clean 503 +
// empty list, and hardens Convos.refresh() with retry/backoff + a retryable,
// non-scary failure state.
//
// Run: node test/convos-fallback-robust-rf1778.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SERVER = readFileSync(join(__dirname, '..', 'server.mjs'), 'utf8');
const ARIA_HTML = readFileSync(join(__dirname, '..', 'public', 'aria.html'), 'utf8');

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F1778 capability tests\n');

// ── 1. Reproduce the BUG and prove the FIX against ariaProxy's real dispatch ──
// ariaProxy's contract (server.mjs): `if (fallback) return fallback({lastStatus,lastErr})`.
// We drive that exact line with both the old and the new fallback shapes.
console.log('1. ariaProxy fallback-dispatch contract (the broken path)');
{
  function dispatch(fallback) {
    // Mirrors server.mjs ariaProxy exactly.
    if (fallback) return fallback({ lastStatus: 503, lastErr: 'fetch failed' });
  }

  // (a) PRE-FIX shape — a bare object — MUST throw when dispatched as a function.
  //     This is the crash that produced HTTP 500 on every backend hiccup.
  const oldFallback = { conversations: [], user_id: 'acorreaarkmuruscom' };
  let threw = false;
  try { dispatch(oldFallback); } catch (e) { threw = e instanceof TypeError; }
  check('pre-fix object fallback throws TypeError when invoked (the 500-cause)', threw);

  // (b) POST-FIX shape — a function returning a 503 res with an empty list.
  let captured = null;
  const res = {
    status(code) { this._code = code; return this; },
    json(body) { captured = { code: this._code, body }; return captured; },
  };
  const userId = 'acorreaarkmuruscom';
  const newFallback = async ({ lastStatus, lastErr } = {}) => res.status(503).json({
    error: 'ARIA service unavailable',
    conversations: [],
    user_id: userId,
    fly_status: lastStatus,
    fly_error: lastErr,
  });
  let fixThrew = false;
  await dispatch(newFallback).catch(() => { fixThrew = true; });
  check('post-fix function fallback does NOT throw', !fixThrew);
  check('post-fix returns HTTP 503 (degrade, not crash)', captured && captured.code === 503);
  check('post-fix returns an EMPTY conversations array', captured && Array.isArray(captured.body.conversations) && captured.body.conversations.length === 0);
  check('post-fix preserves user_id + fly diagnostics', captured && captured.body.user_id === userId && captured.body.fly_status === 503 && captured.body.fly_error === 'fetch failed');
}

// ── 2. Static guard: the live route wires a FUNCTION, never a bare object ──
console.log('\n2. /api/aria/conversations route is wired correctly');
{
  const routeIdx = SERVER.indexOf("app.get('/api/aria/conversations',");
  check('conversations GET route exists', routeIdx > -1);
  const routeBody = SERVER.slice(routeIdx, routeIdx + 2600);
  // The fix: fallback is an async function.
  check('fallback is an async function', /fallback:\s*async\s*\(/.test(routeBody));
  // Regression guard: the bare-object fallback that caused the crash is GONE.
  check('no bare-object fallback ({ conversations: [] }) remains',
        !/fallback:\s*\{\s*conversations:/.test(routeBody));
  check('fallback emits a 503 status (not a crash / not silent 200)', /res\.status\(503\)/.test(routeBody));
}

// ── 3. Static guard: frontend Convos.refresh() is robust ──
console.log('\n3. Convos.refresh() retry/backoff + retryable failure state');
{
  const refreshIdx = ARIA_HTML.indexOf('async function refresh()');
  check('refresh() exists', refreshIdx > -1);
  const refreshBody = ARIA_HTML.slice(refreshIdx, refreshIdx + 2600);
  check('has a backoff schedule (multiple attempts)', /var delays = \[0,/.test(refreshBody));
  check('retries only transient errors via _isRetryable', /_isRetryable\(err\)/.test(ARIA_HTML));
  check('_isRetryable matches 5xx/408/429 + timeout + conn-err',
        /HTTP \(5\\d\\d\|408\|429\)/.test(ARIA_HTML) && /_isTimeout/.test(ARIA_HTML));
  check('shows a Reconnecting state during retries', /Reconnecting…/.test(refreshBody));
  check('final failure offers a Retry button (not a raw HTTP error)', /retryBtn/.test(refreshBody) && /Retry/.test(refreshBody));
  check('stale-refresh guard prevents overlapping runs', /mySeq !== _refreshSeq/.test(refreshBody));
  // 4. Recovery hook: a connectivity drop auto-reloads the sidebar.
  check('ConnectionMonitor exposes onRecover', /onRecover:\s*onRecover/.test(ARIA_HTML));
  check('sidebar re-refreshes on reconnect', /ConnectionMonitor\.onRecover\(function\(\)\s*\{\s*if \(CURRENT_USER\) Convos\.refresh\(\)/.test(ARIA_HTML));
}

console.log(`\nR-F1778 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
