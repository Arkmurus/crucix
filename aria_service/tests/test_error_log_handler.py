"""Regression guard for B1 — error-ledger logging handler.

Live observation 2026-04-27 (and all prior sessions): self_improve cycle
reported "Cycle complete: 0 errors, 0 bugs, 0 auto-deployed" every run.
Cause: self_improve.record_error was only called from 2 LLM-error sites
in aria_engine.py. Other failure modes (JSON parse, HTTP fetch, sanctions
errors, brain absorption errors, RSS failures, etc.) were log.warning'd
but never recorded. The cycle had nothing to analyse.

Fix: install a logging handler that mirrors WARNING+ records from any
aria.* logger into the error ledger via record_error. This is the
lowest-touch way to extend coverage without instrumenting every callsite.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch


def test_handler_install_idempotent():
    """Calling install twice must not stack handlers."""
    from aria_service.intel import error_log_handler
    error_log_handler.uninstall()
    h1 = error_log_handler.install()
    h2 = error_log_handler.install()
    assert h1 is h2, "second install must return the same handler instance"
    aria_logger = logging.getLogger("aria")
    matching = [h for h in aria_logger.handlers if isinstance(h, error_log_handler.ErrorLedgerHandler)]
    assert len(matching) == 1, f"expected exactly 1 handler, got {len(matching)}"
    error_log_handler.uninstall()


def test_warning_log_forwarded_to_ledger():
    """A WARNING from an aria.* logger must call record_error."""
    from aria_service.intel import error_log_handler

    recorded: list = []

    async def fake_record_error(**kwargs):
        recorded.append(kwargs)

    async def run():
        error_log_handler.uninstall()
        with patch("aria_service.intel.self_improve.record_error",
                   side_effect=fake_record_error):
            error_log_handler.install()
            try:
                logging.getLogger("aria.test").warning("simulated failure XYZ")
                # Let the create_task fire
                await asyncio.sleep(0.01)
            finally:
                error_log_handler.uninstall()

    asyncio.run(run())
    assert len(recorded) == 1
    assert "log:warning" in recorded[0]["error_type"]
    assert "simulated failure XYZ" in recorded[0]["message"]


def test_third_party_logger_NOT_forwarded():
    """httpx, huggingface_hub, sentence_transformers etc. must NOT be
    routed through the ledger — they aren't actionable for self-improvement."""
    from aria_service.intel import error_log_handler

    recorded: list = []

    async def fake_record_error(**kwargs):
        recorded.append(kwargs)

    async def run():
        error_log_handler.uninstall()
        with patch("aria_service.intel.self_improve.record_error",
                   side_effect=fake_record_error):
            error_log_handler.install()
            try:
                logging.getLogger("httpx").warning("third-party warning, ignore")
                logging.getLogger("huggingface_hub.utils._http").warning(
                    "Warning: You are sending unauthenticated requests to the HF Hub"
                )
                logging.getLogger("sentence_transformers").warning("model warming")
                await asyncio.sleep(0.01)
            finally:
                error_log_handler.uninstall()

    asyncio.run(run())
    assert recorded == [], (
        f"third-party loggers leaked into ledger: {recorded}"
    )


def test_skip_substrings_filtered():
    """Transient noise like 'Skipping X (cooling down)' must not pollute
    the ledger — it's operational, not a bug."""
    from aria_service.intel import error_log_handler

    recorded: list = []

    async def fake_record_error(**kwargs):
        recorded.append(kwargs)

    async def run():
        error_log_handler.uninstall()
        with patch("aria_service.intel.self_improve.record_error",
                   side_effect=fake_record_error):
            error_log_handler.install()
            try:
                lg = logging.getLogger("aria.test")
                lg.warning("Provider anthropic failed (1): trying next")  # 'trying next'
                lg.warning("Skipping searx (cooling down)")               # 'skipping'
                lg.warning("Provider X HARD cooldown re-fired by burst peer")  # 'burst peer'
                lg.warning("RAG init failed (non-fatal): some error")     # 'non-fatal'
                # One non-noise line that SHOULD be recorded
                lg.warning("Real failure that needs fixing")
                await asyncio.sleep(0.01)
            finally:
                error_log_handler.uninstall()

    asyncio.run(run())
    assert len(recorded) == 1, (
        f"expected only the real failure recorded, got {len(recorded)}: "
        f"{[r['message'] for r in recorded]}"
    )
    assert "Real failure that needs fixing" in recorded[0]["message"]


def test_self_recursion_guard():
    """The handler logs an INFO message on install. That message comes
    from aria.error_log_handler itself — must NOT recurse into the ledger."""
    from aria_service.intel import error_log_handler

    recorded: list = []

    async def fake_record_error(**kwargs):
        recorded.append(kwargs)

    async def run():
        error_log_handler.uninstall()
        with patch("aria_service.intel.self_improve.record_error",
                   side_effect=fake_record_error):
            error_log_handler.install()  # this fires aria.error_log_handler INFO
            try:
                # Now fire our own logger at WARNING — must NOT propagate
                logging.getLogger("aria.error_log_handler").warning("self-test")
                await asyncio.sleep(0.01)
            finally:
                error_log_handler.uninstall()

    asyncio.run(run())
    assert recorded == [], "handler must skip records from its own logger"
