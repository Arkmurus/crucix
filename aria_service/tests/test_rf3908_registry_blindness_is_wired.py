"""R-F3908 (C-29 follow-through) — the blindness DETECTOR shipped dark.

C-29 fixed a registry-reliability report that could not see its own data, and gave
it an honest `store_readable: false` state for the case where the store cannot be
read at all. That state was returned to the caller and **logged**, and nothing else:
no `brain_hook.absorb`, no gap, no metric. Under CLAUDE.md §21a that is DARK, not
wired — "logged to console / except: pass / local ring buffer" is explicitly not
wiring, and §21d says the fix for finding something dark is to wire it.

Leaving THIS path dark is the sharpest version of the mistake: the whole of C-29 is
that an instrument which cannot see is indistinguishable from a clean reading. A
blindness detector that tells nobody it fired reproduces the defect one level up —
ARIA would go blind on her own source registry and her brain would never learn of
it, so §25 proprioception ("she must KNOW whether the intended result was actually
produced") is unmet for this limb.

Two things are asserted here, and the second is the one with teeth:

1. the failure branch reaches the brain at all; and
2. the `gap_type` it emits is REGISTERED in `capability_gaps`. An unregistered type
   is **silently rejected** (`capability_gaps.py:315`, and see the R-F3428 / R-F3793
   / R-F3520 comment blocks recording types that were emitted in production while
   registered nowhere). Emitting an unregistered type looks exactly like wiring and
   delivers nothing — a dark wire wearing a live wire's clothes.

The success path is deliberately NOT wired: `registry_health_report` backs a polled
dashboard panel, and emitting per read would be the `source_atlas_update` storm that
`defence_source_seed.skip_if_populated` exists to prevent. Observability of the
healthy case is the returned `store_readable: true` the page renders.
"""
from __future__ import annotations

import pytest

from aria_service.intel import capability_gaps as cg
from aria_service.intel import source_validator as sv
from aria_service.intel.redis_store import StoreReadError


GAP_TYPE = "source_registry_unreadable"


class BlindStore:
    """Every read fails the way a wedged store actually fails."""

    async def get_json(self, key: str):
        return None

    async def get_json_strict(self, key: str):
        raise StoreReadError(f"store unreadable: {key}")

    async def set_json(self, key: str, obj, ex=None, keepttl=False) -> None:
        return None

    async def scan_keys(self, pattern: str, count: int = 200) -> list[str]:
        return []


@pytest.fixture
def blind(monkeypatch):
    monkeypatch.setattr(sv, "rs", BlindStore())


@pytest.fixture
def captured(monkeypatch):
    """Capture what actually reaches the brain."""
    calls: list[dict] = []

    async def _fake_absorb(**kwargs):
        calls.append(kwargs)

    import aria_service.intel.brain_hook as bh

    monkeypatch.setattr(bh, "absorb", _fake_absorb)
    return calls


@pytest.mark.asyncio
async def test_registry_health_report_reports_its_blindness_to_the_brain(blind, captured) -> None:
    report = await sv.registry_health_report()

    assert report["store_readable"] is False  # precondition: the branch was taken
    assert captured, (
        "DARK PATH: the registry went blind and the brain was never told (§21a)"
    )
    sig = captured[-1]
    assert sig["module"] == "source_validator"
    assert sig["success"] is False, "a blindness event must not absorb as a success"
    assert sig["gap_type"] == GAP_TYPE


@pytest.mark.asyncio
async def test_suspend_failing_sources_reports_its_blindness_to_the_brain(blind, captured) -> None:
    result = await sv.suspend_failing_sources(threshold=0.40)

    assert result["store_readable"] is False
    assert captured, (
        "DARK PATH: auto-suspend could not read the registry and said nothing (§21a)"
    )
    assert captured[-1]["success"] is False
    assert captured[-1]["gap_type"] == GAP_TYPE


def test_the_gap_type_is_registered_so_the_signal_is_not_silently_dropped() -> None:
    """THE TEST WITH TEETH.

    `record_gap` silently rejects an unregistered type, so emitting one is
    indistinguishable from wiring while delivering nothing. If this fails, do NOT
    change the gap_type to something already in the list — register the real one.
    """
    assert GAP_TYPE in cg.VALID_GAP_TYPES, (
        f"{GAP_TYPE!r} is emitted but NOT registered — record_gap will drop it silently"
    )


@pytest.mark.asyncio
async def test_a_healthy_read_does_not_spam_the_brain(captured) -> None:
    """The success path stays quiet on purpose — this panel is polled.

    Guards the opposite failure: a per-read absorb would be the `source_atlas_update`
    storm `defence_source_seed.skip_if_populated` exists to prevent.
    """
    import fnmatch

    data = {"aria:atlas:index:families": []}

    class OkStore:
        async def get_json(self, k):
            return data.get(k)

        async def get_json_strict(self, k):
            return data.get(k)

        async def set_json(self, k, v, ex=None, keepttl=False):
            data[k] = v

        async def scan_keys(self, p, count=200):
            return [k for k in data if fnmatch.fnmatch(k, p)][:count]

        async def scan_keys_strict(self, p, count=200):
            # C-38 — healthy store: strict scan behaves as the ordinary one.
            return await self.scan_keys(p, count)

    import aria_service.intel.source_validator as _sv

    original = _sv.rs
    _sv.rs = OkStore()
    try:
        report = await _sv.registry_health_report()
    finally:
        _sv.rs = original

    assert report["store_readable"] is True
    assert not captured, "healthy poll emitted a brain signal — this endpoint is polled"
