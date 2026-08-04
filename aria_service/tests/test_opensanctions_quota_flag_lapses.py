"""A monthly quota flag must not outlive the month that resets it.

MEASURED 2026-08-04. /api/aria/sanctions/source/status reported:

    "opensanctions": {
      "quota_exhausted": true,
      "since": "2026-07-31T23:04:22+00:00",
      "operator_action": "upgrade the OpenSanctions plan or wait for the
                          monthly reset - no retry or pacing change can clear this"
    }

Four days after a MONTHLY allowance had rolled over on 08-01. The record was
written with no TTL, nothing re-probed it, and the operator clear path the
docstring promised ("only the operator can clear this one") existed nowhere in
the codebase. The flag could move in exactly one direction: once set, exhausted
forever.

WHAT IT DID AND DID NOT COST. Nothing gated on it — grep showed the key was read
only by the status endpoint, never by the screening path, which always attempts
OpenSanctions and falls back to the local canonical lists. So no screen was ever
blocked by it. What it cost was TRUST IN THE SURFACE: an operator reading
"quota exhausted" reasonably concludes screening is degraded when it is not, and
attributes missing screens to the vendor rather than to the real cause (on run
dd_29368fbb8b3d, three IS-13/IS-13b elections declined on the New DD form).

Same class as this session's other findings — deploy-fly.yml asserting the CI
token was stale after it had been rotated, CLAUDE.md §16 warning a baseline file
was older than it was. A claim that was true when written and never expired.

Failure direction is deliberate: being wrong now means ATTEMPTING a screen and
possibly eating one 429, never skipping a screen that could have run.

NOTE: no R-number — data/r_number_reservations.json is the peer agent's ledger.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_service.intel import sanctions as S


def test_next_month_start_rolls_the_year():
    assert S._next_month_start_utc(datetime(2026, 12, 9, tzinfo=timezone.utc)) == \
        datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_next_month_start_is_the_first_at_midnight_utc():
    assert S._next_month_start_utc(datetime(2026, 7, 31, 23, 4, 22, tzinfo=timezone.utc)) == \
        datetime(2026, 8, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_an_elapsed_record_reports_NOT_exhausted(monkeypatch):
    """The exact live record: exhausted 2026-07-31, read on 2026-08-04."""
    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")

    async def _get(_key):
        return {"since": "2026-07-31T23:04:22+00:00", "expires_at": past,
                "detail": "rate limit for the month"}

    import aria_service.intel.redis_store as _rs
    monkeypatch.setattr(_rs, "get_json", _get)
    out = await S.get_opensanctions_quota_state()
    assert out["exhausted"] is False, (
        "a monthly allowance cannot still be spent past the boundary that resets "
        f"it; got {out}"
    )
    assert out.get("lapsed_at") == past, "the lapse must be stated, not silent"


@pytest.mark.asyncio
async def test_an_unexpired_record_still_reports_exhausted(monkeypatch):
    """Guard against over-correction — a genuinely current record must stand."""
    future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(timespec="seconds")

    async def _get(_key):
        return {"since": "now", "expires_at": future, "detail": "spent"}

    import aria_service.intel.redis_store as _rs
    monkeypatch.setattr(_rs, "get_json", _get)
    out = await S.get_opensanctions_quota_state()
    assert out["exhausted"] is True


@pytest.mark.asyncio
async def test_a_legacy_record_without_expiry_still_reports_exhausted(monkeypatch):
    """Back-compat: records written before expires_at existed keep their meaning.

    They are the ones that could hang forever, but silently flipping them to
    'fine' would be inventing a reset nobody observed. They now carry a TTL when
    rewritten, and the operator has clear_opensanctions_quota_state() meanwhile.
    """
    async def _get(_key):
        return {"since": "2026-07-31T23:04:22+00:00", "detail": "spent"}

    import aria_service.intel.redis_store as _rs
    monkeypatch.setattr(_rs, "get_json", _get)
    out = await S.get_opensanctions_quota_state()
    assert out["exhausted"] is True


@pytest.mark.asyncio
async def test_no_record_is_not_exhausted(monkeypatch):
    async def _get(_key):
        return None

    import aria_service.intel.redis_store as _rs
    monkeypatch.setattr(_rs, "get_json", _get)
    assert (await S.get_opensanctions_quota_state())["exhausted"] is False


@pytest.mark.asyncio
async def test_the_operator_clear_path_exists_and_works(monkeypatch):
    """The old docstring promised one and the codebase had none."""
    seen = {}

    async def _del(key):
        seen["key"] = key
        return True

    import aria_service.intel.redis_store as _rs
    monkeypatch.setattr(_rs, "delete", _del)
    assert await S.clear_opensanctions_quota_state() is True
    assert seen["key"] == S._QUOTA_STATE_KEY


@pytest.mark.asyncio
async def test_recording_sets_an_expiry_at_the_month_boundary(monkeypatch):
    captured = {}

    async def _set(key, obj, ex=None, keepttl=False):
        captured.update({"key": key, "obj": obj, "ex": ex})

    import aria_service.intel.redis_store as _rs
    monkeypatch.setattr(_rs, "set_json", _set)
    await S._record_quota_exhausted("exceeded its rate limit for the month")

    assert captured["ex"] and captured["ex"] > 0, "must carry a TTL, not live forever"
    exp = datetime.fromisoformat(captured["obj"]["expires_at"])
    assert exp == S._next_month_start_utc(), "expiry must be the monthly reset boundary"
