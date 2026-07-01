"""R-F2259 — the reranker is enabled + bulletproofed: the ~60s cold model LOAD is
offloaded off the event loop (was synchronous → would freeze the single-process brain
on the first search) and pre-warmed at boot so no live search eats the cold load."""
from __future__ import annotations
import asyncio, inspect
from pathlib import Path

from aria_service.intel import reranker

_RR = (Path(__file__).resolve().parent.parent / "intel" / "reranker.py").read_text(encoding="utf-8")
_MAIN = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")


def test_prewarm_is_a_coroutine():
    assert inspect.iscoroutinefunction(reranker.prewarm)


def test_prewarm_is_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_RERANK_ENABLED", raising=False)
    assert reranker.is_enabled() is False
    assert asyncio.run(reranker.prewarm()) is False  # no model load when off


def test_model_load_is_offloaded_off_the_event_loop():
    # the cold ~60s load must NOT run synchronously in the async rerank path
    assert "await asyncio.to_thread(_get_model)" in _RR
    assert "model = _get_model()" not in _RR  # the old blocking call is gone


def test_boot_prewarms_the_reranker():
    assert "R-F2259" in _MAIN
    assert "reranker" in _MAIN and ".prewarm()" in _MAIN
