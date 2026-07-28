"""R-F3355 — ARIA's own memory:// records were routed to the EXTERNAL article fetcher.

THE CHAIN (each link verified at file:line, not inferred):

  1. web_search.py:1188 — a memory-first RAG hit with no URL gets an opaque
     pointer minted for it: `url = f"memory://{sha1}"`, purely so the dedupe
     key stays stable across retrievals. It is an IDENTIFIER, not a locator.
  2. researcher.py:1632 — `_web_search` maps EVERY SearchResult into the
     pipeline dict with `"link": r.url`, with no filter for pseudo-URLs.
  3. researcher.py:4079 — `research_and_learn` then calls
     `_fetch_article_text(article["link"])` on it (also 3631 via read_article
     and 4207 in the evidence loop).
  4. researcher.py:1104 → security.py:243 — `sanitise_url` rejects the unknown
     scheme and logs `Blocked URL: memory://… — Blocked protocol: memory` at
     WARNING.
  5. error_log_handler.py:164 — the ledger handler mirrors WARNING+ `aria.*`
     logs into the 200-slot error ledger.

MEASURED CONSEQUENCE (live aria-intel, 2026-07-28): 86-88 of the 200 ledger
slots — 43-44% — were this one self-inflicted line, arriving roughly every
10-15s during a research burst, with the SAME record ids recurring. The ledger
reported `window_errors_24h == window_errors_7d == 200` while physically
retaining ~6.4h, and `ledger_saturated: true`. error_streak.py:72 documents the
hazard in its own words: "a warning burst >200 evicts a real ERROR out of the
ledger".

THE FIX IS THE ABSENCE OF THE WORK, NOT THE ABSENCE OF THE LOG. Suppressing the
line (error_log_handler keeps a documented suppression tuple for exactly that
kind of thing) would have been the band-aid CLAUDE.md §1 forbids: the pointless
fetch of ARIA's own record would remain. Instead the fetcher short-circuits an
internal ref before it does anything, so the warning has nothing to report.

BEHAVIOUR IS UNCHANGED. `_fetch_article_text` already returned "" for these
(sanitise_url → None → return ""), and research_and_learn already handles an
empty body by processing the item from its title + snippet. This removes a
wasted call and a log line; it does not drop an article.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from aria_service.intel import security as sec
from aria_service.intel import researcher as res


def _run(coro):
    return asyncio.run(coro)


INTERNAL = [
    "memory://582d7291606e",
    "rag://abc123",
    "aria://note/1",
    "brain_hook:deadbeef",
    "MEMORY://UPPERCASE",          # scheme match must be case-insensitive
    "  memory://leading-space",    # and whitespace-tolerant
]

REAL_BLOCKS = [
    "javascript:alert(1)",
    "file:///etc/passwd",
    "data:text/html;base64,PHNjcmlwdD4=",
]


# ── the canonical predicate ─────────────────────────────────────────────────

def test_internal_refs_are_recognised():
    for u in INTERNAL:
        assert sec.is_internal_ref(u), u


def test_real_urls_and_dangerous_schemes_are_not_internal_refs():
    for u in ["https://example.com/a", "http://x.io", *REAL_BLOCKS]:
        assert not sec.is_internal_ref(u), u


def test_predicate_is_total_on_junk_input():
    for u in [None, "", "   ", 123, [], {}]:
        assert sec.is_internal_ref(u) is False


# ── CAPABILITY: the fetcher does not touch an internal ref ──────────────────

def test_fetching_an_internal_ref_emits_no_warning(caplog):
    """The user-visible outcome is what reaches the ledger: error_log_handler
    mirrors WARNING+ only, so 'no WARNING' IS 'no ledger entry'."""
    caplog.set_level(logging.WARNING)
    out = _run(res._fetch_article_text("memory://582d7291606e"))
    assert out == "", "an internal ref must yield no body"
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warned, (
        f"internal ref still logged at WARNING+ (this is what floods the "
        f"200-slot ledger): {[r.getMessage()[:90] for r in warned]}"
    )


def test_internal_ref_short_circuits_before_any_network_attempt():
    """Root-cause guard: the skip must happen BEFORE the fetch machinery, not
    inside it. If anything downstream of the guard runs, this explodes."""
    calls: list[str] = []

    class _Boom:
        def __getattr__(self, name):
            raise AssertionError(
                f"internal ref reached the fetch layer (httpx.{name}) — "
                f"the short-circuit is in the wrong place"
            )

    import aria_service.intel.researcher as _r
    original = getattr(_r, "httpx", None)
    if original is not None:
        _r.httpx = _Boom()
    try:
        out = _run(_r._fetch_article_text("memory://582d7291606e"))
    finally:
        if original is not None:
            _r.httpx = original
    assert out == ""
    assert not calls


@pytest.mark.parametrize("ref", INTERNAL)
def test_every_internal_scheme_is_short_circuited(caplog, ref):
    caplog.set_level(logging.WARNING)
    assert _run(res._fetch_article_text(ref)) == ""
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ── the guard must NOT go blind: genuinely dangerous URLs still shout ───────

@pytest.mark.parametrize("bad", REAL_BLOCKS)
def test_dangerous_schemes_still_warn(caplog, bad):
    """If this fix silenced ALL blocked URLs it would hide real signal. A
    javascript:/file:/data: block must still reach the ledger."""
    caplog.set_level(logging.WARNING)
    assert sec.sanitise_url(bad) is None
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warned, f"{bad!r} was blocked SILENTLY — real signal lost"
    assert any("Blocked URL" in r.getMessage() for r in warned)


def test_sanitise_url_still_rejects_internal_refs():
    """Quieter, not permissive: the ref is still refused, just not as an anomaly."""
    for u in INTERNAL:
        assert sec.sanitise_url(u) is None


def test_sanitise_url_still_passes_real_urls():
    assert sec.sanitise_url("https://example.com/story") == "https://example.com/story"


# ── the producer that makes this reachable, pinned so the chain stays known ──

def test_web_search_still_mints_the_pointer_this_guards_against():
    """If web_search ever stops minting memory:// the guard is dead weight and
    should be revisited — assert the producer still exists so this test fails
    loudly rather than silently guarding nothing."""
    from pathlib import Path
    ws = (Path(res.__file__).parent / "web_search.py").read_text(encoding="utf-8")
    assert 'f"memory://{_id}"' in ws, (
        "web_search no longer mints memory:// pointers — re-check whether this "
        "guard is still needed (producer→consumer drift)"
    )
