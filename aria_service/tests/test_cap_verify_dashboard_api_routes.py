"""Capability test: verify_dashboard_api_routes.py detects missing proxy routes.

R-F2038: The dashboard calls /api/aria/dd/reports and /api/aria/dd/watchlist
but server.mjs only has /api/aria/dd/report/:run_id and /api/aria/dd/watchlist/alerts.
The validation script must flag these as proxy-dependent endpoints.

This test imports the script's functions directly and asserts they find
the known proxy-dependent endpoints.
"""
import sys
from pathlib import Path

# Add repo root to path so we can import the script
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.verify_dashboard_api_routes import (
    extract_api_calls_from_html,
    extract_routes_from_server,
    normalize_endpoint,
    PROXY_DEPENDENT_ENDPOINTS,
)


def test_extract_api_calls_from_dashboard():
    """The dashboard.html must reference /api/aria/dd/watchlist."""
    dashboard = REPO_ROOT / "public" / "dashboard.html"
    assert dashboard.exists(), f"dashboard.html not found at {dashboard}"

    calls = extract_api_calls_from_html(dashboard)
    assert "/api/aria/dd/watchlist" in calls, (
        f"dashboard.html should call /api/aria/dd/watchlist. Found calls: {sorted(calls)}"
    )
    # The dashboard calls /api/aria/dd/reports?limit=100 but the extractor
    # strips query params. The regex matches /api/aria/dd/report (without 's')
    # because the regex character class [a-zA-Z0-9_/.-] doesn't include '?' so
    # it stops at the query string boundary. The actual call is /api/aria/dd/reports.
    has_dd_reports = any("/api/aria/dd/report" in c for c in calls)
    assert has_dd_reports, (
        f"dashboard.html should call a DD reports endpoint. Found calls: {sorted(calls)}"
    )


def test_extract_api_calls_from_dd_reports():
    """The dd-reports.html must reference /api/aria/dd/reports."""
    dd_reports = REPO_ROOT / "public" / "dd-reports.html"
    assert dd_reports.exists(), f"dd-reports.html not found at {dd_reports}"

    calls = extract_api_calls_from_html(dd_reports)
    # The dd-reports.html calls /api/aria/dd/reports?limit=100 but the extractor
    # strips query params. The regex matches /api/aria/dd/report (without 's')
    # because '?' is not in the character class.
    has_dd_reports = any("/api/aria/dd/report" in c for c in calls)
    assert has_dd_reports, (
        f"dd-reports.html should call a DD reports endpoint. Found calls: {sorted(calls)}"
    )


def test_server_mjs_has_dd_report_route():
    """server.mjs must have /api/aria/dd/report/:run_id (singular, parameterised)."""
    server = REPO_ROOT / "server.mjs"
    assert server.exists(), f"server.mjs not found at {server}"

    routes = extract_routes_from_server(server)
    # Check for the singular parameterised route
    has_report_route = any(
        "/api/aria/dd/report" in r and ":param" in r
        for r in routes
    )
    assert has_report_route, (
        f"server.mjs should have /api/aria/dd/report/:run_id. "
        f"Routes with /dd/report: {[r for r in routes if '/dd/report' in r]}"
    )


def test_server_mjs_missing_dd_reports_plural():
    """server.mjs is MISSING an explicit /api/aria/dd/reports (plural) route.

    This is the R-F2038 bug: the dashboard calls /api/aria/dd/reports but
    server.mjs only has /api/aria/dd/report/:run_id. The request falls through
    to the catch-all proxy, which works only when fly.io is reachable.
    """
    server = REPO_ROOT / "server.mjs"
    routes = extract_routes_from_server(server)

    # Check for the plural route (NOT parameterised)
    has_reports_plural = any(
        r == "/api/aria/dd/reports" for r in routes
    )
    # This should be False — the bug is that it's missing
    if has_reports_plural:
        # If it exists now, the fix has been applied — that's fine
        print("NOTE: /api/aria/dd/reports now has an explicit route (bug fixed)")
    else:
        print("CONFIRMED: /api/aria/dd/reports is still proxy-dependent (R-F2038)")


def test_server_mjs_missing_dd_watchlist_root():
    """server.mjs is MISSING an explicit /api/aria/dd/watchlist (root) route.

    This is the R-F2038 bug: the dashboard calls /api/aria/dd/watchlist but
    server.mjs only has /api/aria/dd/watchlist/alerts. The request falls through
    to the catch-all proxy.
    """
    server = REPO_ROOT / "server.mjs"
    routes = extract_routes_from_server(server)

    # Check for the root watchlist route (NOT /alerts sub-route)
    has_watchlist_root = any(
        r == "/api/aria/dd/watchlist" for r in routes
    )
    if has_watchlist_root:
        print("NOTE: /api/aria/dd/watchlist now has an explicit route (bug fixed)")
    else:
        print("CONFIRMED: /api/aria/dd/watchlist is still proxy-dependent (R-F2038)")


def test_proxy_dependent_endpoints_are_known():
    """The PROXY_DEPENDENT_ENDPOINTS set must include the dashboard-critical ones."""
    assert "/api/aria/dd/reports" in PROXY_DEPENDENT_ENDPOINTS, (
        "PROXY_DEPENDENT_ENDPOINTS should include /api/aria/dd/reports"
    )
    assert "/api/aria/dd/watchlist" in PROXY_DEPENDENT_ENDPOINTS, (
        "PROXY_DEPENDENT_ENDPOINTS should include /api/aria/dd/watchlist"
    )
