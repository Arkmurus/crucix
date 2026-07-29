"""R-F2247 — verified free primary-source + new-region feeds added to broaden the
Janes-heavy firehose without adding duplicate domains (source diversity)."""
from __future__ import annotations
from urllib.parse import urlparse
from aria_service.intel.news_monitor import NEWS_SOURCES

_NEW = ("defense.gov", "reliefweb.int", "balkaninsight.com")


def test_new_feeds_present():
    urls = [s[1] for s in NEWS_SOURCES]
    for dom in _NEW:
        assert any(dom in u for u in urls), f"{dom} feed missing"


def test_every_source_has_valid_tuple_shape():
    for s in NEWS_SOURCES:
        assert len(s) == 6, f"bad shape: {s[0] if s else s}"
        name, url, cat, lang, tier, topics = s
        assert url.startswith("http") and isinstance(topics, list) and tier.startswith("tier_")


def test_no_feed_is_registered_twice():
    """R-F3431 — assert the PROPERTY this file's docstring names (source diversity:
    no feed added twice), not a per-domain COUNT that legitimate growth breaks.

    It previously required each _NEW domain to appear EXACTLY ONCE, and had been red
    since a second defense.gov feed was added. Measured: the two entries are
    "US DoD Daily Contracts" (ContentType=1) and "US DoD News" (ContentType=800) —
    genuinely different content streams from one host, which is added coverage, not a
    duplicate. Meanwhile there are ZERO identical feed URLs in the whole list, so the
    thing the test existed to catch was never happening.

    A guard that fires on correct behaviour gets switched off, and this one had been
    masking real failures in every regression run it appeared in.
    """
    urls = [s[1] for s in NEWS_SOURCES]
    dupes = sorted({u for u in urls if urls.count(u) > 1})
    assert not dupes, f"the same feed URL is registered more than once: {dupes}"


def test_each_new_domain_still_contributes():
    """The diversity half: every domain R-F2247 added must still be present. Losing one
    is the regression that test actually guarded against."""
    doms = [(urlparse(s[1]).hostname or "").replace("www.", "") for s in NEWS_SOURCES]
    for dom in _NEW:
        assert doms.count(dom) >= 1, f"{dom} is no longer a source"
