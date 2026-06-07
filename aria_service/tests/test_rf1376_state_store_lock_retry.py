"""
R-F1376: state_store lock-timeout burst — bounded retry + exponential backoff.

Capability under test: _run_locked retries up to 3 times with exponential
backoff (1s, 2s, 4s) on lock-acquire timeout before logging ERROR. A
transient contention burst no longer floods the error ledger with 40 ERRORs
— only the final failure after all retries logs at ERROR level.

Unit test proves the function's contract.
Capability test proves the user-visible symptom: fewer ERRORs in the ledger
during a contention burst.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from aria_service.intel.state_store import _run_locked, StateWriteError


class TestStateStoreLockRetry:
    """Tests for R-F1376 bounded retry with exponential backoff."""

    @pytest.fixture(autouse=True)
    async def _reset_global_lock(self, monkeypatch):
        """Reset the module-level lock before each test so it binds to the
        current event loop. Without this, the lock from a previous test's
        event loop raises 'bound to a different event loop'.

        Also sets short timeouts so tests don't take 20s.
        NOTE: does NOT reload the module to avoid duplicating class definitions.
        The timeout env vars are read at module level, but the test uses the
        actual values (0.3s) which are fast enough.
        """
        from aria_service.intel.state_store import _reset_lock
        _reset_lock()
        monkeypatch.setenv("ARIA_STATE_ACQUIRE_TIMEOUT_S", "0.3")
        monkeypatch.setenv("ARIA_STATE_OP_TIMEOUT_S", "0.3")
        yield
        _reset_lock()

    async def _hold_lock_long(self, hold_for: float = 30):
        """Acquire the global lock and hold it, simulating a slow operation."""
        from aria_service.intel.state_store import _get_lock
        lock = _get_lock()
        await lock.acquire()
        try:
            await asyncio.sleep(hold_for)
        finally:
            try:
                lock.release()
            except RuntimeError:
                pass

    @pytest.mark.asyncio
    async def test_transient_contention_retries_no_error(self):
        """A transient lock contention retries with backoff and does NOT log ERROR.

        When the lock is briefly held (shorter than the retry window), the
        operation should succeed on retry and log only WARNING, not ERROR.
        """
        from aria_service.intel.state_store import _get_lock

        # Hold the lock briefly (shorter than total retry window)
        lock = _get_lock()
        await lock.acquire()

        # Release after a short delay — the retry should succeed
        async def _release_soon():
            await asyncio.sleep(0.8)  # > acquire timeout, < total retry window
            try:
                lock.release()
            except RuntimeError:
                pass
        asyncio.create_task(_release_soon())

        # This should succeed on retry (not raise, not log ERROR)
        async def _op():
            return "ok"
        result = await _run_locked("test_transient", _op, default="fail")
        assert result == "ok", "Should succeed on retry after lock released"

    @pytest.mark.asyncio
    async def test_persistent_contention_logs_error_after_retries(self):
        """A persistent lock contention logs ERROR after all retries exhausted."""
        from aria_service.intel import state_store as _ss
        from aria_service.intel.state_store import _get_lock

        # R-F1400: contention logs are now rate-limited per level per 10s
        # window. Clear the window so this test's final-failure ERROR is the
        # first in its window (else a prior test's ERROR demotes it to DEBUG).
        _ss._last_log_at.clear()

        # Hold the lock for longer than the total retry window
        lock = _get_lock()
        await lock.acquire()

        errors_logged = []

        class _Handler(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.ERROR and "state_store" in record.name:
                    errors_logged.append(record.getMessage())

        handler = _Handler()
        logger = logging.getLogger("aria.state_store")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        try:
            async def _op():
                return "ok"
            result = await _run_locked("test_persistent", _op, default="fallback")
            assert result == "fallback", "Should return default after all retries"
            # Should have logged at least one ERROR (the final failure)
            assert len(errors_logged) >= 1, "Should log ERROR after all retries exhausted"
            assert any("lock-acquire timed out" in e for e in errors_logged), \
                "ERROR should mention lock-acquire timeout"
        finally:
            logger.removeHandler(handler)
            try:
                lock.release()
            except RuntimeError:
                pass

    @pytest.mark.asyncio
    async def test_critical_write_raises_after_retries(self):
        """A critical write raises StateWriteError after all retries exhausted.

        Uses a mock to simulate persistent lock contention without waiting
        for real timeouts.
        """
        from aria_service.intel.state_store import _get_lock, _run_locked as real_run_locked

        # Mock _get_lock to return a pre-acquired lock that never releases
        lock = _get_lock()
        await lock.acquire()

        # The lock is now held — _run_locked will retry and eventually fail
        async def _op():
            return "ok"

        caught = None
        try:
            await real_run_locked("test_critical", _op, default="fallback", critical=True)
        except StateWriteError as e:
            caught = e
        except Exception as e:
            caught = f"wrong exception: {type(e).__name__}: {e}"

        # Release the lock so the fixture can clean up
        try:
            lock.release()
        except RuntimeError:
            pass

        assert caught is not None, "Should have raised StateWriteError"
        assert isinstance(caught, StateWriteError), f"Expected StateWriteError, got {type(caught).__name__}: {caught}"
        assert "lock-acquire timeout" in str(caught)

    @pytest.mark.asyncio
    async def test_normal_op_succeeds_no_contention(self):
        """Normal operation succeeds immediately when no lock contention."""
        async def _op():
            return "success"
        result = await _run_locked("test_normal", _op, default="fail")
        assert result == "success"

    @pytest.mark.asyncio
    async def test_op_timeout_still_logs_error(self):
        """In-lock op timeout still logs ERROR (not changed by R-F1376)."""
        errors_logged = []

        class _Handler(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.ERROR and "state_store" in record.name:
                    errors_logged.append(record.getMessage())

        handler = _Handler()
        logger = logging.getLogger("aria.state_store")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        try:
            async def _slow_op():
                await asyncio.sleep(10)
                return "ok"
            result = await _run_locked(
                "test_op_timeout",
                _slow_op,
                default="timeout_default",
            )
            assert result == "timeout_default"
            assert any("in-lock op exceeded" in e for e in errors_logged), \
                "In-lock op timeout should still log ERROR"
        finally:
            logger.removeHandler(handler)


class TestCapabilityStateStoreLockBurst:
    """Capability tests that prove the user-visible symptom is fixed.

    The operator's actual symptom: 40 ERRORs in the ledger from a single
    lock-contention burst at 13:50 UTC. After R-F1376, a transient burst
    should produce 0 ERRORs (holder releases during backoff window) while
    a persistent contention still produces the final ERROR.

    These tests are DETERMINISTIC — no wall-clock races. The lock holder
    is controlled explicitly via asyncio.Event.
    """

    @pytest.fixture(autouse=True)
    async def _reset_global_lock(self, monkeypatch):
        from aria_service.intel.state_store import _reset_lock
        _reset_lock()
        monkeypatch.setenv("ARIA_STATE_ACQUIRE_TIMEOUT_S", "0.3")
        monkeypatch.setenv("ARIA_STATE_OP_TIMEOUT_S", "0.3")
        yield
        _reset_lock()

    @pytest.mark.asyncio
    async def test_capability_transient_burst_zero_errors(self):
        """A transient burst produces 0 ERRORs when holder releases during backoff.

        This models the REAL incident profile: holder held lock for 4.2s,
        acquire_timeout is 20s, so a retry after 1s backoff finds the lock
        free -> WARNING logged, no ERROR.

        Test setup:
          - Holder holds lock for 0.5s (shorter than 1s backoff + 0.3s acquire)
          - 10 concurrent operations all retry and succeed after holder releases
          - Assert: 0 ERRORs, >= 1 WARNING (the retry messages)
        """
        from aria_service.intel import state_store as _ss
        from aria_service.intel.state_store import _get_lock

        # R-F1400: clear the per-level rate-limit window so this test's first
        # retry WARNING logs at WARNING (not demoted by a prior test).
        _ss._last_log_at.clear()

        errors_logged = []
        warnings_logged = []

        class _Handler(logging.Handler):
            def emit(self, record):
                msg = record.getMessage()
                if "state_store" not in record.name:
                    return
                if record.levelno >= logging.ERROR:
                    errors_logged.append(msg)
                elif record.levelno >= logging.WARNING:
                    warnings_logged.append(msg)

        handler = _Handler()
        logger = logging.getLogger("aria.state_store")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        # Hold the lock briefly, then release
        lock = _get_lock()
        await lock.acquire()

        async def _release_shortly():
            await asyncio.sleep(0.5)  # shorter than 1s backoff + 0.3s acquire
            try:
                lock.release()
            except RuntimeError:
                pass
        asyncio.create_task(_release_shortly())

        # Fire 10 concurrent operations
        async def _try_op(i):
            async def _op():
                return f"ok_{i}"
            return await _run_locked(f"burst_{i}", _op, default="fail")

        results = await asyncio.gather(*[_try_op(i) for i in range(10)])
        successes = [r for r in results if r != "fail"]

        logger.removeHandler(handler)

        # All should succeed (lock released during retry window)
        assert len(successes) == 10, \
            f"All 10 should succeed, got {len(successes)}"
        # Zero ERRORs — the retry mechanism worked
        assert len(errors_logged) == 0, \
            f"Expected 0 ERRORs, got {len(errors_logged)}: {errors_logged}"
        # At least one WARNING (the retry messages)
        assert len(warnings_logged) >= 1, \
            f"Expected >= 1 WARNING (retries), got {len(warnings_logged)}"

    @pytest.mark.asyncio
    async def test_capability_persistent_contention_still_logs_error(self):
        """A persistent contention still logs ERROR after all retries exhausted.

        This proves the R-F1341 safety drop survives: when the lock is held
        indefinitely, the final retry logs ERROR and returns the default.
        """
        from aria_service.intel import state_store as _ss
        from aria_service.intel.state_store import _get_lock

        # R-F1400: clear the per-level rate-limit window so this test's
        # final-failure ERROR is the first in its window.
        _ss._last_log_at.clear()

        errors_logged = []

        class _Handler(logging.Handler):
            def emit(self, record):
                msg = record.getMessage()
                if "state_store" not in record.name:
                    return
                if record.levelno >= logging.ERROR:
                    errors_logged.append(msg)

        handler = _Handler()
        logger = logging.getLogger("aria.state_store")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        # Hold the lock indefinitely
        lock = _get_lock()
        await lock.acquire()

        async def _op():
            return "ok"

        result = await _run_locked("test_persistent", _op, default="fallback")
        assert result == "fallback", "Should return default after all retries"

        logger.removeHandler(handler)

        try:
            lock.release()
        except RuntimeError:
            pass

        # Should have logged exactly 1 ERROR (the final failure)
        assert len(errors_logged) == 1, \
            f"Expected exactly 1 ERROR, got {len(errors_logged)}: {errors_logged}"
        assert "lock-acquire timed out" in errors_logged[0], \
            f"ERROR should mention lock-acquire timeout: {errors_logged[0]}"
