"""
ARIA Entity Relationship Graph — Multi-Hop Network Intelligence

PURPOSE:
  When ARIA investigates Entity A, she doesn't just screen A — she maps A's
  entire corporate network and checks EVERY node for risk. This is what makes
  ARIA different from any other defence AI: automated multi-hop traversal.

  Example: "Investigate Acme Defence Ltd"
    Hop 0: Acme Defence Ltd → UK company, active
    Hop 1: Director John Smith → also directs → Bravo Trading Ltd
    Hop 2: Bravo Trading Ltd → has PSC → Viktor Petrov → SANCTIONED (OFAC SDN)
    RESULT: Acme Defence Ltd has indirect sanctions exposure via shared director

  No competitor does this automatically. This is ARIA's moat.

ARCHITECTURE:
  - EntityNode: company, person, or sanctions list entry
  - RelationshipEdge: typed, sourced, confidence-rated connection
  - ERGraph: Redis-backed graph with BFS multi-hop search
  - Integrates with: dd_orchestrator (Layer 2), network_walker, sanctions

STORAGE:
  Redis key: crucix:entity_graph:{run_id}
  TTL: 30 days (for re-screening)
  Format: JSON {nodes: [...], edges: [...]}
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from . import redis_store as rs

logger = logging.getLogger("aria.intel.entity_graph")

GRAPH_KEY_PREFIX = "crucix:entity_graph"
GRAPH_TTL = 30 * 86400  # 30 days


# ── Data Types ───────────────────────────────────────────────────────────────

@dataclass
class EntityNode:
    entity_id: str                      # "company:GB:12345678", "person:john_smith_abc"
    entity_type: str                    # "company" | "person" | "sanctions_entry"
    label: str                          # Human-readable name
    jurisdiction: str = ""              # ISO2 country code
    status: str = ""                    # "active", "dissolved", "sanctioned", etc.
    registration_number: str = ""
    risk_level: str = "unknown"         # "clear" | "info" | "amber" | "red" | "hard_stop"
    risk_reason: str = ""
    attributes: dict = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class RelationshipEdge:
    from_id: str
    to_id: str
    relationship_type: str              # "has_director", "also_directs", "has_psc",
                                        # "sanctioned_by", "family_of", "associate_of",
                                        # "subsidiary_of", "supplies_to", "shared_address"
    confidence: str = "PROBABLE"        # CONFIRMED | PROBABLE | ASSESSED | UNCERTAIN
    source: str = ""                    # "companies_house", "opensanctions", "network_walker"
    detail: str = ""                    # Extra context
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


@dataclass
class TraversalPath:
    """A single path through the graph from source to a flagged entity."""
    nodes: list[dict]                   # Ordered list of node summaries
    edges: list[dict]                   # Ordered list of edge summaries
    length: int = 0                     # Number of hops
    terminal_risk: str = "clear"        # Risk level of the terminal node
    terminal_reason: str = ""


# ── Entity Graph ─────────────────────────────────────────────────────────────

class ERGraph:
    """Entity Relationship Graph with multi-hop traversal.

    Supports:
      - add_node / add_edge for incremental construction
      - multi_hop_search: BFS from a seed entity, returns all paths to flagged nodes
      - risk_summary: aggregate risk across all reachable nodes
      - Redis persistence for re-screening
    """

    def __init__(self):
        self.nodes: dict[str, EntityNode] = {}
        self.edges: list[RelationshipEdge] = []
        self._adj: dict[str, list[tuple[str, RelationshipEdge]]] = defaultdict(list)

    def add_node(self, node: EntityNode) -> None:
        """Add or update a node in the graph."""
        existing = self.nodes.get(node.entity_id)
        if existing:
            # Merge: keep highest risk, update attributes
            if _risk_rank(node.risk_level) > _risk_rank(existing.risk_level):
                existing.risk_level = node.risk_level
                existing.risk_reason = node.risk_reason
            existing.sources = list(set(existing.sources + node.sources))
            existing.attributes.update(node.attributes)
        else:
            self.nodes[node.entity_id] = node

    def add_edge(self, edge: RelationshipEdge) -> None:
        """Add a directed edge to the graph."""
        self.edges.append(edge)
        self._adj[edge.from_id].append((edge.to_id, edge))
        # Bidirectional for traversal (but keep direction metadata)
        self._adj[edge.to_id].append((edge.from_id, edge))

    def multi_hop_search(
        self,
        start_id: str,
        max_depth: int = 3,
        target_risk_levels: set[str] | None = None,
    ) -> dict:
        """BFS from start_id, find all paths to flagged entities.

        Args:
            start_id: Entity ID to start from
            max_depth: Maximum hops (default 3)
            target_risk_levels: Stop at nodes with these risk levels.
                Default: {"amber", "red", "hard_stop"}

        Returns:
            {
                "paths": [TraversalPath, ...],  # paths to flagged nodes
                "risk_summary": {
                    "total_nodes_reachable": int,
                    "flagged_nodes": int,
                    "worst_risk": str,
                    "risk_by_hop": {1: [...], 2: [...], 3: [...]},
                    "sanctions_exposure": bool,
                },
                "graph_stats": {
                    "total_nodes": int,
                    "total_edges": int,
                    "max_depth_reached": int,
                }
            }
        """
        if target_risk_levels is None:
            target_risk_levels = {"amber", "red", "hard_stop"}

        if start_id not in self.nodes:
            return {
                "paths": [],
                "risk_summary": {"total_nodes_reachable": 0, "flagged_nodes": 0,
                                 "worst_risk": "unknown", "sanctions_exposure": False},
                "graph_stats": {"total_nodes": len(self.nodes), "total_edges": len(self.edges)},
            }

        # BFS with path tracking
        visited: set[str] = {start_id}
        queue: deque[tuple[str, list[str], list[RelationshipEdge], int]] = deque()
        # (current_node, path_nodes, path_edges, depth)
        queue.append((start_id, [start_id], [], 0))

        flagged_paths: list[TraversalPath] = []
        risk_by_hop: dict[int, list[dict]] = defaultdict(list)
        max_depth_reached = 0
        worst_risk = "clear"

        while queue:
            current, path_nodes, path_edges, depth = queue.popleft()
            max_depth_reached = max(max_depth_reached, depth)

            # Check if current node is flagged
            node = self.nodes.get(current)
            if node and node.risk_level in target_risk_levels and depth > 0:
                flagged_paths.append(TraversalPath(
                    nodes=[_node_summary(self.nodes[nid]) for nid in path_nodes if nid in self.nodes],
                    edges=[asdict(e) for e in path_edges],
                    length=depth,
                    terminal_risk=node.risk_level,
                    terminal_reason=node.risk_reason,
                ))
                risk_by_hop[depth].append({
                    "entity": node.label,
                    "risk": node.risk_level,
                    "reason": node.risk_reason,
                })
                if _risk_rank(node.risk_level) > _risk_rank(worst_risk):
                    worst_risk = node.risk_level

            # Explore neighbors
            if depth < max_depth:
                for neighbor_id, edge in self._adj.get(current, []):
                    if neighbor_id not in visited:
                        visited.add(neighbor_id)
                        queue.append((
                            neighbor_id,
                            path_nodes + [neighbor_id],
                            path_edges + [edge],
                            depth + 1,
                        ))

        sanctions_exposure = any(
            p.terminal_risk in ("red", "hard_stop") for p in flagged_paths
        )

        return {
            "paths": [asdict(p) for p in flagged_paths],
            "risk_summary": {
                "total_nodes_reachable": len(visited),
                "flagged_nodes": len(flagged_paths),
                "worst_risk": worst_risk,
                "risk_by_hop": dict(risk_by_hop),
                "sanctions_exposure": sanctions_exposure,
            },
            "graph_stats": {
                "total_nodes": len(self.nodes),
                "total_edges": len(self.edges),
                "max_depth_reached": max_depth_reached,
            },
        }

    def get_network_summary(self) -> str:
        """Return a human-readable summary for ARIA prompt injection."""
        if not self.nodes:
            return ""

        companies = [n for n in self.nodes.values() if n.entity_type == "company"]
        persons = [n for n in self.nodes.values() if n.entity_type == "person"]
        flagged = [n for n in self.nodes.values() if n.risk_level in ("amber", "red", "hard_stop")]

        lines = [
            f"[ENTITY NETWORK GRAPH — {len(self.nodes)} nodes, {len(self.edges)} edges]",
            f"Companies: {len(companies)} | Persons: {len(persons)} | Flagged: {len(flagged)}",
        ]
        if flagged:
            lines.append("FLAGGED ENTITIES:")
            for n in flagged[:10]:
                lines.append(f"  {n.risk_level.upper()}: {n.label} — {n.risk_reason}")

        # R-F996 — wire to brain
        from .engine_wiring import wire_success, wire_failure
        wire_success(
            module="entity_graph",
            summary="Get Network Summary",
            source_id="entity_graph:R-F996",
        )

        return "\n".join(lines)

    # ── Persistence ──────────────────────────────────────────────────────

    async def save(self, run_id: str) -> None:
        """Persist graph to Redis for re-screening."""
        key = f"{GRAPH_KEY_PREFIX}:{run_id}"
        data = {
            "nodes": {nid: asdict(n) for nid, n in self.nodes.items()},
            "edges": [asdict(e) for e in self.edges],
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        await rs.set_json(key, data, ex=GRAPH_TTL)
        logger.info("Entity graph saved: %s (%d nodes, %d edges)", run_id, len(self.nodes), len(self.edges))

    @classmethod
    async def load(cls, run_id: str) -> Optional["ERGraph"]:
        """Load a previously saved graph from Redis."""
        key = f"{GRAPH_KEY_PREFIX}:{run_id}"
        data = await rs.get_json(key)
        if not data:
            return None

        graph = cls()
        for nid, nd in (data.get("nodes") or {}).items():
            graph.nodes[nid] = EntityNode(**{k: v for k, v in nd.items() if k in EntityNode.__dataclass_fields__})
        for ed in data.get("edges") or []:
            edge = RelationshipEdge(**{k: v for k, v in ed.items() if k in RelationshipEdge.__dataclass_fields__})
            graph.edges.append(edge)
            graph._adj[edge.from_id].append((edge.to_id, edge))
            graph._adj[edge.to_id].append((edge.from_id, edge))
        logger.info("Entity graph loaded: %s (%d nodes, %d edges)", run_id, len(graph.nodes), len(graph.edges))
        return graph


# ── Graph Builder — constructs graph from DD walk results ────────────────────

def build_from_walk_result(
    seed_name: str,
    seed_type: str,
    seed_jurisdiction: str,
    seed_reg_number: str,
    walk_result: dict,
    sanctions_results: list[dict] | None = None,
) -> ERGraph:
    """Construct an ERGraph from network_walker.walk_network() output.

    Automatically:
      - Creates nodes for companies and persons
      - Creates edges for director/PSC relationships
      - Marks sanctioned nodes with risk levels
      - Creates sanctions_entry nodes for matched lists
    """
    graph = ERGraph()

    # Seed entity
    seed_id = _entity_id("company", seed_jurisdiction, seed_reg_number or seed_name)
    graph.add_node(EntityNode(
        entity_id=seed_id,
        entity_type=seed_type,
        label=seed_name,
        jurisdiction=seed_jurisdiction,
        registration_number=seed_reg_number,
        status="active",
        risk_level="clear",
        sources=["dd_orchestrator"],
    ))

    dg = walk_result.get("director_graph") or {}

    # Add nodes from director graph
    node_id_map = {"seed": seed_id}
    for node in dg.get("nodes", []):
        nid = node.get("id", "")
        if nid == "seed":
            continue
        entity_type = node.get("type", "person")
        jurisdiction = node.get("jurisdiction", "")
        reg_num = node.get("registration_number", "")
        name = node.get("name", "unknown")
        eid = _entity_id(entity_type, jurisdiction, reg_num or name)
        node_id_map[nid] = eid

        graph.add_node(EntityNode(
            entity_id=eid,
            entity_type=entity_type,
            label=name,
            jurisdiction=jurisdiction,
            registration_number=reg_num,
            attributes={
                "role": node.get("role", ""),
                "appointed_on": node.get("appointed_on", ""),
                "nationality": node.get("nationality", ""),
            },
            sources=["network_walker"],
        ))

    # Add edges
    for edge in dg.get("edges", []):
        from_id = node_id_map.get(edge.get("from", ""), edge.get("from", ""))
        to_id = node_id_map.get(edge.get("to", ""), edge.get("to", ""))
        graph.add_edge(RelationshipEdge(
            from_id=from_id,
            to_id=to_id,
            relationship_type=edge.get("kind", "related_to"),
            confidence="PROBABLE",
            source="network_walker",
        ))

    # Mark PEP connections
    for pep in walk_result.get("pep_connections", []):
        name = pep.get("name", "")
        eid = _entity_id("person", "", name)
        if eid in graph.nodes:
            graph.nodes[eid].risk_level = pep.get("severity", "amber")
            graph.nodes[eid].risk_reason = f"PEP: {pep.get('summary', '')[:200]}"

    # Mark sanctions network
    for san in walk_result.get("sanctions_network", []):
        name = san.get("entity", "")
        eid = _entity_id("company", "", name) if "company" in san.get("role", "").lower() else _entity_id("person", "", name)
        if eid in graph.nodes:
            graph.nodes[eid].risk_level = san.get("severity", "red")
            graph.nodes[eid].risk_reason = f"Sanctions: {san.get('summary', '')[:200]}"

    # Add family/associate relationships from sanctions matches
    if sanctions_results:
        for match in sanctions_results:
            for rel in match.get("relationships", []):
                target_name = rel.get("target", "")
                if target_name:
                    target_eid = _entity_id("person", "", target_name)
                    graph.add_node(EntityNode(
                        entity_id=target_eid,
                        entity_type="person",
                        label=target_name,
                        risk_level="amber",
                        risk_reason=f"Family/associate of {match.get('name', 'unknown')}",
                        sources=["opensanctions"],
                    ))
                    match_eid = _entity_id("person", "", match.get("name", ""))
                    graph.add_edge(RelationshipEdge(
                        from_id=match_eid,
                        to_id=target_eid,
                        relationship_type=rel.get("kind", "related_to"),
                        source="opensanctions",
                    ))

    # Brain signal — graph construction is a discrete relationships
    # signal: number of nodes + edges + risk-flagged nodes feeds the
    # mastery rollup on relationships/osint topics. Fire-and-forget
    # from this sync function.
    try:
        import asyncio as _aio
        from . import brain_hook as _bh

        _risk_count = sum(
            1 for n in graph.nodes.values()
            if n.risk_level in ("amber", "red", "hard_stop")
        )

        async def _emit():
            await _bh.absorb(
                module="entity_graph",
                summary=(
                    f"Graph built for {seed_name}: {len(graph.nodes)} nodes, "
                    f"{len(graph.edges)} edges, {_risk_count} risk-flagged"
                ),
                detail=f"seed_jurisdiction={seed_jurisdiction} seed_type={seed_type}",
                entity_name=seed_name,
                success=len(graph.nodes) > 1,
                gap_type=("entity_graph_singleton"
                          if len(graph.nodes) <= 1 else None),
                gap_detail=("walk_result returned no network — only seed node"
                            if len(graph.nodes) <= 1 else None),
                confidence="ASSESSED",
            )

        try:
            loop = _aio.get_running_loop()
            loop.create_task(_emit())
        except RuntimeError:
            pass
    except Exception:
        pass

    return graph


# ── Helpers ──────────────────────────────────────────────────────────────────

_RISK_RANK = {"unknown": -1, "clear": 0, "info": 1, "amber": 2, "red": 3, "hard_stop": 4}


def _risk_rank(level: str) -> int:
    return _RISK_RANK.get(level, -1)


def _entity_id(entity_type: str, jurisdiction: str, identifier: str) -> str:
    """Generate a deterministic entity ID."""
    raw = f"{entity_type}:{jurisdiction}:{identifier}".lower().strip()
    h = hashlib.md5(raw.encode()).hexdigest()[:10]
    return f"{entity_type}:{h}"


def _node_summary(node: EntityNode) -> dict:
    """Compact node representation for path output."""
    return {
        "id": node.entity_id,
        "type": node.entity_type,
        "label": node.label,
        "jurisdiction": node.jurisdiction,
        "risk_level": node.risk_level,
        "risk_reason": node.risk_reason[:150] if node.risk_reason else "",
    }
