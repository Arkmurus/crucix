"""R-F660 — per-topic completion criteria measurement.

Design-analysis 2026-05-17:
    "I know enough about X when 5 verified facts cite ≥3 tier-1 sources
     and no critical sub-question is OPEN."

Phase A slice 2 of the learning-framework buildout. Pure measurement,
read-only, zero LLM. The Phase B controller (R-F662) consumes this to
decide when to stop a learning session.

These tests verify:
  - pure helpers (topic_matches, fact_is_verified, count_tier1_sources)
  - end-to-end topic_completion() against mocked storage
  - the route handler shape + clamping
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from aria_service.intel import topic_completion as tc


# ── Pure helpers ──────────────────────────────────────────────────────────

def test_rf660_topic_matches_tagged():
    fact = {"topics": ["sanctions", "russia"], "claim": "", "entity_name": ""}
    assert tc.topic_matches("sanctions", fact) is True
    assert tc.topic_matches("SANCTIONS", fact) is True  # case-insensitive
    assert tc.topic_matches("aviation", fact) is False


def test_rf660_topic_matches_substring():
    fact = {
        "claim": "Sergey Chemezov is the CEO of Rostec.",
        "entity_name": "Rostec",
        "topics": [],
    }
    assert tc.topic_matches("rostec", fact) is True
    assert tc.topic_matches("chemezov", fact) is True
    assert tc.topic_matches("modirum", fact) is False


def test_rf660_topic_matches_empty_inputs():
    assert tc.topic_matches("", {"claim": "x"}) is False
    assert tc.topic_matches("rostec", {}) is False
    assert tc.topic_matches("   ", {"claim": "rostec"}) is False


def test_rf660_fact_is_verified_status_filter():
    assert tc.fact_is_verified({"verification_status": "VERIFIED"}) is True
    assert tc.fact_is_verified({"verification_status": "verified"}) is True  # case-insensitive
    assert tc.fact_is_verified({"verification_status": "UNVERIFIED"}) is False
    assert tc.fact_is_verified({"verification_status": "PENDING"}) is False
    assert tc.fact_is_verified({"verification_status": "CONTRADICTED"}) is False
    assert tc.fact_is_verified({}) is False
    assert tc.fact_is_verified(None) is False  # type: ignore[arg-type]


def test_rf660_count_tier1_sources_distinct_domains():
    facts = [
        {
            "sources": [
                {"tier": "1a", "domain": "ofac.treasury.gov"},
                {"tier": "1b", "domain": "reuters.com"},
                {"tier": "2",  "domain": "ft.com"},  # not tier-1
            ],
        },
        {
            "sources": [
                {"tier": "1a", "domain": "ofac.treasury.gov"},  # duplicate domain
                {"tier": "1b", "domain": "bbc.com"},
            ],
        },
    ]
    # Distinct tier-1 domains: ofac.treasury.gov, reuters.com, bbc.com = 3
    assert tc.count_tier1_sources(facts) == 3


def test_rf660_count_tier1_sources_url_fallback():
    """If the writer didn't pre-extract `domain`, the helper must fall back
    to urlparse on the `url` field."""
    facts = [{"sources": [
        {"tier": "1a", "url": "https://www.gov.uk/some/path"},
        {"tier": "1a", "url": "https://www.gov.uk/other/path"},  # same domain after www. strip
        {"tier": "1b", "url": "https://reuters.com/article"},
    ]}]
    assert tc.count_tier1_sources(facts) == 2  # gov.uk + reuters.com


def test_rf660_count_tier1_ignores_tier2_through_tier5():
    facts = [{"sources": [
        {"tier": "2", "domain": "ft.com"},
        {"tier": "3", "domain": "defensenews.com"},
        {"tier": "4", "domain": "localpaper.example"},
        {"tier": "5", "domain": "blog.example"},
    ]}]
    assert tc.count_tier1_sources(facts) == 0


# ── topic_completion end-to-end ───────────────────────────────────────────

def _build_fact(topic: str, verified: bool, tier1_domains: list[str]) -> dict:
    return {
        "claim": f"Important fact about {topic}",
        "entity_name": topic,
        "verification_status": "VERIFIED" if verified else "PENDING",
        "sources": [{"tier": "1a", "domain": d} for d in tier1_domains],
    }


def test_rf660_completion_all_gates_pass():
    """5 verified facts citing 3 distinct tier-1 sources, 0 open Qs."""
    facts = [
        _build_fact("rostec", True, ["ofac.treasury.gov", "reuters.com", "bbc.com"]),
        _build_fact("rostec", True, ["ft.com"]),  # tier-1 means tier-1 only — but ft.com is tier-2 normally; we're forcing 1a in _build_fact
        _build_fact("rostec", True, ["bbc.com"]),
        _build_fact("rostec", True, ["ofac.treasury.gov"]),
        _build_fact("rostec", True, ["reuters.com"]),
    ]

    async def runner():
        with patch.object(tc.rs, "get_json", return_value=facts), \
             patch.object(tc, "count_open_critical_for_topic", return_value=0):
            return await tc.topic_completion("rostec")

    result = asyncio.run(runner())
    assert result["complete"] is True
    assert result["blockers"] == []
    assert result["criteria"]["verified_facts"] == 5
    # 4 distinct domains: ofac.treasury.gov, reuters.com, bbc.com, ft.com
    # (some facts cite the same domain, distinct-count collapses them)
    assert result["criteria"]["tier1_sources"] == 4
    assert result["criteria"]["open_critical_questions"] == 0


def test_rf660_completion_fails_on_insufficient_facts():
    facts = [
        _build_fact("rostec", True, ["ofac.treasury.gov", "reuters.com", "bbc.com"]),
        _build_fact("rostec", True, ["ft.com"]),
    ]  # only 2 verified, need 5

    async def runner():
        with patch.object(tc.rs, "get_json", return_value=facts), \
             patch.object(tc, "count_open_critical_for_topic", return_value=0):
            return await tc.topic_completion("rostec")

    result = asyncio.run(runner())
    assert result["complete"] is False
    assert any("verified_facts" in b for b in result["blockers"])


def test_rf660_completion_fails_on_insufficient_tier1_breadth():
    """5 verified facts but all citing the SAME tier-1 source — fails the
    breadth criterion (need ≥3 distinct tier-1 domains)."""
    facts = [
        _build_fact("rostec", True, ["ofac.treasury.gov"]) for _ in range(5)
    ]

    async def runner():
        with patch.object(tc.rs, "get_json", return_value=facts), \
             patch.object(tc, "count_open_critical_for_topic", return_value=0):
            return await tc.topic_completion("rostec")

    result = asyncio.run(runner())
    assert result["complete"] is False
    assert result["criteria"]["tier1_sources"] == 1  # only ofac.treasury.gov
    assert any("tier1_sources" in b for b in result["blockers"])


def test_rf660_completion_fails_on_open_questions():
    """5 verified facts, 3+ tier-1 sources, but a critical open Q remains."""
    facts = [
        _build_fact("rostec", True, ["ofac.treasury.gov", "reuters.com", "bbc.com"]),
        _build_fact("rostec", True, ["ft.com"]),
        _build_fact("rostec", True, ["bbc.com"]),
        _build_fact("rostec", True, ["ofac.treasury.gov"]),
        _build_fact("rostec", True, ["reuters.com"]),
    ]

    async def runner():
        with patch.object(tc.rs, "get_json", return_value=facts), \
             patch.object(tc, "count_open_critical_for_topic", return_value=2):
            return await tc.topic_completion("rostec")

    result = asyncio.run(runner())
    assert result["complete"] is False
    assert result["criteria"]["open_critical_questions"] == 2
    assert any("open_critical_questions" in b for b in result["blockers"])


def test_rf660_completion_unverified_facts_dont_count():
    """Facts with verification_status != VERIFIED must NOT count toward
    the verified_facts threshold even if they match the topic."""
    facts = [
        _build_fact("rostec", verified=False, tier1_domains=["ofac.treasury.gov"])
        for _ in range(10)
    ]

    async def runner():
        with patch.object(tc.rs, "get_json", return_value=facts), \
             patch.object(tc, "count_open_critical_for_topic", return_value=0):
            return await tc.topic_completion("rostec")

    result = asyncio.run(runner())
    assert result["criteria"]["verified_facts"] == 0
    assert result["complete"] is False


def test_rf660_completion_empty_topic():
    async def runner():
        return await tc.topic_completion("")
    result = asyncio.run(runner())
    assert result["complete"] is False
    assert "topic_empty" in result["blockers"]


def test_rf660_completion_thresholds_overridable():
    """Operator-supplied thresholds must override defaults."""
    facts = [
        _build_fact("rostec", True, ["ofac.treasury.gov"]),
        _build_fact("rostec", True, ["reuters.com"]),
    ]

    async def runner():
        with patch.object(tc.rs, "get_json", return_value=facts), \
             patch.object(tc, "count_open_critical_for_topic", return_value=0):
            return await tc.topic_completion(
                "rostec",
                verified_facts_min=2,
                tier1_sources_min=2,
                open_questions_max=0,
            )

    result = asyncio.run(runner())
    assert result["complete"] is True


# ── Route handler ─────────────────────────────────────────────────────────

def test_rf660_route_returns_shape():
    """The handler must call topic_completion and return its dict."""
    from aria_service.routes import aria as routes_mod

    async def _fake_completion(topic, **kw):
        return {
            "topic": topic,
            "complete": True,
            "criteria": {
                "verified_facts": 5,
                "verified_facts_min": 5,
                "tier1_sources": 3,
                "tier1_sources_min": 3,
                "open_critical_questions": 0,
                "open_questions_max": 0,
            },
            "blockers": [],
        }

    async def runner():
        with patch.object(tc, "topic_completion", _fake_completion):
            return await routes_mod.mastery_topic_completion_ep(topic="rostec")

    result = asyncio.run(runner())
    assert result["topic"] == "rostec"
    assert result["complete"] is True
    assert result["criteria"]["verified_facts_min"] == 5


def test_rf660_route_rejects_empty_topic():
    from aria_service.routes import aria as routes_mod
    from fastapi import HTTPException

    async def runner():
        await routes_mod.mastery_topic_completion_ep(topic="")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(runner())
    assert excinfo.value.status_code == 400


def test_rf660_route_clamps_negative_thresholds():
    """Negative threshold values must be clamped to 0, not passed through."""
    from aria_service.routes import aria as routes_mod
    captured = {}

    async def _spy(topic, *, verified_facts_min, tier1_sources_min, open_questions_max):
        captured["verified_facts_min"] = verified_facts_min
        captured["tier1_sources_min"] = tier1_sources_min
        captured["open_questions_max"] = open_questions_max
        return {"topic": topic, "complete": False, "criteria": {}, "blockers": []}

    async def runner():
        with patch.object(tc, "topic_completion", _spy):
            await routes_mod.mastery_topic_completion_ep(
                topic="rostec",
                verified_facts_min=-5,
                tier1_sources_min=-1,
                open_questions_max=-99,
            )

    asyncio.run(runner())
    assert captured["verified_facts_min"] == 0
    assert captured["tier1_sources_min"] == 0
    assert captured["open_questions_max"] == 0
