"""Regression tests for F87 + F88 — observability around Upstash value-cap.

F87 (2026-04-29 first batch): intel ledger silently dropped 4587 → 2000
signals between two boots. Suspected silent truncation when JSON blob
crossed Upstash's per-value cap.

F88 follow-up (same day, second batch): the F87 thresholds (700 KB
warn / 950 KB error) fired on every knowledge save because the
knowledge blob is ~2.6 MB and this account's Upstash is on a higher
tier than free. Recalibrated to env-configurable defaults (4 MB warn,
25 MB error) so the warnings are noise-free at steady state but still
catch genuinely-large outliers before the *actual* tier cap.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch


def test_set_warns_above_4mb_default(caplog):
    """Above the 4 MB default warn threshold, a WARNING fires."""
    from aria_service.intel import redis_store

    fake_client = AsyncMock()
    payload = "x" * 5_000_000  # 5 MB

    with patch.object(redis_store, "_client", fake_client), \
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
    ), "5 MB SET did not emit warn-threshold warning"


def test_set_errors_above_25mb_default(caplog):
    """Above the 25 MB error threshold, an ERROR fires."""
    from aria_service.intel import redis_store

    fake_client = AsyncMock()
    payload = "y" * 26_000_000  # 26 MB

    with patch.object(redis_store, "_client", fake_client), \
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


def test_env_override_lowers_threshold(caplog):
    """Operator can dial thresholds down via env vars to match a
    smaller-tier Upstash account."""
    import os
    from aria_service.intel import redis_store

    fake_client = AsyncMock()
    payload = "q" * 800_000  # 800 KB

    with patch.object(redis_store, "_client", fake_client), \
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
