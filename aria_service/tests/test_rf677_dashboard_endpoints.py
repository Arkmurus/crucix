"""R-F677 (2026-05-18) — Phase A gate-indicator endpoints mount + public-bypass.

Live evidence 2026-05-18 07:09:02-05 — operator dashboard hit:
  - GET /api/aria/health/composite   → 404 Not Found
  - GET /api/aria/mastery/heatmap    → 404 Not Found
  - GET /api/aria/eval/count         → 404 Not Found

All three were unmounted despite underlying compute existing
(autonomy_scorer.compute_composite, student.get_regional_heatmap,
eval_runner.get_golden_set). Without these, gate #1 (composite ≥71%),
gate #2 (heatmap floor ≥70%), and gate #6 (500-Q eval frozen) are
unobservable from the dashboard.

R-F677 mounts the 3 routes and adds them to _PUBLIC_AUTH_BYPASS_PATHS
so the dashboard can render the gate panels without operator having to
wire token auth into the frontend. Mirrors the /api/aria/health and
/api/aria/health/live precedent.

These tests are pure route-registration checks — no TestClient needed.
"""
from __future__ import annotations


def test_rf677_health_composite_mounted():
    from aria_service.routes import aria as aria_routes
    paths = {getattr(r, "path", "") for r in aria_routes.router.routes}
    assert "/api/aria/health/composite" in paths, (
        f"R-F677: /api/aria/health/composite missing. Sample mounted: "
        f"{sorted(p for p in paths if 'health' in p)[:6]}"
    )


def test_rf677_mastery_heatmap_alias_mounted():
    from aria_service.routes import aria as aria_routes
    paths = {getattr(r, "path", "") for r in aria_routes.router.routes}
    # Both the original /student/-prefixed AND the new alias must exist.
    assert "/api/aria/student/mastery/heatmap" in paths
    assert "/api/aria/mastery/heatmap" in paths, (
        "R-F677: /api/aria/mastery/heatmap alias missing — dashboard 404s"
    )


def test_rf677_eval_count_mounted():
    from aria_service.routes import aria as aria_routes
    paths = {getattr(r, "path", "") for r in aria_routes.router.routes}
    assert "/api/aria/eval/count" in paths, (
        f"R-F677: /api/aria/eval/count missing. Sample eval routes: "
        f"{sorted(p for p in paths if '/eval/' in p)[:8]}"
    )


def test_rf677_endpoints_in_public_bypass():
    """All three R-F677 endpoints must be in _PUBLIC_AUTH_BYPASS_PATHS
    so the dashboard can hit them without a bearer token (mirrors
    /api/aria/health precedent)."""
    from aria_service.routes import aria as aria_routes
    bypass = aria_routes._PUBLIC_AUTH_BYPASS_PATHS
    assert "/api/aria/health/composite" in bypass
    assert "/api/aria/mastery/heatmap" in bypass
    assert "/api/aria/eval/count" in bypass


def test_rf677_lifespan_smoke_imports():
    """The 3 new endpoints import their compute functions lazily inside
    the handler body. Smoke-test that the modules import cleanly at the
    *route* import time (which is what lifespan triggers)."""
    from aria_service.routes import aria as aria_routes  # noqa: F401
    from aria_service.intel import autonomy_scorer
    from aria_service.intel import student
    from aria_service.intel import eval_runner
    assert callable(autonomy_scorer.compute_composite)
    assert callable(student.get_regional_heatmap)
    assert callable(eval_runner.get_golden_set)
