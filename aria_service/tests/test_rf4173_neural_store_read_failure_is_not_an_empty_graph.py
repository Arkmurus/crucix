"""R-F4173 / C-185 - a boot-time store read TIMEOUT was adopted as "this graph
has no edges", and reported as a -100% state regression.

**Measured live 2026-08-19**, on the machine, minutes apart:

    machine restarted (R-F4170 build) : 12:43:59Z
    [R-F251] STATE REGRESSION DETECTED - counters dropped >5% since previous
             boot: neural_edges: 159198 -> 0 (-100%)      at 12:46:25Z
    /api/aria/neural/stats (same process, ~20 min later):
             loaded: True, neurons: 17743, edges: 159254

Nothing was lost. `state_backend_read_timeouts` was live in `degraded_reasons`
across exactly that window (10 timeouts, 10 distinct keys).

**The mechanism, and it is the R-F1 None-on-error contract meeting a loader
that has no way to say "I could not read".** `neural_memory.init()` reads the
edge store through the NON-strict `rs.get`, whose documented contract collapses
a store-layer failure and a genuinely absent key into the same value: `None`.
So the loader took its "nothing on disk -> fresh" branch, set `_edges = {}`,
and carried on. No exception was raised, so nothing downstream could tell.

Two guards were in place and neither could fire:

* **R-F2951** writes the string `"loading"` for `neural_edges` when
  `nm_stats["loaded"]` is False. `_loaded` was True - `init()` sets it True on
  its success path, and (separately) sets it True in its `except` branch too,
  so the flag means "we stopped trying", not "the data is here".
* **R-F4170 (C-184)** skips the boot diff when the readiness wait times out.
  This boot left that wait EARLY, at 2m26s, because `neural_ready` means "the
  warmup task did not raise" (`main.py:1323`) - which it did not.

**The fix is the R-F2664 pattern**, which CLAUDE.md section 1 records for
`_load_regional_mastery`: read STRICTLY, so a store failure raises instead of
returning a value, and never adopt an unreadable store as an empty one. A
failed read now leaves the graph un-loaded and SAYS SO through a new
`load_complete` flag, which the boot snapshot folds into `stores_ready`.

**What must NOT change:** a genuinely absent key is still a clean fresh start.
A first boot on an empty volume has to work, and turning "no data yet" into an
error would be the opposite defect.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import neural_memory as nm
from aria_service.intel import redis_store as real_rs


def _run(coro):
    return asyncio.run(coro)


class _Rs:
    """Stand-in for the redis_store module.

    `behaviour(key)` returns either the stored raw value, or the sentinel
    RAISE to make that key's read fail at the STORE layer.
    """

    RAISE = object()
    StoreReadError = real_rs.StoreReadError

    def __init__(self, behaviour):
        self._b = behaviour
        self.reads: list[str] = []

    def _value(self, key, strict):
        self.reads.append(key)
        v = self._b(key)
        if v is self.RAISE:
            if strict:
                raise real_rs.StoreReadError(f"stub store failure on {key}")
            # THE DEFECT: the non-strict contract turns a failure into "absent".
            return None
        return v

    async def get(self, key):
        return self._value(key, strict=False)

    async def get_strict(self, key):
        return self._value(key, strict=True)

    async def get_json(self, key):
        import json
        raw = self._value(key, strict=False)
        return json.loads(raw) if isinstance(raw, str) else raw

    async def get_json_strict(self, key):
        import json
        raw = self._value(key, strict=True)
        return json.loads(raw) if isinstance(raw, str) else raw


def _neurons_blob(n=3):
    return {f"n{i}": {"id": f"n{i}", "label": f"e{i}", "category": "c",
                      "activation": 1.0, "confidence": 1.0,
                      "evidence_count": 1} for i in range(n)}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """The module keeps its graph in module-level globals; every test must start
    from a known state or it inherits the previous one."""
    monkeypatch.setattr(nm, "_neurons", {}, raising=False)
    monkeypatch.setattr(nm, "_edges", {}, raising=False)
    monkeypatch.setattr(nm, "_meta", {}, raising=False)
    monkeypatch.setattr(nm, "_loaded", False, raising=False)
    monkeypatch.setattr(nm, "_offload_sweep_all", lambda *a, **k: None,
                        raising=False)


def _install(monkeypatch, behaviour) -> _Rs:
    stub = _Rs(behaviour)
    monkeypatch.setattr(nm, "rs", stub)
    return stub


# ── THE CAPABILITY TEST: the live incident ──────────────────────────────────

def test_an_unreadable_edge_store_is_not_adopted_as_an_empty_graph(monkeypatch):
    """The exact live shape: neurons read fine, the edge store read FAILS.

    Before R-F4173 this produced `_edges = {}` with `_loaded = True`, which the
    boot diff read as neural_edges -100%.
    """
    import json

    def behaviour(key):
        if key == nm.NEURONS_KEY:
            return json.dumps(_neurons_blob())
        if key in (nm.EDGES_KEY, nm.EDGES_SHARD_META_KEY):
            return _Rs.RAISE
        return None

    _install(monkeypatch, behaviour)
    _run(nm.init())

    stats = _run(nm.get_stats())
    assert stats.get("load_complete") is False, (
        "a store read FAILURE was reported as a completed load — the boot diff "
        "will read the empty edge set as a -100% regression"
    )


def test_a_failed_read_does_not_claim_the_graph_is_loaded(monkeypatch):
    """`loaded` must not mean "we stopped trying". R-F2951's guard reads it, and
    it was True here, which is why "loading" was never written."""
    def behaviour(key):
        return _Rs.RAISE

    _install(monkeypatch, behaviour)
    _run(nm.init())

    stats = _run(nm.get_stats())
    assert stats.get("load_complete") is False
    assert stats["total_edges"] == 0     # nothing was invented
    assert stats["total_neurons"] == 0


# ── WHAT MUST NOT CHANGE ────────────────────────────────────────────────────

def test_a_genuinely_empty_store_is_still_a_clean_fresh_start(monkeypatch):
    """A first boot on an empty volume must work. Turning "no data yet" into a
    failure is the opposite defect, and it would block every new deployment."""
    _install(monkeypatch, lambda key: None)
    _run(nm.init())

    stats = _run(nm.get_stats())
    assert stats.get("load_complete") is True, (
        "an absent key was treated as a read failure — a fresh install cannot boot"
    )
    assert stats["total_neurons"] == 0


def test_a_successful_load_reports_complete_and_populated(monkeypatch):
    import json

    def behaviour(key):
        if key == nm.NEURONS_KEY:
            return json.dumps(_neurons_blob(4))
        if key == nm.EDGES_KEY:
            return json.dumps({"n0": {"n1": 0.5}, "n1": {"n2": 0.5}})
        return None

    _install(monkeypatch, behaviour)
    _run(nm.init())

    stats = _run(nm.get_stats())
    assert stats.get("load_complete") is True
    assert stats["total_neurons"] == 4
    assert stats["total_edges"] == 2
    assert stats["loaded"] is True


def test_the_reads_actually_went_through_the_strict_api(monkeypatch):
    """A guard that cannot fail is not a guard: if the loader silently reverted
    to the non-strict readers, every test above would still pass because the
    stub returns the same values — only the RAISE path differs. Assert the
    failure is genuinely observable end to end."""
    def behaviour(key):
        return _Rs.RAISE if key == nm.EDGES_KEY else None

    stub = _install(monkeypatch, behaviour)
    _run(nm.init())
    assert nm.EDGES_KEY in stub.reads, "the edge store was never read at all"
    assert _run(nm.get_stats()).get("load_complete") is False


# ── THE WIRING: the boot snapshot must consume it ───────────────────────────

def test_the_boot_snapshot_folds_load_complete_into_stores_ready():
    """A flag nothing reads is the producer-with-no-consumer defect (C-27,
    C-183). C-184's `stores_ready` is the gate that decides whether the boot
    counters are comparable, and this is the fact it was missing."""
    from ._source_probe import repo_path

    src = repo_path("aria_service/main.py").read_text(encoding="utf-8",
                                                      errors="replace")
    assert "load_complete" in src, (
        "main.py never reads load_complete, so a failed neural load still "
        "produces a comparable-looking snapshot"
    )
