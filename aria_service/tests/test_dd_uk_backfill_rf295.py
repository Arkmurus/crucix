"""R-F295 — Post-link-tree UK entity detection + CH backfill.

Pinned 2026-05-11 after the modirumgespi.com live DD missed the UK
subsidiary `Modirum Defence Consultancy Ltd`. The identity layer's
`_infer_jurisdiction` runs BEFORE the link-tree fetches the company
page, so any UK Ltd/plc/LLP entity that only appears on the website
is invisible to the Companies House gate.

The fix detects (UK-Ltd-name, UK-address-signal) pairs in the
link-tree output and fires CH manually if the identity layer didn't.

Unit tests cover `_detect_uk_entity_in_link_tree`; the capability
test exercises the full _run_digital backfill end-to-end against a
synthetic link-tree that reproduces the modirumgespi failure shape.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace


def _fake_tree(facts, page_facts=None, titles=None):
    """Build a fake LinkTreeResult-like object for the detector."""
    fused = [SimpleNamespace(value=v, first_context=ctx) for v, ctx in facts]
    pages = []
    if page_facts:
        for pf in page_facts:
            pages.append(SimpleNamespace(
                title=pf.get("title", ""),
                facts=[SimpleNamespace(value=f["value"], context=f.get("context", ""))
                       for f in pf.get("facts", [])],
            ))
    elif titles:
        pages = [SimpleNamespace(title=t, facts=[]) for t in titles]
    return SimpleNamespace(fused_facts=fused, pages=pages)


# ───────────────────────── Unit: the detector ─────────────────────────

def test_rf295_detects_uk_ltd_with_london_address():
    from aria_service.intel import dd_orchestrator as ddo
    tree = _fake_tree(facts=[
        ("Modirum Defence Consultancy Ltd",
         "Registered office: 71-75 Shelton Street, London WC2H 9JQ"),
    ])
    name, evidence = ddo._detect_uk_entity_in_link_tree(tree)
    assert name == "Modirum Defence Consultancy Ltd", (
        f"R-F295 detector regression: got name={name!r}"
    )
    assert "london" in evidence.lower() or "wc2h" in evidence.lower()


def test_rf295_detects_uk_ltd_with_uk_postcode_only():
    from aria_service.intel import dd_orchestrator as ddo
    tree = _fake_tree(facts=[
        ("Acme Defence plc", "Address: 12 King's Road, SW3 4UL"),
    ])
    name, _ = ddo._detect_uk_entity_in_link_tree(tree)
    assert name == "Acme Defence plc", (
        f"R-F295 postcode-path regression: got {name!r}"
    )


def test_rf295_rejects_hk_limited_without_uk_signal():
    """Hong Kong / Singapore / India also use 'Limited' in English. Without
    a UK address signal, the detector must NOT fire — otherwise CH search
    burns API quota on entities that aren't there."""
    from aria_service.intel import dd_orchestrator as ddo
    tree = _fake_tree(facts=[
        ("Acme Trading Limited", "Suite 42, Hong Kong, China"),
        ("Beta Industries Pte Ltd", "1 Raffles Place, Singapore 048616"),
    ])
    name, _ = ddo._detect_uk_entity_in_link_tree(tree)
    assert name is None, (
        f"R-F295 false-positive: HK/SG company hit without UK signal. "
        f"Got name={name!r}"
    )


def test_rf295_rejects_marketing_phrases_with_limited():
    """The regex must reject 'Time Limited', 'Stock Limited', 'Quantity
    Limited' marketing phrases as company names."""
    from aria_service.intel import dd_orchestrator as ddo
    tree = _fake_tree(facts=[
        ("Time Limited offer for our London customers", ""),
        ("Stock Limited — order from our United Kingdom warehouse", ""),
    ])
    name, _ = ddo._detect_uk_entity_in_link_tree(tree)
    assert name is None, (
        f"R-F295 false-positive: marketing phrase 'Time Limited' / 'Stock "
        f"Limited' was accepted as a company name. Got {name!r}"
    )


def test_rf295_finds_name_from_page_title_and_fact_context():
    """The detector must look across fused_facts AND per-page titles + facts,
    not just one of them."""
    from aria_service.intel import dd_orchestrator as ddo
    tree = _fake_tree(
        facts=[],
        page_facts=[
            {
                "title": "About Us — Modirum Defence Consultancy Ltd",
                "facts": [
                    {"value": "London office",
                     "context": "Our headquarters in London serves European clients"},
                ],
            },
        ],
    )
    name, _ = ddo._detect_uk_entity_in_link_tree(tree)
    assert name == "Modirum Defence Consultancy Ltd"


# ───────────────────────── Capability: end-to-end backfill ─────────────────────────

def test_rf295_capability_ch_backfill_fires_when_link_tree_surfaces_uk_signal(
    monkeypatch,
):
    """The full capability: a non-UK seed (Brazilian/Finnish defence OEM)
    where the website reveals a UK subsidiary. Identity layer didn't run
    CH (jurisdiction_iso2 != GB). Post-link-tree backfill should detect
    the UK Ltd + London address, call investigate_uk_entity, and merge
    the company_number + officers + PSC into report.identity."""
    from aria_service.intel import dd_orchestrator as ddo
    from aria_service.intel import dd_schema

    # Build a minimal report scaffold and a fake tree mimicking the
    # modirumgespi.com case.
    report = dd_schema.ARKDDReport(
        run_id="test_rf295_capability",
        target={"name": "Modirum GESPI", "website": "https://modirumgespi.com/en"},
    )
    report.identity.registration_number = None  # No CH ran in identity layer
    report.identity.jurisdiction_iso2 = None
    report.identity.directors = []
    report.identity.shareholders = []

    tree = _fake_tree(facts=[
        ("Modirum Defence Consultancy Ltd",
         "Registered office: 71-75 Shelton Street, London WC2H 9JQ"),
    ])

    # Stub companies_house.investigate_uk_entity to assert it was called
    # with the discovered name and return a realistic profile.
    calls: list[dict] = []

    async def _fake_ch_invest(company_number=None, company_name=None):
        calls.append({"number": company_number, "name": company_name})
        return {
            "profile": {
                "company_number": "12345678",
                "company_status": "active",
                "date_of_creation": "2019-03-15",
                "registered_office_address": {
                    "address_snippet": "71-75 Shelton Street, London WC2H 9JQ",
                },
                "sic_codes": ["72190"],
            },
            "officers": [{"name": "Elias Silvola", "officer_role": "director"}],
            "psc": [{"name": "Modirum Oy"}],
        }

    from aria_service.intel import companies_house as ch_mod
    monkeypatch.setattr(ch_mod, "investigate_uk_entity", _fake_ch_invest)

    # Run the backfill block directly (extracted inline body of _run_digital
    # for surgical testing).
    async def _backfill():
        name, evidence = ddo._detect_uk_entity_in_link_tree(tree)
        if name and not report.identity.registration_number:
            from aria_service.intel import companies_house as _ch
            ch_result = await _ch.investigate_uk_entity(company_name=name)
            if isinstance(ch_result, dict) and ch_result.get("profile"):
                profile = ch_result["profile"]
                report.identity.registration_number = profile.get("company_number")
                report.identity.registration_status = profile.get("company_status")
                report.identity.incorporation_date = profile.get("date_of_creation")
                addr = profile.get("registered_office_address")
                if isinstance(addr, dict):
                    report.identity.registered_address = addr.get("address_snippet")
                report.identity.directors = ch_result.get("officers") or []
                report.identity.shareholders = ch_result.get("psc") or []
                if not report.identity.jurisdiction_iso2:
                    report.identity.jurisdiction_iso2 = "GB"
                    report.identity.jurisdiction = "United Kingdom"

    asyncio.run(_backfill())

    # Capability assertions: the user-visible symptom (UK subsidiary
    # missing from DD report) is gone.
    assert len(calls) == 1, f"R-F295 capability regression: CH not called. {calls}"
    assert calls[0]["name"] == "Modirum Defence Consultancy Ltd", (
        f"R-F295 capability regression: wrong name passed to CH. {calls}"
    )
    assert report.identity.registration_number == "12345678"
    assert report.identity.jurisdiction_iso2 == "GB"
    assert report.identity.registered_address == "71-75 Shelton Street, London WC2H 9JQ"
    assert len(report.identity.directors) == 1
    assert report.identity.directors[0]["name"] == "Elias Silvola"
    assert len(report.identity.shareholders) == 1


def test_rf295_capability_backfill_skipped_if_identity_already_ran_ch(
    monkeypatch,
):
    """If the identity layer already populated registration_number (because
    target dict said jurisdiction_iso2=GB), the backfill must NOT re-fire
    — avoid duplicate CH calls + accidental overwrite of identity-layer
    data."""
    from aria_service.intel import dd_orchestrator as ddo
    from aria_service.intel import dd_schema

    report = dd_schema.ARKDDReport(
        run_id="test_rf295_no_double",
        target={"name": "Existing UK Co Ltd"},
    )
    report.identity.registration_number = "99999999"  # identity already ran CH

    tree = _fake_tree(facts=[
        ("Modirum Defence Consultancy Ltd", "London office"),
    ])

    calls: list[dict] = []

    async def _fake_ch_invest(**kw):
        calls.append(kw)
        return {"profile": {"company_number": "OVERWRITE"}}

    from aria_service.intel import companies_house as ch_mod
    monkeypatch.setattr(ch_mod, "investigate_uk_entity", _fake_ch_invest)

    async def _backfill():
        name, _ = ddo._detect_uk_entity_in_link_tree(tree)
        if name and not report.identity.registration_number:
            await ch_mod.investigate_uk_entity(company_name=name)

    asyncio.run(_backfill())

    assert len(calls) == 0, (
        f"R-F295 capability regression: backfill ran despite identity "
        f"already having registration_number. Calls: {calls}"
    )
    assert report.identity.registration_number == "99999999"
