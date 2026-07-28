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

from aria_service.routes.aria import _PUBLIC_AUTH_BYPASS_PATHS, require_aria_token


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
    """R-F3330 — assert the bypass PROPERTY, over the real set.

    This named "/api/aria/agents" as its example public path and had been red
    ever since R-F2140 (2026-06-29) removed that path from the allowlist,
    deliberately, as an agent-registry leak. Live-probed: an unauthenticated
    GET /api/aria/agents returns 401 in production, so the CODE is right and
    the test was pinning a fact it had copied instead of read.

    Reading the set at test time means the next legitimate change to the
    allowlist cannot make this test wrong again — and it now covers every
    member rather than one hand-picked example.
    """
    _clear_tokens(monkeypatch)
    monkeypatch.setenv("FLY_APP_NAME", "aria-intel")
    assert _PUBLIC_AUTH_BYPASS_PATHS, (
        "the allowlist is empty — this test would pass vacuously"
    )
    for path in sorted(_PUBLIC_AUTH_BYPASS_PATHS):
        # returns before the gate, so no token is needed even in production
        assert require_aria_token(_req(path=path)) is None, path


def test_rf3330_paths_removed_from_the_bypass_are_really_gated():
    """The other direction: a path REMOVED from the allowlist stays removed.

    R-F2140 took /agents, /capability-gaps, /vault and the mistake-ledger reads
    out of the bypass because each leaks operational posture (what ARIA knows
    she cannot do, the agent registry, vault contents) to anyone. Nothing
    asserted the removal, so the only trace it ever happened was a commented-out
    line in the allowlist and a test failing for seven weeks with no explanation
    attached. Re-adding any of these now fails here.
    """
    for path in ("/api/aria/agents",
                 "/api/aria/capability-gaps",
                 "/api/aria/capability-gaps/summary",
                 "/api/aria/self/mistakes/stats",
                 "/api/aria/self/mistakes/recent",
                 "/api/aria/vault",
                 "/api/aria/vault/stats"):
        assert path not in _PUBLIC_AUTH_BYPASS_PATHS, (
            f"{path} was removed from the public bypass by R-F2140 (operational "
            f"posture leak) and must not be public again"
        )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
