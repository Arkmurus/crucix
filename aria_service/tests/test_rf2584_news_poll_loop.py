"""R-F2584 — reliable news-poll lifespan loop (keeps the Golden Intel feed fresh).

The HOURLY-NEWS-MONITOR autonomous task stopped firing (scheduler), so news_monitor.poll_feeds
went 27h stale and the Telegram Golden Intel channel + dashboard went silent (the gate correctly
skips stale signals). This tests the replacement lifespan helper `_news_poll_once`
(staleness-gated: polls only when the feed is stale).
"""
from __future__ import annotations

import asyncio
import datetime as dt

from aria_service import main as M
from aria_service.intel import news_monitor as nm


def test_news_poll_skips_when_fresh(monkeypatch):
    async def _fresh():
        return {"last_poll_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    monkeypatch.setattr(nm, "_read_poll_state", _fresh)
    called = {"n": 0}

    async def _should_not_run(*a, **k):
        called["n"] += 1
        return {}
    monkeypatch.setattr(nm, "poll_feeds", _should_not_run)
    r = asyncio.run(M._news_poll_once())
    assert r["polled"] is False and r["reason"] == "fresh"
    assert called["n"] == 0     # a fresh feed must NOT re-run the ~250s poll


def test_news_poll_runs_when_stale(monkeypatch):
    async def _stale():
        old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=27)
        return {"last_poll_at": old.isoformat()}
    monkeypatch.setattr(nm, "_read_poll_state", _stale)

    async def _poll(*a, **k):
        return {"ingested": 5, "promotion_bridge": {"distribution_ready": 1}}
    monkeypatch.setattr(nm, "poll_feeds", _poll)
    r = asyncio.run(M._news_poll_once())
    assert r["polled"] is True


def test_news_poll_runs_when_never_polled(monkeypatch):
    async def _empty():
        return {}
    monkeypatch.setattr(nm, "_read_poll_state", _empty)

    async def _poll(*a, **k):
        return {"ingested": 0}
    monkeypatch.setattr(nm, "poll_feeds", _poll)
    r = asyncio.run(M._news_poll_once())
    assert r["polled"] is True


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
