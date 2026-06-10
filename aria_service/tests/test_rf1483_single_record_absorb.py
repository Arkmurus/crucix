"""R-F1483 capability test — absorb records per-module stats EXACTLY ONCE.

The bug: brain_hook.absorb recorded the per-module signal TWICE — an unconditional
`_record_signal(module, success=True)` immediately, PLUS the background tier's real
outcome (`success=_core_ok`, brain_hook_bg.py:170). That double-count floored every
module's success_rate at 50% and measured "absorb was called" + brain persistence,
not agent reliability (agent_registry/signal_generator/pending_actions all showed
artificially low rates as a result).

The fix: record once — shed branch -> success=True (fact is WAL-preserved); normal
branch -> the bg tier's real-outcome record stands alone.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import aria_service.intel.brain_hook as bh


@pytest.mark.asyncio
async def test_normal_path_has_no_synchronous_immediate_record():
    """In the normal (non-shed) path, absorb must NOT synchronously record a stat.

    With the bg tier mocked to a no-op, the ONLY _record_signal that could fire is
    the old unconditional immediate success=True. After R-F1483 that is gone -> 0.
    """
    bh._breaker_state["open"] = False
    bh._pending_absorb = 0  # below cap -> normal (bg) path is taken
    with patch.object(bh, "_record_signal", new=AsyncMock()) as rec, \
         patch("aria_service.intel.brain_hook_bg.absorb_tiers_bg", new=AsyncMock()):
        await bh.absorb(module="cap_rf1483_normal", summary="hello", success=True)
        await asyncio.sleep(0.05)  # let the (no-op) bg task schedule
        assert rec.call_count == 0, (
            f"normal path must make ZERO synchronous stat records (the bg tier records "
            f"the real outcome once); got {rec.call_count} — the unconditional "
            f"success=True double-count is back"
        )
    bh._pending_absorb = 0


@pytest.mark.asyncio
async def test_shed_path_records_exactly_once_success_true():
    """When the bg tier is shed (over cap), absorb records ONCE with success=True.

    The fact is durably WAL-queued, so the durable outcome succeeded — and there is
    no bg tier to record it, so the shed branch must record exactly one signal.
    """
    bh._breaker_state["open"] = False
    bh._pending_absorb = bh._MAX_PENDING_ABSORB + 5  # force over-cap shed (non-interactive)
    with patch.object(bh, "_record_signal", new=AsyncMock()) as rec:
        # no user_id -> not interactive -> eligible to shed
        await bh.absorb(module="cap_rf1483_shed", summary="hello", success=True)
        assert rec.call_count == 1, (
            f"shed path must record exactly once, got {rec.call_count}"
        )
        call = rec.call_args
        recorded_success = call.kwargs.get("success")
        if recorded_success is None and len(call.args) > 1:
            recorded_success = call.args[1]
        assert recorded_success is True, f"shed record must be success=True, got {recorded_success}"
    bh._pending_absorb = 0


# NOTE: the breaker-open invariant (R-F1480: breaker-open records NO module fail) is
# covered by test_rf1480_brain_breaker_misattribution.py, which forces the breaker
# open correctly. Not duplicated here — this file is scoped to the R-F1483 single-record fix.
