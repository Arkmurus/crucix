"""Tests for the "brain never dies" resilience floor.

Covers:
  1. Groq provider added to factory + fallback chain seed
  2. Autonomy surface now includes a `resilience` section
  3. Resilience verdict ladder: ROBUST / ADEQUATE / SINGLE_POINT / CRITICAL
  4. Operator prompt surfaces degraded verdicts but stays silent on ROBUST
"""
from __future__ import annotations

import asyncio

import pytest


# R-F782: autonomy_surface caches sub-task results for 60s. Reset before
# each test so monkeypatched/env-driven branches see fresh computation.
@pytest.fixture(autouse=True)
def _r_f782_reset_autonomy_surface_cache():
    from aria_service.intel import autonomy_surface
    autonomy_surface._SUBTASK_CACHE.clear()
    yield
    autonomy_surface._SUBTASK_CACHE.clear()


# ═══════════════════════════════════════════════════════════════════════
# 1. Groq provider
# ═══════════════════════════════════════════════════════════════════════

def test_factory_supports_groq():
    from aria_service.llm.factory import create_llm_provider
    p = create_llm_provider("groq", api_key="test-key", model="llama-3.3-70b-versatile")
    assert p is not None
    assert p.name == "groq"
    assert p.is_configured is True


def test_groq_uses_groq_base_url():
    from aria_service.llm.factory import create_llm_provider
    p = create_llm_provider("groq", api_key="test-key")
    # The OpenAICompat provider exposes _base_url; verify it's Groq's
    assert "groq.com" in (getattr(p, "_base_url", "") or "")


def test_fallback_chain_includes_groq():
    """Reading the fallback.py source confirms groq is in the config list.
    A full end-to-end test would need real API keys; we just confirm
    the config entry exists."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "llm" / "fallback.py"
    text = src.read_text(encoding="utf-8")
    assert '"groq"' in text
    assert 'GROQ_API_KEY' in text


# ═══════════════════════════════════════════════════════════════════════
# 2. Autonomy surface — resilience section
# ═══════════════════════════════════════════════════════════════════════

def test_get_surface_includes_resilience_section():
    from aria_service.intel import autonomy_surface

    async def run():
        return await autonomy_surface.get_surface()

    s = asyncio.run(run())
    assert "resilience" in s
    r = s["resilience"]
    for k in ("providers", "providers_active", "providers_configured",
              "providers_cooling", "local_brain_ready",
              "local_brain_invocations_24h", "memory",
              "resilience_count", "verdict"):
        assert k in r, f"resilience section missing {k!r}"


def test_memory_section_has_durability_fields():
    from aria_service.intel import autonomy_surface

    async def run():
        return await autonomy_surface.get_surface()

    s = asyncio.run(run())
    mem = s["resilience"]["memory"]
    for k in ("redis_reachable", "rag_chunks", "mem0_facts", "claim_ledger_entries"):
        assert k in mem


# ═══════════════════════════════════════════════════════════════════════
# 3. Verdict ladder
# ═══════════════════════════════════════════════════════════════════════

def test_verdict_ladder_values():
    """Verdict must be one of the four named values, never unknown
    string formats that would break the dashboard."""
    from aria_service.intel import autonomy_surface

    async def run():
        return await autonomy_surface.get_surface()

    s = asyncio.run(run())
    verdict = s["resilience"]["verdict"]
    assert verdict in ("ROBUST", "ADEQUATE", "SINGLE_POINT", "CRITICAL", "unknown")


def test_resilience_count_matches_components():
    """resilience_count must equal providers_active + (1 if local_brain_ready else 0)."""
    from aria_service.intel import autonomy_surface

    async def run():
        return await autonomy_surface.get_surface()

    s = asyncio.run(run())
    r = s["resilience"]
    expected = r["providers_active"] + (1 if r["local_brain_ready"] else 0)
    assert r["resilience_count"] == expected


# ═══════════════════════════════════════════════════════════════════════
# 4. Operator prompt — verdicts + local-brain fire counts
# ═══════════════════════════════════════════════════════════════════════

def test_prompt_silent_on_robust_verdict(monkeypatch):
    """ROBUST verdict must NOT bloat the morning briefing with noise."""
    from aria_service.intel import autonomy_surface

    async def _fake_surface():
        return {
            "generated_at": "2026-04-17T22:00:00Z",
            "auto_allowed": {},
            "drafts_awaiting": {},
            "operator_queue": {},
            "resilience": {
                "verdict": "ROBUST",
                "resilience_count": 5,
                "providers_active": 4,
                "providers_cooling": 0,
                "local_brain_ready": True,
                "local_brain_invocations_24h": 0,
                "providers": [],
                "memory": {},
            },
        }

    monkeypatch.setattr(autonomy_surface, "get_surface", _fake_surface)

    async def run():
        return await autonomy_surface.build_operator_prompt()

    text = asyncio.run(run())
    # ROBUST state with nothing else in A/B/C is an empty briefing line
    # (no nag). Just verify we don't inject a RESILIENCE warning.
    assert "Resilience: *ROBUST*" not in text


def test_prompt_surfaces_critical_verdict(monkeypatch):
    """CRITICAL must always surface with an operator action line."""
    from aria_service.intel import autonomy_surface

    async def _fake_surface():
        return {
            "generated_at": "2026-04-17T22:00:00Z",
            "auto_allowed": {},
            "drafts_awaiting": {},
            "operator_queue": {},
            "resilience": {
                "verdict": "CRITICAL",
                "resilience_count": 0,
                "providers_active": 0,
                "providers_cooling": 2,
                "local_brain_ready": False,
                "local_brain_invocations_24h": 0,
                "providers": [],
                "memory": {},
            },
        }

    monkeypatch.setattr(autonomy_surface, "get_surface", _fake_surface)

    async def run():
        return await autonomy_surface.build_operator_prompt()

    text = asyncio.run(run())
    assert "CRITICAL" in text
    assert "action" in text.lower() or "provider" in text.lower()


def test_prompt_surfaces_local_brain_fire_burst(monkeypatch):
    """>=5 local-brain fires in 24h is an early-warning signal — must show."""
    from aria_service.intel import autonomy_surface

    async def _fake_surface():
        return {
            "generated_at": "2026-04-17T22:00:00Z",
            "auto_allowed": {},
            "drafts_awaiting": {},
            "operator_queue": {},
            "resilience": {
                "verdict": "ADEQUATE",
                "resilience_count": 2,
                "providers_active": 1,
                "providers_cooling": 0,
                "local_brain_ready": True,
                "local_brain_invocations_24h": 12,
                "providers": [],
                "memory": {},
            },
        }

    monkeypatch.setattr(autonomy_surface, "get_surface", _fake_surface)

    async def run():
        return await autonomy_surface.build_operator_prompt()

    text = asyncio.run(run())
    assert "Local brain" in text
    assert "12" in text
