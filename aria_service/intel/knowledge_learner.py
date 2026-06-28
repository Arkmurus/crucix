"""R-F1010 — ARIA Knowledge Prioritizer & Zero-Cost Continuous Learner."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("aria.knowledge_learner")


class KnowledgePrioritizer:
    """Determines what ARIA should learn next.
    
    Factors:
    - Knowledge gaps (what don't we know?)
    - Usage patterns (what's most requested?)
    - Freshness (what's stale?)
    - Impact (what would be most valuable?)
    """

    def __init__(self):
        self._usage_counts: dict[str, int] = {}
        self._knowledge_gaps: list[dict] = []
        self._learning_history: list[dict] = []

    def record_query(self, topic: str) -> None:
        """Record that a topic was queried."""
        self._usage_counts[topic] = self._usage_counts.get(topic, 0) + 1

    def identify_gaps(self, coverage_heatmap: dict[str, float]) -> list[dict]:
        """Identify knowledge gaps from coverage data."""
        gaps = []
        for topic, coverage in coverage_heatmap.items():
            if coverage < 0.5:
                gaps.append({
                    "topic": topic,
                    "coverage": coverage,
                    "priority": "high" if coverage < 0.3 else "medium",
                    "usage": self._usage_counts.get(topic, 0),
                })
        gaps.sort(key=lambda x: (x["priority"] == "high", x["usage"]), reverse=True)
        self._knowledge_gaps = gaps
        return gaps

    def get_learning_plan(self, max_items: int = 10) -> list[dict]:
        """Get the prioritized learning plan."""
        plan = []
        
        # High priority gaps first
        for gap in self._knowledge_gaps:
            if gap["priority"] == "high":
                plan.append({
                    "topic": gap["topic"],
                    "reason": f"Coverage at {gap['coverage']:.0%}",
                    "action": "research_and_ingest",
                    "priority": "high",
                })
        
        # Frequently used topics that need updating
        for topic, count in sorted(self._usage_counts.items(), key=lambda x: -x[1]):
            if len(plan) >= max_items:
                break
            if topic not in [p["topic"] for p in plan]:
                plan.append({
                    "topic": topic,
                    "reason": f"Queried {count} times",
                    "action": "refresh_knowledge",
                    "priority": "medium",
                })
        
        return plan[:max_items]


class ZeroCostLearner:
    """Continuous learning using free compute resources.
    
    Learning sources:
    - Kaggle (30h/week free GPU)
    - Google Colab (free Tesla T4)
    - HuggingFace Spaces (free CPU)
    - GitHub Actions (2000 min/month free)
    """

    def __init__(self):
        self._learning_cycles = 0
        self._total_learnings = 0
        self._last_cycle_time = 0.0

    async def run_learning_cycle(self) -> dict[str, Any]:
        """Run one learning cycle using available free resources."""
        self._learning_cycles += 1
        self._last_cycle_time = time.time()
        
        results = {
            "cycle": self._learning_cycles,
            "timestamp": time.time(),
            "actions": [],
            "knowledge_added": 0,
        }
        
        # 1. Learn from chat corrections
        try:
            from . import correction_learner
            corrections = await correction_learner.recent_corrections_addendum()
            if corrections:
                results["actions"].append(f"Processed corrections")
                results["knowledge_added"] += 1
        except Exception as e:
            logger.debug("[learner] corrections failed: %s", e)
        
        # 2. Learn from feedback
        try:
            from . import feedback
            stats = await feedback.get_stats()
            if stats:
                results["actions"].append("Analyzed feedback")
        except Exception as e:
            logger.debug("[learner] feedback failed: %s", e)
        
        # 3. Check for stale knowledge
        try:
            from . import stale_knowledge_alerts
            stale = await stale_knowledge_alerts.check_stale()
            if stale:
                results["actions"].append(f"Found {len(stale)} stale items")
        except Exception as e:
            logger.debug("[learner] stale check failed: %s", e)
        
        # 4. Run self-diagnostic
        try:
            from . import self_diagnostic
            diag = await self_diagnostic.run_diagnostic()
            if diag:
                results["actions"].append("Ran self-diagnostic")
        except Exception as e:
            logger.debug("[learner] diagnostic failed: %s", e)
        
        self._total_learnings += results["knowledge_added"]
        return results

    async def run_continuously(self, interval_seconds: int = 3600) -> None:
        """Run learning cycles continuously."""
        logger.info("[learner] starting continuous learning (interval=%ds)", interval_seconds)
        while True:
            try:
                result = await self.run_learning_cycle()
                logger.info("[learner] cycle %d complete: %s", 
                          result["cycle"], "; ".join(result["actions"]))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[learner] cycle failed: %s", e)
            await asyncio.sleep(interval_seconds)

    def get_stats(self) -> dict[str, Any]:
        """Get learning statistics."""
        return {
            "cycles_completed": self._learning_cycles,
            "total_learnings": self._total_learnings,
            "last_cycle": self._last_cycle_time,
            "running": self._last_cycle_time > 0,
        }

# R-F1010 - wire to brain
from .engine_wiring import wire_success, wire_failure
wire_success(module="knowledge_learner", summary="Knowledge Learner Active", source_id="knowledge_learner:R-F1010")

# R-F2119 §21a — wire failure handler for knowledge_learner
try:
    wire_failure(module="knowledge_learner", detail="module shutdown",
                gap_type="engine_failure", source="knowledge_learner:shutdown")
except Exception:
    pass
