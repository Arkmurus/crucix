"""R-F1783 — Wiring gates detect decorators via AST, not string proximity.

FAIL->PASS (§3c):
  BEFORE: GATE B matched wire.py's docstring `@fail_wire(...)` usage example and
          GATE D matched run_gate_c() (a `raise` near the harness's docstring
          example) as REAL wirings. run_all_gates() therefore never returned
          green -> Phase 1's autonomous loop ("if not green: fix") would read RED
          on every module forever, jamming the whole backfill.
  AFTER: only genuine entries in a function's decorator_list count, so the
          harness is clean while real gap_type violations are still caught.
"""
import textwrap

from aria_service.intel import wiring_harness as wh


def test_all_gates_clean_no_infra_false_positives():
    """run_all_gates() is green and never flags the harness's own infra files."""
    results = wh.run_all_gates()
    flat = results["gate_a"] + results["gate_b"] + results["gate_d"]
    offenders = [v for v in flat if "wire.py" in v or "wiring_harness.py" in v]
    assert not offenders, f"infra docstring examples flagged as real: {offenders}"
    assert results["gate_a"] == []
    assert results["gate_b"] == []
    assert results["gate_d"] == []


def test_docstring_example_is_not_a_real_decorator(tmp_path):
    """A `@fail_wire(...)` shown only inside a docstring must NOT be counted."""
    f = tmp_path / "mod.py"
    f.write_text(textwrap.dedent('''
        """Usage:
            @fail_wire(module="x", gap_type="source_failure")
            async def example(): ...
        """
        def real():
            return 1
    '''))
    assert wh.fail_wire_decorators(str(f)) == {}


def test_real_decorator_detected_with_gap_type(tmp_path):
    """A genuine decorator_list entry is detected with its gap_type."""
    f = tmp_path / "mod.py"
    f.write_text(textwrap.dedent('''
        from .wire import fail_wire
        @fail_wire(module="mod", gap_type="engine_failure")
        def do_thing():
            raise RuntimeError("x")
    '''))
    found = wh.fail_wire_decorators(str(f))
    assert "do_thing" in found
    assert found["do_thing"]["gap_type"] == "engine_failure"


def test_gate_b_still_catches_real_gap_type_mismatch(tmp_path, monkeypatch):
    """GATE B must block a real decorator using the wrong gap_type."""
    monkeypatch.setitem(wh.MODULE_GAP_TYPES, "fakemod", "engine_failure")
    f = tmp_path / "fakemod.py"
    f.write_text(textwrap.dedent('''
        from .wire import fail_wire
        @fail_wire(module="fakemod", gap_type="source_failure")
        def do_thing():
            raise RuntimeError("x")
    '''))
    violations = wh.check_gate_b(str(f), "fakemod.py")
    assert len(violations) == 1
    assert "source_failure" in violations[0]
    assert "engine_failure" in violations[0]


def test_gate_a_still_catches_dark_public_function(tmp_path, monkeypatch):
    """GATE A must flag a public fn in a WIRED module with no real decorator."""
    monkeypatch.setitem(wh.MODULE_GAP_TYPES, "darkmod", "engine_failure")
    monkeypatch.setattr(wh, "WIRED_MODULES", wh.WIRED_MODULES | {"darkmod"})
    f = tmp_path / "darkmod.py"
    f.write_text(textwrap.dedent('''
        """@fail_wire(module="darkmod", gap_type="engine_failure")"""  # docstring decoy
        def naked_public():
            raise RuntimeError("x")
    '''))
    violations = wh.check_gate_a(str(f), "darkmod.py")
    assert len(violations) == 1
    assert "naked_public" in violations[0]
