"""R-F2636 — a SCOPED poll erased the full-poll health signal (false-fresh).

THE BUG (observed live 2026-07-15):
  Full 76-feed poll : feeds_polled=76 feeds_failed=42 ratio=0.55 -> "source_failure_degraded"
  Then a 3-feed poll: feeds_polled=3  feeds_failed=0  ratio=0.00 -> stale_reasons=[] => "fresh"

  routes/aria.py:26749 calls `poll_feeds(categories=cats)` — a SUBSET poll. Its summary
  overwrote poll_state wholesale, so 3 clean feeds ERASED the fact that 42 of 76 sources
  are dead. The dashboard then reported a healthy feed while 73 sources went unpolled.

  That is a false-clean of the observability surface — same class as R-F2621 (GREEN
  default emitted as a verdict), R-F2625 (dark metric read as "no failures") and
  R-F2622 (gate #3 certified on an empty ledger).

THE RULE (honest): only a FULL poll may refresh the feed's health + freshness.
A scoped poll is a targeted operation, NOT a feed refresh — it records itself
separately and must not touch last_success_at / feeds_polled / feeds_failed / results.
"""
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import news_monitor as nm


_FULL_STATE = {
    "status": "degraded",
    "last_poll_at": "2026-07-15T11:23:02+00:00",
    "last_success_at": "2026-07-15T11:23:02+00:00",
    "feeds_polled": 76,
    "feeds_failed": 42,
    "results": [{"name": "Janes Defence", "status": "error", "error": "404"}],
}


async def _write(summary, existing):
    captured = {}

    async def _fake_set_json(key, value, **kw):
        captured["state"] = value

    with patch.object(nm.rs, "set_json", AsyncMock(side_effect=_fake_set_json)), \
         patch.object(nm, "_read_poll_state", AsyncMock(return_value=dict(existing))):
        await nm._write_poll_state(summary)
    return captured.get("state") or {}


async def test_rf2636_scoped_poll_does_not_erase_full_poll_health():
    """THE CAPABILITY TEST — the live symptom.

    A 3-feed scoped poll must NOT wipe the 76/42 health recorded by the last full poll.
    """
    state = await _write({
        "feeds_polled": 3, "feeds_failed": 0, "articles_fetched": 130,
        "articles_new": 4, "signals_promoted": 4, "results": [],
        "scope": "filtered", "polled_at": "2026-07-15T15:32:20+00:00",
    }, _FULL_STATE)

    assert state.get("feeds_polled") == 76, (
        f"a 3-feed scoped poll overwrote the full-poll feeds_polled -> the 55%-dead "
        f"signal is ERASED and the dashboard reads 'fresh': {state.get('feeds_polled')}"
    )
    assert state.get("feeds_failed") == 42, (
        f"scoped poll erased feeds_failed -> source_failure_degraded disappears: "
        f"{state.get('feeds_failed')}"
    )
    assert state.get("results"), "scoped poll erased the failed-feed names (R-F2630)"


async def test_rf2636_scoped_poll_does_not_fake_freshness():
    """A subset poll must not clear poll_stale for the WHOLE feed.

    73 of 76 sources were not polled — advancing last_success_at would claim a feed
    refresh that never happened.
    """
    state = await _write({
        "feeds_polled": 3, "feeds_failed": 0, "articles_fetched": 130,
        "articles_new": 4, "signals_promoted": 4, "results": [],
        "scope": "filtered", "polled_at": "2026-07-15T15:32:20+00:00",
    }, _FULL_STATE)

    assert state.get("last_success_at") == _FULL_STATE["last_success_at"], (
        "a 3-feed scoped poll advanced last_success_at -> the whole feed reads FRESH "
        "while 73 sources went unpolled"
    )


async def test_rf2636_scoped_poll_is_still_recorded():
    """Honesty cuts both ways: the scoped poll DID happen and must be visible —
    just not as a full-feed refresh."""
    state = await _write({
        "feeds_polled": 3, "feeds_failed": 0, "articles_fetched": 130,
        "articles_new": 4, "signals_promoted": 4, "results": [],
        "scope": "filtered", "polled_at": "2026-07-15T15:32:20+00:00",
    }, _FULL_STATE)

    assert state.get("last_filtered_poll_at") == "2026-07-15T15:32:20+00:00", (
        f"the scoped poll must still be recorded (separately): {state}"
    )


async def test_rf2636_full_poll_still_refreshes_everything():
    """NON-REGRESSION: a FULL poll must still update health AND freshness —
    otherwise the fix would freeze the feed permanently (worse than the bug)."""
    state = await _write({
        "feeds_polled": 76, "feeds_failed": 30, "articles_fetched": 800,
        "articles_new": 20, "signals_promoted": 20,
        "results": [{"name": "X", "status": "ok"}],
        "scope": "full", "polled_at": "2026-07-15T16:00:00+00:00",
    }, _FULL_STATE)

    assert state.get("feeds_polled") == 76 and state.get("feeds_failed") == 30, (
        f"a FULL poll must refresh health: {state}"
    )
    assert state.get("last_success_at") == "2026-07-15T16:00:00+00:00", (
        f"a FULL poll must advance last_success_at: {state.get('last_success_at')}"
    )


async def test_rf2636_missing_scope_defaults_to_full():
    """Back-compat: callers that omit `scope` (the autonomous task, the boot loop)
    are FULL polls and must keep working exactly as before."""
    state = await _write({
        "feeds_polled": 76, "feeds_failed": 10, "articles_fetched": 500,
        "articles_new": 5, "signals_promoted": 5, "results": [],
        "polled_at": "2026-07-15T16:05:00+00:00",
    }, _FULL_STATE)

    assert state.get("last_success_at") == "2026-07-15T16:05:00+00:00", (
        "a poll without an explicit scope must behave as FULL (back-compat)"
    )
    assert state.get("feeds_failed") == 10
