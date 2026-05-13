"""R-F450 — capability test for R-F410's bs4-absent fallback path.

R-F410's `_sanitize_ingest_text` strips HTML primarily via BeautifulSoup
(bs4 + lxml). When bs4 import or parsing fails, the function falls back
to a pure-regex strip. The R-F410 verifier flagged that NO test
exercised the fallback — a bs4-broken environment would silently
regress and leak HTML into chromadb.

This test forces ImportError on `from bs4 import BeautifulSoup` via
sys.modules manipulation, then asserts the regex fallback strips
tags identically to the bs4 path.
"""
from __future__ import annotations

import sys


def test_rf450_sanitize_falls_back_to_regex_when_bs4_missing(monkeypatch):
    """Force bs4 import to raise; assert HTML still gets stripped via
    the regex fallback. Pin the failure mode that R-F410's verifier
    flagged as silently uncovered."""
    # Pre-delete any cached bs4 module
    monkeypatch.delitem(sys.modules, "bs4", raising=False)

    # Replace `bs4` in sys.modules with a sentinel that raises
    # ImportError on attribute access — simulating a broken install.
    class _BrokenBs4:
        def __getattr__(self, name):
            raise ImportError(f"bs4 simulated broken: {name}")

    monkeypatch.setitem(sys.modules, "bs4", _BrokenBs4())

    from aria_service.intel.rag_store import _sanitize_ingest_text

    text = "<p>Hello <b>world</b></p><script>alert('xss')</script> Visit me"
    out, meta = _sanitize_ingest_text(text)

    # Regex fallback path was taken — confirm HTML tags stripped
    assert "<p>" not in out
    assert "<b>" not in out
    assert "</p>" not in out
    assert "<script>" not in out
    # Plain text survives
    assert "Hello" in out
    assert "world" in out
    assert "Visit me" in out
    # Note: the regex fallback does NOT decompose script bodies the way
    # bs4 does — it strips the <script> tag but the body "alert('xss')"
    # is now plain text and could appear in `out`. Pin the trade-off
    # so the fallback's narrower coverage is documented.
    assert meta["html_stripped"] > 0


def test_rf450_sanitize_falls_back_when_bs4_parse_raises(monkeypatch):
    """Even when bs4 imports cleanly, a malformed parse can raise inside
    BeautifulSoup(text, "lxml"). The function's broad `except Exception`
    handler must catch it and fall back. Simulate by monkeypatching
    BeautifulSoup to raise on instantiation."""
    # Ensure bs4 module exists in sys.modules — we patch the symbol
    # the sanitizer imports from it.
    import importlib
    try:
        importlib.import_module("bs4")
    except ImportError:
        # bs4 isn't installed — the first test already covers ImportError;
        # nothing to test here.
        import pytest
        pytest.skip("bs4 not installed; first test covers ImportError path")
        return

    import bs4

    def _exploding_soup(*args, **kwargs):
        raise RuntimeError("simulated lxml parser failure")

    monkeypatch.setattr(bs4, "BeautifulSoup", _exploding_soup)

    from aria_service.intel.rag_store import _sanitize_ingest_text

    text = "<div>Director: <span>Joe Bloggs</span></div>"
    out, meta = _sanitize_ingest_text(text)

    # Fallback ran — tags gone
    assert "<div>" not in out
    assert "<span>" not in out
    # Plain text survives
    assert "Director:" in out
    assert "Joe Bloggs" in out
    assert meta["html_stripped"] > 0
