"""R-F605 — Purge poisoned reasoning_library capability-overview cases.

Past incident 2026-05-16: operator asked the same capability-overview
question three times. ARIA's first reply (hallucinated content: "34
autonomous tasks", "48/49 sources", "18-month TTL", "5,000-10,000
verified facts") was absorbed into the reasoning_library and
subsequently re-served from cache 3× even after R-F593/F594/F595
shipped. R-F599 stops new capability questions hitting the cache;
R-F605 deletes the EXISTING poisoned entries so they cannot
resurface via semantically-adjacent queries.

This file pins:
  - the needle list contains all known bad phrasings
  - purge_capability_hallucinations() returns the right shape
  - the wrapper delegates to purge_by_keywords with the needles
  - module-level constant is accessible (allowlist transparency)
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock


def test_rf605_needle_list_includes_observed_hallucinations():
    """Every exact-quote substring from the 2026-05-16 incident must
    be in the needle list."""
    from aria_service.intel.reasoning_library import (
        _CAPABILITY_HALLUCINATION_NEEDLES,
    )
    needles_lc = [n.lower() for n in _CAPABILITY_HALLUCINATION_NEEDLES]
    must_have = (
        "34 autonomous tasks",
        "48/49 sources",
        "18-month ttl",
        "5,000-10,000 verified facts",
        "no uk ofsi direct access",
        "i cannot query ofsi",
        "no outbound email capability",
        "no corporate registry direct adapters",
    )
    for phrase in must_have:
        assert phrase in needles_lc, (
            f"R-F605: needle list missing observed hallucination {phrase!r}"
        )


def test_rf605_needle_list_includes_openclaw_legacy():
    """The OpenClaw needle list from the 2026-04-24 incident must remain
    in the purge needles so historic fabrications stay purged."""
    from aria_service.intel.reasoning_library import (
        _CAPABILITY_HALLUCINATION_NEEDLES,
    )
    needles_lc = [n.lower() for n in _CAPABILITY_HALLUCINATION_NEEDLES]
    assert "openclaw" in needles_lc


def test_rf605_purge_returns_purge_by_keywords_shape():
    """purge_capability_hallucinations() must return the same shape as
    purge_by_keywords(): scanned, removed, dry_run, keywords_used,
    removed_samples."""
    from aria_service.intel import reasoning_library as rl

    async def _stub_purge_by_keywords(needles, *, dry_run=False):
        return {
            "scanned": 100,
            "removed": 5,
            "dry_run": dry_run,
            "keywords_used": needles,
            "removed_samples": [{"id": "x"}],
        }

    with patch.object(
        rl, "purge_by_keywords", side_effect=_stub_purge_by_keywords
    ):
        result = asyncio.run(rl.purge_capability_hallucinations(dry_run=True))

    assert set(result.keys()) == {
        "scanned", "removed", "dry_run", "keywords_used", "removed_samples"
    }
    assert result["scanned"] == 100
    assert result["removed"] == 5
    assert result["dry_run"] is True


def test_rf605_purge_delegates_with_full_needle_list():
    """The wrapper must pass the full needle list (no slicing / missed
    phrases) to purge_by_keywords."""
    from aria_service.intel import reasoning_library as rl
    from aria_service.intel.reasoning_library import (
        _CAPABILITY_HALLUCINATION_NEEDLES,
    )

    captured_needles: list[list[str]] = []

    async def _capture(needles, *, dry_run=False):
        captured_needles.append(list(needles))
        return {
            "scanned": 0, "removed": 0, "dry_run": dry_run,
            "keywords_used": needles, "removed_samples": [],
        }

    with patch.object(rl, "purge_by_keywords", side_effect=_capture):
        asyncio.run(rl.purge_capability_hallucinations(dry_run=False))

    assert len(captured_needles) == 1
    passed = captured_needles[0]
    assert len(passed) == len(_CAPABILITY_HALLUCINATION_NEEDLES)
    for expected in _CAPABILITY_HALLUCINATION_NEEDLES:
        assert expected in passed, (
            f"R-F605: wrapper dropped needle {expected!r} when calling purge"
        )


def test_rf605_dry_run_flag_propagates():
    """dry_run=True must reach purge_by_keywords unchanged."""
    from aria_service.intel import reasoning_library as rl

    received_dry: list[bool] = []

    async def _capture(needles, *, dry_run=False):
        received_dry.append(dry_run)
        return {
            "scanned": 0, "removed": 0, "dry_run": dry_run,
            "keywords_used": needles, "removed_samples": [],
        }

    with patch.object(rl, "purge_by_keywords", side_effect=_capture):
        asyncio.run(rl.purge_capability_hallucinations(dry_run=True))
        asyncio.run(rl.purge_capability_hallucinations(dry_run=False))

    assert received_dry == [True, False]


def test_rf605_public_constant_is_tuple():
    """The needle list is a tuple (immutable) so a downstream caller
    can't accidentally mutate it."""
    from aria_service.intel.reasoning_library import (
        _CAPABILITY_HALLUCINATION_NEEDLES,
    )
    assert isinstance(_CAPABILITY_HALLUCINATION_NEEDLES, tuple)
