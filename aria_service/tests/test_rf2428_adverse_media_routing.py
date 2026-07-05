"""R-F2428 (Blocker 2) — capability test: adverse-media phrasings must route to
deep_research (which runs researcher.py + R-F2426's adverse angles), not to
tool=None (answered from model memory).

Root cause (proven live, a99e smoke 2026-07-05):
  "Adverse media check on Wagner Group" routed to tool=None — adverse-media is
  absent from _COMPLIANCE_KW_RE / _SCREEN_KW / _INVESTIGATE_KW — so the LLM
  answered from memory and R-F2426's adverse angles never fired.

Fix (`_ADVERSE_MEDIA_KW_RE` + `_extract_adverse_subject` in
`_detect_tool_intent`): route adverse-media / negative-news / reputational-check
phrasings to deep_research with the adverse term FOLDED into the entity so
R-F2426's `_adverse_signalled` fires (prepending the sanctions/war-crimes
angles). §22a preserved — an attached document stays on the LLM-pure path.

These tests drive the REAL `_detect_tool_intent`, the chat/WA/TG entry point.
"""
from __future__ import annotations

import pytest

from aria_service.routes.aria import _detect_tool_intent as detect


@pytest.mark.parametrize(
    "message,subject",
    [
        ("Adverse media check on Wagner Group", "Wagner Group"),
        ("run an adverse media check on Sberbank", "Sberbank"),
        ("Wagner Group adverse media", "Wagner Group"),
        ("negative news on Rosneft", "Rosneft"),
        ("check Wagner Group for adverse media", "Wagner Group"),
        ("reputational check on Acme Corp", "Acme Corp"),
        ("adverse media screening for KTRV", "KTRV"),
    ],
)
def test_adverse_media_routes_to_deep_research(message, subject):
    intent = detect(message)
    assert intent is not None, f"{message!r} produced no tool intent (was None)"
    assert intent["tool"] == "deep_research", (
        f"{message!r} routed to {intent['tool']!r}, expected deep_research"
    )
    entity = (intent.get("entity") or "")
    # The subject must be present AND the adverse signal folded in so R-F2426
    # fires (`_adverse_signalled` looks for an adverse term in the entity).
    assert subject.lower() in entity.lower(), (
        f"{message!r} => entity {entity!r} missing subject {subject!r}"
    )
    assert "adverse" in entity.lower(), (
        f"{message!r} => entity {entity!r} lost the adverse signal (R-F2426 "
        "would not fire)"
    )


def test_attached_document_stays_llm_pure():
    # §22a — a doc-review ask that mentions adverse media must NOT dispatch an
    # external tool; it goes to the LLM-pure path (None).
    msg = "[ATTACHED DOCUMENT foo] run an adverse media check on the attached report"
    assert detect(msg) is None


def test_definitional_question_not_routed_as_entity():
    # "what is adverse media" is a definition, not a subject — must not route a
    # question string as the deep_research entity.
    intent = detect("what is adverse media")
    if intent is not None:
        assert intent.get("_reason") != "adverse_media_rf2428"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
