"""R-F3254 — a source that has NEVER been measured must not be reported as failing.

CAUSE: both `registry_health_report()` and `suspend_failing_sources()` compute

    overall = sum(scores) / len(scores) if scores else 0.5

and then judge `overall` as if it were a measurement. For a source with zero
reliability samples the 0.5 is a PRIOR, not an observation — and 0.5 falls in the
`failing` band (0.40 <= x < 0.60), so every never-measured source in the atlas is
reported as FAILING. That is starvation rendered as a verdict: exactly the
`INITIAL_MASTERY = 0.5` reading that made the Phase A gate-#2 heatmap floor
meaningless, and the same measure-vs-assert gap R-F2735 recorded against this very
function ("registry_health_report ASSERTED an unmeasured 0.5 for every source").

It also makes the sibling dangerous: `suspend_failing_sources(threshold=...)` is
reachable from `POST /api/aria/source_validator/suspend_failing` with ANY threshold,
so a 0.6 threshold SUSPENDS every never-measured source and writes the fabricated
reason "Overall reliability 0.50 below 0.60 threshold" into its record.

ABSENT IS NOT FALSE. Unmeasured is its own state: not healthy, not failing.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import source_validator as sv


class FakeStore:
    """Minimal in-memory stand-in for the atlas keys these functions read."""

    def __init__(self, families: dict[str, dict], reliability: dict[str, float]):
        self.families = families
        self.reliability = reliability
        self.written: dict[str, dict] = {}

    async def get_json(self, key: str):
        if key == "aria:atlas:index:families":
            return list(self.families.keys())
        if key.startswith("aria:atlas:source:"):
            fam = key.split("aria:atlas:source:", 1)[1]
            return self.families.get(fam)
        if key.startswith("aria:atlas:reliability:"):
            suffix = key.split("aria:atlas:reliability:", 1)[1]
            if suffix in self.reliability:
                return {"score": self.reliability[suffix]}
            return None
        return None

    async def set_json(self, key: str, value, ex=None):
        self.written[key] = value

    # C-29 — the real store contract this fake stands in for grew two members the
    # consumer now depends on. Without them the fake diverges from production and
    # the tests fail on the FAKE, not on the behaviour they assert.
    async def get_json_strict(self, key: str):
        """Strict read: this fake never fails, so it mirrors get_json exactly.
        A store failure is exercised in the C-29 suite, which has a fake that can."""
        return await self.get_json(key)

    async def scan_keys(self, pattern: str, count: int = 200) -> list[str]:
        """C-29 — enumerate the reliability keys that actually exist.

        This fake models reliability as `{"family:topic": score}` rather than as
        stored keys, so materialise the key names the same way `get_json` above
        resolves them.
        """
        import fnmatch as _fn

        keys = [f"aria:atlas:reliability:{suffix}" for suffix in self.reliability]
        return [k for k in keys if _fn.fnmatch(k, pattern)][:count]

    async def scan_keys_strict(self, pattern: str, count: int = 200) -> list[str]:
        """C-38 — the strict contract this fake must mirror: same result as
        `scan_keys`, but a store failure RAISES rather than returning []. This
        fake never fails, so it delegates."""
        return await self.scan_keys(pattern, count)


def _atlas(monkeypatch, families: dict[str, dict], reliability: dict[str, float]):
    store = FakeStore(families, reliability)
    monkeypatch.setattr(sv, "rs", store)
    return store


# A family that HAS topics but no reliability record for any of them — the
# never-measured case. And one that has been genuinely measured, low.
NEVER_MEASURED = {"topics": ["compliance", "defence"], "tier": "1b", "last_ok": None}
MEASURED_BAD = {"topics": ["compliance"], "tier": "2", "last_ok": "2026-07-01"}
MEASURED_GOOD = {"topics": ["compliance"], "tier": "1a", "last_ok": "2026-07-26"}


def test_never_measured_source_is_unmeasured_not_failing(monkeypatch) -> None:
    """THE SYMPTOM: zero samples must not read as a 0.5 'failing' verdict."""
    _atlas(
        monkeypatch,
        {"never.example": dict(NEVER_MEASURED)},
        {},                                     # no reliability records at all
    )
    rep = asyncio.run(sv.registry_health_report())

    failing_families = [r["family"] for r in rep["failing"]]
    assert "never.example" not in failing_families, (
        "a source with ZERO reliability samples was reported as failing — that is "
        "the 0.5 prior being read as a measurement"
    )
    assert rep["failing_count"] == 0
    assert rep["unmeasured_count"] == 1
    assert [r["family"] for r in rep["unmeasured"]] == ["never.example"]
    # and it must not fabricate a score for something it never measured
    assert rep["unmeasured"][0]["overall_health"] is None


def test_measured_sources_still_bucket_correctly(monkeypatch) -> None:
    """Regression: real measurements must keep their existing buckets."""
    _atlas(
        monkeypatch,
        {
            "good.example": dict(MEASURED_GOOD),
            "bad.example": dict(MEASURED_BAD),
        },
        {"good.example:compliance": 0.95, "bad.example:compliance": 0.30},
    )
    rep = asyncio.run(sv.registry_health_report())

    assert [r["family"] for r in rep["top_performers"]] == ["good.example"]
    assert [r["family"] for r in rep["dead"]] == ["bad.example"]
    assert rep["healthy_count"] == 1 and rep["dead_count"] == 1
    assert rep["unmeasured_count"] == 0


def test_every_source_is_accounted_for(monkeypatch) -> None:
    """Buckets must partition the registry — a dropped source is a silent lie."""
    _atlas(
        monkeypatch,
        {
            "never.example": dict(NEVER_MEASURED),
            "good.example": dict(MEASURED_GOOD),
            "bad.example": dict(MEASURED_BAD),
        },
        {"good.example:compliance": 0.95, "bad.example:compliance": 0.30},
    )
    rep = asyncio.run(sv.registry_health_report())

    counted = (
        rep["healthy_count"] + rep["degraded_count"] + rep["failing_count"]
        + rep["dead_count"] + rep["unmeasured_count"]
    )
    assert counted == rep["total_sources"] == 3


def test_suspend_never_suspends_a_source_it_never_measured(monkeypatch) -> None:
    """You cannot demote what you never measured.

    Drives the real threshold the endpoint exposes: at 0.6 the 0.5 prior used to
    trip the suspension and write a fabricated degradation_reason.
    """
    store = _atlas(
        monkeypatch,
        {
            "never.example": dict(NEVER_MEASURED),
            "bad.example": dict(MEASURED_BAD),
        },
        {"bad.example:compliance": 0.30},
    )
    result = asyncio.run(sv.suspend_failing_sources(threshold=0.60))

    # NOTE the shape: {"suspended": <count:int>, "families": [<name>, ...]}.
    assert "never.example" not in result["families"], (
        "suspended a source that has never been measured — the 0.5 prior was "
        "treated as an observation"
    )
    # the genuinely-measured failure IS still suspended: the guard must not
    # become a blanket amnesty
    assert "bad.example" in result["families"]
    assert result["suspended"] == 1
    assert "aria:atlas:source:never.example" not in store.written
