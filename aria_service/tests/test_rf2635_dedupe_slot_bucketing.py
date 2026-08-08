"""R-F2635 (2026-07-15) — dedupe must follow the SCHEDULE, not a flat 23h.

LIVE (aria-intel, after R-F2631 got the tick loop running at all):

    [autonomous engine] task DRAIN-COLLAB-BRIDGE blocked: duplicate_recent_run
    [autonomous engine] task DRAIN-COLLAB-BRIDGE blocked: duplicate_recent_run

DRAIN-COLLAB-BRIDGE's cron is `*/2` — 720 scheduled slots/day. It got ONE
fire and was blocked for the other 719.

ROOT CAUSE: the marker was keyed on task+entity ONLY and held for a FLAT
DEDUPE_WINDOW_SECONDS (23h) that has nothing to do with the task's cadence.
So EVERY task fired at most once per 23h whatever its cron said. Across
tasks.yaml the ~964 cron-implied fires/24h collapsed to a ~59/day ceiling.
The old docstring stated the assumption out loud — "so a DAILY task can
re-fire the next day" — and it silently became false as `*/2`, `*/30` and
hourly crons were added.

FIX (§1 root cause, not band-aid): bucket the key to the scheduled slot, so
dedupe expresses what it always meant — "do not run this task twice for the
SAME scheduled moment". Lowering the 23h constant would be the band-aid; the
failure class is "window unrelated to schedule".

It also retires a latent bug instead of patching it: tasks.py:1790 calls
`clear_dedupe(task.id, "")` after a failed run so the slot is not burned, but
that hashes entity="" while the marker used `entity or task_id` — the keys
can NEVER match (proven below), so it has never worked. With slot-keyed
markers a failed run cannot burn a future slot at all.
"""
from __future__ import annotations

import asyncio
import time

import pytest

# R-F3782/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


async def _true(v):
    return v


def _fake_store(monkeypatch):
    from aria_service.intel import redis_store as rs

    store: dict[str, dict] = {}

    async def fake_set(key, value, ex=None, keepttl=False):
        store[key] = {"v": value, "expires_at": (time.time() + ex) if ex else None}

    async def fake_get(key):
        row = store.get(key)
        if not row:
            return None
        exp = row.get("expires_at")
        if exp is not None and exp <= time.time():
            del store[key]
            return None
        return row["v"]

    async def fake_delete(key):
        return store.pop(key, None) is not None

    monkeypatch.setattr(rs, "set", fake_set)
    monkeypatch.setattr(rs, "get", fake_get)
    monkeypatch.setattr(rs, "delete", fake_delete)
    return store


# ── The live symptom ────────────────────────────────────────────────────


def test_two_minute_cron_fires_every_slot_not_once_a_day(monkeypatch):
    """THE R-F2635 BUG, reproduced. A `*/2` task must fire at EVERY
    scheduled slot. Under the flat 23h window it fired once and was blocked
    719 more times per day with `duplicate_recent_run`."""
    from aria_service.autonomous import safety

    _fake_store(monkeypatch)
    base = int(time.time() // 60)

    fired = 0
    for i in range(0, 60, 2):          # 30 slots of a */2 cron, one hour
        slot = base + i
        if asyncio.run(safety.check_and_mark_dedupe(
            "DRAIN-COLLAB-BRIDGE", "DRAIN-COLLAB-BRIDGE", slot=slot,
        )):
            fired += 1

    assert fired == 30, (
        f"a */2 task fired {fired}/30 scheduled slots in an hour — the flat "
        "23h dedupe window is capping it (live: 1 fire, 719 blocked)"
    )


def test_same_slot_is_still_deduped(monkeypatch):
    """Do not fix the throttle by removing the guard: the SAME scheduled
    minute must still fire only once. This is what stops the tick and a
    catch-up double-firing one slot."""
    from aria_service.autonomous import safety

    _fake_store(monkeypatch)
    slot = int(time.time() // 60)

    assert asyncio.run(safety.check_and_mark_dedupe("T", "e", slot=slot)) is True
    assert asyncio.run(safety.check_and_mark_dedupe("T", "e", slot=slot)) is False
    assert asyncio.run(safety.check_and_mark_dedupe("T", "e", slot=slot)) is False


def test_slot_marker_carries_a_ttl_and_is_not_the_23h_window(monkeypatch):
    """Slot markers must still be TTL'd (R-F2626: a NULL TTL is a permanent
    lockout) — but with the SHORT slot window, not the 23h one that caused
    this bug."""
    from aria_service.autonomous import safety

    store = _fake_store(monkeypatch)
    slot = int(time.time() // 60)
    asyncio.run(safety.check_and_mark_dedupe("T", "e", slot=slot))

    row = next(r for k, r in store.items() if k.startswith("crucix:autonomous:dedupe:"))
    assert row["expires_at"] is not None, "R-F2626: never write a NULL TTL"
    ttl = row["expires_at"] - time.time()
    assert ttl <= safety.DEDUPE_SLOT_WINDOW_SECONDS + 5
    assert ttl < safety.DEDUPE_WINDOW_SECONDS, (
        "slot markers must not use the flat 23h window"
    )


def test_slot_window_outlives_the_catchup_lookback():
    """A catch-up may fire a slot up to _CATCH_UP_MAX_AGE_S old. If the slot
    marker expired first, the tick and the catch-up could BOTH fire that
    slot. The slot TTL must exceed the lookback."""
    from aria_service.autonomous import safety
    from aria_service.autonomous import engine

    assert safety.DEDUPE_SLOT_WINDOW_SECONDS > engine._CATCH_UP_MAX_AGE_S, (
        "slot TTL must outlive the catch-up lookback or a slot can double-fire"
    )


# ── Unscheduled work keeps the flat 23h window ─────────────────────────


def test_coder_without_a_slot_keeps_the_flat_window(monkeypatch):
    """The coder's per-gap dedupe has NO cron — "do not retry this gap for a
    day" is the CORRECT intent there, so omitting `slot` must preserve the
    old behaviour exactly. The fix must be selective, not global."""
    from aria_service.autonomous import safety

    store = _fake_store(monkeypatch)
    assert asyncio.run(safety.check_and_mark_dedupe("aria_coder_fix", "gap-1")) is True
    assert asyncio.run(safety.check_and_mark_dedupe("aria_coder_fix", "gap-1")) is False

    key = next(k for k in store if k.startswith("crucix:autonomous:dedupe:"))
    assert key.count(":") == 4, f"unscheduled key must NOT be slot-suffixed: {key}"
    ttl = store[key]["expires_at"] - time.time()
    assert ttl > safety.DEDUPE_SLOT_WINDOW_SECONDS, (
        "unscheduled work must keep the flat 23h window"
    )


def test_can_task_run_threads_the_slot_through(monkeypatch):
    """CAPABILITY: drives the real can_task_run — the guard the engine
    actually calls — not just the helper underneath it."""
    from aria_service.autonomous import safety

    _fake_store(monkeypatch)

    async def _paused(*_a, **_k):
        return False

    async def _cost():
        return True, 0.0

    async def _rate(**_kw):
        return True, 1

    monkeypatch.setattr(safety, "is_engine_paused", _paused)
    monkeypatch.setattr(safety, "is_task_paused", _paused)
    monkeypatch.setattr(safety, "check_cost_cap", _cost)
    monkeypatch.setattr(safety, "check_and_increment_rate", _rate)

    base = int(time.time() // 60)
    ok1, _ = asyncio.run(safety.can_task_run("T", "e", slot=base))
    ok2, why2 = asyncio.run(safety.can_task_run("T", "e", slot=base))
    ok3, _ = asyncio.run(safety.can_task_run("T", "e", slot=base + 2))

    assert ok1 is True
    assert ok2 is False and why2 == "duplicate_recent_run"  # same slot
    assert ok3 is True, "the NEXT scheduled slot must be allowed to fire"


def test_tick_and_catchup_key_the_same_slot_identically():
    """VERIFY-PASS-1: catch_up passed entity=task_id while the tick passed
    `entity or task_id`, so for the 38 of 97 entity-bearing tasks the two
    produced DIFFERENT keys FOR THE SAME SLOT — dedupe could not bind them.
    That made R-F2631's justification for running catch_up CONCURRENTLY with
    the tick ("dedupe is exactly the guard against a catch-up and a tick
    firing the same task twice") false for exactly those tasks. One shared
    resolver => one key => the guarantee holds.
    """
    import inspect
    from aria_service.autonomous import engine

    src = module_source(engine)
    # Both cron-driven call sites must resolve the entity the SAME way.
    assert src.count("_resolve_task_entity(task) or task_id") >= 1, (
        "catch_up must use the shared entity resolver"
    )
    assert "entity = _resolve_task_entity(task)" in src, (
        "the tick must use the shared entity resolver"
    )

    class _T:
        tool_chain = [{"topic": "angola procurement"}]

    class _NoEntity:
        tool_chain = [{}]

    assert engine._resolve_task_entity(_T()) == "angola procurement"
    assert engine._resolve_task_entity(_NoEntity()) == ""
    assert engine._resolve_task_entity(None) == ""


def test_slot_window_invariant_warns_at_import(monkeypatch, caplog):
    """The TTL-vs-lookback invariant is env-tunable on both sides; a test
    does not run in production. The process must SAY SO if it is broken."""
    import logging
    from aria_service.autonomous import safety

    monkeypatch.setattr(safety, "DEDUPE_SLOT_WINDOW_SECONDS", 60)
    monkeypatch.setenv("ARIA_ENGINE_CATCHUP_MAX_AGE_S", "7200")
    with caplog.at_level(logging.WARNING, logger="aria.autonomous.safety"):
        safety._warn_if_slot_window_too_short()
    assert any("INVARIANT BROKEN" in r.message for r in caplog.records), (
        "a slot window below the catch-up lookback must warn loudly"
    )


# ── The latent clear_dedupe bug this retires ───────────────────────────


def test_clear_dedupe_key_mismatch_is_real_and_now_moot():
    """tasks.py:1790 calls clear_dedupe(task.id, "") after a failed run so
    the slot is not burned — but it hashes entity="" while the marker used
    `entity or task_id`. Pin the mismatch so nobody 'fixes' the symptom by
    reintroducing a flat window: slot-keying makes it moot, because a failed
    run can no longer burn a FUTURE slot."""
    from aria_service.autonomous.safety import _entity_hash, _DEDUPE_KEY_FMT

    marked = _DEDUPE_KEY_FMT.format(task_id="T", entity_hash=_entity_hash("T"))
    cleared = _DEDUPE_KEY_FMT.format(task_id="T", entity_hash=_entity_hash(""))
    assert marked != cleared, (
        "if these ever match, clear_dedupe's premise changed — re-check "
        "whether the flat-window burn is back"
    )
