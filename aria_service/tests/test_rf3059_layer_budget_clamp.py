"""R-F3059 — the digital layer could not fit its own ops, so it always ERRORed.

LIVE, on every report this session (dd_7ac19aa7941d, dd_75bc5a5a7e7c, dd_ef351f140935):

    DIGITAL FOOTPRINT   ERROR   Layer error: timeout after 90s

R-F2977 introduced per-op bounds precisely so a slow op would degrade to a data_gap
and the LAYER WOULD COMPLETE. It sized them against the 180s digital budget
(sum ≈145s). But the digital layer only gets 180s when a website was supplied —
otherwise it is DEFAULT_LAYER_TIMEOUT_S = 90s, while the op bounds still sum to ~175s:

    multi-query 45 + web-search 30 + site-mine 30 + rag 15 + kb 15 + deep-research 40

So on the common no-website path the layer was GUARANTEED to be cancelled, and
R-F2977's whole mechanism could never take effect. The fix is not a bigger bound: it
is to clamp each op to the LAYER's remaining time, which makes the sum fit by
construction whatever the budget is.
"""
import asyncio
import time
from unittest.mock import patch

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import ARKDDReport


def _layer():
    return ARKDDReport().digital


def test_rf3059_op_bounds_really_do_exceed_a_no_website_budget():
    """The arithmetic that makes this a structural defect, not bad luck."""
    total = (ddo._OP_T_MULTIQUERY + ddo._OP_T_WEBSEARCH + ddo._OP_T_WEBSITE_MINE
             + ddo._OP_T_RAG + ddo._OP_T_KB + ddo._OP_T_DEEPRESEARCH)
    assert total > ddo.DEFAULT_LAYER_TIMEOUT_S, (
        f"per-op sum {total}s vs a {ddo.DEFAULT_LAYER_TIMEOUT_S}s layer budget")


def test_rf3059_op_timeout_is_clamped_to_the_layer_remainder():
    async def go():
        layer = _layer()
        ddo._LAYER_DEADLINE.set(time.monotonic() + 3.0)   # only 3s left

        async def slow():
            await asyncio.sleep(30)
            return "never"

        t0 = time.time()
        out = await ddo._bounded_dd_op(slow(), 40.0, layer, "deep research", default={})
        elapsed = time.time() - t0
        assert out == {}, "degrades to the default, not an exception"
        assert elapsed < 6, f"clamped to the layer remainder, took {elapsed:.1f}s"
        assert any("deep research" in g for g in layer.data_gaps)
    asyncio.run(go())


def test_rf3059_an_exhausted_budget_skips_without_starting_the_op():
    async def go():
        layer = _layer()
        ddo._LAYER_DEADLINE.set(time.monotonic() - 5.0)   # already past
        started = {"yes": False}

        async def op():
            started["yes"] = True
            return "ran"

        coro = op()
        out = await ddo._bounded_dd_op(coro, 40.0, layer, "web search", default=None)
        assert out is None and started["yes"] is False, "must not start a doomed op"
        gap = layer.data_gaps[-1]
        assert "SKIPPED" in gap and "unchecked, not as clean" in gap
        coro.close()
    asyncio.run(go())


def test_rf3059_without_a_deadline_behaviour_is_exactly_as_before():
    async def go():
        layer = _layer()
        ddo._LAYER_DEADLINE.set(None)

        async def quick():
            return "ok"

        assert await ddo._bounded_dd_op(quick(), 5.0, layer, "kb", default=None) == "ok"
        assert layer.data_gaps == []
    asyncio.run(go())


def test_rf3059_a_fast_op_is_untouched_by_the_clamp():
    async def go():
        layer = _layer()
        ddo._LAYER_DEADLINE.set(time.monotonic() + 60.0)

        async def quick():
            return "fast"

        assert await ddo._bounded_dd_op(quick(), 40.0, layer, "rag", default=None) == "fast"
        assert layer.data_gaps == []
    asyncio.run(go())


def test_rf3059_every_bounded_layer_publishes_its_deadline():
    import inspect
    src = inspect.getsource(ddo)
    assert src.count("_LAYER_DEADLINE.set(") >= 3, (
        "digital, compliance and network must each publish a deadline")
    # the digital wrapper is the one that was failing live
    i = src.index("_digital_budget = DEFAULT_LAYER_TIMEOUT_S")
    assert "_LAYER_DEADLINE.set(" in src[i:i + 900]
