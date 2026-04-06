"""
ARIA Semantic Search — Finds relevant knowledge by meaning, not just keywords.

Uses TF-IDF vectorisation with cosine similarity for fast, dependency-free
semantic matching. No external embedding API needed.

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

logger = logging.getLogger("aria.semantic")

# ── Stop words (excluded from indexing) ──────────────────────────────────────
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


# ── TF-IDF Index ─────────────────────────────────────────────────────────────

class SemanticIndex:
    """In-memory TF-IDF index for semantic search."""

    def __init__(self):
        self._docs: dict[str, dict] = {}  # doc_id → {text, tokens, metadata}
        self._idf: dict[str, float] = {}
        self._dirty = True

    def add(self, doc_id: str, text: str, metadata: dict = None) -> None:
        """Add or update a document in the index."""
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

    def remove(self, doc_id: str) -> None:
        if doc_id in self._docs:
            del self._docs[doc_id]
            self._dirty = True

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

    def search(self, query: str, top_k: int = 10, min_score: float = 0.1) -> list[dict]:
        """Search the index semantically. Returns ranked results."""
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

    @property
    def size(self) -> int:
        return len(self._docs)


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
    logger.info("Semantic index rebuilt: %d facts indexed", count)
    return count


def get_index_stats() -> dict:
    return {"indexed_documents": _index.size, "vocabulary_size": len(_index._idf)}
