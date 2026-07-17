"""R-F2694 — the sovereign router requires PROOF the pod is up, not just policy.

To-do list #2 item #4's true close (P0.5). R-F2648 stopped grounded traffic to a
DELIBERATELY-STOPPED pod by gating `route_decision` on
`runpod_scheduler.expected_serving()`. But that signal is POLICY — "the §24 schedule
says the pod SHOULD be up" — computed with NO network call. A pod that is
scheduled-on but COLD, HUNG, or still scaling from zero passes it, so every grounded
turn still went to `aria_llm_provider` → the full call timeout → fallback to DeepSeek.
Pure latency + noise, which is the exact traffic R-F2648's comment says cannot reach a
dead pod.

The probe already knows the truth (10s cadence, R-F1957 warm-gate, breaker-aware) and
R-F2686 bound the singleton it lives in. R-F2694 makes the router consult it.

TRI-STATE, deliberately (CLAUDE.md §1's rule, applied to routing):
  False -> MEASURED cold  -> DeepSeek.
  True  -> MEASURED warm  -> unchanged routing.
  None  -> NOT measurable (no checker running) -> keep the schedule-only behaviour,
           but WARN loudly. "Could not measure" is not "measured and failed", and a
           checker glitch must not silently disable sovereign routing.
"""
import time

import pytest

from aria_service.llm import model_router
from aria_service.llm import resilience as _res
from aria_service.intel import runpod_scheduler as sched


@pytest.fixture(autouse=True)
def _isolate():
    """The gate reads module globals — never leak them across tests."""
    saved = _res._health_checker_instance
    saved_warn = model_router._last_unmeasurable_warn
    yield
    _res._health_checker_instance = saved
    model_router._last_unmeasurable_warn = saved_warn


def _force_grounded(monkeypatch, stage="serve"):
    """Everything upstream says 'route to the sovereign' — isolate the warm-gate."""
    monkeypatch.setattr(model_router, "two_track_active", lambda: True)
    monkeypatch.setattr(model_router, "is_grounded_synthesis", lambda m="", c="": True)
    monkeypatch.setattr(model_router, "promotion_stage", lambda: stage)
    # POLICY says the pod is serving — this is the state the bug lived in.
    monkeypatch.setattr(sched, "expected_serving", lambda now=None: True)


def _checker(*, warm: bool, dormant: bool = False):
    hc = _res.LLMHealthChecker(endpoint="https://pod.example/v1")
    if warm:
        hc.last_success_at = time.time()
        hc.probe_status = "healthy"
    if dormant:
        hc.probe_status = "dormant"
    return hc


# ── THE capability test: scheduled-on but COLD ───────────────────────────────

def test_cold_pod_does_not_take_grounded_traffic(monkeypatch):
    """PRE-FIX: 'sovereign' — the cold pod eats the call. POST-FIX: 'deepseek'."""
    _force_grounded(monkeypatch, stage="serve")
    _res._health_checker_instance = _checker(warm=False)   # never probed OK = cold

    assert model_router.route_decision("grounded q", "ctx") == "deepseek", (
        "the schedule says serving, but no probe has PROVEN the pod warm — routing a "
        "grounded turn to it burns the full call timeout before falling back"
    )


def test_cold_pod_also_blocks_shadow_capture(monkeypatch):
    """Shadow generates against the pod too — a cold pod yields no useful capture."""
    _force_grounded(monkeypatch, stage="shadow")
    _res._health_checker_instance = _checker(warm=False)
    assert model_router.route_decision("grounded q", "ctx") == "deepseek"


def test_dormant_pod_blocks(monkeypatch):
    """R-F2648's dormant state is honoured by the same gate.

    warm=True is load-bearing: with warm=False the checker is cold anyway, so this
    passed without dormancy ever being consulted (a vacuous test — Pass 2 caught it;
    it survived deleting the dormant branch entirely). A RECENT successful probe plus
    probe_status="dormant" is the only setup that isolates the dormant rule.
    """
    _force_grounded(monkeypatch, stage="serve")
    hc = _checker(warm=True, dormant=True)
    assert hc.last_success_at > 0, "fixture must be warm, else dormancy is never reached"
    _res._health_checker_instance = hc
    assert model_router.route_decision("grounded q", "ctx") == "deepseek"


def test_disabled_checker_is_unmeasurable_not_silently_cold(monkeypatch, caplog):
    """A checker built before ARIA_LLM_URL was visible reports False forever.

    That is "never measured", not "measured cold" — reporting it as cold would send
    every grounded turn to DeepSeek SILENTLY. It must warn (the same import-time-vs-
    call-time env drift R-F2686 guards).
    """
    _force_grounded(monkeypatch, stage="serve")
    hc = _res.LLMHealthChecker(endpoint="")      # disabled: _enabled is False
    assert hc.is_available() is False
    _res._health_checker_instance = hc
    model_router._last_unmeasurable_warn = 0.0

    with caplog.at_level("WARNING"):
        assert model_router.route_decision("grounded q", "ctx") == "deepseek"
    assert any("R-F2694" in r.message for r in caplog.records), (
        "a disabled checker must be reported as unmeasurable, never silently cold"
    )


def test_unmeasurable_gate_records_a_brain_gap(monkeypatch):
    """§21a — the wire must actually fire, not just be written.

    The wire_failure call sits inside a try/except, so a wrong kwarg would raise
    TypeError and be SWALLOWED, leaving a wire that looks present and is dark. Only a
    test keeps it honest (an earlier draft did exactly that with `error=`).
    """
    _force_grounded(monkeypatch, stage="serve")
    _res._health_checker_instance = None
    model_router._last_unmeasurable_warn = 0.0
    fired = []
    monkeypatch.setattr(model_router, "wire_failure", lambda **kw: fired.append(kw))

    model_router.route_decision("grounded q", "ctx")

    assert len(fired) == 1, "the unmeasurable gate must reach the brain (§21a)"
    assert fired[0]["gap_type"] == "llm_provider_failure"   # registered (R-F2551)
    assert fired[0]["module"] == "model_router"
    assert "detail" in fired[0], "engine_wiring.wire_failure takes `detail`, not `error`"


# ── The gate must not become a wall ──────────────────────────────────────────

def test_proven_warm_pod_still_routes_sovereign(monkeypatch):
    _force_grounded(monkeypatch, stage="serve")
    _res._health_checker_instance = _checker(warm=True)
    assert model_router.route_decision("grounded q", "ctx") == "sovereign"


def test_proven_warm_pod_still_shadows(monkeypatch):
    _force_grounded(monkeypatch, stage="shadow")
    _res._health_checker_instance = _checker(warm=True)
    assert model_router.route_decision("grounded q", "ctx") == "shadow"


def test_stale_success_is_cold(monkeypatch):
    """R-F1957 warm-gate: a probe that succeeded long ago proves nothing NOW."""
    _force_grounded(monkeypatch, stage="serve")
    hc = _checker(warm=True)
    hc.last_success_at = time.time() - (_res._ARIA_LLM_WARM_TTL + 60)
    _res._health_checker_instance = hc
    assert model_router.route_decision("grounded q", "ctx") == "deepseek"


# ── Tri-state: unmeasurable != cold ──────────────────────────────────────────

def test_no_checker_fails_closed_and_warns(monkeypatch, caplog):
    """None = cannot measure → DeepSeek, loudly.

    This test asserted the OPPOSITE in the first draft ("keep schedule-only behaviour,
    route sovereign") on a §1 tri-state argument. Pass 2 refuted it: the sibling gate on
    this same singleton (resilience._admission, R-F2686) fails CLOSED, the router does
    NOT go through wrap() so there is no second line of defence, and None is reachable
    in the boot window before start() binds — which correlates positively with a cold
    scale-from-zero pod. Two gates disagreeing on identical input is the bug.
    Unproven = skip (R-F1957). The tri-state survives in what we SAY, not what we admit.
    """
    _force_grounded(monkeypatch, stage="serve")
    _res._health_checker_instance = None
    model_router._last_unmeasurable_warn = 0.0

    with caplog.at_level("WARNING"):
        decision = model_router.route_decision("grounded q", "ctx")

    assert decision == "deepseek", (
        "nothing has proven the pod up, so admitting it would be a claim we cannot back"
    )
    assert any("R-F2694" in r.message for r in caplog.records), (
        "an unmeasurable gate must never be silent (§21a)"
    )


def test_unmeasurable_warning_is_throttled(monkeypatch, caplog):
    """A per-call warning on the hot chat path would be log spam."""
    _force_grounded(monkeypatch, stage="serve")
    _res._health_checker_instance = None
    model_router._last_unmeasurable_warn = 0.0

    with caplog.at_level("WARNING"):
        for _ in range(5):
            model_router.route_decision("grounded q", "ctx")

    assert sum("R-F2694" in r.message for r in caplog.records) == 1


# ── The schedule gate still short-circuits first (R-F2648 preserved) ─────────

def test_stopped_pod_short_circuits_without_the_probe(monkeypatch):
    """A deliberately-stopped pod (§24) must not depend on the probe to be skipped."""
    _force_grounded(monkeypatch, stage="serve")
    monkeypatch.setattr(sched, "expected_serving", lambda now=None: False)

    def _boom():
        raise AssertionError("warm-gate must not be consulted when policy already says off")

    monkeypatch.setattr(model_router, "_sovereign_warm", _boom)
    assert model_router.route_decision("grounded q", "ctx") == "deepseek"
