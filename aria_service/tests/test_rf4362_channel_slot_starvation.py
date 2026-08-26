"""R-F4362 (C-308) — the channel's limited slots must not be spent on signals it
cannot publish.

MEASURED LIVE on aria-intel, 2026-08-26. `channelServerHooks.mjs:1134` fetches
`limit=60, grades=A,B` and the Telegram selector picks from what it gets. What it
got:

    returned 60   suppressed: {non_publishable 166, duplicates 17, over_limit 57}
    types: natural_hazard 21, sanctions_change 17, active_tender 11,
           contract_award 6, conflict_escalation 3, competitor_activity 2

**21 of 60 slots — 35% — went to `natural_hazard`, which is NOT in
`_GOLDEN_ALLOWED_TYPES` and therefore can never be published by the channel.**
At the same moment **57 further candidates were dropped `over_limit`**. The feed
truncates by RECENCY, so unpublishable-but-recent items evict publishable ones
before the channel ever sees them.

This is R-F3536's defect returning through a different door. That fix stopped
`natural_hazard` BUYING Grade A with source authority alone ("46 of 56 Grade-A
signals were natural_hazard ... crowding out every designation and escalation").
They still reach Grade B honestly, and still consume the channel's slots — so the
crowding came back by VOLUME after being closed by GRADE.

THE FIX IS ORDERING, NOT REMOVAL. Nothing is dropped that was not already being
dropped: the dashboard and Mining Queue still see situational awareness, which is
real and worth showing. Only the question "which survive the truncation" changes,
and it is answered by "the ones the consumer can actually act on".

DELIBERATELY NOT a bigger `limit`. That is a constant that rots the moment news
volume rises — the same band-aid §1 forbids, and the same shape as C-304 (a
deadline set independently of the work) and C-298 (a list widened by one).
Recency ordering is PRESERVED within each group, so this changes which signals
survive truncation and nothing else.
"""
from __future__ import annotations

import pytest

from aria_service.intel import news_monitor as nm


def _sig(sid: str, stype: str, **kw) -> dict:
    base = {
        "id": sid,
        "signal_type": stype,
        "priority": "HIGH",
        "confidence": "HIGH",
        "source_tier": "tier_1a",
        "url": f"https://example.test/{sid}",
        "title": f"{stype} {sid}",
        "intel_grade": "B",
        # the endpoint RECOMPUTES the grade on read, so the fixture must
        # satisfy the real grading inputs or everything lands as REJECT
        "entities": {"countries": ["Testland"], "products": [], "oems": []},
        "evidence_count": 2,
        "decision_summary": f"{stype} decision",
    }
    base.update(kw)
    return base


def test_channel_publishable_types_are_known() -> None:
    """The ranking must key on the SAME set the channel gates on, or it would
    optimise for the wrong consumer."""
    allowed = nm._channel_publishable_types()
    assert "sanctions_change" in allowed
    assert "active_tender" in allowed
    assert "contract_award" in allowed
    # the live crowder — real, worth storing, and unpublishable by the channel
    assert "natural_hazard" not in allowed


def test_unpublishable_types_no_longer_evict_publishable_ones() -> None:
    """THE DEFECT, with the live shape: 21 unpublishable arrive first (they are
    the most recent), 11 publishable arrive after, and only 20 slots exist.
    Before this fix the publishable ones were all truncated away."""
    recent_unpublishable = [_sig(f"h{i}", "natural_hazard") for i in range(21)]
    older_publishable = [_sig(f"t{i}", "active_tender") for i in range(11)]
    ordered = nm._rank_for_channel_slots(recent_unpublishable + older_publishable, 20)

    assert len(ordered) == 20
    kept_types = {s["signal_type"] for s in ordered}
    assert "active_tender" in kept_types, (
        "every publishable signal was evicted by more-recent unpublishable ones")
    assert sum(1 for s in ordered if s["signal_type"] == "active_tender") == 11, (
        "all 11 publishable signals should survive a 20-slot truncation")


def test_recency_is_preserved_within_each_group() -> None:
    """Ordering by publishability must not scramble the feed: the channel picks
    'best-first' and the dashboard reads newest-first, so relative order inside a
    group has to survive."""
    sigs = [
        _sig("t1", "active_tender"), _sig("h1", "natural_hazard"),
        _sig("t2", "active_tender"), _sig("h2", "natural_hazard"),
        _sig("t3", "active_tender"),
    ]
    ordered = nm._rank_for_channel_slots(sigs, 5)
    tenders = [s["id"] for s in ordered if s["signal_type"] == "active_tender"]
    hazards = [s["id"] for s in ordered if s["signal_type"] == "natural_hazard"]
    assert tenders == ["t1", "t2", "t3"], "recency order lost among publishable"
    assert hazards == ["h1", "h2"], "recency order lost among unpublishable"


def test_nothing_is_dropped_when_the_limit_allows() -> None:
    """LOAD-BEARING: this is a RE-ORDER, not a filter. With room for everything,
    everything stays — the Mining Queue and dashboard lose nothing."""
    sigs = [_sig("h1", "natural_hazard"), _sig("t1", "active_tender"),
            _sig("s1", "situational_awareness")]
    ordered = nm._rank_for_channel_slots(sigs, 10)
    assert len(ordered) == 3
    assert {s["id"] for s in ordered} == {"h1", "t1", "s1"}


def test_an_all_publishable_feed_is_untouched() -> None:
    """No reordering when there is nothing to demote — the common healthy case
    must be a no-op."""
    sigs = [_sig(f"t{i}", "active_tender") for i in range(5)]
    ordered = nm._rank_for_channel_slots(sigs, 5)
    assert [s["id"] for s in ordered] == [f"t{i}" for i in range(5)]


def test_unknown_or_missing_type_is_treated_as_unpublishable() -> None:
    """FAILS SAFE toward the channel's own rule: a type we cannot prove
    publishable must not outrank one we can. Guessing the other way would put an
    unpublishable signal in a slot a tender needed."""
    sigs = [_sig("x1", ""), _sig("x2", "some_new_type"), _sig("t1", "contract_award")]
    ordered = nm._rank_for_channel_slots(sigs, 1)
    assert ordered[0]["id"] == "t1"


@pytest.mark.asyncio
async def test_the_endpoint_itself_no_longer_truncates_by_recency(monkeypatch) -> None:
    """CALL SITE, not the helper.

    A mutation run proved this necessary: with `_rank_for_channel_slots` correct
    but `get_recent_intel_signals` still truncating DURING collection, every
    helper test above still passed. Truncating first and ranking second ranks
    only the survivors, which is the defect wearing the fix's clothes.

    This drives the real endpoint with the live shape — 21 recent unpublishable
    ahead of 11 older publishable, 20 slots.
    """
    import json

    feed = ([_sig(f"h{i}", "natural_hazard") for i in range(21)]
            + [_sig(f"t{i}", "active_tender") for i in range(11)])

    async def _fake_lrange(_key, _start, _stop):
        return [json.dumps(s) for s in feed]

    async def _fake_poll_state():
        return {"last_success_at": None, "feeds_polled": 43, "feeds_failed": 0}

    monkeypatch.setattr(nm.rs, "lrange", _fake_lrange)
    monkeypatch.setattr(nm, "_read_poll_state", _fake_poll_state)

    out = await nm.get_recent_intel_signals(limit=20, grades="A,B")
    types = [s.get("signal_type") for s in out.get("signals") or []]
    assert "active_tender" in types, (
        "the endpoint still truncates by recency, so publishable signals are "
        "evicted before ranking ever sees them")
    assert types.count("active_tender") == 11


def test_a_broken_allowed_set_never_reorders_destructively(monkeypatch) -> None:
    """If the allowed-type set cannot be resolved (import failure, refactor), the
    honest fallback is the previous behaviour — recency — never an ordering built
    on an empty set, which would rank EVERYTHING unpublishable and be arbitrary."""
    monkeypatch.setattr(nm, "_channel_publishable_types", lambda: frozenset())
    sigs = [_sig("a", "active_tender"), _sig("b", "natural_hazard")]
    ordered = nm._rank_for_channel_slots(sigs, 2)
    assert [s["id"] for s in ordered] == ["a", "b"], "order changed on an empty set"
