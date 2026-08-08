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

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


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
    """R-F3419 — assert the PROPERTY (same function), not the SPELLING.

    This test had been RED since the digital budget was refactored from a one-line
    `_digital_budget = DEFAULT_LAYER_TIMEOUT_S` to a multi-line `_digital_budget = (`.
    It located the wrapper with `src.index(<that exact string>)`, so the refactor made it
    raise ValueError: substring not found — permanently, on every run.

    A permanently-red guard protects nothing: it is read as background noise, and it
    actively hides real breakage. This one also cost a live diagnostic, halting a `-x`
    bisect before it reached an unrelated suite hang.

    The property being guarded is unchanged: whichever function computes the digital
    layer's budget must ALSO publish the layer deadline, or `_layer_budget_left()`
    returns the caller's default and nothing downstream is actually clamped. Asserted
    over the AST, so reformatting cannot break it and deleting the `.set()` cannot pass.
    """
    import ast
    import inspect

    src = module_source(ddo)
    assert src.count("_LAYER_DEADLINE.set(") >= 3, (
        "digital, compliance and network must each publish a deadline")

    tree = ast.parse(src)
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assigns = [
            n for n in ast.walk(node) if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name) and t.id == "_digital_budget"
        ]
        if not assigns:
            continue
        # The innermost wrapper only — the enclosing orchestrator also contains the
        # assignment via ast.walk, and would pass on a sibling layer's .set().
        if node.name != "_run_digital_layer":
            continue
        sets = [
            n for n in ast.walk(node) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "set"
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "_LAYER_DEADLINE"
        ]
        assert sets, (
            f"{node.name} computes _digital_budget but never calls _LAYER_DEADLINE.set() "
            f"— the digital layer would run unclamped and _layer_budget_left() would "
            f"hand every caller its own default"
        )
        checked += 1
    assert checked == 1, (
        "guard is blind — expected exactly one _run_digital_layer computing "
        f"_digital_budget, found {checked}. Re-anchor this test on the wrapper that now "
        f"owns the digital budget."
    )


# ── R-F3066 — the blocks that were still UNBOUNDED ─────────────────────────
def test_rf3066_layer_budget_left_reports_the_remainder():
    ddo._LAYER_DEADLINE.set(time.monotonic() + 30.0)
    left = ddo._layer_budget_left()
    assert 27.0 < left < 30.0
    ddo._LAYER_DEADLINE.set(time.monotonic() - 5.0)
    assert ddo._layer_budget_left() == 0.0


def test_rf3066_no_deadline_returns_the_callers_default():
    """Outside a bounded layer (direct calls, tests) a caller keeps its allowance."""
    ddo._LAYER_DEADLINE.set(None)
    assert ddo._layer_budget_left(default=90.0) == 90.0


def test_rf3066_rag_and_neural_blocks_are_bounded():
    """LIVE: ops WERE bounded (45s + 37s honoured) yet the layer still overran at
    180s — because these blocks ran free."""
    import inspect
    src = module_source(ddo)
    assert '_OP_T_RAG, report.digital, "RAG context"' in src
    assert '"neural associations"' in src


def test_rf3066_link_tree_budget_is_derived_not_flat():
    """THE deep-mode overrun: the link tree claimed a flat 90s inside a 180s layer
    that had already spent ~82s (multi-query 45 + deep research 37). 82+90 > 180, so
    a deep DD was arithmetically certain to blow the layer."""
    import inspect
    src = module_source(ddo)
    assert "wall_budget_s=min(90.0, _lt_budget)" in src, "must not claim a flat 90s"
    assert "_lt_budget = min(90.0, _layer_budget_left(default=90.0) * 0.6)" in src, (
        "a trailing op must take at most a FRACTION of the remainder, or it starves "
        "every block after it — which is how the 90s person path still overran")
    assert '"link-tree investigation"' in src, "and it must be bounded like every other op"


def test_rf3066_link_tree_skips_honestly_when_there_is_no_budget():
    import inspect
    src = module_source(ddo)
    i = src.index("_lt_budget = min(90.0, _layer_budget_left")
    window = src[i:i + 900]
    assert "if _lt_budget < 15.0:" in window
    assert "SKIPPED" in window and "unchecked, not as clean" in window


def test_rf3066_a_real_tail_is_reserved_for_the_layers_own_work():
    """With a 1s tail the last op could return at the wall and the layer still
    overran on its own post-op work (press processing, tiering, wiring)."""
    assert ddo._LAYER_TAIL_S >= 5.0
    import inspect
    assert "_dl - time.monotonic() - _LAYER_TAIL_S" in function_source(ddo, "_bounded_dd_op")


def test_rf3066_the_last_op_cannot_consume_the_whole_remainder():
    async def go():
        layer = _layer()
        ddo._LAYER_DEADLINE.set(time.monotonic() + 30.0)
        # an op asking for far more than remains is clamped BELOW the remainder
        import asyncio as _a
        async def slow():
            await _a.sleep(60)
        t0 = time.time()
        await ddo._bounded_dd_op(slow(), 90.0, layer, "greedy op", default=None)
        assert time.time() - t0 < 30.0 - ddo._LAYER_TAIL_S + 3
    asyncio.run(go())
