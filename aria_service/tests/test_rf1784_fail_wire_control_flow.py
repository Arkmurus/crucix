"""R-F1784 — fail_wire must NOT gap on control-flow exceptions.

FAIL->PASS (§3c):
  BEFORE: fail_wire caught bare `except Exception`, which includes
          HTTPException (raised on every 4xx). Wiring the 165 route handlers
          (277 HTTPException raise-sites) would flood the brain with a gap per
          404/403/400 — catastrophic gap-spam.
  AFTER: HTTPException / validation errors (matched by class name up the MRO)
          re-raise WITHOUT recording a gap; real failures still record one.
"""
import asyncio

import pytest

import aria_service.intel.wire as wire
from aria_service.intel.wire import fail_wire


class HTTPException(Exception):
    """Stand-in with the same class name as fastapi/starlette HTTPException."""
    def __init__(self, status_code=400):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class _SubHTTP(HTTPException):
    """Subclass — must also be treated as control flow (MRO match)."""


class MyControlError(Exception):
    pass


def _capture(monkeypatch):
    recorded = []

    async def _mock(gap_type, detail, source):
        recorded.append({"gap_type": gap_type, "detail": detail, "source": source})

    monkeypatch.setattr(wire, "_record_gap", _mock)
    return recorded


async def _drain(recorded, deadline=2.0):
    for _ in range(int(deadline / 0.01)):
        if recorded:
            return
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_httpexception_does_not_record_gap(monkeypatch):
    recorded = _capture(monkeypatch)

    @fail_wire(module="fake_route", gap_type="engine_failure")
    async def handler():
        raise HTTPException(404)

    with pytest.raises(HTTPException):
        await handler()
    await _drain(recorded)
    assert recorded == [], f"control-flow HTTPException wrongly gapped: {recorded}"


@pytest.mark.asyncio
async def test_httpexception_subclass_does_not_record_gap(monkeypatch):
    recorded = _capture(monkeypatch)

    @fail_wire(module="fake_route", gap_type="engine_failure")
    async def handler():
        raise _SubHTTP(403)

    with pytest.raises(_SubHTTP):
        await handler()
    await _drain(recorded)
    assert recorded == [], f"HTTPException subclass wrongly gapped: {recorded}"


@pytest.mark.asyncio
async def test_real_exception_still_records_gap(monkeypatch):
    recorded = _capture(monkeypatch)

    @fail_wire(module="fake_route", gap_type="engine_failure")
    async def handler():
        raise RuntimeError("real failure")

    with pytest.raises(RuntimeError):
        await handler()
    await _drain(recorded)
    assert len(recorded) == 1
    assert recorded[0]["gap_type"] == "engine_failure"


@pytest.mark.asyncio
async def test_control_flow_exempt_param_extends_set(monkeypatch):
    recorded = _capture(monkeypatch)

    @fail_wire(module="fake", gap_type="engine_failure",
               control_flow_exempt=("MyControlError",))
    async def handler():
        raise MyControlError("not a failure for this module")

    with pytest.raises(MyControlError):
        await handler()
    await _drain(recorded)
    assert recorded == [], f"control_flow_exempt name wrongly gapped: {recorded}"


def test_sync_wrapper_also_honors_control_flow(monkeypatch):
    async def _run():
        recorded = _capture(monkeypatch)

        @fail_wire(module="fake_route", gap_type="engine_failure")
        def handler():
            raise HTTPException(400)

        with pytest.raises(HTTPException):
            handler()
        await _drain(recorded)
        assert recorded == [], f"sync control-flow wrongly gapped: {recorded}"

    asyncio.run(_run())
