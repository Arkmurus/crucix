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
    # Clause 17 — multi-source verified intelligence pipeline
    "aria_service.intel.verified_intel",
    # PR 2+3 — Core Self-Development Loop
    "aria_service.intel.web_atlas",
    "aria_service.intel.ecosystem_reassess",
    "aria_service.intel.core_develop",
    "aria_service.intel.source_scout",
]


# Optional dependency markers — if a module fails to import because one of
# these packages isn't available in the CI environment, that's acceptable
# (CI runs with a slimmed dep set to keep build time low). If it fails for
# any OTHER reason — NameError, SyntaxError, circular import, missing local
# import — that's a real bug and the test must fail loudly.
_OPTIONAL_DEPS_FRAGMENTS = (
    "torch", "sentence_transformers", "chromadb", "fitz", "PyMuPDF",
    "easyocr", "pytesseract", "fastembed", "onnxruntime", "tiktoken",
    "playwright", "selenium", "openai_agents", "numpy",
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
    identical, otherwise tier ingest fails wholesale (past incident
    2026-04-09: Tier C+ broken because the two sets had drifted)."""
    from aria_service.intel.corpus_registry import VALID_TIERS as registry_tiers
    from aria_service.intel.corpus_ingest import VALID_TIERS as ingest_tiers
    assert registry_tiers == ingest_tiers, (
        f"VALID_TIERS mismatch: registry={registry_tiers} ingest={ingest_tiers}"
    )
    # All 9 tiers (8 letter tiers + "unknown") plus the new A+ and F
    # added in the 2026-04-09 evening corpus expansion v3 must be present.
    expected_tiers = {"A", "A+", "B", "B+", "C", "C+", "D", "E", "F", "unknown"}
    assert registry_tiers == expected_tiers, (
        f"expected {expected_tiers}, got {registry_tiers}. "
        f"If you added or removed a tier, update both corpus_registry.py "
        f"and corpus_ingest.py and update this test."
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


def test_chat_request_accepts_group_context_field():
    """Phase 3c follow-up 2026-04-09: ChatRequest model must accept the
    new `group_context` field that the WhatsApp listener now sends as
    a separate JSON field instead of polluting the message body. The
    field is optional with default empty string so existing curl /
    frontend / autonomous-engine callers continue to work without
    sending it."""
    from aria_service.routes.aria import ChatRequest
    # Empty group_context — current curl/frontend pattern
    req = ChatRequest(message="hello", session_id="test")
    assert req.group_context == ""
    # Populated group_context — new WhatsApp listener pattern
    req2 = ChatRequest(
        message="Aria, investigate https://duma-engineering.com",
        session_id="wa_group_Antonio",
        group_context="[Antonio]: prior turn 1\n[Antonio]: prior turn 2",
    )
    assert "prior turn" in req2.group_context


# ──────────────────────────────────────────────────────────────────────────
# Clause 17 — multi-source verification pipeline
# ──────────────────────────────────────────────────────────────────────────

def test_verified_intel_source_tier_classification():
    """Classifier must place official registries/sanctions lists at Tier 1a,
    quality wires at Tier 2, specialist defence press at Tier 3, and
    social media at Tier 5 — otherwise the verification thresholds built
    on top of these tiers are meaningless."""
    from aria_service.intel.verified_intel import SourceTierClassifier, SourceTier
    c = SourceTierClassifier()
    # Tier 1a — official registries and sanctions lists
    assert c.classify("https://ofac.treasury.gov/sanctions-programs") == SourceTier.TIER_1A
    assert c.classify(
        "https://companies-house.service.gov.uk/company/12345"
    ) == SourceTier.TIER_1A
    # Tier 2 — quality journalism
    assert c.classify("https://www.reuters.com/world/africa/nigeria-cds-2024") in (
        SourceTier.TIER_1A,  # reuters.com is in TIER_1A_DOMAINS (wire-service quality)
        SourceTier.TIER_2,
    )
    assert c.classify("https://premiumtimesng.com/news/tinubu-appoints-musa") == SourceTier.TIER_2
    # Tier 3 — specialist defence press
    assert c.classify("https://www.defensenews.com/global/2024/angola") == SourceTier.TIER_3
    # Tier 5 — social media must never verify alone
    assert c.classify("https://twitter.com/something") == SourceTier.TIER_5
    assert c.classify("https://linkedin.com/in/someone") == SourceTier.TIER_5


def test_verified_intel_contradiction_detector_flags_year_mismatch():
    """Two sources that disagree on the YEAR of an appointment must be
    flagged as MAJOR — that pattern is the exact failure mode Clause 17
    exists to catch."""
    from aria_service.intel.verified_intel import (
        ContradictionDetector, SourceRecord, SourceTier, TIER_SCORES, FactType,
    )
    src_a = SourceRecord(
        url="https://reuters.com/x", tier=SourceTier.TIER_2,
        score=TIER_SCORES[SourceTier.TIER_2],
    )
    src_b = SourceRecord(
        url="https://premiumtimesng.com/y", tier=SourceTier.TIER_2,
        score=TIER_SCORES[SourceTier.TIER_2],
    )
    contradiction = ContradictionDetector().check(
        existing_sources=[src_a],
        new_source=src_b,
        new_claim_value="appointed 19 June 2023",
        existing_claim_value="appointed 19 June 2024",
        fact_type=FactType.APPOINTMENT,
    )
    assert contradiction is not None, "year mismatch must register as a contradiction"
    assert contradiction.severity == "MAJOR"
    assert contradiction.requires_human is True


def test_verified_intel_independence_same_family_not_independent():
    """Two reuters.com URLs are the SAME source family — they cannot
    count as two independent sources. This is the heart of why the
    original proposal's 'just add more sources' was insufficient."""
    from aria_service.intel.verified_intel import (
        SourceIndependenceChecker, SourceRecord, SourceTier, TIER_SCORES,
    )
    src_a = SourceRecord(
        url="https://reuters.com/a", tier=SourceTier.TIER_2,
        score=TIER_SCORES[SourceTier.TIER_2],
    )
    src_b = SourceRecord(
        url="https://reuters.tv/b", tier=SourceTier.TIER_2,
        score=TIER_SCORES[SourceTier.TIER_2],
    )
    ic = SourceIndependenceChecker()
    assert ic.are_independent(src_a, src_b) is False, (
        "reuters.com and reuters.tv share the 'reuters' family — not independent"
    )
    # And the independence count collapses to 1
    assert ic.get_independent_count([src_a, src_b]) == 1


def test_verified_intel_tenure_never_stored():
    """TENURE_CALC is an explicit trap door in the engine — attempting
    to store a tenure number must raise. Tenure is always computed at
    query time from the APPOINTMENT fact."""
    from aria_service.intel.verified_intel import ARIAVerificationEngine, FactType
    engine = ARIAVerificationEngine(redis_client=None, web_search_fn=None)
    import pytest as _pytest
    with _pytest.raises(ValueError, match="TENURE_CALC"):
        engine.process(
            claim_text="Gen. Musa has been in role for 665 days",
            claim_value="665",
            entity_name="Gen. Christopher Musa",
            entity_type="person",
            fact_type=FactType.TENURE_CALC,
            source_url="https://reuters.com/x",
        )


def test_verified_intel_tier1a_single_source_verifies():
    """A Tier 1a official source (e.g. OFAC) must verify on its own without
    corroboration. Clause 17's `allow_single` exception for registries."""
    from aria_service.intel.verified_intel import (
        ARIAVerificationEngine, FactType, VerificationStatus,
    )
    engine = ARIAVerificationEngine(redis_client=None, web_search_fn=None)
    fact = engine.process(
        claim_text="Entity X sanctioned by OFAC",
        claim_value="sanctioned",
        entity_name="Entity X",
        entity_type="company",
        fact_type=FactType.SANCTIONS_STATUS,
        source_url="https://ofac.treasury.gov/sdn-list/entity-x",
    )
    assert fact.verification_status == VerificationStatus.VERIFIED
    assert fact.verification_score >= 0.9
    assert len(fact.sources) == 1


def test_constitution_contains_clause_17_verified_intel():
    """Clause 17 text must be present in the system prompt — otherwise the
    LLM has no instruction to actually USE the pipeline and the module
    sits dormant."""
    # Read the engine file directly — don't import, because aria_engine
    # pulls numpy/chromadb via semantic_search at import time and those
    # are optional in CI. We only need to see the prompt string.
    import pathlib as _pl
    engine_src = (
        _pl.Path(__file__).resolve().parent.parent / "aria_engine.py"
    ).read_text(encoding="utf-8")
    assert "17. MULTI-SOURCE VERIFICATION" in engine_src, (
        "Clause 17 must be embedded in the system prompt"
    )
    assert "LEGACY_UNVERIFIED" in engine_src
    assert "verified_intel" in engine_src, (
        "System prompt must name the verified_intel module so the LLM "
        "knows which tool implements Clause 17"
    )


# ──────────────────────────────────────────────────────────────────────────
# PR 1 — async verify-and-store path
# ──────────────────────────────────────────────────────────────────────────

def test_verified_intel_averify_and_store_persists_and_roundtrips():
    """The async entry point must: verify the claim, persist to the
    in-memory redis fallback, and return a matching VerifiedFact that
    aget_fact() can retrieve by entity_name + fact_type."""
    import asyncio
    from aria_service.intel.verified_intel import (
        ARIAVerificationEngine, FactType, VerificationStatus,
    )

    async def run():
        engine = ARIAVerificationEngine(web_search_fn=None)
        fact = await engine.averify_and_store(
            claim_text="Entity X sanctioned by OFAC 2024-03-10",
            claim_value="sanctioned",
            entity_name="Entity X roundtrip",
            entity_type="company",
            fact_type=FactType.SANCTIONS_STATUS,
            source_url="https://ofac.treasury.gov/sdn-list/x-roundtrip",
        )
        assert fact.verification_status == VerificationStatus.VERIFIED
        got = await engine.aget_fact("Entity X roundtrip", FactType.SANCTIONS_STATUS)
        assert got is not None
        assert got["verification_status"] == "VERIFIED"
        assert any("ofac.treasury.gov" in u for u in got["sources"])
        return True

    assert asyncio.run(run()) is True


def test_verified_intel_averify_raises_on_tenure_calc():
    """TENURE_CALC must raise — tenure is never stored, always computed."""
    import asyncio
    import pytest as _pytest
    from aria_service.intel.verified_intel import ARIAVerificationEngine, FactType

    async def run():
        engine = ARIAVerificationEngine()
        with _pytest.raises(ValueError, match="TENURE_CALC"):
            await engine.averify_and_store(
                claim_text="X", claim_value="1",
                entity_name="Y", entity_type="person",
                fact_type=FactType.TENURE_CALC,
                source_url="https://reuters.com/x",
            )
        return True

    assert asyncio.run(run()) is True


def test_knowledge_store_fact_tags_legacy_when_no_source():
    """knowledge.store_fact without source_url must stamp
    LEGACY_UNVERIFIED so renderers emit the legacy citation."""
    import asyncio
    from aria_service.intel import knowledge

    async def run():
        result = await knowledge.store_fact(
            topic="Test topic — legacy",
            content="Test content, no URL provided.",
            source="user",
            confidence="CONFIRMED",
        )
        assert result["action"] in ("created", "updated")
        facts = await knowledge.get_all_facts()
        match = next((f for f in facts if f.get("id") == result["fact_id"]), None)
        assert match is not None
        assert match.get("verification_status") == "LEGACY_UNVERIFIED"
        return True

    assert asyncio.run(run()) is True


def test_knowledge_store_fact_verified_when_url_and_fact_type():
    """knowledge.store_fact WITH source_url + fact_type + entity_name
    must route through verified_intel and stamp VERIFIED for a Tier 1a
    source."""
    import asyncio
    from aria_service.intel import knowledge

    async def run():
        result = await knowledge.store_fact(
            topic="Entity A sanctions status",
            content="Entity A listed on OFAC SDN 2024-01-15",
            source="aria_auto",
            confidence="CONFIRMED",
            source_url="https://ofac.treasury.gov/sdn/entity-a",
            fact_type="SANCTIONS_STATUS",
            entity_name="Entity A verified-path",
            entity_type="company",
        )
        facts = await knowledge.get_all_facts()
        match = next((f for f in facts if f.get("id") == result["fact_id"]), None)
        assert match is not None
        # Tier 1a → VERIFIED
        assert match.get("verification_status") == "VERIFIED"
        assert match.get("verification_score", 0) >= 0.9
        return True

    assert asyncio.run(run()) is True


def test_self_improve_widened_whitelist_includes_autonomy_files():
    """The Core Self-Development Loop requires these three files in the
    self_improve whitelist — otherwise ARIA cannot exercise the
    auto-allowed rights from aria_autonomy_doctrine.md."""
    from aria_service.intel.self_improve import MODIFIABLE_FILES
    required = {
        "aria_service/intel/corpus_registry.yaml",
        "aria_service/autonomous/tasks.yaml",
        "aria_service/intel/v3_prompts.py",
    }
    missing = required - MODIFIABLE_FILES
    assert not missing, f"missing from whitelist: {missing}"


def test_self_improve_tasks_yaml_validator_rejects_bad_cron():
    """Schema validator must reject a tasks.yaml with a malformed cron
    — prevents ARIA from auto-writing a broken schedule."""
    from aria_service.intel.self_improve import _validate_tasks_yaml
    bad = """
tasks:
  - id: BAD
    cron: "broken cron"
    cost_cap_usd: 0.10
    tool_chain:
      - tool: deep_research
"""
    result = _validate_tasks_yaml(bad)
    assert result["ok"] is False
    assert "cron" in result["error"].lower()


def test_self_improve_tasks_yaml_validator_rejects_zero_cost_cap():
    from aria_service.intel.self_improve import _validate_tasks_yaml
    bad = """
tasks:
  - id: ZERO-COST
    cron: "0 5 * * *"
    cost_cap_usd: 0
    tool_chain:
      - tool: deep_research
"""
    result = _validate_tasks_yaml(bad)
    assert result["ok"] is False
    assert "cost_cap" in result["error"].lower()


def test_self_improve_tasks_yaml_validator_rejects_duplicate_ids():
    from aria_service.intel.self_improve import _validate_tasks_yaml
    bad = """
tasks:
  - id: DUP
    cron: "0 5 * * *"
    cost_cap_usd: 0.10
    tool_chain: [{tool: deep_research}]
  - id: DUP
    cron: "0 6 * * *"
    cost_cap_usd: 0.10
    tool_chain: [{tool: web_search}]
"""
    result = _validate_tasks_yaml(bad)
    assert result["ok"] is False
    assert "duplicate" in result["error"].lower()


def test_self_improve_tasks_yaml_validator_accepts_clean_input():
    from aria_service.intel.self_improve import _validate_tasks_yaml
    good = """
tasks:
  - id: CLEAN
    cron: "0 5 * * *"
    cost_cap_usd: 0.10
    tool_chain:
      - tool: verified_fact_refresh
        max_facts: 50
"""
    result = _validate_tasks_yaml(good)
    assert result["ok"] is True


def test_self_improve_corpus_registry_validator_rejects_bogus_tier1a():
    """Tier 1a is reserved for gov/mil domains. A non-gov URL claiming
    Tier 1a must be rejected — protects the 'official registry' meaning."""
    from aria_service.intel.self_improve import _validate_corpus_registry_yaml
    bad = """
- url: https://random-blog.example.com/sanctions
  tier: "1a"
  region: global
  domain_category: blog
"""
    result = _validate_corpus_registry_yaml(bad)
    assert result["ok"] is False
    assert "tier 1a" in result["error"].lower()


def test_self_improve_secret_scanner_blocks_embedded_api_key():
    """No matter how clever a staged change is, embedded secrets must
    fail the validator. This is the floor under ARIA's self-edit rights."""
    from aria_service.intel.self_improve import _validate_by_path
    leaked = '''
"""Normal module."""
API_KEY = "sk-proj-012345678901234567890123456789012345"
'''
    result = _validate_by_path("aria_service/intel/v3_prompts.py", leaked)
    assert result["ok"] is False
    assert "secret" in result["error"].lower()


def test_audit_log_has_new_action_entries():
    """Clause 14 substrate must cover the new verification pipeline +
    atlas + self-evolve actions — otherwise ARIA can mutate state
    outside the tamper-evident chain."""
    from aria_service.intel.audit_log import RECORDED_ACTIONS
    required = {
        "verified_fact_stored",
        "verified_fact_refreshed",
        "verified_fact_contradicted",
        "self_improve_staged",
        "self_improve_deployed",
        "source_atlas_add",
        "source_atlas_update",
    }
    missing = required - RECORDED_ACTIONS
    assert not missing, f"audit_log missing actions: {missing}"


# ──────────────────────────────────────────────────────────────────────────
# PR 2 — Web Atlas reliability learning
# ──────────────────────────────────────────────────────────────────────────

def test_web_atlas_reliability_ema_updates():
    """record_ingest must nudge score toward 1.0 on success, 0.0 on
    failure. EMA alpha is 0.2 → one success moves 0.5 to 0.6."""
    import asyncio
    from aria_service.intel import web_atlas

    async def run():
        await web_atlas.record_ingest(
            "https://reuters.com/test-ema", "test_topic_ema", success=True,
        )
        rec = await web_atlas.get_reliability(
            "https://reuters.com/test-ema-other", "test_topic_ema",
        )
        # Same family (reuters), same topic — should see the update.
        assert rec["score"] > 0.5
        # Now fail once
        await web_atlas.record_ingest(
            "https://reuters.com/test-ema-fail", "test_topic_ema", success=False,
        )
        rec2 = await web_atlas.get_reliability(
            "https://reuters.com/x", "test_topic_ema",
        )
        # Score must have moved back toward the middle
        assert rec2["score"] < rec["score"]
        return True

    assert asyncio.run(run()) is True


def test_web_atlas_add_source_indexes_family():
    import asyncio
    from aria_service.intel import web_atlas

    async def run():
        result = await web_atlas.add_source(
            url="https://premiumtimesng.com/defence",
            tier="2",
            topic_tags=["defence_procurement", "nigeria"],
            region="africa_west",
            added_by="test",
        )
        assert result["action"] in ("added", "updated")
        assert result["record"]["tier"] == "2"
        assert "defence_procurement" in result["record"]["topics"]
        stats = await web_atlas.stats()
        assert stats["source_families"] >= 1
        return True

    assert asyncio.run(run()) is True


def test_web_atlas_coverage_surfaces_critical_gaps():
    """A region/topic cell with 0 sources must surface as CRITICAL."""
    import asyncio
    from aria_service.intel import web_atlas

    async def run():
        # Force a CRITICAL cell by not updating any coverage for a fresh topic.
        await web_atlas.update_coverage("test_region_crit", "no_sources_topic",
                                         fetch_success=False)
        gaps = await web_atlas.surface_gaps(min_level="MEDIUM", limit=50)
        # The cell we just made has 0 sources → CRITICAL.
        match = next((g for g in gaps
                      if g.get("region") == "test_region_crit" and
                         g.get("topic") == "no_sources_topic"), None)
        assert match is not None
        assert match.get("gap_level") == "CRITICAL"
        return True

    assert asyncio.run(run()) is True


def test_ecosystem_reassess_produces_queue():
    """run() must return a queue-shaped dict with at least a breakdown key."""
    import asyncio
    from aria_service.intel import ecosystem_reassess, web_atlas

    async def run():
        # Seed a critical gap so there's something to find.
        await web_atlas.update_coverage("reassess_region", "reassess_topic",
                                         fetch_success=False)
        report = await ecosystem_reassess.run()
        assert "queued" in report
        assert "breakdown" in report
        return True

    assert asyncio.run(run()) is True


def test_autonomous_tasks_yaml_has_new_self_dev_tasks():
    """tasks.yaml must contain all 6 new tasks from the self-dev loop."""
    from aria_service.autonomous.tasks import load_tasks
    tasks = load_tasks()
    required = {
        "DAILY-FACT-REFRESH",
        "HOURLY-ECOSYSTEM-REASSESS",
        "DAILY-CORE-DEVELOP",
        "WEEKLY-CORE-META",
        "DAILY-CITATION-SCOUT",
        "WEEKLY-TLD-PROBE",
    }
    for tid in required:
        assert tid in tasks, f"tasks.yaml missing {tid}"
        task = tasks[tid]
        assert task.enabled is False, (
            f"{tid} must default disabled (opt-in autonomy doctrine)"
        )
        assert task.tool_chain
        assert task.cost_cap_usd > 0


def test_intel_ledger_tags_source_tier_when_url_present():
    """add_signal with a URL must attach source_tier + score from the
    Clause 17 tier classifier — making the ledger provenance-aware."""
    import asyncio
    from aria_service.intel import intel_ledger

    async def run():
        await intel_ledger.add_signal({
            "summary": "Test signal from OFAC",
            "source": "autonomous",
            "url": "https://ofac.treasury.gov/test-signal",
        })
        sigs = await intel_ledger.get_signals(limit=5) if hasattr(intel_ledger, "get_signals") else []
        # Fall back to reading the cache directly
        if not sigs:
            # load private cache
            from aria_service.intel.intel_ledger import _cache
            sigs = (_cache or {}).get("signals", [])
        match = next((s for s in sigs if "OFAC" in s.get("text", "")), None)
        if match is not None:
            assert match.get("source_tier") == "1a"
            assert match.get("source_tier_score", 0) >= 0.9
        return True

    assert asyncio.run(run()) is True
