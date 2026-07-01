"""R-F2241 — self-heal loop must write resolution BACK to the capability_gaps
ledger so drain is tracked honestly (and resolved gaps stop being re-extracted).

Bug: on a successful fix the coder called only gap_detector.mark_fixed(), which
writes a `crucix:aria:gap:fixed:<detector_sha>` sentinel in a DISJOINT keyspace.
The operator's /capability-gaps/summary reads the ledger's per-entry `resolved`
flag — never flipped — so it showed {resolved: 0} for ALL 500 gaps by
construction, whether or not the coder was working. The extractor also skips
`resolved` entries (gap_detector.py:934), so an un-flipped ledger never drains.

These capability tests drive the REAL success branch of ARIACoder._one_cycle and
assert capability_gaps.resolve_gap is called with the LEDGER uuid (carried at
Gap.evidence['capability_gap_id']), plus the drain property (resolved → skipped).
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity


def _run(coro):
    return asyncio.run(coro)


class _StubRedis:
    """Minimal async redis stub — the coder only needs these to no-op."""
    def __init__(self):
        self.store = {}
    async def get(self, k): return self.store.get(k)
    async def setex(self, k, ttl, v): self.store[k] = v
    async def lrange(self, k, a, b): return []
    async def lpush(self, k, v): self.store.setdefault(k, []).insert(0, v)
    async def ltrim(self, k, a, b): return None
    async def expire(self, k, ttl): return None


@pytest.fixture
def _unpaused(monkeypatch):
    # ensure the engine isn't paused so _one_cycle runs the fix branch
    import aria_service.autonomous.self_coder as sc
    if hasattr(sc, "is_engine_paused"):
        monkeypatch.setattr(sc, "is_engine_paused", lambda: False, raising=False)
    yield


def _make_coder(monkeypatch, gaps, resolve_spy):
    from unittest.mock import AsyncMock
    from aria_service.autonomous.self_coder import ARIACoder, FixResult
    import aria_service.intel.capability_gaps as capgaps
    monkeypatch.setattr(capgaps, "resolve_gap", resolve_spy)

    mock_detector = AsyncMock()
    mock_detector.scan = AsyncMock(return_value=gaps)
    mock_detector.mark_attempted = AsyncMock()
    mock_detector.mark_fixed = AsyncMock()

    class _TestCoder(ARIACoder):
        async def fix_gap(self, gap, **kwargs):
            return FixResult(success=True, fix_id="f1", gap_id=gap.gap_id, r_number=2000)

    return _TestCoder(
        redis_client=_StubRedis(), aria_service_url="http://localhost:8000",
        gap_detector=mock_detector,
    )


def test_successful_fix_writes_back_to_ledger(monkeypatch, _unpaused):
    """A ledger-sourced gap (evidence.capability_gap_id) → resolve_gap(uuid, R#)."""
    calls = []
    async def resolve_spy(gap_id, resolution):
        calls.append((gap_id, resolution)); return {"ok": True}

    gaps = [Gap(
        gap_id="detector-sha-1", gap_type=GapType.MODULE_BUG, severity=GapSeverity.HIGH,
        title="Bug", description="x", module="aria_service/intel/researcher.py",
        evidence={"capability_gap_id": "ledger-uuid-123", "type": "engine_failure"},
    )]
    coder = _make_coder(monkeypatch, gaps, resolve_spy)
    _run(coder._one_cycle())
    assert calls == [("ledger-uuid-123", "R-F2000")], calls


def test_fix_without_ledger_origin_does_not_crash(monkeypatch, _unpaused):
    """A gap with NO capability_gap_id (e.g. a source-code-scan gap) must not
    call resolve_gap and must not raise."""
    calls = []
    async def resolve_spy(gap_id, resolution):
        calls.append((gap_id, resolution)); return {}

    gaps = [Gap(
        gap_id="detector-sha-2", gap_type=GapType.MODULE_BUG, severity=GapSeverity.HIGH,
        title="Bug", description="x", module="aria_service/intel/researcher.py",
        evidence={},  # no capability_gap_id
    )]
    coder = _make_coder(monkeypatch, gaps, resolve_spy)
    _run(coder._one_cycle())
    assert calls == [], "must not call resolve_gap when no ledger origin"


def test_resolve_gap_failure_is_swallowed(monkeypatch, _unpaused):
    """If resolve_gap raises, the cycle must not crash (best-effort write-back)."""
    async def resolve_boom(gap_id, resolution):
        raise RuntimeError("ledger down")

    gaps = [Gap(
        gap_id="detector-sha-3", gap_type=GapType.MODULE_BUG, severity=GapSeverity.HIGH,
        title="Bug", description="x", module="aria_service/intel/researcher.py",
        evidence={"capability_gap_id": "ledger-uuid-9"},
    )]
    coder = _make_coder(monkeypatch, gaps, resolve_boom)
    _run(coder._one_cycle())  # must not raise


def test_extractor_skips_resolved_gaps_the_drain_property(monkeypatch):
    """The write-back's PAYOFF: a resolved ledger entry is excluded from
    re-extraction (gap_detector.py:934) → the loop drains what it fixes."""
    from datetime import datetime, timezone
    from aria_service.autonomous.gap_detector import CapabilityGapExtractor
    import json

    now = datetime.now(timezone.utc)
    fresh_ts = now.isoformat()
    entries = [
        json.dumps({"id": "u1", "type": "source_failure", "detail": "dead feed",
                    "timestamp": fresh_ts, "resolved": False}),
        json.dumps({"id": "u2", "type": "source_failure", "detail": "fixed feed",
                    "timestamp": fresh_ts, "resolved": True}),  # RESOLVED → must skip
    ]

    class _R:
        async def lrange(self, *a): return entries
    ext = CapabilityGapExtractor(_R())
    out = _run(ext.extract(since=datetime.fromtimestamp(0, tz=timezone.utc)))
    ids = {g.evidence.get("capability_gap_id") for g in out}
    assert "u1" in ids, "unresolved gap should be surfaced"
    assert "u2" not in ids, "resolved gap must be excluded (the drain property)"
