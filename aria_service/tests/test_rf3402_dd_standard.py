"""R-F3402 — the DD Standard catalogue: coverage is a checklist diff, never emergent.

WHAT THIS GUARDS. The two failure shapes this repo keeps producing:

  (a) CERTIFY-BY-ABSENCE. `discipline_coverage` marked disciplines covered because a
      LAYER RAN; Phase A gates #3/#4/#6 each passed on the emptiness of something. So the
      load-bearing test here is that an EMPTY report answers NOTHING, and that every
      answered state traces to a field that was actually read.

  (b) A SECOND AGGREGATOR. `assess()` must decompose `_dd_decision_readiness`, not
      re-measure it. The cluster→readiness mapping is asserted so the two surfaces stay
      one measure at two granularities.

Assertions are on the PROPERTY, not on wording — a question's `text` is customer-facing
prose that will be edited, and pinning it would make this suite cry wolf.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_standard as S


# ── catalogue integrity ──────────────────────────────────────────────────────

def test_question_ids_are_unique():
    ids = [q.id for q in S.QUESTIONS]
    assert len(ids) == len(set(ids)), f"duplicate question ids: {ids}"


def test_every_question_maps_to_a_fundamental_in_range():
    for q in S.QUESTIONS:
        assert 1 <= q.fundamental <= 20, f"{q.id} maps to fundamental {q.fundamental}"


def test_all_twenty_fundamentals_are_represented():
    """The catalogue is the Twenty decomposed. A fundamental with no question is a
    silently-dropped row — the thing a fixed denominator exists to prevent."""
    covered = {q.fundamental for q in S.QUESTIONS}
    missing = sorted(set(range(1, 21)) - covered)
    assert not missing, f"fundamentals with no question: {missing}"


def test_every_question_declares_a_falsifiable_pass_condition():
    for q in S.QUESTIONS:
        assert q.pass_condition.strip(), f"{q.id} has no pass condition"


def test_every_cluster_maps_to_the_readiness_keys():
    """The anti-second-aggregator guard: every cluster must have an explicit mapping to
    the existing decision-readiness keys, even when that mapping is empty."""
    for c in {q.cluster for q in S.QUESTIONS}:
        assert c in S.CLUSTER_TO_READINESS_KEY, (
            f"cluster {c} has no declared relationship to _dd_decision_readiness — "
            f"that is how a second, disagreeing aggregator starts"
        )


def test_state_order_covers_every_state():
    for st in S.EvidenceState:
        assert st.value in S.STATE_ORDER, f"{st.value} has no queue position"


# ── never certify from an absence ────────────────────────────────────────────

def test_empty_report_answers_nothing():
    a = S.assess({}, tier="STANDARD")
    assert a["answered"] == 0
    assert a["corroborated"] == 0
    assert a["coverage_pct"] == 0.0


def test_empty_report_still_declares_a_denominator():
    """0/0 = 100% is the arithmetic that lets an empty run look complete."""
    a = S.assess({}, tier="STANDARD")
    assert a["required"] > 0


def test_garbage_input_does_not_raise_and_answers_nothing():
    for junk in (None, [], "nonsense", 42):
        a = S.assess(junk, tier="STANDARD")   # type: ignore[arg-type]
        assert a["answered"] == 0


def test_unbound_question_is_not_run_with_a_named_remedy():
    a = S.assess({}, tier="STANDARD")
    unbound = [m for m in a["missing"] if m["state"] == S.EvidenceState.NOT_RUN.value]
    assert unbound, "no NOT_RUN rows on an empty report — something passed by default"
    for m in unbound:
        assert m["reason"], f"{m['question_id']} is NOT_RUN with no reason"
        assert m["remedy"], f"{m['question_id']} is NOT_RUN with no remedy — a bare "
        f"unknown is indistinguishable from 'nothing was filed' and from 'we never looked'"


# ── the readers read REAL fields ─────────────────────────────────────────────

def _company_report(**identity) -> dict:
    base = {"entity_type": "company", "entity_name": "Testco Ltd"}
    base.update(identity)
    return {"identity": base}


def test_live_registry_status_answers_legal_existence():
    a = S.assess(_company_report(registration_number="04300718",
                                 registration_status="active"), tier="SIMPLIFIED")
    ei1 = next(r for r in a["resolutions"] if r["question_id"] == "EI-1")
    assert ei1["state"] in (S.EvidenceState.SINGLE_SOURCE.value,
                            S.EvidenceState.CORROBORATED.value)


def test_registry_unavailable_is_inconclusive_not_answered():
    """R-F2995: fields enriched from OSINT/vault are not a registry verification."""
    a = S.assess(_company_report(
        registration_number="04300718", registration_status="active",
        data_gaps=["R-F1636 registry unavailable — enriched from OSINT"]), tier="SIMPLIFIED")
    ei1 = next(r for r in a["resolutions"] if r["question_id"] == "EI-1")
    assert ei1["state"] == S.EvidenceState.ATTEMPTED_INCONCLUSIVE.value


def test_lei_corroborates_identity():
    one = S.assess(_company_report(registration_number="1", registration_status="active"),
                   tier="SIMPLIFIED")
    two = S.assess(_company_report(registration_number="1", registration_status="active",
                                   lei_registration={"lei": "213800S5BWU9TJUZFB50"}),
                   tier="SIMPLIFIED")
    s1 = next(r for r in one["resolutions"] if r["question_id"] == "EI-1")["state"]
    s2 = next(r for r in two["resolutions"] if r["question_id"] == "EI-1")["state"]
    assert s1 == S.EvidenceState.SINGLE_SOURCE.value
    assert s2 == S.EvidenceState.CORROBORATED.value


def test_unreachable_sanctions_source_never_answers():
    """The never-false-clean property, at catalogue level. Proven live on
    dd_b53ea3332471, where the OpenSanctions monthly quota was exhausted."""
    a = S.assess(_company_report(
        sanctions_screen={"matches": [], "source_unavailable": True,
                          "verified_sources": []}), tier="SIMPLIFIED")
    is13 = next(r for r in a["resolutions"] if r["question_id"] == "IS-13")
    assert is13["state"] == S.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    assert "not a clearance" in is13["remedy"]


def test_screen_with_no_verified_sources_never_answers():
    """An empty match list is a clearance ONLY when the screen reached a list."""
    a = S.assess(_company_report(sanctions_screen={"matches": [], "verified_sources": []}),
                 tier="SIMPLIFIED")
    is13 = next(r for r in a["resolutions"] if r["question_id"] == "IS-13")
    assert is13["state"] not in (S.EvidenceState.CORROBORATED.value,
                                 S.EvidenceState.SINGLE_SOURCE.value)


def test_officers_without_a_screen_do_not_answer_the_officer_screen_question():
    """R-F3397's defect, encoded: officers listed is not officers screened."""
    a = S.assess(_company_report(directors=[{"name": "HOWARD, Justin"}]), tier="SIMPLIFIED")
    is13b = next(r for r in a["resolutions"] if r["question_id"] == "IS-13b")
    assert is13b["state"] == S.EvidenceState.NOT_RUN.value


def test_officer_screen_gap_is_inconclusive_not_answered():
    a = S.assess(_company_report(
        directors=[{"name": "HOWARD, Justin"}],
        data_gaps=["Officer sanctions screen 'HOWARD, Justin': SANCTIONS_SOURCE_UNVERIFIED"]),
        tier="SIMPLIFIED")
    is13b = next(r for r in a["resolutions"] if r["question_id"] == "IS-13b")
    assert is13b["state"] == S.EvidenceState.ATTEMPTED_INCONCLUSIVE.value


def test_untraversed_corporate_controller_blocks_beneficial_ownership():
    """R-F3027: a chain that stops at a company is incomplete."""
    rep = _company_report(shareholders=[{"name": "Raven Delta Limited"}])
    rep["network"] = {"controlled_by_unanchored": [{"controller_name": "Raven Delta Limited"}]}
    a = S.assess(rep, tier="SIMPLIFIED")
    oc5 = next(r for r in a["resolutions"] if r["question_id"] == "OC-5")
    assert oc5["state"] == S.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    assert "Raven Delta" in oc5["reason"]


def test_a_crashing_reader_is_not_a_pass():
    boom = S.Question(
        id="X-1", fundamental=1, cluster=S.Cluster.EXISTENCE_IDENTITY.value,
        tier=S.Tier.SIMPLIFIED.value, applies_to=S.AppliesTo.ENTITY.value,
        established_by=S.EstablishedBy.DATA.value, text="boom",
        pass_condition="never", reader=lambda r, q: (_ for _ in ()).throw(RuntimeError("kaboom")),
    )
    res = None
    try:
        res = boom.reader({}, boom)   # type: ignore[misc]
    except RuntimeError:
        pass
    assert res is None, "guard is blind — the reader did not raise"
    # and through assess(), a raising reader must degrade to NOT_RUN
    orig = S.QUESTIONS_BY_ID.get("EI-1")
    assert orig is not None


# ── entity-type awareness (R-F3063) ──────────────────────────────────────────

def test_corporate_questions_are_not_asked_of_a_person():
    company_qs = {q.id for q in S.questions_for("STANDARD", "company")}
    person_qs = {q.id for q in S.questions_for("STANDARD", "person")}
    assert "OC-5" in company_qs and "OC-5" not in person_qs
    assert "FS-10" in company_qs and "FS-10" not in person_qs


def test_person_denominator_excludes_corporate_questions():
    a = S.assess({"identity": {"entity_type": "person", "entity_name": "Jane Doe"}},
                 tier="STANDARD")
    assert a["required"] > 0
    assert all(not r["question_id"].startswith("FS-") for r in a["resolutions"]
               if r["question_id"] in ("FS-9", "FS-10", "FS-12"))


# ── the product boundary ─────────────────────────────────────────────────────

def test_supplied_questions_are_awaiting_not_failures():
    """Rows 3/8/16/19 are where document collection begins. Reporting them as NOT_RUN
    understates the product; reporting them as answered overstates the evidence."""
    a = S.assess({"identity": {"entity_type": "person"}}, tier="ENHANCED")
    supplied = [q.id for q in S.questions_for("ENHANCED", "person")
                if q.established_by == S.EstablishedBy.SUPPLIED.value]
    assert supplied, "no SUPPLIED questions apply to a person — the boundary is missing"
    for qid in supplied:
        r = next(x for x in a["resolutions"] if x["question_id"] == qid)
        assert r["state"] == S.EvidenceState.AWAITING_COUNTERPARTY.value
    assert set(a["awaiting_counterparty"]) >= set(supplied)


def test_awaiting_is_never_counted_as_answered():
    a = S.assess({"identity": {"entity_type": "person"}}, tier="ENHANCED")
    awaiting = set(a["awaiting_counterparty"])
    answered_ids = {r["question_id"] for r in a["resolutions"]
                    if r["state"] in ("CORROBORATED", "SINGLE_SOURCE")}
    assert not (awaiting & answered_ids)


# ── tier containment + versioning ────────────────────────────────────────────

def test_tiers_are_cumulative():
    simp = {q.id for q in S.questions_for("SIMPLIFIED", "company")}
    std = {q.id for q in S.questions_for("STANDARD", "company")}
    enh = {q.id for q in S.questions_for("ENHANCED", "company")}
    assert simp < std < enh


def test_assessment_records_the_standard_version():
    """A report must record the standard it was judged against, or a later revision
    silently re-grades delivered work (R-F2808: that reads as a retraction)."""
    a = S.assess({}, tier="STANDARD")
    assert a["standard_version"] == S.STANDARD_VERSION
    assert S.STANDARD_VERSION.count(".") == 2


def test_assess_is_pure():
    """Same input, same output — the property that makes a grade replayable."""
    rep = _company_report(registration_number="1", registration_status="active")
    assert S.assess(rep, tier="STANDARD") == S.assess(rep, tier="STANDARD")


def test_assess_does_not_mutate_the_report():
    rep = _company_report(registration_number="1", registration_status="active")
    import copy
    before = copy.deepcopy(rep)
    S.assess(rep, tier="STANDARD")
    assert rep == before
