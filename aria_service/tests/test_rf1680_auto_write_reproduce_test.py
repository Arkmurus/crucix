"""R-F1680 — auto-write reproduce test when no existing test exists.

The reproduce-symptom gate (R-F1460) previously rejected fixes when no
existing test was found for the target module. R-F1680 adds auto-writing
of a reproduce test via LLM, with a critical guard: the test MUST fail on
unfixed code. If it passes, it's a gamed/trivial test and is rejected.

Capability assertions:
1. _auto_write_reproduce_test exists and is called when no test found + LLM set
2. A reproduce test that PASSES on unfixed code is REJECTED (no false gold)
3. A reproduce test that FAILS on unfixed code is ACCEPTED (symptom reproduced)
"""
from __future__ import annotations

from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parents[1] / "autonomous" / "gap_detector.py").read_text(encoding="utf-8")


def test_auto_write_reproduce_method_exists():
    """The _auto_write_reproduce_test method must exist on GapDetector."""
    assert "async def _auto_write_reproduce_test" in SRC


def test_reproduce_symptom_calls_auto_write_when_llm_set():
    """When no existing test is found AND _llm is set, reproduce_symptom
    must call _auto_write_reproduce_test instead of rejecting."""
    assert "return await self._auto_write_reproduce_test(gap, module)" in SRC


def test_auto_write_rejects_passing_test():
    """An auto-written reproduce test that PASSES on unfixed code must be
    rejected — it's a gamed/trivial test that doesn't reproduce anything."""
    assert "auto-written reproduce test PASSES on unfixed code" in SRC
    assert "test is not a valid reproduction (gamed)" in SRC


def test_auto_write_accepts_failing_test():
    """An auto-written reproduce test that FAILS on unfixed code must be
    accepted — symptom reproduced."""
    assert "symptom reproduced via auto-written test" in SRC


def test_auto_write_handles_empty_test_code():
    """If the LLM returns empty test code, the method must reject."""
    assert "LLM returned empty test code" in SRC


def test_auto_write_handles_llm_failure():
    """If the LLM call itself fails, the method must reject gracefully."""
    assert "LLM failed to write reproduce test" in SRC


def test_gap_detector_accepts_llm_param():
    """GapDetector.__init__ must accept an optional llm parameter."""
    assert "llm: Any | None = None" in SRC


def test_self_coder_passes_llm_to_gap_detector():
    """The coder must pass its LLM to GapDetector."""
    coder_src = (Path(__file__).resolve().parents[1] / "autonomous" / "self_coder.py").read_text(encoding="utf-8")
    assert "GapDetector(redis_client, llm=llm)" in coder_src


@pytest.mark.asyncio
async def test_auto_write_reproduce_rejects_gamed_test(monkeypatch):
    """Capability: a reproduce test that PASSES on unfixed code is REJECTED.

    This is the core anti-gaming guard. We mock the LLM to return a test
    that trivially passes (assert True), then verify _auto_write_reproduce_test
    returns (False, ...) — no false gold.
    """
    from aria_service.autonomous.gap_detector import GapDetector, Gap, GapSeverity

    class _MockLLM:
        async def _call(self, prompt, task):
            return {"test_code": "def test_reproduce_xxx(): assert True"}

    gap = Gap(
        gap_id="test_gamed_001",
        gap_type="module_bug",
        severity=GapSeverity.MEDIUM,
        title="Test gamed reproduce",
        description="A test that trivially passes should be rejected",
        module="aria_service/intel/test_module.py",
    )

    detector = GapDetector(None, llm=_MockLLM())
    ok, msg = await detector._auto_write_reproduce_test(gap, gap.module)
    assert not ok, f"Gamed test should be rejected, got: {msg}"
    assert "PASSES on unfixed code" in msg or "gamed" in msg


@pytest.mark.asyncio
async def test_auto_write_reproduce_handles_llm_error(monkeypatch):
    """Capability: if the LLM raises, the method returns (False, ...)."""
    from aria_service.autonomous.gap_detector import GapDetector, Gap, GapSeverity

    class _BrokenLLM:
        async def _call(self, prompt, task):
            raise RuntimeError("LLM unavailable")

    gap = Gap(
        gap_id="test_broken_llm_001",
        gap_type="module_bug",
        severity=GapSeverity.MEDIUM,
        title="Broken LLM",
        description="LLM failure should be handled gracefully",
        module="aria_service/intel/test_module.py",
    )

    detector = GapDetector(None, llm=_BrokenLLM())
    ok, msg = await detector._auto_write_reproduce_test(gap, gap.module)
    assert not ok
    assert "LLM failed" in msg


@pytest.mark.asyncio
async def test_auto_write_reproduce_handles_empty_code(monkeypatch):
    """Capability: if the LLM returns empty test code, reject."""
    from aria_service.autonomous.gap_detector import GapDetector, Gap, GapSeverity

    class _EmptyLLM:
        async def _call(self, prompt, task):
            return {"test_code": ""}

    gap = Gap(
        gap_id="test_empty_001",
        gap_type="module_bug",
        severity= GapSeverity.MEDIUM,
        title="Empty test code",
        description="Empty test code should be rejected",
        module="aria_service/intel/test_module.py",
    )

    detector = GapDetector(None, llm=_EmptyLLM())
    ok, msg = await detector._auto_write_reproduce_test(gap, gap.module)
    assert not ok
    assert "empty test code" in msg
