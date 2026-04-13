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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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


# ── Lazy chromadb client ───────────────────────────────────────────────────

_client = None
_documents_collection = None
_facts_collection = None
_chromadb_failed = False
_init_lock = asyncio.Lock()


def _get_client():
    """Lazy-load the chromadb persistent client + collections.

    All three globals (_client, _documents_collection, _facts_collection)
    must succeed together or we roll back to None — otherwise callers can
    see `_client is not None` but hit `_documents_collection.upsert`
    against None, which is the production crash the audit fixed.
    """
    global _client, _documents_collection, _facts_collection, _chromadb_failed
    if _client is not None and _documents_collection is not None and _facts_collection is not None:
        return _client
    if _chromadb_failed:
        return None

    # Local vars — commit to globals only if every step succeeds.
    local_client = None
    local_docs = None
    local_facts = None
    try:
        import chromadb
        from chromadb.config import Settings
        Path(RAG_PATH).mkdir(parents=True, exist_ok=True)
        local_client = chromadb.PersistentClient(
            path=RAG_PATH,
            settings=Settings(anonymized_telemetry=False, allow_reset=False),
        )
        from chromadb.utils import embedding_functions
        embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL_NAME,
        )
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
        logger.info(
            "RAG store ready at %s — documents: %d, facts: %d",
            RAG_PATH, local_docs.count(), local_facts.count(),
        )
    except ImportError:
        _chromadb_failed = True
        logger.warning("chromadb not installed — RAG store unavailable. Run: pip install chromadb")
        return None
    except Exception as e:
        _chromadb_failed = True
        logger.warning("RAG store init failed: %s", e, exc_info=True)
        return None

    # Commit atomically.
    _client = local_client
    _documents_collection = local_docs
    _facts_collection = local_facts
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
    if _chromadb_failed:
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
    """
    if not text:
        return []
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= chunk_size:
        return [text] if len(text) >= MIN_CHUNK_SIZE else []

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

async def ingest_document(
    text: str,
    *,
    source: str,
    source_type: str = "unknown",
    title: str = "",
    url: str = "",
    market: str = "",
    extra_metadata: dict | None = None,
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
    if not await _ensure_async():
        return {"ingested": False, "error": "rag_store_unavailable"}
    if not text or len(text.strip()) < MIN_CHUNK_SIZE:
        return {"ingested": False, "reason": "text_too_short"}

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
    if extra_metadata:
        for k, v in extra_metadata.items():
            if isinstance(v, (str, int, float, bool)):
                base_meta[str(k)[:50]] = v if not isinstance(v, str) else v[:300]

    ids = []
    metadatas = []
    documents = []
    for i, chunk in enumerate(chunks):
        cid = _hash_id(chunk, source)
        meta = dict(base_meta)
        meta["chunk_index"] = i
        meta["chunk_count"] = len(chunks)
        ids.append(cid)
        metadatas.append(meta)
        documents.append(chunk)

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
    if not await _ensure_async():
        return False
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
        logger.debug("RAG fact ingest failed: %s", e)
        return False


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


async def search(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    source_type: str | None = None,
    market: str | None = None,
    include_facts: bool = True,
    include_documents: bool = True,
) -> list[dict]:
    """Hybrid retrieval over the RAG store.

    Returns a ranked list of chunks with their metadata. Each item:
        {text, score, source, source_type, title, url, market, ingested_at, collection}

    Optional filters narrow the search to a source_type or market.
    """
    if not await _ensure_async():
        return []
    if not query or len(query.strip()) < 3:
        return []

    results: list[dict] = []
    import asyncio as _aio

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
            kwargs = {"query_texts": [query], "n_results": top_k}
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
                # Apply recency boost
                ts = meta.get("ts_epoch") if isinstance(meta, dict) else None
                score = similarity * _recency_boost(ts)
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
                    "collection": name,
                })
        except Exception as e:
            logger.debug("RAG query on %s failed: %s", name, e)

    # Offload the whole query (embedding + vector search) to a thread so
    # the event loop can continue serving other requests. chromadb's query
    # path re-embeds the query via sentence-transformers, which is sync
    # and GIL-bound.
    if include_documents:
        await _aio.to_thread(_sync_query_collection, _documents_collection, "documents")
    if include_facts:
        await _aio.to_thread(_sync_query_collection, _facts_collection, "facts")

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


async def get_rag_context(
    query: str,
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    top_k: int = DEFAULT_TOP_K,
) -> str:
    """Build a formatted context string for LLM prompt injection.

    Returns the top retrieved passages with citations, capped at max_chars.
    Designed to slot into aria_engine's context layer pipeline.
    """
    if not await _ensure_async():
        return ""
    results = await search(query, top_k=top_k)
    if not results:
        return ""

    # M3: flag stale RAG chunks. Passages older than 90 days for
    # fast-moving domains (sanctions, procurement, officeholders) are
    # shown to the LLM with a ⚠ STALE marker so the model knows not to
    # present them as current intelligence.
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

    lines = ["\n\n[RAG RETRIEVED — proprietary intelligence indexed from your sources. Respect ⚠ STALE markers — do not present stale chunks as current fact.]"]
    total = 0
    for r in results:
        cite_parts = []
        if r.get("title"):
            cite_parts.append(r["title"])
        if r.get("source"):
            cite_parts.append(r["source"])
        if r.get("ingested_at"):
            cite_parts.append(r["ingested_at"][:10])
        cite = " | ".join(cite_parts) if cite_parts else "unknown source"
        stale = _staleness_marker(r.get("ingested_at", ""))
        body = f"\n• [{r['score']:.2f}]{stale} {r['text'][:600]}\n  ↳ source: {cite}"
        if total + len(body) > max_chars:
            break
        lines.append(body)
        total += len(body)
    return "\n".join(lines)


# ── Public API: stats + maintenance ────────────────────────────────────────

async def get_stats() -> dict:
    """Report on the RAG store state."""
    if not await _ensure_async():
        return {
            "available": False,
            "reason": "chromadb not installed or init failed",
            "path": RAG_PATH,
        }
    try:
        import asyncio as _aio
        doc_count = await _aio.to_thread(_documents_collection.count) if _documents_collection else 0
        fact_count = await _aio.to_thread(_facts_collection.count) if _facts_collection else 0
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


# ── Backfill from existing knowledge base + ledger ─────────────────────────

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
