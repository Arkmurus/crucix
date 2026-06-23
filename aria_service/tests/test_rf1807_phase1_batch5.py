"""R-F1807 — Phase 1 batch 5: intel stragglers + closure-exclusion harness fix.

Wires the last intel modules that needed judgment:
  - dd_disciplines / mistake_ledger / dd_orchestrator: ValueError raises are
    validation (control flow) -> control_flow_exempt=("ValueError",).
  - cost_tracker: MonthlyCostCapExceeded is a deliberate signal -> exempt.
  - correction_learner: has a nested closure ('relevance') that scan_public_
    functions must NOT count (else GATE A false-positives).

Proves: each module fires an engine_failure gap on a real (non-exempt) failure;
the exemptions are encoded; and the closure is excluded from the wire surface.
"""
import asyncio
import importlib
import inspect

import pytest

import aria_service.intel.wire as wire
from aria_service.intel import wiring_harness as wh

BATCH5 = ["dd_disciplines", "dd_orchestrator", "mistake_ledger",
          "correction_learner", "cost_tracker"]
_SENTINEL = object()


def _forcefail_args(fn):
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (ValueError, TypeError):
        return None
    if any(p.default is inspect.Parameter.empty and p.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.KEYWORD_ONLY) for p in params):
        return ()
    if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params):
        return None
    slots = sum(1 for p in params if p.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY))
    return tuple(_SENTINEL for _ in range(slots + 1))


@pytest.mark.parametrize("module_name", BATCH5)
def test_batch5_module_fail_wire_records_gap(module_name):
    mod = importlib.import_module(f"aria_service.intel.{module_name}")
    expected = wh.get_gap_type(module_name)
    wired = wh.fail_wire_decorators(mod.__file__)
    target = None
    for name in wired:
        fn = getattr(mod, name, None)
        if fn is None:
            continue
        args = _forcefail_args(fn)
        if args is not None:
            target = (name, fn, args)
            if args == ():
                break  # cleanest (missing required arg -> TypeError, not exempt)
    assert target, f"{module_name}: no force-failable wired fn"
    name, fn, args = target

    async def _run():
        recorded = []

        async def _mock(gap_type, detail, source):
            recorded.append(gap_type)

        original = wire._record_gap
        wire._record_gap = _mock
        try:
            with pytest.raises(Exception):
                if inspect.iscoroutinefunction(fn):
                    await fn(*args)   # TypeError (not exempt) -> gap fires
                else:
                    fn(*args)
            import time
            deadline = time.time() + 5
            while time.time() < deadline:
                if recorded:
                    break
                await asyncio.sleep(0.01)
        finally:
            wire._record_gap = original

        assert recorded, f"{module_name}.{name}(): no gap recorded"
        assert expected in recorded, f"{module_name}.{name}(): expected {expected}, got {recorded}"

    asyncio.run(_run())


def test_validation_exemptions_encoded():
    for m, fn_substr in [("dd_disciplines", None), ("mistake_ledger", None),
                        ("dd_orchestrator", None)]:
        mod = importlib.import_module(f"aria_service.intel.{m}")
        wired = wh.fail_wire_decorators(mod.__file__)
        assert any(info.get("control_flow_exempt") for info in wired.values()), \
            f"{m}: expected at least one control_flow_exempt fn"
    ct = importlib.import_module("aria_service.intel.cost_tracker")
    wired = wh.fail_wire_decorators(ct.__file__)
    assert any(info.get("control_flow_exempt") for info in wired.values())


def test_correction_learner_closure_not_in_wire_surface():
    mod = importlib.import_module("aria_service.intel.correction_learner")
    names = {f["name"] for f in wh.scan_public_functions(mod.__file__)}
    assert "relevance" not in names, "nested closure wrongly counted in wire surface"
