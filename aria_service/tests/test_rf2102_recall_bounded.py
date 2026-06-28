"""R-F2102 — neural recall() spreading-activation BFS must be BOUNDED.

A neuron can hold up to _MAX_HOT_EDGES_PER_NEURON (5000) edges, so an unbounded
depth-2 spread from 10 seeds could touch 50k nodes then 250M edge-ops — seconds of
GIL-bound work that ran on the event loop (via _prefetch_neural) and wedged the
WHOLE chat turn and every document review (live wedge_681). These tests build a
dense graph (a 4000-edge hub) and assert recall returns FAST and within the node
cap, proving one high-degree hub can no longer blow up the traversal.
"""
import asyncio
import time

import aria_service.intel.neural_memory as NM


def _neuron(nid, concept):
    return {
        "id": nid, "concept": concept, "label": concept, "category": "general",
        "activation": 0.9, "confidence": "ASSESSED", "evidence_count": 1,
        "last_activated": time.time(),
    }


def _seed_dense_graph(hub_edges=4000, second_level_edges=200):
    # hub neuron + many targets; the hub has a huge edge fan-out (the explosion risk)
    neurons = {"hub": _neuron("hub", "contract")}
    edges = {"hub": {}}
    for i in range(hub_edges):
        tid = f"t{i}"
        neurons[tid] = _neuron(tid, f"clause{i}")
        edges["hub"][tid] = 0.9
        # give the first 50 targets their own fan-out too (would compound the blowup)
        if i < 50:
            edges[tid] = {}
            for j in range(second_level_edges):
                t2 = f"u{i}_{j}"
                neurons[t2] = _neuron(t2, f"term{i}{j}")
                edges[tid][t2] = 0.8
    NM._neurons = neurons
    NM._edges = NM.defaultdict(dict, edges)
    NM._word_to_ids = {"contract": {"hub"}}
    NM._concept_to_id = {"contract": "hub"}
    # skip the heavy decay + the disk persist so we time ONLY the BFS
    NM._LAST_GLOBAL_DECAY = time.time()
    NM._last_neurons_write = time.time()


def test_rf2102_dense_hub_recall_is_fast_and_bounded():
    _seed_dense_graph()
    t0 = time.monotonic()
    res = asyncio.run(NM.recall("contract clause review", depth=2, max_results=15))
    elapsed = time.monotonic() - t0
    # The whole point: a 4000-edge hub (+ second-level fan-out) must NOT take seconds.
    assert elapsed < 2.0, f"recall must be bounded; took {elapsed:.2f}s on a dense hub"
    assert isinstance(res, dict) and "neurons" in res
    assert len(res["neurons"]) <= 15  # max_results respected


def test_rf2102_caps_are_configurable_and_sane():
    assert NM._RECALL_MAX_NODES >= 100
    assert NM._RECALL_MAX_EDGES_PER_NODE >= 8
    assert NM._RECALL_MAX_CANDIDATES >= 100


def test_rf2102_empty_graph_recall_safe():
    NM._neurons = {}
    NM._edges = NM.defaultdict(dict)
    NM._word_to_ids = {}
    NM._concept_to_id = {}
    NM._LAST_GLOBAL_DECAY = time.time()
    res = asyncio.run(NM.recall("anything", depth=2))
    assert res["neurons"] == []
