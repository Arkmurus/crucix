"""R-F2861 — Swiss (CH) entities had NO registry lookup, only manual-action text.

`dd_orchestrator` answered every CH company with a hint string ("check Zefix at
zefix.ch"), so "verified legal identity" could never be satisfied from a primary
source for Swiss entities — a real coverage hole for commodity traders, holding
structures and defence intermediaries, which cluster in CH.

The Zefix REST API (`www.zefix.admin.ch/ZefixPublicREST`) requires credentials —
VERIFIED 401 on every endpoint 2026-07-22. But the SAME federal dataset is
published as OPEN linked data on LINDAS and needs no credentials:
    POST https://lindas.admin.ch/query   (SPARQL, verified HTTP 200)
That is the source this module uses.

These tests NEVER touch the network. The fixture below is a verbatim capture of
a real LINDAS response (company 225002, "Socarim S.A."), so the parse is pinned
to the true wire shape rather than an invented one. A live smoke is run by hand
and recorded in the R-number, not executed here — the suite must never depend on
a government endpoint being up (that is how the Playwright suite-hang happened).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from aria_service.intel import zefix


# Verbatim LINDAS SPARQL JSON (captured live 2026-07-22).
_FIXTURE = {
    "head": {"vars": ["company", "name", "uid", "legalForm", "municipality", "description"]},
    "results": {
        "bindings": [
            {
                "company": {"type": "uri", "value": "https://register.ld.admin.ch/zefix/company/225002"},
                "name": {"type": "literal", "value": "Socarim S.A."},
                "uid": {"type": "uri",
                        "value": "https://register.ld.admin.ch/zefix/company/225002/UID/CHE102145963"},
                "legalForm": {"type": "uri", "value": "https://ld.admin.ch/ech/97/legalforms/0106"},
                "municipality": {"type": "uri", "value": "https://ld.admin.ch/municipality/6800"},
                "description": {"type": "literal", "value": "La construction et la vente d'immeubles"},
            },
            {   # deliberately sparse row — every OPTIONAL absent
                "company": {"type": "uri", "value": "https://register.ld.admin.ch/zefix/company/999"},
                "name": {"type": "literal", "value": "Minimal AG"},
            },
        ]
    },
}


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _install(monkeypatch, *, payload=_FIXTURE, status=200, raises=None):
    calls = {}

    class _FakeClient:
        def __init__(self, *a, **k):
            calls['timeout'] = k.get('timeout')

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            calls['url'] = url
            calls['kwargs'] = kwargs
            if raises:
                raise raises
            return _FakeResponse(payload, status)

    monkeypatch.setattr(zefix.httpx, "AsyncClient", _FakeClient)
    return calls


def test_search_returns_normalised_swiss_companies(monkeypatch):
    """CAPABILITY: a CH name search yields primary-source registry records."""
    _install(monkeypatch)
    rows = asyncio.run(zefix.search_company("socarim"))

    assert rows, "a matching Swiss company must be returned"
    top = rows[0]
    assert top["name"] == "Socarim S.A."
    # The UID is the Swiss registration number a DD report must cite.
    assert top["uid"] == "CHE-102.145.963", f"UID must be normalised, got {top['uid']}"
    assert top["source_url"] == "https://register.ld.admin.ch/zefix/company/225002"
    assert top["municipality_id"] == "6800"
    assert top["legal_form_code"] == "0106"
    assert "immeubles" in (top["purpose"] or "")


def test_absent_fields_are_none_never_invented(monkeypatch):
    """A sparse record must yield None, never a guessed or blank-string value.

    Absent is not the same as empty — inventing a value here would put an
    unsourced 'fact' into a compliance report.
    """
    _install(monkeypatch)
    rows = asyncio.run(zefix.search_company("socarim"))
    minimal = [r for r in rows if r["name"] == "Minimal AG"][0]
    for field in ("uid", "legal_form_code", "municipality_id", "purpose"):
        assert minimal[field] is None, f"{field} must be None when absent, got {minimal[field]!r}"


def test_transport_failure_returns_empty_and_never_raises(monkeypatch):
    """A registry outage must not crash a DD run — it must degrade to no data.

    Returning [] (not a partial or fabricated result) is what lets the caller
    record an honest data gap.
    """
    _install(monkeypatch, raises=RuntimeError("connection reset"))
    rows = asyncio.run(zefix.search_company("socarim"))
    assert rows == [], "a transport failure must yield no rows, not an exception"


def test_http_error_returns_empty(monkeypatch):
    """NEGATIVE CONTROL: a non-200 must not be parsed as success."""
    _install(monkeypatch, payload={}, status=503)
    rows = asyncio.run(zefix.search_company("socarim"))
    assert rows == [], "a 503 must yield no rows"


def test_query_is_bounded_and_injection_safe(monkeypatch):
    """The name goes into a SPARQL string — a quote must not break out of it."""
    calls = _install(monkeypatch)
    asyncio.run(zefix.search_company('evil" ) } INSERT { <x> <y> <z> } #', limit=3))

    sent = calls['kwargs']['data']['query']
    # The needle is interpolated into a SPARQL string literal. The ONLY thing
    # that makes that safe is escaping the quote so it cannot close the literal
    # and let the rest be parsed as syntax.
    assert '\\"' in sent, 'the double quote in the needle must be escaped'
    assert '") }' not in sent, 'an unescaped literal-terminator must never reach the query'
    assert calls['timeout'] is not None, 'the request must be timeout-bounded'


def test_dd_dispatch_routes_CH_to_zefix(monkeypatch):
    """CAPABILITY: the path dd_orchestrator actually calls.

    A client nothing dispatches to is dead code, so drive the real entry point —
    registry_adapters.lookup_entity — with jurisdiction CH and assert a
    normalised registry result comes back.
    """
    from aria_service.intel import registry_adapters

    _install(monkeypatch)
    result = asyncio.run(registry_adapters.lookup_entity(
        name="Socarim S.A.", jurisdiction_iso2="CH",
    ))

    assert result is not None, "CH must no longer fall through to 'unsupported'"
    profile = result["profile"]
    assert profile["company_name"] == "Socarim S.A."
    assert profile["company_number"] == "CHE-102.145.963", "the UID must be the registration number"
    assert profile["jurisdiction"] == "CH"
    assert result["adapter"] == "switzerland_zefix_lindas"
    assert result["source_url"].startswith("https://register.ld.admin.ch/")


def test_dd_dispatch_never_claims_an_unevidenced_status(monkeypatch):
    """NEGATIVE CONTROL: the open dataset carries no active/dissolved flag.

    Defaulting it to 'active' would be a false clean — the precise failure this
    platform exists to prevent. It must stay empty, and the gap must be stated.
    """
    from aria_service.intel import registry_adapters

    _install(monkeypatch)
    result = asyncio.run(registry_adapters.lookup_entity(
        name="Socarim S.A.", jurisdiction_iso2="CH",
    ))

    assert result["profile"]["company_status"] == "", (
        "registration status is not in this dataset and must not be assumed"
    )
    gaps = " ".join(result.get("data_gaps") or [])
    assert "status" in gaps.lower(), "the status gap must be stated, not left silent"
    assert "beneficial ownership" in gaps.lower(), "Swiss UBO is private — say so"
    assert result["psc"] == [], "no UBO may be invented for a CH entity"


def test_municipality_uses_the_admin_ch_namespace_not_schema_org():
    """REGRESSION GUARD — the bug only the LIVE smoke could catch (§23).

    The first implementation queried `schema:municipality`, which expands to
    http://schema.org/municipality. Zefix does not use that predicate, so the
    field came back null against the real endpoint — while these fixture tests
    stayed GREEN, because a fixture supplies the binding regardless of which
    predicate was asked for. Pin the namespace so it cannot regress silently.
    """
    q = zefix.build_query("acme", 5)
    assert "<https://schema.ld.admin.ch/municipality>" in q, (
        "municipality must use the admin.ch schema namespace"
    )
    assert "schema:municipality" not in q, (
        "schema:municipality expands to schema.org and returns nothing from Zefix"
    )


def test_limit_is_honoured(monkeypatch):
    """An unbounded LIMIT against a national dataset is a self-inflicted DoS."""
    calls = _install(monkeypatch)
    asyncio.run(zefix.search_company("socarim", limit=7))
    sent = calls['kwargs']['data']['query'] if 'data' in calls['kwargs'] else calls['kwargs']['content']
    assert "LIMIT 7" in sent, f"the caller's limit must reach the query: {sent[-80:]}"
