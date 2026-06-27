"""R-F2059: capability test — verify every search backend has a circuit breaker.

This test reads the source code to confirm that every _search_* function
has a circuit breaker guard. It does NOT call the live backends.
"""
from __future__ import annotations

import re
import ast
from pathlib import Path


def _get_search_functions(source: str) -> list[tuple[str, int]]:
    """Parse the source and return (function_name, line_number) for every
    async def _search_* function."""
    functions = []
    for i, line in enumerate(source.splitlines(), 1):
        m = re.match(r"^async def (_search_\w+)", line)
        if m:
            functions.append((m.group(1), i))
    return functions


def _function_has_circuit_breaker(source_lines: list[str], start_line: int) -> bool:
    """Check if a function (starting at start_line, 1-based) has a circuit breaker."""
    # Check the next 50 lines for get_breaker or circuit_breaker
    end = min(start_line + 50, len(source_lines))
    for i in range(start_line, end):
        line = source_lines[i - 1]
        if "get_breaker" in line or "CircuitBreaker" in line:
            return True
    return False


def _function_has_wire_failure(source_lines: list[str], start_line: int) -> bool:
    """Check if a function has wire_failure on error."""
    end = min(start_line + 80, len(source_lines))
    for i in range(start_line, end):
        line = source_lines[i - 1]
        if "wire_failure(" in line:
            return True
    return False


def test_all_search_backends_have_circuit_breakers():
    """Every _search_* backend function must have a circuit breaker.
    
    R-F2059: backends without circuit breakers burn requests on every
    search call when the upstream is down, with no cooldown.
    """
    path = Path("aria_service/intel/web_search.py")
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    functions = _get_search_functions(source)
    
    # These backends are exempt from circuit breaker requirements:
    # - _search_brave: permanent stub returning []
    exempt = {"_search_brave"}
    
    missing_cb = []
    missing_wf = []
    for func_name, line_no in functions:
        if func_name in exempt:
            continue
        if not _function_has_circuit_breaker(lines, line_no):
            missing_cb.append(f"{func_name} (line {line_no})")
        if not _function_has_wire_failure(lines, line_no):
            missing_wf.append(f"{func_name} (line {line_no})")
    
    assert not missing_cb, (
        f"Backends missing circuit breakers: {', '.join(missing_cb)}"
    )
    assert not missing_wf, (
        f"Backends missing wire_failure: {', '.join(missing_wf)}"
    )


def test_search_cache_imports():
    """The search cache functions are importable and have the right shape."""
    from aria_service.intel.web_search import (
        _SEARCH_CACHE,
        _SEARCH_CACHE_TTL,
        _SEARCH_CACHE_MAX,
        _search_cache_key,
        _search_cache_get,
        _search_cache_set,
    )
    assert isinstance(_SEARCH_CACHE, dict)
    assert _SEARCH_CACHE_TTL > 0
    assert _SEARCH_CACHE_MAX > 0
    assert callable(_search_cache_key)
    assert callable(_search_cache_get)
    assert callable(_search_cache_set)


def test_searxng_is_configured():
    """Verify the SearXNG self-host adapter is importable and checkable."""
    from aria_service.intel.search_searxng import is_configured, search
    # is_configured() checks SEARXNG_URL env var — on the production
    # server this returns True. In tests it may be False (no env set).
    # The important thing is the function exists and doesn't crash.
    assert callable(is_configured)
    assert callable(search)
