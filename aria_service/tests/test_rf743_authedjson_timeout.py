"""R-F743 (2026-05-20) — authedJson timeout so backend wedges don't hang the UI.

Pre-R-F743 the conversation sidebar showed "Loading…" forever when
the backend was wedged. Symptom captured live in fly logs:

  18:05:02 WARNING aria.intel.autonomy_surface: auto_allowed timed out at 8.0s
  18:05:02 WARNING aria.intel.autonomy_surface: drafts timed out at 8.0s
  18:05:02 WARNING aria.intel.autonomy_surface: queue timed out at 8.0s
  18:05:02 WARNING aria.intel.autonomy_surface: resilience timed out at 8.0s

When the event loop wedges, the chat handler still accepts the
request but never returns a body. The frontend `await fetch(url)`
hangs indefinitely. The user sees the skeleton placeholder
"Loading…" with no actionable error.

R-F743 wraps every authedJson call in an AbortController with a
20-second default timeout. When timeout fires:
  - AbortController aborts the fetch
  - The catch re-throws a clear `timeout after 20000ms (backend may
    be wedged)` Error
  - The convos catch handler renders the message via R-F739

These tests are static-presence checks (no JSDOM in pytest); they
guard against revert / rebase loss of the timeout wrapper.
"""
from __future__ import annotations

from pathlib import Path

ARIA_HTML = Path(__file__).resolve().parents[2] / "public" / "aria.html"


def test_authedjson_has_abortcontroller_timeout():
    """R-F743: authedJson must wrap fetch in an AbortController."""
    src = ARIA_HTML.read_text(encoding="utf-8")
    assert "AbortController" in src, (
        "R-F743 regression: AbortController missing from aria.html — "
        "convos panel will hang on backend wedge."
    )
    # Default timeout value
    assert "timeoutMs" in src
    # R-F2525 (codex finding 7): assert a SUBSTANTIAL default timeout is present, not a
    # brittle exact value. The default was intentionally raised 20000→30000ms (a UX
    # accommodation for slow-backend tail latency); the old exact-"20000" assertion drifted.
    import re as _re
    _m = _re.search(r"timeoutMs\s*=\s*\([^)]*\)\s*\|\|\s*(\d+)", src)
    assert _m and int(_m.group(1)) >= 15000, (
        "R-F743 regression: authedJson must have a substantial (>=15000ms) default "
        f"timeout; found {_m.group(1) if _m else 'none'}"
    )


def test_authedjson_clears_timer_in_finally():
    """R-F743: timer must be cleared on resolve to avoid stale abort
    firing on a subsequent request."""
    src = ARIA_HTML.read_text(encoding="utf-8")
    assert "clearTimeout(timer)" in src


def test_authedjson_translates_abort_to_clear_error():
    """R-F743: the AbortError must surface as a human-readable
    'backend may be wedged' message, not the generic AbortError."""
    src = ARIA_HTML.read_text(encoding="utf-8")
    assert "backend may be wedged" in src, (
        "R-F743 regression: timeout error message should include the "
        "'backend may be wedged' hint so the operator can diagnose."
    )
    # And error name detection
    assert "AbortError" in src


def test_rf743_marker_in_authedjson_block():
    """R-F743: marker present in the function body for revert detection."""
    src = ARIA_HTML.read_text(encoding="utf-8")
    # Find authedJson definition and look for R-F743 nearby
    idx = src.find("async function authedJson(")
    assert idx >= 0
    block = src[idx:idx + 2000]
    assert "R-F743" in block


def test_existing_authedjson_callers_still_work():
    """R-F743: must preserve backwards-compatible signature
    `authedJson(method, url, body)`. The 4th arg (opts2) is optional
    so existing call sites continue to work unchanged."""
    src = ARIA_HTML.read_text(encoding="utf-8")
    # Old signature still callable: authedJson('GET', '/api/...') and
    # authedJson('PUT', '/api/...', {body})
    # The opts2 must be optional (no required 4th positional)
    assert "function authedJson(method, url, body, opts2)" in src, (
        "R-F743 regression: authedJson signature must keep "
        "(method, url, body) backwards-compatible — new opts2 must "
        "be the 4th positional and optional."
    )
    # R-F2525 (codex finding 7): the real guard is the backwards-compatible SIGNATURE
    # above. The old specific-caller list (GET/PUT/DELETE) was brittle — PUT/DELETE were
    # refactored to raw fetch, so only authedJson('GET') remains. Assert authedJson is
    # actually invoked (>=1 caller) rather than pinning exact method strings.
    import re
    assert "authedJson('GET'," in src
    assert len(re.findall(r"authedJson\('[A-Z]+'", src)) >= 1, (
        "R-F743 regression: authedJson has no callers — the timeout-wrapped helper is dead code."
    )
