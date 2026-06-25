"""R-F1932 (G4 tail) — neural recall must use an inverted index, not an
O(all-neurons) scan, AND must return the same neurons it did before.

`_find_neuron` (every _find_or_create) and recall()'s seed scan both walked every
neuron on the event loop. With infinite memory that cost grows forever (audit H4 —
a creeping wedge). The fix adds concept→id + word→ids indices. These tests prove:
the indices stay in sync, _find_neuron is exact + case-insensitive, and the
index candidate set NEVER misses a neuron the old full scan would have seeded
(behavioural equivalence — the speed-up changes nothing about results).
"""
from __future__ import annotations

import pytest

import aria_service.intel.neural_memory as nm


@pytest.fixture(autouse=True)
def _clean_graph():
    for d in (nm._neurons, nm._edges, nm._concept_to_id, nm._word_to_ids):
        d.clear()
    yield
    for d in (nm._neurons, nm._edges, nm._concept_to_id, nm._word_to_ids):
        d.clear()


def test_find_neuron_is_indexed_exact_and_case_insensitive():
    n = nm._find_or_create("Modirum Gespi", "market")
    assert nm._concept_to_id.get("modirum gespi") == n["id"]
    assert nm._find_neuron("MODIRUM  gespi".replace("  ", " ")) is not None
    assert nm._find_neuron("Modirum Gespi")["id"] == n["id"]
    assert nm._find_neuron("does not exist") is None


def test_word_index_stays_in_sync_on_create():
    a = nm._find_or_create("angola procurement risk", "market")
    assert a["id"] in nm._word_to_ids.get("angola", set())
    assert a["id"] in nm._word_to_ids.get("procurement", set())
    assert a["id"] in nm._word_to_ids.get("risk", set())


def test_rebuild_reconstructs_index_after_bulk_load():
    # simulate a bulk load (loader path): populate _neurons directly, then rebuild
    n = nm._make_neuron("gulf defence broker", "oem")
    nm._neurons[n["id"]] = n
    assert n["id"] not in nm._word_to_ids.get("gulf", set())  # not indexed yet
    nm._rebuild_neuron_index()
    assert n["id"] in nm._word_to_ids.get("gulf", set())
    assert nm._concept_to_id.get("gulf defence broker") == n["id"]


def test_index_never_misses_a_neuron_the_full_scan_would_seed():
    """Behavioural equivalence: every neuron that scores > 0 in the OLD full scan
    is present in the index candidate set (so recall results are unchanged)."""
    for c in ["angola procurement", "mozambique deal", "procurement risk",
              "wagner group", "unrelated topic", "angola"]:
        nm._find_or_create(c, "general")
    from aria_service.intel.neural_memory import extract_concepts

    for query in ["angola procurement", "wagner", "procurement risk in angola", "mozambique"]:
        qwords = set(query.lower().split())
        concepts = extract_concepts(query)
        # brute force = the old full-scan seeding rule
        brute = set()
        for nid, n in nm._neurons.items():
            score = sum(1.0 for c, _ in concepts if c.lower() == n["concept"]) \
                + 0.3 * len(qwords & set(n["concept"].split()))
            if score > 0:
                brute.add(nid)
        # index candidate set (what recall now scores)
        cand_words = set(qwords)
        for c, _ in concepts:
            cand_words.update(c.lower().split())
        cand = set()
        for w in cand_words:
            cand |= nm._word_to_ids.get(w, set())
        assert brute <= cand, f"index missed a scoring neuron for query={query!r}: {brute - cand}"


async def test_recall_still_finds_by_word_overlap():
    nm._find_or_create("angola procurement risk", "market")
    nm._find_or_create("mozambique logistics", "market")
    res = await nm.recall("what about angola procurement")
    concepts = " ".join(r.get("concept", "").lower() for r in res.get("neurons", []))
    assert "angola" in concepts
