"""Regression guard for F29 — fallback HARD-cooldown burst debounce.

Live observation 2026-04-27 20:00:35: 5 parallel calls all hit
Anthropic with billing 400 in 196ms. Each independently tripped
_record_failure → set cooldown_until → emit ERROR log. Net effect: 5
identical "Provider anthropic HARD cooldown (billing) for 1800s"
ERROR lines per cold-start ingest. Log noise made it harder to spot
real cooldowns from new sources.

Fix: when a burst peer has already set the cooldown within the last
~5 seconds (i.e. the cooldown is fresh), don't re-set or re-log.
Failure count is still incremented so health metrics stay accurate.
"""
from __future__ import annotations

import logging
import time

import pytest


def _build_chain(provider_name: str = "anthropic"):
    """Build a minimal LLMFallbackChain with a single fake provider so we
    can call _record_failure directly."""
    from aria_service.llm.fallback import FallbackProvider
    chain = FallbackProvider.__new__(FallbackProvider)
    chain._stats = {provider_name: {"calls": 0, "failures": 0, "last_failure": 0,
                                     "cooldown_until": 0, "last_kind": ""}}
    chain._HARD_COOLDOWN_SECONDS = 1800
    chain._SOFT_COOLDOWN_SECONDS = 60
    return chain


def _make_billing_error():
    """Build a ProviderError that classifies as billing — same as the
    Anthropic 400 path in production."""
    from aria_service.llm.provider import ProviderError
    return ProviderError(
        "anthropic",
        "billing / credit exhausted (HTTP 400)",
        kind="billing",
        retryable=False,
    )


class _FakeProvider:
    name = "anthropic"


def test_first_failure_logs_error(caplog):
    """First call to fail must emit the ERROR log."""
    chain = _build_chain()
    err = _make_billing_error()

    with caplog.at_level(logging.DEBUG, logger="aria.llm.fallback"):
        chain._record_failure(_FakeProvider(), chain._stats["anthropic"], err)

    error_lines = [r.message for r in caplog.records if "HARD cooldown" in r.message and r.levelname == "ERROR"]
    assert len(error_lines) == 1, f"first failure must emit one ERROR; got {error_lines}"


def test_burst_peers_debounced_to_debug(caplog):
    """5 parallel calls with the same billing failure must produce ONE
    ERROR log, not 5."""
    chain = _build_chain()
    err = _make_billing_error()

    with caplog.at_level(logging.DEBUG, logger="aria.llm.fallback"):
        for _ in range(5):
            chain._record_failure(_FakeProvider(), chain._stats["anthropic"], err)

    error_lines = [r for r in caplog.records if "HARD cooldown" in r.message and r.levelname == "ERROR"]
    debug_lines = [r for r in caplog.records if "burst peer" in r.message]
    assert len(error_lines) == 1, (
        f"expected 1 ERROR after 5 bursts, got {len(error_lines)}: "
        f"{[r.message for r in error_lines]}"
    )
    assert len(debug_lines) == 4, (
        f"expected 4 DEBUG burst-peer lines, got {len(debug_lines)}"
    )


def test_failure_count_still_incremented_in_burst():
    """Even when debounced, failure count must keep counting so health
    metrics are accurate."""
    chain = _build_chain()
    err = _make_billing_error()
    for _ in range(5):
        chain._record_failure(_FakeProvider(), chain._stats["anthropic"], err)
    assert chain._stats["anthropic"]["failures"] == 5


def test_late_failure_after_cooldown_relogged(caplog):
    """If a new failure arrives AFTER the previous cooldown expired
    (or got close to expiring), it must re-log so the operator sees a
    repeat outage."""
    chain = _build_chain()
    err = _make_billing_error()

    chain._record_failure(_FakeProvider(), chain._stats["anthropic"], err)
    # Manually expire the cooldown to simulate "30 minutes later"
    chain._stats["anthropic"]["cooldown_until"] = time.time() - 10

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="aria.llm.fallback"):
        chain._record_failure(_FakeProvider(), chain._stats["anthropic"], err)

    error_lines = [r for r in caplog.records if "HARD cooldown" in r.message and r.levelname == "ERROR"]
    assert len(error_lines) == 1, (
        "expired-cooldown re-failure must re-log as ERROR; the operator "
        "needs to see that the outage continues"
    )
