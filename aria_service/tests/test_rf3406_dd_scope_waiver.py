"""R-F3406 — scope selection: declining a check is a WAIVER, never a toggle.

THE DESIGN PROBLEM. The operator needs to scope a run — not every counterparty warrants
a sanctions screen, and the OpenSanctions monthly allowance is finite (VERIFIED EXHAUSTED
2026-07-29, HTTP 429 on every screen). The obvious implementation is a tick box, and on a
compliance product a tick box is dangerous: an unticked check yields a report with no
sanctions section, which reads exactly like a report whose sanctions section found
nothing. That is a false clean manufactured by the UI, and it is indistinguishable from
the R-F3217/R-F3229 class this suite exists to prevent.

So these tests pin the four properties that make an opt-out safe:

  1. a waiver carries WHO and WHY, and an anonymous one is discarded rather than honoured
  2. WAIVED stays in the DENOMINATOR — coverage FALLS when you waive
  3. WAIVED is never `answered`, so it can never render as clean
  4. a waiver is not an outage — "we chose not to" and "the source was unreachable" are
     different states with different remedies

Plus the resolver registry: a question can be wired now and bound to an API later, and
until then it must report what would establish it — never pass, and never imply a
capability we do not have.
"""
from __future__ import annotations

from aria_service.intel import dd_standard as S


def _company(**identity) -> dict:
    base = {"entity_type": "company", "entity_name": "Testco Ltd"}
    base.update(identity)
    return {"identity": base}


def _wv(qid="IS-13", by="A. Correa", reason="domestic UK cleaning contract, no "
                                            "cross-border exposure", at="2026-07-29"):
    return S.Waiver(question_id=qid, waived_by=by, reason=reason, waived_at=at)


# ── 1. a waiver carries who and why ──────────────────────────────────────────

def test_waiver_requires_a_name_and_a_reason():
    assert _wv().is_valid()
    assert not S.Waiver("IS-13", "", "reason").is_valid()
    assert not S.Waiver("IS-13", "someone", "").is_valid()
    assert not S.Waiver("", "someone", "reason").is_valid()


def test_anonymous_waiver_is_discarded_not_honoured():
    """Fail-safe direction: an unusable waiver means the question is assessed
    normally, never that it silently disappears."""
    a = S.assess(_company(), tier="SIMPLIFIED",
                 waivers=[{"question_id": "IS-13", "waived_by": "", "reason": ""}])
    is13 = next(r for r in a["resolutions"] if r["question_id"] == "IS-13")
    assert is13["state"] != S.EvidenceState.WAIVED.value


def test_waiver_reason_and_name_reach_the_output():
    a = S.assess(_company(), tier="SIMPLIFIED", waivers=[_wv()])
    is13 = next(r for r in a["resolutions"] if r["question_id"] == "IS-13")
    assert is13["state"] == S.EvidenceState.WAIVED.value
    assert "A. Correa" in is13["reason"]
    assert "cross-border" in is13["reason"]


def test_waiver_accepts_dicts_from_the_api_path():
    a = S.assess(_company(), tier="SIMPLIFIED", waivers=[{
        "question_id": "IS-13", "waived_by": "A. Correa", "reason": "scoped out"}])
    is13 = next(r for r in a["resolutions"] if r["question_id"] == "IS-13")
    assert is13["state"] == S.EvidenceState.WAIVED.value


# ── 2. waiving LOWERS coverage ───────────────────────────────────────────────

def test_waived_question_stays_in_the_denominator():
    """The whole point. NOT_APPLICABLE leaves the denominator because the question was
    never asked; WAIVED does not, because it was asked and declined."""
    plain = S.assess(_company(), tier="SIMPLIFIED")
    waived = S.assess(_company(), tier="SIMPLIFIED", waivers=[_wv()])
    assert waived["required"] == plain["required"], (
        "waiving a question shrank the denominator — that is how declining a check "
        "makes a report look MORE complete"
    )


def test_waiving_never_raises_coverage():
    plain = S.assess(_company(registration_number="1", registration_status="active"),
                     tier="SIMPLIFIED")
    waived = S.assess(_company(registration_number="1", registration_status="active"),
                      tier="SIMPLIFIED", waivers=[_wv()])
    assert waived["coverage_pct"] <= plain["coverage_pct"]


# ── 3. WAIVED is never an answer ─────────────────────────────────────────────

def test_waived_is_never_counted_as_answered():
    a = S.assess(_company(), tier="SIMPLIFIED", waivers=[_wv()])
    answered_ids = {r["question_id"] for r in a["resolutions"]
                    if r["state"] in ("CORROBORATED", "SINGLE_SOURCE")}
    assert "IS-13" not in answered_ids


def test_waived_question_appears_in_the_open_list_with_a_remedy():
    a = S.assess(_company(), tier="SIMPLIFIED", waivers=[_wv()])
    row = next((m for m in a["missing"] if m["question_id"] == "IS-13"), None)
    assert row is not None, "a waived check vanished from the open list"
    assert row["state"] == S.EvidenceState.WAIVED.value
    assert "not a clear one" in row["remedy"]


def test_waiver_skips_the_reader_entirely():
    """Declining a check must not spend the quota anyway — otherwise the opt-out
    achieves nothing operationally."""
    called = {"n": 0}

    def _spy(report, q):
        called["n"] += 1
        return S.Resolution(q.id, S.EvidenceState.SINGLE_SOURCE.value)

    q = S.QUESTIONS_BY_ID["IS-13"]
    spied = S.Question(
        id=q.id, fundamental=q.fundamental, cluster=q.cluster, tier=q.tier,
        applies_to=q.applies_to, established_by=q.established_by, text=q.text,
        pass_condition=q.pass_condition, resolvers=q.resolvers, reader=_spy)
    # drive the waiver branch directly against a catalogue containing the spy
    import unittest.mock as _mock
    patched = tuple(spied if x.id == "IS-13" else x for x in S.QUESTIONS)
    with _mock.patch.object(S, "QUESTIONS", patched):
        S.assess(_company(), tier="SIMPLIFIED", waivers=[_wv()])
    assert called["n"] == 0, "the reader ran for a waived question"


# ── 4. a waiver is not an outage ─────────────────────────────────────────────

def test_waived_and_source_unavailable_are_different_states():
    """'We chose not to screen' and 'the source was unreachable' carry different
    remedies and different liability. Collapsing them loses the distinction that makes
    a waiver defensible."""
    waived = S.assess(_company(), tier="SIMPLIFIED", waivers=[_wv()])
    outage = S.assess(_company(sanctions_screen={
        "matches": [], "source_unavailable": True, "verified_sources": []}),
        tier="SIMPLIFIED")
    w = next(r for r in waived["resolutions"] if r["question_id"] == "IS-13")["state"]
    o = next(r for r in outage["resolutions"] if r["question_id"] == "IS-13")["state"]
    assert w == S.EvidenceState.WAIVED.value
    assert o == S.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    assert w != o


def test_waiver_for_an_unknown_question_is_ignored():
    a = S.assess(_company(), tier="SIMPLIFIED",
                 waivers=[_wv(qid="NOT-A-REAL-QUESTION")])
    assert not any(r["state"] == S.EvidenceState.WAIVED.value for r in a["resolutions"])


def test_assess_remains_pure_with_waivers():
    rep = _company(registration_number="1", registration_status="active")
    w = [_wv()]
    assert S.assess(rep, tier="STANDARD", waivers=w) == S.assess(rep, tier="STANDARD", waivers=w)


# ── the resolver registry: wired now, bound later ────────────────────────────

def test_every_declared_resolver_exists_in_the_registry():
    """A question naming a source the registry does not describe is a promise with
    nothing behind it."""
    missing = sorted({
        r for q in S.QUESTIONS for r in q.resolvers if r not in S.RESOLVERS
    })
    assert not missing, f"questions declare unknown resolvers: {missing}"


def test_every_question_declares_at_least_one_resolver():
    """'No source identified' is a real answer, but it must be a deliberate one — an
    empty resolver list is indistinguishable from an oversight."""
    bare = [q.id for q in S.QUESTIONS if not q.resolvers]
    assert not bare, f"questions with no declared source: {bare}"


def test_unbuilt_resolver_does_not_imply_capability():
    """A declared-but-unbuilt source must resolve NOT_RUN, never pass."""
    unbuilt_qs = [q for q in S.QUESTIONS
                  if q.reader is None
                  and q.established_by != S.EstablishedBy.SUPPLIED.value]
    assert unbuilt_qs, "guard is blind — no unbound questions left to check"
    a = S.assess(_company(), tier="ENHANCED")
    for q in unbuilt_qs:
        r = next((x for x in a["resolutions"] if x["question_id"] == q.id), None)
        if r is None:
            continue
        assert r["state"] not in ("CORROBORATED", "SINGLE_SOURCE"), (
            f"{q.id} has no reader but reported {r['state']}"
        )


def test_resolver_status_separates_no_adapter_from_no_source():
    ccj = S.resolver_status(S.QUESTIONS_BY_ID["IS-17b"])
    assert ccj["declared"] == ["registry_trust"]
    assert ccj["unbuilt"] == ["registry_trust"]
    assert S.Access.PAID_PER_SEARCH.value in ccj["blocked_on"], (
        "a metered source must be flagged as blocked on a commercial decision, not "
        "silently queued for build"
    )


def test_paid_and_licence_gated_sources_are_flagged_not_hidden():
    """§6 puts the burden of proof on a new third party and §17 caps spend, so a source
    that costs money or needs a licence must announce itself."""
    assert S.RESOLVERS["registry_trust"].access == S.Access.PAID_PER_SEARCH.value
    assert "£" in S.RESOLVERS["registry_trust"].note
    assert S.RESOLVERS["find_case_law"].access == S.Access.LICENCE_REQUIRED.value
    assert "Open Justice Licence" in S.RESOLVERS["find_case_law"].note


def test_free_uk_sources_are_declared_for_the_rows_they_answer():
    """The four free wins found on 2026-07-29, each bound to the question it answers."""
    assert "gazette" in S.QUESTIONS_BY_ID["FS-11"].resolvers          # insolvency
    assert "ch_insolvency" in S.QUESTIONS_BY_ID["FS-11"].resolvers
    assert "ch_charges" in S.QUESTIONS_BY_ID["FS-12"].resolvers       # charges
    assert "ch_disqualified" in S.QUESTIONS_BY_ID["IS-16b"].resolvers  # disqualification
    assert "employment_tribunal" in S.QUESTIONS_BY_ID["IS-17c"].resolvers
    for rid in ("gazette", "ch_charges", "ch_insolvency", "ch_disqualified",
                "employment_tribunal"):
        assert S.RESOLVERS[rid].access in (S.Access.FREE.value, S.Access.KEYED_FREE.value)


def test_ccj_question_covers_companies_and_individuals():
    """The operator's requirement: CCJs for BOTH. A CCJ against a director is a
    different fact from one against the company, and both bear on the decision."""
    assert S.QUESTIONS_BY_ID["IS-17b"].applies_to == S.AppliesTo.BOTH.value
    assert "IS-17b" in {q.id for q in S.questions_for("STANDARD", "company")}
    assert "IS-17b" in {q.id for q in S.questions_for("STANDARD", "person")}


def test_litigation_is_decomposed_not_one_bucket():
    """Three evidence bases, three sources, three remedies — the decomposition that
    stops one of them being silently skipped under a single green tick."""
    lit = sorted(q.id for q in S.QUESTIONS if q.fundamental == 17)
    assert lit == ["IS-17a", "IS-17b", "IS-17c"]
    srcs = {q.id: set(q.resolvers) for q in S.QUESTIONS if q.fundamental == 17}
    assert srcs["IS-17a"] != srcs["IS-17b"] != srcs["IS-17c"]
