// lib/persist/store.mjs
// File-first persistence layer (R-F383: Upstash side retired).
//
// Every critical store reads + writes a local JSON file in /data (or runs/).
// The Redis dual-write that lived here from 2026-04-29 (F87/F88) through
// 2026-05-12 (R-F381 + R-F382) has been removed: fly.io migrated to SQLite
// (R-F261), seenode now runs file-only (R-F235 + R-F261 + this commit).
//
// The redisGet / redisSet / redisDel / redisPush / redisConfigured exports
// remain as no-ops so the ~20 importing modules don't need touching in this
// commit; their `if (redisConfigured()) { … }` branches naturally short-circuit.
// redisLockAcquire / redisLockRelease are kept functional via a process-local
// Map — seenode is single-instance, so a local mutex is sufficient to prevent
// two staggered cron callbacks racing on the same critical section (the only
// real caller is bd_intelligence.mjs's BD-sweep guard).

// ── Compatibility shims (no-ops since R-F383) ──────────────────────────────

export function redisConfigured() {
  return false;
}

export async function redisGet(_key) {
  return null;
}

export async function redisSet(_key, _value, _ttlSeconds) {
  return;
}

export async function redisDel(_key) {
  return;
}

export async function redisPush(_key, _value) {
  return;
}

// ── Process-local advisory lock (replaces former Redis SET NX EX) ──────────
//
// Single-instance seenode = the only concurrency we need to defend against is
// two async cron callbacks firing within the same Node process. A Map keyed
// by lock-name with an expires-at timestamp handles that.

const _localLocks = new Map(); // key → expiresAt (ms)

export async function redisLockAcquire(key, ttlSeconds = 60) {
  if (!key) return false;
  const now = Date.now();
  const existing = _localLocks.get(key);
  if (existing && existing > now) return false;
  _localLocks.set(key, now + Math.max(5, ttlSeconds) * 1000);
  return true;
}

export async function redisLockRelease(key) {
  if (!key) return;
  _localLocks.delete(key);
}

// ── High-level file-only PersistStore ──────────────────────────────────────
// Usage:
//   import { PersistStore } from '../persist/store.mjs';
//   const store = new PersistStore('crucix:bd_pipeline', filePath, defaultValue);
//   await store.init();          // loads from file, else uses default
//   const data = store.read();   // synchronous, from in-memory cache
//   store.write(data);           // sync file write (atomic via rename)
//
// The first constructor arg (redisKey) is kept for signature compatibility
// with the 18+ existing call sites. It is no longer used for persistence.

import { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync } from 'fs';
import { dirname } from 'path';

export class PersistStore {
  constructor(redisKey, filePath, defaultValue = null) {
    this.key      = redisKey;
    this.path     = filePath;
    this.default  = defaultValue;
    this._cache   = null;
  }

  async init() {
    if (existsSync(this.path)) {
      try {
        this._cache = JSON.parse(readFileSync(this.path, 'utf8'));
        console.log(`[Persist] Loaded ${this.key} from file (${this.path.split(/[\\/]/).pop()})`);
        return;
      } catch {
        console.warn(`[Persist] ${this.key}: file corrupt, starting fresh`);
      }
    }
    this._cache = typeof this.default === 'function' ? this.default() : this.default;
    console.log(`[Persist] ${this.key}: starting fresh (no existing data)`);
  }

  read() {
    if (this._cache !== null) return this._cache;
    try {
      if (existsSync(this.path)) {
        this._cache = JSON.parse(readFileSync(this.path, 'utf8'));
        return this._cache;
      }
    } catch {}
    this._cache = typeof this.default === 'function' ? this.default() : this.default;
    return this._cache;
  }

  write(data) {
    this._cache = data;
    this._writeFile(data);
  }

  _writeFile(data) {
    try {
      mkdirSync(dirname(this.path), { recursive: true });
      const tmp = this.path + '.tmp';
      writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf8');
      renameSync(tmp, this.path);
    } catch (e) {
      console.warn(`[Persist] File write failed for ${this.key}:`, e.message);
    }
  }

  async syncFromRedis() {
    return this._cache;
  }
}
