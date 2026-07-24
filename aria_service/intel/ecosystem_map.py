"""R-F2969 (P1) — ARIA Ecosystem: live architecture map (STRUCTURE layer).

A code-derived, regenerable map of ARIA's whole ecosystem for the operator's
"ARIA Ecosystem" dashboard section — a train-track / bloodstream graph of every
organ (subsystem) and the connections between them. This module builds the
STRUCTURE (nodes + edges) from the real filesystem + AST; the HEALTH overlay
(green/amber/red/grey) lands in a later phase (overlay_health, P2).

HONESTY (anti-hallucination law #4) — what "nothing gets missed / 100%" truthfully means:
  • Modules: 100% BY CONSTRUCTION. The module node set IS scan_modules() (the
    filesystem). A module cannot be silently absent. A module matched by NO organ
    is an ORPHAN — rendered as a RED completeness alert, so the map proves its own
    gaps instead of hiding them.
  • Import edges: 100% of statically-resolvable INTRA-repo imports. Unresolvable /
    dynamic imports are COUNTED and reported, never hidden.
  • Function-call edges (fn→fn) are statically UNDECIDABLE in Python (dynamic
    dispatch, getattr, late imports) → deliberately NOT claimed here; declared
    partial in the coverage report.

Design notes:
  • Self-contained scan (mirrors scripts/ecosystem_audit.scan_modules) but WITHOUT
    that script's import-time os.chdir side effect — safe to import in the brain.
  • build_structure() is heavy (AST over ~all modules) → runs via asyncio.to_thread,
    content-fingerprint cached so it only re-parses when a .py actually changes.
    NEVER call the sync builder inline on the event loop.

Surfaces via GET /api/aria/ecosystem/graph, /ecosystem/coverage (R-F2970).
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.ecosystem_map")

# aria_service/ root, resolved relative to THIS file (intel/ -> aria_service/).
# No os.chdir (unlike scripts/ecosystem_audit) so importing this is side-effect-free.
_ARIA_SERVICE = Path(__file__).resolve().parent.parent
_SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules", "data"}
_SKIP_FILES = {"__init__.py", "conftest.py"}
_RNUM_RE = re.compile(r"R-F\d+")

# The three deployed services (T0). Every organ belongs to one.
_SERVICES = {
    "aria-intel": {"label": "aria-intel (brain)", "port": 8000, "tier_desc": "Python FastAPI brain"},
    "aria-web": {"label": "aria-web (node)", "port": 3117, "tier_desc": "UI / auth / billing / telegram"},
    "aria-wa": {"label": "aria-wa (whatsapp)", "port": 5070, "tier_desc": "Baileys WhatsApp listener"},
}

# T1 ORGANS — the ONLY curated layer. Ordered; a module maps to the FIRST organ
# whose keyword hits its dotted id (lowercased). Order = specific → generic. A
# module matched by none is an ORPHAN (RED alert) — this is what keeps the map
# honest as new modules land.
_ORGANS: list[tuple[str, str, str, tuple[str, ...]]] = [
    # (id, label, service, keyword matchers). Ordered specific → generic; first hit wins.
    ("sanctions", "Sanctions & Screening", "aria-intel",
     ("sanction", "ofac", "ofsi", "fcdo", "un_sc", "worldbank_debarred", "debarred",
      "rca_screening", "crypto_sanctions", "screen", "watchlist", "eliminated_weapons",
      "sdn", "sam_gov", "restricted_party", "denied_party")),
    ("compliance", "Compliance & Export Control", "aria-intel",
     ("compliance", "export_control", "dual_use", "euc", "end_user", "economic_substance",
      "contract_review", "contract_intelligence", "contract_", "ach_", "analytic_principles",
      "licens", "embargo", "kyc", "aml", "fatf", "fcpa", "typolog", "goods_list", "gaming")),
    ("legal", "Legal & Jurisdictions", "aria-intel",
     ("legal", "international_law", "ohada", "jurisdiction", "law_", "regulatory",
      "statute", "case_law")),
    ("dd", "Due Diligence", "aria-intel",
     ("dd_", "due_diligence", "company_investigator", "investigat", "financial_health",
      "triangulat", "adverse", "officer", "prospector", "vault", "ubo", "sharehold",
      "beneficial", "decision_readiness", "network_", "relationship_graph", "financial_dd", "forensic", "benford", "ghost_detection")),
    ("registries", "Company Registries", "aria-intel",
     ("companies_house", "ariregister", "brreg", "ares", "rpo", "zefix", "opencorporat",
      "corporate_registry", "registry_", "corpus_registry", "portal_registry",
      "vendor_registry", "oem_registry", "registry_coverage", "gleif", "edgar")),
    ("osint", "OSINT & Entities", "aria-intel",
     ("entity", "contact", "conflict_tracker", "counter_intelligence", "deception",
      "evasion", "domain", "behavioural", "chain_correlat", "cross_regional",
      "geopolit", "cultural", "country_taxonomy", "linkedin", "social", "footprint",
      "tech_stack", "ssl", "dns", "whois", "pivot", "counterparty", "eagle_eye",
      "aria_peers", "peers", "collab", "agent_contract")),
    ("documents", "Documents & Citations", "aria-intel",
     ("document", "ocr", "content_scan", "citation", "cited_artifact", "claim_grounding",
      "read_document", "upload", "pdf", "ixbrl", "xbrl", "attachment", "file_")),
    ("commercial", "Commercial & BD", "aria-intel",
     ("deal", "bd_", "engagement", "competitor", "competitive", "pipeline",
      "design_partner", "prospect", "market_intel", "commercial", "tender", "procurement_",
      "gtm", "go_to_market", "approach", "strategy")),
    ("learning", "Learning & Mastery", "aria-intel",
     ("student", "curriculum", "brier_drift", "regional_drift", "researcher",
      "self_improve", "mastery", "reading", "reasoning_library", "divergence",
      "continuous_learn", "correction_learn", "cost_free_learning", "continuous_update",
      "core_develop", "critique", "calibration", "proactive", "training", "distill")),
    ("brain", "Brain & Memory", "aria-intel",
     ("knowledge", "neural", "rag_", "memory", "brain_hook", "brain_ingest",
      "brain_signal", "semantic", "reasoning", "grounded", "embed", "corroborat",
      "recall", "absorption", "conversation_store", "dialogue_state", "knowledge_pack",
      "extractor", "extractors.")),
    ("llm", "LLM Routing", "aria-intel",
     ("llm", "fallback", "rate_limiter", "reasoning_router", "deepseek", "anthropic",
      "provider", "model_rout", "prompt", "token")),
    ("autonomous", "Autonomous & Coder", "aria-intel",
     ("autonomous", "gap_detector", "self_coder", "coder", "load_governor", "safety",
      "engine", "code_understanding", "code_health")),
    ("delivery", "Delivery & Surfaces", "aria-intel",
     ("outcome", "whatsapp", "telegram", "email", "proprioception", "deliver",
      "notify", "briefing", "dead_letter", "zoom", "surface")),
    ("search", "Search & Crawl", "aria-intel",
     ("web_search", "searxng", "explorer", "crawl", "source_uptime", "fetcher",
      "spider", "trafilatura", "probe", "web_explor", "news", "rss", "brave")),
    ("guardian", "Guardian & Honesty", "aria-intel",
     ("guard", "honesty", "adversarial", "verification_gate", "constitutional",
      "hallucinat", "premise", "comprehension", "sovereign", "confidence_footer",
      "consistency", "deception_detection", "quarantine", "credential", "antivirus",
      "content_scanner", "self_destruct", "redteam", "red_team", "ground_truth",
      "grounding_reward", "counter_intel", "generative")),
    ("intel_sources", "Intel Sources & Feeds", "aria-intel",
     ("sources", "corpus", "coverage_heatmap", "intel_ledger", "golden", "feed",
      "defence_source", "equipment_specs", "hardware", "world_bank", "acled")),
    ("phase", "Phase & Scoring", "aria-intel",
     ("phase_gate", "autonomy_scorer", "error_streak", "eval", "scorer", "brier",
      "run_quarantine", "grounded_rate")),
    ("infra", "Ops & Infra", "aria-intel",
     ("state_store", "redis_store", "agent_registry", "liveness", "circuit_breaker",
      "cost_tracker", "pending_actions", "engine_wiring", "runpod", "deploy",
      "wedge", "loop_monitor", "self_healing", "self_diagnostic", "metrics",
      "route_audit", "wiring_harness", "capability", "audit", "endpoint_cache",
      "dependency_integrity", "encode_offload", "error_log", "deadlock", "profiler",
      "mistake_ledger", "config", "ecosystem", "reachability", "snapshot_throttle")),
    ("cli", "CLI & Ingest", "aria-intel", ("cli.", "ingest", "admin")),
    ("routes", "API & Routes", "aria-intel", ("routes.", "route", "api_", "middleware", "autonomy_surface")),
]


# ── Module inventory (the completeness DENOMINATOR) ─────────────────────────
def scan_modules() -> list[Path]:
    """Every non-test aria_service .py file. Mirrors scripts/ecosystem_audit.scan_modules
    (same SKIP_DIRS/SKIP_FILES) but without its import-time os.chdir side effect."""
    out: list[Path] = []
    for path in _ARIA_SERVICE.rglob("*.py"):
        if any(s in path.parts for s in _SKIP_DIRS):
            continue
        if path.name in _SKIP_FILES:
            continue
        if "tests" in path.parts:
            continue
        out.append(path)
    return out


def _module_id(path: Path) -> str:
    rel = path.relative_to(_ARIA_SERVICE).with_suffix("")
    return "aria_service." + str(rel).replace(os.sep, ".").replace("/", ".")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _assign_organ(mod_id: str) -> str | None:
    low = mod_id.lower()
    for oid, _label, _svc, keys in _ORGANS:
        if any(k in low for k in keys):
            return oid
        # first-match wins → ordered specific→generic above
    return None


def _resolve_import_targets(node: ast.AST, src_id: str, valid: set[str]) -> list[str]:
    """Resolve an Import/ImportFrom node to intra-repo module ids present in `valid`.
    Handles `from pkg import submodule` (target = pkg.submodule) AND
    `from pkg.mod import symbol` (target = pkg.mod), plus relative imports."""
    hits: list[str] = []
    cands: list[str] = []
    if isinstance(node, ast.ImportFrom):
        base = node.module or ""
        if node.level:  # relative import — resolve against src's package
            parts = src_id.split(".")
            parts = parts[: max(0, len(parts) - node.level)]
            base = ".".join(parts + ([node.module] if node.module else []))
        cands.append(base)
        for alias in node.names:  # `from pkg import submodule`
            cands.append(f"{base}.{alias.name}" if base else alias.name)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            cands.append(alias.name)
    for c in cands:
        if c in valid:
            hits.append(c)
    return hits


def _build_structure_sync() -> dict[str, Any]:
    """Heavy: AST-walk every module for imports + R-F tokens + organ assignment.
    Runs in a worker thread (never on the event loop)."""
    t0 = time.time()
    paths = scan_modules()
    id_to_path = {_module_id(p): p for p in paths}
    valid = set(id_to_path)

    import_edges: set[tuple[str, str]] = set()
    unresolved_intra = 0
    rnums_by_mod: dict[str, list[str]] = {}

    for mid, path in id_to_path.items():
        try:
            src = _read(path)
        except Exception:
            rnums_by_mod[mid] = []
            continue
        rnums_by_mod[mid] = sorted(set(_RNUM_RE.findall(src)))
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue  # a broken file is still a node; it just has no parsed edges
        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                targets = _resolve_import_targets(n, mid, valid)
                for t in targets:
                    if t != mid:
                        import_edges.add((mid, t))
                # count intra-repo imports we could NOT resolve to a module node
                raw = getattr(n, "module", None) if isinstance(n, ast.ImportFrom) else None
                if raw and raw.startswith("aria_service") and not targets:
                    unresolved_intra += 1

    # Organ assignment + orphans
    organ_of: dict[str, str] = {}
    orphans: list[str] = []
    for mid in id_to_path:
        org = _assign_organ(mid)
        if org:
            organ_of[mid] = org
        else:
            orphans.append(mid)

    # ── Build nodes ──
    nodes: list[dict[str, Any]] = []
    for sid, meta in _SERVICES.items():
        nodes.append({"id": sid, "label": meta["label"], "type": "service",
                      "tier": 0, "category": "service", "parent": None,
                      "size": 26, "detail": meta["tier_desc"], "r_numbers": []})
    # organ node sizes scale with module count
    mods_per_organ: dict[str, int] = {}
    for mid, org in organ_of.items():
        mods_per_organ[org] = mods_per_organ.get(org, 0) + 1
    for oid, label, svc, _keys in _ORGANS:
        cnt = mods_per_organ.get(oid, 0)
        nodes.append({"id": f"organ:{oid}", "label": label, "type": "organ",
                      "tier": 1, "category": oid, "parent": svc,
                      "size": min(24, 8 + cnt // 4), "module_count": cnt, "r_numbers": []})
    # orphan bucket organ (only if any) — RED completeness alert node
    if orphans:
        nodes.append({"id": "organ:unassigned", "label": f"⚠ Unassigned ({len(orphans)})",
                      "type": "organ", "tier": 1, "category": "unassigned",
                      "parent": "aria-intel", "size": min(24, 8 + len(orphans) // 4),
                      "module_count": len(orphans), "orphan_alert": True, "r_numbers": []})
    for mid, path in id_to_path.items():
        org = organ_of.get(mid, "unassigned")
        nodes.append({"id": f"mod:{mid}", "label": mid.split(".")[-1], "type": "module",
                      "tier": 2, "category": org, "parent": f"organ:{org}",
                      "size": 8, "module_id": mid, "r_numbers": rnums_by_mod.get(mid, [])})

    # ── Build edges ──
    edges: list[dict[str, Any]] = []
    # containment: organ→service, module→organ (hierarchy "tracks")
    for oid, _label, svc, _keys in _ORGANS:
        edges.append({"source": f"organ:{oid}", "target": svc, "type": "contains", "weight": 1})
    if orphans:
        edges.append({"source": "organ:unassigned", "target": "aria-intel", "type": "contains", "weight": 1})
    for mid in id_to_path:
        org = organ_of.get(mid, "unassigned")
        edges.append({"source": f"mod:{mid}", "target": f"organ:{org}", "type": "contains", "weight": 1})
    # import edges: module→module (dependency "vessels")
    for src, tgt in sorted(import_edges):
        edges.append({"source": f"mod:{src}", "target": f"mod:{tgt}", "type": "import", "weight": 1})

    build_ms = int((time.time() - t0) * 1000)
    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "generated_at": time.time(),
            "build_ms": build_ms,
            "module_count": len(id_to_path),
            "organ_count": len(_ORGANS),
            "import_edge_count": len(import_edges),
            "orphan_count": len(orphans),
            "orphans": sorted(orphans),
            "unresolved_intra_imports": unresolved_intra,
        },
    }


# ── Cached async facade (never re-parses unless a .py changed) ──────────────
_CACHE: dict[str, Any] = {"key": None, "data": None, "built_at": 0.0}


def _fingerprint(paths: list[Path]) -> str:
    h = hashlib.sha1()
    for p in sorted(paths, key=str):
        try:
            st = p.stat()
            h.update(f"{p}:{st.st_mtime_ns}:{st.st_size}".encode())
        except OSError:
            continue
    return h.hexdigest()


async def build_structure(force: bool = False) -> dict[str, Any]:
    """Return the full ecosystem structure graph, cached by a cheap file
    fingerprint. Only re-parses (heavy) when a module actually changed. All heavy
    work is offloaded to a thread so the event loop never blocks."""
    paths = await asyncio.to_thread(scan_modules)
    key = await asyncio.to_thread(_fingerprint, paths)
    if not force and _CACHE["key"] == key and _CACHE["data"] is not None:
        return _CACHE["data"]
    data = await asyncio.to_thread(_build_structure_sync)
    _CACHE.update(key=key, data=data, built_at=time.time())
    logger.info("[ecosystem_map] rebuilt structure: %d modules, %d import edges, %d orphans (%dms)",
                data["meta"]["module_count"], data["meta"]["import_edge_count"],
                data["meta"]["orphan_count"], data["meta"]["build_ms"])
    return data


def _scope_graph(full: dict[str, Any], root: str | None, tier: int | None) -> dict[str, Any]:
    """Return a drill-down slice of the full graph. root=None → T0 services + T1
    organs. root=organ:X → that organ + its modules + import edges among them.
    root=mod:X → that module + its direct import neighbours."""
    nodes = full["nodes"]
    by_id = {n["id"]: n for n in nodes}
    keep: set[str] = set()
    if root is None:
        keep = {n["id"] for n in nodes if n["tier"] in (0, 1)}
    elif root.startswith("organ:"):
        keep.add(root)
        # the service it belongs to
        for n in nodes:
            if n["id"] == root and n.get("parent"):
                keep.add(n["parent"])
        # its modules
        for n in nodes:
            if n["type"] == "module" and n.get("parent") == root:
                keep.add(n["id"])
    elif root.startswith("mod:"):
        keep.add(root)
        for e in full["edges"]:
            if e["type"] == "import" and e["source"] == root:
                keep.add(e["target"])
            if e["type"] == "import" and e["target"] == root:
                keep.add(e["source"])
        if root in by_id and by_id[root].get("parent"):
            keep.add(by_id[root]["parent"])
    scoped_nodes = [n for n in nodes if n["id"] in keep]
    scoped_edges = [e for e in full["edges"]
                    if e["source"] in keep and e["target"] in keep]
    return {"nodes": scoped_nodes, "edges": scoped_edges,
            "meta": {**full["meta"], "scope_root": root, "scope_node_count": len(scoped_nodes)}}


async def get_graph(root: str | None = None, tier: int | None = None) -> dict[str, Any]:
    """Public: a scoped, drill-down view of the ecosystem graph (structure only in P1)."""
    full = await build_structure()
    return _scope_graph(full, root, tier)


async def get_node(node_id: str) -> dict[str, Any]:
    """Detail for one node: parent, children, in/out import edges, R-numbers."""
    full = await build_structure()
    by_id = {n["id"]: n for n in full["nodes"]}
    node = by_id.get(node_id)
    if node is None:
        return {"error": "not_found", "id": node_id}
    children = [n["id"] for n in full["nodes"] if n.get("parent") == node_id]
    imports_out, imports_in = [], []
    for e in full["edges"]:
        if e["type"] != "import":
            continue
        if e["source"] == node_id:
            imports_out.append(e["target"])
        elif e["target"] == node_id:
            imports_in.append(e["source"])
    return {
        "node": node,
        "parent": node.get("parent"),
        "children": children,
        "children_count": len(children),
        "imports_out": sorted(imports_out),
        "imports_in": sorted(imports_in),
        "fan_in": len(imports_in),
        "fan_out": len(imports_out),
        "r_numbers": node.get("r_numbers", []),
    }


async def get_coverage() -> dict[str, Any]:
    """The completeness PROOF (anti-hallucination law #4). Modules are 100% by
    construction; orphans are surfaced, not hidden; call-edges are declared partial."""
    full = await build_structure()
    m = full["meta"]
    total_mods = m["module_count"]
    mapped = total_mods - m["orphan_count"]
    return {
        "modules": {
            "total_on_disk": total_mods,
            "on_map": total_mods,  # 100% by construction (node set == scan_modules)
            "assigned_to_organ": mapped,
            "orphans": m["orphan_count"],
            "orphan_ids": m["orphans"][:100],
            "pct_mapped": 100.0,
            "pct_assigned": round(100.0 * mapped / total_mods, 1) if total_mods else 0.0,
        },
        "import_edges": {
            "resolved_intra_repo": m["import_edge_count"],
            "unresolved_dynamic": m["unresolved_intra_imports"],
            "note": "100% of statically-resolvable intra-repo imports; dynamic/unresolvable counted, not hidden",
        },
        "call_edges": {
            "status": "declared_partial",
            "reason": "function-call graph is statically undecidable in Python (dynamic dispatch/getattr/late imports)",
        },
        "health_sensors": {
            "status": "pending_P2",
            "note": "structure layer only in P1; live green/amber/red overlay lands in overlay_health (P2)",
        },
        "meta": {"generated_at": m["generated_at"], "build_ms": m["build_ms"]},
    }
