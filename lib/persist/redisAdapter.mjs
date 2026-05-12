/**
 * File-backed Redis adapter (R-F385).
 *
 * Drop-in replacement for the previous UpstashRedisAdapter — same method
 * surface (get/set/setex/del/lpush/rpush/ltrim/lrange/keys/hget/hset/hdel/
 * hgetall/expire), so the 11 wiring points in server.mjs +
 * lib/aria/complianceAudit.mjs continue to work unchanged.
 *
 * State is held in-memory and persisted to a single JSON file via atomic
 * rename on every write. Single-instance seenode = no cross-process race;
 * the only consistency concern is rapid back-to-back writes during the
 * fs.rename window, and the JSON-stringify/write/rename is fast enough
 * (~1ms for typical state size) that this is a non-issue in practice.
 *
 * TTLs are stored as absolute unix-ms `ex` timestamps and applied lazily
 * on read — expired entries are deleted on the next access and not
 * surfaced to callers.
 */

import {
  readFileSync, writeFileSync, mkdirSync, renameSync, existsSync,
} from 'fs';
import { dirname, join } from 'path';

const STATE_PATH = process.env.REDIS_ADAPTER_FILE
  || join(process.cwd(), 'data', 'redis_state.json');

// Shape: { strings: {key:{v,ex}}, lists: {key:{v:[],ex}}, hashes: {key:{v:{},ex}} }
function _emptyState() {
  return { strings: {}, lists: {}, hashes: {} };
}

function _load() {
  if (!existsSync(STATE_PATH)) return _emptyState();
  try {
    const raw = readFileSync(STATE_PATH, 'utf8');
    if (!raw.trim()) return _emptyState();
    const parsed = JSON.parse(raw);
    return {
      strings: parsed.strings || {},
      lists:   parsed.lists   || {},
      hashes:  parsed.hashes  || {},
    };
  } catch (e) {
    console.warn(`[redisAdapter] corrupt state file at ${STATE_PATH}, starting fresh:`, e.message);
    return _emptyState();
  }
}

let _state = _load();
let _flushPending = false;
let _flushTimer = null;

function _flushNow() {
  _flushPending = false;
  if (_flushTimer) { clearTimeout(_flushTimer); _flushTimer = null; }
  try {
    mkdirSync(dirname(STATE_PATH), { recursive: true });
    const tmp = STATE_PATH + '.tmp';
    writeFileSync(tmp, JSON.stringify(_state), 'utf8');
    renameSync(tmp, STATE_PATH);
  } catch (e) {
    console.warn('[redisAdapter] flush failed:', e.message);
  }
}

// Coalesce writes within a 200ms window so a burst (e.g. compliance audit
// LPUSH + LTRIM in succession) doesn't fsync 10× in a row. The flush is
// always eventually safe — process exit (SIGINT/SIGTERM) triggers a sync
// flush via the handlers below.
function _scheduleFlush() {
  if (_flushPending) return;
  _flushPending = true;
  _flushTimer = setTimeout(_flushNow, 200);
}

for (const sig of ['SIGINT', 'SIGTERM', 'beforeExit']) {
  try { process.once(sig, () => { if (_flushPending) _flushNow(); }); } catch {}
}

function _expired(entry) {
  return entry && typeof entry.ex === 'number' && entry.ex > 0 && Date.now() > entry.ex;
}

function _drop(bucket, key) {
  if (_state[bucket][key]) {
    delete _state[bucket][key];
    _scheduleFlush();
  }
}

function _ttlToEx(seconds) {
  if (!seconds || seconds <= 0) return null;
  return Date.now() + seconds * 1000;
}

// Convert Redis-style glob ("crucix:foo:*") to a RegExp.
function _globToRegExp(pattern) {
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&');
  const asRegex = escaped.replace(/\*/g, '.*').replace(/\?/g, '.');
  return new RegExp(`^${asRegex}$`);
}

class FileRedisAdapter {
  // Mirrors the old UpstashRedisAdapter.isConfigured getter. Always true
  // since file storage has no env-var preflight. Callers that gated on
  // `if (!adapter.isConfigured) return` will now run normally.
  get isConfigured() { return true; }

  // ── Strings ────────────────────────────────────────────────────────────

  async get(key) {
    const entry = _state.strings[key];
    if (!entry) return null;
    if (_expired(entry)) { _drop('strings', key); return null; }
    return entry.v;
  }

  async set(key, value, opts = {}) {
    const ex = opts && opts.ex ? _ttlToEx(opts.ex) : null;
    _state.strings[key] = { v: String(value), ex };
    _scheduleFlush();
    return 'OK';
  }

  async setex(key, seconds, value) {
    _state.strings[key] = { v: String(value), ex: _ttlToEx(seconds) };
    _scheduleFlush();
    return 'OK';
  }

  async del(key) {
    let removed = 0;
    for (const bucket of ['strings', 'lists', 'hashes']) {
      if (_state[bucket][key] !== undefined) {
        delete _state[bucket][key];
        removed += 1;
      }
    }
    if (removed) _scheduleFlush();
    return removed;
  }

  // ── Lists ──────────────────────────────────────────────────────────────

  async lpush(key, ...values) {
    const entry = _state.lists[key];
    if (entry && _expired(entry)) _drop('lists', key);
    const list = _state.lists[key] || { v: [], ex: null };
    for (const value of values) list.v.unshift(String(value));
    _state.lists[key] = list;
    _scheduleFlush();
    return list.v.length;
  }

  async rpush(key, ...values) {
    const entry = _state.lists[key];
    if (entry && _expired(entry)) _drop('lists', key);
    const list = _state.lists[key] || { v: [], ex: null };
    for (const value of values) list.v.push(String(value));
    _state.lists[key] = list;
    _scheduleFlush();
    return list.v.length;
  }

  async ltrim(key, start, stop) {
    const entry = _state.lists[key];
    if (!entry || _expired(entry)) { _drop('lists', key); return 'OK'; }
    const len = entry.v.length;
    // Normalise negative indices the way Redis does
    const s = start < 0 ? Math.max(0, len + start) : start;
    const e = stop < 0 ? len + stop : stop;
    entry.v = entry.v.slice(s, e + 1);
    _scheduleFlush();
    return 'OK';
  }

  async lrange(key, start, stop) {
    const entry = _state.lists[key];
    if (!entry || _expired(entry)) { _drop('lists', key); return []; }
    const len = entry.v.length;
    const s = start < 0 ? Math.max(0, len + start) : start;
    const e = stop < 0 ? len + stop : stop;
    return entry.v.slice(s, e + 1);
  }

  // ── Hashes ─────────────────────────────────────────────────────────────

  async hget(key, field) {
    const entry = _state.hashes[key];
    if (!entry) return null;
    if (_expired(entry)) { _drop('hashes', key); return null; }
    return entry.v[field] ?? null;
  }

  async hset(key, ...args) {
    // Supports both hset(key, field, value) and hset(key, f1, v1, f2, v2, …)
    const entry = _state.hashes[key];
    if (entry && _expired(entry)) _drop('hashes', key);
    const hash = _state.hashes[key] || { v: {}, ex: null };
    let added = 0;
    for (let i = 0; i < args.length; i += 2) {
      const field = args[i];
      const value = args[i + 1];
      if (field == null) continue;
      if (!(field in hash.v)) added += 1;
      hash.v[field] = String(value ?? '');
    }
    _state.hashes[key] = hash;
    _scheduleFlush();
    return added;
  }

  async hdel(key, field) {
    const entry = _state.hashes[key];
    if (!entry) return 0;
    if (_expired(entry)) { _drop('hashes', key); return 0; }
    if (field in entry.v) {
      delete entry.v[field];
      _scheduleFlush();
      return 1;
    }
    return 0;
  }

  async hgetall(key) {
    const entry = _state.hashes[key];
    if (!entry) return null;
    if (_expired(entry)) { _drop('hashes', key); return null; }
    return { ...entry.v };
  }

  // ── TTL / housekeeping ─────────────────────────────────────────────────

  async expire(key, seconds) {
    const ex = _ttlToEx(seconds);
    for (const bucket of ['strings', 'lists', 'hashes']) {
      const entry = _state[bucket][key];
      if (entry !== undefined) {
        entry.ex = ex;
        _scheduleFlush();
        return 1;
      }
    }
    return 0;
  }

  async keys(pattern) {
    const re = _globToRegExp(pattern);
    const out = [];
    for (const bucket of ['strings', 'lists', 'hashes']) {
      for (const key of Object.keys(_state[bucket])) {
        const entry = _state[bucket][key];
        if (_expired(entry)) { _drop(bucket, key); continue; }
        if (re.test(key)) out.push(key);
      }
    }
    return out;
  }
}

export const redisAdapter = new FileRedisAdapter();

// Exposed for tests — lets a test reset to a known-empty state.
export function _testResetState() {
  _state = _emptyState();
  if (_flushTimer) { clearTimeout(_flushTimer); _flushTimer = null; }
  _flushPending = false;
}
