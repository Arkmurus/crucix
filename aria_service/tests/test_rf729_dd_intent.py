"""R-F729 (2026-05-20) — regression tests for DD intent broadening.

Background: Agent 1 DD audit found that `dd_orchestrator` (the 7-layer
DD path the system prompt advertises as available) only fired on
explicit verb forms ("DD on X" / "due diligence on X" / "ark-dd on
X"). Natural-language asks ("investigate counterparty X" / "deep
dive on X" / "background on X" / "vet the counterparty X") silently
fell through to the lighter `deep_research` narrative path.

R-F729 adds:
  1. Slash commands: /dd /diligence /due-diligence /pdd /ark-dd
     (low-friction explicit invocation, like R-F731's /screen)
  2. Conservative regex extensions to `_DD_INTENT_RE` +
     `_DD_ENTITY_CAPTURE_RE` for natural-language phrasings:
       - "investigate (the) counterparty X"
       - "counterparty check/diligence/review on X"
       - "deep dive on/into X"
       - "(full) background on X"
       - "vet (the) counterparty/company/firm/entity X"

These tests confirm:
  - All 5 slash variants route to dd_orchestrate
  - Each natural-language phrasing fires DD intent + captures entity
  - Existing DD verb forms ("DD on X" etc.) still work unchanged
  - Negative: generic prose ("I investigated some claims") does NOT
    spuriously fire
"""
from __future__ import annotations


def test_dd_slash_routes_to_dd_orchestrate():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/dd Acme Corp")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"
    assert intent.get("entity") == "Acme Corp"
    assert intent.get("_reason") == "dd_slash_command"


def test_diligence_slash_routes():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/diligence Smith Industries Ltd")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"
    assert intent.get("entity") == "Smith Industries Ltd"


def test_due_diligence_slash_with_hyphen():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/due-diligence Beijing Aero")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"


def test_pdd_slash_routes():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/pdd Sergei Petrov")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"
    assert intent.get("entity") == "Sergei Petrov"


def test_ark_dd_slash_routes():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/ark-dd Acme Corp Mozambique")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"


def test_dd_slash_caps_entity_to_500_chars():
    """R-F729: DD entity cap is 500 (vs 200 for /screen) because DD
    captures can legitimately include registered address + jurisdiction
    suffix."""
    from aria_service.routes.aria import _detect_tool_intent

    big = "x" * 1000
    intent = _detect_tool_intent(f"/dd {big}")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"
    assert len(intent.get("entity", "")) <= 500


# ── Natural-language DD intent extensions ──

def test_investigate_counterparty_natural_language():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("Investigate counterparty Acme Corp")
    # Must fire DD intent (entity captured downstream)
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"


def test_investigate_the_counterparty_natural_language():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("Investigate the counterparty Acme Corp")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"


def test_counterparty_check_natural_language():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("Counterparty check on Smith Industries")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"


def test_deep_dive_natural_language():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("Deep dive on Beijing Aero Holdings")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"


def test_background_on_natural_language():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("Background on Sergei Petrov")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"


def test_vet_counterparty_natural_language():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("Vet the counterparty Acme Corp")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"


# ── Existing patterns must still work ──

def test_existing_dd_on_phrasing_still_works():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("DD on Acme Corp")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"


def test_existing_due_diligence_on_phrasing_still_works():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("Run due diligence on Smith Industries")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"


def test_existing_profile_on_phrasing_still_works():
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("Profile on Beijing Aero")
    assert intent is not None
    assert intent.get("tool") == "dd_orchestrate"


# ── Negatives: generic prose must NOT misroute to DD ──

def test_generic_investigate_without_counterparty_does_not_fire_dd():
    """R-F729 negative: 'I investigated some claims' must NOT fire DD —
    the regex requires `counterparty` after `investigate` for the new
    branch to match."""
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("I investigated some claims earlier")
    if intent is not None:
        assert intent.get("tool") != "dd_orchestrate"


def test_casual_company_mention_does_not_fire_dd():
    """R-F729 negative: 'What is Acme Corp?' must not auto-fire DD."""
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("What is Acme Corp?")
    if intent is not None:
        assert intent.get("tool") != "dd_orchestrate"


def test_short_dd_slash_with_short_entity_falls_through():
    """R-F729: bare `/dd X` (single char) must NOT fire — defensive
    against typos. min entity length = 3."""
    from aria_service.routes.aria import _detect_tool_intent

    intent = _detect_tool_intent("/dd X")
    # Either None or routed to a different tool
    if intent is not None:
        assert intent.get("_reason") != "dd_slash_command"


def test_slash_takes_precedence_over_screen_slash():
    """R-F729 ordering: /dd appears AFTER /screen in the function so
    /screen Acme still wins on its own."""
    from aria_service.routes.aria import _detect_tool_intent

    # Pure /screen — should hit screen tool, not DD
    intent = _detect_tool_intent("/screen Acme Corp")
    assert intent is not None
    assert intent.get("tool") == "screen"
    assert intent.get("_reason") == "screen_slash_command"
