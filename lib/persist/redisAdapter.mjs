/**
 * Upstash Redis Adapter
 *
 * Wraps the Upstash REST API in a standard Redis client interface so that
 * the server integration modules (errorTracker, listRefresher, sourceMaintenance,
 * procurementDedup) can call redis.get/set/hget/hset/keys/lpush/ltrim without
 * knowing about the REST layer.
 *
 * Uses POST / with ["COMMAND", ...args] format for all write operations.
 * Uses GET /command/args for read operations (Upstash REST standard).
 */

const REDIS_URL   = () => process.env.UPSTASH_REDIS_URL;
const REDIS_TOKEN = () => process.env.UPSTASH_REDIS_TOKEN;

const headers = () => ({
  Authorization:  `Bearer ${REDIS_TOKEN()}`,
  'Content-Type': 'application/json',
});

const ok = () => !!(REDIS_URL() && REDIS_TOKEN());

// Read operations — GET /command/args (works reliably)
async function upstashGet(path) {
  if (!ok()) return null;
  try {
    const res = await fetch(`${REDIS_URL()}${path}`, {
      method: 'GET',
      headers: headers(),
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.result ?? null;
  } catch {
    return null;
  }
}

// Write operations — POST / with ["COMMAND", ...args] (reliable for all value types)
async function upstashCmd(...args) {
  if (!ok()) return null;
  try {
    const res = await fetch(`${REDIS_URL()}`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(args),
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.result ?? null;
  } catch {
    return null;
  }
}

class UpstashRedisAdapter {
  get isConfigured() { return ok(); }

  /** GET key → raw string or null */
  async get(key) {
    return upstashGet(`/get/${encodeURIComponent(key)}`);
  }

  /** SET key value [EX seconds] */
  async set(key, value, opts = {}) {
    if (opts.ex) {
      return upstashCmd('SET', key, value, 'EX', opts.ex);
    }
    return upstashCmd('SET', key, value);
  }

  /** SETEX key seconds value */
  async setex(key, seconds, value) {
    return upstashCmd('SET', key, value, 'EX', seconds);
  }

  /** DEL key */
  async del(key) {
    return upstashCmd('DEL', key);
  }

  /** LPUSH key value(s) */
  async lpush(key, ...values) {
    return upstashCmd('LPUSH', key, ...values);
  }

  /** RPUSH key value(s) */
  async rpush(key, ...values) {
    return upstashCmd('RPUSH', key, ...values);
  }

  /** LTRIM key start stop */
  async ltrim(key, start, stop) {
    return upstashCmd('LTRIM', key, start, stop);
  }

  /** KEYS pattern — use sparingly */
  async keys(pattern) {
    const result = await upstashGet(`/keys/${encodeURIComponent(pattern)}`);
    return Array.isArray(result) ? result : [];
  }

  /** HGET key field */
  async hget(key, field) {
    return upstashGet(`/hget/${encodeURIComponent(key)}/${encodeURIComponent(field)}`);
  }

  /** HSET key field value */
  async hset(key, field, value) {
    return upstashCmd('HSET', key, field, value);
  }

  /** HDEL key field */
  async hdel(key, field) {
    return upstashCmd('HDEL', key, field);
  }

  /** HGETALL key → object or null */
  async hgetall(key) {
    const result = await upstashGet(`/hgetall/${encodeURIComponent(key)}`);
    if (!result || typeof result !== 'object') return null;
    // Upstash returns alternating [field, value, ...] array; convert to object
    if (Array.isArray(result)) {
      const obj = {};
      for (let i = 0; i < result.length; i += 2) obj[result[i]] = result[i + 1];
      return obj;
    }
    return result;
  }

  /** EXPIRE key seconds */
  async expire(key, seconds) {
    return upstashCmd('EXPIRE', key, seconds);
  }
}

// Singleton — share one adapter across all modules
export const redisAdapter = new UpstashRedisAdapter();
