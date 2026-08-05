"""R-F3722 — CAPABILITY: a blind store must not disable ARIA's metabolism.

`refresh_runtime_override()` read the master switch with `rs.get`, which returns
None on a store FAILURE as well as on an absent key, and wrote that None into the
cache. `is_enabled()` then fell through to ARIA_AUTONOMOUS_ENABLED — which is "0"
in production, because the durable override is the only thing keeping autonomy on
(§1 gate #5 / R-F3640). So one unreadable read turned autonomy OFF.

At boot that is not a flicker: `main.py:3966` calls `start_engine()` once, it hard-
refuses when `is_enabled()` is False, and nothing retries — the R-F2004 class
(metabolism dark for 187h), reachable from the slow-booting store §11c calls the
NORMAL cold-boot condition.

The safety net `maybe_autorecover_master_switch()` shared the blindness: it read
the desired-enabled marker with the same lenient `get`, so an unreadable store
made it report "no desired-enabled marker" — the wrong cause, pointing at the
wrong fix, at exactly the moment it was supposed to catch the fall.

Run: python -m pytest aria_service/tests/test_rf3722_autonomy_switch_blind_store.py -v
"""
from __future__ import annotations

import asyncio

import pytest


def _engine(monkeypatch):
    from aria_service.autonomous import engine
    # production posture: the env floor is OFF, so the override is load-bearing
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "0")
    return engine


def test_a_store_blip_does_not_disable_autonomy(monkeypatch):
    """THE HEADLINE: autonomy stays ON across an unreadable read."""
    engine = _engine(monkeypatch)
    from aria_service.intel import redis_store

    engine._RUNTIME_ENABLE_CACHE["val"] = "1"          # operator had it enabled
    assert engine.is_enabled() is True

    async def _boom(key):
        raise redis_store.StoreReadError("no read connection (reconnect in progress)")

    monkeypatch.setattr(redis_store, "get_strict", _boom)
    asyncio.run(engine.refresh_runtime_override())

    assert engine._RUNTIME_ENABLE_CACHE.get("val") == "1", (
        "an unreadable store erased the master switch — at boot this is the "
        "R-F2004 outage class, because start_engine() never retries"
    )
    assert engine.is_enabled() is True


def test_a_readable_absent_key_still_clears_the_override(monkeypatch):
    """The guard must not freeze the switch: absent-and-readable still clears."""
    engine = _engine(monkeypatch)
    from aria_service.intel import redis_store

    engine._RUNTIME_ENABLE_CACHE["val"] = "1"

    async def _absent(key):
        return None

    monkeypatch.setattr(redis_store, "get_strict", _absent)
    asyncio.run(engine.refresh_runtime_override())

    assert engine._RUNTIME_ENABLE_CACHE.get("val") is None
    assert engine.is_enabled() is False   # falls through to env, correctly


def test_a_deliberate_disable_still_wins(monkeypatch):
    """override="0" is an operator decision and must survive the fix."""
    engine = _engine(monkeypatch)
    from aria_service.intel import redis_store

    engine._RUNTIME_ENABLE_CACHE["val"] = "1"

    async def _zero(key):
        return "0"

    monkeypatch.setattr(redis_store, "get_strict", _zero)
    asyncio.run(engine.refresh_runtime_override())
    assert engine._RUNTIME_ENABLE_CACHE.get("val") == "0"
    assert engine.is_enabled() is False


def test_autorecover_does_not_blame_a_missing_marker_for_a_dead_store(monkeypatch):
    """The net reported the WRONG CAUSE: 'no marker' when it simply could not read."""
    engine = _engine(monkeypatch)
    from aria_service.intel import redis_store

    engine._RUNTIME_ENABLE_CACHE["val"] = None        # master-disabled
    assert engine.is_enabled() is False

    async def _boom(key):
        raise redis_store.StoreReadError("no read connection")

    monkeypatch.setattr(redis_store, "get_strict", _boom)
    res = asyncio.run(engine.maybe_autorecover_master_switch())

    assert res.get("recovered") is False
    assert "UNREADABLE" in (res.get("reason") or ""), (
        f"reported {res.get('reason')!r} — an unreadable store must not be "
        f"reported as the operator never having enabled autonomy"
    )


def test_autorecover_still_restores_a_genuinely_lost_flag(monkeypatch):
    """R-F2184's actual job must survive: readable marker=1 → recover."""
    engine = _engine(monkeypatch)
    from aria_service.intel import redis_store

    engine._RUNTIME_ENABLE_CACHE["val"] = None
    restored: dict = {}

    async def _desired(key):
        return "1"

    async def _set(key, val, **kw):
        restored[key] = val

    monkeypatch.setattr(redis_store, "get_strict", _desired)
    monkeypatch.setattr(redis_store, "set", _set)

    res = asyncio.run(engine.maybe_autorecover_master_switch())
    assert res.get("recovered") is True, res
    assert engine.is_enabled() is True


def test_the_blindness_is_wired_to_the_brain():
    """§21a — the master switch going blind must not be a debug log."""
    from aria_service.autonomous import engine
    from ._source_probe import function_source

    src = function_source(engine, "refresh_runtime_override")
    assert "wire_failure" in src and "get_strict" in src
    assert "logger.debug" not in src, (
        "a failure that can disable the entire autonomous subsystem was logged "
        "at debug level — invisible in production"
    )


def test_refresh_never_raises_even_if_the_store_module_is_unimportable(monkeypatch):
    """R-F3732 — `_engine_loop` awaits this BARE, so it must not throw.

    R-F3722 lifted the redis_store import out of the try. The original wrapped
    everything, so callers could rely on never-raises; the tick loop still does.
    An unimportable store module is "no news", exactly like an unreadable one.
    """
    import builtins

    engine = _engine(monkeypatch)
    engine._RUNTIME_ENABLE_CACHE["val"] = "1"

    real_import = builtins.__import__

    def _boom(name, globals=None, locals=None, fromlist=(), level=0):
        if "redis_store" in name or (fromlist and "redis_store" in fromlist):
            raise ImportError("redis_store unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _boom)
    # must NOT raise, and must not erase the operator's enabled state
    assert asyncio.run(engine.refresh_runtime_override()) == "1"
    assert engine._RUNTIME_ENABLE_CACHE.get("val") == "1"
    assert engine.is_enabled() is True
