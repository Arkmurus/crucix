"""R-F514 — local_brain yield-guard for comprehension/scratchpad prefix.

Discovered during R-F510 verify-after-fix. The /chat path at
routes/aria.py:6506-6509 prepends comprehension.build_prefix() output
to message_for_llm, then passes the combined text to aria_chat →
reasoning_router.try_local_reasoning → local_brain.try_local_response.

The comprehension prefix at comprehension.py:415-417 literally contains:

    "  3. If the request is HIGH-STAKES and you are NOT 100% confident,
    state what you would need to confirm — do NOT fabricate verifiable
    facts (jurisdictions, financials, sanctions status) to fill the gap."

local_brain.py:68 sanctions regex `\\b(?:is|are)\\s+(.+?)\\s+(?:on\\s+the\\s+)?
(?:sanction(?:ed|s\\s+list)?|ofac|ofsi|embargo(?:ed)?)` matches
"is HIGH-STAKES" lazily up to the first "sanctions" keyword — which is
in the SAME prefix at "sanctions status". Result: arg becomes a ~150-
char prompt-fragment, fuzzy_screen is called on garbage, _fmt_sanctions
emits a bogus "*Sanctions screen — HIGH-STAKES and you are NOT 100%..."
headline.

Live failure reproduced 2026-05-14: /chat POST with body
{"message": "Is Hikvision sanctioned in the UK?"} returned the
prompt-fragment-headlined garbage. The bug has likely been firing on
every non-trivial /chat sanctions query since the comprehension pass
landed at commit 0c641c3 (2026-04-18) — ~25 days.

R-F514 adds a yield-guard parallel to the existing `[TOOL:` /
`[I have already run...` guard at local_brain.py:262-263. When the
message contains comprehension or scratchpad prefix markers, yield
to the LLM. Those messages are by definition non-trivial and worth
the LLM cost.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import local_brain


# The actual comprehension prefix the live system builds — verbatim
# excerpt from comprehension.build_prefix() so the test pins the
# prefix-leak repro reliably.
_COMPREHENSION_PREFIX = (
    "[COMPREHENSION PASS — Clause 21, Understand Before Act]\n"
    "Complexity: MODERATE\n"
    "Confidence: CONFIDENT\n"
    "\n"
    "RESPONSE CONTRACT for this turn:\n"
    "  1. Start your reply with 'UNDERSTOOD AS: <one sentence restating "
    "what the user is asking>'. Keep it tight.\n"
    "  2. If you are filling any gap with an assumption, name it "
    "explicitly: 'Assuming <X>...'.\n"
    "  3. If the request is HIGH-STAKES and you are NOT 100% confident, "
    "state what you would need to confirm — do NOT fabricate verifiable "
    "facts (jurisdictions, financials, sanctions status) to fill the gap.\n"
    "[END COMPREHENSION PASS]\n"
    "\n"
    "USER MESSAGE FOLLOWS:\n"
)


# ───────── R-F514 — comprehension prefix MUST cause yield ─────────


def test_rf512_comprehension_prefix_yields_no_short_circuit(monkeypatch):
    """The exact /chat path message shape: comprehension prefix
    prepended to user message. local_brain MUST yield so the LLM
    runs — preventing the 150-char prompt-fragment-headlined garbage
    response observed live on 2026-05-14."""

    called = {"fuzzy_screen": 0}

    async def _no_screen(name):
        called["fuzzy_screen"] += 1
        return {"matches": [], "blocked": False, "variants_tried": [name]}

    monkeypatch.setattr(local_brain.fuzzy_sanctions, "fuzzy_screen", _no_screen)

    msg = _COMPREHENSION_PREFIX + "Is Hikvision sanctioned in the UK?"
    out = asyncio.run(local_brain.try_local_response(msg))

    assert out.get("answered") is False, (
        "When the comprehension prefix is present, local_brain MUST "
        "yield. Without this, the sanctions regex matches "
        "'is HIGH-STAKES...' from the prefix and short-circuits with "
        "a garbage headline."
    )
    assert called["fuzzy_screen"] == 0, (
        "fuzzy_screen MUST NOT be called when prefix markers detected "
        "— the yield happens before any pattern-driven dispatch."
    )


@pytest.mark.parametrize(
    "marker_msg",
    [
        # Just the start marker
        "[COMPREHENSION PASS — Clause 21, Understand Before Act]\nIs X sanctioned in the UK?",
        # Just the end marker
        "RESPONSE CONTRACT...\n[END COMPREHENSION PASS]\nIs Y sanctioned?",
        # Just the USER MESSAGE FOLLOWS: marker
        "Some preceding text\nUSER MESSAGE FOLLOWS:\nIs Z sanctioned?",
        # Clause 22 scratchpad marker (appended, not prepended)
        "Is W sanctioned?\n\n[CLAUSE 22 — Think Before Speak]\nproduce a scratchpad...",
        # Scratchpad tag in body (defensive)
        "Is V sanctioned?\n\n<scratchpad>private reasoning</scratchpad>",
    ],
)
def test_rf512_any_prefix_marker_triggers_yield(marker_msg, monkeypatch):
    """Any single prompt-prefix marker is sufficient — five markers
    are checked because chat_ep may construct messages with different
    subsets present (comprehension may fire without scratchpad and
    vice versa)."""

    async def _no_screen(name):
        return {"matches": [], "blocked": False, "variants_tried": [name]}

    monkeypatch.setattr(local_brain.fuzzy_sanctions, "fuzzy_screen", _no_screen)

    out = asyncio.run(local_brain.try_local_response(marker_msg))
    assert out.get("answered") is False, (
        f"Prefix-marker message MUST yield to LLM:\n{marker_msg[:200]}"
    )


def test_rf512_clean_message_still_short_circuits(monkeypatch):
    """The yield-guard is narrow — it only fires when prefix markers
    are present. A clean unqualified sanctions question (no prefix,
    no jurisdiction) must still take the local short-circuit."""

    async def _fake_screen(name):
        return {
            "matches": [],
            "blocked": False,
            "variants_tried": [name],
            "top_score": 0,
        }

    monkeypatch.setattr(local_brain.fuzzy_sanctions, "fuzzy_screen", _fake_screen)

    out = asyncio.run(local_brain.try_local_response("Is Acme sanctioned?"))
    assert out.get("answered") is True
    assert out.get("intent") == "sanctions"
    assert "Acme" in out["response"], (
        "Headline must contain the real entity name 'Acme', not a "
        "prompt-fragment substring."
    )


def test_rf512_prefix_garbage_headline_no_longer_emitted(monkeypatch):
    """Direct symptom pin: regenerate the bogus headline shape live
    /chat returned ('*Sanctions screen — HIGH-STAKES and you are NOT
    100% confident, state what you would need to confirm — do NOT
    fabricate verifiable facts (jurisdictions, financials*'). After
    R-F514 the local short-circuit never runs on prefix-bearing input,
    so this exact garbage cannot be produced."""

    called = {"n": 0}

    async def _no_screen(name):
        called["n"] += 1
        # If the screen IS called, the regression has re-emerged.
        # Return the same empty-match payload _fmt_sanctions formats
        # so failing assertions point at the right line.
        return {"matches": [], "blocked": False, "variants_tried": [name]}

    monkeypatch.setattr(local_brain.fuzzy_sanctions, "fuzzy_screen", _no_screen)

    msg = _COMPREHENSION_PREFIX + "Is Hikvision sanctioned in the UK?"
    out = asyncio.run(local_brain.try_local_response(msg))

    if out.get("answered"):
        response = out.get("response", "")
        # If yield somehow failed, ensure at minimum the garbage
        # headline string is not in the response — but with R-F514
        # the function should never reach here.
        assert "HIGH-STAKES" not in response, (
            "REGRESSION: prefix-fragment leaked into sanctions headline."
        )
    assert called["n"] == 0, (
        "fuzzy_screen called on prefix-bearing input — yield-guard failed."
    )
