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

import logging
import math
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
_embedder = None
_embedder_checked = False  # avoid repeated ImportError attempts


def _get_embedder():
    """Lazy-load the sentence-transformers model (singleton)."""
    global _embedder, _embedder_checked
    if _embedder is not None:
        return _embedder
    if _embedder_checked:
        return None
    _embedder_checked = True
    try:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Loaded sentence-transformers embedding model (all-MiniLM-L6-v2)")
    except ImportError:
        logger.warning("sentence-transformers not installed — using TF-IDF only")
    except Exception as exc:
        logger.warning("Failed to load embedding model: %s — using TF-IDF only", exc)
    return _embedder


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
                emb = model.encode(text[:2000], normalize_embeddings=True)
                self._embeddings[doc_id] = emb
                self._matrix_dirty = True
            except Exception as exc:
                logger.debug("Embedding failed for %s: %s", doc_id, exc)

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
            q_emb = model.encode(expanded_query, normalize_embeddings=True)
        except Exception as exc:
            logger.debug("Query embedding failed: %s", exc)
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
