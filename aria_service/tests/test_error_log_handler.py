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


def test_recorded_file_is_project_relative_path_F96():
    """F96 2026-04-30: pre-fix the handler stored record.filename which is
    the basename only (knowledge.py), so the self_improve.py:950 check
    `if file_path not in MODIFIABLE_FILES` (keyed on relative paths like
    'aria_service/intel/knowledge.py') always failed and bugs_detected
    stayed at 0 forever. Post-fix we must store the project-relative POSIX
    path so the membership check has a fighting chance."""
    from aria_service.intel import error_log_handler, self_improve

    recorded: list = []

    async def fake_record_error(**kwargs):
        recorded.append(kwargs)

    async def run():
        error_log_handler.uninstall()
        with patch("aria_service.intel.self_improve.record_error",
                   side_effect=fake_record_error):
            error_log_handler.install()
            try:
                # Log from a real aria.* logger — the handler will populate
                # `file` from the call site (THIS test file's pathname).
                logging.getLogger("aria.test").warning(
                    "F96 path-resolution probe"
                )
                await asyncio.sleep(0.01)
            finally:
                error_log_handler.uninstall()

    asyncio.run(run())
    assert len(recorded) == 1
    file_field = recorded[0]["file"]
    # Must be project-relative POSIX, not the bare basename.
    assert "/" in file_field, (
        f"expected project-relative POSIX path, got bare basename: {file_field!r}"
    )
    assert file_field.endswith("test_error_log_handler.py"), (
        f"path should end with this test file's name; got {file_field!r}"
    )
    assert "aria_service/tests/" in file_field, (
        f"expected aria_service/tests/ prefix; got {file_field!r}"
    )

    # And the resolution helper itself must produce a string that lines up
    # with at least one MODIFIABLE_FILES entry shape (the membership check
    # this whole fix exists to enable).
    sample_resolved = error_log_handler._project_relative_path(
        str(error_log_handler._PROJECT_ROOT
            / "aria_service" / "intel" / "knowledge.py")
    )
    assert sample_resolved == "aria_service/intel/knowledge.py"
    # Ensure MODIFIABLE_FILES is populated (R-F1032: dynamically populated at boot)
    import asyncio as _aio
    _aio.run(self_improve._ensure_modifiable_files())
    assert sample_resolved in self_improve.MODIFIABLE_FILES


def test_uppercase_ARIA_logger_forwarded_R_F891():
    """R-F891 capability test: ~30 modules (dd_orchestrator, security_protocol,
    global_export_control, regional_compliance, …) use the legacy uppercase
    'ARIA.*' logger name. Pre-fix those records never reached this handler
    (it was attached only to lowercase 'aria' and filtered on a case-sensitive
    'aria' prefix), so the R-F886 DD-compliance-layer WARNING promotions were
    invisible to the brain/coder. Post-fix an 'ARIA.*' WARNING must hit the
    ledger exactly like an 'aria.*' one."""
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
                # The exact logger name dd_orchestrator uses (dd_orchestrator.py:71)
                logging.getLogger("ARIA.DDOrchestrator").warning(
                    "PSC layer degraded: upstream lookup failed"
                )
                await asyncio.sleep(0.01)
            finally:
                error_log_handler.uninstall()

    asyncio.run(run())
    assert len(recorded) == 1, (
        f"uppercase ARIA.* WARNING did not reach the ledger: {recorded}"
    )
    assert "log:warning" in recorded[0]["error_type"]
    assert "PSC layer degraded" in recorded[0]["message"]


def test_install_attaches_to_both_aria_and_ARIA_R_F891():
    """R-F891: install must attach the handler to BOTH the lowercase 'aria'
    and uppercase 'ARIA' root loggers, idempotently (exactly one each)."""
    from aria_service.intel import error_log_handler
    error_log_handler.uninstall()
    error_log_handler.install()
    error_log_handler.install()  # idempotent
    try:
        for root_name in ("aria", "ARIA"):
            root = logging.getLogger(root_name)
            matching = [h for h in root.handlers
                        if isinstance(h, error_log_handler.ErrorLedgerHandler)]
            assert len(matching) == 1, (
                f"expected exactly 1 handler on '{root_name}', got {len(matching)}"
            )
    finally:
        error_log_handler.uninstall()
    # uninstall must detach from both
    for root_name in ("aria", "ARIA"):
        root = logging.getLogger(root_name)
        assert not [h for h in root.handlers
                    if isinstance(h, error_log_handler.ErrorLedgerHandler)], (
            f"uninstall left a handler attached to '{root_name}'"
        )


def test_security_operational_noise_filtered_R_F891():
    """R-F891: now the ARIA.* tree feeds the ledger, security_protocol's
    per-request operational detections ('Prompt injection detected',
    'Output sanitisation total') must be filtered — they are detections, not
    code bugs, and would flood/mislead the error ledger."""
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
                lg = logging.getLogger("ARIA.SecurityProtocol")
                lg.warning("Prompt injection detected: risk=HIGH blocked=False categories=['x']")
                lg.warning("Output sanitisation total: 3 redactions applied")
                # A genuine security ENGINE failure must still be recorded.
                lg.warning("Security audit crashed: unexpected state")
                await asyncio.sleep(0.01)
            finally:
                error_log_handler.uninstall()

    asyncio.run(run())
    assert len(recorded) == 1, (
        f"only the genuine failure should record, got: {[r['message'] for r in recorded]}"
    )
    assert "Security audit crashed" in recorded[0]["message"]


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


def test_timed_out_cascade_killer_R_F2156():
    """R-F2156: a WARNING containing 'timed out' must NOT trigger record_error.
    
    This is the cascade-killer for the state_store.get() timeout feedback loop:
    when state_store.get("crucix:aria:error_log") times out, it logs a WARNING
    containing "timed out". Without this filter, the error_log_handler would
    call record_error(), which reads the error_log key again → another timeout
    → another WARNING → infinite feedback loop.
    """
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
                lg = logging.getLogger("aria.state_store")
                # Exact message from state_store.get() when it times out
                lg.warning(
                    "state_store.get(crucix:aria:error_log) timed out after 5s "
                    "— DB may be bloated or under WAL recovery. Returning None."
                )
                # Also test a generic "timed out" message from any module
                lg.warning("Some other operation timed out after 10s")
                # A non-timeout WARNING should still be recorded
                lg.warning("Real code defect that needs fixing")
                await asyncio.sleep(0.01)
            finally:
                error_log_handler.uninstall()

    asyncio.run(run())
    # Only the non-timeout message should be recorded
    assert len(recorded) == 1, (
        f"expected only the real defect recorded, got {len(recorded)}: "
        f"{[r['message'] for r in recorded]}"
    )
    assert "Real code defect" in recorded[0]["message"]


def test_state_store_error_log_cooldown_R_F2156():
    """R-F2156: rapid repeated reads of the error_log key must be cached.
    
    When state_store is under load, every read of the error_log key times out.
    The cooldown cache prevents rapid-fire reads from hammering the DB.
    """
    from aria_service.intel import state_store

    # Reset the cache
    state_store._error_log_cache.clear()

    async def run():
        # First read — should hit the DB (or timeout)
        result1 = await state_store.get("crucix:aria:error_log")
        # Second read immediately — should return cached result without hitting DB
        result2 = await state_store.get("crucix:aria:error_log")
        # Both should return the same thing (None if not found, or the cached value)
        assert result1 == result2, (
            f"cached result mismatch: {result1!r} vs {result2!r}"
        )
        # The cache should have an entry for this key
        assert "crucix:aria:error_log" in state_store._error_log_cache, (
            "error_log key should be cached after first read"
        )

    asyncio.run(run())
    state_store._error_log_cache.clear()
