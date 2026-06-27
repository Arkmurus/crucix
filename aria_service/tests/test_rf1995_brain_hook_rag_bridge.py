"""R-F1995: brain_hook RAG enrichment — detail text fed into knowledge+RAG.

Tests:
1. brain_hook.absorb() stores summary in knowledge (existing behaviour preserved)
2. brain_hook.absorb() also stores detail text as a separate knowledge fact
3. The detail fact has the :detail suffix topic
4. Short detail text (<200 chars) is skipped (no pointless tiny facts)
5. The bridge doesn't break when detail is empty
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel.brain_hook_bg import absorb_tiers_bg


@pytest.fixture
def mock_deps() -> dict:
    """Set up all the dependencies absorb_tiers_bg needs."""
    result: dict = {
        "mastery_ok": False,
        "knowledge_ok": False,
        "neural_ok": False,
        "gap_ok": True,
        "errors": [],
    }

    async def _run_tier(coro, label: str):
        try:
            await coro
            return (True, None)
        except Exception as e:
            return (False, str(e))

    async def _record_signal(*a, **kw):
        pass

    def _record_latency(*a, **kw):
        pass

    def _maybe_trip_breaker(*a, **kw):
        pass

    def _get_sem():
        class FakeSem:
            async def acquire(self):
                pass

            def release(self):
                pass
        return FakeSem()

    return {
        "result": result,
        "_run_tier": _run_tier,
        "_record_signal": _record_signal,
        "_record_latency": _record_latency,
        "_maybe_trip_breaker": _maybe_trip_breaker,
        "_get_absorb_concurrency_sem": _get_sem,
        "_get_neural_concurrency_sem": _get_sem,
        "_ABSORB_CONCURRENCY": 5,
        "_ABSORB_SEM_ACQUIRE_TIMEOUT_S": 1.0,
        "_start_ms": 0.0,
    }


class TestBrainHookRagBridge:
    """Capability tests for the RAG enrichment bridge."""

    async def _run_absorb(self, mock_deps: dict, **overrides) -> dict:
        """Run absorb_tiers_bg with overridable params."""
        params = {
            "module": "test_module",
            "summary": "Test summary for Modirum Gespi",
            "text_for_neural": "",
            "source": "brain_hook:test_module:test_run",
            "topics": ["compliance"],
            "success": True,
            "weight": 0.15,
            "confidence": "ASSESSED",
            "entity_name": "Modirum Gespi",
            "gap_type": None,
            "gap_detail": None,
            "sector": "",
            "user_id": "",
        }
        params.update(overrides)
        params.update(mock_deps)
        await absorb_tiers_bg(**params)
        return mock_deps["result"]

    @pytest.mark.asyncio
    async def test_summary_stored(self, mock_deps: dict) -> None:
        """Summary fact is stored (existing behaviour preserved)."""
        with patch.object(
            __import__("aria_service.intel.knowledge", fromlist=["store_fact"]),
            "store_fact",
            new=AsyncMock(return_value={"action": "created", "fact_id": "f1"}),
        ) as mock_store:
            await self._run_absorb(
                mock_deps,
                summary="DD report: Modirum Gespi - risk=AMBER-LIGHT",
                text_for_neural="",
            )
            # store_fact should have been called at least once (for summary)
            assert mock_store.called

    @pytest.mark.asyncio
    async def test_detail_stored_as_separate_fact(self, mock_deps: dict) -> None:
        """Detail text is stored as a separate :detail fact."""
        calls = []

        async def _capture_store(topic, content, source, confidence, **kw):
            calls.append({"topic": topic, "content_len": len(content)})
            return {"action": "created", "fact_id": "f2"}

        with patch.object(
            __import__("aria_service.intel.knowledge", fromlist=["store_fact"]),
            "store_fact",
            new=_capture_store,
        ):
            await self._run_absorb(
                mock_deps,
                summary="DD report: Modirum Gespi - risk=AMBER-LIGHT",
                text_for_neural=(
                    "Modirum Gespi is a Finnish payment processing company. "
                    "They provide merchant services and payment gateway solutions. "
                    "The company was founded in 2015 and is headquartered in Helsinki. "
                    "Key findings: no direct sanctions exposure, registered with Finnish "
                    "Financial Supervisory Authority (FIN-FSA). "
                    "Website: https://modirumgespi.com/en. "
                    "The company processes payments for high-risk merchants including "
                    "gaming, forex, and adult entertainment. "
                    "This warrants further AML/KYC review."
                ),
            )
            # Should have 2 calls: one for summary, one for detail
            detail_calls = [c for c in calls if ":detail" in c["topic"]]
            assert len(detail_calls) == 1, (
                f"Expected 1 detail fact, got {len(detail_calls)}. "
                f"All calls: {calls}"
            )
            assert detail_calls[0]["content_len"] > 200

    @pytest.mark.asyncio
    async def test_short_detail_skipped(self, mock_deps: dict) -> None:
        """Detail text under 200 chars is not stored as a separate fact."""
        calls = []

        async def _capture_store(topic, content, source, confidence, **kw):
            calls.append({"topic": topic})
            return {"action": "created", "fact_id": "f3"}

        with patch.object(
            __import__("aria_service.intel.knowledge", fromlist=["store_fact"]),
            "store_fact",
            new=_capture_store,
        ):
            await self._run_absorb(
                mock_deps,
                summary="Short summary",
                text_for_neural="Short detail under 200 chars",
            )
            detail_calls = [c for c in calls if ":detail" in c["topic"]]
            assert len(detail_calls) == 0, (
                f"Expected 0 detail facts for short text, got {len(detail_calls)}"
            )

    @pytest.mark.asyncio
    async def test_empty_detail_no_break(self, mock_deps: dict) -> None:
        """Empty detail text doesn't break the bridge."""
        with patch.object(
            __import__("aria_service.intel.knowledge", fromlist=["store_fact"]),
            "store_fact",
            new=AsyncMock(return_value={"action": "created", "fact_id": "f4"}),
        ):
            result = await self._run_absorb(
                mock_deps,
                summary="Test summary",
                text_for_neural="",
            )
            # Should complete without error
            assert "errors" not in result or not result.get("errors")
