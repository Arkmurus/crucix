"""R-F3361 — a structurally-empty query was treated as proof that all search engines are blocked.

THE ASSUMPTION, stated in the code (R-F1657/R-F1790, web_search.py:661):

    "A 200 with 0 results from a configured self-host instance means search is
     BLOCKED (all upstream engines CAPTCHA/rate-limited), not that the world has
     no answer."

That is true for a broad open-web query. It is FALSE for a query that restricts
the result space so far that zero is the ordinary outcome — above all a `site:`
restriction against a domain that blocks crawlers. Live 2026-07-28, six of the
nine zero-result events in the ledger window were:

    site:twitter.com the latest on: Turkey Turkish defence indus...
    site:linkedin.com the latest on: Gulf UAE Saudi Arabia Qatar...
    site:twitter.com the latest on: Indo-Pacific AUKUS Quad ASEA...

X/Twitter and LinkedIn are not indexed by SearXNG's upstreams, so those return 0
every time, by construction. ARIA generates them itself
(company_investigator.py:733-734, deep_researcher.py:707).

THREE CONSEQUENCES, none of them cosmetic:
  1. `_sx_cb.record_failure(reason="rate_limit")` — a false failure on the
     breaker for ARIA's PRIMARY search backend. Enough self-inflicted `site:`
     queries and SearXNG gets circuit-broken while perfectly healthy.
  2. a WARNING asserting a cause that was never established ("all upstream
     engines likely blocked") — a fabricated diagnosis (CLAUDE.md §22), and
     ledger noise.
  3. `wire_failure(gap_type="search_all_engines_blocked")` — a false gap handed
     to the brain and the autonomous coder, which may then "fix" a non-problem.

THE FIX preserves the signal R-F1657 wanted and removes only the false positive:
zero results for a `site:`-restricted query is EXPECTED and is not evidence about
engine health. A plain open-web query returning zero still trips all three, loudly.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import web_search as ws


def _run(coro):
    return asyncio.run(coro)


RESTRICTED = [
    "site:twitter.com the latest on: Turkey Turkish defence industry",
    "site:linkedin.com the latest on: Gulf UAE Saudi Arabia",
    'SITE:X.COM "acme corp"',
    'foo bar site:facebook.com',
]

OPEN_QUERIES = [
    "UAE trade sanctions compliance framework 2026",
    "South Korea Hanwha KAI defence export 2026",
]


# ── the discriminator ───────────────────────────────────────────────────────

def test_site_restricted_queries_are_recognised():
    for q in RESTRICTED:
        assert ws._zero_results_is_expected(q), q


def test_open_queries_are_not_treated_as_expected_zero():
    for q in OPEN_QUERIES:
        assert not ws._zero_results_is_expected(q), q


def test_discriminator_is_total_on_junk():
    for q in [None, "", "   ", 123, []]:
        assert ws._zero_results_is_expected(q) is False


def test_website_in_prose_is_not_a_site_operator():
    """Don't go blind: the operator is `site:`, not the word 'site'."""
    assert not ws._zero_results_is_expected("best site for defence news")


# ── behaviour: a restricted query must not accuse the backend ───────────────

def _drive(query: str):
    """Drive the REAL _search_searxng with the adapter returning ok+configured+0."""
    empty = {"ok": True, "configured": True, "results": [], "backend": "searxng"}
    breaker = type("B", (), {
        "is_open": lambda self: False,
        "record_success": lambda self: None,
        "record_failure": lambda self, reason=None: self.__dict__.setdefault("fails", []).append(reason),
    })()
    wired: list[str] = []

    with patch.object(ws, "_get_cb", create=True), \
         patch("aria_service.intel.circuit_breaker.get_breaker", return_value=breaker), \
         patch("aria_service.intel.search_searxng.is_configured", return_value=True), \
         patch("aria_service.intel.search_searxng.search", new=AsyncMock(return_value=empty)), \
         patch("aria_service.intel.engine_wiring.wire_failure",
               side_effect=lambda **kw: wired.append(kw.get("gap_type", ""))):
        out = _run(ws._search_searxng(query))
    return out, breaker.__dict__.get("fails", []), wired


@pytest.mark.parametrize("q", RESTRICTED)
def test_restricted_zero_does_not_trip_the_breaker(q, caplog):
    caplog.set_level(logging.WARNING)
    out, fails, wired = _drive(q)
    assert out == []
    assert not fails, (
        f"a site:-restricted query recorded a breaker failure against ARIA's "
        f"PRIMARY search backend: {fails}"
    )
    assert "search_all_engines_blocked" not in wired, "false gap wired to the brain"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "fabricated 'all upstream engines blocked' diagnosis on an expected zero"
    )


@pytest.mark.parametrize("q", OPEN_QUERIES)
def test_open_query_zero_still_raises_the_real_alarm(q, caplog):
    """The guard must not go blind — this is the case R-F1657 exists for."""
    caplog.set_level(logging.WARNING)
    out, fails, wired = _drive(q)
    assert fails, "a genuine all-engines-blocked signal was swallowed"
    assert "search_all_engines_blocked" in wired
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]
