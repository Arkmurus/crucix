"""R-F1636 — registry-gap enrichment (rich DD data for no-registry targets).

When a registry lookup is absent/thin/junk (e.g. the TR/MERSIS homepage-scrape
returns empty fields + a boilerplate 'address'), the DD must NOT surface junk —
it enriches identity from FREE OSINT (Wayback domain vintage) + the intel VAULT,
and writes the enrichment back to the vault so the next DD recalls it at $0
(§15). Drives the REAL helpers.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator as DD
from aria_service.intel.dd_schema import ARKDDReport, Finding


def test_registry_thin_detection():
    assert DD._registry_result_is_thin(None) is True
    assert DD._registry_result_is_thin({"profile": {"company_number": ""}, "officers": []}) is True
    # real company number → not thin
    assert DD._registry_result_is_thin({"profile": {"company_number": "12345678"}, "officers": []}) is False
    # directors present → not thin
    assert DD._registry_result_is_thin({"profile": {}, "officers": [{"name": "Jane"}]}) is False


def test_extract_domain():
    assert DD._extract_domain({"website": "https://www.gozensecurity.com/about"}, "Gozen") == "gozensecurity.com"
    assert DD._extract_domain({}, "gozensecurity.com") == "gozensecurity.com"
    assert DD._extract_domain({}, "Gözen Security Services") == ""  # a name, not a domain


@pytest.mark.asyncio
async def test_wayback_graceful_empty_domain():
    # No domain → None, no network, never raises.
    assert await DD._wayback_earliest("") is None


@pytest.mark.asyncio
async def test_enrich_clears_junk_address_adds_vintage_and_honest_gap(monkeypatch):
    rep = ARKDDReport(target={"name": "Gozen"}, orchestrator_mode="deep")
    rep.identity.entity_name = "Gozen Security"
    # The MERSIS homepage-scrape junk that R-F1636 must discard:
    rep.identity.registered_address = (
        "Veri Tabanı (UAVT), Tapu ve Kadastro Bilgi Sistemi (TAKBİS) ve "
        "Merkezi Sicil Kayıt Sistemi (MERSİS)) birisidir."
    )

    async def _fake_wb(domain):
        return {"first_seen": "2014-03-12", "snapshot_url": "https://web.archive.org/web/2014/x"}

    monkeypatch.setattr(DD, "_wayback_earliest", _fake_wb)

    await DD._enrich_registry_gap({"website": "gozensecurity.com", "name": "Gozen"}, rep, "Gozen Security")

    # 1. junk boilerplate 'address' discarded (the A-guard, folded in)
    assert rep.identity.registered_address == "", "boilerplate homepage-scrape text must be discarded"
    # 2. Wayback vintage added as a real Finding object (not a raw string)
    vintage = [f for f in rep.identity.findings if isinstance(f, Finding) and "2014-03-12" in (f.title + f.detail)]
    assert vintage, "Wayback vintage must be added as a Finding"
    # 3. honest 'not registry-verified' gap present
    assert any("registry-verified" in g.lower() or "registry unavailable" in g.lower()
               for g in rep.identity.data_gaps), "must honestly flag enrichment is not registry-verified"


@pytest.mark.asyncio
async def test_enrich_flags_new_domain_when_no_wayback(monkeypatch):
    rep = ARKDDReport(target={"name": "NewCo"}, orchestrator_mode="deep")
    rep.identity.entity_name = "NewCo"

    async def _no_wb(domain):
        return None  # no archive → possibly a brand-new domain (risk signal)

    monkeypatch.setattr(DD, "_wayback_earliest", _no_wb)
    await DD._enrich_registry_gap({"website": "newco.example", "name": "NewCo"}, rep, "NewCo")
    assert any("new domain" in g.lower() or "no wayback" in g.lower() for g in rep.identity.data_gaps), \
        "absence of a Wayback archive must be flagged as an age-risk signal"
