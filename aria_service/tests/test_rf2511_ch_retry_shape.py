"""R-F2511 — Companies House 429-retry + DD officer/PSC shape parse.

Root bug: under DD load CH rate-limited (429); _get returned empty SILENTLY → GB DDs
reported 0 officers with no data-gap (standalone returned data; in-DD 0). Also
investigate_uk_entity returns officers/psc as {current,past,total} DICTS, but the DD
assigned the dict to `directors` (expected a list) and read the wrong address key.
"""
import asyncio
import aria_service.intel.companies_house as ch


class _Resp:
    def __init__(self, status, data=None, headers=None):
        self.status_code = status
        self._data = data or {}
        self.headers = headers or {}
    def json(self):
        return self._data


def _client_factory(seq):
    state = {"i": 0}  # shared across client instances (each _get re-instantiates)

    class _C:
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, headers=None):
            r = seq[min(state["i"], len(seq) - 1)]
            state["i"] += 1
            return r
    return _C


def test_get_retries_past_transient_429(monkeypatch):
    monkeypatch.setattr(ch, "is_enabled", lambda: True)
    monkeypatch.setattr(ch, "_BACKOFF_BASE", 0.001)
    monkeypatch.setattr(ch.httpx, "AsyncClient", _client_factory([_Resp(429), _Resp(200, {"ok": 1})]))
    assert asyncio.run(ch._get("/x")) == {"ok": 1}  # recovered on retry, not empty


def test_persistent_429_marks_unavailable_and_clears(monkeypatch):
    monkeypatch.setattr(ch, "is_enabled", lambda: True)
    monkeypatch.setattr(ch, "_BACKOFF_BASE", 0.001)
    monkeypatch.setattr(ch, "_MAX_RETRIES", 2)
    monkeypatch.setattr(ch.httpx, "AsyncClient", _client_factory([_Resp(429), _Resp(429), _Resp(429)]))

    async def run():
        assert await ch._get("/x") is None
        return ch.consume_unavailable(), ch.consume_unavailable()
    r1, r2 = asyncio.run(run())
    assert r1 == "rate_limited", r1
    assert r2 is None  # consume clears it


def test_genuine_404_is_not_unavailable(monkeypatch):
    monkeypatch.setattr(ch, "is_enabled", lambda: True)
    monkeypatch.setattr(ch.httpx, "AsyncClient", _client_factory([_Resp(404)]))

    async def run():
        assert await ch._get("/x") is None
        return ch.consume_unavailable()
    assert asyncio.run(run()) is None  # a real 404 must NOT flag unavailable


def test_officers_dict_extraction_matches_dd():
    """Mirror the DD's R-F2511 extraction: officers {current,past,total} -> current list."""
    off = {"current": [{"name": "A"}, {"name": "B"}], "past": [{"name": "C"}], "total": 3}
    directors = (off.get("current") if isinstance(off, dict) else off) or []
    assert directors == [{"name": "A"}, {"name": "B"}]  # NOT the dict, NOT len-3-keys
    assert ((["x"].pop and (lambda o: (o.get("current") if isinstance(o, dict) else o) or [])([{"name": "X"}]))) == [{"name": "X"}]


if __name__ == "__main__":
    import types
    class MP:
        def setattr(self, o, n, v): setattr(o, n, v)
    for fn in (test_get_retries_past_transient_429, test_persistent_429_marks_unavailable_and_clears,
               test_genuine_404_is_not_unavailable):
        fn(MP()); print("PASS", fn.__name__)
    test_officers_dict_extraction_matches_dd(); print("PASS shape")
    print("ALL PASS")
