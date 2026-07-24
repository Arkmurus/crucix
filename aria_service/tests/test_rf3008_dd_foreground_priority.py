"""R-F3008 — foreground priority: a user DD must not be starved of the event loop
by the 6-hourly background crawler sweep.

Live defect: a fired DD made ZERO external calls for ~25 min while the crawler
saturated the app. Now an in-process in-flight-DD gauge lets the crawler yield to
any running DD (slow the crawl, never stop it), and orchestrate_dd inc/dec the
gauge on every exit path.
"""
from pathlib import Path

from aria_service.intel import dd_orchestrator as ddo

_DDO_SRC = (Path(__file__).resolve().parent.parent / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8")
_RUNNER_SRC = (Path(__file__).resolve().parent.parent.parent / "aria_service" / "crawler" / "runner.py").read_text(encoding="utf-8")


def test_rf3008_gauge_inc_dec_and_floor():
    start = ddo.dd_inflight_count()
    try:
        ddo._dd_inflight_inc()
        ddo._dd_inflight_inc()
        assert ddo.dd_inflight_count() == start + 2
        ddo._dd_inflight_dec()
        assert ddo.dd_inflight_count() == start + 1
        ddo._dd_inflight_dec()
        assert ddo.dd_inflight_count() == start
        ddo._dd_inflight_dec()  # never goes negative
        assert ddo.dd_inflight_count() == 0
    finally:
        # leave the process gauge at 0 for other tests
        while ddo.dd_inflight_count() > 0:
            ddo._dd_inflight_dec()


def test_rf3008_orchestrate_dd_wraps_impl_in_the_gauge():
    i = _DDO_SRC.index("async def orchestrate_dd(")
    j = _DDO_SRC.index("async def _orchestrate_dd_impl(", i)
    body = _DDO_SRC[i:j]
    assert "_dd_inflight_inc()" in body, "orchestrate_dd must increment the gauge"
    assert "_dd_inflight_dec()" in body, "orchestrate_dd must decrement the gauge"
    # inc (inside try) must precede dec (inside the always-run finally)
    assert body.index("_dd_inflight_inc()") < body.index("_dd_inflight_dec()")
    # the dec lives in the finally so it releases on success, timeout AND exception
    fin = body.index("finally:")
    assert body.index("_dd_inflight_dec()") > fin


def test_rf3008_crawler_yields_to_inflight_dd():
    assert "dd_inflight_count" in _RUNNER_SRC, "crawler must consult the in-flight-DD gauge"
    assert "R-F3008" in _RUNNER_SRC
    # in the sweep loop, it pauses when a DD is in-flight (slows, never stops)
    assert "_dd_inflight() > 0" in _RUNNER_SRC and "asyncio.sleep" in _RUNNER_SRC
