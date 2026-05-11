"""
Redis persistence layer — shared across all intel modules.
Falls back to in-memory dicts if Redis is unavailable.

R-F235 (2026-05-11) — backend selector. ARIA_STATE_BACKEND picks:
  upstash  — original Upstash Redis path (default, backwards compatible)
  sqlite   — disk-resident SQLite via state_store.py (recommended)
  memory   — in-process dict only (tests / break-glass)

Per the "ARIA mirrors Claude" rule (aria_mirrors_claude.md feedback
memory), the target architecture is files-on-disk + LLM, with Upstash
as a deprecated convenience. SQLite at /data/aria_state.db replaces
Redis without any caller-side changes — every public function in this
module dispatches through `_backend` to either the legacy aioredis
path or the new state_store path.

The aioredis client is kept loaded so the legacy path remains
functional for backwards-compatible deployments AND so the migration
script (scripts/migrate_state.py) can read from both at once.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

import redis.asyncio as aioredis

logger = logging.getLogger("aria.redis")

_client: Optional[aioredis.Redis] = None
_mem_store: dict[str, str] = {}

# R-F235: backend selector. Default 'upstash' so existing deploys are
# untouched. Operator flips to 'sqlite' (after running the migration
# script) to drop Upstash. 'memory' is for tests + break-glass when
# both Upstash and SQLite are unavailable.
_BACKEND = (os.getenv("ARIA_STATE_BACKEND") or "upstash").strip().lower()


def _use_sqlite() -> bool:
    return _BACKEND == "sqlite"


def _use_memory() -> bool:
    return _BACKEND == "memory"

# F51/F52 fix 2026-04-28: capture the main app loop on connect() so worker
# threads can schedule redis-touching coroutines back onto it via
# run_on_main_loop(). The aioredis client is loop-bound at construction;
# awaiting its operations from a different loop (e.g. a fresh asyncio.run
# inside a worker thread) raises "got Future attached to a different loop"
# and — combined with the WARNING-mirroring error_log_handler — used to
# cascade into 20+ recursive record_error attempts per autonomous fire.
_main_loop: Optional[asyncio.AbstractEventLoop] = None


async def connect(redis_url: str) -> bool:
    global _client, _main_loop
    _main_loop = asyncio.get_running_loop()

    # R-F235 (2026-05-11) — SQLite backend dispatch. When
    # ARIA_STATE_BACKEND=sqlite, route every operation through
    # state_store.py. The Upstash client is intentionally NOT opened
    # in this mode so we don't pay round-trips or take dependency on
    # the Upstash subscription.
    if _use_sqlite():
        try:
            from . import state_store as _ss
            ok = await _ss.connect()
            if ok:
                logger.info("redis_store: dispatching to SQLite backend (R-F235)")
                _client = None  # legacy path explicitly disabled
                return True
            logger.error(
                "redis_store: SQLite backend failed to initialise; falling "
                "back to in-memory fallback (data lost on restart)"
            )
            _client = None
            return False
        except Exception as e:
            logger.error("redis_store: SQLite import/connect failed: %s", e)
            _client = None
            return False

    if _use_memory():
        logger.info("redis_store: memory-only backend (ARIA_STATE_BACKEND=memory)")
        _client = None
        return True

    # Legacy: Upstash Redis path
    try:
        _client = aioredis.from_url(redis_url, decode_responses=True)
        await _client.ping()
        logger.info("Redis connected (Upstash backend)")
        return True
    except Exception as e:
        logger.warning(f"Redis unavailable, using in-memory fallback: {e}")
        _client = None
        return False


def run_on_main_loop(coro, timeout: float = 8.0):
    """Schedule a coroutine on the captured main app loop and block until done.

    Use from worker threads when the coroutine awaits resources that are
    bound to the main loop (notably the aioredis client). Falls back to
    asyncio.run() if no main loop has been captured (startup, tests,
    post-shutdown) — those contexts don't have the loop-affinity hazard.
    """
    main = _main_loop
    if main is not None and not main.is_closed():
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, main)
            return fut.result(timeout=timeout)
        except RuntimeError:
            # Loop closed between the is_closed check and submit.
            pass
    return asyncio.run(coro)


async def get(key: str) -> Optional[str]:
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.get(key)
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
    # F87 + F88 observability 2026-04-29: Upstash caps single-value
    # sizes. The cap depends on tier (free ~1 MB, Pro ~100 MB). The
    # original F87 commit assumed free-tier and warned at 700 KB / 950
    # KB, which fired on every knowledge save (current blob ≈ 2.6 MB)
    # because this account is on a higher tier — a noise problem.
    # Now: env-configurable thresholds, defaulting to 4 MB warn / 25 MB
    # error. Override via ARIA_REDIS_WARN_BYTES / ARIA_REDIS_ERROR_BYTES
    # to match your tier's actual cap.
    #
    # Why we still warn at all: the 2026-04-29 ledger collapse (4587 →
    # 2000 signals) shows that *some* truncation pathway exists on this
    # account at large blob sizes. Until knowledge + ledger are
    # restructured off the one-giant-JSON-blob shape (F88 architectural
    # follow-up), early visibility into blob growth is what tells us
    # when the next collapse is imminent.
    val_len = len(value) if isinstance(value, str) else 0
    _warn_bytes = int(os.getenv("ARIA_REDIS_WARN_BYTES", "4000000"))
    _error_bytes = int(os.getenv("ARIA_REDIS_ERROR_BYTES", "25000000"))
    if val_len > _error_bytes:
        logger.error(
            "Redis SET %s: value size %d bytes exceeds error threshold "
            "(%d). Write may silently truncate. Reduce payload or split "
            "across keys.", key, val_len, _error_bytes,
        )
    elif val_len > _warn_bytes:
        logger.warning(
            "Redis SET %s: value size %d bytes exceeds warn threshold "
            "(%d). Plan a key split before this hits the tier cap.",
            key, val_len, _warn_bytes,
        )
    if _use_sqlite():
        from . import state_store as _ss
        await _ss.set(key, value, ex=ex, keepttl=keepttl)
        return
    if _client:
        try:
            if keepttl and ex is None:
                await _client.set(key, value, keepttl=True)
            else:
                await _client.set(key, value, ex=ex)
            return
        except Exception as e:
            logger.warning("Redis SET %s failed (size=%d): %s", key, val_len, e)
    _mem_store[key] = value


async def delete(key: str) -> bool:
    """Remove a key from Redis (or the in-memory fallback). Returns True if
    the key existed before the call. Used by purge / forget endpoints."""
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.delete(key)
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
    if _use_sqlite():
        from . import state_store as _ss
        await _ss.lpush(key, value)
        return
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
    if _use_sqlite():
        from . import state_store as _ss
        await _ss.ltrim(key, start, stop)
        return
    if _client:
        try:
            await _client.ltrim(key, start, stop)
            return
        except Exception as e:
            logger.warning("Redis LTRIM %s failed: %s", key, e)


async def llen(key: str) -> int:
    """Return the length of a Redis list."""
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.llen(key)
    if _client:
        try:
            return await _client.llen(key)
        except Exception as e:
            logger.warning("Redis LLEN %s failed: %s", key, e)
    lst = json.loads(_mem_store.get(key, "[]"))
    return len(lst) if isinstance(lst, list) else 0


async def lrange(key: str, start: int, stop: int) -> list[str]:
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.lrange(key, start, stop)
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
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.incr(key, amount)
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
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.incrbyfloat(key, amount)
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
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.expire(key, seconds)
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
    if _use_sqlite():
        from . import state_store as _ss
        await _ss.zadd(key, score, member)
        return
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
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.zrevrange(key, start, stop)
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
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.zrem(key, member)
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
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.zcard(key)
    if _client:
        try:
            return await _client.zcard(key)
        except Exception as e:
            logger.warning("Redis ZCARD %s failed: %s", key, e)
    raw = json.loads(_mem_store.get(key, "[]"))
    return len(raw)


async def hset(key: str, mapping: dict) -> None:
    """Set multiple hash fields."""
    if _use_sqlite():
        from . import state_store as _ss
        await _ss.hset(key, mapping)
        return
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
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.hgetall(key)
    if _client:
        try:
            return await _client.hgetall(key)
        except Exception as e:
            logger.warning("Redis HGETALL %s failed: %s", key, e)
    return json.loads(_mem_store.get(key, "{}"))


async def scan_keys(pattern: str, count: int = 200) -> list[str]:
    """Return keys matching a glob pattern (uses SCAN for Redis, filter for in-memory)."""
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.scan_keys(pattern, count)
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
