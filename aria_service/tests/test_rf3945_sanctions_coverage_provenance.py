"""C-39 / R-F3945 — a never-SEARCHED sanctions list must never render CLEAN.

THE DEFECT, measured live 2026-08-12. OpenSanctions' monthly plan quota has been
spent since 2026-07-31, so R-F3529's local canonical floor serves every screen.
That floor holds exactly TWO sources — `ofac_sdn` and `eu_consolidated` (the
loader registry, `sanctions_canonical.lookup._expected_sources`). But
`derive_verified_sources` is BINARY: given `screen_succeeded=True` it stamps ALL
TEN canonical sources `status: CLEAN, via: "opensanctions_aggregate"`.

So eight lists nothing queried — OFAC NS-CMIC, OFAC SSI, BIS Entity List, BIS
Military End User, UK OFSI/HMT, UN SC Consolidated, NDAA 1260H, DoD 1233 — were
reported to the customer as CLEAN, attributed to the aggregator that had refused
us. That is a false clean, the one output a compliance tool must never produce.

WHY IT SURVIVED. R-F287's premise was correct WHEN WRITTEN: "OpenSanctions is an
aggregator, a clean response means all underlying sources were queried". R-F3529
later added a fallback that is NOT an aggregator, and this function was never
revisited. The escape hatch already existed — `unavailable_sources` — and had
NO CALLER anywhere in the tree, while `dd_orchestrator.py:3406` hardcoded
`screen_succeeded=True`. A guard that cannot fire (CLAUDE.md §1's "certified by
an absence", applied to the product's highest-stakes output).

THE FIX IS PROVENANCE, NOT A NEW LIST. `fuzzy_screen` now always reports which
sources it actually consulted, and ONE function maps that to the canonical names
that went unsearched — so the three call sites cannot drift apart (§1 R-F2639:
"there is ONE measure now, do not fork it again"). No hardcoded mapping is added:
the existing `_CANONICAL_SANCTIONS_SOURCES` slug table already carries
`ofac_sdn` and `eu_consolidated`.
"""
import asyncio

import pytest

from aria_service.intel import _sanctions_classify as sc


# ── The precise live scenario: OpenSanctions spent, local floor answers ──────

async def _quota_exhausted(*_a, **_kw):
    """Stand in for both OpenSanctions entry points at their real seam.

    ASYNC deliberately (§3b): `_opensanctions_match`/`_opensanctions_search` are
    coroutines, and a sync stub is swallowed by the caller's `except Exception`
    as "OpenSanctions crashed" — which reaches the same `source_ok=False` branch
    by the WRONG route and would let this test pass against a broken fix.
    """
    from aria_service.intel.sanctions import _SourceQuery
    return _SourceQuery([], False, "quota_exhausted")


def _local_store_answers_clear(name, *_a, **_kw):
    """The R-F3529 floor: the local canonical store genuinely answered CLEAR.

    This is the branch that sets `source_ok = True` — i.e. a PERFORMED screen.
    It is deliberately a real answer, not an error: the defect only appears when
    the screen SUCCEEDS against a narrower source set than the verdict claims.
    """
    return {"verdict": "CLEAR", "matches": [], "reason": None}


@pytest.fixture
def screen_on_local_floor(monkeypatch):
    """Drive the REAL fuzzy_screen with OpenSanctions dead and the floor live."""
    from aria_service.intel import sanctions as s

    monkeypatch.setattr(s, "_opensanctions_match", _quota_exhausted)
    monkeypatch.setattr(s, "_opensanctions_search", _quota_exhausted)

    import aria_service.intel.sanctions_canonical.lookup as _lookup
    monkeypatch.setattr(_lookup, "check_sanctions", _local_store_answers_clear)

    return lambda name="Rosoboronexport": asyncio.run(s.fuzzy_screen(name))


# ── 1. The screen must SAY what it actually consulted ────────────────────────

def test_fuzzy_screen_reports_which_sources_it_consulted(screen_on_local_floor):
    """Without provenance, no consumer can tell a 10-list screen from a 2-list one.

    This is the input the fix needs; everything below depends on it.
    """
    res = screen_on_local_floor()

    assert res.get("screened") is True, (
        "precondition: the local floor answered, so this IS a performed screen"
    )
    coverage = res.get("coverage")
    assert isinstance(coverage, dict), (
        "fuzzy_screen must ALWAYS report coverage provenance — not only on "
        "failure. A coverage block that appears only when something breaks is "
        "the same trap as `source_reasons`: the degraded-but-successful case, "
        "which is exactly this one, stays invisible."
    )
    assert coverage.get("mode") == "local_canonical_floor", (
        f"expected the R-F3529 floor to be named as the serving mode, got "
        f"{coverage.get('mode')!r}"
    )
    consulted = coverage.get("sources_consulted") or []
    assert set(consulted) == {"ofac_sdn", "eu_consolidated"}, (
        f"the floor holds exactly the loader registry, got {sorted(consulted)}"
    )


def test_full_aggregate_screen_reports_full_coverage(monkeypatch):
    """The healthy path must be UNCHANGED — no narrowing when OpenSanctions answers."""
    from aria_service.intel import sanctions as s
    from aria_service.intel.sanctions import _SourceQuery

    async def _ok(*_a, **_kw):
        return _SourceQuery([], True, "ok")

    monkeypatch.setattr(s, "_opensanctions_match", _ok)
    monkeypatch.setattr(s, "_opensanctions_search", _ok)

    res = asyncio.run(s.fuzzy_screen("Some Clean Company Ltd"))

    assert res.get("screened") is True
    assert (res.get("coverage") or {}).get("mode") == "opensanctions_aggregate"
    assert sc.unavailable_sources_for(res) == set(), (
        "a real aggregate screen covers every canonical source — the fix must "
        "not manufacture UNAVAILABLE rows on the healthy path"
    )


# ── 2. THE SYMPTOM: eight unsearched lists must not read CLEAN ───────────────

_NEVER_SEARCHED_BY_THE_FLOOR = [
    "OFAC NS-CMIC", "OFAC SSI", "BIS Entity List", "BIS Military End User",
    "UK OFSI / HMT", "UN SC Consolidated",
]


def test_unsearched_lists_are_UNAVAILABLE_not_CLEAN(screen_on_local_floor):
    """The capability test (§3c): the user-visible symptom, on the real path."""
    res = screen_on_local_floor()

    verified = sc.derive_verified_sources(
        res.get("matches") or [], screen_succeeded=True, screen=res,
    )

    for src in _NEVER_SEARCHED_BY_THE_FLOOR:
        assert src in verified, f"{src} missing from the canonical table"
        assert verified[src]["status"] == "UNAVAILABLE", (
            f"{src} was NEVER QUERIED — the local floor holds only OFAC SDN and "
            f"EU Consolidated — yet it reports {verified[src]['status']!r}. "
            f"A list nothing searched cannot be clean."
        )
        assert verified[src]["via"] != "opensanctions_aggregate", (
            f"{src} is attributed to the aggregator that refused us "
            f"(via={verified[src]['via']!r}). The attribution must name what "
            f"actually ran, or the reader cannot audit the claim."
        )


def test_the_two_lists_the_floor_DOES_hold_still_read_clean(screen_on_local_floor):
    """The fix must not over-correct: a genuinely searched list stays CLEAN.

    Blanking every source would trade a false clean for a useless report, and
    would hide that OFAC and EU coverage never lapsed.
    """
    res = screen_on_local_floor()
    verified = sc.derive_verified_sources(
        res.get("matches") or [], screen_succeeded=True, screen=res,
    )

    for src in ("OFAC SDN", "EU Consolidated"):
        assert verified[src]["status"] == "CLEAN", (
            f"{src} IS held by the local floor and was genuinely searched"
        )
        assert verified[src]["via"] == "local_canonical", (
            f"{src} was cleared by the local store, not by OpenSanctions — the "
            f"provenance must say so"
        )


# ── 3. The mapper itself — one measure, so the call sites cannot drift ───────

def test_unavailable_sources_for_is_the_single_measure():
    """§3b — the function the three dd_orchestrator call sites must share."""
    assert callable(getattr(sc, "unavailable_sources_for", None)), (
        "unavailable_sources_for() must exist in _sanctions_classify so all "
        "callers derive the set the SAME way (§1 R-F2639)"
    )


def test_locally_covered_sources_names_what_the_floor_actually_cleared():
    """The other half of the split — it drives the `via:` attribution.

    Without it a CLEAN row cleared by the local store would still read
    `via: opensanctions_aggregate`, i.e. credited to a source that refused us.
    That is a quieter defect than the false clean, and the same class.
    """
    degraded = {"screened": True, "matches": [],
                "coverage": {"mode": "local_canonical_floor",
                             "sources_consulted": ["ofac_sdn", "eu_consolidated"]}}
    local = sc.locally_covered_sources_for(degraded)
    assert local == {"OFAC SDN", "EU Consolidated"}, (
        f"the floor cleared these two and nothing else, got {sorted(local)}"
    )
    # The two halves must PARTITION the canonical table — no source may be both
    # cleared-by-local and never-searched, and none may fall through the gap.
    unavailable = sc.unavailable_sources_for(degraded)
    assert not (local & unavailable), "a source cannot be both cleared and unsearched"
    assert local | unavailable == set(sc._CANONICAL_SANCTIONS_SOURCES), (
        "every canonical source must land on exactly one side of the split"
    )


def test_locally_covered_is_empty_on_the_healthy_path():
    """An aggregate screen has no locally-cleared rows — attribution stays as it was."""
    assert sc.locally_covered_sources_for(
        {"screened": True, "coverage": {"mode": "opensanctions_aggregate",
                                        "sources_consulted": []}}) == set()
    assert sc.locally_covered_sources_for({"screened": True}) == set()


def test_missing_coverage_is_treated_as_unknown_not_as_full_coverage():
    """An absent coverage block must never be read as 'everything was checked'.

    A screen result from an older cache, or from a caller that has not been
    updated, must degrade to the SAFE reading. This is the absence-collapsing-
    into-a-measurement class CLAUDE.md §1 records three times.
    """
    legacy = {"screened": True, "matches": []}          # no coverage key
    assert sc.unavailable_sources_for(legacy) == set(), (
        "a legacy full-aggregate result (the historical shape) keeps its "
        "existing meaning — full coverage — so this fix cannot silently "
        "rewrite past behaviour"
    )

    degraded = {"screened": True, "matches": [],
                "coverage": {"mode": "local_canonical_floor",
                             "sources_consulted": ["ofac_sdn"]}}
    out = sc.unavailable_sources_for(degraded)
    assert "EU Consolidated" in out, (
        "a floor that lost EU coverage must mark EU Consolidated unavailable"
    )
    assert "OFAC SDN" not in out


def test_screen_that_did_not_run_still_marks_everything_unavailable(monkeypatch):
    """Regression guard on the pre-existing R-F1696 behaviour."""
    from aria_service.intel import sanctions as s

    monkeypatch.setattr(s, "_opensanctions_match", _quota_exhausted)
    monkeypatch.setattr(s, "_opensanctions_search", _quota_exhausted)

    import aria_service.intel.sanctions_canonical.lookup as _lookup
    monkeypatch.setattr(_lookup, "check_sanctions",
                        lambda *a, **k: {"verdict": "INSUFFICIENT_DATA",
                                         "matches": [], "reason": "store_unavailable"})

    res = asyncio.run(s.fuzzy_screen("Nobody Ltd"))

    assert res.get("source_unavailable") is True
    assert res.get("screened") is False
    verified = sc.derive_verified_sources(
        res.get("matches") or [], screen_succeeded=False, screen=res,
    )
    assert all(v["status"] == "UNAVAILABLE" for v in verified.values())
