"""
Redis persistence layer — shared across all intel modules.
Falls back to in-memory dicts if Redis is unavailable.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

logger = logging.getLogger("aria.redis")

_client: Optional[aioredis.Redis] = None
_mem_store: dict[str, str] = {}


async def connect(redis_url: str) -> bool:
    global _client
    try:
        _client = aioredis.from_url(redis_url, decode_responses=True)
        await _client.ping()
        logger.info("Redis connected")
        return True
    except Exception as e:
        logger.warning(f"Redis unavailable, using in-memory fallback: {e}")
        _client = None
        return False


async def get(key: str) -> Optional[str]:
    if _client:
        try:
            return await _client.get(key)
        except Exception as e:
            logger.warning("Redis GET %s failed: %s", key, e)
    return _mem_store.get(key)


async def set(key: str, value: str, ex: int | None = None,
              keepttl: bool = False) -> None:
    """Set a key. `ex` (seconds) sets a fresh TTL. `keepttl=True` preserves
    the existing TTL across the update -- needed when a counter or rolling
    aggregate is rewritten on every event but the rolling window must NOT
    restart from zero each time. Cannot specify both.

    Note: redis-py's `set` clears TTL by default unless either `ex` is
    given or `keepttl=True` is set. Without this distinction, a write
    pattern like `set_json(key, updated, ex=N)` resets the N-second
    window every event under continuous traffic, turning what was meant
    to be a rolling window into a lifetime tally.
    """
    if _client:
        try:
            if keepttl and ex is None:
                await _client.set(key, value, keepttl=True)
            else:
                await _client.set(key, value, ex=ex)
            return
        except Exception as e:
            logger.warning("Redis SET %s failed: %s", key, e)
    _mem_store[key] = value


async def delete(key: str) -> bool:
    """Remove a key from Redis (or the in-memory fallback). Returns True if
    the key existed before the call. Used by purge / forget endpoints."""
    if _client:
        try:
            n = await _client.delete(key)
            return bool(n)
        except Exception as e:
            logger.warning("Redis DEL %s failed: %s", key, e)
    return _mem_store.pop(key, None) is not None


async def get_json(key: str) -> Any:
    raw = await get(key)
    if raw:
        try:
            return json.loads(raw)
        except Exception as e:
            logger.warning("JSON parse failed for key %s: %s", key, e)
    return None


async def set_json(key: str, obj: Any, ex: int | None = None,
                   keepttl: bool = False) -> None:
    await set(key, json.dumps(obj, default=str), ex=ex, keepttl=keepttl)


async def lpush(key: str, value: str) -> None:
    if _client:
        try:
            await _client.lpush(key, value)
            return
        except Exception as e:
            logger.warning("Redis LPUSH %s failed: %s", key, e)
    lst = json.loads(_mem_store.get(key, "[]"))
    lst.insert(0, value)
    _mem_store[key] = json.dumps(lst)


async def ltrim(key: str, start: int, stop: int) -> None:
    if _client:
        try:
            await _client.ltrim(key, start, stop)
            return
        except Exception as e:
            logger.warning("Redis LTRIM %s failed: %s", key, e)


async def llen(key: str) -> int:
    """Return the length of a Redis list."""
    if _client:
        try:
            return await _client.llen(key)
        except Exception as e:
            logger.warning("Redis LLEN %s failed: %s", key, e)
    lst = json.loads(_mem_store.get(key, "[]"))
    return len(lst) if isinstance(lst, list) else 0


async def lrange(key: str, start: int, stop: int) -> list[str]:
    if _client:
        try:
            return await _client.lrange(key, start, stop)
        except Exception as e:
            logger.warning("Redis LRANGE %s failed: %s", key, e)
    lst = json.loads(_mem_store.get(key, "[]"))
    return lst[start : stop + 1 if stop >= 0 else None]


async def incr(key: str, amount: int = 1) -> int:
    """Atomic integer increment. Used by rate-limit token buckets and
    similar counters where racing callers must not lose increments.
    Falls back to a non-atomic get+set on the in-memory store.
    """
    if _client:
        try:
            return int(await _client.incrby(key, amount))
        except Exception as e:
            logger.warning("Redis INCR %s failed: %s", key, e)
    # In-memory fallback (NOT atomic — only used when Redis is offline)
    current = int(_mem_store.get(key, "0") or "0")
    new_val = current + amount
    _mem_store[key] = str(new_val)
    return new_val


async def incrbyfloat(key: str, amount: float) -> float:
    """Atomic float increment for cost / metric counters. Used by the
    autonomous engine cost cap so concurrent task cost writes don't
    race. Falls back to non-atomic get+set on the in-memory store.
    """
    if _client:
        try:
            return float(await _client.incrbyfloat(key, amount))
        except Exception as e:
            logger.warning("Redis INCRBYFLOAT %s failed: %s", key, e)
    current = float(_mem_store.get(key, "0") or "0")
    new_val = current + amount
    _mem_store[key] = f"{new_val:.6f}"
    return new_val


async def expire(key: str, seconds: int) -> bool:
    """Set TTL on an existing key. Returns True if the key existed.
    No-op on the in-memory store (no TTL implementation, but the
    next process restart clears everything anyway).
    """
    if _client:
        try:
            return bool(await _client.expire(key, seconds))
        except Exception as e:
            logger.warning("Redis EXPIRE %s failed: %s", key, e)
            return False
    # In-memory fallback has no TTL — present-key check only
    return key in _mem_store


# ── Sorted set operations (conversation index) ─────────────────────────


async def zadd(key: str, score: float, member: str) -> None:
    """Add or update a member in a sorted set."""
    if _client:
        try:
            await _client.zadd(key, {member: score})
            return
        except Exception as e:
            logger.warning("Redis ZADD %s failed: %s", key, e)
    # In-memory fallback — list of (score, member) tuples
    raw = json.loads(_mem_store.get(key, "[]"))
    entries = [(s, m) for s, m in raw if m != member]
    entries.append((score, member))
    entries.sort(key=lambda x: x[0])
    _mem_store[key] = json.dumps(entries)


async def zrevrange(key: str, start: int, stop: int) -> list[str]:
    """Return members in descending score order (highest first)."""
    if _client:
        try:
            return await _client.zrevrange(key, start, stop)
        except Exception as e:
            logger.warning("Redis ZREVRANGE %s failed: %s", key, e)
    raw = json.loads(_mem_store.get(key, "[]"))
    entries = sorted(raw, key=lambda x: x[0], reverse=True)
    return [m for _, m in entries[start : stop + 1 if stop >= 0 else None]]


async def zrem(key: str, member: str) -> bool:
    """Remove a member from a sorted set. Returns True if removed."""
    if _client:
        try:
            return bool(await _client.zrem(key, member))
        except Exception as e:
            logger.warning("Redis ZREM %s failed: %s", key, e)
    raw = json.loads(_mem_store.get(key, "[]"))
    before = len(raw)
    raw = [(s, m) for s, m in raw if m != member]
    _mem_store[key] = json.dumps(raw)
    return len(raw) < before


async def zcard(key: str) -> int:
    """Return the number of members in a sorted set."""
    if _client:
        try:
            return await _client.zcard(key)
        except Exception as e:
            logger.warning("Redis ZCARD %s failed: %s", key, e)
    raw = json.loads(_mem_store.get(key, "[]"))
    return len(raw)


async def hset(key: str, mapping: dict) -> None:
    """Set multiple hash fields."""
    if _client:
        try:
            await _client.hset(key, mapping=mapping)
            return
        except Exception as e:
            logger.warning("Redis HSET %s failed: %s", key, e)
    existing = json.loads(_mem_store.get(key, "{}"))
    existing.update(mapping)
    _mem_store[key] = json.dumps(existing)


async def hgetall(key: str) -> dict:
    """Get all hash fields."""
    if _client:
        try:
            return await _client.hgetall(key)
        except Exception as e:
            logger.warning("Redis HGETALL %s failed: %s", key, e)
    return json.loads(_mem_store.get(key, "{}"))


async def scan_keys(pattern: str, count: int = 200) -> list[str]:
    """Return keys matching a glob pattern (uses SCAN for Redis, filter for in-memory)."""
    if _client:
        try:
            keys: list[str] = []
            async for key in _client.scan_iter(match=pattern, count=count):
                keys.append(key)
                if len(keys) >= count:
                    break
            return keys
        except Exception as e:
            logger.warning("Redis SCAN %s failed: %s", pattern, e)
    # In-memory fallback — simple glob match
    import fnmatch
    return [k for k in list(_mem_store.keys()) if fnmatch.fnmatch(k, pattern)][:count]
