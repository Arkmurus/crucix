"""R-F987 — the semantic-index worker yields the encode lock to live chats.

A background index batch's model.encode contends the SAME process-wide
_encode_lock as the chat RAG query. When a chat is interactive-active (R-F860),
the worker defers its already-assembled batch in short steps so the query
encodes first. Items are held (never dropped) and the deferral is capped so
background indexing can't starve.
"""
from __future__ import annotations

import asyncio

import aria_service.intel._semantic_index_queue as q
import aria_service.intel.brain_hook as bh


def test_rf987_defers_while_interactive(monkeypatch):
    state = {"checks": 0}

    def fake_active():
        state["checks"] += 1
        return state["checks"] <= 2     # interactive for the first 2 checks

    monkeypatch.setattr(bh, "_interactive_active", fake_active)
    monkeypatch.setattr(q, "_INTERACTIVE_DEFER_STEP_S", 0.01)
    monkeypatch.setattr(q, "_INTERACTIVE_DEFER_MAX_S", 1.0)

    deferred = asyncio.run(q._yield_to_interactive())
    assert deferred > 0, "should defer while a chat is interactive"
    assert state["checks"] >= 3, "should re-check until interactive clears"


def test_rf987_no_defer_when_idle(monkeypatch):
    monkeypatch.setattr(bh, "_interactive_active", lambda: False)
    deferred = asyncio.run(q._yield_to_interactive())
    assert deferred == 0.0, "no chat in flight → process the batch immediately"


def test_rf987_defer_is_capped(monkeypatch):
    """A continuous chat stream must not starve indexing forever."""
    monkeypatch.setattr(bh, "_interactive_active", lambda: True)  # always interactive
    monkeypatch.setattr(q, "_INTERACTIVE_DEFER_STEP_S", 0.02)
    monkeypatch.setattr(q, "_INTERACTIVE_DEFER_MAX_S", 0.1)
    deferred = asyncio.run(q._yield_to_interactive())
    assert deferred <= 0.2, f"deferral must be capped near _INTERACTIVE_DEFER_MAX_S, got {deferred}"


def test_rf987_never_raises_if_brain_hook_unavailable(monkeypatch):
    """Best-effort: a failure resolving the interactive signal must not break
    indexing — _yield_to_interactive swallows and returns 0."""
    def boom():
        raise RuntimeError("brain_hook down")
    monkeypatch.setattr(bh, "_interactive_active", boom)
    deferred = asyncio.run(q._yield_to_interactive())
    assert deferred == 0.0
