"""
CRUCIX Autonomous Brain — Feedback Loop
Closes the learning cycle: deal outcomes → ML retraining → better predictions.
Also manages source reliability scoring and adaptive region weighting.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import redis

from .config import CONFIG
from .memory import CrucixMemory
from .ml_engine import CrucixMLEngine

logger = logging.getLogger("crucix.brain.feedback")


class FeedbackLoop:
    """
    Manages the full learning cycle for Crucix:
      1. Receive outcome signals (WON/LOST/NO_BID, user ratings, source reliability)
      2. Persist to vector memory
      3. Retrain ML models when threshold reached
      4. Adjust source weights and regional scoring
      5. Expose metrics for admin dashboard
    """

    # Redis key schema
    KEY_SOURCE_WEIGHTS   = CONFIG.redis_brain_prefix + "source_weights"
    KEY_REGION_SCORES    = CONFIG.redis_brain_prefix + "region_scores"
    KEY_RETRAIN_QUEUE    = CONFIG.redis_brain_prefix + "retrain_queue"
    KEY_FEEDBACK_METRICS = CONFIG.redis_brain_prefix + "feedback_metrics"
    KEY_SOURCE_HEALTH    = CONFIG.redis_brain_prefix + "source_health"

    # Default source weights (adjusted over time by reliability)
    DEFAULT_SOURCE_WEIGHTS = {
        "acled":        1.0,
        "reliefweb":    0.95,
        "defenceweb":   0.90,
        "dsca_fms":     1.0,
        "rfi_afrique":  0.85,
        "dw_afrika":    0.85,
        "voa_portugues":0.80,
        "angola_press": 0.75,
        "afdb":         0.90,
        "ecowas":       0.85,
        "au_psc":       0.85,
        "janes":        1.0,
        "nato":         1.0,
        "web_explorer": 0.70,
        "procurement_portal": 1.0,   # direct government portals = highest trust
    }

    def __init__(self, memory: CrucixMemory, ml_engine: CrucixMLEngine):
        self.memory    = memory
        self.ml_engine = ml_engine
        try:
            self.redis = redis.from_url(CONFIG.redis_url, decode_responses=True)
            self.redis.ping()
            logger.info("Feedback loop connected to Redis")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            self.redis = None

        self._init_weights()

    def _init_weights(self):
        """Initialise weights in Redis if not present."""
        if self.redis and not self.redis.exists(self.KEY_SOURCE_WEIGHTS):
            self.redis.hset(self.KEY_SOURCE_WEIGHTS, mapping=self.DEFAULT_SOURCE_WEIGHTS)
        if self.redis and not self.redis.exists(self.KEY_REGION_SCORES):
            initial = {m: str(s) for m, s in {
                "Angola": 0.90, "Mozambique": 0.85, "Guinea-Bissau": 0.75,
                "Nigeria": 0.70, "Kenya": 0.65,
            }.items()}
            self.redis.hset(self.KEY_REGION_SCORES, mapping=initial)

    # ── Outcome Recording (human-in-the-loop) ────────────────────────────────

    def record_outcome(self, lead_id: str, outcome: str, market: str,
                       notes: str = "", user_rating: Optional[int] = None) -> Dict:
        """
        Record a deal outcome. Called from dashboard or Telegram /outcome command.
        outcome: WON | LOST | NO_BID
        user_rating: 1-5 quality rating of the lead recommendation
        """
        # Persist to vector memory
        success = self.memory.record_lead_outcome(lead_id, outcome, notes)

        if not success:
            return {"status": "error", "message": f"Lead {lead_id} not found"}

        # Queue for ML retraining
        if self.redis:
            self.redis.rpush(self.KEY_RETRAIN_QUEUE, json.dumps({
                "lead_id": lead_id,
                "outcome": outcome,
                "market":  market,
                "notes":   notes,
                "queued_at": datetime.now(timezone.utc).isoformat(),
            }))

            # Update regional success metrics
            self._update_region_score(market, outcome)

            # Track user rating of brain recommendation quality
            if user_rating is not None:
                self._record_recommendation_quality(lead_id, user_rating)

        # Trigger retraining if queue is large enough
        retrain_result = self._maybe_retrain()

        logger.info(f"Outcome recorded: lead={lead_id} outcome={outcome} market={market}")
        return {
            "status":         "recorded",
            "lead_id":        lead_id,
            "outcome":        outcome,
            "retrain_status": retrain_result,
        }

    def record_lead_rating(self, lead_id: str, rating: int, is_real_tender: Optional[bool] = None,
                            is_false_alarm: bool = False) -> Dict:
        """
        Human feedback on brain recommendation quality.
        rating: 1 (useless) to 5 (excellent)
        """
        self._record_recommendation_quality(lead_id, rating)
        if is_false_alarm:
            self._penalise_sources_for_false_alarm(lead_id)
        logger.info(f"Lead rating: {lead_id} rated {rating}/5")
        return {"status": "rated", "lead_id": lead_id, "rating": rating}

    # ── Source Reliability Tracking ───────────────────────────────────────────

    def record_source_fetch(self, source: str, success: bool, latency_ms: int = 0):
        """Called by sweep engine after each source fetch attempt."""
        if not self.redis:
            return
        health_key = f"{self.KEY_SOURCE_HEALTH}:{source}"
        pipe = self.redis.pipeline()
        pipe.hincrby(health_key, "total_fetches", 1)
        if success:
            pipe.hincrby(health_key, "successful_fetches", 1)
        pipe.hset(health_key, "last_latency_ms", latency_ms)
        pipe.hset(health_key, "last_attempt", datetime.now(timezone.utc).isoformat())
        pipe.expire(health_key, 7 * 86400)   # 7-day rolling window
        pipe.execute()

        # Dynamically adjust source weight based on reliability
        self._adjust_source_weight(source)

    def get_source_reliability_report(self) -> List[Dict]:
        """Returns admin-visible source health for dashboard."""
        if not self.redis:
            return []
        report = []
        pattern = f"{self.KEY_SOURCE_HEALTH}:*"
        for key in self.redis.scan_iter(pattern):
            source = key.split(":")[-1]
            data   = self.redis.hgetall(key)
            total  = int(data.get("total_fetches", 0))
            success = int(data.get("successful_fetches", 0))
            reliability = (success / total * 100) if total > 0 else 0
            weight = float(self.redis.hget(self.KEY_SOURCE_WEIGHTS, source) or 1.0)
            report.append({
                "source":          source,
                "total_fetches":   total,
                "successful":      success,
                "reliability_pct": round(reliability, 1),
                "current_weight":  round(weight, 3),
                "last_latency_ms": data.get("last_latency_ms", "N/A"),
                "last_attempt":    data.get("last_attempt", "N/A"),
                "status":          "HEALTHY" if reliability >= 80 else ("DEGRADED" if reliability >= 50 else "FAILING"),
            })
        return sorted(report, key=lambda x: x["reliability_pct"])

    def get_source_weight(self, source: str) -> float:
        if not self.redis:
            return self.DEFAULT_SOURCE_WEIGHTS.get(source, 0.7)
        weight = self.redis.hget(self.KEY_SOURCE_WEIGHTS, source)
        return float(weight) if weight else self.DEFAULT_SOURCE_WEIGHTS.get(source, 0.7)

    def get_region_score(self, market: str) -> float:
        if not self.redis:
            return 0.5
        score = self.redis.hget(self.KEY_REGION_SCORES, market)
        return float(score) if score else 0.5

    # ── ML Retraining ─────────────────────────────────────────────────────────

    def _maybe_retrain(self) -> Dict:
        """Retrain ML models if enough new outcomes have accumulated."""
        if not self.redis:
            return {"status": "skipped", "reason": "no_redis"}

        queue_len = self.redis.llen(self.KEY_RETRAIN_QUEUE)
        if queue_len < 5:   # batch threshold — retrain after 5 new outcomes
            return {"status": "queued", "queue_size": queue_len}

        logger.info(f"Triggering ML retrain with {queue_len} new outcomes in queue")
        records, labels = self.memory.get_training_data()

        if len(records) < CONFIG.min_training_samples:
            return {"status": "insufficient_data", "samples": len(records)}

        result = self.ml_engine.retrain(records, labels)

        # Clear the queue after successful retrain
        self.redis.delete(self.KEY_RETRAIN_QUEUE)

        # Store retrain event
        metrics = self.redis.hgetall(self.KEY_FEEDBACK_METRICS) or {}
        metrics["last_retrain"]    = datetime.now(timezone.utc).isoformat()
        metrics["last_cv_score"]   = str(result.get("cv_score", "N/A"))
        metrics["total_outcomes"]  = str(len(records))
        self.redis.hset(self.KEY_FEEDBACK_METRICS, mapping=metrics)

        return result

    def force_retrain(self) -> Dict:
        """Manual retrain trigger — callable from admin dashboard."""
        records, labels = self.memory.get_training_data()
        return self.ml_engine.retrain(records, labels)

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def _update_region_score(self, market: str, outcome: str):
        if not self.redis:
            return
        current = float(self.redis.hget(self.KEY_REGION_SCORES, market) or 0.5)
        delta   = 0.05 if outcome == "WON" else (-0.03 if outcome == "LOST" else -0.01)
        new_score = min(max(current + delta, 0.1), 1.0)
        self.redis.hset(self.KEY_REGION_SCORES, market, new_score)
        logger.debug(f"Region score updated: {market} {current:.3f} → {new_score:.3f} (outcome={outcome})")

    def _adjust_source_weight(self, source: str):
        if not self.redis:
            return
        health_key = f"{self.KEY_SOURCE_HEALTH}:{source}"
        data    = self.redis.hgetall(health_key)
        total   = int(data.get("total_fetches", 0))
        success = int(data.get("successful_fetches", 0))
        if total < 5:
            return
        reliability  = success / total
        default      = self.DEFAULT_SOURCE_WEIGHTS.get(source, 0.7)
        current      = float(self.redis.hget(self.KEY_SOURCE_WEIGHTS, source) or default)
        # Exponential moving average towards reliability
        new_weight = 0.8 * current + 0.2 * (default * reliability)
        new_weight = round(min(max(new_weight, 0.1), 1.0), 3)
        self.redis.hset(self.KEY_SOURCE_WEIGHTS, source, new_weight)

    def _record_recommendation_quality(self, lead_id: str, rating: int):
        if not self.redis:
            return
        key = f"{CONFIG.redis_brain_prefix}quality:{lead_id}"
        self.redis.set(key, rating, ex=30 * 86400)   # 30-day retention
        # Track running average
        metrics = self.redis.hgetall(self.KEY_FEEDBACK_METRICS) or {}
        total_ratings = int(metrics.get("total_ratings", 0)) + 1
        total_score   = float(metrics.get("total_rating_score", 0)) + rating
        metrics["total_ratings"]      = str(total_ratings)
        metrics["total_rating_score"] = str(total_score)
        metrics["avg_rating"]         = str(round(total_score / total_ratings, 2))
        self.redis.hset(self.KEY_FEEDBACK_METRICS, mapping=metrics)

    def _penalise_sources_for_false_alarm(self, lead_id: str):
        """Reduce weight of sources that contributed to a false alarm."""
        logger.info(f"False alarm flagged for lead {lead_id} — source penalty queued")
        # In production: retrieve the source tags from the lead and apply weight reduction

    def get_feedback_metrics(self) -> Dict:
        if not self.redis:
            return {}
        metrics = self.redis.hgetall(self.KEY_FEEDBACK_METRICS) or {}
        metrics["retrain_queue_size"] = self.redis.llen(self.KEY_RETRAIN_QUEUE)
        metrics["ml_model_status"]    = self.ml_engine.model_status()
        return metrics
