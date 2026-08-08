"""R-F2630 — Golden Intel was permanently dead: the poll's TAIL never ran.

THE BUG (all three verified live 2026-07-15 on aria-intel v2439):
  poll_feeds() is SEQUENTIAL over 76 feeds with a 15s per-feed timeout. 42 of
  76 feeds were failing (55%, vs a 15% budget), so the feed loop alone needed
  ~630s of timeouts. main._news_poll_once wraps the whole thing in
  `asyncio.wait_for(poll_feeds(), timeout=330)` (main.py:519).

  poll_feeds does its two most valuable operations LAST:
      :1345  _write_poll_state(summary)              -> freshness heartbeat
      :1366  golden_intel_bridge.run_promotion_pass() -> sets distribution_ready
  So the 330s cap ALWAYS killed the tail:
    * last_poll_at froze at 11:23 forever -> freshness "poll_stale" forever
      (proven: frozen through 2 boots + 1 catch-up fire = 3 attempts, 0 writes;
       last_error_at:None corroborates — _write_poll_state is the ONLY writer)
    * the promotion bridge never ran -> the promoted-signal store stayed empty
      -> the API fell back to _backfill_intel_signals_from_articles, whose
      signals carry NO distribution_ready field -> the dashboard's
      "Distribution Ready" column read 0 PERMANENTLY, even though 8 backfilled
      signals scored >= _CUSTOMER_VALUE_DISTRIBUTION_MIN (70): 82,82,82,77,76,76,70,70.

  SELF-REINFORCING: poll duration scales with FAILURES, so as sources rot the
  poll crosses its own cap and can never recover. Raising 330->900 is the §1
  band-aid — it fails again at the next rot. The fix is the R-F1879 pattern
  already proven in dd_orchestrator: budget the loop to (budget - tail_reserve)
  so the TAIL ALWAYS RUNS, and mark the run truncated (honest, per R-F1572).

PLUS a §3b bug that hid it: news_monitor.py:1325 called
    wire_failure(module=..., summary=..., detail=..., source_id=...)
but wire_failure takes (module, detail, gap_type, source) -> TypeError, swallowed
by the surrounding `except: pass`. Tagged "R-F1057 — wire failure to brain so
ARIA sees it"; she never did. Third instance today of wrong-call + broad-except
(cf. R-F2486 hget, R-F2625 hincrby).

PLUS _write_poll_state dropped `results`, so failed_feeds was always [] and the
dead sources could not even be NAMED.
"""
import inspect
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel import news_monitor as nm
from aria_service.intel.engine_wiring import wire_failure

# R-F3781/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


async def test_rf2630_wire_failure_call_signature_is_valid():
    """§3b — the exact call at news_monitor.py:1325 must actually bind.

    Before the fix this raised TypeError into an `except: pass`, so all 42 feed
    failures per poll were DARK.
    """
    sig = inspect.signature(wire_failure)
    try:
        sig.bind(
            module="news_monitor",
            detail="boom",
            gap_type="source_failure",
            source="news_monitor:feed:X",
        )
    except TypeError as e:
        pytest.fail(f"the corrected wire_failure call does not bind: {e}")

    # And the OLD kwargs must NOT be what the code uses any more.
    src = module_source(nm)
    assert "summary=f\"Feed poll failed" not in src, (
        "news_monitor still passes summary=/source_id= to wire_failure — that "
        "raises TypeError into `except: pass`, so every feed failure stays DARK"
    )


async def test_rf2630_write_poll_state_persists_failed_feed_names():
    """The operator must be able to NAME the dead feeds.

    _write_poll_state dropped `results`, so the freshness reader's
    `poll_state.get("results")` was always empty -> failed_feeds: [] despite
    feeds_failed: 42.
    """
    captured = {}

    async def _fake_set_json(key, value, **kw):
        captured["state"] = value

    with patch.object(nm.rs, "set_json", AsyncMock(side_effect=_fake_set_json)), \
         patch.object(nm, "_read_poll_state", AsyncMock(return_value={})):
        await nm._write_poll_state({
            "feeds_polled": 3,
            "feeds_failed": 2,
            "articles_fetched": 10,
            "articles_new": 1,
            "signals_promoted": 1,
            "results": [
                {"name": "DeadFeedA", "status": "error", "error": "timeout"},
                {"name": "DeadFeedB", "status": "failed"},
                {"name": "GoodFeed", "status": "ok"},
            ],
            "polled_at": "2026-07-15T15:00:00+00:00",
        })

    state = captured.get("state") or {}
    assert "results" in state, (
        "poll state dropped `results` — the freshness reader builds failed_feeds "
        "from it, so the dead sources can never be named (live: failed_feeds=[] "
        "while feeds_failed=42)"
    )
    names = [r.get("name") for r in state["results"] if str(r.get("status")) in ("error", "failed")]
    assert "DeadFeedA" in names and "DeadFeedB" in names, f"failed feeds not persisted: {state.get('results')}"


async def test_rf2630_tail_runs_even_when_feed_loop_exceeds_budget():
    """THE CAPABILITY TEST — the operator-visible symptom.

    With a feed loop that blows the budget, poll_feeds MUST still:
      (a) write poll state  -> last_poll_at advances instead of freezing, and
      (b) run the promotion bridge -> distribution_ready gets computed.
    Before the fix both were unreachable because they sit after the loop.
    """
    slow_sources = [(f"Feed{i}", f"https://example.invalid/{i}.xml", "defence", "en", 2, ["x"])
                    for i in range(40)]

    async def _slow_fetch(url, name):
        # Simulate a failing/hanging feed burning its per-feed timeout.
        time.sleep(0)          # keep it cheap; the budget is what we shrink
        import asyncio
        await asyncio.sleep(0.05)
        return None            # None => counted as a failed feed

    wrote = {}
    bridge_ran = {"n": 0}

    async def _fake_write(summary):
        wrote["summary"] = summary
        return {"status": "degraded"}

    async def _fake_bridge_pass():
        bridge_ran["n"] += 1
        return {"promoted": 1}

    # NOTE: patch the REAL module's attribute, not sys.modules. poll_feeds does
    # `from . import golden_intel_bridge`, which resolves via the already-imported
    # package attribute — so patch.dict("sys.modules", ...) silently does nothing
    # once any other test has imported the bridge (passes alone, fails in a full run).
    from aria_service.intel import golden_intel_bridge as _gib

    with patch.object(nm, "NEWS_SOURCES", slow_sources), \
         patch.object(nm, "_get_vault_feed_sources", MagicMock(return_value=[])), \
         patch.object(nm, "_fetch_feed", AsyncMock(side_effect=_slow_fetch)), \
         patch.object(nm, "_read_poll_state", AsyncMock(return_value={})), \
         patch.object(nm, "_write_poll_state", AsyncMock(side_effect=_fake_write)), \
         patch.object(_gib, "run_promotion_pass", AsyncMock(side_effect=_fake_bridge_pass)), \
         patch.object(nm, "_POLL_BUDGET_S", 0.5, create=True), \
         patch.object(nm, "_POLL_TAIL_RESERVE_S", 0.2, create=True):
        summary = await nm.poll_feeds()

    assert wrote.get("summary") is not None, (
        "poll_feeds did not write poll state when the loop exceeded budget — "
        "last_poll_at freezes and the feed is poll_stale FOREVER"
    )
    assert bridge_ran["n"] == 1, (
        "the Golden Intel promotion bridge never ran — distribution_ready is "
        "never computed, so the dashboard column stays 0 forever"
    )
    assert summary.get("truncated") is True, (
        "a time-boxed poll must be MARKED truncated (R-F1572 honesty), got: "
        f"{ {k: v for k, v in summary.items() if k != 'results'} }"
    )
    assert summary.get("feeds_polled", 0) < len(slow_sources), (
        "truncation must report the feeds it actually attempted, not the full list"
    )


async def test_rf2630_fast_poll_still_completes_untruncated():
    """NON-REGRESSION: a poll that fits the budget must NOT be marked truncated
    and must still poll every source."""
    sources = [(f"Feed{i}", f"https://example.invalid/{i}.xml", "defence", "en", 2, ["x"])
               for i in range(3)]

    from aria_service.intel import golden_intel_bridge as _gib

    with patch.object(nm, "NEWS_SOURCES", sources), \
         patch.object(nm, "_get_vault_feed_sources", MagicMock(return_value=[])), \
         patch.object(nm, "_fetch_feed", AsyncMock(return_value=None)), \
         patch.object(nm, "_read_poll_state", AsyncMock(return_value={})), \
         patch.object(nm, "_write_poll_state", AsyncMock(return_value={"status": "failed"})), \
         patch.object(_gib, "run_promotion_pass", AsyncMock(return_value={"promoted": 0})):
        summary = await nm.poll_feeds()

    assert not summary.get("truncated"), f"a fast poll must not be truncated: {summary.get('truncated')}"
    assert summary.get("feeds_polled") == 3, f"all sources must be polled: {summary.get('feeds_polled')}"
