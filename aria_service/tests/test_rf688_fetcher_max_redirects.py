"""R-F688 (2026-05-18) — fetcher caps redirect chains at 5 hops.

Live fly logs 2026-05-18 10:36:45 showed a single crawl of email.net
burning ~30 outbound GETs in 500ms, all logging the same destination
`https://email.net/mailfence.com/`. Mechanism: the auto-registered
email.net entry from the pre-R-F676 era is a parked domain that
301-redirects /robots.txt → /mailfence.com/ → /mailfence.com/ in a
loop. httpx's default max_redirects=20 lets the chain run 20+ hops
per call before bailing; the crawler then issues a second call for
the page itself, and the combined cycle hits ~30 logged hops.

R-F688 caps max_redirects at 5 in the crawler-side httpx client
(mirroring the existing cap in politeness.py:90 for robots.txt
fetches). 5 hops is enough for any legitimate chain (https→http,
www-prefix, region-redirect, canonical URL) and bounds wasted
bandwidth + log noise when a parked domain loops.

The R-F687 crawler-time filter already drops `email.net` from the
sweep (it's auto-registered + label="email" is in the blocklist),
so the immediate trigger is removed. R-F688 is a defensive cap for
any FUTURE parked-domain loop that slips past the blocklist.
"""
from __future__ import annotations

import inspect

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def test_rf688_fetcher_uses_max_redirects_5():
    """fetch_for_crawl must pass max_redirects=5 to httpx.AsyncClient."""
    from aria_service.crawler import fetcher
    src = module_source(fetcher)
    # Find the AsyncClient call in fetch_for_crawl and assert the cap is set.
    assert "max_redirects=5" in src, (
        "R-F688: fetcher's AsyncClient must cap redirects at 5"
    )


def test_rf688_politeness_still_capped():
    """Sanity guard for the existing politeness.py:90 cap so it doesn't
    regress while we're touching adjacent code."""
    from aria_service.crawler import politeness
    src = module_source(politeness)
    assert "max_redirects=5" in src, (
        "R-F688: politeness.py's robots.txt fetcher must keep its cap"
    )


def test_rf688_fetcher_smoke_imports():
    """Lifespan-style smoke test — fetcher module imports clean after
    the AsyncClient kwargs edit (no syntax / indentation breakage)."""
    from aria_service.crawler import fetcher as _f  # noqa: F401
    assert callable(_f.fetch_for_crawl)
