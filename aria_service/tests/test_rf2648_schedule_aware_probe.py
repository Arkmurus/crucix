"""R-F2648 — schedule-aware sovereign gate (finding #2).

SYMPTOM (live aria-intel 2026-07-16): the `aria_llm` circuit breaker sits
permanently OPEN and the logs show periodic 404s to a RunPod endpoint —
because `ARIA_LLM_URL` points at pod `lwvxmhoatf42kb`, which is EXITED. Per
CLAUDE.md §24 (stop-only, pre-shadow) the pod is DELIBERATELY off outside the
weekly-cycle windows, so "sovereign unreachable" is POLICY, not a failure. But
`resilience._probe` treated it as a failure (tripping the breaker, a §25 false-
positive) and `model_router.route_decision` still routed grounded turns to it
(404 → fall back to DeepSeek → pure latency + noise on the flywheel path).

ROOT CAUSE: nothing consulted the pod schedule — the probe's enable-gate was
`bool(ARIA_LLM_URL)`, conflating "configured" with "expected up".

FIX (operator chose option B): one no-network signal,
`runpod_scheduler.expected_serving()` (active work-claim OR shadow-autostart-in-
window), consulted by BOTH the health probe and the chat router, so a
deliberately-stopped pod goes quiet on every path and is_available() still
fails closed to DeepSeek via the R-F1957 warm-gate.

These tests drive the REAL gated paths (`_probe`, `route_decision`,
`expected_serving`) and assert the user-visible outcome: when the pod is off, no
network call is made to the dead endpoint and the breaker is not tripped; when a
work-claim appears, both paths re-activate WITHOUT a restart.
"""

from __future__ import annotations

from typing import Any

import pytest

from aria_service.intel import runpod_scheduler as sched
from aria_service.llm import model_router, resilience

_URL = "https://pod-8888.proxy.runpod.net/v1"


# ── the shared signal ───────────────────────────────────────────────────────

def test_expected_serving_true_on_active_work_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_claim_active", lambda now=None: (True, {"reason": "cycle"}))
    assert sched.expected_serving() is True


def test_expected_serving_true_on_shadow_autostart_in_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_claim_active", lambda now=None: (False, {}))
    monkeypatch.setattr(sched, "_cfg", lambda: {"api_key": "k", "autostart": True, "start_hour": 0,
                                                "stop_hour": 24, "tz": "Europe/London"})
    monkeypatch.setattr(sched, "in_window", lambda now=None: True)
    assert sched.expected_serving() is True


def test_expected_serving_false_when_deliberately_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live pre-shadow reality: no claim, autostart off → deliberately off."""
    monkeypatch.setattr(sched, "_claim_active", lambda now=None: (False, {}))
    monkeypatch.setattr(sched, "_cfg", lambda: {"api_key": "k", "autostart": False, "start_hour": 10,
                                                "stop_hour": 18, "tz": "Europe/London"})
    assert sched.expected_serving() is False


def test_expected_serving_false_autostart_out_of_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_claim_active", lambda now=None: (False, {}))
    monkeypatch.setattr(sched, "_cfg", lambda: {"api_key": "k", "autostart": True, "start_hour": 10,
                                                "stop_hour": 18, "tz": "Europe/London"})
    monkeypatch.setattr(sched, "in_window", lambda now=None: False)
    assert sched.expected_serving() is False


# ── the health probe (resilience._probe) ────────────────────────────────────

class _RecordingClient:
    calls: list[str] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "_RecordingClient":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def post(self, url: str, **k: Any) -> Any:
        type(self).calls.append(url)
        class _R:
            status_code = 200
            text = "{}"
        return _R()


@pytest.fixture
def recording_httpx(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingClient]:
    import httpx
    _RecordingClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _RecordingClient)
    return _RecordingClient


@pytest.mark.asyncio
async def test_probe_is_dormant_when_pod_off_no_network_no_breaker(
    monkeypatch: pytest.MonkeyPatch, recording_httpx: type[_RecordingClient],
) -> None:
    """THE FIX: deliberately-off pod → probe makes NO network call, breaker not tripped."""
    monkeypatch.setattr(sched, "expected_serving", lambda now=None: False)

    checker = resilience.LLMHealthChecker(endpoint=_URL, failure_threshold=1)
    await checker._probe()

    assert recording_httpx.calls == [], "probe hit the dead endpoint while pod is off"
    assert checker.probe_status == "dormant"
    assert checker.consecutive_failures == 0  # never recorded a failure
    assert checker.is_available() is False    # still routes to DeepSeek


@pytest.mark.asyncio
async def test_dormant_probe_stands_down_an_already_open_breaker(
    monkeypatch: pytest.MonkeyPatch, recording_httpx: type[_RecordingClient],
) -> None:
    """A breaker opened by earlier probing of the now-stopped pod is reset —
    /health reads honest (deliberately off ≠ alarmed failure)."""
    from aria_service.intel import circuit_breaker

    br = circuit_breaker.get_breaker("aria_llm", failure_threshold=1)
    br.record_failure(reason="server")  # simulate prior open
    assert br.is_open()

    monkeypatch.setattr(sched, "expected_serving", lambda now=None: False)
    checker = resilience.LLMHealthChecker(endpoint=_URL, failure_threshold=1)
    checker._breaker = br
    await checker._probe()

    assert not br.is_open(), "dormant probe must stand down the stale open breaker"
    assert recording_httpx.calls == []


@pytest.mark.asyncio
async def test_probe_reactivates_when_claim_appears(
    monkeypatch: pytest.MonkeyPatch, recording_httpx: type[_RecordingClient],
) -> None:
    """Dynamic gate: a pod a cycle starts mid-session gets probed WITHOUT restart
    (the reason we gate in _probe, not at __init__)."""
    monkeypatch.setattr(sched, "expected_serving", lambda now=None: True)

    checker = resilience.LLMHealthChecker(endpoint=_URL)
    await checker._probe()

    assert recording_httpx.calls == [f"{_URL}/chat/completions"]
    assert checker.probe_status == "healthy"


# ── the chat router (model_router.route_decision) ───────────────────────────

def _force_grounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_router, "two_track_active", lambda: True)
    monkeypatch.setattr(model_router, "is_grounded_synthesis", lambda m="", c="": True)
    monkeypatch.setattr(model_router, "promotion_stage", lambda: "shadow")
    # R-F2694 — the router now also requires PROOF the pod is warm (a succeeded probe),
    # and fails closed when that is unmeasurable. These tests isolate the SCHEDULE gate,
    # so hold the warm signal True: a test that asserts "pod serving → shadow" must not
    # pass or fail on the probe's state. The dedicated warm-gate cases live in
    # test_rf2694_router_warm_gate.py.
    monkeypatch.setattr(model_router, "_sovereign_warm", lambda: True)


def test_route_decision_deepseek_when_pod_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE FIX: no grounded/flywheel traffic to a deliberately-stopped pod."""
    _force_grounded(monkeypatch)
    monkeypatch.setattr(sched, "expected_serving", lambda now=None: False)

    assert model_router.route_decision("grounded q", "ctx") == "deepseek"


def test_route_decision_shadow_when_pod_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serving pod → normal shadow routing preserved (no behavior change)."""
    _force_grounded(monkeypatch)
    monkeypatch.setattr(sched, "expected_serving", lambda now=None: True)

    assert model_router.route_decision("grounded q", "ctx") == "shadow"


# ── the self-healing health monitor (self_healing.check_all) ─────────────────

@pytest.mark.asyncio
async def test_self_healing_skips_aria_llm_when_pod_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The GET /models 404 source: self_healing must NOT add aria_llm when the
    pod is deliberately off (the other agent's flagged second 404 path)."""
    from aria_service.intel import self_healing

    monkeypatch.setenv("ARIA_LLM_URL", _URL)
    monkeypatch.setattr(sched, "expected_serving", lambda now=None: False)

    mon = self_healing.HealthMonitor()
    # Drive the REAL service-map build via a stubbed check_subsystem so no
    # network happens; assert aria_llm is simply never in the checked set.
    checked: list[str] = []

    async def _fake_check(name: str, url: str):  # type: ignore[no-untyped-def]
        checked.append(name)
        return self_healing.HealthCheck(subsystem=name, status=self_healing.HealthStatus.HEALTHY)

    monkeypatch.setattr(mon, "check_subsystem", _fake_check)
    await mon.check_all()

    assert "aria_llm" not in checked, "self_healing GET /models hit the dead pod"


@pytest.mark.asyncio
async def test_self_healing_checks_aria_llm_when_pod_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    from aria_service.intel import self_healing

    monkeypatch.setenv("ARIA_LLM_URL", _URL)
    monkeypatch.setattr(sched, "expected_serving", lambda now=None: True)

    mon = self_healing.HealthMonitor()
    checked: list[str] = []

    async def _fake_check(name: str, url: str):  # type: ignore[no-untyped-def]
        checked.append(name)
        return self_healing.HealthCheck(subsystem=name, status=self_healing.HealthStatus.HEALTHY)

    monkeypatch.setattr(mon, "check_subsystem", _fake_check)
    await mon.check_all()

    assert "aria_llm" in checked
