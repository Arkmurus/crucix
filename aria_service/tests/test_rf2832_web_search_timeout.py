"""R-F2832 — _web_search must honour the timeout it declares.

THE DEFECT (measured, not inferred). `_web_search(query, timeout: float = 10.0)`
applied that timeout ONLY to its legacy/RSS fallback branches (researcher.py
~:132/:154/:166 of the function body). Its PRIMARY path —

    await asyncio.gather(ws.search_multilingual(...), _query_internal_index(...))

had no wait_for and no asyncio.timeout: zero occurrences in the 193-line body. The
declared timeout was a false contract, and NINE production call sites depend on it
(deep_researcher.py x6, researcher.py x3); eight do not even pass one and rely on
the 10s default that does nothing.

MEASURED against real backends (P2-G, 2026-07-21), five adverse-media-shaped queries:
    declared timeout : 10.00s
    actual           : 36.07 / 45.00 / 52.90 s   (min / median / max)
    mean             : 44.01s
    exceeded 10s     : 5/5, by 3.6x-5.3x

That is the mechanism behind the adverse-media overrun. run_adverse_media_deep_search
checks its deadline BEFORE each template and then runs one unbounded search to
completion, so:
    180s budget + 52.9s worst overrun = 232.9s  >  210s wait_for backstop
The backstop fires and the PARTIAL findings are discarded — evidence that was
genuinely gathered is thrown away. Bounding the search makes the honest-partial path
reachable, i.e. it raises COVERAGE; it does not lower any bar.

HONESTY CONSTRAINT (the reason this test exists rather than a one-line wait_for).
In the adverse-media loop a raised exception is accounted as `breaker_skips += 1;
continue` and is correctly EXCLUDED from `_templates_searched` (R-F2791's field for
"templates that actually reached the search layer"). If a timed-out search instead
returned [], it would be counted as SEARCHED-AND-FOUND-NOTHING — inflating the
honesty field with searches that never ran, which is a false clean. So the
honesty-critical caller must be able to make a timeout RAISE. But only 1 of 5
sampled callers has a local try/except, so raising unconditionally would trade a
false clean for crashes across the research stack. Hence: bounded for everyone,
strict opt-in for the caller whose accounting depends on it.
"""
import asyncio
import time

import pytest

from aria_service.intel import researcher as R

# R-F3772/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def _patch_backends(monkeypatch, *, delay: float, results=None):
    """Patch the REAL symbols _web_search uses: `web_search.search_multilingual`
    (imported inside the function as `ws`) and `_query_internal_index`.
    """
    from aria_service.intel import web_search as ws

    async def _slow_multilingual(query, languages=None, max_results=30, **_kw):  # R-F2846 kwarg
        await asyncio.sleep(delay)
        return results or []

    async def _slow_internal(_query):
        await asyncio.sleep(delay)
        return []

    monkeypatch.setattr(ws, "search_multilingual", _slow_multilingual, raising=False)
    monkeypatch.setattr(R, "_query_internal_index", _slow_internal, raising=False)

    # Keep the test HERMETIC. When the primary path is bounded and returns empty,
    # the non-strict branch falls through to the DDG / Google-News-RSS fallbacks,
    # which make REAL network calls — that made this test both slow and
    # non-deterministic (isolated it ran in 2.36s; under pytest it exceeded the
    # bound). Those fallbacks already honour `timeout` and are not what is under
    # test here; stub them so this measures the PRIMARY bound only.
    async def _no_ddg(*_a, **_k):
        return []

    async def _no_rss(*_a, **_k):
        return []

    monkeypatch.setattr(ws, "web_search", _no_ddg, raising=False)
    monkeypatch.setattr(R, "_fetch_rss", _no_rss, raising=False)


@pytest.mark.asyncio
async def test_web_search_returns_within_its_declared_timeout(monkeypatch):
    """CAPABILITY: the call must not outlive the timeout it advertises.

    Pre-fix this returned after the full 30s backend delay; the timeout argument
    was inert on the primary path.
    """
    _patch_backends(monkeypatch, delay=30.0)

    # Use the STRICT path for the timing proof: asyncio.wait_for raises at the
    # deadline and the R-F2832 re-raise escapes the broad handler, so no fallback
    # runs and the measurement isolates the primary gather. The backend below
    # sleeps 30s; anything near the declared 1s proves the gather is now bounded.
    t0 = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await R._web_search("some adverse media query", timeout=1.0,
                            raise_on_timeout=True)
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, (
        f"_web_search took {elapsed:.1f}s for a declared timeout of 1.0s — the "
        "primary path is still unbounded. Measured live at 36-53s against a "
        "declared 10s, which is what discards partial adverse-media evidence."
    )


@pytest.mark.asyncio
async def test_timeout_can_raise_so_honesty_accounting_stays_correct(
    monkeypatch
):
    """A timed-out search must be distinguishable from 'searched, found nothing'.

    run_adverse_media_deep_search counts `_templates_searched` only for calls that
    RETURN. If a timeout returned [], a sweep in which every backend timed out would
    report 30/30 templates searched with zero findings — indistinguishable from a
    genuinely clean entity. That is the exact false-clean class R-F2791 exists to
    prevent.
    """
    _patch_backends(monkeypatch, delay=30.0)

    with pytest.raises(asyncio.TimeoutError):
        await R._web_search("q", timeout=1.0, raise_on_timeout=True)


@pytest.mark.asyncio
async def test_default_callers_are_bounded_but_not_crashed(monkeypatch):
    """The 8 call sites with no local try/except must degrade, not raise.

    Bounding them is the robustness win; raising at them would convert a slow
    search into an outage across the research stack.
    """
    _patch_backends(monkeypatch, delay=30.0)

    out = await R._web_search("q", timeout=1.0)  # raise_on_timeout defaults False

    assert isinstance(out, list), "default callers must still receive a list"
    # NOTE: deliberately NO wall-clock assertion here. The non-strict branch
    # continues into the DDG / RSS fallback chain, whose duration depends on global
    # search state that other suites warm or leave slow (rf1597 / rf2745 / rf2791
    # were measured polluting this timing). Coupling a boundedness proof to that is
    # brittle, and widening the bound to accommodate it would weaken the assertion
    # rather than test the contract. The PRIMARY bound is proven deterministically
    # in test_web_search_returns_within_its_declared_timeout via the strict path,
    # which raises at the timeout and escapes before any fallback runs. This test
    # asserts the OTHER contract: an unguarded caller degrades, it does not crash.


@pytest.mark.asyncio
async def test_fast_search_is_unaffected(monkeypatch):
    """ANTI-REGRESSION: a normal, fast search must behave exactly as before."""

    class _R:
        title = "t"
        url = "https://example.test/a"
        snippet = "s"
        source = "src"
        credibility_tier = "tier1"
        relevance_score = 0.9
        language = "en"

    _patch_backends(monkeypatch, delay=0.0, results=[_R()])

    out = await R._web_search("q", timeout=10.0)
    assert isinstance(out, list)
    assert any(r.get("link") == "https://example.test/a" for r in out), (
        "the normal path must still return converted results — the timeout wrapper "
        "must not swallow successful searches"
    )


def test_adverse_media_loop_asks_for_strict_timeouts():
    """The honesty-critical caller must opt in, or its accounting silently rots."""
    import inspect
    src = function_source(R, "run_adverse_media_deep_search")
    assert "raise_on_timeout=True" in src, (
        "run_adverse_media_deep_search must request strict timeout behaviour so a "
        "timed-out template is counted as a breaker skip, never as a template that "
        "was searched and found nothing (R-F2791)"
    )
