/**
 * Upstash Redis Adapter
 *
 * Wraps the Upstash REST API in a standard Redis client interface so that
 * the serverintegration modules (errorTracker, listRefresher, sourceMaintenance,
 * procurementDedup) can call redis.get/set/hget/hset/keys/lpush/ltrim without
 * knowing about the REST layer.
 *
 * Values are stored as raw strings (modules JSON.stringify before calling set).
 */

const REDIS_URL   = () => process.env.UPSTASH_REDIS_URL;
const REDIS_TOKEN = () => process.env.UPSTASH_REDIS_TOKEN;

const headers = () => ({
  Authorization:  `Bearer ${REDIS_TOKEN()}`,
  'Content-Type': 'application/json',
});

const ok = () => !!(REDIS_URL() && REDIS_TOKEN());

async function upstash(method, path, body) {
  if (!ok()) return null;
  try {
    const res = await fetch(`${REDIS_URL()}${path}`, {
      method,
      headers: headers(),
      body: body !== undefined ? JSON.stringify(body) : undefined,
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
    return upstash('GET', `/get/${encodeURIComponent(key)}`);
  }

  /** SET key value [EX seconds] */
  async set(key, value, opts = {}) {
    const body = { value };
    if (opts.ex) body.ex = opts.ex;
    return upstash('POST', `/set/${encodeURIComponent(key)}`, body);
  }

  /** DEL key */
  async del(key) {
    return upstash('POST', `/del/${encodeURIComponent(key)}`);
  }

  /** LPUSH key value(s) */
  async lpush(key, ...values) {
    return upstash('POST', `/lpush/${encodeURIComponent(key)}`, values);
  }

  /** RPUSH key value(s) */
  async rpush(key, ...values) {
    return upstash('POST', `/rpush/${encodeURIComponent(key)}`, values);
  }

  /** LTRIM key start stop */
  async ltrim(key, start, stop) {
    return upstash('POST', `/ltrim/${encodeURIComponent(key)}/${start}/${stop}`);
  }

  /** KEYS pattern — use sparingly */
  async keys(pattern) {
    const result = await upstash('GET', `/keys/${encodeURIComponent(pattern)}`);
    return Array.isArray(result) ? result : [];
  }

  /** HGET key field */
  async hget(key, field) {
    return upstash('GET', `/hget/${encodeURIComponent(key)}/${encodeURIComponent(field)}`);
  }

  /** HSET key field value */
  async hset(key, field, value) {
    return upstash('POST', `/hset/${encodeURIComponent(key)}`, { [field]: value });
  }

  /** HDEL key field */
  async hdel(key, field) {
    return upstash('POST', `/hdel/${encodeURIComponent(key)}`, [field]);
  }

  /** HGETALL key → object or null */
  async hgetall(key) {
    const result = await upstash('GET', `/hgetall/${encodeURIComponent(key)}`);
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
    return upstash('POST', `/expire/${encodeURIComponent(key)}/${seconds}`);
  }
}

// Singleton — share one adapter across all modules
export const redisAdapter = new UpstashRedisAdapter();
