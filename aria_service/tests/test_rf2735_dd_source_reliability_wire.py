"""R-F2735 (Batch C) — DD verdicts must FEED the web_atlas reliability EMA.

Before this wire, web_atlas.record_ingest had NO producer anywhere in the tree:
every source sat at the 0.5 prior forever and source_validator.registry_health_report
ASSERTED an unmeasured 0.5 for every source (measure-vs-assert honesty gap). This
capability test drives the REAL path — real Finding objects through the real R-5005
gate, real _record_source_reliability, real (in-memory) state_store — and asserts the
user-visible outcome: reliability is now MEASURED, and ONLY from findings that genuinely
held up, deduped per (family, topic), leaking nothing about the DD subject.
"""
import asyncio
from types import SimpleNamespace

import pytest

from aria_service.intel import web_atlas
from aria_service.intel.dd_schema import Finding
from aria_service.intel import dd_orchestrator


# Two distinct sources so the R-5005 gate KEEPS confidence at CONFIRMED
# (≥2 sources) independent of the Tier-1a allowlist.
_CONFIRMED_URL = "https://find-and-update.company-information.service.gov.uk/company/00445790"
_CONFIRMED_FAMILY = web_atlas._source_family(_CONFIRMED_URL)


def _confirmed(url=_CONFIRMED_URL, sources=("companies house", "gov.uk filing")):
    f = Finding(
        severity="info", title="Directors confirmed against the register",
        confidence="CONFIRMED", sources=list(sources), url=url,
    )
    # Guard: the R-5005 gate must NOT have demoted this — it's our positive.
    assert f.confidence == "CONFIRMED" and not f.gate_demoted
    return f


def _demoted_single_weak():
    # A [CONFIRMED] tag with a single NON-Tier-1a source → __post_init__ demotes it.
    f = Finding(
        severity="amber", title="Adverse media (single blog)",
        confidence="CONFIRMED", sources=["randomblog.example"],
        url="https://randomblog.example/post/1",
    )
    assert f.confidence == "ASSESSED" and f.gate_demoted  # gate did its job
    return f


def _make_report(findings_by_layer):
    layers = list(findings_by_layer.keys())
    report = SimpleNamespace(layers_run=layers)
    for layer, findings in findings_by_layer.items():
        setattr(report, layer, SimpleNamespace(findings=findings))
    return report


def test_confirmed_finding_measures_reliability_up():
    """A gate-cleared CONFIRMED finding with a url lifts its source's EMA above 0.5."""
    async def _run():
        before = await web_atlas.get_reliability(_CONFIRMED_URL, "compliance")
        assert before["score"] == 0.5 and before["confirmed"] == 0  # unmeasured prior

        report = _make_report({"compliance": [_confirmed()]})
        n = await dd_orchestrator._record_source_reliability(report)
        assert n == 1

        after = await web_atlas.get_reliability(_CONFIRMED_URL, "compliance")
        assert after["score"] > 0.5, "reliability must MOVE UP on a held-up finding"
        assert after["confirmed"] == 1 and after["contradicted"] == 0
    asyncio.run(_run())


def test_demoted_assessed_and_urlless_do_not_earn_reliability():
    """gate_demoted / native-ASSESSED / no-url findings must NOT lift any score
    (positive-only, honesty guard) — the exact anti-fabrication boundary."""
    async def _run():
        report = _make_report({
            "network": [
                _demoted_single_weak(),                         # demoted → skip
                Finding(severity="info", title="assessed only",
                        confidence="ASSESSED", sources=["a", "b"],
                        url="https://assessed.example/x"),       # not CONFIRMED → skip
                Finding(severity="info", title="confirmed but no url",
                        confidence="CONFIRMED", sources=["a", "b"]),  # url=None → skip
                Finding(severity="info", title="confirmed bare label",
                        confidence="CONFIRMED", sources=["a", "b"],
                        url="companies-house"),                  # family=unknown → skip
            ],
        })
        n = await dd_orchestrator._record_source_reliability(report)
        assert n == 0, "no finding here held up with an attributable url"

        # None of the skipped sources should have a reliability record.
        for url, topic in (("https://assessed.example/x", "network"),
                           ("https://randomblog.example/post/1", "network")):
            rel = await web_atlas.get_reliability(url, topic)
            assert rel["confirmed"] == 0 and rel["score"] == 0.5
    asyncio.run(_run())


def test_one_observation_per_family_topic_per_run():
    """A source cited by N confirmed findings in one report = ONE EMA observation,
    so a single report can't inflate reliability by repetition."""
    async def _run():
        # Distinct family so the persisted assertion is isolated from other tests
        # sharing the in-memory store.
        base = "https://register.example.gov.uk/entity/42"
        report = _make_report({
            "compliance": [_confirmed(url=base), _confirmed(url=base + "?f=2"),
                           _confirmed(url=base + "/officers")],
        })
        n = await dd_orchestrator._record_source_reliability(report)
        assert n == 1, "same family+topic must be recorded once per run"

        rel = await web_atlas.get_reliability(base, "compliance")
        assert rel["confirmed"] == 1  # not 3
    asyncio.run(_run())


def test_finalizer_hook_actually_fires():
    """Prove the wire (not just the helper): _finalize_dd_run calls the recorder."""
    async def _run():
        url = "https://www.sec.gov/cgi-bin/browse-edgar?company=acme"
        fam = web_atlas._source_family(url)
        report = SimpleNamespace(
            layers_run=["compliance"], layers_skipped=[],
            data_gaps_summary=[], identity=SimpleNamespace(entity_name="Acme Ltd"),
            target={"name": "Acme Ltd"}, trace_id="t-2735",
            confidence_gate_triggered=False, total_duration_ms=1234,
            compliance=SimpleNamespace(
                meta=SimpleNamespace(status="ok"),
                findings=[Finding(severity="info", title="SEC filing confirmed",
                                  confidence="CONFIRMED", sources=["sec", "edgar"],
                                  url=url)],
            ),
        )
        await dd_orchestrator._finalize_dd_run(report)
        rel = await web_atlas.get_reliability(url, "compliance")
        assert rel["confirmed"] == 1 and rel["score"] > 0.5
    asyncio.run(_run())
