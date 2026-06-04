"""
R-F1318 capability tests — mistake_ledger.py wiring.

Tests:
  1. Module-level wire_success is present
  2. record() wires persist failure to brain via observe_self_event
  3. record() already calls brain_hook.absorb on success
"""
from __future__ import annotations


def _source() -> str:
    with open("aria_service/intel/mistake_ledger.py", "r", encoding="utf-8") as f:
        return f.read()


def test_module_has_wire_success():
    """mistake_ledger.py must have wire_success call at module level."""
    source = _source()
    assert "wire_success" in source
    assert "R-F1318" in source


def test_record_wires_persist_failure():
    """record() must wire persist failures to the brain."""
    source = _source()
    idx = source.index("async def record(")
    body = source[idx:]
    assert "observe_self_event" in body
    assert "mistake_ledger_persist_failed" in body


def test_record_calls_brain_hook():
    """record() must call brain_hook.absorb on success."""
    source = _source()
    idx = source.index("async def record(")
    body = source[idx:]
    assert "brain_hook.absorb" in body
    assert 'module="mistake_ledger"' in body
