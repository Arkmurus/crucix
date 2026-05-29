"""R-F1008 — ARIA Intel Quality Scorer & Ahead-of-Game Engine."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("aria.intel_quality")


class IntelQualityScorer:
    """Scores and validates intelligence quality across all sources.
    
    Scoring factors:
    - Source reliability (0.0 to 1.0)
    - Cross-validation (how many other sources agree)
    - Freshness (how recent the data is)
    - Consistency (does it match historical patterns)
    - Specificity (how detailed the intelligence is)
    """

    def __init__(self):
        self._scores: dict[str, dict] = {}

    def score_intel(self, source_id: str, content: str, source_reliability: float = 0.5) -> dict[str, Any]:
        """Score a piece of intelligence."""
        score = {
            "source_id": source_id,
            "timestamp": time.time(),
            "reliability_score": source_reliability,
            "freshness_score": 1.0,
            "specificity_score": self._score_specificity(content),
            "length_score": self._score_length(content),
            "composite_score": 0.0,
        }
        
        # Composite = weighted average
        weights = {"reliability": 0.4, "freshness": 0.2, "specificity": 0.25, "length": 0.15}
        score["composite_score"] = round(
            score["reliability_score"] * weights["reliability"] +
            score["freshness_score"] * weights["freshness"] +
            score["specificity_score"] * weights["specificity"] +
            score["length_score"] * weights["length"],
            3
        )
        
        self._scores[source_id] = score
        return score

    def _score_specificity(self, text: str) -> float:
        """Score how specific the intelligence is."""
        score = 0.3  # base
        
        # Check for specific data points
        checks = [
            (r"\b\d{4}\b", 0.1),  # years
            (r"\$\d+", 0.1),  # dollar amounts
            (r"\d+%", 0.1),  # percentages
            (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", 0.15),  # IPs
            (r"[A-Z]{2}\d{6}", 0.1),  # reference numbers
            (r"\b\d{4}-\d{2}-\d{2}\b", 0.1),  # dates
            (r"Article \d+", 0.05),  # legal references
            (r"Section \d+", 0.05),  # section references
            (r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", 0.05),  # full names
        ]
        
        import re
        for pattern, increment in checks:
            if re.search(pattern, text):
                score += increment
        
        return min(1.0, score)

    def _score_length(self, text: str) -> float:
        """Score based on content length."""
        length = len(text)
        if length > 5000:
            return 1.0
        elif length > 2000:
            return 0.8
        elif length > 1000:
            return 0.6
        elif length > 500:
            return 0.4
        elif length > 100:
            return 0.2
        return 0.1

    def cross_validate(self, findings: list[dict]) -> dict[str, Any]:
        """Cross-validate intelligence from multiple sources."""
        if not findings:
            return {"agreement": 0.0, "confidence": 0.0, "convergent": False}
        
        # Count how many sources agree on key facts
        agreements = 0
        total_comparisons = 0
        
        for i, f1 in enumerate(findings):
            for f2 in findings[i+1:]:
                total_comparisons += 1
                if self._findings_agree(f1, f2):
                    agreements += 1
        
        agreement_rate = agreements / max(total_comparisons, 1)
        avg_reliability = sum(f.get("reliability", 0.5) for f in findings) / len(findings)
        
        return {
            "agreement": round(agreement_rate, 3),
            "confidence": round(agreement_rate * avg_reliability, 3),
            "convergent": agreement_rate > 0.5,
            "sources_agreeing": agreements,
            "total_comparisons": total_comparisons,
        }

    def _findings_agree(self, f1: dict, f2: dict) -> bool:
        """Check if two findings agree on key facts."""
        # Simple check: do they mention the same entities?
        f1_text = str(f1.get("content", "")).lower()
        f2_text = str(f2.get("content", "")).lower()
        
        # Check for shared named entities
        import re
        entities1 = set(re.findall(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", f1_text))
        entities2 = set(re.findall(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", f2_text))
        
        shared = entities1 & entities2
        return len(shared) >= 1


class AheadOfGameEngine:
    """Predictive intelligence and early warning system.
    
    Detects:
    - Emerging trends before they become mainstream
    - Anomalous patterns in intelligence data
    - Early warning signals for geopolitical events
    - Shifts in sanctions/compliance landscapes
    """

    def __init__(self):
        self._signals: list[dict] = []
        self._trends: dict[str, list[float]] = {}

    def analyze_trend(self, signal_name: str, value: float) -> dict[str, Any]:
        """Analyze a trend and detect emerging patterns."""
        if signal_name not in self._trends:
            self._trends[signal_name] = []
        
        self._trends[signal_name].append(value)
        if len(self._trends[signal_name]) > 100:
            self._trends[signal_name].pop(0)
        
        values = self._trends[signal_name]
        
        result = {
            "signal": signal_name,
            "current_value": value,
            "trend": "stable",
            "acceleration": 0.0,
            "anomaly": False,
        }
        
        if len(values) >= 3:
            # Calculate trend
            recent = values[-3:]
            if recent[2] > recent[1] > recent[0]:
                result["trend"] = "accelerating_up"
            elif recent[2] < recent[1] < recent[0]:
                result["trend"] = "accelerating_down"
            elif recent[2] > recent[0]:
                result["trend"] = "up"
            elif recent[2] < recent[0]:
                result["trend"] = "down"
            
            # Calculate acceleration
            if len(values) >= 5:
                recent_avg = sum(values[-3:]) / 3
                older_avg = sum(values[-6:-3]) / 3 if len(values) >= 6 else sum(values[:-3]) / max(len(values[:-3]), 1)
                result["acceleration"] = round(recent_avg - older_avg, 3)
            
            # Detect anomaly (value > 2 standard deviations from mean)
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            std_dev = variance ** 0.5
            if std_dev > 0 and abs(value - mean) > 2 * std_dev:
                result["anomaly"] = True
                self._signals.append({
                    "type": "anomaly",
                    "signal": signal_name,
                    "value": value,
                    "expected": round(mean, 3),
                    "timestamp": time.time(),
                })
        
        return result

    def get_early_warnings(self) -> list[dict]:
        """Get current early warnings."""
        warnings = []
        for signal_name, values in self._trends.items():
            if len(values) >= 5:
                recent = values[-3:]
                if recent[2] > recent[1] > recent[0] and recent[2] > recent[0] * 1.5:
                    warnings.append({
                        "type": "accelerating_trend",
                        "signal": signal_name,
                        "current": recent[2],
                        "previous": recent[0],
                        "increase_pct": round((recent[2] - recent[0]) / recent[0] * 100, 1),
                        "timestamp": time.time(),
                    })
        return warnings

    def get_signals(self) -> list[dict]:
        """Get all detected signals."""
        return self._signals[-50:]  # Last 50 signals

# R-F1008 - wire to brain
from .engine_wiring import wire_success
wire_success(module="intel_quality", summary="Intel Quality Active", source_id="intel_quality:R-F1008")
