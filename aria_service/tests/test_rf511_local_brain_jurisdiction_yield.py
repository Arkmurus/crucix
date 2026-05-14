"""R-F511 — local_brain yields jurisdiction-scoped sanctions to the LLM.

Live failure 2026-05-14 08:55 BST: WhatsApp question "Is Hikvision
sanctioned in the UK?" short-circuited at local_brain.try_local_response
→ fuzzy_screen returned multi-regime matches → _fmt_sanctions led with
*BLOCKING MATCH* citing us_sam_exclusions (US-only) plus ann_graph_topics
and ext_gb_coh_psc (Companies House presence, NOT a sanctions signal).

R-F510 (constitution clause 26) was added to ARIA_SYSTEM_PROMPT to force
per-regime authoritative-source reasoning, but the LLM was bypassed —
local_brain short-circuited before clause 26 could fire. R-F510 was
dead code for this question shape.

R-F511 yields to the LLM whenever the user names a jurisdiction
(UK/US/EU/OFSI/OFAC/etc.) so clause 26 actually runs. Unqualified
"Is X sanctioned?" still uses the local short-circuit (fast + cheap).

Capability bar: post the original Hikvision question to local_brain.
Result MUST have answered=False (LLM takes over). Repeat for OFAC, EU,
SECO, DFAT phrasings. Unqualified case MUST still answer locally.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import local_brain


# ───────────────── R-F511 — jurisdiction yield (positive cases) ─────────────


@pytest.mark.parametrize(
    "msg",
    [
        # The exact 08:55 BST live failure phrasing
        "Is Hikvision sanctioned in the UK?",
        # Same regression with each major jurisdiction
        "Is Hikvision sanctioned in the US?",
        "Is Hikvision sanctioned in the EU?",
        "Is Acme Ltd sanctioned in Switzerland?",
        # OFSI named explicitly
        "Is Hikvision on the OFSI list?",
        # OFAC named explicitly — including the SDN keyword
        "Is Acme on OFAC's SDN list?",
        # Pattern-2 phrasings with jurisdiction
        "Screen Hikvision sanctioned in the UK",
        "Check if Acme is on the UK sanctions list",
        # Pattern-3 phrasing with jurisdiction
        "Sanctions check on Hikvision for the UK",
        "Sanctions status for Acme in the EU",
        # Adjacent-regime keywords from clause 26 forbidden patterns
        "Is Hikvision on the Entity List?",
    ],
)
def test_rf511_yields_when_jurisdiction_named(msg):
    """Every jurisdiction-scoped sanctions question must defer to the LLM
    so R-F510 clause 26 can produce a per-regime answer."""
    out = asyncio.run(local_brain.try_local_response(msg))
    assert out.get("answered") is False, (
        f"local_brain SHOULD yield to LLM for jurisdiction-scoped query "
        f"{msg!r} but returned answered=True (response would be a flat "
        f"multi-regime BLOCKING MATCH). R-F510 clause 26 cannot fire "
        f"when local_brain short-circuits."
    )


# ───────────── R-F511 — unqualified short-circuit preserved ─────────────────


def test_rf511_unqualified_still_short_circuits(monkeypatch):
    """When no jurisdiction is named, local_brain MUST still answer.
    fuzzy_screen is mocked so the test is hermetic (no live OpenSanctions
    network call)."""

    async def _fake_screen(name):
        return {
            "matches": [],
            "blocked": False,
            "variants_tried": [name],
            "top_score": 0,
        }

    monkeypatch.setattr(local_brain.fuzzy_sanctions, "fuzzy_screen", _fake_screen)

    out = asyncio.run(local_brain.try_local_response("Is Hikvision sanctioned?"))
    assert out.get("answered") is True, (
        "Unqualified sanctions questions should still short-circuit at "
        "local_brain — no jurisdiction means no clause-26 leverage to "
        "exploit, and the local fuzzy_screen answer is acceptable."
    )
    assert out.get("intent") == "sanctions"
    assert out.get("source") == "local_brain"
    # Headline shape must contain the entity name, not a prompt fragment.
    assert "Hikvision" in out["response"]


def test_rf511_pronoun_capture_still_skipped():
    """Existing _INVALID_ENTITY_CAPTURES guard must remain intact:
    "Is the company sanctioned?" should fall through to the next pattern,
    not produce a bogus 'screen the company' result."""
    out = asyncio.run(local_brain.try_local_response("Is the company sanctioned?"))
    # The pronoun "the company" hits _INVALID_ENTITY_CAPTURES and is
    # skipped. With no other matching pattern, the loop falls through
    # to the final not-answered return — same as before R-F511.
    assert out.get("answered") is False


# ───────────────── R-F511 — exact-symptom regression pin ────────────────────


def test_rf511_hikvision_uk_does_not_emit_blocking_match_locally(monkeypatch):
    """The specific 08:55 BST symptom: even if fuzzy_screen WOULD return
    multi-regime BLOCKING MATCH data for 'Hikvision', the local headline
    must not be rendered when the user named a jurisdiction. The R-F511
    yield is what prevents _fmt_sanctions from ever being called.

    fuzzy_screen is mocked to RETURN the same dirty multi-regime payload
    that fired live — if local_brain still calls it, the test fails."""
    called = {"n": 0}

    async def _poisoned_screen(name):
        called["n"] += 1
        return {
            "matches": [
                {"name": "Hikvision (China)", "list": "ext_gb_coh_psc", "score": 1.0, "countries": ["cn"]},
                {"name": "HIKVISION", "list": "us_sam_exclusions", "score": 1.0, "countries": ["cn"]},
                {"name": "HIKVISION UK LIMITED", "list": "ann_graph_topics", "score": 0.769, "countries": ["gb"]},
            ],
            "blocked": True,
            "top_score": 1.0,
            "variants_tried": ["Hikvision"],
        }

    monkeypatch.setattr(local_brain.fuzzy_sanctions, "fuzzy_screen", _poisoned_screen)

    out = asyncio.run(local_brain.try_local_response("Is Hikvision sanctioned in the UK?"))
    assert out.get("answered") is False, (
        "Live 2026-05-14 08:55 BST regression: must NOT short-circuit "
        "with a multi-regime BLOCKING MATCH when user named UK."
    )
    assert called["n"] == 0, (
        "fuzzy_screen must never be invoked once R-F511 detects a "
        "jurisdiction qualifier — yielding to the LLM happens BEFORE "
        "the screen call."
    )
