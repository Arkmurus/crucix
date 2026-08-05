"""R-F3737 — CAPABILITY: the Phase 0.3 usage instrument must report its own failure.

`cure_usage.flush()` already COUNTED its store-write failures into
`_flush_failures` and then told nobody — per-field and meta-write failures were
silent, and the outer handler reached `log.warning`, which §21a defines as DARK.

This matters more than a usual counter. This module IS the Phase 0.3 runtime
proof: the evidence deciding whether 109 dead-candidate modules may be deleted.
A flush that silently stops writing does not look broken — it looks like "that
route was never called", which is precisely the reading that makes a LIVE module
deletable. Its silence is indistinguishable from a real result (Invariant 8:
unknown state must never be converted into a measurement).

Run: python -m pytest aria_service/tests/test_rf3737_cure_usage_wired.py -v
"""
from __future__ import annotations

import asyncio

import pytest



class _Store:
    """R-F3737 — swap the MODULE in sys.modules, matching test_rf3730's idiom.

    `flush()` does `from aria_service.intel import state_store` per call, so the
    module object is re-resolved every time. test_rf3730 replaces it wholesale;
    patching attributes on the real module instead left residue that made five
    of ITS tests fail when this file ran first. One module, one idiom.
    """

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.data: dict = {}

    async def hincrby(self, key, field, n):
        if self.fail:
            raise RuntimeError("store down")
        self.data[field] = self.data.get(field, 0) + n
        return self.data[field]

    async def set_json(self, *a, **k):
        return None


def _use_store(monkeypatch, store):
    monkeypatch.setitem(
        __import__("sys").modules, "aria_service.intel.state_store", store)


@pytest.fixture()
def sink(monkeypatch):
    seen: dict[str, list] = {"ok": [], "fail": []}
    from aria_service.intel import engine_wiring as ew
    monkeypatch.setattr(ew, "wire_success", lambda **kw: seen["ok"].append(kw))
    monkeypatch.setattr(ew, "wire_failure", lambda **kw: seen["fail"].append(kw))
    return seen


@pytest.fixture(autouse=True)
def _clean():
    from aria_service.intel import cure_usage
    cure_usage._reset_for_tests()
    yield
    cure_usage._reset_for_tests()


def test_a_failing_store_write_reaches_the_brain(monkeypatch, sink):
    """THE HEADLINE: a lost interval must not look like 'never called'."""
    from aria_service.intel import cure_usage

    _use_store(monkeypatch, _Store(fail=True))

    cure_usage.record_route("/api/aria/dd/{id}", "GET")
    asyncio.run(cure_usage.flush())

    assert sink["fail"], "a failed usage flush must raise a brain signal"
    d = sink["fail"][0]
    assert d["module"] == "cure_usage"
    assert "INCOMPLETE" in d["detail"], (
        "the signal must say the EVIDENCE is incomplete — that is what a reader "
        "of the Phase 0.3 census needs to know"
    )
    assert not sink["ok"]


def test_a_healthy_flush_reports_success(monkeypatch, sink):
    """§21a needs both branches."""
    from aria_service.intel import cure_usage

    _use_store(monkeypatch, _Store())

    cure_usage.record_route("/api/aria/dd/{id}", "GET")
    written = asyncio.run(cure_usage.flush())

    assert written == 1
    assert sink["ok"], "a successful flush must also reach the brain"
    assert not sink["fail"]


def test_an_empty_buffer_is_not_a_failure(monkeypatch, sink):
    """Nothing to write is not degradation — it must not cry wolf."""
    from aria_service.intel import cure_usage

    assert asyncio.run(cure_usage.flush()) == 0
    assert not sink["fail"] and not sink["ok"]


def test_the_counts_are_still_preserved_on_failure(monkeypatch, sink):
    """R-F3730's own guarantee must survive the wiring: a failed write re-buffers."""
    from aria_service.intel import cure_usage

    _use_store(monkeypatch, _Store(fail=True))

    cure_usage.record_route("/api/aria/dd/{id}", "GET")
    asyncio.run(cure_usage.flush())
    assert cure_usage._buffer, "a failed flush must put the counts back, not drop them"


def test_broken_wiring_cannot_break_the_instrument(monkeypatch):
    """The measurement outranks its own telemetry."""
    from aria_service.intel import cure_usage, engine_wiring as ew

    def _explode(**kw):
        raise RuntimeError("brain unreachable")

    monkeypatch.setattr(ew, "wire_success", _explode)
    monkeypatch.setattr(ew, "wire_failure", _explode)
    _use_store(monkeypatch, _Store())

    cure_usage.record_route("/api/aria/dd/{id}", "GET")
    assert asyncio.run(cure_usage.flush()) == 1, (
        "a wiring failure must never stop the usage counter from recording"
    )
