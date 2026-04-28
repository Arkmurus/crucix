"""Regression guard for F75/F76 (2026-04-28 batch 7) — tender-portal
noise suppressor.

Production research cycles (~30 min cadence) repeatedly hit broken
upstream endpoints (TED 404, AfDB 403, SEACE Peru 403). Each cycle
fired a fresh WARNING for the same status, polluting the dashboard
and the error ledger. The new `_log_portal_failure` helper logs
WARNING the FIRST time per process, then demotes identical repeats
to debug. A status change re-arms WARNING.
"""
from __future__ import annotations

import logging


def test_first_failure_logs_warning(caplog):
    """First time a portal returns a non-200, we WARN."""
    from aria_service.intel import tender_monitor as tm
    tm._PORTAL_FAILURE_LOGGED.clear()
    with caplog.at_level(logging.WARNING, logger="aria.tender_monitor"):
        tm._log_portal_failure("TED", 404, "API returned 404: <binary, 24359 bytes>")
    matching = [r for r in caplog.records if "[TED]" in r.getMessage()]
    assert matching, "expected first-time TED 404 to emit a WARNING"
    assert matching[0].levelno == logging.WARNING


def test_identical_failure_demoted_to_debug(caplog):
    """A second identical failure must NOT log a WARNING."""
    from aria_service.intel import tender_monitor as tm
    tm._PORTAL_FAILURE_LOGGED.clear()
    # Prime the cache (will emit a WARNING but we don't care).
    tm._log_portal_failure("TED", 404, "first run")
    caplog.clear()  # discard the priming WARNING
    with caplog.at_level(logging.WARNING, logger="aria.tender_monitor"):
        tm._log_portal_failure("TED", 404, "second run")
        tm._log_portal_failure("TED", 404, "third run")
    matching = [r for r in caplog.records if r.levelno == logging.WARNING and "[TED]" in r.getMessage()]
    assert matching == [], (
        f"identical TED 404 should not re-log WARNING; got: "
        f"{[r.getMessage() for r in matching]}"
    )


def test_status_change_re_arms_warning(caplog):
    """If the upstream changes status (recovery + new failure), the
    WARNING re-arms — operator wants to see status churn."""
    from aria_service.intel import tender_monitor as tm
    tm._PORTAL_FAILURE_LOGGED.clear()
    tm._log_portal_failure("TED", 404, "msg404")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="aria.tender_monitor"):
        # Transition to a different status (e.g. 503 from a planned outage)
        tm._log_portal_failure("TED", 503, "msg503")
    matching = [r for r in caplog.records if "msg503" in r.getMessage()]
    assert matching, "status change must re-arm WARNING"
    assert matching[0].levelno == logging.WARNING


def test_separate_portals_dont_block_each_other(caplog):
    """TED suppression must not silence AfDB."""
    from aria_service.intel import tender_monitor as tm
    tm._PORTAL_FAILURE_LOGGED.clear()
    tm._log_portal_failure("TED", 404, "TED first")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="aria.tender_monitor"):
        tm._log_portal_failure("AfDB", 403, "AfDB first")
    matching = [r for r in caplog.records if "[AfDB]" in r.getMessage() and r.levelno == logging.WARNING]
    assert matching, "AfDB first failure must still WARN even when TED is suppressed"


# ── F76: dead RSS feeds removed from list ────────────────────────────────


def test_persistently_dead_feeds_pruned():
    """The 11 feeds that returned 403/404 across every research cycle
    today must NOT be in RESEARCH_FEEDS. Re-adding them later (when an
    upstream restores RSS) is fine — but until then, every cycle that
    hits a known-dead feed wastes a request and a log line."""
    from aria_service.intel.researcher import RESEARCH_FEEDS

    dead_urls = {
        "https://www.defenseone.com/rss/",
        "https://www.armyrecognition.com/rss",
        "https://www.shephardmedia.com/feed/",
        "https://www.dsca.mil/dsca-rss-really-simple-syndication",
        "https://www.iiss.org/online-analysis/military-balance/feed",
        "https://www.chathamhouse.org/rss.xml",
        "https://www.csis.org/rss",
        "https://www.rand.org/pubs/rss.xml",
        "https://www.africa-confidential.com/rss",
        "https://www.infodefensa.com/feed",
        "https://www.huanqiu.com/rss/mil.xml",
    }
    active_urls = {f["url"] for f in RESEARCH_FEEDS}
    leaked = dead_urls & active_urls
    assert leaked == set(), (
        f"these dead feeds are still in the active list and will be hit "
        f"every research cycle: {leaked}"
    )


def test_working_replacement_for_shephard_kept():
    """Shephard /feed/ → 404 was pruned, but the working
    /news/defence-notes/feed/ endpoint must still be in the list so we
    don't lose Shephard coverage entirely."""
    from aria_service.intel.researcher import RESEARCH_FEEDS

    active_urls = {f["url"] for f in RESEARCH_FEEDS}
    assert "https://www.shephardmedia.com/news/defence-notes/feed/" in active_urls, (
        "F76 prune should not remove the working Shephard endpoint"
    )
