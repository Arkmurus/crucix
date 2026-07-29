"""R-F3435/R-F3436 — pre-run selection of metered/gated DD sources.

R-F3435 — THE DEFECT: `ResolverSpec.built` was a hand-maintained declaration, and it
drifted inside a single session. gazette, ch_charges, ch_insolvency, ch_disqualified and
employment_tribunal were all still `built=False` AFTER R-F3403/R-F3404/R-F3422/R-F3424
shipped them and wired them into every DD. Anything reading the declaration — including
the selection screen the operator asked for — would have told them that live, running
sources did not exist. `is_built()` now DERIVES it from the adapter's presence.

R-F3436 — the selection surface itself: which gated sources does THIS subject need, is
each usable right now, and which questions does each unlock. REQUIRED is derived from the
catalogue (a source is required when nothing else can answer the question), so it stays
correct as resolvers are added instead of rotting like the flag above.

These tests drive the real functions and the real FastAPI route, not helpers.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_standard as ds


# ── R-F3435: built must be MEASURED, not declared ──────────────────────────

def test_declared_built_matches_reality_for_every_bound_resolver():
    """THE DRIFT GUARD. This is the test that would have caught the original defect the
    day it appeared, and it fails the moment a declaration and an adapter disagree."""
    drift = []
    for rid, spec in ds.RESOLVERS.items():
        if not spec.binding:
            continue                      # nothing to derive from; declaration stands
        if spec.built != spec.is_built():
            drift.append(f"{rid}: declared built={spec.built} but is_built()={spec.is_built()}")
    assert not drift, "declared build state disagrees with reality:\n  " + "\n  ".join(drift)


def test_the_five_sources_shipped_this_session_report_as_built():
    """The concrete regression: each of these was declared unbuilt while running live."""
    for rid in ("gazette", "ch_charges", "ch_insolvency", "ch_disqualified",
                "employment_tribunal"):
        assert ds.RESOLVERS[rid].is_built(), f"{rid} ships in this build but reports unbuilt"


def test_is_built_is_false_when_the_binding_is_absent():
    """The instrument must be able to say NO — a probe that always returns True would
    certify every future unbuilt source as present (the certify-by-absence shape)."""
    spec = ds.ResolverSpec(
        "phantom", "Phantom source", built=True, access=ds.Access.FREE.value,
        binding=("aria_service.intel.definitely_not_a_real_module", "nope"))
    assert spec.is_built() is False, "a missing module must derive as NOT built"

    spec2 = ds.ResolverSpec(
        "phantom2", "Phantom attr", built=True, access=ds.Access.FREE.value,
        binding=("aria_service.intel.dd_standard", "no_such_attribute_here"))
    assert spec2.is_built() is False, "a present module with a missing attr is NOT built"


def test_genuinely_unbuilt_sources_still_report_unbuilt():
    """Registry Trust and Find Case Law have no adapter — deriving must not invent one."""
    for rid in ("registry_trust", "find_case_law", "idv"):
        assert ds.RESOLVERS[rid].is_built() is False, f"{rid} has no adapter but reports built"


def test_built_and_available_are_separate_questions(monkeypatch):
    """An adapter can exist and still be unusable. Collapsing the two would tell the
    operator nothing about whether this is a coding task or a credential task."""
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    spec = ds.RESOLVERS["ch_charges"]
    assert spec.is_built() is True
    available, reason = spec.availability()
    assert available is False and "credential" in reason.lower(), reason

    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")
    available, reason = spec.availability()
    assert available is True and reason == "", f"should be usable with a key, got {reason}"


# ── R-F3436: the selection surface ─────────────────────────────────────────

@pytest.fixture
def credentialed(monkeypatch):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")
    return True


def test_ccj_is_required_and_blocking_because_nothing_else_answers_it(credentialed):
    """IS-17b declares resolvers=("registry_trust",) and nothing else, so without it the
    CCJ question cannot be answered by any means. That is the highlight the operator
    asked for, and it must be DERIVED rather than asserted in a hardcoded list."""
    opts = ds.gated_source_options("company", "STANDARD")
    rt = next(o for o in opts["options"] if o["source_id"] == "registry_trust")
    assert rt["required"] is True
    assert rt["available"] is False
    assert "IS-17b" in [q["question_id"] for q in rt["required_for"]]
    assert "BLOCKING" in rt["decision"], rt["decision"]


def test_sanctions_is_required_and_usable(credentialed):
    opts = ds.gated_source_options("company", "STANDARD")
    s = next(o for o in opts["options"] if o["source_id"] == "sanctions")
    assert s["required"] is True, "IS-13 has no non-sanctions resolver"
    qids = [q["question_id"] for q in s["required_for"]]
    assert "IS-13" in qids and "IS-13b" in qids, qids


def test_find_case_law_is_optional_because_court_records_covers_the_question(credentialed):
    """The other half of the derivation: IS-17a lists court_records AND find_case_law, and
    court_records is usable, so the licence-gated source only ENHANCES. Marking it required
    would push the operator toward a legal application they do not need."""
    opts = ds.gated_source_options("company", "STANDARD")
    f = next(o for o in opts["options"] if o["source_id"] == "find_case_law")
    assert f["required"] is False
    assert "IS-17a" in [q["question_id"] for q in f["enhances"]]


def test_only_gated_sources_are_offered(credentialed):
    """FREE/KEYED_FREE sources just run — offering them as a choice would imply the
    operator could decline something that costs nothing, and clutter the decision."""
    opts = ds.gated_source_options("company", "STANDARD")
    for o in opts["options"]:
        assert o["access"] in ds.GATED_ACCESS, f"{o['source_id']} is not a gated source"


def test_a_person_and_a_company_are_offered_different_scopes(credentialed):
    """The requirement is per-subject. IS-13b (officers screened in their own name) is
    entity-only, so it must not appear for a person."""
    company = ds.gated_source_options("company", "STANDARD")
    person = ds.gated_source_options("person", "STANDARD")
    assert company["questions_in_scope"] > person["questions_in_scope"]

    c_sanctions = next(o for o in company["options"] if o["source_id"] == "sanctions")
    p_sanctions = next(o for o in person["options"] if o["source_id"] == "sanctions")
    assert "IS-13b" in [q["question_id"] for q in c_sanctions["required_for"]]
    assert "IS-13b" not in [q["question_id"] for q in p_sanctions["required_for"]]


def test_required_rows_sort_first(credentialed):
    """A blocking decision must not be below the fold."""
    opts = ds.gated_source_options("company", "STANDARD")["options"]
    required_flags = [o["required"] for o in opts]
    assert required_flags == sorted(required_flags, reverse=True), (
        f"required rows must lead: {[(o['source_id'], o['required']) for o in opts]}")


# ── The route the form actually calls ──────────────────────────────────────

def test_scope_options_endpoint_serves_the_selection(credentialed, monkeypatch):
    """Capability test: drive the REAL route, not the function behind it."""
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    from fastapi.testclient import TestClient
    from aria_service.routes.aria import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        r = c.get("/api/aria/dd/scope-options?entity_type=company&tier=STANDARD")
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body.get("ok") is True, body
        ids = [o["source_id"] for o in body["options"]]
        assert "registry_trust" in ids and "sanctions" in ids, ids
        assert body["standard_version"] == ds.STANDARD_VERSION


def test_a_broken_options_call_never_renders_an_empty_selection():
    """Failing OPEN here would show an empty list, which reads as 'nothing is needed' —
    the false-clean shape, one layer over."""
    import asyncio

    from aria_service.routes import aria as routes_aria

    async def _run():
        import aria_service.intel.dd_standard as real
        orig = real.gated_source_options
        real.gated_source_options = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            return await routes_aria.dd_scope_options_ep("company", "STANDARD")
        finally:
            real.gated_source_options = orig

    out = asyncio.run(_run())
    assert out["ok"] is False
    assert out["options"] is None, "a failure must NOT be an empty option list"


# ── Pre-flight: an ordered section that cannot run must say so up front ────

def test_electing_an_unavailable_source_is_disclosed_before_the_run():
    """THE OPERATOR'S REQUIREMENT, negative case. Selecting a paid section the run cannot
    search must be stated immediately — R-F3408 catches it in the checklist afterwards,
    but a run cut short before the checklist would otherwise deliver a silent hole."""
    from aria_service.intel.dd_orchestrator import _preflight_elections
    from aria_service.intel.dd_schema import ARKDDReport

    report = ARKDDReport()
    report.dd_scope = {"tier": "STANDARD", "waivers": [],
                       "elections": [{"question_id": "IS-17b", "elected_by": "operator"}]}
    _preflight_elections(report)

    gaps = " ".join(report.identity.data_gaps)
    assert "IS-17b" in gaps, f"the elected question must be named: {gaps}"
    assert "CANNOT BE SEARCHED" in gaps, gaps
    assert "must not be charged" in gaps, "an unsearched section must not be billable"


def test_electing_an_available_source_adds_no_gap(monkeypatch):
    """The guard must not cry wolf: a usable elected source produces no disclosure."""
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "test-key")
    from aria_service.intel.dd_orchestrator import _preflight_elections
    from aria_service.intel.dd_schema import ARKDDReport

    report = ARKDDReport()
    report.dd_scope = {"tier": "STANDARD", "waivers": [],
                       "elections": [{"question_id": "IS-16b", "elected_by": "operator"}]}
    _preflight_elections(report)
    assert not [g for g in report.identity.data_gaps if "IS-16b" in g], report.identity.data_gaps


def test_electing_an_unknown_question_is_refused_not_ignored():
    from aria_service.intel.dd_orchestrator import _preflight_elections
    from aria_service.intel.dd_schema import ARKDDReport

    report = ARKDDReport()
    report.dd_scope = {"elections": [{"question_id": "NOT-A-REAL-QUESTION"}]}
    _preflight_elections(report)
    assert any("NOT-A-REAL-QUESTION" in g for g in report.identity.data_gaps)


def test_preflight_never_raises_on_a_malformed_scope():
    """A pre-flight that could kill the run would be worse than the gap it reports."""
    from aria_service.intel.dd_orchestrator import _preflight_elections
    from aria_service.intel.dd_schema import ARKDDReport

    for bad in ({"elections": "not-a-list"}, {"elections": [None, 42, "str"]}, {}, None):
        report = ARKDDReport()
        report.dd_scope = bad
        _preflight_elections(report)        # must not raise
