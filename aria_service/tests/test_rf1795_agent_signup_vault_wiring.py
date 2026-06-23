"""R-F1795 — Phase 1 batch 3b: agent_signup_vault wired (validation = control flow).

agent_signup_vault.record/update_status raise ValueError for validation
("Invalid status", "already exists") — control flow, not failure. They are
wired WITH control_flow_exempt=("ValueError",) so a real (non-validation)
failure still gaps but validation does not spam the brain.

Proves: (1) a clean method fires a registry_lookup gap on failure; (2) the two
validation methods carry control_flow_exempt; (3) GATE D no longer warns for
this module (the encoded judgment is recognized). The ValueError-skip behavior
itself is proven generically by test_rf1784.
"""
import asyncio

import pytest

import aria_service.intel.wire as wire
import aria_service.intel.agent_signup_vault as vault
from aria_service.intel import wiring_harness as wh


def test_validation_methods_carry_control_flow_exempt():
    wired = wh.fail_wire_decorators(vault.__file__)
    assert wired["record"]["control_flow_exempt"] is True
    assert wired["update_status"]["control_flow_exempt"] is True


def test_gate_d_no_longer_warns_for_this_module():
    # The control_flow_exempt judgment is encoded -> GATE D must be clean here.
    assert wh.check_gate_d(vault.__file__, "agent_signup_vault.py") == []


def test_clean_method_force_fail_records_registry_lookup_gap():
    # AgentSignupVault.get(self, site_id): call unbound with no args -> missing
    # `self` TypeError, caught by the wrapper -> a registry_lookup gap lands.
    get_fn = getattr(vault.AgentSignupVault, "get")

    async def _run():
        recorded = []

        async def _mock(gap_type, detail, source):
            recorded.append(gap_type)

        original = wire._record_gap
        wire._record_gap = _mock
        try:
            with pytest.raises(Exception):
                get_fn()
            import time
            deadline = time.time() + 5
            while time.time() < deadline:
                if recorded:
                    break
                await asyncio.sleep(0.01)
        finally:
            wire._record_gap = original

        assert recorded, "no gap recorded — method @fail_wire did not fire"
        assert "registry_lookup" in recorded, f"expected registry_lookup, got {recorded}"

    asyncio.run(_run())
