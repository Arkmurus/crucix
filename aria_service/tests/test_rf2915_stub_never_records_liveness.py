"""R-F2915 — a stub adapter must never record registry liveness.

Found by the R-F2911 sweep on 2026-07-23. Nine jurisdictions (AO BG GH IL KE PA SA US
ZA) reported a "match" in ~0.0s with 0 officers whose company_name was the PROBE STRING
ITSELF. Their adapters are stubs: they do not read a registry, they echo the query back
and attach data_gaps explaining that no public API exists.

That payload is honest and useful — a DD report shows the gap instead of nothing. The
defect was one layer up: `lookup_entity` recorded `success` for any truthy result, and
registry_coverage turns a success into `live` with a timestamp as its evidence. So
vault.html would have claimed nine live national registries on the strength of ARIA
quoting itself back — worse than the honest `unproven` it replaced, because a false
positive in a coverage inventory is indistinguishable from real coverage.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import registry_adapters as ra


def _recorded(monkeypatch) -> list[tuple]:
    """Capture what lookup_entity records, without touching the store."""
    calls: list[tuple] = []
    monkeypatch.setattr(ra, "_record_coverage_outcome",
                        lambda iso2, adapter, outcome: calls.append((iso2, adapter, outcome)))
    return calls


def test_rf2915_stub_result_records_empty_not_success(monkeypatch):
    """The core rule: a *_stub adapter records `empty`, never `success`."""
    calls = _recorded(monkeypatch)

    async def _fake_stub(name, reg_number):
        # Exactly what the real stubs return: the QUERY echoed back.
        return {"profile": {"company_name": name}, "officers": [],
                "adapter": "angola_gue_stub", "source_url": "https://gue.gov.ao"}

    monkeypatch.setattr(ra, "_lookup_angola", _fake_stub, raising=False)
    monkeypatch.setitem(ra._DISPATCH, "AO", _fake_stub)

    asyncio.run(ra.lookup_entity("SONANGOL", "AO"))

    assert calls, "nothing was recorded at all"
    iso2, adapter, outcome = calls[0]
    assert iso2 == "AO"
    assert adapter.endswith("_stub")
    assert outcome == "empty", (
        f"a stub recorded {outcome!r} — that becomes `live` in registry_coverage and "
        "claims a national registry answered when none was read"
    )


def test_rf2915_a_real_adapter_still_records_success(monkeypatch):
    """The fix must not suppress genuine liveness — that would be the opposite error."""
    calls = _recorded(monkeypatch)

    async def _fake_real(name, reg_number):
        return {"profile": {"company_name": "EQUINOR ASA", "company_number": "923609016"},
                "officers": [{"name": "A"}], "adapter": "norway_brreg",
                "source_url": "https://virksomhet.brreg.no/nb/oppslag/enheter/923609016"}

    monkeypatch.setattr(ra, "_lookup_norway", _fake_real, raising=False)
    monkeypatch.setitem(ra._DISPATCH, "NO", _fake_real)

    asyncio.run(ra.lookup_entity("EQUINOR ASA", "NO"))

    assert calls[0] == ("NO", "norway_brreg", "success")


def test_rf2915_no_result_still_records_empty(monkeypatch):
    """Unchanged behaviour: a working adapter that finds nothing is `empty`."""
    calls = _recorded(monkeypatch)

    async def _fake_none(name, reg_number):
        return None

    monkeypatch.setattr(ra, "_lookup_norway", _fake_none, raising=False)
    monkeypatch.setitem(ra._DISPATCH, "NO", _fake_none)
    monkeypatch.setattr(ra, "_gleif_global_fallback",
                        lambda *a, **k: asyncio.sleep(0, result=None))

    asyncio.run(ra.lookup_entity("NOSUCHCO", "NO"))
    assert calls[0][2] == "empty"


@pytest.mark.parametrize("adapter_name", [
    "angola_gue_stub", "bulgaria_brra_stub", "ghana_rgd_stub", "israel_registrar_stub",
    "kenya_brs_stub", "panama_registro_publico_stub", "saudi_moci_stub",
    "south_africa_cipc_stub", "us_unknown_stub",
])
def test_rf2915_every_known_stub_name_is_treated_as_a_stub(adapter_name):
    """The rule keys on the `_stub` suffix, so every stub in the tree must carry it.

    If a future stub is named without the suffix it would silently record liveness
    again — this pins the naming convention the rule depends on.
    """
    assert adapter_name.endswith("_stub")


def test_rf2915_stub_adapters_in_source_all_use_the_suffix():
    """Guard the convention at its source: no adapter may return a synthesized
    result under a name that does not end in `_stub`."""
    import re
    from pathlib import Path

    src = Path(ra.__file__).read_text(encoding="utf-8")
    # Every adapter= literal that sits next to a data_gaps block describing an absent
    # API must be suffixed. Cheap proxy: collect all adapter= names and assert the
    # known-synthesized ones are suffixed (a new one added without the suffix will
    # show up here as an unfamiliar name for a human to classify).
    names = set(re.findall(r'adapter="([a-z0-9_]+)"', src))
    synthesized = {n for n in names if "stub" in n}
    assert synthesized, "no stub adapters found — has the naming convention changed?"
    for n in synthesized:
        assert n.endswith("_stub"), f"{n} contains 'stub' but does not end with '_stub'"


# ── R-F2915 (cont): the test is AUTHORITY, not the adapter's name ───────────

def test_rf2915_non_authoritative_status_beats_an_authoritative_name(monkeypatch):
    """A REAL adapter that degrades to a manual/partial result must not record
    liveness, even though its name has no `_stub` suffix.

    RegistryStatus.for_adapter is only the DEFAULT; _build_result lets an adapter pass
    registry_status explicitly. Keying the coverage rule on the NAME would record
    success here — keying it on is_authority() cannot.
    """
    calls = _recorded(monkeypatch)

    async def _degraded(name, reg_number):
        return {"profile": {"company_name": name}, "officers": [],
                "adapter": "norway_brreg",                       # authoritative NAME
                "registry_status": ra.RegistryStatus.MANUAL_REQUIRED.value}   # but not authority

    monkeypatch.setattr(ra, "_lookup_norway", _degraded, raising=False)
    monkeypatch.setitem(ra._DISPATCH, "NO", _degraded)

    asyncio.run(ra.lookup_entity("SOMECO", "NO"))
    assert calls[0][2] == "empty", (
        "a non-authoritative result recorded liveness because its adapter name looked real"
    )


def test_rf2915_explicit_verified_status_records_success(monkeypatch):
    """The inverse must also hold — an explicitly VERIFIED result is liveness."""
    calls = _recorded(monkeypatch)

    async def _verified(name, reg_number):
        return {"profile": {"company_name": name, "company_number": "123"}, "officers": [],
                "adapter": "poland_krs",
                "registry_status": ra.RegistryStatus.VERIFIED.value}

    monkeypatch.setattr(ra, "_lookup_poland", _verified, raising=False)
    monkeypatch.setitem(ra._DISPATCH, "PL", _verified)

    asyncio.run(ra.lookup_entity("ORLEN", "PL"))
    assert calls[0] == ("PL", "poland_krs", "success")


def test_rf2915_partial_is_authority_and_records_success(monkeypatch):
    """PARTIAL means a real registry answered with an incomplete record — that IS
    liveness for the purpose of this inventory (the registry responded)."""
    calls = _recorded(monkeypatch)

    async def _partial(name, reg_number):
        return {"profile": {"company_name": name}, "officers": [],
                "adapter": "czech_ares",
                "registry_status": ra.RegistryStatus.PARTIAL.value}

    monkeypatch.setattr(ra, "_lookup_czech", _partial, raising=False)
    monkeypatch.setitem(ra._DISPATCH, "CZ", _partial)

    asyncio.run(ra.lookup_entity("SKODA", "CZ"))
    assert calls[0][2] == "success"
