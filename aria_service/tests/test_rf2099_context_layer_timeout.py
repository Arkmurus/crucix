"""R-F2099 — the 7-layer chat context build must not wedge on a hung retrieval
layer. Live incident 2026-06-28: substantive/long chat messages (and document
reviews, which use the same builder) hung >200s while the fast-lane single-LLM
path answered in ~2s. Root cause: _build_7_layer_context used `as_completed`
with NO timeout inside a `with ThreadPoolExecutor` whose shutdown(wait=True)
blocked on a hung layer (mem0's O(all-facts) scan). Now a per-build layer budget
takes whatever finished and proceeds without the slow layer(s).
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as _FT


def test_rf2099_layer_budget_pattern_returns_on_hung_task():
    """The core mechanism: as_completed(timeout) + shutdown(wait=False) returns
    promptly even when one worker hangs, keeping the fast results."""
    pool = ThreadPoolExecutor(max_workers=3)
    results = {}
    t0 = time.monotonic()
    try:
        futs = {
            pool.submit(lambda: time.sleep(30)): "hung",
            pool.submit(lambda: "A"): "fast_a",
            pool.submit(lambda: "B"): "fast_b",
        }
        try:
            for f in as_completed(futs, timeout=1.0):
                results[futs[f]] = f.result()
        except _FT:
            pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    elapsed = time.monotonic() - t0
    assert elapsed < 5, f"must not block on the hung task; took {elapsed:.1f}s"
    assert results.get("fast_a") == "A" and results.get("fast_b") == "B"
    assert "hung" not in results


def test_rf2099_build_7_layer_does_not_wedge_on_hung_layer(monkeypatch):
    """Integration: a hung layer (mem0) must not wedge _build_7_layer_context.
    Budget set to 2s; the build must return well under the old >200s hang."""
    monkeypatch.setenv("ARIA_CONTEXT_LAYER_BUDGET_S", "2")
    import aria_service.intel.mem0 as mem0_mod
    monkeypatch.setattr(mem0_mod, "retrieve_for_query", lambda q: (time.sleep(60), "")[1])
    from aria_service import aria_engine
    t0 = time.monotonic()
    out = aria_engine._build_7_layer_context("a query about contract commission clauses", None)
    elapsed = time.monotonic() - t0
    assert elapsed < 30, f"build must not wedge on the hung mem0 layer; took {elapsed:.1f}s"
    assert isinstance(out, str)
