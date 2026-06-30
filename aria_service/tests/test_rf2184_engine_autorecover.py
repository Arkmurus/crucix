# -*- coding: utf-8 -*-
"""Capability tests for R-F2184 — autonomous-engine master-switch auto-recovery.

Heals a LOST flag (env default off + no override) when the operator's durable
intent is enabled, while RESPECTING a deliberate disable (override="0"). This is
the R-F2004 187h-outage class: a dropped ARIA_AUTONOMOUS_ENABLED silently killed
the whole real-time metabolism and was only alerted, never recovered.
"""
from __future__ import annotations

import asyncio

import aria_service.autonomous.engine as engine
import aria_service.intel.redis_store as rs
# Direct-import the called functions (bare calls) so the pre-commit direct-calls
# check resolves them; `engine` is kept only for module-state attribute access.
from aria_service.autonomous.engine import (
    maybe_autorecover_master_switch,
    _mark_desired_enabled,
)


def _set_cache(val):
    engine._RUNTIME_ENABLE_CACHE["val"] = val


def _patch_rs(monkeypatch, store):
    async def fget(k):
        return store.get(k)

    async def fset(k, v, ex=None):
        store[k] = v
        return True

    async def fdel(k):
        store.pop(k, None)
        return True

    monkeypatch.setattr(rs, "get", fget)
    monkeypatch.setattr(rs, "set", fset)
    monkeypatch.setattr(rs, "delete", fdel)


def test_rf2184_recovers_lost_flag(monkeypatch):
    monkeypatch.delenv("ARIA_AUTONOMOUS_ENABLED", raising=False)
    monkeypatch.delenv("ARIA_AUTONOMOUS_AUTORECOVER", raising=False)
    _set_cache(None)  # no override → is_enabled() = env default off = False
    store = {engine._DESIRED_KEY: "1"}
    _patch_rs(monkeypatch, store)
    res = asyncio.run(maybe_autorecover_master_switch())
    assert res["recovered"] is True
    assert engine._RUNTIME_ENABLE_CACHE["val"] == "1"           # restored in cache
    assert store[engine._REDIS_ENABLE_KEY] == "1"               # restored in Redis


def test_rf2184_respects_deliberate_disable(monkeypatch):
    monkeypatch.delenv("ARIA_AUTONOMOUS_ENABLED", raising=False)
    _set_cache("0")  # operator deliberately disabled via /autonomous/disable
    store = {engine._DESIRED_KEY: "1"}
    _patch_rs(monkeypatch, store)
    res = asyncio.run(maybe_autorecover_master_switch())
    assert res["recovered"] is False and "respected" in res["reason"]
    assert engine._RUNTIME_ENABLE_CACHE["val"] == "0"           # NOT overridden


def test_rf2184_no_recovery_without_desired_marker(monkeypatch):
    monkeypatch.delenv("ARIA_AUTONOMOUS_ENABLED", raising=False)
    _set_cache(None)
    _patch_rs(monkeypatch, {})  # no desired marker
    res = asyncio.run(maybe_autorecover_master_switch())
    assert res["recovered"] is False


def test_rf2184_skips_when_already_enabled(monkeypatch):
    _set_cache("1")  # already enabled
    _patch_rs(monkeypatch, {engine._DESIRED_KEY: "1"})
    res = asyncio.run(maybe_autorecover_master_switch())
    assert res["recovered"] is False and "already enabled" in res["reason"]


def test_rf2184_env_gate_off(monkeypatch):
    monkeypatch.delenv("ARIA_AUTONOMOUS_ENABLED", raising=False)
    monkeypatch.setenv("ARIA_AUTONOMOUS_AUTORECOVER", "0")
    _set_cache(None)
    _patch_rs(monkeypatch, {engine._DESIRED_KEY: "1"})
    res = asyncio.run(maybe_autorecover_master_switch())
    assert res["recovered"] is False and "disabled by env" in res["reason"]


def test_rf2184_mark_desired_sets_marker(monkeypatch):
    engine._desired_marked = False
    store = {}
    _patch_rs(monkeypatch, store)
    asyncio.run(_mark_desired_enabled())
    assert store.get(engine._DESIRED_KEY) == "1"
