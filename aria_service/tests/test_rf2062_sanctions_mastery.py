"""R-F2062: capability test — sanctions mastery fix.

Verifies that:
1. "sanctions" is in the TOPICS list (so student loop proactively studies it)
2. detect_topics() tags common sanctions questions as "sanctions" (not just "compliance")
3. brain_hook maps sanctions modules to include "sanctions" topic
"""
from __future__ import annotations

from aria_service.intel.student import TOPICS, detect_topics


def test_sanctions_in_topics():
    """Sanctions must be in the TOPICS list so the student loop studies it."""
    assert "sanctions" in TOPICS, (
        "sanctions not in TOPICS — student loop never proactively studies it"
    )


def test_core_mastery_tags_in_topics():
    """All four core mastery tags must be in TOPICS."""
    for tag in ("sanctions", "nato_standards", "strategic_geography", "export_control"):
        assert tag in TOPICS, f"{tag} not in TOPICS — student loop never studies it"


def test_detect_topics_tags_sanctions_question():
    """A question about sanctions status must tag as 'sanctions'."""
    questions = [
        "Is Hikvision sanctioned in the UK?",
        "What is the OFAC SDN status of this entity?",
        "Run a sanctions check on this company",
        "Are there any sanctions hits for this counterparty?",
        "What is the sanctions status of this person?",
        "Check OFSI sanctions list for this entity",
    ]
    for q in questions:
        topics = detect_topics(q)
        assert "sanctions" in topics, (
            f"Question {q!r} should tag as 'sanctions' but got {topics}"
        )


def test_detect_topics_tags_compliance_too():
    """A sanctions question should also tag as 'compliance' when the
    compliance pattern matches. Note: 'sanctioned' (past tense) doesn't
    match the compliance pattern's trailing \\b — this is a pre-existing
    limitation, not a regression from R-F2062."""
    topics = detect_topics("What is the OFAC SDN status of this entity?")
    assert "compliance" in topics, (
        "OFAC question should tag as 'compliance'"
    )
    assert "sanctions" in topics, (
        "OFAC question should tag as 'sanctions'"
    )


def test_brain_hook_maps_sanctions_module():
    """brain_hook must map the 'sanctions' module to include 'sanctions' topic."""
    from aria_service.intel.brain_hook import _MODULE_TOPICS
    assert "sanctions" in _MODULE_TOPICS, (
        "brain_hook missing 'sanctions' module mapping"
    )
    topics = _MODULE_TOPICS["sanctions"]
    assert "sanctions" in topics, (
        f"brain_hook 'sanctions' module should map to 'sanctions' topic, got {topics}"
    )


def test_brain_hook_maps_sanctions_propagation():
    """sanctions_propagation module must include 'sanctions' topic."""
    from aria_service.intel.brain_hook import _MODULE_TOPICS
    topics = _MODULE_TOPICS.get("sanctions_propagation", [])
    assert "sanctions" in topics, (
        f"sanctions_propagation should map to 'sanctions', got {topics}"
    )


def test_brain_hook_maps_sanctions_sources():
    """Sanctions source modules must include 'sanctions' topic."""
    from aria_service.intel.brain_hook import _MODULE_TOPICS
    for mod in ("sources_fcdo_sanctions", "sources_un_sc_sanctions"):
        topics = _MODULE_TOPICS.get(mod, [])
        assert "sanctions" in topics, (
            f"{mod} should map to 'sanctions', got {topics}"
        )
