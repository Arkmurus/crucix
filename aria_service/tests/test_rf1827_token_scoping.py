"""R-F1827 — Phase 3 (staged): per-service token scoping (operator vs service tier).

Authorization review H1 (HIGH, architectural): ONE shared token grants full brain
control (autonomous/destructive) + the WA/web tiers hold it — a WA compromise = total
control. Staged fix: control/destructive routes require an OPERATOR token when
ARIA_TOKEN_SCOPING=1; the service token (WA/web) keeps chat/read/telemetry. OFF by
default (no behavior change until the flag + ARIA_OPERATOR_TOKEN secret are set).

Capability test drives the REAL router auth dep (require_aria_token).
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aria_service.routes import aria as A


def _req(path, token):
    return SimpleNamespace(
        headers={"Authorization": f"Bearer {token}"},
        url=SimpleNamespace(path=path),
    )


def test_scoping_off_is_backcompat(monkeypatch):
    monkeypatch.delenv("ARIA_TOKEN_SCOPING", raising=False)
    monkeypatch.setenv("ARIA_API_TOKEN", "svc")
    monkeypatch.setenv("ARIA_OPERATOR_TOKEN", "op")
    # OFF: service token works on a control path (current behavior preserved)
    A.require_aria_token(_req("/api/aria/autonomous/pause", "svc"))  # no raise


def test_scoping_on_blocks_service_token_on_control_plane(monkeypatch):
    monkeypatch.setenv("ARIA_TOKEN_SCOPING", "1")
    monkeypatch.setenv("ARIA_SERVICE_TOKEN", "svc")
    monkeypatch.setenv("ARIA_OPERATOR_TOKEN", "op")
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)

    # service token on a control/destructive path → 403
    for path in ("/api/aria/autonomous/pause", "/api/aria/self/deploy/x",
                 "/api/aria/cost/set-cap", "/api/aria/admin/purge-cases"):
        with pytest.raises(HTTPException) as e:
            A.require_aria_token(_req(path, "svc"))
        assert e.value.status_code == 403, path

    # operator token on the same control path → allowed
    A.require_aria_token(_req("/api/aria/autonomous/pause", "op"))
    # service token on a NON-control path (chat/read) → allowed
    A.require_aria_token(_req("/api/aria/chat", "svc"))
