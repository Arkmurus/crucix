"""R-F3638 — the coder liveness probe could not see the gate that stops it coding.

THE DEFECT. `coder_entrypoint._heartbeat_ticker` ticks every 30s and is started
unconditionally (R-F996 removed the env gate from `start_aria_coder`). R-F3064
then armed `ARIA_CODER_ENABLED` at the real chokepoint — `self_coder.fix_gap` —
so with the flag off the loop still scans and claims gaps while EVERY fix is
refused `coder_disabled`.

`self_introspect_guard` read heartbeat freshness ALONE and rendered:

    running: True (heartbeat age 6s)
    NOTE: the coder IS actively running — it detects gaps, plans fixes, writes
    code, and stages improvements. Do NOT report it as dormant.

That NOTE was true in the R-F996..R-F3064 era, when the flag gated nothing.
R-F3064 inverted the situation and the probe was never updated, so a PAUSED
lane read as active. Live on 2026-08-01 with ARIA_CODER_ENABLED='0':

    [aria_coder] 1 actionable gaps -- fixing top 20
    [aria_coder] fix_gap REFUSED for ed70f2589e7f36a7 — ARIA_CODER_ENABLED='0'
    [aria_coder] gap ed70f2589e7f36a7 not fixed: coder_disabled

…while an operator briefing the same evening cited "ARIA-Coder running with
heartbeat age 6s" as evidence of "true autonomous operation" and recommended
anchoring the marketing USP on "a self-coding autonomous engine". A probe that
cannot see its own gate manufactures a capability claim.

THE FIX. Loop-liveness and lane-enablement are two independent facts and both
are reported. The lane resolves through the SAME predicate the gate uses
(`safety.is_coder_lane_enabled`) so the probe cannot drift from the gate again.

Capability test (§3c): drives the real entry point —
`self_introspect_context_block()`, the block BOTH chat and stream forks inject
(aria_engine.py:4173 and :5102) — under the operator's actual live condition
(fresh heartbeat + lane off) and asserts the user-visible text.
"""
from __future__ import annotations

import asyncio

import pytest


_FRESH_HEARTBEAT = {
    "agents": {
        "aria_coder": {
            "heartbeat_age_s": 6.0,   # the exact age quoted in the live briefing
            "blackout_count": 0,
            "recovery_count": 0,
        }
    }
}

# The claim the old NOTE forced. If any of these survive while the lane is off,
# ARIA is being told to assert autonomous self-coding that is not happening.
_ACTIVE_CLAIM_FRAGMENTS = (
    "plans fixes",
    "writes code",
    "stages improvements",
)

# §23 — the OPERATOR'S ACTUAL WORDING from the 2026-08-01 WhatsApp briefing that
# produced the false claim, not a proxy phrasing.
_SELF_Q = (
    "Aria deep gap analysis of your current system infrastructure, gap analysis "
    "and capabilities measure against your USP or North Star?"
)


def _patch_introspect_env(monkeypatch, *, blackout=_FRESH_HEARTBEAT):
    """Stub the two heavy route calls + the heartbeat probe.

    All three are LAZY imports inside `self_introspect_context_block`, so
    patching the source modules is what the call actually resolves.
    """
    import aria_service.routes.aria as _ra
    import aria_service.intel.self_restart as _sr

    async def _perf():
        return {
            "inventory": {}, "retention": {}, "autonomy": {},
            "advisories": [], "llm_providers": {},
        }

    async def _health():
        return {"status": "operational", "operating_mode": "NORMAL",
                "degraded_reasons": []}

    monkeypatch.setattr(_ra, "health_perf_ep", _perf, raising=False)
    monkeypatch.setattr(_ra, "health_check_ep", _health, raising=False)
    monkeypatch.setattr(_sr, "get_blackout_status", lambda: blackout, raising=False)


def _block(monkeypatch) -> str:
    from aria_service.intel.self_introspect_guard import self_introspect_context_block
    out = asyncio.run(self_introspect_context_block(_SELF_Q))
    assert "CODER STATUS" in out, (
        "self_introspect did not render the coder section — the capability test "
        f"is not driving the real path. Got: {out[:400]!r}"
    )
    return out


# ---------------------------------------------------------------------------
# THE DEFECT — a paused lane must never read as active self-coding
# ---------------------------------------------------------------------------

def test_rf3638_paused_lane_is_reported_as_paused(monkeypatch):
    """FAILS BEFORE: rendered "running: True ... plans fixes, writes code"
    with ARIA_CODER_ENABLED=0 and a fresh heartbeat — the exact live state."""
    monkeypatch.setenv("ARIA_CODER_ENABLED", "0")
    _patch_introspect_env(monkeypatch)
    out = _block(monkeypatch)

    assert "fix_lane_enabled: False" in out, out[-1200:]
    for frag in _ACTIVE_CLAIM_FRAGMENTS:
        assert frag not in out, (
            f"introspection still claims the coder {frag!r} while the lane is off"
        )
    # and it must say so in words the model cannot round off
    assert "NOTHING IS BEING FIXED" in out
    assert "coder_disabled" in out


def test_rf3638_paused_lane_forbids_the_capability_claim(monkeypatch):
    """The briefing used the heartbeat as evidence for a marketing claim."""
    monkeypatch.setenv("ARIA_CODER_ENABLED", "0")
    _patch_introspect_env(monkeypatch)
    out = _block(monkeypatch)
    assert "PAUSED by the operator" in out
    assert "marketing claim" in out
    assert "heartbeat" in out  # names the specific bad inference


def test_rf3638_enabled_lane_still_reports_active(monkeypatch):
    """The fix must not invert into a false NEGATIVE — a live lane reads live."""
    monkeypatch.setenv("ARIA_CODER_ENABLED", "1")
    _patch_introspect_env(monkeypatch)
    out = _block(monkeypatch)
    assert "fix_lane_enabled: True" in out
    assert "plans fixes" in out
    assert "NOTHING IS BEING FIXED" not in out


def test_rf3638_unresolvable_lane_never_reads_as_enabled(monkeypatch):
    """Tri-state: could-not-measure is not measured-and-fine."""
    import aria_service.autonomous.safety as _safety

    def _boom():
        raise RuntimeError("env unreadable")

    monkeypatch.setattr(_safety, "is_coder_lane_enabled", _boom, raising=False)
    _patch_introspect_env(monkeypatch)
    out = _block(monkeypatch)
    assert "fix_lane_enabled: UNKNOWN" in out
    for frag in _ACTIVE_CLAIM_FRAGMENTS:
        assert frag not in out, "an unresolvable lane rendered as active self-coding"


def test_rf3638_loop_dead_still_reports_lane(monkeypatch):
    """No heartbeat at all — the lane fact must not vanish with the loop."""
    monkeypatch.setenv("ARIA_CODER_ENABLED", "0")
    _patch_introspect_env(monkeypatch, blackout={"agents": {}})
    out = _block(monkeypatch)
    assert "loop_alive: False" in out
    assert "fix_lane_enabled: False" in out


# ---------------------------------------------------------------------------
# ANTI-DRIFT — the gate and the probe must resolve ONE predicate
# ---------------------------------------------------------------------------

def test_rf3638_gate_resolves_through_the_shared_predicate(monkeypatch, tmp_path):
    """Behavioural, not a source grep (cf. 'assert the property, not the wording').

    Force the shared predicate TRUE while the env says '0'. If fix_gap still
    reads the env itself, it refuses `coder_disabled` and this fails — which is
    precisely the duplicate-read that let probe and gate disagree.
    """
    import aria_service.autonomous.safety as _safety
    from aria_service.autonomous.self_coder import ARIACoder
    from aria_service.autonomous.gap_detector import Gap, GapType

    monkeypatch.setenv("ARIA_CODER_ENABLED", "0")
    monkeypatch.setattr(_safety, "is_coder_lane_enabled", lambda: True, raising=False)

    c = ARIACoder(redis_client=None, aria_service_url="http://localhost:8000")
    c.workspace_base = tmp_path
    gap = Gap(gap_id="g_rf3638", gap_type=GapType.MODULE_BUG,
              title="probe/gate drift", description="x", severity="HIGH",
              module="aria_service/intel/self_introspect_guard.py")
    res = asyncio.run(c.fix_gap(gap))
    assert res.failure_reason != "coder_disabled", (
        "fix_gap re-read ARIA_CODER_ENABLED itself instead of resolving "
        "safety.is_coder_lane_enabled — probe and gate can drift apart again"
    )


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), (" 1 ", True),
    ("0", False), ("", False), ("false", False), ("no", False), ("off", False),
])
def test_rf3638_predicate_matches_the_rf3064_gate(monkeypatch, val, expected):
    """The shared predicate's truthy set is R-F3064's verbatim."""
    from aria_service.autonomous.safety import is_coder_lane_enabled
    monkeypatch.setenv("ARIA_CODER_ENABLED", val)
    assert is_coder_lane_enabled() is expected


def test_rf3638_unset_is_not_consent(monkeypatch):
    from aria_service.autonomous.safety import is_coder_lane_enabled
    monkeypatch.delenv("ARIA_CODER_ENABLED", raising=False)
    assert is_coder_lane_enabled() is False
