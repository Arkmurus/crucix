"""R-F2408 capability test — the hoisted §21a wiring is now REACHABLE and fires.

Before R-F2408, the R-F2118/R-F2119 "wire module active" block sat AFTER the
function's return in 56 intel modules -> dead code, success signal never
reached the brain. This test drives the real functions and asserts
wire_success -> brain_hook.record_signal(success=True) actually fires.
"""
import asyncio

import pytest

from aria_service.intel import engine_wiring
from aria_service.intel import brain_hook


@pytest.fixture()
def fired(monkeypatch):
    """Capture wire_success -> record_signal calls, run dispatch inline."""
    calls = []

    def fake_record_signal(module, success, summary):  # noqa: D401
        calls.append((module, success))

    monkeypatch.setattr(brain_hook, "record_signal", fake_record_signal, raising=False)
    monkeypatch.setattr(engine_wiring, "_dispatch_fire_and_forget", lambda factory: factory())
    return calls


def test_sync_module_active_fires_metrics(fired):
    from aria_service.intel import metrics
    metrics.generate_metrics()
    assert ("metrics", True) in fired


def test_sync_module_active_fires_rca(fired):
    from aria_service.intel import rca_screening
    rca_screening.summary()
    assert ("rca_screening", True) in fired


def test_async_module_active_fires_absorption_quarantine(fired):
    from aria_service.intel import absorption_quarantine
    asyncio.run(absorption_quarantine.stats())
    assert ("absorption_quarantine", True) in fired


def test_compound_branch_module_active_fires_link_investigator(fired):
    # link_investigator.get_tree: wire block was hoisted before the try/except
    # whose branches both return -> must fire even when redis is absent.
    from aria_service.intel import link_investigator
    asyncio.run(link_investigator.get_tree("rf2408-smoke"))
    assert ("link_investigator", True) in fired
