"""
ARIA Neural Memory — Associative Knowledge Graph with Growing Neurons.

Each "neuron" is a concept node with:
  - Connections to other neurons (weighted edges)
  - Activation strength (increases with use, decays with time)
  - Confidence level (grows as evidence accumulates)
  - Source attributions (where ARIA learned this)

The network grows autonomously:
  - Every conversation extracts concepts and links them
  - Frequently co-occurring concepts form stronger connections
  - Unused neurons decay (but never fully die — long-term memory)
  - ARIA can recall by association: ask about "Angola" and she pulls
    connected neurons like "FADM", "Luanda procurement", "Baykar UAV"

This gives ARIA emergent intelligence — she doesn't just search keywords,
she thinks in connected concepts like a human analyst.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
import uuid
from collections import defaultdict
from typing import Any, Optional

from . import redis_store as rs

logger = logging.getLogger("aria.neural")

# ── Constants ────────────────────────────────────────────────────────────────
NEURONS_KEY = "crucix:aria:neurons"
EDGES_KEY = "crucix:aria:neural_edges"
NEURAL_META_KEY = "crucix:aria:neural_meta"

MAX_NEURONS = 10000
MAX_EDGES_PER_NEURON = 50
DECAY_RATE = 0.995          # per-day decay (0.5% per day)
MIN_ACTIVATION = 0.05       # neurons never fully die
ACTIVATION_BOOST = 0.15     # each access boosts activation
CO_OCCURRENCE_BOOST = 0.08  # co-occurring concepts strengthen edges
NEW_NEURON_ACTIVATION = 0.5
PRUNE_THRESHOLD = 0.08      # prune edges weaker than this

# ── In-memory cache ──────────────────────────────────────────────────────────
_neurons: dict[str, dict] = {}       # id → neuron
_edges: dict[str, dict[str, float]] = defaultdict(dict)  # from_id → {to_id: weight}
_meta: dict = {"total_neurons": 0, "total_edges": 0, "total_activations": 0, "born": None}
_loaded = False


# ── Core Data Structures ────────────────────────────────────────────────────
def _make_neuron(concept: str, category: str = "general", source: str = "auto",
                 confidence: str = "ASSESSED") -> dict:
    now = time.time()
    return {
        "id": str(uuid.uuid4())[:12],
        "concept": concept.strip().lower(),
        "label": concept.strip(),  # human-readable (preserves case)
        "category": category,      # market, oem, person, capability, event, regulation, general
        "activation": NEW_NEURON_ACTIVATION,
        "confidence": confidence,
        "source": source,
        "evidence_count": 1,
        "created_at": now,
        "last_activated": now,
        "last_decayed": now,
        "access_count": 1,
        "metadata": {},
    }


# ── Init ─────────────────────────────────────────────────────────────────────
async def init() -> None:
    global _neurons, _edges, _meta, _loaded
    try:
        neurons_raw = await rs.get_json(NEURONS_KEY)
        edges_raw = await rs.get_json(EDGES_KEY)
        meta_raw = await rs.get_json(NEURAL_META_KEY)

        if neurons_raw and isinstance(neurons_raw, dict):
            _neurons = neurons_raw
        if edges_raw and isinstance(edges_raw, dict):
            _edges = defaultdict(dict, {k: v for k, v in edges_raw.items()})
        if meta_raw and isinstance(meta_raw, dict):
            _meta.update(meta_raw)

        if not _meta.get("born"):
            _meta["born"] = time.time()

        _loaded = True
        logger.info("Neural memory loaded: %d neurons, %d edge groups",
                     len(_neurons), len(_edges))
    except Exception as e:
        logger.warning("Neural memory init failed: %s", e)
        _loaded = True


async def _persist() -> None:
    try:
        _meta["total_neurons"] = len(_neurons)
        _meta["total_edges"] = sum(len(v) for v in _edges.values())
        await rs.set_json(NEURONS_KEY, dict(_neurons), ex=90 * 86400)
        await rs.set_json(EDGES_KEY, dict(_edges), ex=90 * 86400)
        await rs.set_json(NEURAL_META_KEY, _meta, ex=90 * 86400)
    except Exception as e:
        logger.warning("Neural memory persist failed: %s", e)


# ── Neuron Operations ────────────────────────────────────────────────────────

def _find_neuron(concept: str) -> Optional[dict]:
    """Find neuron by concept (case-insensitive)."""
    key = concept.strip().lower()
    for n in _neurons.values():
        if n["concept"] == key:
            return n
    return None


def _find_or_create(concept: str, category: str = "general",
                    source: str = "auto", confidence: str = "ASSESSED") -> dict:
    """Get existing neuron or create a new one."""
    existing = _find_neuron(concept)
    if existing:
        # Boost activation on access
        existing["activation"] = min(1.0, existing["activation"] + ACTIVATION_BOOST)
        existing["last_activated"] = time.time()
        existing["access_count"] = existing.get("access_count", 0) + 1
        _meta["total_activations"] = _meta.get("total_activations", 0) + 1
        return existing

    # Create new neuron
    neuron = _make_neuron(concept, category, source, confidence)
    _neurons[neuron["id"]] = neuron
    _meta["total_activations"] = _meta.get("total_activations", 0) + 1

    # Prune if over limit (remove lowest activation neurons)
    if len(_neurons) > MAX_NEURONS:
        _prune_weakest(len(_neurons) - MAX_NEURONS + 100)

    return neuron


def _strengthen_edge(from_id: str, to_id: str, boost: float = CO_OCCURRENCE_BOOST) -> None:
    """Strengthen connection between two neurons."""
    if from_id == to_id:
        return
    current = _edges[from_id].get(to_id, 0.0)
    _edges[from_id][to_id] = min(1.0, current + boost)
    # Bidirectional
    current_rev = _edges[to_id].get(from_id, 0.0)
    _edges[to_id][from_id] = min(1.0, current_rev + boost)

    # Prune weak edges if too many
    if len(_edges[from_id]) > MAX_EDGES_PER_NEURON:
        _prune_edges(from_id)


def _prune_edges(neuron_id: str) -> None:
    """Remove weakest edges from a neuron."""
    edges = _edges[neuron_id]
    if len(edges) <= MAX_EDGES_PER_NEURON:
        return
    sorted_edges = sorted(edges.items(), key=lambda x: x[1])
    to_remove = len(edges) - MAX_EDGES_PER_NEURON + 5
    for target_id, _ in sorted_edges[:to_remove]:
        del edges[target_id]


def _prune_weakest(count: int) -> None:
    """Remove the weakest neurons."""
    sorted_neurons = sorted(_neurons.values(), key=lambda n: n["activation"])
    for n in sorted_neurons[:count]:
        nid = n["id"]
        del _neurons[nid]
        _edges.pop(nid, None)
        # Remove references from other neurons
        for other_edges in _edges.values():
            other_edges.pop(nid, None)


def _apply_decay() -> None:
    """Apply time-based decay to all neurons and edges."""
    now = time.time()
    for n in _neurons.values():
        days_since_decay = (now - n.get("last_decayed", now)) / 86400
        if days_since_decay < 0.5:
            continue  # Don't decay more than twice a day
        decay = DECAY_RATE ** days_since_decay
        n["activation"] = max(MIN_ACTIVATION, n["activation"] * decay)
        n["last_decayed"] = now

    # Decay edges
    for from_id in list(_edges.keys()):
        edges = _edges[from_id]
        for to_id in list(edges.keys()):
            edges[to_id] *= 0.998  # Slower decay for edges
            if edges[to_id] < PRUNE_THRESHOLD:
                del edges[to_id]
        if not edges:
            del _edges[from_id]


# ── Concept Extraction ───────────────────────────────────────────────────────

# Categories for auto-detection
_MARKET_PATTERNS = re.compile(
    r'\b(angola|mozambique|cape verde|guinea.bissau|'
    r'nigeria|kenya|south africa|ghana|senegal|ethiopia|'
    r'indonesia|philippines|vietnam|uae|saudi arabia|poland|'
    r'brazil|colombia|peru|taiwan|ukraine|turkey|egypt|'
    r'rwanda|uganda|cameroon|chad|mali|burkina faso|niger)\b',
    re.IGNORECASE
)
_OEM_PATTERNS = re.compile(
    r'\b(paramount|elbit|baykar|norinco|rheinmetall|thales|'
    r'leonardo|bae systems|lockheed|boeing|raytheon|'
    r'saab|embraer|denel|airbus|mbda|rafael|'
    r'iai|general dynamics|northrop|l3harris|hanwha)\b',
    re.IGNORECASE
)
_CAPABILITY_PATTERNS = re.compile(
    r'\b(uav|drone|mrap|apv|frigate|corvette|patrol vessel|'
    r'fighter|radar|c4isr|cyber|ew|counter.?ied|'
    r'artillery|mortar|small arms|ammunition|helicopter|'
    r'missile|torpedo|submarine|transport|logistics|'
    r'surveillance|intelligence|communications|satcom)\b',
    re.IGNORECASE
)
_REGULATION_PATTERNS = re.compile(
    r'\b(itar|ear|ofac|ofsi|eu sanctions|arms embargo|'
    r'export licence|end.user certificate|brokering|'
    r'dual.use|wassenaar|mtcr|nsg|australia group)\b',
    re.IGNORECASE
)
_EVENT_PATTERNS = re.compile(
    r'\b(tender|rfp|rfq|contract award|mou|'
    r'defence budget|procurement|acquisition|'
    r'military exercise|coup|conflict|election|'
    r'conference|exhibition|idex|dsei|aad|'
    r'arms deal|offset|fms notification)\b',
    re.IGNORECASE
)


def extract_concepts(text: str) -> list[tuple[str, str]]:
    """Extract (concept, category) pairs from text."""
    if not text:
        return []

    concepts = []
    seen = set()
    t = text[:5000]  # Cap for performance

    for pattern, category in [
        (_MARKET_PATTERNS, "market"),
        (_OEM_PATTERNS, "oem"),
        (_CAPABILITY_PATTERNS, "capability"),
        (_REGULATION_PATTERNS, "regulation"),
        (_EVENT_PATTERNS, "event"),
    ]:
        for m in pattern.finditer(t):
            concept = m.group(0).strip()
            key = concept.lower()
            if key not in seen and len(key) > 2:
                seen.add(key)
                concepts.append((concept, category))

    return concepts


# ── Public API ───────────────────────────────────────────────────────────────

async def learn_from_text(text: str, source: str = "conversation",
                          confidence: str = "ASSESSED") -> dict:
    """Extract concepts from text and grow the neural network."""
    concepts = extract_concepts(text)
    if not concepts:
        return {"neurons_activated": 0, "connections_formed": 0}

    neurons = []
    for concept, category in concepts:
        n = _find_or_create(concept, category, source, confidence)
        neurons.append(n)

    # Form connections between co-occurring concepts
    connections = 0
    for i, n1 in enumerate(neurons):
        for n2 in neurons[i + 1:]:
            _strengthen_edge(n1["id"], n2["id"])
            connections += 1

    await _persist()
    return {
        "neurons_activated": len(neurons),
        "connections_formed": connections,
        "concepts": [c for c, _ in concepts],
    }


async def learn_explicit(concept: str, category: str, related_to: list[str] = None,
                         source: str = "user", confidence: str = "CONFIRMED",
                         metadata: dict = None) -> dict:
    """Explicitly teach ARIA a concept and its relationships."""
    neuron = _find_or_create(concept, category, source, confidence)
    neuron["evidence_count"] = neuron.get("evidence_count", 0) + 1
    if confidence == "CONFIRMED":
        neuron["confidence"] = "CONFIRMED"
    if metadata:
        neuron["metadata"].update(metadata)

    connections = 0
    if related_to:
        for rel in related_to:
            rel_neuron = _find_or_create(rel, "general", source)
            _strengthen_edge(neuron["id"], rel_neuron["id"], boost=0.2)
            connections += 1

    await _persist()
    return {"neuron_id": neuron["id"], "concept": concept, "connections": connections}


async def recall(query: str, depth: int = 2, max_results: int = 20) -> dict:
    """Associative recall — find connected concepts by spreading activation."""
    _apply_decay()

    # Find seed neurons matching query
    concepts = extract_concepts(query)
    query_words = set(query.lower().split())

    seeds = []
    for n in _neurons.values():
        score = 0
        # Exact concept match
        for c, _ in concepts:
            if c.lower() == n["concept"]:
                score += 1.0
        # Word overlap
        concept_words = set(n["concept"].split())
        overlap = len(query_words & concept_words)
        if overlap:
            score += overlap * 0.3
        if score > 0:
            seeds.append((n, score))

    seeds.sort(key=lambda x: -x[1])
    seeds = seeds[:10]

    if not seeds:
        return {"query": query, "neurons": [], "associations": [], "network_size": len(_neurons)}

    # Spreading activation — BFS through connections
    activated = {}  # neuron_id → activation score
    for seed, score in seeds:
        activated[seed["id"]] = score * seed["activation"]

    for d in range(depth):
        new_activations = {}
        decay_factor = 0.6 ** (d + 1)  # Activation drops with distance
        for nid, act in activated.items():
            for target_id, edge_weight in _edges.get(nid, {}).items():
                if target_id in activated:
                    continue
                target_neuron = _neurons.get(target_id)
                if not target_neuron:
                    continue
                spread = act * edge_weight * decay_factor * target_neuron["activation"]
                if spread > 0.01:
                    if target_id not in new_activations or new_activations[target_id] < spread:
                        new_activations[target_id] = spread
        activated.update(new_activations)

    # Sort by activation and return
    results = []
    for nid, act_score in sorted(activated.items(), key=lambda x: -x[1])[:max_results]:
        neuron = _neurons.get(nid)
        if not neuron:
            continue
        connections = []
        for target_id, weight in sorted(_edges.get(nid, {}).items(), key=lambda x: -x[1])[:5]:
            target = _neurons.get(target_id)
            if target:
                connections.append({"concept": target["label"], "weight": round(weight, 3)})
        results.append({
            "concept": neuron["label"],
            "category": neuron["category"],
            "activation": round(neuron["activation"], 3),
            "confidence": neuron["confidence"],
            "evidence": neuron["evidence_count"],
            "relevance": round(act_score, 3),
            "connections": connections,
        })

    # Boost accessed neurons
    for seed, _ in seeds:
        seed["activation"] = min(1.0, seed["activation"] + ACTIVATION_BOOST * 0.5)
        seed["last_activated"] = time.time()

    await _persist()

    return {
        "query": query,
        "neurons": results,
        "associations": [r["concept"] for r in results if r["relevance"] > 0.05],
        "network_size": len(_neurons),
        "total_activations": _meta.get("total_activations", 0),
    }


async def get_neural_context(message: str) -> str:
    """Build context string from neural recall for injection into ARIA prompts."""
    result = await recall(message, depth=2, max_results=15)
    if not result["neurons"]:
        return ""

    lines = ["\n\n[NEURAL MEMORY — ARIA's learned associations]"]
    for n in result["neurons"][:10]:
        conn_str = ", ".join(c["concept"] for c in n["connections"][:3])
        line = f"  [{n['confidence']}] {n['concept']} ({n['category']}) — activation {n['activation']}"
        if conn_str:
            line += f" — linked to: {conn_str}"
        lines.append(line)

    return "\n".join(lines)


async def get_stats() -> dict:
    """Return neural network statistics."""
    _apply_decay()
    if not _neurons:
        return {
            "total_neurons": 0,
            "total_edges": 0,
            "total_activations": 0,
            "categories": {},
            "strongest_neurons": [],
            "born": _meta.get("born"),
            "age_days": 0,
        }

    categories = defaultdict(int)
    for n in _neurons.values():
        categories[n["category"]] += 1

    strongest = sorted(_neurons.values(), key=lambda n: -n["activation"])[:10]
    born = _meta.get("born", time.time())

    return {
        "total_neurons": len(_neurons),
        "total_edges": sum(len(v) for v in _edges.values()),
        "total_activations": _meta.get("total_activations", 0),
        "categories": dict(categories),
        "strongest_neurons": [
            {
                "concept": n["label"],
                "category": n["category"],
                "activation": round(n["activation"], 3),
                "confidence": n["confidence"],
                "evidence": n["evidence_count"],
                "connections": len(_edges.get(n["id"], {})),
            }
            for n in strongest
        ],
        "born": born,
        "age_days": round((time.time() - born) / 86400, 1),
    }


async def get_cluster(concept: str, depth: int = 1) -> dict:
    """Get a concept and its immediate neighborhood."""
    neuron = _find_neuron(concept)
    if not neuron:
        return {"found": False, "concept": concept}

    nodes = [{"id": neuron["id"], "concept": neuron["label"],
              "category": neuron["category"], "activation": neuron["activation"]}]
    edges_out = []

    visited = {neuron["id"]}
    frontier = [(neuron["id"], 0)]

    while frontier:
        nid, d = frontier.pop(0)
        if d >= depth:
            continue
        for target_id, weight in _edges.get(nid, {}).items():
            target = _neurons.get(target_id)
            if not target:
                continue
            edges_out.append({"from": nid, "to": target_id, "weight": round(weight, 3)})
            if target_id not in visited:
                visited.add(target_id)
                nodes.append({"id": target_id, "concept": target["label"],
                              "category": target["category"], "activation": target["activation"]})
                frontier.append((target_id, d + 1))

    return {"found": True, "concept": concept, "nodes": nodes, "edges": edges_out}
