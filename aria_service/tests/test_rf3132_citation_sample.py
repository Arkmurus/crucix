"""R-F3132 — a 2-URL incidental sample was reported as a 0% grounding rate.

LIVE on the Babcock DD (dd_8c7242c2b45b, 2026-07-26):

    "Cited URLs that resolve: 0 of 2 cited links checked and reachable"
    "Quality blocked by: ... citation grounding rate below 80% (0%)"   -> Grade C

while the SAME report listed FIFTEEN cited sources.

`citations_checked` does not count the report's citations. It counts URLs that
happened to appear INLINE in prose `detail` text — on Babcock a GLEIF search link and
an OpenSanctions verify link, neither present in the evidence blob. The underlying
check is `source_verifier.verify_response`, built for a CHAT answer that cites URLs in
its prose; a DD cites through structured evidence, so the sample is incidental to how
a finding happened to be worded.

Two things were wrong: penalising the evidence grade on a 2-item incidental sample
measures FORMATTING, not evidence; and "0 of 2" is not a measured failure, it is a
sample too small to support any rate. That is the phase-gate rule (R-F2639) applied
to a quality metric — COULD NOT MEASURE is not MEASURED AND FAILED, and must never
render as a score of zero.

The check still runs and is still reported: a genuinely ungrounded set of prose
citations is a real signal. It simply cannot move the grade until the sample can
carry the claim.
"""
import pytest

from aria_service.intel.dd_schema import _quality_metrics, _quality_penalties, structured_view


def _metrics(**over):
    """R-F4224 / C-204 — DERIVED from the production builder, never hand-rolled.

    A 14-key literal against a 27-key builder: when `claim_grounded_rate` was
    added, `_quality_penalties` began raising KeyError and six tests here went
    permanently red. See test_rf3183_memory_only_tiering._metrics.
    """
    base = _quality_metrics({})
    base.update(dict(
        adverse_media_skipped=False, confidence_gate_triggered=False,
        export_control_checked=True, has_search_degradation_gap=False,
        identity_authority_present=True, memory_only_sources=0, own_site_sources=5,
        press_total=24, quality_press=2, sanctions_source_unavailable=False,
        unverified_sources=16, verified_sources=0,
        citations_checked=2, citation_grounding_rate=0.0,
    ))
    base.update(over)
    return base


def _citation_penalty(m):
    return [r for _, r in _quality_penalties(m) if "citation grounding" in r]


def test_rf3132_the_live_babcock_sample_does_not_penalise():
    """THE DEFECT: 2 incidental prose URLs downgraded a FTSE-250 report to Grade C."""
    assert _citation_penalty(_metrics()) == [], (
        "R-F3132 REGRESSION: a 2-URL incidental sample is moving the evidence grade")


@pytest.mark.parametrize("checked", [0, 1, 2])
def test_rf3132_no_sub_threshold_sample_can_penalise(checked):
    assert _citation_penalty(_metrics(citations_checked=checked)) == []


def test_rf3132_a_representative_poor_sample_STILL_penalises():
    """The guard must not become a blanket excuse — a real grounding problem on a
    real sample must still cost the grade."""
    hit = _citation_penalty(_metrics(citations_checked=5, citation_grounding_rate=0.2))
    assert hit and "20%" in hit[0]


def test_rf3132_a_representative_healthy_sample_does_not_penalise():
    assert _citation_penalty(
        _metrics(citations_checked=5, citation_grounding_rate=1.0)) == []


# ── the surface must say NOT MEASURABLE, never a bare "0 of 2" ─────────────
def _verification_row(ver: dict):
    sv = structured_view({"identity": {"entity_name": "X", "entity_type": "company"},
                          "verification": ver})
    sec = next(s for s in sv["sections"] if s["key"] == "verification")
    return next((h["value"] for h in sec["highlights"]
                 if h["label"] == "Cited URLs that resolve"), None)


def test_rf3132_small_sample_renders_as_not_measurable():
    v = _verification_row({"citations_checked": 2, "citations_grounded": 0})
    assert v is not None and "NOT MEASURABLE" in v
    assert "does not affect the evidence grade" in v
    assert not v.startswith("0 of 2"), (
        "a bare '0 of 2' invites exactly the reading the sample cannot support")


def test_rf3132_representative_sample_renders_the_figure():
    v = _verification_row({"citations_checked": 5, "citations_grounded": 4})
    assert v.startswith("4 of 5")
    assert "inline prose links" in v, (
        "the label must say WHAT was counted — these are not the report's citations")


def test_rf3132_no_citations_at_all_renders_nothing():
    assert _verification_row({"citations_checked": 0, "citations_grounded": 0}) is None
