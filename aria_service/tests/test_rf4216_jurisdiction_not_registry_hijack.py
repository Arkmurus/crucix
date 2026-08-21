"""R-F4216 / C-196: a bare country name must not disable live search.

Live failure, WhatsApp, 2026-08-21 11:15-11:20, reproduced on the server:

    "What is your insight on the Turkey and Israel tensions in Syria?"
    -> tool_used: "registry_lookup", verification: no_tool, sources: []

The same question WITHOUT the word "Turkey" routes to `brave_answer` (a
web-class tool that reaches web_explorer). One country word flipped a
current-events question into a company-registry lookup, so web search never ran
and ARIA answered from training data with a cutoff caveat.

`_REGISTRY_JURISDICTIONS` maps bare country/adjective words — turkey, angola,
kenya, ghana, saudi, brazil, panama, poland, romania, bulgaria, estonia,
gibraltar — and the regex returned registry_lookup on the WORD ALONE, with no
company signal required and an early return that pre-empted every later rule.

The stated intent was never this. The comment above it reads: "Detects patterns
like 'Turkish company X', 'Estonian firm Y', 'MERSIS lookup for Z'." The
contract was written down and the implementation did something broader — the
same shape as C-187/C-189, and as §22a's NDA review being routed to
company_investigator.

Those jurisdictions are Arkmurus's core markets, so this silently removed live
search from most of the operator's natural questions about his own markets.
"""

from __future__ import annotations

import pytest

from aria_service.routes.aria import _detect_tool_intent, _WEB_CLASS_TOOLS_RF1713


# ── the live failure, and the markets it also hit ────────────────────────────

CURRENT_EVENTS = [
    "What is your insight on the Turkey and Israel tensions in Syria?",
    "Any news from Angola this week?",
    "What is happening in Kenya right now?",
    "Give me the latest on Saudi defence procurement",
    "How is the Brazil economy doing?",
    "Tell me about Ghana elections",
    "Latest news on Panama canal traffic",
    "What is the security situation in Poland?",
]


@pytest.mark.parametrize("message", CURRENT_EVENTS)
def test_a_country_word_alone_does_not_route_to_registry(message):
    intent = _detect_tool_intent(message)
    tool = (intent or {}).get("tool")
    assert tool != "registry_lookup", (
        f"a bare jurisdiction word hijacked this to a company-registry lookup, "
        f"so live search never runs: {message!r}"
    )


@pytest.mark.parametrize("message", CURRENT_EVENTS)
def test_those_questions_still_reach_a_web_class_tool(message):
    """The capability assertion: the user must actually get live search.

    Not routing to registry is necessary but not sufficient — routing to
    nothing would leave the same symptom (answered from training data).
    """
    intent = _detect_tool_intent(message)
    tool = (intent or {}).get("tool")
    assert tool in _WEB_CLASS_TOOLS_RF1713, (
        f"routes to {tool!r}, not a web-class tool — the user still gets a "
        f"training-data answer with no sources: {message!r}"
    )


# ── the capability the rule exists for must survive ──────────────────────────

GENUINE_REGISTRY = [
    "Turkish company Baykar Makina registry check",
    "MERSIS lookup for Baykar",
    "Estonian firm Skeleton Technologies company registration",
    "CNPJ for Petrobras",
    "Look up the company register entry for an Angolan entity",
    "KRS number for a Polish company",
]


@pytest.mark.parametrize("message", GENUINE_REGISTRY)
def test_real_registry_asks_still_fire(message):
    intent = _detect_tool_intent(message)
    assert (intent or {}).get("tool") == "registry_lookup", (
        f"the registry capability regressed — this is a genuine registry ask "
        f"and must still auto-fire: {message!r}"
    )


def test_registry_still_resolves_the_right_jurisdiction():
    """R-F3858: the guard must still be able to FAIL, not just be permissive."""
    assert _detect_tool_intent("Turkish company Baykar registry")["jurisdiction"] == "TR"
    assert _detect_tool_intent("CNPJ for Petrobras")["jurisdiction"] == "BR"
    assert _detect_tool_intent("KRS number for a Polish company")["jurisdiction"] == "PL"


def test_the_same_question_routes_the_same_with_and_without_the_country():
    """The exact asymmetry that caused the incident."""
    with_country = _detect_tool_intent(
        "What is your insight on the Turkey and Israel tensions in Syria?")
    without = _detect_tool_intent(
        "What are the tensions between Israel and Syria?")
    assert (with_country or {}).get("tool") in _WEB_CLASS_TOOLS_RF1713
    assert (without or {}).get("tool") in _WEB_CLASS_TOOLS_RF1713


# ── operator 2026-08-21: "everything is core market" ─────────────────────────
# The router named 12 jurisdictions while registry_adapters._DISPATCH serves 26,
# so AE CH CZ DE FI FR HU IL IN NG NO SK US ZA had working adapters that chat
# could not reach. The dispatch table is the authority now.

NEWLY_REACHABLE = [
    ("Nigerian company Dangote Cement registry check", "NG"),
    ("South African firm Sasol company registration", "ZA"),
    ("German company Rheinmetall registry", "DE"),
    ("French firm Thales company register", "FR"),
    ("Indian company Tata Motors registration", "IN"),
    ("Israeli company Elbit Systems registry", "IL"),
    ("Swiss firm Glencore company register", "CH"),
    ("UAE company registration for a Dubai entity", "AE"),
    ("Czech company Skoda registry check", "CZ"),
    ("Slovak firm registry lookup", "SK"),
    ("Hungarian company registration", "HU"),
    ("Finnish company Nokia register", "FI"),
    ("Norwegian firm Equinor company registry", "NO"),
    ("United States company Lockheed registration", "US"),
]


@pytest.mark.parametrize("message,iso2", NEWLY_REACHABLE)
def test_every_supported_jurisdiction_is_reachable_from_chat(message, iso2):
    intent = _detect_tool_intent(message)
    assert (intent or {}).get("tool") == "registry_lookup", (
        f"{iso2} has a working registry adapter but chat cannot route to it: {message!r}")
    assert intent["jurisdiction"] == iso2


def test_no_supported_jurisdiction_lacks_a_chat_alias():
    """Anti-rot: adding an adapter without a chat alias must FAIL, not go quiet.

    The original defect was a hand-maintained 12-country literal drifting behind
    a 26-entry dispatch table for 69 days, invisibly. §27d: do not hand-maintain
    a list that another table already owns.
    """
    from aria_service.intel.registry_adapters import supported_jurisdictions
    import inspect
    src = inspect.getsource(_detect_tool_intent)
    missing = [j for j in supported_jurisdictions() if f'"{j}": (' not in src]
    assert not missing, (
        f"these jurisdictions have registry adapters but no chat alias, so a user "
        f"cannot reach them by name: {missing}. Add them to "
        f"_REGISTRY_JURISDICTION_ALIASES."
    )


AMBIGUOUS_WORDS = [
    "Can you tell us about the latest defence news?",   # 'us' != United States
    "There is no company update this week",             # 'no' != Norway
    "What is in the report?",                           # 'in' != India
    "The de facto standard for reporting",              # 'de' != Germany
]


@pytest.mark.parametrize("message", AMBIGUOUS_WORDS)
def test_short_ambiguous_tokens_are_not_jurisdictions(message):
    """'us'/'no'/'in'/'de' are ordinary English words, not country codes."""
    intent = _detect_tool_intent(message)
    assert (intent or {}).get("tool") != "registry_lookup", (
        f"an ordinary English word was read as a jurisdiction: {message!r}")
