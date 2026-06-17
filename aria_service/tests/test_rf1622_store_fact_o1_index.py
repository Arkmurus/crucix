"""R-F1622 — store_fact must dedup/detect-contradictions via O(1) indices, NOT
an O(len(facts)) scan. At ~87k facts the old per-call scan cost ~3.5s, driving
the brain_hook 'knowledge: timeout (>3.5s)' floods + the absorb circuit tripping
+ event-loop wedges. These drive the REAL store_fact and assert (a) behavior is
preserved (created/updated/duplicate_skipped/contradiction), and (b) the
contradiction scan is scoped to the single topic, not all facts.
"""
from __future__ import annotations

import pytest

from aria_service.intel import knowledge as K


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(K, "_cache", None, raising=False)
    monkeypatch.setattr(K, "_topic_index", {}, raising=False)
    monkeypatch.setattr(K, "_content_index", {}, raising=False)
    monkeypatch.setattr(K, "_index_count", -1, raising=False)
    monkeypatch.setattr(K, "_indexed_cache_id", 0, raising=False)
    # _save just marks dirty (no disk I/O, flush loop not running in tests)
    yield


def _seed(facts):
    K._cache = {"facts": list(facts), "queries": [], "learnings": [], "version": 1}


async def test_brand_new_fact_created_and_indexed():
    _seed([])
    r = await K.store_fact(
        "Acme Corp revenue",
        "Acme Corp reported revenue of fifty million dollars in fiscal year 2025.",
        source="user", skip_semantic_index=True,
    )
    assert r["action"] == "created"
    assert "acme corp revenue" in K._topic_index
    # index stays consistent with the facts list (store_fact can cascade
    # downstream absorbs that add more facts; the invariant is count==len).
    assert K._index_count == len(K._cache["facts"])


async def test_content_hash_dedup_skips_exact_repeat():
    _seed([])
    c = "Globex signed a two hundred million dollar defence contract with the Ministry."
    r1 = await K.store_fact("Globex contract", c, source="http://news.example/1", skip_semantic_index=True)
    r2 = await K.store_fact("Globex contract", c, source="http://news.example/1", skip_semantic_index=True)
    assert r1["action"] == "created"
    assert r2["action"] == "duplicate_skipped"
    assert r2["reason"] == "content_hash_match_R-F174"


async def test_same_topic_update_preserved():
    _seed([])
    await K.store_fact(
        "CEO of Acme",
        "The chief executive of Acme is currently Jane Doe according to filings.",
        source="user", skip_semantic_index=True,
    )
    r = await K.store_fact(
        "CEO of Acme",
        "The chief executive of Acme is now reported to be John Smith per the update.",
        source="user", skip_semantic_index=True,
    )
    assert r["action"] in ("updated", "superseded")


async def test_contradiction_scan_scoped_to_topic_not_all_facts(monkeypatch):
    # 200 facts across 200 distinct topics. Storing into ONE topic must pass
    # only that topic's facts to _detect_contradictions — proving the scan is
    # no longer O(all facts).
    facts = [
        {"id": f"f{i}", "topic": f"topic number {i}", "content": f"a seeded statement {i}",
         "confidence": "CONFIRMED", "content_hash": f"h{i}", "source_domain": "d"}
        for i in range(200)
    ]
    _seed(facts)
    seen = {}
    real = K._detect_contradictions

    def _spy(topic, content, existing):
        seen["n"] = len(existing)
        return real(topic, content, existing)

    monkeypatch.setattr(K, "_detect_contradictions", _spy)
    await K.store_fact(
        "topic number 5",
        "A brand new and sufficiently detailed statement regarding topic number five here.",
        source="user", skip_semantic_index=True,
    )
    assert seen.get("n", 999) <= 1, (
        f"contradiction scan must be scoped to the one topic, received {seen.get('n')} of 200 facts"
    )


async def test_out_of_band_append_triggers_rebuild():
    _seed([{"id": "x", "topic": "t", "content": "seeded content here long enough to matter",
            "confidence": "CONFIRMED", "content_hash": "h", "source_domain": "d"}])
    K._ensure_indices(K._cache)
    assert K._index_count == 1
    # Something appends WITHOUT going through store_fact → length drift.
    K._cache["facts"].insert(0, {"id": "y", "topic": "t2", "content": "another",
                                 "confidence": "CONFIRMED", "content_hash": "h2", "source_domain": "d"})
    K._ensure_indices(K._cache)  # must self-heal via the length-drift guard
    assert K._index_count == 2
    assert "t2" in K._topic_index
