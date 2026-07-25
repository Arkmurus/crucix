"""R-F2282 — DD verification-layer honesty.

The verification layer's `grounded_rate` was structurally FAKE: each material
claim was hard-coded to ONE source string (e.g. `_add("identity:sanctions_checked",
"sanctions")`), so a claim could never reach source_count>=2 no matter how many
lists/indices actually corroborated it — capping grounded_rate at ~1/N (~17% in
practice). And `source_verifier` was NEVER invoked (the scope_note admitted it).

R-F2282: (A) count the REAL distinct sources — each sanctions list actually
queried (OFAC/UK OFSI/EU/UN…) and each country-risk index present (CPI/Basel/
FATF/WGI/OECD) — and (B) actually invoke source_verifier to compute a real
citation-grounding rate over the report's cited URLs vs the fetched evidence.

These capability tests drive the REAL `_run_verification` against a constructed
report and assert the honest outcome.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import ARKDDReport, Finding, Evidence

_REUTERS = "https://www.reuters.com/article/x"


def _build_report() -> ARKDDReport:
    r = ARKDDReport()
    # A sanctions screen queries MANY lists — 3 really queried, 1 unavailable.
    r.identity.sanctions_screen = {
        "matches": [],
        "verified_sources": {
            "OFAC SDN": {"status": "CLEAN"},
            "UK OFSI": {"status": "CLEAN"},
            "EU Consolidated": {"status": "HIT"},
            "Interpol": {"status": "UNAVAILABLE"},  # must NOT count
        },
    }
    # Country risk corroborated by 5 independent indices.
    r.compliance.country_risk = {
        "cpi_score": 34, "basel_aml": 5.2, "fatf_status": "clear",
        "wgi": -0.5, "oecd_crc": 4, "headline_risk": "AMBER",
    }
    # Fetched evidence (real retrieved URLs).
    r.digital.press_coverage = [
        Evidence(source="Reuters", url=_REUTERS),
        Evidence(source="FT", url="https://www.ft.com/content/y"),
    ]
    # One finding cites a FETCHED url (grounded); one cites an UNFETCHED url.
    r.identity.findings = [
        Finding(severity="info", title="press",
                detail=f"Coverage at {_REUTERS} confirms activity.", source="press"),
        Finding(severity="amber", title="claim",
                detail="Also see https://unfetched-source.example/z for details.",
                source="analysis"),
    ]
    return r


class TestRealCorroboration:
    @pytest.mark.asyncio
    async def test_sanctions_claim_counts_each_queried_list(self):
        r = _build_report()
        await ddo._run_verification({}, r)
        tri = {t["claim"]: t["source_count"] for t in r.verification.triangulated_claims}
        # 3 lists actually queried (CLEAN/HIT), NOT 1, NOT the UNAVAILABLE one.
        assert tri.get("identity:sanctions_checked") == 3

    @pytest.mark.asyncio
    async def test_unavailable_list_is_not_a_source(self):
        r = _build_report()
        await ddo._run_verification({}, r)
        srcs = next(t["sources"] for t in r.verification.triangulated_claims
                    if t["claim"] == "identity:sanctions_checked")
        assert not any("Interpol" in s for s in srcs)

    @pytest.mark.asyncio
    async def test_country_risk_counts_each_index(self):
        r = _build_report()
        await ddo._run_verification({}, r)
        tri = {t["claim"]: t["source_count"] for t in r.verification.triangulated_claims}
        assert tri.get("compliance:country_risk_known") == 5

    @pytest.mark.asyncio
    async def test_grounded_rate_no_longer_structurally_capped(self):
        r = _build_report()
        await ddo._run_verification({}, r)
        # Under the OLD hard-coded logic sanctions+country_risk were 1 source each
        # → grounded_rate 0.0. Now both are multi-source → materially higher.
        assert r.verification.grounded_rate is not None
        assert r.verification.grounded_rate >= 0.5


class TestCitationGroundingDoesNotMasqueradeAsIndependentVerification:
    @pytest.mark.asyncio
    async def test_independent_verification_flag_stays_false_without_refetch(self):
        r = _build_report()
        await ddo._run_verification({}, r)
        assert r.verification.independent_source_verification_run is False

    @pytest.mark.asyncio
    async def test_citation_grounding_is_real(self):
        r = _build_report()
        await ddo._run_verification({}, r)
        # 2 URLs cited in findings; 1 (reuters) was fetched, 1 (example) was not.
        assert r.verification.citations_checked == 2
        assert r.verification.citations_grounded == 1
        assert r.verification.citation_grounding_rate == 0.5

    @pytest.mark.asyncio
    async def test_scope_note_is_honest_about_both_metrics(self):
        r = _build_report()
        await ddo._run_verification({}, r)
        note = r.verification.scope_note.lower()
        assert "citation grounding" in note or "source_verifier" in note
        assert "not invoked" not in note  # the old dishonest disclaimer is gone


class TestHonestOnEmptyReport:
    @pytest.mark.asyncio
    async def test_empty_report_no_crash_and_honest(self):
        r = ARKDDReport()
        await ddo._run_verification({}, r)
        # No claims → grounded_rate None (honest, not a fake number).
        assert r.verification.grounded_rate is None
        # Citation grounding still runs; no citations → citation rate None. It is
        # not an independent source re-fetch, so the stronger flag stays false.
        assert r.verification.independent_source_verification_run is False
        assert r.verification.citation_grounding_rate is None
