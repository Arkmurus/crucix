"""
ARIA Semantic Search — Finds relevant knowledge by meaning, not just keywords.

Primary: sentence-transformer embeddings (all-MiniLM-L6-v2) with cosine
similarity for true semantic understanding.

Fallback: TF-IDF vectorisation with cosine similarity — dependency-free,
activates automatically when sentence-transformers is not installed.

Upgrades ARIA from "find exact words" to "find related concepts":
  - "Angola defence budget" finds "FADM procurement allocation"
  - "UAV suppliers" finds "Baykar drone exports"
  - "sanctions risk" finds "OFAC embargo compliance"

Runs alongside keyword search (knowledge.py) — results are merged for best recall.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from collections import Counter, defaultdict
from typing import Any

# numpy is optional — sentence-transformers embedding path needs it, but
# the TF-IDF fallback path (default when embeddings are unavailable) does
# not. Making the import lazy means `import aria_engine` succeeds on
# minimal test environments that haven't installed numpy. Callers that
# actually hit the embedding path will get a clear error then.
try:
    import numpy as np  # type: ignore
    _NUMPY_AVAILABLE = True
except ImportError:  # pragma: no cover — CI always has numpy
    np = None  # type: ignore
    _NUMPY_AVAILABLE = False

logger = logging.getLogger("aria.semantic")

# ── Stop words (excluded from TF-IDF indexing) ─────────────────────────────
_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "both",
    "each", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "just",
    "but", "and", "or", "if", "this", "that", "these", "those", "it",
    "its", "he", "she", "they", "we", "you", "i", "me", "my", "your",
    "his", "her", "their", "our", "what", "which", "who", "whom",
}

# ── Defence domain synonyms (expands query understanding) ────────────────────
_SYNONYMS = {
    "uav": ["drone", "unmanned aerial", "rpas"],
    "drone": ["uav", "unmanned aerial", "rpas"],
    "mrap": ["mine resistant", "armoured vehicle", "apv"],
    "tender": ["rfp", "rfq", "procurement", "bid"],
    "rfp": ["tender", "procurement", "request for proposal"],
    "sanctions": ["embargo", "ofac", "restricted", "blocked"],
    "embargo": ["sanctions", "ban", "restricted"],
    "oem": ["manufacturer", "supplier", "defence company"],
    "mod": ["ministry of defence", "defense ministry"],
    "procurement": ["acquisition", "purchasing", "tender"],
    "contract": ["deal", "agreement", "award"],
    "budget": ["allocation", "spending", "expenditure", "funding"],
    "naval": ["maritime", "navy", "sea", "patrol vessel", "frigate"],
    "artillery": ["howitzer", "mortar", "guns"],
    "helicopter": ["rotary", "helo", "chopper"],
    "radar": ["surveillance", "detection", "sensor"],
    "angola": ["luanda", "faa", "angolan"],
    "mozambique": ["maputo", "fadm", "mozambican"],
}


# ── Embedding model singleton ──────────────────────────────────────────────
# R-F459 (2026-05-14) — race-safe singleton + async prewarm.
#
# Background: R-F379 (originally observed in fly logs 2026-05-12, recurred
# 2026-05-13 22:07-22:09 BST) — multiple concurrent callers were each
# instantiating their own SentenceTransformer because `_get_embedder` had
# no lock. Cost per cold-burst: 30-90s of redundant loading, ~80MB ×
# N model copies in RAM, fly LB PR04 cascade.
#
# R-F458 (2026-05-13 22:18 BST) attempted to add `threading.Lock` but did
# NOT prewarm before allowing HTTP traffic. Result: lock made first-burst
# WORSE — all worker threads serialised on a 32s load, ThreadPoolExecutor
# saturated, event loop GIL-starved, healthcheck timed out, PR04 cascade.
# Reverted 22:25 BST (ad88848). Memory: rf458_embedder_lock_deferred.md.
#
# R-F459 (this commit, 2026-05-14, validated on local Python 3.14 CI):
#   1. `threading.Lock` around the singleton init (race-safe for the N
#      concurrent worker-thread callers from chromadb / semantic_search /
#      reasoning_library / consistency_suite paths).
#   2. `async def aget_embedder()` — async accessor that wraps the sync
#      load in `asyncio.to_thread`. Lifespan prewarm uses this so the
#      load runs in a worker thread WITHOUT blocking the event loop.
#   3. Lifespan prewarm fires IMMEDIATELY (not after a 15s sleep) so the
#      model is ready before HTTP traffic arrives. Cold-load still takes
#      ~32s but it overlaps with uvicorn boot, not with first-burst.
#   4. `is_embedder_ready()` — non-blocking check used by callers that
#      can fall back to TF-IDF if the model isn't warm yet.
import threading as _threading

_embedder = None
_embedder_checked = False  # avoid repeated ImportError attempts
_embedder_lock = _threading.Lock()  # R-F459 — serialise singleton init

# R-F1118 (2026-05-30) — REVERTED R-F1080 process-pool encode.
# R-F1080 added a ProcessPoolExecutor to avoid GIL contention, but
# model.encode() cannot be pickled across process boundaries — every
# call timed out after 15s and fell back to the thread lock, making
# things WORSE (each encode paid a 15s dead wait + the thread-encode).
# Reverting to the thread-level lock approach that was clean in R-F987.
# The lock is bounded by _ENCODE_LOCK_WAIT_S; callers degrade to TF-IDF
# on timeout. The GIL is still held during encode, but the bounded wait
# prevents indefinite blocking and the to_thread worker yields between
# encodes.
_encode_lock = _threading.Lock()
_ENCODE_LOCK_WAIT_S = float(os.environ.get("ARIA_ENCODE_LOCK_WAIT_S", "10"))


class EncodeLockTimeout(RuntimeError):
    """Raised when _safe_encode can't acquire the encode lock within
    _ENCODE_LOCK_WAIT_S. Callers should catch this and fall back to
    TF-IDF / cached embeddings rather than crashing."""


def _safe_encode(model, text_or_texts, **kwargs):
    """Call model.encode() under the global encode lock.

    The lock is bounded by _ENCODE_LOCK_WAIT_S; raises EncodeLockTimeout
    on timeout so callers can degrade rather than block forever.
    Returns whatever encode() returns on success; passes through kwargs.

    Note: model.encode() holds the GIL (torch C extension), but the
    bounded lock wait prevents indefinite blocking. Callers should run
    this via asyncio.to_thread so the event loop isn't blocked."""
    acquired = _encode_lock.acquire(timeout=_ENCODE_LOCK_WAIT_S)
    if not acquired:
        raise EncodeLockTimeout(
            f"R-F789: could not acquire encode lock within "
            f"{_ENCODE_LOCK_WAIT_S}s — embedder is busy. Caller "
            f"should fall back to TF-IDF / cached embeddings."
        )
    try:
        return model.encode(text_or_texts, **kwargs)
    finally:
        _encode_lock.release()


# R-F895 — the embedder encode runs inside asyncio.to_thread workers (see the
# _safe_encode lock note above), so an encode failure — an EncodeLockTimeout
# under load (the wedge precursor) or a model error — is invisible to the
# brain: error_log_handler drops log records emitted off the loop thread, and
# the old except blocks here logged at debug anyway. Capture the main loop from
# the async entry points so a worker-thread failure can still schedule a
# *deduped* capability_gaps signal the coder/operator can act on.
_MAIN_LOOP: "asyncio.AbstractEventLoop | None" = None


def _capture_main_loop() -> None:
    """R-F895 — record the running loop. Called from the async embedder entry
    points (which run on the main loop) so worker-thread encode failures can
    reach the loop via run_coroutine_threadsafe."""
    global _MAIN_LOOP
    try:
        _MAIN_LOOP = asyncio.get_running_loop()
    except RuntimeError:
        pass


def _report_encode_failure(where: str, exc: Exception) -> None:
    """R-F895 — surface an embedder encode failure to the brain/coder.

    Safe from a sync to_thread worker (no loop) OR the loop thread. Best-effort
    and never raises. `where` is kept COARSE ('add'/'add_batch'/'query') so the
    capability_gaps dedupe window collapses a wedge burst into one gap rather
    than flooding."""
    logger.warning("embedder encode failed [%s]: %s: %s", where, type(exc).__name__, exc)
    try:
        from . import capability_gaps as _cg
        coro = _cg.record_gap(
            gap_type="embedder_failure",
            detail=f"semantic_search encode failed at {where} ({type(exc).__name__})",
            source=f"semantic_search.{where}",
        )
    except Exception:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    try:
        if loop is not None:
            _t = loop.create_task(coro)                         # on the loop thread
            _t.add_done_callback(
                lambda t: t.result() if not t.cancelled() and not t.exception() else None
            )
        elif _MAIN_LOOP is not None and _MAIN_LOOP.is_running():
            asyncio.run_coroutine_threadsafe(coro, _MAIN_LOOP)  # from a worker thread
        else:
            coro.close()                                        # no loop (e.g. test) — avoid "never awaited"
    except Exception:
        try:
            coro.close()
        except Exception:
            pass


def is_embedder_ready() -> bool:
    """R-F459 — non-blocking check. True iff the model is fully loaded.
    Use this from hot paths where you'd rather fall back to TF-IDF
    than block on `_get_embedder()` during cold-window.
    """
    return _embedder is not None


def _get_embedder():
    """Lazy-load the sentence-transformers model (singleton).

    R-F459 (2026-05-14) — race-safe via double-checked locking. The
    fast path (model already loaded) does NOT acquire the lock. The
    slow path (first cold-load) serialises so exactly one constructor
    invocation happens despite N concurrent callers.

    Callers from the asyncio event loop should prefer `aget_embedder()`
    so the load runs in a worker thread.
    """
    global _embedder, _embedder_checked
    # Fast path — no lock when already loaded
    if _embedder is not None:
        return _embedder
    # NOTE: do NOT short-circuit on `_embedder_checked and _embedder is None`
    # outside the lock. Local CI caught this race: thread A enters the slow
    # path, acquires the lock, sets `_embedder_checked=True`, and starts the
    # 32s load while `_embedder` is still None. Concurrent thread B then
    # checks `_embedder_checked AND _embedder is None` → True → returns
    # None to the caller WITHOUT waiting for the load. Caller sees the
    # None and silently degrades to TF-IDF. The check must be INSIDE the
    # lock so it only fires after a genuinely failed load attempt.
    # Slow path — serialise the initialisation
    with _embedder_lock:
        # Re-check inside the lock: another thread may have loaded
        # the model while we were waiting on the lock.
        if _embedder is not None:
            return _embedder
        if _embedder_checked:
            return None
        _embedder_checked = True
        try:
            # R-F857 (2026-05-24) — cap torch intra-op threads to 1 before the
            # model loads. The encoder is GIL-bound; extra OpenMP/torch worker
            # threads both oversubscribe the shared-cpu box AND hold the GIL
            # longer, starving the asyncio event loop during the autonomous
            # absorb storm (the 2026-05-24 wedge). all-MiniLM-L6-v2 is tiny — 1
            # thread suffices and releases the GIL cleaner. Pairs with the
            # ARIA_BRAIN_ABSORB_PAUSE_MS pacer + OMP_NUM_THREADS=1 (fly secret,
            # the pre-import OpenMP lever).
            try:
                import torch as _torch
                _torch.set_num_threads(1)
            except Exception as _torch_err:
                logger.debug("R-F857 torch.set_num_threads(1) skipped: %s", _torch_err)
            from sentence_transformers import SentenceTransformer
            _embedder = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("Loaded sentence-transformers embedding model (all-MiniLM-L6-v2, torch threads=1)")
        except ImportError:
            logger.warning("sentence-transformers not installed — using TF-IDF only")
        except Exception as exc:
            logger.warning("Failed to load embedding model: %s — using TF-IDF only", exc)
        return _embedder


async def aget_embedder():
    """R-F459 — async accessor. Wraps the sync load in asyncio.to_thread
    so the cold-load (~32s) does NOT block the asyncio event loop.

    Use this from any async context that needs the embedder. The sync
    `_get_embedder()` stays available for sync callers AND for callers
    that are already inside a worker thread (e.g. chromadb's embedding
    function wrapper, which is invoked from `asyncio.to_thread` at the
    rag_store layer).

    Idempotent: when the model is already loaded, returns immediately
    without involving the thread pool.
    """
    _capture_main_loop()  # R-F895 — let worker-thread encode failures reach the loop
    if _embedder is not None:
        return _embedder
    # Same race-window note as _get_embedder — do not short-circuit on
    # `_embedder_checked` outside the lock. Let _get_embedder handle
    # the in-progress-load case correctly via the lock.
    import asyncio as _aio
    return await _aio.to_thread(_get_embedder)


async def prewarm_embedder() -> None:
    """R-F459 — lifespan-friendly prewarm. Call this from FastAPI
    lifespan startup BEFORE accepting traffic. Loads the model in a
    worker thread so the 32s cold-load happens during boot rather than
    on the first chat/DD/embed request.

    R-F531 (2026-05-15) — also run a throwaway encode() so the lazy-init
    inside sentence-transformers (tokenizer warm-up, PyTorch graph
    JIT, output-projection allocation, attention buffer allocation)
    happens during lifespan rather than on the first real request.
    Pre-R-F531 evidence: the first encode after model-load cost
    22-28s of cold-CPU time even though the model was already loaded
    in memory. With many concurrent absorbs queued at cold-boot,
    that 22-28s first-encode hogged the GIL, starved the asyncio
    event loop, and the healthcheck timed out → fly PR04 wedge
    (2026-05-15 10:22:51 BST during sanctions refresh).
    Live measured: subsequent encodes are ~50-100ms once the lazy
    paths have fired. The first throwaway encode here pays the
    22-28s cost ONCE, during boot, off the request path.

    Idempotent. Never raises — failures degrade gracefully to TF-IDF
    fallback via the existing `_get_embedder()` error handling.
    """
    _capture_main_loop()  # R-F895 — capture loop at boot prewarm too
    if _embedder is not None:
        return
    try:
        model = await aget_embedder()
    except Exception as exc:
        logger.warning(
            "R-F459 prewarm_embedder failed (non-fatal): %s", exc,
        )
        return
    if model is None:
        return
    # R-F531 — actual encode warmup. Use _safe_encode so the
    # process-wide encode lock semantics are honoured even during
    # prewarm (otherwise a request that arrives during warmup could
    # race the warmup encode and double the cold-cost).
    import asyncio as _aio
    import time as _time
    try:
        t0 = _time.time()
        await _aio.to_thread(
            _safe_encode, model, "warmup",
            normalize_embeddings=True,
        )
        dt = _time.time() - t0
        logger.info(
            "[R-F531] embedder warmup-encode complete in %.2fs (subsequent "
            "encodes should run at ~50-100ms steady-state)", dt,
        )
    except Exception as exc:
        logger.warning(
            "R-F531 warmup-encode failed (non-fatal — first real "
            "encode will pay the cold cost): %s", exc,
        )


# ── Tokenisation ─────────────────────────────────────────────────────────────

def _tokenise(text: str) -> list[str]:
    """Tokenise text into lowercase words, remove stop words."""
    words = re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)*", text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 1]


def _expand_query(tokens: list[str]) -> list[str]:
    """Expand query with synonyms for better recall."""
    expanded = list(tokens)
    for token in tokens:
        if token in _SYNONYMS:
            expanded.extend(_SYNONYMS[token])
    return list(set(expanded))


def _expand_query_text(query: str) -> str:
    """Expand a query string with synonyms, returning enriched text for embedding."""
    tokens = _tokenise(query)
    expanded = _expand_query(tokens)
    extra = set(expanded) - set(tokens)
    if extra:
        return query + " " + " ".join(extra)
    return query


# ── TF-IDF Index ─────────────────────────────────────────────────────────────

class SemanticIndex:
    """In-memory hybrid index: sentence embeddings (primary) + TF-IDF (fallback)."""

    def __init__(self):
        # TF-IDF state
        self._docs: dict[str, dict] = {}  # doc_id -> {text, tokens, tf, metadata}
        self._idf: dict[str, float] = {}
        self._dirty = True
        # Embedding state
        self._embeddings: dict[str, np.ndarray] = {}  # doc_id -> embedding vector
        self._embedding_matrix: np.ndarray | None = None  # stacked matrix for batch search
        self._embedding_ids: list[str] = []  # ordered doc_ids matching matrix rows
        self._matrix_dirty = True

    # ── Document management ─────────────────────────────────────────────

    def add(self, doc_id: str, text: str, metadata: dict = None) -> None:
        """Add or update a document in both TF-IDF and embedding indices."""
        tokens = _tokenise(text)
        if not tokens:
            return
        self._docs[doc_id] = {
            "text": text[:2000],
            "tokens": tokens,
            "tf": Counter(tokens),
            "metadata": metadata or {},
        }
        self._dirty = True

        # Compute embedding if model is available
        model = _get_embedder()
        if model is not None:
            try:
                emb = _safe_encode(model, text[:2000], normalize_embeddings=True)
                self._embeddings[doc_id] = emb
                self._matrix_dirty = True
            except Exception as exc:
                _report_encode_failure("add", exc)  # R-F895

    def add_batch(self, items: list[tuple[str, str, dict | None]]) -> None:
        """Add many docs in a single model.encode() pass.

        F83 fix 2026-04-29: live log showed 15 separate `Batches: 1/1`
        sentence-transformer calls per article ingest (one per fact in
        researcher._process_analysis), because store_fact's tail
        invokes index_fact() per-fact. Routing the same facts through
        this batch helper collapses those into one model.encode() call
        — same shape as rag_store.add_facts_batch (F23) but for the
        in-memory semantic index.

        items: list of (doc_id, text, metadata) tuples. Items with
        empty text or no tokens are silently skipped (matches add()'s
        contract).
        """
        if not items:
            return
        # Phase 1: update TF-IDF state per-item (cheap, no model).
        keep: list[tuple[str, str]] = []
        for doc_id, text, metadata in items:
            if not text:
                continue
            tokens = _tokenise(text)
            if not tokens:
                continue
            self._docs[doc_id] = {
                "text": text[:2000],
                "tokens": tokens,
                "tf": Counter(tokens),
                "metadata": metadata or {},
            }
            keep.append((doc_id, text[:2000]))
        if keep:
            self._dirty = True
        # Phase 2: ONE model.encode() for all kept texts together.
        model = _get_embedder()
        if model is None or not keep:
            return
        ids = [doc_id for doc_id, _ in keep]
        texts = [t for _, t in keep]
        try:
            embeddings = _safe_encode(model, texts, normalize_embeddings=True)
            for doc_id, emb in zip(ids, embeddings):
                self._embeddings[doc_id] = emb
            self._matrix_dirty = True
        except Exception as exc:
            _report_encode_failure("add_batch", exc)  # R-F895

    def remove(self, doc_id: str) -> None:
        if doc_id in self._docs:
            del self._docs[doc_id]
            self._dirty = True
        if doc_id in self._embeddings:
            del self._embeddings[doc_id]
            self._matrix_dirty = True

    # ── Embedding helpers ───────────────────────────────────────────────

    def _rebuild_matrix(self) -> None:
        """Rebuild the stacked embedding matrix for fast batch cosine search."""
        if not self._matrix_dirty:
            return
        if not _NUMPY_AVAILABLE:
            # No numpy in this environment — TF-IDF fallback is the only
            # search path. Clear any stale matrix state.
            self._embedding_matrix = None
            self._embedding_ids = []
            self._matrix_dirty = False
            return
        if not self._embeddings:
            self._embedding_matrix = None
            self._embedding_ids = []
            self._matrix_dirty = False
            return
        self._embedding_ids = list(self._embeddings.keys())
        self._embedding_matrix = np.stack(
            [self._embeddings[did] for did in self._embedding_ids]
        )
        self._matrix_dirty = False

    def _search_embeddings(self, query: str, top_k: int, min_score: float) -> list[dict] | None:
        """Search using sentence embeddings. Returns None if unavailable."""
        if not _NUMPY_AVAILABLE:
            return None
        model = _get_embedder()
        if model is None or not self._embeddings:
            return None

        self._rebuild_matrix()
        if self._embedding_matrix is None:
            return None

        # Expand query with synonyms before encoding
        expanded_query = _expand_query_text(query)
        try:
            q_emb = _safe_encode(model, expanded_query, normalize_embeddings=True)
        except Exception as exc:
            _report_encode_failure("query", exc)  # R-F895
            return None

        # Cosine similarity (embeddings are already L2-normalised)
        scores = self._embedding_matrix @ q_emb

        # Get top-k indices
        if len(scores) <= top_k:
            top_indices = np.argsort(-scores)
        else:
            # Partial sort for efficiency on large indices
            top_indices = np.argpartition(-scores, top_k)[:top_k]
            top_indices = top_indices[np.argsort(-scores[top_indices])]

        results = []
        for idx in top_indices:
            score = float(scores[idx])
            if score < min_score:
                break
            doc_id = self._embedding_ids[idx]
            doc = self._docs.get(doc_id)
            if doc is None:
                continue
            results.append({
                "id": doc_id,
                "score": round(score, 4),
                "text": doc["text"][:500],
                "metadata": doc["metadata"],
            })
        return results

    # ── TF-IDF helpers ──────────────────────────────────────────────────

    def _rebuild_idf(self) -> None:
        """Rebuild IDF scores."""
        if not self._dirty:
            return
        n = len(self._docs)
        if n == 0:
            self._idf = {}
            self._dirty = False
            return

        df = defaultdict(int)
        for doc in self._docs.values():
            for token in set(doc["tokens"]):
                df[token] += 1

        self._idf = {
            token: math.log((n + 1) / (count + 1)) + 1
            for token, count in df.items()
        }
        self._dirty = False

    def _tfidf_vector(self, tf: Counter) -> dict[str, float]:
        """Compute TF-IDF vector for a token frequency counter."""
        vec = {}
        max_tf = max(tf.values()) if tf else 1
        for token, count in tf.items():
            tf_score = 0.5 + 0.5 * (count / max_tf)
            idf_score = self._idf.get(token, 1.0)
            vec[token] = tf_score * idf_score
        return vec

    def _cosine_similarity(self, vec_a: dict, vec_b: dict) -> float:
        """Cosine similarity between two sparse vectors."""
        common = set(vec_a.keys()) & set(vec_b.keys())
        if not common:
            return 0.0
        dot = sum(vec_a[k] * vec_b[k] for k in common)
        mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
        mag_b = math.sqrt(sum(v * v for v in vec_b.values()))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    def _search_tfidf(self, query: str, top_k: int, min_score: float) -> list[dict]:
        """Search using TF-IDF (fallback)."""
        self._rebuild_idf()

        query_tokens = _tokenise(query)
        expanded = _expand_query(query_tokens)
        query_tf = Counter(expanded)
        query_vec = self._tfidf_vector(query_tf)

        if not query_vec:
            return []

        results = []
        for doc_id, doc in self._docs.items():
            doc_vec = self._tfidf_vector(doc["tf"])
            score = self._cosine_similarity(query_vec, doc_vec)
            if score >= min_score:
                results.append({
                    "id": doc_id,
                    "score": round(score, 4),
                    "text": doc["text"][:500],
                    "metadata": doc["metadata"],
                })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    # ── Public search ───────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 10, min_score: float = 0.1) -> list[dict]:
        """Search the index semantically.

        Uses sentence embeddings as primary search method. Falls back to
        TF-IDF when the embedding model is unavailable.
        """
        # Try embedding search first
        results = self._search_embeddings(query, top_k, min_score)
        if results is not None:
            return results
        # Fall back to TF-IDF
        return self._search_tfidf(query, top_k, min_score)

    @property
    def size(self) -> int:
        return len(self._docs)

    @property
    def embedding_count(self) -> int:
        return len(self._embeddings)

    @property
    def has_embeddings(self) -> bool:
        return _get_embedder() is not None and len(self._embeddings) > 0


# ── Global Index ─────────────────────────────────────────────────────────────
_index = SemanticIndex()


def index_fact(fact_id: str, text: str, metadata: dict = None) -> None:
    """Add a fact to the semantic index."""
    _index.add(fact_id, text, metadata)


def index_facts_batch(items: list[tuple[str, str, dict | None]]) -> None:
    """Add many facts to the semantic index in one model.encode pass.

    Used by callers that already have all facts in hand — e.g.
    researcher._process_analysis after parsing an article, read_document
    compliance after analysing a chunk, document_intelligence
    persist_filing after extracting a registry filing. Pair with
    `knowledge.store_fact(skip_semantic_index=True)` so the per-fact
    tail does not double-encode.
    """
    _index.add_batch(items)


def index_neuron(neuron_id: str, concept: str, category: str = "") -> None:
    """Add a neural concept to the semantic index."""
    _index.add(f"neuron:{neuron_id}", f"{concept} {category}", {"type": "neuron"})


def semantic_search(query: str, top_k: int = 10) -> list[dict]:
    """Search across all indexed knowledge semantically."""
    return _index.search(query, top_k)


def get_semantic_context(query: str, max_chars: int = 1500) -> str:
    """Build context string from semantic search for ARIA prompt injection."""
    results = _index.search(query, top_k=8, min_score=0.15)
    if not results:
        return ""

    lines = ["\n\n[SEMANTIC RECALL — related knowledge by meaning]"]
    total = 0
    for r in results:
        line = f"  [{r['score']:.2f}] {r['text'][:200]}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)

    return "\n".join(lines)


def rebuild_index_from_knowledge(facts: list[dict]) -> int:
    """Rebuild the semantic index from the knowledge base."""
    count = 0
    for fact in facts:
        text = f"{fact.get('topic', '')} {fact.get('content', '')}"
        if text.strip():
            _index.add(fact.get("id", str(count)), text, {
                "confidence": fact.get("confidence", "ASSESSED"),
                "source": fact.get("source", ""),
            })
            count += 1
    method = "embeddings" if _index.has_embeddings else "TF-IDF"
    logger.info("Semantic index rebuilt: %d facts indexed (%s)", count, method)
    return count


def get_index_stats() -> dict:
    """Return index size, model status, and backend info."""
    model = _get_embedder()
    return {
        "indexed_documents": _index.size,
        "vocabulary_size": len(_index._idf),
        "embedding_count": _index.embedding_count,
        "embedding_model": "all-MiniLM-L6-v2" if model is not None else None,
        "search_backend": "sentence-transformers" if _index.has_embeddings else "tfidf",
    }


def get_stats() -> dict:
    """Alias for get_index_stats (used by status endpoints)."""
    return get_index_stats()
