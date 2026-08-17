"""
ARIA RAG Store — persistent retrieval-augmented generation backbone.

This is what makes ARIA "know" your proprietary intelligence at runtime
without any model training or vendor LLM upgrades. Every article she
reads, every page she crawls, every image she OCRs, every document she
ingests gets chunked and stored in a local vector database. At query
time she retrieves the most relevant passages and passes them to the
LLM as context — proper RAG, no Pinecone bill, fully local.

Backend: chromadb in PERSISTENT mode (file-backed, no network).
  - Stores at /data/aria_rag if a Fly.io volume is mounted there
  - Falls back to /tmp/aria_rag with a clear warning if not
  - Embeddings: sentence-transformers all-MiniLM-L6-v2 (same model as
    semantic_search.py — already downloaded, no extra weights)

Two collections:
  1. "documents" — raw passages from crawls/articles/OCR/PDFs.
                    The KB grows from every read operation. Chunks have
                    full provenance (source URL, ingest date, doc type).
  2. "facts"     — distilled facts from the existing knowledge base.
                    Backfilled once on first deploy. Stays in sync.

Hybrid retrieval:
  - Semantic similarity (cosine over embeddings)
  - Optional metadata filters (source_type, date range, market)
  - Recency boost on the score
  - Fall through to keyword match if vector search returns nothing

This is the foundation of ARIA-as-product:
  - Customers can drop documents into her knowledge base
  - She can cite sources for every claim
  - She gets smarter with every interaction without retraining
  - Zero vendor lock-in (chromadb + sentence-transformers are both Apache 2.0)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import threading      # R-F3527 — the chromadb client build lock must be cross-THREAD
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.rag")

# ── Configuration ──────────────────────────────────────────────────────────

# Persistent storage path. Fly.io volumes mount at /data by default.
# If /data exists, use it; else fall back to /tmp with a clear warning.
def _resolve_rag_path() -> str:
    override = os.getenv("ARIA_RAG_PATH", "").strip()
    if override:
        return override
    if Path("/data").exists() and os.access("/data", os.W_OK):
        return "/data/aria_rag"
    fallback = "/tmp/aria_rag"
    logger.warning(
        "RAG: /data volume not mounted — falling back to %s. "
        "Index will NOT persist across restarts! "
        "Mount a fly.io volume at /data to enable persistence.",
        fallback,
    )
    return fallback


RAG_PATH = _resolve_rag_path()
DOCUMENTS_COLLECTION = "aria_documents"
FACTS_COLLECTION = "aria_facts"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# Chunking parameters
CHUNK_SIZE = 800        # chars per chunk — tuned for sentence-transformers token limit
CHUNK_OVERLAP = 150     # chars of overlap between chunks for context continuity
MIN_CHUNK_SIZE = 100    # don't store chunks smaller than this

# Retrieval parameters
DEFAULT_TOP_K = 8
DEFAULT_MAX_CONTEXT_CHARS = 6000  # how much retrieved text to inject into LLM prompt

# R-F397 (2026-05-13): minimum similarity floor for RAG injection. Before
# this fix, `search()` had no similarity threshold — whatever chromadb
# returned in the top_k went straight into the LLM context block, even
# at 0.43 cosine similarity (verified by ARIA on 2026-05-13: "Semantic
# recall includes Hezbollah drone content at 0.43 similarity to Arnaldo
# La Scala queries… phonetically matching 'Lebanon'"). A floor of 0.50
# kills the worst phonetic / acronym false-positives without disrupting
# legitimately related content (well-aligned semantic recall hits 0.7+).
# Override via `min_similarity=` kwarg per-call when broader recall is
# wanted (e.g. exploratory crawls).
DEFAULT_MIN_SIMILARITY = 0.50

# R-F173 (2026-05-11) — REVERSED by R-F238 (same day).
# Original R-F173 added an oldest-first PRUNE on cap overflow. That
# violates the "ARIA has infinite memory" operator rule (see
# memory/aria_infinite_memory.md) — she must never forget anything.
#
# R-F238 (2026-05-11) — replace prune with warn-only soft alert.
# The threshold still exists so the operator gets visibility when
# the volume fills, but data is NEVER deleted. Future work: offload-
# to-cold via a second chromadb collection (aria_documents_cold) so
# search latency stays bounded as the hot collection rotates — the
# data still exists, just slower to query. That's the right answer
# under the infinite-memory rule.
RAG_WARN_CHUNKS = int(os.getenv("ARIA_RAG_WARN_CHUNKS", "500000"))
# Check count every N ingests (cheap counter; chromadb count() is O(1))
RAG_PRUNE_CHECK_EVERY = 50
_rag_ingest_counter = 0
_rag_overflow_warned_count = 0  # log throttle on the soft-alert


# ── Lazy chromadb client ───────────────────────────────────────────────────

_client = None
_documents_collection = None
_facts_collection = None
# R-F252 (2026-05-11) — cold-storage collection. When the hot collection
# crosses WARN_CHUNKS, the oldest 10% gets MOVED here (not deleted).
# Search() consults the cold collection at a lower priority (1.5x score
# penalty) so hot results win on relevance but the data remains
# recallable. Per the infinite-memory rule, this is the right answer
# instead of pruning.
_documents_cold_collection = None
_chromadb_failed = False          # PERMANENT — set ONLY when chromadb is not importable
# R-F2151 (2026-06-30) — a RUNTIME init failure (slow/contended disk during the
# boot warmup storm, a transient sqlite lock) used to set _chromadb_failed=True
# PERMANENTLY, disabling RAG/grounding for the entire process life with NO retry.
# Witnessed: RAG sat unavailable for hours because _rag_init_bg's boot+15s probe
# threw while the volume was busy, yet a fresh subprocess init succeeded in 1.2s.
# A transient failure now arms a short cooldown instead, so the next request
# after the boot storm passes re-attempts init and self-heals.
_chromadb_retry_after = 0.0       # monotonic deadline; 0.0 = no cooldown armed

# ── R-F2855 — chromadb-init crash-loop breaker ───────────────────────────────
#
# A native SIGSEGV in chromadb's Rust core (constructing PersistentClient on a
# corrupt store — the 2026-07-22 incident) KILLS the process. try/except cannot
# catch a signal (R-F2808), and the in-memory _chromadb_failed flag dies WITH the
# process, so it can never break a boot crash-loop. A counter FILE on the /data
# volume survives the crash: bump it immediately BEFORE the risky PersistentClient
# call and reset it on success — if the process dies in between, the elevated
# counter tells the next boot to SKIP the construction and boot the brain ALIVE
# (RAG degraded), so the store can be rebuilt while it runs.
#
# Self-healing (auto-skips after N consecutive crashed inits), reversible
# (ARIA_RAG_FORCE_RETRY=1 clears it after a rebuild), manually overridable
# (ARIA_RAG_DISABLED=1 skips unconditionally for immediate incident control). A
# CAUGHT exception is not a segfault and resets the counter — R-F2151's cooldown
# owns transients; the breaker is only for a process that DIED mid-init.
_CRASH_BREAKER_THRESHOLD = int(os.getenv("ARIA_RAG_CRASH_BREAKER_THRESHOLD", "2"))
_CRASH_COUNTER_PATH = str(Path(RAG_PATH).parent / ".chroma_init_crashes")


def _crash_counter_read() -> int:
    try:
        with open(_CRASH_COUNTER_PATH, "r", encoding="utf-8") as fh:
            return max(0, int((fh.read() or "0").strip() or "0"))
    except FileNotFoundError:
        return 0
    except (ValueError, OSError):
        # Unparseable/unreadable → treat as 0 so a corrupt counter never disables
        # a working RAG (the guard must fail OPEN).
        return 0


def _crash_counter_write(n: int) -> None:
    try:
        Path(_CRASH_COUNTER_PATH).parent.mkdir(parents=True, exist_ok=True)
        # write+rename so a crash mid-write can't leave a torn counter
        tmp = _CRASH_COUNTER_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(str(max(0, int(n))))
        os.replace(tmp, _CRASH_COUNTER_PATH)
    except OSError as e:
        logger.warning("[R-F2855] could not persist chroma crash counter: %s", e)



def _ensure_rag_wal(rag_path: str) -> bool:
    """Put chroma's sqlite into WAL before chromadb opens it. Returns True if the
    database is (now) in WAL, False if there was nothing to do or it could not be
    done. NEVER raises.

    R-F4130 (C-165). Measured on aria-intel: `PSI io full = 77.9s of 93 min`,
    meaning EVERY runnable task blocked — the whole process frozen — which is
    §28's unexplained stall signature (50 of 59 dumps showing a bare asyncio
    frame with nothing blocking; nothing blocks in Python because the block is
    beneath it, in the kernel). The app wrote +647 MB of blocks in 45 s
    (~1.24 TB/day) at ~6,175 write syscalls/sec while NO file grew to match, so
    it was rewrite and fsync churn.

    chroma.sqlite3 was the only database on the volume still in rollback-journal
    mode, at 5.08 GB with synchronous=FULL — roughly three fsyncs per commit
    (journal write + fsync, page write + fsync, journal delete + directory
    fsync). `aria_state.db` and `aria_knowledge_store.db` already run WAL on the
    SAME volume, which is what proves the mode works on this filesystem.

    ORDERING IS THE SAFETY PROPERTY. Switching journal_mode requires that no
    other connection holds the database, so this runs after `mkdir` and BEFORE
    `PersistentClient` is constructed. Once chroma exists it holds the file.

    It must also never be a new way to fail: this module documents a native
    SIGSEGV in chromadb's Rust core and carries a crash-loop breaker for it. A
    degraded RAG is survivable; a service that will not boot is not (§9). So
    every path is exception-wrapped, and a short timeout skips a locked database
    rather than waiting on it.
    """
    try:
        import sqlite3
        db = Path(rag_path) / "chroma.sqlite3"
        if not db.exists():
            return False          # fresh volume: chroma has not created it yet
        conn = sqlite3.connect(str(db), timeout=5.0)
        try:
            current = (conn.execute("pragma journal_mode").fetchone()
                       or [""])[0]
            if str(current).lower() == "wal":
                return True       # idempotent: WAL persists in the file header
            result = conn.execute("pragma journal_mode=WAL").fetchone()
            got = str((result or [""])[0]).lower()
            if got == "wal":
                logger.warning(
                    "[R-F4130] chroma sqlite journal_mode %s -> WAL (%s); "
                    "rollback-journal cost ~3 fsyncs per commit on a %.1fGB file "
                    "and froze the event loop",
                    current, db, db.stat().st_size / 1e9)
                return True
            logger.warning(
                "[R-F4130] chroma sqlite would not switch to WAL (still %s) — "
                "the RAG store keeps paying rollback-journal fsyncs", got)
            return False
        finally:
            conn.close()
    except Exception as e:
        # Never block boot on a durability optimisation.
        logger.warning("[R-F4130] could not set WAL on the chroma store: %s", e)
        return False


def _crash_counter_bump() -> None:
    # Must never raise into _get_client — a broken counter must not break RAG init.
    try:
        _crash_counter_write(_crash_counter_read() + 1)
    except Exception as e:  # noqa: BLE001 — guard must fail OPEN
        logger.warning("[R-F2855] crash-counter bump failed (ignored): %s", e)


def _crash_counter_reset() -> None:
    try:
        if _crash_counter_read() != 0:
            _crash_counter_write(0)
    except Exception as e:  # noqa: BLE001 — guard must fail OPEN
        logger.warning("[R-F2855] crash-counter reset failed (ignored): %s", e)


def _crash_breaker_should_skip() -> bool:
    """True iff chromadb init must be SKIPPED (never construct the client)."""
    if os.getenv("ARIA_RAG_FORCE_RETRY", "").strip().lower() in ("1", "true", "yes", "on"):
        _crash_counter_reset()          # operator forced a retry after a rebuild
        return False
    if os.getenv("ARIA_RAG_DISABLED", "").strip().lower() in ("1", "true", "yes", "on"):
        return True                     # manual kill-switch — immediate incident control
    try:
        crashes = _crash_counter_read()
    except Exception:                   # noqa: BLE001 — guard must fail OPEN
        return False
    if crashes >= _CRASH_BREAKER_THRESHOLD:
        logger.error(
            "[R-F2855] chromadb init SKIPPED — %d consecutive crashed inits at %s "
            "(threshold %d). RAG is DEGRADED but the brain is ALIVE. Rebuild the "
            "store (scripts/admin/rebuild_rag_collection.py) then set "
            "ARIA_RAG_FORCE_RETRY=1.", crashes, RAG_PATH, _CRASH_BREAKER_THRESHOLD,
        )
        return True
    return False


# ── R-F2856 — per-collection corruption SELF-HEAL ────────────────────────────
# When the R-F2855 breaker trips, RAG is degraded because ONE collection's HNSW
# segfaults on query — but the breaker takes ALL of RAG down with it and needs an
# operator to identify + quarantine the bad one by hand (done manually 2026-07-22).
# This automates that: probe each collection in a SUBPROCESS (a SIGSEGV there cannot
# kill the brain), quarantine ONLY a collection that DEFINITIVELY + REPRODUCIBLY
# segfaults (never on a timeout/error — ambiguous), clear the breaker, and re-init so
# the HEALTHY collections come back up. No human, no restart, no data deleted (§7).
# 60s headroom: a large HEALTHY collection cold-loads its HNSW slowly (live-measured
# ~32s for aria_facts/451K vectors), while a CORRUPT one SIGSEGVs fast on load start —
# so the bound comfortably clears a healthy load without delaying corruption detection.
_PROBE_TIMEOUT_S = float(os.getenv("ARIA_RAG_PROBE_TIMEOUT_S", "60"))

# Runs in a fresh subprocess: open the store, load ONE collection's HNSW via a dummy
# vector query (query_embeddings bypasses the embedder — no torch load), and print
# HEALTHY. A corrupt index SIGSEGVs here (exit -11/139); the parent reads that.
_PROBE_SRC = (
    "import sys, os\n"
    "name, path = sys.argv[1], sys.argv[2]\n"
    "import chromadb\n"
    "c = chromadb.PersistentClient(path=path)\n"
    "col = c.get_collection(name)\n"
    "col.query(query_embeddings=[[0.0]*384], n_results=1)\n"
    # The query SURVIVED (a corrupt HNSW would have SIGSEGV'd above). Flush the
    # verdict then os._exit(0) — a normal exit hangs joining torch/onnx threads
    # (live-observed: healthy probe printed HEALTHY then timed out on cleanup),
    # which would false-time-out a healthy collection to 'unknown'.
    "print('HEALTHY'); sys.stdout.flush()\n"
    "os._exit(0)\n"
)


def _list_collection_names_via_sqlite() -> list[str]:
    """Collection names read straight from chroma.sqlite3 — NEVER loads an HNSW
    segment, so it is safe even when a collection is corrupt."""
    import sqlite3
    db_path = str(Path(RAG_PATH) / "chroma.sqlite3")
    con = sqlite3.connect(db_path)
    try:
        return [r[0] for r in con.execute("select name from collections")]
    finally:
        con.close()


def _probe_collection_isolated(name: str) -> str:
    """Probe ONE collection in a subprocess. Returns 'healthy' | 'corrupt' | 'unknown'.

    'corrupt' is returned ONLY on a native crash signal (SIGSEGV = returncode -11, or
    128+11=139). A timeout or any other non-zero exit is 'unknown' — deliberately NOT
    'corrupt', because those are ambiguous (slow disk, empty/dim-mismatch) and must
    never trigger a quarantine.
    """
    import subprocess
    import sys
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE_SRC, name, RAG_PATH],
            timeout=_PROBE_TIMEOUT_S, capture_output=True,
        )
    except subprocess.TimeoutExpired:
        return "unknown"
    except Exception:                       # noqa: BLE001 — a broken probe is not proof of corruption
        return "unknown"
    if proc.returncode in (-11, 139):       # SIGSEGV — a corrupt HNSW crashed the reader
        return "corrupt"
    if proc.returncode == 0 and b"HEALTHY" in (proc.stdout or b""):
        return "healthy"
    return "unknown"


def _quarantine_collection(name: str) -> str:
    """Rename a collection aside at the sqlite metadata layer (R-F2799 technique —
    does NOT load the corrupt segment). Preserves the data; never deletes (§7).
    Returns the parked name."""
    import sqlite3
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    parked = f"{name}__corrupt_{ts}"
    db_path = str(Path(RAG_PATH) / "chroma.sqlite3")
    con = sqlite3.connect(db_path)
    try:
        con.execute("update collections set name=? where name=?", (parked, name))
        con.commit()
    finally:
        con.close()
    logger.warning("[R-F2856] quarantined corrupt collection %s -> %s (parked, NOT deleted)",
                   name, parked)
    return parked


@fail_wire(module="rag_store", gap_type="embedder_failure")
def diagnose_and_heal_corrupt_collections(
    *, probe_fn=None, quarantine_fn=None, reinit_fn=None, reproduce: int = 2,
) -> dict:
    """Find the collection(s) that segfault, quarantine ONLY those, and heal RAG.

    Called when the breaker has tripped (RAG degraded). Safe to run with the store
    open by nobody — the degraded path leaves _client None. `reproduce` is how many
    consecutive segfaults are required before a collection is deemed DEFINITIVELY
    corrupt (guards against a one-off). Injectable fns keep it unit-testable.
    """
    probe_fn = probe_fn or _probe_collection_isolated
    quarantine_fn = quarantine_fn or _quarantine_collection
    reinit_fn = reinit_fn or _get_client
    result: dict = {"probed": [], "corrupt": [], "quarantined": [], "healed": False, "errors": []}
    try:
        names = _list_collection_names_via_sqlite()
    except Exception as e:                  # noqa: BLE001
        result["errors"].append(f"list collections failed: {e}")
        return result

    for name in names:
        if "__corrupt_" in name:            # already parked — never re-probe/re-park
            continue
        verdicts: list[str] = []
        for _ in range(max(1, reproduce)):
            v = probe_fn(name)
            verdicts.append(v)
            if v != "corrupt":              # not corrupt this round -> not definitive; stop early
                break
        result["probed"].append({"name": name, "verdicts": verdicts})
        # DEFINITIVE only: every one of `reproduce` probes must have segfaulted.
        if len(verdicts) >= reproduce and all(v == "corrupt" for v in verdicts):
            result["corrupt"].append(name)
            try:
                parked = quarantine_fn(name)
                result["quarantined"].append({"name": name, "parked": parked})
            except Exception as e:          # noqa: BLE001
                result["errors"].append(f"quarantine {name} failed: {e}")

    if result["quarantined"]:
        # The corrupt collection(s) are parked — clear the breaker and re-init so the
        # HEALTHY collections come back UP (a fresh empty collection replaces each
        # parked one, exactly as the manual 2026-07-22 recovery did).
        _crash_counter_reset()
        global _client, _documents_collection, _facts_collection
        global _documents_cold_collection, _chromadb_failed, _chromadb_retry_after
        _client = None
        _documents_collection = None
        _facts_collection = None
        _documents_cold_collection = None
        _chromadb_failed = False
        _chromadb_retry_after = 0.0
        try:
            result["healed"] = reinit_fn() is not None
        except Exception as e:              # noqa: BLE001
            result["errors"].append(f"re-init failed: {e}")
    return result


_CHROMADB_RETRY_COOLDOWN_S = float(os.getenv("ARIA_RAG_RETRY_COOLDOWN_S", "60"))
_init_lock = asyncio.Lock()

# ── R-F3527 — construct the chromadb client under a THREAD lock ──────────────
#
# THE INCIDENT (2026-07-30): aria-intel crash-looped on SIGSEGV (exit_code=139) every
# ~70-150s, surviving three deploys and the pausing of DD reconcile + autonomous work.
# `PYTHONFAULTHANDLER=1` named it:
#
#     rag_store.py:498 in _get_client
#       chromadb/__init__.py:228 in PersistentClient
#         client.py:105 __init__ -> 641/650 _validate_tenant -> 721 get_tenant
#           chromadb/api/rust.py:175 in get_tenant          <- Rust core
#     ...and on ANOTHER THREAD, concurrently:
#       chromadb/api/shared_system_client.py:124
#         chromadb/config.py:473 in stop
#           chromadb/api/rust.py:131 in stop                <- Rust core
#
# One thread CONSTRUCTING while another STOPS the shared system. chromadb keys its
# systems by path in `SharedSystemClient._identifier_to_system`; a second concurrent
# `PersistentClient(path=...)` for the same path tears down / re-enters a system the
# first is still inside, and the Rust core dereferences freed state.
#
# `_get_client` is SYNC and had no mutual exclusion. `_init_lock` above is an
# asyncio.Lock guarding a DIFFERENT (async) path — it cannot serialise sync callers,
# and an asyncio lock protects nothing across threads anyway. The process runs ~25
# threads (7 executor workers + 7 aiosqlite workers + uvicorn), so first-touch from
# two of them at once is not a rare interleaving; it is the ordinary boot pattern
# once several subsystems warm RAG together.
#
# WHY R-F2855 DID NOT SAVE US, which matters as much as the race itself. Its counter
# is bumped before construction and RESET ON SUCCESS. The first init succeeds and
# resets to 0; the racing second construction then dies with the counter at 1 — below
# the threshold of 2. It oscillates 0<->1 forever and can never trip. That is exactly
# why `/data/.chroma_init_crashes` read 0 on a box that was crash-looping, and why the
# breaker's own "prevention, not error handling" promise silently did not apply.
# The breaker is still correct for a genuinely corrupt store; it simply cannot see a
# CONCURRENCY fault, so the concurrency has to be removed at the source.
_client_build_lock = threading.Lock()


class _SharedSentenceTransformerEmbeddingFn:
    """Chromadb-compatible embedding function backed by the shared
    sentence-transformers singleton in semantic_search.

    Replaces chromadb.utils.embedding_functions.SentenceTransformer-
    EmbeddingFunction to eliminate the second model load (F79). Output
    matches chromadb's default settings — no normalize_embeddings — so
    queries against pre-existing collection vectors continue to score
    identically to before this change.
    """

    def __init__(self, model_name: str):
        self._model_name = model_name

    def __call__(self, input):  # noqa: A002 — chromadb protocol uses `input`
        texts = list(input)
        # R-F2044 — route the chromadb embed through the PROCESS-OFFLOADED
        # encoder FIRST. This was the recurring loop-wedge root cause (captured
        # live in /data/wedge_stacks): a RAG query → this __call__ → the OLD
        # `_get_embedder()` below COLD-LOADED the in-process SentenceTransformer
        # on the request path (10s+, under the event loop), and `_safe_encode`
        # then ran an in-process GIL-bound `model.encode()` because chromadb
        # passes convert_to_numpy=True (not in _safe_encode's {normalize_embeddings}
        # offload allow-set) — so RAG-query encodes NEVER offloaded and stalled
        # the loop 6-11s at a time, starving WA chat dispatch + web SSE. With
        # offload ON (default) the in-process model is deliberately NOT prewarmed
        # (main.py R-F1890) — so this path was guaranteed to cold-load. The
        # offload worker owns a warmed model in its OWN process: no in-process
        # load, no main-GIL encode. normalize=False matches chromadb's default
        # (and the in-process fallback) so vectors stay identical to the existing
        # persisted collection embeddings.
        try:
            from . import encode_offload as _eo
            if _eo.is_enabled():
                return _eo.encode(texts, normalize=False).tolist()
        except Exception as _off_err:
            # §21a/§25 — offload genuinely ERRORED (not merely disabled, which
            # raises nothing here). Surface the encode-offload degradation to the
            # brain so ARIA sees her embedding limb weakening, then fall back
            # in-process below. Deduped by capability_gaps; never raises.
            try:
                from .engine_wiring import wire_failure
                wire_failure(
                    module="rag_store",
                    detail=f"encode-offload degraded on RAG embed: {type(_off_err).__name__}: {_off_err}",
                    gap_type="embedder_failure",
                    source="rag_store:R-F2044",
                )
            except Exception:
                pass
        from .semantic_search import _get_embedder, _safe_encode
        model = _get_embedder()
        if model is None:
            raise RuntimeError(
                "Shared sentence-transformer model unavailable — "
                "semantic_search._get_embedder() returned None. RAG "
                "init should have been gated upstream."
            )
        # chromadb default: list[str] → list[list[float]] without
        # normalize_embeddings (cosine HNSW handles it index-side).
        # R-F530 — route through _safe_encode so the chromadb path
        # serialises against the semantic_search path's encode calls.
        return _safe_encode(model, texts, convert_to_numpy=True).tolist()

    @fail_wire(module="rag_store", gap_type="embedder_failure")
    def embed_query(self, input: str | list[str]) -> list[list[float]]:
        """chromadb 1.5+ query protocol — delegates to __call__.

        R-F1532: chromadb 1.5+ requires ``embed_query`` on the embedding
        function for query operations. Without this, every ``search()``
        call raises AttributeError and returns 0 results — which is why
        the open-book eval (build_openbook_eval.py) got 0 grounded
        contexts despite 202K chunks in the store.
        """
        if isinstance(input, str):
            input = [input]
        return self.__call__(input)

    @fail_wire(module="rag_store", gap_type="embedder_failure")
    def name(self) -> str:
        # MUST match chromadb's built-in SentenceTransformerEmbedding-
        # Function.name() ("sentence_transformer") — chromadb 0.5+
        # persists this in collection metadata and refuses (or warns
        # loudly about) a mismatched embedder on subsequent boots.
        # Returning the same name makes this a drop-in replacement
        # against existing prod collections.
        return "sentence_transformer"


def _get_client():
    """Lazy-load the chromadb client, serialising CONSTRUCTION across threads.

    R-F3527 — two threads constructing `PersistentClient` for the same path at once
    segfaults chromadb's Rust core (see `_client_build_lock`). The fast path stays
    lock-free so the ~every-call hot path costs nothing once the client exists; only
    the build is serialised, and it is re-checked INSIDE the lock because a thread
    that waited may find the client already built by the winner.

    Deliberately a wrapper rather than an inline `with` around the body: the build is
    a long function and re-indenting it wholesale is how subtle damage gets in.
    """
    if (_client is not None and _documents_collection is not None
            and _facts_collection is not None):
        return _client
    with _client_build_lock:
        # Re-check under the lock — the winner may have finished while we waited.
        if (_client is not None and _documents_collection is not None
                and _facts_collection is not None):
            return _client
        return _get_client_unlocked()


def _get_client_unlocked():
    """Lazy-load the chromadb persistent client + collections.

    CALLER MUST HOLD `_client_build_lock` (R-F3527). Never call this directly — a
    second concurrent entry is the use-after-free that crash-looped production.

    All three globals (_client, _documents_collection, _facts_collection)
    must succeed together or we roll back to None — otherwise callers can
    see `_client is not None` but hit `_documents_collection.upsert`
    against None, which is the production crash the audit fixed.
    """
    global _client, _documents_collection, _facts_collection, _documents_cold_collection, _chromadb_failed, _chromadb_retry_after
    if _client is not None and _documents_collection is not None and _facts_collection is not None:
        return _client
    if _chromadb_failed:           # chromadb not importable — never retry
        return None
    # R-F2151 — a prior TRANSIENT failure armed a cooldown; don't hammer init on
    # every request while the disk is still busy. Once the cooldown elapses, fall
    # through and re-attempt — this is what lets RAG self-heal after the boot storm.
    if _chromadb_retry_after and time.monotonic() < _chromadb_retry_after:
        return None

    # R-F2855 — crash-loop breaker: never CONSTRUCT the client if the last inits
    # segfaulted. This is prevention, not error handling — a SIGSEGV can't be caught.
    if _crash_breaker_should_skip():
        _chromadb_failed = True         # route all callers to the degraded path
        return None

    # Local vars — commit to globals only if every step succeeds.
    local_client = None
    local_docs = None
    local_facts = None
    _crash_counter_bump()               # persist "init in progress" BEFORE the risky
                                        # native call; a segfault here leaves it set.
    try:
        import chromadb
        from chromadb.config import Settings
        Path(RAG_PATH).mkdir(parents=True, exist_ok=True)
        # R-F4130 (C-165) — WAL before chroma opens the file. After
        # PersistentClient exists, chroma holds it and the switch cannot be made.
        _ensure_rag_wal(RAG_PATH)
        local_client = chromadb.PersistentClient(
            path=RAG_PATH,
            settings=Settings(anonymized_telemetry=False, allow_reset=False),
        )
        # F79 2026-04-29: previously chromadb instantiated its OWN
        # SentenceTransformer via SentenceTransformerEmbeddingFunction,
        # parallel to semantic_search._get_embedder(). Two model copies
        # in memory, two HF HEAD-flood passes on cold start (~25 reqs
        # each), ~3s extra cold-boot, and 5–10× slower encode batches
        # once both loaded (witnessed during the F50 boot cascade).
        # _SharedSentenceTransformerEmbeddingFn delegates to the
        # semantic_search singleton so there's exactly one model in
        # the process. Output is byte-for-byte compatible with the
        # previous chromadb default (normalize_embeddings=False, same
        # all-MiniLM-L6-v2 weights), so existing collection vectors
        # match newly-encoded queries.
        embed_fn = _SharedSentenceTransformerEmbeddingFn(EMBEDDING_MODEL_NAME)
        local_docs = local_client.get_or_create_collection(
            name=DOCUMENTS_COLLECTION,
            embedding_function=embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        local_facts = local_client.get_or_create_collection(
            name=FACTS_COLLECTION,
            embedding_function=embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        # R-F252 (2026-05-11) — cold-storage collection. Created lazily
        # so existing deployments don't pay an extra collection init
        # until offload actually fires. Same embedding fn so queries
        # against cold are byte-compatible with hot.
        local_cold = local_client.get_or_create_collection(
            name=DOCUMENTS_COLLECTION + "_cold",
            embedding_function=embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        # R-F2798 — do NOT count the collections here. `.count()` on documents/
        # facts is an O(collection-size) NATIVE scan (~38s cold over ~215K chunks);
        # R-F1911 established exactly that at :1488 and memoised it behind a TTL
        # cache for /health, but missed this call site. So every RAG client init
        # paid a full native scan purely to print three numbers.
        #
        # It was also FATAL: on a collection whose native scan faults, the scan
        # takes the whole process down with a Windows access violation — which a
        # Python try/except cannot catch. That silently killed the binding
        # CLAUDE.md §20 coding-RAG priming step (the process exits 0 with no
        # output unless faulthandler is on), the second silent failure in that
        # step after R-F2623.
        #
        # The counts are slowly-changing diagnostics and are already exposed,
        # cached and single-flight, via get_stats(). Log what init actually
        # established — that the store opened and which collections exist —
        # and let anyone who wants numbers pay for them through the cached path.
        #
        # ★ R-F2808 — READ THIS BEFORE CONCLUDING THE CRASH CLASS IS CLOSED.
        # Removing the counts MOVED the native fault out of boot; it did not
        # remove it. A collection whose HNSW index is corrupt will still fault on
        # the first query()/get() from a real RAG search — mid-request, which is
        # a WORSE place to lose the process than at boot, because it is
        # nondeterministic and user-facing. The actual structural fix is to
        # repair the collection: scripts/admin/rebuild_rag_collection.py
        # (R-F2799 rebuild / R-F2800 purge). If you see a native access violation
        # in a RAG path, rebuild the collection — do not add error handling here,
        # because a Windows access violation cannot be caught by try/except.
        logger.info(
            "RAG store ready at %s — collections: %s, %s, %s (counts via get_stats)",
            RAG_PATH, local_docs.name, local_facts.name, local_cold.name,
        )
    except ImportError:
        _crash_counter_reset()      # R-F2855 — not a segfault; a clean import failure
        _chromadb_failed = True     # PERMANENT — package missing, retrying is pointless
        logger.warning("chromadb not installed — RAG store unavailable. Run: pip install chromadb")
        return None
    except Exception as e:
        # R-F2151 — TRANSIENT failure (disk contention / sqlite lock during the
        # boot warmup storm). Arm a cooldown instead of disabling RAG forever;
        # the next request after the cooldown re-attempts and self-heals.
        _crash_counter_reset()      # R-F2855 — a CAUGHT exception is not a segfault;
                                    # R-F2151's cooldown owns transients.
        _chromadb_retry_after = time.monotonic() + _CHROMADB_RETRY_COOLDOWN_S
        logger.warning(
            "RAG store init failed (transient) — will retry after %.0fs: %s",
            _CHROMADB_RETRY_COOLDOWN_S, e, exc_info=True,
        )
        return None

    # R-F2855 — init completed without a native crash: clear the breaker.
    _crash_counter_reset()
    # Commit atomically.
    _client = local_client
    _documents_collection = local_docs
    _facts_collection = local_facts
    _documents_cold_collection = local_cold
    _chromadb_retry_after = 0.0     # R-F2151 — init recovered; clear any armed cooldown
    return _client


def _ensure() -> bool:
    """Ensure the RAG store is initialised. Returns True if ready.

    Sync version — only safe to call from already-initialised code
    paths. For async paths (ingest / search / context build), prefer
    `await _ensure_async()` so the expensive first-call chromadb +
    sentence-transformers init happens in a thread and does not pin
    the event loop. Production incident: first request after startup
    used to block uvicorn for ~5 min while the 200MB embedding model
    downloaded + initialised on the loop thread.
    """
    return _get_client() is not None


async def _ensure_async() -> bool:
    """Async-safe init. Runs the expensive first-call chromadb setup
    in a worker thread. Subsequent calls are cheap — `_get_client`
    short-circuits once `_client is not None`.

    Returns True only when BOTH _client AND the two collections are
    non-None, because _get_client sets _client before it creates the
    collections — a partial init (collection create fails) would
    otherwise leave callers looking at None.upsert.
    """
    global _client, _documents_collection, _facts_collection
    if _client is not None and _documents_collection is not None and _facts_collection is not None:
        return True
    if _chromadb_failed:           # chromadb not importable — never retry
        return False
    # R-F2151 — respect the transient-failure cooldown so a poll burst doesn't
    # re-attempt init every request while the disk is still busy.
    if _chromadb_retry_after and time.monotonic() < _chromadb_retry_after:
        return False
    import asyncio as _aio
    try:
        async with _init_lock:
            if _client is not None and _documents_collection is not None and _facts_collection is not None:
                return True
            await _aio.to_thread(_get_client)
    except Exception as e:
        logger.warning("RAG store async init failed: %s", e)
        return False
    ok = _client is not None and _documents_collection is not None and _facts_collection is not None
    if not ok:
        logger.warning(
            "RAG store partial init: client=%s docs=%s facts=%s",
            _client is not None, _documents_collection is not None, _facts_collection is not None,
        )
    return ok


# ── Chunking ───────────────────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks aligned on sentence boundaries
    where possible. Each chunk is ~chunk_size chars, with `overlap` chars
    of context bleed between consecutive chunks.

    Short non-empty inputs (>=20 chars) return a single chunk — previously
    anything below MIN_CHUNK_SIZE (100 chars) was dropped, which meant short
    emails ("Confirmed, see you Tuesday.") vanished from RAG entirely.
    Incident 2026-04-21: 0 email-tagged RAG chunks despite 22 absorb signals.
    """
    if not text:
        return []
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        # Keep anything non-trivially sized — even short emails / one-liner
        # notes belong in RAG so they're searchable later.
        return [text] if len(text) >= 20 else []

    chunks: list[str] = []
    pos = 0
    while pos < len(text):
        end = min(pos + chunk_size, len(text))
        # If we're not at the very end, try to break on a sentence boundary
        # within the last 25% of the chunk
        if end < len(text):
            min_break = pos + int(chunk_size * 0.75)
            for punct in (". ", "! ", "? ", ".\n", "!\n", "?\n", "\n\n"):
                idx = text.rfind(punct, min_break, end)
                if idx > 0:
                    end = idx + len(punct)
                    break
            else:
                # No sentence boundary — fall back to word boundary
                idx = text.rfind(" ", min_break, end)
                if idx > 0:
                    end = idx
        chunk = text[pos:end].strip()
        if len(chunk) >= MIN_CHUNK_SIZE:
            chunks.append(chunk)
        # Advance with overlap
        pos = end - overlap if end - overlap > pos else end
    return chunks


def _hash_id(text: str, source: str = "") -> str:
    """Stable deterministic ID for a chunk so re-ingesting the same content
    is idempotent (chromadb upserts on existing IDs)."""
    h = hashlib.sha256(f"{source}|{text}".encode("utf-8", errors="replace")).hexdigest()
    return h[:32]


# ── Public API: ingest ─────────────────────────────────────────────────────

# ── R-F410 (2026-05-13) — RAG corpus sanitization ─────────────────────────
# Pre-R-F410 email/webhook content landed in chromadb without HTML strip,
# control-char filter, or UTF-8 validation. Adversarial fact-teaching
# surface: an attacker could send an email with raw HTML that included
# styled "facts" or hidden zero-width characters that ARIA would later
# return in chat. Now every ingest path runs text through
# _sanitize_ingest_text() before chunking / upserting. Conservative:
# strip HTML, drop control chars except \n/\t/\r, replace invalid UTF-8,
# cap at 1MB per ingest call to prevent OOM attacks.

_RAG_INGEST_MAX_BYTES = 1_048_576  # 1 MB hard cap

# Lazy-loaded BeautifulSoup; fall back to regex if bs4 missing.
_HTML_TAG_RE = __import__("re").compile(r"<[^>]+>")
_HTML_DANGEROUS_BLOCK_RE = __import__("re").compile(
    r"(?is)<(script|style|noscript)\b[^>]*>.*?</\1>"
)
_CONTROL_CHARS_RE = __import__("re").compile(
    # All C0/C1 control chars EXCEPT \n (\x0a), \r (\x0d), \t (\x09)
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
)
# Zero-width / formatting chars that can hide injected text from human
# review but get parsed as regular text by the LLM. Includes ZWSP/ZWNJ/
# ZWJ/word joiner/BOM/LRM/RLM/etc.
_ZERO_WIDTH_RE = __import__("re").compile(
    r"[\u200b\u200c\u200d\u2060\ufeff\u200e\u200f\u202a-\u202e]"
)


def _sanitize_ingest_text(text: str) -> tuple[str, dict]:
    """Clean text before chunking/upsert into the RAG store.

    Returns (sanitized_text, meta) where meta is:
      {"html_stripped": int, "control_chars": int, "zero_width": int,
       "invalid_utf8_replaced": bool, "truncated_bytes": int}

    Conservative — strips HTML tags via bs4 (or regex fallback), drops
    control chars except \\n/\\r/\\t, removes zero-width formatting
    chars, replaces invalid UTF-8 with U+FFFD, and caps at 1 MB.
    """
    meta = {
        "html_stripped": 0,
        "control_chars": 0,
        "zero_width": 0,
        "invalid_utf8_replaced": False,
        "truncated_bytes": 0,
    }
    if not text:
        return "", meta

    # 1. UTF-8 validate — replace invalid sequences
    if isinstance(text, bytes):
        try:
            text.decode("utf-8")
        except UnicodeDecodeError:
            meta["invalid_utf8_replaced"] = True
        text = text.decode("utf-8", errors="replace")
    else:
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            meta["invalid_utf8_replaced"] = True
            text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")

    # 2. Length cap (BEFORE expensive parsing). 1 MB is the hard limit
    #    per ingest call; longer content is truncated with a marker.
    raw_bytes = text.encode("utf-8", errors="replace")
    if len(raw_bytes) > _RAG_INGEST_MAX_BYTES:
        meta["truncated_bytes"] = len(raw_bytes) - _RAG_INGEST_MAX_BYTES
        text = raw_bytes[:_RAG_INGEST_MAX_BYTES].decode("utf-8", errors="ignore")
        text += "\n\n[!RAG_INGEST_TRUNCATED]"

    # 3. HTML strip — prefer bs4 (preserves text properly across tags);
    #    fall back to regex if bs4 isn't present (it should be — see
    #    requirements.txt — but defensive).
    if "<" in text and ">" in text:
        try:
            text = _HTML_DANGEROUS_BLOCK_RE.sub(" ", text)
            from bs4 import BeautifulSoup  # type: ignore
            soup = BeautifulSoup(text, "lxml")
            # Drop <script>, <style>, <noscript> entirely (their text
            # is never meaningful for RAG and may be attack vector).
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            new_text = soup.get_text(separator=" ")
            meta["html_stripped"] = max(0, len(text) - len(new_text))
            text = new_text
        except Exception:
            # Regex fallback — less precise but always works
            text = _HTML_DANGEROUS_BLOCK_RE.sub(" ", text)
            new_text = _HTML_TAG_RE.sub(" ", text)
            meta["html_stripped"] = max(0, len(text) - len(new_text))
            text = new_text

    # 4. Control-char strip (except \n \r \t)
    cc_count = len(_CONTROL_CHARS_RE.findall(text))
    if cc_count:
        meta["control_chars"] = cc_count
        text = _CONTROL_CHARS_RE.sub("", text)

    # 5. Zero-width / direction-override strip
    zw_count = len(_ZERO_WIDTH_RE.findall(text))
    if zw_count:
        meta["zero_width"] = zw_count
        text = _ZERO_WIDTH_RE.sub("", text)

    # 6. Collapse runs of whitespace — HTML strip often leaves "       "
    text = __import__("re").sub(r" {2,}", " ", text)
    text = __import__("re").sub(r"\n{3,}", "\n\n", text)
    return text.strip(), meta


def _url_candidate(value: str | None) -> str:
    """Return value only when it is a direct HTTP(S) URL."""
    raw = str(value or "").strip()
    if raw.lower().startswith(("http://", "https://")):
        return raw
    return ""


async def _memory_ingest_allowed(
    *,
    url: str = "",
    source: str = "",
    credibility_tier: int | None = None,
) -> tuple[bool, str]:
    """Gate URL-backed memory writes through source_validator.

    Non-URL documents are first-party/manual/corpus memory and are left alone.
    URL-backed generic web memory must pass source_validator's low-value-domain
    rule before it enters the persistent RAG document or fact collections.
    """
    candidate_url = _url_candidate(url) or _url_candidate(source)
    if not candidate_url:
        return True, "no_url"
    try:
        from . import source_validator as _sv
        return await _sv.memory_ingest_allowed(candidate_url, credibility_tier)
    except Exception:
        return False, "source_validator_unavailable"


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def ingest_document(
    text: str,
    *,
    source: str,
    source_type: str = "unknown",
    title: str = "",
    url: str = "",
    market: str = "",
    extra_metadata: dict | None = None,
    data_subject_key: str = "",
    lawful_basis: str = "",
    retention_class: str = "",
    data_jurisdiction: str = "",
    personal_data: bool = False,
    owner_key: str = "",
) -> dict:
    """Chunk a document and add it to the RAG store.

    Args:
        text: Raw document text.
        source: Where it came from (e.g. "crawl:wearwiser.com", "ocr:invoice.jpg").
        source_type: One of "article", "crawl", "ocr", "pdf", "manual", "ledger".
        title: Display title for the source (page title, filename, etc).
        url: Original URL if applicable.
        market: Country/region tag for filtering.
        extra_metadata: Anything else to attach to every chunk.
    """
    tier = (extra_metadata or {}).get("credibility_tier")
    allowed, gate_reason = await _memory_ingest_allowed(
        url=url,
        source=source,
        credibility_tier=tier,
    )
    if not allowed:
        logger.info(
            "RAG document ingest skipped unapproved low-value source %s (%s)",
            (url or source)[:120], gate_reason,
        )
        return {
            "ingested": False,
            "reason": gate_reason,
            "source": source,
            "url": url,
        }
    if not await _ensure_async():
        return {"ingested": False, "error": "rag_store_unavailable"}
    # R-F410 (2026-05-13) — sanitize BEFORE the length check so HTML
    # like "<p>Hi</p>" (which strips to ~2 chars) is correctly rejected
    # as too short rather than passing the gate as raw HTML.
    text, _sanitize_meta = _sanitize_ingest_text(text or "")
    # Floor lowered from MIN_CHUNK_SIZE (100) to 20 chars — short emails like
    # "Confirmed — see you Tuesday. — John" must still land in RAG so ARIA can
    # recall them later. Incident 2026-04-21: 0 email-tagged RAG chunks.
    if not text or len(text.strip()) < 20:
        return {
            "ingested": False,
            "reason": "text_too_short",
            "sanitization": _sanitize_meta,  # operator-visibility
        }

    chunks = _chunk_text(text)
    if not chunks:
        return {"ingested": False, "reason": "no_chunks_produced"}

    now_iso = datetime.now(timezone.utc).isoformat()
    ts_epoch = time.time()
    base_meta = {
        "source": source[:300],
        "source_type": source_type,
        "title": (title or "")[:300],
        "url": (url or "")[:500],
        "market": (market or "")[:100],
        "ingested_at": now_iso,
        "ts_epoch": ts_epoch,
    }
    # ── R-F3488 — the data-subject envelope ────────────────────────────────
    #
    # R-F3484 gave ARIA provable, exact erasure keyed on `data_subject_key`. It reached
    # nothing, because no write path carried one: a capability with no caller is a
    # dormant specification, which is the defect this same session criticised elsewhere.
    #
    # Personal data stored WITHOUT a subject key cannot be erased on request — only
    # swept best-effort, which cannot prove completeness. That is a fact about the
    # record, so it is recorded ON the record. `erasure_reachable` lets a controller
    # ANSWER "what personal data do we hold that we could not erase if asked?" instead
    # of discovering it during a request.
    #
    # The write is NOT refused: refusing would drop a customer's document to enforce a
    # metadata rule, which trades a data-protection gap for data loss. It is made
    # VISIBLE instead — logged, wired to the brain, and flagged on the record.
    if data_subject_key:
        base_meta[DATA_SUBJECT_KEY] = str(data_subject_key)[:200]
    if lawful_basis:
        base_meta[LAWFUL_BASIS_KEY] = str(lawful_basis)[:80]
    if retention_class:
        base_meta[RETENTION_CLASS_KEY] = str(retention_class)[:80]
    if data_jurisdiction:
        base_meta[DATA_JURISDICTION_KEY] = str(data_jurisdiction)[:16].strip().lower()
    if personal_data:
        base_meta["personal_data"] = True
        base_meta["erasure_reachable"] = bool(data_subject_key)
        if not data_subject_key:
            logger.warning(
                "[R-F3488] personal data ingested with NO data_subject_key "
                "(source=%s): it cannot be erased on request, only swept best-effort",
                str(source)[:120],
            )
            try:  # §21a — a gap the controller must be able to see, not just a log line
                from .engine_wiring import wire_failure
                wire_failure(
                    module="rag_store",
                    detail=(
                        f"personal data ingested without a data_subject_key "
                        f"(source={str(source)[:120]}): not erasable on request"
                    ),
                    gap_type="knowledge_gap",
                    source="rag_store:R-F3488",
                )
            except Exception as _wf:
                logger.debug("[R-F3488] wire_failure failed: %s", _wf)
    if extra_metadata:
        for k, v in extra_metadata.items():
            if isinstance(v, (str, int, float, bool)):
                base_meta[str(k)[:50]] = v if not isinstance(v, str) else v[:300]
    # ── R-F3699 — stamp the OWNER so retrieval can scope to it ──────────────
    #
    # Set this for anything derived from a specific user's material: an OCR'd
    # upload, a document read, a per-tenant corpus. Leave it EMPTY for shared
    # corpus (web-search results, curated intel) — search() treats an unstamped
    # chunk as universally retrievable, which is what keeps the ~667k
    # pre-existing chunks working.
    #
    # Written AFTER extra_metadata so an accidental `owner_key` passed through
    # that dict can never override the explicit argument.
    _own = (owner_key or "").strip()
    if _own:
        base_meta["owner_key"] = _own[:200]

    # R-F225 (2026-05-11) — content-hash dedup. Pre-R-F225 _hash_id
    # was `hash(chunk + source)` — the SAME RSS article fetched from
    # two different URLs created TWO chunks because `source` was part
    # of the ID. With ~46 ingest callers writing the same content from
    # different paths, RAG was accumulating duplicates indefinitely.
    # Fix: compute a content-only hash, query chromadb for existing
    # chunks carrying that hash, skip the upsert if any match. Source
    # provenance is preserved in metadata of the first write.
    import asyncio as _aio
    ids = []
    metadatas = []
    documents = []
    # Build per-chunk content_hash; later check chromadb for collisions.
    content_hashes = []
    for i, chunk in enumerate(chunks):
        cid = _hash_id(chunk, source)
        chash = hashlib.sha1(chunk.strip().encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        meta = dict(base_meta)
        meta["chunk_index"] = i
        meta["chunk_count"] = len(chunks)
        meta["content_hash"] = chash
        ids.append(cid)
        metadatas.append(meta)
        documents.append(chunk)
        content_hashes.append(chash)

    # R-F225: drop chunks whose content_hash already exists in RAG.
    # R-F3384: "in RAG" means hot AND cold. The lookup moved into
    # `_existing_content_hashes` — this comment used to describe a single
    # `_documents_collection.get(...)`, which is precisely the hot-only bug that
    # let offloaded chunks re-duplicate.
    try:
        if content_hashes:
            # R-F3384 — ask BOTH collections. Querying hot alone let every
            # offloaded chunk re-duplicate on re-ingest (see the helper).
            existing_hashes: set[str] = await _existing_content_hashes(content_hashes)
            if existing_hashes:
                keep_ids, keep_metas, keep_docs = [], [], []
                skipped_dup = 0
                for i, ch in enumerate(content_hashes):
                    if ch in existing_hashes:
                        skipped_dup += 1
                        continue
                    keep_ids.append(ids[i])
                    keep_metas.append(metadatas[i])
                    keep_docs.append(documents[i])
                if skipped_dup > 0:
                    # R-F367 (2026-05-12): single-chunk all-duplicate skips
                    # were producing ~50+ INFO lines per research cycle
                    # (live evidence fly logs 2026-05-12 11:30:55-11:31:08:
                    # 50+ `skipping 1 of 1 chunks` lines for every
                    # web_search-derived ingest). Downgrade those to DEBUG —
                    # they're operationally uninteresting (100% skip = nothing
                    # learned, common when web_search hits previously-seen
                    # content). Multi-chunk partial dedups stay at INFO
                    # since they indicate new + duplicate content mixed.
                    if skipped_dup == len(chunks) == 1:
                        logger.debug(
                            "[rag] R-F225 dedup: 1 of 1 chunks already in RAG for %s",
                            source,
                        )
                    else:
                        logger.info(
                            "[rag] R-F225 dedup: skipping %d of %d chunks (content already in RAG) for %s",
                            skipped_dup, len(chunks), source,
                        )
                ids, metadatas, documents = keep_ids, keep_metas, keep_docs
        if not ids:
            return {
                "ingested": False,
                "reason": "all_chunks_already_in_rag_R-F225",
                "duplicate_chunks_skipped": len(content_hashes),
            }
    except Exception as _de:
        logger.debug("R-F225 dedup probe failed (non-fatal, proceeding): %s", _de)

    try:
        # chromadb upsert is idempotent — same ID → overwrite.
        # IMPORTANT: chromadb's embedding function runs sentence-transformers
        # encode() synchronously and holds the GIL for 15-30s per batch.
        # When called from an async context (background lifespan tasks,
        # chat handlers, etc.) this pins the event loop and makes the whole
        # uvicorn process unresponsive. Known incident: 5+ minute startup
        # outages during international-law auto-seed because 12 law
        # sections were encoded back-to-back on the loop thread. Run the
        # upsert in a thread so the loop stays live.
        import asyncio as _aio
        await _aio.to_thread(
            _documents_collection.upsert,
            ids=ids, documents=documents, metadatas=metadatas,
        )
        logger.info("RAG ingest: %d chunks from %s (%s)", len(chunks), source, source_type)
        try:
            total = await _aio.to_thread(_documents_collection.count)
        except Exception:
            total = -1

        # R-F252 (2026-05-11) — offload-to-cold (replaces R-F238 warn-only).
        # When the hot collection crosses RAG_WARN_CHUNKS, MOVE the oldest
        # 10% to `aria_documents_cold`. Searches now consult both
        # collections (hot first, cold as fallback with score penalty).
        # No data is deleted — the infinite-memory rule is honoured;
        # hot-search latency stays bounded.
        global _rag_ingest_counter, _rag_overflow_warned_count
        _rag_ingest_counter += 1
        if (
            total > 0
            and total > RAG_WARN_CHUNKS
            and _rag_ingest_counter % RAG_PRUNE_CHECK_EVERY == 0
        ):
            try:
                offload_result = await _offload_oldest_to_cold(total)
                _rag_overflow_warned_count += 1
                # Single brain_hook absorb per offload event so dashboard
                # shows the activity (every 5 offloads to avoid spam).
                if _rag_overflow_warned_count % 5 == 1:
                    try:
                        from . import brain_hook as _bh_rag
                        await _bh_rag.absorb(
                            module="rag_store",
                            summary=(
                                f"R-F252: RAG offloaded {offload_result.get('moved', 0)} "
                                f"oldest chunks to cold (was {total}, "
                                f"threshold {RAG_WARN_CHUNKS})"
                            ),
                            detail=(
                                "Hot-collection search latency stays bounded. "
                                "Cold chunks remain queryable via the same "
                                "search() call — they just receive a 1.5x "
                                "relevance penalty so hot wins on ties."
                            ),
                            success=True,
                            gap_type="rag_offload_event",
                            gap_detail=f"moved={offload_result.get('moved', 0)}",
                        )
                    except Exception:
                        pass
            except Exception as _oe:
                logger.warning("R-F252 offload failed (will retry on next overflow): %s", _oe)

        return {
            "ingested": True,
            "chunks": len(chunks),
            "source": source,
            "source_type": source_type,
            "total_documents": total,
        }
    except Exception as e:
        logger.warning("RAG ingest failed for %s: %s", source, e)
        return {"ingested": False, "error": str(e)}


# R-F238 (2026-05-11) — _prune_oldest_chunks was DELETED.
# The function used to delete the oldest 10% of RAG chunks when chunk
# count exceeded a cap. That violates the "ARIA has infinite memory"
# operator rule (memory/aria_infinite_memory.md): she must never lose
# data. The function has been removed; the overflow path now warns the
# operator instead.
#
# R-F252 (2026-05-11) — the offload function that R-F238 promised.
# Moves the oldest 10% of hot chunks to `aria_documents_cold` when the
# warn threshold trips. Same embedding function so vectors are
# byte-compatible. Honours the infinite-memory rule: NO data deleted,
# just moved. Cold-add precedes hot-delete so a failure between the
# two leaves duplicates (operator can reconcile) instead of gaps.
RAG_COLD_OFFLOAD_FRACTION = 0.10


async def _existing_content_hashes(content_hashes: list[str]) -> set[str]:
    """Which of `content_hashes` are ALREADY stored — across hot AND cold.

    R-F3384 — the R-F225 dedup queried `_documents_collection` only. But
    `_offload_oldest_to_cold` MOVES chunks: it adds them to the cold collection
    and removes them from hot. Once offloaded, a chunk's hash was invisible to
    the dedup, so re-ingesting the same document wrote a fresh copy into hot.

    That is the R-F257 class again — that fix found `search()` reading only hot,
    leaving "every offloaded chunk invisible to retrieval". The identical
    oversight survived here.

    It matters beyond tidiness: two copies of one passage are two retrieval hits,
    and ARIA's verification posture (the C-3 independence gate, the corroboration
    rule that refuses to call one source two) assumes distinct hits mean distinct
    evidence. A duplicate makes a document corroborate itself.

    Fails SOFT per collection — a cold outage degrades to hot-only rather than to
    nothing, because failing open on both would silently re-duplicate everything.
    Dedup must never break ingest.
    """
    if not content_hashes:
        return set()
    import asyncio as _aio_h
    found: set[str] = set()
    for coll in (_documents_collection, _documents_cold_collection):
        if coll is None:
            continue
        try:
            rows = await _aio_h.to_thread(
                coll.get,
                where={"content_hash": {"$in": list(content_hashes)}},
                include=["metadatas"],
            )
            for m in (rows.get("metadatas") or []):
                if isinstance(m, dict) and m.get("content_hash"):
                    found.add(m["content_hash"])
        except Exception as e:                      # noqa: BLE001
            logger.debug("R-F3384 dedup probe failed on one collection: %s", e)
    return found


async def _offload_oldest_to_cold(current_total: int) -> dict:
    """Move RAG_COLD_OFFLOAD_FRACTION of the oldest hot chunks to cold."""
    import asyncio as _aio_o
    if _documents_collection is None or _documents_cold_collection is None:
        return {"moved": 0, "reason": "collections_not_ready"}
    target_move = max(1, int(current_total * RAG_COLD_OFFLOAD_FRACTION))
    try:
        rows = await _aio_o.to_thread(
            _documents_collection.get,
            include=["metadatas", "documents", "embeddings"],
        )
    except Exception as e:
        logger.warning("R-F252 offload fetch failed: %s", e)
        return {"moved": 0, "error": str(e)[:200]}

    ids = rows.get("ids") or []
    metas = rows.get("metadatas") or []
    docs = rows.get("documents") or []
    embeds = rows.get("embeddings") or []
    if not ids or len(ids) != len(metas):
        return {"moved": 0, "reason": "rows_mismatch"}

    # Pair indices by ts_epoch ascending (oldest first), take bottom N
    paired_idx = sorted(
        range(len(ids)),
        key=lambda i: (metas[i] or {}).get("ts_epoch") or 0,
    )[:target_move]
    if not paired_idx:
        return {"moved": 0, "reason": "no_chunks_to_move"}

    move_ids = [ids[i] for i in paired_idx]
    move_docs = [docs[i] for i in paired_idx] if docs else None
    move_metas = []
    now_ts = time.time()
    for i in paired_idx:
        m = dict(metas[i] or {})
        m["offloaded_at"] = now_ts
        m["offloaded_from"] = "hot"
        move_metas.append(m)
    move_embeds = None
    if embeds is not None and len(embeds) == len(ids):
        try:
            move_embeds = [embeds[i] for i in paired_idx]
        except Exception:
            move_embeds = None

    # 1) Add to cold FIRST (safer ordering)
    try:
        if move_embeds:
            await _aio_o.to_thread(
                _documents_cold_collection.add,
                ids=move_ids, documents=move_docs,
                metadatas=move_metas, embeddings=move_embeds,
            )
        else:
            await _aio_o.to_thread(
                _documents_cold_collection.add,
                ids=move_ids, documents=move_docs, metadatas=move_metas,
            )
    except Exception as e:
        logger.warning("R-F252 cold add failed: %s — NOT removing from hot", e)
        return {"moved": 0, "error": f"cold_add: {str(e)[:200]}"}

    # 2) Only after cold-add succeeds, remove from hot
    try:
        await _aio_o.to_thread(_documents_collection.delete, ids=move_ids)
    except Exception as e:
        logger.error(
            "R-F252 hot-delete failed after cold-add succeeded: %s — "
            "duplicates exist in hot+cold for %d ids; operator can "
            "reconcile via /admin/rag/dedupe-cold-vs-hot. Data is NOT lost.",
            e, len(move_ids),
        )
        return {
            "moved": len(move_ids),
            "warning": "duplicates_in_hot_and_cold",
            "error": str(e)[:200],
        }

    logger.warning(
        "[rag] R-F252 OFFLOADED %d oldest chunks to cold (was %d, "
        "threshold %d). Searches now consult BOTH collections.",
        len(move_ids), current_total, RAG_WARN_CHUNKS,
    )
    return {
        "moved": len(move_ids),
        "previous_hot_total": current_total,
        "threshold": RAG_WARN_CHUNKS,
    }


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def ingest_fact(
    fact_id: str,
    topic: str,
    content: str,
    *,
    confidence: str = "ASSESSED",
    source: str = "",
    market: str = "",
) -> bool:
    """Add a single distilled fact to the facts collection.
    Called by knowledge.store_fact() so the RAG facts collection stays
    in sync with the canonical Redis knowledge base.
    """
    allowed, gate_reason = await _memory_ingest_allowed(source=source)
    if not allowed:
        logger.info(
            "RAG fact ingest skipped unapproved low-value source %s (%s)",
            source[:120], gate_reason,
        )
        return False
    if not await _ensure_async():
        return False
    # R-F410 sanitize the content (NOT the topic — topic is canonical)
    content, _sm = _sanitize_ingest_text(content or "")
    if not content or len(content.strip()) < 10:
        return False
    text = f"{topic}: {content}".strip()
    try:
        import asyncio as _aio
        # Same thread-offload reason as ingest_document — chromadb
        # embedding is sync and holds the GIL.
        await _aio.to_thread(
            _facts_collection.upsert,
            ids=[fact_id],
            documents=[text],
            metadatas=[{
                "topic": topic[:200],
                "confidence": confidence,
                "source": source[:300],
                "market": market[:100],
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "ts_epoch": time.time(),
            }],
        )
        return True
    except Exception as e:
        # R-F2404 — the @fail_wire above only fires on a RAISE, but this internal
        # try/except swallows the upsert failure and returns False, so a persistent
        # RAG-collection write failure was DARK (§21) — facts silently stopped
        # becoming retrievable via RAG with no gap/signal (the durable knowledge
        # fact is still saved per §7; only the RAG index degrades). record_gap
        # dedupes by (gap_type, detail) within a window, so a chromadb outage won't
        # flood — it surfaces ONE gap the self-heal loop can act on.
        logger.debug("RAG fact ingest failed: %s", e)
        try:
            from . import capability_gaps
            await capability_gaps.record_gap(
                gap_type="embedder_failure",
                detail=f"rag_store.ingest_fact upsert failed: {type(e).__name__}: {str(e)[:200]}",
                source="rag_store.ingest_fact",
            )
        except Exception:
            pass
        return False


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def add_facts_batch(facts: list[dict]) -> int:
    """Batched variant of add_fact — collapses N encode calls into 1.

    F23 fix 2026-04-27: live log showed 28 separate "Batches: 1/1"
    sentence-transformer calls per article ingest because each fact
    was upserted individually. Routing them through this batch helper
    cuts the per-article encode time from ~700ms to ~80ms (one model
    pass instead of N).

    Each item must be a dict with keys: fact_id, topic, content,
    confidence, source, market. Items with content < 10 chars are
    silently skipped (matches add_fact's contract).

    Returns the number of facts actually upserted.
    """
    if not facts:
        return 0
    if not await _ensure_async():
        return 0
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    ts_epoch = time.time()
    for f in facts:
        content = (f.get("content") or "").strip()
        # R-F410 sanitize per-fact content before length check + upsert
        content, _ = _sanitize_ingest_text(content)
        topic = f.get("topic") or ""
        allowed, gate_reason = await _memory_ingest_allowed(
            source=f.get("source") or "",
            credibility_tier=f.get("credibility_tier"),
        )
        if not allowed:
            logger.info(
                "RAG batch fact ingest skipped unapproved low-value source %s (%s)",
                str(f.get("source") or "")[:120], gate_reason,
            )
            continue
        if not content or len(content) < 10:
            continue
        ids.append(f.get("fact_id") or _hash_id(content, f.get("source", "")))
        docs.append(f"{topic}: {content}".strip())
        metas.append({
            "topic": topic[:200],
            "confidence": f.get("confidence", "ASSESSED"),
            "source": (f.get("source") or "")[:300],
            "market": (f.get("market") or "")[:100],
            "ingested_at": now_iso,
            "ts_epoch": ts_epoch,
        })
    if not ids:
        return 0
    try:
        import asyncio as _aio
        await _aio.to_thread(
            _facts_collection.upsert,
            ids=ids, documents=docs, metadatas=metas,
        )
        return len(ids)
    except Exception as e:
        logger.debug("RAG batch fact ingest failed: %s", e)
        return 0


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def add_search_results_batch(items: list[dict]) -> int:
    """R-F859 (2026-05-24) — batched ingest for short search-result docs.

    Collapses N per-result encodes into ONE batched upsert (chromadb encodes
    the whole batch in a single sentence-transformers pass). Mirrors
    add_facts_batch but targets the documents collection. Fixes the FIX-1b
    half of finding #1: R-F184 looped ingest_document once per web_search
    result (~25 'Batches:1/1' encodes per burst), each a separate GIL-holding
    encode that starved the event loop.

    Each item: {text, source, source_type?, title?, url?, metadata?}. Search
    results are short (title+snippet) so each is one chunk — no splitting
    needed. Items < 40 chars are skipped. The id is content-hashed so re-runs
    upsert idempotently (same dedup guarantee as ingest_document). Returns the
    number upserted.
    """
    if not items:
        return 0
    if not await _ensure_async() or _documents_collection is None:
        return 0
    filtered: list[dict] = []
    for it in items:
        url = str(it.get("url") or "")
        source = str(it.get("source") or "")
        metadata = it.get("metadata") or {}
        tier = metadata.get("credibility_tier")
        if url or _url_candidate(source):
            allowed, reason = await _memory_ingest_allowed(
                url=url,
                source=source,
                credibility_tier=tier,
            )
            if not allowed:
                logger.info(
                    "RAG batch ingest skipped unapproved low-value source %s (%s)",
                    (url or source)[:120], reason,
                )
                continue
        filtered.append(it)
    items = filtered
    if not items:
        return 0
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    ts_epoch = time.time()
    seen: set[str] = set()
    for it in items:
        text = (it.get("text") or "").strip()
        text, _ = _sanitize_ingest_text(text)
        if not text or len(text) < 40:
            continue
        src = (it.get("source") or "")
        cid = _hash_id(text, src)
        if cid in seen:   # de-dupe within the batch (chromadb rejects dup ids)
            continue
        seen.add(cid)
        ids.append(cid)
        docs.append(text)
        meta = {
            "source": src[:300],
            "source_type": (it.get("source_type") or "")[:50],
            "title": (it.get("title") or "")[:200],
            "url": (it.get("url") or "")[:500],
            "ingested_at": now_iso,
            "ts_epoch": ts_epoch,
        }
        for k, v in (it.get("metadata") or {}).items():
            if v is not None:   # chromadb metadata values cannot be None
                meta[k] = v
        metas.append(meta)
    if not ids:
        return 0
    try:
        import asyncio as _aio
        await _aio.to_thread(
            _documents_collection.upsert,
            ids=ids, documents=docs, metadatas=metas,
        )
        logger.info("RAG batch ingest: %d search results (1 encode pass)", len(ids))
        return len(ids)
    except Exception as e:
        logger.debug("RAG batch search-result ingest failed: %s", e)
        return 0


# ── Public API: retrieve ───────────────────────────────────────────────────

def _recency_boost(ts_epoch: float | None) -> float:
    """Boost retrieval score for recent content."""
    if not ts_epoch:
        return 1.0
    age_days = (time.time() - ts_epoch) / 86400
    if age_days < 1:    return 1.20
    if age_days < 7:    return 1.10
    if age_days < 30:   return 1.00
    if age_days < 90:   return 0.95
    return 0.90


# R-F2215 (2026-07-01) — credibility-aware ranking. web_search tags each search
# result with a credibility_tier (1=official/.gov … 6=quarantine) which is stored
# as a FLAT int in chunk metadata (web_search.py:1375 → add_search_results_batch
# rag_store.py:1004-1006) — but search() never read it, so a Tier-1 .gov chunk
# ranked identically to a Tier-5 blog. Apply a bounded multiplier. NEUTRAL (1.0)
# when the tier is absent, so corpus + user/vault chunks (no tier) are UNCHANGED —
# only the relative ranking of web-search chunks shifts. Env-reversible.
_CREDIBILITY_RANK_ENABLED = os.getenv("ARIA_RAG_CREDIBILITY_RANK", "1") == "1"
_CREDIBILITY_MULT = {1: 1.20, 2: 1.10, 3: 1.00, 4: 0.90, 5: 0.85, 6: 0.70}


def _credibility_multiplier(tier) -> float:
    """Bounded retrieval multiplier for a source credibility tier. Returns a
    neutral 1.0 when disabled or the tier is missing/unrecognised — so this can
    never demote content that simply lacks a tier."""
    if not _CREDIBILITY_RANK_ENABLED or tier is None:
        return 1.0
    try:
        return _CREDIBILITY_MULT.get(int(tier), 1.0)
    except (TypeError, ValueError):
        return 1.0


@fail_wire(module="rag_store", gap_type="embedder_failure")
def chunk_visible_to(chunk_owner: str, caller_owner: str, *, serve_owned_to_all: bool = False) -> bool:
    """R-F3699 — may a retrieved chunk be served to this caller?

    Extracted as a named predicate rather than left inline in the query loop so
    it is directly testable: chromadb has no win-arm64 wheel, so `search()`
    itself cannot execute on the dev box (§16) and an inline rule would ship
    unexercised.

    The rule:
      * chunk with NO owner  -> shared corpus (web results, curated intel).
        Universally retrievable. This is what keeps the ~667k pre-existing
        chunks — none of which carry an owner_key — working.
      * chunk WITH an owner  -> served only to that owner. A caller with no
        owner key gets none of them, because the alternative is serving
        someone's uploaded documents to whoever asks.
      * `serve_owned_to_all` is the explicit single-tenant declaration.
    """
    owner = (chunk_owner or "").strip()
    if not owner:
        return True
    if serve_owned_to_all:
        return True
    return owner == (caller_owner or "").strip()


def _rag_serve_owned_to_all() -> bool:
    """R-F3699 — single-tenant escape hatch, opt-in and explicit.

    A deployment with exactly one real user can set
    ``ARIA_RAG_SERVE_OWNED_TO_ALL=1`` so owner-stamped chunks stay retrievable
    from unattributed internal callers. It is a DECLARATION that there is no
    second tenant to leak to — never a default, so it cannot silently become
    one again.
    """
    import os as _os
    return str(_os.getenv("ARIA_RAG_SERVE_OWNED_TO_ALL", "")).strip().lower() in {"1", "true", "yes"}


# ── R-F3766 — §21a. This shipped unwired, and it is the worst place for it ───
#
# `search()` returns a LIST. Its failure mode is an EMPTY list, which is
# indistinguishable from "the store holds nothing relevant" — and this is the
# retrieval path feeding DD and chat answers. An unwired failure here does not
# produce a visible error; it produces a CONFIDENT ANSWER WITH LESS EVIDENCE
# BEHIND IT, which is the one failure a due-diligence product cannot afford.
#
# Same class as every other fix in this sweep (R-F3716/17, R-F3722, R-F3758,
# R-F3759, R-F3762, R-F3764): a value that cannot distinguish "looked and found
# nothing" from "the lookup broke". The module already imports fail_wire and
# uses it at :311 and :476 with gap_type="embedder_failure"; that is the
# retrieval convention here, so it is reused rather than inventing a new type.
@fail_wire(module="rag_store", gap_type="embedder_failure")
async def search(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    source_type: str | None = None,
    market: str | None = None,
    include_facts: bool = True,
    include_documents: bool = True,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    owner_key: str = "",
) -> list[dict]:
    """Hybrid retrieval over the RAG store.

    Returns a ranked list of chunks with their metadata. Each item:
        {text, score, source, source_type, title, url, market, ingested_at, collection}

    Optional filters narrow the search to a source_type or market.

    R-F397 (2026-05-13): `min_similarity` floor (default 0.50) bounces
    chunks whose cosine similarity is below the threshold BEFORE they
    enter the result list. Without this, chromadb's top_k included
    phonetic/acronym false-positives (e.g. "La Scala" → 0.43 sim → an
    unrelated Lebanon/Hezbollah chunk). Pass `min_similarity=0.0` to
    restore the legacy no-floor behaviour (e.g. for exploratory
    crawls where any related chunk is welcome).
    """
    if not await _ensure_async():
        return []
    if not query or len(query.strip()) < 3:
        return []

    results: list[dict] = []
    import asyncio as _aio

    # R-F3699 — resolved ONCE per search, read by the per-chunk filter below.
    # `_owner_filter_active` drives the over-fetch: it is True whenever owned
    # chunks could be excluded, i.e. unless this deployment has declared itself
    # single-tenant.
    _owner_norm = (owner_key or "").strip()
    _serve_owned_to_all = _rag_serve_owned_to_all()
    _owner_filter_active = not _serve_owned_to_all

    def _sync_query_collection(coll, name: str):
        if coll is None:
            return
        try:
            if coll.count() == 0:
                return
            where = {}
            if source_type:
                where["source_type"] = source_type
            if market:
                where["market"] = market
            # R-F3699 — over-fetch when an owner filter is active. The filter is
            # applied in Python below (not in `where`), so without this a page
            # of another tenant's chunks could crowd out the caller's own and
            # silently return nothing. 4× is the same headroom internal_search
            # uses before its re-rank.
            _n_results = top_k * 4 if _owner_filter_active else top_k
            kwargs = {"query_texts": [query], "n_results": _n_results}
            if where:
                kwargs["where"] = where
            r = coll.query(**kwargs)
            ids_list = (r.get("ids") or [[]])[0]
            docs = (r.get("documents") or [[]])[0]
            metas = (r.get("metadatas") or [[]])[0]
            distances = (r.get("distances") or [[]])[0]
            for i, doc in enumerate(docs):
                if not doc:
                    continue
                meta = metas[i] if i < len(metas) else {}
                # chromadb cosine distance → similarity
                dist = distances[i] if i < len(distances) else 1.0
                similarity = max(0.0, 1.0 - dist)
                # R-F397 (2026-05-13): floor enforcement. Below the floor
                # is the "topic bleed" zone — phonetic/acronym matches
                # that look related to the embedding model but aren't.
                # The 0.43 La Scala→Lebanon case ARIA flagged sits here.
                if similarity < min_similarity:
                    continue
                # Apply recency boost + R-F2215 credibility multiplier (neutral
                # when the chunk has no tier, so corpus/vault content is unchanged).
                # ── R-F3699 — TENANT SCOPING, fail closed ──────────────────
                #
                # THE DEFECT: `search()` had no owner parameter at all, and
                # `ocr.py` ingests EVERY successful OCR extraction into the
                # shared `aria_documents` collection with no owner metadata.
                # That path is reached from the WhatsApp / upload document flow,
                # and `aria_engine._prefetch_rag` pulls up to 6000 chars of this
                # collection into the model context on EVERY chat turn for EVERY
                # user. So user A's invoice could be retrieved into user B's
                # prompt and quoted back.
                #
                # This is the same class R-F3489 closed for mem0 recall; the RAG
                # half was never done, and `tenant_namespace.py:252-255` says so
                # itself: `"wired_into": []` with a TODO naming this function.
                #
                # Filtering happens HERE, in Python, rather than in chromadb's
                # `where`: the store holds ~667k pre-existing chunks with NO
                # `owner_key` field, and chroma's `where` cannot express "field
                # absent". A `$in` filter would therefore have excluded the
                # entire legacy corpus — a catastrophic, silent recall loss.
                #
                # The rule: a chunk with NO owner is shared corpus (web results,
                # curated intel) and stays universally retrievable. A chunk WITH
                # an owner is served only to that owner. A caller with no owner
                # key gets no owned chunks at all, because the alternative is
                # serving someone's uploaded documents to whoever asks.
                _chunk_owner = (meta.get("owner_key") or "") if isinstance(meta, dict) else ""
                if not chunk_visible_to(_chunk_owner, _owner_norm,
                                        serve_owned_to_all=_serve_owned_to_all):
                    continue
                ts = meta.get("ts_epoch") if isinstance(meta, dict) else None
                _cred_tier = meta.get("credibility_tier") if isinstance(meta, dict) else None
                score = similarity * _recency_boost(ts) * _credibility_multiplier(_cred_tier)
                results.append({
                    "id": ids_list[i] if i < len(ids_list) else "",
                    "text": doc,
                    "score": round(score, 4),
                    "similarity": round(similarity, 4),
                    "source": meta.get("source", "") if isinstance(meta, dict) else "",
                    "source_type": meta.get("source_type", "") if isinstance(meta, dict) else "",
                    "title": meta.get("title", "") if isinstance(meta, dict) else "",
                    "url": meta.get("url", "") if isinstance(meta, dict) else "",
                    "market": meta.get("market", "") if isinstance(meta, dict) else "",
                    "ingested_at": meta.get("ingested_at", "") if isinstance(meta, dict) else "",
                    "credibility_tier": _cred_tier,
                    # R-F3379 — CARRY the rights marking. This selected-field list
                    # previously dropped it, so the R-F3376 ingest gate wrote a
                    # marking no consumer could ever see: `may_quote_verbatim` had
                    # nothing to read. A marking that does not survive the
                    # retrieval boundary is not a control.
                    "rights": meta.get("rights", "") if isinstance(meta, dict) else "",
                    "collection": name,
                })
        except Exception as e:
            logger.debug("RAG query on %s failed: %s", name, e)

    # Offload the whole query (embedding + vector search) to a thread so
    # the event loop can continue serving other requests. chromadb's query
    # path re-embeds the query via sentence-transformers, which is sync
    # and GIL-bound.
    #
    # R-F257 (2026-05-11): include the cold collection. R-F252 wired the
    # offload-on-overflow path but search() only read hot — every offloaded
    # chunk became invisible to retrieval, violating the infinite-memory
    # rule. Cold is always queried alongside hot; the final top_k clamp at
    # the end limits the returned set so cold can't bloat output.
    #
    # Three collections (hot docs + cold docs + facts) are queried in PARALLEL
    # via asyncio.gather. CPython list.append is atomic under the GIL so
    # the shared `results` list is safe under concurrent worker appenders.
    # Without parallelism we'd pay 3× embedding-compute latency per search.
    query_tasks = []
    if include_documents:
        query_tasks.append(_aio.to_thread(_sync_query_collection, _documents_collection, "documents"))
        query_tasks.append(_aio.to_thread(_sync_query_collection, _documents_cold_collection, "documents_cold"))
    if include_facts:
        query_tasks.append(_aio.to_thread(_sync_query_collection, _facts_collection, "facts"))
    if query_tasks:
        await _aio.gather(*query_tasks)

    # R-F257 (2026-05-11): dedupe by chunk id. In the steady-state path
    # the hot+cold collections are disjoint, but R-F252's recovery path
    # leaves duplicates when hot-delete fails after cold-add succeeds.
    # Cheap O(n) dedupe keeps search correct in that recovery state.
    # First occurrence wins (preserves the hot result + its scoring).
    if results:
        seen_ids: set[str] = set()
        deduped: list[dict] = []
        for r in results:
            rid = r.get("id") or ""
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            deduped.append(r)
        results = deduped

    # ── Hybrid re-ranking: BM25-like lexical boost ──
    # Pure semantic search confuses entities with similar embeddings
    # (e.g. "BTG a.s." ↔ "BTG Pactual"). This adds a term-overlap boost
    # so results containing the exact query tokens rank higher.
    # No external dependency — simple inline TF scoring.
    results = _hybrid_rerank(results, query)
    return results[:top_k]


# ── Inline BM25-like re-ranker ──────────────────────────────────────────────

_RERANK_STOPWORDS = {
    "the", "and", "or", "in", "of", "a", "an", "to", "for", "is", "are",
    "was", "were", "has", "have", "had", "with", "by", "from", "at", "on",
    "as", "it", "its", "be", "this", "that", "not", "but", "no", "so",
}

# Corporate suffixes to ignore in matching
_RERANK_SUFFIXES = {
    "ltd", "limited", "plc", "inc", "corp", "llc", "gmbh", "srl", "sa",
    "bv", "nv", "ag", "ab", "oy", "oyj", "sp", "sro", "doo", "as",
}


def _tokenize_for_rerank(text: str) -> list[str]:
    """Tokenize text for BM25-like matching. Preserves entity-significant tokens."""
    if not text:
        return []
    # Lowercase, split on non-alphanumeric (preserve dots in "a.s.", "s.r.o.")
    tokens = re.findall(r'[a-z0-9]+(?:\.[a-z]+)*', text.lower())
    return [t for t in tokens if len(t) >= 2 and t not in _RERANK_STOPWORDS]


def _hybrid_rerank(results: list[dict], query: str) -> list[dict]:
    """Re-rank semantic results with BM25-like term overlap boost.

    Exact entity name matches get a significant score boost.
    This prevents "BTG a.s." from being outranked by "BTG Pactual"
    when the user searched for the Slovak entity specifically.
    """
    if not results or not query:
        return results

    query_tokens = _tokenize_for_rerank(query)
    if not query_tokens:
        return results

    # Compute meaningful tokens (excluding corporate suffixes)
    query_meaningful = [t for t in query_tokens if t not in _RERANK_SUFFIXES]
    query_lower = query.lower()

    for r in results:
        text = (r.get("text") or "").lower()
        title = (r.get("title") or "").lower()
        doc_text = f"{title} {text}"

        # 1. Exact substring match — strongest signal
        if query_lower in doc_text:
            r["score"] *= 1.5
            r["_rerank"] = "exact_substring"
            continue

        # 2. Token overlap ratio
        doc_tokens = set(_tokenize_for_rerank(doc_text))
        if not doc_tokens:
            continue

        # Count how many meaningful query tokens appear in the document
        overlap = sum(1 for t in query_meaningful if t in doc_tokens)
        overlap_ratio = overlap / len(query_meaningful) if query_meaningful else 0

        if overlap_ratio >= 0.8:
            r["score"] *= 1.35
            r["_rerank"] = "high_overlap"
        elif overlap_ratio >= 0.5:
            r["score"] *= 1.15
            r["_rerank"] = "medium_overlap"
        elif overlap_ratio == 0 and query_meaningful:
            # Zero token overlap with meaningful query = likely wrong entity
            r["score"] *= 0.6
            r["_rerank"] = "no_overlap_penalty"

    # Re-sort by boosted score
    results.sort(key=lambda r: -r["score"])
    return results


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def get_rag_context_with_sources(
    query: str,
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    top_k: int = DEFAULT_TOP_K,
) -> tuple[str, list[dict]]:
    """R-F107 (2026-05-09): same as get_rag_context but also returns the
    structured source list so the chat-audit layer can record what was
    actually retrieved (vs what made it into the response text).

    Returns (formatted_context_text, [{title, source, url, score}, ...]).
    The source list survives even when the LLM paraphrases without
    quoting URLs — fixes the chronic 'sources_count: 0' on chat_audit
    entries despite real retrieval.
    """
    if not await _ensure_async():
        return ("", [])
    results = await search(query, top_k=top_k)
    if not results:
        return ("", [])
    sources = [
        {
            "title":  r.get("title", ""),
            "source": r.get("source", ""),
            "url":    r.get("url") or r.get("source", ""),
            "score":  r.get("score"),
            "ingested_at": r.get("ingested_at"),
        }
        for r in results
    ]
    text = _format_rag_context(results, max_chars)
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="rag_store",
        summary="Get Rag Context With Sources",
        source_id="rag_store:R-F996",
    )

    return (text, sources)


def _rights_marker(result: dict) -> str:
    """DO-NOT-QUOTE marker for a chunk whose rights do not permit reproduction.

    Delegates to `corpus_ingest.may_quote_verbatim` — the canonical predicate —
    rather than re-implementing the rule here. Two measures of one thing is how
    they drift apart (the Phase-A gate lesson).
    """
    try:
        from .corpus_ingest import may_quote_verbatim
    except Exception:
        return ""                      # never break retrieval over a marker
    if not isinstance(result, dict):
        return ""
    rights = str(result.get("rights") or "")
    if may_quote_verbatim({"rights": rights}):
        return ""
    if rights:
        return f" ⛔ DO NOT QUOTE ({rights} — summarise only)"
    # Pre-R-F3376 chunks carry no rights at all. Unknown provenance is exactly
    # the condition this gate exists for, so it is marked, not waved through.
    return " ⛔ DO NOT QUOTE (provenance unrecorded — summarise only)"


@fail_wire(module="rag_store", gap_type="embedder_failure")
def rights_gate_stats(results: list[dict]) -> dict:
    """How much of a result set the gate marks, and why.

    Makes the migration surface measurable: every chunk ingested before R-F3376
    is `unrecorded`, and re-ingesting it with a declared rights value is what
    moves it back to quotable.
    """
    total = quotable = marked = unrecorded = 0
    for r in results or []:
        if not isinstance(r, dict):
            continue
        total += 1
        rights = str(r.get("rights") or "")
        try:
            from .corpus_ingest import may_quote_verbatim
            ok = may_quote_verbatim({"rights": rights})
        except Exception:
            ok = False
        if ok:
            quotable += 1
        else:
            marked += 1
            if not rights:
                unrecorded += 1
    return {"total": total, "quotable": quotable, "marked": marked,
            "unrecorded": unrecorded}


def _format_rag_context(results: list[dict], max_chars: int) -> str:
    """Build the formatted prompt-injection context block. Extracted so
    both get_rag_context (back-compat) and get_rag_context_with_sources
    (R-F107) can share the rendering logic without duplication."""
    from datetime import datetime, timezone
    _now = datetime.now(timezone.utc)
    _STALE_DAYS = 90

    def _staleness_marker(ingested_at: str) -> str:
        if not ingested_at:
            return ""
        try:
            dt = datetime.fromisoformat(ingested_at.replace("Z", "+00:00"))
            age_days = (_now - dt).days
            if age_days > _STALE_DAYS * 2:
                return f" ⚠ STALE ({age_days}d old — verify before citing)"
            if age_days > _STALE_DAYS:
                return f" ⚠ aging ({age_days}d old — may be outdated)"
        except Exception:
            return ""
        return ""

    # R-F3379 — the rights gate, at the ONE renderer both context functions share
    # (get_rag_context and get_rag_context_with_sources). Non-quotable chunks are
    # still retrieved and still inform the answer; they are MARKED so the model
    # summarises instead of reproducing. Removing them would destroy the value of
    # licensed material we legitimately hold, and this renderer already marks
    # rather than drops for staleness.
    _gate_on = (os.getenv("ARIA_RAG_RIGHTS_GATE", "1") or "1").strip() not in (
        "0", "false", "no", "off")

    lines = ["\n\n[RAG RETRIEVED — proprietary intelligence indexed from your sources. Respect ⚠ STALE markers — do not present stale chunks as current fact.]"]
    if _gate_on:
        lines.append(
            "[RIGHTS — a chunk marked ⛔ DO NOT QUOTE may be used to reason and may "
            "be summarised in your own words, but its text must NOT be reproduced "
            "verbatim or quoted to the user.]"
        )
    total = 0
    for r in results:
        cite_parts = []
        if r.get("title"):  cite_parts.append(r["title"])
        if r.get("source"): cite_parts.append(r["source"])
        if r.get("ingested_at"): cite_parts.append(r["ingested_at"][:10])
        cite = " | ".join(cite_parts) if cite_parts else "unknown source"
        stale = _staleness_marker(r.get("ingested_at", ""))
        rights_mark = _rights_marker(r) if _gate_on else ""
        body = f"\n• [{r['score']:.2f}]{stale}{rights_mark} {r['text'][:600]}\n  ↳ source: {cite}"
        if total + len(body) > max_chars:
            break
        lines.append(body)
        total += len(body)
    return "\n".join(lines)


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def get_rag_context(
    query: str,
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    top_k: int = DEFAULT_TOP_K,
) -> str:
    """Build a formatted context string for LLM prompt injection.

    Returns the top retrieved passages with citations, capped at max_chars.
    Designed to slot into aria_engine's context layer pipeline.

    Back-compat wrapper around get_rag_context_with_sources — drops the
    sources list. New callers should prefer get_rag_context_with_sources
    so retrieval provenance reaches the audit layer (R-F107).
    """
    text, _ = await get_rag_context_with_sources(query, max_chars=max_chars, top_k=top_k)
    return text


# ── Public API: stats + maintenance ────────────────────────────────────────

# R-F1911 (2026-06-25) — single-flight TTL cache for get_stats.
# ROOT CAUSE (§22 evidence, live aria-intel): /api/aria/health was a flat ~30s
# on EVERY poll. Per-sub-call timing showed the entire cost was here —
# ChromaDB `.count()` on the documents + facts collections (~215K chunks) is an
# O(collection-size) NATIVE scan, ~38s cold, and it ran uncached on every
# diagnostic request (dashboard polls /health). The counts are slowly-changing
# diagnostic data, so memoising them eliminates the per-request scan (this is the
# structural fix for the /health latency class — not a timeout band-aid).
# A single asyncio.Lock makes it single-flight: after the TTL exactly ONE caller
# pays the count while concurrent callers await the lock and then read the fresh
# value, so a burst of /health polls can never launch N concurrent 30s scans.
_STATS_TTL_S = float(os.getenv("ARIA_RAG_STATS_TTL_S", "120"))
_stats_cache: dict = {"value": None, "ts": 0.0}
_stats_lock = asyncio.Lock()


async def _compute_stats() -> dict:
    """The actual (expensive) count — offloaded so it never runs on the loop."""
    try:
        doc_count = await asyncio.to_thread(_documents_collection.count) if _documents_collection else 0
        fact_count = await asyncio.to_thread(_facts_collection.count) if _facts_collection else 0
        return {
            "available": True,
            "path": RAG_PATH,
            "persistent": Path(RAG_PATH).exists() and not RAG_PATH.startswith("/tmp"),
            "documents_indexed": doc_count,
            "facts_indexed": fact_count,
            "total_chunks": doc_count + fact_count,
            "embedding_model": EMBEDDING_MODEL_NAME,
            "chunk_size": CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP,
        }
    except Exception as e:
        return {"available": False, "error": str(e), "path": RAG_PATH}


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def get_stats() -> dict:
    """Report on the RAG store state (memoised — see R-F1911 above)."""
    if not await _ensure_async():
        return {
            "available": False,
            "reason": "chromadb not installed or init failed",
            "path": RAG_PATH,
        }
    now = time.monotonic()
    cached = _stats_cache["value"]
    if cached is not None and (now - _stats_cache["ts"]) < _STATS_TTL_S:
        return {**cached, "stats_cache_age_s": round(now - _stats_cache["ts"], 1)}
    # Stale or empty — single-flight refresh so a poll burst can't fan out
    # into N concurrent 30s scans.
    async with _stats_lock:
        now2 = time.monotonic()
        cached2 = _stats_cache["value"]
        if cached2 is not None and (now2 - _stats_cache["ts"]) < _STATS_TTL_S:
            # another caller refreshed while we waited on the lock
            return {**cached2, "stats_cache_age_s": round(now2 - _stats_cache["ts"], 1)}
        val = await _compute_stats()
        if val.get("available"):  # never cache a transient failure
            _stats_cache["value"] = val
            _stats_cache["ts"] = time.monotonic()
        return {**val, "stats_cache_age_s": 0.0}


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def add_chunk(
    text: str,
    *,
    metadata: dict | None = None,
) -> dict:
    """Thin shim over `ingest_document` — knowledge_spider (and any
    other caller) uses this cleaner two-arg shape. Metadata keys are
    flattened into `ingest_document`'s named parameters where they map
    (source, url) and otherwise passed through as `extra_metadata`.

    Before 2026-04-20 this didn't exist; the spider's
    `hasattr(rag_store, "add_chunk")` gate silently skipped every
    ingest attempt. Even if the spider had found URLs to fetch, none
    of the discovered content would have reached the RAG store.
    """
    m = dict(metadata or {})
    source = str(m.pop("source", "") or "spider")
    url = str(m.pop("url", "") or "")
    source_type = str(m.pop("source_type", "") or "crawl")
    title = str(m.pop("title", "") or "")
    market = str(m.pop("market", "") or "")
    return await ingest_document(
        text,
        source=source,
        source_type=source_type,
        title=title,
        url=url,
        market=market,
        extra_metadata=m or None,
    )


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def recent_chunks(limit: int = 200) -> list[dict]:
    """Return recently-ingested chunks, newest first, as
    [{"text", "metadata"}] dicts.

    Used by knowledge_spider._collect_seeds() to harvest URLs mentioned
    in fresh corpus material. Before 2026-04-20 this function didn't
    exist; the spider's `hasattr(rag_store, "recent_chunks")` gate
    silently skipped this seed source for weeks and
    knowledge_spider.fetches_24h stayed 0.

    ChromaDB has no native order-by-ingestion; we sample up to 2000
    chunks and sort locally on metadata.ingested_at. Chunks without a
    timestamp fall to the end (treated as oldest).
    """
    if not await _ensure_async():
        return []
    try:
        import asyncio as _aio
        sample_size = max(limit * 5, 1000)
        sample = await _aio.to_thread(
            _documents_collection.get,
            limit=min(sample_size, 5000),
            include=["documents", "metadatas"],
        )
        docs = sample.get("documents") or []
        metas = sample.get("metadatas") or []
        rows: list[dict] = []
        for doc, meta in zip(docs, metas):
            if not doc:
                continue
            rows.append({
                "text": doc,
                "metadata": meta if isinstance(meta, dict) else {},
            })
        rows.sort(
            key=lambda r: r["metadata"].get("ingested_at") or "",
            reverse=True,
        )
        return rows[:limit]
    except Exception as e:
        logger.debug("recent_chunks failed: %s", e)
        return []


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def list_sources(limit: int = 50) -> dict:
    """Return a summary of unique sources in the RAG store grouped by type."""
    if not await _ensure_async():
        return {"available": False}
    try:
        import asyncio as _aio
        # chromadb doesn't have a great "distinct" — fetch a sample of metadatas
        sample = await _aio.to_thread(
            _documents_collection.get, limit=2000, include=["metadatas"],
        )
        metadatas = sample.get("metadatas") or []
        by_source: dict[str, dict] = {}
        for m in metadatas:
            if not isinstance(m, dict):
                continue
            src = m.get("source", "unknown")
            if src not in by_source:
                by_source[src] = {
                    "source": src,
                    "source_type": m.get("source_type", ""),
                    "title": m.get("title", ""),
                    "url": m.get("url", ""),
                    "market": m.get("market", ""),
                    "chunks": 0,
                    "ingested_at": m.get("ingested_at", ""),
                }
            by_source[src]["chunks"] += 1
        sources = sorted(by_source.values(), key=lambda s: -s["chunks"])
        by_type: dict[str, int] = {}
        for s in sources:
            t = s.get("source_type") or "unknown"
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "available": True,
            "total_unique_sources": len(sources),
            "by_type": by_type,
            "sources": sources[:limit],
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


# ── Surgical purge by keyword ──────────────────────────────────────────────

#: R-F3478 — every collection a SEARCH can return content from, and therefore every
#: collection an ERASURE must reach. The labels match the ones the retrieval path passes
#: to `_sync_query_collection`, so the two surfaces are comparable by a test rather than
#: by whoever remembers to update both.
_SEARCHABLE_COLLECTION_LABELS = ("documents", "facts", "documents_cold")


#: R-F3484 — the metadata key that makes erasure PROVABLE.
#:
#: Erasure by keyword substring cannot prove completeness: a subject's data survives an
#: alias, a transliteration, an initial, a misspelling, or any encoding the needle does
#: not literally appear in. Under UK GDPR Art. 17 the controller must be able to show the
#: request was fulfilled — "we grepped for the name" is not that showing.
#:
#: A record written WITH this key can be erased exactly and the receipt can prove it. A
#: record written without one can only be swept best-effort, and the receipt must say so
#: rather than implying completeness.
DATA_SUBJECT_KEY = "data_subject_key"
LAWFUL_BASIS_KEY = "lawful_basis"
RETENTION_CLASS_KEY = "retention_class"
#: R-F3492 — WHICH jurisdiction's law governs this record. Retention periods and
#: residency obligations both depend on it, so it cannot be inferred from the storage
#: region: where data SITS and whose law GOVERNS it are different questions, and
#: conflating them is what makes a cross-border transfer invisible.
DATA_JURISDICTION_KEY = "data_jurisdiction"


def _searchable_collections() -> list[tuple[str, object]]:
    """(label, collection) for every searchable collection, live objects included.

    Shared by retrieval-coverage assertions and by `purge_by_keywords`. A collection
    that is `None` (chromadb unavailable, or cold not yet created) is still returned so
    the caller records that it was CONSIDERED — an absent collection must not read the
    same as one that was scanned and matched nothing.
    """
    return [
        ("documents", _documents_collection),
        ("facts", _facts_collection),
        ("documents_cold", _documents_cold_collection),
    ]


#: R-F3492 — where this instance actually stores data. `fly.toml` declares
#: `primary_region = 'lhr'`, but a committed config file is a claim about intent, not
#: evidence about a running replica, so the value is read from the environment the
#: process is actually in and defaults to the declared region only as a last resort.
def storage_region() -> str:
    return (os.getenv("FLY_REGION")
            or os.getenv("ARIA_STORAGE_REGION")
            or "lhr").strip().lower()


#: Regions that keep personal data inside the UK/EEA perimeter for Chapter V purposes.
#: Deliberately short and explicit: an unknown region is treated as OUTSIDE, because
#: guessing in the permissive direction is how an unlawful transfer goes unnoticed.
_UK_EEA_REGIONS = frozenset({
    "lhr", "cdg", "ams", "fra", "mad", "arn", "waw", "otp", "dub",
})


def jurisdiction_of_region(region: str) -> str:
    """UK_EEA | OTHER — the perimeter a storage region sits in."""
    return "UK_EEA" if str(region or "").strip().lower() in _UK_EEA_REGIONS else "OTHER"


#: R-F3493 — UK statutory retention profile. RESEARCHED AND CITED, OPT-IN, NOT LAW ADVICE.
#:
#: Each entry is (days, legal_basis). These are not invented: every period below traces to
#: a named instrument, and where the instrument sets a duty rather than a ceiling that is
#: stated. Enable with ARIA_RETENTION_PROFILE=uk_statutory_v1. It is OFF by default because
#: applying a retention schedule is a controller's decision, and one that depends on facts
#: this code cannot see (whether a relationship has ended, whether proceedings are in
#: contemplation).
#:
#: NOTE ON THE TRIGGER, which is the part most easily got wrong: MLR 2017 reg 40 runs its
#: five years from the END OF THE BUSINESS RELATIONSHIP or completion of the transaction —
#: NOT from the date a document was ingested. `retention_review` measures from
#: `ingested_at`, so for CDD material it reports the EARLIEST possible due date. Treat a
#: `due` count as "review these", never as "these are unlawful to hold".
_RETENTION_PROFILES: dict[str, dict[str, tuple[int, str]]] = {
    "uk_statutory_v1": {
        # Money Laundering, Terrorist Financing and Transfer of Funds (Information on the
        # Payer) Regulations 2017, reg 40: keep 5 years from completion of the transaction
        # or the end of the business relationship; a 10-year cap applies to transactions
        # within a relationship. Reg 40 also imposes a DELETION DUTY at the end of that
        # period, subject to three exceptions (legal requirement/proceedings, consent,
        # reasonable grounds for anticipated proceedings).
        "uk:cdd_evidence": (1825, "MLR 2017 reg 40 — 5y from end of relationship; "
                                  "deletion duty applies, 3 exceptions"),
        "uk:dd_evidence": (1825, "MLR 2017 reg 40 — treated as CDD material"),
        # BS 7858:2019 screening: unsuccessful applicants 12 months; retained during
        # employment; specified records 7 years after employment ends. Encoded as periods
        # only — no standard text is reproduced (see the R-F3466 assurance review).
        "uk:vetting_unsuccessful": (365, "BS 7858:2019 — 12 months for unsuccessful "
                                         "applicants"),
        "uk:vetting_leaver": (2555, "BS 7858:2019 — 7 years after employment ends"),
    },
}


def _profile_periods() -> dict[str, int]:
    """The active statutory profile's periods, or {} when none is enabled."""
    name = str(os.getenv("ARIA_RETENTION_PROFILE", "") or "").strip().lower()
    if not name:
        return {}
    prof = _RETENTION_PROFILES.get(name)
    if prof is None:
        logger.warning("[R-F3493] unknown retention profile %r — ignoring", name)
        return {}
    return {k: v[0] for k, v in prof.items()}


def retention_bases() -> dict[str, str]:
    """key -> the legal basis for its period, for display next to any due count.

    A retention number a controller cannot trace to an instrument is a number they
    cannot defend, so the citation travels with the period rather than living in a
    comment nobody reads.
    """
    name = str(os.getenv("ARIA_RETENTION_PROFILE", "") or "").strip().lower()
    prof = _RETENTION_PROFILES.get(name) or {}
    return {k: v[1] for k, v in prof.items()}


def _retention_periods() -> dict[str, int]:
    """R-F3490/R-F3492/R-F3493 — retention period keys.

    R-F3492 makes the key JURISDICTION-SPECIFIC, because a retention period is a
    function of (data category, jurisdiction) and not of category alone: the same
    category of personal data is lawfully kept for different periods under UK, EU and
    other regimes. A flat class silently applied one country's answer everywhere, which
    is wrong the moment a second jurisdiction is served.

    Accepted keys, most specific first:
      ``uk:chat_notebook=365``   — this class, this jurisdiction
      ``chat_notebook=365``      — this class, ANY jurisdiction (explicit fallback)


    DELIBERATELY EMPTY BY DEFAULT. A retention period is a legal and commercial decision
    about a specific data category in a specific jurisdiction; inventing "7 years" in code
    and letting a report present it as policy is the same defect class as a fabricated
    finding. A class with no configured period is REPORTED as undecided, not quietly
    treated as indefinite.

    Configure with ARIA_RETENTION_PERIODS_DAYS, e.g.
    ``uk:dd_evidence=2555,uk:chat_notebook=365,de:chat_notebook=730``.
    """
    raw = str(os.getenv("ARIA_RETENTION_PERIODS_DAYS", "") or "").strip()
    # R-F3493 — a statutory profile supplies the baseline; explicit env entries OVERRIDE
    # it. The operator's own decision always wins over a researched default.
    out: dict[str, int] = dict(_profile_periods())
    for part in raw.split(","):
        if "=" not in part:
            continue
        name, _, days = part.partition("=")
        try:
            out[name.strip().lower()] = int(days.strip())
        except ValueError:
            logger.warning("[R-F3490] ignoring malformed retention period: %r", part)
    return out


def _period_for(retention_class: str, jurisdiction: str,
                periods: dict[str, int]) -> int | None:
    """R-F3492 — the period for this class IN THIS JURISDICTION, or None if undecided.

    Most specific wins, and the fallback must be DECLARED. A bare ``class=days`` entry
    means "this period applies in any jurisdiction" — an operator saying so explicitly.
    What never happens is one country's period being applied to another's data because
    it was the only one configured.
    """
    rclass = str(retention_class or "").strip().lower()
    juris = str(jurisdiction or "").strip().lower()
    if not rclass:
        return None
    if juris:
        specific = periods.get(f"{juris}:{rclass}")
        if specific is not None:
            return specific
    return periods.get(rclass)


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def retention_review(*, now_iso: str = "") -> dict:
    """R-F3490 — WHAT IS DUE FOR REVIEW. Reports; never deletes.

    UK GDPR Art. 5(1)(e) requires personal data be kept no longer than necessary, and the
    ICO's route to that is a retention schedule with PERIODIC REVIEW and erasure *or
    anonymisation* — not an automatic timer.

    An automatic timer is also unavailable here: CLAUDE.md §7 forbids TTL, oldest-first
    prune and eviction outright. Both rules are satisfiable at once, because the thing
    Art. 5(1)(e) actually needs is a controller who can SEE what is overdue and decide.
    This function is that surface, and it takes no destructive action of any kind.

    Three answers, and the last two matter as much as the first:
      * `due`            — personal data past its configured period, per class
      * `no_period_set`  — a retention CLASS was declared but no period configured, so
                           nothing can be said to be overdue. Undecided, not compliant.
      * `unclassified`   — personal data with NO retention class at all. This is the
                           population a controller most needs to know about, and the one
                           an automatic timer would have silently ignored.
    """
    if not await _ensure_async():
        return {"available": False, "reason": "rag_store_unavailable"}

    from datetime import datetime, timedelta, timezone as _tz
    try:
        now = (datetime.fromisoformat(now_iso) if now_iso
               else datetime.now(_tz.utc))
    except ValueError:
        now = datetime.now(_tz.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_tz.utc)

    periods = _retention_periods()
    _region = storage_region()
    _region_perimeter = jurisdiction_of_region(_region)
    import asyncio as _aio
    _PAGE = 500

    def _scan(coll, label: str) -> tuple[dict, str]:
        acc = {"due": 0, "no_period_set": 0, "unclassified": 0, "within_period": 0,
               "personal_records": 0, "no_jurisdiction": 0, "residency_mismatch": 0,
               "by_jurisdiction": {}, "undecided_keys": set()}
        if coll is None:
            return acc, ""
        offset = 0
        while True:
            try:
                page = coll.get(include=["metadatas"], limit=_PAGE, offset=offset)
            except Exception as e:
                return acc, f"{label}: {str(e)[:160]}"
            ids = page.get("ids") or []
            for m in (page.get("metadatas") or []):
                if not isinstance(m, dict) or not m.get("personal_data"):
                    continue
                acc["personal_records"] += 1
                rclass = str(m.get(RETENTION_CLASS_KEY) or "").strip()
                # R-F3492 — the record's OWN jurisdiction decides which period applies.
                _juris = str(m.get(DATA_JURISDICTION_KEY) or "").strip().lower()
                if _juris:
                    acc["by_jurisdiction"][_juris] = acc["by_jurisdiction"].get(_juris, 0) + 1
                else:
                    acc["no_jurisdiction"] += 1
                # Chapter V: personal data for one jurisdiction sitting in a region
                # outside the UK/EEA perimeter is a transfer that needs a lawful basis.
                # Reported, never assumed lawful.
                if _juris in ("uk", "gb", "eu") and _region_perimeter != "UK_EEA":
                    acc["residency_mismatch"] += 1
                if not rclass:
                    acc["unclassified"] += 1
                    continue
                days = _period_for(rclass, _juris, periods)
                if days is None:
                    acc["no_period_set"] += 1
                    acc["undecided_keys"].add(f"{_juris or 'any'}:{rclass}")
                    continue
                stamped = str(m.get("ingested_at") or "")
                try:
                    written = datetime.fromisoformat(stamped)
                    if written.tzinfo is None:
                        written = written.replace(tzinfo=_tz.utc)
                except ValueError:
                    acc["no_period_set"] += 1
                    continue
                if written + timedelta(days=days) <= now:
                    acc["due"] += 1
                else:
                    acc["within_period"] += 1
            if len(ids) < _PAGE:
                break
            offset += _PAGE
        return acc, ""

    per_collection: dict[str, dict] = {}
    scan_errors: list[str] = []
    totals = {"due": 0, "no_period_set": 0, "unclassified": 0, "within_period": 0,
              "personal_records": 0, "no_jurisdiction": 0, "residency_mismatch": 0}
    by_juris: dict[str, int] = {}
    undecided: set[str] = set()
    for label, coll in _searchable_collections():
        acc, err = await _aio.to_thread(_scan, coll, label)
        if err:
            scan_errors.append(err)
        for _j, _n in (acc.get("by_jurisdiction") or {}).items():
            by_juris[_j] = by_juris.get(_j, 0) + _n
        undecided |= acc.get("undecided_keys") or set()
        acc["undecided_keys"] = sorted(acc.get("undecided_keys") or set())
        per_collection[label] = acc
        for k in totals:
            totals[k] += acc[k]

    # ── R-F3492 — REMIND. A review nobody sees is not a review. ──────────────
    #
    # Art. 5(2) accountability sits with the controller, so an overdue population, an
    # undecided period, or personal data sitting outside its own perimeter has to reach
    # an operator rather than wait to be queried. Wired to the brain (§21a) so it
    # surfaces on the operator's existing gap surface; the payload is returned too so a
    # dashboard or a super-admin view can render it directly.
    _reminders: list[str] = []
    if totals["due"]:
        _reminders.append(
            f"{totals['due']} personal record(s) past their configured retention period "
            f"— erase or anonymise (nothing is deleted automatically)")
    if totals["unclassified"]:
        _reminders.append(
            f"{totals['unclassified']} personal record(s) carry NO retention class, so "
            f"no period can apply to them")
    if undecided:
        _reminders.append(
            f"no retention period configured for: {', '.join(sorted(undecided)[:8])} "
            f"— set ARIA_RETENTION_PERIODS_DAYS (jurisdiction:class=days)")
    _mlr_due = any(k.endswith("cdd_evidence") or k.endswith("dd_evidence")
                   for k in retention_bases())
    if totals["due"] and _mlr_due:
        _reminders.append(
            "some overdue records fall under MLR 2017 reg 40, which imposes a DELETION "
            "DUTY (not merely a ceiling) subject to three exceptions — legal "
            "requirement/proceedings, consent, or reasonable grounds for anticipated "
            "proceedings. Deciding whether an exception applies is a controller "
            "decision; ARIA deletes nothing on its own")
    if totals["residency_mismatch"]:
        _reminders.append(
            f"{totals['residency_mismatch']} UK/EU personal record(s) are stored in "
            f"region {_region!r} ({_region_perimeter}) — a Chapter V transfer needs a "
            f"lawful basis")
    if _reminders:
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="rag_store",
                detail="[R-F3492] retention/residency review needs an operator: "
                       + "; ".join(_reminders)[:600],
                gap_type="knowledge_gap",
                source="rag_store:R-F3492",
            )
        except Exception as _wf:
            logger.debug("[R-F3492] reminder wiring failed: %s", _wf)

    return {
        "available": True,
        "as_of": now.isoformat(),
        "configured_classes": sorted(periods),
        # R-F3493 — the legal basis travels with the number.
        "retention_profile": (os.getenv("ARIA_RETENTION_PROFILE", "") or "").strip().lower(),
        "retention_bases": retention_bases(),
        # R-F3492 — jurisdiction is now first-class in the answer.
        "storage_region": _region,
        "storage_perimeter": _region_perimeter,
        "by_jurisdiction": by_juris,
        "undecided_period_keys": sorted(undecided),
        "reminders": _reminders,
        "needs_operator": bool(_reminders),
        **totals,
        "per_collection": per_collection,
        "scan_errors": scan_errors,
        # No destructive action is taken or scheduled. Say so, so nobody assumes it was.
        "action_taken": "none",
        "note": (
            "Review only — nothing is deleted, and no deletion is scheduled (§7 forbids "
            "TTL/prune/eviction). Act on `due` explicitly via erase_by_subject, or "
            "anonymise. `unclassified` personal records have no retention policy at all "
            "and `no_period_set` classes have no configured period: both are undecided, "
            "not compliant."
        ),
    }


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def erase_by_subject(subject_key: str, *, dry_run: bool = False) -> dict:
    """R-F3484 — erase every chunk written for one data subject, PROVABLY.

    THE GAP THIS CLOSES. Until now the only erasure was `purge_by_keywords`, a substring
    sweep. That cannot fulfil a UK GDPR Art. 17 request in a way a controller can
    demonstrate: the subject's data survives inside an alias, a transliteration, an
    initial, a misspelling, or any phrasing the needle does not literally appear in. The
    caller could not tell a complete erasure from a lucky one.

    Matching on `data_subject_key` metadata is exact, so the receipt is EVIDENCE: every
    chunk carrying the key is removed, across every collection a search can read from
    (the R-F3478 invariant — erasure surface equals retrieval surface), and the per
    collection counts show what was covered.

    HONEST LIMIT, returned in the receipt rather than buried here: this reaches records
    that were WRITTEN with a subject key. Historical records predate the key and are not
    reachable this way; `coverage` says `keyed` and `unkeyed_records_exist` warns when a
    keyword sweep is still required to approach them. Erasure that overstates itself is
    worse than erasure that fails, because the controller stops looking.

    Consistent with CLAUDE.md §7: this deletes only on an explicit, attributed request.
    It introduces no TTL, no prune and no eviction.
    """
    key = (subject_key or "").strip()
    if not key:
        return {"available": True, "erased": 0, "coverage": "none",
                "reason": "subject_key_required", "per_collection": {}}
    if not await _ensure_async():
        return {"available": False, "reason": "rag_store_unavailable",
                "erased": 0, "coverage": "none", "per_collection": {}}

    import asyncio as _aio
    _PAGE = 500

    def _scan(coll, label: str) -> tuple[int, list[str], str]:
        if coll is None:
            return 0, [], ""
        ids_all: list[str] = []
        metas_all: list[dict] = []
        offset = 0
        while True:
            try:
                page = coll.get(include=["metadatas"], limit=_PAGE, offset=offset)
            except Exception as e:
                # R-F3389 — "erased 0" must never be ambiguous between "nothing matched"
                # and "I could not look". On an erasure request that difference is legal.
                return len(ids_all), [], f"{label}: {str(e)[:160]}"
            p_ids = page.get("ids") or []
            ids_all.extend(p_ids)
            metas_all.extend(page.get("metadatas") or [])
            if len(p_ids) < _PAGE:
                break
            offset += _PAGE
        hits = [
            doc_id for i, doc_id in enumerate(ids_all)
            if isinstance(metas_all[i] if i < len(metas_all) else None, dict)
            and str((metas_all[i] or {}).get(DATA_SUBJECT_KEY) or "").strip() == key
        ]
        return len(ids_all), hits, ""

    per_collection: dict[str, dict] = {}
    scan_errors: list[str] = []
    pending: list[tuple[object, list[str]]] = []
    total_scanned = 0
    for label, coll in _searchable_collections():
        scanned, hits, err = await _aio.to_thread(_scan, coll, label)
        if err:
            scan_errors.append(err)
        total_scanned += scanned
        per_collection[label] = {
            "scanned": scanned, "erased": len(hits), "present": coll is not None,
        }
        if hits and coll is not None:
            pending.append((coll, hits))

    erased = sum(v["erased"] for v in per_collection.values())
    if not dry_run:
        for coll, ids in pending:
            await _aio.to_thread(coll.delete, ids=ids)
        if erased:
            logger.warning(
                "rag_store.erase_by_subject: erased %d chunk(s) for subject_key=%s",
                erased, key[:64],
            )

    return {
        "available": True,
        "subject_key": key,
        "erased": erased,
        "scanned": total_scanned,
        "per_collection": per_collection,
        "dry_run": dry_run,
        # EVIDENCE, not decoration: `keyed` means every match was exact.
        "coverage": "keyed",
        # The honest caveat a controller must act on.
        "note": (
            "Exact-match erasure over records written with a data_subject_key. Records "
            "stored before subject keying, or without one, are NOT reachable this way "
            "and require a separate best-effort sweep, which cannot prove completeness."
        ),
        "scan_errors": scan_errors,
    }


@fail_wire(module="rag_store", gap_type="embedder_failure")
async def purge_by_keywords(
    keywords: list[str],
    *,
    dry_run: bool = False,
) -> dict:
    """Remove every chunk in EVERY SEARCHABLE COLLECTION whose stored text
    contains any of the given keywords (case-insensitive substring).

    R-F3478 — THE ERASURE SURFACE MUST EQUAL THE RETRIEVAL SURFACE.

    This scanned `aria_documents` and `aria_facts` only. Retrieval also queries
    `aria_documents_cold` (see the `documents_cold` query task in `search`), because
    R-F2989 offloads the oldest ~10% of documents there and searches consult both. So
    material offloaded to cold SURVIVED a purge and stayed retrievable, while the purge
    returned removed-counts and reported success.

    That is the erasure analogue of a false clean, and it is worse than a purge that
    fails: a caller acting on a right-to-erasure request was told the content was gone.

    The collection list now comes from `_searchable_collections()`, which is the single
    source of truth shared with the retrieval path — adding a fourth collection cannot
    silently escape erasure again, and `test_rf3478_*` fails if one does.

    Background: 2026-04-24 OpenClaw incident — ARIA's brain_hook absorbed
    a fabricated brave_answer about a fictional "OpenClaw" gateway into
    the RAG store via pay-once-remember-forever. After blocking surfacing
    on self-infra questions, the chunks are still in chromadb and could
    leak back into adjacent semantic searches. ChromaDB doesn't support
    keyword-substring `where` filters server-side, so we materialise the
    documents text + ids client-side, filter in Python, then delete by ID.

    For 8813 documents / 32614 facts (typical fly volume size) this
    materialisation runs in <2s.

    Returns:
        {scanned_docs, scanned_facts, scanned_cold, removed_docs, removed_facts,
         removed_cold, per_collection, dry_run, keywords_used,
         removed_samples: [...], scan_errors: [...]}
    """
    if not await _ensure_async():
        return {
            "available": False,
            "reason": "rag_store_unavailable",
            "scanned_docs": 0, "scanned_facts": 0, "scanned_cold": 0,
            "removed_docs": 0, "removed_facts": 0, "removed_cold": 0,
            "dry_run": dry_run, "keywords_used": [],
            "removed_samples": [],
        }
    if not keywords:
        return {
            "available": True,
            "scanned_docs": 0, "scanned_facts": 0, "scanned_cold": 0,
            "removed_docs": 0, "removed_facts": 0, "removed_cold": 0,
            "dry_run": dry_run, "keywords_used": [],
            "removed_samples": [],
        }
    needles = [k.lower().strip() for k in keywords if k and k.strip()]
    if not needles:
        return {
            "available": True,
            "scanned_docs": 0, "scanned_facts": 0, "scanned_cold": 0,
            "removed_docs": 0, "removed_facts": 0, "removed_cold": 0,
            "dry_run": dry_run, "keywords_used": [],
            "removed_samples": [],
        }

    import asyncio as _aio

    removed_samples: list[dict] = []
    counts = {"scanned_docs": 0, "scanned_facts": 0, "scanned_cold": 0,
              "removed_docs": 0, "removed_facts": 0, "removed_cold": 0}

    # R-F3389 — PAGE through the collection. The previous version issued one
    # unbounded `coll.get(include=[...])`, materialising every row in a single
    # query. On documents (~21k) that survived; on facts (~32k) it exceeded
    # SQLite's variable limit and threw "too many SQL variables". The except
    # logged a warning and returned (0, []), so the failure degraded to
    # "nothing matched" — indistinguishable from "nothing to remove". The purge
    # that exists to remove fabricated content from ARIA's memory silently did
    # nothing to one of its two collections, while reporting success.
    #
    # Pagination, not a bigger cap: raising a limit only moves the cliff.
    _PAGE = 500

    def _scan_collection(coll, label: str) -> tuple[int, list[str], str]:
        if coll is None:
            return 0, [], ""
        ids_all: list[str] = []
        docs_all: list[str] = []
        metas_all: list[dict] = []
        offset = 0
        while True:
            try:
                page = coll.get(
                    include=["documents", "metadatas"], limit=_PAGE, offset=offset
                )
            except Exception as e:
                # Report it. "removed 0" must never be ambiguous between
                # "nothing matched" and "I could not look".
                logger.warning(
                    "rag_store.purge_by_keywords: %s.get failed at offset %d: %s",
                    label, offset, e,
                )
                return len(ids_all), [], f"{label}: {str(e)[:160]}"
            p_ids = page.get("ids") or []
            ids_all.extend(p_ids)
            docs_all.extend(page.get("documents") or [])
            metas_all.extend(page.get("metadatas") or [])
            if len(p_ids) < _PAGE:
                break
            offset += _PAGE

        n = len(ids_all)
        to_delete: list[str] = []
        for i, doc_id in enumerate(ids_all):
            text_lower = (docs_all[i] if i < len(docs_all) else "") or ""
            text_lower = text_lower.lower()
            hit = next((nd for nd in needles if nd in text_lower), None)
            if hit is None:
                continue
            to_delete.append(doc_id)
            if len(removed_samples) < 25:
                m = metas_all[i] if i < len(metas_all) else {}
                removed_samples.append({
                    "id": doc_id,
                    "collection": label,
                    "matched_keyword": hit,
                    "source": (m.get("source", "") or "")[:120] if isinstance(m, dict) else "",
                    "title": (m.get("title", "") or "")[:120] if isinstance(m, dict) else "",
                    "preview": (docs_all[i] if i < len(docs_all) else "")[:160],
                })
        return n, to_delete, ""

    try:
        # R-F3478 — iterate the SHARED list rather than two hardcoded calls. The cold
        # collection was missing here while retrieval queried it, so purged material
        # stayed searchable and the purge still reported success.
        scan_errors: list[str] = []
        per_collection: dict[str, dict] = {}
        pending_deletes: list[tuple[object, list[str]]] = []
        for _label, _coll in _searchable_collections():
            _scanned, _ids_to_del, _err = await _aio.to_thread(
                _scan_collection, _coll, _label
            )
            # R-F3389 — surface any collection we could not read, so a caller can
            # tell an empty result from a blind one.
            if _err:
                scan_errors.append(_err)
            per_collection[_label] = {
                "scanned": _scanned,
                "removed": len(_ids_to_del),
                # An absent collection is NOT a scanned-and-clean one.
                "present": _coll is not None,
            }
            if _ids_to_del and _coll is not None:
                pending_deletes.append((_coll, _ids_to_del))

        counts["scanned_docs"] = per_collection["documents"]["scanned"]
        counts["scanned_facts"] = per_collection["facts"]["scanned"]
        counts["scanned_cold"] = per_collection["documents_cold"]["scanned"]
        counts["removed_docs"] = per_collection["documents"]["removed"]
        counts["removed_facts"] = per_collection["facts"]["removed"]
        counts["removed_cold"] = per_collection["documents_cold"]["removed"]

        if not dry_run:
            for _coll, _ids in pending_deletes:
                await _aio.to_thread(_coll.delete, ids=_ids)
            logger.warning(
                "rag_store.purge_by_keywords: removed %d docs + %d facts + %d cold "
                "matching %s",
                counts["removed_docs"], counts["removed_facts"],
                counts["removed_cold"], needles,
            )
    except Exception as e:
        logger.warning("rag_store.purge_by_keywords failed: %s", e)
        return {
            "available": True,
            "error": str(e),
            **counts,
            "dry_run": dry_run,
            "keywords_used": needles,
            "removed_samples": removed_samples,
        }

    return {
        "available": True,
        **counts,
        "dry_run": dry_run,
        "keywords_used": needles,
        "removed_samples": removed_samples,
        # R-F3478 — per-collection detail, so "removed 0" from a collection that was
        # ABSENT is distinguishable from one that was scanned and matched nothing.
        "per_collection": per_collection,
        # R-F3484 — SAY WHAT THIS CAN AND CANNOT PROVE. A substring sweep misses an
        # alias, a transliteration, an initial or a misspelling, so it can never
        # demonstrate that a subject's data is gone. For an Art. 17 request use
        # erase_by_subject(), which matches exactly and returns coverage="keyed".
        "coverage": "keyword_best_effort",
        "completeness_caveat": (
            "Substring matching cannot prove completeness: content referring to the "
            "same subject under an alias, transliteration, initial or misspelling is "
            "NOT removed. Do not record this as a fulfilled erasure request."
        ),
        # R-F3389 — empty means "nothing matched" ONLY when this is empty too.
        "scan_errors": scan_errors,
    }


# ── Backfill from existing knowledge base + ledger ─────────────────────────

@fail_wire(module="rag_store", gap_type="embedder_failure")
async def backfill_from_existing() -> dict:
    """One-shot backfill: pull every existing fact + ledger signal into the
    RAG store so we have a useful baseline from minute one of the first deploy.

    Idempotent — chromadb upserts so re-running is safe.
    """
    if not await _ensure_async():
        return {"ok": False, "error": "rag_store_unavailable"}

    from . import knowledge as kb
    from . import intel_ledger

    facts_added = 0
    ledger_added = 0

    # Backfill facts
    try:
        db = await kb._load()
        facts = db.get("facts", [])
        for f in facts:
            ok = await ingest_fact(
                fact_id=f.get("id") or _hash_id(f.get("topic", ""), f.get("source", "")),
                topic=f.get("topic", ""),
                content=f.get("content", ""),
                confidence=f.get("confidence", "ASSESSED"),
                source=f.get("source", ""),
            )
            if ok:
                facts_added += 1
        logger.info("RAG backfill: indexed %d facts", facts_added)
    except Exception as e:
        logger.warning("RAG backfill facts failed: %s", e)

    # Backfill ledger signals as documents
    try:
        if intel_ledger._cache:
            signals = intel_ledger._cache.get("signals", [])
            for i, s in enumerate(signals):
                text = s.get("text") or ""
                if not text or len(text) < MIN_CHUNK_SIZE:
                    continue
                countries = s.get("countries") or []
                market = countries[0] if countries else ""
                result = await ingest_document(
                    text=text,
                    source=f"ledger:{s.get('source', 'unknown')}",
                    source_type="ledger",
                    title=text[:80],
                    url=s.get("url", ""),
                    market=market,
                    extra_metadata={
                        "signal_type": s.get("type", ""),
                        "severity": s.get("severity", ""),
                        "ts": s.get("ts", ""),
                    },
                )
                if result.get("ingested"):
                    ledger_added += 1
        logger.info("RAG backfill: indexed %d ledger signals", ledger_added)
    except Exception as e:
        logger.warning("RAG backfill ledger failed: %s", e)

    stats = await get_stats()
    return {
        "ok": True,
        "facts_added": facts_added,
        "ledger_added": ledger_added,
        "stats": stats,
    }
