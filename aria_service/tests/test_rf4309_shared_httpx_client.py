"""R-F4309 — web_search reuses ONE shared httpx client instead of building a
fresh one (and a fresh TLS context) per request.

THE DEFECT (measured, not inferred). Every search backend did
`async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:` — 7 hot-path
sites. Each construction calls `ssl.create_default_context`, which reads the CA
bundle from disk SYNCHRONOUSLY on the event loop; the live continuous profiler
measured it at ~10% of the hot path. The autonomous loops (student reading,
web_explorer) hammer these backends, so the per-request TLS rebuild was a real,
recurring cost.

THE FIX. A module-level shared client, reused across all 7 backends.

WHAT THESE TESTS ARE ACTUALLY FOR. Sharing an object changes its LIFETIME, not
just its cost, and each of the three lifetime properties below was a live defect
in the first cut of this change. They are pinned here because every one of them
fails SILENTLY:

  (a) WHO RESETS IT. A test that monkeypatches `httpx.AsyncClient` and restores
      it left its fake INSTANCE installed in the module global forever —
      monkeypatch restores the attribute, not the object already built from it.
      Measured: `test_rf1614_make_loud`'s `_BoomClient` leaked into
      `test_rf2318_brave_dd_search::test_search_brave_parses`, which failed with
      "Brave search failed: bing backend down". Worse than one red test — every
      LATER test's own patch was bypassed, so tests that only assert "a failure
      was wired" passed while certifying nothing.
  (b) WHAT IF IT IS CLOSED. Rebuilding only on `is None` would let
      `close_shared_client` (wired to app shutdown) permanently poison the client
      for any task still in flight.
  (c) WHAT IT CARRIES BETWEEN CALLERS. A shared client keeps a COOKIE JAR. With
      `random_ua()` rotating the User-Agent per request, one persistent session
      presenting a different browser every time is a fingerprint inconsistency,
      against exactly the engines §27 records blocking us on bot signals.

Plus the redirect contract: the 3 feed backends now opt into redirect-following
per REQUEST rather than on the client. A feed that stops following redirects
returns EMPTY, which reads as "nothing found" — so this is pinned by calling the
backends, not by counting strings in the source. The first version of this test
asserted `src.count("follow_redirects=True") >= 3` while FOUR matched and one was
a comment; dropping a real one left 3 and the assertion still passed.
"""
import ast
import asyncio
import inspect

import httpx
import pytest

from aria_service.intel import web_search as ws

from . import _source_probe


@pytest.fixture(autouse=True)
def _clean_shared_client():
    """Every test here starts and ends with no shared client installed.

    Scoped to this file deliberately — the module is expected to survive a
    dirty global on its own (that is property (a)); a suite-wide fixture would
    hide the very defect these tests exist to pin.
    """
    ws._reset_shared_client()
    yield
    ws._reset_shared_client()


# ── the point of the change ──────────────────────────────────────────────────

async def test_shared_client_is_reused_across_calls():
    """UNIT: _get_shared_client returns the SAME client every call.

    If each call built a fresh client the TLS context would be rebuilt per
    request and the hot-path cost R-F4309 removes would remain.
    """
    c1 = ws._get_shared_client()
    c2 = ws._get_shared_client()
    assert c1 is c2, (
        "_get_shared_client must return the same client across calls — a fresh "
        "client per call rebuilds the TLS context, which is the ~10% hot-path "
        "cost R-F4309 removes"
    )


async def test_shared_client_cm_yields_the_shared_client_without_closing():
    """UNIT: the context manager yields the shared client and does NOT close it.

    The old `async with httpx.AsyncClient(...)` closed on block exit. The
    replacement must not, or the very next backend gets a closed client.
    """
    shared = ws._get_shared_client()
    async with ws._shared_client_cm() as client:
        assert client is shared, "_shared_client_cm must yield the shared client"
    assert ws._get_shared_client() is shared, (
        "the context manager must not close the shared client on exit"
    )
    assert not shared.is_closed


def test_hot_path_backends_do_not_build_their_own_client():
    """CAPABILITY: none of the 7 hot-path backends constructs an httpx client.

    Read by AST over the CURRENT file, per function — not by counting strings.
    A backend that regressed to `httpx.AsyncClient(...)` brings the per-request
    TLS rebuild back, and would be invisible to a whole-module string count.
    """
    tree = ast.parse(_source_probe.module_source(ws))
    backends = {
        "_search_brave", "_search_searxng", "_search_gnews_api",
        "_search_google_news", "_search_gdelt", "_search_duckduckgo",
        "_search_bing_news",
    }
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in backends:
            found[node.name] = any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and c.func.attr == "AsyncClient"
                for c in ast.walk(node)
            )
    missing = backends - set(found)
    assert not missing, f"backend(s) not found in web_search — renamed? {missing}"
    offenders = sorted(n for n, builds in found.items() if builds)
    assert not offenders, (
        f"these backends build their own httpx client: {offenders} — each "
        "construction rebuilds the TLS context on the event loop, which is the "
        "cost R-F4309 removes. Use `async with _shared_client_cm() as client:`."
    )


def test_health_probe_keeps_its_own_short_lived_client():
    """CAPABILITY: the timeout=5.0 health probe is deliberately NOT shared.

    Different timeout, not on the hot path. Pinned so a later tidy-up does not
    fold it into the 12s shared client and slow every health check.
    """
    src = _source_probe.code_only(_source_probe.module_source(ws))
    assert "httpx.AsyncClient(timeout=5.0)" in src, (
        "the health probe must keep its own short-lived client (timeout=5.0)"
    )


# ── (a) who resets it ────────────────────────────────────────────────────────

def test_a_fake_client_does_not_outlive_the_patch_that_installed_it():
    """CAPABILITY — the regression that made this change red.

    Reproduces `test_rf1614_make_loud` exactly: patch httpx.AsyncClient with a
    fake, drive a real backend, restore the attribute. The NEXT caller must get
    a genuine client. Before the provenance key, it got the fake — forever —
    and `test_rf2318_brave_dd_search` failed with "bing backend down".
    """
    class _BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise RuntimeError("bing backend down")

    original = httpx.AsyncClient
    httpx.AsyncClient = _BoomClient
    try:
        assert asyncio.run(ws._search_bing_news("x", max_results=2)) == []
        assert isinstance(ws._shared_client, _BoomClient), (
            "the patch must take effect while it is installed, or this test "
            "proves nothing about what happens after it is removed"
        )
    finally:
        httpx.AsyncClient = original  # what monkeypatch does at teardown

    async def _who():
        async with ws._shared_client_cm() as client:
            return client

    got = asyncio.run(_who())
    assert not isinstance(got, _BoomClient), (
        "a client built while httpx.AsyncClient was patched must NOT survive the "
        "patch — it silently disarms every later test's own patch and made "
        "test_rf2318_brave_dd_search fail with another backend's error"
    )
    assert isinstance(got, httpx.AsyncClient)


# ── (b) what if it is closed ─────────────────────────────────────────────────

async def test_a_closed_client_rebuilds_instead_of_poisoning_the_process():
    """CAPABILITY: a closed shared client is replaced, not handed out again.

    `close_shared_client` runs at app shutdown. A task still in flight would
    otherwise raise "Cannot send a request, as the client has been closed" for
    the life of the process, because the global is not None.
    """
    c1 = ws._get_shared_client()
    await c1.aclose()  # closed by something other than close_shared_client
    c2 = ws._get_shared_client()
    assert c2 is not c1 and not c2.is_closed, (
        "a closed shared client must be rebuilt — rebuilding only on `is None` "
        "turns one shutdown race into a permanently dead search path"
    )


async def test_close_shared_client_is_idempotent_and_rebuilds():
    """UNIT: closing twice must not raise, and the next call rebuilds."""
    c1 = ws._get_shared_client()
    await ws.close_shared_client()
    await ws.close_shared_client()  # second close is a no-op
    assert c1.is_closed
    c2 = ws._get_shared_client()
    assert c2 is not c1 and not c2.is_closed
    await ws.close_shared_client()


# ── (c) what it carries between callers ──────────────────────────────────────

async def test_the_shared_client_never_stores_a_cookie():
    """CAPABILITY: Set-Cookie is dropped, so backends stay session-free.

    Drives httpx's real cookie path — `Cookies.extract_cookies(response)` is
    exactly what `AsyncClient._send_handling_redirects` calls. A per-request
    client was cookie-free by accident; the shared one must be so deliberately,
    because `random_ua()` rotates the User-Agent and a persistent session under
    a changing browser is the bot signal that gets these engines to block us.
    """
    client = ws._get_shared_client()
    request = httpx.Request("GET", "https://example.com/search")
    response = httpx.Response(
        200, headers={"Set-Cookie": "sess=abc123; Path=/"}, request=request
    )

    client.cookies.extract_cookies(response)

    assert len(client.cookies.jar) == 0, (
        "the shared client must not retain Set-Cookie — a persistent session "
        "combined with a rotating User-Agent is a fingerprint inconsistency "
        "against the engines §27 records blocking us"
    )
    assert isinstance(client.cookies.jar, ws._NoStoreCookieJar)


# ── the redirect contract, driven through the backends ───────────────────────

class _RecordingClient:
    """Records request kwargs and returns an empty 200, for redirect assertions."""

    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _record(self, method, url, **kwargs):
        type(self).calls.append({"method": method, "url": url, **kwargs})
        return httpx.Response(200, text="", request=httpx.Request(method.upper(), url))

    async def get(self, url, **kwargs):
        return await self._record("get", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self._record("post", url, **kwargs)


@pytest.mark.parametrize(
    "backend_name",
    ["_search_google_news", "_search_duckduckgo", "_search_bing_news"],
)
def test_feed_backends_request_redirect_following(monkeypatch, backend_name):
    """CAPABILITY: each feed backend passes follow_redirects=True on the REQUEST.

    These three fetch RSS/HTML behind interstitial 302s (F78c). They used to set
    it on the client; the shared client has one default, so it must now be a
    per-request argument. Losing it returns EMPTY, which reads as "nothing
    found" — a silent failure, hence one assertion per backend rather than a
    count that a comment can satisfy.
    """
    _RecordingClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _RecordingClient)

    asyncio.run(getattr(ws, backend_name)("test query", max_results=5))

    assert _RecordingClient.calls, f"{backend_name} made no request"
    assert all(c.get("follow_redirects") is True for c in _RecordingClient.calls), (
        f"{backend_name} must pass follow_redirects=True on the request — this "
        f"site redirects, and without it the feed returns empty, which reads as "
        f"'nothing found'. Recorded: {_RecordingClient.calls}"
    )


def test_a_non_feed_backend_does_not_request_redirect_following(monkeypatch):
    """CAPABILITY: the redirect opt-in is per-backend, not blanket.

    Proves the parametrised test above is discriminating: if every backend
    passed follow_redirects=True it would pass while asserting nothing. gdelt
    is a JSON API and never needed redirects.
    """
    _RecordingClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _RecordingClient)

    asyncio.run(ws._search_gdelt("test query", max_results=5))

    assert _RecordingClient.calls, "_search_gdelt made no request"
    assert all(c.get("follow_redirects") is None for c in _RecordingClient.calls), (
        "_search_gdelt must not opt into redirect-following — if it does, the "
        "feed-backend assertion above stops discriminating"
    )


# ── the pool bound is declared, not inherited ────────────────────────────────

def test_pool_limits_are_stated_explicitly(monkeypatch):
    """CAPABILITY: the declared limits actually reach the client's pool.

    The pool is now GLOBAL across all 7 backends and pool-wait time counts
    against REQUEST_TIMEOUT, so a saturated pool surfaces as a backend timeout.

    Asserted by moving SHARED_CLIENT_LIMITS to a value httpx would never pick.
    The first version of this test asserted the pool read 100/20 — which is
    exactly httpx's DEFAULT_LIMITS, so deleting `limits=SHARED_CLIENT_LIMITS`
    left it GREEN. Mutation-tested: it now goes red on that deletion.
    """
    assert ws.SHARED_CLIENT_LIMITS.max_connections == 100
    assert ws.SHARED_CLIENT_LIMITS.max_keepalive_connections == 20

    monkeypatch.setattr(
        ws, "SHARED_CLIENT_LIMITS",
        httpx.Limits(max_connections=7, max_keepalive_connections=3),
    )
    ws._reset_shared_client()
    pool = ws._get_shared_client()._transport._pool
    assert (pool._max_connections, pool._max_keepalive_connections) == (7, 3), (
        "the declared SHARED_CLIENT_LIMITS must be passed to the client — "
        "without it the pool silently inherits httpx's DEFAULT_LIMITS, which "
        "can move under a version bump with no line in this repo changing"
    )


def test_close_shared_client_is_wired_to_app_shutdown():
    """CAPABILITY: something actually calls close_shared_client.

    A shutdown hook nobody invokes is the same as no hook. Asserted against
    main.py because that is the only place with a lifespan to hang it on.
    """
    main_src = _source_probe.repo_path("aria_service/main.py").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "close_shared_client" in main_src, (
        "close_shared_client must be called from the lifespan shutdown path — "
        "otherwise the shared client's connection pool is never released"
    )
