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
import time

import pytest


@pytest.mark.asyncio
async def test_dd_report_markdown_render_does_not_starve_loop(monkeypatch):
    from aria_service.routes import aria as A
    from aria_service.intel import dd_orchestrator as _ddo

    async def _get_report(run_id):
        return {"identity": {"entity_name": "Acme"}, "run_id": run_id}

    monkeypatch.setattr(_ddo, "get_report", _get_report, raising=False)

    SLEEP = 0.3  # stands in for the cx-132 CPU-bound render

    class _SlowReport:
        def render_markdown(self, concise=False):
            time.sleep(SLEEP)  # BLOCKING sync work — exactly the wedge pattern
            return "# DD Report\nAcme body"

    monkeypatch.setattr(A, "_rebuild_report_from_dict", lambda report, schema: _SlowReport())

    ticks = {"n": 0}

    async def _heartbeat():
        # ticks every SLEEP/20 → ~20 ticks across the render window if the loop is free
        for _ in range(200):
            await asyncio.sleep(SLEEP / 20)
            ticks["n"] += 1

    hb = asyncio.create_task(_heartbeat())
    try:
        result = await A.dd_report_ep("run-123", format="markdown")
    finally:
        hb.cancel()

    # user-visible output still correct
    assert "DD Report" in result["markdown"]

    # The decisive assertion: if render ran inline, time.sleep(0.3) blocks the
    # loop and the heartbeat cannot tick (n≈0). Offloaded via to_thread, the
    # heartbeat keeps advancing through the render window.
    assert ticks["n"] >= 5, (
        f"event loop was starved during DD render (heartbeat ticks={ticks['n']}) "
        f"— render_markdown is NOT offloaded to a thread"
    )
