"""R-F2862 — Norway (NO) had no registry lookup, only manual-action text.

Same hole R-F2861 closed for Switzerland. Brønnøysundregistrene publishes the
Norwegian Central Coordinating Register as a fully OPEN, official government API
(https://data.brreg.no/enhetsregisteret/api) — no key, no registration.

It is materially richer than Zefix, and two fields make it decision-grade:

  * REAL STATUS. `konkurs` / `underAvvikling` /
    `underTvangsavviklingEllerTvangsopplosning` are published booleans, so an
    all-false reading is EVIDENCE of an active entity — unlike Zefix, where the
    field simply does not exist and status must stay unknown. The distinction is
    the whole point: absent is not the same as false. If a flag is MISSING from
    the payload we make no claim at all.
  * OFFICERS WITH DATE OF BIRTH, from the open /roller endpoint. A DOB collapses
    the false-positive rate on sanctions/PEP name screening.

`institusjonellSektorkode` 1xx0 identifies STATE-OWNED entities, which is
directly load-bearing for defence DD and RCA screening.

No network in these tests — fixtures are verbatim captures of live responses
(EQUINOR ASA, org 923609016, captured 2026-07-22). A live smoke is run by hand
and recorded in the R-number.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import brreg


_UNIT = {
    "organisasjonsnummer": "923609016",
    "navn": "EQUINOR ASA",
    "organisasjonsform": {"kode": "ASA", "beskrivelse": "Allmennaksjeselskap"},
    "registreringsdatoEnhetsregisteret": "1995-03-12",
    "stiftelsesdato": "1972-09-18",
    "naeringskode1": {"kode": "06.100", "beskrivelse": "Utvinning av råolje"},
    "naeringskode2": {"kode": "06.200", "beskrivelse": "Utvinning av naturgass"},
    "forretningsadresse": {"land": "Norge", "landkode": "NO", "postnummer": "4035",
                           "poststed": "STAVANGER", "adresse": ["Forusbeen 50"]},
    "institusjonellSektorkode": {"kode": "1120",
                                 "beskrivelse": "Statlig eide aksjeselskaper mv."},
    "historiskeNavn": [{"navn": "Den norske stats oljeselskap a.s"}],
    "antallAnsatte": 21393,
    "konkurs": False,
    "underAvvikling": False,
    "underTvangsavviklingEllerTvangsopplosning": False,
}

_SEARCH = {"_embedded": {"enheter": [_UNIT]}, "page": {"totalElements": 1}}

_ROLLER = {
    "rollegrupper": [
        {"type": {"beskrivelse": "Daglig leder"},
         "roller": [{"type": {"beskrivelse": "Daglig leder"},
                     "person": {"navn": {"fornavn": "Anders", "etternavn": "Opedal"},
                                "fodselsdato": "1968-05-04"}}]},
        {"type": {"beskrivelse": "Styre"},
         "roller": [
             {"type": {"beskrivelse": "Styrets leder"},
              "person": {"navn": {"fornavn": "Jarle Kjell", "etternavn": "Roth"},
                         "fodselsdato": "1960-04-26"}},
             {"type": {"beskrivelse": "Revisor"}, "person": {}},   # empty person
         ]},
    ]
}


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


def _install(monkeypatch, *, unit=_SEARCH, roller=_ROLLER, status=200, raises=None):
    calls = {"urls": []}

    class _Client:
        def __init__(self, *a, **k):
            calls["timeout"] = k.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            calls["urls"].append(url)
            calls.setdefault("params", []).append(kw.get("params"))
            if raises:
                raise raises
            return _Resp(roller if "/roller" in url else unit, status)

    monkeypatch.setattr(brreg.httpx, "AsyncClient", _Client)
    return calls


# ── search ───────────────────────────────────────────────────────────────────

def test_search_returns_normalised_norwegian_company(monkeypatch):
    _install(monkeypatch)
    rows = asyncio.run(brreg.search_company("equinor"))
    assert rows, "a matching Norwegian company must be returned"
    top = rows[0]
    assert top["organisation_number"] == "923609016"
    assert top["name"] == "EQUINOR ASA"
    assert top["legal_form_code"] == "ASA"
    assert top["registration_date"] == "1995-03-12"
    assert top["employees"] == 21393
    assert "STAVANGER" in (top["address"] or "")
    assert top["former_names"] == ["Den norske stats oljeselskap a.s"]


def test_all_flags_false_is_EVIDENCE_of_active(monkeypatch):
    """brreg publishes these as booleans, so all-false genuinely means active."""
    _install(monkeypatch)
    top = asyncio.run(brreg.search_company("equinor"))[0]
    assert top["status"] == "active", f"expected active, got {top['status']!r}"


@pytest.mark.parametrize("flag,expected", [
    ("konkurs", "bankrupt"),
    ("underTvangsavviklingEllerTvangsopplosning", "compulsory_liquidation"),
    ("underAvvikling", "in_liquidation"),
])
def test_negative_status_flags_are_reported(monkeypatch, flag, expected):
    """NEGATIVE CONTROL: each distress flag must surface, not be flattened away."""
    unit = {**_UNIT, flag: True}
    _install(monkeypatch, unit={"_embedded": {"enheter": [unit]}})
    top = asyncio.run(brreg.search_company("equinor"))[0]
    assert top["status"] == expected


def test_missing_flags_make_NO_status_claim(monkeypatch):
    """★ The honesty line: ABSENT is not FALSE.

    If brreg omits the flags we must not infer 'active' — that would be
    manufacturing a clean status from missing data.
    """
    unit = {k: v for k, v in _UNIT.items()
            if k not in ("konkurs", "underAvvikling",
                         "underTvangsavviklingEllerTvangsopplosning")}
    _install(monkeypatch, unit={"_embedded": {"enheter": [unit]}})
    top = asyncio.run(brreg.search_company("equinor"))[0]
    assert top["status"] == "", f"absent flags must yield no claim, got {top['status']!r}"


def test_state_ownership_is_detected(monkeypatch):
    """Sector code 1120 = state-owned. Load-bearing for defence DD / RCA."""
    _install(monkeypatch)
    top = asyncio.run(brreg.search_company("equinor"))[0]
    assert top["state_owned"] is True
    assert top["sector_code"] == "1120"


def test_private_company_is_not_flagged_state_owned(monkeypatch):
    """NEGATIVE CONTROL: a private sector code must NOT read as state-owned."""
    unit = {**_UNIT, "institusjonellSektorkode": {"kode": "2100",
                                                  "beskrivelse": "Private aksjeselskaper"}}
    _install(monkeypatch, unit={"_embedded": {"enheter": [unit]}})
    top = asyncio.run(brreg.search_company("equinor"))[0]
    assert top["state_owned"] is False


def test_transport_failure_returns_empty_never_raises(monkeypatch):
    _install(monkeypatch, raises=RuntimeError("connection reset"))
    assert asyncio.run(brreg.search_company("equinor")) == []


def test_http_error_returns_empty(monkeypatch):
    _install(monkeypatch, status=503, unit={})
    assert asyncio.run(brreg.search_company("equinor")) == []


# ── officers ─────────────────────────────────────────────────────────────────

def test_officers_include_date_of_birth(monkeypatch):
    """DOB is why this endpoint matters — it cuts screening false positives."""
    _install(monkeypatch)
    officers = asyncio.run(brreg.get_officers("923609016"))
    ceo = [o for o in officers if o["name"] == "Anders Opedal"]
    assert ceo, f"the CEO must be returned, got {officers}"
    assert ceo[0]["date_of_birth"] == "1968-05-04"
    assert ceo[0]["role"] == "Daglig leder"


def test_officers_with_no_person_are_dropped_not_blanked(monkeypatch):
    """A role with an empty person (e.g. a firm auditor) must not become a
    nameless 'director' — that would inject a phantom officer into a DD report."""
    _install(monkeypatch)
    officers = asyncio.run(brreg.get_officers("923609016"))
    assert all(o["name"].strip() for o in officers), f"blank officer leaked: {officers}"


def test_officers_failure_is_empty_not_fatal(monkeypatch):
    _install(monkeypatch, raises=RuntimeError("boom"))
    assert asyncio.run(brreg.get_officers("923609016")) == []


# ── DD dispatch ──────────────────────────────────────────────────────────────

def test_dd_dispatch_routes_NO_to_brreg(monkeypatch):
    """CAPABILITY: the path dd_orchestrator actually calls."""
    from aria_service.intel import registry_adapters

    _install(monkeypatch)
    result = asyncio.run(registry_adapters.lookup_entity(
        name="EQUINOR ASA", jurisdiction_iso2="NO",
    ))
    assert result is not None, "NO must no longer fall through to unsupported"
    p = result["profile"]
    assert p["company_number"] == "923609016"
    assert p["company_status"] == "active"
    assert p["date_of_creation"] == "1995-03-12"
    assert p["jurisdiction"] == "NO"
    assert "06.100" in p["sic_codes"]
    assert result["adapter"] == "norway_brreg"
    assert any(o["name"] == "Anders Opedal" for o in result["officers"]), \
        "directors must reach the DD report"


def test_dd_dispatch_surfaces_the_UBO_gap(monkeypatch):
    """brreg has officers but NOT beneficial ownership — say so explicitly."""
    from aria_service.intel import registry_adapters

    _install(monkeypatch)
    result = asyncio.run(registry_adapters.lookup_entity(
        name="EQUINOR ASA", jurisdiction_iso2="NO",
    ))
    assert result["psc"] == [], "no UBO may be invented"
    gaps = " ".join(result.get("data_gaps") or []).lower()
    assert "beneficial" in gaps or "ubo" in gaps, "the UBO gap must be stated"
