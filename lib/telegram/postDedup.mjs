// lib/telegram/postDedup.mjs
//
// R-F2321 — cross-day channel post dedup.
// ========================================
// The daily post could surface the SAME sanctions entity / tender on consecutive
// mornings (the sweep's top items are often stable for days), and the in-memory
// _postedHashes reset on every redeploy. This is a small PERSISTED, windowed
// dedup: a key posted within the last N days is suppressed, so the channel never
// repeats an item day-over-day. Persists to the Fly volume (/data), falls back to
// ./data in dev.

import fs from 'node:fs';
import path from 'node:path';

const WINDOW_DAYS = Number(process.env.CHANNEL_DEDUP_WINDOW_DAYS) || 7;
const MAX_ENTRIES = 2000;

function _statePath() {
  const envPath = process.env.CHANNEL_POST_DEDUP_PATH;
  if (envPath) return envPath;
  let dir = process.env.CHANNEL_DATA_DIR;
  if (!dir) { try { dir = fs.existsSync('/data') ? '/data' : path.join(process.cwd(), 'data'); } catch { dir = path.join(process.cwd(), 'data'); } }
  return path.join(dir, 'channel_post_dedup.json');
}

function _today() { return new Date().toISOString().slice(0, 10); }
function _daysBetween(a, b) { return Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000); }

function _load() {
  try {
    const raw = JSON.parse(fs.readFileSync(_statePath(), 'utf8'));
    const entries = Array.isArray(raw?.entries) ? raw.entries : [];
    const today = _today();
    // prune outside the window
    return entries.filter(e => e && e.k && e.d && _daysBetween(e.d, today) < WINDOW_DAYS);
  } catch { return []; }
}

function _save(entries) {
  try {
    const p = _statePath();
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, JSON.stringify({ entries: entries.slice(-MAX_ENTRIES) }, null, 0), 'utf8');
    return true;
  } catch (e) { console.warn('[PostDedup] persist failed:', e.message); return false; }
}

/** Normalise a signal into a stable dedup key (its title/entity, lower-cased). */
export function dedupKey(signal) {
  const t = String(signal?.title || signal?.text || '').toLowerCase().replace(/\s+/g, ' ').trim();
  return t.slice(0, 120);
}

/** @returns {boolean} true if this key was posted within the window. */
export function wasRecentlyPosted(key) {
  if (!key) return false;
  return _load().some(e => e.k === key);
}

/** Record keys as posted today (persists). Accepts a string or array. */
export function recordPosted(keys) {
  const arr = Array.isArray(keys) ? keys : [keys];
  const entries = _load();
  const today = _today();
  for (const k of arr) { if (k) entries.push({ k, d: today }); }
  return _save(entries);
}
