"""Regression test for F87 — observability around Upstash 1MB value cap.

Live event 2026-04-29: intel ledger silently dropped from 4587 signals
to 2000 between two boots — almost certainly because the serialised
JSON crossed Upstash free-tier's ~1MB single-value cap and the SET
either silently truncated or rejected with no in-app exception.

The fix can't recover the lost signals, but it MUST surface the next
truncation event in the logs so we can act before more data is lost.
This test pins the warn-at-700KB / error-at-950KB observability
contract on redis_store.set.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch


def test_set_warns_above_700kb_threshold(caplog):
    """Values > 700 KB should emit a WARNING (early trajectory signal)
    so the operator sees the ledger drift toward truncation BEFORE the
    cap is hit."""
    from aria_service.intel import redis_store

    fake_client = AsyncMock()
    payload = "x" * 750_000  # 750 KB

    with patch.object(redis_store, "_client", fake_client), \
         caplog.at_level(logging.WARNING, logger="aria.redis"):
        asyncio.run(redis_store.set("crucix:test", payload))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "approaching Upstash 1MB cap" in r.getMessage()
        and str(len(payload)) in r.getMessage()
        for r in warnings
    ), (
        "F87 regression: 750 KB SET did not emit the cap-approaching "
        "warning. Operator will lose visibility into ledger drift."
    )


def test_set_errors_above_950kb_threshold(caplog):
    """Values > 950 KB should emit an ERROR — that's the unmistakable
    'truncation imminent' signal."""
    from aria_service.intel import redis_store

    fake_client = AsyncMock()
    payload = "y" * 1_000_000  # 1 MB exactly

    with patch.object(redis_store, "_client", fake_client), \
         caplog.at_level(logging.ERROR, logger="aria.redis"):
        asyncio.run(redis_store.set("crucix:test", payload))

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        "EXCEEDS Upstash free-tier 1MB cap" in r.getMessage()
        for r in errors
    ), (
        "F87 regression: 1 MB SET did not emit the cap-exceeded error. "
        "Silent truncation will recur invisibly."
    )


def test_set_under_threshold_does_not_warn(caplog):
    """Normal-sized values (<700 KB) must NOT warn — otherwise we
    drown the logs in noise."""
    from aria_service.intel import redis_store

    fake_client = AsyncMock()
    payload = "z" * 100_000  # 100 KB — well under threshold

    with patch.object(redis_store, "_client", fake_client), \
         caplog.at_level(logging.WARNING, logger="aria.redis"):
        asyncio.run(redis_store.set("crucix:test", payload))

    cap_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
        and ("Upstash" in r.getMessage() or "approaching" in r.getMessage())
    ]
    assert cap_warnings == [], (
        f"100 KB SET emitted unexpected size warning(s): "
        f"{[r.getMessage() for r in cap_warnings]}"
    )
