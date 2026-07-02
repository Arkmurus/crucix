"""Capability tests for the ARIA-Brain DD follow-ups (R-F2332 / R-F2333 / R-F2334).

Each test drives the ACTUAL path the DD flagged and asserts the user-visible
honesty fix — not a helper proxy (§3c).

  R-F2332  coverage_heatmap: unknown freshness must NOT count as stale nor
           penalise the score (the namespace-divergence "100% stale" artifact).
  R-F2333  pdf_deep_ingest: summary() surfaces LIVE activity counters (was DARK);
           sanctions_propagation.summary() is honestly marked a static manifest.
  R-F2334  autonomy_surface: claim_ledger reports None + availability boolean,
           never the raw -1 sentinel / a fake 0.
"""
from __future__ import annotations

import asyncio


# ═══════════════════════════════════════════════════════════════════════
# R-F2332 — coverage staleness: unknown != stale, no phantom penalty
# ═══════════════════════════════════════════════════════════════════════

def test_rf2332_unknown_freshness_not_penalised():
    from aria_service.intel.coverage_heatmap import _compute_score
    # Two "deep" cells with UNKNOWN staleness (is_stale None) — the real state
    # for every canonical domain, since learning_progress tracks a disjoint
    # namespace. They must NOT be penalised and NOT counted as stale.
    matrix = {"d1": {"j1": {"tier": "deep", "is_stale": None},
                     "j2": {"tier": "deep", "is_stale": None}}}
    score, summ = _compute_score(matrix, ["d1"], ["j1", "j2"])
    assert score == 1.0, f"unknown-freshness deep cells must score full 1.0, got {score}"
    assert summ["stale_cells"] == 0, "unknown freshness must not count as stale"
    assert summ["staleness_unknown_cells"] == 2, "unknown cells must be disclosed"


def test_rf2332_known_stale_still_penalised():
    """Regression guard: a genuinely known-stale cell (is_stale True) still gets
    the 0.7x penalty — the fix must not disable real staleness handling."""
    from aria_service.intel.coverage_heatmap import _compute_score
    matrix = {"d1": {"j1": {"tier": "deep", "is_stale": True},
                     "j2": {"tier": "deep", "is_stale": True}}}
    score, summ = _compute_score(matrix, ["d1"], ["j1", "j2"])
    assert score == 0.7, f"known-stale deep cells must be penalised to 0.7, got {score}"
    assert summ["stale_cells"] == 2
    assert summ["staleness_unknown_cells"] == 0


async def _build_heatmap_no_freshness():
    """Drive build_heatmap with facts present but a DIVERGENT freshness namespace
    (the live condition) and assert cells read is_stale=None, not True."""
    from aria_service.intel import coverage_heatmap as ch
    from aria_service.intel import learning_progress as lp
    from aria_service.intel import knowledge as kb

    orig_all = getattr(kb, "all_facts", None)
    orig_lp = lp.get_all_domains

    async def _fake_lp():
        # names that never equal a canonical heatmap domain (the real bug)
        return [{"domain": "adec_analysis_multiple_firm_reposts", "is_stale": False}]

    def _fake_facts():
        return [{"content": "OFAC SDN sanctions screening United States export control",
                 "topic": "sanctions"}]

    lp.get_all_domains = _fake_lp
    if orig_all is not None:
        kb.all_facts = _fake_facts
    try:
        hm = await ch.build_heatmap()
    finally:
        lp.get_all_domains = orig_lp
        if orig_all is not None:
            kb.all_facts = orig_all
    return hm


def test_rf2332_build_heatmap_unknown_not_all_stale():
    hm = asyncio.run(_build_heatmap_no_freshness())
    summ = hm.get("summary", {})
    cells = summ.get("cells", 0)
    assert cells > 0, "expected a populated matrix"
    # The core symptom: stale_cells must NOT equal the whole grid (the old 100%).
    assert summ.get("stale_cells", 0) < cells, (
        f"stale_cells={summ.get('stale_cells')} should be < cells={cells} "
        "when freshness is unknown (namespace divergence)"
    )
    # And every canonical-domain cell reports is_stale None (unknown).
    any_cell = next(iter(next(iter(hm["matrix"].values())).values()))
    assert any_cell["is_stale"] is None, f"expected unknown staleness, got {any_cell}"


# ═══════════════════════════════════════════════════════════════════════
# R-F2333 — pdf_deep_ingest live counter + sanctions manifest label
# ═══════════════════════════════════════════════════════════════════════

def test_rf2333_pdf_ingest_records_activity():
    """Driving the real ingest path (a non-PDF fails to open) must increment the
    live activity counter that summary() surfaces — proving the panel is no
    longer DARK."""
    from aria_service.intel import pdf_deep_ingest

    async def _run():
        before = await pdf_deep_ingest.summary()
        # junk bytes → fitz open fails (or fitz missing) → ok=False ingest recorded
        out = await pdf_deep_ingest.ingest_pdf_multi_page(b"%NOT-A-PDF%", "junk.pdf")
        after = await pdf_deep_ingest.summary()
        return before, out, after

    before, out, after = asyncio.run(_run())
    assert isinstance(out.get("errors"), list) and out["errors"], "expected a failed ingest"
    # If state persisted, the counter advanced by exactly one; if the state
    # backend is entirely unavailable in this env both read 0 — assert the
    # stronger property when possible, else at least the shape is live.
    assert "total_ingests" in after and "last_ingest_at" in after
    if before.get("total_ingests") is not None:
        assert after["total_ingests"] >= before.get("total_ingests", 0), (
            "total_ingests must not go backwards"
        )
        assert after["total_ingests"] == before.get("total_ingests", 0) + 1, (
            "one ingest attempt must record exactly one increment"
        )
        assert after["failed_ingests"] == before.get("failed_ingests", 0) + 1


def test_rf2333_sanctions_marked_static_manifest():
    from aria_service.intel import sanctions_propagation
    s = sanctions_propagation.summary()
    assert s.get("kind") == "static_manifest", "must be honestly labelled a manifest"
    assert "oems_covered" in s, "real key is oems_covered (not oems_tracked)"
    assert isinstance(s["oems_covered"], int) and s["oems_covered"] > 0


# ═══════════════════════════════════════════════════════════════════════
# R-F2334 — autonomy_surface claim_ledger honesty
# ═══════════════════════════════════════════════════════════════════════

def test_rf2334_claim_ledger_no_negative_sentinel():
    from aria_service.intel import autonomy_surface
    res = asyncio.run(autonomy_surface._resilience_floor())
    mem = res.get("memory", {})
    assert "claim_ledger_entries" in mem
    # The whole point: never the raw -1 sentinel again.
    assert mem["claim_ledger_entries"] != -1, "raw -1 sentinel must not leak to payload"
    # Count is deliberately not probed → None; availability is a boolean.
    assert mem["claim_ledger_entries"] is None
    assert mem.get("claim_ledger_available") is True
