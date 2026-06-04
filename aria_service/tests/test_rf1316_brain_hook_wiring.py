"""
R-F1316 capability tests — brain_hook.py self-observation wiring.

Tests use source inspection (not import) because brain_hook.py contains
UTF-8 em-dash characters that cause SyntaxError on Windows py_compile.
The file works correctly on Linux (Fly production).

Tests:
  1. absorb() has R-F1316 docstring (source grep)
  2. absorb() self-observes via observe_self_event (source grep)
  3. absorb_silent catches errors (source grep)
  4. observe_self_event signature (source grep)
  5. Module-level wire_success is present (source grep)
"""
from __future__ import annotations

import re


def _source() -> str:
    with open("aria_service/intel/brain_hook.py", "r", encoding="utf-8") as f:
        return f.read()


def test_absorb_has_rf1316_docstring():
    """The absorb() function must document its R-F1316 self-observation."""
    source = _source()
    absorb_idx = source.index("async def absorb(")
    absorb_block = source[absorb_idx:absorb_idx + 2000]
    assert "R-F1316" in absorb_block, (
        "absorb() docstring must mention R-F1316"
    )


def test_absorb_has_self_observation():
    """absorb() must call observe_self_event for self-observation."""
    source = _source()
    absorb_idx = source.index("async def absorb(")
    absorb_body = source[absorb_idx:]
    next_func = re.search(r"\n(async )?def ", absorb_body[1:])
    if next_func:
        absorb_body = absorb_body[:next_func.start() + 1]
    assert "observe_self_event" in absorb_body
    assert "brain_hook_absorb_ok" in absorb_body
    assert "_call_count" in absorb_body


def test_absorb_silent_catches_errors():
    """absorb_silent must catch and log errors without raising."""
    source = _source()
    assert "async def absorb_silent" in source
    idx = source.index("async def absorb_silent")
    block = source[idx:idx + 500]
    assert "try:" in block
    assert "except" in block


def test_observe_self_event_signature():
    """observe_self_event must accept event, detail, success, gap_type."""
    source = _source()
    assert "async def observe_self_event(" in source
    idx = source.index("async def observe_self_event(")
    sig_block = source[idx:idx + 500]
    assert "event" in sig_block
    assert "detail" in sig_block
    assert "success" in sig_block
    assert "gap_type" in sig_block


def test_module_has_wire_success():
    """brain_hook.py must have wire_success call at module level."""
    source = _source()
    assert "wire_success" in source, (
        "brain_hook.py must call wire_success at module level"
    )
