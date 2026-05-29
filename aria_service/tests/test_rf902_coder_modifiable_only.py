"""R-F902 — the coder only attempts gaps in MODIFIABLE_FILES.

R-F996 made ALL project files modifiable, so the MODIFIABLE_FILES filter
is now a pass-through. These tests verify that the filter still works
correctly (all files pass) and that the coder doesn't skip cycles.

The original test (2026-05-26) checked that non-modifiable files were
skipped; that behaviour was removed by R-F996. These tests now verify
the R-F996 invariant: every project file is modifiable.
"""
from __future__ import annotations

import asyncio
import types

from aria_service.autonomous import self_coder
from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity
from aria_service.intel.self_improve import _ensure_modifiable_files


def _mk_coder(scan_gaps):
    attempted = []

    class _GapDet:
        async def scan(self):
            return scan_gaps
        async def mark_attempted(self, gap_id):
            pass

    c = self_coder.ARIACoder(
        redis_client=None,
        aria_service_url="http://localhost:8000",
        gap_detector=_GapDet(),
        llm=object(), validator=object(), codebase=object(),
        test_runner=object(), deployer=object(), r_counter=object(),
    )

    async def _fake_fix(gap):
        attempted.append(gap.module)
        return types.SimpleNamespace(success=False, r_number=None, gap_id=gap.gap_id,
                                     fix_id="x", failure_reason="test")
    c.fix_gap = _fake_fix
    return c, attempted


def _gap(module, sev):
    return Gap(gap_id=f"g_{module}", gap_type=GapType.MODULE_BUG, severity=sev,
               title=f"Error in {module}", description="x", module=module)


def test_all_gaps_are_modifiable():
    """R-F996: every project file is modifiable, so all gaps pass the filter."""
    asyncio.run(_ensure_modifiable_files())
    gaps = [
        _gap("aria_service/autonomous/self_coder.py", GapSeverity.CRITICAL),
        _gap("aria_service/intel/researcher.py", GapSeverity.HIGH),
        _gap("aria_service/autonomous/safety.py", GapSeverity.HIGH),
    ]
    c, attempted = _mk_coder(gaps)
    asyncio.run(c._one_cycle())
    # All 3 gaps should be attempted (all files are modifiable per R-F996)
    assert len(attempted) == 3, f"Expected 3 attempts, got {attempted}"


def test_skips_cycle_when_no_actionable_gaps():
    """LOW severity gaps are not actionable regardless of modifiability."""
    asyncio.run(_ensure_modifiable_files())
    gaps = [
        _gap("aria_service/autonomous/self_coder.py", GapSeverity.LOW),
        _gap("aria_service/autonomous/safety.py", GapSeverity.LOW),
    ]
    c, attempted = _mk_coder(gaps)
    asyncio.run(c._one_cycle())
    assert attempted == []   # LOW severity → not actionable → no budget burned
