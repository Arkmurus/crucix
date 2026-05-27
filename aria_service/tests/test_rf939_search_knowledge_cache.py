"""R-F939 (2026-05-27) — search_knowledge lowercased-text cache.

search_knowledge rebuilt `f"{topic} {content}".lower()` for every fact on every
chat query (the 7-layer-context "knowledge" layer). At ~67k facts that pure-Python
string build was a CPU hog that contended for the GIL with the autonomous
chain_correlator + the knowledge disk-write → 5-6s event-loop stalls (wedge_672,
2026-05-27 10:15-10:22). R-F939 caches the lc text per fact id (keyed on content
length; cleared when the facts list is replaced).

These tests prove the cache does NOT change results (same matches as the uncached
build), rebuilds on an in-place content edit, and clears on reload.
"""
from __future__ import annotations


def _seed(k, facts):
    k._cache = {"facts": facts, "queries": [], "learnings": [], "version": 1}
    k._search_lc.clear()
    k._search_lc_facts_id = 0


def test_rf939_search_cache_correct_rebuild_and_clear():
    from aria_service.intel import knowledge as k

    orig_cache = k._cache
    orig_lc = dict(k._search_lc)
    orig_id = k._search_lc_facts_id
    try:
        facts = [
            {"id": "a1", "topic": "sanctions", "content": "OFAC SDN listing for Acme Corp", "confidence": "high"},
            {"id": "b2", "topic": "export", "content": "ECCN classification for a widget", "confidence": "med"},
        ]
        _seed(k, facts)

        # 1) correct match + cache populated
        out1 = k.search_knowledge("OFAC sanctions Acme")
        assert "Acme Corp" in out1, out1
        assert "a1" in k._search_lc and "b2" in k._search_lc

        # 2) second call (cache hit) → identical result
        out2 = k.search_knowledge("OFAC sanctions Acme")
        assert out2 == out1

        # 3) in-place content edit (different length) → that entry rebuilds,
        #    new content is searchable, stale text is gone
        facts[0]["content"] = "OFAC delisted Acme Corp entirely as of today"
        out3 = k.search_knowledge("delisted")
        assert "delisted" in out3.lower(), out3
        assert "delisted" in k._search_lc["a1"][1]
        assert k._search_lc["a1"][0] == len(facts[0]["content"])  # length re-keyed

        # 4) reload (new facts-list object) clears stale ids
        _new = [{"id": "c3", "topic": "fcpa", "content": "enforcement note", "confidence": "low"}]
        k._cache = {"facts": _new, "queries": [], "learnings": [], "version": 1}
        k.search_knowledge("abc")  # valid word (len>2) → reaches the clear path
        assert "a1" not in k._search_lc and "b2" not in k._search_lc
    finally:
        k._cache = orig_cache
        k._search_lc.clear()
        k._search_lc.update(orig_lc)
        k._search_lc_facts_id = orig_id


def test_rf939_cache_matches_uncached_build():
    """The cached lc text must equal the original `f"{topic} {content}".lower()`
    build for every fact — guards against the cache changing match semantics."""
    from aria_service.intel import knowledge as k

    orig_cache = k._cache
    orig_lc = dict(k._search_lc)
    orig_id = k._search_lc_facts_id
    try:
        facts = [
            {"id": "x1", "topic": "Sanctions Screening", "content": "Saudi Arabia SDN UPDATE", "confidence": "high"},
            {"id": "x2", "topic": "Export", "content": "ECCN 6A003 for THERMAL imager", "confidence": "med"},
        ]
        _seed(k, facts)
        k.search_knowledge("saudi thermal sdn")  # populate cache
        for f in facts:
            expected = f"{f['topic']} {f['content']}".lower()
            assert k._search_lc[f["id"]][1] == expected, (
                f"R-F939 cached lc text diverged for {f['id']}"
            )
    finally:
        k._cache = orig_cache
        k._search_lc.clear()
        k._search_lc.update(orig_lc)
        k._search_lc_facts_id = orig_id
