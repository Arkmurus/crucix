"""R-F1886 (review Class B) — IDOR checks must fail CLOSED.

The R-F1865 ownership work had two fail-OPEN gaps the 4-step review caught:
  RV-02: /scratchpad/{trace_id} nested its ownership check inside
         `if _trace is not None`, so when the owning trace was deleted/expired
         the check was SKIPPED and any authenticated user got the sensitive
         Clause-22 reasoning.
  entity-graph: the ownership check sat in a try whose `except Exception: pass`
         swallowed a report-load error and then RETURNED the confidential graph
         (a store blip became an IDOR bypass).

Both now fail CLOSED: a user-scoped caller who cannot establish ownership is
denied. Admin/internal (user_id='') still resolves.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException


def _run(coro):
    return asyncio.run(coro)


def test_rf1886_scratchpad_fail_closed_when_trace_absent():
    from aria_service.routes import aria
    from aria_service.intel import redis_store as rs

    # A scratchpad exists, but its OWNING TRACE does NOT (deleted/expired).
    _run(rs.set_json("crucix:scratchpad:rf1886_orphan", {"steps": ["SECRET REASONING"]}))

    # user-scoped caller cannot establish ownership → DENY (fail closed)
    out = _run(aria.scratchpad_for_trace_ep("rf1886_orphan", user_id="alice"))
    assert out.get("ok") is False, "scratchpad returned to a user-scoped caller for an orphaned trace (fail-OPEN)"
    assert "scratchpad" not in out

    # admin / internal (user_id='') still resolves
    admin = _run(aria.scratchpad_for_trace_ep("rf1886_orphan", user_id=""))
    assert admin.get("ok") is True
    assert admin.get("scratchpad", {}).get("steps") == ["SECRET REASONING"]


def test_rf1886_scratchpad_still_allows_owner_and_denies_other(monkeypatch):
    from aria_service.routes import aria
    from aria_service.intel import redis_store as rs, trace_stream

    tid = "rf1886_owned"
    # create the owning trace with a stored user_id, + the scratchpad
    _run(trace_stream.start_trace(question="q", user_id="alice", source="chat"))
    # start_trace uses its own id; stamp a deterministic trace for this test
    async def _fake_get_trace(t):
        return {"id": tid, "user_id": "alice"} if t == tid else None
    monkeypatch.setattr(trace_stream, "get_trace", _fake_get_trace)
    _run(rs.set_json(f"crucix:scratchpad:{tid}", {"steps": ["S"]}))

    assert _run(aria.scratchpad_for_trace_ep(tid, user_id="alice")).get("ok") is True
    other = _run(aria.scratchpad_for_trace_ep(tid, user_id="mallory"))
    assert other.get("ok") is False and "scratchpad" not in other


def test_rf1886_entity_graph_fail_closed_on_report_error(monkeypatch):
    from aria_service.routes import aria
    from aria_service.intel import entity_graph, dd_orchestrator

    class _G:
        nodes = [1]
        edges = []

    async def _load(rid):
        return _G()

    async def _boom(rid):
        raise RuntimeError("report store unavailable")

    monkeypatch.setattr(entity_graph.ERGraph, "load", staticmethod(_load))
    monkeypatch.setattr(dd_orchestrator, "get_report", _boom)

    # user-scoped caller + ownership cannot be verified (report load errored)
    # → DENY (fail closed), NOT return the confidential graph. (Pre-fix the
    # `except: pass` swallowed the error and returned the graph.)
    with pytest.raises(HTTPException) as ei:
        _run(aria.entity_graph_ep("rf1886_run", user_id="alice"))
    assert ei.value.status_code == 404
