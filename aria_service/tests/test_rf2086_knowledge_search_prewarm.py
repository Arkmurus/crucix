"""R-F2086 — knowledge search cold-cache prewarm + tighter GIL yield.

search_knowledge() builds a per-fact lowercased-text cache (_search_lc) on its
FIRST scan. That cold build is GIL-bound and stalled the event loop ~5s post-deploy
(live wedge stack) when it ran inside the 7-layer-context worker pool on a chat
turn. Fix: a boot prewarm warms the cache off the request path, and the in-scan
yield now fires every 256 facts (was 2048). These tests drive the real
search_knowledge and assert the cache-warming contract that makes the prewarm work.
"""
import aria_service.intel.knowledge as K


def _seed_facts(n):
    K._cache = {"facts": [
        {"id": f"f{i}", "topic": f"topic {i}", "content": f"alpha beta gamma procurement angola {i}",
         "confidence": "ASSESSED", "accessCount": 0, "updatedAt": "2026-01-01"}
        for i in range(n)
    ]}
    K._search_lc.clear()
    K._search_lc_facts_id = None


def test_rf2086_first_scan_warms_the_whole_cache():
    """One search_knowledge() call must populate _search_lc for every fact — that
    is what lets a single boot prewarm cover the whole knowledge base."""
    _seed_facts(500)
    assert len(K._search_lc) == 0, "cache starts cold"
    K.search_knowledge("procurement")
    assert len(K._search_lc) == 500, "first scan must cache lowercased text for ALL facts"


def test_rf2086_warm_scan_reuses_cache_no_rebuild():
    """After warming, a second call must hit the cache (no cold rebuild) — so user
    requests after the boot prewarm never pay the GIL-bound cold scan."""
    _seed_facts(300)
    K.search_knowledge("warmup")                 # cold → builds cache
    # snapshot the cached tuple identities; a rebuild would replace them
    before = {fid: id(v) for fid, v in K._search_lc.items()}
    K.search_knowledge("angola procurement")     # warm → must reuse
    after = {fid: id(v) for fid, v in K._search_lc.items()}
    assert before == after, "warm scan must reuse cached entries, not rebuild them"


def test_rf2086_cache_invalidates_when_facts_object_replaced():
    """Reloading the facts list (new object) must clear the cache so stale/removed
    ids don't linger — the prewarm then re-warms on the next scan."""
    _seed_facts(50)
    K.search_knowledge("procurement")   # words must be len>2 to trigger a scan
    assert len(K._search_lc) == 50
    # simulate a reload: replace the facts list object
    K._cache = {"facts": [{"id": "new1", "topic": "t", "content": "fresh content",
                           "confidence": "ASSESSED", "accessCount": 0, "updatedAt": "2026-02-02"}]}
    K.search_knowledge("fresh")
    assert "f0" not in K._search_lc and "new1" in K._search_lc, "cache must rebuild for the new facts object"
