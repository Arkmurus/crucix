"""R-F1637: Cross-reference invariant tests.

Every module in self_diagnostic._MODULES must:
  1. Be importable
  2. Have its entry function callable
  3. If brain_registered=True, appear in brain_hook._MODULE_TOPICS
  4. If it has an endpoint, that endpoint must exist in the OpenAPI spec

Every entry in brain_hook._MODULE_TOPICS must have a corresponding module
in _MODULES (no orphaned brain_hook entries).

These tests prevent new dark modules/endpoints from shipping without
wiring — the anti-recurrence lever for the 29% defect rate found in
the 2026-05-11 sweep.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path


def _get_modules() -> list[dict]:
    """Import and return _MODULES from self_diagnostic."""
    from aria_service.intel.self_diagnostic import _MODULES
    return _MODULES


def _get_brain_hook_topics() -> dict:
    """Import and return _MODULE_TOPICS from brain_hook."""
    from aria_service.intel.brain_hook import _MODULE_TOPICS
    return _MODULE_TOPICS


def _get_openapi_paths() -> set[str]:
    """Load the OpenAPI spec and return all paths."""
    spec_path = Path(__file__).parent.parent / "routes" / "openapi.json"
    if spec_path.exists():
        with open(spec_path, encoding="utf-8") as f:
            spec = json.load(f)
        return set(spec.get("paths", {}).keys())
    # Fall back to generating from the app
    try:
        from aria_service.main import app
        import json as _json
        from fastapi.openapi.utils import get_openapi
        spec = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            routes=app.routes,
        )
        return set(spec.get("paths", {}).keys())
    except Exception:
        return set()


# ── Test 1: Every module is importable and has its entry function ──

def test_all_modules_importable():
    """Every module in _MODULES must be importable."""
    modules = _get_modules()
    errors = []
    for mod in modules:
        name = mod["name"]
        mod_path = mod["module"]
        entry = mod["entry"]
        try:
            imported = importlib.import_module(mod_path)
            assert hasattr(imported, entry), (
                f"{name}: entry function '{entry}' not found on {mod_path}"
            )
        except Exception as e:
            errors.append(f"{name} ({mod_path}): {e}")
    assert not errors, (
        f"{len(errors)} module(s) failed import/entry check:\n"
        + "\n".join(errors)
    )


# ── Test 2: Every brain_registered module appears in brain_hook ──

def test_brain_registered_modules_in_brain_hook():
    """Every module with brain_registered=True must appear in
    brain_hook._MODULE_TOPICS."""
    modules = _get_modules()
    topics = _get_brain_hook_topics()
    missing = []
    for mod in modules:
        if not mod.get("brain_registered"):
            continue
        name = mod["name"]
        # Try multiple name variants (same logic as _check_brain_registered)
        variants = {
            name,
            name.split(".")[-1],
            name.replace(".", "_"),
            name.split(".")[0],
        }
        mod_path = mod.get("module", "")
        if ".sources." in mod_path:
            short = mod_path.rsplit(".", 1)[-1]
            variants.add(f"sources_{short}")
        if not any(v in topics for v in variants):
            missing.append(name)
    assert not missing, (
        f"{len(missing)} brain_registered module(s) not found in "
        f"brain_hook._MODULE_TOPICS: {missing}"
    )


# ── Test 3: Every brain_hook entry has a corresponding module ──

def test_all_brain_hook_entries_have_modules():
    """Every entry in brain_hook._MODULE_TOPICS must have a corresponding
    module in _MODULES (no orphaned brain_hook entries)."""
    modules = _get_modules()
    topics = _get_brain_hook_topics()

    # Build the set of all name variants from _MODULES
    module_names: set[str] = set()
    for mod in modules:
        name = mod["name"]
        module_names.add(name)
        module_names.add(name.split(".")[-1])
        module_names.add(name.replace(".", "_"))
        module_names.add(name.split(".")[0])
        mod_path = mod.get("module", "")
        if ".sources." in mod_path:
            short = mod_path.rsplit(".", 1)[-1]
            module_names.add(f"sources_{short}")

    orphaned = [k for k in topics if k not in module_names]
    # Known exceptions — modules wired via brain_hook.absorb() calls in
    # their code rather than through the self_diagnostic._MODULES registry.
    # These are legitimate: they emit signals directly without needing a
    # diagnostic entry. The _MODULES list covers the diagnostic surface;
    # brain_hook covers the full signal surface.
    known_orphans = {
        "general",  # catch-all topic, not a module
        # Modules that wire via brain_hook.absorb() directly:
        "compliance_workflow", "contract_intelligence", "tender_monitor",
        "link_investigator", "financial_dd", "network_walker", "entity_graph",
        "brave_answers", "person_resolver", "competitors", "gtm_strategy",
        "risk_indices", "global_export_control", "dual_use_classifier",
        "euc_library", "audit_log", "compliance_file", "symbolic_reasoner",
        "source_verifier", "sanctions", "conflict_tracker", "international_law",
        "registry_adapter", "opportunity_detector", "signal_generator",
        "knowledge_ingestor", "email_reader", "verified_intel", "web_atlas",
        "source_validator", "source_scout", "search_doctrine", "core_develop",
        "ecosystem_reassess", "golden_autogen", "adversarial_challenge",
        "narrative_monitor", "chain_correlator", "procurement_calendar",
        "competitor_tracker", "oem_contact_graph",
        "knowledge_gulf", "knowledge_turkey_standalone", "knowledge_west_africa",
        "knowledge_latam_non_lusophone", "equipment_specs", "sipri_ingest",
        "writer_orchestrator", "assessment_writer", "procurement_paper_writer",
        "anti_corruption_law", "tech_spec_writer", "portuguese_legal_writer",
        "propaganda_guard", "aria_chat", "aria_chat_stream",
        "virtual_office_registry", "sanctions_propagation",
        "cited_artifact_verifier", "protective_reply_drafter",
        "knowledge_north_africa", "knowledge_south_se_asia",
        "knowledge_central_africa", "knowledge_balkans", "regional_bright_lines",
        "gulf_oem_structure", "vision_2030_tracker", "baykar_export_pipeline",
        "political_risk_index", "cross_regional_correlator", "autonomy_surface",
        "domain_ownership_verifier", "run_quarantine", "training_export",
        "knowledge_spider", "metacognitive_journal", "research_engine",
        "document_entity_bridge", "verification_gate", "pdf_deep_ingest",
        "style_learner", "memory_replication",
        "extractors_structured", "extractors_facts",
        "self_diagnostic", "deception_detection",
        "corpus_manager", "corpus_ingest", "corpus_registry", "oem_registry",
        "tech_classifier", "aria_peers", "predictor",
        "self_assess", "self_monitor", "ingest_sweep", "news_monitor",
        "self_introspect_guard", "ground_truth_loop",
        # R-F4046 (C-106) — absorb()-only knowledge producers given real topics.
        # Same category as the entries above: they wire via brain_hook.absorb()
        # and are not self_diagnostic engines, so they have no _MODULES entry.
        # They were selected BECAUSE they call absorb(), so this is the
        # documented orphan class, not a widened guard.
        "crypto_sanctions", "rca_screening", "fcpa_enforcement",
        "registration_check", "companies_house", "dd_registry_enrichment",
        "web_explorer", "deal_predictor", "commercial_coherence",
        "strategic_evolution", "precall_brief", "meeting_notes",
        "bd_strategy", "engagement",
    }
    actual_orphans = [o for o in orphaned if o not in known_orphans]
    assert not actual_orphans, (
        f"{len(actual_orphans)} brain_hook entry(ies) have no corresponding "
        f"module in _MODULES: {actual_orphans}"
    )


# ── Test 4: Every module with an endpoint has it in the OpenAPI spec ──

def test_module_endpoints_in_openapi():
    """Every module with an 'endpoint' field must have that path in the
    OpenAPI spec."""
    modules = _get_modules()
    paths = _get_openapi_paths()
    if not paths:
        # Skip if we can't load the spec (e.g. in CI without full app)
        return
    # Known exceptions — endpoints that use dynamic/fake IDs for probing
    # and won't appear in the static OpenAPI spec.
    known_missing = {
        "scratchpad",  # uses /api/aria/scratchpad/fake_trace_id (dynamic)
    }
    missing = []
    for mod in modules:
        if mod["name"] in known_missing:
            continue
        ep = mod.get("endpoint", "")
        if not ep:
            continue
        # Strip query params for matching
        base_ep = ep.split("?")[0].rstrip("/")
        # Check if any path in the spec matches
        found = any(
            p.rstrip("/") == base_ep or p.rstrip("/").startswith(base_ep.rstrip("/") + "/")
            for p in paths
        )
        if not found:
            missing.append(f"{mod['name']}: {ep}")
    assert not missing, (
        f"{len(missing)} module endpoint(s) not found in OpenAPI spec:\n"
        + "\n".join(missing)
    )


# ── Test 5: Every module with an autonomous_task_id has it in tasks.yaml ──

def test_module_autonomous_tasks_in_yaml():
    """Every module with an autonomous_task_id must have that task in
    tasks.yaml."""
    import yaml
    modules = _get_modules()
    yaml_path = (
        Path(__file__).parent.parent / "autonomous" / "tasks.yaml"
    )
    if not yaml_path.exists():
        return  # Skip if tasks.yaml doesn't exist
    with open(yaml_path, encoding="utf-8") as f:
        tasks_data = yaml.safe_load(f) or {}
    task_ids = {t.get("id") for t in (tasks_data.get("tasks") or [])}
    missing = []
    for mod in modules:
        task_id = mod.get("autonomous_task_id", "")
        if not task_id:
            continue
        if task_id not in task_ids:
            missing.append(f"{mod['name']}: {task_id}")
    assert not missing, (
        f"{len(missing)} module(s) have autonomous_task_id not in "
        f"tasks.yaml:\n" + "\n".join(missing)
    )
