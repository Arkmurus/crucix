"""C-36 / R-F3922 — a POSITIVE-ONLY counter was surfaced as "reliability".

C-32 established that `web_atlas.record_correction` — the only negative reliability
signal — has no caller, so the EMA can only ever RISE. C-32 itself stays open, because
closing it needs a reliable contradiction detector plus source-level adjudication, and
`deep_researcher` Rule B declares a contradiction when two same-topic facts share fewer
than five words: a lexical heuristic whose false-positive mode is that a paraphrase of
the SAME fact reads as a contradiction. Building auto-suspension on that would suspend
CORRECT sources.

But two things are wrong TODAY, independent of that missing capability, and both are
closeable:

1. THE LABEL OVERCLAIMS. `/api/aria/source_validator/health` presents the number as
   reliability, and the page renders "Registry reliability (measured observations)" with
   healthy / degraded / failing / dead bands. A quantity that cannot decrease is not a
   reliability score — it is an accumulation of confirmations. A reader who trusts the
   word "reliability" will conclude a 0.995 source has been VERIFIED not to fail, when
   in truth nothing in the system is capable of recording that it did.

2. THE CONSUMER OVERCLAIMS. `suspend_failing_sources` is reachable at
   `POST /api/aria/source_validator/suspend_failing` with a caller-supplied threshold,
   and it exists to enforce "never silently trust a failing source". Against a one-way
   metric that enforcement is theatre: it cannot fire. Worse, it is armed — the moment
   anyone wires a negative signal carelessly (the obvious next step, and precisely what
   C-32 warns against) it starts suspending sources on that signal's first bad day, with
   `degradation_reason` text that reads as a considered verdict.

So this is not the C-32 feature. It is the honesty defect C-32 leaves behind, and it is
the same family as everything else in this sweep: C-29 (absence read as health), C-30/31
(absence read as failure), C-34 (a guard that could not fail). Here a number that cannot
move is presented as a measurement that can.

The fix states the direction of the signal on the surface, and makes the suspender
refuse to act while no negative signal exists — declining loudly rather than silently
never firing.
"""
from __future__ import annotations

import fnmatch

import pytest

from aria_service.intel import source_validator as sv
from aria_service.intel import web_atlas as wa


class Store:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    async def get_json(self, k):
        return self.data.get(k)

    async def get_json_strict(self, k):
        return self.data.get(k)

    async def set_json(self, k, v, ex=None, keepttl=False):
        self.data[k] = v

    async def scan_keys(self, pattern, count=200):
        return [k for k in self.data if fnmatch.fnmatch(k, pattern)][:count]

    async def scan_keys_strict(self, pattern: str, count: int = 200):
        """C-38 — the strict contract this fake must mirror: same result as
        `scan_keys`, but a store failure RAISES instead of returning []."""
        return await self.scan_keys(pattern, count)


@pytest.fixture
def store(monkeypatch):
    st = Store()
    monkeypatch.setattr(sv, "rs", st)
    monkeypatch.setattr(wa, "rs", st)
    return st


def _seed(store, family, topics, tier="tier_1a"):
    store.data["aria:atlas:index:families"] = sorted(
        set(store.data.get("aria:atlas:index:families") or []) | {family}
    )
    store.data[f"aria:atlas:source:{family}"] = {
        "family": family, "tier": tier, "topics": topics, "last_ok": None,
    }


@pytest.mark.asyncio
async def test_report_declares_the_signal_is_positive_only(store) -> None:
    """THE LABEL: the surface must say the number cannot fall.

    Without this the bands (healthy / degraded / failing / dead) imply a two-sided
    measurement, and 0.995 reads as "verified not to fail" rather than "confirmed 21
    times, and nothing in the system can record a failure".
    """
    _seed(store, "example.gov", ["registry"])
    await wa.record_ingest("https://example.gov/x", "identity", success=True)

    report = await sv.registry_health_report()

    assert report.get("signal_direction") == "positive_only", (
        "C-36: the report presents a one-way accumulation as reliability without "
        "saying so — see `signal_direction`"
    )
    assert "confirmation" in (report.get("metric_note") or "").lower(), (
        "the note must say what the number actually counts"
    )


@pytest.mark.asyncio
async def test_suspend_declines_while_no_negative_signal_exists(store) -> None:
    """THE CONSUMER: refuse loudly instead of never firing.

    "Never silently trust a failing source" cannot be enforced by a metric that
    cannot fall. Declining with a reason is honest; silently never firing looks like
    enforcement that happens to have nothing to do.
    """
    _seed(store, "example.gov", ["registry"])
    await wa.record_ingest("https://example.gov/x", "identity", success=True)

    result = await sv.suspend_failing_sources(threshold=0.99)

    assert result.get("enforceable") is False, (
        "C-36: suspend reports as if it enforced something on a one-way metric"
    )
    assert result["suspended"] == 0
    assert "negative" in (result.get("reason") or "").lower(), (
        "the refusal must name the cause: no negative signal is ever recorded"
    )


@pytest.mark.asyncio
async def test_suspend_becomes_enforceable_the_moment_a_correction_is_recorded(store) -> None:
    """THE UN-BLOCKING CONDITION, executable.

    This is the acceptance test for C-32: wire a genuine negative signal and the
    suspender must arm itself automatically. It keys on real recorded evidence
    (`contradicted > 0`), never on a flag someone can set by hand.
    """
    _seed(store, "wrong.example", ["registry"])
    for _ in range(30):
        await wa.record_ingest("https://wrong.example/x", "identity", success=False)

    result = await sv.suspend_failing_sources(threshold=0.40)

    assert result.get("enforceable") is True, (
        "a recorded contradiction must arm the suspender — otherwise closing C-32 "
        "would silently leave enforcement off"
    )
    assert result["suspended"] == 1
    assert "wrong.example" in result["families"]


@pytest.mark.asyncio
async def test_a_measured_source_still_reports_its_score(store) -> None:
    """C-36 relabels; it must not blank the measurement C-29 restored."""
    _seed(store, "good.example", ["registry"])
    for _ in range(21):
        await wa.record_ingest("https://good.example/x", "identity", success=True)

    report = await sv.registry_health_report()
    assert report["healthy_count"] == 1
    assert (report["top_performers"][0]["overall_health"] or 0) > 0.9
