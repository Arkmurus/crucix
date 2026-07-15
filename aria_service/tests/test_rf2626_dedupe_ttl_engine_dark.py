"""R-F2626 (2026-07-15) — ENGINE DARK: dedupe markers that never expire.

LIVE P0 (aria-intel, 2026-07-15):

    [R-F2006 watchdog] ENGINE DARK: autonomous engine is alive but FIRING
    NOTHING — 3h since last task fire (tasks blocked?)
    (tick_age=434s fire_age=14138s)

Read-only probe of /data/aria_state.db: **1078 of 1210** dedupe markers had
`expires_at = NULL`; 81 of 97 tasks were permanently locked out with
`blocked: duplicate_recent_run`. The engine ticked normally and fired nothing.

ROOT CAUSE — a non-atomic write, whose failure was silently discarded:

    await rs.set(key, "1")                       # ENQUEUED insert
    await rs.expire(key, DEDUPE_WINDOW_SECONDS)  # separate; return IGNORED

  - state_store.set() enqueues the INSERT (state_store.py:2121); a background
    worker drains it every 100ms.
  - expire() calls _flush_write_queue() first (R-F1933 saw the ordering
    hazard) — but that flush returns 0 the moment the queue is EMPTY
    (state_store.py:626), and the worker has ALREADY dequeued the write into
    an in-flight batch it is still awaiting.
  - Queue empty + INSERT uncommitted => UPDATE matches 0 rows => expire()
    returns False (state_store.py:2911 — silently; it never raises) => the
    caller ignored it => the INSERT landed afterwards with expires_at = NULL.

The dedupe key is NOT time-bucketed (`dedupe:{task_id}:{entity_hash}`), so a
NULL TTL is a PERMANENT lockout — unlike the rate/cost keys, which are
hour/date-bucketed and merely leak.

FIX (§1 root cause, not symptom): `set(key, "1", ex=...)` — one write that
carries its own TTL, so the row cannot exist without one. Plus a one-time
sentinel-guarded repair for the markers already stranded on disk, which the
code fix alone cannot help.
"""
from __future__ import annotations

import asyncio
import time

import pytest


def _fake_store(monkeypatch, *, initial=None, race_expire=False):
    """In-memory store modelling the REAL semantics that caused the bug:

      - set(key, val)            -> row with expires_at = None
      - set(key, val, ex=N)      -> row with a TTL (atomic)
      - expire(key, N)           -> returns False and does NOTHING when the
                                    row isn't committed yet (race_expire),
                                    exactly like state_store's 0-row UPDATE.
      - get(key)                 -> None once expired
    """
    from aria_service.intel import redis_store as rs

    store: dict[str, dict] = dict(initial or {})

    async def fake_set(key, value, ex=None, keepttl=False):
        store[key] = {
            "v": value,
            "expires_at": (time.time() + ex) if ex else None,
        }

    async def fake_get(key):
        row = store.get(key)
        if not row:
            return None
        exp = row.get("expires_at")
        if exp is not None and exp <= time.time():
            del store[key]
            return None
        return row["v"]

    async def fake_expire(key, seconds):
        # THE BUG: under the race the row isn't visible yet -> 0 rows -> False.
        if race_expire or key not in store:
            return False
        store[key]["expires_at"] = time.time() + seconds
        return True

    async def fake_delete(key):
        return store.pop(key, None) is not None

    async def fake_scan_keys(pattern, count=200):
        # HONOUR `count` — the real scan_keys HARD-TRUNCATES at it
        # (state_store.py:3240 `if len(matched) >= count: break`); it does NOT
        # page. An earlier version of this fake ignored `count` and returned
        # everything, which made the repair look complete while live it would
        # have cleared only 200 of 1078 markers and then burnt the sentinel.
        # A fake that cannot fail is a wrong test (§23).
        pre = pattern.rstrip("*")
        return [k for k in list(store) if k.startswith(pre)][:count]

    monkeypatch.setattr(rs, "set", fake_set)
    monkeypatch.setattr(rs, "get", fake_get)
    monkeypatch.setattr(rs, "expire", fake_expire)
    monkeypatch.setattr(rs, "delete", fake_delete)
    monkeypatch.setattr(rs, "scan_keys", fake_scan_keys)
    return store


# ── The root cause: a marker must never be written without a TTL ────────


def test_dedupe_marker_always_carries_a_ttl_even_when_expire_would_fail(
    monkeypatch,
):
    """THE R-F2626 BUG. `race_expire=True` reproduces the live race: the
    separate expire() no-ops and returns False. The OLD code ignored that and
    left expires_at = NULL -> permanent lockout. The atomic set(ex=) must be
    immune, because there is no second call to fail.

    Capability: drives the real safety.check_and_mark_dedupe().
    """
    from aria_service.autonomous import safety

    store = _fake_store(monkeypatch, race_expire=True)
    assert asyncio.run(safety.check_and_mark_dedupe("DAILY-GEO-LATAM", "angola")) is True

    rows = [r for k, r in store.items() if k.startswith("crucix:autonomous:dedupe:")]
    assert len(rows) == 1
    assert rows[0]["expires_at"] is not None, (
        "dedupe marker written with NULL TTL — this is the permanent lockout "
        "that took the engine dark (81/97 tasks blocked live)"
    )


def test_marker_expires_and_unblocks_the_task(monkeypatch):
    """The marker must actually let go. A dedupe hint that never expires is
    an outage, not a guard."""
    from aria_service.autonomous import safety

    monkeypatch.setattr(safety, "DEDUPE_WINDOW_SECONDS", 1)
    _fake_store(monkeypatch, race_expire=True)

    assert asyncio.run(safety.check_and_mark_dedupe("T1", "e")) is True
    assert asyncio.run(safety.check_and_mark_dedupe("T1", "e")) is False  # deduped
    time.sleep(1.1)
    assert asyncio.run(safety.check_and_mark_dedupe("T1", "e")) is True, (
        "after the TTL elapses the task MUST be allowed to run again"
    )


def test_dedupe_still_blocks_a_genuine_duplicate(monkeypatch):
    """Don't fix the outage by breaking the guard: a real duplicate inside
    the window must still be blocked."""
    from aria_service.autonomous import safety

    _fake_store(monkeypatch, race_expire=True)
    assert asyncio.run(safety.check_and_mark_dedupe("T2", "acme")) is True
    assert asyncio.run(safety.check_and_mark_dedupe("T2", "acme")) is False
    # A DIFFERENT entity is not a duplicate.
    assert asyncio.run(safety.check_and_mark_dedupe("T2", "other")) is True


# ── The repair: stranded markers must be released ──────────────────────


def test_repair_clears_stranded_null_ttl_markers(monkeypatch):
    """The code fix cannot help markers already on disk — they are permanent
    by construction. Without the repair the engine stays dark forever."""
    from aria_service.autonomous import safety

    store = _fake_store(monkeypatch, initial={
        "crucix:autonomous:dedupe:DAILY-GEO-LATAM:abc": {"v": "1", "expires_at": None},
        "crucix:autonomous:dedupe:DRAIN-COLLAB-BRIDGE:def": {"v": "1", "expires_at": None},
        "crucix:autonomous:other:keep": {"v": "1", "expires_at": None},
    })

    out = asyncio.run(safety.repair_nulled_dedupe_markers())
    assert out["deleted"] == 2
    assert not [k for k in store if k.startswith("crucix:autonomous:dedupe:")], (
        "stranded dedupe markers must be released"
    )
    assert "crucix:autonomous:other:keep" in store, "repair must not touch other keys"


def test_repair_unblocks_a_permanently_locked_task_end_to_end(monkeypatch):
    """CAPABILITY: reproduce the LIVE symptom — a task blocked by a NULL-TTL
    marker — and prove the repair unblocks it. This is the user-visible
    outcome: the engine fires again."""
    from aria_service.autonomous import safety

    _fake_store(monkeypatch, initial={
        "crucix:autonomous:dedupe:DAILY-GEO-LATAM:"
        + safety._entity_hash("angola"): {"v": "1", "expires_at": None},
    })

    # Before: permanently blocked (the live "blocked: duplicate_recent_run").
    assert asyncio.run(safety.check_and_mark_dedupe("DAILY-GEO-LATAM", "angola")) is False

    asyncio.run(safety.repair_nulled_dedupe_markers())

    # After: allowed — and re-marked WITH a TTL this time.
    assert asyncio.run(safety.check_and_mark_dedupe("DAILY-GEO-LATAM", "angola")) is True, (
        "repair must unblock the task the engine was dark on"
    )


def test_repair_clears_MORE_than_one_scan_batch(monkeypatch):
    """VERIFY-PASS-1 BLOCKER. scan_keys HARD-TRUNCATES at `count` (default
    200) — it does not page. The live incident had 1078 stranded markers, so
    a single un-batched scan would clear 200, burn the sentinel, and leave
    the engine dark while logging "success". The sweep must batch until the
    scan comes back empty.
    """
    from aria_service.autonomous import safety

    initial = {
        f"crucix:autonomous:dedupe:TASK{i:04d}:hash": {"v": "1", "expires_at": None}
        for i in range(1078)  # the live count
    }
    store = _fake_store(monkeypatch, initial=initial)

    out = asyncio.run(safety.repair_nulled_dedupe_markers())
    assert out["deleted"] == 1078, (
        f"only cleared {out['deleted']} of 1078 — a truncated sweep leaves "
        "most tasks permanently locked out and the engine dark"
    )
    assert out["complete"] is True
    assert not [k for k in store if k.startswith("crucix:autonomous:dedupe:")]


def test_incomplete_sweep_does_not_burn_the_sentinel(monkeypatch):
    """If the sweep cannot drain, it must NOT record itself complete —
    otherwise its one attempt is spent and a corrected sweep can never
    retry. Same class as the discarded expire() return that caused R-F2626."""
    from aria_service.autonomous import safety

    store = _fake_store(monkeypatch, initial={
        f"crucix:autonomous:dedupe:T{i}:h": {"v": "1", "expires_at": None}
        for i in range(50)
    })
    # Force exhaustion: no rounds allowed to converge.
    monkeypatch.setattr(safety, "_DEDUPE_REPAIR_MAX_ROUNDS", 1)
    monkeypatch.setattr(safety, "_DEDUPE_REPAIR_BATCH", 10)

    out = asyncio.run(safety.repair_nulled_dedupe_markers())
    assert out["complete"] is False
    assert safety._DEDUPE_REPAIR_SENTINEL not in store, (
        "an incomplete sweep must leave itself ARMED, not claim success"
    )
    # Next start (budget restored) finishes the job.
    monkeypatch.setattr(safety, "_DEDUPE_REPAIR_MAX_ROUNDS", 40)
    out2 = asyncio.run(safety.repair_nulled_dedupe_markers())
    assert out2["complete"] is True
    assert not [k for k in store if k.startswith("crucix:autonomous:dedupe:")]


def test_repair_is_sentinel_guarded_and_runs_only_once(monkeypatch):
    """It runs on every engine start, so it MUST be one-time — otherwise each
    restart wipes live markers and defeats dedupe entirely."""
    from aria_service.autonomous import safety

    store = _fake_store(monkeypatch, initial={
        "crucix:autonomous:dedupe:T:x": {"v": "1", "expires_at": None},
    })

    first = asyncio.run(safety.repair_nulled_dedupe_markers())
    assert first["deleted"] == 1
    assert first["skipped"] is False

    # A live marker written after the repair must survive a second call.
    store["crucix:autonomous:dedupe:T2:y"] = {"v": "1", "expires_at": time.time() + 3600}
    second = asyncio.run(safety.repair_nulled_dedupe_markers())
    assert second["skipped"] is True, "repair must not re-arm on restart"
    assert "crucix:autonomous:dedupe:T2:y" in store, (
        "a second run would have wiped a LIVE marker — dedupe defeated"
    )


def test_zero_ttl_env_cannot_recreate_the_permanent_lockout(monkeypatch):
    """VERIFY-PASS-2 #3. _ttl_to_expires(0) and (-5) both return None, so
    ARIA_AUTONOMOUS_DEDUPE_WINDOW_S=0 would write expires_at=NULL and silently
    recreate the exact permanent lockout R-F2626 removes. The max(1, ...)
    clamp makes "the row can never exist without a TTL" actually true."""
    from aria_service.autonomous import safety

    store = _fake_store(monkeypatch, race_expire=True)
    monkeypatch.setattr(safety, "DEDUPE_WINDOW_SECONDS", 0)

    assert asyncio.run(safety.check_and_mark_dedupe("T", "e")) is True
    row = next(r for k, r in store.items() if k.startswith("crucix:autonomous:dedupe:"))
    assert row["expires_at"] is not None, (
        "a 0/negative dedupe window must not write a NULL TTL — that is the "
        "permanent lockout again"
    )


def test_deleted_count_reports_only_real_deletions(monkeypatch):
    """VERIFY-PASS-2 #2. rs.delete swallows failures and returns False, so an
    unconditional counter would log "cleared N markers — tasks unblocked"
    while clearing nothing — the same discarded-return-value class as the
    expire() bug this R-number fixes."""
    from aria_service.intel import redis_store as rs
    from aria_service.autonomous import safety

    _fake_store(monkeypatch, initial={
        f"crucix:autonomous:dedupe:T{i}:h": {"v": "1", "expires_at": None}
        for i in range(5)
    })

    async def delete_always_fails(key):
        return False  # exactly what state_store.delete does on failure

    monkeypatch.setattr(rs, "delete", delete_always_fails)
    monkeypatch.setattr(safety, "_DEDUPE_REPAIR_MAX_ROUNDS", 2)

    out = asyncio.run(safety.repair_nulled_dedupe_markers())
    assert out["deleted"] == 0, (
        f"reported {out['deleted']} deletions that never happened"
    )
    assert out["delete_failed"] > 0
    assert out["complete"] is False  # nothing drained → stays armed


def test_repair_runs_after_the_startup_delay_not_during_boot(monkeypatch):
    """VERIFY-PASS-2 #1. state_store.get() returns None on a 5s TIMEOUT
    (state_store.py:2183) — indistinguishable from 'sentinel absent'. Reading
    it during peak boot could re-run the sweep and wipe LIVE markers every
    restart. Pin the ORDER: the repair must sit after the startup-delay sleep
    and before catch_up_overdue_tasks (which consumes dedupe).
    """
    import inspect
    from aria_service.autonomous import engine

    src = inspect.getsource(engine._engine_loop)
    i_sleep = src.index("await asyncio.sleep(STARTUP_DELAY_SECONDS)")
    i_repair = src.index("repair_nulled_dedupe_markers")
    i_catchup = src.index("catch_up_overdue_tasks(llm)")
    assert i_sleep < i_repair, (
        "repair must run AFTER the startup delay — a sentinel read during "
        "peak boot can time out to None and re-arm the sweep"
    )
    assert i_repair < i_catchup, (
        "repair must run BEFORE catch-up, which is a dedupe consumer"
    )


def test_repair_never_raises_into_the_engine(monkeypatch):
    """It runs in the engine's startup path — it must never stop the engine."""
    from aria_service.intel import redis_store as rs
    from aria_service.autonomous import safety

    async def boom(*a, **k):
        raise RuntimeError("store wedged")

    monkeypatch.setattr(rs, "get", boom)
    out = asyncio.run(safety.repair_nulled_dedupe_markers())
    assert "error" in out


def test_repair_gap_type_is_registered():
    """§3b/§21a — a wire_failure with an unregistered gap_type is silently
    rejected, so the brain would never see the repair fail."""
    from aria_service.intel.capability_gaps import VALID_GAP_TYPES

    assert "engine_failure" in VALID_GAP_TYPES
    assert "module_bug" not in VALID_GAP_TYPES  # the one I nearly shipped
