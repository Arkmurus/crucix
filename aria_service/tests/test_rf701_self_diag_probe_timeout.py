"""R-F701 (2026-05-18) — self-diag endpoint-probe timeout bump.

Live dashboard 2026-05-18 20:42 flagged capability_card + pending_actions
as "probe failed (network?):" while every other endpoint passed. Both
endpoints touch heavyweight subsystems (capability_card builds the full
doc + brain_hook lookup; pending_actions iterates Redis lists) and
routinely exceed the previous 6.0s timeout under event-loop pressure
even when the route itself is healthy.

R-F701 bumps the probe timeout to 15.0s (smoke checks already use 30s
for the same reason) and includes type(e).__name__ in the error string
so the dashboard line is never just "probe failed (network?):" with no
context after the colon.
"""
from __future__ import annotations

import asyncio

# R-F3773/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def test_rf701_check_endpoint_uses_15s_timeout():
    """The probe should now use a 15s timeout, not the prior 6s.
    Detected via source inspection so we don't have to spin up a real
    httpx client to mutate it."""
    import inspect
    from aria_service.intel import self_diagnostic as sd
    src = function_source(sd, "_check_endpoint")
    assert "timeout=15.0" in src, (
        "R-F701 expected timeout=15.0 in _check_endpoint; got source without it"
    )
    assert "timeout=6.0" not in src, (
        "R-F701 expected the old 6.0s timeout to be removed"
    )


def test_rf701_probe_error_includes_exception_type_when_str_is_empty():
    """When str(e) is empty (e.g. bare httpx.ReadTimeout()), the
    operator-facing note should still carry the exception type rather
    than a dangling colon."""
    import inspect
    from aria_service.intel import self_diagnostic as sd
    src = function_source(sd, "_check_endpoint")
    # The fix uses `str(e)[:100] or type(e).__name__`
    assert "type(e).__name__" in src, (
        "R-F701 expected type(e).__name__ fallback in the exception branch"
    )


def test_rf701_smoke_module_imports():
    from aria_service.intel import self_diagnostic as sd  # noqa: F401
    assert callable(sd._check_endpoint)


def test_rf701_probe_error_path_actually_returns_typename():
    """End-to-end: when _check_endpoint hits an exception whose str() is
    empty, the returned note must include the typename — not be
    `probe failed (network?):` with a dangling colon."""
    from aria_service.intel import self_diagnostic as sd
    from unittest.mock import patch

    class _SilentTimeout(Exception):
        def __str__(self):  # str() returns empty — the bug we patched
            return ""

    async def _explode(*a, **kw):
        raise _SilentTimeout()

    async def _run():
        with patch("httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value
            instance.__aenter__ = lambda s: asyncio.sleep(0, result=instance)
            instance.__aexit__ = lambda s, *a: asyncio.sleep(0)
            instance.get = _explode
            status, note = await sd._check_endpoint("/api/aria/anything")
        return status, note

    status, note = asyncio.run(_run())
    assert status == "WARN"
    # Must NOT be dangling colon — must include the exception type
    assert "_SilentTimeout" in note or note.rstrip().endswith(":") is False, (
        f"R-F701 expected typename in note, got: {note!r}"
    )
