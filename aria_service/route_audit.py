"""R-F2278 — duplicate-route guard.

Eliminates a recurring, silent failure class: two handlers registered on the
same (method, path). FastAPI/Starlette matches routes in *registration order*
and serves the FIRST match, so the second handler becomes dead code — with NO
error at import or boot. This has bitten the codebase repeatedly:

  - R-F2150 added `/brain/stats` + `/health/perf` stubs that shadowed the
    canonical brain_hook / R-F396 handlers (dashboard got wrong data; 20
    R-F396/F400/F974 source-scan tests silently went red).
  - `/dd/layer-5c/stats` had two different features (commercial-coherence vs
    digital-footprint) claiming one path — one was dead.
  - The 2026-04-26 `/ingest` incident: a router handler shadowed a main.py
    handler and swallowed all sweep payloads with a 422.

The structural fix is detection, not hand-patching each collision: the pytest
in test_rf2278_no_duplicate_routes fails CI on ANY duplicate, and the boot-time
check in main.py logs it loudly so a duplicate can never ship silently again.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.route_audit")

# Methods that FastAPI auto-adds and that legitimately share a path with the
# real verb (HEAD mirrors GET; OPTIONS is CORS preflight). Excluded so they
# don't produce false-positive "duplicates".
_IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})


def _endpoint_label(route) -> str:
    """Best-effort human label for a route's handler."""
    ep = getattr(route, "endpoint", None)
    if ep is not None:
        return getattr(ep, "__qualname__", None) or getattr(ep, "__name__", None) or repr(ep)
    return getattr(route, "name", None) or repr(route)


def find_duplicate_routes(app_or_routes) -> dict[tuple[str, str], list[str]]:
    """Return every (METHOD, path) registered by more than one handler.

    Accepts a FastAPI app, an APIRouter, or an iterable of routes. The value is
    the list of handler labels that collided on that key (order = registration
    order, so the first entry is the one FastAPI actually serves).
    """
    routes = getattr(app_or_routes, "routes", app_or_routes)
    seen: dict[tuple[str, str], list[str]] = {}
    for route in routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            # Mounts / static / websockets have no (method, path) verb pair.
            continue
        label = _endpoint_label(route)
        for method in methods:
            if method in _IGNORED_METHODS:
                continue
            seen.setdefault((method, path), []).append(label)
    return {key: labels for key, labels in seen.items() if len(labels) > 1}


def assert_no_duplicate_routes(app_or_routes) -> None:
    """Raise AssertionError if any (method, path) is registered twice.

    Used by the CI gate (test_rf2278). Boot uses log_duplicate_routes instead so
    an accidental duplicate degrades one route rather than crashing the process.
    """
    dups = find_duplicate_routes(app_or_routes)
    if dups:
        lines = [
            f"  {method} {path}  ->  {labels} (FastAPI serves the FIRST: {labels[0]})"
            for (method, path), labels in sorted(dups.items())
        ]
        raise AssertionError(
            "Duplicate route registration(s) detected — the second handler is "
            "dead code and FastAPI silently serves the first:\n" + "\n".join(lines)
        )


def log_duplicate_routes(app_or_routes) -> dict[tuple[str, str], list[str]]:
    """Log (ERROR) any duplicate routes found. Never raises. Returns the dups.

    Boot-time use: loud + non-fatal, so a slipped-through duplicate is visible in
    the fly logs without taking the app down. The CI test is the hard gate.
    """
    try:
        dups = find_duplicate_routes(app_or_routes)
    except Exception as exc:  # pragma: no cover - defensive; never break boot
        logger.warning("R-F2278 route-audit failed to run: %s", exc)
        return {}
    for (method, path), labels in sorted(dups.items()):
        logger.error(
            "R-F2278 DUPLICATE ROUTE %s %s registered by %s — FastAPI serves the "
            "FIRST (%s); the rest are DEAD. Fix the collision (rename or delete).",
            method, path, labels, labels[0],
        )
    return dups
