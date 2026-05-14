"""R-F523 — extend max_redirects=5 cap to the indexer's robots fetcher.

Live evidence 2026-05-14 12:41:30 BST cold boot (after R-F522 deployed):
imo.org/robots.txt fired 40+ identical 301s in 360ms — but from
politeness.py:_fetch_robots_txt(), NOT crawl_enhancements.check_robots()
which R-F522 covered. Two separate robots.txt code paths, R-F522 only
fixed one. Same server-side 301-loop bug on imo.org → cap our patience
in BOTH fetchers.

Source-text check mirrors R-F522's test pattern.
"""
from __future__ import annotations

import inspect

from aria_service.crawler import politeness


def test_rf523_fetch_robots_txt_has_max_redirects_cap():
    """Confirm max_redirects=5 is in the AsyncClient construction inside
    politeness._fetch_robots_txt — defuses the 12:41:30 BST imo.org
    redirect storm that R-F522 missed."""
    src = inspect.getsource(politeness._fetch_robots_txt)
    assert "max_redirects=5" in src, (
        f"R-F523 redirect cap missing from politeness._fetch_robots_txt — "
        f"indexer cold-boot will re-fire the 2026-05-14 12:41:30 BST "
        f"imo.org 40-hop storm. Source:\n{src}"
    )
    assert "follow_redirects=True" in src, (
        "follow_redirects=True must coexist with the cap so normal "
        "single-hop redirects (302/301 → resolved URL) still work."
    )
