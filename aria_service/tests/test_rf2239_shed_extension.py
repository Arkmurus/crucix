"""R-F2239 — the load governor now sheds the HEAVY autonomous loops, not just
the engine tick.

The incident-responder found R-F2185's load_governor was consulted in exactly one
place (engine.py:652), so under state_store/loop pressure only the engine tick was
shed while self_improve + research (heavy LLM+absorb loops) kept contending with
serving. R-F2239 extends the shed gate to those loops (mirrors engine.py). Source-
contract locks so a future edit can't silently drop the gate; the shed BEHAVIOUR
itself is covered by test_rf2185_load_governor.
"""
from __future__ import annotations

from pathlib import Path

_MAIN = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")


def test_should_shed_extended_beyond_the_engine():
    # main.py had no should_shed before R-F2239 (the engine tick lives in engine.py).
    # Now the two heavy loops each consult it.
    assert _MAIN.count("_lg.should_shed()") >= 2, "expected shed gates in research + self_improve"
    assert _MAIN.count("R-F2239") >= 2


def test_research_loop_sheds():
    assert "[Research] load-shed" in _MAIN
    # the gate is fail-safe (governor must never break the loop)
    assert "from .intel import load_governor as _lg" in _MAIN


def test_self_improve_loop_sheds():
    assert "[Self-Improve] load-shed" in _MAIN


def test_load_governor_should_shed_is_importable():
    from aria_service.intel.load_governor import should_shed
    assert callable(should_shed)
    # fail-safe: never raises even if the state_store probe fails
    _ = should_shed()
