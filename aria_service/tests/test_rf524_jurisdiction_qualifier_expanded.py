"""R-F524 — expand R-F511 _JURISDICTION_QUALIFIER with embargoed countries.

Live WhatsApp 2026-05-14 13:55-13:57 BST:

  [13:55] User: "Aria, is Yemen under sanctions, the GOV of Yemen?"
  [13:55] ARIA: ✅ Sanctions screen — Yemen under
                No matches in OpenSanctions...
  → Wrong entity ("Yemen under" instead of "Yemen")

  [13:56] User: "Aria, which Government party is under sanctions in Yemen,
                  if any?"
  [13:56] ARIA: ⚠️ Sanctions screen — under
                • Under Secretary (M) — us_state_dept (score 0.224)
                • UNDER TECHNOLOGIES INC. — iso9362_bic (score 0.184)
                ...
  → Entity captured was just "under" — fuzzy_screen matched random
    "Under XXX" entries

Both queries explicitly named Yemen but R-F511's _JURISDICTION_QUALIFIER
didn't include it (only covered regulators + Western/G7). R-F524 adds the
20+ embargoed/sanctioned destination countries operators routinely ask
about, plus high-traffic dual-use trade countries.

The capability pin is the LIVE FAILURE: both queries must now yield
(answered=False) instead of short-circuiting on local_brain's broken
entity extraction.

Negative case preserved: unqualified "Is X sanctioned?" still answers
locally (the original R-F511 fast path).
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import local_brain


# ───────── R-F524 — live failure repros (must now yield) ─────────


@pytest.mark.parametrize(
    "msg",
    [
        # Exact 13:55 BST live failure
        "Is Yemen under sanctions, the GOV of Yemen?",
        # Exact 13:56 BST live failure
        "which Government party is under sanctions in Yemen, if any?",
    ],
)
def test_rf524_live_yemen_repros_yield(msg):
    """The two exact WhatsApp queries that surfaced this bug must now
    yield to the LLM. Pre-R-F524 they short-circuited with wrong-entity
    answers ('Yemen under' / 'under')."""
    out = asyncio.run(local_brain.try_local_response(msg))
    assert out.get("answered") is False, (
        f"R-F524 must yield on {msg!r} — pre-fix it short-circuited "
        f"local_brain with garbage entity extraction. Got answered=True "
        f"with response: {(out.get('response') or '')[:200]}"
    )


# ───────── R-F524 — new countries all yield ─────────


@pytest.mark.parametrize(
    "country",
    [
        "Yemen", "Syria", "Libya", "Sudan", "South Sudan",
        "Somalia", "Myanmar", "Burma", "Lebanon", "Iraq",
        "Afghanistan", "Eritrea", "DRC", "Cuba", "Venezuela",
        "Nicaragua", "Belarus", "Zimbabwe", "Mali", "Burkina Faso",
        "Niger", "Central African Republic",
        # Adjectival forms
        "Yemeni", "Syrian", "Libyan", "Cuban", "Venezuelan",
        "Burmese", "Lebanese", "Iraqi", "Sudanese",
    ],
)
def test_rf524_each_added_country_yields(country):
    """Each newly-added country term must trigger jurisdiction-yield
    when present in a sanctions question."""
    msg = f"Is Acme Corp sanctioned under {country} regime?"
    out = asyncio.run(local_brain.try_local_response(msg))
    assert out.get("answered") is False, (
        f"R-F524: '{country}' must trigger yield, got answered=True"
    )


# ───────── R-F524 — high-traffic dual-use countries ─────────


@pytest.mark.parametrize(
    "country",
    [
        "Saudi Arabia", "UAE", "Egypt", "Turkey", "Turkiye",
        "Pakistan", "India", "Brazil", "Angola", "Mozambique",
        "Nigeria", "Ethiopia", "Kenya", "Ukraine", "Hong Kong", "Taiwan",
        # Some adjectival forms
        "Saudi", "Egyptian", "Turkish", "Brazilian", "Angolan",
    ],
)
def test_rf524_high_traffic_country_yields(country):
    """Dual-use / defence-trade countries should yield to LLM too — the
    operator commonly asks per-regime sanctions questions about these."""
    msg = f"Is X sanctioned in {country}?"
    out = asyncio.run(local_brain.try_local_response(msg))
    assert out.get("answered") is False


# ───────── R-F524 — unqualified path still short-circuits ─────────


def test_rf524_unqualified_question_still_local(monkeypatch):
    """The original R-F511 fast path must remain — when NO country is
    named, local_brain answers without an LLM call."""

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
        "Unqualified sanctions question should still take the local "
        "fast path — R-F524 only widens the YIELD trigger."
    )
    assert out.get("source") == "local_brain"


# ───────── R-F524 — pre-existing R-F511 cases unaffected ─────────


def test_rf524_preserves_uk_yield():
    """The original R-F511 live failure (UK Hikvision question) must
    still yield — R-F524 is purely additive."""
    out = asyncio.run(local_brain.try_local_response(
        "Is Hikvision sanctioned in the UK?"
    ))
    assert out.get("answered") is False


def test_rf524_preserves_ofac_yield():
    """OFAC qualifier still yields."""
    out = asyncio.run(local_brain.try_local_response(
        "Is Acme on OFAC's SDN list?"
    ))
    assert out.get("answered") is False
