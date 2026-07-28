"""R-F3362 — the operational-gaps surface served months-old signals as "real-time".

THE DEFECT. `/api/aria/metacognitive/operational-gaps` documents itself as
"real-time gap signals" and `get_operational_gaps()` says "Return recent
operational gap signals". Neither is enforced: the function does

    raw = await rs.lrange(_OPERATIONAL_GAPS_LIST, 0, limit - 1)
    return _safe_json_list(raw)

— the newest N entries in a list, which is only "recent" if something has been
written recently. Measured live 2026-07-28 the top three entries were dated
2026-05-21, 2026-05-20 and 2026-05-15: over two months old, presented with no
age whatsoever, to an operator and an autonomous coder that both treat this as a
live work queue.

Same class as the error ledger reporting `window_hours: 168` while physically
retaining 6.4h — a surface asserting a recency it does not measure.

THE FIX does not filter by default, because silently dropping a still-open gap
would trade one dishonesty for another. It MEASURES: every gap carries
`age_days` and `stale`, the summary carries `stale_count` and `newest_age_days`,
and a caller that wants a real window can ask for one via `max_age_days`.
A gap with an unparseable or missing timestamp reports `age_days: None` /
`stale: None` — unknown age is not fresh.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.metacognitive import gaps as g


def _run(coro):
    return asyncio.run(coro)


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _feed(entries):
    return patch.object(
        g.rs, "lrange", new=AsyncMock(return_value=[json.dumps(e) for e in entries])
    )


OLD = {"type": "RESEARCH_FAILURE", "detected_at": _iso(68), "search_query": "old"}
FRESH = {"type": "RESEARCH_FAILURE", "detected_at": _iso(0.5), "search_query": "new"}
UNDATED = {"type": "RESEARCH_FAILURE", "search_query": "no timestamp"}
JUNK = {"type": "RESEARCH_FAILURE", "detected_at": "not-a-date", "search_query": "junk"}


# ── every gap must carry its age ────────────────────────────────────────────

def test_gaps_carry_age_days():
    with _feed([FRESH, OLD]):
        out = _run(g.get_operational_gaps())
    assert out[0]["age_days"] == pytest.approx(0.5, abs=0.2)
    assert out[1]["age_days"] == pytest.approx(68, abs=1)


def test_old_gap_is_marked_stale_and_fresh_one_is_not():
    with _feed([FRESH, OLD]):
        out = _run(g.get_operational_gaps())
    assert out[0]["stale"] is False
    assert out[1]["stale"] is True, (
        "a 68-day-old gap was served as current to the operator and the coder"
    )


def test_unknown_age_is_not_reported_as_fresh():
    """Absent is not false: no timestamp means UNKNOWN age, never 'fresh'."""
    with _feed([UNDATED, JUNK]):
        out = _run(g.get_operational_gaps())
    for row in out:
        assert row["age_days"] is None
        assert row["stale"] is None, "unparseable timestamp was scored as fresh"


def test_stale_threshold_is_declared():
    assert isinstance(g.OPERATIONAL_GAP_STALE_DAYS, (int, float))
    assert g.OPERATIONAL_GAP_STALE_DAYS > 0


# ── the default must not hide anything ──────────────────────────────────────

def test_nothing_is_filtered_by_default():
    """Dropping a still-open gap would trade one dishonesty for another."""
    with _feed([FRESH, OLD, UNDATED]):
        out = _run(g.get_operational_gaps())
    assert len(out) == 3


def test_caller_can_request_a_real_window():
    with _feed([FRESH, OLD, UNDATED]):
        out = _run(g.get_operational_gaps(max_age_days=30))
    kinds = [r["search_query"] for r in out]
    assert "new" in kinds
    assert "old" not in kinds, "max_age_days did not exclude the 68-day-old gap"


def test_unknown_age_survives_a_window_request():
    """An unknown-age gap must not be silently dropped by a freshness filter —
    that would delete evidence on the strength of a missing field."""
    with _feed([OLD, UNDATED]):
        out = _run(g.get_operational_gaps(max_age_days=30))
    assert any(r["search_query"] == "no timestamp" for r in out)


# ── the summary must surface staleness ──────────────────────────────────────

def test_summary_reports_staleness():
    with _feed([FRESH, OLD, OLD]):
        s = _run(g.get_operational_gap_summary())
    assert s["total"] == 3
    assert s["stale_count"] == 2, s
    assert s["newest_age_days"] == pytest.approx(0.5, abs=0.2)


def test_summary_of_an_all_stale_queue_says_so():
    with _feed([OLD, OLD]):
        s = _run(g.get_operational_gap_summary())
    assert s["stale_count"] == s["total"] == 2
    assert s.get("all_stale") is True, (
        "a queue where every entry is months old should say so outright"
    )


def test_empty_queue_is_not_reported_as_stale():
    with _feed([]):
        s = _run(g.get_operational_gap_summary())
    assert s["total"] == 0
    assert s.get("all_stale") is False
    assert s["newest_age_days"] is None
