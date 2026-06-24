"""R-F1895 (review #4, surgical) — derive jurisdiction from the entity's
self-reported HQ on its mined site.

The Modirum DD read 'jurisdiction MISSING' and compliance was wired-but-silent —
yet the site states 'Finland Helsinki HQ, Estonia ..., Austria ..., UK ...'. That
stated HQ is a LEGITIMATE jurisdiction indicator (unlike the TLD/language the
Clause-14 guard rejects). The identity layer now extracts it (self-reported) so
compliance can run. Non-fabricating: tagged self-reported, registry-verify still
required.
"""
from __future__ import annotations

import inspect

import aria_service.intel.dd_orchestrator as dd


def test_extracts_hq_and_office_footprint_from_real_site_text():
    txt = ("Modirum GESPI defence solutions. Our global presence wherever you are. "
           "Finland Helsinki HQ Estonia Harjumaa, Tallinn Austria Vienna "
           "United Kingdom London Switzerland Prilly Serbia Belgrade")
    loc = dd._extract_site_locations(txt)
    assert loc["hq_iso2"] == "FI", f"HQ should be Finland, got {loc['hq_iso2']}"
    assert loc["hq_country"] == "finland"
    # office footprint includes the named countries
    for iso2 in ("FI", "EE", "AT", "GB", "CH", "RS"):
        assert iso2 in loc["countries"], f"{iso2} missing from {loc['countries']}"


def test_no_hq_when_no_marker_and_no_fabrication():
    # countries present but NO hq/headquarters marker → hq stays None (no guess)
    loc = dd._extract_site_locations("We serve clients in Germany and France.")
    assert loc["hq_iso2"] is None
    assert set(loc["countries"]) == {"DE", "FR"}
    # empty text → empty, never raises
    empty = dd._extract_site_locations("")
    assert empty["hq_iso2"] is None and empty["countries"] == []


def test_headquartered_in_phrasing():
    loc = dd._extract_site_locations("Acme Defence is headquartered in Vienna, Austria.")
    assert loc["hq_iso2"] == "AT"


def test_identity_layer_wires_site_jurisdiction_and_digital_reuses_pages():
    # static guards: identity seeds jurisdiction from the site when GLEIF didn't,
    # tags it self-reported, and stashes pages for digital to reuse (no double-mine).
    src = inspect.getsource(dd._run_identity)
    assert "_extract_site_locations(" in src, "identity layer does not extract site locations"
    assert "report.identity.jurisdiction_iso2 = _loc[\"hq_iso2\"]" in src
    assert 'target["_mined_pages"]' in src, "mined pages not stashed for reuse"
    assert "SELF-REPORTED" in src, "site jurisdiction not tagged self-reported"
    dig = inspect.getsource(dd._run_digital)
    assert 'target.get("_mined_pages")' in dig, "digital layer does not reuse stashed pages"
