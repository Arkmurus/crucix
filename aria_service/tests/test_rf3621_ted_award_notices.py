"""R-F3621 — a TED contract AWARD must reach the channel as `contract_award`.

THE DEFECT
----------
`_crawl_ted` has always asked TED for `notice-type` and never read it, so a contract
award notice (`can-standard` — who WON) became an indistinguishable `active_tender`.
R-F3536 then removed `active_tender` from the Telegram channel while explicitly
keeping `contract_award` ("an award is who WON, which is market intelligence, unlike
an open tender") — so it banned the very signal it meant to keep, and the channel lost
its supply.

Measured on the live TED API 2026-08-01, defence CPV, 14-day window:
  50 sampled notices -> 27 cn-standard (open tender), 21 can-standard (AWARD),
  1 can-modif, 1 veat;  340 award notices available in total;
  6/6 sampled awards carried a winner name and a value.

Meanwhile `contract_award` existed in the live signal store only as 2
classifier_template news items, which R-F2899 refuses. So the type was declared
publishable by R-F3536 and was structurally unproducible — recorded as gap
d5a03b78-1033-4670-83c2-c5279c6ec77e (R-F3611); this closes it with a producer
rather than by weakening a gate.
"""

from __future__ import annotations

import pytest

from aria_service.intel import golden_intel_bridge as gib
from aria_service.intel import tender_monitor as tm


# ── The TED payload shapes, taken from real 2026-08-01 responses ──────────────

def _award_alert(**over):
    """A TenderAlert as _crawl_ted now builds one for `can-standard`."""
    base = dict(
        id="tender_award1",
        portal="TED",
        title="Slovakia - Computers and accessories - Zariadenia informacnych technologii",
        description="Supply of IT equipment.",
        buyer="Hotelova akademia Ludovita Wintera",
        country="Slovakia",
        country_iso2="SK",
        value_estimate="undisclosed",
        cpv_codes=["35000000"],
        deadline="",
        publication_date="2026-07-20",
        url="https://ted.europa.eu/en/notice/-/detail/123456-2026",
        relevance_score=0.72,
        matched_products=["IT equipment"],
        notice_type="can-standard",
        award_winners=["Datacomp s.r.o."],
        award_value="392,516",
        award_date="2026-07-01",
    )
    base.update(over)
    return tm.TenderAlert(**base)


def _tender_alert(**over):
    """A TenderAlert for an OPEN tender (`cn-standard`) — must stay active_tender."""
    base = dict(
        id="tender_open1",
        portal="TED",
        title="Croatia - Surveillance and security systems and devices",
        description="Open call for surveillance systems.",
        buyer="LNG Hrvatska d.o.o.",
        country="Croatia",
        country_iso2="HR",
        value_estimate="undisclosed",
        cpv_codes=["35000000"],
        deadline="2026-09-01T12:00:00",
        publication_date="2026-07-20",
        url="https://ted.europa.eu/en/notice/-/detail/222222-2026",
        relevance_score=0.72,
        matched_products=["surveillance"],
        notice_type="cn-standard",
    )
    base.update(over)
    return tm.TenderAlert(**base)


async def _run_adapter(monkeypatch, alerts):
    async def _fake_get_new_tenders(since_hours=48):
        return alerts
    monkeypatch.setattr(tm, "get_new_tenders", _fake_get_new_tenders)
    return await gib._tender_adapter()


# ── The classification, which is the whole defect ────────────────────────────

@pytest.mark.asyncio
async def test_award_notice_becomes_contract_award(monkeypatch):
    findings = await _run_adapter(monkeypatch, [_award_alert()])
    assert len(findings) == 1
    f = findings[0]
    assert f["signal_type"] == "contract_award", (
        "an award labelled active_tender is banned by R-F3536 and never reaches the channel"
    )
    assert f["category"] == "contract_award"


@pytest.mark.asyncio
async def test_open_tender_is_still_active_tender(monkeypatch):
    findings = await _run_adapter(monkeypatch, [_tender_alert()])
    assert findings[0]["signal_type"] == "active_tender", (
        "R-F3536's tender ban is a deliberate operator decision and must not be undone"
    )


@pytest.mark.asyncio
async def test_the_award_names_the_winner_not_the_buyer(monkeypatch):
    findings = await _run_adapter(monkeypatch, [_award_alert()])
    f = findings[0]
    # The subject of an award is who WON. This lands in the channel post's "Target"
    # line and in the dedup key, so getting it wrong misattributes the whole post.
    assert f["target"] == "Datacomp s.r.o."
    assert "Datacomp s.r.o." in f["why_it_matters"]
    assert "Hotelova akademia" in f["why_it_matters"], "the buyer is still stated"
    assert f["entities"]["oems"] == ["Datacomp s.r.o."]


@pytest.mark.asyncio
async def test_multiple_winners_are_not_reported_as_one(monkeypatch):
    alert = _award_alert(award_winners=["DANCO PRO", "TAROM", "EXPERT MULTISERVICES", "X4"])
    f = (await _run_adapter(monkeypatch, [alert]))[0]
    why = f["why_it_matters"]
    assert "4 suppliers" in why, "a framework with several winners must not read as one winner"
    assert "won this contract" not in why


# ── Honesty of the published value ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_value_never_asserts_a_currency(monkeypatch):
    # TED exposes total-value with NO currency field (probed 2026-08-01: every
    # candidate currency field name is rejected). Inferring EUR from "it is a
    # European notice" would be an invented unit on a customer-facing number.
    f = (await _run_adapter(monkeypatch, [_award_alert()]))[0]
    why = f["why_it_matters"]
    assert "392,516" in why
    assert "currency as published" in why
    for unit in ("EUR", "€", "USD", "$", "GBP", "£"):
        assert unit not in why, f"the value must not claim a currency ({unit})"


def test_implausible_value_is_dropped_not_published():
    # The live sample carried `total-value: 4` on a boat-supply framework — a lot
    # count or a malformed field, not a contract value. A wrong number is worse
    # than no number.
    assert tm._ted_award_value(4) == ""
    assert tm._ted_award_value(392515.62) == "392,516"
    assert tm._ted_award_value(None) == ""
    assert tm._ted_award_value("not a number") == ""


@pytest.mark.asyncio
async def test_award_without_a_value_still_publishes(monkeypatch):
    f = (await _run_adapter(monkeypatch, [_award_alert(award_value="")]))[0]
    assert f["signal_type"] == "contract_award"
    assert "currency" not in f["why_it_matters"]
    assert "Datacomp s.r.o." in f["why_it_matters"]


# ── The two gates that decide whether it can actually be published ───────────

@pytest.mark.asyncio
async def test_the_award_earns_source_adapter_provenance(monkeypatch):
    # R-F2899/R-F2930: the channel refuses anything whose why/action is a classifier
    # template. This must be EARNED from the content, not asserted — the winner name
    # and the decision date come from the notice itself.
    f = (await _run_adapter(monkeypatch, [_award_alert()]))[0]
    promoted = gib._normalize_finding_to_signal(f, source_name="tender_monitor")
    assert promoted["why_action_provenance"] == "source_adapter"


@pytest.mark.asyncio
async def test_the_award_grades_A_and_is_channel_publishable(monkeypatch):
    # TED is tier_1a; an award carries a named entity and a real evidence URL, so it
    # should reach Grade A. If this drops to B the channel only gets it on the 17:00
    # slot, labelled corroboration-pending.
    from aria_service.intel import news_monitor as nm

    f = (await _run_adapter(monkeypatch, [_award_alert()]))[0]
    s = gib._normalize_finding_to_signal(f, source_name="tender_monitor")
    # _compute_intel_grade is THE authority the channel selector gates on (R-F2714);
    # assert against it rather than a field some later layer happens to stamp.
    grade, reason = nm._compute_intel_grade(
        source_tier=s.get("source_tier"),
        signal_type=s.get("signal_type"),
        priority=s.get("priority"),
        evidence_count=s.get("evidence_count") or 1,
        url=s.get("url") or s.get("evidence_url"),
        entities=s.get("entities") or {},
    )
    assert grade == "A", reason
    assert s["signal_type"] in gib._GOLDEN_ALLOWED_TYPES


@pytest.mark.asyncio
async def test_contract_award_is_in_the_channel_allowlist():
    # The allowlist declared this type publishable while nothing produced it
    # (R-F3611). Pin both halves together so they cannot drift apart again.
    assert "contract_award" in gib._GOLDEN_ALLOWED_TYPES


# ── The crawler-level parse, where notice-type was being discarded ───────────

def test_award_notice_types_exclude_veat():
    # `veat` announces an INTENT to award directly without competition. Treating it
    # as an award would report a contract that may never be signed.
    assert "can-standard" in tm._TED_AWARD_NOTICE_TYPES
    assert "can-modif" in tm._TED_AWARD_NOTICE_TYPES
    assert "veat" not in tm._TED_AWARD_NOTICE_TYPES
    assert "cn-standard" not in tm._TED_AWARD_NOTICE_TYPES


def test_winner_list_dedupes_repeated_lot_winners():
    # winner-name repeats the same supplier once per awarded lot.
    assert tm._ted_i18n_pick_list({"slk": ["Datacomp s.r.o.", "Datacomp s.r.o."]}) == ["Datacomp s.r.o."]
    assert tm._ted_i18n_pick_list({}) == []
    assert tm._ted_i18n_pick_list({"ron": ["A", "B"]}) == ["A", "B"]


def test_tenderalert_award_fields_default_empty():
    # Every other portal crawler constructs TenderAlert without these; they must not
    # become required arguments.
    t = tm.TenderAlert(
        id="", portal="SAM_GOV", title="t", description="d", buyer="b", country="c",
        country_iso2="US", value_estimate="", cpv_codes=[], deadline="",
        publication_date="", url="https://example.gov/x",
    )
    assert t.notice_type == ""
    assert t.award_winners == []
    assert "award_winners" in t.to_dict()
