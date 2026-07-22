"""R-F2865 — Estonia (EE) registry lookup via the open RIK ariregister endpoint.

Third jurisdiction recovered from the manual-action-only list (after CH/R-F2861
and NO/R-F2862). Source is the official Estonian Centre of Registers and
Information Systems (RIK), open, no credentials — verified live 2026-07-22.

THE STATUS RULE, and why it differs from Norway again:
Estonia publishes a single-letter registry status. "R" (registrisse kantud —
entered in the register) is well established and maps to active. Every OTHER
code seen in the wild is NOT guessed: it is passed through as unknown with an
explicit data gap. Only "R" was ever OBSERVED during this work, so inventing a
mapping for codes we have never seen would be exactly the fabrication this
platform refuses — and a wrong "dissolved"/"active" on a counterparty is a
material error, not a cosmetic one.

    NO  — asserts active, because brreg publishes the distress booleans
    CH  — refuses to assert, because Zefix's open projection has no status field
    EE  — asserts active ONLY for the one code we can evidence

`historical_names` is captured for alias screening (a renamed entity is a common
sanctions-evasion pattern).

No network in these tests. Fixtures are verbatim captures of live responses
(Swedbank AS, reg_code 10060701). A live smoke is run by hand and recorded in
the R-number.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import ariregister


_ROW = {
    "company_id": 2000009492,
    "reg_code": 10060701,
    "name": "Swedbank AS",
    "historical_names": ["Aktsiaselts Hansapank"],
    "status": "R",
    "legal_address": "Harju maakond, Tallinn, Kesklinna linnaosa, Liivalaia tn 8",
    "zip_code": "15040",
    "legal_form": "1",
    "url": "https://ariregister.rik.ee/est/company/10060701/Swedbank-AS",
}
_PAYLOAD = {"status": "OK", "data": [_ROW]}


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


def _install(monkeypatch, *, payload=_PAYLOAD, status=200, raises=None):
    calls: dict = {}

    class _Client:
        def __init__(self, *a, **k):
            calls["timeout"] = k.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            calls["url"] = url
            calls["params"] = kw.get("params")
            if raises:
                raise raises
            return _Resp(payload, status)

    monkeypatch.setattr(ariregister.httpx, "AsyncClient", _Client)
    return calls


def test_search_returns_normalised_estonian_company(monkeypatch):
    calls = _install(monkeypatch)
    rows = asyncio.run(ariregister.search_company("swedbank"))
    assert rows, "a matching Estonian company must be returned"
    top = rows[0]
    assert top["registration_code"] == "10060701"
    assert top["name"] == "Swedbank AS"
    assert top["status"] == "active"
    assert top["former_names"] == ["Aktsiaselts Hansapank"]
    assert "Tallinn" in (top["address"] or "")
    assert top["source_url"].startswith("https://ariregister.rik.ee/")
    assert calls["timeout"] is not None, "the request must be timeout-bounded"


def test_unknown_status_code_is_NOT_guessed(monkeypatch):
    """★ The honesty line: only "R" was ever observed, so only "R" is mapped.

    Guessing that some unseen letter means dissolved (or active) would put a
    fabricated registration status on a counterparty.
    """
    _install(monkeypatch, payload={"status": "OK",
                                   "data": [{**_ROW, "status": "X"}]})
    top = asyncio.run(ariregister.search_company("swedbank"))[0]
    assert top["status"] == "", f"an unmapped code must yield no claim, got {top['status']!r}"
    assert top["status_code_raw"] == "X", "the raw code must be preserved as evidence"


def test_absent_fields_are_none_never_invented(monkeypatch):
    _install(monkeypatch, payload={"status": "OK",
                                   "data": [{"reg_code": 123, "name": "Minimal OU"}]})
    top = asyncio.run(ariregister.search_company("minimal"))[0]
    assert top["address"] is None
    assert top["former_names"] == []


def test_row_without_a_name_is_dropped(monkeypatch):
    """NEGATIVE CONTROL: a nameless record is unusable and must not surface."""
    _install(monkeypatch, payload={"status": "OK", "data": [{"reg_code": 9}]})
    assert asyncio.run(ariregister.search_company("x")) == []


def test_transport_failure_returns_empty_never_raises(monkeypatch):
    _install(monkeypatch, raises=RuntimeError("connection reset"))
    assert asyncio.run(ariregister.search_company("swedbank")) == []


def test_http_error_returns_empty(monkeypatch):
    _install(monkeypatch, status=503, payload={})
    assert asyncio.run(ariregister.search_company("swedbank")) == []


def test_non_ok_api_status_returns_empty(monkeypatch):
    """The endpoint reports its own status — a non-OK body is not data."""
    _install(monkeypatch, payload={"status": "ERROR", "data": [_ROW]})
    assert asyncio.run(ariregister.search_company("swedbank")) == []


# ── DD dispatch ──────────────────────────────────────────────────────────────

def test_dd_dispatch_routes_EE_to_ariregister(monkeypatch):
    """CAPABILITY: the path dd_orchestrator actually calls."""
    from aria_service.intel import registry_adapters

    _install(monkeypatch)
    result = asyncio.run(registry_adapters.lookup_entity(
        name="Swedbank AS", jurisdiction_iso2="EE",
    ))
    assert result is not None, "EE must no longer fall through to unsupported"
    p = result["profile"]
    assert p["company_number"] == "10060701"
    assert p["company_status"] == "active"
    assert p["jurisdiction"] == "EE"
    assert result["adapter"] == "estonia_ariregister"
    assert result["source_url"].startswith("https://ariregister.rik.ee/")


def test_dd_dispatch_states_the_officer_and_ubo_gaps(monkeypatch):
    """This endpoint carries no directors and no UBO — say so, don't stay silent."""
    from aria_service.intel import registry_adapters

    _install(monkeypatch)
    result = asyncio.run(registry_adapters.lookup_entity(
        name="Swedbank AS", jurisdiction_iso2="EE",
    ))
    assert result["officers"] == [], "no officers may be invented"
    assert result["psc"] == [], "no UBO may be invented"
    gaps = " ".join(result.get("data_gaps") or []).lower()
    assert "director" in gaps or "board" in gaps, "the officer gap must be stated"
    assert "beneficial" in gaps or "ubo" in gaps, "the UBO gap must be stated"
