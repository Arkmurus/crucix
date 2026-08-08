"""R-F3403 — The Gazette: the statutory record of insolvency, including PERSONAL.

WHY IT IS NOT REDUNDANT WITH COMPANIES HOUSE. R-F3404/R-F3422 wired CH `/insolvency`,
which covers the COMPANY's own proceedings while it remains on the register. The Gazette
is the publication of record and carries PERSONAL insolvency — bankruptcy orders, IVAs —
which no company register holds. A DD that must resolve to natural persons needs that
half; without it, "is this director bankrupt?" is unanswerable.

TWO PROPERTIES THIS SUITE EXISTS TO PIN.

1. A FETCH FAILURE IS NEVER "NO NOTICES". On an insolvency check that is the most
   expensive false clean available: it converts "we could not look" into "they are
   solvent". The adapter separates `empty` (the register answered, nothing on file)
   from `timeout` (it did not answer), and only the first may be read as clean.

2. A FREE-TEXT HIT IS A CANDIDATE, NOT A DETERMINATION. MEASURED live 2026-07-29:
   searching "Carillion" returns 20 corporate notices titled "Notice of Intended
   Dividends" and "SEMPERIAN (FAZAKERLEY) LIMITED and other companies" — matches on the
   notice BODY, not on the subject's name. Reported without that distinction they read
   as twenty insolvency notices ABOUT Carillion. `subject_in_title` carries it, and
   `corroboration_required` says in words that the match is on text rather than on a
   registration number. That is the R-F3089 class, and attaching a winding-up notice to
   a solvent counterparty ends a commercial relationship.

THE TRANSPORT NOTE. `_common.http_get_json` cannot be used here: it always sends
`Accept: application/json` and The Gazette answers that with HTTP 500. Isolated with the
User-Agent held constant (ARIA UA + Accept -> 500; ARIA UA alone -> 200; browser UA +
Accept -> 500; Accept: */* -> 200), so it is the header, not the agent. Left as a guard
below because a well-meaning refactor to "use the shared helper" would silently turn
every insolvency search into a false clean.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel.sources import gazette as gz

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _run(coro):
    return asyncio.run(coro)


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _client(resp):
    c = MagicMock()
    c.get = AsyncMock(return_value=resp)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


def _with(resp):
    import httpx
    return patch.object(httpx, "AsyncClient", return_value=_client(resp))


# ── 1. a failure is never "no notices" ───────────────────────────────────────

def test_http_500_is_a_failure_not_an_empty_result():
    """The exact live failure mode: Accept: application/json -> 500."""
    with _with(_Resp(status=500)):
        r = _run(gz.search_insolvency("Testco Ltd"))
    assert r["ok"] is False
    assert r["outcome"] == gz.OUTCOME_TIMEOUT
    assert "500" in (r["error"] or "")
    assert r["hits"] == []


def test_a_raised_exception_is_a_failure_not_an_empty_result():
    import httpx
    with patch.object(httpx, "AsyncClient", side_effect=RuntimeError("network down")):
        r = _run(gz.search_insolvency("Testco Ltd"))
    assert r["ok"] is False and r["outcome"] == gz.OUTCOME_TIMEOUT


def test_an_answered_empty_register_is_distinguishable_from_a_failure():
    """`empty` means the register answered with nothing — the only state a caller may
    read as clean."""
    with _with(_Resp(payload={"entry": []})):
        r = _run(gz.search_insolvency("Testco Ltd"))
    assert r["ok"] is True
    assert r["outcome"] == gz.OUTCOME_EMPTY
    assert r["hit_count"] == 0


def test_failure_and_empty_do_not_share_an_outcome():
    with _with(_Resp(status=503)):
        failed = _run(gz.search_insolvency("Testco Ltd"))
    with _with(_Resp(payload={"entry": []})):
        empty = _run(gz.search_insolvency("Testco Ltd"))
    assert failed["outcome"] != empty["outcome"]


# ── 2. a hit is a candidate, not a determination ─────────────────────────────

_BODY_MATCH = {"entry": [{"title": "Notice of Intended Dividends",
                          "link": {"@href": "https://www.thegazette.co.uk/notice/1"},
                          "updated": "2019-02-01"}]}
_TITLE_MATCH = {"entry": [{"title": "TESTCO LIMITED",
                           "link": {"@href": "https://www.thegazette.co.uk/notice/2"},
                           "updated": "2020-05-05"}]}


def test_a_body_only_match_is_flagged_as_such():
    """20 of 20 live Carillion hits were body matches titled 'Notice of Intended
    Dividends'. Without this flag they read as notices ABOUT the subject."""
    with _with(_Resp(payload=_BODY_MATCH)):
        r = _run(gz.search_insolvency("Testco Ltd"))
    assert r["hits"][0]["subject_in_title"] is False


def test_a_title_match_is_flagged_as_such():
    with _with(_Resp(payload=_TITLE_MATCH)):
        r = _run(gz.search_insolvency("Testco"))
    assert r["hits"][0]["subject_in_title"] is True


def test_every_result_carries_the_corroboration_warning():
    with _with(_Resp(payload=_TITLE_MATCH)):
        r = _run(gz.search_insolvency("Testco"))
    assert "registration number" in r["corroboration_required"]


# ── the single-entry shape ───────────────────────────────────────────────────

def test_a_single_notice_is_not_dropped():
    """The feed returns `entry` as a DICT when there is exactly one result. Assuming a
    list silently loses the single-hit case — which on an insolvency check means one
    winding-up notice disappearing."""
    with _with(_Resp(payload={"entry": {"title": "TESTCO LIMITED",
                                        "link": {"@href": "https://x/1"}}})):
        r = _run(gz.search_insolvency("Testco"))
    assert r["hit_count"] == 1


# ── personal insolvency is a distinct search ─────────────────────────────────

def test_personal_and_corporate_use_different_category_codes():
    seen = {}

    def _cap(kind):
        def _mk(*a, **k):
            resp = _Resp(payload={"entry": []})
            c = _client(resp)
            orig = c.get

            async def _get(url, params=None, headers=None):
                seen[kind] = params.get("categorycode")
                return await orig(url, params=params, headers=headers)
            c.get = _get
            return c
        return _mk

    import httpx
    with patch.object(httpx, "AsyncClient", _cap("corporate")):
        _run(gz.search_insolvency("Testco", personal=False))
    with patch.object(httpx, "AsyncClient", _cap("personal")):
        _run(gz.search_insolvency("Testco", personal=True))
    assert seen["corporate"] == gz.CATEGORY_CORPORATE_INSOLVENCY
    assert seen["personal"] == gz.CATEGORY_PERSONAL_INSOLVENCY
    assert seen["corporate"] != seen["personal"]


def test_search_all_reports_each_half_separately():
    """A combined `ok` would hide a half that never ran."""
    with _with(_Resp(payload={"entry": []})):
        b = _run(gz.search_all("Testco"))
    assert "corporate" in b and "personal" in b
    assert b["corporate"]["outcome"] and b["personal"]["outcome"]


def test_search_all_is_not_ok_when_one_half_failed():
    call = {"n": 0}

    def _mk(*a, **k):
        call["n"] += 1
        return _client(_Resp(payload={"entry": []}) if call["n"] == 1 else _Resp(status=500))

    import httpx
    with patch.object(httpx, "AsyncClient", _mk):
        b = _run(gz.search_all("Testco"))
    assert b["ok"] is False, "a half that failed was hidden behind a combined ok"


# ── guards ───────────────────────────────────────────────────────────────────

def test_a_too_short_query_is_skipped_not_searched():
    r = _run(gz.search_insolvency("Ab"))
    assert r["outcome"] == "skipped"
    assert r["hits"] == []


def test_the_shared_json_helper_is_not_called():
    """`http_get_json` always sends Accept: application/json, which The Gazette answers
    with HTTP 500. A refactor to 'use the shared helper' would turn every insolvency
    search into a silent false clean.

    Asserted over the AST — no CALL — not over the source text. My first version banned
    the substring and failed on the adapter's own comment EXPLAINING why the helper is
    unusable, which is precisely the assert-the-wording anti-pattern R-F3419 removed
    from three other guards. A comment naming the hazard must not trip the guard against
    the hazard.
    """
    import ast
    import inspect

    tree = ast.parse(module_source(gz))
    called = {
        n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    }
    assert "http_get_json" not in called, (
        "the adapter calls http_get_json again — that sends the Accept header that "
        "makes The Gazette return HTTP 500, which would present as 'no insolvency notices'"
    )


def test_no_accept_header_is_sent():
    assert "Accept" not in gz._HEADERS
