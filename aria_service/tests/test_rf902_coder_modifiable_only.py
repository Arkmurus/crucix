"""R-F902 — the coder only attempts gaps in MODIFIABLE_FILES.

Live 2026-05-26: after R-F901 gave the coder its own budget it ran the pipeline
but STAGED=0 / DISCARDED=0. Root cause: _one_cycle picked the top gaps by
severity + auto_fixable only, and the top-severity error gaps were ALL in
NON-modifiable files (brain_hook.py=155 errors, fallback.py, safety.py) — which
stage_improvement rejects. So the coder burned its whole budget on un-stageable
gaps. R-F902: keep only gaps whose file is in self_improve.MODIFIABLE_FILES;
skip the cycle if none. (The "prioritise auto-deployable gaps" residual.)
"""
from __future__ import annotations

import asyncio
import types

from aria_service.autonomous import self_coder
from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity


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


def test_only_modifiable_gaps_attempted():
    # brain_hook is HIGHER severity but NON-modifiable; researcher is modifiable
    gaps = [
        _gap("aria_service/intel/brain_hook.py", GapSeverity.CRITICAL),   # non-modifiable
        _gap("aria_service/intel/researcher.py", GapSeverity.HIGH),       # modifiable
        _gap("aria_service/llm/fallback.py", GapSeverity.HIGH),           # non-modifiable
    ]
    c, attempted = _mk_coder(gaps)
    asyncio.run(c._one_cycle())
    assert attempted == ["aria_service/intel/researcher.py"], attempted   # only the modifiable one


def test_skips_cycle_when_no_modifiable_gaps():
    gaps = [
        _gap("aria_service/intel/brain_hook.py", GapSeverity.CRITICAL),
        _gap("aria_service/intel/safety.py", GapSeverity.HIGH),
    ]
    c, attempted = _mk_coder(gaps)
    asyncio.run(c._one_cycle())
    assert attempted == []   # nothing the coder can stage → no budget burned
