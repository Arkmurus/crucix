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

import { existsSync, readFileSync, writeFileSync, mkdirSync, renameSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { redisGet, redisSet, redisConfigured } from '../persist/store.mjs';
import { getTier, DEFAULT_TIER } from './tiers.mjs';

// In-memory fallback when Redis isn't configured (dev). Same shape as the
// Redis-backed counters. NOT shared across processes — fine for dev,
// production should always have Redis.
const _memCounters = new Map();
const _memReset = new Map();

// ── R-F2829 — durable counters ───────────────────────────────────────────────
//
// These counters used to live ONLY in the two Maps above. The redis path they
// appear to use is dead: R-F383 retired Upstash and left redisGet/redisSet/
// redisConfigured as no-ops (`redisConfigured()` returns a hardcoded false).
// That was safe for the FILE-FIRST stores — their short-circuit lands on a JSON
// file on the /data volume — but quotas.mjs had no file fallback, so its
// short-circuit landed in process memory. Every restart and every deploy reset
// every counter to zero: the 5-runs-per-month DD cap (tiers.mjs) was in practice
// "5 per deploy", and aria-web was deployed nine times in a single day.
//
// Same disease lib/auth/users.mjs:18-21 already documents — state that looked
// persisted, lived in a process, and silently reset (there it churned user ids and
// orphaned conversation history). Same cure: PERSIST_DIR, the /data fly volume.
//
// Persistence must NEVER deny a paying user: every failure path below degrades to
// the in-memory behaviour rather than throwing into the request.
const _PERSIST_DIR = process.env.PERSIST_DIR || join(__dirname_quotas(), '..', '..', 'runs');
const _QUOTA_FILE = process.env.QUOTA_FILE_OVERRIDE || join(_PERSIST_DIR, 'quotas.json');
let _diskLoaded = false;
let _diskBroken = false;   // one-shot: stop retrying a hopeless path every request

function __dirname_quotas() {
  return dirname(fileURLToPath(import.meta.url));
}

/** Load persisted counters into the Maps once per process. Never throws. */
function _loadFromDisk() {
  if (_diskLoaded) return;
  _diskLoaded = true;
  try {
    if (!existsSync(_QUOTA_FILE)) return;
    const raw = JSON.parse(readFileSync(_QUOTA_FILE, 'utf8'));
    const now = Date.now();
    for (const [key, rec] of Object.entries(raw || {})) {
      if (!rec || typeof rec.count !== 'number') continue;
      // An elapsed period must NOT carry its count forward — that would deny a
      // user their new month's allowance.
      if (rec.exp && rec.exp <= now) continue;
      _memCounters.set(key, rec.count);
      if (rec.exp) _memReset.set(key, rec.exp);
    }
  } catch (e) {
    console.warn('[Quotas] could not load persisted counters:', e.message);
  }
}

/** Write the live counters back, pruning expired entries. Never throws. */
function _saveToDisk() {
  if (_diskBroken) return;
  try {
    const now = Date.now();
    const out = {};
    for (const [key, count] of _memCounters) {
      const exp = _memReset.get(key) || 0;
      if (exp && exp <= now) continue;          // prune — bounds the file
      out[key] = { count, exp };
    }
    if (!existsSync(_PERSIST_DIR)) mkdirSync(_PERSIST_DIR, { recursive: true });
    // Atomic-ish: write then rename, so a crash mid-write cannot leave a truncated
    // file that would zero every counter on the next boot.
    const tmp = `${_QUOTA_FILE}.tmp`;
    writeFileSync(tmp, JSON.stringify(out));
    renameSync(tmp, _QUOTA_FILE);
  } catch (e) {
    _diskBroken = true;
    console.error('[Quotas] PERSISTENCE FAILED — counters are now memory-only and ' +
      'will reset on restart:', e.message);
  }
}

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
  // R-F2829 — durable-backed memory. _loadFromDisk() hydrates the Maps once per
  // process from the /data volume, so a restart no longer zeroes every counter.
  _loadFromDisk();
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
  _loadFromDisk();
  _memCounters.set(key, count);
  _memReset.set(key, Date.now() + ttl * 1000);
  _saveToDisk();   // R-F2829 — write through, or the count dies with the process
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
