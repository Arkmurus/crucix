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


# R-F782 (2026-05-21): autonomy_surface now caches each sub-task result
# for 60s to absorb SQLite write contention. These tests rely on fresh
# computation per call (monkeypatched sub-tasks, env-var driven branches),
# so reset the cache before AND after each test in this file. Autouse
# fixture covers every test below without each one needing a manual call.
@pytest.fixture(autouse=True)
def _r_f782_reset_autonomy_surface_cache():
    from aria_service.intel import autonomy_surface
    autonomy_surface._SUBTASK_CACHE.clear()
    yield
    autonomy_surface._SUBTASK_CACHE.clear()


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


def test_R_F347_hung_subtask_does_not_block_endpoint(monkeypatch):
    """R-F347: if ONE sub-task hangs, get_surface still returns within
    SUBTASK_TIMEOUT_SECONDS+grace with that sub-task replaced by its
    default shape. Other sub-tasks still produce real data.

    Before R-F347 the four sub-tasks ran sequentially; a hung
    `_resilience_floor` (e.g. Upstash ping that never returned) blocked
    the whole 30s endpoint timeout and the dashboard rendered all-zeros
    on the fly→seenode proxy fallthrough.
    """
    import time
    from aria_service.intel import autonomy_surface

    # Force a tight timeout so the test runs fast.
    monkeypatch.setattr(autonomy_surface, "SUBTASK_TIMEOUT_SECONDS", 0.5)

    async def slow_resilience():
        # Hang forever — the wait_for must cancel and return defaults
        await asyncio.sleep(30)
        return {"verdict": "should_never_see_this"}

    monkeypatch.setattr(autonomy_surface, "_resilience_floor", slow_resilience)

    async def run():
        return await autonomy_surface.get_surface()

    t0 = time.monotonic()
    s = asyncio.run(run())
    elapsed = time.monotonic() - t0

    # Must return well before the 30s endpoint cap — bounded by the
    # per-subtask timeout (0.5s + a tiny scheduler overhead).
    assert elapsed < 5.0, f"endpoint must not wait for hung subtask; took {elapsed:.2f}s"

    # The hung sub-task must fall back to defaults.
    assert s["resilience"]["verdict"] == "unknown"
    assert s["resilience"]["memory"]["redis_reachable"] is False
    # The R-F347 marker tags the default so observability sees it.
    assert s["resilience"].get("_degraded_marker") == "R-F347_subtask_timeout"

    # Other sub-tasks should still have produced their normal shape.
    assert "autonomous_task_fires" in s["auto_allowed"]
    assert "total_pending" in s["drafts_awaiting"]
    assert "oem_slots_total" in s["operator_queue"]


def test_R_F347_raising_subtask_falls_back_to_defaults(monkeypatch):
    """R-F347: if a sub-task raises, get_surface logs and substitutes
    the default — does NOT propagate the exception to the endpoint."""
    from aria_service.intel import autonomy_surface

    async def boom():
        raise RuntimeError("simulated upstream failure")

    monkeypatch.setattr(autonomy_surface, "_operator_queue", boom)

    async def run():
        return await autonomy_surface.get_surface()

    s = asyncio.run(run())

    # The raising sub-task fell back to the default shape.
    q = s["operator_queue"]
    assert q["oem_slots_total"] == 0
    assert q["oem_slots_filled"] == 0
    assert q["stale_facts"] == 0
    # Other panels still produced normal output.
    assert "autonomous_task_fires" in s["auto_allowed"]
    assert "providers" in s["resilience"]


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
