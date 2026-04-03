"""
CRUCIX Autonomous Brain — ML Engine
Self-training machine learning layer:
  - RandomForest risk/win-probability scorer (retrained from real deal outcomes)
  - IsolationForest anomaly detector (unsupervised, no labels needed)
  - Feature engineering pipeline for defence procurement signals
  - Model persistence via Redis (base64-encoded pickle) — Render-compatible,
    no local disk writes needed.
"""
import base64
import json
import logging
import pickle
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, IsolationForest
from sklearn.metrics import classification_report
from sklearn.model_selection import cross_val_score
from sklearn.preprocessing import LabelEncoder, StandardScaler

from .config import CONFIG

logger = logging.getLogger("crucix.brain.ml")

# ── Constants ──────────────────────────────────────────────────────────────────

OUTCOME_LABELS = ["WON", "LOST", "NO_BID"]

MARKET_PRIORITY_MAP = {
    "Angola":              0.90,
    "Mozambique":          0.85,
    "Guinea-Bissau":       0.75,
    "Nigeria":             0.70,
    "Kenya":               0.65,
    "Cape Verde":          0.60,
    "Ghana":               0.58,
    "Senegal":             0.55,
    "Tanzania":            0.52,
    "Ivory Coast":         0.50,
    "São Tomé and Príncipe": 0.48,
    "Morocco":             0.45,
}

URGENCY_MAP = {"HIGH": 1.0, "MEDIUM": 0.5, "LOW": 0.2}


# ── Feature Engineering ────────────────────────────────────────────────────────

class FeatureEngineer:
    """Converts raw intelligence dicts into numpy feature vectors."""

    FEATURES = [
        "market_priority",
        "urgency_score",
        "confidence",
        "oem_match_count",
        "compliance_flag_count",
        "win_prob_adjustment",
        "has_procurement_signal",
        "has_relationship_signal",
        "has_anomaly_flag",
        "signal_source_count",
    ]

    def transform(self, records: List[Dict]) -> np.ndarray:
        return np.array([self._extract(r) for r in records])

    def _extract(self, r: Dict) -> List[float]:
        market = r.get("market", "unknown")
        return [
            MARKET_PRIORITY_MAP.get(market, 0.3),
            URGENCY_MAP.get(r.get("urgency", "MEDIUM"), 0.5),
            float(r.get("confidence", 50)) / 100.0,
            min(float(r.get("oem_match_count", 0)) / 5.0, 1.0),
            min(float(r.get("compliance_flags", 0)) / 3.0, 1.0),  # higher = worse
            float(r.get("win_prob_adj", 0.0)),
            float(r.get("has_procurement_signal", False)),
            float(r.get("has_relationship_signal", False)),
            float(r.get("has_anomaly_flag", False)),
            min(float(r.get("signal_source_count", 1)) / 10.0, 1.0),
        ]


# ── Win Probability / Risk Scorer ─────────────────────────────────────────────

class WinProbabilityModel:
    """
    Gradient Boosting classifier for deal win probability.
    Falls back to rule-based scoring until MIN_TRAINING_SAMPLES outcomes exist.
    Self-retrains whenever new outcomes are recorded.
    Model is persisted to Redis as base64-encoded pickle (Render-compatible).
    """

    def __init__(self, redis_client=None):
        self.redis    = redis_client
        self.fe       = FeatureEngineer()
        self.encoder  = LabelEncoder().fit(OUTCOME_LABELS)
        self.model    = None
        self.scaler   = StandardScaler()
        self.trained  = False
        self.meta: Dict = {}
        self._load()

    def _load(self):
        """Load model from Redis."""
        if not self.redis:
            return
        try:
            raw = self.redis.get(CONFIG.redis_key_win_prob_model)
            if not raw:
                return
            bundle = pickle.loads(base64.b64decode(raw))
            self.model   = bundle["model"]
            self.scaler  = bundle["scaler"]
            self.encoder = bundle["encoder"]
            self.meta    = bundle.get("meta", {})
            self.trained = True
            logger.info(
                f"Win-prob model loaded from Redis | "
                f"trained_on={self.meta.get('sample_count')} outcomes | "
                f"cv_score={self.meta.get('cv_score', 'N/A')}"
            )
        except Exception as e:
            logger.warning(f"Win-prob model Redis load failed: {e} — will use rule-based fallback")

    def _save(self):
        """Persist model to Redis as base64-encoded pickle."""
        if not self.redis:
            return
        try:
            bundle = {
                "model":   self.model,
                "scaler":  self.scaler,
                "encoder": self.encoder,
                "meta":    self.meta,
            }
            model_bytes = pickle.dumps(bundle)
            ttl = CONFIG.redis_model_ttl_days * 86400
            self.redis.set(
                CONFIG.redis_key_win_prob_model,
                base64.b64encode(model_bytes).decode(),
                ex=ttl,
            )
            logger.info("Win-prob model saved to Redis")
        except Exception as e:
            logger.error(f"Win-prob model Redis save failed: {e}")

    def train(self, records: List[Dict], labels: List[str]) -> Dict:
        """Retrain from outcome records. Returns training report."""
        if len(records) < CONFIG.min_training_samples:
            logger.info(
                f"Insufficient samples ({len(records)}) for ML training. "
                f"Need {CONFIG.min_training_samples}."
            )
            return {"status": "insufficient_data", "samples": len(records)}

        X = self.fe.transform(records)
        y = self.encoder.transform(labels)

        X_scaled = self.scaler.fit_transform(X)

        self.model = GradientBoostingClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42,
        )

        # Cross-validation before committing
        cv_scores = cross_val_score(self.model, X_scaled, y, cv=min(5, len(records) // 3 + 1))
        self.model.fit(X_scaled, y)
        self.trained = True

        self.meta = {
            "trained_at":    datetime.now(timezone.utc).isoformat(),
            "sample_count":  len(records),
            "cv_score":      float(cv_scores.mean()),
            "cv_std":        float(cv_scores.std()),
            "feature_names": FeatureEngineer.FEATURES,
        }

        self._save()

        logger.info(
            f"Win-prob model retrained | samples={len(records)} | "
            f"cv={cv_scores.mean():.3f}±{cv_scores.std():.3f}"
        )
        return {
            "status":   "trained",
            "samples":  len(records),
            "cv_score": float(cv_scores.mean()),
            "report":   classification_report(
                y, self.model.predict(X_scaled), target_names=OUTCOME_LABELS
            ),
        }

    def predict(self, record: Dict) -> Dict:
        """
        Predict win probability and outcome class.
        Falls back to rule-based if ML model not ready.
        """
        if self.trained and self.model is not None:
            return self._ml_predict(record)
        return self._rule_based_predict(record)

    def _ml_predict(self, record: Dict) -> Dict:
        X        = self.fe.transform([record])
        X_scaled = self.scaler.transform(X)
        proba    = self.model.predict_proba(X_scaled)[0]
        pred_idx = int(np.argmax(proba))
        classes  = self.encoder.classes_

        return {
            "predicted_outcome": classes[pred_idx],
            "win_probability":   float(proba[list(classes).index("WON")] if "WON" in classes else 0),
            "probabilities":     {cls: float(p) for cls, p in zip(classes, proba)},
            "model_type":        "gradient_boosting",
            "model_confidence":  self.meta.get("cv_score", 0),
        }

    def _rule_based_predict(self, record: Dict) -> Dict:
        """Deterministic scoring used before ML model is trained."""
        market   = record.get("market", "unknown")
        base     = MARKET_PRIORITY_MAP.get(market, 0.30)
        urgency  = URGENCY_MAP.get(record.get("urgency", "MEDIUM"), 0.5)
        conf     = float(record.get("confidence", 50)) / 100.0
        oem_ok   = 0.10 if record.get("oem_match_count", 0) > 0 else 0.0
        comp_pen = -0.15 * min(record.get("compliance_flags", 0), 2)

        win_prob = min(max(base * 0.5 + urgency * 0.25 + conf * 0.15 + oem_ok + comp_pen, 0.0), 1.0)
        return {
            "predicted_outcome": "WON" if win_prob > 0.55 else ("NO_BID" if win_prob < 0.25 else "LOST"),
            "win_probability":   round(win_prob, 3),
            "probabilities":     {"WON": win_prob, "LOST": 0.5 - win_prob / 2, "NO_BID": 0.5 - win_prob / 2},
            "model_type":        "rule_based_fallback",
            "model_confidence":  0.5,
        }

    def feature_importance(self) -> Optional[Dict]:
        if not self.trained or self.model is None:
            return None
        return dict(zip(FeatureEngineer.FEATURES, self.model.feature_importances_.tolist()))


# ── Anomaly Detector ──────────────────────────────────────────────────────────

class SignalAnomalyDetector:
    """
    IsolationForest for detecting unusual patterns in intelligence signal streams.
    Unsupervised — starts working immediately without labelled data.
    Model is persisted to Redis as base64-encoded pickle (Render-compatible).
    """

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self.fe    = FeatureEngineer()
        self.model = IsolationForest(
            n_estimators=200,
            contamination=CONFIG.anomaly_contamination,
            random_state=42,
            n_jobs=-1,
        )
        self.fitted       = False
        self.signal_buffer: List[Dict] = []
        self._load()

    def _load(self):
        """Load anomaly detector from Redis."""
        if not self.redis:
            return
        try:
            raw = self.redis.get(CONFIG.redis_key_anomaly_model)
            if not raw:
                return
            bundle = pickle.loads(base64.b64decode(raw))
            self.model  = bundle["model"]
            self.fitted = True
            logger.info("Anomaly detector loaded from Redis")
        except Exception as e:
            logger.warning(f"Anomaly detector Redis load failed: {e}")

    def _save(self):
        """Persist anomaly detector to Redis."""
        if not self.redis:
            return
        try:
            model_bytes = pickle.dumps({"model": self.model})
            ttl = CONFIG.redis_model_ttl_days * 86400
            self.redis.set(
                CONFIG.redis_key_anomaly_model,
                base64.b64encode(model_bytes).decode(),
                ex=ttl,
            )
        except Exception as e:
            logger.error(f"Anomaly detector Redis save failed: {e}")

    def update(self, records: List[Dict]):
        """Add new signals and refit detector. Online-style updates."""
        self.signal_buffer.extend(records)
        if len(self.signal_buffer) < 10:
            return
        X = self.fe.transform(self.signal_buffer[-500:])  # rolling window of 500
        self.model.fit(X)
        self.fitted = True
        self._save()
        logger.debug(f"Anomaly detector refitted on {len(self.signal_buffer)} signals")

    def score_signal(self, record: Dict) -> Dict:
        """
        Score a single signal for anomalousness.
        Returns anomaly_score (-1 = anomaly, 1 = normal) and normalised 0-1 risk delta.
        """
        if not self.fitted:
            return {"anomaly": False, "anomaly_score": 0.0, "is_anomaly": False}

        X     = self.fe.transform([record])
        score = float(self.model.score_samples(X)[0])     # more negative = more anomalous
        pred  = int(self.model.predict(X)[0])              # -1 anomaly, 1 normal

        # Normalise to 0-1 risk delta (anomaly detector contribution)
        normalised = max(0.0, min(1.0, (-score + 0.5) / 1.0))

        return {
            "is_anomaly":    pred == -1,
            "anomaly_score": score,
            "risk_delta":    round(normalised, 3),
            "explanation":   "Signal pattern deviates significantly from baseline" if pred == -1
                             else "Signal within normal parameters",
        }

    def batch_score(self, records: List[Dict]) -> List[Dict]:
        return [self.score_signal(r) for r in records]


# ── NLP Risk Scorer (text-based) ──────────────────────────────────────────────

class NLPRiskScorer:
    """
    Keyword and pattern-based risk scoring for counterparty documents.
    Combines with DeepSeek LLM for full NLP analysis.
    """

    HIGH_RISK_KEYWORDS = [
        "sanctioned", "embargo", "debarred", "OFAC", "OFSI", "SDN list",
        "money laundering", "shell company", "beneficial ownership unclear",
        "no verifiable address", "newly incorporated", "PEP", "politically exposed",
        "arms trafficking", "UN arms embargo", "ITAR violation",
    ]

    MEDIUM_RISK_KEYWORDS = [
        "undisclosed", "offshore", "nominee director", "zero employees",
        "single purpose vehicle", "no audited accounts", "parent company unclear",
        "jurisdiction: BVI", "jurisdiction: Cayman", "intermediary only",
    ]

    POSITIVE_KEYWORDS = [
        "ISO certified", "NATO approved", "ITAR registered", "EAR99 eligible",
        "audited accounts", "established", "publicly traded", "listed company",
        "government entity", "ministry of defence",
    ]

    def score_document(self, text: str, entity_name: str = "") -> Dict:
        text_lower = text.lower()
        high_hits  = [kw for kw in self.HIGH_RISK_KEYWORDS   if kw.lower() in text_lower]
        med_hits   = [kw for kw in self.MEDIUM_RISK_KEYWORDS  if kw.lower() in text_lower]
        pos_hits   = [kw for kw in self.POSITIVE_KEYWORDS     if kw.lower() in text_lower]

        base_score = (len(high_hits) * 20) + (len(med_hits) * 8) - (len(pos_hits) * 5)
        score      = min(max(base_score, 0), 100)
        tier       = "HIGH" if score >= 60 else ("MEDIUM" if score >= 30 else "LOW")

        return {
            "entity":       entity_name,
            "risk_score":   score,
            "risk_tier":    tier,
            "red_flags":    high_hits,
            "amber_flags":  med_hits,
            "green_flags":  pos_hits,
            "method":       "nlp_keyword",
            "note":         "Combine with DeepSeek LLM for full analysis",
        }


# ── Unified ML Engine ─────────────────────────────────────────────────────────

class CrucixMLEngine:
    """Single entry point for all ML capabilities."""

    def __init__(self, redis_client=None):
        self.redis    = redis_client
        self.win_prob = WinProbabilityModel(redis_client=redis_client)
        self.anomaly  = SignalAnomalyDetector(redis_client=redis_client)
        self.nlp_risk = NLPRiskScorer()

    def score_opportunity(self, record: Dict) -> Dict:
        """Full opportunity score: ML win-prob + anomaly contribution."""
        wp   = self.win_prob.predict(record)
        anom = self.anomaly.score_signal(record)

        # Blend: anomaly raises risk awareness but doesn't tank win-prob
        blended_win_prob = wp["win_probability"] * (1.0 - anom["risk_delta"] * 0.3)

        return {
            **wp,
            "win_probability_blended": round(blended_win_prob, 3),
            "anomaly_assessment":      anom,
        }

    def retrain(self, records: List[Dict], labels: List[str]) -> Dict:
        result = self.win_prob.train(records, labels)
        self.anomaly.update(records)
        return result

    def update_anomaly_baseline(self, signals: List[Dict]):
        self.anomaly.update(signals)

    def score_counterparty_document(self, text: str, entity_name: str = "") -> Dict:
        return self.nlp_risk.score_document(text, entity_name)

    def feature_importance(self) -> Optional[Dict]:
        return self.win_prob.feature_importance()

    def model_status(self) -> Dict:
        return {
            "win_probability_model": {
                "trained":  self.win_prob.trained,
                "metadata": self.win_prob.meta,
            },
            "anomaly_detector": {
                "fitted":        self.anomaly.fitted,
                "signal_buffer": len(self.anomaly.signal_buffer),
                "contamination": CONFIG.anomaly_contamination,
            },
        }
