"""R-F986 — neural persist is debounced for high-frequency callers.

learn_from_text (the absorb storm) and recall (the chat retrieval path) re-gzip
the full neuron set on every call via _persist() — a GIL-bound CPU burst that
slowed chats during the autonomous absorb storm (143s traces / R-F704 wedges).
They now go through _maybe_persist(), which skips when the last persist was <
interval ago; the next call after the interval flushes the current (full)
snapshot. _persist() itself is unchanged (infrequent callers + the R-F371
infinite-memory guard still write every time). No data is dropped.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.neural_memory as nm

# R-F3773/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def test_rf986_maybe_persist_is_debounced(monkeypatch):
    persists = {"n": 0}

    async def fake_persist():
        persists["n"] += 1

    monkeypatch.setattr(nm, "_persist", fake_persist)
    monkeypatch.setattr(nm, "_NEURONS_MIN_WRITE_INTERVAL_S", 100.0)
    nm._last_neurons_write = 0.0

    async def burst():
        await nm._maybe_persist()   # first → persists
        await nm._maybe_persist()   # within interval → skipped
        await nm._maybe_persist()   # within interval → skipped
    asyncio.run(burst())
    assert persists["n"] == 1, f"expected 1 persist across a 3-call burst, got {persists['n']}"

    # Simulate the interval elapsing → the next call flushes again.
    nm._last_neurons_write = 0.0
    asyncio.run(nm._maybe_persist())
    assert persists["n"] == 2


def test_rf986_first_call_after_boot_persists(monkeypatch):
    persists = {"n": 0}

    async def fake_persist():
        persists["n"] += 1

    monkeypatch.setattr(nm, "_persist", fake_persist)
    monkeypatch.setattr(nm, "_NEURONS_MIN_WRITE_INTERVAL_S", 9999.0)
    nm._last_neurons_write = 0.0

    asyncio.run(nm._maybe_persist())
    assert persists["n"] == 1, "_last_neurons_write=0.0 → first call must persist"


def test_rf986_persist_itself_still_always_writes(monkeypatch):
    """Guard: the debounce lives in _maybe_persist, NOT _persist — direct
    _persist() callers (learn_explicit / consolidate / migration guard) and the
    R-F371 protection are unaffected."""
    import inspect
    src = function_source(nm, "_persist")
    assert "_NEURONS_MIN_WRITE_INTERVAL_S" not in src, (
        "_persist must not contain the debounce — it would break the R-F371 "
        "guard tests and the immediate-durability contract."
    )
