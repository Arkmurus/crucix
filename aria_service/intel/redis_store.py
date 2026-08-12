"""
Redis persistence layer — shared across all intel modules.
Falls back to in-memory dicts if Redis is unavailable.

R-F235 (2026-05-11) — backend selector. ARIA_STATE_BACKEND picks:
  sqlite   — disk-resident SQLite via state_store.py (default, R-F745)
  upstash  — original Upstash Redis path (legacy, declined 2026-05-12)
  memory   — in-process dict only (tests / break-glass)

R-F745 (2026-05-20) — flipped the default from "upstash" to "sqlite"
to close the Phase A gate #5 false-closure documented in CLAUDE.md:97.
Upstash was cancelled 2026-05-12 so "upstash" as a default invited a
dead-provider lookup on any deploy where the operator forgot to set
the env var. The fly secret ARIA_STATE_BACKEND=sqlite is already set
in production, so this change is a safety net for new envs + local
dev, not a runtime behaviour change.

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
import builtins
import json
import logging
import os
from typing import Any, Optional

# R-F2116: monkey-patch aiosqlite to handle "Event loop is closed" during shutdown.
# The aiosqlite worker thread crashes when the event loop is closed before the
# connection worker finishes (Python 3.13 + aiosqlite 0.22.1). This is a known
# issue: _connection_worker_thread catches BaseException but then tries to call
# future.get_loop().call_soon_threadsafe() which raises RuntimeError on a closed
# loop — and that second exception is unhandled, crashing the thread and the process.
# We patch the inner _run_job or wrap the worker to catch RuntimeError gracefully.
try:
    import aiosqlite.core as _ac
    _orig_worker = _ac._connection_worker_thread
    _orig_connection_init = _ac.Connection.__init__

    def _patched_worker(tx):
        try:
            _orig_worker(tx)
        except RuntimeError as _re:
            # "Event loop is closed" during interpreter shutdown — ignore gracefully
            if "Event loop is closed" in str(_re):
                pass
            else:
                raise

    def _patched_connection_init(self, *args, **kwargs):
        _orig_connection_init(self, *args, **kwargs)
        try:
            self._thread.daemon = True
        except Exception:
            pass

    _ac._connection_worker_thread = _patched_worker
    if not getattr(_ac.Connection, "_aria_daemon_patch", False):
        _ac.Connection.__init__ = _patched_connection_init
        _ac.Connection._aria_daemon_patch = True
except Exception:
    pass

import redis.asyncio as aioredis
from .engine_wiring import wire_success, wire_failure

logger = logging.getLogger("aria.redis")

_client: Optional[aioredis.Redis] = None
_mem_store: dict[str, str] = {}

# R-F235 + R-F745: backend selector. Default 'sqlite' (was 'upstash'
# until R-F745 — Upstash subscription cancelled 2026-05-12, defaulting
# to a dead provider was a Phase A gate #5 false-closure). Operators
# can still pin to 'upstash' or 'memory' explicitly. SQLite at
# /data/aria_state.db is the prod path (state_store.py).
_BACKEND = (os.getenv("ARIA_STATE_BACKEND") or "sqlite").strip().lower()


def _use_sqlite() -> bool:
    return _BACKEND == "sqlite"


def _use_memory() -> bool:
    return _BACKEND == "memory"


def is_shared() -> bool:
    """R-F2526 — True iff a REAL shared Redis backend is live (cross-machine coordination
    available). False in the in-memory fallback (single machine — the current §6 state,
    Upstash cancelled), where process-local primitives are authoritative and any
    cross-machine coordination gate MUST be a no-op. Callers use this so a gate is
    byte-identical on one machine and becomes a global cap the instant REDIS_URL is set —
    horizontal scale as a config flip, no code refactor."""
    return _client is not None

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
    # R-F726 (2026-05-19): size warn is Upstash-specific noise on
    # SQLite/memory backends. Upstash had a per-value tier cap (free
    # ~1MB, Pro ~100MB) — the warning was telling the operator "split
    # before you hit it". SQLite has no per-value cap; in-memory dict
    # has no cap. With Upstash cancelled 2026-05-12 (upstash_redis_
    # provider memory) the warn was firing ~12× per signal_generator
    # cycle on the neural_edges blob (steady ~4.04MB, past the 4MB
    # default warn threshold) and never going to clear because §7
    # mandates infinite memory + no eviction. Pure noise.
    #
    # Gate is BOTH env intent AND live-client presence — the env
    # default flipped to "sqlite" in R-F745 (was "upstash"), but if
    # the operator still pins to upstash AND the Upstash client failed
    # to connect (subscription cancelled), _client is None and writes
    # fall through to the in-process _mem_store — also no cap. So:
    # only warn when we have a real Upstash client that the operator
    # configured intentionally. Error threshold still applies to all
    # backends — a 25MB+ blob is a write-amp concern regardless of
    # storage (SQLite would rewrite the full row on every flush).
    val_len = len(value) if isinstance(value, str) else 0
    _warn_bytes = int(os.getenv("ARIA_REDIS_WARN_BYTES", "4000000"))
    _error_bytes = int(os.getenv("ARIA_REDIS_ERROR_BYTES", "25000000"))
    if val_len > _error_bytes:
        logger.error(
            "state SET %s: value size %d bytes exceeds error threshold "
            "(%d). Reduce payload or split across keys — large blobs "
            "amplify write cost on every flush.",
            key, val_len, _error_bytes,
        )
    elif (
        val_len > _warn_bytes
        and _BACKEND == "upstash"
        and _client is not None
    ):
        logger.warning(
            "Redis SET %s: value size %d bytes exceeds warn threshold "
            "(%d). Plan a key split before this hits the tier cap.",
            key, val_len, _warn_bytes,
        )
    if _use_sqlite():
        from . import state_store as _ss
        await _ss.set_key(key, value, ex=ex, keepttl=keepttl)
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
            # R-F2108: offload JSON deserialization to a thread — a 50K-entry
            # seen-urls dict can take 50-100ms to parse on the event loop.
            result = await asyncio.to_thread(json.loads, raw)
            # R-F2108 §21a — wire success so the brain knows redis_store is reachable
            try:
                wire_success(module="redis_store", summary="get_json OK",
                             source_id="redis_store:get_json")
            except Exception:
                pass
            return result
        except Exception as e:
            logger.warning("JSON parse failed for key %s: %s", key, e)
            try:
                wire_failure(module="redis_store", detail=f"get_json parse failed for {key}: {e}",
                             gap_type="engine_failure", source="redis_store.get_json")
            except Exception:
                pass
    return None


class StoreReadError(Exception):
    """R-F1392 — a read failed at the STORE layer (dead conn / reconnect
    window), as opposed to the key being genuinely absent. Raised only by the
    *_strict readers; the plain get()/get_json() keep their graceful
    None-on-error contract."""


async def get_strict(key: str) -> Optional[str]:
    """R-F1392: like get(), but a store-layer failure raises StoreReadError so
    callers (async job-poll endpoints) can answer 503-retry instead of a false
    not_found. None means the key is genuinely absent."""
    if _use_sqlite():
        from . import state_store as _ss
        try:
            return await _ss.get_strict(key)
        except _ss.StateReadError as e:
            raise StoreReadError(str(e)) from e
    if _client:
        try:
            return await _client.get(key)
        except Exception as e:
            raise StoreReadError(f"Redis GET {key} failed: {e}") from e
    return _mem_store.get(key)


async def get_json_strict(key: str) -> Any:
    """R-F1392: get_json over get_strict — raises StoreReadError on store
    failure; None only when the key is genuinely absent."""
    raw = await get_strict(key)
    if raw:
        try:
            return json.loads(raw)
        except Exception as e:
            logger.warning("JSON parse failed for key %s: %s", key, e)
    return None


async def set_json(key: str, obj: Any, ex: int | None = None,
                   keepttl: bool = False) -> None:
    # R-F2108: offload JSON serialization to a thread — a 50K-entry seen-urls
    # dict can take 50-100ms to serialize on the event loop.
    raw = await asyncio.to_thread(lambda: json.dumps(obj, default=str))
    await set(key, raw, ex=ex, keepttl=keepttl)


async def lpush(key: str, value: str, *, critical: bool = False) -> None:
    if _use_sqlite():
        from . import state_store as _ss
        await _ss.lpush(key, value, critical=critical)  # R-F1351
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


async def lpop_multi(key: str, count: int = 10) -> list[str]:
    """Pop multiple items from the head of a list.

    Args:
        key: Redis key.
        count: Max items to pop (default 10).

    Returns:
        List of popped values (oldest first).
    """
    result: list[str] = []
    if _use_sqlite():
        from . import state_store as _ss
        for _ in range(count):
            val = await _ss.lpop(key)
            if val is None:
                break
            result.append(val)
        return result
    if _client:
        try:
            pipe = _client.pipeline()
            for _ in range(count):
                pipe.lpop(key)
            outcomes = await pipe.execute()
            return [o for o in outcomes if o is not None]
        except Exception as e:
            logger.warning("Redis LPOP %s failed: %s", key, e)
    lst = json.loads(_mem_store.get(key, "[]"))
    popped = lst[:count]
    _mem_store[key] = json.dumps(lst[count:])
    return popped


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


async def lrem(key: str, count: int, value: str) -> int:
    """Remove matching list entries and return the number removed."""
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.lrem(key, count, value)
    if _client:
        try:
            return int(await _client.lrem(key, count, value))
        except Exception as e:
            logger.warning("Redis LREM %s failed: %s", key, e)
    items = json.loads(_mem_store.get(key, "[]"))
    if not isinstance(items, list):
        return 0
    remaining = abs(count) if count else len(items)
    indexes = range(len(items)) if count >= 0 else range(len(items) - 1, -1, -1)
    # R-F3645: this module defines `async def set(key, value, ...)` at module
    # scope (the Redis SET mirror), which SHADOWS the builtin. A bare `set()`
    # here therefore called that coroutine function with no arguments and raised
    # `TypeError: set() missing 2 required positional arguments`, so the
    # in-memory fallback of lrem never worked — including the fall-through taken
    # when a real Redis LREM raises (line above logs and drops through). Address
    # the builtin explicitly. The annotation is qualified too: `set[int]` there
    # is inert today only because `from __future__ import annotations` makes
    # annotations strings — anything that later resolves them (typing.get_type_hints,
    # a runtime validator) would subscript the coroutine function and blow up.
    remove_indexes: builtins.set[int] = builtins.set()
    for index in indexes:
        if remaining and items[index] == value:
            remove_indexes.add(index)
            remaining -= 1
    _mem_store[key] = json.dumps(
        [item for index, item in enumerate(items) if index not in remove_indexes]
    )
    return len(remove_indexes)


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


async def incr(key: str, amount: int = 1, *, critical: bool = False) -> int:
    """Atomic integer increment. Used by rate-limit token buckets and
    similar counters where racing callers must not lose increments.
    Falls back to a non-atomic get+set on the in-memory store.
    R-F1351: critical=True raises StateWriteError on drop (sqlite path).
    """
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.incr(key, amount, critical=critical)  # R-F1351
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


async def incrbyfloat(key: str, amount: float, *, critical: bool = False) -> float:
    """Atomic float increment for cost / metric counters. Used by the
    autonomous engine cost cap so concurrent task cost writes don't
    race. Falls back to non-atomic get+set on the in-memory store.
    R-F1351: critical=True raises StateWriteError on drop (sqlite path).
    """
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.incrbyfloat(key, amount, critical=critical)  # R-F1351
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


async def hset(key: str, mapping: dict, *, critical: bool = False) -> None:
    """Set multiple hash fields."""
    if _use_sqlite():
        from . import state_store as _ss
        await _ss.hset(key, mapping, critical=critical)  # R-F1351
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


async def hget(key: str, field: str) -> Optional[str]:
    """Get a single hash field (None if absent).

    R-F2486: this method was missing entirely, so `rs.hget(...)` in
    dd_trigger_pipeline raised AttributeError and the DD trigger guard failed
    OPEN. Mirrors hgetall across the sqlite / Redis / in-memory backends.
    """
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.hget(key, field)
    if _client:
        try:
            return await _client.hget(key, field)
        except Exception as e:
            logger.warning("Redis HGET %s.%s failed: %s", key, field, e)
    try:
        return json.loads(_mem_store.get(key, "{}")).get(field)
    except Exception:
        return None


async def hincrby(key: str, field: str, amount: int = 1, *, critical: bool = False) -> int:
    """Atomically increment an integer hash field, returning the new value.

    R-F2625: this method was missing entirely, so `rs.hincrby(...)` at
    dd_orchestrator.py:8167 (the R-F1914 per-layer stats block) raised
    AttributeError. That block's `except: pass` swallowed it, so
    `crucix:dd:layer_stats:<layer>` was NEVER written and the DD health endpoint
    (routes/aria.py:1699) read {} for all 11 layers — "no failures" that actually
    meant "never recorded" (DARK per §21a). Mirrors hget/hgetall across the
    sqlite / Redis / in-memory backends. Same class as R-F2486 (hget).

    Concurrent DD finalizers hit the SAME hash key, so the sqlite and Redis paths
    are atomic (single UPSERT / native HINCRBY). Only the in-memory fallback is
    non-atomic — consistent with `incr` above, and it is used only when Redis is
    offline and sqlite is not the backend.
    """
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.hincrby(key, field, amount, critical=critical)
    if _client:
        try:
            return int(await _client.hincrby(key, field, amount))
        except Exception as e:
            logger.warning("Redis HINCRBY %s.%s failed: %s", key, field, e)
    # In-memory fallback (NOT atomic — only used when Redis is offline)
    try:
        existing = json.loads(_mem_store.get(key, "{}"))
    except Exception:
        existing = {}
    try:
        current = int(existing.get(field, 0) or 0)
    except (TypeError, ValueError):
        current = 0
    new_val = current + int(amount)
    existing[field] = str(new_val)
    _mem_store[key] = json.dumps(existing)
    return new_val


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


async def scan_keys_strict(pattern: str, count: int = 200) -> list[str]:
    """C-38 — like `scan_keys`, but a store-layer failure RAISES StoreReadError.

    `scan_keys` returns `[]` on a backend error exactly as it does for a genuinely
    empty keyspace (the Redis path logs the SCAN exception and falls through to the
    in-memory glob; the SQLite path catches and returns []). Any caller that turns an
    empty result into a statement about the world therefore publishes a fabricated
    measurement whenever the scan fails — which is how `registry_health_report` came
    to report `scan_complete: true, unmeasured_count: 194` from a scan that never ran.

    This mirrors the R-F1392 `get_strict` / `get_json_strict` contract: an empty list
    means genuinely nothing matched; a failure is raised so the caller can say so.
    """
    if _use_sqlite():
        from . import state_store as _ss
        try:
            return await _ss.scan_keys_strict(pattern, count)
        except _ss.StateReadError as e:
            raise StoreReadError(str(e)) from e
    if _client:
        try:
            keys: list[str] = []
            async for key in _client.scan_iter(match=pattern, count=count):
                keys.append(key)
                if len(keys) >= count:
                    break
            return keys
        except Exception as e:
            raise StoreReadError(f"Redis SCAN {pattern} failed: {e}") from e
    import fnmatch
    return [k for k in list(_mem_store.keys()) if fnmatch.fnmatch(k, pattern)][:count]


async def scan_keys_null_ttl(pattern: str, count: int = 500) -> list[str]:
    """R-F2629 — keys matching `pattern` that carry NO TTL.

    Only the SQLite backend can answer this, which is fine: §6 makes SQLite
    the production backend (Upstash cancelled). On the Redis/in-memory paths
    this returns [] — the repair that uses it is idempotent and treats []
    as "nothing to do this pass", never as "the keyspace is clean".
    """
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.scan_keys_null_ttl(pattern, count)
    return []


async def scan_json(pattern: str, count: int = 200) -> list[tuple[str, Any]]:
    """R-F1885 — return [(key, parsed-JSON)] for keys matching `pattern` in ONE
    backend round-trip, so callers avoid N separate get_json calls (see
    absorption_quarantine.stats). Mirrors scan_keys' backend dispatch."""
    if _use_sqlite():
        from . import state_store as _ss
        return await _ss.scan_json(pattern, count)
    if _client:
        try:
            keys: list[str] = []
            async for key in _client.scan_iter(match=pattern, count=count):
                keys.append(key)
                if len(keys) >= count:
                    break
            if not keys:
                return []
            vals = await _client.mget(keys)
            out: list[tuple[str, Any]] = []
            for k, v in zip(keys, vals):
                if v is None:
                    continue
                try:
                    out.append((k, json.loads(v)))
                except Exception:
                    continue
            return out
        except Exception as e:
            logger.warning("Redis SCAN_JSON %s failed: %s", pattern, e)
            return []
    # In-memory fallback
    import fnmatch as _fnm
    out: list[tuple[str, Any]] = []
    for k in list(_mem_store.keys()):
        if not _fnm.fnmatch(k, pattern):
            continue
        try:
            out.append((k, json.loads(_mem_store[k])))
        except Exception:
            continue
        if len(out) >= count:
            break
    return out
