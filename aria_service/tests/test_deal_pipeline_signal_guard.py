"""Regression test for F85 — auto_create_from_signal must reject vague leads.

Live log 2026-04-29 08:16:13 showed:
  [pipeline] Lead created: LEAD-8A389CAF — Saudi Arabia /  ($0)

DAILY-PROC-SAM ran with SAM_GOV_API_KEY missing → no real notice data
→ LLM produced a generic "Saudi Arabia is interesting for defence"
answer → opportunity_detector found country + procurement keyword
and created a placeholder lead with no buyer and no value. That's
pipeline pollution — operator gets non-actionable junk in the CRM.

Fix: a real lead needs at least ONE concrete anchor — either a buyer
entity OR a $ value figure. Country alone is the noise floor.

This test pins that contract:
  - country only          → SKIP (return None)
  - country + buyer       → CREATE
  - country + value       → CREATE
  - country + both        → CREATE
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock


def _mk_response(country_phrase: str, buyer_phrase: str = "", value_phrase: str = "") -> str:
    """Build a fake LLM response that exercises the country-extractor +
    procurement-keyword filter. The response is deliberately long so
    `len(response_text) >= 100` passes."""
    parts = [
        f"{country_phrase} is showing increased interest in defence procurement.",
        f"Recent signals indicate emerging tender activity in the region.",
    ]
    if buyer_phrase:
        parts.append(f"Buyer: {buyer_phrase}.")
    if value_phrase:
        parts.append(f"Estimated value: {value_phrase}.")
    parts.append("Multiple sources confirm sustained acquisition momentum.")
    return " ".join(parts)


def _run(country_phrase: str, buyer_phrase: str = "", value_phrase: str = ""):
    """Drive auto_create_from_signal with a synthetic response."""
    from aria_service.intel import deal_pipeline

    response = _mk_response(country_phrase, buyer_phrase, value_phrase)

    # Stub create_lead so we can assert call/no-call without writing to disk.
    fake_create = AsyncMock(return_value=object())  # truthy sentinel

    with patch.object(deal_pipeline, "create_lead", fake_create):
        result = asyncio.run(deal_pipeline.auto_create_from_signal(
            task_id="DAILY-PROC-SAM",
            task_name="Daily SAM.gov scan",
            response_text=response,
            triggered_flags=["procurement", "defence"],
        ))
    return result, fake_create


def test_country_only_response_does_not_create_lead():
    """The Saudi Arabia / ($0) regression — must return None."""
    result, fake_create = _run("Saudi Arabia")
    assert result is None, (
        "F85 regression: lead created from a country-only response "
        "(no buyer, no value). This is the exact pipeline-pollution "
        "case from the 2026-04-29 08:16:13 production log."
    )
    fake_create.assert_not_called()


def test_country_plus_buyer_creates_lead():
    """Buyer present + $0 value is a valid signal (value TBD).
    Real example: LEAD-0F14129B — Turkey / MoD ($0)."""
    result, fake_create = _run("Turkey", buyer_phrase="MoD")
    assert result is not None
    fake_create.assert_called_once()
    kwargs = fake_create.call_args.kwargs
    assert kwargs["country"].lower() == "turkey"
    assert "mod" in kwargs["buyer"].lower()


def test_country_plus_value_creates_lead():
    """Value present + no buyer is also a valid signal — there's a
    concrete contract figure even if the entity is unnamed."""
    result, fake_create = _run("Pakistan", value_phrase="US$500 million")
    assert result is not None
    fake_create.assert_called_once()
    kwargs = fake_create.call_args.kwargs
    assert kwargs["country"].lower() == "pakistan"
    assert kwargs["estimated_value_usd"] > 0


def test_country_plus_both_creates_lead():
    """Strongest signal — both buyer and value present."""
    result, fake_create = _run(
        "Pakistan", buyer_phrase="MoD", value_phrase="US$500 million"
    )
    assert result is not None
    fake_create.assert_called_once()
    kwargs = fake_create.call_args.kwargs
    assert kwargs["country"].lower() == "pakistan"
    assert "mod" in kwargs["buyer"].lower()
    assert kwargs["estimated_value_usd"] > 0


def test_no_country_returns_none():
    """Pre-existing contract — no country detected = no lead. F85's
    fix doesn't change this behaviour, but pin it to prevent
    accidental loosening alongside the F85 tightening."""
    response = (
        "There is significant defence procurement happening this quarter. "
        "Multiple tenders are open and contracts are being awarded. "
        "Vendors are positioning for upcoming acquisition cycles."
    )
    from aria_service.intel import deal_pipeline
    fake_create = AsyncMock()
    with patch.object(deal_pipeline, "create_lead", fake_create):
        result = asyncio.run(deal_pipeline.auto_create_from_signal(
            task_id="TEST",
            task_name="test",
            response_text=response,
            triggered_flags=[],
        ))
    assert result is None
    fake_create.assert_not_called()
