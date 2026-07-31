"""R-F3560 — the §21a wiring gate accepts a deliberately MORE SPECIFIC gap_type.

GATE B enforced one gap_type per module. That is right for a module with one failure
domain and wrong for `routes/aria.py`, which is every DD, vetting, billing and GDPR
endpoint in one file.

The forcing case: `leads_inbound_delete_ep` — a GDPR erasure endpoint — declares
`data_protection_violation`, while module `aria` is registered `engine_failure`.
`capability_gaps.VALID_GAP_TYPES` says of that type, verbatim, that it is
"Deliberately NOT folded into engine_failure: this is a GDPR-severity signal and
collapsing it into a generic type would bury a regulatory obligation in ordinary
noise." Rewriting the decorator would have turned the gate green by destroying the
signal the type exists to carry.

So the gate learned the exception instead — as a NAMED ALLOWLIST, which is the whole
risk of this change: an override table is a loophole unless it is itself guarded. These
tests are that guard. They assert the table cannot rot into a silent exemption, and that
a function absent from it is still held to its module's type.
"""
from __future__ import annotations

import pytest

from aria_service.intel import wiring_harness as wh
from aria_service.intel.capability_gaps import VALID_GAP_TYPES


# ── The blocking case actually clears ─────────────────────────────────────────

def test_the_gdpr_erasure_endpoint_no_longer_violates_gate_b():
    """The real defect: this exact function was the last blocking gate_b violation."""
    assert wh.get_gap_type("aria", "leads_inbound_delete_ep") == "data_protection_violation"


def test_gate_b_is_green_across_the_whole_tree():
    """CAPABILITY: run the real gate, not a stand-in for it."""
    violations = wh.run_all_gates()["gate_b"]
    assert violations == [], (
        "gate_b regressed — a @fail_wire gap_type disagrees with its module:\n  "
        + "\n  ".join(str(v) for v in violations)
    )


def test_no_blocking_wiring_violations_remain():
    """CAPABILITY: the §21a gate as main.py/CI consume it."""
    results = wh.run_all_gates()
    assert not wh.has_blocking_violations(results), {
        g: v for g, v in results.items() if v and g != "gate_d"
    }


# ── The override table cannot rot into a silent exemption ─────────────────────

def test_every_override_names_a_registered_gap_type():
    """An override to a type `record_gap` rejects would emit nothing at all."""
    for module, funcs in wh.GAP_TYPE_OVERRIDES.items():
        for fname, gtype in funcs.items():
            assert gtype in VALID_GAP_TYPES, (
                f"{module}.{fname} overrides to '{gtype}', which is not in "
                f"VALID_GAP_TYPES — capability_gaps.record_gap would reject it."
            )


def test_every_override_is_actually_claimed_by_a_live_decorator():
    """A STALE entry is the real hazard.

    If the function is renamed or its decorator changed, the entry silently becomes a
    permanent blanket exemption for a name nothing enforces. It must always describe
    code that exists.
    """
    import aria_service.routes.aria as _aria

    sources = {"aria": _aria.__file__}
    for module, funcs in wh.GAP_TYPE_OVERRIDES.items():
        path = sources.get(module)
        assert path, f"override module '{module}' has no source mapping in this test"
        decorators = wh.fail_wire_decorators(path)
        for fname, gtype in funcs.items():
            assert fname in decorators, (
                f"STALE OVERRIDE: {module}.{fname} is allowlisted but carries no "
                f"@fail_wire decorator — it is exempting nothing and must be removed."
            )
            assert decorators[fname]["gap_type"] == gtype, (
                f"DRIFTED OVERRIDE: {module}.{fname} is allowlisted as '{gtype}' but "
                f"the decorator declares '{decorators[fname]['gap_type']}'."
            )


def test_an_override_that_merely_restates_the_module_default_is_pointless():
    """Guards against noise accumulating in the table until nobody reads it."""
    for module, funcs in wh.GAP_TYPE_OVERRIDES.items():
        default = wh.MODULE_GAP_TYPES.get(module, wh.MODULE_GAP_TYPES["_default"])
        for fname, gtype in funcs.items():
            assert gtype != default, (
                f"{module}.{fname} overrides to '{gtype}', which is already the module "
                f"default — delete the entry."
            )


# ── It is an allowlist, NOT a loosening ───────────────────────────────────────

def test_a_function_not_in_the_table_is_still_held_to_its_module_type():
    """PROVE RED. The exemption must be per-name, never per-module.

    Without this the change reads as 'gate_b now tolerates anything in routes/aria.py'.
    """
    assert wh.get_gap_type("aria", "some_unlisted_endpoint") == "engine_failure"
    assert wh.get_gap_type("aria", "") == "engine_failure"
    assert wh.get_gap_type("aria") == "engine_failure"


def test_an_unlisted_function_declaring_the_gdpr_type_still_fails_the_gate(monkeypatch):
    """The override is keyed on the FUNCTION NAME, so a copy-paste elsewhere is caught."""
    monkeypatch.setattr(wh, "GAP_TYPE_OVERRIDES", {}, raising=True)
    assert wh.get_gap_type("aria", "leads_inbound_delete_ep") == "engine_failure"


def test_the_default_argument_keeps_every_pre_existing_call_site_working():
    """Six call sites (harness + tests + scripts/admin/apply_wiring.py) pass one arg."""
    for module in ("dd_orchestrator", "rag_store", "sanctions", "aria"):
        assert wh.get_gap_type(module) == wh.MODULE_GAP_TYPES.get(
            module, wh.MODULE_GAP_TYPES["_default"]
        )


# ── Gate C must not contradict Gate B ─────────────────────────────────────────

def test_gate_c_resolves_the_same_per_function_type_as_gate_b():
    """Two gates disagreeing about what a function must declare is worse than either
    being wrong: whichever you satisfy, the other fails."""
    import inspect

    src = inspect.getsource(wh.run_gate_c)
    assert "_expected_types" in src, "gate C still asserts against the module type alone"
    assert 'get_gap_type(module_name, f["name"])' in src, (
        "gate C must resolve the per-function type, exactly as gate B does"
    )
