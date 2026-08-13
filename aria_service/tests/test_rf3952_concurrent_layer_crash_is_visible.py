"""R-F3952 / C-43 — a DD layer that CRASHED rendered as `[COMPLETED]` and empty.

The network and digital layers are the only two the orchestrator runs
concurrently, and the result of that gather was thrown away:

    dd_orchestrator.py:15666
        await asyncio.gather(_run_network_layer(), _run_digital_layer(),
                             return_exceptions=True)        # ← never inspected

Both wrappers catch only `asyncio.TimeoutError`. Any other exception — a
`TypeError` on a malformed registry payload, an `AttributeError` in a new
adapter — escaped the wrapper, was captured by `return_exceptions=True`, and
was dropped on the floor. The section then kept the `SectionMeta` default:

    dd_schema.py:184
        status: str = LayerStatus.OK.value                  # ← fails OPEN

and because the layer had already been appended to `report.layers_run`, the
skip-detector could not see it either. **A digital section that crashed was
indistinguishable from one that searched and found nothing** — in the header,
in the status, and in the gaps.

The asymmetry is what proves it was an oversight: identity, compliance,
verification and synthesis crashes all propagate and abort the DD loudly
(15541, 15585, 16378, 16389). Only these two concurrent layers were silenced.

Note the fix deliberately does NOT widen `except asyncio.TimeoutError`.
Swallowing the exception closer to the raise would keep the handling in two
places that can drift apart; letting it reach the one marker at the gather
means every non-timeout failure — including one in a layer added later — is
handled at a single decision point.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import dd_orchestrator as DD
from aria_service.intel.dd_schema import ARKDDReport, LayerStatus

from ._source_probe import function_code


# ── the default that made silence possible ───────────────────────────────────

def test_section_meta_still_defaults_to_ok_so_the_marker_is_load_bearing():
    """Pin the premise: an untouched section reads `ok`.

    If a future change makes the default honest, this test should be updated
    deliberately — not deleted. It exists so nobody removes the marker below
    on the belief that the default already protects them.
    """
    rep = ARKDDReport()
    assert rep.digital.meta.status == LayerStatus.OK.value
    assert rep.network.meta.status == LayerStatus.OK.value


# ── the marker itself ────────────────────────────────────────────────────────

def test_crash_flips_the_section_off_ok():
    rep = ARKDDReport()
    rep.layers_run.append("digital")
    boom = TypeError("'NoneType' object is not subscriptable")

    DD._mark_concurrent_layer_crashes(
        rep, [("network", rep.network), ("digital", rep.digital)], [None, boom],
    )

    assert rep.digital.meta.status == LayerStatus.ERROR.value
    assert "TypeError" in (rep.digital.meta.error or "")
    assert "not subscriptable" in (rep.digital.meta.error or "")
    # the untouched sibling must be left exactly as it was
    assert rep.network.meta.status == LayerStatus.OK.value
    assert rep.network.meta.error is None


def test_crash_is_disclosed_to_the_reader_not_only_to_the_status_field():
    """A status nobody renders is not a disclosure."""
    rep = ARKDDReport()
    DD._mark_concurrent_layer_crashes(
        rep, [("digital", rep.digital)], [RuntimeError("adapter exploded")],
    )
    gaps = " ".join(rep.digital.data_gaps).lower()
    assert "digital" in gaps
    assert "crash" in gaps or "did not complete" in gaps
    assert "not" in gaps, "the gap must say the absence of findings is not a clean result"


def test_a_timeout_already_marked_by_the_wrapper_is_not_overwritten():
    """The wrapper's own `timeout after Ns` message is more specific — keep it."""
    rep = ARKDDReport()
    rep.digital.meta.status = LayerStatus.ERROR.value
    rep.digital.meta.error = "timeout after 120s"

    DD._mark_concurrent_layer_crashes(
        rep, [("digital", rep.digital)], [asyncio.TimeoutError()],
    )
    assert rep.digital.meta.error == "timeout after 120s"


def test_a_skipped_layer_is_not_relabelled_as_a_crash():
    rep = ARKDDReport()
    rep.digital.meta.status = LayerStatus.SKIPPED.value
    DD._mark_concurrent_layer_crashes(rep, [("digital", rep.digital)], [None])
    assert rep.digital.meta.status == LayerStatus.SKIPPED.value


def test_clean_run_changes_nothing():
    rep = ARKDDReport()
    DD._mark_concurrent_layer_crashes(
        rep, [("network", rep.network), ("digital", rep.digital)], [None, None],
    )
    assert rep.network.meta.status == LayerStatus.OK.value
    assert rep.digital.meta.status == LayerStatus.OK.value
    assert rep.digital.data_gaps == []


def test_marker_never_raises_on_a_ragged_result_list():
    """It runs in the DD hot path; it must never become the crash itself."""
    rep = ARKDDReport()
    DD._mark_concurrent_layer_crashes(rep, [("digital", rep.digital)], [])
    DD._mark_concurrent_layer_crashes(rep, [], [ValueError("x")])
    DD._mark_concurrent_layer_crashes(rep, None, None)  # type: ignore[arg-type]
    assert rep.digital.meta.status == LayerStatus.OK.value


def test_baseexception_is_marked_too():
    """`return_exceptions=True` captures BaseException subclasses as well."""
    rep = ARKDDReport()
    DD._mark_concurrent_layer_crashes(
        rep, [("digital", rep.digital)], [KeyboardInterrupt()],
    )
    assert rep.digital.meta.status == LayerStatus.ERROR.value


# ── the mechanism, reproduced end to end ─────────────────────────────────────

@pytest.mark.asyncio
async def test_gather_shape_reproduces_the_symptom_and_the_cure():
    """Faithful reproduction of the orchestrator's concurrent-layer shape.

    Wrapper catches TimeoutError only, layer raises something else, gather
    collects it. Without the marker the section still reads `ok`.
    """
    rep = ARKDDReport()

    async def _run_network_layer():
        rep.layers_run.append("network")

    async def _run_digital_layer():
        rep.layers_run.append("digital")
        try:
            raise TypeError("malformed registry payload")
        except asyncio.TimeoutError:                      # the real narrow catch
            rep.digital.meta.status = LayerStatus.ERROR.value

    results = await asyncio.gather(
        _run_network_layer(), _run_digital_layer(), return_exceptions=True,
    )

    # This is exactly what production did before the fix, and why it was silent:
    assert isinstance(results[1], TypeError)
    assert rep.digital.meta.status == LayerStatus.OK.value
    assert "digital" in rep.layers_run, "so the skip-detector cannot see it either"

    DD._mark_concurrent_layer_crashes(
        rep, [("network", rep.network), ("digital", rep.digital)], results,
    )
    assert rep.digital.meta.status == LayerStatus.ERROR.value
    assert rep.digital.data_gaps, "the reader must be told"


# ── the call site, so the marker cannot be orphaned ──────────────────────────

def test_the_orchestrator_actually_consumes_the_gather_result():
    """A marker nobody calls is the defect with extra steps.

    AST-resolved by name (R-F3597/§16) rather than sliced by line number.
    """
    src = function_code(DD, "_orchestrate_dd_impl")
    assert "_mark_concurrent_layer_crashes(" in src, (
        "the concurrent-layer gather result is not passed to the crash marker — "
        "a crashed network/digital layer will render as [COMPLETED] again"
    )
    # and the result must be bound, not discarded
    assert "await asyncio.gather(_run_network_layer(), _run_digital_layer()" not in src.replace(
        "_layer_results = await asyncio.gather(_run_network_layer(), _run_digital_layer()", "",
    ), "the gather result is still being discarded"
