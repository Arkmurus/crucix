"""R-F652 — read_article short-circuits static-asset URLs.

Live evidence 2026-05-17 08:01:40 → 08:02:34 UTC: 4 LinkedIn CDN URLs
(static.licdn.com/aero-v1/sc/h/*) each triggered a DeepSeek call and a
RAG dedup pass — all returned 0 facts. Each burned ~5s and a small but
real LLM cost per email. Multiplied across daily LinkedIn newsletters
that's a steady leak.

The filter is intentionally narrow: only host + path patterns + file
extensions we have direct evidence for. We do NOT want to block, say, a
PDF served from a CDN — only obvious image/CSS/JS/font assets.
"""
from __future__ import annotations

import pytest

from aria_service.intel.researcher import _is_static_asset_url


# ── Positive cases (should be filtered) ────────────────────────────────────

@pytest.mark.parametrize("url", [
    # The live URLs from the 2026-05-17 boot log
    "https://static.licdn.com/aero-v1/sc/h/2fp5x7ci191mxbdy1eynscn59",
    "https://static.licdn.com/aero-v1/sc/h/4zo4b28tqisx2lerkp2qoioke",
    "https://static.licdn.com/aero-v1/sc/h/anu0f2h3c0xay9ou5nkdrjk8q",
    "https://static.licdn.com/aero-v1/sc/h/9hgqsdlf0kw0jx9cks39xu6s",
    # Other static-asset hosts on our list
    "https://static.fbcdn.net/some/path",
    "https://static.twimg.com/icons/x.png",
    "https://abs.twimg.com/responsive-web/foo",
    "https://pbs.twimg.com/media/Eaaaaaa.jpg",
    "https://media.licdn.com/dms/image/v2/something",
    # File-extension matches (any host)
    "https://example.com/path/to/asset.css",
    "https://example.com/path/main.js",
    "https://example.com/img.png",
    "https://example.com/photo.jpeg",
    "https://example.com/font.woff2",
    "https://example.com/icon.svg",
    "https://example.com/video.mp4",
    # Mixed case + query string
    "https://EXAMPLE.com/asset.CSS?v=1",
    "https://example.com/style.css?hash=abc",
])
def test_rf652_blocks_known_static_assets(url):
    assert _is_static_asset_url(url) is True, (
        f"R-F652 false-negative: {url!r} should be blocked but wasn't"
    )


# ── Negative cases (should NOT be filtered) ────────────────────────────────

@pytest.mark.parametrize("url", [
    # Real article URLs from the same log window — must pass
    "https://techgenez.com/samsung-faces-largest-strike-in-history-as-workers-demand-share-of-ai-boom-profits/",
    "https://www.defensenews.com/some-article/",
    "https://www.linkedin.com/pulse/article-title-author",  # LinkedIn article, not CDN
    "https://www.linkedin.com/feed/update/urn:li:activity:12345",
    # Ambiguous: PDF/DOCX hosted on CDN — these CAN be articles, must pass
    "https://example.com/whitepaper.pdf",
    "https://example.com/report.docx",
    "https://example.com/dataset.xlsx",
    # Government / institutional sites
    "https://www.gov.uk/government/publications/export-control",
    "https://www.imo.org/en/About/Documents/some-document",
    # Empty / malformed should not block (let downstream handle)
    "",
    "not a url",
])
def test_rf652_passes_real_articles(url):
    assert _is_static_asset_url(url) is False, (
        f"R-F652 false-positive: {url!r} should pass but was blocked"
    )


def test_rf652_read_article_short_circuits_on_asset():
    """End-to-end: read_article with a CDN URL must return immediately
    without fetching or calling the LLM."""
    import asyncio
    from aria_service.intel import researcher

    # Stub LLM (must be is_configured for the early-return branch we want
    # to test to even be reached)
    class _FakeLLM:
        is_configured = True

        async def complete(self, *a, **k):
            raise AssertionError(
                "R-F652 leak: LLM.complete called for a static-asset URL"
            )

    bad_url = "https://static.licdn.com/aero-v1/sc/h/2fp5x7ci191mxbdy1eynscn59"
    result = asyncio.run(researcher.read_article(_FakeLLM(), bad_url))
    assert result["skipped_reason"] == "static_asset_url"
    assert result["facts_learned"] == 0
    assert result["hypotheses_generated"] == 0
