"""R-F2046 — vault feed sites become ingestion sources in poll_feeds.

The Agent Signup Vault is the controlled data-point catalogue. A site added with
site_type rss/website becomes a live feed in poll_feeds via _get_vault_feed_sources,
shaped as a NEWS_SOURCES tuple so the existing fetch→parse→ledger loop handles it.
portal/api/failed/non-http entries are excluded.
"""
from __future__ import annotations
from unittest.mock import MagicMock
from aria_service.intel import news_monitor as nm
from aria_service.intel import agent_signup_vault as asv


def test_rf2046_only_feed_sites_become_sources(monkeypatch):
    vault = MagicMock()
    vault.list.return_value = [
        {"site_name": "SIPRI Feed", "site_url": "https://sipri.org/rss", "site_type": "rss", "status": "verified"},
        {"site_name": "GovNews", "site_url": "https://gov.example/news", "site_type": "website", "status": "registered"},
        {"site_name": "PortalX", "site_url": "https://portal.example", "site_type": "portal", "status": "verified"},   # creds — skip
        {"site_name": "DeadFeed", "site_url": "https://dead.example/rss", "site_type": "rss", "status": "failed"},      # failed — skip
        {"site_name": "BadProto", "site_url": "ftp://x/rss", "site_type": "rss", "status": "verified"},                # non-http — skip
        {"site_name": "ApiThing", "site_url": "https://api.example", "site_type": "api", "status": "verified"},        # api — skip
    ]
    monkeypatch.setattr(asv, "get_vault", lambda: vault)

    out = nm._get_vault_feed_sources()
    names = [t[0] for t in out]
    assert names == ["vault:SIPRI Feed", "vault:GovNews"], names

    # Every entry is a 6-field NEWS_SOURCES tuple the poll loop can unpack.
    for t in out:
        assert len(t) == 6
        name, url, category, lang, tier, topics = t
        assert category == "vault_curated"
        assert url.startswith("https://")
        assert isinstance(topics, list)


def test_rf2046_empty_or_broken_vault_is_safe(monkeypatch):
    def _boom():
        raise RuntimeError("vault down")
    monkeypatch.setattr(asv, "get_vault", _boom)
    assert nm._get_vault_feed_sources() == []   # never breaks the poll cycle
