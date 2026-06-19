"""R-F1730 — captcha_solver is §21a brain-wired (success AND failure).

The ARIA 360 probe flagged captcha_solver as a P0 dark path: it solved/failed
CAPTCHAs with zero brain signal, so ARIA could not feel her onboarding limb (§25)
and the government-portal CAPTCHAs 2captcha can't read (SAM.gov/FPDS/DDTC) died
silently instead of becoming actionable gaps. This drives the real chokepoint
(`_solve_with_failover`) and asserts BOTH branches reach the brain.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from aria_service.intel import captcha_solver as cs


class _OkProvider:
    name = "fakeprovider"
    is_configured = True

    async def solve_recaptcha_v2(self, site_key, page_url, **kw):
        return "TOKEN-abc123"


class _NoneProvider:
    name = "deadprovider"
    is_configured = True

    async def solve_recaptcha_v2(self, site_key, page_url, **kw):
        return None


def _solver_with(providers):
    s = cs.CaptchaSolver.__new__(cs.CaptchaSolver)
    s.providers = providers
    return s


@pytest.mark.asyncio
async def test_success_fires_wire_success():
    solver = _solver_with([_OkProvider()])
    with patch.object(cs, "_wire_captcha_success") as ws, \
         patch.object(cs, "_wire_captcha_failure") as wf:
        tok = await solver.solve_recaptcha_v2("6Lfb-xyz", "https://newsapi.org/register")
    assert tok == "TOKEN-abc123"
    assert ws.called, "a solved CAPTCHA must wire_success → brain"
    # args: label, host, provider, elapsed
    args = ws.call_args.args
    assert "newsapi.org" in args[1]
    assert args[2] == "fakeprovider"
    assert not wf.called


@pytest.mark.asyncio
async def test_all_providers_none_fires_failure_and_records_gap():
    solver = _solver_with([_NoneProvider()])
    with patch("aria_service.intel.engine_wiring.wire_failure") as wf, \
         patch("aria_service.intel.capability_gaps.record_gap") as rg:
        # record_gap is async — make the patch awaitable
        async def _rg(*a, **k):
            return {"ok": True}
        rg.side_effect = _rg
        tok = await solver.solve_recaptcha_v2("6Lfb-xyz", "https://sam.gov/register")
    assert tok is None
    assert wf.called, "all-providers-failed must wire_failure → brain"
    assert rg.called, "all-providers-failed must record a capability gap (§21e)"
    # the gap type must be a VALID_GAP_TYPES member
    gap_type = rg.call_args.kwargs.get("gap_type") or (rg.call_args.args[0] if rg.call_args.args else "")
    from aria_service.intel.capability_gaps import VALID_GAP_TYPES
    assert gap_type in VALID_GAP_TYPES


@pytest.mark.asyncio
async def test_no_providers_configured_still_wires_failure():
    solver = _solver_with([])
    with patch.object(cs, "_wire_captcha_failure") as wf:
        # _wire_captcha_failure is async — give the mock an awaitable
        async def _wf(*a, **k):
            return None
        wf.side_effect = _wf
        tok = await solver.solve_recaptcha_v2("6Lfb-xyz", "https://fpds.gov/register")
    assert tok is None
    assert wf.called, "even with no providers, the failure must reach the brain"
