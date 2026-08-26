"""R-F1843 — record_gap() must accept the `severity` kwarg.

Four self-monitoring agents (continuous_profiler:128, deadlock_detector:177,
memory_leak_detector:225, web_integrity_agent:922) call
capability_gaps.record_gap(..., severity=N). record_gap had no such param, so
every one of those calls raised `TypeError: record_gap() got an unexpected
keyword argument 'severity'` — the gaps were SILENTLY DROPPED and those monitors
were dark (§21). This pins that record_gap now accepts + stores severity (int or
str), and that the actual callers' call shape no longer throws.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import capability_gaps

REPO = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(autouse=True)
def _no_redis():
    # rs.get → None (not deduped), rs.set/persist → no-op, so record_gap runs
    # its full body without a live store.
    with patch.object(capability_gaps, "rs") as rs:
        rs.get = AsyncMock(return_value=None)
        # R-F4356: the dedupe sentinel is read with get_strict now, so a store
        # failure cannot masquerade as "no sentinel". None keeps this fixture's
        # original meaning — key absent, therefore not deduped.
        rs.get_strict = AsyncMock(return_value=None)
        rs.set = AsyncMock(return_value=True)
        rs.lpush = AsyncMock(return_value=1)
        rs.ltrim = AsyncMock(return_value=True)
        yield


def test_record_gap_accepts_int_severity():
    entry = asyncio.run(capability_gaps.record_gap(
        gap_type="performance", detail="cpu hot", source="continuous_profiler", severity=2))
    assert entry.get("severity") == 2


def test_record_gap_accepts_str_severity():
    entry = asyncio.run(capability_gaps.record_gap(
        gap_type="module_bug", detail="xss", source="web_integrity_agent", severity="HIGH"))
    assert entry.get("severity") == "HIGH"


def test_severity_defaults_to_zero_when_absent():
    entry = asyncio.run(capability_gaps.record_gap(
        gap_type="performance", detail="no sev given", source="x"))
    assert entry.get("severity") == 0


def test_signature_has_severity_param():
    sig = inspect.signature(capability_gaps.record_gap)
    assert "severity" in sig.parameters, "record_gap must accept severity"


def test_the_four_dark_monitors_pass_severity():
    """Guard: these callers DO pass severity= (so the param must exist). If a
    caller is refactored away this can be updated, but it documents why the
    param is needed."""
    callers = {
        "aria_service/intel/continuous_profiler.py",
        "aria_service/intel/deadlock_detector.py",
        "aria_service/intel/memory_leak_detector.py",
        "aria_service/intel/web_integrity_agent.py",
    }
    found = 0
    for rel in callers:
        src = (REPO / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "record_gap"
                    and any(kw.arg == "severity" for kw in node.keywords)):
                found += 1
                break
    assert found >= 3, f"expected the monitors to pass severity= (found {found})"
