"""R-F1890 — process-offloaded sentence-transformer encode.

ROOT CAUSE of the recurring web-chat / brain wedge (continuous_profiler:
"main loop heartbeat stale 2-6s — frame semantic_search._safe_encode"):
`model.encode()` is a GIL-holding torch C-extension call. Running it via
`asyncio.to_thread` does NOT help — the encode thread and the event-loop thread
share ONE process-wide GIL, so a batch encode (RAG query, the semantic index
queue, neural_memory, EagleEye codebase index) starves the asyncio loop for
seconds. Every encode funnels through semantic_search._safe_encode, the single
choke point this module plugs into.

FIX: run the encode in a SEPARATE PROCESS — a 1-worker ProcessPoolExecutor using
the 'spawn' context (fork + torch can deadlock), with the model loaded ONCE in
the worker. The worker holds its OWN GIL; the main event loop is never blocked.
The (sync) caller parks on `future.result()`, which releases the main-process
GIL while waiting, so the loop runs freely during the encode.

Bulletproof + reversible:
  - env-gated: ARIA_ENCODE_OFFLOAD (default "1"); set "0" to revert instantly.
  - ANY failure (pool won't start / broken / timeout / unexpected kwargs)
    raises OffloadUnavailable so _safe_encode transparently falls back to the
    existing in-process path. The offload can never make encoding WORSE.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("aria.encode_offload")

_MODEL_NAME = (os.getenv("ARIA_EMBED_MODEL", "all-MiniLM-L6-v2") or "all-MiniLM-L6-v2").strip()
_ENABLED = (os.getenv("ARIA_ENCODE_OFFLOAD", "1").strip().lower() in ("1", "true", "yes", "on"))
_RESULT_TIMEOUT_S = float(os.getenv("ARIA_ENCODE_OFFLOAD_TIMEOUT_S", "60"))
_WARMUP_TIMEOUT_S = float(os.getenv("ARIA_ENCODE_OFFLOAD_WARMUP_S", "90"))

_pool = None
_pool_broken = False


class OffloadUnavailable(RuntimeError):
    """Raised when the encode could not be offloaded — caller must fall back
    to the in-process path. Never indicates a wrong result, only that the
    separate-process path is unavailable for this call."""


# ── worker side (runs in the spawned child process) ──────────────────────────
_worker_model = None


def _worker_init(model_name: str) -> None:
    """Pool initializer — load the embedding model ONCE per worker process."""
    global _worker_model
    try:
        import torch
        torch.set_num_threads(1)   # mirror the in-process R-F857 setting
    except Exception:
        pass
    try:
        from sentence_transformers import SentenceTransformer
        _worker_model = SentenceTransformer(model_name)
    except Exception as e:  # leave _worker_model None → _worker_encode raises → parent falls back
        _worker_model = None
        raise RuntimeError(f"worker model load failed: {e}")


def _worker_encode(text_or_texts, normalize: bool):
    """Encode in the worker. Returns the embedding ndarray (pickles cleanly
    back to the parent, preserving the exact in-process return type/shape)."""
    global _worker_model
    if _worker_model is None:
        _worker_init(_MODEL_NAME)
    return _worker_model.encode(text_or_texts, normalize_embeddings=normalize)


# ── parent side ──────────────────────────────────────────────────────────────
def start(*, warmup: bool = True) -> None:
    """Start the 1-worker encode pool (idempotent). Safe to call at boot. On
    any failure, leaves the pool unavailable so callers fall back in-process."""
    global _pool, _pool_broken
    if not _ENABLED or _pool is not None:
        return
    try:
        import multiprocessing as _mp
        from concurrent.futures import ProcessPoolExecutor
        ctx = _mp.get_context("spawn")
        _pool = ProcessPoolExecutor(
            max_workers=1, mp_context=ctx,
            initializer=_worker_init, initargs=(_MODEL_NAME,),
        )
        _pool_broken = False
        logger.info("R-F1890 encode-offload pool started (1 worker, spawn, model=%s)", _MODEL_NAME)
        if warmup:
            # Force worker spawn + model load NOW (pool workers are lazy), so the
            # first real encode isn't a ~30s cold load. Bounded; failure → broken.
            try:
                _pool.submit(_worker_encode, "warmup", True).result(timeout=_WARMUP_TIMEOUT_S)
                logger.info("R-F1890 encode-offload worker warmed (model loaded in child process)")
            except Exception as e:
                logger.warning("R-F1890 encode-offload warmup failed — marking broken, will fall back: %s", e)
                _pool_broken = True
    except Exception as e:
        _pool, _pool_broken = None, True
        logger.warning("R-F1890 encode-offload pool failed to start — in-process fallback: %s", e)


def stop() -> None:
    global _pool
    if _pool is not None:
        try:
            _pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _pool = None


def is_enabled() -> bool:
    return bool(_ENABLED and _pool is not None and not _pool_broken)


def encode(text_or_texts, *, normalize: bool = True):
    """Encode in the worker process. Returns the embedding(s) (ndarray) or
    raises OffloadUnavailable so the caller falls back in-process."""
    global _pool_broken
    if not is_enabled():
        raise OffloadUnavailable("offload disabled or pool unavailable")
    try:
        fut = _pool.submit(_worker_encode, text_or_texts, normalize)
        return fut.result(timeout=_RESULT_TIMEOUT_S)   # parking here releases the main GIL
    except Exception as e:
        # BrokenProcessPool (worker crash) is terminal for this pool — mark it so
        # we stop hammering a dead pool every call and fall back cleanly.
        from concurrent.futures import BrokenProcessPool
        if isinstance(e, BrokenProcessPool):
            _pool_broken = True
            logger.warning("R-F1890 encode-offload pool BROKEN — permanent fallback to in-process: %s", e)
        else:
            logger.debug("R-F1890 encode-offload call failed (%s) — fallback in-process", e)
        raise OffloadUnavailable(str(e)) from e
