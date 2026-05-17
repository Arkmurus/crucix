"""R-F668 — load-bearing brain_hook modules get severity=critical when stale.

Audit 2026-05-17: "No stale-module alert fires when email_reader goes
silent for days" — historical 2026-04-18 incident had 302 emails read
with 0 brain signals (seenode→Python token mismatch). Existing
get_stale_alerts() was emitting severity=warning even for load-bearing
modules, so the alert sat in /api/aria/brain/stats but didn't ride the
proactive-alerts pipeline.

R-F668 defines _LOAD_BEARING_MODULES and bumps their severity to
critical (when stale) or critical (when never-seen — that's a wiring
bug, not informational).

Verifies:
  - _LOAD_BEARING_MODULES covers the audit-flagged set
  - _stale_severity / _never_seen_severity branch correctly
  - get_stale_alerts emits the critical severity for load-bearing
  - get_stale_alerts emits warning/info for non-load-bearing modules
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from aria_service.intel import brain_hook as bh


def test_rf668_load_bearing_set_covers_audit():
    """email_reader is the specific module the audit flagged; the
    other four are load-bearing per the chat / DD / autonomous pipelines."""
    assert "email_reader" in bh._LOAD_BEARING_MODULES
    assert "aria_chat" in bh._LOAD_BEARING_MODULES
    assert "web_atlas" in bh._LOAD_BEARING_MODULES
    assert "knowledge_ingestor" in bh._LOAD_BEARING_MODULES
    assert "knowledge_spider" in bh._LOAD_BEARING_MODULES


def test_rf668_stale_severity_critical_for_load_bearing():
    assert bh._stale_severity("email_reader") == "critical"
    assert bh._stale_severity("aria_chat") == "critical"
    assert bh._stale_severity("web_atlas") == "critical"


def test_rf668_stale_severity_warning_for_other():
    """Non-load-bearing modules keep the existing warning severity so
    we don't page on every quiet 24h period of a low-traffic module."""
    assert bh._stale_severity("verified_intel_compliance") == "warning"
    assert bh._stale_severity("anything_else") == "warning"


def test_rf668_never_seen_severity_critical_for_load_bearing():
    """A load-bearing module that has NEVER sent a signal is a wiring
    bug, not informational."""
    assert bh._never_seen_severity("email_reader") == "critical"
    assert bh._never_seen_severity("knowledge_spider") == "critical"


def test_rf668_never_seen_severity_info_for_other():
    assert bh._never_seen_severity("some_low_volume_module") == "info"


def test_rf668_get_stale_alerts_flags_load_bearing_critical():
    """End-to-end: when email_reader appears in stats.stale_modules,
    the alert dict must have severity='critical' and load_bearing=True."""

    async def _fake_get_stats():
        return {
            "stale_modules": ["email_reader", "verified_intel_compliance"],
            "never_seen": [],
            "modules": {
                "email_reader": {"last_signal_ago_h": 48.2},
                "verified_intel_compliance": {"last_signal_ago_h": 31.0},
            },
        }

    async def runner():
        with patch.object(bh, "get_stats", _fake_get_stats):
            return await bh.get_stale_alerts()

    alerts = asyncio.run(runner())
    by_mod = {a["module"]: a for a in alerts}

    assert by_mod["email_reader"]["severity"] == "critical"
    assert by_mod["email_reader"]["load_bearing"] is True
    assert by_mod["email_reader"]["last_signal_ago_h"] == 48.2

    assert by_mod["verified_intel_compliance"]["severity"] == "warning"
    assert by_mod["verified_intel_compliance"]["load_bearing"] is False


def test_rf668_get_stale_alerts_flags_never_seen_load_bearing():
    """A load-bearing module that's never been seen → critical, not info."""

    async def _fake_get_stats():
        return {
            "stale_modules": [],
            "never_seen": ["email_reader", "some_optional_module"],
            "modules": {},
        }

    async def runner():
        with patch.object(bh, "get_stats", _fake_get_stats):
            return await bh.get_stale_alerts()

    alerts = asyncio.run(runner())
    by_mod = {a["module"]: a for a in alerts}

    assert by_mod["email_reader"]["severity"] == "critical"
    assert by_mod["email_reader"]["load_bearing"] is True
    assert by_mod["some_optional_module"]["severity"] == "info"
    assert by_mod["some_optional_module"]["load_bearing"] is False
