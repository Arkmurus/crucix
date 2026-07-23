"""R-F2890/2891/2892/2893/2896 — Golden Intel collection + distribution integrity.

CAPABILITY tests (§3c): each one drives the function that was actually broken and
asserts the user-visible outcome, not a helper's return shape.

Symptoms these lock down, all observed on the LIVE production feed 2026-07-23:
  * 46 of 86 configured feeds dead (the whole defence-specialist layer), which
    poisoned failed_ratio to 0.483 -> `source_failure_degraded` -> the customer
    dashboard rendered EMPTY while 3 Grade A + 26 Grade B signals existed.
  * A pork recipe, a football injury and Sahel pastoral bulletins promoted into a
    security-and-defence intelligence feed.
  * "Marine Corps Detachment BIDS farewell to Mestemacher" classified as a HIGH
    priority `active_tender` by unanchored substring matching.
  * The only 3 Grade A signals sitting at feed positions 66-68 while the Telegram
    cron fetched the newest 60 and reported "no Grade A".
"""
from __future__ import annotations

import asyncio

import pytest

from datetime import datetime, timezone

from aria_service.intel import news_monitor as nm


def _now_iso() -> str:
    """Freshness is measured against wall-clock; a hardcoded date would make these
    tests pass today and rot into false failures tomorrow."""
    return datetime.now(timezone.utc).isoformat()


# ── R-F2890: the dead sources are gone, and cannot silently come back ─────────

DEAD_HOSTS = [
    "janes.com", "rusi.org", "csis.org", "iiss.org", "chathamhouse.org",
    "shephardmedia.com", "militaryaerospace.com", "army-technology.com",
    "naval-technology.com", "airforce-technology.com", "atlanticcouncil.org",
]


def test_rf2890_dead_feed_hosts_are_not_configured():
    """The 46 twice-probed-dead feeds must not be in the polling list."""
    urls = " ".join(s[1] for s in nm.NEWS_SOURCES).lower()
    still_present = [h for h in DEAD_HOSTS if h in urls]
    assert not still_present, f"dead feed hosts still configured: {still_present}"


def test_rf2890_no_duplicate_feed_urls():
    """Two names on one URL double-counts an article => FALSE corroboration."""
    urls = [s[1] for s in nm.NEWS_SOURCES]
    assert len(urls) == len(set(urls))


def test_rf2890_quarantine_after_consecutive_failures_then_self_heals():
    """A curated feed that fails repeatedly is quarantined; ONE success clears it.

    This is the failure class that let 46 corpses poll hourly for 8 days after
    R-F2634 documented them as dead.
    """
    health: dict = {}
    url, name, now = "https://dead.example/rss", "Dead Feed", 1_000_000.0

    for i in range(nm._CURATED_QUARANTINE_AFTER - 1):
        assert nm._note_feed_failure(health, url, name, now=now) is False, f"early quarantine at {i}"
        assert nm._feed_quarantined_until(health, url) <= now

    assert nm._note_feed_failure(health, url, name, now=now) is True
    assert nm._feed_quarantined_until(health, url) > now

    # Self-healing: a later success fully clears streak AND quarantine.
    nm._note_feed_success(health, url, name)
    assert health[url]["fails"] == 0
    assert nm._feed_quarantined_until(health, url) == 0


def test_rf2890_feed_health_read_is_strict_and_never_clobbers(monkeypatch):
    """A store read error must yield None (=> skip + do NOT write), not {}.

    `get_json` returns None for BOTH 'absent' and 'store broken'; writing back on
    that ambiguity is the non-strict-read clobber class that has already destroyed
    durable state in this repo.
    """
    async def boom(_key):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(nm.rs, "get_json_strict", boom)
    assert asyncio.run(nm._load_feed_health()) is None

    async def absent(_key):
        return None

    monkeypatch.setattr(nm.rs, "get_json_strict", absent)
    assert asyncio.run(nm._load_feed_health()) == {}


# ── R-F2891: the topical relevance gate ──────────────────────────────────────

OFF_TOPIC = [
    "WHAT'S COOKING: Pork T-bone with caper butter and braised red cabbage and pears",
    "Arsenal defender Saliba sidelined for 'extended period' due to back injury",
    "World Cup 2026: The true winner was Palestine",
    "Marine Corps Detachment bids farewell to Mestemacher, welcomes Grissett during ceremony",
    "Report: Construction already underway on Ben Gvir's prison crocodile moat",
]

ON_TOPIC = [
    "US Treasury sanctions three entities over Iran missile procurement network",
    "Poland awarded a contract to Rheinmetall for 200 armoured vehicles",
    "Yemen's Houthis hit tankers in Red Sea as US strikes Iran for 12th consecutive night",
    "Boeing, Lufthansa Technik team up on Germany's Chinook fleet",
    "France's future deep-strike weapon may face its first real test in Ukraine",
    "GE Aerospace, Magellan sign deal for Canada Gripen engines",
]


@pytest.mark.parametrize("title", OFF_TOPIC)
def test_rf2891_off_topic_is_rejected(title):
    assert nm._topical_relevance({"title": title, "summary": ""})["on_topic"] is False


@pytest.mark.parametrize("title", ON_TOPIC)
def test_rf2891_real_intelligence_is_kept(title):
    """Recall matters more than precision for a COLLECTION gate: intel dropped
    here is invisible by construction, so a false negative can never be noticed."""
    r = nm._topical_relevance({"title": title, "summary": ""})
    assert r["on_topic"] is True, f"lost real intel: {title} ({r['reason']})"


def test_rf2891_exclusion_marker_never_beats_a_real_event():
    """A sanctioned football club is still intelligence. EXCLUDE only vetoes when
    neither detector fired — it must not veto a genuine domain event."""
    r = nm._topical_relevance(
        {"title": "US Treasury sanctions football club owner over arms trafficking", "summary": ""}
    )
    assert r["on_topic"] is True


def test_rf2891_capability_off_topic_article_is_not_promoted(monkeypatch):
    """CAPABILITY: drive _promote_article_signal itself — the function that put a
    recipe into the intel store — and assert nothing is stored."""
    stored: list = []

    async def _capture(sig):
        stored.append(sig)

    monkeypatch.setattr(nm, "_store_intel_signal", _capture)

    article = {
        "title": "WHAT'S COOKING: Pork T-bone with caper butter",
        "summary": "", "url": "https://example.com/food", "source": "Daily Maverick",
        "tier": "tier_2", "category": "regional_news", "topics": [],
    }
    assert asyncio.run(nm._promote_article_signal(article)) is False
    assert stored == [], "an off-topic article reached the intel signal store"
    assert article["off_topic"] is True
    assert "relevance_score" in article


def test_rf2891_capability_on_topic_article_is_promoted(monkeypatch):
    """The same path must still promote genuine intel — a gate that drops
    everything would 'fix' the symptom by deleting the product."""
    stored: list = []

    async def _capture(sig):
        stored.append(sig)

    monkeypatch.setattr(nm, "_store_intel_signal", _capture)

    article = {
        "title": "US Treasury sanctions three entities over Iran missile procurement network",
        "summary": "", "url": "https://home.treasury.gov/news/x", "source": "US Treasury",
        "tier": "tier_1a", "category": "defence_global", "topics": ["defence"],
    }
    assert asyncio.run(nm._promote_article_signal(article)) is True
    assert len(stored) == 1
    assert stored[0]["signal_type"] == "sanctions_change"


# ── R-F2892: anchored classification + auditable evidence ────────────────────

def test_rf2892_bids_farewell_is_not_a_tender():
    """The live defect: substring 'bid' in 'bids farewell' produced a HIGH
    priority active_tender from an official tier_1a feed."""
    stype, _why, _action, _ev = nm._classify_article_signal(
        "Marine Corps Detachment bids farewell to Mestemacher, welcomes Grissett", "news", []
    )
    assert stype != "active_tender"


@pytest.mark.parametrize("text,not_type", [
    ("Workers end a three-day strike at the port", "conflict_escalation"),
    ("The programme of cultural events was announced", "programme_signal"),
    ("He was appointed head of the local football association", "political_transition"),
])
def test_rf2892_weak_needles_no_longer_fire(text, not_type):
    stype, _w, _a, _e = nm._classify_article_signal(text, "news", [])
    assert stype != not_type


def test_rf2892_real_events_still_classify():
    for text, expect in [
        ("Ministry issues invitation to bid for radar maintenance", "active_tender"),
        ("Rheinmetall awarded a contract for 200 vehicles", "contract_award"),
        ("US Treasury sanctions two shipping firms", "sanctions_change"),
    ]:
        stype, _w, _a, _e = nm._classify_article_signal(text, "news", [])
        assert stype == expect, f"{text!r} -> {stype}, expected {expect}"


def test_rf2892_classification_evidence_is_captured():
    """A classification nobody can audit is how 'bids farewell' survived."""
    stype, _w, _a, ev = nm._classify_article_signal(
        "Ministry issues invitation to bid for radar maintenance", "news", []
    )
    assert stype == "active_tender"
    assert "invitation to bid" in ev

    article = {
        "title": "Ministry issues invitation to bid for radar maintenance",
        "summary": "", "url": "https://gov.example/n", "source": "Gov",
        "tier": "tier_1a", "category": "defence_global", "topics": [],
    }
    signal = nm._build_intel_signal(article)
    assert "invitation to bid" in signal["classification_evidence"]


# ── R-F2893: server-side grade selection ─────────────────────────────────────

def test_rf2893_grade_filter_selects_at_the_source(monkeypatch):
    """CAPABILITY: the 3 Grade A signals sat at positions 66-68 while the cron
    fetched 60. Asking the server for grade A must return them regardless of how
    much lower-grade volume sits in front."""
    import json as _json

    noise = [{"id": f"n{i}", "intel_grade": "REJECT", "signal_type": "market_watch",
              "url": f"https://x.example/{i}", "detected_at": "2026-07-23T09:00:00+00:00"}
             for i in range(66)]
    # intel_grade is RECOMPUTED on read (R-F2714), so the fixture must carry the
    # evidence that genuinely earns Grade A: tier_1a + HIGH + a named entity + URL.
    gold = [{"id": f"g{i}", "intel_grade": "A", "signal_type": "active_tender",
             "url": f"https://ted.europa.eu/{i}", "detected_at": "2026-07-23T06:00:00+00:00",
             "why_it_matters": "w", "recommended_action": "a", "title": "t",
             "priority": "HIGH", "source_tier": "tier_1a",
             "entities": {"countries": ["Germany"], "products": [], "oems": []}}
            for i in range(3)]
    raw = [_json.dumps(s) for s in noise + gold]

    async def _lrange(_key, _start, _end):
        return raw[_start:_end + 1]

    async def _poll_state():
        return {"status": "ok", "last_poll_at": "2026-07-23T09:10:00+00:00",
                "last_success_at": _now_iso(), "feeds_polled": 40,
                "feeds_failed": 1, "results": []}

    monkeypatch.setattr(nm.rs, "lrange", _lrange)
    monkeypatch.setattr(nm, "_read_poll_state", _poll_state)

    res = asyncio.run(nm.get_recent_intel_signals(limit=20, grades="A"))
    assert len(res["signals"]) == 3, "Grade A crowded out of the window again"
    assert {s["intel_grade"] for s in res["signals"]} == {"A"}


def test_rf2893_unknown_grade_fails_closed(monkeypatch):
    """A garbage `grades` value must fall back to A,B — never widen the gate."""
    import json as _json

    raw = [_json.dumps({"id": "r", "intel_grade": "REJECT", "signal_type": "market_watch",
                        "url": "https://x.example/r"})]

    async def _lrange(_k, _s, _e):
        return raw

    async def _poll_state():
        return {"status": "ok", "results": []}

    monkeypatch.setattr(nm.rs, "lrange", _lrange)
    monkeypatch.setattr(nm, "_read_poll_state", _poll_state)

    res = asyncio.run(nm.get_recent_intel_signals(limit=10, grades="REJECT,C,../etc"))
    assert res["signals"] == [], "an invalid grade widened the publishable set"


# ── R-F2896: one canonical publishability verdict ────────────────────────────

def test_rf2896_source_failure_degraded_alone_does_not_block_publishing(monkeypatch):
    """The live 2026-07-23 state: the ONLY stale reason was source_failure_degraded,
    the channel published from it, the dashboard blanked. One verdict now."""
    import json as _json

    raw = [_json.dumps({"id": "a", "intel_grade": "A", "signal_type": "active_tender",
                        "url": "https://ted.europa.eu/1", "why_it_matters": "w",
                        "recommended_action": "a", "title": "t",
                        "priority": "HIGH", "source_tier": "tier_1a",
                        "entities": {"countries": ["Germany"], "products": [], "oems": []},
                        "detected_at": _now_iso()})]

    async def _lrange(_k, _s, _e):
        return raw

    async def _poll_state():
        # 42/87 failed => ratio 0.48 => source_failure_degraded, nothing else stale.
        return {"status": "degraded", "last_poll_at": _now_iso(),
                "last_success_at": _now_iso(), "feeds_polled": 87,
                "feeds_failed": 42, "results": []}

    monkeypatch.setattr(nm.rs, "lrange", _lrange)
    monkeypatch.setattr(nm, "_read_poll_state", _poll_state)

    fresh = asyncio.run(nm.get_recent_intel_signals(limit=10))["freshness"]
    assert "source_failure_degraded" in fresh["stale_reasons"]
    assert fresh["blocking_stale_reasons"] == []
    assert fresh["publishable"] is True, "ambient source noise blanked the product again"


def test_rf2896_real_staleness_still_blocks(monkeypatch):
    """The verdict must still be capable of saying NO — a gate that never fails
    is not a gate."""
    async def _lrange(_k, _s, _e):
        return []

    async def _poll_state():
        return {"status": "ok", "last_poll_at": "2020-01-01T00:00:00+00:00",
                "feeds_polled": 40, "feeds_failed": 0, "results": []}

    async def _no_backfill(_n):
        return []

    monkeypatch.setattr(nm.rs, "lrange", _lrange)
    monkeypatch.setattr(nm, "_read_poll_state", _poll_state)
    monkeypatch.setattr(nm, "_backfill_intel_signals_from_articles", _no_backfill)

    fresh = asyncio.run(nm.get_recent_intel_signals(limit=10))["freshness"]
    assert fresh["publishable"] is False
    assert fresh["blocking_stale_reasons"]
