"""R-F1814 — C2: SSRF / local-file read fixed at the fetch boundary.

Authorization review C2 (CRITICAL): document_reader._resolve_source and
link_investigator._fetch_page fetched user-controlled URLs with follow_redirects=True
and NO is_safe_url guard, and _resolve_source fell back to os.path.exists(source) →
arbitrary local-file read.

Fix: a single SSRF-checked fetch boundary `url_safety.safe_get` (validates the url +
every redirect hop, follow_redirects off), used by both fetchers; the local-file
branch is restricted to the system temp dir (where uploads land).

Capability test drives the REAL helpers: safe_get blocks internal/loopback/metadata/
non-http + internal redirects (and never fetches them), allows public; _resolve_source
rejects an arbitrary local file and accepts a tempdir file.
"""
import asyncio
import os
import tempfile
import time

import pytest

from aria_service.intel import url_safety as us


class _Resp:
    def __init__(self, status=200, headers=None, url="http://93.184.216.34/"):
        self.status_code = status
        self.headers = headers or {}
        self.url = url


class _FakeClient:
    def __init__(self, script):
        self.calls = []
        self._script = list(script)
    async def get(self, u, **k):
        self.calls.append(u)
        return self._script.pop(0)


@pytest.mark.asyncio
async def test_safe_get_blocks_internal_and_never_fetches():
    c = _FakeClient([_Resp()])
    for bad in ["http://127.0.0.1/", "http://169.254.169.254/latest/meta",
                "http://aria-intel.internal/", "http://10.0.0.1/",
                "file:///etc/passwd", "http://[::1]/"]:
        with pytest.raises(ValueError) as e:
            await us.safe_get(c, bad)
        assert "ssrf_blocked" in str(e.value)
    assert c.calls == [], f"safe_get fetched a blocked URL: {c.calls}"


@pytest.mark.asyncio
async def test_safe_get_allows_public():
    c = _FakeClient([_Resp(200)])
    r = await us.safe_get(c, "http://93.184.216.34/")
    assert r.status_code == 200
    assert c.calls == ["http://93.184.216.34/"]


@pytest.mark.asyncio
async def test_async_url_verdict_preserves_ssrf_policy():
    """The async public facade allows public literals and blocks metadata IPs."""
    assert await us.is_safe_url_async("https://93.184.216.34/report") == (True, "")
    ok, reason = await us.is_safe_url_async("http://169.254.169.254/latest/meta")
    assert not ok
    assert "link_local" in reason


@pytest.mark.asyncio
async def test_safe_get_revalidates_redirect_to_internal():
    # public host 302-redirects to a metadata/internal IP — must block at the hop.
    redirect = _Resp(302, {"location": "http://169.254.169.254/"}, url="http://93.184.216.34/")
    c = _FakeClient([redirect])
    with pytest.raises(ValueError) as e:
        await us.safe_get(c, "http://93.184.216.34/")
    assert "ssrf_blocked" in str(e.value)
    assert c.calls == ["http://93.184.216.34/"]  # fetched once, then blocked the redirect


@pytest.mark.asyncio
async def test_safe_get_dns_miss_never_blocks_event_loop(monkeypatch):
    """A real safe_get cache miss resolves off-loop and still allows public IPs."""
    us._DNS_CACHE.clear()

    def _slow_public_resolution(host, port, *args, **kwargs):
        time.sleep(0.1)
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(us.socket, "getaddrinfo", _slow_public_resolution)
    c = _FakeClient([_Resp(200, url="https://slow-safe.example.com/")])
    task = asyncio.create_task(us.safe_get(c, "https://slow-safe.example.com/"))
    loop = asyncio.get_running_loop()
    last = loop.time()
    max_gap = 0.0
    while not task.done():
        await asyncio.sleep(0)
        now = loop.time()
        max_gap = max(max_gap, now - last)
        last = now

    response = await task
    assert response.status_code == 200
    assert max_gap < 0.05, f"DNS blocked the event loop for {max_gap:.3f}s"


@pytest.mark.asyncio
async def test_resolve_source_blocks_arbitrary_local_file():
    from aria_service.intel import document_reader as dr
    # A real file OUTSIDE the temp dir (this repo's server.mjs) must be rejected.
    outside = os.path.join(os.getcwd(), "server.mjs")
    assert os.path.exists(outside)
    assert await dr._resolve_source(outside) is None


@pytest.mark.asyncio
async def test_resolve_source_allows_tempdir_file():
    from aria_service.intel import document_reader as dr
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, prefix="aria_test_") as f:
        f.write(b"%PDF-1.4 test")
        p = f.name
    try:
        assert await dr._resolve_source(p) == os.path.realpath(p)
    finally:
        os.unlink(p)
