"""
Strategic Evolution Agent — ARIA's self-competitive intelligence (R-F1532).

Tracks ARIA's own capabilities against other coding AI agents (Claude Code,
GitHub Copilot, Cursor, Cline) and identifies strategic advantages to build.

This is a THIN layer over existing infrastructure — it reads from:
  - eval_runner.py       → regression test pass rates
  - coverage_heatmap.py  → domain × jurisdiction coverage
  - student.py           → per-topic mastery scores
  - self_assess.py       → overall self-assessment
  - coding_rag_indexer   → past fix/failure patterns (R-F1531)

It does NOT duplicate any of those systems. It aggregates their signals
into a strategic view and feeds strategic gaps into the existing
gap_detector → self_coder → self_improve pipeline.

Architecture
════════════
  1. assess() — snapshot of ARIA's current capabilities vs coding agents
  2. identify_strategic_gaps() — where ARIA lags and should invest
  3. record_strategic_gain() — log when a capability improves
  4. generate_strategy_briefing() — markdown for the daily briefing
  5. start_strategic_loop() — background task that runs the cycle

Wiring
──────
  - Called from main.py lifespan as a background task
  - Outputs feed into gap_detector via brain_hook
  - Strategic gains recorded via brain_hook.absorb()
  - Strategy briefing delivered via proactive.py alert queue
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from . import redis_store as rs
from .engine_wiring import wire_success, wire_failure

logger = logging.getLogger("aria.strategic_evolution")

# R-F1532: wire module health to the brain on import
try:
    wire_success(
        module="strategic_evolution",
        summary="Strategic Evolution Agent active — self-competitive intelligence ready",
        source_id="strategic_evolution:R-F1532",
    )
except Exception:
    pass

# ── Redis keys ────────────────────────────────────────────────────────────────

_STRATEGIC_ASSESSMENT_KEY = "crucix:aria:strategic:latest_assessment"
_STRATEGIC_GAINS_KEY = "crucix:aria:strategic:gains"
_STRATEGIC_GAPS_KEY = "crucix:aria:strategic:gaps"
_STRATEGIC_BRIEFING_KEY = "crucix:aria:strategic:latest_briefing"
_MAX_GAINS = 500
_SCAN_INTERVAL_S = int(os.getenv("ARIA_STRATEGIC_SCAN_INTERVAL", "43200"))  # 12h

# ── Known coding agent competitors ────────────────────────────────────────────

CODING_AGENTS: list[dict] = [
    {
        "name": "Claude Code",
        "provider": "Anthropic",
        "strengths": ["code understanding", "UI/UX", "response speed"],
        "weaknesses": ["no persistent memory", "no self-evolution", "single provider"],
        "aria_advantages": ["rag_memory", "self_evolution", "multi_provider"],
    },
    {
        "name": "GitHub Copilot",
        "provider": "OpenAI/Microsoft",
        "strengths": ["IDE integration", "completion speed", "training scale"],
        "weaknesses": ["no autonomy", "no command execution", "no memory"],
        "aria_advantages": ["autonomy", "command_execution", "rag_memory"],
    },
    {
        "name": "Cursor",
        "provider": "Anysphere",
        "strengths": ["codebase indexing", "multi-file editing", "agent mode"],
        "weaknesses": ["limited autonomy", "no self-improvement", "no persistent memory"],
        "aria_advantages": ["self_evolution", "rag_memory", "full_autonomy"],
    },
    {
        "name": "Cline",
        "provider": "Open-source",
        "strengths": ["open source", "multi-provider", "terminal access"],
        "weaknesses": ["no self-evolution", "no persistent memory", "no competitive intel"],
        "aria_advantages": ["self_evolution", "rag_memory", "competitive_intel"],
    },
]

# ── ARIA's capability dimensions (what we measure) ────────────────────────────

CAPABILITY_DIMENSIONS: list[str] = [
    "rag_memory",           # persistent cross-session memory
    "self_evolution",       # ability to modify own code
    "autonomy",             # operate without human supervision
    "multi_provider",       # support multiple LLM providers
    "command_execution",    # run shell commands, tests, deploys
    "competitive_intel",    # track competitors and market
    "self_healing",         # detect and recover from failures
    "proactive_detection",  # find issues before they're reported
    "offline_capability",   # operate without internet
    "cost_efficiency",      # cost per task
    "code_understanding",   # AST analysis, dataflow, type inference
    "test_generation",      # automatic test writing
]


@dataclass
class StrategicGain:
    """A recorded improvement in a strategic capability."""
    dimension: str
    previous_score: float
    new_score: float
    delta: float
    source: str  # what caused the improvement (eval run, code change, etc.)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class StrategicGap:
    """A gap where ARIA lags behind a competitor."""
    dimension: str
    aria_score: float
    best_competitor_score: float
    gap: float
    competitor: str
    priority: int  # 1-10
    recommendation: str


# ── Core assessment ───────────────────────────────────────────────────────────


async def assess() -> dict:
    """Produce a snapshot of ARIA's current capabilities.

    Reads from existing infrastructure (eval_runner, coverage_heatmap,
    student, self_assess) and produces a structured assessment.

    Returns dict with:
      - capabilities: per-dimension scores (0-10)
      - overall_score: weighted average
      - competitor_comparison: how ARIA stacks up against each agent
      - strategic_gaps: where to invest
      - recent_gains: improvements since last assessment
    """
    capabilities = await _measure_capabilities()
    overall = _compute_overall(capabilities)
    comparison = _compare_to_competitors(capabilities)
    gaps = _identify_gaps(capabilities, comparison)
    gains = await _load_recent_gains()

    assessment = {
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "capabilities": capabilities,
        "overall_score": round(overall, 2),
        "competitor_comparison": comparison,
        "strategic_gaps": [g.__dict__ for g in gaps],
        "recent_gains": gains[-10:] if gains else [],
        "total_gains_recorded": len(gains),
    }

    # Persist for dashboard
    try:
        await rs.set_json(_STRATEGIC_ASSESSMENT_KEY, assessment)
    except Exception as e:
        logger.debug("[strategic] Failed to persist assessment: %s", e)

    return assessment


async def _measure_capabilities() -> dict[str, float]:
    """Measure ARIA's current capabilities from existing systems.

    Each dimension is scored 0-10 based on real data from the codebase.
    Returns a dict of dimension → score.
    """
    scores: dict[str, float] = {}

    # ── rag_memory: from coding_rag_indexer stats ─────────────────────────
    try:
        from .coding_rag_indexer import get_stats as _cri_stats
        stats = _cri_stats()
        if stats.get("ready"):
            # Score based on total indexed items (0-10 scale)
            total = (
                stats.get("total_fixes", 0)
                + stats.get("total_failures", 0)
                + stats.get("total_codebase_chunks", 0)
            )
            scores["rag_memory"] = min(10.0, max(1.0, total / 20))
        else:
            scores["rag_memory"] = 1.0
    except Exception:
        scores["rag_memory"] = 0.0

    # ── self_evolution: from self_improve stats ───────────────────────────
    try:
        from .self_improve import _SI_DEPLOYED, _SI_STAGED
        total_improvements = _SI_DEPLOYED + _SI_STAGED
        scores["self_evolution"] = min(10.0, max(1.0, total_improvements / 5))
    except Exception:
        scores["self_evolution"] = 3.0  # conservative baseline

    # ── autonomy: from eval pass rate ─────────────────────────────────────
    # R-F2589: eval_runner exposes no `latest()` accessor (type drift — the
    # symbol never existed; the old import silently fell through to this
    # fallback every call). No live "last eval summary" getter exists yet, so
    # use the neutral midpoint until one is wired (this engine is dormant).
    scores["autonomy"] = 5.0

    # ── multi_provider: from tier_router config ───────────────────────────
    # R-F2589: tier_router has no AVAILABLE_PROVIDERS constant (drift — it never
    # existed; live availability is a per-call param). Count the DISTINCT
    # providers the tier map can route to as a static capability proxy.
    try:
        from ..llm.tier_router import _TIER_TO_PROVIDER
        provider_count = len(set(_TIER_TO_PROVIDER.values()))
        scores["multi_provider"] = min(10.0, provider_count * 2.5)
    except Exception:
        scores["multi_provider"] = 3.0

    # ── command_execution: always available (core feature) ────────────────
    scores["command_execution"] = 9.0

    # ── competitive_intel: from this module's own data ────────────────────
    try:
        gains = await _load_recent_gains()
        scores["competitive_intel"] = min(10.0, max(1.0, len(gains) / 5))
    except Exception:
        scores["competitive_intel"] = 2.0

    # ── self_healing: from self_healing health status ─────────────────────
    try:
        from .self_healing import get_status as _get_health  # R-F2589: real name
        health = _get_health()
        healthy_count = sum(
            1 for s in health.values() if isinstance(s, str) and s == "healthy"
        )
        total = max(1, len(health))
        scores["self_healing"] = (healthy_count / total) * 10.0
    except Exception:
        scores["self_healing"] = 6.0

    # ── proactive_detection: from proactive module ────────────────────────
    try:
        from .proactive import ALERT_QUEUE_KEY
        alerts = await rs.lrange(ALERT_QUEUE_KEY, 0, -1)
        scores["proactive_detection"] = min(10.0, max(1.0, len(alerts) / 3))
    except Exception:
        scores["proactive_detection"] = 4.0

    # ── offline_capability: from self_sufficient ──────────────────────────
    try:
        from .self_sufficient import SymbolicReasoner
        _sr = SymbolicReasoner()
        scores["offline_capability"] = 7.0  # symbolic reasoner exists
    except Exception:
        scores["offline_capability"] = 3.0

    # ── cost_efficiency: from cost_tracker ────────────────────────────────
    try:
        from .cost_tracker import get_month_breakdown  # R-F2589: real name (async, dict)
        _mb = await get_month_breakdown()
        monthly = float(_mb.get("total_cost_usd", 0.0)) if isinstance(_mb, dict) else 0.0
        # $300 cap; lower spend = higher score
        if monthly > 0:
            scores["cost_efficiency"] = max(1.0, 10.0 - (monthly / 30))
        else:
            scores["cost_efficiency"] = 8.0
    except Exception:
        scores["cost_efficiency"] = 7.0

    # ── code_understanding: from code_understanding module ────────────────
    try:
        from .code_understanding import CodebaseMap
        scores["code_understanding"] = 8.0  # CodebaseMap exists and works
    except Exception:
        scores["code_understanding"] = 4.0

    # ── test_generation: from self_coder test writing ─────────────────────
    try:
        from ..autonomous.self_coder import ARIACoder
        scores["test_generation"] = 7.0  # self_coder writes tests
    except Exception:
        scores["test_generation"] = 3.0

    return scores


def _compute_overall(capabilities: dict[str, float]) -> float:
    """Weighted average of all capability scores."""
    if not capabilities:
        return 0.0
    # Higher weight on autonomy, self_evolution, rag_memory
    weights = {
        "rag_memory": 2.0,
        "self_evolution": 2.0,
        "autonomy": 2.0,
        "multi_provider": 1.0,
        "command_execution": 1.0,
        "competitive_intel": 1.5,
        "self_healing": 1.5,
        "proactive_detection": 1.5,
        "offline_capability": 1.0,
        "cost_efficiency": 1.0,
        "code_understanding": 1.5,
        "test_generation": 1.0,
    }
    total_weight = 0.0
    weighted_sum = 0.0
    for dim, score in capabilities.items():
        w = weights.get(dim, 1.0)
        weighted_sum += score * w
        total_weight += w
    return weighted_sum / total_weight if total_weight > 0 else 0.0


def _compare_to_competitors(
    capabilities: dict[str, float],
) -> list[dict]:
    """Compare ARIA's capabilities against each coding agent.

    Returns a list of {agent_name, aria_advantages, aria_disadvantages,
    overall_comparison} for each competitor.
    """
    comparison = []
    for agent in CODING_AGENTS:
        advantages = []
        disadvantages = []
        for adv in agent.get("aria_advantages", []):
            score = capabilities.get(adv, 0.0)
            if score >= 6.0:
                advantages.append({"dimension": adv, "score": score})
            else:
                disadvantages.append({"dimension": adv, "score": score})

        comparison.append({
            "agent_name": agent["name"],
            "provider": agent["provider"],
            "aria_advantages": advantages,
            "aria_disadvantages": disadvantages,
            "advantage_count": len(advantages),
            "disadvantage_count": len(disadvantages),
        })

    return comparison


def _identify_gaps(
    capabilities: dict[str, float],
    comparison: list[dict],
) -> list[StrategicGap]:
    """Identify strategic gaps where ARIA should invest.

    A gap exists when:
    1. A competitor has an advantage ARIA doesn't (from comparison)
    2. A capability score is below 5.0 (weak area)
    """
    gaps: list[StrategicGap] = []

    # Gaps from competitor comparison
    for comp in comparison:
        for disadv in comp.get("aria_disadvantages", []):
            dim = disadv["dimension"]
            score = disadv["score"]
            gaps.append(StrategicGap(
                dimension=dim,
                aria_score=score,
                best_competitor_score=8.0,  # assumed competitor strength
                gap=8.0 - score,
                competitor=comp["agent_name"],
                priority=_priority_for_dimension(dim, score),
                recommendation=_recommendation_for_dimension(dim),
            ))

    # Gaps from low scores (even if no competitor explicitly has the advantage)
    for dim, score in capabilities.items():
        if score < 5.0 and not any(g.dimension == dim for g in gaps):
            gaps.append(StrategicGap(
                dimension=dim,
                aria_score=score,
                best_competitor_score=7.0,
                gap=7.0 - score,
                competitor="industry baseline",
                priority=_priority_for_dimension(dim, score),
                recommendation=_recommendation_for_dimension(dim),
            ))

    # Sort by priority descending
    gaps.sort(key=lambda g: g.priority, reverse=True)
    return gaps


def _priority_for_dimension(dimension: str, score: float) -> int:
    """Determine priority (1-10) for improving a dimension."""
    # Core differentiators get highest priority
    high_priority = {"rag_memory", "self_evolution", "autonomy", "competitive_intel"}
    medium_priority = {"self_healing", "proactive_detection", "multi_provider"}

    if dimension in high_priority:
        base = 9
    elif dimension in medium_priority:
        base = 7
    else:
        base = 5

    # Lower current score = higher priority
    urgency = max(0, min(3, int((5.0 - score) / 2)))
    return min(10, base + urgency)


def _recommendation_for_dimension(dimension: str) -> str:
    """Generate a recommendation for improving a dimension."""
    recs = {
        "rag_memory": "Index more codebase structure and fix/failure records to improve retrieval coverage",
        "self_evolution": "Increase self-coder cycles and expand the range of auto-fixable gap types",
        "autonomy": "Improve eval pass rate through better prompt engineering and test coverage",
        "multi_provider": "Add more LLM providers to the tier router (Groq, local Ollama)",
        "competitive_intel": "Run strategic assessment more frequently and track gains over time",
        "self_healing": "Add more health checks and auto-recovery paths for edge cases",
        "proactive_detection": "Increase scan frequency and add more anomaly detection patterns",
        "offline_capability": "Complete the symbolic reasoner and local LLM integration",
        "cost_efficiency": "Optimise token usage with better RAG retrieval and caching",
        "code_understanding": "Expand AST analysis to cover more patterns and cross-file references",
        "test_generation": "Improve test quality through better LLM prompts and coverage analysis",
    }
    return recs.get(dimension, "Investigate and improve this capability")


# ── Strategic gain recording ──────────────────────────────────────────────────


async def record_strategic_gain(
    dimension: str,
    previous_score: float,
    new_score: float,
    source: str,
) -> StrategicGain:
    """Record an improvement in a strategic capability.

    Called from:
      - eval_runner after a successful eval run
      - self_improve after a deploy that improves a capability
      - self_coder after a fix that closes a strategic gap

    The gain is persisted to Redis and fed to brain_hook.
    """
    gain = StrategicGain(
        dimension=dimension,
        previous_score=round(previous_score, 2),
        new_score=round(new_score, 2),
        delta=round(new_score - previous_score, 2),
        source=source,
    )

    # Persist to Redis
    try:
        gains = await rs.get_json(_STRATEGIC_GAINS_KEY) or []
        gains.append(gain.__dict__)
        gains = gains[-_MAX_GAINS:]
        await rs.set_json(_STRATEGIC_GAINS_KEY, gains)
    except Exception as e:
        logger.debug("[strategic] Failed to persist gain: %s", e)

    # Feed to brain_hook so it becomes knowledge
    try:
        from .brain_hook import absorb
        await absorb(
            module="strategic_evolution",
            summary=(
                f"Strategic gain: {dimension} improved from "
                f"{gain.previous_score} to {gain.new_score} "
                f"(+{gain.delta}) via {source}"
            ),
            confidence="CONFIRMED",
            source_id=f"strategic_evolution:gain:{dimension}",
        )
    except Exception as e:
        logger.debug("[strategic] brain_hook.absorb failed: %s", e)

    # Index in coding_rag_indexer for future retrieval
    try:
        from .coding_rag_indexer import FixRecord, index_fix as _cri_index
        import asyncio as _aio
        _fix = FixRecord(
            r_number=f"STRAT-{dimension[:16]}",
            title=f"Strategic gain: {dimension}",
            gap_type="strategic_improvement",
            module="strategic_evolution",
            problem_description=f"Improved {dimension} from {gain.previous_score} to {gain.new_score}",
            approach=source,
            files_changed=[],
            tests_passed=0,
            timestamp=gain.timestamp,
            outcome="success",
        )
        _aio.create_task(_aio.to_thread(_cri_index, _fix))
    except Exception as e:
        logger.debug("[strategic] coding_rag index failed: %s", e)

    logger.info(
        "[Strategic] Gain: %s %.2f → %.2f (+%.2f) via %s",
        dimension, gain.previous_score, gain.new_score, gain.delta, source,
    )
    return gain


async def _load_recent_gains() -> list[dict]:
    """Load recent strategic gains from Redis."""
    try:
        return await rs.get_json(_STRATEGIC_GAINS_KEY) or []
    except Exception:
        return []


# ── Strategy briefing ─────────────────────────────────────────────────────────


async def generate_strategy_briefing() -> str:
    """Generate a markdown briefing of ARIA's strategic position.

    Designed for the daily team briefing (proactive.py alert queue).
    """
    assessment = await assess()
    caps = assessment.get("capabilities", {})
    gaps = assessment.get("strategic_gaps", [])
    comparison = assessment.get("competitor_comparison", [])

    lines = [
        "## ARIA Strategic Position",
        "",
        f"**Overall Score:** {assessment['overall_score']}/10",
        f"**Assessed:** {assessment['assessed_at'][:19]}",
        "",
        "### Capabilities",
        "",
    ]

    # Sort capabilities by score ascending (weakest first)
    sorted_caps = sorted(caps.items(), key=lambda x: x[1])
    for dim, score in sorted_caps:
        bar = "█" * int(score) + "░" * (10 - int(score))
        lines.append(f"  {bar}  {dim}: {score:.1f}/10")

    lines.extend(["", "### Strategic Gaps (Priority Order)", ""])
    for gap in gaps[:5]:
        # R-F1532: handle both dict and StrategicGap objects
        if isinstance(gap, dict):
            dim = gap.get("dimension", "unknown")
            score = gap.get("aria_score", 0.0)
            g = gap.get("gap", 0.0)
            comp = gap.get("competitor", "unknown")
            pri = gap.get("priority", 5)
            rec = gap.get("recommendation", "")
        else:
            dim = getattr(gap, 'dimension', 'unknown')
            score = getattr(gap, 'aria_score', 0.0)
            g = getattr(gap, 'gap', 0.0)
            comp = getattr(gap, 'competitor', 'unknown')
            pri = getattr(gap, 'priority', 5)
            rec = getattr(gap, 'recommendation', '')
        lines.append(
            f"  **{dim}** — score {score:.1f}/10, "
            f"gap {g:.1f} vs {comp} "
            f"(priority {pri}/10)"
        )
        lines.append(f"  → {rec}")
        lines.append("")

    lines.extend(["", "### Competitor Comparison", ""])
    for comp in comparison:
        adv_count = comp["advantage_count"]
        dis_count = comp["disadvantage_count"]
        status = "✅ Leading" if adv_count > dis_count else "⚠️ Trailing"
        lines.append(
            f"  {status} vs **{comp['agent_name']}** "
            f"({adv_count} advantages, {dis_count} disadvantages)"
        )

    briefing = "\n".join(lines)

    # Persist for dashboard
    try:
        await rs.set_json(_STRATEGIC_BRIEFING_KEY, {
            "briefing": briefing,
            "assessed_at": assessment["assessed_at"],
        })
    except Exception:
        pass

    return briefing


# ── Strategic loop ────────────────────────────────────────────────────────────


async def start_strategic_loop() -> None:
    """Background task that runs the strategic assessment cycle.

    Called from main.py lifespan. Runs every SCAN_INTERVAL_S (default 12h).
    On each cycle:
      1. Assess current capabilities
      2. Identify strategic gaps
      3. Feed high-priority gaps into the gap_detector
      4. Generate strategy briefing
      5. Push briefing to proactive alert queue
    """
    logger.info("[Strategic] Starting strategic evolution loop (interval=%ds)", _SCAN_INTERVAL_S)

    while True:
        try:
            # 1. Assess
            assessment = await assess()
            overall = assessment["overall_score"]
            gaps = assessment["strategic_gaps"]

            logger.info(
                "[Strategic] Assessment complete — overall=%.2f, gaps=%d",
                overall, len(gaps),
            )

            # 2. Feed high-priority gaps into gap_detector
            high_priority = [g for g in gaps if g.priority >= 8]
            for gap in high_priority[:3]:  # top 3 per cycle
                try:
                    from ..autonomous.gap_detector import Gap, GapSeverity, GapType
                    from .capability_gaps import record_gap

                    await record_gap(
                        gap_type=GapType.OPPORTUNITY,
                        detail=(
                            f"Strategic gap: {gap.dimension} at {gap.aria_score:.1f}/10 "
                            f"vs {gap.competitor} ({gap.gap:.1f} gap). "
                            f"Recommendation: {gap.recommendation}"
                        ),
                        source="strategic_evolution",
                    )
                    logger.info(
                        "[Strategic] Fed gap to capability_gaps: %s (priority %d)",
                        gap.dimension, gap.priority,
                    )
                except Exception as e:
                    logger.debug("[Strategic] Failed to feed gap: %s", e)

            # 3. Generate and push briefing
            briefing = await generate_strategy_briefing()

            try:
                from .proactive import ALERT_QUEUE_KEY
                await rs.lpush(ALERT_QUEUE_KEY, json.dumps({
                    "type": "strategy_briefing",
                    "content": briefing,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
                # Trim queue to prevent unbounded growth
                await rs.ltrim(ALERT_QUEUE_KEY, 0, 99)
            except Exception as e:
                logger.debug("[Strategic] Failed to push briefing: %s", e)

            # 4. Wire success to brain
            wire_success(
                module="strategic_evolution",
                summary=f"Strategic cycle complete — overall={overall:.2f}, gaps={len(gaps)}",
                source_id="strategic_evolution:cycle",
            )

        except Exception as e:
            logger.error("[Strategic] Cycle failed: %s", e, exc_info=True)
            wire_failure(
                module="strategic_evolution",
                detail=f"Strategic cycle failed: {e}",
                gap_type="engine_failure",
                source="strategic_evolution:cycle",
            )

        await asyncio.sleep(_SCAN_INTERVAL_S)


# ── Public API ────────────────────────────────────────────────────────────────


async def get_latest_assessment() -> dict | None:
    """Get the most recent strategic assessment from Redis."""
    try:
        return await rs.get_json(_STRATEGIC_ASSESSMENT_KEY)
    except Exception:
        return None


async def get_strategic_gains(
    dimension: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Get strategic gains, optionally filtered by dimension."""
    gains = await _load_recent_gains()
    if dimension:
        gains = [g for g in gains if g.get("dimension") == dimension]
    return gains[-limit:]


async def get_strategy_briefing() -> str | None:
    """Get the most recent strategy briefing."""
    try:
        data = await rs.get_json(_STRATEGIC_BRIEFING_KEY)
        if data:
            return data.get("briefing")
    except Exception:
        pass
    return None
