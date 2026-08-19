"""R-F4170 (C-184) — the ONE decision about whether two boot snapshots are
comparable, extracted from `main.py::_log_boot_state` so it can be tested.

**Why this module exists.** R-F251's regression guard diffs this boot's counter
snapshot against the previous one and logs `ERROR` when any counter fell more
than 5%. An ERROR resets Phase A exit gate #3 ("0 fly ERRORs / 7 days"), so a
guard that can fire on a snapshot the loader had not finished filling makes the
gate unclosable — the same class as R-F2663, R-F2668 and R-F2951.

**Measured live 2026-08-19:**

    [R-F251] STATE REGRESSION DETECTED — counters dropped >5% since previous
    boot: neural_neurons: 17742 -> 10378 (-41.5%)

Probed in the same machine minutes later: `loaded: True, neurons: 17743`. The
graph had lost nothing — it had gained one. `_log_boot_state` waits for
`knowledge_ready and neural_ready` but gives up after a 20-minute cap, and the
timing is exact: the machine started at 12:03:12Z and the snapshot was stamped
12:23:08Z. The cap was reached, so the snapshot recorded a graph that was 58%
loaded and the diff compared it against a complete one.

R-F2951 had already met this exact race and fixed it for `neural_edges`, by
emitting the string `"loading"` so the numeric diff skips the field. Its sibling
`neural_neurons` is read from the SAME `nm_stats` dict on the line above and was
left uncovered. Per-counter guards are whack-a-mole; the comparability of the
whole snapshot is one fact, so it is decided once, here.

**The second-order bug, which is the dangerous one.** A partial snapshot is
persisted and becomes the NEXT boot's baseline. Diffing against a low baseline
cannot detect a real drop — so the false positive does not merely cry wolf, it
can BLIND the guard afterwards. `select_baseline` therefore walks back to the
most recent snapshot that was complete, rather than trusting index 1.

**This guard must still be able to fail** (R-F3858). A boot that finished
loading and genuinely lost data still reports drops; only "we could not measure"
is silenced, and it is silenced as *unknown*, never as *clean* (§1's tri-state
rule: could-not-measure is not measured-and-passed).
"""
from __future__ import annotations

# The counters R-F251 compares. Kept here beside the decision that uses them.
DIFFED_COUNTERS = (
    "knowledge_facts", "ledger_signals", "rag_chunks", "rag_facts",
    "chat_audit_total", "neural_neurons", "neural_edges", "state_keys",
)

# R-F251's threshold: a counter that fell more than 5% is state loss.
_DROP_FLOOR = 0.95


def is_complete(snapshot) -> bool:
    """True when this snapshot's counters are comparable.

    A snapshot written before R-F4170 carries no `stores_ready` key. Those are
    treated as COMPLETE: they are the historical norm, almost all were taken on
    a finished load, and treating them as unusable would silence the guard for
    every boot until two new snapshots exist — trading a false alarm for a blind
    spot, which is the worse direction for a data-loss detector.
    """
    if not isinstance(snapshot, dict):
        return False
    # A snapshot that recorded no counter at all is not a baseline, whatever
    # its readiness says. Without this the "legacy" branch below read `{}` as
    # complete, so a snapshot that failed to record ANYTHING was diffed,
    # matched no numeric field and returned "comparable, no drops" - an
    # absence rendered as an all-clear.
    if not any(k in snapshot for k in DIFFED_COUNTERS):
        return False
    ready = snapshot.get("stores_ready")
    if ready is None:
        return True          # legacy snapshot - see docstring
    return bool(ready)


def select_baseline(prior_snapshots) -> dict | None:
    """The most recent PRIOR snapshot that is safe to diff against.

    Deliberately not "index 1". A partial snapshot persisted by an earlier slow
    boot would otherwise become a permanently low baseline, against which a
    genuine loss reads as growth.
    """
    if not isinstance(prior_snapshots, (list, tuple)):
        return None
    for snap in prior_snapshots:
        if isinstance(snap, dict) and is_complete(snap):
            return snap
    return None


def diff_boot_snapshots(current, prior_snapshots) -> dict:
    """Compare `current` against the newest complete snapshot in
    `prior_snapshots` (newest first).

    Returns ``{"comparable": bool, "reason": str, "drops": list[str],
    "baseline": dict | None}``.

    `comparable=False` means COULD NOT MEASURE. The caller must report that as
    a warning, never as a regression and never as an all-clear.
    """
    out: dict = {"comparable": False, "reason": "", "drops": [], "baseline": None}

    if not isinstance(current, dict):
        out["reason"] = "current_snapshot_malformed"
        return out

    if not is_complete(current):
        # THE FIX. The stores had not finished loading, so every counter here
        # is a lower bound on itself. Comparing it to a complete snapshot
        # manufactures a drop that did not happen.
        out["reason"] = "current_snapshot_incomplete"
        return out

    baseline = select_baseline(prior_snapshots)
    if baseline is None:
        out["reason"] = "no_complete_baseline"
        return out

    out["baseline"] = baseline
    out["comparable"] = True
    out["reason"] = "compared"

    drops: list[str] = []
    for key in DIFFED_COUNTERS:
        cur = current.get(key)
        prv = baseline.get(key)
        # Non-numeric on either side means the field was not measured (R-F2951
        # writes "loading", the error paths write "err:..."). Skipped, not
        # guessed.
        if isinstance(cur, bool) or isinstance(prv, bool):
            continue
        if not isinstance(cur, (int, float)) or not isinstance(prv, (int, float)):
            continue
        if prv > 0 and cur < prv * _DROP_FLOOR:
            drop_pct = round((1 - cur / prv) * 100, 1)
            drops.append(f"{key}: {prv} → {cur} (-{drop_pct}%)")

    out["drops"] = drops
    return out

# -- §21a WIRING -------------------------------------------------------------
#
# The functions above are pure by design (they are the DECISION, and a decision
# that touches the store cannot be tested cheaply). The outcome still has to
# reach the brain, and the branch that most needs to is the one that used to be
# a bare console line: "the regression check did not run". §21a is explicit
# that a log is DARK, and a data-loss detector that silently skipped a boot is
# exactly the kind of thing ARIA must be able to notice about herself (§25).
#
# Called ONCE per boot from main.py, so it cannot flood the 500-slot ledger.


def record_verdict(verdict) -> None:
    """Report a `diff_boot_snapshots` outcome to the brain. Never raises.

    Three outcomes, three signals, because they mean different things:
      * compared, no drops  -> success. The check RAN and found nothing.
      * compared, drops     -> failure. State loss (main.py also absorbs the
                               detail; this is the health metric half).
      * not comparable      -> failure, distinct gap_type. NOT a data loss —
                               the DETECTOR did not run. Recording it as a
                               success would be the absence-as-measurement
                               mistake this whole module exists to remove.
    """
    try:
        from .engine_wiring import wire_success, wire_failure
    except Exception:
        return
    try:
        v = verdict if isinstance(verdict, dict) else {}
        reason = str(v.get("reason") or "unknown")
        if not v.get("comparable"):
            wire_failure(
                module="boot_snapshot_diff",
                detail=(
                    f"boot state-regression check did not run ({reason}). "
                    "The counters were not comparable, so NO claim is made "
                    "either way — this is not an all-clear."
                ),
                gap_type="boot_regression_check_skipped",
                source="boot_snapshot_diff:record_verdict",
            )
            return
        drops = v.get("drops") or []
        if drops:
            wire_failure(
                module="boot_snapshot_diff",
                detail="boot state regression: " + "; ".join(str(d) for d in drops),
                gap_type="boot_state_regression",
                source="boot_snapshot_diff:record_verdict",
            )
            return
        wire_success(
            module="boot_snapshot_diff",
            summary="boot state-regression check ran; no counter dropped",
            source_id="boot_snapshot_diff:record_verdict",
        )
    except Exception:
        # Boot path. A wiring failure must never take the boot with it.
        return
