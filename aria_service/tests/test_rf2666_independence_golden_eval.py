"""R-F2666 — the golden-set eval that GATES the R-F2413 flag.

Any independent-verification classifier (C-3 v1 now; C-3 v2's re-fetch verifier later)
must score false_positive_rate == 0 on this golden set before
`independent_source_verification_run` may ever be set True — a claim wrongly marked
independently corroborated is the honesty-USP betrayal. These tests pin that gate and
document v1's known recall gap (the target C-3 v2 must close while holding FP=0).
"""

from __future__ import annotations

from aria_service.intel.dd_independence_eval import (
    load_golden,
    run_v1_eval,
    score_independence,
    v1_corroboration_classifier,
)


def test_golden_set_loads_and_is_labelled() -> None:
    g = load_golden()
    cases = g.get("cases") or []
    assert len(cases) >= 10
    assert g.get("gate", {}).get("metric") == "false_positive_rate"
    for c in cases:
        assert "sources" in c and "expected" in c and "id" in c
    # covers both TRUE and FALSE ground-truth
    assert any(c["expected"] for c in cases) and any(not c["expected"] for c in cases)


def test_v1_false_positive_rate_is_zero_THE_GATE() -> None:
    """THE GATE: the shipped v1 classifier must NEVER mark a not-corroborated claim as
    corroborated. C-3 v2 must keep this at 0."""
    res = run_v1_eval()
    assert res["false_positive_rate"] == 0.0, (
        "R-F2413 GATE VIOLATED — false positives (claims wrongly 'independently "
        f"corroborated'): {res['false_positive_cases']}"
    )
    assert res["precision"] == 1.0


def test_v1_correctly_rejects_the_moat_critical_negatives() -> None:
    """The cases that MUST be rejected (would be false-green): ARIA-internal compute,
    memory echo, wire syndication, same-publisher family, single source."""
    g = load_golden()
    must_reject = {
        "internal_compute_only", "internal_memory_echo", "wire_syndication",
        "same_publisher_family", "single_registry", "one_external_plus_internal",
        "no_sources",
    }
    for c in g["cases"]:
        if c["id"] in must_reject:
            assert v1_corroboration_classifier(c["sources"]) is False, (
                f"{c['id']} must NOT be counted as independently corroborated"
            )


def test_v1_accepts_the_clearly_independent_positives() -> None:
    """Named distinct external authorities MUST be corroborated (true positives)."""
    g = load_golden()
    must_accept = {
        "multi_sanctions_authorities", "multi_country_indices",
        "two_registries", "registry_plus_one_press",
    }
    for c in g["cases"]:
        if c["id"] in must_accept:
            assert v1_corroboration_classifier(c["sources"]) is True, (
                f"{c['id']} should be independently corroborated"
            )


def test_v1_recall_gap_is_documented_as_the_v2_target() -> None:
    """v1 is deliberately conservative: it UNDERCOUNTS genuine multi-publisher press
    (collapses press domains) → a false NEGATIVE. This is the recall gap C-3 v2 closes
    (real domain/family/wire modelling) while holding FP-rate at 0."""
    res = run_v1_eval()
    assert "genuine_multi_publisher" in res["false_negative_cases"], (
        "v1 should undercount genuine multi-publisher press (the v2 target); if this "
        "now passes, v2's real domain-family model is in place — update the eval."
    )
    # recall is therefore < 1.0 today; FP-rate is still 0 (safe).
    assert res["recall"] < 1.0


def test_scorer_is_reusable_for_v2() -> None:
    """The scorer must accept ANY classifier so C-3 v2 plugs in its re-fetch verifier.
    A perfect oracle scores FP=0 AND recall=1 — the v2 acceptance target."""
    g = load_golden()

    # oracle that reads the labels (proves the scorer computes the confusion correctly)
    labels = {c["id"]: c["expected"] for c in g["cases"]}
    by_sources = {tuple(c["sources"]): c["expected"] for c in g["cases"]}
    res = score_independence(g["cases"], lambda s: by_sources[tuple(s)])
    assert res["false_positive_rate"] == 0.0 and res["recall"] == 1.0 and res["fn"] == 0
