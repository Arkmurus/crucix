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
import threading
import time

logger = logging.getLogger("aria.encode_offload")

_MODEL_NAME = (os.getenv("ARIA_EMBED_MODEL", "all-MiniLM-L6-v2") or "all-MiniLM-L6-v2").strip()
_ENABLED = (os.getenv("ARIA_ENCODE_OFFLOAD", "1").strip().lower() in ("1", "true", "yes", "on"))
# R-F1906 (verification V-05): `or "60"` not the getenv default — an explicitly
# EMPTY env var ("") passes the default-check and float("") crashes module import
# → boot crash. `or` falls back on empty too.
_RESULT_TIMEOUT_S = float(os.getenv("ARIA_ENCODE_OFFLOAD_TIMEOUT_S") or "60")
_WARMUP_TIMEOUT_S = float(os.getenv("ARIA_ENCODE_OFFLOAD_WARMUP_S") or "90")

_pool = None
_pool_broken = False
# R-F2950 — self-heal state. A dead/broken offload pool must not condemn ARIA to
# in-process encode() forever (each in-process encode holds the GIL and freezes
# the loop — live 2026-07-23 a 77.8s R-F703 stall from exactly this). Rebuild the
# pool on the next encode after a cooldown, bounded so a persistently-crashing
# worker can't thrash-spawn.
_last_restart_attempt = 0.0
_RESTART_COOLDOWN_S = float(os.getenv("ARIA_ENCODE_OFFLOAD_RESTART_COOLDOWN_S") or "300")
_restart_lock = threading.Lock()


def _ensure_pool() -> None:
    """R-F2950 — self-heal a BROKEN (crash-latched) offload pool so embeds return
    to the off-loop subprocess instead of the GIL-freezing in-process fallback.
    Scope is deliberately the `_pool_broken` latch ONLY: before R-F2950 a single
    BrokenProcessPool crash set `_pool_broken=True` PERMANENTLY (is_enabled()
    False forever) → every subsequent embed ran in-process on the loop → recurring
    77s R-F703 stalls until a full restart. The never-started case (`_pool is
    None` without a crash) is boot `start()`'s job and is already gap-wired — we
    do NOT silently start it here (that would change the documented
    unstarted→fall-back contract, cf. test_rf1890). Cooldown-bounded + lock-guarded
    (encode() may run from worker threads); no warmup — the worker lazy-loads in
    the child on first encode (off the loop)."""
    global _pool, _pool_broken, _last_restart_attempt
    if not _ENABLED or not _pool_broken:
        return  # only the crash-latched state is self-healed here
    now = time.time()
    if (now - _last_restart_attempt) < _RESTART_COOLDOWN_S:
        return  # within cooldown — fall back in-process for this call
    with _restart_lock:
        # re-check under the lock (another thread may have healed it)
        if not _pool_broken:
            return
        if (time.time() - _last_restart_attempt) < _RESTART_COOLDOWN_S:
            return
        _last_restart_attempt = time.time()
        if _pool is not None:
            try:
                _pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            _pool = None
        _pool_broken = False
        try:
            start(warmup=False)
            if _pool is not None:
                logger.info("R-F2950 encode-offload pool self-healed (rebuilt after unavailability)")
        except Exception as e:
            logger.warning("R-F2950 encode-offload self-heal rebuild failed: %s", e)


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
        # R-F2092 §21a — surface the embedding-offload limb coming up (proprioception:
        # a boot-time event, NOT per-encode). Fail-safe — wiring must never break boot.
        try:
            from .engine_wiring import wire_success
            wire_success(module="encode_offload",
                summary=f"encode-offload pool started (model={_MODEL_NAME})")
        except Exception:
            pass
        if warmup:
            # Force worker spawn + model load NOW (pool workers are lazy), so the
            # first real encode isn't a ~30s cold load. Bounded; failure → broken.
            try:
                _pool.submit(_worker_encode, "warmup", True).result(timeout=_WARMUP_TIMEOUT_S)
                logger.info("R-F1890 encode-offload worker warmed (model loaded in child process)")
            except Exception as e:
                # R-F2092 — a warmup TIMEOUT / transient failure must NOT permanently
                # mark the pool broken. Under cold-boot CPU contention (the torch model
                # load in the child racing the parent's own boot work) the 90s warmup
                # frequently loses the race — and latching `_pool_broken=True` here was
                # the live regression: every aria-intel cold-boot then fell back to
                # in-process sentence_transformers.encode() ON the event loop →
                # "R-F703 event loop stalled 5-12s" → fly health fail → aria-wa
                # 'brain unreachable' → WhatsApp not responding (2026-06-28 deploy storm).
                # The worker PROCESS is alive; `_worker_encode` lazy-loads the model in
                # the child on the first real encode (off the main loop — the whole
                # point). Only a genuine BrokenProcessPool (caught in encode()) is
                # terminal. Leave _pool_broken False so offload is still used.
                logger.warning(
                    "R-F2092 encode-offload warmup did not finish in %ss — NOT marking "
                    "broken; the worker will lazy-load the model on the first encode "
                    "(in-child, off the main loop): %s", _WARMUP_TIMEOUT_S, e,
                )
    except Exception as e:
        _pool, _pool_broken = None, True
        logger.warning("R-F1890 encode-offload pool failed to start — in-process fallback: %s", e)
        # R-F2092 §21a — the offload limb failed to come up; embeds will run
        # in-process (loop-stall risk). Surface it so ARIA sees the degradation.
        try:
            from .engine_wiring import wire_failure
            wire_failure(module="encode_offload",
                detail=f"encode-offload pool failed to start ({type(e).__name__}: {e}) — in-process fallback",
                gap_type="embedder_failure", source="encode_offload.start")
        except Exception:
            pass


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


def get_status() -> dict[str, bool | str]:
    """Return process-local offload state for live diagnostics.

    The pool exists only inside the serving process, so an external Python
    interpreter cannot truthfully probe it by importing this module.
    """
    return {
        "configured": _ENABLED,
        "pool_started": _pool is not None,
        "pool_broken": _pool_broken,
        "enabled": is_enabled(),
        "model": _MODEL_NAME,
    }


def encode(text_or_texts, *, normalize: bool = True):
    """Encode in the worker process. Returns the embedding(s) (ndarray) or
    raises OffloadUnavailable so the caller falls back in-process."""
    global _pool_broken
    # R-F2950 — try to self-heal a dead/broken pool before giving up to the
    # GIL-freezing in-process fallback (cooldown-bounded inside).
    _ensure_pool()
    if not is_enabled():
        raise OffloadUnavailable("offload disabled or pool unavailable")
    try:
        fut = _pool.submit(_worker_encode, text_or_texts, normalize)
        return fut.result(timeout=_RESULT_TIMEOUT_S)   # parking here releases the main GIL
    except Exception as e:
        # BrokenProcessPool (worker crash) is terminal for this pool — mark it so
        # we stop hammering a dead pool every call and fall back cleanly.
        # R-F2092: Python 3.14 moved BrokenProcessPool out of the concurrent.futures
        # top-level into .process — import robustly so the latch logic works on both
        # the 3.13 fly image and 3.14 local (else this except handler would itself
        # ImportError on 3.14 and never latch a genuinely dead pool).
        try:
            from concurrent.futures import BrokenProcessPool
        except ImportError:  # pragma: no cover — version-dependent
            from concurrent.futures.process import BrokenProcessPool
        if isinstance(e, BrokenProcessPool):
            _pool_broken = True
            logger.warning("R-F1890 encode-offload pool BROKEN — permanent fallback to in-process: %s", e)
            # R-F2092 §21a — terminal limb failure; surface to the brain (once, on latch).
            try:
                from .engine_wiring import wire_failure
                wire_failure(module="encode_offload",
                    detail=f"encode-offload worker pool BROKEN (worker crash) — permanent in-process fallback: {e}",
                    gap_type="embedder_failure", source="encode_offload.encode")
            except Exception:
                pass
        else:
            logger.debug("R-F1890 encode-offload call failed (%s) — fallback in-process", e)
        raise OffloadUnavailable(str(e)) from e
