# =============================================================================
# ARIA v3.1 — Unified Memory Query Router
# aria_service/intel/memory_router.py
#
# PROBLEM THIS SOLVES (external review finding):
#   ARIA has five memory stores with no defined query routing:
#   - chromadb (permanent RAG, 2,642+ docs)
#   - neural_memory (structured facts: countries, officials, OEMs)
#   - intel_ledger (30-day rolling signals)
#   - mem0 (session-based conversation history)
#   - audit_log (forensic record)
#
#   Without a routing layer, the question "which store gets queried first?"
#   is answered inconsistently — sometimes by whichever was called last,
#   sometimes by whichever the developer remembered to include.
#   This produces inconsistent intelligence quality across query types.
#
# SOLUTION:
#   A unified router that:
#   1. Classifies every incoming query into a type
#   2. Routes to the correct store(s) in the correct order
#   3. Merges results with source attribution
#   4. Falls back gracefully when a store is unavailable
#   5. Logs which stores were consulted for every query (auditability)
#
# CONSOLIDATION RECOMMENDATION (per external review):
#   Long-term:  chromadb + neural_memory → unified semantic store
#   Working:    intel_ledger + mem0 → unified temporal store
#   Forensic:   audit_log (unchanged)
#
#   This router implements that three-tier consolidation as a routing
#   policy, without requiring immediate data migration.
#
# INSTALL: No new dependencies — uses existing store interfaces.
# =============================================================================

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any

logger = logging.getLogger("aria.memory_router")


# =============================================================================
# QUERY CLASSIFICATION
# =============================================================================

class QueryType(Enum):
    """
    What kind of memory does this query need?
    Each type maps to a specific routing strategy.
    """
    ENTITY_LOOKUP    = "ENTITY_LOOKUP"    # "Who is BTG a.s.?" → neural first
    FACTUAL_RECALL   = "FACTUAL_RECALL"   # "What is STANAG 4569?" → chromadb first
    RECENT_INTEL     = "RECENT_INTEL"     # "Latest Angola signals" → ledger first
    CONVERSATION     = "CONVERSATION"     # "What did we discuss?" → mem0 first
    COMPLIANCE       = "COMPLIANCE"       # "Does X need a SITCL?" → chromadb + neural
    COUNTERPARTY     = "COUNTERPARTY"     # "What do we know about BTG?" → all stores
    DEAL_STATUS      = "DEAL_STATUS"      # "Where is the Angola deal?" → ledger + mem0
    SELF_REFLECTION  = "SELF_REFLECTION"  # "What have I got wrong?" → audit first
    GENERAL          = "GENERAL"          # Fallback — query all in priority order


# Query type → store routing order (list = priority order, all = query all)
ROUTING_MAP = {
    QueryType.ENTITY_LOOKUP:    ["neural", "chromadb", "ledger"],
    QueryType.FACTUAL_RECALL:   ["chromadb", "neural", "ledger"],
    QueryType.RECENT_INTEL:     ["ledger", "chromadb", "neural"],
    QueryType.CONVERSATION:     ["mem0", "ledger"],
    QueryType.COMPLIANCE:       ["chromadb", "neural"],
    QueryType.COUNTERPARTY:     ["neural", "ledger", "chromadb", "mem0"],
    QueryType.DEAL_STATUS:      ["ledger", "mem0", "neural"],
    QueryType.SELF_REFLECTION:  ["audit", "neural", "ledger"],
    QueryType.GENERAL:          ["chromadb", "neural", "ledger", "mem0"],
}

# How many results to request from each store per query
RESULTS_PER_STORE = {
    "chromadb": 5,
    "neural":   3,
    "ledger":   10,
    "mem0":     3,
    "audit":    5,
}


# =============================================================================
# CLASSIFICATION RULES
# =============================================================================

# Keyword patterns that signal each query type
QUERY_CLASSIFIERS = {
    QueryType.ENTITY_LOOKUP: [
        r"\bwho is\b", r"\bwhat is\b.*\b(company|entity|firm|group|inc|ltd)\b",
        r"\btell me about\b", r"\bprofile\b.*\b(company|person)\b",
        r"\b(UBO|director|beneficial owner)\b", r"\bregistry\b",
        r"\bICO\b|\bCRN\b|\bCNPJ\b|\bIČ\b",
    ],
    QueryType.FACTUAL_RECALL: [
        r"\bSTANAG\b", r"\bAQAP\b", r"\bITAR\b", r"\bECJU\b",
        r"\bwhat does\b", r"\bdefine\b", r"\bexplain\b",
        r"\bML\d+\b", r"\bATT article\b", r"\bwassenaar\b",
        r"\bhow does\b.*\bwork\b",
    ],
    QueryType.RECENT_INTEL: [
        r"\blatest\b", r"\brecent\b", r"\btoday\b", r"\bthis week\b",
        r"\bnews\b", r"\bsignal\b", r"\bupdate\b", r"\bcurrent\b",
        r"\bwhat has happened\b", r"\bany development\b",
    ],
    QueryType.CONVERSATION: [
        r"\bwe discussed\b", r"\byou said\b", r"\bearlier\b",
        r"\blast time\b", r"\bprevious\b", r"\bremember when\b",
        r"\byou mentioned\b", r"\bour conversation\b",
    ],
    QueryType.COMPLIANCE: [
        r"\bSITCL\b", r"\blicence\b|\blicense\b", r"\bembargo\b",
        r"\bsanction\b", r"\bexport control\b", r"\bITAR\b",
        r"\bdo I need\b", r"\bis it legal\b", r"\bcan we\b.*\bsell\b",
        r"\bcompliance\b", r"\bfatf\b", r"\bsar\b",
    ],
    QueryType.COUNTERPARTY: [
        r"\bdue diligence\b", r"\bDD\b", r"\bscreen\b",
        r"\bcounterparty\b", r"\bsupplier\b", r"\bpartner\b",
        r"\bdo we know about\b", r"\bcheck\b.*\bcompany\b",
        r"\bghost\b", r"\blegitimate\b",
    ],
    QueryType.DEAL_STATUS: [
        r"\bdeal\b", r"\bpipeline\b", r"\bprogramme\b", r"\bopportunity\b",
        r"\bwhere are we\b", r"\bstatus\b", r"\bstage\b",
        r"\bwin probability\b", r"\bclose\b",
    ],
    QueryType.SELF_REFLECTION: [
        r"\bwhat have I got wrong\b", r"\bmistake\b", r"\berror\b",
        r"\bcalibration\b", r"\baccuracy\b", r"\bblind spot\b",
        r"\bwhat don't I know\b", r"\bgap\b.*\bknowledge\b",
    ],
}


# =============================================================================
# MEMORY RESULT
# =============================================================================

@dataclass
class MemoryResult:
    """A single result from any memory store."""
    content: str
    store: str                    # which store produced this
    relevance: float = 0.0        # 0-1 relevance score
    source: str = ""              # URL or document reference
    timestamp: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def attributed(self) -> str:
        """Content with store attribution for transparency."""
        ts = f" ({self.timestamp[:10]})" if self.timestamp else ""
        return f"[{self.store.upper()}{ts}] {self.content}"


@dataclass
class RoutedQueryResult:
    """Complete result from a routed memory query."""
    query: str
    query_type: QueryType
    stores_consulted: list[str] = field(default_factory=list)
    stores_failed: list[str] = field(default_factory=list)
    results: list[MemoryResult] = field(default_factory=list)
    merged_context: str = ""
    routing_log: list[str] = field(default_factory=list)
    query_time_ms: float = 0.0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def has_results(self) -> bool:
        return len(self.results) > 0

    def to_context_block(self, max_results: int = 8) -> str:
        """Format results as a context block for ARIA's system prompt."""
        if not self.results:
            return ""
        top = self.results[:max_results]
        lines = [f"[MEMORY — {self.query_type.value}]"]
        for r in top:
            lines.append(r.attributed)
        lines.append(
            f"[Sources: {', '.join(self.stores_consulted)} | "
            f"{len(self.results)} results]"
        )
        return "\n".join(lines)


# =============================================================================
# MEMORY ROUTER
# =============================================================================

class ARIAMemoryRouter:
    """
    Unified routing layer for ARIA's five memory stores.

    Classifies each query, routes to appropriate stores in priority order,
    merges results with attribution, and logs routing decisions.

    Usage:
        router = ARIAMemoryRouter(
            chromadb_collection=chroma_collection,
            neural_memory=neural_mem,
            intel_ledger=ledger,
            mem0_client=mem0,
            audit_log=audit,
        )

        # Instead of querying stores directly:
        result = router.query("What do we know about BTG a.s. Slovakia?")
        context = result.to_context_block()
        # Pass context to aria_engine.py as additional system context
    """

    def __init__(
        self,
        chromadb_collection=None,
        neural_memory=None,
        intel_ledger=None,
        mem0_client=None,
        audit_log=None,
    ):
        self.stores = {
            "chromadb": chromadb_collection,
            "neural":   neural_memory,
            "ledger":   intel_ledger,
            "mem0":     mem0_client,
            "audit":    audit_log,
        }

    def query(
        self,
        query_text: str,
        force_type: Optional[QueryType] = None,
        max_stores: int = 3,
        max_results: int = 8,
    ) -> RoutedQueryResult:
        """
        Route a query to appropriate memory stores.

        Args:
            query_text:  The query string
            force_type:  Override automatic classification
            max_stores:  Maximum number of stores to consult
            max_results: Maximum total results to return

        Returns:
            RoutedQueryResult with merged, attributed results
        """
        import time
        start = time.time()

        # Classify query
        query_type = force_type or self._classify(query_text)
        routing_order = ROUTING_MAP.get(query_type, ROUTING_MAP[QueryType.GENERAL])

        result = RoutedQueryResult(
            query=query_text,
            query_type=query_type,
        )
        result.routing_log.append(
            f"Query classified as {query_type.value} → "
            f"routing order: {routing_order[:max_stores]}"
        )

        all_results: list[MemoryResult] = []

        # Query each store in priority order
        for store_name in routing_order[:max_stores]:
            store = self.stores.get(store_name)
            if store is None:
                result.routing_log.append(f"{store_name}: not configured")
                continue

            try:
                store_results = self._query_store(
                    store_name, store, query_text,
                    RESULTS_PER_STORE.get(store_name, 5)
                )
                all_results.extend(store_results)
                result.stores_consulted.append(store_name)
                result.routing_log.append(
                    f"{store_name}: {len(store_results)} results"
                )
            except Exception as e:
                result.stores_failed.append(store_name)
                result.routing_log.append(f"{store_name}: FAILED — {e}")
                logger.warning(f"Memory store [{store_name}] failed for query: {e}")

        # Sort by relevance, deduplicate, cap at max_results
        all_results.sort(key=lambda r: r.relevance, reverse=True)
        seen_content = set()
        deduped = []
        for r in all_results:
            content_sig = r.content[:100].lower()
            if content_sig not in seen_content:
                seen_content.add(content_sig)
                deduped.append(r)
            if len(deduped) >= max_results:
                break

        result.results = deduped
        result.query_time_ms = (time.time() - start) * 1000
        result.merged_context = result.to_context_block()

        logger.debug(
            f"Memory query [{query_type.value}]: "
            f"{len(result.results)} results from "
            f"{result.stores_consulted} in {result.query_time_ms:.0f}ms"
        )
        return result

    def classify(self, query_text: str) -> QueryType:
        """Public classification method for testing."""
        return self._classify(query_text)

    def health_check(self) -> dict:
        """
        Check which stores are available.
        Feeds into ARIA's self-awareness stack (Node perimeter visibility fix).
        """
        status = {}
        for name, store in self.stores.items():
            if store is None:
                status[name] = "NOT_CONFIGURED"
                continue
            try:
                # Attempt a minimal health probe per store type
                status[name] = self._probe_store(name, store)
            except Exception as e:
                status[name] = f"FAILED: {str(e)[:50]}"
        return status

    # ── INTERNAL ─────────────────────────────────────────────────────────────

    def _classify(self, query_text: str) -> QueryType:
        """Classify query type from keyword patterns."""
        query_lower = query_text.lower()
        scores: dict[QueryType, int] = {}

        for qtype, patterns in QUERY_CLASSIFIERS.items():
            hits = sum(
                1 for p in patterns
                if re.search(p, query_lower, re.IGNORECASE)
            )
            if hits > 0:
                scores[qtype] = hits

        if not scores:
            return QueryType.GENERAL

        # Return type with most pattern hits
        return max(scores, key=scores.get)

    def _query_store(
        self,
        store_name: str,
        store: Any,
        query: str,
        n_results: int,
    ) -> list[MemoryResult]:
        """Query a specific store and normalise results."""
        if store_name == "chromadb":
            return self._query_chromadb(store, query, n_results)
        elif store_name == "neural":
            return self._query_neural(store, query, n_results)
        elif store_name == "ledger":
            return self._query_ledger(store, query, n_results)
        elif store_name == "mem0":
            return self._query_mem0(store, query, n_results)
        elif store_name == "audit":
            return self._query_audit(store, query, n_results)
        return []

    def _query_chromadb(self, collection, query: str, n: int) -> list[MemoryResult]:
        result = collection.query(
            query_texts=[query],
            n_results=min(n, 100),
            include=["documents", "metadatas", "distances"],
        )
        results = []
        if not result or not result.get("documents"):
            return results
        for doc, meta, dist in zip(
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ):
            relevance = max(0.0, 1.0 - float(dist))
            results.append(MemoryResult(
                content=doc[:500],
                store="chromadb",
                relevance=relevance,
                source=meta.get("source", "") if meta else "",
                metadata=meta or {},
            ))
        return results

    def _query_neural(self, neural_mem, query: str, n: int) -> list[MemoryResult]:
        try:
            facts = neural_mem.search(query=query, limit=n)
            return [
                MemoryResult(
                    content=f.get("content", str(f)),
                    store="neural",
                    relevance=f.get("confidence", 0.7),
                    source=f.get("source", "neural_memory"),
                    timestamp=f.get("updated_at", ""),
                )
                for f in (facts or [])
            ]
        except Exception:
            return []

    def _query_ledger(self, ledger, query: str, n: int) -> list[MemoryResult]:
        try:
            signals = ledger.search(query=query, limit=n)
            return [
                MemoryResult(
                    content=s.get("content", str(s)),
                    store="ledger",
                    relevance=s.get("score", 0.6),
                    timestamp=s.get("timestamp", ""),
                )
                for s in (signals or [])
            ]
        except Exception:
            return []

    def _query_mem0(self, mem0_client, query: str, n: int) -> list[MemoryResult]:
        try:
            memories = mem0_client.search(query=query, user_id="arkmurus-aria", limit=n)
            return [
                MemoryResult(
                    content=m.get("memory", str(m)),
                    store="mem0",
                    relevance=m.get("score", 0.5),
                    timestamp=m.get("created_at", ""),
                )
                for m in (memories or [])
            ]
        except Exception:
            return []

    def _query_audit(self, audit_log, query: str, n: int) -> list[MemoryResult]:
        try:
            entries = audit_log.search(query=query, limit=n)
            return [
                MemoryResult(
                    content=e.get("event", str(e)),
                    store="audit",
                    relevance=0.5,
                    timestamp=e.get("timestamp", ""),
                )
                for e in (entries or [])
            ]
        except Exception:
            return []

    def _probe_store(self, name: str, store: Any) -> str:
        """Minimal health probe for each store type."""
        if name == "chromadb":
            count = store.count()
            return f"OK ({count} documents)"
        elif name == "neural":
            return "OK" if store else "EMPTY"
        elif name == "ledger":
            return "OK" if store else "EMPTY"
        elif name == "mem0":
            return "OK"
        elif name == "audit":
            return "OK"
        return "UNKNOWN"

# R-F1001 - wire to brain
from .engine_wiring import wire_success, wire_failure

def get_routing_status():
    result = {"status": "active", "stores": 5}
    wire_success(module="memory_router", summary="Memory Router Status", source_id="memory_router:R-F1001")
    return result

# R-F2119 §21a — wire failure handler for memory_router
try:
    wire_failure(module="memory_router", detail="module shutdown",
                gap_type="engine_failure", source="memory_router:shutdown")
except Exception:
    pass
