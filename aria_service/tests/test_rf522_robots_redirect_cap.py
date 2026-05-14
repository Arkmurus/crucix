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


def test_rf522_check_robots_source_has_max_redirects_cap():
    """Inspect the check_robots source to confirm the redirect cap is
    in place. Source-level check is more robust than runtime spying
    because httpx.AsyncClient is constructed inside an inner async
    block that's easier to assert against textually."""
    src = inspect.getsource(crawl_enhancements.check_robots)
    assert "max_redirects=5" in src, (
        f"R-F522 redirect cap missing from check_robots — server-side "
        f"redirect loops will re-fire the 2026-05-14 imo.org pattern. "
        f"Got source:\n{src}"
    )
    # Cap must coexist with follow_redirects=True (we still want to
    # resolve normal redirects, just not loops).
    assert "follow_redirects=True" in src
