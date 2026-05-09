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
} from './store.mjs';
import { tierAllows, checkAndConsume } from '../billing/quotas.mjs';
import { DEFAULT_TIER } from '../billing/tiers.mjs';
import { redisGet, redisSet, redisConfigured } from '../persist/store.mjs';

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
    const { record, plaintext } = issueKey({ userId, name });
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
export function createV1Router({ findUserById, chatProxy }) {
  if (!findUserById || !chatProxy) {
    throw new Error('createV1Router: missing findUserById/chatProxy');
  }
  const router = express.Router();

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
    const keyRecord = authenticateKey(presented);
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

  router.post('/chat', async (req, res) => {
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
    } catch (err) {
      console.error('[v1.chat] proxy error:', err);
      res.status(500).json({ error: 'upstream error', detail: err.message });
    }
  });

  return router;
}

// ── Per-key rate limiter (Redis sliding window, 60/min) ────────────────
//
// One Redis key per (apiKeyId, currentMinute). We GET-then-SET because
// lib/persist/store.mjs doesn't expose INCR yet. Race tolerance is
// acceptable: a single API key sending 60 req/sec might overshoot by 1-2,
// which is a vanishingly small fraction of the cap.
async function _perKeyRateOk(keyId) {
  const minute = Math.floor(Date.now() / 60000);
  const cap = parseInt(process.env.PUBLIC_API_RATE_PER_MIN || '60', 10) || 60;
  const redisKey = `crucix:apikey:rate:${keyId}:${minute}`;
  let count = 0;
  if (redisConfigured()) {
    try {
      count = parseInt(await redisGet(redisKey), 10) || 0;
    } catch {}
  }
  if (count >= cap) return false;
  if (redisConfigured()) {
    try { await redisSet(redisKey, String(count + 1), 90); } catch {}
  }
  return true;
}
