"""R-F3640 — gate #5 measured env only, so it could not see the control plane.

LIVE ON aria-intel, 2026-08-02, which is what prompted this:

    ARIA_AUTONOMOUS_ENABLED = 0                        (fly secret)
    crucix:autonomous:enabled_override = '1'           (durable, in /data/aria_state.db)
    /health -> autonomous {enabled: true, running: true, autonomy_level: 3, tasks: 98}
    /phase/gates -> gate #5 FAIL, missing: [ARIA_AUTONOMOUS_ENABLED]

The engine was genuinely running at L3 and the gate said the platform was not
configured. `engine.is_enabled()` documents the precedence — the override wins over the
env var in BOTH directions, and /autonomous/enable exists precisely so the switch can be
flipped without a redeploy. Reading `os.environ` alone measured the wrong surface.

This is a false NEGATIVE, the mirror of the fabricated passes this file's history is
made of (R-F2622 gate #3, R-F2640 gate #6, R-F2643 gate #4). It is fixed the same way
those were: by measuring MORE. The direction of the error does not change the rule —
a gate that cannot see the thing it gates is not a measurement.

Capability tests (§3c): every test drives `compute_phase_gates()`, the ONE canonical
measure both routes render, and asserts the operator-visible verdict.
"""
from __future__ import annotations

import asyncio

import pytest


def _run(coro):
    """Run without destroying the ambient loop (see test_rf2639_2640 for why)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


OVERRIDE_KEY = "crucix:autonomous:enabled_override"


@pytest.fixture()
def gate5(monkeypatch):
    """Drive the real compute_phase_gates(); control only env + the override key."""
    from aria_service.intel import phase_gates, redis_store

    monkeypatch.setenv("ARIA_OUTPUT_HARVEST_ENABLED", "1")
    monkeypatch.setenv("ARIA_AUTONOMY_LEVEL", "3")

    state: dict = {"override": None, "raise": False}

    async def _get_strict(key, *a, **k):
        if key == OVERRIDE_KEY:
            if state["raise"]:
                raise RuntimeError("StoreReadError: store unavailable")
            return state["override"]
        return None

    monkeypatch.setattr(redis_store, "get_strict", _get_strict)

    def _measure():
        return _run(phase_gates.compute_phase_gates())["gates"]["gate_5_env_vars"]

    return state, _measure


# ── the live production state ────────────────────────────────────────────────

def test_override_on_with_env_off_passes_and_names_the_override(gate5, monkeypatch):
    """The exact aria-intel state on 2026-08-02: autonomy IS on; the gate must say so."""
    state, measure = gate5
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "0")
    state["override"] = "1"

    g = measure()
    assert g["pass"] is True
    assert g["by_var"]["ARIA_AUTONOMOUS_ENABLED"] is True
    # a pass earned by the override must never look like a pass earned by the secret
    assert g["by_var_source"]["ARIA_AUTONOMOUS_ENABLED"] == "runtime_override=1"
    assert g["env_var_value"]["ARIA_AUTONOMOUS_ENABLED"] == "0"
    assert "ARIA_AUTONOMOUS_ENABLED" not in g["value"]["missing"]


def test_deliberate_disable_is_not_masked_by_the_env_var(gate5, monkeypatch):
    """The override wins in BOTH directions. /autonomous/disable writes '0' and §21c
    says a deliberate disable is respected — a stale =1 secret must not hide it."""
    state, measure = gate5
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "1")
    state["override"] = "0"

    g = measure()
    assert g["pass"] is False, "autonomy is OFF; the gate must not pass on a stale secret"
    assert g["by_var"]["ARIA_AUTONOMOUS_ENABLED"] is False
    assert g["by_var_source"]["ARIA_AUTONOMOUS_ENABLED"] == "runtime_override=0"
    assert "ARIA_AUTONOMOUS_ENABLED" in g["value"]["missing"]


# ── back-compat: no override means env still decides ─────────────────────────

@pytest.mark.parametrize("env,expected", [("1", True), ("0", False), ("", False)])
def test_absent_override_defers_to_env(gate5, monkeypatch, env, expected):
    state, measure = gate5
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", env)
    state["override"] = None

    g = measure()
    assert g["by_var"]["ARIA_AUTONOMOUS_ENABLED"] is expected
    assert g["by_var_source"]["ARIA_AUTONOMOUS_ENABLED"] == "env"


def test_garbage_override_value_defers_to_env_not_to_truthiness(gate5, monkeypatch):
    """engine.is_enabled() only honours exactly '0'/'1'. A junk value must not
    force-enable the gate by being a non-empty string."""
    state, measure = gate5
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "0")
    state["override"] = "yes"

    g = measure()
    assert g["by_var"]["ARIA_AUTONOMOUS_ENABLED"] is False
    assert g["by_var_source"]["ARIA_AUTONOMOUS_ENABLED"] == "env"


# ── the tri-state contract: could-not-measure is not measured-and-failed ─────

def test_unreadable_store_is_unknown_never_a_measured_fail(gate5, monkeypatch):
    """With the store down an override could exist in EITHER direction, so the
    effective state is genuinely unknown. §1: `None` = COULD NOT MEASURE, rendered
    `unknown`, never `open`."""
    state, measure = gate5
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "1")
    state["raise"] = True

    g = measure()
    assert g["pass"] is None, "a store failure must not be reported as a failed gate"
    assert g["measurable"] is False
    assert g["by_var"]["ARIA_AUTONOMOUS_ENABLED"] is None
    assert g["by_var_source"]["ARIA_AUTONOMOUS_ENABLED"] == "override_unreadable"
    assert "ARIA_AUTONOMOUS_ENABLED" in g["value"]["unknown"]


def test_unreadable_store_does_not_pass_the_gate_on_a_set_env_var(gate5, monkeypatch):
    """The failure that would be easy to write: env=1 and an unreadable store looks
    like a pass if you ignore the override. It must not resolve to True."""
    state, measure = gate5
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "1")
    state["raise"] = True
    assert measure()["pass"] is not True


# ── the other two vars are env-only and must stay strict ────────────────────

def test_override_does_not_leak_into_the_other_two_vars(gate5, monkeypatch):
    """Only the master switch has an override. Harvest/level have no such control
    plane, so the override must not be allowed to satisfy them."""
    state, measure = gate5
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "0")
    monkeypatch.setenv("ARIA_OUTPUT_HARVEST_ENABLED", "0")
    state["override"] = "1"

    g = measure()
    assert g["by_var"]["ARIA_AUTONOMOUS_ENABLED"] is True
    assert g["by_var"]["ARIA_OUTPUT_HARVEST_ENABLED"] is False
    assert g["pass"] is False
    assert g["by_var_source"]["ARIA_OUTPUT_HARVEST_ENABLED"] == "env"


def test_gate_5_still_reports_through_the_one_canonical_measure(gate5, monkeypatch):
    """R-F2639: both routes render compute_phase_gates(). The new source must be
    named in the evidence so a reader can tell WHICH surfaces were consulted."""
    state, measure = gate5
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "0")
    state["override"] = "1"

    g = measure()
    assert "override" in g["evidence"].lower()
    assert "R-F3640" in g["evidence"]
