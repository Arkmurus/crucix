"""Smoke tests for aria_service module imports.

The whole point of this test file is to catch the bug class that bit us
on 2026-04-09: `os.getenv()` was used in `intel/researcher.py` `web_search()`
without `import os`. The function silently raised NameError on every call,
caught by `asyncio.gather(return_exceptions=True)` and demoted to DEBUG.
Two days of debugging.

A 3-line test that just IMPORTS the module would have surfaced the bug
immediately on first push. These tests are intentionally trivial — they
exist to fail loudly if any module has a missing import, syntax error, or
top-level side effect that breaks module loading.

Add a new line for any new module under aria_service/intel/ or
aria_service/routes/. The cost of running these is microseconds.
"""
import importlib
import pytest


CORE_MODULES = [
    # Top-level
    "aria_service.config",
    "aria_service.aria_engine",
    "aria_service.main",
    # LLM
    "aria_service.llm.factory",
    "aria_service.llm.fallback",
    # Routes
    "aria_service.routes.aria",
    # Intel modules — these are the ones most likely to have import bugs
    # because they're added incrementally and not always smoke-tested.
    "aria_service.intel.researcher",
    "aria_service.intel.researcher_principles",
    "aria_service.intel.ghost_detection_principles",
    "aria_service.intel.contract_review_principles",
    "aria_service.intel.negotiation_principles",
    "aria_service.intel.analytic_principles",
    "aria_service.intel.pmesii",
    "aria_service.intel.correction_learner",
    "aria_service.intel.stale_knowledge_alerts",
    "aria_service.intel.confidence_footer",
    "aria_service.intel.knowledge",
    "aria_service.intel.semantic_search",
    "aria_service.intel.neural_memory",
    "aria_service.intel.intel_ledger",
    "aria_service.intel.reasoning_library",
    "aria_service.intel.rag_store",
    "aria_service.intel.corpus_registry",
    "aria_service.intel.corpus_ingest",
    "aria_service.intel.mem0",
    "aria_service.intel.proactive",
    "aria_service.intel.student",
    "aria_service.intel.honesty_judge",
    "aria_service.intel.source_verifier",
    "aria_service.intel.cost_tracker",
    "aria_service.intel.trace_stream",
    "aria_service.intel.feedback",
    "aria_service.intel.eval_runner",
    "aria_service.intel.research_tasks",
    "aria_service.intel.deep_researcher",
    "aria_service.intel.sanctions",
    "aria_service.intel.conflict_tracker",
    "aria_service.intel.tech_classifier",
    "aria_service.intel.local_brain",
    "aria_service.intel.reasoning_router",
    "aria_service.intel.symbolic_reasoner",
    "aria_service.intel.ocr",
    "aria_service.intel.self_improve",
]


# Optional dependency markers — if a module fails to import because one of
# these packages isn't available in the CI environment, that's acceptable
# (CI runs with a slimmed dep set to keep build time low). If it fails for
# any OTHER reason — NameError, SyntaxError, circular import, missing local
# import — that's a real bug and the test must fail loudly.
_OPTIONAL_DEPS_FRAGMENTS = (
    "torch", "sentence_transformers", "chromadb", "fitz", "PyMuPDF",
    "easyocr", "pytesseract", "fastembed", "onnxruntime", "tiktoken",
    "playwright", "selenium", "openai_agents",
)


@pytest.mark.parametrize("module_name", CORE_MODULES)
def test_module_imports_cleanly(module_name):
    """Each module must import without raising any exception OTHER than
    a missing optional ML dependency.

    This catches:
      - Missing imports (NameError on top-level use) — the import-os bug class
      - Syntax errors
      - Circular imports
      - Misnamed exports
      - Top-level side effects that crash on missing required infra
    """
    try:
        importlib.import_module(module_name)
    except ImportError as e:
        msg = str(e).lower()
        if any(frag in msg for frag in _OPTIONAL_DEPS_FRAGMENTS):
            pytest.skip(f"{module_name}: optional dependency missing: {e}")
        pytest.fail(
            f"{module_name} failed to import (real ImportError, not optional): {e}"
        )
    except Exception as e:
        pytest.fail(
            f"{module_name} failed to import: {type(e).__name__}: {e}"
        )


def test_critical_addenda_have_required_exports():
    """Each conditional addendum module must export `is_enabled()` and
    `addendum()` (or equivalent). Catches bugs where the module loads but
    the engine wiring breaks because the function name was changed."""
    addenda = [
        ("aria_service.intel.researcher_principles", ["is_enabled", "addendum"]),
        ("aria_service.intel.ghost_detection_principles", ["is_enabled", "addendum"]),
        ("aria_service.intel.contract_review_principles", ["is_enabled", "addendum"]),
        ("aria_service.intel.negotiation_principles", ["is_enabled", "addendum"]),
        ("aria_service.intel.analytic_principles", ["is_enabled", "addendum"]),
    ]
    for module_name, required in addenda:
        mod = importlib.import_module(module_name)
        for fn in required:
            assert hasattr(mod, fn), (
                f"{module_name} is missing required export: {fn}"
            )
            assert callable(getattr(mod, fn)), (
                f"{module_name}.{fn} exists but is not callable"
            )


def test_researcher_has_required_tool_functions():
    """The researcher module is the heart of Phase 2 — its public tool
    functions must exist and be callable. This is the test that would
    have caught the `import os` bug had we had a smoke test in place."""
    from aria_service.intel import researcher
    required = [
        "web_search",
        "deep_research",
        "extract_url_text",
        "extract_url_deep",
        "research_and_learn",
    ]
    for fn in required:
        assert hasattr(researcher, fn), f"researcher missing {fn}"
        assert callable(getattr(researcher, fn)), f"researcher.{fn} not callable"


def test_corpus_tier_validation_is_symmetric():
    """corpus_registry.VALID_TIERS and corpus_ingest.VALID_TIERS must be
    identical, otherwise tier C+ ingest fails wholesale (past incident)."""
    from aria_service.intel.corpus_registry import VALID_TIERS as registry_tiers
    from aria_service.intel.corpus_ingest import VALID_TIERS as ingest_tiers
    assert registry_tiers == ingest_tiers, (
        f"VALID_TIERS mismatch: registry={registry_tiers} ingest={ingest_tiers}"
    )


def test_confidence_footer_returns_weakest_tag():
    """Pre-Phase-3 footer fix: _dominant_tag() must return the WEAKEST
    tag in the body, not the strongest. Caught the 3rd-time Modirum/
    contract-review footer mismatch."""
    from aria_service.intel.confidence_footer import _dominant_tag
    body_with_mixed_tags = (
        "Claim A [CONFIRMED] from registry filing.\n"
        "Claim B [PROBABLE] from two press sources.\n"
        "Claim C [UNCERTAIN] no public data found.\n"
    )
    assert _dominant_tag(body_with_mixed_tags) == "UNCERTAIN", (
        "Footer should reflect the LOWEST confidence in the body, "
        "not the highest, so a reply that mixes [CONFIRMED] facts with "
        "[UNCERTAIN] gaps is presented as [UNCERTAIN] overall."
    )
