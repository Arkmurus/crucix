"""R-F2122 — heavy graph loads must be OFF the boot critical path.

2026-06-28 incident: knowledge (~223k facts) + neural_memory (~1.2M edges) loaded
SYNCHRONOUSLY in lifespan() before `yield`, making boot take ~10 min — far past
fly's 1-min health grace, so every restart was a 10-min outage that looked like a
crash loop. R-F2122 moves those two into a background warmup task so /health goes
green in seconds; queries degrade to empty/partial (never error/hang) until warm.

These tests lock (a) the deferral structure and (b) the degrade-safety property the
deferral RELIES on — that the heavy query fns return empty, not raise, when unloaded.
"""
import inspect

import aria_service.main as M

# R-F3772/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def test_rf2122_heavy_inits_not_on_synchronous_boot_path():
    src = function_source(M, "lifespan")
    # the synchronous _run_boot_inits block is everything before the warmup def
    pre_warmup = src.split("_warmup_heavy_graphs")[0]
    assert '("knowledge", knowledge.init)' not in pre_warmup, \
        "knowledge.init must NOT be in the synchronous boot inits (R-F2122)"
    assert '("neural_memory", neural_memory.init)' not in pre_warmup, \
        "neural_memory.init must NOT be in the synchronous boot inits (R-F2122)"


def test_rf2122_heavy_inits_are_in_the_background_warmup():
    src = function_source(M, "lifespan")
    assert "_warmup_heavy_graphs" in src, "the background warmup task must exist"
    warmup = src.split("_warmup_heavy_graphs", 1)[1]
    # knowledge + neural + the freeze all move into the warmup
    assert '("knowledge", knowledge.init)' in warmup
    assert '("neural_memory", neural_memory.init)' in warmup
    assert "_freeze_long_lived_state()" in warmup, \
        "freeze must run AFTER the graphs load, i.e. inside the warmup"


def test_rf2122_knowledge_degrades_to_empty_when_unloaded():
    """The deferral is only safe because queries degrade, not raise, while unloaded."""
    from aria_service.intel import knowledge as kn
    # search_knowledge returns "" and all_facts returns [] when the cache is empty
    # (knowledge.py: `if not _cache: return ""` / `return []`). Force-empty the cache
    # to simulate the warmup window.
    orig = getattr(kn, "_cache", None)
    try:
        kn._cache = {}
        assert kn.search_knowledge("anything") == ""
        assert kn.all_facts() == []
    finally:
        if orig is not None:
            kn._cache = orig


def test_rf2122_neural_recall_degrades_to_empty_when_unloaded():
    import asyncio
    from aria_service.intel import neural_memory as nm
    orig_n, orig_w = nm._neurons, nm._word_to_ids
    try:
        nm._neurons = {}
        nm._word_to_ids = {}
        res = asyncio.run(nm.recall("anything"))
        assert isinstance(res, dict)
        assert res.get("neurons") == []  # empty, not an error/hang
    finally:
        nm._neurons, nm._word_to_ids = orig_n, orig_w
