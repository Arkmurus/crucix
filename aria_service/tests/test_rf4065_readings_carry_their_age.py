"""R-F4065 (C-117) — readings that carried no age, no window, or the name of a
decommissioned backend.

Four small lies of omission on the command centre, all measured 2026-08-16:

1. **"Memory: Redis: up"** — the state store is SQLite on the fly volume.
   Upstash was decommissioned 2026-05-12 (§6/§18) and `REDIS_URL` is unset. The
   SAME page says so, in the cost panel's State backend box. A stale name is how
   a future session goes hunting a dependency that does not exist.

2. **Operating Mode showed transitions only.** The newest history entry was
   2026-08-07 — nine days old — while the evaluator runs hourly, so the panel
   could not distinguish "evaluated, nothing to change" from "the evaluator
   died". Here it was the former (R-F3764's minimum-sample floor correctly
   ignores the n=1 grounded rate), but nothing on the page could say so, and
   `evaluate_auto_transition` is the ONLY route out of DEGRADED — a state that
   suppresses all external delivery. `autonomous/tasks.py` already reports
   `mode_evaluated` for exactly this reason; nothing durable existed for the
   dashboard to read.

3. **Engine "Tasks Fired 29 · Ticks 50" were per-process and unlabelled.**
   aria-intel restarted at 17:11Z mid-audit and they became 5/7, so after every
   restart the engine appears to have done nothing. The honest 24h figure sits
   two panels away (`autonomous_task_fires: 431`, from a properly TTL'd key).

4. **Bare timestamps presented as current.** Layer 5c's "Latest run
   2026-08-13" (three days stale) and the training corpus's 1882 examples (last
   exported 2026-08-03, thirteen days) were rendered with no age at all.

This file covers the two BACKEND halves — the durable evaluation stamp and the
honest store name. The four rendering halves are guarded in
`test/aria-brain-age-labels-rf4065.test.mjs`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


# ── 1. the evaluator must leave a durable mark that it RAN ─────────────────

@pytest.mark.asyncio
async def test_evaluate_stamps_last_evaluated_even_with_no_transition():
    """The no-change case is exactly the one that was invisible."""
    from aria_service.intel import operating_modes as om

    writes: dict = {}

    async def _set(key, value, ex=None, keepttl=False):
        writes[key] = (value, ex)

    with patch.object(om, "get_mode", AsyncMock(return_value=om.Mode.NORMAL)), \
         patch("aria_service.intel.redis_store.get", AsyncMock(return_value=None)), \
         patch("aria_service.intel.redis_store.set", _set), \
         patch("aria_service.intel.adversarial_challenge.stats",
               AsyncMock(return_value={"last_run": {"overall_score": 0.9}})), \
         patch("aria_service.intel.source_verifier.get_verification_stats",
               AsyncMock(return_value={"avg_grounded_rate": None})):
        result = await om.evaluate_auto_transition()

    assert result is None, "no transition expected in this fixture"
    assert om._K_LAST_EVAL in writes, (
        "the evaluator ran and left no durable mark, so a nine-day-old "
        f"transition history is indistinguishable from a dead loop: {writes}")
    stamped, ttl = writes[om._K_LAST_EVAL]
    datetime.fromisoformat(stamped)          # must be parseable
    assert ttl == om._LAST_EVAL_TTL_S


@pytest.mark.asyncio
async def test_the_stamp_expires_so_absence_is_a_signal():
    """A 72h TTL against an hourly check: absent means "has not run in three
    days", which is a reading. A never-expiring stamp would decay into an
    ambiguous old timestamp instead."""
    from aria_service.intel import operating_modes as om
    assert om._LAST_EVAL_TTL_S == 72 * 3600
    assert om._LAST_EVAL_TTL_S > 3600, "must outlast the hourly cadence"


@pytest.mark.asyncio
async def test_a_stamp_failure_never_blocks_the_transition():
    """Bookkeeping must not be able to strand the platform in DEGRADED."""
    from aria_service.intel import operating_modes as om

    async def _boom(*a, **k):
        raise RuntimeError("store down")

    with patch.object(om, "get_mode", AsyncMock(return_value=om.Mode.DEGRADED)), \
         patch.object(om, "set_mode", AsyncMock(return_value={"mode": "NORMAL"})), \
         patch("aria_service.intel.redis_store.get", AsyncMock(return_value=None)), \
         patch("aria_service.intel.redis_store.set", _boom), \
         patch("aria_service.intel.adversarial_challenge.stats",
               AsyncMock(return_value={"last_run": {"overall_score": 0.9}})), \
         patch("aria_service.intel.source_verifier.get_verification_stats",
               AsyncMock(return_value={"avg_grounded_rate": None})):
        result = await om.evaluate_auto_transition()

    assert result == {"mode": "NORMAL"}, (
        "a failed stamp must not stop the only route out of DEGRADED")


# ── 2. name the backend that is actually in use ────────────────────────────

@pytest.mark.asyncio
async def test_resilience_reports_the_state_store_not_redis():
    from aria_service.intel import autonomy_surface as asf

    # The probe both READS and WRITES the health-ping key; patch both, or the
    # write raises `state_store: no connection` off-box and the whole memory
    # block lands in its except.
    with patch("aria_service.intel.redis_store.get", AsyncMock(return_value="1")), \
         patch("aria_service.intel.redis_store.set", AsyncMock()), \
         patch("aria_service.intel.redis_store._use_sqlite", lambda: True):
        out = await asf._resilience_floor()

    mem = out["memory"]
    assert mem.get("state_store_reachable") is True, mem
    assert mem.get("state_store_backend") == "sqlite", (
        "the page rendered 'Redis: up' while the store is SQLite on the fly "
        f"volume and Upstash was decommissioned 2026-05-12: {mem}")
    # kept for any existing reader — renaming in place would be its own break
    assert "redis_reachable" in mem


@pytest.mark.asyncio
async def test_ages_are_computable_from_what_the_api_publishes():
    """The rendering half needs a timestamp to subtract from. These are the
    fields the panels read; if one stops being published the age silently
    disappears and the reading looks current again."""
    from aria_service.intel import operating_modes as om
    assert isinstance(om._K_LAST_EVAL, str) and om._K_LAST_EVAL

    # A stamp older than the TTL cannot be produced by a live evaluator, so the
    # panel's "not in the last 72h" branch is reachable only by real absence.
    old = datetime.now(timezone.utc) - timedelta(hours=100)
    assert (datetime.now(timezone.utc) - old).total_seconds() > om._LAST_EVAL_TTL_S
