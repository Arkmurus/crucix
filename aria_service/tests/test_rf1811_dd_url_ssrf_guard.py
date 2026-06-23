"""R-F1811 — SSRF guard on _extract_entity_from_url (confirmed by the R-F1808 IAST run).

The DD entity-extraction fetch took an attacker-controlled URL (target.url /
target.website) and did httpx.get(url) with follow_redirects=True and NO guard —
a server-side request forgery: a DD request on website=http://aria-intel.internal:8000/
or http://127.0.0.1:<port>/ made the brain fetch internal/localhost-trusted endpoints
and return their content.

Capability test: drive the REAL guard + the REAL _extract_entity_from_url and assert
(1) the guard classifies internal/loopback/private/link-local/.internal/non-http as
unsafe and public IPs as safe, and (2) a blocked URL NEVER constructs an httpx client
(no outbound request is made).
"""
import pytest


def test_ssrf_guard_classification():
    from aria_service.intel.dd_orchestrator import _ssrf_safe_url
    blocked = [
        "http://127.0.0.1:8000/x",            # loopback
        "http://10.0.0.5/",                    # private A
        "http://192.168.1.10/",                # private C
        "http://169.254.169.254/latest/meta", # link-local / metadata
        "http://localhost/admin",              # localhost name
        "http://aria-intel.internal:8000/",    # fly internal
        "http://[::1]/",                       # ipv6 loopback
        "file:///etc/passwd",                  # non-http scheme
        "gopher://x/",                         # non-http scheme
    ]
    for u in blocked:
        ok, why = _ssrf_safe_url(u)
        assert ok is False, f"SSRF guard should BLOCK {u} (got ok=True)"

    # A public IP literal must pass (no DNS needed, deterministic offline).
    ok, why = _ssrf_safe_url("http://93.184.216.34/")  # public
    assert ok is True, f"public IP should pass, got {why}"


@pytest.mark.asyncio
async def test_blocked_url_never_fetches(monkeypatch):
    from aria_service.intel import dd_orchestrator as ddo

    constructed = {"n": 0}

    class _BoomClient:
        def __init__(self, *a, **k):
            constructed["n"] += 1
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, *a, **k):
            raise AssertionError("SSRF guard FAILED — a blocked URL was fetched")

    monkeypatch.setattr("httpx.AsyncClient", _BoomClient)

    for bad in ["http://127.0.0.1:8000/x", "http://aria-intel.internal:8000/",
                "http://169.254.169.254/", "http://10.0.0.5/", "file:///etc/passwd"]:
        res = await ddo._extract_entity_from_url(bad)
        # blocked → returns the domain-derived fallback, never the fetched title
        assert res["name"] == res["_fallback_name"]

    assert constructed["n"] == 0, (
        f"SSRF guard failed — httpx.AsyncClient was constructed {constructed['n']}x "
        f"for blocked URLs (should be 0)"
    )
