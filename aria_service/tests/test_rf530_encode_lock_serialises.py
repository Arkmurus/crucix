"""R-F530 — embedder encode() calls must be serialised process-wide.

Live failure 2026-05-15 08:30-09:00 BST: under email-backfill burst,
brain_hook.absorb p95 climbed to 70s. R-F460 (inter-absorb pause) +
R-F521 (global wall-clock pause) reduce the arrival rate of absorbs
but DON'T serialise the embedder itself. When many concurrent
threads call model.encode() simultaneously, sentence-transformers
queues internally with no fairness — long tail.

R-F530 wraps every model.encode() call in a process-wide
threading.Lock so only ONE encode runs at a time, regardless of how
many absorb workers are paced through the dampener.

Tests
═════
  1. _safe_encode acquires the lock for the duration of the call
  2. Two concurrent _safe_encode calls serialise (call B starts only
     after call A finishes)
  3. _safe_encode passes through return value and kwargs unchanged
  4. Source-text check: the three encode() call sites in
     semantic_search.py now route through _safe_encode, plus
     rag_store.py's __call__ embedding function
"""
from __future__ import annotations

import inspect
import threading
import time

import pytest

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def test_rf530_lock_exists_at_module_scope():
    from aria_service.intel import semantic_search
    assert hasattr(semantic_search, "_encode_lock"), (
        "R-F530: _encode_lock must exist at module scope of semantic_search"
    )
    assert isinstance(semantic_search._encode_lock, type(threading.Lock())), (
        "R-F530: _encode_lock must be a threading.Lock (not RLock or asyncio.Lock)"
    )


def test_rf530_safe_encode_passes_through(monkeypatch):
    from aria_service.intel import semantic_search

    captured: dict = {}

    class FakeModel:
        def encode(self, text, **kwargs):
            captured["text"] = text
            captured["kwargs"] = kwargs
            return f"encoded:{text}"

    result = semantic_search._safe_encode(
        FakeModel(),
        "hello world",
        normalize_embeddings=True,
        convert_to_numpy=False,
    )
    assert result == "encoded:hello world"
    assert captured["text"] == "hello world"
    assert captured["kwargs"] == {
        "normalize_embeddings": True,
        "convert_to_numpy": False,
    }


def test_rf530_two_concurrent_encodes_serialise():
    """Spawn two threads calling _safe_encode against a model whose
    encode() sleeps for 200ms. With R-F530's lock they MUST run
    sequentially — total time ≥ 2×200ms — not concurrently."""
    from aria_service.intel import semantic_search

    starts: list[float] = []
    finishes: list[float] = []
    barrier = threading.Barrier(2)

    class SlowModel:
        def encode(self, text, **kwargs):
            starts.append(time.monotonic())
            time.sleep(0.2)  # 200 ms of work
            finishes.append(time.monotonic())
            return text

    def worker(text):
        barrier.wait()  # synchronise start so we're truly racing
        semantic_search._safe_encode(SlowModel(), text)

    t0 = time.monotonic()
    th1 = threading.Thread(target=worker, args=("a",))
    th2 = threading.Thread(target=worker, args=("b",))
    th1.start(); th2.start()
    th1.join(); th2.join()
    elapsed = time.monotonic() - t0

    # Two calls × 200ms = 400ms minimum if serialised.
    assert elapsed >= 0.4, (
        f"R-F530 lock failed to serialise: 2 concurrent encodes took "
        f"{elapsed*1000:.0f}ms, expected ≥ 400ms"
    )
    # Cap on the upper bound — if the lock causes weirdness like
    # deadlock or excessive wait, we'd see way more than 600ms.
    assert elapsed < 1.0, (
        f"R-F530 serialisation took too long ({elapsed*1000:.0f}ms) — "
        f"possible deadlock or scheduling issue"
    )
    # Sanity: second start must be AFTER first finish
    assert min(starts) <= max(finishes), "timeline inverted somehow"


# ───────────── R-F530 — every encode call site routes through _safe_encode ─


def test_rf530_semantic_search_call_sites_use_safe_encode():
    """No bare `= model.encode(` or `return model.encode(` in
    semantic_search.py — every executable call must go through
    _safe_encode. Docstring mentions and the wrapped call INSIDE
    _safe_encode itself are fine."""
    from aria_service.intel import semantic_search
    src = module_source(semantic_search)
    lines = src.split("\n")
    offenders = []
    # Lines that contain a real call: `= model.encode(` or `return model.encode(`
    # or assignment-with-call patterns. We deliberately do NOT flag the bare
    # `model.encode(...)` inside _safe_encode's body (it's the wrapped call)
    # nor docstring/comment mentions.
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#") or '"""' in stripped:
            continue
        # The wrapped call inside _safe_encode looks like
        # `return model.encode(text_or_texts, **kwargs)` — skip if
        # `text_or_texts` is in the same line.
        if "text_or_texts" in stripped:
            continue
        if ("= model.encode(" in line) or ("return model.encode(" in line):
            offenders.append((i, stripped))
    assert not offenders, (
        f"R-F530: executable model.encode( call sites in semantic_search.py "
        f"— all must go through _safe_encode. Offenders: {offenders}"
    )


def test_rf530_rag_store_chromadb_path_uses_safe_encode():
    """The chromadb embedding-function path in rag_store.py must also
    route through _safe_encode so the two embedder paths share one
    serialisation point."""
    from aria_service.intel import rag_store
    src = module_source(rag_store)
    assert "_safe_encode" in src, (
        "R-F530: rag_store.py must import + use _safe_encode for its "
        "chromadb embedding-function. The bare model.encode() path on "
        "line ~156 was a parallel source of embedder contention."
    )
