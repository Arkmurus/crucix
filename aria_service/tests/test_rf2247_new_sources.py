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


def test_new_domains_are_not_duplicates():
    doms = [(urlparse(s[1]).hostname or "").replace("www.", "") for s in NEWS_SOURCES]
    for dom in _NEW:
        assert doms.count(dom) == 1, f"{dom} must be a NEW single domain, got {doms.count(dom)}"
