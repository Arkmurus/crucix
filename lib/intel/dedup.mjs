// lib/intel/dedup.mjs
// Signal deduplication + severity scoring
// Prevents duplicate Telegram alerts and ranks signals by risk level

import { createHash } from 'crypto';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join } from 'path';

const STORE_FILE = join(process.cwd(), 'runs', 'sent_signals.json');
const MAX_STORE_AGE_HOURS = 48;

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
      if (text.includes(kw)) {
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
function hashSignal(signal) {
  const text = (signal.text || signal.headline || signal.title || '').substring(0, 120);
  return createHash('sha256').update(text).digest('hex').substring(0, 16);
}

// ── Load the sent signal store ───────────────────────────────────────────────
function loadStore() {
  try {
    if (!existsSync(STORE_FILE)) return {};
    return JSON.parse(readFileSync(STORE_FILE, 'utf8'));
  } catch { return {}; }
}

// ── Save the store ────────────────────────────────────────────────────────────
function saveStore(store) {
  try {
    writeFileSync(STORE_FILE, JSON.stringify(store, null, 2), 'utf8');
  } catch (e) {
    console.error('[Dedup] Save error:', e.message);
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

// ── Filter signals to only those not yet sent ─────────────────────────────────
export function filterNewSignals(signals) {
  let store = pruneStore(loadStore());
  const newSignals = [];

  for (const signal of signals) {
    const hash = hashSignal(signal);
    if (!store[hash]) {
      store[hash] = Date.now();
      newSignals.push({ ...signal, _hash: hash });
    }
  }

  saveStore(store);
  return newSignals;
}

// ── Mark signals as sent (call after successful Telegram send) ────────────────
export function markSignalsSent(signals) {
  let store = pruneStore(loadStore());
  for (const s of signals) {
    const hash = s._hash || hashSignal(s);
    store[hash] = Date.now();
  }
  saveStore(store);
}

// ── Combined: filter new + rank by severity ───────────────────────────────────
export function processSignals(signals) {
  const newOnes = filterNewSignals(signals);
  return rankSignals(newOnes);
}
