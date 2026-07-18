"""R-F2726 — DD Grade-A: anchored `controlled_by` from corporate-PSC identification.

get_psc previously DROPPED the `identification` object, so the one field that turns
"controlled by X" into a VERIFIED edge — a corporate PSC's own registry number —
was thrown away, leaving only name-match "relationships" (fabrication; R-F2703
correctly refused to publish those). Now a corporate PSC identified by its registry
number becomes an anchored, Grade-A `controlled_by` edge; an individual PSC never
fabricates a corporate control edge.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.companies_house as ch


_PSC_PAYLOAD = {
    "items": [
        {  # corporate PSC WITH a registry number → anchored Grade-A edge
            "name": "PARENT HOLDINGS LTD",
            "kind": "corporate-entity-person-with-significant-control",
            "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
            "identification": {"registration_number": "09999999", "country_registered": "England",
                               "legal_form": "Private Limited Company", "place_registered": "Companies House"},
        },
        {  # individual PSC → real ownership fact, but NOT a corporate control edge
            "name": "Jane Individual",
            "kind": "individual-person-with-significant-control",
            "natures_of_control": ["voting-rights-25-to-50-percent"],
        },
    ]
}


def test_rf2726_get_psc_preserves_corporate_identification(monkeypatch):
    async def fake_get(path, _attempt=0):
        return _PSC_PAYLOAD if "persons-with-significant-control" in path else None
    monkeypatch.setattr(ch, "_get", fake_get)
    psc = asyncio.run(ch.get_psc("12345678"))
    corp = next(p for p in psc if "corporate" in (p["kind"] or ""))
    indiv = next(p for p in psc if "individual" in (p["kind"] or ""))
    assert corp["identification"]["registration_number"] == "09999999"
    assert corp["identification"]["country_registered"] == "England"
    assert indiv["identification"] is None, "individual PSC has no identification anchor"


def test_rf2726_investigation_emits_only_anchored_corporate_edges(monkeypatch):
    async def fake_get(path, _attempt=0):
        if "persons-with-significant-control" in path:
            return _PSC_PAYLOAD
        if "/officers" in path:
            return {"items": []}
        if "/filing-history" in path:
            return {"items": []}
        # company profile
        return {"company_name": "SUBJECT LTD", "company_number": "12345678", "company_status": "active",
                "date_of_creation": "2010-01-01", "type": "ltd"}
    monkeypatch.setattr(ch, "_get", fake_get)
    inv = asyncio.run(ch.investigate_uk_entity("12345678"))
    cb = inv.get("controlled_by") or []
    assert len(cb) == 1, f"exactly one ANCHORED corporate edge expected, got {cb}"
    edge = cb[0]
    assert edge["controller_name"] == "PARENT HOLDINGS LTD"
    assert edge["controller_registration_number"] == "09999999"
    assert edge["grade"] == "A"
    assert edge["anchor"] == "companies_house_psc_identification"
    # the individual PSC is NOT emitted as a corporate control edge (no fabrication)
    assert all(e["controller_name"] != "Jane Individual" for e in cb)
    # but the individual remains a disclosed PSC ownership fact
    assert any(p["name"] == "Jane Individual" for p in inv["psc"]["current"])


def test_rf2726_prompt_presents_anchor_as_verified(monkeypatch):
    inv = {
        "found": True,
        "profile": {"company_name": "SUBJECT LTD", "company_number": "12345678",
                    "company_status": "active", "company_type": "ltd", "date_of_creation": "2010-01-01"},
        "officers": {"current": []}, "psc": {"current": []}, "filings": {"recent": []}, "ghost_signals": [],
        "controlled_by": [{"controller_name": "PARENT HOLDINGS LTD", "controller_registration_number": "09999999",
                           "controller_country_registered": "England",
                           "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
                           "anchor": "companies_house_psc_identification", "grade": "A"}],
    }
    out = ch.format_for_prompt(inv)
    assert "Anchored control (VERIFIED" in out
    assert "09999999" in out and "PARENT HOLDINGS LTD" in out
