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


async def set(key: str, value: str, ex: int | None = None) -> None:
    if _client:
        try:
            await _client.set(key, value, ex=ex)
            return
        except Exception as e:
            logger.warning("Redis SET %s failed: %s", key, e)
    _mem_store[key] = value


async def get_json(key: str) -> Any:
    raw = await get(key)
    if raw:
        try:
            return json.loads(raw)
        except Exception as e:
            logger.warning("JSON parse failed for key %s: %s", key, e)
    return None


async def set_json(key: str, obj: Any, ex: int | None = None) -> None:
    await set(key, json.dumps(obj, default=str), ex=ex)


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


async def lrange(key: str, start: int, stop: int) -> list[str]:
    if _client:
        try:
            return await _client.lrange(key, start, stop)
        except Exception as e:
            logger.warning("Redis LRANGE %s failed: %s", key, e)
    lst = json.loads(_mem_store.get(key, "[]"))
    return lst[start : stop + 1 if stop >= 0 else None]
