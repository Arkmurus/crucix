"""C-30 / C-31 / R-F3909 — two wiring monitors that could only ever report failure.

Both were found live on aria-intel: `GET /api/aria/brain/stats` showed
`wiring_monitor:M3` at 3 total / 3 fail and `wiring_monitor:M4` at 3 total / 3 fail,
`success_rate: 0.0`. Neither was detecting a real problem.

C-30 — M3 IS INVERTED. `check_wa_connection_health` counts `wa_auth_lost` /
`wa_disconnected` entries in capability_gaps and then does:

    if auth_lost == 0 and disconnected == 0:  wire_failure(...)
    else:                                     wire_success(...)

So a WhatsApp listener that has never dropped is reported as FAILING, and one that
is disconnecting constantly is reported as HEALTHY. The verdict is exactly backwards.
Its own docstring concedes it cannot tell the two apart ("Either the WA listener has
never disconnected (unlikely) or these signals are dark") and then resolves that
ambiguity by asserting failure. The returned dict says `healthy: True` at the same
moment it wires a failure, so the function contradicts itself.

C-31 — M4 IS BLIND TO ITS OWN IMAGE. `test_brain_signal_path` greps three files:
`lib/observability/errorTracker.mjs`, `services/wa-listener/aria_wa_listener.mjs`,
`services/aria_zoom_service.py`. Measured inside the running aria-intel container:

    MISSING lib/observability/errorTracker.mjs
    MISSING services/wa-listener/aria_wa_listener.mjs
    MISSING services/aria_zoom_service.py
    -> errorTracker_wired: false, wa_listener_wired: false, path_healthy: false

aria-intel ships the PYTHON service; those are Node-tier files and are not in it. So
M4 reports "the Node tier is not wired to the brain" when it simply cannot SEE the
Node tier — and that conclusion is provably false: C-27 / R-F3889 measured that wire
live and made it readable at `/api/health/brain-wire`. The failure message names
`errorTracker.mjs` as unwired, which would send an engineer to wire something already
wired: a wrong cause pointing at a wrong fix.

BOTH ARE THE SAME CLASS AS C-29, INVERTED. C-29 was absence rendered as health; these
are absence rendered as failure. In every case the honest answer is UNKNOWN — the
check could not measure — and a monitor that cannot distinguish "no evidence" from
"evidence of failure" must say so rather than pick one.

Why this matters beyond tidiness: a monitor that is red no matter what carries no
information, and it trains everyone to scroll past `wiring_monitor` — so the day M4
has something real to say, nobody will be listening. That is the same reasoning
recorded for the C-28 "degraded because" row.
"""
from __future__ import annotations

import pytest

from aria_service.intel import wiring_monitor as wm


class _Recorder:
    """Captures the brain verdicts the monitors actually emit."""

    def __init__(self) -> None:
        self.failures: list[dict] = []
        self.successes: list[dict] = []
        self.unknowns: list[dict] = []


@pytest.fixture
def verdicts(monkeypatch):
    rec = _Recorder()

    def _fail(**kw):
        rec.failures.append(kw)

    def _ok(**kw):
        rec.successes.append(kw)

    monkeypatch.setattr(wm, "wire_failure", _fail)
    monkeypatch.setattr(wm, "wire_success", _ok)
    return rec


class _Store:
    def __init__(self, gaps: list[str]) -> None:
        self._gaps = gaps

    async def lrange(self, key: str, start: int, stop: int):
        return self._gaps


# ─────────────────────────────────────────────────────────────────────────────
# C-30 — M3's verdict is inverted.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_m3_quiet_wa_is_not_reported_as_a_failure(monkeypatch, verdicts) -> None:
    """THE SYMPTOM: no disconnect signals must not be a FAILURE verdict.

    Zero disconnects is the shape of a healthy listener AND the shape of a dark
    signal path. The monitor cannot tell them apart, so it must report unknown —
    never `wire_failure`, which asserts a defect it has no evidence for.
    """
    monkeypatch.setattr(wm, "rs", _Store([]))

    result = await wm.check_wa_connection_health()

    assert not verdicts.failures, (
        "C-30: a quiet WA listener was wired to the brain as a FAILURE — the "
        "monitor is inverted and can never go green"
    )
    # And the function must not contradict itself the way it used to.
    assert result.get("healthy") is not False or verdicts.failures, (
        "returned dict and brain verdict disagree"
    )
    assert result.get("determinate") is False, (
        "zero signals is INDETERMINATE — it is neither health nor failure"
    )


@pytest.mark.asyncio
async def test_m3_actual_disconnects_are_not_reported_as_success(monkeypatch, verdicts) -> None:
    """THE INVERSION'S OTHER HALF: real disconnects used to wire SUCCESS."""
    monkeypatch.setattr(
        wm, "rs", _Store(['{"gap_type": "wa_auth_lost", "detail": "loggedOut"}'])
    )

    await wm.check_wa_connection_health()

    assert not verdicts.successes, (
        "C-30: observed WA auth-loss was wired to the brain as a SUCCESS"
    )


# ─────────────────────────────────────────────────────────────────────────────
# C-31 — M4 must not convert "I cannot see the file" into "it is not wired".
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_m4_absent_node_tier_is_unknown_not_unwired(monkeypatch, verdicts) -> None:
    """THE SYMPTOM, reproduced: the Node files are not in the Python image.

    Live-measured inside aria-intel — all three are MISSING. The monitor must
    report that it could not inspect them, NOT that they are unwired.
    """
    # Reproduces production exactly: the file is simply not there. Note the OLD
    # `_cached_source` returned "" here, so the grep found no token and the check
    # concluded "not wired" — absence read as a defect.
    monkeypatch.setattr(wm, "_read_source", lambda path: ("", False))

    result = await wm.test_brain_signal_path()

    assert result.get("inspectable") is False, (
        "M4 must declare that it could not inspect the Node tier"
    )
    assert not verdicts.failures, (
        "C-31: M4 asserted the Node brain wire is broken on the strength of files "
        "that are not in its own image — C-27/R-F3889 measured that wire LIVE"
    )
    assert result.get("path_healthy") is not True, (
        "and it must not swing the other way and certify a tier it cannot see"
    )


@pytest.mark.asyncio
async def test_m4_still_fails_on_a_genuinely_dark_node_tier(monkeypatch, verdicts) -> None:
    """THE GUARD THAT KEEPS M4 USEFUL.

    A monitor that can no longer fail is worse than one that always fails — it is
    the C-29 defect. When the files ARE readable and genuinely lack the brain wire,
    M4 must still raise a failure.
    """
    def _src(path: str) -> tuple[str, bool]:
        if path.endswith(".mjs"):
            return "console.log('something happened');", True  # no brain wire
        if path.endswith("aria.py"):
            return '@router.post("/brain/signal")', True       # endpoint present
        return "requests.post('/api/brain/signal')", True      # zoom on dead path

    monkeypatch.setattr(wm, "_read_source", _src)

    result = await wm.test_brain_signal_path()

    assert result.get("inspectable") is True
    assert result.get("path_healthy") is False
    assert verdicts.failures, (
        "M4 went blind instead of failing — a guard that cannot fail is not a guard"
    )


def test_composite_does_not_fold_unknown_into_degraded() -> None:
    """C-31 — `path_healthy` is tri-state; a bare truthiness test loses that.

    `None and ...` is falsy, so the composite would have reported "degraded"
    forever on aria-intel for a tier it never inspected — re-creating the
    permanently-red signal one level up.
    """
    import inspect

    src = inspect.getsource(wm.run_all_checks)
    assert "is None" in src and "is True" in src, (
        "composite reads path_healthy by truthiness — None collapses into degraded"
    )


@pytest.mark.asyncio
async def test_m4_passes_on_a_correctly_wired_node_tier(monkeypatch, verdicts) -> None:
    """And it must be able to go GREEN, which it could not do in production."""
    def _src(path: str) -> tuple[str, bool]:
        if path.endswith("errorTracker.mjs"):
            return "await fetch(base + '/api/aria/brain/signal', {...})", True
        if path.endswith("aria_wa_listener.mjs"):
            return "await brainPost('/api/aria/brain/signal', payload)", True
        return "httpx.post(f'{base}/api/aria/brain/signal', json=payload)", True

    monkeypatch.setattr(wm, "_read_source", _src)

    result = await wm.test_brain_signal_path()

    assert result.get("path_healthy") is True, f"M4 cannot go green: {result}"
    assert verdicts.successes and not verdicts.failures
