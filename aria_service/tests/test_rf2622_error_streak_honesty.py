"""R-F2622 (2026-07-15) — Phase A gate #3 must be MEASURED, not assumed.

R-F560 shipped `error_streak.compute_error_streak` with a branch that read
"no ERROR in the ledger" as "7 clean days, gate passes":

    if last_reset_ev is None:
        out["consecutive_clean_seconds"] = 7 * 86400
        out["consecutive_clean_days"] = 7
        out["phase_a_gate_3_pass"] = True

That made the gate unfalsifiable. The ledger it reads
(`self_improve.ERROR_LOG_KEY`) is a 200-slot ring buffer SHARED by warnings
and errors (trimmed `errors[-200:]`) with a 7-day TTL, and `record_error`
drops errors outright while the R-F1510 breaker is open. So an error-free
ledger is equally consistent with:

    (a) a genuinely clean week,
    (b) a warning burst that evicted a real ERROR,
    (c) a TTL'd-away / never-written key,
    (d) errors dropped while the breaker was open.

R-F560 certified Phase A gate #3 in ALL FOUR cases. These tests pin the
honest contract: cleanliness is claimed only as far back as real evidence
supports, and the gate is EARNED.

Capability (§3c): every test drives the real entry point
`error_streak.compute_error_streak()` — the same call the live endpoint
`GET /api/aria/health/error-streak` makes at routes/aria.py:24312 — and
asserts the user-visible field the Phase A gate is read from
(`phase_a_gate_3_pass`), not a helper's return value.
"""
from __future__ import annotations

import asyncio
import time

import pytest


def _setup_fake_redis(
    monkeypatch, initial_events=None, anchor=None, genesis_ts=None,
):
    """Stub redis_store with an in-memory store, so the ledger, the durable
    anchor and the genesis marker are all exercised for real.

    NOTE: `get_json_strict` (R-F1392) is stubbed as well as `get_json`.
    compute_error_streak deliberately reads the anchor through the STRICT
    reader — get_json swallows store failures and returns None, which the
    gate would read as "no error ever recorded". Leaving it unstubbed would
    let these tests fall through to the real state_store.
    """
    from aria_service.intel import redis_store as rs
    from aria_service.intel import self_improve as si
    from aria_service.intel import error_streak as es

    store: dict[str, object] = {}
    if initial_events is not None:
        store[si.ERROR_LOG_KEY] = list(initial_events)
    if anchor is not None:
        store[es.STREAK_ANCHOR_KEY] = dict(anchor)
    if genesis_ts is not None:
        store[es.STREAK_GENESIS_KEY] = {"genesis_ts": genesis_ts}

    async def fake_get_json(key):
        v = store.get(key)
        return v if v is not None else None

    async def fake_set_json(key, obj, ex=None, keepttl=False):
        store[key] = obj

    monkeypatch.setattr(rs, "get_json", fake_get_json)
    monkeypatch.setattr(rs, "get_json_strict", fake_get_json)
    monkeypatch.setattr(rs, "set_json", fake_set_json)
    # Drop markers are process-global; isolate each test.
    monkeypatch.setattr(si, "_SI_ERRORS_DROPPED", 0, raising=False)
    monkeypatch.setattr(si, "_SI_LAST_DROP_TS", 0.0, raising=False)
    return store


def _ev(ts_offset_h: float, level: str = "error", file: str = "x.py") -> dict:
    return {
        "type": f"log:{level}",
        "message": f"sample {level}",
        "file": file,
        "function": "fn",
        "timestamp": time.time() - ts_offset_h * 3600,
    }


# ── (a) The core bug: empty ledger is UNKNOWN, not clean ────────────────


def test_empty_ledger_does_not_certify_the_gate(monkeypatch):
    """THE R-F560 BUG. An empty ledger + no anchor proves nothing at all.
    R-F560 returned days=7 / pass=True here — a fabricated measurement.
    Honest contract: pass=False + insufficient_history."""
    _setup_fake_redis(monkeypatch, initial_events=[])
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["phase_a_gate_3_pass"] is False, (
        "empty ledger must NOT certify gate #3 — absence of evidence is "
        "not evidence of cleanliness"
    )
    assert r["consecutive_clean_days"] == 0
    assert r["streak_basis"] == "no_evidence"
    assert r["insufficient_history"] is True


def test_fresh_boot_does_not_claim_seven_days(monkeypatch):
    """A box that booted minutes ago cannot have a 7-day clean streak.
    R-F560 hardcoded 7. Honest contract: measure from genesis."""
    _setup_fake_redis(monkeypatch, initial_events=[_ev(0.05, "warning")])
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["consecutive_clean_days"] == 0
    assert r["phase_a_gate_3_pass"] is False
    assert r["consecutive_clean_seconds"] < 3600


# ── (b) Ring-buffer eviction can't erase an ERROR ───────────────────────


def test_anchor_survives_ring_buffer_eviction(monkeypatch):
    """A warning burst evicts the ERROR out of the 200-slot ledger. The
    durable anchor still holds it, so the streak is measured from the real
    error — not reset to 'clean'."""
    from aria_service.intel import error_streak as es

    # Ledger holds ONLY warnings (the ERROR was trimmed away).
    burst = [_ev(0.5, "warning") for _ in range(200)]
    _setup_fake_redis(
        monkeypatch,
        initial_events=burst,
        anchor={
            "last_error_ts": time.time() - 2 * 3600,  # real ERROR 2h ago
            "last_error": {"type": "log:error", "message": "evicted boom"},
        },
        genesis_ts=time.time() - 30 * 86400,
    )

    r = asyncio.run(es.compute_error_streak())
    assert r["phase_a_gate_3_pass"] is False, (
        "an ERROR evicted by a warning burst must still fail the gate"
    )
    assert r["streak_basis"] == "last_error"
    assert 1.5 < r["last_error_age_hours"] < 2.5
    assert r["ledger_saturated"] is True


def test_anchor_survives_ledger_ttl_expiry(monkeypatch):
    """The 7d TTL wipes the ledger key entirely, but the TTL-less anchor
    remembers the recent ERROR → gate stays honestly closed."""
    _setup_fake_redis(
        monkeypatch,
        initial_events=[],  # key expired
        anchor={
            "last_error_ts": time.time() - 6 * 3600,
            "last_error": {"type": "log:critical", "message": "gone"},
        },
        genesis_ts=time.time() - 30 * 86400,
    )
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["phase_a_gate_3_pass"] is False
    assert r["streak_basis"] == "last_error"


# ── (c) The gate can still be EARNED ────────────────────────────────────


def test_real_seven_day_evidence_earns_the_pass(monkeypatch):
    """Honesty cuts both ways: with 8 days of continuous warning-only
    evidence and no ERROR ever, the gate legitimately passes."""
    events = [_ev(8 * 24, "warning"), _ev(1, "warning")]
    _setup_fake_redis(
        monkeypatch,
        initial_events=events,
        genesis_ts=time.time() - 8 * 86400,
    )
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["phase_a_gate_3_pass"] is True
    assert r["consecutive_clean_days"] >= 7
    assert r["streak_basis"] == "evidence_window"
    assert r["last_error"] is None
    assert r["insufficient_history"] is False


def test_old_error_still_passes_after_threshold(monkeypatch):
    """An ERROR 8 days ago → 8 clean days → gate passes, measured."""
    _setup_fake_redis(monkeypatch, initial_events=[_ev(8 * 24, "error")])
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["phase_a_gate_3_pass"] is True
    assert r["streak_basis"] == "last_error"
    assert r["consecutive_clean_days"] >= 7


# ── (d) Dropped errors block certification ──────────────────────────────


def test_dropped_error_restarts_the_streak(monkeypatch):
    """R-F1510's breaker drops errors silently (self_improve.py:1823). A
    drop is a KNOWN HOLE in the record, so the clean streak cannot span it
    — even though the surviving ledger looks clean for 8 days."""
    from aria_service.intel import self_improve as si

    _setup_fake_redis(
        monkeypatch,
        initial_events=[_ev(8 * 24, "warning")],
        genesis_ts=time.time() - 8 * 86400,
    )
    monkeypatch.setattr(si, "_SI_ERRORS_DROPPED", 3, raising=False)
    monkeypatch.setattr(si, "_SI_LAST_DROP_TS", time.time() - 3600, raising=False)
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["errors_dropped_unrecorded"] == 3
    assert r["phase_a_gate_3_pass"] is False, (
        "a record with a known hole must not certify gate #3"
    )
    assert r["streak_basis"] == "dropped_evidence"
    assert "gate_blocked_reason" in r


def test_dropped_warning_does_not_block_the_gate(monkeypatch):
    """VERIFY-PASS-2 HIGH — the over-correction regression.

    error_log_handler mirrors ALL WARNING+ logs into record_error, and
    state_store saturation drops them routinely. If a dropped WARNING
    restarted the clean streak, gate #3 would be pinned False forever —
    R-F560's dishonesty inverted (a false FAIL instead of a false PASS),
    and a direct contradiction of the module's "WARNINGs never reset the
    streak" contract. Drive the REAL record_error with a warning.
    """
    from aria_service.intel import self_improve as si
    from aria_service.intel import error_streak as es

    _setup_fake_redis(
        monkeypatch,
        initial_events=[_ev(30 * 24, "warning")],
        genesis_ts=time.time() - 30 * 86400,
    )
    # Breaker open → the warning gets dropped.
    monkeypatch.setattr(
        si, "_record_error_cb_until", time.monotonic() + 60, raising=False
    )
    asyncio.run(si.record_error("log:warning", "dropped noise"))

    assert si._SI_LAST_DROP_TS == 0.0, (
        "a dropped WARNING must not mark a hole in an ERROR-only record"
    )
    r = asyncio.run(es.compute_error_streak())
    assert r["phase_a_gate_3_pass"] is True, (
        "a dropped warning must not block gate #3 — 30 days of clean "
        "evidence still stand"
    )
    assert r["streak_basis"] == "evidence_window"


def test_dropped_error_is_not_written_on_the_breaker_path(monkeypatch):
    """VERIFY-PASS-2 MED — the breaker's job is to skip the write ENTIRELY.
    Doing store I/O on the drop path defeats it and can re-enter it via the
    store's own error logs. The marker must be persisted on the READ path
    instead."""
    from aria_service.intel import self_improve as si

    writes: list = []
    from aria_service.intel import redis_store as rs

    _setup_fake_redis(monkeypatch, initial_events=[])

    async def spy_set(key, obj, ex=None, keepttl=False):
        writes.append(key)

    monkeypatch.setattr(rs, "set_json", spy_set)
    monkeypatch.setattr(
        si, "_record_error_cb_until", time.monotonic() + 60, raising=False
    )
    asyncio.run(si.record_error("log:error", "dropped boom"))

    assert not writes, (
        "record_error must do NO store I/O while the breaker is open — "
        f"wrote: {writes}"
    )
    assert si._SI_LAST_DROP_TS > 0  # still tracked in-process


def test_read_path_persists_the_process_local_drop(monkeypatch):
    """The read path is where the store is healthy — that is where the
    process-local drop marker becomes durable (surviving a restart)."""
    from aria_service.intel import self_improve as si
    from aria_service.intel import error_streak as es

    store = _setup_fake_redis(
        monkeypatch,
        initial_events=[_ev(8 * 24, "warning")],
        genesis_ts=time.time() - 8 * 86400,
    )
    monkeypatch.setattr(
        si, "_SI_LAST_DROP_TS", time.time() - 600, raising=False
    )

    asyncio.run(es.compute_error_streak())
    anchor = store.get(es.STREAK_ANCHOR_KEY) or {}
    assert anchor.get("last_drop_ts"), (
        "the read path must persist the process-local drop marker so it "
        "survives a restart"
    )


def test_durable_drop_marker_survives_restart(monkeypatch):
    """Verify-pass-1 #4: the process-local counter dies on restart, but the
    gate does NOT re-measure from boot (genesis/oldest_event outlive it).
    The durable last_drop_ts must therefore carry the hole across."""
    _setup_fake_redis(
        monkeypatch,
        initial_events=[_ev(8 * 24, "warning")],
        anchor={"last_drop_ts": time.time() - 2 * 3600},  # durable
        genesis_ts=time.time() - 8 * 86400,
    )
    # Simulate a restart: process-local counters are back to zero.
    from aria_service.intel import self_improve as si
    monkeypatch.setattr(si, "_SI_ERRORS_DROPPED", 0, raising=False)
    monkeypatch.setattr(si, "_SI_LAST_DROP_TS", 0.0, raising=False)
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["phase_a_gate_3_pass"] is False, (
        "a restart must not erase the knowledge that evidence is missing"
    )
    assert r["streak_basis"] == "dropped_evidence"


def test_old_drop_ages_out_and_stops_blocking(monkeypatch):
    """Verify-pass-1 #4 (other half): a drop must not block the gate
    FOREVER. Once it is older than the threshold it ages out, like any
    other error."""
    _setup_fake_redis(
        monkeypatch,
        initial_events=[_ev(20 * 24, "warning")],
        anchor={"last_drop_ts": time.time() - 10 * 86400},  # 10d ago
        genesis_ts=time.time() - 20 * 86400,
    )
    from aria_service.intel import error_streak as es

    r = asyncio.run(es.compute_error_streak())
    assert r["phase_a_gate_3_pass"] is True
    assert r["consecutive_clean_days"] >= 10


# ── Anchor write path ───────────────────────────────────────────────────


def test_record_streak_anchor_advances_high_water_mark(monkeypatch):
    store = _setup_fake_redis(monkeypatch, initial_events=[])
    from aria_service.intel import error_streak as es

    ok = asyncio.run(es.record_streak_anchor("log:error", message="boom"))
    assert ok is True
    anchor = store[es.STREAK_ANCHOR_KEY]
    assert anchor["last_error_ts"] > 0
    assert anchor["last_error"]["type"] == "log:error"


def test_record_streak_anchor_ignores_warnings(monkeypatch):
    """Only ERROR/CRITICAL move the anchor — warnings must never reset it."""
    store = _setup_fake_redis(monkeypatch, initial_events=[])
    from aria_service.intel import error_streak as es

    ok = asyncio.run(es.record_streak_anchor("log:warning", message="noise"))
    assert ok is False
    assert es.STREAK_ANCHOR_KEY not in store


def test_record_streak_anchor_never_regresses(monkeypatch):
    """An older error arriving late must not rewind the high-water mark."""
    now = time.time()
    _setup_fake_redis(
        monkeypatch,
        initial_events=[],
        anchor={"last_error_ts": now, "last_error": {"type": "log:error"}},
    )
    from aria_service.intel import error_streak as es

    ok = asyncio.run(
        es.record_streak_anchor("log:error", message="older", now=now - 9999)
    )
    assert ok is False


def test_anchor_failure_cannot_cascade_into_record_error(monkeypatch):
    """R-F2622 cascade-killer (F54 / R-F1400 / R-F2156 class).

    record_streak_anchor runs on record_error's SUCCESS path. If it logged
    at WARNING+, error_log_handler would mirror that back into record_error
    → ledger write succeeds → anchor fails → WARNING → unbounded recursion,
    which the R-F1510 breaker cannot stop (record_error keeps succeeding).
    Assert the anchor failure never emits WARNING+, and that the handler
    would skip it anyway.
    """
    import logging

    from aria_service.intel import redis_store as rs
    from aria_service.intel import error_streak as es
    from aria_service.intel import error_log_handler as elh

    async def boom(*a, **k):
        raise RuntimeError("anchor key unwritable")

    monkeypatch.setattr(rs, "get_json", boom)

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    cap = _Capture(level=logging.WARNING)
    lg = logging.getLogger("aria.error_streak")
    lg.addHandler(cap)
    try:
        asyncio.run(es.record_streak_anchor("log:error", message="boom"))
    finally:
        lg.removeHandler(cap)

    assert not records, (
        "anchor-write failure must not log at WARNING+ — it would recurse "
        f"through error_log_handler. Got: {[r.getMessage() for r in records]}"
    )
    # Defence in depth: the handler skips it even if a future edit re-raises
    # the level.
    assert any("streak-anchor" in s for s in elh._SKIP_SUBSTRINGS)


def test_record_streak_anchor_never_raises(monkeypatch):
    """It runs ON the error path — it must never mask the original error."""
    from aria_service.intel import redis_store as rs

    async def boom(*a, **k):
        raise RuntimeError("store wedged")

    monkeypatch.setattr(rs, "get_json", boom)
    from aria_service.intel import error_streak as es

    assert asyncio.run(es.record_streak_anchor("log:error")) is False


def test_anchor_fetch_failure_does_not_certify(monkeypatch):
    """VERIFY-PASS-1 CRITICAL. Under state_store saturation the anchor read
    times out. `get_json` SWALLOWS that and returns None (redis_store.py:275
    → get()), which is indistinguishable from 'no anchor exists' — so the
    gate would drop into the no-error-ever branch and certify a system whose
    last ERROR was minutes ago, then permanently erase the anchor.

    compute_error_streak must therefore use the STRICT reader (R-F1392) and
    fail honestly. This test pins that: a raising anchor read → pass=False,
    error reported, and NOTHING written back.
    """
    from aria_service.intel import redis_store as rs
    from aria_service.intel import self_improve as si
    from aria_service.intel import error_streak as es

    writes: list = []

    async def selective_strict(key):
        if key in (es.STREAK_ANCHOR_KEY, es.STREAK_GENESIS_KEY):
            raise RuntimeError("store read timed out")
        return []

    async def spy_set(key, obj, ex=None, keepttl=False):
        writes.append(key)

    monkeypatch.setattr(rs, "get_json_strict", selective_strict)
    monkeypatch.setattr(rs, "get_json", selective_strict)
    monkeypatch.setattr(rs, "set_json", spy_set)
    monkeypatch.setattr(si, "_SI_ERRORS_DROPPED", 0, raising=False)
    monkeypatch.setattr(si, "_SI_LAST_DROP_TS", 0.0, raising=False)

    r = asyncio.run(es.compute_error_streak())
    assert r["phase_a_gate_3_pass"] is False, (
        "a failed anchor read must never certify gate #3"
    )
    assert "anchor_fetch_failed" in r.get("error", "")
    assert not writes, (
        "a read that may have failed must never write the anchor — that is "
        "how a transient timeout permanently destroyed the high-water mark"
    )


def test_degraded_returns_keep_the_same_dict_shape(monkeypatch):
    """Verify-pass-1 #7: routes/aria.py:24312 returns this dict raw. The
    degraded early-returns must not omit fields the happy path defines, or
    a consumer KeyErrors exactly when the system is already unhealthy."""
    from aria_service.intel import redis_store as rs
    from aria_service.intel import error_streak as es

    async def boom(key):
        raise RuntimeError("ledger gone")

    monkeypatch.setattr(rs, "get_json", boom)
    monkeypatch.setattr(rs, "get_json_strict", boom)

    r = asyncio.run(es.compute_error_streak())
    for f in (
        "streak_basis", "clean_since", "insufficient_history",
        "ledger_saturated", "ledger_events_retained",
        "ledger_evidence_since", "errors_dropped_unrecorded",
        "phase_a_gate_3_pass", "consecutive_clean_days",
    ):
        assert f in r, f"degraded return dropped field {f!r}"


# ── Write-path integration: record_error → anchor ───────────────────────


def test_record_error_writes_anchor_end_to_end(monkeypatch):
    """Capability: the REAL write path (self_improve.record_error — what
    error_log_handler actually calls) must advance the durable anchor, so
    a later gate read measures from it."""
    from aria_service.intel import self_improve as si
    from aria_service.intel import error_streak as es

    store = _setup_fake_redis(monkeypatch, initial_events=[])
    monkeypatch.setattr(si, "_record_error_cb_until", 0.0, raising=False)

    asyncio.run(si.record_error("log:error", "real boom", file="m.py"))

    assert es.STREAK_ANCHOR_KEY in store, (
        "record_error must write the gate-3 anchor at ERROR time"
    )
    r = asyncio.run(es.compute_error_streak())
    assert r["phase_a_gate_3_pass"] is False
    assert r["streak_basis"] == "last_error"


def test_record_error_counts_drops_when_breaker_open(monkeypatch):
    """When the R-F1510 breaker is open the error is dropped — that must be
    COUNTED, because it is evidence we no longer have."""
    from aria_service.intel import self_improve as si

    _setup_fake_redis(monkeypatch, initial_events=[])
    monkeypatch.setattr(si, "_SI_ERRORS_DROPPED", 0, raising=False)
    monkeypatch.setattr(
        si, "_record_error_cb_until", time.monotonic() + 60, raising=False
    )

    asyncio.run(si.record_error("log:error", "dropped boom"))
    assert si._SI_ERRORS_DROPPED == 1
