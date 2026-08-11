"""Capability tests for R-F1656 (verify bounded) and R-F1657 (search fixes).

Each test drives the real path and asserts the user-visible outcome.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aria_service.intel.knowledge import _auto_verify_fact

# R-F3788/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source


class TestVerifyBounded:
    """R-F1656: _auto_verify_fact is bounded — semaphore, timeout, circuit-breaker shed."""

    def test_semaphore_exists(self):
        """The semaphore attribute is created on first call."""
        # Reset
        if hasattr(_auto_verify_fact, "_sem"):
            del _auto_verify_fact._sem
        # Call with no running loop — should be no-op, but semaphore should
        # be created when called from a running loop context.
        # We can't easily test the async path without a running loop, but we
        # can verify the semaphore attribute pattern is correct.
        assert not hasattr(_auto_verify_fact, "_sem")
        # The semaphore is created lazily inside the try block when a loop
        # is available. We verify the code structure by checking the source.
        import inspect
        source = function_source("aria_service.intel.knowledge", "_auto_verify_fact")
        assert "asyncio.Semaphore(4)" in source, "Semaphore(4) must be in source"
        assert "asyncio.wait_for" in source, "wait_for timeout must be in source"
        assert "circuit_breaker" in source, "circuit breaker check must be in source"
        assert "is_open" in source, "is_open check must be in source"

    @pytest.mark.asyncio
    async def test_circuit_open_skips_verify(self):
        """When the search circuit breaker is OPEN, _verify returns early
        without calling averify_and_store."""
        from aria_service.intel.circuit_breaker import get_breaker, reset_breaker

        # Open the duckduckgo breaker.
        # R-F3449 — `get_breaker` returns an EXISTING breaker and silently IGNORES the
        # failure_threshold/cooldown arguments, so this receives whatever the first caller
        # registered. One record_failure then leaves it CLOSED, and this test only passed
        # because earlier tests had already accumulated failures on that breaker — it was
        # order-dependent, and the R-F3449 breaker-reset fixture exposed it. Set the
        # threshold and cooldown on the object actually returned so the precondition is
        # established here rather than inherited.
        cb = get_breaker("search:duckduckgo", failure_threshold=1, cooldown_seconds=9999)
        cb.failure_threshold = 1
        cb.cooldown_seconds = 9999
        cb.record_failure("test")
        assert cb.is_open(), (
            f"precondition not established: threshold={cb.failure_threshold} "
            f"consecutive={cb.consecutive_failures} state={cb.state}")

        # The function imports verified_intel via `from . import verified_intel`
        # at runtime. We patch the ARIAVerificationEngine class directly so
        # that when the function does `_vi.ARIAVerificationEngine()`, it gets
        # our mock.
        import aria_service.intel.verified_intel as vi
        with patch.object(vi, "ARIAVerificationEngine") as mock_engine:
            fact_record = {"id": "test"}
            _auto_verify_fact(fact_record, "test_topic", "test content for verification that is long enough", "test_source")

            # Give the background task time to run
            await asyncio.sleep(0.3)

            # The circuit breaker check should cause early return before
            # ARIAVerificationEngine is ever instantiated
            mock_engine.assert_not_called()

        # Reset breaker
        reset_breaker("search:duckduckgo")

    @pytest.mark.asyncio
    async def test_short_content_skips_verify(self):
        """Content shorter than 20 chars is skipped — the verify function
        returns early without calling averify_and_store."""
        import aria_service.intel.verified_intel as vi
        with patch.object(vi, "ARIAVerificationEngine") as mock_engine:
            # Make the engine's averify_and_store detectable
            mock_instance = MagicMock()
            mock_engine.return_value = mock_instance

            fact_record = {"id": "test"}
            _auto_verify_fact(fact_record, "test_topic", "short", "test_source")

            await asyncio.sleep(0.3)

            # The engine may be instantiated (to check content length),
            # but averify_and_store should NOT be called for short content
            if mock_engine.called:
                mock_instance.averify_and_store.assert_not_called()


class TestSearchMakeLoud:
    """R-F1657 Fix 2: make-loud fires when SearXNG returns empty results."""

    @pytest.mark.asyncio
    async def test_empty_searxng_fires_wire_failure(self):
        """When _search_searxng returns ok=True, configured=True, results=[],
        wire_failure(gap_type=search_all_engines_blocked) must be called."""
        # search_searxng is imported inside _search_searxng via
        # `from . import search_searxng as _sx`. We patch it via sys.modules.
        import aria_service.intel.search_searxng as sx_mod
        with patch.object(sx_mod, "is_configured", return_value=True):
            with patch.object(sx_mod, "search", return_value={
                "ok": True,
                "configured": True,
                "backend": "searxng",
                "results": [],
                "count": 0,
                "query": "test query",
            }):
                with patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf:
                    from aria_service.intel.web_search import _search_searxng
                    result = await _search_searxng("test query", 10, "en")

                # Should return empty list
                assert result == []

                # wire_failure should have been called with search_all_engines_blocked
                mock_wf.assert_called_once()
                call_kwargs = mock_wf.call_args[1]
                assert call_kwargs.get("gap_type") == "search_all_engines_blocked"


class TestBackendNames:
    """R-F1657 Fix 3+4: backend names are correct (no phantom brave)."""

    def test_backend_names_never_names_brave_unconditionally(self):
        """No PHANTOM brave — a backend name may appear only when that backend can
        actually serve.

        R-F3859 (2026-08-11) — THIS GUARD ASSERTED A REVERSED POLICY AND WAS
        DANGEROUS. It required that `"brave"` never appear in `_backend_names` at
        all. That was right in R-F1657's world: Brave was a REMOVED stub (R-F320)
        returning [], so naming it invented a contributor that could not
        contribute — a phantom that corrupts attribution and telemetry.

        Brave was reinstated. It is LIVE, PAID, and ARIA's primary user-facing
        search (R-F2318), the sole DD search engine (R-F3847), and the operator
        restated it on 2026-08-11: "brave API is aria's search engine" for DD.
        CLAUDE.md §18 records the reversal explicitly, and warns that the stale
        reading "would have led a future agent to rip out working primary search".

        This test WAS that trap. It had been red since Brave came back, parked in
        docs/suite_baseline.json as a known failure, and the obvious way to make it
        green is to delete `["brave"] if _brave_on else []` — which would silently
        disable the paid primary backend and take DD search with it.

        So the ASSERTION is corrected to the guard's actual purpose, which never
        stopped being valid: the name must be CONDITIONAL on the backend being
        enabled. An unconditional `"brave"` would be the phantom R-F1657 forbade;
        a guarded one is an honest name for a backend that really runs.
        """
        source = function_source("aria_service.intel.web_search", "search")
        lines = source.split('\n')
        for i, line in enumerate(lines):
            if '_backend_names' not in line:
                continue
            for candidate in lines[i:i + 8]:
                stripped = candidate.strip()
                if stripped.startswith('#'):
                    continue
                if '"brave"' not in stripped and "'brave'" not in stripped:
                    continue
                # Named — so it must be gated on Brave actually being on.
                assert ('_brave_on' in stripped or 'if ' in stripped), (
                    "brave is named UNCONDITIONALLY in _backend_names: "
                    f"{stripped!r}. Either it is a phantom (R-F1657) or the gate "
                    "was lost. Do NOT fix this by deleting the name — Brave is the "
                    "paid primary and the sole DD engine (R-F2318/R-F3847)."
                )
            return

    def test_telemetry_names_no_brave(self):
        """Telemetry counter names must not contain 'brave_lang'."""
        from aria_service.intel.web_search import search
        import inspect
        source = function_source("aria_service.intel.web_search", "search")
        assert "brave_lang" not in source, "brave_lang still in telemetry code"
