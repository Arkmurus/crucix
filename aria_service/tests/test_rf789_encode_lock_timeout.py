"""R-F789 — semantic_search._safe_encode lock-acquire timeout.

Pre-R-F789 a slow encode holding the global threading.Lock blocked
every other encode caller indefinitely. Live evidence: wedge stacks
showed `rag_store.__call__` at semantic_search → model.encode. Now:
acquire is bounded by ARIA_ENCODE_LOCK_WAIT_S (default 10s) and
raises EncodeLockTimeout on timeout so callers can degrade.
"""
from __future__ import annotations

import threading
import time


def test_safe_encode_times_out_when_lock_held(monkeypatch):
    """If something else holds the encode lock for longer than the
    timeout, _safe_encode must raise EncodeLockTimeout — not block
    forever. Tighten the timeout via env so the test is fast."""
    import importlib
    import aria_service.intel.semantic_search as ss_orig

    monkeypatch.setenv("ARIA_ENCODE_LOCK_WAIT_S", "0.2")
    ss = importlib.reload(ss_orig)

    # Hold the lock in a background thread for longer than the timeout.
    held = threading.Event()
    release = threading.Event()

    def _holder():
        with ss._encode_lock:
            held.set()
            release.wait(timeout=2.0)

    t = threading.Thread(target=_holder, daemon=True)
    t.start()
    held.wait(timeout=1.0)

    # Stub model so we never actually call encode (we'll hit the
    # timeout first).
    class _FakeModel:
        def encode(self, *a, **kw):
            raise AssertionError("should not be reached — lock held")

    try:
        try:
            ss._safe_encode(_FakeModel(), ["x"])
        except ss.EncodeLockTimeout as e:
            assert "R-F789" in str(e)
            assert "encode lock" in str(e).lower()
        else:
            raise AssertionError(
                "expected EncodeLockTimeout while another thread holds "
                "the encode lock"
            )
    finally:
        release.set()
        t.join(timeout=2.0)

    monkeypatch.delenv("ARIA_ENCODE_LOCK_WAIT_S", raising=False)
    importlib.reload(ss_orig)


def test_safe_encode_releases_lock_on_exception(monkeypatch):
    """If model.encode raises, the lock must be released so subsequent
    callers can acquire it. Failure mode pre-fix: encode raises → lock
    leaks → next caller hangs."""
    import importlib
    import aria_service.intel.semantic_search as ss_orig

    monkeypatch.setenv("ARIA_ENCODE_LOCK_WAIT_S", "1.0")
    ss = importlib.reload(ss_orig)

    class _BadModel:
        def encode(self, *a, **kw):
            raise RuntimeError("simulated encoder failure")

    class _GoodModel:
        def encode(self, *a, **kw):
            return "ok"

    try:
        ss._safe_encode(_BadModel(), ["x"])
    except RuntimeError:
        pass

    # If the lock leaked, this hangs > 1s and timing assertion fails.
    t0 = time.monotonic()
    result = ss._safe_encode(_GoodModel(), ["x"])
    elapsed = time.monotonic() - t0
    assert result == "ok"
    assert elapsed < 0.5, (
        f"R-F789: encode lock was not released after exception "
        f"(second call took {elapsed*1000:.0f}ms)"
    )

    monkeypatch.delenv("ARIA_ENCODE_LOCK_WAIT_S", raising=False)
    importlib.reload(ss_orig)


def test_safe_encode_normal_path(monkeypatch):
    """Sanity: when the lock is free, _safe_encode returns the model's
    output unchanged."""
    from aria_service.intel import semantic_search as ss

    class _Model:
        def encode(self, texts, **kw):
            return [[1.0, 2.0, 3.0] for _ in texts]

    out = ss._safe_encode(_Model(), ["a", "b"])
    assert out == [[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]]
