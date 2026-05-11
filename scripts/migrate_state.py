"""
migrate_state — one-shot copy of every Redis key from Upstash to the
new SQLite state_store (R-F235, 2026-05-11).

Usage:
    # Dry-run — count keys, print sample, no writes
    python scripts/migrate_state.py --dry-run

    # Full copy
    python scripts/migrate_state.py

    # Only specific prefixes (multiple --prefix flags supported)
    python scripts/migrate_state.py --prefix crucix:aria:student --prefix crucix:dd

Env vars required:
    REDIS_URL                         — source Upstash URL
    ARIA_STATE_DB_PATH                — target SQLite path (default /data/aria_state.db)

Process:
    1. Connect to Upstash AND SQLite simultaneously.
    2. SCAN every key matching the prefix filters (or "*" by default).
    3. For each key, detect type via Redis TYPE command.
    4. Copy with the matching method (set/lpush/zadd/hset).
    5. Preserve TTL when present (TTL command).
    6. Write a manifest at /data/aria_backups/state_migration.json
       with counts + duration so the operator has an audit trail.

What it CANNOT preserve perfectly:
    - Streams (XADD) — ARIA doesn't use any, so no-op.
    - Exact insertion order of unordered sets — ARIA only uses sorted
      sets and lists, both order-preserved.

Per the "ARIA mirrors Claude" rule (aria_mirrors_claude.md), this is
the migration that makes Upstash optional. After running this script
+ flipping ARIA_STATE_BACKEND=sqlite on fly.io, the Upstash
subscription can be cancelled.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("migrate_state")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def _migrate(prefixes: list[str], dry_run: bool, limit: int) -> dict:
    import redis.asyncio as aioredis
    from aria_service.intel import state_store as ss

    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if not redis_url:
        raise SystemExit("REDIS_URL not set — cannot read source")

    src = aioredis.from_url(redis_url, decode_responses=True)
    await src.ping()

    if not dry_run:
        ok = await ss.connect()
        if not ok:
            raise SystemExit("state_store SQLite backend failed to initialise")

    t_start = time.time()
    stats = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "patterns": prefixes or ["*"],
        "dry_run": dry_run,
        "by_type": {},
        "ttl_preserved": 0,
        "errors": [],
        "total_keys": 0,
    }

    patterns = [p + "*" if not p.endswith("*") else p for p in (prefixes or ["*"])]
    sample: list[dict] = []
    keys_seen = 0

    for pattern in patterns:
        async for key in src.scan_iter(match=pattern, count=500):
            keys_seen += 1
            stats["total_keys"] = keys_seen
            if limit and keys_seen > limit:
                break
            try:
                ktype = await src.type(key)
            except Exception as e:
                stats["errors"].append({"key": key, "stage": "type", "err": str(e)[:200]})
                continue
            stats["by_type"][ktype] = stats["by_type"].get(ktype, 0) + 1

            try:
                ttl = await src.ttl(key)
                # Redis TTL returns -1 for no TTL, -2 for missing. Map to None.
                ttl_seconds: int | None = ttl if isinstance(ttl, int) and ttl > 0 else None
                if ttl_seconds:
                    stats["ttl_preserved"] += 1
            except Exception:
                ttl_seconds = None

            try:
                if ktype == "string":
                    val = await src.get(key)
                    if val is not None and not dry_run:
                        await ss.set(key, val, ex=ttl_seconds)
                    if len(sample) < 5:
                        sample.append({"key": key, "type": ktype, "ttl": ttl_seconds,
                                       "preview": (val or "")[:80]})
                elif ktype == "list":
                    # Redis LRANGE returns head-to-tail. lpush(value) inserts
                    # at head, so to preserve order we lpush in REVERSE.
                    members = await src.lrange(key, 0, -1)
                    if not dry_run:
                        for v in reversed(members):
                            await ss.lpush(key, v)
                        if ttl_seconds:
                            await ss.expire(key, ttl_seconds)
                    if len(sample) < 5:
                        sample.append({"key": key, "type": ktype, "ttl": ttl_seconds,
                                       "len": len(members)})
                elif ktype == "zset":
                    # zrange WITHSCORES returns [(member, score), ...]
                    members = await src.zrange(key, 0, -1, withscores=True)
                    if not dry_run:
                        for member, score in members:
                            await ss.zadd(key, float(score), member)
                        if ttl_seconds:
                            await ss.expire(key, ttl_seconds)
                    if len(sample) < 5:
                        sample.append({"key": key, "type": ktype, "ttl": ttl_seconds,
                                       "card": len(members)})
                elif ktype == "hash":
                    h = await src.hgetall(key)
                    if h and not dry_run:
                        await ss.hset(key, h)
                        if ttl_seconds:
                            await ss.expire(key, ttl_seconds)
                    if len(sample) < 5:
                        sample.append({"key": key, "type": ktype, "ttl": ttl_seconds,
                                       "fields": len(h)})
                else:
                    stats["errors"].append({"key": key, "stage": "type",
                                            "err": f"unsupported type: {ktype}"})
            except Exception as e:
                stats["errors"].append({"key": key, "stage": "copy",
                                        "err": str(e)[:200]})
            if keys_seen % 500 == 0:
                logger.info("Migrated %d keys so far (%.1fs)…",
                            keys_seen, time.time() - t_start)
        if limit and keys_seen > limit:
            break

    await src.close()
    if not dry_run:
        await ss.close()

    stats["duration_s"] = round(time.time() - t_start, 2)
    stats["sample"] = sample

    # Write manifest
    try:
        out_dir = Path(os.getenv("ARIA_BACKUP_DIR", "/data/aria_backups"))
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = out_dir / f"state_migration_{int(time.time())}.json"
        manifest_path.write_text(json.dumps(stats, indent=2, default=str))
        logger.info("Manifest written: %s", manifest_path)
    except Exception as e:
        logger.warning("Manifest write failed: %s", e)

    return stats


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prefix", action="append", default=[],
                   help="Migrate only keys with this prefix (may repeat)")
    p.add_argument("--dry-run", action="store_true",
                   help="Count + sample only; no writes to SQLite")
    p.add_argument("--limit", type=int, default=0,
                   help="Cap total keys (0 = no cap)")
    args = p.parse_args()

    stats = asyncio.run(_migrate(args.prefix, args.dry_run, args.limit))
    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
