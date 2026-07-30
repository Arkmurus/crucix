"""R-F459 — race-safe sentence-transformer singleton + async accessor.

Background (the bug we're fixing for real this time):
  - Original problem: `_get_embedder()` had no lock. Multiple concurrent
    chromadb/semantic_search/reasoning_library callers each instantiated
    their own SentenceTransformer (the constructor releases the GIL
    during HF cache I/O so CPython's GIL doesn't save us). Each parallel
    load ate ~32s + 80MB RAM. Live evidence: fly logs 2026-05-13 22:07
    BST showed the model loaded TWICE in 2 minutes by ONE process.
  - R-F458 first attempt (reverted): added `threading.Lock` + a 15-second
    sleep before lifespan prewarm. The sleep meant first HTTP request
    arrived BEFORE prewarm, started the 32s load while holding the lock,
    and N other worker threads queued on the lock. ThreadPoolExecutor
    saturated, GIL starved the event loop, /health/live timed out, fly
    PR04 cascade for ~5 minutes until revert.
  - R-F459 (this fix): keep the lock (the race is real) BUT prewarm
    IMMEDIATELY at lifespan startup so by the time HTTP traffic arrives
    the model is warm and worker threads hit the fast path.

Tests cover the three load-bearing invariants:
  1. Singleton race-safe under N concurrent callers (no double-load)
  2. Fast path bypasses the lock when warm (no perf regression)
  3. aget_embedder is async + wraps load in to_thread (no event-loop block)

Plus operational invariants:
  4. is_embedder_ready() non-blocking
  5. prewarm_embedder is idempotent + never raises
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import threading

import pytest  # R-F3449 — the autouse teardown fixture below needs it


def _reset_state():
    """Reset the singleton between tests so each starts clean."""
    from aria_service.intel import semantic_search
    semantic_search._embedder = None
    semantic_search._embedder_checked = False


@pytest.fixture(autouse=True)
def _reset_embedder_after_each_test():
    """R-F3449 — `_reset_state()` is called at the START of each test here, which keeps this
    FILE clean but leaves the last test's fake embedder installed for the rest of the
    session: `semantic_search._embedder = _Sentinel()` with `_embedder_checked = True` makes
    every later semantic search use a bogus embedder. Latent in the R-F3448 baseline only
    because nothing after this file exercised it. Reset on the way OUT as well."""
    yield
    _reset_state()


# ───────────────────────── 1. race-safety ────────────────────────────────────


def test_rf459_concurrent_callers_load_model_exactly_once(monkeypatch):
    """N worker threads racing on _get_embedder() must result in EXACTLY
    ONE constructor call. This is the load-bearing R-F379 fix."""
    _reset_state()

    construct_count = {"n": 0}
    construct_lock = threading.Lock()

    class _FakeST:
        def __init__(self, name):
            with construct_lock:
                construct_count["n"] += 1
            # Simulate constructor that releases the GIL during I/O —
            # ALL racing threads should observe count=1 because the
            # _embedder_lock serialises.
            import time
            time.sleep(0.05)

    fake_module = type("M", (), {"SentenceTransformer": _FakeST})
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    from aria_service.intel import semantic_search

    results = []
    barrier = threading.Barrier(12)

    def _worker():
        barrier.wait()
        results.append(semantic_search._get_embedder())

    threads = [threading.Thread(target=_worker) for _ in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert construct_count["n"] == 1, (
        f"R-F459 REGRESSION: {construct_count['n']} concurrent constructor "
        f"calls from 12 racing threads. The lock failed to serialise — "
        f"R-F379 race is back."
    )
    assert len({id(r) for r in results}) == 1, (
        "R-F459: all threads must get the SAME instance"
    )

    _reset_state()


# ───────────────────────── 2. fast path ──────────────────────────────────────


def test_rf459_fast_path_does_not_acquire_lock(monkeypatch):
    """When the model is already loaded, _get_embedder must return
    WITHOUT touching the lock — preserving the hot-path performance
    that production depends on."""
    _reset_state()
    from aria_service.intel import semantic_search

    class _Sentinel: pass
    fake = _Sentinel()
    semantic_search._embedder = fake
    semantic_search._embedder_checked = True

    # Replace the lock with one that raises if acquired — proves fast
    # path doesn't touch it.
    class _PoisonLock:
        def __enter__(self): raise AssertionError("R-F459: fast path acquired lock")
        def __exit__(self, *a): pass

    monkeypatch.setattr(semantic_search, "_embedder_lock", _PoisonLock())
    result = semantic_search._get_embedder()
    assert result is fake

    _reset_state()


# ───────────────────────── 3. async accessor + prewarm ───────────────────────


def test_rf459_aget_embedder_is_coroutine():
    """aget_embedder must be defined as an async function so it can be
    awaited from lifespan / HTTP handlers."""
    from aria_service.intel import semantic_search
    assert hasattr(semantic_search, "aget_embedder")
    assert inspect.iscoroutinefunction(semantic_search.aget_embedder)


def test_rf459_aget_embedder_wraps_load_in_thread(monkeypatch):
    """The async accessor must dispatch the actual load via
    asyncio.to_thread so the event loop stays responsive during the
    32s cold-load. This is the load-bearing event-loop-protection."""
    _reset_state()

    to_thread_called = {"n": 0, "fn": None}

    real_to_thread = asyncio.to_thread

    async def _spying_to_thread(fn, *args, **kwargs):
        to_thread_called["n"] += 1
        to_thread_called["fn"] = fn
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spying_to_thread)

    class _FakeST:
        def __init__(self, name): pass
    monkeypatch.setitem(
        sys.modules, "sentence_transformers",
        type("M", (), {"SentenceTransformer": _FakeST}),
    )

    from aria_service.intel import semantic_search
    result = asyncio.run(semantic_search.aget_embedder())

    assert to_thread_called["n"] == 1, (
        "R-F459: aget_embedder must dispatch the load via asyncio.to_thread"
    )
    assert to_thread_called["fn"] is semantic_search._get_embedder
    assert result is not None

    _reset_state()


def test_rf459_aget_embedder_fast_path_skips_to_thread(monkeypatch):
    """When the model is already loaded, aget_embedder returns directly
    without involving the thread pool. Preserves hot-path performance
    AND avoids unnecessary task switches."""
    _reset_state()
    from aria_service.intel import semantic_search

    class _Sentinel: pass
    fake = _Sentinel()
    semantic_search._embedder = fake
    semantic_search._embedder_checked = True

    to_thread_called = {"n": 0}
    async def _spy(fn, *a, **kw):
        to_thread_called["n"] += 1
    monkeypatch.setattr(asyncio, "to_thread", _spy)

    result = asyncio.run(semantic_search.aget_embedder())
    assert result is fake
    assert to_thread_called["n"] == 0, (
        "R-F459: warm-path aget_embedder must skip asyncio.to_thread"
    )
    _reset_state()


# ───────────────────────── 4. is_embedder_ready non-blocking ─────────────────


def test_rf459_is_embedder_ready_non_blocking():
    """is_embedder_ready must NEVER block — callers in hot paths use it
    to decide whether to fall back to TF-IDF."""
    _reset_state()
    from aria_service.intel import semantic_search

    assert semantic_search.is_embedder_ready() is False

    class _Sentinel: pass
    semantic_search._embedder = _Sentinel()
    assert semantic_search.is_embedder_ready() is True

    _reset_state()


# ───────────────────────── 5. prewarm idempotent + safe ──────────────────────


def test_rf459_prewarm_skips_when_already_loaded(monkeypatch):
    """prewarm_embedder on a warm singleton is a no-op — doesn't
    re-trigger load, doesn't touch thread pool."""
    _reset_state()
    from aria_service.intel import semantic_search

    class _Sentinel: pass
    fake = _Sentinel()
    semantic_search._embedder = fake
    semantic_search._embedder_checked = True

    # Patch _get_embedder to detect if prewarm calls it
    called = {"n": 0}
    real_get = semantic_search._get_embedder
    def _spy():
        called["n"] += 1
        return real_get()
    monkeypatch.setattr(semantic_search, "_get_embedder", _spy)

    asyncio.run(semantic_search.prewarm_embedder())
    assert called["n"] == 0, (
        "R-F459: warm prewarm must be a no-op (don't reload)"
    )
    assert semantic_search._embedder is fake

    _reset_state()


def test_rf459_prewarm_never_raises_on_load_failure(monkeypatch):
    """If the cold-load itself fails (e.g. sentence-transformers
    missing), prewarm logs a warning but never raises — lifespan must
    not crash because of an optional embedder."""
    _reset_state()
    from aria_service.intel import semantic_search

    async def _failing_aget():
        raise RuntimeError("simulated load failure")
    monkeypatch.setattr(semantic_search, "aget_embedder", _failing_aget)

    # Must not raise
    asyncio.run(semantic_search.prewarm_embedder())

    _reset_state()


# ───────────────────────── 6. integration: lifespan wiring ───────────────────


def test_rf459_main_lifespan_creates_prewarm_task():
    """Static check: main.py lifespan must call
    `asyncio.create_task(_embedder_prewarm_bg())` so the prewarm fires
    immediately at startup (NOT after a sleep). Pre-R-F459 history shows
    the 15s-sleep version caused the lock-saturation problem."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parent.parent / "main.py"
    ).read_text(encoding="utf-8")

    assert "R-F459" in src, "R-F459 marker missing from main.py"
    assert "_embedder_prewarm_bg" in src, (
        "R-F459: lifespan prewarm task missing from main.py"
    )
    assert "prewarm_embedder" in src, (
        "R-F459: main.py no longer calls prewarm_embedder"
    )
    # Critical invariant: the prewarm fires via create_task (NOT after
    # the rag_init_bg 15s sleep). Verify the create_task appears BEFORE
    # the rag_init_bg sleep marker.
    create_task_idx = src.find("asyncio.create_task(_embedder_prewarm_bg")
    sleep_idx = src.find("await asyncio.sleep(15)")
    assert create_task_idx > 0, "R-F459: create_task call missing"
    assert sleep_idx > 0, "R-F459: rag_init_bg sleep marker missing"
    assert create_task_idx < sleep_idx, (
        "R-F459 REGRESSION: prewarm task scheduled AFTER the rag-init "
        "sleep. The whole point of R-F459 is to fire prewarm IMMEDIATELY."
    )
