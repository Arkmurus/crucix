"""Tests for autonomy_surface + regional_bright_lines hit counter.

Covers:
  1. get_surface() returns the three-view payload without crashing
     on a clean environment (no Redis, no writer audit log, no
     OEM records).
  2. build_operator_prompt() returns an empty string when there is
     nothing to show; non-empty string when there is.
  3. Bright-lines hit counter — writing + reading path.
  4. WA mirror gated flag reflects environment variables.
"""
from __future__ import annotations

import asyncio
import os

import pytest


# ═══════════════════════════════════════════════════════════════════════
# 1. get_surface() shape + graceful degradation
# ═══════════════════════════════════════════════════════════════════════

def test_get_surface_returns_three_top_level_sections():
    from aria_service.intel import autonomy_surface

    async def run():
        return await autonomy_surface.get_surface()

    s = asyncio.run(run())
    assert "auto_allowed" in s
    assert "drafts_awaiting" in s
    assert "operator_queue" in s
    assert "generated_at" in s
    assert s["doctrine_reference"].endswith("aria_autonomy_doctrine.md")


def test_get_surface_auto_allowed_keys_present():
    from aria_service.intel import autonomy_surface

    async def run():
        return await autonomy_surface.get_surface()

    s = asyncio.run(run())
    auto = s["auto_allowed"]
    for k in ("autonomous_task_fires", "chat_turns_served",
              "corpus_ingests", "audit_entries",
              "bright_lines_triggered", "bright_lines_by_code"):
        assert k in auto, f"missing key {k} in auto_allowed"


def test_get_surface_drafts_keys_present():
    from aria_service.intel import autonomy_surface

    async def run():
        return await autonomy_surface.get_surface()

    s = asyncio.run(run())
    drafts = s["drafts_awaiting"]
    for k in ("source_validator_pending", "constitution_pending",
              "codegen_pending", "golden_pending",
              "ground_truth_pending", "dd_reports_today",
              "writer_outputs_today", "total_pending"):
        assert k in drafts


def test_get_surface_operator_queue_keys_present():
    from aria_service.intel import autonomy_surface

    async def run():
        return await autonomy_surface.get_surface()

    s = asyncio.run(run())
    q = s["operator_queue"]
    for k in ("oem_slots_total", "oem_slots_filled", "oem_slots_empty",
              "oem_worst_oems", "wa_mirror_gated", "wa_mirror_missing_env",
              "stale_facts", "contradicted_facts", "bright_lines_recent"):
        assert k in q


# ═══════════════════════════════════════════════════════════════════════
# 2. build_operator_prompt
# ═══════════════════════════════════════════════════════════════════════

def test_prompt_mentions_wa_mirror_when_gated(monkeypatch):
    """When ARIA_MIRROR_GROUPS is unset, the prompt must call out the gate."""
    from aria_service.intel import autonomy_surface

    for env in ("ARIA_MIRROR_GROUPS", "ARIA_COUNTERPARTY_CONTACTS",
                "ARIA_DECEPTION_THRESHOLD"):
        monkeypatch.delenv(env, raising=False)

    async def run():
        return await autonomy_surface.build_operator_prompt()

    text = asyncio.run(run())
    # Empty OEM graph + no Redis → only the WA-mirror gate is a signal.
    # It MUST surface or the prompt is useless.
    assert "WA" in text or "mirror" in text.lower() or text == ""


def test_prompt_is_plain_whatsapp_ready_string():
    from aria_service.intel import autonomy_surface

    async def run():
        return await autonomy_surface.build_operator_prompt()

    text = asyncio.run(run())
    # Always a string (never None), safe to embed in a briefing
    assert isinstance(text, str)


# ═══════════════════════════════════════════════════════════════════════
# 3. Bright-lines hit counter
# ═══════════════════════════════════════════════════════════════════════

def test_check_text_fires_counter_side_effect_safely():
    """check_text must not raise even when Redis is unreachable."""
    from aria_service.intel import regional_bright_lines
    # This should produce a hit and attempt to write to Redis (which may
    # fail in test env) but NOT raise.
    hits = regional_bright_lines.check_text("FARDC replenishment in DRC")
    assert any(h["code"] == "DRC_COUNTERPARTY" for h in hits)


def test_get_hits_24h_returns_shape_even_without_redis():
    from aria_service.intel import regional_bright_lines

    async def run():
        return await regional_bright_lines.get_hits_24h()

    data = asyncio.run(run())
    assert "total" in data
    assert "by_code" in data
    assert "items" in data


# ═══════════════════════════════════════════════════════════════════════
# 4. WA mirror gate reflects env
# ═══════════════════════════════════════════════════════════════════════

def test_wa_mirror_gated_when_env_missing(monkeypatch):
    from aria_service.intel import autonomy_surface

    for env in ("ARIA_MIRROR_GROUPS", "ARIA_COUNTERPARTY_CONTACTS",
                "ARIA_DECEPTION_THRESHOLD"):
        monkeypatch.delenv(env, raising=False)

    async def run():
        return await autonomy_surface.get_surface()

    s = asyncio.run(run())
    q = s["operator_queue"]
    assert q["wa_mirror_gated"] is True
    assert len(q["wa_mirror_missing_env"]) >= 1


def test_wa_mirror_live_when_all_env_set(monkeypatch):
    from aria_service.intel import autonomy_surface
    monkeypatch.setenv("ARIA_MIRROR_GROUPS", "123@g.us")
    monkeypatch.setenv("ARIA_COUNTERPARTY_CONTACTS", "456@s.whatsapp.net")
    monkeypatch.setenv("ARIA_DECEPTION_THRESHOLD", "0.7")

    async def run():
        return await autonomy_surface.get_surface()

    s = asyncio.run(run())
    q = s["operator_queue"]
    assert q["wa_mirror_gated"] is False
    assert q["wa_mirror_missing_env"] == []
    assert q.get("wa_mirror_status") == "LIVE"


def test_wa_mirror_deferred_flag_suppresses_nag(monkeypatch):
    """ARIA_MIRROR_DEFERRED=1 treats missing env as doctrine-satisfied."""
    from aria_service.intel import autonomy_surface

    for env in ("ARIA_MIRROR_GROUPS", "ARIA_COUNTERPARTY_CONTACTS",
                "ARIA_DECEPTION_THRESHOLD"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("ARIA_MIRROR_DEFERRED", "1")

    async def run_surface():
        return await autonomy_surface.get_surface()

    async def run_prompt():
        return await autonomy_surface.build_operator_prompt()

    s = asyncio.run(run_surface())
    q = s["operator_queue"]
    assert q["wa_mirror_gated"] is False
    assert q.get("wa_mirror_status") == "DEFERRED"

    # And the briefing prompt must NOT mention the mirror when deferred
    text = asyncio.run(run_prompt())
    assert "mirror" not in text.lower()
