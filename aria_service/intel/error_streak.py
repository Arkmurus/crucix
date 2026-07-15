"""R-F560 (2026-05-16) — error-streak counter for Phase A exit gate #3.

Phase A gate #3 (`platform_buildout_north_star.md`) requires:
    "0 fly ERROR logs in last 7 days"

Before R-F560 the gate was observed by eye against `fly logs | grep ERROR`
on each session start. R-F560 turns that into a queryable number by
reading the existing self_improve error ledger (already populated by
`error_log_handler.py`) and computing the consecutive-clean-day streak.

Surface:
    GET /api/aria/health/error-streak
    →  {
        "consecutive_clean_days": 3,
        "consecutive_clean_seconds": 261000,
        "last_error": {"type": "log:error", "timestamp": ..., "file": ...},
        "last_error_age_hours": 72.5,
        "phase_a_gate_3_pass": false,
        "phase_a_gate_3_threshold_days": 7,
        "window_errors_24h": 4,
        "window_errors_7d": 12,
    }

Capability:
    The dashboard panel can show "X / 7 clean days" without anyone
    having to manually `fly logs`.

Honesty:
    - Only ERROR + CRITICAL levels count toward the streak.
    - WARNINGs are reported in the windows but don't reset the streak.

R-F2622 (2026-07-15) — the streak is MEASURED, never assumed.
    R-F560 shipped a branch that read "no ERROR in the ledger" as
    "7 clean days, gate passes". That inverted the burden of proof: the
    ledger is a 200-slot ring buffer SHARED with warnings and carries a
    7-day TTL, so an empty/error-free ledger is equally consistent with
    (a) a genuinely clean week, (b) a warning burst that evicted a real
    ERROR, (c) a TTL'd-away key, or (d) errors dropped while the R-F1510
    breaker was open. R-F560 certified the gate in ALL four cases and
    hardcoded `consecutive_clean_days = 7` — a box booted 2h ago still
    claimed a 7-day clean streak. The gate that exists to measure Phase A
    honesty was itself unfalsifiable.

    The fix is structural, at the root rather than the readout:
      1. `record_streak_anchor` writes a durable, never-trimmed, TTL-less
         high-water mark of the last ERROR at record_error() time, so
         eviction and TTL can no longer erase the fact one occurred.
      2. The streak is computed from real timestamps: the later of the
         retained-ledger ERROR and the anchor ERROR. With no ERROR ever
         observed, cleanliness is claimed only back to the earliest point
         we hold continuous evidence for (oldest retained event / the
         genesis marker) — never further.
      3. Errors dropped unrecorded (breaker open / write failure) block
         certification outright: we know the record has holes.
      4. When nothing can be proven the gate reports pass=False +
         `insufficient_history`, and EARNS the pass as evidence accrues.

    Report fields added: `streak_basis` ("last_error" | "evidence_window"
    | "no_evidence"), `clean_since`, `insufficient_history`,
    `ledger_saturated`, `ledger_events_retained`, `ledger_evidence_since`,
    `errors_dropped_unrecorded`, `gate_blocked_reason`.

R-F969 (2026-05-28) — THIS endpoint is the CANONICAL gate-#3 measure, and
it is deploy-noise-immune by construction. Two classes of noise that look
like "fly ERROR logs" but do NOT (and must not) reset the streak:
  1. Fly-platform deploy-window errors — `[PM08]`/`[PR03]` proxy-lease +
     health-check-fail lines emitted while a machine is replaced during a
     redeploy. These are fly ORCHESTRATION events, not app logs; they never
     enter the app error ledger this endpoint reads, so a deploy cannot
     reset the streak here. (Hand-grepping `flyctl logs | grep -i error`
     DOES surface them — which is exactly why that hand method was retired
     in favour of this counter.)
  2. Operational WARNINGs — circuit-breaker source-down transitions
     (`HALF_OPEN → OPEN`, `CLOSED → OPEN`, brain_hook.py / circuit_breaker.py)
     and R-F703 event-loop-stall warnings all log at WARNING (`log:warning`),
     so they show in the windowed totals but never reset the clean streak.
The `noise_excluded` field below documents this so the gate stays trustworthy
without re-litigating "does a deploy break gate #3?" every session (it does
not).
"""
from __future__ import annotations
import logging
import time
from typing import Any

logger = logging.getLogger("aria.error_streak")

# Phase A gate #3 threshold from `platform_buildout_north_star.md`.
PHASE_A_GATE_3_DAYS = 7

# Severities that COUNT as a streak-resetting error. Anything else
# (warning, info, debug) is reported in the windowed totals but does
# not reset the clean streak.
_RESET_LEVEL_PREFIXES = ("log:error", "log:critical", "log:fatal")

# R-F2622 — durable streak anchor. The source ledger
# (`self_improve.ERROR_LOG_KEY`) is a 200-slot ring buffer SHARED by
# warnings and errors (self_improve.py:1792, trimmed `errors[-200:]`)
# with a 7-day TTL. Both properties destroy evidence:
#   - a warning burst >200 evicts a real ERROR out of the ledger, and
#   - the TTL expires the key entirely when writes go quiet,
# after which "no ERROR in the ledger" is indistinguishable from
# "no ERROR ever happened". R-F560 resolved that ambiguity by ASSUMING
# the gate passed, which made gate #3 self-certifying: absence of
# evidence was reported as proof of cleanliness.
#
# The anchor is the fix at the root: it is written at record_error()
# time (where the truth is known), holds only a high-water mark, is
# never trimmed, and carries NO TTL (§7 — no TTL on knowledge). The
# streak is then MEASURED from it rather than assumed.
#
# R-F2622 verify-pass-1: the anchor and the genesis marker live in
# SEPARATE keys on purpose. They have different writers — the anchor is
# written by record_error (error path), genesis by compute_error_streak
# (read path) — and a single key meant a read-modify-write race in which
# genesis's stale snapshot could clobber a last_error_ts written between
# its read and its write, silently erasing the very evidence the anchor
# exists to preserve. Separate keys make that race structurally
# impossible rather than merely unlikely.
STREAK_ANCHOR_KEY = "crucix:aria:error_streak:anchor"
STREAK_GENESIS_KEY = "crucix:aria:error_streak:genesis"

# R-F2622 verify-pass-1: coalesce anchor writes. The anchor is consumed
# at DAY granularity by a 7-day gate, so a write per error is pure cost —
# and worse, it is a read-modify-write on a hot key added to the error
# path, i.e. exactly the R-F2157 pattern that produces the 5s state_store
# timeouts which degrade this very anchor. During an error storm this cuts
# writes ~100x with no observable effect on the gate.
_ANCHOR_COALESCE_S = 60.0


def _is_reset_event(event: dict) -> bool:
    et = (event.get("type") or "").lower()
    return any(et.startswith(p) for p in _RESET_LEVEL_PREFIXES)


def is_reset_type(error_type: str) -> bool:
    """True when `error_type` is an ERROR/CRITICAL-class type that resets
    the gate-#3 streak. Shared with self_improve.record_error so the write
    path and the read path cannot drift on what counts as an error."""
    return _is_reset_event({"type": error_type})


async def record_streak_anchor(
    error_type: str,
    *,
    message: str = "",
    file: str = "",
    function: str = "",
    now: float | None = None,
) -> bool:
    """R-F2622 — advance the durable last-ERROR high-water mark.

    Called from `self_improve.record_error` for ERROR/CRITICAL events so the
    streak survives ring-buffer eviction and ledger TTL expiry. Returns True
    when the anchor was advanced. Never raises — the caller is the error path
    itself, so a failure here must not mask the original error.
    """
    if not is_reset_type(error_type):
        return False

    from . import redis_store as rs

    t_now = float(now if now is not None else time.time())
    try:
        # get_json_strict (R-F1392) — NOT get_json. get_json swallows a
        # store failure and returns None, which here would read as "no
        # error ever recorded" and let us overwrite a real high-water mark
        # with a fresh one. On a strict read failure we must NOT write.
        anchor = await rs.get_json_strict(STREAK_ANCHOR_KEY) or {}
        if not isinstance(anchor, dict):
            anchor = {}
        prior = float(anchor.get("last_error_ts") or 0.0)
        if t_now <= prior:
            return False
        # Coalesce: the mark already points at the last ~minute; re-writing
        # it changes nothing a 7-day gate can observe.
        if prior and (t_now - prior) < _ANCHOR_COALESCE_S:
            return False
        anchor["last_error_ts"] = t_now
        anchor["last_error"] = {
            "type": error_type,
            "message": (message or "")[:200],
            "file": file,
            "function": function,
            "timestamp": int(t_now),
        }
        # No `ex=` — §7: the anchor is knowledge and never expires.
        await rs.set_json(STREAK_ANCHOR_KEY, anchor)
        return True
    except Exception as e:
        # R-F2622 cascade-killer — same class as F54 / R-F1400 / R-F2156.
        # This runs on record_error's SUCCESS path (the ledger write already
        # landed), so if only the anchor key is unwritable a WARNING here
        # would be mirrored by error_log_handler back into record_error →
        # ledger write succeeds → anchor fails → WARNING → unbounded
        # recursion. The R-F1510 breaker can't stop it: record_error itself
        # keeps SUCCEEDING, so its failure counter never trips. Log at DEBUG
        # and let wire_failure carry it to the brain — that satisfies §21a
        # without re-entering the logging path. ("streak-anchor" is also in
        # error_log_handler._SKIP_SUBSTRINGS as defence in depth.)
        logger.debug(
            "error_streak: streak-anchor write failed (non-fatal) for %s "
            "(%s): %s", error_type, file or "?", e,
        )
        from .engine_wiring import wire_failure
        wire_failure(
            module="error_streak",
            detail=f"R-F2622 streak-anchor write failed for {error_type}: {e}",
            gap_type="engine_failure",
        )
        return False


async def record_drop_marker(now: float | None = None) -> bool:
    """R-F2622 verify-pass-1 — durably mark that an ERROR was dropped.

    `self_improve._SI_ERRORS_DROPPED` is process-local, so a restart erased
    the knowledge that evidence was missing and the gate would then certify
    a window containing a known hole. It also never aged out: one transient
    drop would block the gate forever.

    Storing the drop TIMESTAMP fixes both. It survives restart, and it is
    folded into the streak as if it were an error — so it ages out honestly
    after the threshold rather than blocking certification permanently.

    Best-effort: a drop usually means the store is already unhappy, so this
    write may fail. That is why the process-local counter is KEPT as well —
    belt and braces, each covering the other's blind spot.
    """
    from . import redis_store as rs

    t_now = float(now if now is not None else time.time())
    try:
        anchor = await rs.get_json_strict(STREAK_ANCHOR_KEY) or {}
        if not isinstance(anchor, dict):
            anchor = {}
        if float(anchor.get("last_drop_ts") or 0.0) >= t_now:
            return False
        anchor["last_drop_ts"] = t_now
        await rs.set_json(STREAK_ANCHOR_KEY, anchor)
        return True
    except Exception as e:
        # DEBUG, never WARNING — see the cascade note in record_streak_anchor.
        logger.debug(
            "error_streak: streak-anchor drop-marker write failed "
            "(non-fatal): %s", e,
        )
        return False


def _wire_ok() -> None:
    """§21a — success branch reaches the brain. Shared by every return
    path so an early return can't ship dark (the R-F560 vacuous-pass
    branch returned above the wire_success call and was invisible)."""
    from .engine_wiring import wire_success
    wire_success(
        module="error_streak",
        summary="Compute Error Streak",
        source_id="error_streak:R-F1001",
    )


async def _ensure_genesis(t_now: float, genesis_ts: float) -> None:
    """R-F2622 — persist the measurement-start marker, SET ONCE.

    Genesis records the moment the anchor began covering this system. It is
    written once, at `t_now`, and never moved earlier.

    Verify-pass-1 caught the earlier version dragging genesis backwards to
    `oldest_event_ts`. That was wrong in a way that mattered: it asserted
    anchor coverage over a period when the anchor did not yet exist. On the
    first run after deploy, an 8-day-old warning already in the live ledger
    would have become genesis permanently — and once those events aged out,
    genesis alone would sustain an 8-day clean claim backed by no surviving
    evidence and no anchor. That is R-F560's false pass with extra steps.

    The ledger's evidence is still credited (compute takes
    `min(genesis_ts, oldest_event_ts)`) — but only for as long as the
    ledger actually holds the events proving it.

    Own key (not the anchor's) so this read-path writer can never clobber
    the error-path's last_error_ts.
    """
    from . import redis_store as rs

    if genesis_ts:
        return  # set once — never moved
    try:
        # No `ex=` — §7: the genesis marker is knowledge and never expires.
        await rs.set_json(STREAK_GENESIS_KEY, {"genesis_ts": float(t_now)})
    except Exception as e:
        # DEBUG not WARNING: this is the read path, but a WARNING here would
        # still pollute the very ring buffer the gate is trying to measure.
        # wire_failure carries it to the brain instead (§21a).
        logger.debug(
            "error_streak: genesis marker write failed (non-fatal) "
            "(ts=%s): %s", t_now, e,
        )
        from .engine_wiring import wire_failure as _wf
        _wf(
            module="error_streak",
            detail=f"R-F2622 genesis marker write failed: {e}",
            gap_type="engine_failure",
        )


async def compute_error_streak(
    *,
    threshold_days: int = PHASE_A_GATE_3_DAYS,
    now: float | None = None,
) -> dict[str, Any]:
    """Compute the consecutive error-free-day streak from the live
    error ledger. Returns the dashboard-ready dict shape.

    Never raises — failures degrade to honest "unknown" output so the
    dashboard renders rather than 500-ing.
    """
    from . import redis_store as rs
    from . import self_improve as si

    t_now = float(now if now is not None else time.time())
    out: dict[str, Any] = {
        "consecutive_clean_days": None,
        "consecutive_clean_seconds": None,
        "last_error": None,
        "last_error_age_hours": None,
        "phase_a_gate_3_pass": False,
        "phase_a_gate_3_threshold_days": threshold_days,
        "window_errors_24h": 0,
        "window_errors_7d": 0,
        "level_breakdown_7d": {},
        "as_of": int(t_now),
        # R-F2622 verify-pass-1 — seed EVERY field here so the two degraded
        # early-returns (ledger_fetch_failed / anchor_fetch_failed) return
        # the same dict SHAPE as the happy paths. routes/aria.py:24312 hands
        # this dict straight to the client; a consumer reading a field that
        # only exists on the happy path would KeyError exactly when the
        # system is already unhealthy.
        "streak_basis": None,
        "clean_since": None,
        "insufficient_history": None,
        "ledger_saturated": None,
        "ledger_events_retained": None,
        "ledger_evidence_since": None,
        "errors_dropped_unrecorded": 0,
        "gate_blocked_reason": None,
        # R-F969 — make the deploy-noise immunity explicit + self-documenting.
        "noise_excluded": (
            "fly-platform deploy-window errors (PM08/PR03 proxy-lease + "
            "health-check-fail during machine replacement) are orchestration "
            "events, not app logs, so they never reach this ledger; "
            "operational WARNINGs (circuit-breaker source-down, R-F703 "
            "event-loop stalls) log at WARNING and never reset the streak. "
            "Only app-level ERROR/CRITICAL reset it."
        ),
    }

    try:
        events: list[dict] = await rs.get_json(si.ERROR_LOG_KEY) or []
    except Exception as e:
        logger.warning("R-F560 error_streak: ledger fetch failed: %s", e)
        out["error"] = f"ledger_fetch_failed:{type(e).__name__}"
        # R-F2622 §21a — the failure branch must reach the brain too.
        from .engine_wiring import wire_failure as _wf
        _wf(
            module="error_streak",
            detail=f"gate-3 ledger fetch failed: {type(e).__name__}: {e}",
            gap_type="engine_failure",
        )
        return out

    # ── R-F2622: durable anchor — survives eviction + TTL ─────────────
    # get_json_strict (R-F1392), NOT get_json. This is the single most
    # important line in the fix. get_json swallows a store failure and
    # returns None (redis_store.py:275 → get()), which is indistinguishable
    # from "no anchor exists" — so a routine 5s read timeout under
    # state_store saturation would drop us into the no-error-ever branch and
    # certify the gate on a system whose last ERROR was ten minutes ago.
    # get_strict raises StoreReadError instead, so absent and failed stay
    # distinguishable and the gate fails honestly.
    anchor: dict = {}
    genesis_ts: float = 0.0
    try:
        anchor = await rs.get_json_strict(STREAK_ANCHOR_KEY) or {}
        if not isinstance(anchor, dict):
            anchor = {}
        _g = await rs.get_json_strict(STREAK_GENESIS_KEY) or {}
        genesis_ts = float(_g.get("genesis_ts") or 0.0) if isinstance(
            _g, dict
        ) else 0.0
    except Exception as e:
        # An unreadable anchor must NOT silently downgrade to the old
        # "assume clean" behaviour — report unknown and fail the gate.
        logger.warning(
            "R-F2622 error_streak: anchor fetch failed (%s): %s",
            type(e).__name__, e,
        )
        out["error"] = f"anchor_fetch_failed:{type(e).__name__}"
        from .engine_wiring import wire_failure as _wf
        _wf(
            module="error_streak",
            detail=f"gate-3 anchor fetch failed: {type(e).__name__}: {e}",
            gap_type="engine_failure",
        )
        return out

    # ── Windowed totals (counts include WARNING+, not just ERROR) ─────
    cutoff_24h = t_now - 86400
    cutoff_7d = t_now - 7 * 86400
    breakdown: dict[str, int] = {}
    oldest_event_ts: float | None = None
    for ev in events:
        ts = float(ev.get("timestamp") or 0)
        et = (ev.get("type") or "").lower()
        if ts > 0 and (oldest_event_ts is None or ts < oldest_event_ts):
            oldest_event_ts = ts
        if ts >= cutoff_24h:
            out["window_errors_24h"] += 1
        if ts >= cutoff_7d:
            out["window_errors_7d"] += 1
            breakdown[et] = breakdown.get(et, 0) + 1
    out["level_breakdown_7d"] = breakdown

    # R-F2622 — the ledger keeps only the NEWEST `MAX_ERRORS` events, so
    # anything evicted is strictly OLDER than `oldest_event_ts`. That makes
    # "no ERROR among the retained events" a PROOF of cleanliness over
    # [oldest_event_ts, now] — and nothing before it. Saturation doesn't
    # invalidate the window, it just shortens it, so surface it.
    out["ledger_events_retained"] = len(events)
    out["ledger_saturated"] = len(events) >= si.MAX_ERRORS
    out["ledger_evidence_since"] = (
        int(oldest_event_ts) if oldest_event_ts else None
    )

    # R-F2622 — errors dropped while the R-F1510 breaker was open never
    # reached the ledger at all (self_improve.py:1823). We KNOW evidence is
    # missing, so the gate must not certify clean on it.
    dropped = int(getattr(si, "_SI_ERRORS_DROPPED", 0) or 0)
    out["errors_dropped_unrecorded"] = dropped

    # ── Streak: most recent ERROR/CRITICAL event ──────────────────────
    last_reset_ev: dict | None = None
    last_reset_ts = -1.0
    for ev in events:
        if not _is_reset_event(ev):
            continue
        ts = float(ev.get("timestamp") or 0)
        if ts > last_reset_ts:
            last_reset_ts = ts
            last_reset_ev = ev

    # ── R-F2622: MEASURE the streak; never assume it ──────────────────
    # The durable anchor is the high-water mark of the last ERROR ever
    # recorded. Take the later of (retained-ledger ERROR, anchor ERROR) so
    # an evicted/TTL'd error still counts against the streak.
    anchor_last_ts = float(anchor.get("last_error_ts") or 0.0)
    anchor_last_ev = anchor.get("last_error") if isinstance(
        anchor.get("last_error"), dict
    ) else None

    if last_reset_ts > anchor_last_ts:
        effective_ts, effective_ev = last_reset_ts, {
            "type": last_reset_ev.get("type"),
            "message": (last_reset_ev.get("message") or "")[:200],
            "file": last_reset_ev.get("file"),
            "function": last_reset_ev.get("function"),
            "timestamp": int(last_reset_ts),
        }
    elif anchor_last_ts > 0:
        effective_ts, effective_ev = anchor_last_ts, anchor_last_ev
    else:
        effective_ts, effective_ev = 0.0, None

    # R-F2622 verify-pass-1 — a DROPPED error is knowledge that evidence is
    # missing, so treat it exactly like an error for streak purposes: the
    # clean window cannot start before the last known hole. This replaces
    # the boolean "block forever" gate — it survives a restart (durable
    # last_drop_ts) AND ages out honestly once the threshold has passed.
    durable_drop_ts = float(anchor.get("last_drop_ts") or 0.0)
    proc_drop_ts = float(getattr(si, "_SI_LAST_DROP_TS", 0.0) or 0.0)
    drop_ts = max(durable_drop_ts, proc_drop_ts)
    # Verify-pass-2: persist the process-local marker HERE, on the read
    # path, rather than at drop time. A drop happens precisely when the
    # store is unhappy (breaker open / write failed), so writing then both
    # defeats the R-F1510 breaker — whose stated job is to "skip the write
    # entirely" — and risks re-entering it via the store's own error logs.
    # By the time someone polls this endpoint the store is usually healthy,
    # so the marker becomes durable (surviving restart) at zero risk.
    if proc_drop_ts > durable_drop_ts:
        await record_drop_marker(proc_drop_ts)
    if drop_ts > effective_ts:
        out["streak_basis"] = "dropped_evidence"
        out["last_error"] = None
        out["last_error_age_hours"] = None
        clean_since = drop_ts
        out["gate_blocked_reason"] = (
            "an ERROR was dropped unrecorded (R-F1510 breaker open or a "
            "ledger write failure), so the clean record has a known hole "
            "at that moment — the streak restarts from there."
        )
    elif effective_ts > 0:
        # A real ERROR is known — the streak starts there. Provable.
        clean_since = effective_ts
        out["streak_basis"] = "last_error"
        # Verify-pass-1 #9: never report basis=last_error with last_error
        # None — synthesise from the timestamp we do have.
        out["last_error"] = effective_ev or {
            "type": "log:error",
            "message": "(anchor: detail unavailable)",
            "timestamp": int(effective_ts),
        }
    else:
        # No ERROR has EVER been observed. We can only claim cleanliness
        # back to the earliest point we hold continuous evidence for:
        # the oldest retained ledger event, or genesis (the moment the
        # anchor began covering this system) — whichever is EARLIER, since
        # both are points from which we were demonstrably watching. Both
        # windows end at `now`, so their union starts at the min.
        await _ensure_genesis(t_now, genesis_ts)
        candidates = [c for c in (genesis_ts, oldest_event_ts) if c]
        if not candidates:
            # Nothing to measure from at all: no errors, no events, no
            # genesis. This is the exact case R-F560 called "clean". It is
            # not clean — it is UNKNOWN. Genesis was just seeded above, so
            # the gate now EARNS the pass as real days accrue.
            out["streak_basis"] = "no_evidence"
            out["consecutive_clean_seconds"] = 0
            out["consecutive_clean_days"] = 0
            out["insufficient_history"] = True
            out["phase_a_gate_3_pass"] = False
            out["last_error"] = None
            out["last_error_age_hours"] = None
            _wire_ok()
            return out
        clean_since = min(candidates)
        out["streak_basis"] = "evidence_window"
        out["last_error"] = None
        out["last_error_age_hours"] = None

    clean_seconds = max(0.0, t_now - clean_since)
    out["consecutive_clean_seconds"] = int(clean_seconds)
    out["consecutive_clean_days"] = int(clean_seconds // 86400)
    out["clean_since"] = int(clean_since)
    if effective_ts > 0:
        out["last_error_age_hours"] = round(clean_seconds / 3600.0, 1)

    proven = out["consecutive_clean_days"] >= threshold_days
    out["insufficient_history"] = (
        out["streak_basis"] in ("evidence_window", "dropped_evidence")
        and not proven
    )
    # The streak already restarts at the last dropped error (above), so a
    # drop can no longer certify a window containing a known hole — and it
    # ages out rather than blocking the gate forever.
    out["phase_a_gate_3_pass"] = bool(proven)
    if proven:
        # Seeded to None above — reset rather than pop, so the dict SHAPE
        # stays identical across every return path (verify-pass-2 #3).
        out["gate_blocked_reason"] = None
    # R-F1001 — wire to brain (R-F2622: via _wire_ok so every return path,
    # including the early no_evidence one, wires identically).
    _wire_ok()

    return out

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
