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

R-F3791 (2026-08-08) — THE DETECTOR ITSELF HAD GONE BLIND, and silently.

This module walked `app.routes` as a FLAT list. Under the FastAPI pinned in
requirements.txt (0.141.1, pinned by R-F3726 from the running machine), a call to
`include_router` no longer copies the sub-router's routes into `app.routes` — it
appends ONE lazy wrapper holding the sub-router by reference. So `getattr(route,
"path", None)` returned None for all three mounted routers, every route was skipped
by the `if not path` guard, and `find_duplicate_routes` returned `{}` — for an app
with 770 routes.

Nothing raised. The boot check at main.py:4783 logged nothing, and the CI gate in
test_rf2278 passed on an empty inventory. A guard whose universe is empty always
certifies. That is the same shape as the three Phase A gates §1 records as
"certified by an absence", and it is why the flattening lives HERE, in the one
place both the boot check and the tests already read, rather than being
reimplemented alongside it.

Measured at the fix: 770 routes now visible, 0 genuine duplicates — so restoring
sight does not itself introduce a boot ERROR.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator

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


def iter_routes(app_or_routes, _prefix: str = "", _seen: "set[int] | None" = None) -> Iterator[tuple[str, Any]]:
    """Yield `(absolute_path, route)` for every real route, however it was mounted.

    Accepts a FastAPI app, an APIRouter, or a bare iterable of routes. Declaration
    order is preserved depth-first, which is what makes "FastAPI serves the FIRST
    match" observable to callers.

    Matching is by DUCK-TYPING, not `isinstance(r, _IncludedRouter)`: that wrapper
    class is private and version-specific. Keying off the attribute it exposes means
    this also works on the older FastAPI that flattened routes eagerly (the
    recursion simply never fires) — so a future pin bump in either direction cannot
    blind the detector again, which is the whole defect being fixed.
    """
    # A container reachable from itself (an app mounted into itself) would otherwise
    # recurse until the stack dies. That is caught by log_duplicate_routes and
    # reported honestly, but a RecursionError raised mid-boot is a poor way to learn
    # it: terminate the branch explicitly instead, so the rest of the table is still
    # enumerated and the audit stays a measurement rather than a crash.
    #
    # `_seen` is the ANCESTOR CHAIN, not a visited-set, and the difference is the
    # whole point: including the SAME router twice is a legitimate way to create a
    # duplicate route, so a visited-set would make this detector blind to precisely
    # the collision it exists to find. Only a container reachable from ITSELF is a
    # cycle, so the id is discarded again on the way out.
    if _seen is None:
        _seen = set()
    key = id(app_or_routes)
    if key in _seen:
        return
    _seen.add(key)
    try:
        yield from _iter_routes_inner(app_or_routes, _prefix, _seen)
    finally:
        _seen.discard(key)


def _iter_routes_inner(app_or_routes, _prefix: str, _seen: "set[int]") -> Iterator[tuple[str, Any]]:
    routes = getattr(app_or_routes, "routes", app_or_routes) or ()
    for route in routes:
        # Lazy include wrapper: the real routes hang off `original_router`, and a
        # prefix given to `include_router(prefix=...)` lives on the include context
        # rather than being baked into the child's own `.path`.
        inner = getattr(route, "original_router", None)
        if inner is not None:
            ctx = getattr(route, "include_context", None)
            yield from iter_routes(inner, _prefix + (getattr(ctx, "prefix", "") or ""), _seen)
            continue

        path = getattr(route, "path", None)
        if path is None:
            continue

        # A Mount (sub-app / StaticFiles) carries its children beneath its own path.
        sub = getattr(route, "routes", None)
        if sub and not hasattr(route, "methods"):
            yield from iter_routes(route, _prefix + path, _seen)
            continue

        yield _prefix + path, route


def find_duplicate_routes(app_or_routes) -> dict[tuple[str, str], list[str]]:
    """Return every (METHOD, path) registered by more than one handler.

    Accepts a FastAPI app, an APIRouter, or an iterable of routes. The value is
    the list of handler labels that collided on that key (order = registration
    order, so the first entry is the one FastAPI actually serves).
    """
    seen: dict[tuple[str, str], list[str]] = {}
    for path, route in iter_routes(app_or_routes):
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


def _wire_failure(detail: str, gap_type: str) -> None:
    """Report an audit failure to the brain. Never raises, never blocks.

    R-F3792 — this module used to reach the brain on NEITHER branch. Per §21a a
    log line is DARK: `logger.error` alone means the coder loop and
    /api/aria/brain/stats never learn that the guard fired or that it broke.
    `wire_failure` writes both sinks (capability_gaps for the coder, record_signal
    for the health metric) and is safe here even though boot calls this BEFORE the
    event loop exists — `_dispatch_fire_and_forget` falls back to a daemon thread
    when `get_running_loop()` raises.
    """
    try:
        from .intel.engine_wiring import wire_failure  # local: avoid a boot cycle
        wire_failure(module="route_audit", detail=detail, gap_type=gap_type,
                     source="route_audit:log_duplicate_routes")
    except Exception:  # pragma: no cover - observability must never break boot
        logger.debug("R-F3792 route-audit failure wiring failed", exc_info=True)


def _wire_success(summary: str) -> None:
    """Report a clean audit. §21a wiring is only satisfied when BOTH branches emit."""
    try:
        from .intel.engine_wiring import wire_success
        wire_success(module="route_audit", summary=summary,
                     source_id="route_audit:log_duplicate_routes")
    except Exception:  # pragma: no cover
        logger.debug("R-F3792 route-audit success wiring failed", exc_info=True)


def log_duplicate_routes(app_or_routes) -> dict[tuple[str, str], list[str]]:
    """Log (ERROR) any duplicate routes found. Never raises. Returns the dups.

    Boot-time use: loud + non-fatal, so a slipped-through duplicate is visible in
    the fly logs without taking the app down. The CI test is the hard gate.

    R-F3792 — reports to the brain on EVERY branch (clean / duplicates / blind /
    crashed), because R-F3791 showed the interesting failure is not "a duplicate
    shipped" but "the detector stopped being able to see one", and a guard that
    only logs cannot tell anyone it has gone quiet.
    """
    try:
        # An empty RESULT and an empty UNIVERSE are different facts, so they are
        # measured separately. R-F3791: the enumeration silently began returning
        # nothing for a 770-route app, and `{} duplicates` read as a clean bill of
        # health for months. Counting what was actually examined is what makes the
        # difference observable instead of inferred.
        visible = sum(1 for _ in iter_routes(app_or_routes))
        declared = len(getattr(app_or_routes, "routes", app_or_routes) or ())
        dups = find_duplicate_routes(app_or_routes)
    except Exception as exc:  # pragma: no cover - defensive; never break boot
        logger.warning("R-F2278 route-audit failed to run: %s", exc)
        _wire_failure(f"route audit could not run: {type(exc).__name__}: {str(exc)[:200]}",
                      "engine_failure")
        return {}

    if declared and not visible:
        # The R-F3791 signature exactly: routes ARE registered, and this walk cannot
        # see them. Reporting "no duplicates" here would be a fabricated pass — the
        # shape §1 records for three Phase A gates certified by an absence.
        logger.error(
            "R-F3792 route audit is BLIND: %d top-level entries yielded 0 enumerable "
            "routes, so 'no duplicates' is NOT a clean result — it is an unmeasured "
            "one. `iter_routes` no longer understands this app's route container "
            "(see R-F3791: include_router stopped flattening into app.routes).",
            declared,
        )
        _wire_failure(
            f"route audit blind: {declared} top-level entries yielded 0 enumerable "
            "routes — the duplicate-route guard cannot measure and must not certify",
            "boot_state_regression",
        )
        return {}

    for (method, path), labels in sorted(dups.items()):
        logger.error(
            "R-F2278 DUPLICATE ROUTE %s %s registered by %s — FastAPI serves the "
            "FIRST (%s); the rest are DEAD. Fix the collision (rename or delete).",
            method, path, labels, labels[0],
        )

    if dups:
        _wire_failure(
            f"{len(dups)} duplicate (method, path) registration(s) across {visible} "
            f"routes; FastAPI serves the first and the rest are dead code — "
            f"reproduce with test_rf2278_no_duplicate_routes. First: "
            + "; ".join(f"{m} {p} -> {labels}" for (m, p), labels in sorted(dups.items())[:3]),
            "module_bug",
        )
    else:
        _wire_success(f"route audit clean: {visible} routes, 0 duplicate (method, path) pairs")

    return dups
