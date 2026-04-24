"""Tests for the shared self-introspection detector module.

Verifies the canonical regex matches the patterns the OpenClaw incident
showed need to be blocked, and doesn't false-fire on legitimate external
factual questions that should still reach Brave Answers.
"""
from aria_service.intel.self_infra_detector import (
    SELF_INFRA_INTROSPECTION_RE,
    KNOWN_FABRICATED_TOKENS,
    is_self_infra_query,
    contains_known_fabrication,
)


# Questions that MUST be classified as self-infra introspection
SELF_INFRA_POSITIVES = [
    "Why isn't my WhatsApp listener delivering messages?",
    "Why isn't my listener working?",
    "Why isn't my brain working?",
    "Why is my sweep failing?",
    "What's wrong with the gateway?",
    "What's wrong with my deploy?",
    "Why is aria silent?",
    "Why is aria not responding?",
    "Why isn't aria replying?",
    "Why is the brain stuck?",
    "Why is baileys disconnecting?",
    "What's broken in the listener?",
    "What's broken with the chat path?",
    "Why doesn't my fly deployment respond?",
    "Why isn't seenode redeploying?",
    "What is wrong with our infrastructure?",
    "Why won't my chat stream return?",
    "Why aren't my whatsapp messages getting through?",
]


# Questions that MUST NOT be classified as self-infra (still external,
# Brave Answers should be allowed to handle them)
SELF_INFRA_NEGATIVES = [
    "What is the latest news on Iran?",
    "Who is the current Saudi defence minister?",
    "What are the export control rules for ITAR?",
    "How does WhatsApp end-to-end encryption work?",
    "What is a Baileys library?",
    "Tell me about NATO STANAGs.",
    "What sanctions did the US announce yesterday?",
    "Summarise today's intel sweep.",
    "List every regional correlation from the most recent run.",
    "Why is the dollar falling?",
    # Subtle: not introspective even though it contains "broken"
    "What countries have broken with US sanctions policy?",
]


def test_self_infra_positives_match():
    for q in SELF_INFRA_POSITIVES:
        assert is_self_infra_query(q), f"Should match self-infra: {q!r}"
        assert SELF_INFRA_INTROSPECTION_RE.search(q) is not None


def test_self_infra_negatives_do_not_match():
    for q in SELF_INFRA_NEGATIVES:
        assert not is_self_infra_query(q), f"Should NOT match self-infra: {q!r}"


def test_empty_and_none_inputs():
    assert is_self_infra_query("") is False
    assert is_self_infra_query(None) is False
    assert contains_known_fabrication("") is False
    assert contains_known_fabrication(None) is False


def test_known_fabrication_detected():
    # Each known-fabricated token must be detected case-insensitively.
    for tok in KNOWN_FABRICATED_TOKENS:
        assert contains_known_fabrication(f"This is about {tok}.")
        assert contains_known_fabrication(tok.upper())
        assert contains_known_fabrication(f"  {tok}  trailing")


def test_known_fabrication_not_in_legitimate_text():
    legit = [
        "Baileys is the WhatsApp library we use.",
        "ARIA's listener runs on seenode.",
        "Today's sweep correlations: Middle East critical.",
    ]
    for s in legit:
        assert not contains_known_fabrication(s)


def test_three_call_sites_use_canonical_regex():
    """Compile-time assertion that the three downstream modules import
    the canonical regex rather than redefining their own. Catches
    accidental drift if someone adds a 4th caller without using the
    shared module."""
    from aria_service.routes.aria import _BRAVE_QA_SELF_INFRA_RE
    from aria_service.aria_engine import _SELF_INFRA_INTROSPECTION_RE as eng_re
    from aria_service.intel.reasoning_router import _SELF_INFRA_INTROSPECTION_RE as router_re

    # All three references must be the SAME object as the canonical one.
    assert _BRAVE_QA_SELF_INFRA_RE is SELF_INFRA_INTROSPECTION_RE
    assert eng_re is SELF_INFRA_INTROSPECTION_RE
    assert router_re is SELF_INFRA_INTROSPECTION_RE
