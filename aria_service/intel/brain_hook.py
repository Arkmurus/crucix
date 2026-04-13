"""
ARIA Brain Hook — Central Learning Relay for All Intel Modules.

Every intel module that produces analysis output calls brain_hook.absorb()
after completing its work. This fans out to all five learning tiers:

  1. student.update_mastery()  — EWMA competence per topic
  2. knowledge.store_fact()    — persistent verified facts
  3. neural_memory.learn_from_text() — associative concept graph
  4. capability_gaps.record_gap() — if the module hit a limitation

One function, one call, non-fatal. Modules never need to know about
the internal learning architecture — they just feed the brain.

Added 2026-04-13 to close the gap where 19/24 intel modules were
producing intelligence that vanished without feeding learning.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("aria.brain_hook")

# ── Feature gate ────────────────────────────────────────────────────────────
import os
BRAIN_HOOK_ENABLED = os.environ.get("ARIA_BRAIN_HOOK_ENABLED", "1") == "1"

# Topic mapping: module name → student mastery topics it touches
_MODULE_TOPICS: dict[str, list[str]] = {
    "dd_orchestrator":      ["compliance", "osint", "finance", "relationships"],
    "compliance_workflow":  ["compliance", "legal"],
    "contract_intelligence": ["legal", "procurement"],
    "tender_monitor":       ["procurement", "market_intel"],
    "link_investigator":    ["osint", "relationships"],
    "financial_dd":         ["finance", "compliance"],
    "network_walker":       ["relationships", "compliance", "osint"],
    "entity_graph":         ["relationships", "osint"],
    "deep_researcher":      ["osint", "market_intel", "geopolitics"],
    "person_resolver":      ["relationships", "osint"],
    "competitors":          ["competitor_intel", "market_intel"],
    "gtm_strategy":         ["market_intel", "procurement"],
    "risk_indices":         ["compliance", "geopolitics"],
    "global_export_control": ["compliance", "legal", "technical"],
    "symbolic_reasoner":    ["general"],
    "source_verifier":      ["osint"],
    "sanctions":            ["compliance", "legal"],
    "conflict_tracker":     ["geopolitics", "relationships"],
    "international_law":    ["legal", "compliance"],
    # Node.js seenode modules (via POST /api/aria/brain/absorb)
    "registry_adapter":     ["compliance", "legal"],
    "opportunity_detector": ["market_intel", "competitor_intel"],
    "signal_generator":     ["finance", "compliance", "market_intel"],
    "osint_sweep":          ["osint", "market_intel"],
    "seenode":              ["general"],
}

# Default mastery weight per module (how much a successful run boosts score)
_MODULE_WEIGHT: dict[str, float] = {
    "dd_orchestrator":      0.3,   # full DD is a major exercise
    "compliance_workflow":  0.2,
    "contract_intelligence": 0.25,
    "tender_monitor":       0.1,   # automated scan, lighter signal
    "link_investigator":    0.2,
    "financial_dd":         0.2,
    "network_walker":       0.2,
    "deep_researcher":      0.25,
    "person_resolver":      0.1,
    "competitors":          0.15,
    "gtm_strategy":         0.15,
    "risk_indices":         0.1,
    "global_export_control": 0.2,
    "symbolic_reasoner":    0.1,
    "source_verifier":      0.1,
    "sanctions":            0.15,
    "conflict_tracker":     0.1,
    "international_law":    0.15,
    # Node.js seenode modules
    "registry_adapter":     0.15,
    "opportunity_detector": 0.15,
    "signal_generator":     0.15,
    "osint_sweep":          0.1,
    "seenode":              0.1,
}


async def absorb(
    *,
    module: str,
    summary: str,
    detail: str = "",
    entity_name: str = "",
    success: bool = True,
    gap_type: Optional[str] = None,
    gap_detail: Optional[str] = None,
    extra_topics: Optional[list[str]] = None,
    source_id: str = "",
    confidence: str = "PROBABLE",
) -> dict:
    """Feed one intel module's output into all learning tiers.

    Args:
        module:       Module name (must be a key in _MODULE_TOPICS).
        summary:      1-3 sentence summary of what was produced.
        detail:       Full text for neural_memory concept extraction.
                      If empty, summary is used.
        entity_name:  Primary entity name (for knowledge topic keying).
        success:      Did the module complete successfully? False triggers
                      negative mastery update + optional capability gap.
        gap_type:     If not None, records a capability gap (e.g. 'api_missing',
                      'knowledge_gap', 'timeout').
        gap_detail:   Description for the capability gap record.
        extra_topics: Additional mastery topics beyond the module default.
        source_id:    Unique run/trace ID for attribution.
        confidence:   Knowledge confidence level (CONFIRMED/PROBABLE/ASSESSED).

    Returns:
        dict with keys: mastery_ok, knowledge_ok, neural_ok, gap_ok, errors.
    """
    if not BRAIN_HOOK_ENABLED:
        return {"skipped": True, "reason": "ARIA_BRAIN_HOOK_ENABLED=0"}

    result = {
        "mastery_ok": False,
        "knowledge_ok": False,
        "neural_ok": False,
        "gap_ok": True,  # no gap to record = ok
        "errors": [],
    }

    topics = list(_MODULE_TOPICS.get(module, ["general"]))
    if extra_topics:
        for t in extra_topics:
            if t not in topics:
                topics.append(t)

    weight = _MODULE_WEIGHT.get(module, 0.15)
    text_for_neural = detail or summary
    source = f"brain_hook:{module}"
    if source_id:
        source += f":{source_id}"

    # ── 1. Student mastery ──────────────────────────────────────────────
    try:
        from . import student
        await student.update_mastery(topics, correct=success, weight=weight)
        result["mastery_ok"] = True
    except Exception as e:
        result["errors"].append(f"mastery: {e}")
        logger.debug("brain_hook mastery failed: %s", e)

    # ── 2. Knowledge store ──────────────────────────────────────────────
    if summary:
        try:
            from . import knowledge
            topic_key = f"{module}:{entity_name}" if entity_name else module
            await knowledge.store_fact(
                topic=topic_key,
                content=summary[:2000],
                source=source,
                confidence=confidence,
            )
            result["knowledge_ok"] = True
        except Exception as e:
            result["errors"].append(f"knowledge: {e}")
            logger.debug("brain_hook knowledge failed: %s", e)

    # ── 3. Neural memory ────────────────────────────────────────────────
    if text_for_neural and len(text_for_neural) > 50:
        try:
            from . import neural_memory
            await neural_memory.learn_from_text(
                text=text_for_neural[:5000],
                source=source,
                confidence=confidence,
            )
            result["neural_ok"] = True
        except Exception as e:
            result["errors"].append(f"neural: {e}")
            logger.debug("brain_hook neural failed: %s", e)

    # ── 4. Capability gap (only on failure or explicit gap) ─────────────
    if gap_type:
        try:
            from . import capability_gaps
            await capability_gaps.record_gap(
                gap_type=gap_type,
                description=gap_detail or f"{module} reported gap: {gap_type}",
                module=module,
            )
            result["gap_ok"] = True
        except Exception as e:
            result["errors"].append(f"gap: {e}")
            logger.debug("brain_hook gap record failed: %s", e)

    if result["errors"]:
        logger.warning("brain_hook(%s): %d errors — %s",
                        module, len(result["errors"]), "; ".join(result["errors"]))
    else:
        logger.info("brain_hook(%s): absorbed [mastery=%s knowledge=%s neural=%s]",
                     module, result["mastery_ok"], result["knowledge_ok"], result["neural_ok"])

    return result


async def absorb_silent(**kwargs) -> None:
    """Fire-and-forget wrapper — logs errors but never raises."""
    try:
        await absorb(**kwargs)
    except Exception as e:
        logger.debug("brain_hook.absorb_silent failed entirely: %s", e)
