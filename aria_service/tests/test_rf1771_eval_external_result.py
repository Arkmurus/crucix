"""R-F1771 — capability test for the external eval-result sink.

A self-running eval pod POSTs its judge-DD result to /eval/external-result; we
read it via GET. This removes the Windows RunPod-SCP transport dependency. Drives
the real endpoint functions (POST stores → GET retrieves) with a mocked state
store + Request, asserting the round-trip.
"""
from __future__ import annotations

import asyncio


class _FakeReq:
    def __init__(self, body):
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeRS:
    def __init__(self):
        self.store = {}

    async def set_json(self, key, value, *a, **kw):
        self.store[key] = value

    async def get_json(self, key):
        return self.store.get(key)


def _wire(monkeypatch):
    from aria_service.routes import aria as a
    rs = _FakeRS()
    monkeypatch.setattr(a, "rs", rs)
    return a, rs


def test_post_then_get_roundtrip(monkeypatch):
    a, rs = _wire(monkeypatch)
    body = {"model": "aria-llm-v0.7", "accuracy": 0.308, "leak_rate": 0.3,
            "n": 500, "label": "v0.7 rerun open-book", "source": "runpod_selfrun"}

    out = asyncio.run(a.eval_external_result_post_ep(_FakeReq(body)))
    assert out["ok"] is True
    assert out["stored"]["model"] == "aria-llm-v0.7"
    assert out["stored"]["accuracy"] == 0.308

    got = asyncio.run(a.eval_external_result_get_ep())
    assert got["present"] is True
    assert got["result"]["accuracy"] == 0.308
    assert got["result"]["n"] == 500
    assert got["result"]["model"] == "aria-llm-v0.7"
    assert "received_at" in got["result"]


def test_get_when_empty_is_honest():
    # No POST yet → present False, result None (don't fabricate a result).
    from aria_service.routes import aria as a
    import aria_service.routes.aria as _a
    import types
    # fresh fake store via monkeypatch-free direct swap
    _a.rs = _FakeRS()  # type: ignore
    got = asyncio.run(a.eval_external_result_get_ep())
    assert got["present"] is False and got["result"] is None


def test_post_tolerates_malformed_body(monkeypatch):
    a, rs = _wire(monkeypatch)
    # Body that isn't JSON → endpoint must not crash, stores an empty-ish record.
    out = asyncio.run(a.eval_external_result_post_ep(_FakeReq(ValueError("bad json"))))
    assert out["ok"] is True
    got = asyncio.run(a.eval_external_result_get_ep())
    assert got["present"] is True  # a record was stored (with None fields)
