// lib/billing/quotas.mjs
// Per-user, per-tier quota counters — daily messages, daily uploads,
// monthly DD runs. Backed by Redis keys with explicit TTLs so the
// counters reset themselves; no cron job needed.
//
// Usage:
//   const verdict = await checkAndConsume(userId, tierId, 'message');
//   if (!verdict.allowed) → return 429 with verdict.reason
//
// Three quota kinds (`kind` argument):
//   message   — every chat turn
//   upload    — every file upload
//   ddRun     — every DD orchestrator run (counterparty screening etc)
//
// Counters are GET-then-SET (not INCR) because lib/persist/store.mjs
// doesn't expose INCR yet. Race tolerance is acceptable for quotas: a
// concurrent burst from a single user might overshoot by 1-2; the
// downstream cost cap catches malicious abuse.

import { redisGet, redisSet, redisConfigured } from '../persist/store.mjs';
import { getTier, DEFAULT_TIER } from './tiers.mjs';

// In-memory fallback when Redis isn't configured (dev). Same shape as the
// Redis-backed counters. NOT shared across processes — fine for dev,
// production should always have Redis.
const _memCounters = new Map();
const _memReset = new Map();

function _utcDay() {
  const d = new Date();
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
}
function _utcMonth() {
  const d = new Date();
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

function _keyFor(userId, kind) {
  switch (kind) {
    case 'message': return { key: `crucix:quota:msg:${userId}:${_utcDay()}`, ttl: 36 * 3600 };
    case 'upload':  return { key: `crucix:quota:upl:${userId}:${_utcDay()}`, ttl: 36 * 3600 };
    case 'ddRun':   return { key: `crucix:quota:dd:${userId}:${_utcMonth()}`, ttl: 35 * 86400 };
    default:
      throw new Error(`Unknown quota kind: ${kind}`);
  }
}

function _capForKind(tier, kind) {
  switch (kind) {
    case 'message': return tier.messagesPerDay;
    case 'upload':  return tier.uploadsPerDay;
    case 'ddRun':   return tier.ddRunsPerMonth;
    default:        return 0;
  }
}

async function _readCount(key, ttl) {
  if (redisConfigured()) {
    try {
      const raw = await redisGet(key);
      const n = parseInt(raw, 10);
      return Number.isFinite(n) && n >= 0 ? n : 0;
    } catch {
      // fall through to mem
    }
  }
  // Memory fallback with simple expiry.
  const now = Date.now();
  const exp = _memReset.get(key) || 0;
  if (exp && exp <= now) {
    _memCounters.delete(key);
    _memReset.delete(key);
  }
  return _memCounters.get(key) || 0;
}

async function _writeCount(key, count, ttl) {
  if (redisConfigured()) {
    try {
      await redisSet(key, String(count), ttl);
      return;
    } catch {
      // fall through to mem
    }
  }
  _memCounters.set(key, count);
  _memReset.set(key, Date.now() + ttl * 1000);
}

// Read current usage without consuming. Used by /api/billing/me.
export async function readUsage(userId, tierId = DEFAULT_TIER) {
  const tier = getTier(tierId);
  const out = { messages: 0, uploads: 0, ddRuns: 0, caps: {
    messages: tier.messagesPerDay,
    uploads: tier.uploadsPerDay,
    ddRuns: tier.ddRunsPerMonth,
  }};
  for (const kind of ['message', 'upload', 'ddRun']) {
    const { key, ttl } = _keyFor(userId, kind);
    const n = await _readCount(key, ttl);
    if (kind === 'message') out.messages = n;
    else if (kind === 'upload') out.uploads = n;
    else if (kind === 'ddRun') out.ddRuns = n;
  }
  return out;
}

// Atomically check + consume one unit of the given quota. Returns
// { allowed, current, cap, reason }. Allowed=true means the unit is
// counted; allowed=false means cap was reached and nothing was consumed.
export async function checkAndConsume(userId, tierId, kind) {
  if (!userId) {
    return { allowed: false, current: 0, cap: 0, reason: 'no userId' };
  }
  const tier = getTier(tierId);
  const cap = _capForKind(tier, kind);
  if (cap === 0) {
    return { allowed: false, current: 0, cap: 0,
      reason: `${kind} not available on ${tier.label} tier` };
  }
  const { key, ttl } = _keyFor(userId, kind);
  const current = await _readCount(key, ttl);
  if (current >= cap) {
    return { allowed: false, current, cap,
      reason: `${kind} cap reached (${current}/${cap}) — resets at next ${kind === 'ddRun' ? 'month' : 'UTC day'}` };
  }
  await _writeCount(key, current + 1, ttl);
  return { allowed: true, current: current + 1, cap, reason: '' };
}

// Capability gate (boolean) — used for tier-only features that don't
// have a per-period counter, e.g. deepResearchEnabled.
export function tierAllows(tierId, capability) {
  const tier = getTier(tierId);
  return !!tier[capability];
}
