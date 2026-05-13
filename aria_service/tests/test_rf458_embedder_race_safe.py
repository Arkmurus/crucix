"""R-F458 — sentence-transformer singleton is race-safe + has async prewarm.

Live fly logs 22:07-22:09 BST showed the all-MiniLM-L6-v2 model
loaded TWICE in 2 minutes by a SINGLE Python process, while ~20
fly LB PR04 errors fired. Root cause: `_get_embedder()` had no
lock, so two concurrent callers both saw `_embedder is None` and
both entered the SentenceTransformer constructor (which releases
the GIL during HF I/O + weight loading). Effect: 32s + 47s of
duplicate blocking work, ~80MB of duplicate model in memory, and a
2-minute event-loop block.

R-F458 ships two surgical fixes:
  1. `threading.Lock` around the singleton init with double-checked
     locking — fast path is unchanged when warm.
  2. `prewarm_embedder()` async helper that calls _get_embedder
     via asyncio.to_thread so the model loads in a thread pool
     during startup, never blocking the event loop on first request.

main.py lifespan _rag_init_bg now invokes prewarm_embedder() before
the chromadb probe so the chain runs end-to-end off the event loop.
"""
from __future__ import annotations

import asyncio
import threading


def test_rf458_get_embedder_uses_lock():
    """Static: the singleton accessor must hold _embedder_lock during
    the SentenceTransformer constructor call. Catches a regression
    where someone reverts the lock during a refactor."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parent.parent
        / "intel" / "semantic_search.py"
    ).read_text(encoding="utf-8")
    # The R-F458 marker MUST be present
    assert "R-F458" in src, "R-F458: marker missing from semantic_search.py"
    # threading.Lock instance for the embedder
    assert "_embedder_lock" in src
    assert "_threading.Lock()" in src or "threading.Lock()" in src
    # The constructor call is inside the lock block
    lock_idx = src.find("with _embedder_lock:")
    assert lock_idx > 0, "R-F458: 'with _embedder_lock:' missing"
    constructor_idx = src.find('SentenceTransformer("all-MiniLM-L6-v2")')
    assert constructor_idx > lock_idx, (
        "R-F458 REGRESSION: SentenceTransformer constructor is OUTSIDE "
        "the lock. Race window reopened."
    )


def test_rf458_double_checked_locking_fast_path():
    """When the embedder is already loaded, _get_embedder returns
    without acquiring the lock — fast path performance preserved."""
    from aria_service.intel import semantic_search

    # Simulate warm state: pre-load with a fake embedder.
    class _FakeModel:
        def encode(self, *a, **kw): return [[0.1]]
    fake = _FakeModel()
    semantic_search._embedder = fake
    semantic_search._embedder_checked = True
    try:
        # Should NOT touch the lock — call returns immediately
        result = semantic_search._get_embedder()
        assert result is fake
    finally:
        semantic_search._embedder = None
        semantic_search._embedder_checked = False


def test_rf458_concurrent_callers_load_model_once(monkeypatch):
    """The double-checked-locking pattern means N concurrent threads
    calling _get_embedder() simultaneously result in EXACTLY ONE
    SentenceTransformer instantiation. Pre-R-F458 each thread that
    saw `_embedder is None` would call the constructor independently."""
    from aria_service.intel import semantic_search

    # Reset state
    semantic_search._embedder = None
    semantic_search._embedder_checked = False

    construct_count = {"n": 0}
    construct_lock = threading.Lock()

    class _FakeST:
        def __init__(self, name):
            with construct_lock:
                construct_count["n"] += 1
            # Simulate slow load releasing GIL
            import time
            time.sleep(0.05)

    # Patch the SentenceTransformer import inside _get_embedder
    fake_module = type(
        "M", (), {"SentenceTransformer": _FakeST},
    )
    import sys
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    # Fire 8 concurrent threads, all calling _get_embedder
    results = []
    barrier = threading.Barrier(8)

    def _worker():
        barrier.wait()
        results.append(semantic_search._get_embedder())

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()

    # KEY ASSERTION — exactly one constructor call despite 8 racing threads
    assert construct_count["n"] == 1, (
        f"R-F458 REGRESSION: {construct_count['n']} SentenceTransformer "
        f"instantiations from 8 concurrent threads. Pre-R-F458 race "
        f"reopened."
    )
    # All threads got the SAME instance
    assert len({id(r) for r in results}) == 1

    # Cleanup
    semantic_search._embedder = None
    semantic_search._embedder_checked = False


def test_rf458_prewarm_embedder_is_async():
    """prewarm_embedder must be defined as an async coroutine so it can
    be awaited from the FastAPI lifespan."""
    from aria_service.intel import semantic_search
    import inspect
    assert hasattr(semantic_search, "prewarm_embedder")
    assert inspect.iscoroutinefunction(semantic_search.prewarm_embedder)


def test_rf458_prewarm_skips_when_already_loaded():
    """If the embedder is already loaded (warm state), prewarm_embedder
    must be a no-op — not re-trigger the load."""
    from aria_service.intel import semantic_search

    class _FakeModel:
        def encode(self, *a, **kw): return [[0.1]]
    fake = _FakeModel()
    semantic_search._embedder = fake
    semantic_search._embedder_checked = True

    try:
        # Should complete instantly with no exception
        asyncio.run(semantic_search.prewarm_embedder())
        # Embedder still the fake — prewarm didn't clobber it
        assert semantic_search._embedder is fake
    finally:
        semantic_search._embedder = None
        semantic_search._embedder_checked = False


def test_rf458_main_lifespan_invokes_prewarm():
    """Static: main.py's _rag_init_bg must call prewarm_embedder
    BEFORE rag_store.get_stats() so the model is warm by the time
    chromadb probes. Catches a refactor that removes the call."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parent.parent / "main.py"
    ).read_text(encoding="utf-8")
    assert "prewarm_embedder" in src, (
        "R-F458 REGRESSION: main.py no longer calls prewarm_embedder. "
        "Cold-start blocking-load behavior will return."
    )
    # The prewarm call must appear inside _rag_init_bg, BEFORE
    # the rag_store.get_stats() probe.
    init_bg_idx = src.find("_rag_init_bg")
    probe_idx = src.find("rag_store.get_stats()", init_bg_idx)
    prewarm_idx = src.find("prewarm_embedder", init_bg_idx)
    assert init_bg_idx > 0 and probe_idx > 0 and prewarm_idx > 0
    assert prewarm_idx < probe_idx, (
        "R-F458: prewarm_embedder must be invoked BEFORE the chromadb "
        "probe so the embedder is warm when chromadb instantiates "
        "the embedding function."
    )
