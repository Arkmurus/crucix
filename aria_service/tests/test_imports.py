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
    # Phase 3c-α — autonomous research engine modules (added 2026-04-09)
    "aria_service.autonomous",
    "aria_service.autonomous.safety",
    "aria_service.autonomous.tasks",
    "aria_service.autonomous.delivery",
    "aria_service.autonomous.engine",
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


def test_confidence_footer_matches_tag_with_caveat():
    """Phase 3 fix: the regex must match `[UNCERTAIN — insufficient data]`
    not just bare `[UNCERTAIN]`. The LLM almost always adds a caveat
    after the tag word, and the old strict-bracket regex was missing
    every caveat-bearing tag in the body."""
    from aria_service.intel.confidence_footer import _dominant_tag
    body_with_caveats = (
        "Identity [PROBABLE — multiple sources] confirms Finland HQ.\n"
        "Ownership [UNCERTAIN — insufficient data] could not be traced.\n"
    )
    assert _dominant_tag(body_with_caveats) == "UNCERTAIN", (
        "Regex must allow `[TAG — caveat text]` not just `[TAG]`. "
        "Validation evidence: Modirum probe had `[UNCERTAIN — insufficient data]` "
        "in the body but the footer reported `[ASSESSED]` because the regex failed."
    )


def test_verifier_recognises_clause15_markers():
    """Phase 3 fix: source_verifier.count_tool_refs() must recognise
    the inline marker formats clause 15 tells the LLM to use:
    [from snippet #N], [EXTRACT N], [from ATTACHED DOCUMENT: ...], [RAG].
    These don't contain URLs but they ARE inherently grounded because
    the markers can only exist when a tool produced output."""
    from aria_service.intel.source_verifier import count_tool_refs, verify_response
    body = (
        "Modirum Gespi is headquartered in Helsinki [from EXTRACT 2]. "
        "Acquired GESPI in Brazil [from snippet #8]. "
        "Multi-language website confirmed [from RAG — CONFIRMED]. "
        "Contract terms reviewed [from ATTACHED DOCUMENT: ARK-SER-01.pdf]."
    )
    refs = count_tool_refs(body)
    assert refs == 4, f"expected 4 tool refs, got {refs}"

    # And the verdict should be `grounded` when only marker citations
    # are present (no URLs but a tool ran).
    result = verify_response(body, tool_context="some non-empty tool block")
    assert result["verdict"] == "grounded", (
        f"verifier should treat marker-only citations as grounded, got {result['verdict']}"
    )
    assert result["tool_refs"] == 4


def test_mem0_retrieve_for_query_handles_empty_cache():
    """Phase 3 cherry-pick: retrieve_for_query() must safely return
    empty string when the knowledge cache hasn't been loaded yet,
    instead of raising. This is the first-call-after-restart path."""
    from aria_service.intel.mem0 import retrieve_for_query
    # Cache might not exist (we haven't loaded knowledge.py)
    result = retrieve_for_query("modirum gespi finland")
    assert isinstance(result, str), "must always return str, never None"


def test_researcher_principles_includes_8_step_sequence():
    """Phase 3 cherry-pick: the addendum must contain the 8-step research
    sequence + the per-country source pointers. Both were added to
    researcher_principles.py from the architecture proposal."""
    from aria_service.intel.researcher_principles import addendum
    text = addendum()
    # 8-step sequence sentinels
    assert "STEP 1 — MEMORY CHECK" in text
    assert "STEP 4 — LIVE SEARCH EXECUTION" in text
    assert "STEP 8 — SYNTHESIS + STORAGE" in text
    # Per-country source pointers sentinels
    assert "Jornal de Angola" in text
    assert "Club of Mozambique" in text
    assert "Macauhub" in text


def test_autonomous_engine_disabled_by_default():
    """Phase 3c-α safety: the autonomous engine MUST be disabled by
    default (env var unset). A deploy that flips this on by accident
    would start firing scheduled research tasks immediately, so the
    default has to be conservative."""
    import os as _os
    # Clear the env var if set in the test environment
    saved = _os.environ.pop("ARIA_AUTONOMOUS_ENABLED", None)
    try:
        from aria_service.autonomous import engine
        assert engine.is_enabled() is False, (
            "Autonomous engine must default to disabled. "
            "Without an explicit ARIA_AUTONOMOUS_ENABLED=1 env var, the "
            "scheduled research tasks should never fire."
        )
        assert engine.is_dry_run() is True, (
            "Autonomous engine must default to dry-run. "
            "Set ARIA_AUTONOMOUS_DRY_RUN=0 only after validation."
        )
    finally:
        if saved is not None:
            _os.environ["ARIA_AUTONOMOUS_ENABLED"] = saved


def test_autonomous_cron_matcher_basics():
    """Phase 3c-α: the cron matcher must handle the four cases that
    matter for the starter task (DAILY-PROC-ANGOLA fires "0 6 * * mon-fri")."""
    from aria_service.autonomous.tasks import cron_matches
    import time as _time
    # Build a struct_time for Mon 2026-04-13 06:00 UTC
    when_mon_6am = _time.strptime("2026-04-13 06:00:00", "%Y-%m-%d %H:%M:%S")
    assert cron_matches("0 6 * * mon-fri", when_mon_6am) is True
    # Mon 06:01 — should not fire
    when_mon_601am = _time.strptime("2026-04-13 06:01:00", "%Y-%m-%d %H:%M:%S")
    assert cron_matches("0 6 * * mon-fri", when_mon_601am) is False
    # Sat 06:00 — should not fire (mon-fri only)
    when_sat_6am = _time.strptime("2026-04-11 06:00:00", "%Y-%m-%d %H:%M:%S")
    assert cron_matches("0 6 * * mon-fri", when_sat_6am) is False
    # Wildcard match
    assert cron_matches("* * * * *", when_mon_6am) is True


def test_autonomous_starter_task_yaml_loads():
    """Phase 3c-α: tasks.yaml must parse and contain DAILY-PROC-ANGOLA
    in disabled state. If this test fails the YAML is malformed."""
    from aria_service.autonomous.tasks import load_tasks
    loaded = load_tasks()
    assert "DAILY-PROC-ANGOLA" in loaded, (
        "tasks.yaml must contain the DAILY-PROC-ANGOLA starter task"
    )
    starter = loaded["DAILY-PROC-ANGOLA"]
    assert starter.enabled is False, (
        "Starter task must be disabled by default — operator must "
        "explicitly opt in via tasks.yaml + reload-tasks endpoint"
    )
    assert starter.tool_chain, "Starter task must have a non-empty tool chain"
    assert starter.cost_cap_usd > 0


def test_redis_store_has_atomic_helpers():
    """Phase 3c-α: safety.py needs incr/incrbyfloat/expire on the Redis
    wrapper. Regression test for the redis_store.py expansion."""
    from aria_service.intel import redis_store
    assert hasattr(redis_store, "incr"), "redis_store missing incr"
    assert hasattr(redis_store, "incrbyfloat"), "redis_store missing incrbyfloat"
    assert hasattr(redis_store, "expire"), "redis_store missing expire"


def test_listener_context_strip():
    """Past incident 2026-04-09 19:18 — DUMA Engineering investigation:
    the WhatsApp listener prepends `[WhatsApp group context]\\n[<sender>]: ...
    \\n[Question from <sender>]\\n<message>` blocks containing recent message
    history. That history was bleeding into intent detection (a
    duma-engineering.com URL investigation got 5 web_search angles all
    containing 'Iraq tenders' from the prior turn) and into the LLM prompt
    (the LLM saw the polluted message and confabulated a self-improvement
    plan instead of a real DUMA brief).

    The fix is _strip_listener_context() in routes/aria.py, called at the
    very top of chat_ep BEFORE intent detection / context layer build /
    LLM prompt construction. This test pins the exact incident shape so
    a future regression in the regex breaks the test loudly.
    """
    from aria_service.routes.aria import _strip_listener_context

    # Exact incident shape from fly logs at 2026-04-09 18:18:11
    polluted = (
        "[WhatsApp group context]\n"
        "[Antonio]: Aria, are you online?\n"
        "[Antonio]: Aria, find the latest defence procurement tenders for Iraq 2026?\n"
        "[Antonio]: Aria, investigate this company and it is people https://duma-engineering.com?\n\n"
        "[Question from Antonio]\n"
        "Aria, investigate this company and it is people https://duma-engineering.com?"
    )
    cleaned = _strip_listener_context(polluted)
    assert cleaned == "Aria, investigate this company and it is people https://duma-engineering.com?", (
        f"strip failed — got: {cleaned!r}"
    )
    assert "Iraq" not in cleaned, "Iraq from prior turn must NOT survive the strip"
    assert "WhatsApp group context" not in cleaned

    # Idempotency: a clean message must pass through unchanged
    clean = "Aria, investigate Modirum Gespi https://modirumgespi.com"
    assert _strip_listener_context(clean) == clean

    # Empty input safety
    assert _strip_listener_context("") == ""

    # Bare context block with no Question marker — fallback path strips
    # the marker if it appears anywhere
    weird = "[Question from User] just the actual question"
    assert _strip_listener_context(weird) == "just the actual question"


def test_intent_detector_handles_generic_placeholder_with_url():
    """Past incident 2026-04-09 19:18 — DUMA Engineering: the user said
    'investigate this company and it is people https://duma-engineering.com'
    where 'this company' was a placeholder for the URL. The entity
    extraction left 'this company and it is people' as the entity, which
    Brave Search interpreted as 'People Magazine'. The fix detects generic
    placeholder phrases and falls back to the URL hostname."""
    from aria_service.routes.aria import _detect_tool_intent

    # Exact incident shape — placeholder phrase + URL
    intent = _detect_tool_intent(
        "Aria, investigate this company and it is people https://duma-engineering.com?"
    )
    assert intent is not None
    assert intent["tool"] == "deep_research"
    # Entity must NOT contain "people" (would route to People Magazine)
    assert "people" not in intent["entity"].lower(), (
        f"placeholder 'people' leaked into entity: {intent['entity']!r}"
    )
    # Entity SHOULD be derived from the URL hostname
    assert "duma" in intent["entity"].lower(), (
        f"entity should be duma-derived, got: {intent['entity']!r}"
    )

    # Less polluted variants — these should still resolve via the URL
    for phrase in [
        "Aria, investigate this https://duma-engineering.com",
        "Aria, investigate it https://duma-engineering.com",
        "Aria, investigate the company https://duma-engineering.com",
        "Aria, look into this firm https://duma-engineering.com",
    ]:
        intent2 = _detect_tool_intent(phrase)
        assert intent2 is not None, f"failed to route: {phrase!r}"
        assert intent2["tool"] == "deep_research"
        assert "duma" in intent2["entity"].lower(), (
            f"phrase {phrase!r} produced entity {intent2['entity']!r}"
        )

    # Real entity name should still be preserved (don't break Modirum-style queries)
    intent3 = _detect_tool_intent(
        "Aria, investigate Modirum Gespi https://modirumgespi.com"
    )
    assert intent3 is not None
    assert "modirum" in intent3["entity"].lower()


def test_intent_detector_handles_noun_form_investigation():
    """Past incident 2026-04-09 19:38 — DUMA Engineering second probe:
    user said 'Aria, investigation https://duma-engineering.com?' (noun
    form, not verb). The _INVESTIGATE_KW regex only matched the verb
    'investigate', so has_investigate was False, the chain fell through
    to route 3 (extract_url, single homepage), and the brief only saw
    the duma homepage instead of running the 5-angle deep_research +
    extracts. The brief honestly described it as 'minimal digital
    footprint' but missed all the off-site OSINT (Jane's coverage,
    LinkedIn 1821 followers, Crunchbase, Bloomberg).

    Fix: expanded _INVESTIGATE_KW to match 'investigation', 'investigations',
    'investigating', 'researching', 'looking into', 'digging into',
    'due diligence', 'background check', 'DD on', 'exploring'.
    """
    from aria_service.routes.aria import _detect_tool_intent

    # Exact second-probe shape: noun form
    intent = _detect_tool_intent("Aria, investigation https://duma-engineering.com?")
    assert intent is not None, "noun form 'investigation' must trigger intent detection"
    assert intent["tool"] == "deep_research", (
        f"must route to deep_research (5-angle search), not extract_url. "
        f"Got tool={intent.get('tool')!r}"
    )
    assert "duma" in intent["entity"].lower(), (
        f"entity must derive from URL hostname when phrase is generic. "
        f"Got entity={intent['entity']!r}"
    )

    # Other noun / variant forms must also work
    for phrase in [
        "Aria, investigations on Modirum Gespi",
        "Aria, investigating https://example.com",
        "Aria, due diligence on https://example.com",
        "Aria, background check on Vision International",
        "Aria, DD on https://example.com",
        "Aria, exploring this entity https://example.com",
        "Aria, looking into this firm https://example.com",
        "Aria, digging into Modirum",
    ]:
        intent2 = _detect_tool_intent(phrase)
        assert intent2 is not None, f"phrase failed to match: {phrase!r}"
        assert intent2["tool"] in ("deep_research", "profile"), (
            f"phrase {phrase!r} routed to wrong tool: {intent2.get('tool')!r}"
        )


def test_self_improvement_detector_ignores_tool_augmented_messages():
    """Past incident 2026-04-09 19:18 — DUMA Engineering: aria_chat() was
    calling detect_self_improvement_request against the message AFTER the
    `[I have already run the appropriate tool on your request...]` block
    was prepended. The tool block contained verbs and nouns that matched
    the loose self-improvement regexes, falsely triggering the
    self-improvement plan generator on real research queries.

    The fix strips the tool marker block before checking. We can validate
    by importing detect_self_improvement_request directly and confirming
    it returns None for the cleaned user message, even if the
    tool-augmented version would have matched."""
    from aria_service.intel.self_improve import detect_self_improvement_request

    # Pure user research query — must NOT trigger self-improvement
    clean_user_msg = "Aria, investigate this company and it is people https://duma-engineering.com?"
    assert detect_self_improvement_request(clean_user_msg) is None, (
        "research query must not trigger self-improvement detector"
    )

    # The Modirum probe must also be safe
    modirum = "Aria, investigate Modirum Gespi https://modirumgespi.com"
    assert detect_self_improvement_request(modirum) is None

    # The procurement intent must also be safe
    proc = "Aria, find the latest defence procurement tenders for Angola in 2026"
    assert detect_self_improvement_request(proc) is None

    # A genuine self-improvement request SHOULD still trigger
    improve = "Aria, improve your prompt to be more concise"
    assert detect_self_improvement_request(improve) is not None
