"""R-F2830 — an empty PSC register must be FRAMED, not left to imply evasion.

A company that discloses no beneficial owners looks opaque, potentially
evasive. A company exempt because it trades on a UK regulated market is
behaving entirely normally. Reporting the first when the truth is the second is
a false ACCUSATION — the mirror image of a false clean, and the same underlying
sin: asserting what the evidence does not show.

★ The load-bearing subtlety, taken from the live BAE payload: exemptions can be
HISTORICAL. BAE holds one ACTIVE exemption (`exempt_from` only) and one that
EXPIRED on 2023-02-02. An expired exemption must never excuse a presently-empty
register — that would launder opacity with a lapsed fact.

Live-verified 2026-07-21 against BAE Systems plc (01470151): active 1,
expired 1, and the explanation names the active exemption and its date.
"""
from __future__ import annotations

import pytest

from aria_service.intel import companies_house as ch

_BAE_EXEMPTIONS = {
    "exemptions": {
        "psc_exempt_as_trading_on_uk_regulated_market": {
            "items": [{"exempt_from": "2018-09-30"}],
            "exemption_type": "psc-exempt-as-trading-on-uk-regulated-market",
        },
        "disclosure_transparency_rules_chapter_five_applies": {
            "items": [{"exempt_from": "2016-09-30", "exempt_to": "2023-02-02"}],
            "exemption_type": "disclosure-transparency-rules-chapter-five-applies",
        },
    }
}


def _stub(monkeypatch, payload):
    async def _fake_get(path: str, _attempt: int = 0):
        return payload
    monkeypatch.setattr(ch, "_get", _fake_get)


async def test_active_and_expired_are_separated(monkeypatch):
    """The real BAE shape: one active, one lapsed."""
    _stub(monkeypatch, _BAE_EXEMPTIONS)
    ex = await ch.get_psc_exemptions("01470151")

    assert ex["checked"] is True
    assert ex["has_active_exemption"] is True
    assert len(ex["active"]) == 1
    assert len(ex["expired"]) == 1
    assert ex["active"][0]["exemption_type"] == "psc-exempt-as-trading-on-uk-regulated-market"
    assert ex["expired"][0]["exempt_to"] == "2023-02-02"


async def test_expired_only_is_not_an_active_exemption(monkeypatch):
    """★ A lapsed exemption must NOT excuse an empty register."""
    _stub(monkeypatch, {"exemptions": {"x": {
        "items": [{"exempt_from": "2016-01-01", "exempt_to": "2020-01-01"}],
        "exemption_type": "some-expired-exemption"}}})
    ex = await ch.get_psc_exemptions("00000000")

    assert ex["has_active_exemption"] is False
    msg = ch.explain_empty_psc(0, ex)
    assert "EXPIRED" in msg
    assert "unexplained" in msg


async def test_unreachable_source_is_not_no_exemption(monkeypatch):
    """R-F2511: could-not-look must never read as looked-and-found-nothing."""
    _stub(monkeypatch, None)
    ex = await ch.get_psc_exemptions("01470151")
    assert ex["has_active_exemption"] is False
    assert ex["active"] == []

    msg = ch.explain_empty_psc(0, ex, unavailable="rate_limited")
    assert "UNKNOWN" in msg
    assert "not confirmed absent" in msg


async def test_active_exemption_explanation_exonerates_explicitly(monkeypatch):
    _stub(monkeypatch, _BAE_EXEMPTIONS)
    ex = await ch.get_psc_exemptions("01470151")
    msg = ch.explain_empty_psc(0, ex)

    assert "ACTIVE exemption" in msg
    assert "psc-exempt-as-trading-on-uk-regulated-market" in msg
    assert "2018-09-30" in msg
    assert "NOT an indication of concealment" in msg


async def test_empty_with_no_exemption_states_fact_without_accusing(monkeypatch):
    """No exemption is not evidence of wrongdoing — and not evidence of no owners."""
    _stub(monkeypatch, {"exemptions": {}})
    ex = await ch.get_psc_exemptions("00000000")
    msg = ch.explain_empty_psc(0, ex)

    assert "NOT evidence that the company has no beneficial owners" in msg
    assert "UNVERIFIED" in msg
    for accusing in ("evasive", "concealing", "hiding", "suspicious"):
        assert accusing not in msg.lower(), f"must not imply wrongdoing: {accusing!r}"


async def test_non_empty_register_needs_no_explanation(monkeypatch):
    _stub(monkeypatch, _BAE_EXEMPTIONS)
    ex = await ch.get_psc_exemptions("01470151")
    assert ch.explain_empty_psc(3, ex) == ""


async def test_unchecked_exemptions_is_unknown_not_absent():
    msg = ch.explain_empty_psc(0, {"checked": False})
    assert "UNKNOWN" in msg and "not confirmed absent" in msg


async def test_malformed_payload_degrades_safely(monkeypatch):
    for payload in ({"exemptions": None}, {"exemptions": "nonsense"},
                    {"exemptions": {"x": "bad"}}, {}):
        _stub(monkeypatch, payload)
        ex = await ch.get_psc_exemptions("00000000")
        assert ex["has_active_exemption"] is False
        assert ex["checked"] is True


def test_exemption_active_window_logic():
    f = ch._exemption_is_active
    today = "2026-07-21"
    assert f({"exempt_from": "2018-09-30"}, today) is True            # open-ended
    assert f({"exempt_from": "2016-01-01", "exempt_to": "2030-01-01"}, today) is True
    assert f({"exempt_from": "2016-01-01", "exempt_to": "2023-02-02"}, today) is False
    assert f({"exempt_from": "2030-01-01"}, today) is False           # not yet in force
    assert f({}, today) is False                                     # undated = not assumed
    assert f({"exempt_to": "2030-01-01"}, today) is False             # no start date
