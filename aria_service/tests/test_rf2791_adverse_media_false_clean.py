"""R-F2791 — a FAILED adverse-media sweep must never score as a clean screening.

THE DEFECT (introduced by R-F2786, proven live before this fix):

  researcher.py incremented its template counter BEFORE issuing the search, so
  ``templates_run`` counted templates ENTERED, not templates that actually
  SEARCHED. R-F2786 then removed the 10-point ``adverse_empty`` quality penalty
  on the theory that a zero-finding sweep is proven-complete negative evidence.

  Together those two facts made an adverse-media sweep in which EVERY template's
  backend call failed score *identically* to a genuine clean screening:

      adverse_media_run=True, adverse_media_skipped=False, no penalty,
      decision-readiness Q3 = ANSWERED ("completed dedicated adverse-media search")

  That is a false clean — precisely what ARIA's USP forbids, and precisely what
  R-F2786 exists to prevent. It is reachable in production: Brave returns [] on
  429 without raising, and the template loop's ``if not search_results: continue``
  did not even record that as a skip.

THE CONTRACT THIS LOCKS IN:

  Zero findings is valid negative evidence ONLY when we can show the search
  infrastructure was actually working. Concretely, a completed adverse-media
  screening requires a template that genuinely searched AND either real findings
  (which prove a backend answered) or verified-healthy search backends.

  This deliberately does NOT re-break Grade A for genuinely clean entities: a
  healthy-backend sweep returning zero findings still answers the question. That
  was R-F2786's legitimate fix and it is preserved — see the final test.
"""

from aria_service.intel.dd_schema import (
    _dd_decision_readiness,
    _quality_metrics,
    _quality_penalties,
)


def _report(adverse: dict) -> dict:
    return {"adverse_media": adverse, "identity": {}, "compliance": {}, "network": {}}


def _adverse_question(report: dict) -> dict:
    return (_dd_decision_readiness(report).get("questions") or {}).get("adverse_media") or {}


# ── the exact live failure state ────────────────────────────────────────────
TOTAL_BACKEND_FAILURE = {
    "ok": True,
    "entity": "Acme Ltd",
    "templates_run": 30,          # 30 templates ENTERED...
    "templates_searched": 0,      # ...0 actually searched
    "circuit_breaker_skips": 30,
    "search_backends_answered": False,
    "partial": False,
    "timed_out": False,
    "findings": [],
    "findings_count": 0,
}

GENUINE_CLEAN = {
    "ok": True,
    "entity": "Acme Ltd",
    "templates_run": 30,
    "templates_searched": 30,     # every template reached a backend
    "circuit_breaker_skips": 0,
    "search_backends_answered": True,
    "partial": False,
    "timed_out": False,
    "findings": [],
    "findings_count": 0,
}


def test_total_backend_failure_is_not_a_completed_screening():
    """Every template failed → must NOT read as a run, completed screening."""
    m = _quality_metrics(_report(TOTAL_BACKEND_FAILURE))
    assert m["adverse_media_run"] is False, "a sweep where nothing searched is not a run"
    assert m["adverse_media_skipped"] is True, "it must count as skipped, not completed"


def test_total_backend_failure_is_penalised():
    """The quality score must reflect that the screening did not happen."""
    m = _quality_metrics(_report(TOTAL_BACKEND_FAILURE))
    reasons = [why for _, why in _quality_penalties(m)]
    assert any("adverse-media" in why for why in reasons), (
        f"a wholly-failed adverse sweep must be penalised; got {reasons}"
    )


def test_total_backend_failure_does_not_answer_the_readiness_question():
    """The five-question scorecard must not certify a search that never ran."""
    q = _adverse_question(_report(TOTAL_BACKEND_FAILURE))
    assert q.get("answered") is False, "a failed sweep cannot answer the adverse-media question"
    assert q.get("status") != "ANSWERED"
    assert q.get("blocker"), "an unanswered question must name its blocker"


def test_failed_and_clean_sweeps_are_distinguishable():
    """The two states must not be scored identically — that was the whole defect."""
    failed = _quality_metrics(_report(TOTAL_BACKEND_FAILURE))
    clean = _quality_metrics(_report(GENUINE_CLEAN))
    assert failed["adverse_media_run"] != clean["adverse_media_run"], (
        "a total backend failure and a genuine clean screening must not score the same"
    )


def test_zero_findings_with_unhealthy_backends_is_not_negative_evidence():
    """Templates searched, but backends were degraded → zero proves nothing."""
    degraded = dict(GENUINE_CLEAN, search_backends_answered=False)
    m = _quality_metrics(_report(degraded))
    assert m["adverse_media_run"] is False, (
        "zero findings on degraded backends is absence of evidence, not evidence of absence"
    )
    assert _adverse_question(_report(degraded)).get("answered") is False


def test_real_findings_prove_a_backend_answered_even_if_health_unknown():
    """Findings are themselves proof a backend responded — legacy reports included."""
    legacy = {
        "ok": True,
        "templates_run": 0,          # legacy blobs predate the counters
        "findings_count": 3,
        "findings": [{"title": "x"}, {"title": "y"}, {"title": "z"}],
    }
    m = _quality_metrics(_report(legacy))
    assert m["adverse_media_run"] is True, "real findings prove the search executed"
    assert _adverse_question(_report(legacy)).get("answered") is True


def test_genuine_clean_screening_still_answers_the_question():
    """R-F2786's legitimate fix is preserved: a healthy zero-finding sweep counts.

    This is the regression guard in the OTHER direction — the point of R-F2786
    was that penalising every clean entity made Evidence Grade A structurally
    impossible. That must remain fixed.
    """
    m = _quality_metrics(_report(GENUINE_CLEAN))
    assert m["adverse_media_run"] is True
    assert m["adverse_media_skipped"] is False
    reasons = [why for _, why in _quality_penalties(m)]
    assert not any("adverse-media" in why for why in reasons), (
        f"a proven-complete clean sweep must not be penalised; got {reasons}"
    )
    assert _adverse_question(_report(GENUINE_CLEAN)).get("answered") is True


# ── END-TO-END: the PRODUCER must emit fields that fail closed ───────────────
# The consumer rules above are only safe if researcher actually emits
# templates_searched / search_backends_answered. Without the producer half,
# every new report would read UNRESOLVED forever. This drives the REAL entry
# point (run_adverse_media_deep_search) with the search layer forced into the
# live failure mode, then feeds its real output through the real scorers.

import asyncio

import pytest

from aria_service.intel import researcher


def _run_sweep(monkeypatch, web_search_impl):
    monkeypatch.setattr(researcher, "_web_search", web_search_impl)
    return asyncio.run(
        researcher.run_adverse_media_deep_search(
            "Acme Ltd", max_templates=4, deadline_s=30.0,
        )
    )


def test_e2e_every_backend_call_fails_is_not_a_clean_screening(monkeypatch):
    """Brave-429 shape: _web_search returns [] for every template, never raises."""
    async def all_empty(query, timeout=10.0):
        return []

    result = _run_sweep(monkeypatch, all_empty)

    assert result["templates_run"] > 0, "templates were entered"
    assert result["search_backends_answered"] is False, (
        "no template got a raw result back — the infrastructure did not answer"
    )
    assert result["findings_count"] == 0

    # …and the real scorers must therefore refuse to certify it.
    report = _report(result)
    assert _quality_metrics(report)["adverse_media_run"] is False
    assert _adverse_question(report).get("answered") is False


def test_e2e_every_backend_call_raises_is_not_a_clean_screening(monkeypatch):
    """Hard-failure shape: the search layer raises for every template."""
    async def all_raise(query, timeout=10.0):
        raise RuntimeError("backend down")

    result = _run_sweep(monkeypatch, all_raise)

    assert result["templates_searched"] == 0, (
        "a template that raised never reached the search layer"
    )
    assert result["search_backends_answered"] is False
    assert _quality_metrics(_report(result))["adverse_media_run"] is False


def test_e2e_working_backend_with_no_subject_hits_IS_a_clean_screening(monkeypatch):
    """The genuine-clean case: backends answer, hits exist, none name the subject.

    This is the regression guard for R-F2786's legitimate fix — a working search
    that finds nothing about the subject must still ANSWER the question, or
    Evidence Grade A becomes structurally impossible for clean entities.
    """
    async def answers_offsubject(query, timeout=10.0):
        return [{
            "title": "Completely unrelated company in the news",
            "link": "https://example.com/other",
            "snippet": "A different business entirely.",
            "source": "example.com",
        }]

    result = _run_sweep(monkeypatch, answers_offsubject)

    assert result["templates_searched"] > 0
    assert result["search_backends_answered"] is True, (
        "raw results came back — the search demonstrably worked"
    )
    assert result["findings_count"] == 0, "off-subject hits are correctly dropped"

    report = _report(result)
    assert _quality_metrics(report)["adverse_media_run"] is True, (
        "a working search with no subject hits IS valid negative evidence"
    )
    assert _adverse_question(report).get("answered") is True


# ── R-F2808: legacy blobs fail closed, but say WHY honestly ────────────────
# Reconciles R-F2791 with the R-F2693 convention at dd_schema.py:1181 ("absence
# of a field is not evidence of a negative"). Both are the same principle — do
# not assert what you cannot show — at opposite polarities: R-F2693 avoids a
# false ACCUSATION, R-F2791 avoids a false CLEAN. Failing closed stays; the
# WORDING must not claim knowledge we do not have.

LEGACY_BLOB = {
    "ok": True,
    "templates_run": 30,      # pre-R-F2791: neither new counter exists
    "findings_count": 0,
    "findings": [],
}


def test_legacy_blob_still_fails_closed():
    """Safety is unchanged — an unprovable screening cannot clear."""
    assert _quality_metrics(_report(LEGACY_BLOB))["adverse_media_run"] is False
    assert _adverse_question(_report(LEGACY_BLOB)).get("answered") is False


def test_legacy_blob_blocker_does_not_claim_the_search_failed():
    """We know we cannot PROVE it ran; we do not know that it did not run.

    A delivered GREEN report flipping to "screening did not complete" reads as a
    retraction of a factual claim rather than a disclosure of an evidence gap.
    """
    blocker = _adverse_question(_report(LEGACY_BLOB)).get("blocker", "")
    assert "did not complete" not in blocker, f"asserts unknown knowledge: {blocker!r}"
    assert "predates" in blocker, f"must disclose WHY it is unprovable: {blocker!r}"
    assert "re-run" in blocker, "must tell the customer how to clear it"


def test_a_genuinely_failed_sweep_still_says_it_did_not_complete():
    """The distinction must not blur the other way: a sweep we KNOW failed
    (the counters are present and say zero) keeps the definite wording."""
    failed = dict(LEGACY_BLOB, templates_searched=0, search_backends_answered=False)
    blocker = _adverse_question(_report(failed)).get("blocker", "")
    assert "did not complete" in blocker, f"known failure must be stated plainly: {blocker!r}"
    assert "predates" not in blocker
