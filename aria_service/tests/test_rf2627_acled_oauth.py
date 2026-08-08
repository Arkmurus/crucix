"""R-F2627 — conflict_tracker's ACLED client targeted a DEPRECATED API.

THE BUG:
  conflict_tracker._fetch_acled called the LEGACY host with LEGACY auth:
      GET https://api.acleddata.com/acled/read?key=<KEY>&email=<EMAIL>
  ACLED's current API (verified against acleddata.com/api-documentation/
  getting-started, 2026-07-15) is:
      POST https://acleddata.com/oauth/token
           username / password / grant_type=password / client_id=acled
        -> access_token (24h) + refresh_token (14d)
      GET  https://acleddata.com/api/acled/read   (Authorization: Bearer <token>)
  The legacy key+email host is absent from current docs. So every ACLED call
  failed and SILENTLY fell back to GDELT — ARIA served lower-fidelity data while
  reporting nothing wrong, and Phase A gate #5 could never close.

Credentials (CLAUDE.md §18): ACLED_EMAIL + ACLED_PASSWORD (a myACLED account).
ACLED_API_KEY is dead — the API it authenticated no longer exists.

NOTE: live auth CANNOT be tested without real credentials. These tests pin the
REQUEST CONTRACT (endpoint, params, headers) against the documented API, plus
the honesty behaviour (§21a) — unconfigured is not an error, but a configured
provider that FAILS must reach the brain.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel import conflict_tracker as ct

# R-F3781/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _mk_client(post_resp=None, get_resp=None):
    """Build a mock httpx.AsyncClient usable as `async with`."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=post_resp)
    client.get = AsyncMock(return_value=get_resp)
    return client


def _resp(status=200, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=payload or {})
    r.text = text
    return r


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("ACLED_EMAIL", raising=False)
    monkeypatch.delenv("ACLED_PASSWORD", raising=False)
    monkeypatch.delenv("ACLED_API_KEY", raising=False)
    ct._invalidate_token_cache() if hasattr(ct, "_invalidate_token_cache") else None
    yield


async def test_rf2627_deprecated_legacy_host_is_not_called():
    """The dead endpoint must not be USED.

    Checks the endpoint CONSTANTS and every executable line — a docstring that
    documents the migration is fine and useful; a live reference is the bug.
    """
    import inspect

    assert ct.ACLED_TOKEN_URL == "https://acleddata.com/oauth/token", (
        f"wrong OAuth token endpoint: {ct.ACLED_TOKEN_URL}"
    )
    assert ct.ACLED_BASE == "https://acleddata.com/api/acled/read", (
        f"ACLED_BASE still points at the wrong host: {ct.ACLED_BASE}"
    )

    # No EXECUTABLE line may mention the dead host (comments/docstrings may).
    src = module_source(ct)
    in_doc = False
    offenders = []
    for i, raw in enumerate(src.splitlines(), 1):
        line = raw.strip()
        if line.startswith(('"""', "'''")) or line.endswith(('"""', "'''")):
            # crude but sufficient: toggle on docstring fences
            if line.count('"""') == 1 or line.count("'''") == 1:
                in_doc = not in_doc
            continue
        if in_doc or line.startswith("#"):
            continue
        if "api.acleddata.com" in line:
            offenders.append(f"{i}: {line[:70]}")

    assert not offenders, (
        "conflict_tracker still CALLS the DEPRECATED api.acleddata.com host "
        f"(that API is gone -> every ACLED call silently degrades to GDELT): {offenders}"
    )


async def test_rf2627_token_request_matches_documented_oauth_contract(monkeypatch):
    """POST /oauth/token with exactly the documented params."""
    monkeypatch.setenv("ACLED_EMAIL", "ops@example.com")
    monkeypatch.setenv("ACLED_PASSWORD", "s3cret")

    post_resp = _resp(200, {"access_token": "TOK123", "refresh_token": "REF", "expires_in": 86400})
    client = _mk_client(post_resp=post_resp)

    with patch.object(ct.httpx, "AsyncClient", return_value=client):
        token = await ct._acled_token()

    assert token == "TOK123", f"expected the access_token, got {token!r}"
    url = client.post.call_args[0][0] if client.post.call_args[0] else client.post.call_args.kwargs.get("url")
    assert url == "https://acleddata.com/oauth/token", f"wrong token endpoint: {url}"
    data = client.post.call_args.kwargs.get("data") or {}
    assert data.get("grant_type") == "password", f"grant_type must be 'password': {data}"
    assert data.get("client_id") == "acled", f"client_id must be 'acled': {data}"
    assert data.get("username") == "ops@example.com", f"username must be the email: {data}"
    assert data.get("password") == "s3cret", f"password must be sent: {data}"


async def test_rf2627_fetch_uses_current_base_and_bearer_header(monkeypatch):
    """GET the CURRENT data endpoint with a Bearer token — not key+email params."""
    monkeypatch.setenv("ACLED_EMAIL", "ops@example.com")
    monkeypatch.setenv("ACLED_PASSWORD", "s3cret")

    post_resp = _resp(200, {"access_token": "TOK123", "expires_in": 86400})
    get_resp = _resp(200, {"data": [{"event_date": "2026-07-01", "country": "Angola"}]})
    client = _mk_client(post_resp=post_resp, get_resp=get_resp)

    with patch.object(ct.httpx, "AsyncClient", return_value=client):
        events = await ct._fetch_acled("AGO", days=30)

    assert events, "ACLED returned data but _fetch_acled produced nothing"
    url = client.get.call_args[0][0] if client.get.call_args[0] else client.get.call_args.kwargs.get("url")
    assert url == "https://acleddata.com/api/acled/read", f"wrong data endpoint: {url}"
    headers = client.get.call_args.kwargs.get("headers") or {}
    assert headers.get("Authorization") == "Bearer TOK123", f"missing Bearer auth: {headers}"
    params = client.get.call_args.kwargs.get("params") or {}
    assert "key" not in params and "email" not in params, (
        f"legacy key/email auth params must be gone: {params}"
    )


async def test_rf2627_unconfigured_is_not_an_error(monkeypatch):
    """NO credentials = not yet set up (gate #5 pending). Must fall back to GDELT
    QUIETLY — never spam the brain with a failure for an unconfigured provider."""
    calls = []
    with patch.object(ct, "_fetch_gdelt_fallback", AsyncMock(return_value=[{"_provider": "gdelt"}])), \
         patch.object(ct, "wire_failure", MagicMock(side_effect=lambda **k: calls.append(k))):
        out = await ct._fetch_acled("AGO", days=30)

    assert out and out[0].get("_provider") == "gdelt", "must fall back to GDELT when unconfigured"
    assert not calls, f"unconfigured provider must NOT record a failure gap: {calls}"


async def test_rf2627_configured_but_failing_reaches_the_brain(monkeypatch):
    """§21a: credentials ARE set but ACLED fails -> the brain MUST know.
    Silent degradation to GDELT is exactly how this bug hid for months."""
    monkeypatch.setenv("ACLED_EMAIL", "ops@example.com")
    monkeypatch.setenv("ACLED_PASSWORD", "s3cret")

    post_resp = _resp(401, {}, text="invalid_grant")
    client = _mk_client(post_resp=post_resp)
    calls = []

    with patch.object(ct.httpx, "AsyncClient", return_value=client), \
         patch.object(ct, "_fetch_gdelt_fallback", AsyncMock(return_value=[{"_provider": "gdelt"}])), \
         patch.object(ct, "wire_failure", MagicMock(side_effect=lambda **k: calls.append(k))):
        out = await ct._fetch_acled("AGO", days=30)

    assert out and out[0].get("_provider") == "gdelt", "must still serve GDELT (§14 fallback)"
    assert calls, (
        "ACLED auth FAILED with credentials configured and the brain was never told — "
        "that is DARK per §21a and is how the dead legacy API stayed hidden"
    )


async def test_rf2627_token_is_cached_across_calls(monkeypatch):
    """Re-authenticating on every fetch would hammer ACLED and risk rate limits.
    The access token is valid 24h — cache it."""
    monkeypatch.setenv("ACLED_EMAIL", "ops@example.com")
    monkeypatch.setenv("ACLED_PASSWORD", "s3cret")

    post_resp = _resp(200, {"access_token": "TOK123", "expires_in": 86400})
    get_resp = _resp(200, {"data": []})
    client = _mk_client(post_resp=post_resp, get_resp=get_resp)

    with patch.object(ct.httpx, "AsyncClient", return_value=client):
        await ct._fetch_acled("AGO", days=30)
        await ct._fetch_acled("MOZ", days=30)

    assert client.post.call_count == 1, (
        f"token must be cached — re-authenticated {client.post.call_count} times"
    )


async def test_rf2627_acled_events_are_provenance_tagged(monkeypatch):
    """GDELT rows carry _provider='gdelt'. ACLED rows must say so too, or a
    consumer cannot tell which source served the verdict."""
    monkeypatch.setenv("ACLED_EMAIL", "ops@example.com")
    monkeypatch.setenv("ACLED_PASSWORD", "s3cret")

    post_resp = _resp(200, {"access_token": "TOK123", "expires_in": 86400})
    get_resp = _resp(200, {"data": [{"event_date": "2026-07-01", "country": "Angola"}]})
    client = _mk_client(post_resp=post_resp, get_resp=get_resp)

    with patch.object(ct.httpx, "AsyncClient", return_value=client):
        events = await ct._fetch_acled("AGO", days=30)

    assert events[0].get("_provider") == "acled", (
        f"ACLED events must be tagged _provider='acled': {events[0]}"
    )
