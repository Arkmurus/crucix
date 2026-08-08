"""R-F3183 — ARIA's own RAG counted as an external source, and its tier read 0.

LIVE (Babcock, dd_788e6f3ca2c3). The digital evidence pool contained

    [UNVERIFIED] memory://7d38a9c87a8b

while the same report's metrics said `memory_only_sources: 0`. A SELF-CITATION was
sitting in the external-source pool, and the tier built to exclude it recorded none
present. Both statements cannot be right.

CAUSE: `MEMORY_ONLY` is assigned in exactly one place — the R-F188 fallback, which
fires only when live search returns ZERO hits and the digital layer is served wholesale
from RAG. But per R-F2346 `web_search.search()` mixes RAG hits INTO normal results, so
any memory:// arriving on the NORMAL path never reached that branch and fell through
`_classify_tier` (which cannot classify a non-http scheme) into the UNVERIFIED
remainder. The mechanism existed; it just was not on the path that produces the answer
— the same shape as R-F3161, R-F3135 and the phase-gate forks before them.

A source's tier is a property of the SOURCE, not of the code path that fetched it.

SECOND HALF: the 15-point penalty reads "live web returned memory-only evidence" and
was written for that wholesale R-F188 degradation. Firing it on ANY non-zero count was
harmless only while this counter was stuck at 0. Once memory:// is classified
truthfully, one incidental self-citation among 15 live results would trigger a penalty
meant for total search failure — over-stating the defect as surely as mis-tiering
understated it. It now fires when memory-only evidence is at least HALF the pool.

Net effect on the live report: memory_only 0 -> 1, unverified 13 -> 12, score
UNCHANGED at 75 (grade B). This is a truthfulness fix, not a grade change.
"""
import pytest

from aria_service.intel.dd_schema import _quality_penalties

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _metrics(**kw):
    base = dict(
        press_total=15, verified_sources=3, quality_press=2, unverified_sources=12,
        own_site_sources=5, memory_only_sources=1,
        citations_checked=7, citations_grounded=5, citation_grounding_rate=0.714,
        identity_authority_present=True, sanctions_source_unavailable=False,
        export_control_checked=True, adverse_media_skipped=False,
        has_search_degradation_gap=False, confidence_gate_triggered=False,
        registry_incomplete=False, registry_substance_present=True,
    )
    base.update(kw)
    return base


def _fired(metrics):
    return [reason for _pts, reason in _quality_penalties(metrics)]


def _score(metrics):
    return 100 - sum(pts for pts, _r in _quality_penalties(metrics))


MEM = "live web returned memory-only evidence"


# ── the tiering half ──────────────────────────────────────────────────────────

def test_rf3183_memory_url_is_tiered_memory_only():
    """THE DEFECT: tier by the source, not by the branch that fetched it."""
    import inspect
    from aria_service.intel import dd_orchestrator as ddo
    src = module_source(ddo)
    i = src.index("R-F3183 — a memory:// URL is ARIA'S OWN RAG")
    window = src[i:i + 2200]
    code = "\n".join(l for l in window.splitlines() if not l.strip().startswith("#"))
    assert 'startswith("memory://")' in code, (
        "R-F3183 REGRESSION: memory:// is no longer detected on the normal search path")
    assert '"MEMORY_ONLY"' in code
    # It must run BEFORE the classifier, which cannot handle a non-http scheme.
    assert code.index('startswith("memory://")') < code.index("_classify_tier"), (
        "the memory:// check must precede _classify_tier, or the hit falls through "
        "into the UNVERIFIED remainder again")


# ── the penalty half ──────────────────────────────────────────────────────────

def test_rf3183_incidental_self_citation_is_not_penalised_as_search_failure():
    """One memory:// among 15 live results is not 'live web returned memory-only'."""
    assert MEM not in _fired(_metrics(memory_only_sources=1, press_total=15))


def test_rf3183_wholesale_rag_fallback_is_still_penalised():
    """The R-F188 case this penalty was written for MUST still fire — the digital
    layer served entirely from memory is a real degradation."""
    m = _metrics(press_total=10, memory_only_sources=10, verified_sources=0,
                 quality_press=0, own_site_sources=0, unverified_sources=0)
    assert MEM in _fired(m)
    assert _score(m) < 60, "a memory-only report must not reach a high grade"


@pytest.mark.parametrize("mem,total,should_fire", [
    (1, 15, False),    # incidental
    (7, 15, False),    # under half
    (8, 15, True),     # at half
    (10, 10, True),    # wholesale R-F188
    (5, 5, True),
    (0, 15, False),
])
def test_rf3183_penalty_threshold(mem, total, should_fire):
    fired = MEM in _fired(_metrics(memory_only_sources=mem, press_total=total))
    assert fired is should_fire, f"{mem}/{total} -> fired={fired}"


def test_rf3183_zero_press_total_does_not_divide_by_zero():
    assert MEM in _fired(_metrics(memory_only_sources=3, press_total=0))
    assert MEM not in _fired(_metrics(memory_only_sources=0, press_total=0))


def test_rf3183_live_babcock_grade_is_unchanged():
    """The live report: memory_only 0 -> 1, unverified 13 -> 12. Score must hold at
    75. Correct classification must not become a back-door grade change."""
    before = _metrics(memory_only_sources=0, unverified_sources=13)
    after = _metrics(memory_only_sources=1, unverified_sources=12)
    assert _score(before) == 75, _fired(before)
    assert _score(after) == 75, _fired(after)


def test_rf3183_self_citation_is_never_counted_as_reputable():
    """The whole point: ARIA quoting herself is not independent corroboration."""
    m = _metrics(memory_only_sources=1, verified_sources=0, quality_press=0,
                 own_site_sources=5, unverified_sources=0, press_total=6)
    reasons = _fired(m)
    assert any("reputable independent source" in r for r in reasons), (
        "with 0 verified and 0 quality press, a memory:// hit must not rescue the "
        f"reputable-source count: {reasons}")


def test_rf3183_other_penalties_are_untouched():
    """Nothing else in the penalty table may shift."""
    m = _metrics(sanctions_source_unavailable=True, export_control_checked=False)
    reasons = _fired(m)
    assert any("sanctions screen source was unavailable" in r for r in reasons)
    assert any("export-control" in r for r in reasons)
