"""R-F3791 — enumerate an app's REAL routes, whatever `include_router` did with them.

THE DEFECT THIS REPLACES, measured on 2026-08-08.

Five tests assert that a route is registered by walking `app.routes` and reading
`.path` off each entry. Under the FastAPI pinned in `aria_service/requirements.txt`
(0.141.1, pinned by R-F3726 from `pip freeze` inside the RUNNING machine), that walk
returns FOUR paths — `/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc` —
and nothing else. All 717 aria routes, all 31 vetting routes and both vetting-portal
routes are invisible to it.

`include_router` no longer COPIES the sub-router's routes into `app.routes`. It
appends ONE lazy `_IncludedRouter` wrapper per call, holding the sub-router by
reference. `getattr(route, "path", "")` on a wrapper returns the default — so the
routes did not disappear, the ENUMERATION did, and it degraded to the empty string
rather than to an error.

That is this repo's standing defect class: an absence collapsing into a measurement.
The tests read "route not registered" when the truth was "this walk can no longer
see routes". Verified against the same app in the same interpreter: `/health/live`
→ 200, `/api/aria/health/perf` → 200, `POST`-only `/api/aria/brain/signal` → 405
(a 405 proves the route EXISTS), and `app.openapi()["paths"]` → 723 entries.
Nothing was unmounted. **No production code walks `app.routes`** (checked repo-wide),
so this never reached the server — it is a test-instrument defect only.

WHY DUCK-TYPING AND NOT `isinstance(r, _IncludedRouter)`. The wrapper class is
private and version-specific. Matching on the ATTRIBUTES it exposes
(`original_router`) means this helper also works on the older flattening FastAPI,
where no wrapper exists and the recursion simply never fires. A test helper that
only works on the version installed today would re-break on the next pin bump —
which is exactly how we got here.

WHAT THIS DELIBERATELY DOES NOT DO. It does not read `app.openapi()`. OpenAPI omits
every route declared `include_in_schema=False`, so a guard built on it would go
quietly blind to precisely the routes most worth guarding. The point of these tests
is to detect a route that silently vanished; an enumeration with a blind spot cannot
do that.

WHERE THE FLATTENING ACTUALLY LIVES. Not here. `route_audit.iter_routes` is the one
implementation, because the SAME blindness disabled the production boot-time
duplicate-route check (main.py:4783) — the detector and these tests must never be
able to disagree about which routes exist. This module only adds the two shapes the
tests want on top of it. §1 records what happens when one measure gets forked in
two: the copies drift and each certifies something different.
"""
from __future__ import annotations

from typing import Any

from ..route_audit import iter_routes

__all__ = ["iter_routes", "mounted_paths", "route_endpoints"]


def mounted_paths(container: Any) -> set[str]:
    """Every non-empty absolute path registered on `container`."""
    return {p for p, _ in iter_routes(container) if p}


def route_endpoints(container: Any) -> dict[tuple[str, str], str]:
    """Map `(METHOD, path)` → endpoint function name, first registration winning.

    `HEAD`/`OPTIONS` are excluded: FastAPI synthesises them, so they say nothing
    about which handler a consumer actually reaches.
    """
    by_key: dict[tuple[str, str], str] = {}
    for path, route in iter_routes(container):
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        endpoint = getattr(route, "endpoint", None)
        name = getattr(endpoint, "__name__", "") if endpoint is not None else ""
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            by_key.setdefault((method, path), name)
    return by_key
