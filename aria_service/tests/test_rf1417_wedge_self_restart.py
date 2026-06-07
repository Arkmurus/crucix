"""R-F1417 — self-restart on hard wedge (the keystone of real recovery).

Before R-F1417, a genuinely wedged event loop was unrecoverable in-process:
the on-loop stall detector + blackout detector are themselves frozen, so the
only "recovery" was a slow external Fly kill (~2-3 min), and a "live but
wedged" process never even triggered it. R-F1417 has the OFF-LOOP daemon
watchdog force os._exit(1) past a hard ceiling → Fly cold-boots → ARIA
self-recovers.

os._exit can't be exercised in-process, so the dangerous decision is gated by
a pure module-level predicate `_should_force_restart`; these tests pin its
contract (the watchdog just calls it then exits). This is the §3c capability
test for the decision that triggers the restart.
"""
from __future__ import annotations

import pytest

from aria_service.main import _should_force_restart


CEILING = 90.0


def test_fires_when_genuinely_wedged():
    # armed + enabled + stale past ceiling → force restart
    assert _should_force_restart(120.0, armed=True, enabled=True, ceiling_s=CEILING) is True


def test_does_not_fire_below_ceiling():
    # a legitimate slow op (e.g. 30s) must NOT trigger a restart
    assert _should_force_restart(30.0, armed=True, enabled=True, ceiling_s=CEILING) is False
    # exactly at ceiling is not past it
    assert _should_force_restart(90.0, armed=True, enabled=True, ceiling_s=CEILING) is False


def test_never_fires_before_armed():
    # during cold-boot (not yet armed) a long stall is legitimate hydration —
    # must NEVER restart even far past the ceiling
    assert _should_force_restart(300.0, armed=False, enabled=True, ceiling_s=CEILING) is False


def test_kill_switch_disables():
    # ARIA_WEDGE_SELF_RESTART_ENABLED=0 → never restart, even when wedged
    assert _should_force_restart(300.0, armed=True, enabled=False, ceiling_s=CEILING) is False


def test_bad_inputs_fail_safe():
    # garbage must not crash the daemon nor trigger a spurious restart
    assert _should_force_restart(None, armed=True, enabled=True, ceiling_s=CEILING) is False
    assert _should_force_restart("x", armed=True, enabled=True, ceiling_s=CEILING) is False


def test_just_past_ceiling_fires():
    assert _should_force_restart(90.1, armed=True, enabled=True, ceiling_s=CEILING) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
