// lib/api_keys/routes.mjs
// Two routers:
//   - createKeysRouter(...)  — /api/keys/* CRUD, JWT-auth (the user manages
//     their own keys from account.html)
//   - createV1Router(...)    — /api/v1/*, API-key auth (the public surface)
//
// Both gated on ENABLE_PUBLIC_API=1. When unset:
//   * /api/keys returns 503 from the first byte (account.html hides the
//     panel based on the response).
//   * /api/v1 returns 503 → no public access until operator flips the
//     env var.
//
// Tier gate: only the proIntel tier gets API access (per
// docs/chat_ui_launch_decisions.md and lib/billing/tiers.mjs). Admin role
// bypasses the tier gate (operator + Arkmurus team can use API keys
// regardless of subscription).

import express from 'express';
import {
  issueKey,
  listKeysForUser,
  revokeKey,
  authenticateKey,
  keyHasScope,
  scopesFor,
} from './store.mjs';
import { tierAllows, checkAndConsume } from '../billing/quotas.mjs';
import { DEFAULT_TIER } from '../billing/tiers.mjs';
// R-F386: per-key rate limiter migrated to process-local Map (was Upstash-
// backed sliding window). Single-instance seenode means a process-local
// counter is sufficient and matches the existing race tolerance noted below.

export function publicApiEnabled() {
  return ['1', 'true', 'yes'].includes(
    (process.env.ENABLE_PUBLIC_API || '').toLowerCase().trim(),
  );
}

// ── /api/keys/* — user manages their own keys (JWT-auth) ────────────────
export function createKeysRouter({ requireAuth, findUserById }) {
  if (!requireAuth || !findUserById) {
    throw new Error('createKeysRouter: missing requireAuth/findUserById');
  }
  const router = express.Router();

  // Soft-rollout gate: every route 503s when public API is off so the FE
  // can hide the panel based on the response.
  router.use((req, res, next) => {
    if (!publicApiEnabled()) {
      return res.status(503).json({ error: 'public API not enabled', enabled: false });
    }
    next();
  });

  router.get('/', requireAuth, (req, res) => {
    const userId = req.user?.userId;
    if (!userId) return res.status(401).json({ error: 'auth required' });
    res.json({ keys: listKeysForUser(userId), enabled: true });
  });

  router.post('/', requireAuth, (req, res) => {
    const userId = req.user?.userId;
    if (!userId) return res.status(401).json({ error: 'auth required' });
    const user = findUserById(userId);
    if (!user) return res.status(404).json({ error: 'user not found' });
    // Tier gate: proIntel only (admin bypass).
    if (user.role !== 'admin' && !tierAllows(user.tier || DEFAULT_TIER, 'publicApiEnabled')) {
      return res.status(403).json({
        error: 'API access requires the Pro Intelligence tier',
        currentTier: user.tier || DEFAULT_TIER,
      });
    }
    const name = (req.body?.name || '').toString().trim() || 'API key';
    // R-F3139 — scopes are opt-IN. An omitted/empty list yields DEFAULT_SCOPES
    // (chat only), so no existing caller silently gains vetting access.
    const scopes = Array.isArray(req.body?.scopes) ? req.body.scopes : null;
    const { record, plaintext } = issueKey({ userId, name, scopes });
    // The plaintext is returned ONCE here. The frontend MUST surface it
    // to the user with a "copy now, you won't see it again" message.
    res.json({ key: { ...record, plaintext } });
  });

  router.delete('/:id', requireAuth, (req, res) => {
    const userId = req.user?.userId;
    if (!userId) return res.status(401).json({ error: 'auth required' });
    const ok = revokeKey({ userId, id: req.params.id });
    if (!ok) return res.status(404).json({ error: 'key not found' });
    res.json({ revoked: true, id: req.params.id });
  });

  return router;
}

// ── /api/v1/* — the public API surface (API-key auth) ──────────────────
//
// This router proxies requests INTO the existing /api/aria/chat path on
// the same process. We don't make a network round-trip; we extract the
// chat behaviour into a function the v1 handler can call directly.
//
// For now this router exposes:
//   POST /api/v1/chat — { message, session_id?, stream? }
//
// Future endpoints (not in this commit):
//   POST /api/v1/dd
//   POST /api/v1/research
// `authenticateKeyFn` is injected for the same reason findUserById/chatProxy
// already are: without it the only way to test the scope gate is to
// re-implement the router's logic in the test file (as R-F386's test had to),
// and a test that re-implements the thing it checks cannot catch a change to
// the real thing. Defaults to the real store lookup, so production is
// unchanged.
export function createV1Router({
  findUserById, chatProxy, vettingProxy = null,
  authenticateKeyFn = authenticateKey,
}) {
  if (!findUserById || !chatProxy) {
    throw new Error('createV1Router: missing findUserById/chatProxy');
  }
  const router = express.Router();

  // R-F3139 — scope gate factory. Returns 403 with the scope actually needed,
  // so a caller can tell "your key can't do this" from "you're not allowed".
  const requireScope = (scope) => (req, res, next) => {
    if (!keyHasScope(req.apiKey, scope)) {
      return res.status(403).json({
        error: `this API key lacks the '${scope}' scope`,
        granted: scopesFor(req.apiKey),
      });
    }
    next();
  };

  // R-F844 (2026-05-23): unauthenticated, ungated health endpoint so
  // external monitors can distinguish "service is alive but public API
  // is off" from "service is unreachable". MUST be registered before
  // the publicApiEnabled gate below so it returns 200 either way.
  // Returns just enough state for a probe (no secrets, no PII).
  router.get('/health', (_req, res) => {
    res.json({
      status: 'ok',
      public_api_enabled: publicApiEnabled(),
      service: 'aria-web',
      ts: new Date().toISOString(),
    });
  });

  router.use((req, res, next) => {
    if (!publicApiEnabled()) {
      return res.status(503).json({ error: 'public API not enabled', enabled: false });
    }
    next();
  });

  // API-key auth middleware.
  router.use(express.json({ limit: '500kb' }));
  router.use(async (req, res, next) => {
    const auth = req.headers.authorization || '';
    const m = auth.match(/^Bearer\s+(.+)$/i);
    if (!m) return res.status(401).json({ error: 'missing Authorization: Bearer crx_… header' });
    const presented = m[1].trim();
    const keyRecord = authenticateKeyFn(presented);
    if (!keyRecord) return res.status(401).json({ error: 'invalid or revoked API key' });

    const user = findUserById(keyRecord.userId);
    if (!user) return res.status(401).json({ error: 'API key references unknown user' });
    if (user.status && user.status !== 'active') {
      return res.status(403).json({ error: `account ${user.status} — contact support` });
    }
    if (user.role !== 'admin' && !tierAllows(user.tier || DEFAULT_TIER, 'publicApiEnabled')) {
      return res.status(403).json({
        error: 'API access requires the Pro Intelligence tier',
      });
    }

    // Per-key rate limit: 60 req/min sliding window. This is independent
    // of the user-level message quota (which still applies as a daily
    // cap below). Two layers because a single key can DoS the daily
    // budget; the per-minute cap shapes the curve.
    if (!await _perKeyRateOk(keyRecord.id)) {
      return res.status(429).json({ error: 'rate limit exceeded — 60 req/min per key' });
    }

    // Daily message quota — same counter the chat path consumes for
    // logged-in users. API access counts against it.
    const verdict = await checkAndConsume(user.id, user.tier || DEFAULT_TIER, 'message');
    if (!verdict.allowed) {
      return res.status(429).json({ error: verdict.reason });
    }

    req.apiKey = keyRecord;
    req.user = { userId: user.id, role: user.role || 'viewer' };
    next();
  });

  // ── /api/v1/vetting/* — R-F3139 ────────────────────────────────────────
  //
  // Thin, scope-gated pass-through to the aria-intel routes added by R-F3138.
  // The tenant is ALWAYS the key's owner (req.user.userId), never anything the
  // caller sends: the Python side reads `user_id`, and letting a client set it
  // would undo the whole boundary. Same reason the /api/aria catch-all pins it.
  if (vettingProxy) {
    const vetting = express.Router();
    vetting.use(requireScope('vetting'));

    vetting.get('/packs', async (req, res) => {
      await vettingProxy({ res, method: 'GET', path: '/packs',
                           userId: req.user.userId });
    });
    vetting.post('/cases', async (req, res) => {
      await vettingProxy({ res, method: 'POST', path: '/cases',
                           userId: req.user.userId, body: req.body });
    });
    vetting.get('/cases', async (req, res) => {
      await vettingProxy({ res, method: 'GET', path: '/cases',
                           userId: req.user.userId });
    });
    vetting.get('/cases/:caseId', async (req, res) => {
      await vettingProxy({ res, method: 'GET',
                           path: `/case/${encodeURIComponent(req.params.caseId)}`,
                           userId: req.user.userId });
    });
    vetting.post('/cases/:caseId/assess', async (req, res) => {
      const asOf = (req.body?.as_of || req.query?.as_of || '').toString();
      await vettingProxy({
        res, method: 'POST',
        path: `/case/${encodeURIComponent(req.params.caseId)}/assess`,
        userId: req.user.userId,
        query: asOf ? { as_of: asOf } : {},
      });
    });

    router.use('/vetting', vetting);
  }

  router.post('/chat', requireScope('chat'), async (req, res) => {
    const message = (req.body?.message || '').toString();
    if (!message) return res.status(400).json({ error: 'message required' });
    const sessionId = (req.body?.session_id || '').toString();
    try {
      const result = await chatProxy({
        userId: req.user.userId,
        message,
        sessionId,
      });
      res.json(result);
      // R-F99 (2026-05-09): record this query against the API monitor
      // (R-F83) so per-key behavioural anomaly detection has data.
      // Fire-and-forget — never blocks the response.
      _recordToApiMonitor(req.apiKey?.id, message, false).catch(() => {});
    } catch (err) {
      console.error('[v1.chat] proxy error:', err);
      res.status(500).json({ error: 'upstream error', detail: err.message });
      _recordToApiMonitor(req.apiKey?.id, message, true).catch(() => {});
    }
  });

  return router;
}

// ── R-F99 (2026-05-09): public API monitor recorder ──────────────────
//
// Fire-and-forget POST to the fly /api/aria/security/api-monitor/record
// endpoint after every public-API call so R-F83 has data to score.
// Failures are silently swallowed — recording must never break the
// public chat path.
async function _recordToApiMonitor(keyId, query, refused) {
  if (!keyId || !query) return;
  const flyUrl = (process.env.ARIA_BRAIN_URL
                  || process.env.ARIA_FLY_URL
                  || process.env.BRAIN_URL
                  || 'https://aria-intel.fly.dev').replace(/\/$/, '');
  const token = (process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '').trim();
  if (!token) return;
  try {
    await fetch(`${flyUrl}/api/aria/security/api-monitor/record`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({
        key_id: keyId,
        query: String(query).slice(0, 500),
        refused: !!refused,
      }),
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    // Silent — monitor failures must not affect the public API path
  }
}


// ── Per-key rate limiter (process-local Map, 60/min) ──────────────────
//
// R-F386: in-process counter keyed by (apiKeyId, currentMinute). Each
// counter naturally expires when the minute rolls over (next bucket key).
// A periodic sweep prunes stale buckets so the Map never grows unbounded
// across long-lived processes. Single-instance seenode = no cross-process
// race. Race tolerance within a single process is acceptable: a single
// API key sending 60 req/sec might overshoot by 1-2, a vanishingly small
// fraction of the cap.
const _perKeyCounts = new Map(); // `${keyId}:${minute}` → count
let _perKeySweepAt = 0;

async function _perKeyRateOk(keyId) {
  const minute = Math.floor(Date.now() / 60000);
  const cap = parseInt(process.env.PUBLIC_API_RATE_PER_MIN || '60', 10) || 60;
  const bucketKey = `${keyId}:${minute}`;

  // Lazy sweep — prune every minute. The Map grows by at most one entry
  // per (key, minute) pair, so unbounded growth is only a concern for
  // very high-cardinality key sets — but the sweep keeps it tidy.
  if (Date.now() - _perKeySweepAt > 60_000) {
    _perKeySweepAt = Date.now();
    for (const k of _perKeyCounts.keys()) {
      const m = parseInt(k.split(':').pop(), 10);
      if (m < minute - 1) _perKeyCounts.delete(k); // keep current + previous bucket
    }
  }

  const count = _perKeyCounts.get(bucketKey) || 0;
  if (count >= cap) return false;
  _perKeyCounts.set(bucketKey, count + 1);
  return true;
}
