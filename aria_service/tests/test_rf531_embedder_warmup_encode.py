"""R-F531 — embedder prewarm must run a real encode, not just load.

Live failure 2026-05-15 10:22:51 BST:
  Health check 'servicecheck-00-http-8000' on port 8000 has failed
  ... [PR04] could not find a good candidate within 40 attempts at LB

Root cause: R-F459's prewarm_embedder() loaded the SentenceTransformer
model in lifespan but did NOT run an actual encode. Sentence-
transformers does the bulk of its lazy init on the FIRST encode call
(tokenizer warm-up, PyTorch graph JIT, attention buffer allocation,
output projection), not on construction. So when the cold-boot brain-
absorb storm hit + the sanctions refresh kicked off, the FIRST real
encode cost 22-28s of cold-CPU time even though the model was already
loaded. That first encode hogged the GIL → asyncio event loop starved
→ /api/aria/health/live timed out → fly PR04 cascade.

R-F531 fix: after the model is loaded inside prewarm_embedder(), run
a single `model.encode("warmup")` via the existing _safe_encode lock.
This pays the 22-28s cost ONCE during boot, off the request path. The
first real encode at runtime is then fast (~50-100ms).

These tests pin:
  1. prewarm_embedder() calls _safe_encode at least once when model
     loads successfully (source-text + behavioural check via mock)
  2. If model load fails, prewarm logs the failure but does NOT
     attempt to encode (no crash)
  3. If encode fails, prewarm logs the failure but does NOT crash
  4. prewarm is idempotent when called a second time (already-warm
     model short-circuits, no duplicate warmup encode)
"""
from __future__ import annotations

import asyncio
import inspect

# R-F3773/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def test_rf531_prewarm_runs_warmup_encode_source_check():
    """The prewarm function must call `_safe_encode` so the lazy-init
    JIT/buffer-allocation work fires during lifespan, not on the first
    real request."""
    from aria_service.intel import semantic_search
    src = function_source(semantic_search, "prewarm_embedder")
    # Tag is present
    assert "R-F531" in src, "R-F531 tag missing from prewarm_embedder"
    # Calls _safe_encode (NOT just _get_embedder/aget_embedder)
    assert "_safe_encode" in src, (
        "prewarm_embedder must call _safe_encode to actually warm the "
        "encode path (R-F531). Loading the model alone leaves the "
        "lazy-init cost on the first real request."
    )
    # Uses asyncio.to_thread so the encode runs off the event loop
    assert "to_thread" in src, (
        "prewarm_embedder must route _safe_encode through "
        "asyncio.to_thread so the cold-CPU encode does NOT block "
        "the asyncio event loop."
    )


def test_rf531_prewarm_invokes_safe_encode_at_runtime(monkeypatch):
    """Behavioural: run prewarm with a fake embedder, verify
    _safe_encode was actually called with the warmup string."""
    from aria_service.intel import semantic_search

    # Reset module state so prewarm doesn't early-return
    monkeypatch.setattr(semantic_search, "_embedder", None)

    class _FakeModel:
        def encode(self, _text, **_kw):
            return [[0.0] * 384]

    fake = _FakeModel()

    async def _fake_aget():
        # Mimic aget_embedder side effect of setting the module singleton
        monkeypatch.setattr(semantic_search, "_embedder", fake)
        return fake

    monkeypatch.setattr(semantic_search, "aget_embedder", _fake_aget)

    encode_calls: list[tuple] = []

    def _spy_safe_encode(model, text, **kwargs):
        encode_calls.append((model, text, kwargs))
        return [[0.0] * 384]

    monkeypatch.setattr(semantic_search, "_safe_encode", _spy_safe_encode)

    asyncio.run(semantic_search.prewarm_embedder())

    assert len(encode_calls) == 1, (
        f"Expected 1 warmup encode during prewarm, got {len(encode_calls)}"
    )
    assert encode_calls[0][0] is fake, "Warmup encode used wrong model object"
    assert encode_calls[0][1] == "warmup", (
        f"Warmup encode used unexpected payload: {encode_calls[0][1]!r}"
    )
    assert encode_calls[0][2].get("normalize_embeddings") is True


def test_rf531_prewarm_idempotent_when_already_warm(monkeypatch):
    """If the embedder is already loaded, prewarm must return
    immediately without re-loading or re-encoding."""
    from aria_service.intel import semantic_search

    class _Sentinel:
        def encode(self, *_a, **_k):
            raise AssertionError(
                "encode should NOT be called on second prewarm — the "
                "first one already paid the warmup cost"
            )

    monkeypatch.setattr(semantic_search, "_embedder", _Sentinel())

    encode_calls: list[tuple] = []

    def _spy_safe_encode(*args, **kwargs):
        encode_calls.append((args, kwargs))
        return [[0.0] * 384]

    monkeypatch.setattr(semantic_search, "_safe_encode", _spy_safe_encode)

    # Should return immediately, not call _safe_encode
    asyncio.run(semantic_search.prewarm_embedder())

    assert encode_calls == [], (
        "Second prewarm must short-circuit when _embedder is non-None "
        "(R-F531 idempotency)"
    )


def test_rf531_prewarm_swallows_load_failure(monkeypatch):
    """If aget_embedder raises, prewarm must not propagate — it logs
    and returns. Subsequent _get_embedder() retries via the original
    error-handling path."""
    from aria_service.intel import semantic_search

    monkeypatch.setattr(semantic_search, "_embedder", None)

    async def _raises():
        raise RuntimeError("simulated cold-volume model download failure")

    monkeypatch.setattr(semantic_search, "aget_embedder", _raises)

    # Should not raise
    asyncio.run(semantic_search.prewarm_embedder())


def test_rf531_prewarm_swallows_encode_failure(monkeypatch):
    """If the warmup encode itself raises, prewarm must not propagate
    — log and return. The first real request will pay the cold cost
    (degraded but not crashed)."""
    from aria_service.intel import semantic_search

    monkeypatch.setattr(semantic_search, "_embedder", None)

    class _FakeModel:
        pass

    async def _fake_aget():
        monkeypatch.setattr(semantic_search, "_embedder", _FakeModel())
        return semantic_search._embedder

    def _raise_safe_encode(*_a, **_k):
        raise RuntimeError("simulated torch CUDA OOM during warmup")

    monkeypatch.setattr(semantic_search, "aget_embedder", _fake_aget)
    monkeypatch.setattr(semantic_search, "_safe_encode", _raise_safe_encode)

    # Should not raise
    asyncio.run(semantic_search.prewarm_embedder())


def test_rf531_prewarm_short_circuits_if_aget_returns_none(monkeypatch):
    """If aget_embedder returns None (e.g. sentence-transformers not
    installed), prewarm must NOT attempt to encode (would crash on
    None.encode)."""
    from aria_service.intel import semantic_search

    monkeypatch.setattr(semantic_search, "_embedder", None)

    async def _returns_none():
        return None

    monkeypatch.setattr(semantic_search, "aget_embedder", _returns_none)

    encode_calls: list[tuple] = []

    def _spy(*args, **kwargs):
        encode_calls.append((args, kwargs))
        return None

    monkeypatch.setattr(semantic_search, "_safe_encode", _spy)

    asyncio.run(semantic_search.prewarm_embedder())

    assert encode_calls == [], (
        "Warmup encode must NOT be attempted when aget_embedder "
        "returns None (TF-IDF fallback path)"
    )
