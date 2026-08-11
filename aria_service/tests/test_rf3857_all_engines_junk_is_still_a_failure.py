"""R-F3857 — R-F3853 turned a DETECTED backend failure into a silent empty success.

THE DEFECT I SHIPPED. R-F3853 drops any engine whose entire contribution is
unrelated to the query. It did that unconditionally, including when EVERY engine
is unrelated. The consequence is a chain of individually-correct steps producing
a wrong answer:

    all engines junk
      -> _drop_query_independent_engines() removes every one -> normalised == []
      -> the R-F3844 whole-set gate runs on []
      -> `if not results: return False`  ("nothing found is an honest answer")
      -> search() falls through and returns ok=True, count=0, results=[]

Before R-F3853 that same input returned ok=False, "noise: query-independent
result set" — correctly, because the backend HAD answered a different question.

WHY THIS IS THE WORST POSSIBLE DIRECTION TO FAIL. "The backend answered a
different question" and "there is genuinely nothing to find" are opposite facts,
and callers act on them differently: an adverse-media sweep reads zero results as
a CLEAN sweep. So the regression converts a detected fault into a false clean —
the same absence-collapsing-into-a-measurement class as the three Phase A gates
in §1, and the one this whole SearXNG incident was about.

It is also not a corner case. It is the EXACT state that arrives when `yep` is
eventually blocked from the datacenter IP, which R-F3853's own commit message
predicted would happen. The protection would have disappeared silently at the
moment it was most needed.

THE FIX. When every engine is unrelated, that IS the whole-set case: hand the
results back untouched and let R-F3844 reject them. The per-engine filter exists
to remove a MINORITY bad source from an otherwise good set; it has no business
deciding the all-bad case, which already had a correct owner.
"""
from __future__ import annotations

import pytest

from aria_service.intel import search_searxng as sx


def _r(engine: str, title: str) -> dict:
    return {"engine": engine, "title": title, "url": "", "snippet": ""}


_QUERY = "Rosoboronexport sanctions"

# The real shape once yep is blocked too: two engines, both serving their own junk.
_ALL_JUNK = [
    _r("bing", "Why Am I Dizzy? 13 Possible Causes"),
    _r("bing", "New fonts | dafont.com"),
    _r("yep", "Puna Barcelona - Inciensos Artesanales"),
    _r("yep", "PUMA ESPANA | Zapatillas"),
]


def test_all_engines_junk_is_handed_to_the_whole_set_gate_untouched():
    """The core regression. Dropping every engine leaves [], and an empty list
    reads to R-F3844 as 'nothing found' rather than 'everyone answered a
    different question'."""
    kept, dropped = sx._drop_query_independent_engines(_QUERY, _ALL_JUNK)

    assert kept == _ALL_JUNK, (
        "with every engine unrelated this is the whole-set case — return the "
        "results untouched so R-F3844 can reject them as noise")
    assert dropped == {}, "nothing is dropped here; R-F3844 owns this case"


def test_the_whole_set_gate_still_fires_on_that_untouched_set():
    """Proves the hand-off actually lands: the set R-F3857 declines to empty is
    the same set R-F3844 rejects. Without this, 'return it untouched' could just
    be a different route to the same false clean."""
    kept, _ = sx._drop_query_independent_engines(_QUERY, _ALL_JUNK)

    assert sx._is_query_independent(_QUERY, kept) is True


def test_an_emptied_set_would_read_as_nothing_found():
    """Pins the mechanism itself, so nobody 'fixes' a future symptom by emptying
    the list again. An empty set is indistinguishable from an honest zero."""
    assert sx._is_query_independent(_QUERY, []) is False


def test_a_minority_bad_engine_is_still_dropped():
    """R-F3853's actual purpose must survive the fix — this is the case that
    reached a customer report (C-19)."""
    good = _r("yep", "US sanctions Rosoboronexport — Treasury")
    kept, dropped = sx._drop_query_independent_engines(_QUERY, [good] + _ALL_JUNK[:2])

    assert dropped == {"bing": 2}
    assert kept == [good]


def test_an_engine_named_unknown_is_not_collateral_damage():
    """BUG 2: `dropped` was keyed by `engine or "unknown"`, so an unnamed engine
    and an engine literally called "unknown" collided — and the collision dropped
    the innocent one. The drop set must be keyed by the real engine key."""
    unnamed_junk = _r("", "Puna Barcelona")
    real_hit = _r("unknown", "Rosoboronexport sanctions — Treasury")

    kept, dropped = sx._drop_query_independent_engines(_QUERY, [unnamed_junk, real_hit])

    assert real_hit in kept, "the 'unknown' engine answered the query — keep it"
    assert unnamed_junk not in kept
    assert "unknown" not in dropped, (
        "the unnamed engine must not be labelled with a name a real engine can have")


@pytest.mark.asyncio
async def test_search_reports_a_backend_failure_not_an_empty_success(monkeypatch):
    """Capability test (§3c) — drives the actual broken path, `search()`, and
    asserts the user-visible contract rather than a helper's return value."""
    class _Resp:
        status_code = 200
        def json(self):
            return {"results": [
                {"title": r["title"], "url": "https://x/" + r["title"][:4],
                 "content": "", "engine": r["engine"]} for r in _ALL_JUNK]}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setenv("SEARXNG_URL", "http://searxng.invalid:8080")

    out = await sx.search(_QUERY, count=10)

    assert out["ok"] is False, (
        "every engine answered a different question — that is a BACKEND FAILURE, "
        "and reporting it as an empty success is a false clean")
    assert out.get("error", "").startswith("noise")
    assert out["results"] == []
    assert out.get("discarded") == len(_ALL_JUNK)
