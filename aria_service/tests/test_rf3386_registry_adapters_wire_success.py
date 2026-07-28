"""R-F3386 — first tranche of the §21 wiring backlog: the EE/NO registry adapters.

`ariregister.search_company` and the `brreg` lookups reach the brain on every
failure branch and on NONE of their success paths. The wiring audit reports them
as "has wire_failure but NO wire_success", which is accurate.

Why they matter more than most of the backlog: they are external-source adapters.
CLAUDE.md §21a wants both branches because a source that has silently stopped
RETURNING anything looks identical, from the brain's side, to a source nobody
queried. Failure-only wiring tells ARIA when a registry errors; it cannot tell her
when a registry is answering but has gone empty — which is the shape of the
"source went dark" incidents this rule exists for.

Why `@wired` is NOT the remedy here, despite being the preferred mechanism: both
modules deliberately swallow exceptions and return a falsy value, so a DD is never
crashed by a registry outage (`ariregister.py` says so in its own comment). No
exception escapes for `@wired` to catch, and its success branch would fire on the
degraded return too. These need an explicit success signal on the path that really
did get data.

FAILS BEFORE R-F3386: no wire_success call exists in either module.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from aria_service.intel import ariregister, brreg


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _Client:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **kw):
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


def _run(coro):
    return asyncio.run(coro)


# ── ariregister (Estonia) ───────────────────────────────────────────────────
def test_rf3386_ariregister_wires_success_when_records_are_returned():
    payload = {"status": "OK", "data": [
        {"name": "Test OU", "reg_code": "12345678", "status": "R"},
    ]}
    with patch.object(ariregister.httpx, "AsyncClient", lambda *a, **k: _Client(_Resp(200, payload))), \
         patch.object(ariregister, "wire_success") as ws, \
         patch.object(ariregister, "wire_failure") as wf:
        out = _run(ariregister.search_company("Test"))
    assert isinstance(out, list)
    assert ws.called, "a successful EE registry lookup must reach the brain (§21a success branch)"
    assert not wf.called, "a successful lookup must not report a failure"


def test_rf3386_ariregister_still_wires_failure_and_never_raises():
    """The existing failure contract must survive: degrade to [], signal the brain."""
    with patch.object(ariregister.httpx, "AsyncClient", lambda *a, **k: _Client(RuntimeError("boom"))), \
         patch.object(ariregister, "wire_success") as ws, \
         patch.object(ariregister, "wire_failure") as wf:
        out = _run(ariregister.search_company("Test"))
    assert out == [], "a registry outage must never crash a DD"
    assert wf.called, "the failure branch must still reach the brain"
    assert not ws.called, "a failed lookup must not report success"


def test_rf3386_ariregister_http_error_is_a_failure_not_a_success():
    """A 500 that returns no data is a source failure, not an empty success —
    the distinction the audit's success/failure split exists to preserve."""
    with patch.object(ariregister.httpx, "AsyncClient", lambda *a, **k: _Client(_Resp(500, {}))), \
         patch.object(ariregister, "wire_success") as ws, \
         patch.object(ariregister, "wire_failure") as wf:
        out = _run(ariregister.search_company("Test"))
    assert out == []
    assert wf.called and not ws.called


def test_rf3386_ariregister_blank_query_is_neither(  ):
    """A caller mistake is not a source event — it must not pollute either ledger."""
    with patch.object(ariregister, "wire_success") as ws, \
         patch.object(ariregister, "wire_failure") as wf:
        assert _run(ariregister.search_company("   ")) == []
    assert not ws.called and not wf.called


# ── brreg (Norway) ──────────────────────────────────────────────────────────
def test_rf3386_brreg_wires_success_on_a_real_lookup():
    payload = {"_embedded": {"enheter": [
        {"organisasjonsnummer": "123456789", "navn": "Test AS"},
    ]}}
    with patch.object(brreg.httpx, "AsyncClient", lambda *a, **k: _Client(_Resp(200, payload))), \
         patch.object(brreg, "wire_success") as ws, \
         patch.object(brreg, "wire_failure") as wf:
        out = _run(brreg.search_company("Test"))
    assert isinstance(out, list)
    assert ws.called, "a successful NO registry lookup must reach the brain"
    assert not wf.called


def test_rf3386_brreg_still_wires_failure_and_never_raises():
    with patch.object(brreg.httpx, "AsyncClient", lambda *a, **k: _Client(RuntimeError("boom"))), \
         patch.object(brreg, "wire_success") as ws, \
         patch.object(brreg, "wire_failure") as wf:
        out = _run(brreg.search_company("Test"))
    assert out == []
    assert wf.called
    assert not ws.called


# ── the gate must agree ─────────────────────────────────────────────────────
def test_rf3386_the_wiring_audit_no_longer_flags_these_two():
    """Close the loop: the CI gate that produced this backlog item must stop
    reporting these modules, or the fix is cosmetic."""
    import pathlib
    import sys
    root = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))
    from pre_commit_checks import check_wiring_present

    for name in ("ariregister.py", "brreg.py"):
        issues = check_wiring_present([root / "aria_service" / "intel" / name])
        assert issues == [], f"{name} is still flagged by the wiring audit: {issues}"
