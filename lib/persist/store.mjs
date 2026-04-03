// lib/persist/store.mjs
// Central Redis + file dual-write persistence layer
//
// Every critical store writes to BOTH local file (fast) AND Upstash Redis (survives restarts).
// On startup, if the local file is missing, data is automatically restored from Redis.
// Same REST-based approach as dedup.mjs — no SDK, no new dependencies.

const REDIS_URL   = () => process.env.UPSTASH_REDIS_URL;
const REDIS_TOKEN = () => process.env.UPSTASH_REDIS_TOKEN;

export function redisConfigured() {
  return !!(REDIS_URL() && REDIS_TOKEN());
}

// ── Low-level Redis REST helpers ──────────────────────────────────────────────

export async function redisGet(key) {
  if (!redisConfigured()) return null;
  try {
    const res = await fetch(`${REDIS_URL()}/get/${encodeURIComponent(key)}`, {
      headers: { Authorization: `Bearer ${REDIS_TOKEN()}` },
      signal: AbortSignal.timeout(6000),
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (!data.result) return null;
    return JSON.parse(data.result);
  } catch {
    return null;
  }
}

export async function redisSet(key, value) {
  if (!redisConfigured()) return;
  try {
    // Upstash REST: SET key value EX seconds
    // 90-day TTL keeps data alive through extended outages
    await fetch(`${REDIS_URL()}/set/${encodeURIComponent(key)}`, {
      method:  'POST',
      headers: {
        Authorization:  `Bearer ${REDIS_TOKEN()}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ value: JSON.stringify(value), ex: 90 * 24 * 3600 }),
      signal: AbortSignal.timeout(6000),
    });
  } catch {}
}

export async function redisDel(key) {
  if (!redisConfigured()) return;
  try {
    await fetch(`${REDIS_URL()}/del/${encodeURIComponent(key)}`, {
      method:  'POST',
      headers: { Authorization: `Bearer ${REDIS_TOKEN()}` },
      signal: AbortSignal.timeout(5000),
    });
  } catch {}
}

// RPUSH a JSON-serialisable value onto a Redis list.
// Used to push signals into the brain queue (crucix:brain:incoming_signals).
export async function redisPush(key, value) {
  if (!redisConfigured()) return;
  try {
    // Upstash REST RPUSH: POST /rpush/<key> with body ["value"]
    await fetch(`${REDIS_URL()}/rpush/${encodeURIComponent(key)}`, {
      method:  'POST',
      headers: {
        Authorization:  `Bearer ${REDIS_TOKEN()}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify([JSON.stringify(value)]),
      signal: AbortSignal.timeout(6000),
    });
  } catch {}
}

// ── High-level dual-write store ───────────────────────────────────────────────
// Usage:
//   import { PersistStore } from '../persist/store.mjs';
//   const store = new PersistStore('crucix:bd_pipeline', filePath, defaultValue);
//   await store.init();          // on startup: file → Redis fallback restore
//   const data = store.read();   // synchronous, from in-memory cache
//   store.write(data);           // sync file write + async Redis write

import { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync } from 'fs';
import { dirname } from 'path';

export class PersistStore {
  constructor(redisKey, filePath, defaultValue = null) {
    this.key      = redisKey;
    this.path     = filePath;
    this.default  = defaultValue;
    this._cache   = null;
  }

  // Call once on server startup — restores from Redis if local file is missing
  async init() {
    if (existsSync(this.path)) {
      try {
        this._cache = JSON.parse(readFileSync(this.path, 'utf8'));
        console.log(`[Persist] Loaded ${this.key} from file (${this.path.split(/[\\/]/).pop()})`);
        return;
      } catch {
        console.warn(`[Persist] ${this.key}: file corrupt, attempting Redis restore`);
      }
    }

    // File missing or corrupt — try Redis
    if (redisConfigured()) {
      const remote = await redisGet(this.key);
      if (remote !== null) {
        this._cache = remote;
        this._writeFile(remote); // restore to disk
        console.log(`[Persist] Restored ${this.key} from Redis (${JSON.stringify(remote).length} bytes)`);
        return;
      }
    }

    // Neither file nor Redis — start fresh
    this._cache = typeof this.default === 'function' ? this.default() : this.default;
    console.log(`[Persist] ${this.key}: starting fresh (no existing data)`);
  }

  read() {
    if (this._cache !== null) return this._cache;
    // Lazy load if init() wasn't called
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
    // Async Redis backup — fire and forget, never blocks
    redisSet(this.key, data).catch(() => {});
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

  // Force sync from Redis (useful for multi-instance / after manual Redis edits)
  async syncFromRedis() {
    const remote = await redisGet(this.key);
    if (remote !== null) {
      this._cache = remote;
      this._writeFile(remote);
    }
    return this._cache;
  }
}
