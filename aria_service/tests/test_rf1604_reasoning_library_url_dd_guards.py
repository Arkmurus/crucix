"""R-F1604 — reasoning_library must skip DD-on-URL queries (no stale cache serve).

Operator 2026-06-16: "Aria do a deep dd on www.gozensecurity.com" returned a
completely unrelated UAE/Aerospace-Technology-Corp answer — footer: "↻ Retrieved
from ARIA's reasoning library (prior deepseek … used 6x)". The semantic answer
cache shape-matched a stale, different-entity DD answer and served it.

Root cause: the two guards that should force a fresh answer both missed this
wording:
  - _question_has_url only matched http(s):// — the operator wrote a BARE domain
    "www.gozensecurity.com" (no scheme).
  - _looks_like_investigation_request had "due diligence"/"deep dive" but not the
    abbreviation "dd"/"deep dd".
So the query fell through to entity-gated matching, the gate fell through (no
entity tokens extracted), and a stale shape-match was served.

R-F1604 widens both guards. Either one skips the query → fresh answer.
"""
from __future__ import annotations

import pytest

from aria_service.intel.reasoning_library import (
    _question_has_url,
    _looks_like_investigation_request,
    find_match,
)


@pytest.mark.parametrize("q", [
    "www.gozensecurity.com",
    "do a dd on gozensecurity.com",
    "deep investigation on deltaguard.org",
    "https://x.com/path",          # regression: schemed URLs still caught
    "check capital.bg for me",
])
def test_rf1604_bare_domains_detected_as_url(q):
    assert _question_has_url(q) is True, f"URL/domain not detected in: {q!r}"


@pytest.mark.parametrize("q", [
    "what is the capital of france",
    "are you online",
    "is acme corp sanctioned",       # no domain — must NOT be flagged as URL
])
def test_rf1604_no_url_false_positive(q):
    assert _question_has_url(q) is False, f"false URL detection in: {q!r}"


@pytest.mark.parametrize("q", [
    "do a deep dd on gozensecurity",
    "deep dd on this company",
    "run a dd on acme",
    "investigate acme",              # regression: existing keywords still work
    "due diligence on x",
])
def test_rf1604_dd_recognised_as_investigation(q):
    assert _looks_like_investigation_request(q.lower()) is True, (
        f"not recognised as investigation: {q!r}"
    )


@pytest.mark.asyncio
async def test_rf1604_dd_on_url_is_skipped_not_cache_served():
    """The operator's exact query must NOT be served from the reasoning library
    — find_match returns a skip (fresh answer), never a cached cross-entity hit."""
    res = await find_match("Aria do a deep dd on www.gozensecurity.com")
    assert res["match"] is False, (
        f"R-F1604: DD-on-URL was matched to a cached answer — {res.get('method')}"
    )
    assert res["method"] in ("skipped_investigation", "skipped_url_fresh_crawl"), (
        f"expected an investigation/URL skip, got {res.get('method')!r}"
    )
