"""R-F3694..R-F3697 — CAPABILITY: the Phase A gates measure what they claim.

360 DD sweep, 2026-08-04. Four open gates, and three of them were open (or
passing) because of defects in their OWN MEASUREMENT rather than the state of
the system.

  R-F3694  gate #2 — `_grade_researched_cell` is tri-state (`bool | None`) and
           `None` means UNMEASURED. Two callers in student.py passed it straight
           into `correct=`, where `1.0 if correct else 0.0` (student.py) and
           `if correct: ... else:` recorded it as a MISS. update_regional_mastery
           has no HARD_FLOOR clamp, so the EWMA decayed without bound:
           0.5·(1−0.03)^n = 0.003 at n≈168 — the live heatmap floor exactly.
  R-F3695  gate #3 — `no_symbolic_rule` gaps were fingerprinted on the raw
           QUESTION, so the 1h dedupe never fired, the writer saturated,
           `lpush` raised, and record_gap's ERROR reset the 7-day clean streak.
  R-F3696  gate #1 — `avg_grounded_rate` falls back to the LIFETIME average but
           `rate_sample_size` reported the (empty) 24h count, so the consumer's
           min-sample guard discarded a well-sampled signal as
           `insufficient_samples_n0`, zeroing 45% of the composite.
  R-F3697  gate #4 — could not fail: `_load` swallowed store errors and returned
           four code-resident seeds, all hardcoded `investigation_status:
           "closed"`.

NOTE these fixes do NOT make any gate easier. R-F3694 and R-F3695 make gates #2
and #3 measure honestly (the floor may move either way); R-F3696 measures MORE
of the composite; R-F3697 makes gate #4 able to fail for the first time.

Run: python -m pytest aria_service/tests/test_rf3694_3697_phase_a_gate_honesty.py -v
"""
from __future__ import annotations

import asyncio

import pytest

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


# ══════════════════════════════════════════════════════════════════════════
# R-F3694 — gate #2: an unmeasured cell is never a miss
# ══════════════════════════════════════════════════════════════════════════

def test_update_regional_mastery_refuses_a_non_bool(monkeypatch):
    """The structural half: no caller can coerce None into a wrong answer."""
    from aria_service.intel import student

    touched = {}

    async def _fake_load():
        return touched

    monkeypatch.setattr(student, "_load_regional_mastery", _fake_load)
    monkeypatch.setattr(student, "_regional_cache", {}, raising=False)

    asyncio.run(student.update_regional_mastery(
        ["compliance"], ["lusophone"], correct=None, weight=0.3,
    ))
    assert touched == {}, (
        "correct=None must be REFUSED, not coerced — `obs = 1.0 if correct "
        "else 0.0` recorded an unmeasured cell as a miss, and this axis has no "
        "HARD_FLOOR clamp so it decays to 0"
    )


def test_update_mastery_refuses_a_non_bool(monkeypatch):
    from aria_service.intel import student

    saved = {}

    async def _fake_load():
        return saved

    monkeypatch.setattr(student, "_load_mastery", _fake_load)
    asyncio.run(student.update_mastery(["compliance"], correct=None, weight=0.3))
    assert saved == {}, "correct=None must be refused on the topic axis too"


@pytest.mark.parametrize("bad", [None, "true", 1, 0, 0.5, [], object()])
def test_only_a_real_bool_is_accepted(bad, monkeypatch):
    from aria_service.intel import student

    rm = {}

    async def _fake_load():
        return rm

    monkeypatch.setattr(student, "_load_regional_mastery", _fake_load)
    monkeypatch.setattr(student, "_regional_cache", {}, raising=False)
    asyncio.run(student.update_regional_mastery(["compliance"], ["lusophone"],
                                                correct=bad, weight=0.3))
    assert rm == {}, f"correct={bad!r} ({type(bad).__name__}) must be refused"


def test_a_real_grade_still_updates(monkeypatch):
    """The guard must not break honest grading."""
    from aria_service.intel import student

    rm: dict = {}

    async def _fake_load():
        return rm

    monkeypatch.setattr(student, "_load_regional_mastery", _fake_load)
    monkeypatch.setattr(student, "_regional_cache", rm, raising=False)
    monkeypatch.setattr(student, "_mark_regional_dirty", lambda: None)

    async def _noflush(*a, **k):
        return False

    monkeypatch.setattr(student, "_maybe_flush_regional", _noflush)
    asyncio.run(student.update_regional_mastery(["compliance"], ["lusophone"],
                                                correct=True, weight=0.3))
    assert "compliance:lusophone" in rm
    assert rm["compliance:lusophone"]["score"] > student.INITIAL_MASTERY


def test_the_decay_arithmetic_that_produced_0_003():
    """Documents WHY an unmeasured-as-miss stream is catastrophic here."""
    from aria_service.intel.student import INITIAL_MASTERY

    score = INITIAL_MASTERY
    alpha = min(0.3, 0.1 * 0.3)  # weight=0.3, as both student.py call sites use
    n = 0
    while score > 0.003 and n < 10_000:
        score = score + alpha * (0.0 - score)  # obs=0.0 — a "miss"
        n += 1
    assert n < 300, (
        f"only {n} unmeasured-as-miss observations decay a cell from "
        f"{INITIAL_MASTERY} to the live floor of 0.003 — with no HARD_FLOOR "
        f"clamp on this axis"
    )


def test_the_grader_is_genuinely_tri_state():
    """If this ever becomes bool-only, the guards above are redundant."""
    import inspect
    from aria_service.autonomous import tasks

    sig = inspect.signature(tasks._grade_researched_cell)
    assert "None" in str(sig.return_annotation) or sig.return_annotation is not bool, (
        "_grade_researched_cell must stay tri-state: None = UNMEASURED"
    )


# ══════════════════════════════════════════════════════════════════════════
# R-F3695 — gate #3: the gap firehose that reset the streak
# ══════════════════════════════════════════════════════════════════════════

def test_no_symbolic_rule_gaps_collapse_to_one_fingerprint():
    from aria_service.intel.capability_gaps import _gap_fingerprint

    a = _gap_fingerprint("no_symbolic_rule",
                         "No rule matched: What are the most important compliance "
                         "facts and recent developments for lusophone?")
    b = _gap_fingerprint("no_symbolic_rule",
                         "No rule matched: What are the most important procurement "
                         "facts and recent developments for central_africa?")
    assert a == b, (
        "every distinct question produced a distinct fingerprint, so the 1h "
        "dedupe never fired; the student loop then generated thousands of "
        "unique gaps per day and saturated the single store writer"
    )


def test_actionable_gap_types_still_dedupe_per_detail():
    """The collapse must NOT leak into gap types the coder acts on."""
    from aria_service.intel.capability_gaps import _gap_fingerprint

    a = _gap_fingerprint("module_bug", "foo.py:12 exploded")
    b = _gap_fingerprint("module_bug", "bar.py:99 exploded")
    assert a != b, (
        "module_bug is actionable per-site — collapsing it would merge distinct "
        "bugs into one gap and hide work from the coder"
    )
    assert _gap_fingerprint("missing_capability", "x") != \
           _gap_fingerprint("missing_capability", "y")


def test_the_collapsed_type_is_narrow():
    """Guard against future over-collapsing."""
    from aria_service.intel import capability_gaps as cg

    assert cg._CLASS_FINGERPRINT_GAP_TYPES == frozenset({"no_symbolic_rule"}), (
        "only genuinely unbounded-cardinality TELEMETRY types belong here; "
        "adding an actionable type would hide real work"
    )


def test_the_error_level_was_not_downgraded():
    """We fixed the CARDINALITY, not the reporting — that would clamp gate #3."""
    import inspect
    from aria_service.intel import capability_gaps as cg

    src = function_source(cg, "record_gap")
    assert "logger.error" in src, (
        "a dropped capability gap must still be an ERROR: if drops persist "
        "after the fingerprint fix the store is genuinely unhealthy and gate #3 "
        "should say so. Making the gate pass by logging less is closing it by "
        "measuring less (CLAUDE.md §1)."
    )


# ══════════════════════════════════════════════════════════════════════════
# R-F3696 — gate #1: the window mismatch
# ══════════════════════════════════════════════════════════════════════════

def _stats(data_source, rate, n24, nall):
    return {
        "avg_grounded_rate": rate,
        "rolling_grounded_rate": rate,
        "rate_sample_size": n24,
        "effective_sample_size": (n24 if data_source == "24h_window"
                                  else (nall if data_source == "lifetime_fallback" else 0)),
        "data_source": data_source,
        "lifetime_sample_size": nall,
        "by_verdict": {},
    }


def test_a_lifetime_fallback_is_not_discarded_as_undersampled(monkeypatch):
    from aria_service.intel import autonomy_scorer, source_verifier

    async def _fake():
        # The live shape: quiet 24h window, hundreds of lifetime entries.
        return _stats("lifetime_fallback", 0.82, 0, 412)

    monkeypatch.setattr(source_verifier, "get_verification_stats", _fake)
    out = asyncio.run(autonomy_scorer.compute_composite())
    details = out.get("details") or {}
    assert details.get("verification_samples") == 412, (
        f"expected the LIFETIME sample size to be used with a lifetime value; "
        f"got {details.get('verification_samples')} — judging a value from one "
        f"window by a count from another is what zeroed 45% of the composite"
    )
    assert "insufficient_samples" not in str(details.get("verification_source")), (
        "a rate backed by 412 entries must not be discarded as under-sampled"
    )


def test_a_genuinely_undersampled_24h_signal_is_still_discarded(monkeypatch):
    """The guard must keep working — this fix measures more, not less."""
    from aria_service.intel import autonomy_scorer, source_verifier

    async def _fake():
        return _stats("24h_window", 0.9, 2, 2)

    monkeypatch.setattr(source_verifier, "get_verification_stats", _fake)
    out = asyncio.run(autonomy_scorer.compute_composite())
    details = out.get("details") or {}
    assert "insufficient_samples" in str(details.get("verification_source")), (
        "2 samples in the 24h window is genuinely under-sampled and must still "
        "be rejected — R-F1907's guard stays"
    )


def test_source_verifier_reports_a_matching_sample_size():
    """The producer side of the contract."""
    import inspect
    from aria_service.intel import source_verifier

    src = module_source(source_verifier)
    assert '"effective_sample_size"' in src, (
        "get_verification_stats must publish the sample size that corresponds "
        "to the window avg_grounded_rate actually came from"
    )


# ══════════════════════════════════════════════════════════════════════════
# R-F3697 — gate #4: it must be able to fail, and to say "unmeasurable"
# ══════════════════════════════════════════════════════════════════════════

def test_an_unreadable_store_is_unmeasurable_not_a_pass(monkeypatch):
    from aria_service.intel import run_quarantine, redis_store

    async def _boom(key):
        raise redis_store.StoreReadError("sqlite timeout")

    monkeypatch.setattr(redis_store, "get_json_strict", _boom)
    out = asyncio.run(run_quarantine.closure_summary())
    assert out["gate_passes"] is None, (
        f"an unreadable quarantine store returned gate_passes="
        f"{out['gate_passes']!r} — it must be None (UNMEASURABLE). Before this, "
        f"_load swallowed the error and returned four hardcoded seeds, ALL of "
        f"which carry investigation_status='closed', so the gate certified on "
        f"code constants whether the store was healthy, empty or dead."
    )
    assert out["measurable"] is False
    assert out.get("measure_error")


def test_a_healthy_store_still_reports_a_real_verdict(monkeypatch):
    from aria_service.intel import run_quarantine, redis_store

    async def _ok(key):
        return {}

    monkeypatch.setattr(redis_store, "get_json_strict", _ok)
    out = asyncio.run(run_quarantine.closure_summary())
    assert out["measurable"] is True
    assert isinstance(out["gate_passes"], bool)
    assert out["seeded_total"] == len(run_quarantine._SEEDED)
    assert out["dynamic_total"] == 0, (
        "with an empty dynamic store the basis must be visible as seeds-only"
    )


def test_an_open_dynamic_quarantine_fails_the_gate(monkeypatch):
    """The gate must be able to say NO."""
    from aria_service.intel import run_quarantine, redis_store

    async def _one_open(key):
        return {"dd_newlybroken": {"reason": "x", "quarantined_at": "2026-08-04T00:00:00+00:00",
                                   "investigation_status": "open"}}

    monkeypatch.setattr(redis_store, "get_json_strict", _one_open)
    out = asyncio.run(run_quarantine.closure_summary())
    assert out["gate_passes"] is False
    assert "dd_newlybroken" in out["open_run_ids"]
    assert out["dynamic_total"] == 1


def test_phase_gates_preserves_the_tri_state(monkeypatch):
    """bool(None) is False — that would render 'unknown' as 'failed'."""
    import inspect
    from aria_service.intel import phase_gates

    src = module_source(phase_gates)
    assert "bool(cs4.get(\"gate_passes\"))" not in src, (
        "gate #4's pass must not be coerced with bool() — that collapses the "
        "UNMEASURABLE None into a measured failure"
    )
    assert "_gp4 is None" in src
