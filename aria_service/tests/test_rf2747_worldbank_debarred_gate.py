"""R-F2747 — worldbank_debarred must not attribute a debarment to a same-named firm.

lookup uses _common.fuzzy_filter(threshold=0.72) and set result["hits"] with no
token-overlap gate — so a fuzzy match could attribute a World Bank debarment (a
SEVERE 'RED — active debarment' finding) to a different, similarly-named firm. This
adds the same name-overlap gate sec_edgar (R-F572) uses. (The API path is dormant
without a subscription key; this closes the class so it is safe if the key is set.)
"""
from __future__ import annotations

import asyncio

import aria_service.intel.sources.worldbank_debarred as w


class _CB:
    def is_open(self): return False
    def record_failure(self, **k): pass
    def record_success(self, **k): pass


def _patch(monkeypatch, records):
    async def _load():
        return records
    monkeypatch.setattr(w, "_load_records", _load)
    monkeypatch.setattr(w, "_subscription_key", lambda: "test-key")  # bypass dormant guard
    monkeypatch.setattr(w, "get_breaker", lambda *a, **k: _CB(), raising=False)


_RECORDS = [
    {"name": "ACME WIDGETS LLC", "ineligibility_to": "2030-01-01"},      # different, fuzzy-similar
    {"name": "ACME VENTURES LIMITED", "ineligibility_to": "2030-01-01"},  # the real match
]


def test_rf2747_fuzzy_similar_firm_not_attributed(monkeypatch):
    _patch(monkeypatch, _RECORDS)
    res = asyncio.run(w.lookup("Acme Ventures"))
    names = [h["name"] for h in (res.get("hits") or [])]
    assert "ACME VENTURES LIMITED" in names, "the real debarment must survive"
    assert "ACME WIDGETS LLC" not in names, "a same-named different firm must NOT be attributed a debarment"


def test_rf2747_no_real_match_yields_no_debarment(monkeypatch):
    _patch(monkeypatch, [{"name": "ZENITH TRADING CORP", "ineligibility_to": "2030-01-01"}])
    res = asyncio.run(w.lookup("Acme Ventures"))
    assert (res.get("hits") or []) == [], "no name-overlapping debarment → no false RED finding"
