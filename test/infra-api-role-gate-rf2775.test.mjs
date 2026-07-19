// test/infra-api-role-gate-rf2775.test.mjs
//
// CAPABILITY test for R-F2775 — the operator/infra API role gate.
//
// Before R-F2775, every infra endpoint under /api/aria was `requireAuth`: ANY
// signed-up viewer could read the cost ledger, autonomy state, brain internals and
// student mastery, and could RUN seed sweeps, weekly adversarial runs and
// diagnostics. R-F2774 had already gated the operator PAGES; this closes the APIs
// behind them.
//
// Three layers, all against REAL production code:
//   1. the classification itself (lib/auth/infraRoutes.mjs)
//   2. a live Express app wired exactly as server.mjs wires it — the real gate
//      middleware composition, exercised over the wire for all four roles
//   3. the MOUNT-ORDER invariant in server.mjs, which is load-bearing: Express
//      matches in registration order, so a gate moved below the explicit
//      /api/aria routes silently stops protecting them.
//
// Run: node test/infra-api-role-gate-rf2775.test.mjs

import express from 'express';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, dirname } from 'node:path';

import { requiredRoleForAriaPath, isInfraAriaPath, INFRA_ARIA_PATHS } from '../lib/auth/infraRoutes.mjs';
import { roleSatisfies } from '../lib/auth/roles.mjs';
// R-F2739 hatch: this test boots an isolated Express app and drives it over the
// wire, so it needs loopback — and ONLY loopback. The guard still blocks anything
// that is not 127.0.0.1/localhost, so it can never reach production or the LAN.
import { allowLoopbackNetwork } from './helpers/net_guard.mjs';
allowLoopbackNetwork();

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

let failures = 0;
function check(name, cond) {
  console.log(`${cond ? 'ok  ' : 'FAIL'} - ${name}`);
  if (!cond) failures++;
}

// ── 1. CLASSIFICATION ────────────────────────────────────────────────────────

// Customer surface — must be untouched. A false positive here 403s a paying user,
// which is the failure mode this R-number is most afraid of.
const CUSTOMER = [
  '/chat', '/chat/stream', '/chat/result/job1', '/think', '/research', '/read',
  '/read-document', '/ocr', '/investigate', '/investigate/company', '/profile',
  '/crawl', '/extract-document', '/report', '/outcome', '/correct',
  '/dd/reports', '/dd/report/run1', '/dd/orchestrate', '/dd/watchlist',
  '/dd/watchlist/alerts', '/dd/vault/search', '/dd/vault/case/c1', '/dd/case/c1',
  '/dd/sources', '/dd/vls/key', '/dd/vls/proof', '/dd/vls/verify',
  '/rag/search', '/rag/ingest',
  '/compliance/screen', '/compliance/sanctions', '/compliance/classify',
  '/conversations', '/conversations/s1', '/session/s1',
  '/document/verify', '/document/correct', '/news/recent', '/news/poll',
  '/intel/signals/recent', '/opportunities', '/sanctions/rca', '/sanctions/fuzzy',
  '/security/counter-intel/scan', '/tech/classify', '/conflict/events/iraq',
  '/gtm/uk', '/query/decompose', '/proactive/lead-hunt', '/meeting-notes/process',
  '/user/sources', '/contacts', '/approach', '/ledger', '/knowledge/learn',
  '/identity', '/vision-status', '/trace/t1', '/scratchpad/t1', '/health',
];
for (const p of CUSTOMER) {
  check(`CUSTOMER untouched: ${p}`, requiredRoleForAriaPath('GET', p) === null
    && requiredRoleForAriaPath('POST', p) === null);
}

// Infra reads → poweruser (view-only operator access, R-F2773)
const INFRA_READS = [
  '/cost/monthly', '/cost/cumulative', '/autonomy/composite', '/autonomy/surface',
  '/autonomous/status', '/metrics/grounded_rate', '/predictor/block_rate',
  '/hallucination/stats', '/verification/stats', '/unguarded-fallback/stats',
  '/independence', '/consistency/scores', '/calibration/review',
  '/diagnostic/details', '/circuit-breakers', '/operating-mode',
  '/pending-actions', '/phase/gates', '/brain/stats', '/brain/alerts',
  '/brain/dashboard', '/knowledge/inventory', '/rag/stats', '/rag/sources',
  '/reasoning-library/stats', '/critique/stats', '/rlaif/stats',
  '/student/mastery/heatmap', '/learning/stats', '/training-data/stats',
  '/sources/uptime', '/sources/seed/catalogue', '/oem/contacts',
  '/dd/quarantine', '/dd/vls/chain',
];
for (const p of INFRA_READS) {
  check(`INFRA read → poweruser: ${p}`, requiredRoleForAriaPath('GET', p) === 'poweruser');
}

// Infra mutations → admin. This is what makes `poweruser` genuinely view-only.
const INFRA_WRITES = [
  '/adversarial/run_weekly', '/adversarial/amendments/approve',
  '/autonomy/baseline', '/consistency/run', '/calibration/auto-tune/run',
  '/diagnostic/run', '/rlaif/evaluate', '/sources/uptime/run',
  '/sources/uptime/suspend', '/sources/seed/run',
  '/reasoning-library/consolidate', '/rag/backfill', '/self/deploy/1',
  '/self/code', '/student/study',
];
for (const p of INFRA_WRITES) {
  check(`INFRA write → admin: ${p}`, requiredRoleForAriaPath('POST', p) === 'admin');
}
check('DELETE on infra → admin', requiredRoleForAriaPath('DELETE', '/cost/monthly') === 'admin');
check('PUT on infra → admin', requiredRoleForAriaPath('PUT', '/autonomy/baseline') === 'admin');
check('HEAD on infra read → poweruser', requiredRoleForAriaPath('HEAD', '/cost/monthly') === 'poweruser');

// Prefix matching must not bleed onto sibling paths.
check('prefix does not match a longer word (/costcentre)', isInfraAriaPath('/costcentre') === false);
check('prefix matches exact segment (/cost)', isInfraAriaPath('/cost') === true);
check('prefix matches child (/cost/monthly)', isInfraAriaPath('/cost/monthly') === true);
check('trailing slash tolerated', isInfraAriaPath('/cost/') === true);
check('query string stripped', isInfraAriaPath('/cost/monthly?x=1') === true);
check('INFRA_ARIA_PATHS frozen', Object.isFrozen(INFRA_ARIA_PATHS));

// The MIXED prefixes the audit specifically warned about: gating /dd or /rag
// wholesale would 403 paying customers.
check('MIXED /dd: infra sliver gated', isInfraAriaPath('/dd/quarantine') === true);
check('MIXED /dd: customer reports NOT gated', isInfraAriaPath('/dd/reports') === false);
check('MIXED /dd: customer vls/key NOT gated', isInfraAriaPath('/dd/vls/key') === false);
check('MIXED /dd: infra vls/chain gated', isInfraAriaPath('/dd/vls/chain') === true);
check('MIXED /rag: stats gated', isInfraAriaPath('/rag/stats') === true);
check('MIXED /rag: search NOT gated', isInfraAriaPath('/rag/search') === false);
check('MIXED /rag: ingest NOT gated', isInfraAriaPath('/rag/ingest') === false);

// ── 1b. CASE-FOLDING — a REPRODUCED bypass, not a hypothetical ───────────────
// Express disables `case sensitive routing` by default, so the explicit handlers
// answer a case-mangled path AND forward a hardcoded lowercase path upstream.
// Before the fix, on a live server with a viewer token:
//   GET  /api/aria/STUDENT/mastery/heatmap → 200   (lowercase form → 403)
//   POST /api/aria/SOURCES/uptime/run      → 200   (lowercase form → 403)
// A plain customer could read infra AND trigger an infra mutation by shouting one
// path segment. Every entry must classify identically under any casing.
for (const [upper, method, expected] of [
  ['/STUDENT/mastery/heatmap', 'GET', 'poweruser'],
  ['/Student/Mastery/Heatmap', 'GET', 'poweruser'],
  ['/SOURCES/uptime/run', 'POST', 'admin'],
  ['/ADVERSARIAL/run_weekly', 'POST', 'admin'],
  ['/Cost/Monthly', 'GET', 'poweruser'],
  ['/WA-AUTH/restore', 'GET', 'poweruser'],
]) {
  check(`CASE-FOLDED still gated: ${method} ${upper} → ${expected}`,
    requiredRoleForAriaPath(method, upper) === expected);
}
// Case folding must not accidentally gate a customer path.
check('CASE-FOLDED customer path stays ungated (/DD/reports)',
  requiredRoleForAriaPath('GET', '/DD/reports') === null);
check('CASE-FOLDED customer path stays ungated (/RAG/search)',
  requiredRoleForAriaPath('POST', '/RAG/search') === null);
// Duplicate slashes collapse (defence in depth).
check('duplicate slashes collapse (//cost/monthly)',
  requiredRoleForAriaPath('GET', '//cost/monthly') === 'poweruser');
// Dot-segments: Express strips these before req.path — VERIFIED live (the gate
// returned 403 for /api/aria/./cost/monthly). Asserted so the classifier stays
// correct if it is ever called with a non-normalised path.
check('dot-segment path still classified (Express normalises these anyway)',
  isInfraAriaPath('/cost/monthly') === true);

// ── 1c. Prefixes added by the Pass-2 review ─────────────────────────────────
// '/self' does NOT cover '/self-healing' — matchesPrefix needs an exact match or
// `prefix + '/'`, so these must be their own entries. Regression-guard that.
check('/self-restart/trigger gated (was a viewer-callable brain restart)',
  requiredRoleForAriaPath('POST', '/self-restart/trigger') === 'admin');
check('/self-healing/status gated', requiredRoleForAriaPath('GET', '/self-healing/status') === 'poweruser');
check('/wa-auth/restore gated (returns Baileys device auth bundle)',
  requiredRoleForAriaPath('GET', '/wa-auth/restore') === 'poweruser');
check('/mastery/heatmap gated (R-F677 alias of /student/mastery/heatmap)',
  requiredRoleForAriaPath('GET', '/mastery/heatmap') === 'poweruser');
check('/quality/grounded-rate gated (alias of /metrics/grounded_rate)',
  requiredRoleForAriaPath('GET', '/quality/grounded-rate') === 'poweruser');
check('/admin/wedge-stacks gated', requiredRoleForAriaPath('GET', '/admin/wedge-stacks') === 'poweruser');
check('/eval/golden/freeze gated (Phase A gate-#6 pin)',
  requiredRoleForAriaPath('POST', '/eval/golden/freeze') === 'admin');
check('/neural/stats gated', requiredRoleForAriaPath('GET', '/neural/stats') === 'poweruser');
// …and the NARROW entries must not swallow their customer siblings.
check('/security/api-monitor gated but /security/counter-intel NOT (explorer.html)',
  requiredRoleForAriaPath('GET', '/security/api-monitor/key') === 'poweruser'
  && requiredRoleForAriaPath('POST', '/security/counter-intel/scan') === null);
check('/trace/recent gated but /trace/:id NOT (customer)',
  requiredRoleForAriaPath('GET', '/trace/recent') === 'poweruser'
  && requiredRoleForAriaPath('GET', '/trace/abc-123') === null);
check('/memory/health gated but /memory/other NOT (kept narrow)',
  requiredRoleForAriaPath('GET', '/memory/health') === 'poweruser'
  && requiredRoleForAriaPath('GET', '/memory/recall') === null);

// ── 2. LIVE GATE over the wire, all four roles ───────────────────────────────
// Wired exactly as server.mjs wires it: public model-card routes registered FIRST
// (so they stay exempt), then the gate, then the explicit routes it must protect.

function buildApp() {
  const app = express();
  // stand-in for requireAuth: Bearer <role>, no header = anonymous
  const requireAuth = (req, res, next) => {
    const t = (req.headers.authorization || '').replace('Bearer ', '');
    if (!t) return res.status(401).json({ error: 'Authentication required' });
    req.user = { userId: 'u-' + t, role: t };
    next();
  };
  const requireRole = (...allowed) => (req, res, next) => requireAuth(req, res, () => {
    if (!roleSatisfies(req.user?.role, allowed)) {
      return res.status(403).json({ error: `Access requires role: ${allowed.join(' or ')}` });
    }
    next();
  });

  // R-F577 public model-card endpoints — registered BEFORE the gate, so exempt.
  app.get('/api/aria/adversarial/stats', (_q, s) => s.json({ public: true }));
  app.get('/api/aria/constitution/version', (_q, s) => s.json({ public: true }));

  // THE GATE (mirrors server.mjs)
  app.use('/api/aria', (req, res, next) => {
    const needed = requiredRoleForAriaPath(req.method, req.path);
    if (!needed) return next();
    return requireRole(needed)(req, res, next);
  });

  // explicit routes registered AFTER the gate, as in production
  app.use('/api/aria', requireAuth, (_q, s) => s.json({ served: true }));
  return app;
}

const app = buildApp();
const srv = await new Promise((r) => { const s = app.listen(0, () => r(s)); });
const PORT = srv.address().port;

async function hit(path, role, method = 'GET') {
  const headers = role ? { Authorization: 'Bearer ' + role } : {};
  const r = await fetch(`http://127.0.0.1:${PORT}${path}`, { method, headers });
  return r.status;
}

// Public model-card endpoints stay anonymous — the ordering exemption.
check('LIVE anon → /adversarial/stats 200 (public, registered pre-gate)',
  await hit('/api/aria/adversarial/stats', null) === 200);
check('LIVE anon → /constitution/version 200 (public)',
  await hit('/api/aria/constitution/version', null) === 200);

// THE DEFECT: a viewer reading infra. This is what used to return 200.
check('LIVE viewer → /cost/monthly 403 (WAS 200 — the defect)',
  await hit('/api/aria/cost/monthly', 'viewer') === 403);
check('LIVE viewer → /autonomy/composite 403',
  await hit('/api/aria/autonomy/composite', 'viewer') === 403);
check('LIVE viewer → /brain/stats 403',
  await hit('/api/aria/brain/stats', 'viewer') === 403);
check('LIVE viewer → POST /sources/seed/run 403 (WAS runnable)',
  await hit('/api/aria/sources/seed/run', 'viewer', 'POST') === 403);
check('LIVE support → /cost/monthly 403 (support is not an infra role)',
  await hit('/api/aria/cost/monthly', 'support') === 403);
check('LIVE anon → /cost/monthly 401',
  await hit('/api/aria/cost/monthly', null) === 401);

// poweruser: reads yes, mutations no — the whole point of the role.
check('LIVE poweruser → /cost/monthly 200',
  await hit('/api/aria/cost/monthly', 'poweruser') === 200);
check('LIVE poweruser → /sources/uptime 200',
  await hit('/api/aria/sources/uptime', 'poweruser') === 200);
check('LIVE poweruser → POST /sources/uptime/run 403 (view-only)',
  await hit('/api/aria/sources/uptime/run', 'poweruser', 'POST') === 403);
check('LIVE poweruser → POST /self/deploy/1 403 (view-only)',
  await hit('/api/aria/self/deploy/1', 'poweruser', 'POST') === 403);

// admin passes everything (admin ⊇ poweruser).
check('LIVE admin → /cost/monthly 200', await hit('/api/aria/cost/monthly', 'admin') === 200);
check('LIVE admin → POST /sources/uptime/run 200',
  await hit('/api/aria/sources/uptime/run', 'admin', 'POST') === 200);

// CUSTOMER NO-REGRESSION — the safety net. A viewer must keep every paid flow.
for (const p of ['/chat', '/dd/reports', '/dd/watchlist', '/conversations',
                 '/rag/search', '/compliance/screen', '/dd/vault/search',
                 '/dd/vls/key', '/news/recent', '/investigate']) {
  check(`LIVE viewer keeps customer route ${p} (200)`,
    await hit('/api/aria' + p, 'viewer') === 200);
}
check('LIVE viewer keeps POST /dd/orchestrate',
  await hit('/api/aria/dd/orchestrate', 'viewer', 'POST') === 200);
check('LIVE viewer keeps POST /rag/ingest',
  await hit('/api/aria/rag/ingest', 'viewer', 'POST') === 200);

// Await the close: process.exit() while the listener is still tearing down trips a
// libuv assertion on Windows (UV_HANDLE_CLOSING) and crashes an otherwise-green run.
await new Promise((r) => srv.close(r));

// ── 3. MOUNT-ORDER INVARIANT in server.mjs ───────────────────────────────────
// Express matches in registration order. If this gate is ever moved below the
// explicit /api/aria routes (or below the catch-all, where the original sketch
// put it), it silently stops protecting them — the endpoints keep serving and
// nothing fails loudly. Assert the order the design depends on.
{
  const src = readFileSync(join(ROOT, 'server.mjs'), 'utf8');
  const lines = src.split('\n');
  const idxOf = (pred) => lines.findIndex(pred);

  const gateIdx = idxOf((l) => l.includes('requiredRoleForAriaPath(req.method, req.path)'));
  const publicIdx = idxOf((l) => l.includes("app.get('/api/aria/adversarial/stats'"));
  const firstExplicit = idxOf((l) => /^app\.(get|post|put|patch|delete)\('\/api\/aria\/(?!constitution|chat-audit|adversarial|audit\/key-fingerprint|extract-document)/.test(l));
  const catchAll = idxOf((l) => l.includes("app.use('/api/aria', requireAuth"));

  check('gate is registered in server.mjs', gateIdx > 0);
  check('gate is AFTER the public model-card routes (they stay public)', publicIdx > 0 && gateIdx > publicIdx);
  check('gate is BEFORE the first explicit /api/aria route (else it protects nothing)',
    firstExplicit > 0 && gateIdx < firstExplicit);
  check('gate is BEFORE the catch-all proxy', catchAll > 0 && gateIdx < catchAll);
}

console.log(failures === 0 ? '\nPASS — all checks green' : `\nFAIL — ${failures} check(s) failed`);
// Set exitCode and let the event loop drain rather than calling process.exit():
// forcing exit while undici's keep-alive sockets are still closing trips a libuv
// assertion on Windows (UV_HANDLE_CLOSING) and crashes an otherwise-green run.
// Closing the global dispatcher releases those sockets so the process ends promptly.
process.exitCode = failures === 0 ? 0 : 1;
try {
  const { getGlobalDispatcher } = await import('undici');
  await getGlobalDispatcher().close();
} catch { /* undici not resolvable standalone — loop drains on keep-alive timeout */ }
