"""R-F1219: Capability test — autonomous/safety.py and metacognitive/engine.py wiring.

Verifies that the previously-dark modules now emit brain signals.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ── autonomous/safety.py ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_safety_check_cost_cap_wires_failure_on_hit():
    """check_cost_cap calls wire_failure when daily cap is hit."""
    with patch("aria_service.autonomous.safety.wire_success") as mock_ws, \
         patch("aria_service.autonomous.safety.wire_failure") as mock_wf, \
         patch("aria_service.autonomous.safety.DAILY_COST_CAP_USD", 1.0), \
         patch("aria_service.autonomous.safety.rs.get", return_value=b"5.0"):
        from aria_service.autonomous.safety import check_cost_cap
        within, spent = await check_cost_cap()
        assert not within, "Should be over cap"
        assert mock_wf.called, "wire_failure was not called when cost cap hit"


@pytest.mark.asyncio
async def test_safety_pause_engine_wires_success():
    """pause_engine calls wire_success."""
    with patch("aria_service.autonomous.safety.wire_success") as mock_ws, \
         patch("aria_service.autonomous.safety.wire_failure") as mock_wf, \
         patch("aria_service.autonomous.safety.rs.set", AsyncMock()):
        from aria_service.autonomous.safety import pause_engine
        await pause_engine("test pause")
        assert mock_ws.called, "wire_success was not called on pause"


@pytest.mark.asyncio
async def test_safety_resume_engine_wires_success():
    """resume_engine calls wire_success."""
    with patch("aria_service.autonomous.safety.wire_success") as mock_ws, \
         patch("aria_service.autonomous.safety.wire_failure") as mock_wf, \
         patch("aria_service.autonomous.safety.rs.delete", AsyncMock()):
        from aria_service.autonomous.safety import resume_engine
        await resume_engine()
        assert mock_ws.called, "wire_success was not called on resume"


@pytest.mark.asyncio
async def test_safety_pause_engine_wires_failure_on_error():
    """pause_engine calls wire_failure when Redis fails."""
    with patch("aria_service.autonomous.safety.wire_success") as mock_ws, \
         patch("aria_service.autonomous.safety.wire_failure") as mock_wf, \
         patch("aria_service.autonomous.safety.rs.set", AsyncMock(side_effect=Exception("Redis down"))):
        from aria_service.autonomous.safety import pause_engine
        await pause_engine("test")
        assert mock_wf.called, "wire_failure was not called on Redis error"


# ── metacognitive/engine.py ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_metacog_self_assess_wires_success():
    """self_assess_output calls wire_success on completion."""
    with patch("aria_service.metacognitive.engine.wire_success") as mock_ws, \
         patch("aria_service.metacognitive.engine.wire_failure") as mock_wf, \
         patch("aria_service.metacognitive.engine.rs.lpush"), \
         patch("aria_service.metacognitive.engine.rs.ltrim"), \
         patch("aria_service.metacognitive.engine.calibration.record_assessment"), \
         patch("aria_service.intel.cost_tracker.feature"):

        from aria_service.metacognitive.engine import self_assess_output

        mock_llm = MagicMock()
        mock_llm.is_configured = True
        mock_result = MagicMock()
        mock_result.text = '{"scores": {"overall": 8}, "skill_gaps_revealed": []}'
        mock_llm.complete = AsyncMock(return_value=mock_result)

        result = await self_assess_output(
            query="test query",
            aria_output="x" * 500 + " test output that is long enough to assess the quality of the analysis and methodology used in this intelligence assessment",
            domain="intelligence_analysis",
            llm=mock_llm,
            session_id="test-session",
        )

        assert result.get("ok"), f"Assessment should succeed: {result}"
        assert mock_ws.called, "wire_success was not called on assessment"


@pytest.mark.asyncio
async def test_metacog_daily_check_wires_success():
    """run_daily_check calls wire_success."""
    with patch("aria_service.metacognitive.engine.wire_success") as mock_ws, \
         patch("aria_service.metacognitive.engine.wire_failure") as mock_wf, \
         patch("aria_service.metacognitive.engine.get_assessment_stats",
               return_value={"total": 10}), \
         patch("aria_service.metacognitive.engine.get_recent_assessments",
               return_value=[{"score": 0.7}, {"score": 0.8}]):

        from aria_service.metacognitive.engine import run_daily_check
        result = await run_daily_check()
        assert mock_ws.called, "wire_success was not called on daily check"
        assert "check" in result


@pytest.mark.asyncio
async def test_metacog_weekly_review_wires_success():
    """run_weekly_review calls wire_success."""
    with patch("aria_service.metacognitive.engine.wire_success") as mock_ws, \
         patch("aria_service.metacognitive.engine.wire_failure") as mock_wf, \
         patch("aria_service.metacognitive.engine.get_assessment_stats",
               return_value={"total": 50}), \
         patch("aria_service.metacognitive.engine.get_recent_assessments",
               return_value=[{"score": 0.7, "category": "general"}]):

        from aria_service.metacognitive.engine import run_weekly_review
        result = await run_weekly_review()
        assert mock_ws.called, "wire_success was not called on weekly review"
        assert "review" in result


@pytest.mark.asyncio
async def test_metacog_daily_check_wires_failure_on_error():
    """run_daily_check calls wire_failure on exception."""
    with patch("aria_service.metacognitive.engine.wire_success") as mock_ws, \
         patch("aria_service.metacognitive.engine.wire_failure") as mock_wf, \
         patch("aria_service.metacognitive.engine.get_assessment_stats",
               side_effect=Exception("Redis down")):

        from aria_service.metacognitive.engine import run_daily_check
        result = await run_daily_check()
        assert mock_wf.called, "wire_failure was not called on error"
        assert "error" in result
