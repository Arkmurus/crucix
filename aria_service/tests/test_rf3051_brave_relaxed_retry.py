"""R-F3051 — the paid primary search was contributing NOTHING to real DDs.

LIVE (dd_ef351f140935, SUPACAT LIMITED): `search_ecosystem` recorded
`primary_user_search state=silent, 0 results` while searxng returned 10. Brave was
configured, scope-enabled and NOT rate-limited — it answered HTTP 200 with an empty
result set, because the DD's adverse-media templates build a query shape Brave
cannot satisfy.

Measured against the live paid key (results / how many NAME the subject):

    "SUPACAT LIMITED"                                     5 / 4   on-subject
    "SUPACAT LIMITED" United Kingdom                      5 / -
    "SUPACAT LIMITED" fine OR penalty                     5 / 0   junk
    "SUPACAT LIMITED" regulator OR licence OR ...         5 / 0   junk
    "SUPACAT LIMITED" United Kingdom fine OR penalty      0 / 0   silent
    "SUPACAT LIMITED" United Kingdom regulator OR ...     0 / 0   silent

THE DIRECTION OF THE RELAXATION IS THE WHOLE FIX, and the intuitive one is wrong.
Brave SILENTLY DROPS the quoted phrase whenever an OR-block is present and answers
the OR terms generically — "Penalties | FinCEN.gov", "FIA Super Licence - Wikipedia"
for a Devon vehicle manufacturer. Keeping the OR-block therefore buys results that
R-F2745's subject-name filter immediately discards, which is exactly what the live
reports showed ("29 search result(s) excluded as not referencing 'SUPACAT LIMITED'").
The entity phrase is the anchor; the OR terms are what Brave cannot honour.
"""
import asyncio
from unittest.mock import patch

from aria_service.intel import web_search as ws


# ── the relaxation itself ──────────────────────────────────────────────────
def test_rf3051_relaxes_to_the_quoted_entity_phrase():
    assert ws._relax_brave_query(
        '"SUPACAT LIMITED" United Kingdom regulator OR licence OR fine OR penalty'
    ) == '"SUPACAT LIMITED"'
    assert ws._relax_brave_query('"SUPACAT LIMITED" United Kingdom') == '"SUPACAT LIMITED"'


def test_rf3051_the_or_block_is_dropped_not_kept():
    """The regression that matters: keeping the OR-block returns off-subject junk."""
    out = ws._relax_brave_query('"Acme Ltd" fraud OR bribery OR investigation')
    assert out == '"Acme Ltd"'
    assert " OR " not in out, "an OR-block makes Brave ignore the entity phrase"


def test_rf3051_no_retry_when_there_is_nothing_to_relax():
    # already just the phrase — a retry would repeat the same call
    assert ws._relax_brave_query('"SUPACAT LIMITED"') == ""
    # no quoted anchor — relaxing would change what was asked
    assert ws._relax_brave_query('SUPACAT LIMITED fine OR penalty') == ""
    assert ws._relax_brave_query("") == ""
    assert ws._relax_brave_query(None) == ""


def test_rf3051_multiple_quoted_phrases_are_all_kept():
    out = ws._relax_brave_query('"Acme Ltd" "John Smith" United Kingdom fraud OR bribery')
    assert '"Acme Ltd"' in out and '"John Smith"' in out
    assert "fraud" not in out and "United Kingdom" not in out


# ── the retry behaviour ────────────────────────────────────────────────────
class _Resp:
    status_code = 200

    def __init__(self, n):
        self._n = n

    def json(self):
        return {"web": {"results": [
            {"title": f"r{i}", "url": f"https://example.com/{i}", "description": "d"}
            for i in range(self._n)]}}


def _run_with(responses):
    """Drive _search_brave with a scripted sequence of Brave responses."""
    calls = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            calls.append((params or {}).get("q"))
            return responses[min(len(calls) - 1, len(responses) - 1)]

    with patch.object(ws, "BRAVE_API_KEY", "k"), \
         patch.object(ws.httpx, "AsyncClient", lambda *a, **k: _Client()):
        res = asyncio.run(ws._search_brave(
            '"Acme Ltd" United Kingdom fraud OR bribery', max_results=5))
    return res, calls


def test_rf3051_zero_results_triggers_exactly_one_relaxed_retry():
    res, calls = _run_with([_Resp(0), _Resp(3)])
    assert len(calls) == 2, "one original call plus exactly one retry"
    assert calls[0] == '"Acme Ltd" United Kingdom fraud OR bribery'
    assert calls[1] == '"Acme Ltd"', "the retry drops the qualifier AND the OR-block"
    assert len(res) == 3, "the recovered results are returned"
    assert all(getattr(r, "query_relaxed", False) for r in res), (
        "provenance: a consumer must know the qualifier was dropped")


def test_rf3051_a_productive_query_is_never_retried():
    res, calls = _run_with([_Resp(4)])
    assert len(calls) == 1, "no wasted call when the original query worked"
    assert len(res) == 4
    assert not any(getattr(r, "query_relaxed", False) for r in res)


def test_rf3051_a_genuinely_empty_subject_costs_at_most_one_extra_call():
    res, calls = _run_with([_Resp(0), _Resp(0)])
    assert len(calls) == 2, "the retry must NOT itself retry (no recursion)"
    assert res == []


def test_rf3051_never_raises_and_keeps_the_no_key_contract():
    with patch.object(ws, "BRAVE_API_KEY", ""):
        assert asyncio.run(ws._search_brave('"Acme Ltd" fraud OR bribery')) == []
