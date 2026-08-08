"""R-F2638 — corroboration engine. Phase B+ under an explicit operator §1 override
("I understand Phase A gate is open. Override anyway", 2026-07-15).

THE PROBLEM (R-F2633 scope, proven live):
  `evidence_count >= 2` is what earns "corroborated" (news_monitor.py:638/665/713) —
  but those are READERS ONLY. NO writer anywhere produces >= 2: the RSS path defaults
  to 1 (:691) and the bridge adapters HARDCODE 1 (golden_intel_bridge.py:559/731/790).
  Live: evidence_count {1: 20}. So corroboration was IMPOSSIBLE, the Mining Queue was a
  roach motel, and "8 candidates awaiting corroboration" was a promise the system could
  not keep. Distribution Ready = 0 was STRUCTURAL.

THE DESIGN — wire what exists, do not build:
  verified_intel.SourceIndependenceChecker (:747) already models this exactly
  ("Reuters and AFP often cover the same press conference... sources in the same family
  citing each other are not independent"), and get_independent_count(sources) -> int IS
  the evidence_count producer. The ONLY new logic is fail-closed clustering.

★ THE TRAP IS THE POINT. The live data showed BOTH failure modes:
  - "US says it launched new wave of strikes against Iran" appeared 3x FROM ONE SOURCE
    (Middle East Eye) -> counting them = evidence_count 3 from ONE org = FALSE
    corroboration.
  - entity-only clustering merged "US strikes Iran" with "US resumes Iran ports
    blockade" -> DIFFERENT events.
  A naive build reports evidence_count=10 and publishes confident lies — converting an
  honest "no" into a false "yes". That is STRICTLY WORSE than the inert state and a
  direct USP kill. Tests 1 and 3 below are the gate that makes this safe.
"""
import pytest

from aria_service.intel import corroboration as corr

# R-F3782/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _sig(title, source, url, published="Wed, 15 Jul 2026 10:00:00 +0000", countries=("Iran",), tier="tier_2"):
    # NB: the default is RFC 2822 — the format the LIVE RSS feed actually emits
    # ('Wed, 15 Jul 2026 10:27:52 +0000'). The first version of these fixtures used
    # ISO 8601, which production NEVER emits: all 7 tests passed while the engine was a
    # silent no-op on real data (0/20 clusterable). Test the format the feed sends.
    return {
        "title": title, "source": source, "url": url, "published": published,
        "source_tier": tier, "entities": {"countries": list(countries), "products": [], "oems": []},
        "evidence_count": 1,
    }


def test_rf2638_duplicates_from_one_source_are_not_corroboration():
    """★ THE ANTI-TRAP TEST — the live Middle East Eye case.

    The SAME story 3x from ONE source must stay evidence_count=1. Counting them would
    manufacture corroboration from a single org — the never-false-clean failure this
    engine exists to avoid.
    """
    sigs = [
        _sig("US says it launched new wave of strikes against Iran", "Middle East Eye", "https://middleeasteye.net/a"),
        _sig("US says it launched new wave of strikes against Iran", "Middle East Eye", "https://middleeasteye.net/b"),
        _sig("US says it launched new wave of strikes against Iran", "Middle East Eye", "https://middleeasteye.net/c"),
    ]
    out = corr.corroborate(sigs)
    for s in out:
        assert s["evidence_count"] == 1, (
            f"3 duplicates from ONE source produced evidence_count={s['evidence_count']} "
            "=> FALSE corroboration from a single org"
        )
        assert s["corroboration"] == "single-source"


def test_rf2638_independent_sources_on_one_event_corroborate():
    """THE PRIZE — the live Iran case: a US DoD (tier_1a) OFFICIAL primary source
    corroborating two outlets on the same event. That is decision-grade."""
    sigs = [
        _sig("US strikes Iran military sites in new wave", "US DoD Daily Contracts",
             "https://defense.gov/x", tier="tier_1a"),
        _sig("US says it launched new wave of strikes against Iran", "Middle East Eye",
             "https://middleeasteye.net/a"),
        _sig("US attacks Iran military sites, IRGC claims strikes", "Al Jazeera",
             "https://aljazeera.com/b"),
    ]
    out = corr.corroborate(sigs)
    top = max(out, key=lambda s: s["evidence_count"])
    assert top["evidence_count"] >= 2, (
        f"3 INDEPENDENT orgs on one event must corroborate, got {top['evidence_count']}"
    )
    assert top["corroboration"] == "corroborated"


def test_rf2638_same_entity_different_event_is_not_merged():
    """★ ANTI-TRAP 2 — entity-only clustering false-merges distinct events.

    "US strikes Iran" and "US resumes Iran ports blockade" share entity Iran but are
    DIFFERENT stories. Merging them would corroborate an event that never happened.
    """
    sigs = [
        _sig("US strikes Iran military base in overnight raid", "Middle East Eye", "https://mee.net/1"),
        _sig("US resumes Iran ports blockade as Gulf shipping halts", "Al Jazeera", "https://aj.net/2"),
    ]
    out = corr.corroborate(sigs)
    for s in out:
        assert s["evidence_count"] == 1, (
            f"different events sharing an entity were MERGED -> {s['title']!r} got "
            f"evidence_count={s['evidence_count']} (false corroboration)"
        )


def test_rf2638_outside_time_window_is_not_merged():
    """Same story text, months apart = different events. Fail closed on time."""
    sigs = [
        _sig("US strikes Iran military base", "Middle East Eye", "https://mee.net/1",
             published="Thu, 01 Jan 2026 10:00:00 +0000"),
        _sig("US strikes Iran military base", "Al Jazeera", "https://aj.net/2",
             published="Wed, 15 Jul 2026 10:00:00 +0000"),
    ]
    out = corr.corroborate(sigs)
    for s in out:
        assert s["evidence_count"] == 1, "signals months apart must not corroborate"


def test_rf2638_parses_the_date_format_the_live_feed_actually_sends():
    """★ REAL-DATA REGRESSION — the bug that made this engine a no-op.

    Live RSS emits RFC 2822 ('Wed, 15 Jul 2026 10:27:52 +0000'). An ISO-only parser
    returned None for EVERY live signal => nothing clusterable => 0/20 corroborated
    while all fixture tests passed. Both formats must parse; junk must still fail closed.
    """
    from aria_service.intel.corroboration import _epoch
    assert _epoch("Wed, 15 Jul 2026 10:27:52 +0000") is not None, (
        "RFC 2822 (what the live RSS feed sends) must parse — an ISO-only parser makes "
        "this engine a silent no-op on production data"
    )
    assert _epoch("Wed, 15 Jul 2026 11:57:40 +0100") is not None, "RFC 2822 w/ offset must parse"
    assert _epoch("2026-07-15T10:00:00+00:00") is not None, "ISO 8601 must still parse"
    assert _epoch("not a date") is None, "junk must fail closed"
    assert _epoch("") is None and _epoch(None) is None, "empty must fail closed"


def test_rf2638_missing_data_fails_closed():
    """FAIL-CLOSED: no entities / no timestamp / junk => single-source, never merged.
    Uncertainty must never become corroboration."""
    sigs = [
        {"title": "Something happened", "source": "A", "url": "https://a.com/1"},
        {"title": "Something happened", "source": "B", "url": "https://b.com/2"},
    ]
    out = corr.corroborate(sigs)
    for s in out:
        assert s.get("evidence_count", 1) == 1, "missing entities/timestamps must fail closed"


def test_rf2638_uses_the_existing_independence_checker():
    """Do not reinvent independence. verified_intel.SourceIndependenceChecker already
    models wire-service/family syndication; this engine must delegate to it."""
    import inspect
    src = module_source(corr)
    assert "SourceIndependenceChecker" in src, (
        "corroboration must delegate to verified_intel.SourceIndependenceChecker — "
        "reimplementing independence would miss wire syndication (Reuters via 5 outlets)"
    )


def test_rf2638_empty_and_single_input_are_safe():
    """NON-REGRESSION: trivial inputs must not explode or invent corroboration."""
    assert corr.corroborate([]) == []
    one = corr.corroborate([_sig("Solo story", "A", "https://a.com/1")])
    assert one[0]["evidence_count"] == 1
    assert one[0]["corroboration"] == "single-source"
