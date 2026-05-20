"""R-F731 (2026-05-20) — regression tests for the /screen slash command.

Background: Agent 1 DD audit found that "is X sanctioned?" queries
routed to the heavy deep_research path (60s narrative) instead of the
cheap primary-source screen tool, because the screen tool only fired
on specific verbs ("screen X" / "compliance check X" / "fuzzy match X").
Users who didn't know the magic verb got the slow path by default.

R-F731 adds explicit slash-command aliases so the verb-guess is gone:
  /screen Acme Corp        → screen tool
  /sanctions Acme Corp     → screen tool
  /sanction-check Acme     → screen tool
  /compliance Acme         → screen tool

These tests confirm the slash forms route to the existing `screen`
tool with the entity correctly extracted, AND that the slash form
takes precedence over later intent detectors (briefing / DD /
deep_research).
"""
from __future__ import annotations


def test_screen_slash_routes_to_screen_tool():
    """R-F731: `/screen Acme Corp` returns intent={"tool": "screen", "entity": "Acme Corp"}."""
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/screen Acme Corp")
    assert intent is not None
    assert intent.get("tool") == "screen"
    assert intent.get("entity") == "Acme Corp"
    assert intent.get("_reason") == "screen_slash_command"


def test_sanctions_slash_routes_to_screen_tool():
    """R-F731: `/sanctions <entity>` is an alias for /screen."""
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/sanctions Smith Industries Ltd")
    assert intent is not None
    assert intent.get("tool") == "screen"
    assert intent.get("entity") == "Smith Industries Ltd"


def test_sanction_check_slash_routes_to_screen():
    """R-F731: `/sanction-check <entity>` also routes."""
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/sanction-check Beijing Aero")
    assert intent is not None
    assert intent.get("tool") == "screen"
    assert intent.get("entity") == "Beijing Aero"


def test_compliance_slash_routes_to_screen():
    """R-F731: `/compliance <entity>` routes (lower-friction word for
    compliance officers who think 'compliance' first, not 'screen')."""
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/compliance Sergei Petrov")
    assert intent is not None
    assert intent.get("tool") == "screen"
    assert intent.get("entity") == "Sergei Petrov"


def test_screen_slash_strips_trailing_punctuation():
    """R-F731: user trailing punctuation must not pollute the entity."""
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/screen Acme Corp.")
    assert intent is not None
    assert intent.get("tool") == "screen"
    assert intent.get("entity") == "Acme Corp"


def test_screen_slash_caps_entity_to_200_chars():
    """R-F731: defensive cap to stop a giant paste from blowing up
    downstream pipelines."""
    from aria_service.routes.aria import _detect_tool_intent

    huge = "x" * 500
    intent = _detect_tool_intent(f"/screen {huge}")
    assert intent is not None
    assert intent.get("tool") == "screen"
    assert len(intent.get("entity", "")) <= 200


def test_screen_slash_with_short_entity_falls_through():
    """R-F731: a bare `/screen A` (1-char entity) must NOT trigger —
    falls through to the regular intent chain to avoid spurious
    screens on typos."""
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/screen X")
    # Either None (fell through) or routed to a different tool
    if intent is not None:
        assert intent.get("_reason") != "screen_slash_command"


def test_screen_slash_takes_precedence_over_dd_intent():
    """R-F731: even if the message also contains 'due diligence' words,
    a leading /screen slash wins (explicit user intent)."""
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/screen Acme Corp full due diligence")
    assert intent is not None
    assert intent.get("tool") == "screen"
    assert intent.get("_reason") == "screen_slash_command"


def test_non_slash_screen_still_routes_to_screen_via_legacy_path():
    """R-F731 must NOT break the existing 'screen <entity>' verb path."""
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("screen Acme Corp")
    # The legacy path is at section 6 of _detect_tool_intent
    assert intent is not None
    assert intent.get("tool") in ("screen", "fuzzy_sanctions")


def test_plain_question_without_slash_does_not_trigger_screen_slash():
    """R-F731 negative: 'What is Acme Corp?' must not be misrouted to screen."""
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("What is Acme Corp?")
    # Should not have screen_slash_command reason
    if intent is not None:
        assert intent.get("_reason") != "screen_slash_command"
