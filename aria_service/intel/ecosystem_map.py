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
# R-F2980 (F2): __init__.py is NO LONGER skipped — package __init__ files carry
# real, load-bearing code (e.g. personas/__init__.py = 273 lines, 3 public fns),
# so excluding them made the "100% by construction" claim FALSE. They are now
# nodes (mapped to their PACKAGE dotted name). Only conftest.py (pytest infra) is
# excluded — and that exclusion is stated in the coverage payload, not implied away.
_SKIP_FILES = {"conftest.py"}
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
      "beneficial", "decision_readiness", "network_", "relationship_graph", "financial_dd", "forensic", "benford", "ghost_detection",
      "prime_sub")),  # R-F2986: prime/sub contractor mapping
    ("registries", "Company Registries", "aria-intel",
     ("companies_house", "ariregister", "brreg", "ares", "rpo", "zefix", "opencorporat",
      "corporate_registry", "registry_", "corpus_registry", "portal_registry",
      "vendor_registry", "oem_registry", "registry_coverage", "gleif", "edgar",
      "registration_check", "_romanian_cui", "person_resolver", "name_variants")),  # R-F2986
    ("osint", "OSINT & Entities", "aria-intel",
     ("entity", "contact", "conflict_tracker", "counter_intelligence", "deception",
      "evasion", "domain", "behavioural", "chain_correlat", "cross_regional",
      "geopolit", "cultural", "country_taxonomy", "linkedin", "social", "footprint",
      "tech_stack", "ssl", "dns", "whois", "pivot", "counterparty", "eagle_eye",
      "aria_peers", "peers", "collab", "agent_contract",
      "pmesii", "nato_standards", "political_risk", "risk_indices", "vision_2030",  # R-F2986
      "weapon_origin", "tbml", "narrative_monitor", "osint_username", "signal_correlator",
      "regional_bright_lines", "regional_navigation", "geoip", "github_search",
      "gulf_oem", "web_atlas", "provenance_chain")),
    ("documents", "Documents & Citations", "aria-intel",
     ("document", "ocr", "content_scan", "citation", "cited_artifact", "claim_grounding",
      "read_document", "upload", "pdf", "ixbrl", "xbrl", "attachment", "file_",
      "report_builder", "weekly_report", "meeting_notes", "voice_transcribe",  # R-F2986
      "product_page", "assessment_writer", "writer_orchestrator", "anti_corruption", "tech_spec_and")),
    ("commercial", "Commercial & BD", "aria-intel",
     ("deal", "bd_", "engagement", "competitor", "competitive", "pipeline",
      "design_partner", "prospect", "market_intel", "commercial", "tender", "procurement_",
      "gtm", "go_to_market", "approach", "strategy",
      "opportunity_converter", "negotiation")),  # R-F2986
    ("learning", "Learning & Mastery", "aria-intel",
     ("student", "curriculum", "brier_drift", "regional_drift", "researcher",
      "self_improve", "mastery", "reading", "reasoning_library", "divergence",
      "continuous_learn", "correction_learn", "cost_free_learning", "continuous_update",
      "core_develop", "critique", "calibration", "proactive", "training", "distill",
      "fsrs", "rlaif", "self_assess", "strategic_evolution", "topic_completion",  # R-F2986
      "held_out", "contamination_check", "output_harvester", "pair_builder",
      "style_learner", "learning_progress", "learning_controller", "metacognitive_journal",
      "metacognitive.coding", "metacognitive.consciousness", "metacognitive.cycle",
      "metacognitive.gaps", "bookmarks", "user_model", "predictor")),
    ("brain", "Brain & Memory", "aria-intel",
     ("knowledge", "neural", "rag_", "memory", "brain_hook", "brain_ingest",
      "brain_signal", "semantic", "reasoning", "grounded", "embed", "corroborat",
      "recall", "absorption", "conversation_store", "dialogue_state", "knowledge_pack",
      "extractor", "extractors.",
      "symbolic_reasoner", "local_brain", "mem0", "intel_expander", "intel_quality",  # R-F2986
      "query_decompos", "response_cache", "verified_intel", "verifiable_ledger")),
    ("llm", "LLM Routing", "aria-intel",
     ("llm", "fallback", "rate_limiter", "reasoning_router", "deepseek", "anthropic",
      "provider", "model_rout", "prompt", "token",
      "reranker")),  # R-F2986
    ("autonomous", "Autonomous & Coder", "aria-intel",
     ("autonomous", "gap_detector", "self_coder", "coder", "load_governor", "safety",
      "engine", "code_understanding", "code_health",
      "multi_lang", "self_coding", "self_restart", "self_sufficient")),  # R-F2986
    ("delivery", "Delivery & Surfaces", "aria-intel",
     ("outcome", "whatsapp", "telegram", "email", "proprioception", "deliver",
      "notify", "briefing", "dead_letter", "zoom", "surface",
      "wa_formatter", "tickets")),  # R-F2986; portal_* deleted R-F3202
    ("search", "Search & Crawl", "aria-intel",
     ("web_search", "searxng", "explorer", "crawl", "source_uptime", "fetcher",
      "spider", "trafilatura", "probe", "web_explor", "news", "rss", "brave",
      "scraper", "source_scout", "source_validator", "source_verifier", "ua_rotation",  # R-F2986
      "headless", "captcha", "web_integrity", "search_index")),
    ("guardian", "Guardian & Honesty", "aria-intel",
     ("guard", "honesty", "adversarial", "verification_gate", "constitutional",
      "hallucinat", "premise", "comprehension", "sovereign", "confidence_footer",
      "consistency", "deception_detection", "quarantine", "credential", "antivirus",
      "content_scanner", "self_destruct", "redteam", "red_team", "ground_truth",
      "grounding_reward", "counter_intel", "generative",
      "truth_verifier", "response_verifier", "verification_accumulator",  # R-F2986
      "provenance_watermark", "pii_redaction", "log_redaction", "security_protocol",
      "security_challenge", "protective_reply", "kaspersky")),
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
      "mistake_ledger", "config", "ecosystem", "reachability", "snapshot_throttle",
      "user_quota", "quota_client", "tenant_namespace", "multi_user", "system_health",  # R-F2986
      "infra_health", "trace_stream", "wiring_monitor", "key_resolver", "operator_pending",
      "performance_optimizer", "scratchpad", "public_api", "operating_modes")),
    ("cli", "CLI & Ingest", "aria-intel", ("cli.", "ingest", "admin")),
    ("routes", "API & Routes", "aria-intel", ("routes.", "route", "api_", "middleware", "autonomy_surface")),
]


# R-F3349 — EXPLICIT organ decisions, applied BEFORE keyword matching.
#
# The keyword table above is ordered specific→generic and resolves first-match-wins.
# That is fine for the ~97% of modules only one organ claims, but where several
# organs match, order decided it — so a deliberately-curated keyword in a LATER
# organ could never fire, and nobody could see that it had been overruled. This is
# the same lesson R-F3047 learned on circuit-breaker names ("semantic_scholar" hit
# the brain organ's "semantic" and painted 56 modules RED off an external paper
# API): a node's organ is a CURATION DECISION, not a string coincidence, so where
# strings disagree the decision is DECLARED here rather than inferred from ordering.
#
# Deliberately NOT "longest keyword wins" — measured on the live tree that rule
# fixes these but breaks ~8 others (intel.sources.ofac_sdn → intel_sources, gutting
# Sanctions; uk_ofsi_ingest → cli; sanctions_divergence → learning). Keyword length
# is not a specificity metric.
#
# Every entry is a decision. A no-op entry CONFIRMS the keyword winner against a
# curated keyword that lost — silence there would be indistinguishable from the
# accident this fix removes. `audit_organ_table()` fails the build if any conflict
# lacks a line here, so this table cannot quietly go stale.
_ORGAN_OVERRIDES: dict[str, str] = {
    # ── CORRECTIONS: the curated keyword was right and lost ──
    "aria_service.intel.run_quarantine": "phase",            # phase declares "run_quarantine"; CLAUDE.md §1 calls it the gate #4 closer
    "aria_service.intel.reasoning_router": "llm",            # llm declares "reasoning_router"; lost to brain's "reasoning"
    "aria_service.intel.content_scanner": "guardian",        # guardian declares "content_scanner"; lost to documents' "content_scan"
    "aria_service.intel.engine_wiring": "infra",             # infra declares "engine_wiring"; lost to autonomous' "engine"
    "aria_service.intel.deception_detection": "guardian",    # guardian declares "deception_detection"; lost to osint's "deception"
    "aria_service.intel.autonomy_surface": "routes",         # routes declares "autonomy_surface"; lost to delivery's "surface"
    "aria_service.intel.scraper.playwright_engine": "search",  # a scraper engine, not self-coding machinery
    # ── REHOMED: victims of a substring accident the token rule now rejects ──
    "aria_service.intel.precall_brief": "commercial",        # was brain via "recall" inside "pRECALLbrief"; a pre-call brief is BD
    "aria_service.metacognitive.identity": "learning",       # was osint via "entity" inside "idENTITY"
    # ── CONFIRMATIONS: a curated keyword lost, and losing was CORRECT ──
    "aria_service.intel.evasion_typology_detector": "compliance",  # FATF typology work, not OSINT tradecraft (osint "evasion" overruled)
    "aria_service.intel.sanctions_divergence": "sanctions",  # a sanctions signal (learning "divergence" overruled)
    "aria_service.intel.counter_intelligence": "osint",      # an OSINT discipline (guardian "counter_intel" overruled)
    "aria_service.intel.brier_drift_monitor": "learning",    # calibration drift is a learning signal (phase "brier" overruled)
    "aria_service.intel.reasoning_library": "learning",      # learning declares "reasoning_library"; brain's "reasoning" overruled.
                                                             # Surfaced by the audit only AFTER reasoning_router moved to llm above —
                                                             # that left brain's "reasoning" winning nothing, which is the guard working.
    "aria_service.learning.knowledge_spider": "brain",       # feeds knowledge; lives in learning (search "spider" overruled)
    "aria_service.autonomous.sovereign_llm": "llm",          # an LLM surface (guardian "sovereign" overruled)
    "aria_service.autonomous.autonomous_deploy": "autonomous",  # ARIA's SELF-deploy machinery, not operator infra
    "aria_service.autonomous.deploy_verifier": "autonomous",     # (infra "deploy" overruled for all four)
    "aria_service.autonomous.fly_deployer": "autonomous",
    "aria_service.autonomous.machines_deployer": "autonomous",
}


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
    dotted = "aria_service." + str(rel).replace(os.sep, ".").replace("/", ".")
    # R-F2980 (F2): a package's __init__.py IS the package — map it to the package
    # dotted name (aria_service.personas), not ...personas.__init__. This also lets
    # `from aria_service.personas import X` resolve to a real node (fixes F4).
    if dotted.endswith(".__init__"):
        dotted = dotted[: -len(".__init__")]
    return dotted


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _kw_matches(keyword: str, low_id: str) -> bool:
    """R-F3349 — does `keyword` occur in `low_id` at a TOKEN BOUNDARY?

    The old test was `keyword in low_id`, a naked substring, which filed
    `yaml_reviewer` under anti-money-laundering ("aml" inside "y-AML-"),
    `precall_brief` under Brain & Memory ("recall" inside "p-RECALL-") and
    `metacognitive.identity` under OSINT ("entity" inside "id-ENTITY"). Same
    defect class R-F3047 removed from circuit-breaker names; module paths are the
    other caller of the same keyword table and never got the fix.

    A match must START on a token boundary (string start, `.` or `_`). It need NOT
    end on one, because many keywords are deliberate STEMS — "sanction" has to
    match "sanctions", "investigat" has to match "company_investigator", "crawl"
    has to match "crawler". Requiring a trailing boundary would break those; the
    accidents are all mid-token STARTS, so anchoring the start is exactly enough.
    """
    start = 0
    while True:
        i = low_id.find(keyword, start)
        if i < 0:
            return False
        if i == 0 or low_id[i - 1] in "._":
            return True
        start = i + 1


def _assign_organ(mod_id: str) -> str | None:
    """Module (or internal agent/gap name) → organ id, or None for an orphan.

    R-F3349: an explicit decision in _ORGAN_OVERRIDES wins over any keyword; then
    keywords resolve first-match-wins in the declared specific→generic order,
    matching at token boundaries only.
    """
    override = _ORGAN_OVERRIDES.get(mod_id)
    if override:
        return override
    low = mod_id.lower()
    for oid, _label, _svc, keys in _ORGANS:
        if any(_kw_matches(k, low) for k in keys):
            return oid
        # first-match wins → ordered specific→generic above
    return None


_ORGAN_AUDIT_CACHE: dict[str, Any] = {"key": None, "data": None}


def audit_organ_table(module_ids: list[str] | None = None) -> dict[str, Any]:
    """R-F3349 — the CURATED layer's self-audit, surfaced in /ecosystem/coverage.

    Modules, imports and call-graph coverage all declare their own limits. The
    organ table was the one layer that did not, so its drift was invisible:
    measured on 578 live modules, 60 of its keywords matched NOTHING (stale after
    renames/deletions — "ubo", "whois", "xbrl", "curriculum", "telegram") and 13
    matched modules they never won. Both are reported here rather than hidden,
    which is the same discipline this module applies to orphans and to colour.

    A DEAD keyword is not automatically a bug — several are aspirational, held for
    a module that does not exist yet, and deleting them would orphan that module on
    arrival. So dead keywords are REPORTED, never auto-pruned.

    A SHADOWED keyword — one whose organ loses the module to another organ — IS a
    bug unless a human decided it, which is why `declared` is tracked and why the
    R-F3349 test fails on any undeclared one.

    Measured at ~100ms over 578 modules × ~600 keywords, which is small but is
    SYNCHRONOUS, and get_coverage() sits on the /api/aria/health path that R-F3062
    already had to rescue from a blown budget. So callers pass the module ids
    build_structure() has already produced (no second filesystem walk), and the
    result is memoised against that id set — the organ table is static, so the
    audit can only change when the module set does.
    """
    ids = list(module_ids) if module_ids is not None else [_module_id(p) for p in scan_modules()]
    key = hashlib.sha1("\n".join(sorted(ids)).encode()).hexdigest()
    if _ORGAN_AUDIT_CACHE["key"] == key and _ORGAN_AUDIT_CACHE["data"] is not None:
        return _ORGAN_AUDIT_CACHE["data"]
    live = set(ids)
    assigned = {mid: _assign_organ(mid) for mid in ids}

    dead: list[dict[str, str]] = []
    shadowed: list[dict[str, Any]] = []
    for oid, _label, _svc, keys in _ORGANS:
        for k in keys:
            hits = [m for m in ids if _kw_matches(k, m.lower())]
            if not hits:
                dead.append({"organ": oid, "keyword": k})
                continue
            lost = [m for m in hits if assigned[m] != oid]
            if len(lost) == len(hits):  # this keyword never wins a module for its organ
                for m in lost:
                    shadowed.append({
                        "keyword": k,
                        "intended_organ": oid,
                        "actual_organ": assigned[m],
                        "module": m,
                        "declared": m in _ORGAN_OVERRIDES,
                    })
    out = {
        "dead_keywords": sorted(dead, key=lambda d: (d["organ"], d["keyword"])),
        "shadowed_keywords": shadowed,
        "stale_overrides": sorted(m for m in _ORGAN_OVERRIDES if m not in live),
        "override_count": len(_ORGAN_OVERRIDES),
        "note": "dead keywords may be aspirational and are reported, never auto-pruned; "
                "a shadowed keyword must carry an explicit _ORGAN_OVERRIDES decision",
    }
    _ORGAN_AUDIT_CACHE["key"], _ORGAN_AUDIT_CACHE["data"] = key, out
    return out


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
                # Count intra-repo imports we could NOT resolve to a module node.
                # R-F2980 (review F4): also count RELATIVE imports (from . import x /
                # from .foo import y) — their node.module doesn't start with
                # "aria_service", so the old check silently UNDER-counted them.
                if not targets and isinstance(n, ast.ImportFrom):
                    raw = n.module
                    if (raw and raw.startswith("aria_service")) or (n.level and n.level > 0):
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
    """Public: a scoped, drill-down view of the ecosystem graph with the LIVE
    health overlay (R-F2972). Structure is cached; health is read fresh each call."""
    full = await build_structure()
    scoped = _scope_graph(full, root, tier)
    # T3 (R-F2975): drilling into a module reveals its FUNCTIONS (the lowest level) —
    # on-demand + bounded (one module's fns), never in the full graph.
    if root and root.startswith("mod:"):
        mid = root[4:]
        for f in _module_functions(mid):
            fid = f"fn:{mid}:{f['name']}"
            scoped["nodes"].append({
                "id": fid, "label": f["name"] + ("" if not f["async"] else " ⟳"),
                "type": "function", "tier": 3, "category": "function", "parent": root,
                "size": 4, "wired": f["wired"], "line": f["line"], "r_numbers": []})
            scoped["edges"].append({"source": fid, "target": root, "type": "contains", "weight": 1})
    try:
        signals = await _gather_signals()
        await _apply_health_to(scoped, signals, _organ_of_map(full))
    except Exception as e:  # never let a health-read failure blank the map
        logger.debug("[ecosystem_map] health overlay failed (nodes stay grey): %s", e)
        for n in scoped["nodes"]:
            n.setdefault("health", "grey")
            n.setdefault("sensor", "no live sensor")
    return scoped


# ── R-F2972 (P2) — LIVE HEALTH OVERLAY (green/amber/red/GREY) ────────────────
# Colour discipline (anti-hallucination law #4): a node is GREEN only when a
# POSITIVE liveness signal says so (breaker closed / agent fresh / limb alive).
# The ABSENCE of problems is NOT green — it is GREY ("no live sensor"). Negative
# signals (open breaker, stale agent, dead limb, open HIGH gap) push amber/red;
# worst wins. The colour is a CITATION: every coloured node carries the sensor
# name + value + read-time, so it can be audited, never a claim.
_RANK = {"grey": 0, "green": 1, "amber": 2, "red": 3}
_GREY = {"color": "grey", "sensor": "no live sensor", "value": None}

# R-F3062 — strong refs to the background coverage build so it is never
# garbage-collected mid-flight (the R-F1776 fire-and-forget discipline).
_COVERAGE_TASK: "asyncio.Task | None" = None
_COVERAGE_TASKS: set = set()

# R-F3048 — minimum denominator before a delivery success-rate may colour a
# node. Below this the surface stays grey ("not enough traffic to judge").
# 5 keeps a single failure from reading as a red organ while still colouring
# on a genuinely bad run (e.g. 1/10).
_MIN_OUTCOME_SAMPLES = 5

# R-F3047 — EXPLICIT sensor→organ registry. Keys are the bare backend/agent
# name with any "prefix:" stripped (so "search:duckduckgo" looks up as
# "duckduckgo"). A name absent from this map paints NOTHING; see
# _organ_for_name for why a keyword match is not safe here.
#
# Rule of thumb: file a backend under the organ that OWNS the capability it
# serves, not the organ whose module names happen to share a word. Academic
# literature APIs serve retrieval, so they belong to search — NOT to brain,
# however much "semantic_scholar" looks like "semantic_search".
_SENSOR_ORGAN: dict[str, str] = {
    # ── retrieval / crawl backends ──
    "duckduckgo": "search",
    "searxng": "search",
    "searxng-selfhost": "search",
    "google_news": "search",
    "bing_news": "search",
    "gnews_api": "search",
    "brave": "search",
    "academic": "search",
    "semantic_scholar": "search",   # academic paper API — NOT the brain
    "openalex": "search",           # academic graph API — NOT the brain
    "crossref": "search",
    "wayback": "search",
    "archive_is": "search",
    # ── sanctions / screening sources ──
    "opensanctions": "sanctions",
    "ofac": "sanctions",
    "un_sc": "sanctions",
    "fcdo": "sanctions",
    "worldbank_debarred": "sanctions",
    # ── company registries ──
    "companies_house": "registries",
    "sec_edgar": "registries",
    "ares": "registries",
    "rpo": "registries",
    # ── regulators / intel feeds ──
    "fca_register": "compliance",
    "worldbank_indicators": "intel_sources",
    "acled": "intel_sources",
    "gdelt": "intel_sources",
    # ── delivery surfaces ──
    "whatsapp": "delivery",
    "telegram": "delivery",
    "web": "delivery",
    # ── model providers ──
    "anthropic": "llm",
    "deepseek": "llm",
    "deepseek_backup": "llm",
    "openai": "llm",
    "groq": "llm",
    "gemini": "llm",
    "ollama": "llm",
    "aria_llm": "llm",
}


async def _gather_signals() -> dict[str, Any]:
    """Fetch all live health signals concurrently; a dead source degrades to {}
    (its nodes stay grey), never blanks the whole map (mirrors ecosystem_dashboard)."""
    import time as _t

    async def _breakers():
        from . import circuit_breaker as _cb
        return await asyncio.to_thread(_cb.get_all_breakers)

    async def _agents():
        from .agent_registry import AgentRegistry
        return await AgentRegistry().list_active_agents(include_stale=True)

    async def _limbs():
        from . import liveness as _lv
        return (await _lv.get_liveness()).get("limbs", {})

    async def _surfaces():
        from . import outcome_wire as _ow
        return await _ow.get_all_surface_health()

    async def _gaps():
        from . import capability_gaps as _cg
        return await _cg.get_gaps(resolved=False, limit=200)

    async def _safe(coro):
        try:
            return await coro
        except Exception as e:
            logger.debug("[ecosystem_map] signal fetch failed: %s", e)
            return None

    breakers, agents, limbs, surfaces, gaps = await asyncio.gather(
        _safe(_breakers()), _safe(_agents()), _safe(_limbs()), _safe(_surfaces()), _safe(_gaps()))
    return {
        "breakers": breakers or [], "agents": agents or [], "limbs": limbs or {},
        "surfaces": surfaces or {}, "gaps": gaps or [], "read_at": _t.time(),
    }


def _breaker_color(b: dict) -> str:
    # R-F2980 (F1): CLOSED is NOT automatically a positive signal. get_all_breakers
    # returns EVERY registered breaker, including ones that never recorded a success
    # (total_successes=0) or are failing below the trip threshold
    # (consecutive_failures>0, still CLOSED). Those are NOT "this backend is healthy"
    # — treating them as green was a fabricated green (violates law #4). CLOSED→green
    # ONLY with a proven success and no current failures; otherwise GREY (unproven).
    st = (str(b.get("state") or "")).upper()
    if st == "OPEN":
        return "red"
    if st == "HALF_OPEN":
        return "amber"
    try:
        succ = int(b.get("total_successes") or 0)
        cons = int(b.get("consecutive_failures") or 0)
    except (TypeError, ValueError):
        succ, cons = 0, 0
    if succ > 0 and cons == 0:
        return "green"
    return "grey"  # registered but never proven healthy → no positive signal


def _agent_color(age: float | None) -> str:
    # Anti-cry-wolf: many agents are long-cycle (tender_monitor@6h, eagle_eye@30m),
    # so a beat older than a few minutes is NORMAL, not broken. Heartbeat age yields
    # green (<5min, recently active) or amber (worth a look) for anything under 24h;
    # confirmed-broken normally comes from breakers/limbs/HIGH gaps, not a slow-cycle
    # agent between beats. Honest caveats (R-F2980 F6): a >24h silence DOES escalate
    # to red — a legitimately >24h-cycle agent would false-red there (rare); and
    # there is an inherent ~5-min window after a crash where the last beat is still
    # <300s and the node reads green. Both are accepted trade-offs, not "never red".
    if age is None:
        return "grey"
    if age < 300:
        return "green"
    if age < 86400:
        return "amber"
    return "red"


def _build_health_map(signals: dict[str, Any], node_ids: set[str], organ_of: dict[str, str]) -> dict[str, dict]:
    """Return {node_id: {color, sensor, value, read_at}} for nodes with a live
    sensor. Worst-wins; positive signals turn grey→green, negative → amber/red."""
    read_at = signals.get("read_at")
    health: dict[str, dict] = {}

    def _apply(nid: str, color: str, sensor: str, value: Any):
        if nid not in node_ids:
            return
        cur = health.get(nid)
        if cur is None or _RANK[color] > _RANK[cur["color"]]:
            health[nid] = {"color": color, "sensor": sensor, "value": value, "read_at": read_at}

    def _organ_for_name(name: str) -> str | None:
        """Internal names (agent ids, gap module names) → organ by keyword.

        R-F3047 scopes this back to INTERNAL names only. These are ARIA's own
        module/agent identifiers, which is exactly what the _ORGANS keyword
        lists were written against, so substring matching is right here — and
        the agent/gap tests depend on it (a stale `student_loop` must reach
        organ:learning, a HIGH gap in `brain_hook` must reach organ:brain).
        EXTERNAL backends go through _organ_for_backend instead.
        """
        org = _assign_organ(name.lower())
        return f"organ:{org}" if org else None

    def _organ_for_backend(name: str) -> str | None:
        """R-F3047 (2026-07-25) — attribute an EXTERNAL BACKEND (circuit-breaker
        name) to an organ from an EXPLICIT registry, never a keyword substring.

        `_assign_organ` exists to file MODULE PATHS into organs, and substring
        matching is right for that ("rag_", "brain_hook", "neural"). Reusing it
        for backend names produced two opposite errors at once, both measured
        live on 2026-07-25:

          semantic_scholar  -> organ:brain   FALSE POSITIVE. The brain keyword
              list contains "semantic" (for ARIA's own semantic_search), so an
              external academic-paper API being RATE-LIMITED painted "Brain &
              Memory" (56 modules) RED — and that single node was one of the
              three reds that put the whole ecosystem banner in DEGRADED. The
              same payload reported the real brain healthy: RAG available,
              459,634 facts indexed.
          search:duckduckgo -> None          FALSE NEGATIVE. A genuine search
          openalex          -> None          backend outage painted nothing.

        Token-matching would not have saved it either: "semantic" IS a token of
        "semantic_scholar". A backend's organ is a curation decision, not a
        string coincidence — so it is declared here. An UNKNOWN backend paints
        NOTHING (stays grey) rather than guessing, which is the same discipline
        this module already applies to colour: absence of proof is grey, never
        a claim.
        """
        base = name.lower().split(":")[-1].strip()
        org = _SENSOR_ORGAN.get(base)
        if org:
            return f"organ:{org}"
        logger.debug(
            "[R-F3047] sensor %r has no _SENSOR_ORGAN entry — painting nothing "
            "(add it there rather than relying on a keyword match)", name,
        )
        return None

    def _module_name_matches(name: str, nid: str) -> bool:
        # R-F2980 (F5): match a backend/agent name against the module's BASENAME
        # token, NOT any path substring — a naked `in nid.lower()` let a generic
        # name (e.g. "search") paint health onto unrelated modules (phantom
        # attribution). Require the name to BE a token of the basename.
        base = nid.split(".")[-1].lower()
        nm = name.lower()
        return bool(nm) and (nm == base or nm in base.split("_") or base in nm.split("_"))

    # aria-intel brain is answering THIS request → alive (honest positive signal)
    _apply("aria-intel", "green", "brain process (this request served)", "alive")

    # (1) breakers → the organ (and any matching module) the backend belongs to.
    # A grey breaker (unproven) applies NOTHING — grey is the absence-default, not a
    # sensor reading, so it must not enter the health map / inflate sensor coverage.
    for b in signals.get("breakers", []):
        name = b.get("name", "")
        color = _breaker_color(b)
        if color == "grey":
            continue
        val = f"{b.get('state')}" + (f"/{b['last_failure_reason']}" if b.get("last_failure_reason") else "")
        org = _organ_for_backend(name)   # R-F3047 — explicit registry, not keywords
        if org:
            _apply(org, color, f"circuit_breaker[{name}]", val)
        for nid in node_ids:
            if nid.startswith("mod:") and _module_name_matches(name, nid):
                _apply(nid, color, f"circuit_breaker[{name}]", val)

    # (2) agents/loops → the organ that owns them (+ the module if named)
    for a in signals.get("agents", []):
        aid = a.get("agent_id", "")
        color = _agent_color(a.get("heartbeat_age_s"))
        if color == "grey":
            continue
        val = f"beat {a.get('heartbeat_age_s')}s ago"
        org = _organ_for_name(aid)
        if org:
            _apply(org, color, f"agent[{aid}]", val)
        for nid in node_ids:
            if nid.startswith("mod:") and _module_name_matches(aid, nid):
                _apply(nid, color, f"agent[{aid}]", val)

    # (3) external limbs → service nodes / search organ
    _limb_target = {"aria-wa": "aria-wa", "aria-web": "aria-web", "aria-searxng": "organ:search", "searxng": "organ:search"}
    for lname, lv in (signals.get("limbs") or {}).items():
        tgt = _limb_target.get(lname)
        if not tgt:
            continue
        if lv.get("alive"):
            color = "green"
        elif lv.get("stale"):
            color = "amber"
        else:
            color = "red"
        _apply(tgt, color, f"liveness[{lname}]", f"age {lv.get('age_s')}s, status={lv.get('status')}")

    # (4) delivery surfaces → delivery organ (negative only: low success → amber/red)
    # R-F3048 (2026-07-25) — the guard was `total == 0`, which protects against
    # NO traffic but not against a sample too small to mean anything. Measured
    # live: "Delivery & Surfaces" went RED on `outcome[web] success 33% (n=3)`
    # — one failure in three — and that node was one of the three reds that put
    # the whole ecosystem banner in DEGRADED. A parallel audit found those three
    # records were in fact ONE logical interaction (one success plus two
    # duplicate empty_stream failures), so the true sample was n=1.
    #
    # A rate needs enough denominator to be a measurement rather than an
    # anecdote. Below the floor the surface stays GREY ("not enough traffic to
    # judge") — the same honest not-measured state this module already uses for
    # a node with no sensor, rather than a colour the number cannot support.
    surfaces = (signals.get("surfaces") or {}).get("surfaces", {})
    for sname, sh in surfaces.items():
        rate = sh.get("success_rate")
        total = int(sh.get("total", 0) or 0)
        if rate is None or total == 0:
            continue  # no traffic → no proof → leave grey
        if total < _MIN_OUTCOME_SAMPLES:
            logger.debug(
                "[R-F3048] surface %s: n=%d below the %d-sample floor — leaving "
                "grey (rate %.0f%% is an anecdote, not a measurement)",
                sname, total, _MIN_OUTCOME_SAMPLES, (rate or 0) * 100,
            )
            continue
        color = "green" if rate >= 0.95 else ("amber" if rate >= 0.7 else "red")
        _apply("organ:delivery", color, f"outcome[{sname}]", f"success {round(rate*100)}% (n={total})")

    # (5) open gaps → the organ (NEGATIVE only — never turns a node green)
    for g in signals.get("gaps", []):
        sev = str(g.get("severity", "")).upper()
        color = "red" if sev in ("HIGH", "CRITICAL", "4", "5") else "amber"
        gtype = g.get("type") or g.get("gap_type") or "gap"  # record_gap stores under "type"
        src = g.get("source") or gtype or ""
        org = _organ_for_name(src)
        if org and org in node_ids:
            cur = health.get(org)
            # gaps only escalate; never override a red, never create green
            if cur is None or _RANK[color] > _RANK[cur["color"]]:
                _apply(org, color, f"gap[{gtype}]", f"{sev or 'gap'}: {str(g.get('detail',''))[:80]}")
    return health


async def _apply_health_to(graph: dict[str, Any], signals: dict[str, Any], organ_of: dict[str, str]) -> None:
    """Colour a (scoped or full) graph's nodes + edges in place from live signals."""
    node_ids = {n["id"] for n in graph["nodes"]}
    hmap = _build_health_map(signals, node_ids, organ_of)
    for n in graph["nodes"]:
        # Function nodes have no live sensor, but §21 brain-wiring IS a real,
        # checkable structural signal: wired→green ("reports to the brain"),
        # dark→grey ("not wired" — a §21 gap, not a live fault).
        if n.get("type") == "function":
            wired = n.get("wired")
            n["health"] = "green" if wired else "grey"
            n["sensor"] = "§21 brain-wiring (@fail_wire)" if wired else "dark — no brain wiring"
            n["sensor_value"] = "wired" if wired else "dark"
            n["sensor_read_at"] = signals.get("read_at")
            continue
        h = hmap.get(n["id"], _GREY)
        n["health"] = h["color"]
        n["sensor"] = h["sensor"]
        n["sensor_value"] = h.get("value")
        n["sensor_read_at"] = h.get("read_at")
    # edge health = worst of its endpoints (a broken node bleeds into its links)
    for e in graph["edges"]:
        cs = hmap.get(e["source"], _GREY)["color"]
        ct = hmap.get(e["target"], _GREY)["color"]
        e["health"] = cs if _RANK[cs] >= _RANK[ct] else ct


def _organ_of_map(full: dict[str, Any]) -> dict[str, str]:
    return {n["module_id"]: n["category"] for n in full["nodes"] if n["type"] == "module"}


# ── R-F2974 (P3) — R-number / audit label join ──────────────────────────────
_RNUM_INDEX: dict[str, Any] = {"data": None, "at": 0.0}


def _load_rnum_index() -> dict[str, dict]:
    """{R-F####: {title, commit, status}} from the R-F540 reservation registry.
    Cached ~10 min (the log only grows)."""
    now = time.time()
    if _RNUM_INDEX["data"] is not None and (now - _RNUM_INDEX["at"]) < 600:
        return _RNUM_INDEX["data"]
    idx: dict[str, dict] = {}
    try:
        from . import r_number_registry as _rr
        for r in _rr.list_reservations():
            rn = r.get("r_number")
            if rn:
                idx[rn] = {"title": r.get("title", ""), "commit": r.get("commit_sha", ""),
                           "status": r.get("status", "")}
    except Exception as e:
        logger.debug("[ecosystem_map] rnum index load failed: %s", e)
    _RNUM_INDEX.update(data=idx, at=now)
    return idx


def _audit_refs(r_numbers: list[str]) -> list[dict]:
    idx = _load_rnum_index()
    out = []
    for rn in (r_numbers or []):
        meta = idx.get(rn, {})
        out.append({"r": rn, "title": meta.get("title", ""), "commit": meta.get("commit", ""),
                    "status": meta.get("status", "")})
    return out


# R-F2980 (F3): honest §21 brain-wiring tokens — checked in each function's OWN
# source span (decorators + body), so a function wired via ANY mechanism (@fail_wire,
# @wired, or an inline wire_success/brain_hook/record_gap call) is detected — and
# private functions are NOT falsely called "dark" (the old @fail_wire-only check
# skipped every _-prefixed function, over-claiming "no brain wiring").
_FN_WIRE_TOKENS = ("fail_wire", "@wired", "wire_success", "wire_failure",
                   "brain_hook.absorb", "brain_hook.observe", "capability_gaps.record_gap",
                   "record_gap(", "mistake_ledger.record", "record_error(", "record_signal(")


def _module_functions(module_id: str) -> list[dict]:
    """T3: the functions of ONE module (bounded), with honest §21 wired/dark status.
    On-demand only (never in the full graph — that'd be ~5000 nodes)."""
    rel = module_id.replace("aria_service.", "", 1).replace(".", os.sep)
    path = _ARIA_SERVICE / (rel + ".py")
    if not path.exists():                       # a PACKAGE node → its __init__.py
        path = _ARIA_SERVICE / rel / "__init__.py"
    if not path.exists():
        return []
    try:
        src = _read(path)
        tree = ast.parse(src)
    except Exception:
        return []
    lines = src.splitlines()
    fns = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # scan from the first decorator (above the def) to the function end
            starts = [n.lineno] + [getattr(d, "lineno", n.lineno) for d in getattr(n, "decorator_list", [])]
            start = max(1, min(starts))
            end = getattr(n, "end_lineno", n.lineno) or n.lineno
            span = "\n".join(lines[start - 1:end])
            wired = any(t in span for t in _FN_WIRE_TOKENS)
            fns.append({"name": n.name, "line": n.lineno,
                        "async": isinstance(n, ast.AsyncFunctionDef), "wired": wired})
    return fns


async def get_node(node_id: str) -> dict[str, Any]:
    """Detail for one node: parent, children, in/out import edges, R-numbers (with
    titles/commits), and — for a module — its functions (wired/dark)."""
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
    detail: dict[str, Any] = {
        "node": node,
        "parent": node.get("parent"),
        "children": children,
        "children_count": len(children),
        "imports_out": sorted(imports_out),
        "imports_in": sorted(imports_in),
        "fan_in": len(imports_in),
        "fan_out": len(imports_out),
        "r_numbers": node.get("r_numbers", []),
        "audit_refs": _audit_refs(node.get("r_numbers", [])),  # R-F2974 — titles/commits
    }
    # T3 (R-F2975): a module's functions (wired/dark) — the lowest level
    if node.get("type") == "module" and node.get("module_id"):
        fns = _module_functions(node["module_id"])
        detail["functions"] = fns
        detail["function_count"] = len(fns)
        detail["wired_functions"] = sum(1 for f in fns if f["wired"])
    return detail


async def get_coverage_nonblocking(max_wait_s: float = 2.5) -> dict[str, Any] | None:
    """R-F3062 (2026-07-25) — coverage for callers that must NOT block.

    `get_coverage()` awaits `build_structure()`, which re-parses every module
    when the file fingerprint changes — i.e. on the FIRST call after every
    deploy. Measured `build_ms=5823`. `/api/aria/health` awaited it inline, so
    that first call blew the brain dashboard's 8s per-panel budget, the panel
    was dropped from the aggregate, and `public/aria-brain.html` rendered
    `ECOSYSTEM: UNKNOWN` (`d.status?.toUpperCase() || 'UNKNOWN'`) — a blank
    banner on the operator's main surface purely because the cache was cold.

    Returns the coverage when it is already cached (or completes within
    `max_wait_s`), else None — having STARTED a background rebuild so the next
    caller is warm. The build task is held in a module-level set so it is never
    garbage-collected mid-flight, and it is never cancelled: `asyncio.wait_for`
    CANCELS its awaitable, which would throw away a 6-second parse and leave
    the cache cold forever, so the wait is done on a SHIELDED future.

    None means "not measured yet", which the caller must report as unknown —
    never as healthy. That is the same discipline this module applies to
    colour: absence of proof is grey, never green.

    Only a TIMEOUT yields None. A real exception PROPAGATES, because the
    caller's job is to report the actual reason: R-F2988 exists so /health
    surfaces "RuntimeError: sensor store unavailable" rather than a generic
    message, and swallowing it here would destroy that diagnostic.
    """
    global _COVERAGE_TASK
    if _CACHE.get("data") is not None:
        # Structure is cached; the remaining work is cheap signal-gathering.
        try:
            return await asyncio.wait_for(asyncio.shield(get_coverage()), max_wait_s)
        except asyncio.TimeoutError:
            return None

    if _COVERAGE_TASK is None or _COVERAGE_TASK.done():
        _COVERAGE_TASK = asyncio.create_task(get_coverage())
        _COVERAGE_TASKS.add(_COVERAGE_TASK)
        _COVERAGE_TASK.add_done_callback(_COVERAGE_TASKS.discard)
        logger.info(
            "[R-F3062] ecosystem coverage cold — started a background build; "
            "callers get 'not measured yet' until it lands"
        )
    try:
        return await asyncio.wait_for(asyncio.shield(_COVERAGE_TASK), max_wait_s)
    except asyncio.TimeoutError:
        return None


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
            # R-F2980 (review F2): state the exclusions precisely so "100%" is not
            # implied over a hidden set. Packages (__init__.py) ARE now included.
            "scope": "all non-test aria_service .py incl package __init__.py",
            "excluded": "conftest.py (pytest infra); tests/, __pycache__/, data/ dirs",
        },
        "import_edges": {
            "resolved_intra_repo": m["import_edge_count"],
            "unresolved_intra_repo": m["unresolved_intra_imports"],  # R-F2980 F4: renamed — includes relative-import misses
            "note": "100% of statically-resolvable intra-repo imports resolved; the rest (dynamic/getattr/late) are counted here, not hidden",
        },
        "call_edges": {
            "status": "declared_partial",
            "reason": "function-call graph is statically undecidable in Python (dynamic dispatch/getattr/late imports)",
        },
        # R-F3349 — the organ table is the ONLY curated layer, so it must declare its
        # own drift the way every other layer here does. Dead keywords are stale or
        # aspirational; shadowed ones are keywords their organ never wins. Fed the ids
        # build_structure already resolved so this adds no second filesystem walk.
        "organ_table": audit_organ_table(
            [n["module_id"] for n in full["nodes"] if n["type"] == "module"]
        ),
        "health_sensors": await _health_coverage(full),
        "meta": {"generated_at": m["generated_at"], "build_ms": m["build_ms"]},
    }


async def _health_coverage(full: dict[str, Any]) -> dict[str, Any]:
    """R-F2972 — how many nodes have a LIVE sensor vs grey (no-sensor). The rest is
    honestly grey, never a fabricated green."""
    try:
        signals = await _gather_signals()
        node_ids = {n["id"] for n in full["nodes"]}
        hmap = _build_health_map(signals, node_ids, _organ_of_map(full))
        colors = {"green": 0, "amber": 0, "red": 0}
        for h in hmap.values():
            if h["color"] in colors:
                colors[h["color"]] += 1
        total = len(full["nodes"])
        return {
            "total_nodes": total,
            "with_live_sensor": len(hmap),
            "grey_no_sensor": total - len(hmap),
            "pct_live_sensor": round(100.0 * len(hmap) / total, 1) if total else 0.0,
            "by_color": colors,
            "rule": "GREEN only from a positive liveness signal; absence of problems is GREY, never green",
        }
    except Exception as e:
        return {"status": "unavailable", "reason": str(e)[:120]}
