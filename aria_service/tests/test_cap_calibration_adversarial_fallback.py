"""
Capability test: R-F1165 — calibration_review adversarial fallback.
Tests that run_calibration_review uses the last non-degraded adversarial
score when the latest run is degraded.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_calibration_falls_back_to_non_degraded():
    """When the latest adversarial run is degraded, calibration must fall
    back to the most recent non-degraded run's score."""
    from aria_service.intel.calibration_review import run_calibration_review

    # Mock the functions that calibration_review calls via lazy imports
    with patch("aria_service.intel.adversarial_challenge.stats",
               new=AsyncMock(return_value={
                   "last_run": {
                       "run_at": "2026-05-30T06:00:00+00:00",
                       "overall_score": 0.0,
                       "degraded": True,
                       "invalid_reason": "empty-response cluster",
                   },
                   "runs": [
                       {
                           "run_at": "2026-05-27T06:00:00+00:00",
                           "overall_score": 0.65,
                           "degraded": False,
                           "invalid": False,
                       },
                   ],
               })):
        with patch("aria_service.intel.student.get_mastery_report",
                   new=AsyncMock(return_value={"headline_mastery": 0.8, "topics": {}})):
            with patch("aria_service.intel.honesty_judge.get_honesty_stats",
                       new=AsyncMock(return_value={})):
                with patch("aria_service.intel.chat_audit_log.get_stats",
                           new=AsyncMock(return_value={"total_entries": 100})):
                    with patch("aria_service.intel.redis_store.llen",
                               new=AsyncMock(return_value=5)):
                        with patch("aria_service.intel.eval_runner.get_recent_runs",
                                   new=AsyncMock(return_value=[])):
                            with patch("aria_service.intel.brain_hook.absorb_silent",
                                       new=AsyncMock()):
                                result = await run_calibration_review()

    # The adversarial_accuracy should be 0.65 (the fallback), not 0.0 (degraded)
    signals = result.get("signals", {})
    adv_acc = signals.get("adversarial_accuracy")
    assert adv_acc is not None, f"Missing adversarial_accuracy in signals: {signals}"
    assert abs(adv_acc - 0.65) < 0.01, (
        f"Expected adversarial_accuracy ~0.65, got {adv_acc}"
    )
