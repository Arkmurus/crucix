"""R-F3547 — a corroborated news report can finally reach Grade A.

THE LAST CONSUMER WITH NO PRODUCER. `evidence_count >= 2` is what lifts a tier_2
signal to Grade A, and nothing ever produced a count above 1 for news:
`_build_intel_signal` reads the count off the article, and articles are promoted
ONE AT A TIME, so no two reports were ever compared. Measured live 2026-07-31:
conflict_escalation had 9 signals and 2 at Grade A, and the 7 at Grade B could
not have reached A by any path that existed.

Two corroboration engines were already in the tree and BOTH have zero production
callers — `intel/corroboration.py`, whose fixtures were green while it scored
0/20 on real data, and `intel/news_claims.py`. Nothing here is built on either.
This uses `count_independent_witnesses`, the union-find hardened by live evals.

THE DANGEROUS DIRECTION IS THE FIX. A false corroboration is the fabrication this
product exists to prevent ("one false positive destroys the USP"), so every test
below that asserts a merge is paired with one asserting a refusal.
"""

from __future__ import annotations

from aria_service.intel.news_monitor import (
    _apply_news_corroboration,
    _compute_intel_grade,
    _same_event,
)


_EVENT = "Saudi Arabia prepares major offensive against Houthi forces in Yemen"
_OTHER = "Port infrastructure damaged by flooding near Aden harbour"


def _sig(url, source, title, when="2026-07-31T09:00:00+00:00", stype="conflict_escalation"):
    return {
        "signal_type": stype,
        "entities": {"countries": ["Yemen"]},
        "url": url,
        "source": source,
        "title": title,
        "decision_summary": title,
        "detected_at": when,
        "evidence_count": 1,
        "evidence": {"url": url, "source": source, "count": 1},
    }


def _counts(signals):
    return [s["evidence_count"] for s in _apply_news_corroboration(signals)]


# ── It corroborates when it genuinely should ─────────────────────────────────


def test_two_independent_publishers_on_one_event_corroborate():
    assert _counts([
        _sig("https://reuters.com/a", "Reuters", _EVENT),
        _sig("https://apnews.com/b", "AP", _EVENT),
    ]) == [2, 2], "two independent publishers still read as single-source"


def test_corroboration_is_what_lifts_a_tier_2_report_to_grade_a():
    """The whole point: the 7 Grade-B conflict signals now have a path to A."""
    single = _compute_intel_grade(
        source_tier="tier_2", signal_type="conflict_escalation", priority="HIGH",
        evidence_count=1, url="https://reuters.com/a", entities={"countries": ["Yemen"]},
    )
    corroborated = _compute_intel_grade(
        source_tier="tier_2", signal_type="conflict_escalation", priority="HIGH",
        evidence_count=2, url="https://reuters.com/a", entities={"countries": ["Yemen"]},
    )
    assert single[0] == "B"
    assert corroborated[0] == "A", "corroboration does not reach decision-grade"


def test_every_report_of_one_event_gets_the_same_answer():
    """Done on READ for this reason: doing it on write would make the first
    arrival Grade B and the second Grade A for the same event."""
    counts = _counts([
        _sig("https://reuters.com/a", "Reuters", _EVENT),
        _sig("https://apnews.com/b", "AP", _EVENT),
        _sig("https://afp.com/c", "AFP", _EVENT),
    ])
    assert len(set(counts)) == 1, f"one event, disagreeing counts: {counts}"
    assert counts[0] == 3


# ── It refuses when it should ────────────────────────────────────────────────


def test_two_propaganda_channels_never_corroborate():
    """ARIA's constitution says their CONTENT IS NOT FACT. A rule calling one
    unconfirmed but two corroboration contradicts itself."""
    assert _counts([
        _sig("telegram:intelslava/1", "intelslava", _EVENT),
        _sig("telegram:wartranslated/2", "wartranslated", _EVENT),
    ]) == [1, 1]


def test_a_propaganda_channel_never_becomes_a_second_witness():
    assert _counts([
        _sig("https://reuters.com/a", "Reuters", _EVENT),
        _sig("telegram:intelslava/1", "intelslava", _EVENT),
    ]) == [1, 1], "an aggregator repost was counted alongside a real publisher"


def test_one_publisher_twice_is_one_witness():
    assert _counts([
        _sig("https://reuters.com/a", "Reuters", _EVENT),
        _sig("https://reuters.com/b", "Reuters", _EVENT),
    ]) == [1, 1], "a publisher is not two independent witnesses"


def test_different_events_in_the_same_country_do_not_merge():
    assert _counts([
        _sig("https://reuters.com/a", "Reuters", _EVENT),
        _sig("https://apnews.com/b", "AP", _OTHER),
    ]) == [1, 1]


def test_different_signal_types_never_merge():
    assert _counts([
        _sig("https://reuters.com/a", "Reuters", _EVENT),
        _sig("https://apnews.com/b", "AP", _EVENT, stype="sanctions_change"),
    ]) == [1, 1]


def test_reports_outside_the_window_are_not_one_event():
    assert _counts([
        _sig("https://reuters.com/a", "Reuters", _EVENT),
        _sig("https://apnews.com/b", "AP", _EVENT, when="2026-07-19T09:00:00+00:00"),
    ]) == [1, 1], "reports 12 days apart were treated as one event"


def test_an_undatable_signal_never_merges():
    a = _sig("https://reuters.com/a", "Reuters", _EVENT)
    b = _sig("https://apnews.com/b", "AP", _EVENT)
    b["detected_at"] = ""
    b.pop("published", None)
    assert _same_event(a, b) is False


def test_a_title_too_thin_to_judge_never_merges():
    a = _sig("https://reuters.com/a", "Reuters", "Yemen")
    b = _sig("https://apnews.com/b", "AP", "Yemen")
    assert _same_event(a, b) is False, "two bare country names were merged as one event"


def test_a_partial_wording_overlap_is_not_enough():
    a = _sig("https://reuters.com/a", "Reuters",
             "Saudi Arabia prepares major offensive against Houthi forces in Yemen")
    b = _sig("https://apnews.com/b", "AP",
             "Saudi Arabia signs civilian aviation agreement with Yemen carriers")
    assert _same_event(a, b) is False


# ── It never removes evidence ────────────────────────────────────────────────


def test_an_asserted_count_is_never_lowered():
    """A source that states its own corroboration keeps it."""
    s = _sig("https://reuters.com/a", "Reuters", _EVENT)
    s["evidence_count"] = 4
    assert _counts([s, _sig("https://apnews.com/b", "AP", _EVENT)])[0] == 4


def test_a_lone_signal_is_untouched():
    assert _counts([_sig("https://reuters.com/a", "Reuters", _EVENT)]) == [1]
    assert _apply_news_corroboration([]) == []


def test_the_corroboration_field_tracks_the_count():
    out = _apply_news_corroboration([
        _sig("https://reuters.com/a", "Reuters", _EVENT),
        _sig("https://apnews.com/b", "AP", _EVENT),
    ])
    assert all(s["corroboration"] == "corroborated" for s in out)
    assert all(s["evidence"]["count"] == 2 for s in out)


# ── One definition of an independent witness ─────────────────────────────────


def test_the_correlator_and_the_grader_share_one_rule():
    """The propaganda rule lived only in signal_correlator, which is how the news
    grader ended up with no notion of it. One derivation point now."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parents[2]
    corr = (repo / "aria_service" / "intel" / "signal_correlator.py").read_text(encoding="utf-8")
    assert "count_independent_witnesses" in corr, (
        "the correlator no longer uses the shared witness rule"
    )
    assert "_looks_like_propaganda_source" not in corr, (
        "the correlator still carries its own copy of the propaganda rule"
    )

    from aria_service.intel.dd_independent_verifier import count_independent_witnesses
    assert count_independent_witnesses([
        {"url": "telegram:intelslava/1", "source": "intelslava"},
        {"url": "telegram:wartranslated/2", "source": "wartranslated"},
    ]) == 1
    assert count_independent_witnesses([
        {"url": "https://reuters.com/a"}, {"url": "https://apnews.com/b"},
    ]) == 2
