"""
R-F1318 capability tests — intel_ledger.py wiring.

Tests:
  1. Module-level wire_success is present
  2. add_signal calls brain_hook.absorb on success
"""
from __future__ import annotations


def _source() -> str:
    with open("aria_service/intel/intel_ledger.py", "r", encoding="utf-8") as f:
        return f.read()


def test_module_has_wire_success():
    """intel_ledger.py must have wire_success call at module level."""
    source = _source()
    assert "wire_success" in source
    assert "R-F1318" in source


def test_add_signal_calls_brain_hook():
    """add_signal must call brain_hook.absorb on success."""
    source = _source()
    idx = source.index("async def add_signal(")
    body = source[idx:]
    assert "brain_hook.absorb" in body
    assert 'module="intel_ledger"' in body
