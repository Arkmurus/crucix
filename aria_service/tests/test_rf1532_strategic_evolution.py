"""R-F1532 — Capability tests for Strategic Evolution Agent.

Tests the full chain:
  1. assess() — produces a structured capability assessment
  2. _measure_capabilities() — reads from existing systems
  3. _compare_to_competitors() — compares ARIA to coding agents
  4. _identify_gaps() — finds strategic gaps
  5. record_strategic_gain() — records and persists gains
  6. generate_strategy_briefing() — produces markdown
  7. CODING_AGENTS — known competitor definitions
  8. CAPABILITY_DIMENSIONS — all dimensions are tracked
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


# ── Test: CODING_AGENTS ───────────────────────────────────────────────────────


def test_rf1532_coding_agents_defined():
    """All coding agents have the required fields."""
    from aria_service.intel.strategic_evolution import CODING_AGENTS

    assert len(CODING_AGENTS) >= 3, "Should track at least 3 coding agents"
    for agent in CODING_AGENTS:
        assert "name" in agent, f"Agent missing name: {agent}"
        assert "provider" in agent, f"{agent['name']} missing provider"
        assert "strengths" in agent, f"{agent['name']} missing strengths"
        assert "weaknesses" in agent, f"{agent['name']} missing weaknesses"
        assert "aria_advantages" in agent, f"{agent['name']} missing aria_advantages"
        assert len(agent["aria_advantages"]) > 0, \
            f"{agent['name']} should have at least 1 ARIA advantage"


def test_rf1532_capability_dimensions_defined():
    """All capability dimensions are defined."""
    from aria_service.intel.strategic_evolution import CAPABILITY_DIMENSIONS

    assert len(CAPABILITY_DIMENSIONS) >= 8, "Should track at least 8 dimensions"
    assert "rag_memory" in CAPABILITY_DIMENSIONS
    assert "self_evolution" in CAPABILITY_DIMENSIONS
    assert "autonomy" in CAPABILITY_DIMENSIONS
    assert "competitive_intel" in CAPABILITY_DIMENSIONS


# ── Test: _measure_capabilities ───────────────────────────────────────────────


def test_rf1532_measure_capabilities_structure():
    """_measure_capabilities returns a dict with all dimensions."""
    import asyncio
    from aria_service.intel.strategic_evolution import _measure_capabilities

    scores = asyncio.run(_measure_capabilities())

    assert isinstance(scores, dict), "Should return a dict"
    assert len(scores) >= 8, f"Should have at least 8 dimensions, got {len(scores)}"

    # All scores should be 0-10 floats
    for dim, score in scores.items():
        assert isinstance(score, (int, float)), f"{dim} score should be numeric"
        assert 0.0 <= score <= 10.0, f"{dim} score {score} out of range [0, 10]"

    # Core dimensions must be present
    for core_dim in ["rag_memory", "self_evolution", "autonomy", "multi_provider",
                      "command_execution", "competitive_intel", "self_healing",
                      "cost_efficiency"]:
        assert core_dim in scores, f"Missing core dimension: {core_dim}"


# ── Test: _compute_overall ────────────────────────────────────────────────────


def test_rf1532_compute_overall():
    """_compute_overall produces a weighted average."""
    from aria_service.intel.strategic_evolution import _compute_overall

    # All 10s should give 10
    all_high = {dim: 10.0 for dim in [
        "rag_memory", "self_evolution", "autonomy", "multi_provider",
        "command_execution", "competitive_intel", "self_healing",
        "proactive_detection", "offline_capability", "cost_efficiency",
        "code_understanding", "test_generation",
    ]}
    assert _compute_overall(all_high) == 10.0, "All 10s should give 10"

    # All 0s should give 0
    all_low = {dim: 0.0 for dim in all_high}
    assert _compute_overall(all_low) == 0.0, "All 0s should give 0"

    # Empty dict should give 0
    assert _compute_overall({}) == 0.0, "Empty dict should give 0"

    # Single dimension
    assert _compute_overall({"rag_memory": 5.0}) == 5.0, "Single dim should return its score"


# ── Test: _compare_to_competitors ─────────────────────────────────────────────


def test_rf1532_compare_to_competitors():
    """_compare_to_competitors returns structured comparison."""
    from aria_service.intel.strategic_evolution import _compare_to_competitors

    capabilities = {
        "rag_memory": 8.0,
        "self_evolution": 7.0,
        "autonomy": 6.0,
        "multi_provider": 5.0,
        "command_execution": 9.0,
        "competitive_intel": 4.0,
        "self_healing": 7.0,
        "proactive_detection": 6.0,
        "offline_capability": 3.0,
        "cost_efficiency": 8.0,
        "code_understanding": 7.0,
        "test_generation": 5.0,
    }

    comparison = _compare_to_competitors(capabilities)

    assert len(comparison) >= 3, "Should compare against at least 3 agents"
    for comp in comparison:
        assert "agent_name" in comp
        assert "aria_advantages" in comp
        assert "aria_disadvantages" in comp
        assert "advantage_count" in comp
        assert "disadvantage_count" in comp

    # Claude Code should have rag_memory as an advantage (score >= 6)
    claude = [c for c in comparison if c["agent_name"] == "Claude Code"]
    if claude:
        advantages = [a["dimension"] for a in claude[0]["aria_advantages"]]
        assert "rag_memory" in advantages, \
            "Claude Code comparison should list rag_memory as ARIA advantage"


# ── Test: _identify_gaps ──────────────────────────────────────────────────────


def test_rf1532_identify_gaps():
    """_identify_gaps finds gaps from low scores and competitor disadvantages."""
    from aria_service.intel.strategic_evolution import (
        _compare_to_competitors,
        _identify_gaps,
    )

    capabilities = {
        "rag_memory": 8.0,
        "self_evolution": 7.0,
        "autonomy": 6.0,
        "multi_provider": 5.0,
        "command_execution": 9.0,
        "competitive_intel": 2.0,  # Low score → gap
        "self_healing": 7.0,
        "proactive_detection": 6.0,
        "offline_capability": 3.0,  # Low score → gap
        "cost_efficiency": 8.0,
        "code_understanding": 7.0,
        "test_generation": 5.0,
    }

    comparison = _compare_to_competitors(capabilities)
    gaps = _identify_gaps(capabilities, comparison)

    assert len(gaps) >= 2, "Should find at least 2 gaps"

    # Gaps should be sorted by priority descending
    for i in range(len(gaps) - 1):
        assert gaps[i].priority >= gaps[i + 1].priority, \
            "Gaps should be sorted by priority descending"

    # Each gap should have all fields
    for gap in gaps:
        assert gap.dimension, "Gap should have a dimension"
        assert 0 <= gap.aria_score <= 10, "Gap aria_score out of range"
        assert gap.gap >= 0, "Gap should be non-negative"
        assert 1 <= gap.priority <= 10, "Gap priority out of range"
        assert gap.recommendation, "Gap should have a recommendation"

    # competitive_intel should be a gap (score 2.0)
    ci_gaps = [g for g in gaps if g.dimension == "competitive_intel"]
    assert len(ci_gaps) >= 1, "competitive_intel should be identified as a gap"


# ── Test: record_strategic_gain ───────────────────────────────────────────────


def test_rf1532_record_strategic_gain():
    """record_strategic_gain creates and persists a gain record."""
    import asyncio
    from aria_service.intel.strategic_evolution import (
        _load_recent_gains,
        record_strategic_gain,
    )

    # Record a gain
    gain = asyncio.run(record_strategic_gain(
        dimension="rag_memory",
        previous_score=5.0,
        new_score=7.5,
        source="R-F1531: Coding RAG Indexer",
    ))

    assert gain.dimension == "rag_memory"
    assert gain.previous_score == 5.0
    assert gain.new_score == 7.5
    assert gain.delta == 2.5
    assert gain.source == "R-F1531: Coding RAG Indexer"
    assert gain.timestamp, "Should have a timestamp"

    # Verify it was persisted
    gains = asyncio.run(_load_recent_gains())
    matching = [g for g in gains if g.get("dimension") == "rag_memory"]
    assert len(matching) >= 1, "Gain should be persisted to Redis"
    assert matching[-1]["new_score"] == 7.5


# ── Test: assess ──────────────────────────────────────────────────────────────


def test_rf1532_assess_structure():
    """assess() returns a complete assessment dict."""
    import asyncio
    from aria_service.intel.strategic_evolution import assess

    assessment = asyncio.run(assess())

    assert isinstance(assessment, dict), "Should return a dict"
    assert "assessed_at" in assessment, "Should have assessed_at"
    assert "capabilities" in assessment, "Should have capabilities"
    assert "overall_score" in assessment, "Should have overall_score"
    assert "competitor_comparison" in assessment, "Should have competitor_comparison"
    assert "strategic_gaps" in assessment, "Should have strategic_gaps"
    assert "recent_gains" in assessment, "Should have recent_gains"
    assert "total_gains_recorded" in assessment, "Should have total_gains_recorded"

    # overall_score should be 0-10
    assert 0 <= assessment["overall_score"] <= 10, \
        f"overall_score {assessment['overall_score']} out of range"

    # capabilities should have all dimensions
    caps = assessment["capabilities"]
    assert len(caps) >= 8, f"Should have at least 8 capabilities, got {len(caps)}"

    # competitor_comparison should have at least 3 entries
    comp = assessment["competitor_comparison"]
    assert len(comp) >= 3, f"Should compare against at least 3 agents, got {len(comp)}"

    # strategic_gaps should be sorted by priority
    gaps = assessment["strategic_gaps"]
    for i in range(len(gaps) - 1):
        assert gaps[i]["priority"] >= gaps[i + 1]["priority"], \
            "Gaps should be sorted by priority descending"


# ── Test: generate_strategy_briefing ──────────────────────────────────────────


def test_rf1532_generate_strategy_briefing():
    """generate_strategy_briefing produces markdown."""
    import asyncio
    from aria_service.intel.strategic_evolution import generate_strategy_briefing

    briefing = asyncio.run(generate_strategy_briefing())

    assert isinstance(briefing, str), "Should return a string"
    assert len(briefing) > 100, "Briefing should be substantial"
    assert "ARIA Strategic Position" in briefing, "Should have title"
    assert "Overall Score" in briefing, "Should show overall score"
    assert "Capabilities" in briefing, "Should have capabilities section"
    assert "Strategic Gaps" in briefing, "Should have gaps section"
    assert "Competitor Comparison" in briefing, "Should have comparison section"


# ── Test: _priority_for_dimension ─────────────────────────────────────────────


def test_rf1532_priority_for_dimension():
    """_priority_for_dimension returns correct priorities."""
    from aria_service.intel.strategic_evolution import _priority_for_dimension

    # Core differentiators get high priority
    assert _priority_for_dimension("rag_memory", 3.0) >= 9, \
        "rag_memory at 3.0 should be high priority"
    assert _priority_for_dimension("self_evolution", 3.0) >= 9, \
        "self_evolution at 3.0 should be high priority"
    assert _priority_for_dimension("autonomy", 3.0) >= 9, \
        "autonomy at 3.0 should be high priority"

    # Higher score = lower urgency
    rag_high = _priority_for_dimension("rag_memory", 8.0)
    rag_low = _priority_for_dimension("rag_memory", 3.0)
    assert rag_high <= rag_low, \
        "Higher score should have lower or equal priority"


# ── Test: _recommendation_for_dimension ───────────────────────────────────────


def test_rf1532_recommendation_for_dimension():
    """_recommendation_for_dimension returns a non-empty string for all dims."""
    from aria_service.intel.strategic_evolution import (
        CAPABILITY_DIMENSIONS,
        _recommendation_for_dimension,
    )

    for dim in CAPABILITY_DIMENSIONS:
        rec = _recommendation_for_dimension(dim)
        assert rec, f"Missing recommendation for dimension: {dim}"
        assert isinstance(rec, str), f"Recommendation for {dim} should be a string"
        assert len(rec) > 10, f"Recommendation for {dim} should be substantial"


# ── Test: edge cases ──────────────────────────────────────────────────────────


def test_rf1532_empty_capabilities():
    """_compute_overall handles empty/edge inputs."""
    from aria_service.intel.strategic_evolution import _compute_overall

    assert _compute_overall({}) == 0.0, "Empty dict"
    assert _compute_overall(None) == 0.0, "None input"
    assert _compute_overall({"unknown_dim": 5.0}) == 5.0, "Unknown dimension"


def test_rf1532_compare_no_competitors():
    """_compare_to_competitors handles empty capabilities."""
    from aria_service.intel.strategic_evolution import _compare_to_competitors

    comparison = _compare_to_competitors({})
    assert len(comparison) >= 3, "Should still return comparisons"
    for comp in comparison:
        assert len(comp["aria_advantages"]) == 0, \
            "Empty capabilities should yield no advantages"
