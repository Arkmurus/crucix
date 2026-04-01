// lib/intel/dedup.mjs
// Signal deduplication + severity scoring
// Prevents duplicate Telegram alerts and ranks signals by risk level
//
// Storage: in-memory (primary) → Upstash Redis (persist across Render restarts)
//          falls back to file store when Redis is not configured

import { createHash } from 'crypto';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join } from 'path';

const STORE_FILE         = join(process.cwd(), 'runs', 'sent_signals.json');
const MAX_STORE_AGE_HOURS = 48;
const REDIS_KEY           = 'crucix:dedup';

// In-memory store — authoritative within a session, restored from Redis on init
let _memStore = null;

// ── Upstash Redis helpers (REST API, no SDK needed) ───────────────────────────
function redisConfigured() {
  return !!(process.env.UPSTASH_REDIS_URL && process.env.UPSTASH_REDIS_TOKEN);
}

async function redisGet(key) {
  try {
    const res = await fetch(`${process.env.UPSTASH_REDIS_URL}/get/${key}`, {
      headers: { Authorization: `Bearer ${process.env.UPSTASH_REDIS_TOKEN}` },
      signal: AbortSignal.timeout(5000),
    });
    const data = await res.json();
    return data.result ? JSON.parse(data.result) : null;
  } catch { return null; }
}

async function redisSet(key, value) {
  try {
    await fetch(`${process.env.UPSTASH_REDIS_URL}/set/${key}`, {
      method: 'POST',
      headers: {
        Authorization:  `Bearer ${process.env.UPSTASH_REDIS_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ value: JSON.stringify(value) }),
      signal: AbortSignal.timeout(5000),
    });
  } catch {}
}

// ── Initialise: load from Redis or file on first access ──────────────────────
export async function initDedup() {
  if (redisConfigured()) {
    const remote = await redisGet(REDIS_KEY);
    if (remote && typeof remote === 'object') {
      _memStore = pruneStore(remote);
      console.log(`[Dedup] Loaded ${Object.keys(_memStore).length} hashes from Upstash Redis`);
      return;
    }
  }
  _memStore = pruneStore(loadStoreFromFile());
  console.log(`[Dedup] Loaded ${Object.keys(_memStore).length} hashes from file store`);
}

function getMemStore() {
  if (_memStore === null) _memStore = pruneStore(loadStoreFromFile());
  return _memStore;
}

// ── Severity keyword scoring ─────────────────────────────────────────────────
// Each keyword adds to the score. Score >= threshold = critical.
const SEVERITY_KEYWORDS = {
  critical: {
    keywords: ['strike', 'attack', 'explosion', 'missile', 'nuclear', 'chemical weapon',
               'imminent', 'invasion', 'coup', 'assassin', 'emergency', 'evacuate',
               'martial law', 'war declared', 'sanctions imposed', 'blockade'],
    score: 10
  },
  high: {
    keywords: ['conflict', 'military', 'troops', 'armed', 'killed', 'casualties',
               'escalat', 'threaten', 'intercept', 'deploy', 'mobiliz', 'crisis',
               'seized', 'detained', 'arrested', 'bombing', 'drone', 'warship'],
    score: 5
  },
  medium: {
    keywords: ['tension', 'protest', 'unrest', 'sanction', 'tariff', 'embargo',
               'disputed', 'contested', 'warning', 'alert', 'monitor', 'concern'],
    score: 2
  }
};

const CRITICAL_THRESHOLD = 10;
const HIGH_THRESHOLD     = 5;

// ── Score a single signal ────────────────────────────────────────────────────
export function scoreSignal(signal) {
  const text = ((signal.text || signal.headline || signal.title || '') + ' ' +
                (signal.content || signal.summary || '')).toLowerCase();

  let score = 0;
  const matchedKeywords = [];

  for (const [level, { keywords, score: pts }] of Object.entries(SEVERITY_KEYWORDS)) {
    for (const kw of keywords) {
      // Use word boundaries so "couple" doesn't match "coup", "nuclear" doesn't
      // match "nuclearly", etc. Multi-word keywords use \s+ between words.
      const re = new RegExp(`\\b${kw.replace(/ /g, '\\s+')}\\b`, 'i');
      if (re.test(text)) {
        score += pts;
        matchedKeywords.push(kw);
      }
    }
  }

  let severity;
  if (score >= CRITICAL_THRESHOLD) severity = 'critical';
  else if (score >= HIGH_THRESHOLD) severity  = 'high';
  else if (score >= 2)              severity  = 'medium';
  else                              severity  = 'low';

  return { score, severity, matchedKeywords };
}

// ── Score and sort an array of signals ───────────────────────────────────────
export function rankSignals(signals) {
  return signals
    .map(s => ({ ...s, ...scoreSignal(s) }))
    .sort((a, b) => b.score - a.score);
}

// ── Hash a signal for dedup ──────────────────────────────────────────────────
// Normalise before hashing so minor wording/formatting variations don't slip through.
// If a signal carries a stable `dedupKey` (e.g. commodity symbol+direction), use that
// directly so floating values like percentages don't produce a new hash every sweep.
function hashSignal(signal) {
  if (signal.dedupKey) {
    return createHash('sha256').update(signal.dedupKey).digest('hex').substring(0, 16);
  }
  const raw = (signal.text || signal.headline || signal.title || '');
  const normalized = raw
    .toLowerCase()
    .replace(/^\[[\w\s\/\-]+\]\s*/g, '')          // strip leading [SOURCE] tag
    .replace(/\b\d{1,2}[:/]\d{2}(:\d{2})?\s*(am|pm|utc|gmt)?\b/gi, '') // strip timestamps
    .replace(/\s+/g, ' ')
    .trim()
    .substring(0, 120);
  return createHash('sha256').update(normalized || raw.substring(0, 120)).digest('hex').substring(0, 16);
}

// ── File store (fallback) ─────────────────────────────────────────────────────
function loadStoreFromFile() {
  try {
    if (!existsSync(STORE_FILE)) return {};
    return JSON.parse(readFileSync(STORE_FILE, 'utf8'));
  } catch { return {}; }
}

function saveStoreToFile(store) {
  try {
    writeFileSync(STORE_FILE, JSON.stringify(store, null, 2), 'utf8');
  } catch (e) {
    console.error('[Dedup] File save error:', e.message);
  }
}

// ── Persist to both file and Redis (async, fire-and-forget) ──────────────────
function persistStore(store) {
  saveStoreToFile(store);
  if (redisConfigured()) {
    redisSet(REDIS_KEY, store).catch(() => {});
  }
}

// ── Prune entries older than MAX_STORE_AGE_HOURS ─────────────────────────────
function pruneStore(store) {
  const cutoff = Date.now() - MAX_STORE_AGE_HOURS * 60 * 60 * 1000;
  const pruned = {};
  for (const [hash, ts] of Object.entries(store)) {
    if (ts > cutoff) pruned[hash] = ts;
  }
  return pruned;
}

// ── Peek: check which signals are new WITHOUT marking them as seen ────────────
// Use this to inspect new signals without committing them to the store.
export function peekNewSignals(signals) {
  const store = pruneStore(getMemStore());
  return signals.filter(s => !store[hashSignal(s)]);
}

// ── Filter signals to only those not yet sent ─────────────────────────────────
export function filterNewSignals(signals) {
  const store      = pruneStore(getMemStore());
  _memStore        = store;
  const newSignals = [];

  for (const signal of signals) {
    const hash = hashSignal(signal);
    if (!store[hash]) {
      store[hash] = Date.now();
      newSignals.push({ ...signal, _hash: hash });
    }
  }

  if (newSignals.length > 0) persistStore(store);
  return newSignals;
}

// ── Mark signals as sent (call after successful Telegram send) ────────────────
export function markSignalsSent(signals) {
  const store = pruneStore(getMemStore());
  _memStore   = store;
  for (const s of signals) {
    const hash = s._hash || hashSignal(s);
    store[hash] = Date.now();
  }
  persistStore(store);
}

// ── Combined: filter new + rank by severity ───────────────────────────────────
export function processSignals(signals) {
  const newOnes = filterNewSignals(signals);
  return rankSignals(newOnes);
}
