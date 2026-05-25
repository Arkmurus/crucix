"""R-F864 — periodic/dated briefs are never cached or served from cache.

Live incident (2026-05-25): a 2026-05-18 "Arkmurus Weekly Intelligence Brief"
was cached in the reasoning library and replayed verbatim for the 05-25 request
— stale date header, stale content, 0 fresh sources. The investigation-request
guard didn't catch "generate the weekly intelligence brief", so it slipped past
the time-sensitivity gate. R-F864 adds a periodic-brief detector used on BOTH
the record (never cache) and the find_match (never serve) sides.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import reasoning_library as rl


def test_detects_periodic_briefs():
    for q in [
        "Generate the Arkmurus Weekly Strategic Intelligence Brief for this Monday",
        "weekly intelligence brief",
        "can you send the daily briefing",
        "give me the situation report",
        "market update for this week",
        "today's brief please",
        "monthly intelligence digest",
    ]:
        assert rl._looks_like_periodic_brief(q.lower()) is True, f"missed: {q}"


def test_ignores_non_periodic_questions():
    for q in [
        "what is the status of the angola deal",
        "who is the defence minister of kenya",
        "analyse this contract's payment structure",
        "what does FMS mean",
    ]:
        assert rl._looks_like_periodic_brief(q.lower()) is False, f"false-positive: {q}"


def test_record_response_skips_periodic_brief():
    r = asyncio.run(rl.record_response(
        "Generate the Arkmurus Weekly Intelligence Brief for this Monday",
        "## ARKMURUS WEEKLY BRIEF\n\nExecutive summary: real content here, long enough.",
    ))
    assert r["recorded"] is False
    assert "R-F864" in r["reason"] or "periodic" in r["reason"].lower()


def test_find_match_skips_periodic_brief():
    r = asyncio.run(rl.find_match("Generate the weekly intelligence brief"))
    assert r["match"] is False
    assert r["method"] == "skipped_periodic_brief"
