"""R-F728 (2026-05-20) — regression tests for the build_heatmap wedge fix.

Wedge round 8 captured a 186.89s main-thread stall in
`coverage_heatmap._count_facts_for_cell` driven by the
`/api/aria/learning/coverage` route. Pre-R-F728 shape:
  - 28 domains × 31 jurisdictions = 868 cells
  - Each cell `await`ed `_count_facts_for_cell(d, j)` which:
      a) re-fetched the entire facts list (~55k facts in prod)
      b) awaited a fresh `intel_ledger.get_recent()`
      c) iterated everything synchronously on the event loop
  - Total: 868 × ~60k items = ~50M iterations on the loop, plus 868
    duplicate fetches.

Post-R-F728: facts + signals are fetched ONCE in `build_heatmap`, the
whole nested cell loop runs in a single `asyncio.to_thread` worker.
The event loop is free during the compute.

These tests cover:
  1. `_count_facts_for_cell_sync` produces the same shape + counts as
     the legacy async `_count_facts_for_cell` for identical input.
  2. `build_heatmap` returns the expected matrix shape end-to-end.
  3. The loop stays responsive WHILE `build_heatmap` is running — a
     concurrent `asyncio.sleep(0)` heartbeat completes well before
     the compute finishes. (This is the wedge-prevention assertion.)
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch


# Sample data — small enough for unit test, structured like prod.
_SAMPLE_FACTS = [
    {"entity": "OFAC", "topic": "sanctions", "content": "Saudi Arabia SDN list update", "source": "treasury.gov"},
    {"entity": "EU", "topic": "sanctions", "content": "Russia restrictive measures", "source": "europa.eu"},
    {"entity": "UK", "topic": "export_controls", "content": "license decisions Israel", "source": "gov.uk"},
    {"entity": "FCDO", "topic": "compliance", "content": "screening procedure Iran", "source": "fcdo"},
    {"entity": "US", "topic": "procurement", "content": "Turkey contract award", "source": "sam.gov"},
]

_SAMPLE_SIGNALS = [
    {"source": "ofac.sdn", "summary": "SDN updated", "entity": "Iran", "topic": "sanctions"},
    {"source": "news", "summary": "Turkey arms deal", "entity": "Turkey", "topic": "procurement"},
]


def test_sync_scorer_matches_async_scorer():
    """R-F728: the new sync scorer produces the same counts the legacy
    async path produced for the same inputs. Regression guard against
    accidental matcher-semantics drift."""
    from aria_service.intel import coverage_heatmap as ch

    domain = "sanctions_screening"
    jurisdiction = "saudi_arabia"

    sync_result = ch._count_facts_for_cell_sync(
        domain, jurisdiction, _SAMPLE_FACTS, _SAMPLE_SIGNALS,
    )

    # Run the legacy async path with the same data via mocked module fns
    fake_k = type("K", (), {"all_facts": staticmethod(lambda: _SAMPLE_FACTS)})()
    fake_il = type("IL", (), {"get_recent": staticmethod(lambda: _SAMPLE_SIGNALS)})()

    async def _run():
        with patch.object(ch, "_count_facts_for_cell_sync", wraps=ch._count_facts_for_cell_sync) as spy, \
             patch.dict("sys.modules", {
                 "aria_service.intel.knowledge": fake_k,
                 "aria_service.intel.intel_ledger": fake_il,
             }):
            return await ch._count_facts_for_cell(domain, jurisdiction)

    async_result = asyncio.run(_run())
    assert sync_result == async_result, (
        f"R-F728: sync scorer diverged from async — sync={sync_result} async={async_result}"
    )


def test_build_heatmap_returns_expected_shape():
    """R-F728: end-to-end shape regression. Mock the data fetchers so the
    matrix has predictable cells and the output keys match the contract
    the route handler expects."""
    from aria_service.intel import coverage_heatmap as ch

    # R-F781 (2026-05-21) — build_heatmap is now wrapped in a 120s TTL
    # cache + single-flight future. Clear it so each test sees a fresh
    # compute (otherwise a prior test in the same session populates the
    # cache and this one returns the stale result without invoking the
    # patched data fetchers).
    ch.invalidate_heatmap_cache()

    fake_k = type("K", (), {"all_facts": staticmethod(lambda: _SAMPLE_FACTS)})()
    fake_il = type("IL", (), {"get_recent": staticmethod(lambda: _SAMPLE_SIGNALS)})()

    async def _fake_get_all_domains():
        return []

    fake_lp = type("LP", (), {"get_all_domains": staticmethod(_fake_get_all_domains)})()

    async def _run():
        with patch.dict("sys.modules", {
            "aria_service.intel.knowledge": fake_k,
            "aria_service.intel.intel_ledger": fake_il,
            "aria_service.intel.learning_progress": fake_lp,
        }):
            return await ch.build_heatmap()

    result = asyncio.run(_run())
    assert set(result.keys()) >= {
        "domains", "jurisdictions", "matrix", "summary", "coverage_score",
    }
    # 28 default domains × 31 default jurisdictions (from DOMAINS/JURISDICTIONS)
    assert len(result["domains"]) >= 17
    assert len(result["jurisdictions"]) >= 31
    # Spot-check that the matrix has all cells
    for d in result["domains"]:
        assert d in result["matrix"]
        for j in result["jurisdictions"]:
            cell = result["matrix"][d][j]
            assert "fact_count" in cell
            assert "signal_count" in cell
            assert "tier" in cell


def test_build_heatmap_runs_matrix_compute_in_to_thread():
    """R-F728 STRUCTURAL TEST: the wedge fix is implemented by running
    the matrix compute in `asyncio.to_thread`. A timing-based heartbeat
    assertion would be too fragile (depends on facts-list size in test
    env). Instead, spy on `asyncio.to_thread` and assert it's called
    with a callable whose name resolves to the matrix-compute closure.

    Pre-R-F728 the cell loop ran on the event loop directly; no
    `to_thread` call was made. Any future refactor that drops the
    `to_thread` wrapping would put 868 cells × 55k facts back on the
    loop — caught by this test."""
    from aria_service.intel import coverage_heatmap as ch

    # R-F781 — clear the TTL cache so this test exercises the compute
    # path (not the cached return) and the to_thread spy below fires.
    ch.invalidate_heatmap_cache()

    fake_k = type("K", (), {"all_facts": staticmethod(lambda: _SAMPLE_FACTS)})()
    fake_il = type("IL", (), {"get_recent": staticmethod(lambda: _SAMPLE_SIGNALS)})()

    async def _fake_get_all_domains():
        return []

    fake_lp = type("LP", (), {"get_all_domains": staticmethod(_fake_get_all_domains)})()

    to_thread_calls: list[str] = []
    real_to_thread = asyncio.to_thread

    async def _spy(fn, *args, **kwargs):
        to_thread_calls.append(getattr(fn, "__name__", repr(fn)))
        return await real_to_thread(fn, *args, **kwargs)

    async def _run():
        with patch.dict("sys.modules", {
            "aria_service.intel.knowledge": fake_k,
            "aria_service.intel.intel_ledger": fake_il,
            "aria_service.intel.learning_progress": fake_lp,
        }), patch.object(ch, "asyncio", asyncio), \
             patch("asyncio.to_thread", new=_spy):
            return await ch.build_heatmap()

    result = asyncio.run(_run())
    assert result["matrix"], "build_heatmap returned empty matrix"
    # The matrix compute must have been dispatched to a worker thread.
    assert any("_compute_matrix_sync" in name for name in to_thread_calls), (
        f"R-F728 regression: asyncio.to_thread was not called with the matrix "
        f"compute closure during build_heatmap. Calls observed: {to_thread_calls}. "
        f"The matrix compute MUST run via to_thread or the event loop wedges "
        f"under production data volume (wedge_674 captured 186.89s here)."
    )


# ════════════════════════════════════════════════════════════════════════════
# R-F928 (2026-05-27) — wedge_673 fix: precompute fact/signal text ONCE per
# fact (was rebuilt per (fact × cell) ≈ 57M string-joins on a 67k-fact corpus,
# pinning the GIL ~18.6s and stalling the event loop) + GIL-yield per row.
# ════════════════════════════════════════════════════════════════════════════

def test_rf928_precompute_matches_legacy_scorer():
    """R-F928 EQUIVALENCE: the precompute path (`_count_from_texts` over
    pre-built lowercased text) yields counts IDENTICAL to the legacy
    per-cell scorer (`_count_facts_for_cell_sync`) for EVERY domain ×
    jurisdiction cell. Guards the wedge fix against changing matrix values."""
    from aria_service.intel import coverage_heatmap as ch

    fact_texts = [ch._fact_text(f) for f in _SAMPLE_FACTS if isinstance(f, dict)]
    signal_texts = [ch._signal_text(s) for s in _SAMPLE_SIGNALS if isinstance(s, dict)]

    for d in ch.DOMAINS:
        dom_tokens = ch._domain_tokens(d)
        for j in ch.JURISDICTIONS:
            jur_syn = ch._juris_synonyms(j)
            new = ch._count_from_texts(fact_texts, signal_texts, dom_tokens, jur_syn)
            legacy = ch._count_facts_for_cell_sync(d, j, _SAMPLE_FACTS, _SAMPLE_SIGNALS)
            assert new == legacy, (
                f"R-F928 diverged at {d}×{j}: precompute={new} legacy={legacy}"
            )


def test_rf928_yields_gil_during_compute():
    """R-F928/R-F931: `_compute_matrix_sync` must call `time.sleep(0)` during
    the heavy loop so the worker thread releases the GIL and can't starve the
    asyncio event loop (the wedge_673/wedge_675 failure mode). R-F931 moved the
    yield into the per-item `_tally` (every 1024 items), so a small test corpus
    yields a few times; lower-bound assertion: at least one yield occurred."""
    from aria_service.intel import coverage_heatmap as ch

    ch.invalidate_heatmap_cache()
    fake_k = type("K", (), {"all_facts": staticmethod(lambda: _SAMPLE_FACTS)})()
    fake_il = type("IL", (), {"get_recent": staticmethod(lambda: _SAMPLE_SIGNALS)})()

    async def _fake_get_all_domains():
        return []

    fake_lp = type("LP", (), {"get_all_domains": staticmethod(_fake_get_all_domains)})()

    zero_sleeps = {"n": 0}
    real_sleep = time.sleep

    def _spy_sleep(s):
        if s == 0:
            zero_sleeps["n"] += 1
        return real_sleep(s)

    async def _run():
        with patch.dict("sys.modules", {
            "aria_service.intel.knowledge": fake_k,
            "aria_service.intel.intel_ledger": fake_il,
            "aria_service.intel.learning_progress": fake_lp,
        }), patch.object(ch, "_HEATMAP_DISK_PATH", ""), \
             patch.object(ch.time, "sleep", _spy_sleep):
            return await ch.build_heatmap()

    result = asyncio.run(_run())
    assert result["matrix"], "build_heatmap returned empty matrix"
    assert zero_sleeps["n"] >= 1, (
        f"R-F931 regression: the matrix compute never yielded the GIL "
        f"(time.sleep(0) in _tally), got {zero_sleeps['n']}. Without periodic "
        f"yields the worker thread can monopolize the GIL and wedge the loop."
    )


# ════════════════════════════════════════════════════════════════════════════
# R-F931 (2026-05-27) — inverted-index matcher + disk-persist (cold-boot wedge).
# ════════════════════════════════════════════════════════════════════════════

def test_rf931_inverted_index_matches_legacy_counts():
    """R-F931 EQUIVALENCE: the inverted-index matrix produces fact_count /
    signal_count IDENTICAL to the legacy per-cell scorer
    (`_count_facts_for_cell_sync`) for EVERY cell. Guards the cold-boot-wedge
    fix (facts×(D+J) inverted tally) against changing matrix values."""
    from aria_service.intel import coverage_heatmap as ch

    # Facts crafted to actually populate cells (need ALL domain tokens + a
    # jurisdiction synonym in one text), so the equivalence check exercises
    # non-zero counts, not just an all-absent matrix.
    rich_facts = [
        {"content": "sanctions screening review for Saudi Arabia entity"},      # sanctions_screening × Saudi Arabia
        {"content": "fcpa enforcement action involving Brazil officials"},      # fcpa_enforcement × Brazil
        {"topic": "weapon systems", "content": "weapon systems exported to UAE"},  # weapon_systems × UAE
        {"content": "sanctions screening hit, Nigeria and Saudi Arabia"},       # sanctions_screening × Nigeria + Saudi Arabia
    ]
    rich_signals = [
        {"summary": "procurement pipeline award in Nigeria", "source": "s"},    # procurement_pipeline × Nigeria
    ]
    ch.invalidate_heatmap_cache()
    fake_k = type("K", (), {"all_facts": staticmethod(lambda: rich_facts)})()
    fake_il = type("IL", (), {"get_recent": staticmethod(lambda: rich_signals)})()

    async def _fake_get_all_domains():
        return []

    fake_lp = type("LP", (), {"get_all_domains": staticmethod(_fake_get_all_domains)})()

    async def _run():
        with patch.dict("sys.modules", {
            "aria_service.intel.knowledge": fake_k,
            "aria_service.intel.intel_ledger": fake_il,
            "aria_service.intel.learning_progress": fake_lp,
        }), patch.object(ch, "_HEATMAP_DISK_PATH", ""):
            return await ch.build_heatmap()

    result = asyncio.run(_run())
    diverged = []
    for d in result["domains"]:
        for j in result["jurisdictions"]:
            cell = result["matrix"][d][j]
            legacy = ch._count_facts_for_cell_sync(d, j, rich_facts, rich_signals)
            if (cell["fact_count"], cell["signal_count"]) != legacy:
                diverged.append((d, j, (cell["fact_count"], cell["signal_count"]), legacy))
    assert not diverged, f"R-F931 inverted-index diverged from legacy at: {diverged[:5]}"
    # Sanity: at least one non-zero cell so the test actually exercised matches.
    nonzero = sum(
        1 for d in result["domains"] for j in result["jurisdictions"]
        if result["matrix"][d][j]["fact_count"] or result["matrix"][d][j]["signal_count"]
    )
    assert nonzero > 0, "R-F931 test fixture produced an all-zero matrix — not exercising matches"


def test_rf931_disk_persist_roundtrip(tmp_path, monkeypatch):
    """R-F931: _save_disk_cache writes the matrix and _load_disk_cache reads it
    back within TTL; a stale entry returns None. Cold-start seed serves disk."""
    from aria_service.intel import coverage_heatmap as ch

    cache_file = tmp_path / "heatmap.json"
    monkeypatch.setattr(ch, "_HEATMAP_DISK_PATH", str(cache_file))
    key = (None, None)
    payload = {"matrix": {"d": {"j": {"fact_count": 3}}}, "domains": ["d"], "jurisdictions": ["j"]}

    # Save → load round-trip (fresh)
    monkeypatch.setattr(ch, "_HEATMAP_DISK_TTL_S", 3600.0)
    ch._save_disk_cache(key, payload)
    assert cache_file.exists()
    loaded = ch._load_disk_cache(key)
    assert loaded is not None and loaded["matrix"]["d"]["j"]["fact_count"] == 3

    # Stale (TTL=0) → None
    monkeypatch.setattr(ch, "_HEATMAP_DISK_TTL_S", 0.0)
    assert ch._load_disk_cache(key) is None

    # Filtered key never persists/loads (only default matrix)
    monkeypatch.setattr(ch, "_HEATMAP_DISK_TTL_S", 3600.0)
    assert ch._load_disk_cache((("sanctions_screening",), None)) is None
