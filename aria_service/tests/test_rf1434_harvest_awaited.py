"""R-F1434 capability test — the autonomous coder's output-harvest path
must AWAIT the harvest coroutine.

Before the fix, coder_entrypoint built a nested shim whose async capture()
called the async `output_harvester.harvest` WITHOUT awaiting it. The
coroutine was never awaited (RuntimeWarning) and the harvest never ran, so
the coder's output-harvest success-path was silently dark (CLAUDE.md §21a).

This test drives the actual broken path (_HarvestShim.capture) and asserts
the harvest coroutine is invoked AND awaited.
"""
import warnings

import pytest

from aria_service.autonomous import coder_entrypoint


@pytest.mark.asyncio
async def test_harvest_shim_awaits_harvest(monkeypatch):
    calls: list[dict] = []

    async def _fake_harvest(*, user_msg, response, meta=None):
        # If this body runs, the coroutine was actually awaited (a non-awaited
        # coroutine never executes its body).
        calls.append({"user_msg": user_msg, "response": response, "meta": meta})
        return {"ok": True}

    # Patch the real coroutine the shim imports lazily.
    monkeypatch.setattr(
        "aria_service.learning.output_harvester.harvest", _fake_harvest
    )

    shim = coder_entrypoint._HarvestShim()

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)  # un-awaited coroutine -> error
        await shim.capture(
            {
                "instruction": "do X",
                "response": "did X",
                "persona": "engineer",
                "r_number": "R-F1434",
            }
        )

    assert len(calls) == 1, "harvest coroutine was not awaited/executed"
    assert calls[0]["user_msg"] == "do X"
    assert calls[0]["response"] == "did X"
    assert calls[0]["meta"]["source"] == "autonomous_coder"
    assert calls[0]["meta"]["r_number"] == "R-F1434"


@pytest.mark.asyncio
async def test_harvest_shim_never_raises(monkeypatch):
    """A failing harvest must be swallowed (fire-and-forget)."""

    async def _boom(*, user_msg, response, meta=None):
        raise ValueError("harvest blew up")

    monkeypatch.setattr(
        "aria_service.learning.output_harvester.harvest", _boom
    )

    shim = coder_entrypoint._HarvestShim()
    # Must not raise.
    await shim.capture({"instruction": "i", "response": "r"})
