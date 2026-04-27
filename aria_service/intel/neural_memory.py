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

import asyncio
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

MAX_NEURONS = 50000
MAX_EDGES_PER_NEURON = 200
DECAY_RATE = 0.997          # per-day decay (0.3% per day — memories last longer)
MIN_ACTIVATION = 0.05       # neurons never fully die
ACTIVATION_BOOST = 0.2      # each access boosts activation (faster learning)
CO_OCCURRENCE_BOOST = 0.12  # co-occurring concepts strengthen edges (stronger associations)
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
        # No TTL — neural memory is permanent. Activation decay still
        # applies (that's learning, not forgetting), but neurons never
        # expire from Redis. Was 90d TTL before 2026-04-21.
        if rs._client:
            import json as _json
            pipe = rs._client.pipeline()
            pipe.set(NEURONS_KEY, _json.dumps(dict(_neurons)))
            pipe.set(EDGES_KEY, _json.dumps(dict(_edges)))
            pipe.set(NEURAL_META_KEY, _json.dumps(_meta))
            await pipe.execute()
        else:
            await rs.set_json(NEURONS_KEY, dict(_neurons))
            await rs.set_json(EDGES_KEY, dict(_edges))
            await rs.set_json(NEURAL_META_KEY, _meta)
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


_LAST_GLOBAL_DECAY = 0.0  # epoch seconds — protects against race condition on rapid recall

def _apply_decay() -> None:
    """Apply time-based decay to all neurons and edges.

    Race-condition fix: previously each neuron tracked its own ``last_decayed`` and was
    skipped if <0.5 days had passed. That meant two recall() calls within 12 hours would
    silently DOUBLE-counted the activation boost (the second skipped decay) producing
    spurious reinforcement. We now use a single global timestamp so decay either applies
    to *all* neurons consistently or to none, and we still apply per-neuron exact maths.
    """
    global _LAST_GLOBAL_DECAY
    now = time.time()
    days_since_global = (now - _LAST_GLOBAL_DECAY) / 86400 if _LAST_GLOBAL_DECAY else 1.0
    if days_since_global < 0.25:  # at most 4 decay passes per day
        return
    _LAST_GLOBAL_DECAY = now

    for n in _neurons.values():
        days_since_decay = (now - n.get("last_decayed", now)) / 86400
        if days_since_decay <= 0:
            continue
        decay = DECAY_RATE ** days_since_decay
        # Critical-knowledge protection: CONFIRMED facts decay 50% slower
        if n.get("confidence") == "CONFIRMED":
            decay = decay ** 0.5
        n["activation"] = max(MIN_ACTIVATION, n["activation"] * decay)
        n["last_decayed"] = now

    # Decay edges proportional to elapsed time, not per-call
    edge_decay = 0.998 ** days_since_global
    for from_id in list(_edges.keys()):
        edges = _edges[from_id]
        for to_id in list(edges.keys()):
            edges[to_id] *= edge_decay
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
# Person names — military leaders, defence ministers, key decision-makers
_PERSON_PATTERNS = re.compile(
    r'\b(general|admiral|marshal|colonel|brigadier|minister|secretary|president|'
    r'chief of staff|commander|cdr|maj gen|lt gen|gen\.|adm\.|col\.|brig\.)\s+'
    r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b',
    re.IGNORECASE
)
# Organisation / institution patterns
_ORGANISATION_PATTERNS = re.compile(
    r'\b(ministry of defence|ministry of defense|mod|dod|pentagon|'
    r'nato|african union|ecowas|sadc|asean|gcc|cplp|'
    r'un security council|european council|european commission)\b',
    re.IGNORECASE
)
# Weapons systems and platforms — specific designations
_WEAPONS_SYSTEM_PATTERNS = re.compile(
    r'\b(f-35|f-16|f/a-18|su-35|su-30|mig-29|j-10|j-20|rafale|eurofighter|gripen|'
    r'typhoon|tejas|kf-21|fa-50|jf-17|'
    r'leopard 2|abrams|challenger|k2|altay|t-90|type 99|arjun|'
    r'bayraktar tb2|tb3|anka|wing loong|mq-9|mq-1|heron|hermes|'
    r'patriot|s-400|s-300|iron dome|thaad|nasams|iris-t|'
    r'himars|m777|k9|caesar|pzh 2000|archer|'
    r'sigma|meko|gowind|fremm|type 31|mogami|'
    r'javelin|spike|stinger|nlaw|atacms|scalp|storm shadow|'
    r'brahmos|harpoon|exocet|nsm|lrasm)\b',
    re.IGNORECASE
)
# Financial and commercial terms
_FINANCIAL_PATTERNS = re.compile(
    r'\b(billion|million|usd|eur|gbp|contract value|deal worth|'
    r'budget allocation|defence spending|gdp|credit line|'
    r'letter of credit|loan agreement|sovereign guarantee|'
    r'offset obligation|countertrade|industrial participation|'
    r'down payment|milestone payment|life.?cycle cost|unit cost)\b',
    re.IGNORECASE
)

VALID_CATEGORIES = frozenset({
    "market", "oem", "capability", "regulation", "event",
    "person", "organisation", "general",
})


async def _extract_concepts_llm(text: str, llm) -> list[tuple[str, str]]:
    """Use an LLM to extract named entities that regex patterns would miss.

    Returns a list of (concept, category) tuples.  Falls back to an empty
    list on any error or if the text is too short to justify an LLM call.
    """
    if not text or len(text) < 50:
        return []

    prompt = (
        "Extract named entities from the following text.  Return ONLY valid JSON "
        "with this schema — no commentary:\n"
        '{"entities": [{"concept": "...", "category": "market|oem|capability|'
        'regulation|event|person|organisation|general"}]}\n\n'
        "Rules:\n"
        "- concept: the entity exactly as it appears (preserve case)\n"
        "- category: one of the listed values\n"
        "- Only include entities that are meaningful nouns / proper names\n"
        "- Skip generic words like 'the', 'system', 'report'\n"
        "- Maximum 30 entities\n\n"
        f"TEXT:\n{text[:3000]}"
    )

    try:
        # The fallback chain has a 15s floor: if `remaining < 15.0`, every
        # provider (anthropic / deepseek / groq) is skipped. The previous
        # 10s timeout silently zeroed every neural-memory extraction call
        # — sweep ingest never grew the graph because all 20-30 per-call
        # extractions died with "Fallback budget exhausted (10.0s remaining)".
        # 30s gives anthropic real room on a 600-token JSON extract while
        # still bounding each call.
        result = await asyncio.wait_for(
            llm.complete(
                "You are a concise entity-extraction assistant. "
                "Return only the requested JSON, nothing else.",
                prompt,
                max_tokens=600,
                timeout=30.0,
            ),
            timeout=35.0,  # outer safety net
        )
        raw = result.text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        entities = data.get("entities") or []
        pairs: list[tuple[str, str]] = []
        for ent in entities:
            concept = (ent.get("concept") or "").strip()
            category = (ent.get("category") or "general").strip().lower()
            if not concept or len(concept) < 2:
                continue
            if category not in VALID_CATEGORIES:
                # Surface the remap so a drifting LLM vocabulary (e.g. a new
                # category like "technology_tier" emerging from defence
                # white-papers) doesn't silently lose its signal. Before
                # this log, every new category was collapsed into "general"
                # with no audit trail — the neuron was created but its
                # intended tier was lost.
                logger.info(
                    "[neural_memory] LLM returned unknown category %r for concept %r; remapping to 'general'",
                    category, concept[:60],
                )
                category = "general"
            pairs.append((concept, category))
        return pairs
    except Exception as e:
        logger.debug("LLM concept extraction failed (graceful fallback): %s", e)
        return []


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
        (_PERSON_PATTERNS, "person"),
        (_ORGANISATION_PATTERNS, "organisation"),
        (_WEAPONS_SYSTEM_PATTERNS, "capability"),
        (_FINANCIAL_PATTERNS, "event"),
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
                          confidence: str = "ASSESSED",
                          llm=None) -> dict:
    """Extract concepts from text and grow the neural network.

    If *llm* is provided, also runs LLM-based extraction to catch novel
    entities that the regex patterns miss, then merges the two result sets.
    """
    concepts = extract_concepts(text)

    # Supplement with LLM extraction when an LLM provider is available
    if llm is not None:
        try:
            llm_concepts = await _extract_concepts_llm(text, llm)
            if llm_concepts:
                seen = {c.lower() for c, _ in concepts}
                for concept, category in llm_concepts:
                    key = concept.strip().lower()
                    if key not in seen:
                        seen.add(key)
                        concepts.append((concept, category))
        except Exception as e:
            logger.debug("LLM concept merge failed: %s", e)

    if not concepts:
        return {"neurons_activated": 0, "connections_formed": 0}

    # Conflict detection — check if new text contradicts existing knowledge
    conflicts_found = []
    for concept, category in concepts:
        if category in ("person", "organisation", "oem"):
            conflict = detect_conflict(concept, text)
            if conflict:
                conflicts_found.append(conflict)
                await log_conflict(conflict)

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

    # Also connect to recently activated neurons (cross-signal learning)
    # This creates associations between concepts from different signals
    import time as _time
    recent_cutoff = _time.time() - 300  # last 5 minutes
    recent_neurons = [
        n for n in _neurons.values()
        if n.get("last_activated", 0) > recent_cutoff
        and n["id"] not in {nn["id"] for nn in neurons}
    ]
    for n1 in neurons:
        for n2 in recent_neurons[:5]:  # max 5 cross-connections
            _strengthen_edge(n1["id"], n2["id"], boost=CO_OCCURRENCE_BOOST * 0.5)
            connections += 1

    await _persist()
    return {
        "neurons_activated": len(neurons),
        "connections_formed": connections,
        "concepts": [c for c, _ in concepts],
        "conflicts": conflicts_found,
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


async def recall(
    query: str,
    depth: int = 2,
    max_results: int = 20,
    category_filter: list[str] | None = None,
    recency_boost: bool = True,
) -> dict:
    """Associative recall via spreading activation through the neuron graph.

    Args:
        query: Free-text query — concepts will be extracted from it.
        depth: BFS depth for spreading activation (max 3).
        max_results: Cap on returned neurons.
        category_filter: If set, restrict spreading to neurons in these categories
            (e.g. ["market", "oem"] to ignore irrelevant noise).
        recency_boost: If True, freshly-activated neurons get a relevance multiplier
            so 10-minute-old signals don't get drowned out by 30-day-old neurons.
    """
    _apply_decay()
    depth = max(0, min(depth, 3))

    # Find seed neurons matching query
    concepts = extract_concepts(query)
    query_words = set(query.lower().split())

    cat_set = set(category_filter) if category_filter else None
    now = time.time()

    def _passes_filter(neuron: dict) -> bool:
        if cat_set is None:
            return True
        return neuron.get("category", "general") in cat_set

    def _recency_factor(neuron: dict) -> float:
        """1.5x for last hour, 1.2x for last day, 1.0x for last week, 0.8x older."""
        if not recency_boost:
            return 1.0
        age_h = (now - neuron.get("last_activated", now)) / 3600
        if age_h < 1: return 1.5
        if age_h < 24: return 1.2
        if age_h < 24 * 7: return 1.0
        if age_h < 24 * 30: return 0.85
        return 0.7

    seeds = []
    for n in _neurons.values():
        if not _passes_filter(n):
            continue
        score = 0.0
        for c, _ in concepts:
            if c.lower() == n["concept"]:
                score += 1.0
        concept_words = set(n["concept"].split())
        overlap = len(query_words & concept_words)
        if overlap:
            score += overlap * 0.3
        if score > 0:
            seeds.append((n, score * _recency_factor(n)))

    seeds.sort(key=lambda x: -x[1])
    seeds = seeds[:10]

    if not seeds:
        return {"query": query, "neurons": [], "associations": [],
                "network_size": len(_neurons), "filtered_by": category_filter}

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
                if not target_neuron or not _passes_filter(target_neuron):
                    continue
                spread = act * edge_weight * decay_factor * target_neuron["activation"] * _recency_factor(target_neuron)
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


async def consolidate() -> dict:
    """Nightly memory consolidation — strengthen strong, abstract schemas.

    Weak-neuron pruning was removed 2026-04-21: operator asked for forever
    memory. Neurons still decay in activation (making them less retrievable
    under query) but are never deleted — they're recoverable if new evidence
    reactivates them.
    """
    now = time.time()
    seven_days_ago = now - 7 * 86400
    neurons_before = len(_neurons)
    edges_before = sum(len(v) for v in _edges.values())
    pruned_ids: list[str] = []  # kept for return-shape compatibility

    # ── 2. Strengthen frequently-accessed neurons ────────────────────────
    strengthened = 0
    for n in _neurons.values():
        if (n.get("access_count", 0) >= 5
                and n.get("last_activated", 0) > seven_days_ago):
            n["activation"] = min(1.0, n["activation"] * 1.05)
            strengthened += 1

    # ── 3. Schema extraction — abstraction over similar facts ────────────
    # When 3+ neurons in the same category share strong edges, create a parent
    # "schema" neuron that abstracts the pattern. This is how ARIA learns
    # generalisations like "Lusophone procurement pattern" instead of memorising
    # 30 individual Angola/Mozambique deal facts in isolation.
    schemas_created = 0
    try:
        from collections import defaultdict as _dd
        cat_buckets: dict[str, list[dict]] = _dd(list)
        for n in _neurons.values():
            cat_buckets[n.get("category", "general")].append(n)

        for category, neurons_in_cat in cat_buckets.items():
            if category in ("general", "schema") or len(neurons_in_cat) < 6:
                continue
            # Find the most-strongly-interconnected cluster in this category
            edge_counts: dict[str, int] = {}
            for n in neurons_in_cat:
                outgoing = _edges.get(n["id"], {})
                same_cat_links = sum(
                    1 for tid in outgoing
                    if _neurons.get(tid, {}).get("category") == category
                )
                edge_counts[n["id"]] = same_cat_links

            hubs = sorted(edge_counts.items(), key=lambda x: -x[1])[:5]
            if not hubs or hubs[0][1] < 3:
                continue

            hub_concepts = [_neurons[hid]["label"] for hid, _ in hubs if hid in _neurons]
            if len(hub_concepts) < 3:
                continue

            schema_label = f"schema:{category}:{hub_concepts[0]}"
            if _find_neuron(schema_label):
                continue  # already abstracted

            schema_neuron = _find_or_create(
                schema_label, category="schema",
                source="consolidation", confidence="ASSESSED",
            )
            schema_neuron["metadata"] = {
                "abstracts": hub_concepts,
                "parent_category": category,
                "created_by": "consolidation",
            }
            # Link schema to its constituent hubs
            for hid, _ in hubs:
                if hid in _neurons:
                    _strengthen_edge(schema_neuron["id"], hid, boost=0.4)
            schemas_created += 1
    except Exception as e:
        logger.warning("Schema extraction failed: %s", e)

    # ── 4. Daily decay pass ──────────────────────────────────────────────
    _apply_decay()

    # ── 5. Persist ───────────────────────────────────────────────────────
    await _persist()

    neurons_after = len(_neurons)
    edges_after = sum(len(v) for v in _edges.values())

    report = {
        "neurons_before": neurons_before,
        "neurons_after": neurons_after,
        "neurons_pruned": len(pruned_ids),
        "neurons_strengthened": strengthened,
        "schemas_created": schemas_created,
        "edges_before": edges_before,
        "edges_after": edges_after,
        "timestamp": now,
    }
    logger.info("Neural consolidation: pruned %d, strengthened %d, schemas %d, %d → %d neurons",
                len(pruned_ids), strengthened, schemas_created, neurons_before, neurons_after)
    return report


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


# ── Conflict Detection ──────────────────────────────────────────────────────
# When new intelligence arrives about an entity, check if it contradicts
# what ARIA already knows. Flag contradictions for human review rather
# than silently overwriting.

CONFLICT_KEY = "crucix:aria:neural_conflicts"

_HIGH_RISK_SIGNALS = {"sanctioned", "high risk", "embargo", "blacklist", "fraud",
                      "shell company", "ghost", "suspicious", "debarred", "prohibited"}
_LOW_RISK_SIGNALS = {"compliant", "low risk", "cleared", "verified", "reputable",
                     "legitimate", "approved", "clean"}


def detect_conflict(entity: str, new_text: str) -> dict | None:
    """Check if new intelligence about an entity contradicts stored knowledge.

    Scans new text for risk signals and compares against existing neuron
    metadata and connected neurons. Returns a conflict dict if opposing
    signals are found, or None if no conflict.
    """
    if not _loaded:
        return None  # Neural memory not initialized yet
    neuron = _find_neuron(entity)
    if not neuron:
        return None

    new_lower = new_text.lower()
    new_high = any(s in new_lower for s in _HIGH_RISK_SIGNALS)
    new_low = any(s in new_lower for s in _LOW_RISK_SIGNALS)

    if not new_high and not new_low:
        return None  # no risk signals in new text

    # Check existing neuron metadata for opposing signals
    existing_meta = json.dumps(neuron.get("metadata", {})).lower()
    existing_source = (neuron.get("source") or "").lower()
    existing_label = neuron.get("label", "").lower()

    # Also check connected neurons for context
    connected_text = ""
    for target_id, weight in _edges.get(neuron["id"], {}).items():
        if weight > 0.15:  # only strong connections
            target = _neurons.get(target_id)
            if target:
                connected_text += " " + target.get("label", "") + " " + json.dumps(target.get("metadata", {}))
    connected_lower = connected_text.lower()
    all_existing = f"{existing_meta} {existing_source} {existing_label} {connected_lower}"

    existing_high = any(s in all_existing for s in _HIGH_RISK_SIGNALS)
    existing_low = any(s in all_existing for s in _LOW_RISK_SIGNALS)

    # Conflict: new intel says HIGH risk but existing says LOW (or vice versa)
    if (new_high and existing_low) or (new_low and existing_high):
        conflict = {
            "entity": entity,
            "neuron_id": neuron["id"],
            "existing_assessment": "HIGH_RISK" if existing_high else "LOW_RISK",
            "new_assessment": "HIGH_RISK" if new_high else "LOW_RISK",
            "existing_source": neuron.get("source", "unknown"),
            "existing_confidence": neuron.get("confidence", "ASSESSED"),
            "detected_at": time.time(),
            "new_text_preview": new_text[:300],
        }
        return conflict

    return None


async def log_conflict(conflict: dict) -> None:
    """Store a detected conflict for human review."""
    try:
        conflicts = await rs.get_json(CONFLICT_KEY) or []
        conflicts.append(conflict)
        # Permanent retention — was last-100 cap + 90d TTL before 2026-04-21.
        await rs.set_json(CONFLICT_KEY, conflicts)
        logger.warning(
            "[conflict] %s: existing=%s new=%s — flagged for review",
            conflict.get("entity"),
            conflict.get("existing_assessment"),
            conflict.get("new_assessment"),
        )
    except Exception as e:
        logger.debug("Conflict logging failed: %s", e)


async def get_conflicts(limit: int = 20) -> list[dict]:
    """Retrieve recent conflicts for review."""
    conflicts = await rs.get_json(CONFLICT_KEY) or []
    return conflicts[-limit:]


async def resolve_conflict(entity: str) -> bool:
    """Remove resolved conflicts for an entity."""
    try:
        conflicts = await rs.get_json(CONFLICT_KEY) or []
        before = len(conflicts)
        conflicts = [c for c in conflicts if c.get("entity", "").lower() != entity.lower()]
        if len(conflicts) < before:
            await rs.set_json(CONFLICT_KEY, conflicts)
            return True
        return False
    except Exception:
        return False
