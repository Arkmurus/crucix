"""R-F1800 — GATE D ignores control-flow raises (routes-readiness).

routes/aria.py has 284 HTTPException raises (control flow, allowlisted by
R-F1784) and only a few real-exception raises. GATE D must warn ONLY for
non-control-flow raises, else it would emit 284 false warnings and block the
routes batch. Bare re-raise is ambiguous and must not warn on its own.
"""
import textwrap

from aria_service.intel import wiring_harness as wh


def _write(tmp_path, body):
    f = tmp_path / "rt.py"
    f.write_text(textwrap.dedent(body))
    return str(f)


def test_gate_d_ignores_httpexception_only(tmp_path):
    fp = _write(tmp_path, '''
        from .wire import fail_wire
        class HTTPException(Exception): ...
        @fail_wire(module="rt", gap_type="engine_failure")
        def only_http():
            raise HTTPException()
    ''')
    assert wh.check_gate_d(fp, "rt.py") == []


def test_gate_d_ignores_bare_reraise(tmp_path):
    fp = _write(tmp_path, '''
        from .wire import fail_wire
        @fail_wire(module="rt", gap_type="engine_failure")
        def reraiser():
            try:
                pass
            except Exception:
                raise
    ''')
    assert wh.check_gate_d(fp, "rt.py") == []


def test_gate_d_warns_on_real_exception(tmp_path):
    fp = _write(tmp_path, '''
        from .wire import fail_wire
        @fail_wire(module="rt", gap_type="engine_failure")
        def real_fail():
            raise RuntimeError("boom")
    ''')
    warnings = wh.check_gate_d(fp, "rt.py")
    assert len(warnings) == 1 and "RuntimeError" in warnings[0]


def test_gate_d_silenced_by_control_flow_exempt(tmp_path):
    fp = _write(tmp_path, '''
        from .wire import fail_wire
        @fail_wire(module="rt", gap_type="engine_failure",
                   control_flow_exempt=("ValueError",))
        def validated():
            raise ValueError("bad input")
    ''')
    assert wh.check_gate_d(fp, "rt.py") == []


def test_routes_gap_type_registered():
    assert wh.get_gap_type("aria") == "engine_failure"
