"""Regression tests for F87 + F88 — observability around value-blob sizes.

F87 (2026-04-29 first batch): intel ledger silently dropped 4587 → 2000
signals between two boots. Suspected silent truncation when JSON blob
crossed Upstash's per-value cap.

F88 follow-up (same day, second batch): the F87 thresholds (700 KB
warn / 950 KB error) fired on every knowledge save because the
knowledge blob is ~2.6 MB and this account's Upstash is on a higher
tier than free. Recalibrated to env-configurable defaults (4 MB warn,
25 MB error) so the warnings are noise-free at steady state but still
catch genuinely-large outliers before the *actual* tier cap.

R-F726 (2026-05-19): the warn threshold is Upstash-specific (the
"tier cap" the message refers to is an Upstash thing). With Upstash
cancelled 2026-05-12, the warn fired ~12× per signal_generator cycle
on the neural_edges blob (steady ~4.04MB) — noise that would never
clear because §7 mandates infinite memory. Now: warn ONLY on Upstash
backend. Error threshold still applies to all backends (large blobs
amplify write cost on every flush regardless of storage).
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch


def test_set_warns_above_4mb_default_under_upstash(caplog):
    """Above the 4 MB default warn threshold under Upstash, a WARNING fires."""
    from aria_service.intel import redis_store

    fake_client = AsyncMock()
    payload = "x" * 5_000_000  # 5 MB

    with patch.object(redis_store, "_client", fake_client), \
         patch.object(redis_store, "_BACKEND", "upstash"), \
         patch.dict("os.environ", {}, clear=False), \
         caplog.at_level(logging.WARNING, logger="aria.redis"):
        # Make sure no env override is active
        for var in ("ARIA_REDIS_WARN_BYTES", "ARIA_REDIS_ERROR_BYTES"):
            if var in __import__("os").environ:
                del __import__("os").environ[var]
        asyncio.run(redis_store.set("crucix:test", payload))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "warn threshold" in r.getMessage() and str(len(payload)) in r.getMessage()
        for r in warnings
    ), "5 MB SET did not emit warn-threshold warning under Upstash backend"


def test_set_errors_above_25mb_default(caplog):
    """Above the 25 MB error threshold, an ERROR fires on every backend."""
    from aria_service.intel import redis_store

    fake_client = AsyncMock()
    payload = "y" * 26_000_000  # 26 MB

    with patch.object(redis_store, "_client", fake_client), \
         patch.object(redis_store, "_BACKEND", "upstash"), \
         caplog.at_level(logging.ERROR, logger="aria.redis"):
        for var in ("ARIA_REDIS_WARN_BYTES", "ARIA_REDIS_ERROR_BYTES"):
            if var in __import__("os").environ:
                del __import__("os").environ[var]
        asyncio.run(redis_store.set("crucix:test", payload))

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        "exceeds error threshold" in r.getMessage()
        for r in errors
    ), "26 MB SET did not emit error-threshold error"


def test_set_under_default_threshold_does_not_warn(caplog):
    """Below 4 MB: no warning. The 2.6 MB knowledge blob lands here —
    that's the noise we wanted to eliminate from F88."""
    from aria_service.intel import redis_store

    fake_client = AsyncMock()
    payload = "z" * 2_700_000  # 2.7 MB — like the prod knowledge blob

    with patch.object(redis_store, "_client", fake_client), \
         patch.object(redis_store, "_BACKEND", "upstash"), \
         caplog.at_level(logging.WARNING, logger="aria.redis"):
        for var in ("ARIA_REDIS_WARN_BYTES", "ARIA_REDIS_ERROR_BYTES"):
            if var in __import__("os").environ:
                del __import__("os").environ[var]
        asyncio.run(redis_store.set("crucix:test", payload))

    cap_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
        and ("threshold" in r.getMessage())
    ]
    assert cap_warnings == [], (
        f"2.7 MB SET emitted unexpected size warning(s): "
        f"{[r.getMessage() for r in cap_warnings]}"
    )


def test_env_override_lowers_threshold_under_upstash(caplog):
    """Operator can dial thresholds down via env vars to match a
    smaller-tier Upstash account."""
    import os
    from aria_service.intel import redis_store

    fake_client = AsyncMock()
    payload = "q" * 800_000  # 800 KB

    with patch.object(redis_store, "_client", fake_client), \
         patch.object(redis_store, "_BACKEND", "upstash"), \
         patch.dict(os.environ, {
             "ARIA_REDIS_WARN_BYTES": "500000",   # 500 KB
             "ARIA_REDIS_ERROR_BYTES": "1000000", # 1 MB
         }), \
         caplog.at_level(logging.WARNING, logger="aria.redis"):
        asyncio.run(redis_store.set("crucix:test", payload))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "warn threshold" in r.getMessage() and "500000" in r.getMessage()
        for r in warnings
    ), "Env-override warn threshold did not take effect"


# R-F726 (2026-05-19) — regression tests for backend-aware warn gating.

def test_set_no_warn_on_sqlite_backend_even_above_default(caplog):
    """R-F726: under sqlite backend, the warn does NOT fire — SQLite has
    no per-value tier cap, so the 'plan a key split before this hits the
    tier cap' message is misleading noise. This is the live symptom
    captured 2026-05-19 in fly logs (neural_edges ~4.04 MB, ~12 warnings
    per signal_generator cycle, never clearing because §7 mandates
    infinite memory + no eviction)."""
    from aria_service.intel import redis_store

    payload = "x" * 5_000_000  # 5 MB — above default 4 MB warn

    # Patch state_store.set so the sqlite dispatch is a no-op (we're
    # asserting on the LOGGER behaviour, not the storage call).
    async def _noop_set(key, value, ex=None, keepttl=False):
        return None

    with patch.object(redis_store, "_BACKEND", "sqlite"), \
         patch("aria_service.intel.state_store.set", new=_noop_set), \
         caplog.at_level(logging.WARNING, logger="aria.redis"):
        for var in ("ARIA_REDIS_WARN_BYTES", "ARIA_REDIS_ERROR_BYTES"):
            if var in __import__("os").environ:
                del __import__("os").environ[var]
        asyncio.run(redis_store.set("crucix:test", payload))

    warn_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "warn threshold" in r.getMessage()
    ]
    assert warn_records == [], (
        "R-F726 regression: 5 MB SET under sqlite backend emitted a "
        f"warn-threshold WARNING ({[r.getMessage() for r in warn_records]}). "
        "SQLite has no tier cap — the warn should be Upstash-only."
    )


def test_set_no_warn_on_memory_backend_even_above_default(caplog):
    """R-F726: same gating applies to the memory backend — no per-value
    cap in the in-process dict, so the size warn is noise."""
    from aria_service.intel import redis_store

    payload = "x" * 5_000_000

    with patch.object(redis_store, "_BACKEND", "memory"), \
         patch.object(redis_store, "_client", None), \
         caplog.at_level(logging.WARNING, logger="aria.redis"):
        for var in ("ARIA_REDIS_WARN_BYTES", "ARIA_REDIS_ERROR_BYTES"):
            if var in __import__("os").environ:
                del __import__("os").environ[var]
        asyncio.run(redis_store.set("crucix:test", payload))

    warn_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "warn threshold" in r.getMessage()
    ]
    assert warn_records == [], (
        "R-F726 regression: 5 MB SET under memory backend emitted a "
        f"warn-threshold WARNING ({[r.getMessage() for r in warn_records]})."
    )


def test_set_no_warn_when_upstash_backend_but_client_is_none(caplog):
    """R-F726: production state per next_session_pickup_2026_05_18b —
    ARIA_STATE_BACKEND defaults to 'upstash' (the hidden gate-#5 item
    not yet flipped to sqlite live), but the actual Upstash client
    failed to connect because the subscription was cancelled 2026-05-12.
    Writes fall through to the in-process _mem_store, which has no cap.
    The warn must be silent in this case too, otherwise the fix is a
    no-op for prod until the operator flips the env."""
    from aria_service.intel import redis_store

    payload = "x" * 5_000_000

    with patch.object(redis_store, "_BACKEND", "upstash"), \
         patch.object(redis_store, "_client", None), \
         caplog.at_level(logging.WARNING, logger="aria.redis"):
        for var in ("ARIA_REDIS_WARN_BYTES", "ARIA_REDIS_ERROR_BYTES"):
            if var in __import__("os").environ:
                del __import__("os").environ[var]
        asyncio.run(redis_store.set("crucix:test", payload))

    warn_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "warn threshold" in r.getMessage()
    ]
    assert warn_records == [], (
        "R-F726 regression: 5 MB SET with backend=upstash but _client=None "
        f"emitted a warn-threshold WARNING ({[r.getMessage() for r in warn_records]}). "
        "Without a live Upstash client, the value falls through to "
        "_mem_store — no tier cap applies — so no warn should fire."
    )


def test_set_still_errors_on_sqlite_above_25mb(caplog):
    """R-F726: error threshold is backend-agnostic — large blobs amplify
    write cost on every flush regardless of storage. SQLite at 25MB+
    rewrites a full row on each call, still worth surfacing."""
    from aria_service.intel import redis_store

    payload = "y" * 26_000_000

    async def _noop_set(key, value, ex=None, keepttl=False):
        return None

    with patch.object(redis_store, "_BACKEND", "sqlite"), \
         patch("aria_service.intel.state_store.set", new=_noop_set), \
         caplog.at_level(logging.ERROR, logger="aria.redis"):
        for var in ("ARIA_REDIS_WARN_BYTES", "ARIA_REDIS_ERROR_BYTES"):
            if var in __import__("os").environ:
                del __import__("os").environ[var]
        asyncio.run(redis_store.set("crucix:test", payload))

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        "exceeds error threshold" in r.getMessage() for r in errors
    ), "R-F726: 26 MB SET under sqlite did not emit error-threshold ERROR"
