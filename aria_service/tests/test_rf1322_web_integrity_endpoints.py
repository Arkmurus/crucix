"""
R-F1322 capability tests — web_integrity_agent endpoint definitions.

Tests:
  1. All endpoint paths exist as routes in routes/aria.py
  2. All expected fields match actual endpoint responses
  3. No endpoint expects fields that don't exist
"""
from __future__ import annotations

import ast
import os


def _routes_source() -> str:
    with open("aria_service/routes/aria.py", "r", encoding="utf-8") as f:
        return f.read()


def _agent_source() -> str:
    with open("aria_service/intel/web_integrity_agent.py", "r", encoding="utf-8") as f:
        return f.read()


def test_agent_compiles():
    """web_integrity_agent.py must compile without SyntaxError."""
    source = _agent_source()
    ast.parse(source)


def test_briefing_endpoint_path_correct():
    """The briefing endpoint path must match the actual route."""
    source = _agent_source()
    assert "/self/assess/briefing" in source, (
        "web_integrity_agent must use /self/assess/briefing (not /api/aria/briefing)"
    )


def test_autonomous_status_expected_fields():
    """autonomous/status returns {ok, engine} not {enabled}."""
    source = _agent_source()
    assert '"ok"' in source or "'ok'" in source, (
        "autonomous/status expected fields must include 'ok'"
    )
    assert '"engine"' in source or "'engine'" in source, (
        "autonomous/status expected fields must include 'engine'"
    )


def test_cost_status_expected_fields():
    """cost/monthly/status returns {total, monthly_cap}."""
    source = _agent_source()
    assert '"monthly_cap"' in source or "'monthly_cap'" in source, (
        "cost/monthly/status expected fields must include 'monthly_cap'"
    )


def test_report_endpoint_is_post():
    """report endpoint must be POST not GET."""
    source = _agent_source()
    # Find the report endpoint definition
    idx = source.index("api/aria/report")
    line = source[idx:idx + 100].split("\n")[0]
    assert "POST" in line, "report endpoint must use POST method"


def test_known_endpoint_paths_exist():
    """Known endpoint paths must exist as route decorators in routes/aria.py."""
    routes = _routes_source()
    # These are the paths from web_integrity_agent, stripped of /api/aria prefix
    expected_routes = [
        '/health/live',
        '/api/aria/health',
        '/self/assess/briefing',
        '/api/aria/report',
        '/api/aria/dd/watchlist/alerts/unread-count',
        '/api/aria/self/staged',
        '/api/aria/cost/monthly/status',
        '/autonomous/status',
        '/api/aria/adversarial/stats',
    ]
    for route in expected_routes:
        # Router decorators look like: @router.get("/path")
        # The route might be defined with or without /api/aria prefix
        search_key = route.replace('/api/aria', '')
        assert search_key in routes, f"Route '{route}' (search_key='{search_key}') not found in routes/aria.py"


def test_stale_404_probe_paths_removed():
    """Live logs showed these monitor probes return 404 while still passing."""
    source = _agent_source()
    assert '"/api/aria/status"' not in source
    assert '"/api/aria/self/improvements"' not in source
