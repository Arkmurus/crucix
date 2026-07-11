"""R-F2535 — deterministic ($0, no-LLM) synthesis for company_investigator.

company_investigator runs on try_local_reasoning's explicitly LLM-FREE path
(llm_calls_avoided=1). The old _phase_synthesis called llm_pipeline.LLMPipeline()
— which never existed — so EVERY investigation recorded a "synthesis" phase_failure
and leaked the internal error string into the user-facing summary. Threading a real
cloud LLM here would violate the caller's no-cloud contract and risk bypassing the
§17 cost meter.

R-F2535 replaces it with a deterministic digest assembled from the findings:
cost-safe by construction (no provider), honest, and it stops the permanent
synthesis-failure noise.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.company_investigator as ci


def _finding(cat, title, summary="", source="", conf=0.8):
    return ci.InvestigationFinding(category=cat, title=title, summary=summary,
                                   source=source, confidence=conf)


def test_synthesis_is_deterministic_no_failure_no_llm():
    rep = ci.InvestigationReport(entity_name="Acme Trading Ltd", jurisdiction="GB")
    rep.findings = [
        _finding("registry", "UK Companies House: ACME TRADING LTD",
                 "Status: active. Incorporated: 2001-05-04.", "http://ch/1"),
        _finding("news", "Acme fined by regulator",
                 "The firm faces a penalty over compliance.", "http://n/1"),
        _finding("web", "Acme corporate site", "Home page.", "http://acme/"),
    ]
    asyncio.run(ci._phase_synthesis(rep, "Acme Trading Ltd"))

    # No synthesis failure recorded (the whole point — it used to fail on every run).
    assert not any("synthesis" in p for p in rep.phase_failures), rep.phase_failures
    # A real structured digest, not empty and not an internal error string.
    assert rep.summary and "LLMPipeline" not in rep.summary and "no attribute" not in rep.summary
    assert "3 finding(s) across" in rep.summary
    assert "[REGISTRY]" in rep.summary and "[NEWS]" in rep.summary
    assert "Acme Trading Ltd" in rep.summary and "(GB)" in rep.summary
    # Risk indicator extracted from the "penalty" keyword.
    assert any("fined by regulator" in ri for ri in rep.risk_indicators)
    # Sources collected.
    assert "http://ch/1" in rep.sources_cited and "http://n/1" in rep.sources_cited


def test_synthesis_no_findings_stays_honest():
    # R-F2532 honest branch must still hold under R-F2535.
    rep = ci.InvestigationReport(entity_name="Zzqx Ltd")
    rep.phase_failures = ["company registry: dead", "news search: dead"]
    asyncio.run(ci._phase_synthesis(rep, "Zzqx Ltd"))
    assert "NOT a clean result" in rep.summary
    assert "2 enrichment source(s) failed" in rep.summary


def test_synthesis_single_category_grammar():
    rep = ci.InvestigationReport(entity_name="Solo Co")
    rep.findings = [_finding("web", "Only a website", "x", "http://s/")]
    asyncio.run(ci._phase_synthesis(rep, "Solo Co"))
    assert "1 finding(s) across 1 category." in rep.summary  # singular 'category'
    assert not any("synthesis" in p for p in rep.phase_failures)


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
