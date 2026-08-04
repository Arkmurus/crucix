"""R-F3675 — a feed that parses cleanly and delivers NOTHING was dark to the brain.

Found while auditing §21 wiring across the news pipeline. ``_note_feed_success``
was called for every feed that fetched and parsed, INCLUDING one carrying zero
items, and it cleared the failure streak — so a feed publishing an empty channel
forever was actively recorded as healthy (``fails=0``), counted in the live
source denominator, and reached the brain through no path whatsoever.

Two existing mechanisms each cover a DIFFERENT feed state and neither covers this
one: R-F2214 wires a vault source that returns nothing, and R-F2890 quarantines
and wires a curated feed that FAILS six times. A curated feed that succeeds and
is empty fell between them.

Measured live 2026-08-04: Hurriyet Daily News, O Globo Brazil and UK Defence
Journal Tech all parsed clean, returned 0 items on every poll, and had ``fails=0``
recorded against each. R-F3674 put them on the page; this puts them in front of
the self-heal loop (§21d, §21e).

All tests confirmed FAILING against the pre-fix code (§3c) — pre-fix
``_note_feed_success`` takes no ``delivered`` argument and never wires.
"""

from unittest.mock import MagicMock, patch

import pytest

from aria_service.intel import news_monitor as nm


URL = "https://hurriyetdailynews.example/rss"
NAME = "Hurriyet Daily News"


def _silence(health, times, wire):
    with patch.object(nm, "wire_failure", wire):
        for _ in range(times):
            nm._note_feed_success(health, URL, NAME, delivered=False)


def test_rf3675_a_silent_feed_reaches_the_brain():
    """THE DEFECT: parsed, empty, forever — and nothing could see it."""
    health, wire = {}, MagicMock()
    _silence(health, nm._CURATED_EMPTY_AFTER, wire)

    assert wire.call_count == 1, "a persistently empty feed must wire a gap"
    kwargs = wire.call_args.kwargs
    assert kwargs["module"] == "news_monitor"
    assert kwargs["gap_type"] == "source_failure"
    assert NAME in kwargs["detail"], "the gap must NAME the feed, not report a ratio"
    assert NAME in kwargs["source"]


def test_rf3675_silence_is_ridden_out_below_the_threshold():
    """A quiet publisher is not a broken one — no alarm before the threshold."""
    health, wire = {}, MagicMock()
    _silence(health, nm._CURATED_EMPTY_AFTER - 1, wire)
    assert wire.call_count == 0
    assert health[URL]["empty_polls"] == nm._CURATED_EMPTY_AFTER - 1


def test_rf3675_wires_once_per_episode_not_every_poll():
    """A dead feed must not flood the gap ledger on every hourly poll."""
    health, wire = {}, MagicMock()
    _silence(health, nm._CURATED_EMPTY_AFTER + 20, wire)
    assert wire.call_count == 1, f"wired {wire.call_count} times; must be exactly once"


def test_rf3675_recovery_resets_and_re_arms():
    """A feed that recovers must clear, and a LATER silence must wire again."""
    health, wire = {}, MagicMock()
    _silence(health, nm._CURATED_EMPTY_AFTER, wire)
    assert wire.call_count == 1

    nm._note_feed_success(health, URL, NAME, delivered=True)
    assert health[URL]["empty_polls"] == 0
    assert health[URL]["empty_wired"] is False, "must re-arm, or a second outage is silent"
    assert health[URL].get("last_delivered")

    _silence(health, nm._CURATED_EMPTY_AFTER, wire)
    assert wire.call_count == 2, "a second episode of silence must wire again"


def test_rf3675_a_delivering_feed_never_wires():
    health, wire = {}, MagicMock()
    with patch.object(nm, "wire_failure", wire):
        for _ in range(50):
            nm._note_feed_success(health, URL, NAME, delivered=True)
    assert wire.call_count == 0
    assert health[URL]["empty_polls"] == 0


def test_rf3675_parse_is_still_proof_of_life():
    """REGRESSION GUARD (R-F2890): an empty feed must NOT be quarantined, and a
    delivering feed must still clear a failure streak and any quarantine."""
    health = {URL: {"name": NAME, "fails": 5, "quarantined_until": 9e9}}
    nm._note_feed_success(health, URL, NAME, delivered=True)
    assert health[URL]["fails"] == 0
    assert health[URL]["quarantined_until"] == 0

    # Empty, but answering: still not a fetch failure, so still not quarantined.
    health2 = {}
    with patch.object(nm, "wire_failure", MagicMock()):
        for _ in range(nm._CURATED_EMPTY_AFTER + 5):
            nm._note_feed_success(health2, URL, NAME, delivered=False)
    assert not health2[URL].get("quarantined_until"), (
        "an empty feed must stay pollable — re-probing is how recovery is noticed"
    )


def test_rf3675_unreadable_health_is_still_a_no_op():
    """REGRESSION GUARD: health=None means the store could not be read, so
    nothing may be written (the non-strict-read clobber class)."""
    with patch.object(nm, "wire_failure", MagicMock()) as wire:
        nm._note_feed_success(None, URL, NAME, delivered=False)
        assert wire.call_count == 0


def test_rf3675_default_is_delivered_so_existing_callers_are_unchanged():
    """The kwarg is optional; a caller that does not pass it keeps old behaviour."""
    health = {URL: {"name": NAME, "fails": 3}}
    nm._note_feed_success(health, URL, NAME)
    assert health[URL]["fails"] == 0
    assert health[URL]["empty_polls"] == 0


@pytest.mark.asyncio
async def test_rf3675_capability_empty_channel_through_the_real_poll_path():
    """Drive poll_feeds itself: a feed answering with an empty channel must end
    up flagged, not recorded as a healthy source."""
    from unittest.mock import AsyncMock

    source = (NAME, URL, "regional_news", "en", "tier_2", ["news"])
    empty_rss = ('<?xml version="1.0"?><rss version="2.0"><channel>'
                 '<title>Empty</title></channel></rss>')
    health: dict = {}
    wire = MagicMock()

    from aria_service.intel import golden_intel_bridge

    with (
        patch.object(nm, "NEWS_SOURCES", [source]),
        patch.object(nm, "_get_vault_feed_sources", MagicMock(return_value=[])),
        patch.object(nm, "_fetch_feed", AsyncMock(return_value=empty_rss)),
        patch.object(nm, "_load_feed_health", AsyncMock(return_value=health)),
        patch.object(nm, "_save_feed_health", AsyncMock()),
        patch.object(nm, "_read_poll_state", AsyncMock(return_value={})),
        patch.object(nm, "_write_poll_state", AsyncMock(return_value={"status": "fresh"})),
        patch.object(nm, "wire_failure", wire),
        patch.object(golden_intel_bridge, "run_promotion_pass",
                     AsyncMock(return_value={"promoted": 0})),
    ):
        for _ in range(nm._CURATED_EMPTY_AFTER):
            result = await nm.poll_feeds()

    assert result["articles_fetched"] == 0
    # The feed answered, so it is NOT counted as a failure...
    assert result["feeds_failed"] == 0
    # ...but its silence is no longer invisible.
    assert health[URL]["empty_polls"] == nm._CURATED_EMPTY_AFTER
    assert any(NAME in str(c.kwargs.get("detail", "")) for c in wire.call_args_list), (
        "the real poll path must wire the silent feed to the brain"
    )
