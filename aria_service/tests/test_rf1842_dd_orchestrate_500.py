"""R-F1842 — /api/aria/dd/orchestrate 500s on EVERY call (P0).

The R-F1655 DD-vault pre-check had two stacked bugs that made the endpoint
return 500 for every request (found by the Sonangol e2e verification, 2026-06-23):

  1. routes/aria.py:623 called canonical_entity_id(_entity_name) positionally,
     but that function is keyword-only (def canonical_entity_id(*, entity_type,
     name, ...)) → TypeError.
  2. The except meant to swallow that non-fatally called logger.debug(...), but
     the module logger is _log, not logger → NameError → the handled error
     escalated into a 500.

So the vault pre-check guaranteed a 500 on every /dd/orchestrate call.

This test drives the real endpoint via TestClient and asserts it no longer 500s
(it should reach the orchestrator path / return a structured response). Auth is
satisfied by monkeypatching the router auth dep — we're testing the handler
bug, not auth.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import aria_service.routes.aria as aria_routes
from aria_service.main import app

# R-F3754/§16 — NOT inspect.getsource: it slices at the line numbers captured
# AT IMPORT, so an edit mid-run returns a DIFFERENT function's body, silently.
from ._source_probe import function_source


@pytest.fixture()
def client_no_auth():
    # The router has a global auth dependency; override it so we exercise the
    # handler logic (the bug) rather than auth. We also stub the heavy
    # orchestrator so the test is fast + deterministic — the point is that the
    # pre-check no longer throws before we ever get there.
    app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(aria_routes._router_auth_dep, None)


def test_orchestrate_does_not_500_on_the_vault_precheck(client_no_auth):
    # Stub the orchestrator so we don't run a real 7-layer DD; return a minimal
    # report-like object. If the pre-check bug is present, we 500 BEFORE this is
    # ever called.
    from aria_service.intel.dd_schema import ARKDDReport
    from unittest.mock import MagicMock
    fake_report = ARKDDReport()
    fake_report.identity.entity_name = "Sonangol EP"
    with patch("aria_service.intel.dd_orchestrator.orchestrate_dd",
               AsyncMock(return_value=fake_report)), \
         patch.object(aria_routes, "get_llm", lambda req: MagicMock()), \
         patch("aria_service.intel.dd_vault.get_vault") as gv:
        gv.return_value.get_case.return_value = None  # no existing case
        resp = client_no_auth.post("/api/aria/dd/orchestrate", json={
            "name": "Sonangol EP", "type": "company",
            "jurisdiction_iso2": "AO", "mode": "quick", "force": True,
        })
    # The P0 was a guaranteed 500 from the vault pre-check, BEFORE the orchestrator
    # ran. The fix means we get past it; assert we no longer 500.
    assert resp.status_code != 500, f"vault pre-check regressed (500): {resp.text[:300]}"


def test_orchestrate_canonical_precheck_uses_keyword_args():
    """Guard the exact regression: the call must pass entity_type+name by keyword
    (canonical_entity_id is keyword-only), and the except must use _log."""
    import inspect
    src = function_source(aria_routes, "dd_orchestrate_ep")
    # Strip inline comments + the docstring so the guard checks CODE, not the
    # comments that document the old bug (those legitimately mention it).
    code_lines = []
    for ln in src.splitlines():
        code_lines.append(ln.split("#", 1)[0])
    code = "\n".join(code_lines)
    code = code[code.find('"""', code.find('"""') + 3) + 3:]  # drop the leading docstring

    assert "_canonical_id(_entity_name)" not in code, "positional keyword-only call reintroduced"
    assert "name=_entity_name" in code and "entity_type=" in code, "must call canonical_entity_id by keyword"
    # the non-fatal except must use the real module logger (_log), not `logger`
    assert "logger.debug(" not in code, "wrong logger name (NameError) reintroduced; use _log"
    assert "_log.debug(" in code
