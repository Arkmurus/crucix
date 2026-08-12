"""
Coding RAG Indexer — persistent memory for ARIA's autonomous coding (R-F1531).

Indexes fixes, failures, codebase structure, and constitutional rules into
chromadb collections so ARIA can retrieve relevant past experience when
generating new fixes. Reuses the existing chromadb client + shared embedding
function from rag_store.py — no second model load, no new dependencies.

Four collections:
  1. coding_fixes       — successful fixes (gap_type, module, approach)
  2. coding_failures    — failed attempts before success (what NOT to do)
  3. coding_structure   — AST-chunked module structure (function boundaries)
  4. coding_constitutional — constitutional rules for semantic querying

Wiring:
  - self_improve.py: index_fix() after successful deploy
  - self_coder.py:   index_failure() after failed attempt
  - SovereignLLM:    query_relevant_fixes() before code generation

Thread safety:
  - _ensure() uses a threading.Lock (chromadb operations are sync/blocking)
  - All public functions are sync but safe to call from async contexts via
    asyncio.to_thread (the caller's responsibility — matches rag_store.py
    convention where the caller wraps blocking ops in to_thread).
"""
from __future__ import annotations
from .engine_wiring import wire_success

import ast
import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.coding_rag")

# R-F1531: wire module health to the brain on import
try:
    from .engine_wiring import wire_success as _ws1531, wire_failure
    _ws1531(
        module="coding_rag_indexer",
        summary="Coding RAG Indexer active — fix/failure memory ready",
        source_id="coding_rag_indexer:R-F1531",
    )
except Exception:
    pass

# ── Data records ──────────────────────────────────────────────────────────────


@dataclass
class FixRecord:
    """Record of a successful fix."""
    r_number: str
    title: str
    gap_type: str
    module: str
    problem_description: str
    approach: str
    files_changed: list[str]
    tests_passed: int
    timestamp: str
    outcome: str = "success"


@dataclass
class FailureRecord:
    """Record of a failed attempt before success."""
    r_number: str
    attempt_number: int
    error_type: str
    error_message: str
    why_failed: str
    next_approach: str
    timestamp: str


# ── Collection names ──────────────────────────────────────────────────────────

_COLLECTION_FIXES = "coding_fixes"
_COLLECTION_FAILURES = "coding_failures"
_COLLECTION_STRUCTURE = "coding_structure"
_COLLECTION_CONSTITUTIONAL = "coding_constitutional"

# ── Lazy globals (mirrors rag_store.py pattern) ──────────────────────────────

_client = None
_fixes_collection = None
_failures_collection = None
_structure_collection = None
_constitutional_collection = None
_CONST_SYNCED_VERSION = None  # R-F2130 — last constitutional-rules version synced this process
_CONST_LAZY_SYNC_TRIED = False  # R-F3099 — one lazy populate attempt per process (see below)
_CONST_SYNC_LOCK = threading.Lock()  # R-F3099 — clear+re-index must be atomic across callers
_init_lock = threading.Lock()
_init_done = False


class _CodingRAGEmbeddingFn:
    """Chromadb-compatible embedding function for coding RAG collections.

    Wraps rag_store's _SharedSentenceTransformerEmbeddingFn but adds the
    ``embed_query`` method that chromadb 1.5+ requires for query operations.
    The underlying model is the shared singleton — no second model load.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._inner = None

    def _get_inner(self):
        if self._inner is None:
            from .rag_store import _SharedSentenceTransformerEmbeddingFn
            self._inner = _SharedSentenceTransformerEmbeddingFn(self._model_name)
        return self._inner

    def __call__(self, input):  # noqa: A002 — chromadb protocol
        return self._get_inner()(input)

    def embed_query(self, input: str | list[str]) -> list[list[float]]:
        """chromadb 1.5+ query protocol — delegates to __call__."""
        if isinstance(input, str):
            input = [input]
        return self.__call__(input)

    def name(self) -> str:
        return "sentence_transformer"


def _get_shared_embed_fn():
    """Return a chromadb-compatible embedding function backed by the shared model.

    Wraps rag_store's _SharedSentenceTransformerEmbeddingFn in a wrapper that
    adds the ``embed_query`` method chromadb 1.5+ requires. Fails closed
    (returns None) if the shared function isn't available — never creates a
    second model instance.
    """
    try:
        return _CodingRAGEmbeddingFn("all-MiniLM-L6-v2")
    except Exception as exc:
        logger.debug("CodingRAG: shared embed fn unavailable (%s)", exc)
        return None


def _get_chromadb_client():
    """Return the SHARED chromadb client from rag_store. Never construct one here.

    R-F3530 — THIS FUNCTION USED TO CONTRADICT ITS OWN DOCSTRING, and that is what
    kept aria-intel crash-looping after R-F3527. It said "exactly one chromadb
    instance in the process", then fell back to
    `chromadb.PersistentClient(path=RAG_PATH)` — a SECOND client on the SAME PATH.

    R-F3527 serialised construction inside `rag_store._get_client`, but this module
    holds a DIFFERENT lock (`_init_lock`), so the two could still build concurrently
    on one path. The post-R-F3527 faulthandler dump named this exact site:

        coding_rag_indexer.py:215 in _ensure
          chromadb/api/client.py:361 in get_or_create_collection
            chromadb/api/rust.py:313 -> 244 in create_collection   <- Rust core

    chromadb keys systems by path in `SharedSystemClient._identifier_to_system`; two
    constructions for one path tear down / re-enter a system the other is inside, and
    the Rust core dereferences freed state.

    So there is now ONE owner. If rag_store cannot provide a client — chromadb absent,
    the R-F2855 corrupt-store breaker tripped, or the R-F2151 cooldown armed — coding
    RAG is DEGRADED and says so. That is the correct outcome: every reason
    `_get_client` returns None is a reason NOT to construct a rival client at the same
    path. The old fallback turned "RAG is deliberately disabled" into "build another
    one anyway", which is the worst possible response to a tripped breaker.
    """
    try:
        from .rag_store import _get_client as _rag_get_client
        client = _rag_get_client()
        if client is not None:
            return client
        logger.warning(
            "[R-F3530] CodingRAG DEGRADED — rag_store has no chromadb client "
            "(not initialised, breaker tripped, or cooldown armed). NOT constructing "
            "a second client on the same path: that is the SIGSEGV.")
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("[R-F3530] CodingRAG DEGRADED — rag_store client "
                       "unavailable: %s", e)
        return None


def _ensure() -> bool:
    """Lazy-init all four coding collections. Thread-safe.

    Returns True when all collections are ready. Safe to call from any
    thread — uses a threading.Lock to serialise first-time init.
    """
    global _client, _fixes_collection, _failures_collection
    global _structure_collection, _constitutional_collection, _init_done

    if _init_done:
        return True

    with _init_lock:
        if _init_done:
            return True

        client = _get_chromadb_client()
        if client is None:
            return False

        embed_fn = _get_shared_embed_fn()
        if embed_fn is None:
            logger.warning("CodingRAG: shared embedding function unavailable — cannot init")
            return False

        try:
            _fixes_collection = client.get_or_create_collection(
                name=_COLLECTION_FIXES,
                embedding_function=embed_fn,
                metadata={"hnsw:space": "cosine"},
            )
            _failures_collection = client.get_or_create_collection(
                name=_COLLECTION_FAILURES,
                embedding_function=embed_fn,
                metadata={"hnsw:space": "cosine"},
            )
            _structure_collection = client.get_or_create_collection(
                name=_COLLECTION_STRUCTURE,
                embedding_function=embed_fn,
                metadata={"hnsw:space": "cosine"},
            )
            _constitutional_collection = client.get_or_create_collection(
                name=_COLLECTION_CONSTITUTIONAL,
                embedding_function=embed_fn,
                metadata={"hnsw:space": "cosine"},
            )

            _client = client
            _init_done = True

            logger.info(
                "CodingRAG ready — fixes: %d, failures: %d, structure: %d, constitutional: %d",
                _fixes_collection.count(),
                _failures_collection.count(),
                _structure_collection.count(),
                _constitutional_collection.count(),
            )
            return True

        except Exception as e:
            logger.warning("CodingRAG init failed: %s", e)
            return False


def _generate_id(prefix: str, content: str) -> str:
    """Deterministic ID for dedup across re-indexes."""
    raw = f"{prefix}|{content}"
    return f"{prefix}_{hashlib.md5(raw.encode('utf-8'), usedforsecurity=False).hexdigest()[:16]}"


# ── Public API: Indexing ──────────────────────────────────────────────────────


def index_fix(fix: FixRecord) -> str | None:
    """Index a successful fix for future retrieval.

    Called from self_improve.py after a successful deploy.
    Returns the chromadb document ID, or None if indexing failed.

    NOTE: chromadb.upsert() is blocking (runs sentence-transformers encode
    synchronously). Callers in async contexts MUST wrap this in
    asyncio.to_thread() to avoid pinning the event loop.
    """
    if not _ensure():
        logger.warning("CodingRAG not ready — fix %s not indexed", fix.r_number)
        return None

    document = (
        f"R-{fix.r_number}: {fix.title}\n\n"
        f"Gap type: {fix.gap_type}\n"
        f"Module: {fix.module}\n\n"
        f"Problem: {fix.problem_description}\n\n"
        f"Approach: {fix.approach}\n\n"
        f"Files changed: {', '.join(fix.files_changed)}\n\n"
        f"Outcome: Deployed successfully. Tests passed: {fix.tests_passed}"
    )

    doc_id = _generate_id(f"fix_{fix.r_number}", document)

    try:
        _fixes_collection.upsert(
            documents=[document],
            metadatas=[{
                "r_number": fix.r_number,
                "gap_type": fix.gap_type,
                "module": fix.module,
                "outcome": fix.outcome,
                "tests_passed": fix.tests_passed,
                "timestamp": fix.timestamp,
                "type": "successful_fix",
            }],
            ids=[doc_id],
        )
        logger.info("[CodingRAG] Indexed fix %s — %s", fix.r_number, fix.title[:60])
        return doc_id
    except Exception as e:
        logger.warning("[CodingRAG] Failed to index fix %s: %s", fix.r_number, e)
        return None


def index_failure(failure: FailureRecord) -> str | None:
    """Index a failed attempt for future avoidance.

    Called from self_coder.py after a failed fix attempt.
    Returns the chromadb document ID, or None if indexing failed.

    NOTE: chromadb.upsert() is blocking. Callers in async contexts MUST
    wrap this in asyncio.to_thread().
    """
    if not _ensure():
        logger.warning("CodingRAG not ready — failure not indexed")
        return None

    document = (
        f"R-{failure.r_number} — Attempt {failure.attempt_number} FAILED\n\n"
        f"Error Type: {failure.error_type}\n"
        f"Error Message: {failure.error_message}\n\n"
        f"Why it failed: {failure.why_failed}\n\n"
        f"What worked next: {failure.next_approach}"
    )

    doc_id = _generate_id(f"fail_{failure.r_number}_{failure.attempt_number}", document)

    try:
        _failures_collection.upsert(
            documents=[document],
            metadatas=[{
                "r_number": failure.r_number,
                "attempt": failure.attempt_number,
                "error_type": failure.error_type,
                "timestamp": failure.timestamp,
                "type": "failed_attempt",
            }],
            ids=[doc_id],
        )
        logger.info("[CodingRAG] Indexed failure %s attempt %d", failure.r_number, failure.attempt_number)
        return doc_id
    except Exception as e:
        logger.warning("[CodingRAG] Failed to index failure %s: %s", failure.r_number, e)
        return None


def index_codebase_structure(module_path: Path) -> int:
    """Index a module's structure — function boundaries, classes, imports.

    Chunks at AST boundaries (not arbitrary character counts) so each
    chunk is a coherent semantic unit. Only top-level functions and
    classes are indexed (not nested defs, which are part of their parent).

    Called by nightly scheduled job or on-demand via CLI.
    Returns the number of chunks indexed.

    NOTE: chromadb.upsert() is blocking. Callers in async contexts MUST
    wrap this in asyncio.to_thread().
    """
    if not _ensure():
        return 0
    if not module_path.exists():
        return 0

    chunks = _chunk_module(module_path)
    if not chunks:
        return 0

    chunk_count = 0
    for chunk in chunks:
        doc_id = _generate_id(
            f"struct_{module_path.stem}_{chunk['type']}_{chunk['name']}",
            chunk["content"],
        )
        try:
            _structure_collection.upsert(
                documents=[chunk["content"]],
                metadatas=[{
                    "module": str(module_path),
                    "chunk_type": chunk["type"],
                    "name": chunk["name"],
                    "line_start": chunk["line_start"],
                    "line_end": chunk["line_end"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }],
                ids=[doc_id],
            )
            chunk_count += 1
        except Exception as e:
            logger.debug("[CodingRAG] Failed to index chunk %s: %s", doc_id, e)

    logger.info("[CodingRAG] Indexed %d chunks from %s", chunk_count, module_path.name)
    return chunk_count


def _chunk_module(module_path: Path) -> list[dict]:
    """Chunk a Python module at semantic boundaries.

    Only top-level nodes (direct children of the module body) are chunked
    to avoid double-indexing nested functions/classes. Each chunk is a
    coherent AST node — never an arbitrary character slice.
    """
    try:
        content = module_path.read_text(encoding="utf-8")
        tree = ast.parse(content)
        lines = content.split("\n")
    except SyntaxError as e:
        logger.debug("[CodingRAG] Syntax error in %s: %s", module_path, e)
        return []
    except Exception as e:
        logger.debug("[CodingRAG] Failed to read %s: %s", module_path, e)
        return []

    chunks = []

    # Chunk imports as a group — only top-level imports
    import_nodes = [
        n for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    if import_nodes:
        start = import_nodes[0].lineno - 1
        end = (import_nodes[-1].end_lineno or import_nodes[-1].lineno)
        chunks.append({
            "type": "imports",
            "name": "__imports__",
            "content": "\n".join(lines[start:end]),
            "line_start": import_nodes[0].lineno,
            "line_end": end,
        })

    # Chunk each top-level function and class separately
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            end = node.end_lineno or node.lineno
            chunks.append({
                "type": "function",
                "name": node.name,
                "content": "\n".join(lines[node.lineno - 1:end]),
                "line_start": node.lineno,
                "line_end": end,
            })
        elif isinstance(node, ast.ClassDef):
            end = node.end_lineno or node.lineno
            chunks.append({
                "type": "class",
                "name": node.name,
                "content": "\n".join(lines[node.lineno - 1:end]),
                "line_start": node.lineno,
                "line_end": end,
            })

    return chunks


def index_constitutional_rules(rules: list[dict]) -> int:
    """Index constitutional rules for semantic querying.

    Each rule becomes a retrievable document so ARIA can ask
    "what are the constraints on modifying X?" and get relevant clauses.
    Returns the number of rules indexed.

    NOTE: chromadb.upsert() is blocking. Callers in async contexts MUST
    wrap this in asyncio.to_thread().
    """
    if not _ensure():
        return 0

    count = 0
    for rule in rules:
        name = rule.get("name", "unnamed")
        document = (
            f"CONSTITUTIONAL RULE: {name}\n"
            f"Clause: {rule.get('clause_number', 'N/A')}\n\n"
            f"Rule: {rule.get('description', '')}\n\n"
            f"Constraint: {rule.get('constraint', 'N/A')}\n\n"
            f"Affected Modules: {', '.join(rule.get('affected_modules', []))}\n\n"
            f"Protected Files: {', '.join(rule.get('protected_files', []))}\n\n"
            f"Violation Consequence: {rule.get('consequence', 'Block deployment')}"
        )

        doc_id = _generate_id(f"const_{name}", document)
        try:
            _constitutional_collection.upsert(
                documents=[document],
                metadatas=[{
                    "rule_name": name,
                    "clause": rule.get("clause_number", ""),
                    "affected_modules": json.dumps(rule.get("affected_modules", [])),
                    "type": "constitutional_rule",
                }],
                ids=[doc_id],
            )
            count += 1
        except Exception as e:
            logger.debug("[CodingRAG] Failed to index rule %s: %s", name, e)

    logger.info("[CodingRAG] Indexed %d constitutional rules", count)
    return count


def sync_constitutional_rules() -> dict:
    """R-F2130 — populate coding_constitutional from the canonical rules module.

    The collection was BUILT but never populated (index_constitutional_rules was
    only called in tests), so the coder was never grounded in the playbook. This
    clears stale docs then re-indexes constitutional_rules.CONSTITUTIONAL_RULES so
    the collection always reflects the current rules EXACTLY (no orphans when a
    rule's text changes — content-hashed ids would otherwise linger). Idempotent
    within a process via a version guard. BLOCKING (chromadb + encode) — callers
    in async contexts MUST wrap this in asyncio.to_thread(). Never raises.

    R-F3099 — SERIALISED. This clears the collection and then re-indexes it, which
    is only safe as an atomic pair. Two concurrent callers could both pass the
    version guard and interleave delete/add, leaving the collection short of rules
    or empty — the exact state R-F2130 existed to prevent. Boot (`main.py:1423`)
    and the on-demand populate in `query_constitutional_constraints` are genuinely
    concurrent on the server, so the lock lives HERE, in the function that mutates,
    rather than in one careful caller. Same lesson as R-F3085.
    """
    global _CONST_SYNCED_VERSION
    if not _ensure():
        return {"ok": False, "reason": "coding RAG unavailable"}
    try:
        from . import constitutional_rules as _cr
        rules = _cr.CONSTITUTIONAL_RULES
        version = _cr.RULES_VERSION
    except Exception as e:  # noqa: BLE001
        logger.warning("[CodingRAG] R-F2130 rules import failed: %s", e)
        return {"ok": False, "reason": f"rules import failed: {e}"}

    with _CONST_SYNC_LOCK:
        # Re-read under the lock: a racing caller may have completed the sync
        # while this one waited, in which case there is nothing left to do.
        try:
            existing_count = _constitutional_collection.count()
        except Exception:  # noqa: BLE001
            existing_count = 0
        if _CONST_SYNCED_VERSION == version and existing_count > 0:
            return {"ok": True, "skipped": True, "version": version, "count": existing_count}

        # Clear stale docs first so an edited rule can't leave an orphaned old version.
        try:
            existing = _constitutional_collection.get()
            ids = (existing or {}).get("ids", []) or []
            if ids:
                _constitutional_collection.delete(ids=ids)
        except Exception as e:  # noqa: BLE001
            logger.debug("[CodingRAG] R-F2130 clear failed (continuing): %s", e)

        try:
            n = index_constitutional_rules(rules)
        except Exception as e:  # noqa: BLE001
            logger.warning("[CodingRAG] R-F2130 index failed: %s", e)
            return {"ok": False, "reason": f"index failed: {e}"}

        _CONST_SYNCED_VERSION = version
        # Report INSIDE the lock. `n` is bound only on the success path here, and
        # §9's F28 outage was exactly this shape — a local read on a path where it
        # was never assigned. Keeping the read adjacent to the write means a future
        # edit cannot silently turn this into an UnboundLocalError on the boot path.
        logger.info("[CodingRAG] R-F2130 synced %d constitutional rules (version %s)", n, version)
        return {"ok": True, "indexed": n, "version": version}


# ── Public API: Querying ──────────────────────────────────────────────────────


def query_relevant_fixes(query: str, top_k: int = 5, min_similarity: float = 0.0) -> list[dict]:
    """Retrieve most similar successful fixes for the current gap.

    Core value: ARIA asks "how did I fix this before?" and gets answers.
    Returns list of {content, metadata, similarity} sorted by similarity
    descending (most relevant first). Results below ``min_similarity``
    are filtered out.

    NOTE: chromadb.query() is blocking. Callers in async contexts MUST
    wrap this in asyncio.to_thread().
    """
    if not _ensure():
        return []

    try:
        results = _fixes_collection.query(
            query_texts=[query],
            n_results=top_k,
        )
        if results.get("documents") and results["documents"][0]:
            distances = results.get("distances", [[1.0]])[0]
            out = []
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                distances,
            ):
                sim = round(1.0 - dist, 4)
                if sim >= min_similarity:
                    out.append({
                        "content": doc,
                        "metadata": meta,
                        "similarity": sim,
                    })
            return out
    except Exception as e:
        logger.debug("[CodingRAG] query_relevant_fixes failed: %s", e)

    return []


def query_known_failures(
    gap_type: str,
    error_type: str | None = None,
    top_k: int = 3,
    min_similarity: float = 0.0,
) -> list[dict]:
    """Retrieve known failures to avoid repeating mistakes.

    Allows ARIA to skip directly to the approach that worked.
    Returns list of {content, metadata}. Results below ``min_similarity``
    are filtered out.

    NOTE: chromadb.query() is blocking. Callers in async contexts MUST
    wrap this in asyncio.to_thread().
    """
    if not _ensure():
        return []

    query = f"{gap_type} failure"
    if error_type:
        query += f" {error_type}"

    try:
        results = _failures_collection.query(
            query_texts=[query],
            n_results=top_k,
        )
        if results.get("documents") and results["documents"][0]:
            distances = results.get("distances", [[1.0]])[0]
            out = []
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                distances,
            ):
                sim = round(1.0 - dist, 4)
                if sim >= min_similarity:
                    out.append({
                        "content": doc,
                        "metadata": meta,
                    })
            return out
    except Exception as e:
        logger.debug("[CodingRAG] query_known_failures failed: %s", e)

    return []


def query_codebase_context(module_name: str, top_k: int = 5, min_similarity: float = 0.0) -> list[dict]:
    """Retrieve structural context about a module.

    Reduces need to read 6+ files sequentially — the structure is already
    indexed and retrievable by semantic similarity.
    Returns list of {content, metadata}. Results below ``min_similarity``
    are filtered out.

    NOTE: chromadb.query() is blocking. Callers in async contexts MUST
    wrap this in asyncio.to_thread().
    """
    if not _ensure():
        return []

    try:
        results = _structure_collection.query(
            query_texts=[f"module: {module_name}"],
            n_results=top_k,
        )
        if results.get("documents") and results["documents"][0]:
            distances = results.get("distances", [[1.0]])[0]
            out = []
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                distances,
            ):
                sim = round(1.0 - dist, 4)
                if sim >= min_similarity:
                    out.append({
                        "content": doc,
                        "metadata": meta,
                    })
            return out
    except Exception as e:
        logger.debug("[CodingRAG] query_codebase_context failed: %s", e)

    return []


#: R-F3911 — retrieval modes, reported on every result so a caller can never mistake
#: a degraded answer for a semantic one.
CONST_MODE_SEMANTIC = "semantic"
CONST_MODE_LEXICAL = "lexical"


def _lexical_constitutional_match(query: str, top_k: int) -> list[dict]:
    """Rank the IN-CODE constitutional rules by term overlap. No vector store.

    R-F3911 — THE RULES NEVER NEEDED CHROMADB; ONLY THE RANKING DID.
    `CONSTITUTIONAL_RULES` is a plain Python list of 31 dicts carrying the full
    text of every clause. When the vector store is unavailable, returning `[]` threw
    away constraints that were sitting in the process the whole time.

    Deliberately dumb: substring/term overlap, ties broken by declaration order. It
    does not pretend to be semantic search — it is labelled `lexical` precisely so
    nobody reads it as one. A crude ranking that DELIVERS the constraints beats a
    sophisticated one that delivers nothing.

    NEVER RETURNS EMPTY. If no term matches, the top_k rules come back in
    declaration order with `matched_terms: 0`, because §20's purpose is to surface
    constraints the session might not recall — and "no constraints apply" is a
    conclusion this function is not entitled to draw from a failed keyword match.
    """
    import re as _re

    try:
        from .constitutional_rules import CONSTITUTIONAL_RULES
    except Exception as e:      # pragma: no cover - the rules are a plain module
        logger.warning("[R-F3911] constitutional rules unreadable: %s", e)
        return []

    terms = {t for t in _re.split(r"[^a-z0-9]+", (query or "").lower()) if len(t) > 2}
    scored: list[tuple[int, int, dict]] = []
    for idx, rule in enumerate(CONSTITUTIONAL_RULES):
        blob = " ".join(
            str(rule.get(k, "")) for k in
            ("name", "clause_number", "description", "constraint", "consequence")
        ).lower()
        score = sum(1 for t in terms if t in blob)
        scored.append((-score, idx, rule))
    scored.sort()

    out: list[dict] = []
    for neg_score, _idx, rule in scored[: max(1, top_k)]:
        out.append({
            # Same shape the semantic path returns, so §20's snippet (`r['rule']`)
            # and every other consumer keep working unchanged.
            "rule": _format_rule(rule),
            "metadata": {
                "name": rule.get("name", ""),
                "clause_number": rule.get("clause_number", ""),
            },
            "retrieval_mode": CONST_MODE_LEXICAL,
            "degraded": True,
            "matched_terms": -neg_score,
        })
    return out


def _format_rule(rule: dict) -> str:
    """One rule as the text a reader needs, matching what sync writes to chroma."""
    return (
        f"{rule.get('clause_number', '')} [{rule.get('name', '')}] "
        f"{rule.get('description', '')} CONSTRAINT: {rule.get('constraint', '')} "
        f"CONSEQUENCE: {rule.get('consequence', '')}"
    ).strip()


def constitutional_retrieval_status() -> dict:
    """Which mode WOULD serve right now, and why — §25 proprioception for §20.

    Exists so a session can ask "is my binding priming step actually semantic?"
    instead of inferring it from output that looks the same either way.
    """
    try:
        from .constitutional_rules import CONSTITUTIONAL_RULES
        rules_available = len(CONSTITUTIONAL_RULES)
    except Exception:
        rules_available = 0
    ok = False
    try:
        ok = bool(_ensure())
    except Exception:
        ok = False
    return {
        "mode": CONST_MODE_SEMANTIC if ok else CONST_MODE_LEXICAL,
        "degraded": not ok,
        "vector_store_available": ok,
        "rules_in_code": rules_available,
        "reason": "" if ok else (
            "chromadb/vector store unavailable — serving the in-code "
            "CONSTITUTIONAL_RULES by term overlap. On win32/ARM64 no chromadb wheel "
            "exists (CLAUDE.md §16), so this is the EXPECTED local mode, not a fault "
            "to fix by installing a package that cannot be installed."
        ),
    }


def query_constitutional_constraints(query: str, top_k: int = 3, min_similarity: float = 0.0) -> list[dict]:
    """Retrieve constitutional rules semantically.

    "what are the constraints on modifying stream guard logic?"
    → returns relevant clauses.
    Returns list of {rule, metadata}. Results below ``min_similarity``
    are filtered out.

    NOTE: chromadb.query() is blocking. Callers in async contexts MUST
    wrap this in asyncio.to_thread().

    R-F3099 — this must POPULATE, not just read. R-F2130 correctly identified that
    `coding_constitutional` was built but never filled, and wired the fix into the
    FastAPI lifespan (`main.py:1423`). But the collection's other first-class
    consumer is CLAUDE.md §20's binding pre-code priming step, which runs from the
    CLI — where the server lifespan never executes. So the local collection stayed
    at 0 rules, `_ensure()` returned True because the collection EXISTS, and the
    binding step returned `[]` on every session without ever erroring. That is the
    R-F2623 failure class exactly: a mandatory step certified by an absence.

    The root fix is not "ask the caller to sync first" — that is the band-aid §1
    forbids, and it would leave every future consumer to rediscover the same trap.
    Populate on demand instead, so being grounded is a property of the QUERY rather
    than of who booted. Guarded three ways: only when the collection is genuinely
    empty, at most once per process, and never raising — a sync failure degrades to
    the previous behaviour (empty list) rather than breaking the caller.
    """
    global _CONST_LAZY_SYNC_TRIED

    # R-F3911 — THE THIRD RECURRENCE OF THE SAME FAILURE IN THIS ONE FUNCTION.
    # R-F2623 fixed a TypeError that made the §20 step never run. R-F3099 fixed an
    # empty collection that made it return `[]` on every session — "a mandatory step
    # certified by an absence", in the words above. Both left THIS branch: when
    # chromadb itself is unavailable, `_ensure()` is False and the binding step
    # returned an empty list, indistinguishable from "no constraints apply".
    #
    # On win32/ARM64 that is not a misconfiguration anyone can fix — no chromadb
    # wheel exists for the platform (§16), so the declared dev environment CANNOT
    # have it. Installing it would "fix" one workstation and leave CI, production
    # and every other developer exactly as dark.
    #
    # The rules were never the missing piece: CONSTITUTIONAL_RULES is a plain list
    # in this process. Only the RANKING needed a vector store. So degrade to a
    # lexical match over the real rules and LABEL it, rather than returning nothing.
    if not _ensure():
        return _lexical_constitutional_match(query, top_k)

    if not _CONST_LAZY_SYNC_TRIED:
        try:
            if _constitutional_collection.count() == 0:
                _CONST_LAZY_SYNC_TRIED = True
                res = sync_constitutional_rules()
                logger.info("[R-F3099] lazy constitutional sync on empty collection: %s", res)
        except Exception as e:  # noqa: BLE001
            _CONST_LAZY_SYNC_TRIED = True
            logger.warning("[R-F3099] lazy constitutional sync failed (non-fatal): %s", e)

    try:
        results = _constitutional_collection.query(
            query_texts=[query],
            n_results=top_k,
        )
        if results.get("documents") and results["documents"][0]:
            distances = results.get("distances", [[1.0]])[0]
            out = []
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                distances,
            ):
                sim = round(1.0 - dist, 4)
                if sim >= min_similarity:
                    out.append({
                        "rule": doc,
                        "metadata": meta,
                        "retrieval_mode": CONST_MODE_SEMANTIC,
                        "degraded": False,
                    })
            if out:
                return out
    except Exception as e:
        logger.debug("[CodingRAG] query_constitutional_constraints failed: %s", e)

    # R-F3911 — a store that is PRESENT but answered with nothing (empty collection,
    # a query error, or every hit filtered by min_similarity) is still an absence the
    # caller cannot distinguish from "no rule applies". §20 is binding, so it gets
    # the rules either way; the label says which path served.
    return _lexical_constitutional_match(query, top_k)


def record_precommit_failure(
    check_name: str,
    file_path: str,
    error_message: str,
    r_number: str = "pre-commit",
) -> str | None:
    """R-F2136 — Record a pre-commit hook failure in the coding_failures RAG.

    Called from scripts/pre-commit when a check blocks a commit. This creates
    a FailureRecord and indexes it so the autonomous coder can learn from
    pre-commit rejections (e.g., "don't use bare curl in shell scripts").

    This is a sync, non-blocking function (chromadb ops are sync). Callers
    in async contexts MUST wrap in asyncio.to_thread().

    Returns the chromadb document ID, or None if indexing failed.
    Never raises — failures are logged and swallowed.
    """
    try:
        from datetime import datetime, timezone
        record = FailureRecord(
            r_number=r_number,
            attempt_number=1,
            error_type=f"precommit_{check_name}",
            error_message=error_message[:500],
            why_failed=f"Pre-commit hook '{check_name}' blocked commit on {file_path}",
            next_approach="Fix the reported issue and retry the commit",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        return index_failure(record)
    except Exception as e:
        logger.debug("[R-F2136] record_precommit_failure failed: %s", e)
        return None


def get_stats() -> dict:
    """Return indexing statistics for monitoring.

    Returns dict with ready flag and per-collection counts.
    Never raises — returns error info on failure.
    """
    if not _ensure():
        return {
            "ready": False,
            "total_fixes": 0,
            "total_failures": 0,
            "total_codebase_chunks": 0,
            "total_constitutional_rules": 0,
        }

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="coding_rag_indexer",
                     summary="coding_rag_indexer module active",
                     source_id="coding_rag_indexer:init")
    except Exception:
        try:
            wire_failure(module="coding_rag_indexer", detail="module init failed",
                        gap_type="engine_failure", source="coding_rag_indexer:init")
        except Exception:
            pass

    try:
        return {
            "ready": True,
            "total_fixes": _fixes_collection.count(),
            "total_failures": _failures_collection.count(),
            "total_codebase_chunks": _structure_collection.count(),
            "total_constitutional_rules": _constitutional_collection.count(),
        }
    except Exception as e:
        logger.debug("[CodingRAG] get_stats failed: %s", e)
        return {
            "ready": False,
            "error": str(e),
            "total_fixes": 0,
            "total_failures": 0,
            "total_codebase_chunks": 0,
            "total_constitutional_rules": 0,
        }
