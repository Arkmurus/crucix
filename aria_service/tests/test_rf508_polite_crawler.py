"""R-F508 — polite + quiet crawler.

Tests cover:
  - fetch_for_crawl returns ok dict on 200
  - fetch_for_crawl breaks down 4xx / 5xx / timeout into status_class
  - fetch_for_crawl uses the ARIA-Search-Bot UA (not random Chrome)
  - fetch_for_crawl honours robots-disallow (returns robots_blocked)
  - fetch_for_crawl thin-content returns "thin" not "ok"
  - fetch_for_crawl does NOT fall back to Wayback (no archive.is hit)
  - _strip_html_to_text extracts title + body, drops script/nav/style
  - runner.crawl_seed_homepages returns by_status breakdown
  - runner.crawl_loop honours startup_delay_sec
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from aria_service.search_index import db
from aria_service.crawler import fetcher, runner, politeness


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def db_ready():
    _run(db._reset())
    _run(politeness.reset_bucket())
    ok = _run(db.connect(":memory:"))
    assert ok
    yield db
    _run(db._reset())
    _run(politeness.reset_bucket())


# ─────────────────────────────────────────────────────────────────
# _strip_html_to_text
# ─────────────────────────────────────────────────────────────────

def test_strip_html_extracts_title_and_body():
    html = """
    <html><head><title>Modirum Gespi — contract win</title></head>
    <body>
      <nav>menu items</nav>
      <h1>Modirum Gespi awarded contract</h1>
      <p>The Angolan Ministry of Defence has awarded Modirum Gespi
         a three-year integration contract.</p>
      <script>alert('x')</script>
      <footer>footer junk</footer>
    </body></html>
    """
    title, headings, body = fetcher._strip_html_to_text(html)
    assert "Modirum Gespi" in title
    assert "Modirum Gespi awarded" in headings
    assert "Angolan Ministry of Defence" in body
    # Script/nav/footer must NOT leak into body.
    assert "alert" not in body
    assert "menu items" not in body
    assert "footer junk" not in body


def test_strip_html_empty_returns_blanks():
    assert fetcher._strip_html_to_text("") == ("", "", "")


def test_strip_html_caps_at_max_bytes():
    huge = "<html><body>" + ("x" * 1_000_000) + "</body></html>"
    _, _, body = fetcher._strip_html_to_text(huge, max_bytes=10_000)
    assert len(body.encode("utf-8")) <= 10_000


# ─────────────────────────────────────────────────────────────────
# fetch_for_crawl — uses a custom MockTransport so no live HTTP
# ─────────────────────────────────────────────────────────────────

def _mount_transport(monkeypatch, responder):
    """Patch httpx.AsyncClient to use a MockTransport-backed instance."""
    original = httpx.AsyncClient

    class _Client(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(responder)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


def test_fetch_for_crawl_returns_ok_on_200(db_ready, monkeypatch):
    async def fake_robots(domain, timeout=10.0):
        return ""
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)

    def responder(req: httpx.Request) -> httpx.Response:
        assert "ARIA-Search-Bot/1.0" in req.headers.get("user-agent", ""), (
            f"crawler UA missing — got {req.headers.get('user-agent')}"
        )
        return httpx.Response(200, text=(
            "<html><head><title>About</title></head>"
            "<body><h1>About</h1>"
            "<p>This is the about page for a defence company that "
            "delivers tactical communications systems to ministries "
            "of defence across multiple regions. Contract awards "
            "include integration programmes spanning several years.</p>"
            "</body></html>"
        ))
    _mount_transport(monkeypatch, responder)

    async def go():
        await db.register_domain("good.example", tier=2, sector="defence",
                                 language="en")
        result = await fetcher.fetch_for_crawl("https://good.example/")
        assert result is not None
        assert result["extraction_ok"] is True
        assert result["status_class"] == "ok"
        assert result["status_code"] == 200
        assert result["title"] == "About"
        assert "tactical communications" in result["body"]
        assert result["source_tier"] == 2
        # mark_domain_crawled fired.
        d = await db.get_domain("good.example")
        assert d["last_crawled_at"] is not None
    _run(go())


def test_fetch_for_crawl_marks_4xx_not_ok(db_ready, monkeypatch):
    async def fake_robots(domain, timeout=10.0):
        return ""
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)

    def responder(req):
        return httpx.Response(403, text="<html>forbidden</html>")
    _mount_transport(monkeypatch, responder)

    async def go():
        await db.register_domain("blocked.example", tier=2, sector="defence",
                                 language="en")
        result = await fetcher.fetch_for_crawl("https://blocked.example/")
        assert result is not None
        assert result["extraction_ok"] is False
        assert result["status_class"] == "4xx"
        assert result["status_code"] == 403
        # mark_domain_crawled still fired so we don't loop on this host.
        d = await db.get_domain("blocked.example")
        assert d["last_crawled_at"] is not None
    _run(go())


def test_fetch_for_crawl_marks_5xx(db_ready, monkeypatch):
    async def fake_robots(domain, timeout=10.0):
        return ""
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)
    _mount_transport(monkeypatch,
                     lambda req: httpx.Response(503, text="down"))
    async def go():
        await db.register_domain("sick.example", tier=2, sector="defence",
                                 language="en")
        result = await fetcher.fetch_for_crawl("https://sick.example/")
        assert result["status_class"] == "5xx"
        assert result["status_code"] == 503
    _run(go())


def test_fetch_for_crawl_timeout_class(db_ready, monkeypatch):
    async def fake_robots(domain, timeout=10.0):
        return ""
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)

    def responder(req):
        raise httpx.ReadTimeout("read timeout", request=req)
    _mount_transport(monkeypatch, responder)

    async def go():
        await db.register_domain("slow.example", tier=2, sector="defence",
                                 language="en")
        result = await fetcher.fetch_for_crawl("https://slow.example/",
                                                 timeout=1.0)
        assert result["status_class"] == "timeout"
        assert result["extraction_ok"] is False
    _run(go())


def test_fetch_for_crawl_thin_body(db_ready, monkeypatch):
    async def fake_robots(domain, timeout=10.0):
        return ""
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)
    _mount_transport(monkeypatch, lambda req: httpx.Response(200,
                     text="<html><body>hi</body></html>"))
    async def go():
        await db.register_domain("thin.example", tier=2, sector="defence",
                                 language="en")
        result = await fetcher.fetch_for_crawl("https://thin.example/")
        assert result["status_class"] == "thin"
        assert result["extraction_ok"] is False
    _run(go())


def test_fetch_for_crawl_robots_blocked(db_ready, monkeypatch):
    async def fake_robots(domain, timeout=10.0):
        return "User-agent: *\nDisallow: /\n"
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)
    # Even if the transport were called, this should not reach it.
    _mount_transport(monkeypatch, lambda req: pytest.fail(
        "fetch_for_crawl must not hit network when robots disallows"))

    async def go():
        await db.register_domain("strict.example", tier=2, sector="defence",
                                 language="en")
        result = await fetcher.fetch_for_crawl("https://strict.example/")
        assert result is not None
        assert result["status_class"] == "robots_blocked"
        assert result["extraction_ok"] is False
    _run(go())


def test_fetch_for_crawl_does_not_hit_wayback(db_ready, monkeypatch):
    """The whole point of R-F508: 403 must NOT chain to archive.is."""
    async def fake_robots(domain, timeout=10.0):
        return ""
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)

    calls: list[str] = []

    def responder(req):
        calls.append(str(req.url))
        return httpx.Response(403, text="forbidden")
    _mount_transport(monkeypatch, responder)

    async def go():
        await db.register_domain("forbid.example", tier=1, sector="defence",
                                 language="en")
        await fetcher.fetch_for_crawl("https://forbid.example/")
        # Exactly one outbound call. No Wayback / archive.is followup.
        # (robots.txt is a separate fetch through fake_robots, not the
        # MockTransport).
        wayback_hits = [u for u in calls
                        if "archive.org" in u or "archive.is" in u]
        assert wayback_hits == [], (
            f"crawler must not fall back to Wayback; got: {wayback_hits}"
        )
    _run(go())


def test_fetch_for_crawl_skips_unregistered(db_ready, monkeypatch):
    """Even with the new path, only curated/auto-registered domains
    are eligible."""
    async def fake_robots(domain, timeout=10.0):
        return ""
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)
    _mount_transport(monkeypatch, lambda req: pytest.fail(
        "unregistered domain must not be fetched"))

    async def go():
        result = await fetcher.fetch_for_crawl("https://strange.example/")
        assert result is None
    _run(go())


# ─────────────────────────────────────────────────────────────────
# runner — by_status breakdown
# ─────────────────────────────────────────────────────────────────

def test_runner_aggregates_by_status(db_ready, monkeypatch):
    async def fake_robots(domain, timeout=10.0):
        return ""
    monkeypatch.setattr(politeness, "_fetch_robots_txt", fake_robots)

    def responder(req):
        host = req.url.host
        if host == "ok.example":
            return httpx.Response(200, text=(
                "<html><head><title>t</title></head>"
                "<body><p>This is a substantial body with enough words "
                "to clear the thin-content guard easily and demonstrate "
                "successful indexing through the polite crawler path.</p>"
                "</body></html>"))
        if host == "forbid.example":
            return httpx.Response(403, text="x")
        if host == "down.example":
            return httpx.Response(503, text="x")
        if host == "thin.example":
            return httpx.Response(200, text="<html><body>hi</body></html>")
        return httpx.Response(404, text="x")
    _mount_transport(monkeypatch, responder)

    async def go():
        await db.register_domain("ok.example", tier=2, sector="defence",
                                 language="en")
        await db.register_domain("forbid.example", tier=2, sector="defence",
                                 language="en")
        await db.register_domain("down.example", tier=2, sector="defence",
                                 language="en")
        await db.register_domain("thin.example", tier=2, sector="defence",
                                 language="en")
        summary = await runner.crawl_seed_homepages()
        # Every domain produced a status_class.
        assert summary["domains_total"] == 4
        by_status = summary["by_status"]
        assert by_status.get("ok") == 1
        assert by_status.get("4xx") == 1
        assert by_status.get("5xx") == 1
        assert by_status.get("thin") == 1
        # And the indexed count is just the ok one.
        assert summary["indexed"] == 1
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Loop startup delay
# ─────────────────────────────────────────────────────────────────

def test_crawl_loop_signature_accepts_startup_delay():
    """The R-F508 plumbing fix: lifespan can pass startup_delay_sec
    through to the loop without TypeError."""
    import inspect
    sig = inspect.signature(runner.crawl_loop)
    assert "startup_delay_sec" in sig.parameters, (
        "R-F508 must add a startup_delay_sec parameter to crawl_loop"
    )
    # Default left as None so the loop applies its own _DEFAULT_STARTUP_
    # DELAY_SEC (60s) — that way the lifespan call site doesn't need
    # to know the magic number.
    assert sig.parameters["startup_delay_sec"].default is None


def test_crawl_loop_zero_startup_delay_runs_cycle_immediately(monkeypatch):
    """startup_delay_sec=0 must skip the wait and fire the first cycle
    right away. Uses real (tiny) sleeps so we don't risk monkey-patch
    races against the loop's own awaits."""
    cycle_count = {"n": 0}

    async def fake_cycle(limit=None):
        cycle_count["n"] += 1
        return {"fetched": 0, "indexed": 0, "skipped": 0, "errors": 0,
                "duration_sec": 0, "domains_total": 0, "by_status": {}}

    monkeypatch.setattr(runner, "crawl_seed_homepages", fake_cycle)

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(
            runner.crawl_loop(interval_sec=3600,
                                stop_event=stop,
                                startup_delay_sec=0),
        )
        # Wait for the first cycle to fire — with startup_delay=0 the
        # loop should produce one cycle before reaching the interval
        # sleep. Cap at 2s wall clock.
        for _ in range(40):
            if cycle_count["n"] >= 1:
                break
            await asyncio.sleep(0.05)
        stop.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        assert cycle_count["n"] >= 1, (
            "with startup_delay_sec=0, the first cycle must fire "
            "immediately — instead got cycle_count="
            f"{cycle_count['n']}"
        )
    _run(go())
