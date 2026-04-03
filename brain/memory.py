"""
CRUCIX Autonomous Brain — Vector Memory
Semantic memory using ChromaDB in-memory + Redis backup.
Render has an ephemeral filesystem, so ChromaDB runs in-memory and documents
are backed up to Redis on every write. On startup, documents are restored from Redis.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import chromadb
from sentence_transformers import SentenceTransformer

from .config import CONFIG

logger = logging.getLogger("crucix.brain.memory")


class CrucixMemory:
    """
    Semantic long-term memory for the Crucix brain.

    Three collections:
      - intelligence: raw processed signals and conclusions
      - leads:        generated BD leads with outcomes
      - outcomes:     WON/LOST/NO_BID deal results for ML feedback

    Uses in-memory ChromaDB (Render-compatible) with Redis list backup so data
    survives restarts as long as the Redis instance is available.
    """

    def __init__(self, redis_client=None):
        self.redis = redis_client

        # In-memory ChromaDB — no disk path needed on Render
        self._client = chromadb.Client()
        self._embedder = SentenceTransformer(CONFIG.embedding_model)

        # Initialise or load collections
        self._intel    = self._client.get_or_create_collection(CONFIG.chroma_collection_intel)
        self._leads    = self._client.get_or_create_collection(CONFIG.chroma_collection_leads)
        self._outcomes = self._client.get_or_create_collection(CONFIG.chroma_collection_outcomes)

        # Restore from Redis backup on startup
        self._restore_from_redis()

        logger.info(
            f"Memory loaded | intel={self._intel.count()} "
            f"leads={self._leads.count()} outcomes={self._outcomes.count()}"
        )

    # ── Redis Backup / Restore ─────────────────────────────────────────────────

    def _restore_from_redis(self):
        """Restore ChromaDB collections from Redis on startup."""
        if not self.redis:
            logger.info("No Redis client — starting with empty in-memory ChromaDB")
            return

        for collection, redis_key in [
            (self._intel,    CONFIG.redis_key_chroma_intel),
            (self._leads,    CONFIG.redis_key_chroma_leads),
            (self._outcomes, CONFIG.redis_key_chroma_outcomes),
        ]:
            try:
                raw_docs = self.redis.lrange(redis_key, 0, -1)
                if not raw_docs:
                    continue
                restored = 0
                for raw in raw_docs:
                    try:
                        entry = json.loads(raw)
                        doc_id   = entry["id"]
                        document = entry["document"]
                        metadata = entry.get("metadata", {})
                        # Skip if already exists (idempotent)
                        try:
                            collection.get(ids=[doc_id])
                            continue
                        except Exception:
                            pass
                        collection.add(
                            ids=[doc_id],
                            embeddings=self._embed([document]),
                            documents=[document],
                            metadatas=[metadata],
                        )
                        restored += 1
                    except Exception as e:
                        logger.debug(f"Skipping malformed Redis doc: {e}")
                if restored:
                    logger.info(f"Restored {restored} docs from Redis key {redis_key}")
            except Exception as e:
                logger.warning(f"Redis restore failed for {redis_key}: {e}")

    def _backup_to_redis(self, redis_key: str, doc_id: str, document: str, metadata: Dict):
        """Append a document to the Redis backup list."""
        if not self.redis:
            return
        try:
            entry = json.dumps({"id": doc_id, "document": document, "metadata": metadata})
            self.redis.rpush(redis_key, entry)
            # Trim to keep only the latest N documents
            self.redis.ltrim(redis_key, -CONFIG.redis_chroma_max_docs, -1)
        except Exception as e:
            logger.warning(f"Redis backup failed for {redis_key}: {e}")

    # ── Embedding ──────────────────────────────────────────────────────────────

    def _embed(self, texts: List[str]) -> List[List[float]]:
        return self._embedder.encode(texts, convert_to_numpy=True).tolist()

    # ── Intelligence Memory ────────────────────────────────────────────────────

    def store_conclusion(self, conclusion: Dict, market: str, run_id: str) -> str:
        """Store a brain conclusion from a sweep run."""
        doc_id   = str(uuid.uuid4())
        text     = json.dumps(conclusion)
        metadata = {
            "run_id":    run_id,
            "market":    market,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "urgency":   conclusion.get("urgency", "MEDIUM"),
            "confidence": conclusion.get("confidence", 50),
        }
        self._intel.add(
            ids=[doc_id],
            embeddings=self._embed([text]),
            documents=[text],
            metadatas=[metadata],
        )
        self._backup_to_redis(CONFIG.redis_key_chroma_intel, doc_id, text, metadata)
        return doc_id

    def recall_past_conclusions(self, query: str, n: int = 6, market: Optional[str] = None) -> List[str]:
        """Semantic search over past brain conclusions — injected into next run's context."""
        if self._intel.count() == 0:
            return []
        where = {"market": market} if market else None
        results = self._intel.query(
            query_embeddings=self._embed([query]),
            n_results=min(n, max(1, self._intel.count())),
            where=where,
        )
        docs = results.get("documents", [[]])[0]
        parsed = []
        for doc in docs:
            try:
                obj = json.loads(doc)
                parsed.append(obj.get("reasoning", doc)[:400])
            except Exception:
                parsed.append(doc[:400])
        return parsed

    def get_recent_run_conclusions(self, n_runs: int = 4) -> List[Dict]:
        """Retrieve conclusions from the last N sweep runs."""
        results = self._intel.get(
            where={"timestamp": {"$gte": "2024-01-01"}},
            include=["documents", "metadatas"],
        )
        docs  = results.get("documents", [])
        metas = results.get("metadatas", [])
        paired = sorted(
            zip(metas, docs),
            key=lambda x: x[0].get("timestamp", ""),
            reverse=True,
        )
        seen_runs = {}
        for meta, doc in paired:
            rid = meta.get("run_id")
            if rid not in seen_runs:
                seen_runs[rid] = []
            seen_runs[rid].append(doc)
            if len(seen_runs) >= n_runs:
                break

        summaries = []
        for rid, docs_ in list(seen_runs.items())[:n_runs]:
            summaries.append({
                "run_id": rid,
                "conclusions": [json.loads(d) if d.startswith("{") else d for d in docs_[:3]],
            })
        return summaries

    # ── Lead Memory ───────────────────────────────────────────────────────────

    def store_lead(self, lead: Dict, run_id: str) -> str:
        """Persist a generated BD lead."""
        lead_id  = lead.get("id", str(uuid.uuid4()))
        text     = json.dumps(lead)
        metadata = {
            "run_id":    run_id,
            "market":    lead.get("market", "unknown"),
            "urgency":   lead.get("urgency", "MEDIUM"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status":    "OPEN",
            "outcome":   "PENDING",
        }
        self._leads.upsert(
            ids=[lead_id],
            embeddings=self._embed([text]),
            documents=[text],
            metadatas=[metadata],
        )
        self._backup_to_redis(CONFIG.redis_key_chroma_leads, lead_id, text, metadata)
        return lead_id

    def find_similar_leads(self, query: str, n: int = 5) -> List[Dict]:
        """Find past leads similar to current query — deduplication aid."""
        if self._leads.count() == 0:
            return []
        results = self._leads.query(
            query_embeddings=self._embed([query]),
            n_results=min(n, self._leads.count()),
        )
        return [
            json.loads(doc) if doc.startswith("{") else {"raw": doc}
            for doc in results.get("documents", [[]])[0]
        ]

    def get_open_leads_for_market(self, market: str) -> List[Dict]:
        """Retrieve open leads for a specific market — for brain continuity check."""
        results = self._leads.get(
            where={"$and": [{"market": market}, {"status": "OPEN"}]},
            include=["documents", "metadatas"],
        )
        leads = []
        for doc, meta in zip(results.get("documents", []), results.get("metadatas", [])):
            try:
                obj = json.loads(doc)
                obj["_meta"] = meta
                leads.append(obj)
            except Exception:
                leads.append({"raw": doc, "_meta": meta})
        return leads

    def record_lead_outcome(self, lead_id: str, outcome: str, notes: str = "") -> bool:
        """
        Record deal outcome for ML feedback loop.
        outcome: 'WON' | 'LOST' | 'NO_BID' | 'IN_PROGRESS'
        """
        try:
            existing = self._leads.get(ids=[lead_id], include=["documents", "metadatas"])
            if not existing["documents"]:
                logger.warning(f"Lead {lead_id} not found for outcome recording")
                return False

            meta = existing["metadatas"][0]
            meta["status"]  = "CLOSED" if outcome in ("WON", "LOST", "NO_BID") else "OPEN"
            meta["outcome"] = outcome
            meta["outcome_notes"] = notes
            meta["outcome_at"]    = datetime.now(timezone.utc).isoformat()

            self._leads.update(ids=[lead_id], metadatas=[meta])

            # Mirror to outcomes collection for ML training
            doc      = existing["documents"][0]
            out_text = json.dumps({"lead": json.loads(doc) if doc.startswith("{") else doc,
                                   "outcome": outcome, "notes": notes})
            out_meta = {
                "lead_id": lead_id,
                "outcome": outcome,
                "market":  meta.get("market", "unknown"),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            self._outcomes.upsert(
                ids=[f"outcome_{lead_id}"],
                embeddings=self._embed([out_text]),
                documents=[out_text],
                metadatas=[out_meta],
            )
            self._backup_to_redis(
                CONFIG.redis_key_chroma_outcomes,
                f"outcome_{lead_id}",
                out_text,
                out_meta,
            )
            logger.info(f"Outcome recorded: lead={lead_id} outcome={outcome}")
            return True
        except Exception as e:
            logger.error(f"Outcome recording failed: {e}")
            return False

    # ── Outcome Data for ML Training ──────────────────────────────────────────

    def get_training_data(self) -> Tuple[List[Dict], List[str]]:
        """
        Returns (features_list, labels) for ML model training.
        Labels: 'WON', 'LOST', 'NO_BID'
        """
        results = self._outcomes.get(include=["documents", "metadatas"])
        X, y = [], []
        for doc, meta in zip(results.get("documents", []), results.get("metadatas", [])):
            if meta.get("outcome") not in ("WON", "LOST", "NO_BID"):
                continue
            try:
                obj  = json.loads(doc)
                lead = obj.get("lead", {})
                X.append({
                    "market":           lead.get("market", "unknown"),
                    "urgency":          lead.get("urgency", "MEDIUM"),
                    "confidence":       lead.get("confidence", 50),
                    "oem_match_count":  len(lead.get("oem_match_needed", [])),
                    "compliance_flags": len(lead.get("compliance_flags", [])),
                    "win_prob_adj":     lead.get("win_probability_adjustment", 0.0),
                })
                y.append(meta["outcome"])
            except Exception as e:
                logger.debug(f"Skipping malformed outcome record: {e}")
        return X, y

    def memory_stats(self) -> Dict:
        return {
            "intelligence_records": self._intel.count(),
            "lead_records":         self._leads.count(),
            "outcome_records":      self._outcomes.count(),
        }
