"""R-F2741 — usaspending must not attribute other firms' federal awards to the subject.

`recipient_search_text` is a FUZZY search: it returns awards for every company whose
name resembles the query. lookup attached ALL of them — summing their dollar totals
onto the subject and taking the first result's recipient. A search for "Acme Ventures"
could report $13.5M in federal contracts that actually belong to "ACME WIDGETS LLC" +
"ORION DEFENSE CORP". Now each award is gated on its own recipient name confirming the
query (the sec_edgar R-F572 token-overlap pattern).
"""
from __future__ import annotations

import asyncio

import aria_service.intel.sources.usaspending as u


class _Resp:
    def __init__(self, data, status=200):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


class _Client:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        return self._resp


def _patch(monkeypatch, results):
    monkeypatch.setattr(u.httpx, "AsyncClient", lambda *a, **k: _Client(_Resp({"results": results})))

    class _CB:
        def is_open(self): return False
        def record_failure(self, **k): pass
        def record_success(self, **k): pass
    monkeypatch.setattr(u, "get_breaker", lambda *a, **k: _CB())


def _award(recipient, amount, aid):
    return {"Recipient Name": recipient, "Award Amount": amount, "Awarding Agency": "DoD",
            "Award ID": aid, "Period of Performance Start Date": "2024"}


def test_rf2741_only_confirmed_recipients_are_attributed(monkeypatch):
    _patch(monkeypatch, [
        _award("ACME WIDGETS LLC", 9_000_000, "W1"),     # different company
        _award("ACME VENTURES INC", 500_000, "W2"),      # the real match
        _award("ORION DEFENSE CORP", 4_000_000, "W3"),   # different company
    ])
    res = asyncio.run(u.lookup("Acme Ventures"))
    assert res is not None
    assert res["award_count"] == 1, "only the matching recipient's award counts"
    assert res["total_value_usd"] == 500_000.0, "other firms' $13M must NOT be summed onto the subject"
    assert res["recipient"] == "ACME VENTURES INC"
    assert all("VENTURES" in a["recipient"].upper() for a in res["awards"])


def test_rf2741_search_matching_only_other_companies_returns_none(monkeypatch):
    _patch(monkeypatch, [_award("ZENITH CORP", 1_000_000, "Z1"), _award("ORION LLC", 2_000_000, "O1")])
    assert asyncio.run(u.lookup("Acme Ventures")) is None, \
        "no recipient matches → honest: no federal awards for THIS entity (not other firms')"


def test_rf2741_recipient_confirms_contract():
    from aria_service.intel._sanctions_classify import _tokenize_entity_name as tok
    q = tok("Acme Ventures Ltd")
    assert u._recipient_confirms(q, "ACME VENTURES INCORPORATED") is True
    assert u._recipient_confirms(q, "ACME WIDGETS LLC") is False       # only 'acme' shared (4 chars)
    assert u._recipient_confirms(q, "ORION DEFENSE CORP") is False
    assert u._recipient_confirms(q, "") is False
