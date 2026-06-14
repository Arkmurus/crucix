"""R-F1566 — require_aria_token must FAIL CLOSED in production.

Before: when neither ARIA_API_TOKEN nor ARIA_INTERNAL_TOKEN was set, the
dependency returned (served OPEN) everywhere — fine for local dev, but on fly
(where both secrets ARE normally set) an empty token set means the secrets were
cleared, and serving open then silently exposes every endpoint. Fix: detect
production via FLY_APP_NAME/FLY_MACHINE_ID and refuse (503) when no token is set;
keep the soft-rollout no-op only for local dev.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aria_service.routes.aria import require_aria_token


def _req(path="/api/aria/dd/run", auth=None):
    headers = {}
    if auth is not None:
        headers["Authorization"] = auth
    return SimpleNamespace(url=SimpleNamespace(path=path), headers=headers)


def _clear_tokens(mp):
    mp.delenv("ARIA_API_TOKEN", raising=False)
    mp.delenv("ARIA_INTERNAL_TOKEN", raising=False)


def test_prod_no_token_fails_closed(monkeypatch):
    _clear_tokens(monkeypatch)
    monkeypatch.setenv("FLY_APP_NAME", "aria-intel")  # production signal
    with pytest.raises(HTTPException) as ei:
        require_aria_token(_req())
    assert ei.value.status_code == 503


def test_dev_no_token_soft_rollout_open(monkeypatch):
    _clear_tokens(monkeypatch)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    # local dev: no token, no prod signal → no-op (returns None, no raise)
    assert require_aria_token(_req()) is None


def test_token_set_enforces_everywhere(monkeypatch):
    _clear_tokens(monkeypatch)
    monkeypatch.setenv("ARIA_API_TOKEN", "secret-xyz")
    monkeypatch.setenv("FLY_APP_NAME", "aria-intel")
    # missing/invalid bearer → 401
    with pytest.raises(HTTPException) as ei:
        require_aria_token(_req(auth=None))
    assert ei.value.status_code == 401
    with pytest.raises(HTTPException):
        require_aria_token(_req(auth="Bearer wrong"))
    # correct token → ok
    assert require_aria_token(_req(auth="Bearer secret-xyz")) is None


def test_public_bypass_still_open_in_prod(monkeypatch):
    _clear_tokens(monkeypatch)
    monkeypatch.setenv("FLY_APP_NAME", "aria-intel")
    # a public-bypass path must still be reachable (it returns before the gate)
    assert require_aria_token(_req(path="/api/aria/agents")) is None


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
