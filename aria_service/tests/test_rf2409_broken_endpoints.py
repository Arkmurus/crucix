"""R-F2409 — two brain GET endpoints returned HTTP 500 (found in the endpoint audit).

1. GET /api/aria/dd/layer-5c/digital-stats — 500 AttributeError: 'NoneType' has no
   attribute 'get'. `ext.get("by_module")` returned an explicit None (key present,
   null value) and the code did `by_mod.get(...)` unguarded.
2. GET /api/aria/sources/health — 500 ImportError: cannot import
   SELF_DIAGNOSTIC_CATALOGUE (never existed); the live catalogue is `_MODULES`.

These capability tests call the actual handler functions and assert a clean dict.
"""
from __future__ import annotations

import asyncio
import pytest

from aria_service.routes import aria


def test_digital_stats_handles_null_by_module(monkeypatch):
    """Reproduces the 500: a report whose extensions.by_module is null must not
    crash the aggregation."""
    from aria_service.intel import dd_orchestrator

    async def _fake_list_reports(*a, **k):
        return [
            {"extensions": {"by_module": None}, "digital": True},   # the crashing shape
            {"extensions": {"by_module": {"cert_transparency": True}}},
            {"extensions": None},
        ]
    monkeypatch.setattr(dd_orchestrator, "list_reports", _fake_list_reports)

    out = asyncio.run(aria.dd_layer_5c_digital_stats_ep(limit=10))
    assert isinstance(out, dict)
    assert out["total_entities_scanned"] == 3
    assert out["with_cert_transparency"] == 1
    assert out["with_web_presence"] == 1


def test_sources_health_import_resolves_and_returns(monkeypatch):
    """The _MODULES import must resolve (was ImportError) and the endpoint must
    return a populated health dict."""
    from aria_service.intel import redis_store

    async def _no_errlog(key):
        return None
    monkeypatch.setattr(redis_store, "get", _no_errlog)

    out = asyncio.run(aria.sources_health_ep())
    assert isinstance(out, dict)
    assert "sources" in out and "summary" in out
    assert out["summary"]["total"] > 0, "catalogue (_MODULES) must seed known modules"
    # with no error log, every seeded module rolls up to healthy
    assert out["summary"]["healthy"] == out["summary"]["total"]


def test_modules_catalogue_shape():
    """Guards the fix's premise: self_diagnostic._MODULES exists and its entries
    carry the name/module fields the endpoint reads."""
    from aria_service.intel.self_diagnostic import _MODULES
    assert isinstance(_MODULES, list) and _MODULES
    assert all("name" in e for e in _MODULES)
