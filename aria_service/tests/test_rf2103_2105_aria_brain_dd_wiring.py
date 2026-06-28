"""Capability tests for the ARIA brain-DD surgical fixes (2026-06-28).

These drive the REAL broken entry points (CLAUDE.md §3c / §23):

  R-F2103 — cost_tracker.record_call now invokes the per-user DAILY cost cap
            (user_quota.record_cost was DEAD code, never called). Proven by
            calling the real record_call and asserting record_cost fires with
            the active user + a positive USD.

  R-F2104 — company_investigator.investigate_company now wires its run OUTCOME
            to the brain (§21a). Proven by driving the real entry point down its
            failure path and asserting a wire_failure lands.

  R-F2105 — OSINT lookups (gleif / email_breach / username_enum) now wire BOTH
            success and failure so an API outage that returns [] is no longer
            indistinguishable from a clean result. Proven for gleif by stubbing
            the network and asserting the brain signal on each branch.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import cost_tracker
from aria_service.intel import engine_wiring
from aria_service.intel import gleif
from aria_service.intel import company_investigator


# ── R-F2103: per-user daily cost cap is now actually fed ────────────────────

@pytest.mark.asyncio
async def test_rf2103_record_call_feeds_user_daily_cost_cap(monkeypatch):
    """record_call MUST call user_quota.record_cost for the active user — the
    dead-code path the ARIA brain DD flagged. Pre-fix this assertion failed
    because record_cost was never invoked."""
    captured: list[tuple[str, float]] = []

    from aria_service.intel import user_quota

    async def _fake_record_cost(user_id: str, cost_usd: float) -> None:
        captured.append((user_id, cost_usd))

    monkeypatch.setattr(user_quota, "record_cost", _fake_record_cost)

    token = cost_tracker.set_user("test_user_rf2103")
    try:
        rec = await cost_tracker.record_call(
            model="deepseek-chat",
            input_tokens=100_000,
            output_tokens=100_000,
            provider_name="deepseek",
            success=True,
        )
    finally:
        cost_tracker._current_user.reset(token)

    assert rec["cost_usd"] > 0, "fixture must produce a non-zero cost"
    assert captured, "user_quota.record_cost was NOT called — daily cap still dead"
    user_id, cost = captured[0]
    assert user_id == "test_user_rf2103"
    assert cost == pytest.approx(rec["cost_usd"])


# ── R-F2104: company_investigator wires its outcome to the brain ────────────

@pytest.mark.asyncio
async def test_rf2104_investigate_company_wires_failure(monkeypatch):
    """Drive the real investigate_company down its exception path and assert the
    brain receives a wire_failure. Pre-fix every run was logger-only."""
    fails: list[dict] = []

    def _capture_failure(**kw):
        fails.append(kw)

    monkeypatch.setattr(company_investigator, "_ENABLED", True)
    monkeypatch.setattr(engine_wiring, "wire_failure", _capture_failure)
    monkeypatch.setattr(engine_wiring, "wire_success", lambda **kw: None)

    async def _boom(*a, **k):
        raise RuntimeError("simulated phase failure")

    # First awaited phase — raising here propagates to the except branch.
    monkeypatch.setattr(company_investigator, "_phase_entity_resolution", _boom)

    report = await company_investigator.investigate_company("Acme Defence Ltd", jurisdiction="UK")

    assert report.error, "report.error should be set on pipeline failure"
    assert fails, "company_investigator did NOT wire its failure to the brain"
    assert fails[0].get("module") == "company_investigator"


# ── R-F2105: OSINT lookups wire both branches (gleif representative) ─────────

class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Async-context fake httpx client; .get returns a preset response or raises."""
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        if self._exc is not None:
            raise self._exc
        return self._resp


@pytest.mark.asyncio
async def test_rf2105_gleif_wires_success(monkeypatch):
    import httpx

    ok = [{"acc": []}]
    payload = {"data": [{
        "attributes": {
            "lei": "ABC123",
            "entity": {"legalName": {"name": "Acme Defence Ltd"},
                       "jurisdiction": "GB", "status": "ACTIVE"},
            "registration": {"status": "ISSUED"},
        }
    }]}
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: _FakeClient(resp=_FakeResp(200, payload)))

    successes: list[dict] = []
    monkeypatch.setattr(engine_wiring, "wire_success", lambda **kw: successes.append(kw))
    monkeypatch.setattr(engine_wiring, "wire_failure", lambda **kw: None)

    out = await gleif.search_lei("Acme Defence Ltd")
    assert out, "expected a parsed LEI result"
    assert successes, "gleif success was NOT wired to the brain"
    assert successes[0].get("module") == "gleif"
    _ = ok  # keep linter quiet


@pytest.mark.asyncio
async def test_rf2105_gleif_wires_failure(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: _FakeClient(exc=RuntimeError("network down")))

    fails: list[dict] = []
    monkeypatch.setattr(engine_wiring, "wire_failure", lambda **kw: fails.append(kw))
    monkeypatch.setattr(engine_wiring, "wire_success", lambda **kw: None)

    out = await gleif.search_lei("Acme Defence Ltd")
    assert out == [], "gleif must stay fail-silent (returns [] for the caller)"
    assert fails, "gleif failure was NOT wired to the brain — outage looks like 'no match'"
    assert fails[0].get("module") == "gleif"
