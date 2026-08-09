"""R-F1786 — DD report render runs off the event loop.

Probe finding (AST sweep 2026-06-23): dd_orchestrator runs ZERO to_thread over
8,919 lines yet calls sync CPU-heavy work on the loop — render_markdown (cx ~132,
303 LOC), build_footer (cx ~92). This is the same GIL-starvation class behind the
documented brain-freeze wedges (R-F1621 json.dump, R-F1747 encode-on-loop): a heavy
DD render freezes every concurrent request on the single-process loop.

Fix: the render/footer call sites now run via asyncio.to_thread.

Capability: drive the REAL dd_report_ep markdown path with a deliberately-slow
(CPU-bound) render_markdown, while a heartbeat coroutine ticks. If the render ran
inline on the loop the heartbeat would freeze (ticks ~0); offloaded via to_thread
the loop stays alive (ticks keep advancing). This asserts the user-visible outcome
— concurrent requests are not starved — not just that a helper was called.
"""
import asyncio
import threading   # R-F3449 — thread identity is the load-independent offload proof
import time

import pytest


@pytest.mark.asyncio
async def test_dd_report_markdown_render_does_not_starve_loop(monkeypatch):
    from aria_service.routes import aria as A
    from aria_service.intel import dd_orchestrator as _ddo

    async def _get_report(run_id):
        return {"identity": {"entity_name": "Acme"}, "run_id": run_id}

    monkeypatch.setattr(_ddo, "get_report", _get_report, raising=False)

    # R-F3801 — DECLARE the internal tier. R-F3628 flipped
    # `_AUTH_INTERNAL_DEFAULT` to False (fail-closed), so an unscoped call
    # (user_id="") from a context that never set the var is now DENIED. The
    # denial is a deliberate 404 ("report not found") so existence is not
    # leaked, which is correct — and is why this read as a missing fixture
    # rather than an auth decision.
    A._auth_is_internal_var.set(True)

    SLEEP = 0.3  # stands in for the cx-132 CPU-bound render

    # R-F3449 — record WHICH THREAD the render ran on. This is the property the test is
    # really about ("offloaded to a thread"), it is directly observable, and unlike a tick
    # count it does not depend on how loaded the machine is. See the assertions below.
    render_thread: dict = {}

    class _SlowReport:
        def render_markdown(self, concise=False):
            render_thread["ident"] = threading.get_ident()
            time.sleep(SLEEP)  # BLOCKING sync work — exactly the wedge pattern
            return "# DD Report\nAcme body"

    monkeypatch.setattr(A, "_rebuild_report_from_dict", lambda report, schema: _SlowReport())

    ticks = {"n": 0}

    async def _heartbeat():
        # ticks every SLEEP/20 → ~20 ticks across the render window if the loop is free
        for _ in range(200):
            await asyncio.sleep(SLEEP / 20)
            ticks["n"] += 1

    loop_thread = threading.get_ident()
    hb = asyncio.create_task(_heartbeat())
    try:
        result = await A.dd_report_ep("run-123", format="markdown")
    finally:
        hb.cancel()

    # user-visible output still correct
    assert "DD Report" in result["markdown"]

    # ── The decisive assertion (R-F3449) ──────────────────────────────────────
    # This USED TO BE `ticks["n"] >= 5`, and it was one of the 15 order-dependent
    # failures in the R-F3448 baseline: green standalone, red in-suite. It is not state
    # poisoning — it is a LOAD-SENSITIVE timing measurement. The render is a fixed
    # time.sleep(0.3) (wall-clock, indifferent to load) while the heartbeat depends on
    # scheduler responsiveness (highly sensitive to it), so under a full suite the tick
    # count collapses and the test reports loop starvation that never happened.
    #
    # Assert the PROPERTY instead: the render must not execute on the event-loop thread.
    # Directly observable, deterministic, load-independent — and strictly STRONGER, because
    # an inline render fails it immediately rather than only when the timing happens to be
    # measurable. That is the same "assert the property, not the proxy" correction applied
    # to several long-red guards earlier today.
    assert render_thread.get("ident") is not None, "render_markdown was never called"
    assert render_thread["ident"] != loop_thread, (
        "render_markdown ran ON THE EVENT-LOOP THREAD — it is NOT offloaded, so a real "
        "CPU-bound render would wedge the loop (the cx-132 wedge this test guards)"
    )

    # Kept as a SECONDARY signal only, with a threshold that cannot cry wolf under load:
    # >0 proves the loop drew breath at all during the render window. The thread-identity
    # assertion above is what actually holds the contract.
    assert ticks["n"] > 0, (
        f"the event loop made no progress whatsoever during the DD render "
        f"(heartbeat ticks={ticks['n']})"
    )
