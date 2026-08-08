"""R-F522 — cap httpx redirect chain at 5 for robots.txt fetcher.

Live evidence 2026-05-14 12:22:56 fly logs: imo.org/robots.txt 302 →
www.imo.org/robots.txt then 301-looped on itself ~60 times in 0.5s,
each hop ~6ms, every step logged at INFO via httpx. Default httpx
AsyncClient max_redirects=20 + tight ~6ms hop time meant the loop
runs to completion in <0.5s, polluting logs and wasting outbound
bandwidth. Same pattern observed earlier on email.net.

Server-side bug (Location header points back to the same URL we just
came from) — we cannot fix the upstream server. R-F522 caps OUR
patience at max_redirects=5 so even a misbehaving server costs us
only 5 hops instead of 20.

Test pins the AsyncClient construction to confirm max_redirects=5 is
present. A regression that drops the cap would re-introduce the
2026-05-14 noise pattern.
"""
from __future__ import annotations

import inspect

from aria_service.intel import crawl_enhancements

# R-F3772/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def test_rf522_check_robots_source_has_max_redirects_cap():
    """Inspect the check_robots source to confirm the redirect cap is
    in place. Source-level check is more robust than runtime spying
    because httpx.AsyncClient is constructed inside an inner async
    block that's easier to assert against textually."""
    src = function_source(crawl_enhancements, "check_robots")
    assert "max_redirects=5" in src, (
        f"R-F522 redirect cap missing from check_robots — server-side "
        f"redirect loops will re-fire the 2026-05-14 imo.org pattern. "
        f"Got source:\n{src}"
    )
    # ── R-F3772 — this used to assert `follow_redirects=True` ────────────────
    #
    # It was correct when written: httpx followed redirects itself, capped at 5.
    # R-F1851 then replaced that mechanism with `url_safety.safe_get`, whose own
    # docstring states the reason — "follow_redirects forced OFF so an open
    # redirect to an internal host cannot bypass the guard". safe_get walks the
    # hops itself and REVALIDATES each one against is_safe_url.
    #
    # So the assertion had become the opposite of the security design, and
    # "fixing" the code to satisfy it would REINTRODUCE AN SSRF HOLE: with
    # follow_redirects=True, httpx resolves the chain internally and no hop after
    # the first is ever checked. A robots.txt fetch on a user-derived host could
    # then reach an internal service — exactly what R-F1851 closed.
    #
    # The redirect cap R-F522 exists for is still enforced (max_redirects=5,
    # asserted above); only its LOCATION moved. Assert the current contract.
    assert "follow_redirects=False" in src, (
        "the client must not follow redirects itself — safe_get walks the hops and "
        "revalidates each against is_safe_url (R-F1851). follow_redirects=True here "
        "would let httpx resolve the chain unchecked, which is an SSRF bypass."
    )
    assert "safe_get" in src, (
        "check_robots no longer routes through url_safety.safe_get, the single "
        "SSRF-checked fetch boundary — a raw client.get on a user-derived host is "
        "the hole R-F1851 closed"
    )
