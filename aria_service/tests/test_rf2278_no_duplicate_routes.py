"""R-F2278 — duplicate-route guard: no (method, path) may be registered twice.

Root-cause context: FastAPI/Starlette serves the FIRST-registered handler for a
colliding (method, path), so a second handler silently becomes dead code — no
import or boot error. This shipped at least three times:

  - R-F2150 `/brain/stats` + `/health/perf` stubs shadowed the canonical
    brain_hook / R-F396 handlers (dashboards got the wrong shape; 20
    R-F396/F400/F974 source-scan tests silently went red).
  - `/dd/layer-5c/stats` had two different features on one path (one dead).
  - The 2026-04-26 `/ingest` router-vs-main.py shadow (422 on all sweeps).

These are the CI gate that makes the class impossible to reintroduce silently,
plus a unit test proving the detector actually catches a planted duplicate.
"""
from __future__ import annotations

from fastapi import FastAPI

from aria_service.route_audit import (
    assert_no_duplicate_routes,
    find_duplicate_routes,
    log_duplicate_routes,
)

from ._app_probe import route_endpoints


def _build_app() -> FastAPI:
    """Mirror how main.py mounts the router (prefix + all handlers)."""
    from aria_service.routes.aria import router

    app = FastAPI()
    app.include_router(router)
    return app


# ── The CI gate ──────────────────────────────────────────────────────────────

def test_router_has_no_duplicate_routes():
    """The whole aria router must register each (method, path) exactly once.

    This is the capability test for R-F2278: before the fix, /health/perf,
    /brain/stats and /dd/layer-5c/stats each had two handlers. If this ever
    fails again, a duplicate route was reintroduced and one handler is dead.
    """
    app = _build_app()
    dups = find_duplicate_routes(app)
    assert dups == {}, (
        "Duplicate route(s) registered — the second handler is dead code and "
        "FastAPI serves the first:\n"
        + "\n".join(
            f"  {m} {p} -> {labels}" for (m, p), labels in sorted(dups.items())
        )
    )


def test_previously_colliding_paths_resolve_to_canonical_handler():
    """The three R-F2278 paths must now each resolve to exactly one handler, and
    it must be the CANONICAL one the live consumers read."""
    # R-F3791 — NOT a flat walk of `app.routes`: `include_router` appends a lazy
    # wrapper rather than copying the child's routes up, so the flat walk saw only
    # the four FastAPI built-ins and this assertion failed on a healthy app. The
    # shared enumeration keeps first-registered-wins, which is what "served" means.
    by_key = route_endpoints(_build_app())

    # /health/perf → the full R-F396 self-introspection handler (not the stub).
    assert ("GET", "/api/aria/health/perf") in by_key
    assert by_key[("GET", "/api/aria/health/perf")] == "health_perf_ep"

    # /brain/stats → brain_hook.get_stats() handler (per-module signals).
    assert by_key[("GET", "/api/aria/brain/stats")] == "brain_stats_ep"

    # /dd/layer-5c/stats → commercial-coherence handler; the digital-footprint
    # variant now lives on its own path.
    assert by_key[("GET", "/api/aria/dd/layer-5c/stats")] == "dd_layer_5c_stats_ep"
    assert ("GET", "/api/aria/dd/layer-5c/digital-stats") in by_key
    assert (
        by_key[("GET", "/api/aria/dd/layer-5c/digital-stats")]
        == "dd_layer_5c_digital_stats_ep"
    )


# ── Detector unit tests (prove the guard actually detects) ────────────────────

def test_detector_flags_a_planted_duplicate():
    app = FastAPI()

    @app.get("/dup")
    async def _a():  # pragma: no cover - never called
        return {}

    @app.get("/dup")
    async def _b():  # pragma: no cover - never called
        return {}

    dups = find_duplicate_routes(app)
    assert ("GET", "/dup") in dups
    assert len(dups[("GET", "/dup")]) == 2

    try:
        assert_no_duplicate_routes(app)
    except AssertionError as e:
        assert "/dup" in str(e)
    else:  # pragma: no cover
        raise AssertionError("assert_no_duplicate_routes did not raise on a duplicate")


def test_log_duplicate_routes_returns_dups_and_never_raises(caplog):
    """The boot-time hook must (a) return the duplicates it found and (b) never
    raise — a slipped-through duplicate must be loud in the logs, not fatal."""
    import logging

    app = FastAPI()

    @app.get("/boot-dup")
    async def _a():  # pragma: no cover
        return {}

    @app.get("/boot-dup")
    async def _b():  # pragma: no cover
        return {}

    with caplog.at_level(logging.ERROR, logger="aria.route_audit"):
        dups = log_duplicate_routes(app)  # must not raise
    assert ("GET", "/boot-dup") in dups
    assert any("/boot-dup" in r.message for r in caplog.records), (
        "log_duplicate_routes did not emit an ERROR line for the duplicate"
    )


def test_log_duplicate_routes_clean_app_returns_empty(caplog):
    """On a clean app the hook returns {} and logs nothing at ERROR."""
    import logging

    app = FastAPI()

    @app.get("/only")
    async def _only():  # pragma: no cover
        return {}

    with caplog.at_level(logging.ERROR, logger="aria.route_audit"):
        assert log_duplicate_routes(app) == {}
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_detector_ignores_distinct_methods_on_same_path():
    """GET + POST on the same path is NOT a duplicate."""
    app = FastAPI()

    @app.get("/thing")
    async def _get():  # pragma: no cover
        return {}

    @app.post("/thing")
    async def _post():  # pragma: no cover
        return {}

    assert find_duplicate_routes(app) == {}
    assert_no_duplicate_routes(app)  # must not raise
