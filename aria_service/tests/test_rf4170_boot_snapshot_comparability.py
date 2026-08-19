"""R-F4170 / C-184 - the boot regression guard fired on a snapshot the loader
had not finished filling, and an ERROR resets Phase A gate #3.

**Measured live 2026-08-19, from the error ledger:**

    [R-F251] STATE REGRESSION DETECTED - counters dropped >5% since previous
    boot: neural_neurons: 17742 -> 10378 (-41.5%)

Probed in the same machine minutes later:

    /api/aria/neural/stats -> loaded: True, neurons: 17743, edges: 159254

Nothing was lost. The graph had gained one neuron. `_log_boot_state` waits for
`knowledge_ready and neural_ready` but gives up at a 20-minute cap, and the
arithmetic is exact - machine started 12:03:12Z, snapshot stamped 12:23:08Z. The
cap was hit, so a 58%-loaded graph was compared against a complete one.

**R-F2951 met this exact race and fixed HALF of it.** It emits the string
"loading" for `neural_edges` when `nm_stats["loaded"]` is False, and its own
comment spells out why: "an early-boot get_stats reads total_edges=0 UNTIL
`loaded` flips True ... would reset the Phase A gate-#3 7-day clean streak on
EVERY deploy (same class as R-F2663/R-F2668)". `neural_neurons` is read from the
SAME dict on the line ABOVE and got no guard. That is the third time this class
has produced a false gate-#3 reset.

Per-counter guards are whack-a-mole. Whether the snapshot is comparable at all
is ONE fact, so it is decided once.

**The second-order bug is the dangerous one.** The partial snapshot is persisted
and becomes the next boot's baseline. Against a low baseline a genuine loss
reads as growth, so the false positive can BLIND the guard afterwards.

**And the guard must still be able to fail** (R-F3858) - a completed boot that
genuinely lost data still reports it. Only "could not measure" is silenced, and
it is silenced as UNKNOWN, never as clean.
"""
from __future__ import annotations

from aria_service.intel import boot_snapshot_diff as bsd


def _snap(**kw) -> dict:
    base = {
        "at": "2026-08-19T12:23:08Z",
        "knowledge_facts": 533_103,
        "ledger_signals": 1000,
        "rag_chunks": 32_000,
        "rag_facts": 5_000,
        "chat_audit_total": 900,
        "neural_neurons": 17_742,
        "neural_edges": 159_254,
        "state_keys": 681_000,
        "stores_ready": True,
    }
    base.update(kw)
    return base


# -- THE CAPABILITY TEST: the exact live false positive ----------------------

def test_the_live_false_regression_is_not_reported():
    """Replays the measured event: a 58%-loaded neural graph compared against a
    complete prior. Before R-F4170 this logged ERROR and reset gate #3."""
    partial = _snap(neural_neurons=10_378, neural_edges=0, stores_ready=False)
    complete_prior = _snap(neural_neurons=17_742)

    r = bsd.diff_boot_snapshots(partial, [complete_prior])

    assert r["comparable"] is False, (
        "an unfinished load was compared against a complete one - the guard "
        "will manufacture a -41.5% drop that did not happen"
    )
    assert r["reason"] == "current_snapshot_incomplete"
    assert r["drops"] == [], f"a false regression survived: {r['drops']}"


def test_could_not_measure_is_not_reported_as_clean():
    """Section 1's tri-state rule. `comparable=False` must be distinguishable
    from `comparable=True, drops=[]`, or the caller cannot tell an all-clear
    from a skipped check."""
    partial = _snap(stores_ready=False)
    skipped = bsd.diff_boot_snapshots(partial, [_snap()])
    clean = bsd.diff_boot_snapshots(_snap(), [_snap()])

    assert skipped["drops"] == clean["drops"] == []
    assert skipped["comparable"] is not clean["comparable"], (
        "a skipped check and a passing check are indistinguishable"
    )
    assert clean["reason"] == "compared"


# -- THE GUARD MUST STILL BE ABLE TO FAIL (R-F3858) --------------------------

def test_a_REAL_loss_on_a_completed_boot_is_still_reported():
    """The whole point of R-F251. Infinite memory (section 7) means no counter
    ever drops on a healthy restart, so a completed boot that lost 40% of its
    knowledge must still shout."""
    lost = _snap(knowledge_facts=300_000)
    r = bsd.diff_boot_snapshots(lost, [_snap()])

    assert r["comparable"] is True
    assert len(r["drops"]) == 1, r["drops"]
    assert "knowledge_facts" in r["drops"][0]
    assert "-43.7%" in r["drops"][0] or "43." in r["drops"][0]


def test_a_drop_inside_the_5pc_band_is_not_a_regression():
    """R-F251's threshold, preserved: small movement is not state loss."""
    r = bsd.diff_boot_snapshots(_snap(ledger_signals=970), [_snap()])
    assert r["comparable"] is True
    assert r["drops"] == []


def test_every_diffed_counter_can_still_trip_it():
    """A guard that only watches one field silently blesses the rest - the
    exact shape of the defect being fixed here."""
    for key in bsd.DIFFED_COUNTERS:
        prior = _snap()
        current = _snap(**{key: int(prior[key] * 0.5)})
        r = bsd.diff_boot_snapshots(current, [prior])
        assert len(r["drops"]) == 1, f"{key} cannot trip the guard: {r}"
        assert key in r["drops"][0]


# -- THE POISONED BASELINE (second-order) ------------------------------------

def test_a_partial_snapshot_is_not_used_as_the_baseline():
    """The dangerous direction. A partial snapshot persisted by an earlier slow
    boot would be a permanently LOW baseline, against which a genuine loss reads
    as growth - the false positive blinding the guard."""
    partial_prior = _snap(neural_neurons=10_378, stores_ready=False)
    good_prior = _snap(neural_neurons=17_742)
    # A real loss on this boot: neurons halved from the true 17,742.
    current = _snap(neural_neurons=8_800)

    r = bsd.diff_boot_snapshots(current, [partial_prior, good_prior])

    assert r["comparable"] is True
    assert r["baseline"] is good_prior, (
        "the guard diffed against a partially-loaded baseline"
    )
    assert any("neural_neurons" in d for d in r["drops"]), (
        "a real 50% neuron loss was hidden by a low baseline: %r" % (r["drops"],)
    )


def test_no_complete_baseline_is_unknown_not_clean():
    r = bsd.diff_boot_snapshots(_snap(), [_snap(stores_ready=False)])
    assert r["comparable"] is False
    assert r["reason"] == "no_complete_baseline"


# -- BACKWARD COMPATIBILITY --------------------------------------------------

def test_a_legacy_snapshot_still_works_as_a_baseline():
    """Snapshots written before this R-number carry no `stores_ready`. Treating
    them as unusable would silence a data-loss detector for several boots -
    trading a false alarm for a blind spot, the worse direction."""
    legacy = _snap()
    legacy.pop("stores_ready")
    assert bsd.is_complete(legacy) is True

    r = bsd.diff_boot_snapshots(_snap(knowledge_facts=100), [legacy])
    assert r["comparable"] is True
    assert any("knowledge_facts" in d for d in r["drops"])


def test_non_numeric_fields_are_skipped_not_guessed():
    """R-F2951 writes the string "loading"; the error paths write "err:...".
    Neither is a number and neither may be coerced into one."""
    current = _snap(neural_edges="loading", rag_chunks="err:timeout")
    r = bsd.diff_boot_snapshots(current, [_snap()])
    assert r["comparable"] is True
    assert r["drops"] == [], r["drops"]


def test_malformed_input_never_raises():
    """This runs on the boot path. A raise here would turn a diagnostic into an
    outage.

    `{}` is in the list deliberately, and it caught a hole in the first draft:
    `is_complete` read a missing `stores_ready` as "legacy, therefore complete",
    so an EMPTY snapshot was diffed, matched no numeric field, and returned
    `comparable=True, drops=[]` - a snapshot that failed to record anything
    reporting an all-clear. An absence rendering as a measurement is the exact
    shape section 1 records three Phase A gates being certified by."""
    for junk in (None, "nope", 5, [], {}):
        r = bsd.diff_boot_snapshots(junk, [_snap()])
        assert r["comparable"] is False
    for junk in (None, "nope", 5, {}):
        r = bsd.diff_boot_snapshots(_snap(), junk)
        assert r["comparable"] is False


# -- THE WIRING: main.py must actually use it --------------------------------

def test_main_records_readiness_and_delegates_the_decision():
    """A pure helper nothing calls is the producer-with-no-consumer defect
    (C-27, C-183). Assert the boot path both STAMPS readiness and asks this
    module, rather than keeping its own copy of the comparison."""
    from ._source_probe import repo_path

    src = repo_path("aria_service/main.py").read_text(
        encoding="utf-8", errors="replace")

    assert "stores_ready" in src, (
        "main.py never records whether the stores finished loading, so the "
        "snapshot cannot say whether it is comparable"
    )
    assert "boot_snapshot_diff" in src, (
        "main.py still carries its own copy of the R-F251 comparison"
    )
    assert "STATE REGRESSION DETECTED" in src, (
        "the regression alert was removed rather than gated - a guard that "
        "cannot fire is not a guard (R-F3858)"
    )

# -- select_baseline, called directly (section 3c) ---------------------------

def test_select_baseline_picks_the_newest_COMPLETE_snapshot():
    """Called directly, not just through diff_boot_snapshots: this is the
    function that decides what a boot is measured against, and getting it wrong
    is how a false positive turns into a permanent blind spot."""
    partial = _snap(neural_neurons=10_378, stores_ready=False)
    newest_good = _snap(neural_neurons=17_742, at="2026-08-19T11:00:00Z")
    older_good = _snap(neural_neurons=17_000, at="2026-08-18T11:00:00Z")

    assert bsd.select_baseline([partial, newest_good, older_good]) is newest_good
    assert bsd.select_baseline([newest_good, older_good]) is newest_good


def test_select_baseline_returns_None_rather_than_guessing():
    """No usable baseline is an honest None. Falling back to "the first thing
    in the list" would resurrect the poisoned-baseline bug."""
    assert bsd.select_baseline([]) is None
    assert bsd.select_baseline([_snap(stores_ready=False)]) is None
    assert bsd.select_baseline([{}, "junk", None]) is None
    assert bsd.select_baseline(None) is None
    assert bsd.select_baseline("not a list") is None


def test_select_baseline_skips_malformed_entries_without_raising():
    good = _snap()
    assert bsd.select_baseline(["junk", None, 7, good]) is good


# -- the outcome reaches the brain (section 21a) -----------------------------

def _capture(monkeypatch):
    calls: list = []
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_success",
                        lambda **kw: calls.append(("success", kw)))
    monkeypatch.setattr(ew, "wire_failure",
                        lambda **kw: calls.append(("failure", kw)))
    return calls


def test_a_skipped_check_is_reported_as_a_failure_not_a_success(monkeypatch):
    """The branch that used to be a bare console line. A detector that did not
    run must never register as one that ran and found nothing."""
    calls = _capture(monkeypatch)
    bsd.record_verdict(bsd.diff_boot_snapshots(_snap(stores_ready=False),
                                               [_snap()]))
    assert len(calls) == 1, calls
    kind, kw = calls[0]
    assert kind == "failure"
    assert kw["gap_type"] == "boot_regression_check_skipped"
    assert "not an all-clear" in kw["detail"]


def test_a_clean_comparison_wires_success(monkeypatch):
    calls = _capture(monkeypatch)
    bsd.record_verdict(bsd.diff_boot_snapshots(_snap(), [_snap()]))
    assert [c[0] for c in calls] == ["success"], calls


def test_a_real_regression_wires_a_failure_naming_the_counter(monkeypatch):
    calls = _capture(monkeypatch)
    bsd.record_verdict(
        bsd.diff_boot_snapshots(_snap(knowledge_facts=100), [_snap()]))
    assert len(calls) == 1
    kind, kw = calls[0]
    assert kind == "failure"
    assert kw["gap_type"] == "boot_state_regression"
    assert "knowledge_facts" in kw["detail"]


def test_record_verdict_never_raises_on_the_boot_path(monkeypatch):
    for junk in (None, {}, "nope", 5, {"comparable": True}):
        bsd.record_verdict(junk)

