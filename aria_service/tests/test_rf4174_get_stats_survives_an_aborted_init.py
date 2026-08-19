"""R-F4174 - `get_stats()` 500s when neurons exist but `born` was never set.

**A LIVE REGRESSION I INTRODUCED WITH R-F4173 (C-185), caught by probing my own
deploy rather than by a test.**

Measured on aria-intel, build `cc4a05ec`:

    GET /api/aria/neural/stats -> HTTP 500

Minutes earlier on the same build the endpoint returned 200
(`loaded=True, load_complete=False, neurons=0, edges=0`), and on the previous
build it returned 200 with the full graph. What changed in between is that
`_neurons` became non-empty.

**The chain, and R-F4173 is the link that made it reachable.**

`_meta` is declared at module scope as ``{..., "born": None}`` — the key is
PRESENT and its value is None. `init()` repairs that (`if not _meta.get("born")`)
but the repair sits *inside* the try, after the store reads.

R-F4173 made those reads STRICT. A store failure now RAISES and aborts `init()`
**before** the repair line, where previously the non-strict reader returned None,
the loader carried on with an empty graph, and `born` got set anyway. So the
aborted-init path leaves `born` as None for the first time.

Then `get_stats()`:

    born = _meta.get("born", time.time())     # -> None, the default never fires
    ...
    "age_days": round((time.time() - born) / 86400, 1)   # TypeError -> 500

The `.get(key, default)` default cannot fire for a key that exists holding None.
That is the trap: it reads as "defaulted" and is not.

`get_stats()` must not depend on `init()` having completed — it is the surface
that REPORTS a failed init, so it is exactly the function that has to work when
one has happened.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import neural_memory as nm


def _run(coro):
    return asyncio.run(coro)


def _neuron(i=1):
    return {"id": f"n{i}", "label": f"e{i}", "category": "c",
            "activation": 1.0, "confidence": 1.0, "evidence_count": 1}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(nm, "_neurons", {}, raising=False)
    monkeypatch.setattr(nm, "_edges", {}, raising=False)
    monkeypatch.setattr(nm, "_meta", {}, raising=False)
    monkeypatch.setattr(nm, "_loaded", False, raising=False)
    monkeypatch.setattr(nm, "_load_complete", False, raising=False)


def test_get_stats_survives_an_aborted_init(monkeypatch):
    """THE CAPABILITY TEST: the live 500. An aborted init leaves born unset;
    neurons then arrive through runtime absorb; the stats surface must still
    answer."""
    monkeypatch.setattr(nm, "_meta", {"total_neurons": 0, "total_edges": 0,
                                      "total_activations": 0, "born": None})
    monkeypatch.setattr(nm, "_neurons", {"n1": _neuron()})
    monkeypatch.setattr(nm, "_edges", {"n1": {"n2": 1.0}})

    stats = _run(nm.get_stats())        # raised TypeError -> HTTP 500

    assert stats["total_neurons"] == 1
    assert isinstance(stats["age_days"], (int, float))
    assert stats["age_days"] >= 0


def test_the_empty_branch_also_survives_a_null_born(monkeypatch):
    """The other return branch reports `born` straight through. It must not
    start raising either."""
    monkeypatch.setattr(nm, "_meta", {"born": None})
    stats = _run(nm.get_stats())
    assert stats["total_neurons"] == 0


def test_a_real_born_is_still_reported_unchanged(monkeypatch):
    """REGRESSION GUARD — the fix must not overwrite a genuine birth date, which
    would silently reset the graph's recorded age."""
    born = 1_700_000_000.0
    monkeypatch.setattr(nm, "_meta", {"born": born, "total_activations": 0})
    monkeypatch.setattr(nm, "_neurons", {"n1": _neuron()})

    stats = _run(nm.get_stats())
    assert stats["born"] == born
    assert stats["age_days"] > 0


def test_a_missing_born_KEY_is_still_handled(monkeypatch):
    """The case the original `.get(key, default)` was written for. It must keep
    working — the defect was only ever the key existing with a None value."""
    monkeypatch.setattr(nm, "_meta", {"total_activations": 0})
    monkeypatch.setattr(nm, "_neurons", {"n1": _neuron()})
    assert isinstance(_run(nm.get_stats())["age_days"], (int, float))
