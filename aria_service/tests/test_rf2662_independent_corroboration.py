"""R-F2662 — independent-corroboration signal (a step toward independent verification).

grounded_rate counts raw triangulation source LABELS, including ARIA's OWN internal
memory / RAG / neural echo — so a claim backed by two internal echoes reads as
"grounded" even though nothing external corroborates it. R-F2662 adds an honester
signal: a claim is INDEPENDENTLY corroborated only when >=2 DISTINCT EXTERNAL origins
back it (internal sources never count). This does NOT flip
independent_source_verification_run — that still requires full re-fetch re-verification
(R-F2413), which this deliberately does not claim.
"""

from __future__ import annotations

from aria_service.intel.dd_orchestrator import (
    _claim_independence_group,
    _independent_corroboration,
)


def test_internal_sources_collapse_to_one_non_independent_group() -> None:
    # Pass-2: ARIA's own COMPUTE engines are internal too, not external witnesses.
    for s in ("aria_knowledge", "neural_memory", "memory", "rag",
              "rag:regional_compliance", "neural_associations", "aria_brain",
              "ghost_scorer", "network_walker", "tech_classifier", "risk_indices"):
        assert _claim_independence_group(s) == "internal", s


def test_named_external_authorities_are_distinct_origins() -> None:
    assert _claim_independence_group("companies_house") == "companies_house"
    assert _claim_independence_group("sanctions:ofac") == "sanctions:ofac"
    assert _claim_independence_group("sanctions:ofsi") == "sanctions:ofsi"
    assert (_claim_independence_group("sanctions:ofac")
            != _claim_independence_group("sanctions:ofsi"))
    assert _claim_independence_group("transparency_intl_cpi") == "transparency_intl_cpi"


def test_ambiguous_press_labels_collapse_conservatively() -> None:
    """Pass-2 (moat-safety): a bare press label can't be family/wire-deduped, so two of
    them must NOT count as 2 independent origins (no over-count / false corroboration)."""
    assert _claim_independence_group("BBC News") == "external_unclassified"
    assert _claim_independence_group("Reuters") == "external_unclassified"
    assert (_claim_independence_group("BBC News")
            == _claim_independence_group("Reuters"))  # collapse → 1 origin
    # so a press-only claim is NOT independently corroborated
    count, rate = _independent_corroboration(
        [{"claim": "press", "sources": ["BBC News", "Reuters", "The Guardian"]}])
    assert count == 0, "unverifiable press labels must not over-count as independent"


def test_two_internal_echoes_are_NOT_independently_corroborated() -> None:
    """The core honesty fix: internal memory + neural echo is NOT corroboration."""
    tri = [{"claim": "c1", "sources": ["aria_knowledge", "neural_memory"], "source_count": 2}]
    count, rate = _independent_corroboration(tri)
    assert count == 0 and rate == 0.0
    assert tri[0]["independent_source_count"] == 0
    # grounded_rate WOULD have counted this as grounded (source_count>=2) — the gap C-3 closes.


def test_two_external_lists_ARE_independently_corroborated() -> None:
    tri = [{"claim": "sanctions", "sources": ["sanctions:ofac", "sanctions:ofsi"], "source_count": 2}]
    count, rate = _independent_corroboration(tri)
    assert count == 1 and rate == 1.0
    assert tri[0]["independent_source_count"] == 2


def test_single_external_source_is_not_corroborated() -> None:
    tri = [{"claim": "directors", "sources": ["companies_house"], "source_count": 1}]
    count, rate = _independent_corroboration(tri)
    assert count == 0 and rate == 0.0


def test_one_external_plus_internal_is_not_corroborated() -> None:
    """An external source + internal echo has only ONE independent origin → not corroborated."""
    tri = [{"claim": "c", "sources": ["companies_house", "aria_knowledge"], "source_count": 2}]
    count, rate = _independent_corroboration(tri)
    assert count == 0, "internal echo must not upgrade a single external source to corroborated"


def test_rate_over_mixed_claims() -> None:
    tri = [
        {"claim": "a", "sources": ["sanctions:ofac", "sanctions:ofsi", "sanctions:un"]},  # 3 indep
        {"claim": "b", "sources": ["companies_house"]},                                    # 1
        {"claim": "c", "sources": ["aria_knowledge", "neural_memory"]},                    # 0 (internal)
        {"claim": "d", "sources": ["transparency_intl_cpi", "basel_aml_index"]},           # 2 indep
    ]
    count, rate = _independent_corroboration(tri)
    assert count == 2  # a and d
    assert rate == 0.5  # 2 of 4


def test_empty_is_none() -> None:
    assert _independent_corroboration([]) == (0, None)


def test_r_f2413_flag_is_not_touched_by_this_signal() -> None:
    """R-F2413 binding: the corroboration signal must NOT set the full-verification flag."""
    from aria_service.intel.dd_schema import VerificationSection
    v = VerificationSection()
    # default stays False — R-F2662 only adds independent_corroboration_rate, never flips this.
    assert v.independent_source_verification_run is False
    assert v.independent_corroboration_rate is None
