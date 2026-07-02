"""R-F2340 capability tests — MEASURED per-cell freshness in the coverage heatmap.

Drives the real build (`_build_heatmap_uncached`, cache-bypassed) with facts that
carry recent vs old timestamps and asserts the cell's staleness is a genuine
measurement (recent fact → fresh, old fact → stale), replacing the R-F2332
"everything unknown" honest-but-blind state. A fact with no timestamp stays
honestly unknown.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta


def _run_uncached(fact: dict) -> dict:
    """Build the heatmap over a single crafted fact, isolated from live state.
    Monkeypatches the corpus + freshness + signal sources, calls the uncached
    builder (no 3-layer cache), and restores everything."""
    from aria_service.intel import coverage_heatmap as ch
    from aria_service.intel import knowledge as kb
    from aria_service.intel import learning_progress as lp
    from aria_service.intel import intel_ledger as il

    orig_facts = getattr(kb, "all_facts", None)
    orig_lp = lp.get_all_domains
    orig_recent = getattr(il, "get_recent", None)

    def _facts():
        return [fact]

    async def _lp_empty():
        return []  # no learning_progress override → isolate corpus-derived path

    def _il_empty():
        return []

    kb.all_facts = _facts
    lp.get_all_domains = _lp_empty
    if orig_recent is not None:
        il.get_recent = _il_empty
    try:
        return asyncio.run(ch._build_heatmap_uncached())
    finally:
        if orig_facts is not None:
            kb.all_facts = orig_facts
        lp.get_all_domains = orig_lp
        if orig_recent is not None:
            il.get_recent = orig_recent


_MATCH_CONTENT = "sanctions screening united states export control"  # → sanctions_screening × US


def test_rf2340_recent_fact_is_measured_fresh():
    now = datetime.now(timezone.utc).isoformat()
    hm = _run_uncached({"content": _MATCH_CONTENT, "topic": "sanctions", "updatedAt": now})
    cell = hm["matrix"]["sanctions_screening"]["US"]
    assert cell["fact_count"] >= 1, "crafted fact must land in sanctions_screening/US"
    assert cell["is_stale"] is False, "a just-now fact must read FRESH, not unknown/stale"
    assert cell["freshness_known"] is True
    assert cell["hours_since_refresh"] is not None and cell["hours_since_refresh"] < 24
    # measured, not the old phantom-100%-stale nor all-unknown
    assert hm["summary"]["stale_cells"] == 0


def test_rf2340_old_fact_is_measured_stale():
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    hm = _run_uncached({"content": _MATCH_CONTENT, "topic": "sanctions", "updatedAt": old})
    cell = hm["matrix"]["sanctions_screening"]["US"]
    assert cell["fact_count"] >= 1
    # sanctions_screening window is 24h; 400 days is far past it → STALE (measured)
    assert cell["is_stale"] is True
    assert cell["freshness_known"] is True
    summ = hm["summary"]
    assert summ["stale_cells"] >= 1, "an old-fact cell must count toward stale_cells"
    assert summ["staleness_unknown_cells"] < summ["cells"], "not everything is unknown anymore"


def test_rf2340_fact_without_timestamp_stays_unknown():
    # No updatedAt/createdAt → freshness genuinely cannot be measured → unknown (honest).
    hm = _run_uncached({"content": _MATCH_CONTENT, "topic": "sanctions"})
    cell = hm["matrix"]["sanctions_screening"]["US"]
    assert cell["fact_count"] >= 1
    assert cell["is_stale"] is None, "no dated facts → unknown, not a fabricated fresh/stale"
    assert cell["freshness_known"] is False


def test_rf2340_absent_cell_is_unknown_not_stale():
    # A cell with zero facts must be unknown-freshness (None), never fabricated stale.
    now = datetime.now(timezone.utc).isoformat()
    hm = _run_uncached({"content": _MATCH_CONTENT, "topic": "sanctions", "updatedAt": now})
    # weapon_systems × Japan gets no facts from our single crafted fact
    empty = hm["matrix"]["weapon_systems"]["Japan"]
    assert empty["fact_count"] == 0
    assert empty["is_stale"] is None
    assert empty["freshness_known"] is False
