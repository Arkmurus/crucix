"""R-F648 — GET /api/aria/neural/conflicts surfaces the conflict log.

The conflict log (crucix:aria:neural_conflicts) was permanent-retention
since 2026-04-21 but had NO read endpoint. The operator could not see
what was being flagged. R-F648 adds a router-protected GET that returns
the recent entries, mirroring the auth + shape of other /neural/*
endpoints.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_SRC = (Path(__file__).resolve().parent.parent / "routes" / "aria.py").read_text(
    encoding="utf-8"
)


def test_rf648_route_decorator_present():
    """The router must declare GET /neural/conflicts."""
    assert re.search(
        r'@router\.get\(\s*"/neural/conflicts"\s*\)', _SRC
    ), "R-F648: GET /neural/conflicts route decorator missing"


def test_rf648_handler_calls_get_conflicts():
    """The handler must await neural_memory.get_conflicts(limit=...)."""
    # Find the route block (decorator + handler function body)
    m = re.search(
        r'@router\.get\(\s*"/neural/conflicts"\s*\)\s*\n'
        r'async def neural_conflicts_list_ep\([^)]*\):\s*\n'
        r'(.+?)(?=\n@router\.|\n# \d+|\Z)',
        _SRC, re.DOTALL,
    )
    assert m, "R-F648: neural_conflicts_list_ep handler not found in expected shape"
    body = m.group(1)
    assert "get_conflicts" in body, (
        "R-F648: handler does not call neural_memory.get_conflicts"
    )


def test_rf648_limit_is_capped():
    """A handler that accepts a `limit` query param must cap it server-side
    so a malicious or accidental ?limit=10000000 doesn't OOM the response.
    The convention here is min 1, max 500."""
    m = re.search(
        r'@router\.get\(\s*"/neural/conflicts"\s*\)\s*\n'
        r'async def neural_conflicts_list_ep\([^)]*\):\s*\n'
        r'(.+?)(?=\n@router\.|\n# \d+|\Z)',
        _SRC, re.DOTALL,
    )
    assert m
    body = m.group(1)
    # Look for an explicit cap — either min()/max() bounds or an
    # equivalent clamp.
    has_clamp = bool(re.search(r"\bmin\s*\(\s*limit\s*,\s*\d+", body)) or \
                bool(re.search(r"\bmin\s*\(\s*\d+\s*,\s*limit", body))
    assert has_clamp, (
        "R-F648: limit is not clamped — endpoint can be asked for unbounded "
        "response size"
    )


def test_rf648_endpoint_returns_shape(monkeypatch):
    """End-to-end: call the handler with a fake get_conflicts and verify
    the response shape is {limit, count, conflicts}."""
    import asyncio
    from aria_service.routes import aria as routes_mod
    from aria_service.intel import neural_memory as nm

    async def _fake_get_conflicts(limit=20):
        return [
            {
                "entity": "Some Counterparty",
                "existing_assessment": "HIGH_RISK",
                "new_assessment": "LOW_RISK",
                "detected_at": 1779000000.0,
                "new_text_preview": "...",
            }
        ]

    monkeypatch.setattr(nm, "get_conflicts", _fake_get_conflicts)
    result = asyncio.run(routes_mod.neural_conflicts_list_ep(limit=10))
    assert isinstance(result, dict)
    assert result["limit"] == 10
    assert result["count"] == 1
    assert isinstance(result["conflicts"], list)
    assert result["conflicts"][0]["entity"] == "Some Counterparty"


def test_rf648_endpoint_caps_limit(monkeypatch):
    """A ?limit=10000 request must be clamped to 500."""
    import asyncio
    from aria_service.routes import aria as routes_mod
    from aria_service.intel import neural_memory as nm

    captured = {}

    async def _spy_get_conflicts(limit=20):
        captured["limit"] = limit
        return []

    monkeypatch.setattr(nm, "get_conflicts", _spy_get_conflicts)
    result = asyncio.run(routes_mod.neural_conflicts_list_ep(limit=10_000))
    assert captured["limit"] == 500
    assert result["limit"] == 500
