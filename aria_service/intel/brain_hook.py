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
    "dual_use_classifier":  ["compliance", "legal", "technical"],
    "euc_library":          ["compliance", "legal"],
    "audit_log":            ["compliance", "legal"],
    "compliance_file":      ["compliance", "legal"],
    "symbolic_reasoner":    ["general"],
    "source_verifier":      ["osint"],
    "sanctions":            ["compliance", "legal"],
    "conflict_tracker":     ["geopolitics", "relationships"],
    "international_law":    ["legal", "compliance"],
    # Node.js seenode modules (via POST /api/aria/brain/absorb — registered
    # here so their signals are correctly topic-mapped when they arrive)
    "registry_adapter":     ["compliance", "legal"],
    "opportunity_detector": ["market_intel", "competitor_intel"],
    "signal_generator":     ["finance", "compliance", "market_intel"],
    "knowledge_ingestor":   ["general", "compliance", "legal"],
    # Core Self-Development Loop (Clauses 17/18/19) — shipped 2026-04-15
    "verified_intel":       ["compliance", "osint", "legal"],
    "web_atlas":            ["osint", "market_intel"],
    "source_validator":     ["osint", "compliance"],
    "source_scout":         ["osint", "market_intel"],
    "search_doctrine":      ["osint", "general"],
    "core_develop":         ["general"],
    "ecosystem_reassess":   ["general"],
    "golden_autogen":       ["osint", "general"],
    "adversarial_challenge": ["compliance", "general", "osint"],
    "narrative_monitor": ["osint", "geopolitics", "competitor_intel"],
    # Priority 1 (2026-04-17) — long-horizon causal chain correlator
    "chain_correlator":     ["geopolitics", "procurement", "relationships", "market_intel"],
    # Priorities 2-4 (2026-04-17) — temporal + competitor + OEM graph
    "procurement_calendar": ["procurement", "geopolitics", "market_intel"],
    "competitor_tracker":   ["competitor_intel", "market_intel"],
    "oem_contact_graph":    ["relationships", "market_intel", "competitor_intel"],
    # Tier 1 regional knowledge modules + equipment specs (2026-04-17)
    "knowledge_gulf":              ["market_intel", "procurement", "compliance"],
    "knowledge_turkey_standalone": ["market_intel", "procurement", "compliance"],
    "knowledge_west_africa":       ["market_intel", "procurement", "compliance"],
    "knowledge_latam_non_lusophone": ["market_intel", "procurement", "compliance"],
    "equipment_specs":      ["technical", "market_intel", "procurement"],
    "sipri_ingest":         ["market_intel", "procurement"],
    # Writer package (2026-04-17) — structured document production.
    "writer_orchestrator":  ["general", "legal", "compliance", "procurement"],
    # NAK / SERBAN / F3 learnings (2026-04-17) — six new capabilities closing
    # the gaps observed on the live KNDS / FK-3000 engagement.
    "virtual_office_registry":    ["compliance", "osint", "finance"],
    "sanctions_propagation":      ["compliance", "legal", "procurement"],
    "cited_artifact_verifier":    ["compliance", "legal", "osint"],
    "protective_reply_drafter":   ["compliance", "legal", "general"],
    # Tier 2 regional knowledge (2026-04-17 PM) — heat-map expansion
    "knowledge_north_africa":      ["market_intel", "procurement", "compliance"],
    "knowledge_south_se_asia":     ["market_intel", "procurement", "compliance"],
    "knowledge_central_africa":    ["market_intel", "procurement", "compliance"],
    "knowledge_balkans":           ["market_intel", "procurement", "compliance"],
    "regional_bright_lines":       ["compliance", "legal", "procurement"],
    # Heat-map expansion follow-up modules (2026-04-17 late PM)
    "gulf_oem_structure":          ["market_intel", "procurement", "relationships"],
    "vision_2030_tracker":         ["market_intel", "procurement", "compliance"],
    "baykar_export_pipeline":      ["market_intel", "competitor_intel", "procurement"],
    "political_risk_index":        ["geopolitics", "compliance", "market_intel"],
    "cross_regional_correlator":   ["geopolitics", "procurement", "relationships", "market_intel"],
    # Autonomy Surface (2026-04-17 late PM) — dashboard + briefing aggregator
    "autonomy_surface":            ["general", "compliance"],
    # F3 DD debug follow-up (2026-04-17 late PM)
    "domain_ownership_verifier":   ["compliance", "osint", "legal"],
    # F3 cascade remediation (2026-04-17 21:45-21:55)
    "run_quarantine":              ["compliance", "general"],
    "sanctions_claim_guard":       ["compliance", "legal"],
    # Pre-existing callers exposed by 2026-04-15 integrity audit — these
    # modules were signalling brain but never registered, so their
    # observations filed under "general" with no topical grounding.
    "aria_peers":           ["competitor_intel", "general"],
    "mistake_ledger":       ["general", "compliance"],
    "predictor":            ["general"],
    "self_assess":          ["general"],
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
    "entity_graph":         0.15,
    "person_resolver":      0.1,
    "competitors":          0.15,
    "gtm_strategy":         0.15,
    "risk_indices":         0.1,
    "global_export_control": 0.2,
    "dual_use_classifier":  0.15,
    "euc_library":          0.15,
    "audit_log":            0.05,
    "compliance_file":      0.20,
    "symbolic_reasoner":    0.1,
    "source_verifier":      0.1,
    "sanctions":            0.15,
    "conflict_tracker":     0.1,
    "international_law":    0.15,
    # Node.js seenode modules (via POST /api/aria/brain/absorb)
    "registry_adapter":     0.15,
    "opportunity_detector": 0.15,
    "signal_generator":     0.15,
    "knowledge_ingestor":   0.2,   # /teach URL ingestion — high value
    # Core Self-Development Loop (Clauses 17/18/19)
    "verified_intel":       0.15,  # every verified fact = real provenance work
    "web_atlas":            0.05,  # per-ingest EMA updates — high frequency, low weight
    "source_validator":     0.10,  # each approval = meaningful curation
    "source_scout":         0.05,  # automated discovery
    "search_doctrine":      0.05,  # search-per-turn — high frequency
    "core_develop":         0.20,  # acting on gaps is high-value self-improvement
    "ecosystem_reassess":   0.05,  # read-only queue build
    "golden_autogen":       0.10,  # self-growing eval set — meaningful curation
    "adversarial_challenge": 0.30, # weekly stress test — highest-value mastery signal
    "narrative_monitor": 0.15,     # hourly narrative scan — moderate weight per detection
    "chain_correlator":     0.25,  # was 0.20 — bumped 2026-04-17 PM;
                                    # confirmed causal chain = high-value
    "procurement_calendar": 0.10,  # static calendar upkeep — low frequency
    "competitor_tracker":   0.15,  # competitor observations drive mastery
    "oem_contact_graph":    0.15,  # OEM landscape updates — moderate weight
    "knowledge_gulf":              0.05,  # static knowledge — low weight per query
    "knowledge_turkey_standalone": 0.05,
    "knowledge_west_africa":       0.05,
    "knowledge_latam_non_lusophone": 0.05,
    "equipment_specs":      0.10,
    "sipri_ingest":         0.15,  # each ingest lands real historical data
    "writer_orchestrator":  0.25,  # each produced document is high-value work
    # NAK / SERBAN / F3 learnings (2026-04-17)
    # Weights bumped 2026-04-17 PM after calibration review flagged
    # UNDERCONFIDENT -16pp. These modules produce high-confidence
    # structured signals (RDAP evidence, sanctions list hits, cited
    # document verification, compliance draft produced) — every
    # successful fire is real work and should give mastery credit
    # proportional to that.
    "virtual_office_registry":    0.15,  # was 0.10
    "sanctions_propagation":      0.25,  # was 0.15
    "cited_artifact_verifier":    0.15,  # was 0.10
    "protective_reply_drafter":   0.20,  # was 0.15
    # Tier 2 regional knowledge (2026-04-17 PM)
    "knowledge_north_africa":      0.05,
    "knowledge_south_se_asia":     0.05,
    "knowledge_central_africa":    0.05,
    "knowledge_balkans":           0.05,
    "regional_bright_lines":       0.25,  # was 0.15 — every triggered rule
                                           # is a compliance-gate signal the
                                           # brain should weight heavily
    # Heat-map expansion follow-up (2026-04-17 late PM)
    "gulf_oem_structure":          0.10,
    "vision_2030_tracker":         0.10,
    "baykar_export_pipeline":      0.15,
    "political_risk_index":        0.15,
    "cross_regional_correlator":   0.20,
    "autonomy_surface":            0.05,  # aggregator — read-only, low weight
    # F3 DD follow-up (2026-04-17 late PM)
    "domain_ownership_verifier":   0.20,  # was 0.15 — RDAP is hard external
                                           # evidence, worth solid credit
    # F3 cascade remediation (2026-04-17 21:45-21:55)
    "run_quarantine":              0.15,
    "sanctions_claim_guard":       0.25,  # live primary check = high-value signal
    # Pre-existing callers (from integrity audit)
    "aria_peers":           0.10,  # competitor-landscape updates
    "mistake_ledger":       0.05,  # meta — records its own activity
    "predictor":            0.05,  # meta — forecast-hit-rate signals
    "self_assess":          0.10,  # daily briefing composer
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
                detail=gap_detail or f"{module} reported gap: {gap_type}",
                source=f"brain_hook:{module}",
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

    # ── 5. Record signal for stats/health tracking ──
    # Success = at least mastery OR knowledge stored. Gap errors are non-fatal.
    _core_ok = result["mastery_ok"] or result["knowledge_ok"]
    await _record_signal(module, success=_core_ok)

    return result


async def absorb_silent(**kwargs) -> None:
    """Fire-and-forget wrapper — logs errors but never raises."""
    try:
        await absorb(**kwargs)
    except Exception as e:
        logger.debug("brain_hook.absorb_silent failed entirely: %s", e)


# =============================================================================
# SIGNAL TRACKING + HEALTH MONITORING
# =============================================================================

_STATS_KEY = "crucix:aria:brain_hook:stats"
_ALERT_STALE_HOURS = 24  # alert if module hasn't sent a signal in 24h


async def _record_signal(module: str, success: bool) -> None:
    """Record a signal in Redis for per-module tracking."""
    try:
        from . import redis_store as rs
        stats = await rs.get_json(_STATS_KEY) or {}
        now = time.time()

        if module not in stats:
            stats[module] = {
                "total": 0, "success": 0, "fail": 0,
                "last_signal_at": 0, "first_signal_at": now,
            }
        m = stats[module]
        m["total"] += 1
        if success:
            m["success"] += 1
        else:
            m["fail"] += 1
        m["last_signal_at"] = now

        # Global counters
        stats.setdefault("_global", {"total": 0, "started_at": now})
        stats["_global"]["total"] += 1

        await rs.set_json(_STATS_KEY, stats, ex=30 * 86400)
    except Exception:
        pass  # stats recording must never break absorb


async def get_stats() -> dict:
    """Return brain hook stats — per-module signal counts + health."""
    try:
        from . import redis_store as rs
        stats = await rs.get_json(_STATS_KEY) or {}
    except Exception:
        stats = {}

    now = time.time()
    modules = {}
    stale = []
    healthy = []

    for key, val in stats.items():
        if key.startswith("_"):
            continue
        if not isinstance(val, dict):
            continue
        last = val.get("last_signal_at", 0)
        hours_ago = (now - last) / 3600 if last else None
        status = "active"
        if hours_ago is None:
            status = "never"
        elif hours_ago > _ALERT_STALE_HOURS:
            status = "stale"
            stale.append(key)
        else:
            healthy.append(key)

        modules[key] = {
            "total": val.get("total", 0),
            "success": val.get("success", 0),
            "fail": val.get("fail", 0),
            "success_rate": round(val["success"] / val["total"], 2) if val.get("total") else 0,
            "last_signal_ago_h": round(hours_ago, 1) if hours_ago is not None else None,
            "status": status,
        }

    # Identify modules that are registered but have never sent a signal
    all_known = set(_MODULE_TOPICS.keys())
    never_seen = all_known - set(modules.keys())

    g = stats.get("_global", {})
    return {
        "total_signals": g.get("total", 0),
        "tracking_since": g.get("started_at"),
        "modules": modules,
        "healthy_count": len(healthy),
        "stale_count": len(stale),
        "stale_modules": stale,
        "never_seen": sorted(never_seen),
        "health": "degraded" if stale else ("cold" if not healthy else "healthy"),
    }


async def get_stale_alerts() -> list[dict]:
    """Return alert dicts for modules that haven't sent a signal in 24h."""
    stats = await get_stats()
    alerts = []
    for mod in stats.get("stale_modules", []):
        m = stats["modules"].get(mod, {})
        alerts.append({
            "module": mod,
            "severity": "warning",
            "title": f"Brain signal stale: {mod}",
            "detail": f"{mod} last sent a signal {m.get('last_signal_ago_h', '?')}h ago (threshold: {_ALERT_STALE_HOURS}h)",
            "last_signal_ago_h": m.get("last_signal_ago_h"),
        })
    for mod in stats.get("never_seen", []):
        alerts.append({
            "module": mod,
            "severity": "info",
            "title": f"Brain signal never seen: {mod}",
            "detail": f"{mod} is registered but has never sent a signal to brain_hook",
        })
    return alerts
