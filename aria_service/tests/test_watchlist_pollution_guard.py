"""Regression guard for F57 2026-04-28 — watchlist pollution.

Prod log evidence: daily watchlist re-screen at 09:13:53–09:14:03 logged
25+ `screen_with_aliases: rejecting non-entity input '...'` messages
because the watchlist contained search-query strings that
`_auto_escalate_to_watchlist` had blindly added from `task.tool_chain[0]
.entity` fields. Examples:
  * 'SAM.gov defence military security procurement global last 7 days 2026'
  * 'East Africa Kenya Ethiopia Somalia Uganda defence security ... 2026'
  * 'Without Direct SAM.gov Access' (sentence fragment)

Two-layer fix:
  1. Pre-add filter — _auto_escalate_to_watchlist now runs every candidate
     name through sanctions._looks_like_entity_name before adding.
  2. Opportunistic purge — rescreen_watchlist drops polluted entries on
     each cycle so historical pollution self-cleans.
"""
from __future__ import annotations

from aria_service.intel.sanctions import _looks_like_entity_name


def test_real_pollution_examples_are_rejected():
    """Every string the production guard logged on 04-28 must be classified
    as non-entity-shaped. If any of these slip through, the watchlist will
    re-pollute on the next autonomous run."""
    polluted = [
        "SAM.gov defence military security procurement global last 7 days 2026",
        "East Africa Kenya Ethiopia Somalia Uganda defence security procurement 2026",
        "UK ECJU export control licence SIEL OIEL SITCL update 2026",
        "Iran nexus before engagement",
        "Without Direct SAM.gov Access",
        "Iran is openly signalling it will",
        "sanctions update OFAC SDN EU UN new designation defence global 2026",
        "Turkey defence industry export Baykar ASELSAN ROKETSAN SSB global 2026",
        "Mozambique cooperation",
        "Crisis Group Africa latest reports briefings conflict analysis",
    ]
    leaked = [s for s in polluted if _looks_like_entity_name(s)]
    assert leaked == [], (
        f"these polluted strings would still pass the validator and pollute "
        f"the watchlist: {leaked}"
    )


def test_real_entity_names_still_pass():
    """Conversely, the validator must not over-reject. Real counterparty
    names from past DD runs should still go through."""
    real_entities = [
        "Serban Industries SRL",
        "Hanwha Aerospace",
        "Bank of America Corp",
        "AKFAL Polska",
        "Limestone Trading FZE",
        "F-35 Lightning II",   # model designators
        "T-90",
        "Krasnoyarsk Aluminum Smelter",
    ]
    blocked = [s for s in real_entities if not _looks_like_entity_name(s)]
    assert blocked == [], (
        f"validator over-rejected real entity names: {blocked}"
    )
