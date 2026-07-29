"""R-F3442 — an ELECTED CCJ search must actually deliver.

Operator directive: "we cannot have the mistake that once the user elects to run a CCJ or
to include a CCJ search on a report that requires that data for an informed decision the
tooling or aria system is not able to produce or deliver the request."

Reporting "not completed" (R-F3441) is honest but it is not capability. This proves the
whole chain: election -> permission -> register read -> finding on the report -> the
checklist marking IS-17b answered. The backend is env-gated because Registry Trust has no
public API and supplies data under commercial contract, so switching it on is a
credential change and NOT a code change — these tests drive the real code with a licensed
extract's shape so the day the contract lands there is nothing left to discover.

The three honest outcomes, each pinned below:
  not elected      -> nothing runs, nothing spent
  elected, no key  -> a data gap naming the obstacle; NEVER a clean line
  elected + backed -> the register is read, and a genuine NIL is itself a finding
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel.dd_orchestrator import _run_ccj_search, _gated_search_permitted
from aria_service.intel.dd_schema import ARKDDReport
from aria_service.intel.sources import registry_trust as rt


ELECTED = {"tier": "STANDARD", "waivers": [],
           "elections": [{"question_id": "IS-17b", "elected_by": "ops@arkmurus.com"}]}

_CSV = (
    "defendant_name,postcode,case_number,court,amount,judgment_date,satisfied\n"
    "ACME WIDGETS LIMITED,SW1A 1AA,ABC123,County Court at Central London,12500,2025-03-11,\n"
    "ACME WIDGETS LIMITED,SW1A 1AA,ABC124,County Court at Central London,900,2023-01-05,Satisfied\n"
    "Unrelated Trading Co Ltd,M1 2AB,ZZZ999,County Court at Manchester,4000,2024-07-02,\n"
)


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    p = tmp_path / "ccj_extract.csv"
    p.write_text(_CSV, encoding="utf-8")
    monkeypatch.setenv("REGISTRY_TRUST_DATA_PATH", str(p))
    monkeypatch.setenv("REGISTRY_TRUST_DATA_AS_OF", "2026-07-01")
    rt._CACHE.update({"rows": None, "loaded_at": 0.0, "path": "", "error": ""})
    yield p
    rt._CACHE.update({"rows": None, "loaded_at": 0.0, "path": "", "error": ""})


@pytest.fixture
def no_backend(monkeypatch):
    for v in ("REGISTRY_TRUST_DATA_PATH", "REGISTRY_TRUST_API_URL", "REGISTRY_TRUST_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    rt._CACHE.update({"rows": None, "loaded_at": 0.0, "path": "", "error": ""})


def _report(name="Acme Widgets Ltd", scope=None):
    r = ARKDDReport()
    r.identity.entity_name = name
    r.identity.entity_type = "company"
    if scope is not None:
        r.dd_scope = scope
    return r


# ── the capability the operator asked for ─────────────────────────────────

def test_elected_and_backed_returns_the_judgments(dataset):
    """THE headline case: the user ordered a CCJ search and gets real judgments."""
    r = _report(scope=ELECTED)
    asyncio.run(_run_ccj_search(r))

    ccj = [f for f in r.identity.findings if f.source == "registry_trust.ccj"]
    assert ccj, f"an elected+backed CCJ search must produce a finding; gaps={r.identity.data_gaps}"
    f = ccj[0]
    assert "2 County Court Judgment" in f.title, f.title
    assert "1 UNSATISFIED" in f.title, f"an unsatisfied judgment must be called out: {f.title}"
    assert "ABC123" in f.detail, "the case number is the evidence"
    assert f.confidence == "CONFIRMED" and f.source_tier == "OFFICIAL"
    assert "2026-07-01" in f.detail, "the data vintage must be stated"


def test_an_unsatisfied_judgment_is_graded_red_and_a_satisfied_one_is_not(dataset, monkeypatch):
    """A CCJ is a court's FINAL ruling on a debt, not an allegation, so an unsatisfied one
    is red. All-satisfied is historic and stays amber — grading the procedural stage the
    evidence actually reached (R-F3412), in both directions."""
    r = _report(scope=ELECTED)
    asyncio.run(_run_ccj_search(r))
    assert [f for f in r.identity.findings if f.source == "registry_trust.ccj"][0].severity == "red"

    only_satisfied = _CSV.replace("ABC123,County Court at Central London,12500,2025-03-11,\n",
                                  "ABC123,County Court at Central London,12500,2025-03-11,Satisfied\n")
    p = dataset
    p.write_text(only_satisfied, encoding="utf-8")
    rt._CACHE.update({"rows": None, "loaded_at": 0.0, "path": "", "error": ""})
    r2 = _report(scope=ELECTED)
    asyncio.run(_run_ccj_search(r2))
    f2 = [f for f in r2.identity.findings if f.source == "registry_trust.ccj"][0]
    assert f2.severity == "amber", f2.title
    assert "all satisfied" in f2.title


def test_a_genuine_NIL_is_a_finding_not_an_absence(dataset):
    """IS-17b's pass condition: a CCJ's ABSENCE is a material finding. When the register
    IS read and returns nothing, that is evidence and must be reported as such."""
    r = _report(name="Totally Unknown Trading Company", scope=ELECTED)
    asyncio.run(_run_ccj_search(r))
    ccj = [f for f in r.identity.findings if f.source == "registry_trust.ccj"]
    assert ccj, "a searched-and-empty register must still produce a finding"
    assert "No County Court Judgment on record" in ccj[0].title
    assert ccj[0].confidence == "CONFIRMED"


def test_company_suffix_differences_still_match(dataset):
    """'Acme Widgets Ltd' must find 'ACME WIDGETS LIMITED'. A CCJ missed on a suffix is a
    false clean on the exact question the buyer is asking."""
    for variant in ("Acme Widgets Ltd", "ACME WIDGETS LIMITED", "Acme  Widgets,  Ltd."):
        r = _report(name=variant, scope=ELECTED)
        asyncio.run(_run_ccj_search(r))
        assert any(f.source == "registry_trust.ccj" and "2 County Court" in f.title
                   for f in r.identity.findings), f"{variant!r} failed to match"


def test_a_different_company_is_NOT_matched(dataset):
    """The other half, and the more dangerous one: attributing someone else's judgment is
    the name-coincidence fabrication class (R-F3217/R-F3222)."""
    r = _report(name="Acme Widgets Holdings Limited", scope=ELECTED)
    asyncio.run(_run_ccj_search(r))
    hits = [f for f in r.identity.findings if f.source == "registry_trust.ccj"]
    assert hits and "No County Court Judgment on record" in hits[0].title, (
        "a DIFFERENT company must not inherit these judgments")


# ── the three honest outcomes ─────────────────────────────────────────────

def test_not_elected_means_nothing_runs_and_nothing_is_spent(dataset):
    r = _report()                      # no scope at all
    asyncio.run(_run_ccj_search(r))
    assert not [f for f in r.identity.findings if f.source == "registry_trust.ccj"]
    assert not [g for g in r.identity.data_gaps if "CCJ" in g], (
        "an unordered check is not a failure; R-F3441 states it on the report instead")


def test_elected_but_unconfigured_is_a_named_gap_never_a_clean_line(no_backend):
    """The failure the operator is most worried about: ordered, and we cannot deliver."""
    r = _report(scope=ELECTED)
    asyncio.run(_run_ccj_search(r))
    gaps = " ".join(r.identity.data_gaps)
    assert "ORDERED" in gaps and "could not run" in gaps, gaps
    assert "REGISTRY_TRUST_DATA_PATH" in gaps, "name the exact remedy"
    assert "must not be charged for" in gaps
    assert not [f for f in r.identity.findings if f.source == "registry_trust.ccj"], (
        "a search that never ran must produce NO finding")


def test_a_broken_backend_is_unsearched_never_empty(dataset, monkeypatch):
    """A supply failure must not become 'no judgments'. This is the never-false-clean
    contract for a register whose absence is itself a finding."""
    monkeypatch.setattr(rt, "_rows", lambda: (_ for _ in ()).throw(OSError("disk gone")))
    r = _report(scope=ELECTED)
    asyncio.run(_run_ccj_search(r))
    assert not [f for f in r.identity.findings if f.source == "registry_trust.ccj"]
    assert any("NOT searched" in g for g in r.identity.data_gaps), r.identity.data_gaps


def test_an_empty_dataset_file_is_an_error_not_a_clean_register(tmp_path, monkeypatch):
    """A truncated or failed delivery would otherwise certify every subject as CCJ-free."""
    p = tmp_path / "empty.csv"
    p.write_text("defendant_name,case_number\n", encoding="utf-8")
    monkeypatch.setenv("REGISTRY_TRUST_DATA_PATH", str(p))
    rt._CACHE.update({"rows": None, "loaded_at": 0.0, "path": "", "error": ""})
    out = asyncio.run(rt.search_judgments("Acme Widgets Ltd"))
    assert out["searched"] is False, "a zero-row extract must NOT read as a clean register"


# ── permission + catalogue wiring ─────────────────────────────────────────

def test_the_search_is_gated_on_the_election_not_on_configuration(dataset):
    """Even fully configured, an unordered metered search must not run."""
    ok, _ = _gated_search_permitted(_report(), "IS-17b")
    assert ok is False
    ok2, _ = _gated_search_permitted(_report(scope=ELECTED), "IS-17b")
    assert ok2 is True


def test_the_catalogue_now_reports_registry_trust_as_BUILT(dataset):
    """R-F3435's derivation must pick the new adapter up with no hand edit."""
    from aria_service.intel.dd_standard import RESOLVERS
    spec = RESOLVERS["registry_trust"]
    assert spec.is_built() is True, "the adapter exists; the catalogue must derive it"
    assert spec.availability()[0] is True, "with a dataset configured it must read usable"


def test_without_a_backend_the_catalogue_states_the_remedy(no_backend):
    from aria_service.intel.dd_standard import RESOLVERS
    spec = RESOLVERS["registry_trust"]
    assert spec.is_built() is True, "built and unusable are different facts"
    ok, why = spec.availability()
    assert ok is False
    assert "REGISTRY_TRUST_DATA_PATH" in why, f"the operator must be told what to set: {why}"


def test_the_checklist_reads_the_ccj_evidence(dataset):
    """R-F3426's rule: the checklist must read what the DD actually gathered. IS-17b had
    reader=None, so it could never be answered even once the evidence existed."""
    from aria_service.intel.dd_standard import assess

    r = _report(scope=ELECTED)
    asyncio.run(_run_ccj_search(r))
    payload = {
        "identity": {
            "entity_name": r.identity.entity_name,
            "entity_type": "company",
            "findings": [{"title": f.title, "detail": f.detail, "source": f.source,
                          "severity": f.severity, "confidence": f.confidence}
                         for f in r.identity.findings],
            "data_gaps": list(r.identity.data_gaps),
        },
    }
    out = assess(payload, tier="STANDARD", elections=ELECTED["elections"])
    row = next(x for x in out["resolutions"] if x["question_id"] == "IS-17b")
    assert row["state"] not in ("NOT_RUN",), f"IS-17b must be answered once searched: {row}"
    el = next(e for e in out["elections"] if e["question_id"] == "IS-17b")
    assert el["fulfilled"] is True, f"the ordered section must count as delivered: {el}"
    assert el["billable"] is True, "a delivered metered search IS chargeable"
