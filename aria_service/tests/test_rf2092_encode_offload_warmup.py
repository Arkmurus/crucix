"""R-F2092 — encode_offload warmup timeout must not latch the pool broken.

Live root cause (2026-06-28 deploy storm): on every aria-intel cold-boot,
encode_offload.start(warmup=True) raced the boot CPU and the 90s warmup timed
out → start() set _pool_broken=True PERMANENTLY → is_enabled() False → every
embed fell back to in-process sentence_transformers.encode() ON the event loop
→ "R-F703 event loop stalled 5-12s" → fly health check failed → aria-wa
'brain unreachable' → WhatsApp not responding.

Fix: a warmup timeout/transient must NOT latch broken — the worker process is
alive and _worker_encode lazy-loads the model on the first real encode (in the
child, off the main loop). Only a genuine BrokenProcessPool (worker crash,
caught in encode()) is terminal.
"""
from __future__ import annotations

import concurrent.futures as _cf

import pytest


def _reset(eo):
    eo._pool = None
    eo._pool_broken = False


def test_rf2092_warmup_timeout_keeps_pool_enabled(monkeypatch):
    from aria_service.intel import encode_offload as eo
    _reset(eo)
    monkeypatch.setattr(eo, "_ENABLED", True)

    class _FakeFuture:
        def result(self, timeout=None):
            raise TimeoutError("warmup raced the cold-boot")

    class _FakePool:
        def __init__(self, *a, **k):
            pass

        def submit(self, *a, **k):
            return _FakeFuture()

        def shutdown(self, *a, **k):
            pass

    monkeypatch.setattr(_cf, "ProcessPoolExecutor", _FakePool)
    try:
        eo.start(warmup=True)
        # The pool stays up and ENABLED so the worker lazy-loads on first encode —
        # the loop-stalling in-process fallback is NOT latched.
        assert eo._pool is not None, "pool must remain after a warmup timeout"
        assert eo._pool_broken is False, "warmup timeout must NOT latch _pool_broken (R-F2092)"
        assert eo.is_enabled() is True, "offload must stay enabled after a warmup timeout"
    finally:
        try:
            eo.stop()
        except Exception:
            pass
        _reset(eo)


def test_rf2092_broken_process_pool_still_latches(monkeypatch):
    """A real worker crash (BrokenProcessPool) IS terminal — encode() latches it
    so we stop hammering a dead pool."""
    from aria_service.intel import encode_offload as eo
    try:
        from concurrent.futures import BrokenProcessPool
    except ImportError:  # Python 3.14 moved it
        from concurrent.futures.process import BrokenProcessPool
    _reset(eo)
    monkeypatch.setattr(eo, "_ENABLED", True)

    class _FakeFuture:
        def result(self, timeout=None):
            raise BrokenProcessPool("worker process died")

    class _FakePool:
        def submit(self, *a, **k):
            return _FakeFuture()

    eo._pool = _FakePool()
    try:
        with pytest.raises(eo.OffloadUnavailable):
            eo.encode("hello")
        assert eo._pool_broken is True, "a genuine BrokenProcessPool MUST latch broken"
    finally:
        _reset(eo)


def test_rf2092_warmup_success_marks_warmed(monkeypatch):
    """Happy path: a successful warmup keeps the pool enabled (no regression)."""
    from aria_service.intel import encode_offload as eo
    _reset(eo)
    monkeypatch.setattr(eo, "_ENABLED", True)

    class _OkFuture:
        def result(self, timeout=None):
            return [[0.0, 0.1]]

    class _FakePool:
        def __init__(self, *a, **k):
            pass

        def submit(self, *a, **k):
            return _OkFuture()

        def shutdown(self, *a, **k):
            pass

    monkeypatch.setattr(_cf, "ProcessPoolExecutor", _FakePool)
    try:
        eo.start(warmup=True)
        assert eo.is_enabled() is True
        assert eo._pool_broken is False
    finally:
        try:
            eo.stop()
        except Exception:
            pass
        _reset(eo)
