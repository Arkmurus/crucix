"""R-F4213 / C-192: the heavy-graph barrier must ALWAYS open.

R-F4211 put seven boot workloads behind one `asyncio.Event` — the autonomous
engine, ARIA-Coder, the knowledge seed, the web-integrity agent, the defence
seed, the health precompute loop and the whole boot continuation. That is
ARIA's entire metabolism. The producer that sets the event had no `finally`
and the consumer waited on it unboundedly, so any escape above the single
`.set()` left every one of those workloads parked FOREVER while `/health`
kept reporting `operational` and HTTP kept serving normally.

The `.set()` sat below `float(_os.getenv("ARIA_HEAVY_WARMUP_TIMEOUT_S", ...))`,
so a malformed operator value ("20m", "1200s") was enough to do it — the same
"a capability certified by an absence" shape §1 records three times, except
here the absence silently disables self-improvement rather than passing a gate.
"""

import ast
import asyncio
import inspect
import pathlib
from types import SimpleNamespace

import pytest

from aria_service import main


def _main_tree() -> ast.Module:
    """Parse the CURRENT main.py (R-F3597: resolve by name, never by line number)."""
    path = inspect.getsourcefile(main)
    assert path, "cannot locate main.py source"
    return ast.parse(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))


def _func_node(name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(_main_tree()):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in main.py")


# ── the producer: a malformed cap must not be able to kill the barrier ────────

@pytest.mark.parametrize("raw", ["20m", "1200s", "", "   ", "not-a-number", "1e"])
def test_malformed_warmup_timeout_never_raises(monkeypatch, raw):
    """A bad operator value must degrade to the default, never raise.

    Before R-F4213 this was a bare float() ABOVE the only .set(), so ValueError
    here permanently darkened every gated workload.
    """
    monkeypatch.setenv("ARIA_HEAVY_WARMUP_TIMEOUT_S", raw)
    value = main._heavy_warmup_timeout_s()
    assert value >= 60.0
    assert value == main._HEAVY_WARMUP_TIMEOUT_DEFAULT_S


def test_valid_warmup_timeout_is_honoured_and_floored(monkeypatch):
    """The guard must still be able to FAIL (R-F3858) — a real value wins."""
    monkeypatch.setenv("ARIA_HEAVY_WARMUP_TIMEOUT_S", "900")
    assert main._heavy_warmup_timeout_s() == 900.0
    monkeypatch.setenv("ARIA_HEAVY_WARMUP_TIMEOUT_S", "5")
    assert main._heavy_warmup_timeout_s() == 60.0, "floor must still clamp"


def test_barrier_set_is_guaranteed_by_a_finally():
    """Pin the release in a finalbody so a future edit cannot re-orphan it."""
    node = _func_node("_warmup_heavy_graphs_guarded")
    awaited = [
        ast.unparse(sub.func)
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Name)
    ]
    assert "_warmup_heavy_graphs" in awaited, (
        "the guard must actually wrap the warmup — a finally around nothing "
        "opens the barrier immediately and silently deletes R-F4211's barrier."
    )
    in_finally = [
        sub.lineno
        for stmt in ast.walk(node)
        if isinstance(stmt, ast.Try)
        for leaf in stmt.finalbody
        for sub in ast.walk(leaf)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == "set"
        and "heavy_graph_ready" in ast.unparse(sub.func.value)
    ]
    assert in_finally, (
        "heavy_graph_ready.set() is not inside a finally: block. Any escape "
        "above it (CancelledError from the gather, a malformed env cap, "
        "_aria_role() raising) parks the autonomous engine, coder, knowledge "
        "seed, web-integrity agent and boot continuation FOREVER. Do not fix a "
        "failure here by deleting this test."
    )


# ── the consumer: waiting forever is never the right answer ──────────────────

@pytest.mark.asyncio
async def test_barrier_releases_even_if_the_producer_never_sets_it(monkeypatch, caplog):
    """A workload must run late-and-degraded rather than never at all."""
    monkeypatch.setattr(main, "_heavy_barrier_timeout_s", lambda: 0.05)
    app = SimpleNamespace(state=SimpleNamespace(heavy_graph_ready=asyncio.Event()))

    with caplog.at_level("WARNING"):
        await asyncio.wait_for(main._await_heavy_graph_ready(app), timeout=5.0)

    assert any("R-F4213" in r.message or "R-F4213" in r.getMessage()
               for r in caplog.records), "a silent release is an unwired failure branch (§21a)"


@pytest.mark.asyncio
async def test_barrier_still_blocks_until_hydration_on_the_happy_path():
    """The release must not become an early exit — R-F4211's contract survives."""
    ready = asyncio.Event()
    app = SimpleNamespace(state=SimpleNamespace(heavy_graph_ready=ready))

    waiter = asyncio.create_task(main._await_heavy_graph_ready(app))
    await asyncio.sleep(0.05)
    assert not waiter.done(), "heavy workload escaped before graph hydration"

    ready.set()
    await asyncio.wait_for(waiter, timeout=1.0)


def test_barrier_cap_exceeds_the_warmup_cap(monkeypatch):
    """Derived, not magic: raising the warmup cap must raise the barrier cap.

    A hardcoded barrier cap would silently start releasing early the moment an
    operator lengthened the warmup — the stale-hand-maintained-constant shape.
    """
    for raw in ("60", "600", "1200", "3600"):
        monkeypatch.setenv("ARIA_HEAVY_WARMUP_TIMEOUT_S", raw)
        assert main._heavy_barrier_timeout_s() > main._heavy_warmup_timeout_s()


def test_the_spawned_boot_task_is_the_guarded_wrapper():
    """A guard nothing calls is the §1 'certified by an absence' shape."""
    tree = _main_tree()
    spawned = [
        ast.unparse(sub)
        for sub in ast.walk(tree)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == "create_task"
        and "heavy_graph_warmup" in ast.unparse(sub)
    ]
    assert spawned, "the heavy_graph_warmup task is no longer spawned"
    for call in spawned:
        assert "_warmup_heavy_graphs_guarded(" in call, (
            "boot spawns the UNGUARDED warmup — the finally that guarantees the "
            f"barrier opens is bypassed: {call}"
        )
