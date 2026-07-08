"""R-F2505 — bulk brain-signal ingest. The web sweep posts ONE payload to
/brain/signal/bulk instead of N concurrent /brain/signal posts, and each signal is
routed by the SHARED _route_one_signal helper (content -> brain absorb, failure ->
capability_gap). These test the shared router (the bulk endpoint reuses it sequentially).
"""
import asyncio
import aria_service.intel.brain_hook as bh
import aria_service.intel.capability_gaps as cg
from aria_service.routes.aria import _route_one_signal


def test_content_signal_routes_to_absorb():
    calls = []
    orig = bh.absorb
    async def fake(**kw): calls.append(kw)
    bh.absorb = fake
    try:
        asyncio.run(_route_one_signal("hello world", "briefing:x", "crucix_briefing_signal", {}))
    finally:
        bh.absorb = orig
    assert len(calls) == 1, calls
    assert calls[0]["module"] == "cross_tier:crucix_briefing_signal"
    assert calls[0]["summary"] == "hello world"


def test_failure_signal_routes_to_gap():
    gaps = []
    orig = cg.record_gap
    async def fake(**kw): gaps.append(kw)
    cg.record_gap = fake
    try:
        asyncio.run(_route_one_signal("timeout hit", "web", "wa_chat_failed", {}))
    finally:
        cg.record_gap = orig
    assert len(gaps) == 1, gaps
    assert gaps[0]["gap_type"] == "operational:output_rejection"


def test_never_raises_on_bad_metadata():
    orig = bh.absorb
    async def fake(**kw): pass
    bh.absorb = fake
    try:
        asyncio.run(_route_one_signal("x", "s", "sig", None))  # None metadata must not crash
    finally:
        bh.absorb = orig


if __name__ == "__main__":
    test_content_signal_routes_to_absorb(); print("PASS content->absorb")
    test_failure_signal_routes_to_gap(); print("PASS failure->gap")
    test_never_raises_on_bad_metadata(); print("PASS bad-metadata")
    print("ALL PASS")
