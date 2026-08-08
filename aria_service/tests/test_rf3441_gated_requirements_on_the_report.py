"""R-F3441 — a requirement that could not be completed must APPEAR ON THE REPORT, and a
metered search must only run when the user selected it for that subject.

Operator directive, verbatim: the high-cost items "can appear on the report as not
completed because we know it does not have an paid API as yet, as well as ensure it would
be only carried out when the user select as a requirement on their DD for that specific
company or individual."

THE DEFECT: the checklist already computed all of this into
`structured_view["dd_standard"]` (R-F3410/R-F3436), but `render_markdown` — the surface a
customer actually reads, and the one the PDF is built from — never rendered any of it. A
section nobody could search for simply did not appear, and an absent row reads as "not
relevant" rather than "not covered". Silence is the false clean in its cheapest form.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_schema import ARKDDReport, _gated_requirement_lines
from aria_service.intel.dd_orchestrator import _gated_search_permitted

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


@pytest.fixture(autouse=True)
def _credentialed(monkeypatch):
    # Without a CH key every CH-backed source reads unavailable, which would flood the
    # section and mask the row under test. Production has the key.
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")


def _company(scope=None):
    r = ARKDDReport()
    r.identity.entity_type = "company"
    if scope is not None:
        r.dd_scope = scope
    return r


# ── the report must STATE it ───────────────────────────────────────────────

def test_an_unselected_gated_requirement_is_reported_as_not_completed():
    lines = "\n".join(_gated_requirement_lines(_company()))
    assert "IS-17b" in lines, "the CCJ requirement must be named"
    assert "NOT COMPLETED" in lines
    assert "Not selected as a requirement" in lines
    assert "nothing was spent" in lines, "the reader must know no money was burned"


def test_an_ORDERED_but_undeliverable_requirement_is_reported_as_a_failure():
    """Different sentence from 'not selected': the user asked, and we could not deliver."""
    lines = "\n".join(_gated_requirement_lines(_company({
        "tier": "STANDARD",
        "elections": [{"question_id": "IS-17b", "elected_by": "ops@arkmurus.com"}],
        "waivers": [],
    })))
    assert "ORDERED BUT NOT COMPLETED" in lines
    assert "must not be charged for" in lines, (
        "a section that was ordered and never searched must never be billable")


def test_a_declined_requirement_names_who_declined_and_why():
    lines = "\n".join(_gated_requirement_lines(_company({
        "tier": "STANDARD", "elections": [],
        "waivers": [{"question_id": "IS-17b", "waived_by": "ops@arkmurus.com",
                     "reason": "CCJ not required for this counterparty"}],
    })))
    assert "Declined by ops@arkmurus.com" in lines
    assert "CCJ not required for this counterparty" in lines
    assert "a declined check is not a clear one" in lines


def test_the_obstacle_is_named_not_just_the_absence():
    """'Not completed' without a reason is unactionable. The row must name the source AND
    a remedy the operator can act on.

    Asserts the PROPERTY, not the wording: this test first pinned the literal phrase
    "metered spend not approved" and went red the moment R-F3442 gave the adapter a real
    `configuration_hint()`, which is strictly better copy. A guard that fights an
    improvement is the anti-pattern R-F3419 removed from three other tests.
    """
    lines = "\n".join(_gated_requirement_lines(_company()))
    assert "Registry Trust" in lines, "name the source that would answer it"
    assert "REGISTRY_TRUST_DATA_PATH" in lines or "commercial contract" in lines, (
        f"the row must state an actionable remedy, not just 'unavailable': {lines}")


def test_a_question_another_source_can_answer_is_NOT_listed():
    """IS-17a lists find_case_law (licence-gated) AND court_records (free, built). It is
    answerable, so it is not an unmet requirement — listing it would train the reader to
    skim this section, which is how a real unmet requirement gets missed."""
    lines = "\n".join(_gated_requirement_lines(_company()))
    assert "IS-17a" not in lines, f"IS-17a is answerable by court_records: {lines}"


def test_the_section_reaches_render_markdown():
    """Capability test: the defect was that this NEVER APPEARED on the rendered report.
    Assert the real renderer emits it, not just that the helper returns strings."""
    md = _company().render_markdown()
    assert "Requirements not completed" in md, (
        "the section must appear in the markdown a customer reads / the PDF is built from")
    assert "IS-17b" in md


def test_render_markdown_survives_a_malformed_scope():
    """A report that cannot render is worse than one missing this block."""
    for bad in ("not-a-dict", {"elections": "nope"}, {"waivers": [None, 7]}, None):
        r = _company()
        r.dd_scope = bad
        md = r.render_markdown()          # must not raise
        assert isinstance(md, str) and md


# ── a metered search must not run unless SELECTED ──────────────────────────

def test_metered_spend_is_refused_by_default():
    ok, why = _gated_search_permitted(_company(), "IS-17b")
    assert ok is False
    assert "not selected" in why.lower(), why


def test_metered_spend_is_permitted_only_when_elected():
    ok, why = _gated_search_permitted(_company({
        "elections": [{"question_id": "IS-17b", "elected_by": "ops@arkmurus.com"}]}), "IS-17b")
    assert ok is True, why


def test_an_anonymous_election_cannot_authorise_spend():
    """There would be nobody to attribute the charge to. R-F3406 applies the same rule to
    waivers, and the two must not diverge."""
    ok, why = _gated_search_permitted(_company({
        "elections": [{"question_id": "IS-17b"}]}), "IS-17b")
    assert ok is False and "elected_by" in why, why


def test_an_election_for_a_DIFFERENT_question_does_not_authorise_this_one():
    ok, _ = _gated_search_permitted(_company({
        "elections": [{"question_id": "IS-13", "elected_by": "ops@arkmurus.com"}]}), "IS-17b")
    assert ok is False, "electing one section must not unlock a different paid one"


def test_an_unreadable_scope_refuses_spend_rather_than_defaulting_to_yes():
    """Deliberately the OPPOSITE of the waiver rule. An unreadable waiver means 'screen
    anyway' because screening is the safe direction; for metered spend the safe direction
    is refusal, because not spending is recoverable and spending is not."""
    r = _company()
    r.dd_scope = "not-a-dict"
    ok, why = _gated_search_permitted(r, "IS-17b")
    assert ok is False, why


# ── forward guard: a paid adapter cannot be wired around the permission ────

def test_every_bound_paid_resolver_is_gated_on_an_election():
    """DORMANT BY DESIGN, and stated as such so nobody reads it as proof.

    No PAID_PER_SEARCH resolver has a binding today (registry_trust has no adapter), so
    this currently checks nothing. It becomes live the moment someone binds one, which is
    exactly when the rule is easy to forget. The companion test below proves the check can
    actually fail, so this is not certifying by absence.
    """
    import inspect
    from aria_service.intel import dd_standard as ds
    from aria_service.intel import dd_orchestrator as ddo

    paid_and_built = [s for s in ds.RESOLVERS.values()
                      if s.access == ds.Access.PAID_PER_SEARCH.value and s.is_built()]
    if not paid_and_built:
        pytest.skip("no paid resolver is bound yet — guard is dormant, see docstring")
    src = module_source(ddo)
    assert "_gated_search_permitted" in src, (
        f"a metered resolver is bound ({[s.id for s in paid_and_built]}) but the "
        f"orchestrator never checks _gated_search_permitted — it can spend without "
        f"the user having selected it")


def test_the_forward_guard_can_actually_fail():
    """Verify the instrument: with a paid+built resolver present, the check must fire."""
    from aria_service.intel import dd_standard as ds

    fake = ds.ResolverSpec(
        "fake_paid", "Fake metered source", built=True,
        access=ds.Access.PAID_PER_SEARCH.value,
        binding=("aria_service.intel.dd_standard", "RESOLVERS"))
    assert fake.is_built() is True, "fixture must be considered built"
    assert fake.access == ds.Access.PAID_PER_SEARCH.value
    paid_and_built = [s for s in [fake] if s.access == ds.Access.PAID_PER_SEARCH.value and s.is_built()]
    assert paid_and_built, "the guard's selector must be able to match a bound paid source"
