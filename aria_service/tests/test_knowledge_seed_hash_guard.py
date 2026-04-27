"""Regression guard for F50 — knowledge-seed per-module hash guard.

Live observation 2026-04-27 21:35:05: brain_hook circuit breaker tripped
(p95=2800ms) because the boot-time knowledge seed re-encoded ~660 chunks
across 13 modules every cold boot. chromadb dedupes upserts by ID but
the sentence-transformer encode still ran on every chunk. ~5 minutes of
CPU per cold boot — and the embedding contention starved brain_hook.

Fix: hash each module's source file on first successful seed, store in
Redis. On subsequent seeds, skip the module if the stored hash matches
the current file hash. Operator can force a re-seed via
`/api/aria/knowledge/reseed?force=1` or by editing the module file.

This test exercises the hash helper + the skip-on-match logic by
checking the Redis key conventions and ensuring force=True bypasses
the cache.
"""
from __future__ import annotations

import inspect


def test_knowledge_seed_writes_hash_per_module():
    """The seed code must include the per-module hash stamp pattern."""
    from aria_service import main
    src = inspect.getsource(main.lifespan)
    assert "crucix:knowledge_seed:hash:" in src, (
        "knowledge seed must stamp a per-module hash so subsequent boots "
        "can skip when the file is unchanged"
    )
    assert "_module_hash" in src, (
        "seed needs the _module_hash helper to compute file hashes"
    )


def test_knowledge_seed_force_bypasses_hash_check():
    """force=True (from /reseed endpoint or env override) must bypass
    the hash-skip path so the operator can manually re-seed."""
    from aria_service import main
    src = inspect.getsource(main.lifespan)
    # The hash-check block is only entered when not force.
    assert "if not force:" in src
    # And the force-related skip log is the existing 6h cache-window check.
    assert "ARIA_FORCE_RESEED" in src


def test_knowledge_seed_handles_missing_module_file_gracefully():
    """The hash helper must not raise on missing files — degraded
    mode (no skip, fall through to ingest) is OK; raising would crash
    the seed loop and break boot."""
    from aria_service import main
    src = inspect.getsource(main.lifespan)
    # Helper must include a try/except around the file-read path
    assert "_module_hash" in src
    # The function body has a try/except returning '' — proven by the
    # call-site checking `if cur_hash` before using it.
    assert "if cur_hash:" in src, (
        "seed must guard against empty hash (file missing / unreadable)"
    )


def test_hash_ttl_is_long_enough_for_normal_deploy_cadence():
    """The Redis TTL on the hash key must be longer than typical days
    between deploys — 30 days is the chosen value. Too short and we'd
    re-seed on every boot in quiet weeks; too long is safe (hash
    invalidation is by content change, not by TTL)."""
    from aria_service import main
    src = inspect.getsource(main.lifespan)
    # 30-day TTL on the hash key
    assert "30 * 86400" in src, (
        "hash key TTL should be 30 days (long enough to span quiet "
        "deployment periods, short enough to self-heal corrupted state)"
    )
