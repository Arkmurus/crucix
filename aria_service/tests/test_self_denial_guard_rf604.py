"""R-F604 — self-capability DENIAL guard.

Past incident 2026-05-16: even after R-F593/F594/F595 shipped, ARIA's
fresh LLM call at 18:58 emitted "I cannot query OFSI directly through
my tooling" while fcdo_sanctions.lookup() was actively fetching the
OFSI ConList.xml in fly logs. The R-F594 count-pattern doesn't catch
qualitative denial claims — only numeric inventions.

R-F604 adds a new pattern that fires BLOCK on phrases matching:
  - "I cannot/can't [verb] <tool>"
  - "no <tool>" / "no direct <tool> access"
  - "<tool> is/are not available / unavailable"
  - "missing <tool>" / "lacking <tool>"
  - "need a tool integration for <tool>"
  - "without <tool>"

The <tool> alternation matches names from the R-F603 inventory.
"""
from __future__ import annotations


# ══════════════════════════════════════════════════════════════════
# PART A — denials caught
# ══════════════════════════════════════════════════════════════════

# These are EXACT quotes from the 2026-05-16 incident plus close variants
_DENIAL_PHRASES = (
    "I cannot query OFSI directly through my tooling",
    "No UK OFSI direct access",
    "No outbound email capability",
    "No corporate registry direct adapters for Saudi Panama Bulgaria",
    "I cannot query OFAC directly",
    "I do not have outbound email",
    "I don't have deep_research",
    "we need a tool integration for the OFSI API",
    "OFSI is not available",
    "registry adapters are unavailable",
    "missing registry adapters",
    "lacking deep_research capability",
    "without OFSI access",
)


def test_rf604_catches_canonical_denial_phrases():
    """All exact-quote denials from the 2026-05-16 incident must fire."""
    from aria_service.intel.self_claim_guard import scan_response
    for phrase in _DENIAL_PHRASES:
        vs = scan_response(phrase)
        cap_denials = [v for v in vs if v.pattern_id == "rf604_capability_denial"]
        assert cap_denials, (
            f"R-F604: failed to catch denial phrase {phrase!r}"
        )


def test_rf604_severity_is_always_block():
    """Self-denial is BLOCK even when self_introspect ran — denying a
    capability that exists is not WARN-able."""
    from aria_service.intel.self_claim_guard import scan_response
    phrase = "I cannot query OFSI directly"
    vs1 = scan_response(phrase, self_introspect_ran=False)
    vs2 = scan_response(phrase, self_introspect_ran=True)
    for vs in (vs1, vs2):
        denials = [v for v in vs if v.pattern_id == "rf604_capability_denial"]
        assert denials and all(v.severity == "BLOCK" for v in denials), (
            f"R-F604: denials must always be BLOCK, got {[v.severity for v in denials]}"
        )


def test_rf604_advice_cites_r_f603_inventory():
    """Advice text must direct the LLM to the R-F603 inventory anchor."""
    from aria_service.intel.self_claim_guard import scan_response
    vs = scan_response("No UK OFSI direct access")
    denials = [v for v in vs if v.pattern_id == "rf604_capability_denial"]
    assert denials
    advice = denials[0].advice
    assert "R-F603" in advice or "TOOL INVENTORY" in advice
    assert "fcdo_sanctions" in advice or "ConList" in advice


# ══════════════════════════════════════════════════════════════════
# PART B — over-firing protection
# ══════════════════════════════════════════════════════════════════

_LEGITIMATE_PHRASES = (
    # Generic refusals not about tools
    "I cannot guarantee the outcome",
    "I cannot speculate on future events",
    "I do not have the operator's permission",
    # Genuine call-failure descriptions (these are PERMITTED — see policy)
    "The OFSI fetch failed with timeout 30s",
    "The crawler returned zero pages for this URL",
    # Counterparty referring to their own tooling
    "The supplier has no export licence",
    "The buyer cannot pay in EUR",
    # Generic absence (no tool involved)
    "I have no information about this entity",
    "No data found in the registry for this name",
)


def test_rf604_does_not_overfire_on_legitimate_phrases():
    """Generic 'I cannot X' or 'No Y' with no tool keyword nearby must
    not trigger the guard."""
    from aria_service.intel.self_claim_guard import scan_response
    for phrase in _LEGITIMATE_PHRASES:
        vs = scan_response(phrase)
        denials = [v for v in vs if v.pattern_id == "rf604_capability_denial"]
        assert not denials, (
            f"R-F604 OVER-FIRE: {phrase!r} should not trigger; got {denials}"
        )


def test_rf604_handles_empty_safe():
    from aria_service.intel.self_claim_guard import scan_response
    assert scan_response("") == []
    assert scan_response(None) == []


# ══════════════════════════════════════════════════════════════════
# PART C — pattern surfaced via public API
# ══════════════════════════════════════════════════════════════════

def test_rf604_in_suspicious_phrases():
    from aria_service.intel.self_claim_guard import suspicious_phrases
    assert "rf604_capability_denial" in suspicious_phrases()


def test_rf604_in_get_stats_pattern_ids():
    """The Redis counter pattern_ids list must include the new pattern
    so dashboard panels render its count."""
    import pathlib
    src = pathlib.Path(
        "C:/code/crucix/aria_service/intel/self_claim_guard.py"
    ).read_text(encoding="utf-8", errors="ignore")
    fn = src.find("def get_stats")
    block = src[fn:fn + 3000]
    assert "rf604_capability_denial" in block


# ══════════════════════════════════════════════════════════════════
# PART D — interplay with other patterns
# ══════════════════════════════════════════════════════════════════

def test_rf604_does_not_block_clean_responses():
    """A response with no denial and no other forbidden pattern must
    return an empty violation list."""
    from aria_service.intel.self_claim_guard import scan_response
    clean = (
        "The OFSI Consolidated List was fetched successfully via "
        "fcdo_sanctions.lookup. Entity not found on the list. "
        "[from https://www.gov.uk/government/publications/the-uk-sanctions-list]"
    )
    vs = scan_response(clean)
    assert vs == [], f"R-F604 over-catch on clean response: {vs}"


def test_rf604_fires_alongside_rf594_when_both_present():
    """A response with BOTH a numeric hallucination and a denial must
    surface both pattern_ids."""
    from aria_service.intel.self_claim_guard import scan_response
    bad = (
        "34 autonomous tasks scheduled. I cannot query OFSI through my tooling."
    )
    vs = scan_response(bad)
    pids = {v.pattern_id for v in vs}
    assert "rf594_capability_count" in pids
    assert "rf604_capability_denial" in pids
