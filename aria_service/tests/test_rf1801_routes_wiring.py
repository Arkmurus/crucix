"""R-F1801 — Phase 1 batch 4: routes/aria.py (629 handlers) wired to the brain.

Routes are the highest-risk surface: a generic *args/**kwargs wrapper could break
FastAPI's signature-based dependency injection. fail_wire is signature-transparent
(functools.wraps __wrapped__) and placed INNERMOST (below @router.x), so FastAPI
registers the wrapped handler and still sees the real signature.

Proves: (1) include_router builds the DI graph for ALL wired handlers without
error (a broken signature would raise here); (2) a wired route still responds
(non-500); (3) @fail_wire fires on a handler failure -> engine_failure gap.
"""
import asyncio
import inspect

import pytest

import aria_service.intel.wire as wire
from aria_service.intel import wiring_harness as wh


def test_wired_routes_register_and_respond():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from aria_service.routes.aria import router

    app = FastAPI()
    app.include_router(router)  # builds DI graph for all 629 wired handlers
    client = TestClient(app)
    # Auth dependency gates every route — 401/403/200 all prove the route
    # registered and its signature/DI is intact. A 500 or include error = broken.
    r = client.get("/api/aria/identity")
    assert r.status_code != 500, f"wired route 500'd: {r.text[:300]}"


def test_routes_module_is_coverage_locked():
    assert "aria" in wh.WIRED_MODULES
    # Stream endpoints must remain exempt (not wired).
    assert wh.is_exempt("aria.py", "chat_stream_ep")[0]
    assert wh.is_exempt("aria.py", "chat_ep")[0]


def test_handler_fail_wire_records_engine_failure_gap():
    import aria_service.routes.aria as routes
    # Find a wired handler with a required arg (no-args -> TypeError before body).
    wired = wh.fail_wire_decorators(routes.__file__)
    target = None
    for name in wired:
        fn = getattr(routes, name, None)
        if fn is None:
            continue
        try:
            params = inspect.signature(fn).parameters.values()
        except (ValueError, TypeError):
            continue
        if any(p.default is inspect.Parameter.empty and p.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.KEYWORD_ONLY) for p in params):
            target = (name, fn)
            break
    assert target, "no required-arg wired handler found to force-fail"
    name, fn = target

    async def _run():
        recorded = []

        async def _mock(gap_type, detail, source):
            recorded.append(gap_type)

        original = wire._record_gap
        wire._record_gap = _mock
        try:
            with pytest.raises(Exception):
                if inspect.iscoroutinefunction(fn):
                    await fn()
                else:
                    fn()
            import time
            deadline = time.time() + 5
            while time.time() < deadline:
                if recorded:
                    break
                await asyncio.sleep(0.01)
        finally:
            wire._record_gap = original

        assert recorded, f"{name}(): no gap recorded — handler @fail_wire did not fire"
        assert "engine_failure" in recorded, f"{name}(): expected engine_failure, got {recorded}"

    asyncio.run(_run())
