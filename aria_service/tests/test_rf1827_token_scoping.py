"""R-F1827 — Phase 3 (staged): per-service token scoping (operator vs service tier).

Authorization review H1 (HIGH, architectural): ONE shared token grants full brain
control (autonomous/destructive) + the WA/web tiers hold it — a WA compromise = total
control. Fix: control/destructive routes require an OPERATOR token; the service
token (WA/web) keeps chat/read/telemetry.

R-F3330 — this header used to end "OFF by default (no behavior change until the
flag + ARIA_OPERATOR_TOKEN secret are set)", and `test_scoping_off_is_backcompat`
asserted exactly that. Both were superseded by R-F2139, which flipped scoping ON
by default whenever ARIA_OPERATOR_TOKEN is set (routes/aria.py:485; the opt-OUT
is now ARIA_TOKEN_SCOPING=0). That test had been red ever since, asserting the
negation of the live security posture — verified against production, where
ARIA_OPERATOR_TOKEN is Deployed on aria-intel and distinct from the service
token, so scoping IS active. It is removed rather than repaired: the property it
wanted (explicit opt-out still works) is already pinned by
test_rf2139_scoping_disabled_via_env, so repairing it would have created a second
place to maintain one fact. A stale test that contradicts a live security
default is worse than no test — it argues, in the suite, for reopening the hole.

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


def test_scoping_on_blocks_service_token_on_control_plane(monkeypatch):
    # R-F3330: ARIA_TOKEN_SCOPING is deliberately NOT set here any more. It used
    # to be forced to "1", which meant this test would still pass if the R-F2139
    # default were flipped back to opt-in — it pinned the path list but not the
    # posture. Unset, it now asserts BOTH: that these paths are operator-only,
    # and that they are so by DEFAULT, which is what production runs.
    monkeypatch.delenv("ARIA_TOKEN_SCOPING", raising=False)
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
