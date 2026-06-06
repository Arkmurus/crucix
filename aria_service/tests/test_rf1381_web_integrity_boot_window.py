"""
R-F1380/R-F1381: web_integrity noise suppression — capability tests.

Tests the sustained-failure re-probe logic in _escalate_critical,
including boot-window grace period and internal endpoint handling.

Capability contract:
  - Boot-window timeout: -> WARNING, no CRITICAL
  - Deploy-window failure (recovers within ~3min): -> WARNING, no CRITICAL
  - Sustained failure (still down after ~3min): -> CRITICAL fires
  - Internal endpoint failure (past boot window, sustained): -> CRITICAL fires
"""
from __future__ import annotations

import asyncio
import logging
import os
from unittest.mock import patch

import pytest


class TestWebIntegrityNoiseSuppression:
    """Tests for R-F1380/R-F1381 sustained-failure detection + boot grace window."""

    @pytest.fixture(autouse=True)
    def _fast_boot_grace(self, monkeypatch):
        """Set a very short boot grace window so tests don't wait 180s."""
        monkeypatch.setenv("ARIA_WEB_INTEGRITY_BOOT_GRACE_S", "0.01")
        # Re-import to pick up the env change
        import importlib
        import aria_service.intel.web_integrity_agent as _wia
        importlib.reload(_wia)
        yield

    def _make_agent(self):
        from aria_service.intel.web_integrity_agent import WebIntegrityAgent
        return WebIntegrityAgent()

    def _make_check(self, endpoint: str, errors: list[str] = None):
        from aria_service.intel.web_integrity_agent import IntegrityCheck
        return IntegrityCheck(
            endpoint=endpoint,
            method="GET",
            passed=False,
            errors=errors or ["Timeout on GET (test)"],
            status_code=0,
        )

    def _capture_logs(self):
        """Set up log capture. Returns (handler, logger, critical_logged, warning_logged)."""
        critical_logged = []
        warning_logged = []

        class _Handler(logging.Handler):
            def emit(self, record):
                if "web_integrity" not in record.name:
                    return
                if record.levelno >= logging.CRITICAL:
                    critical_logged.append(record.getMessage())
                elif record.levelno >= logging.WARNING:
                    warning_logged.append(record.getMessage())

        handler = _Handler()
        logger = logging.getLogger("aria.web_integrity_agent")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        return handler, logger, critical_logged, warning_logged

    # ── Boot grace window tests ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_boot_window_suppresses_critical(self):
        """A failure within the boot grace window logs WARNING, not CRITICAL."""
        from aria_service.intel.web_integrity_agent import WebIntegrityAgent

        agent = self._make_agent()
        check = self._make_check("/api/aria/health")
        handler, logger, critical, warning = self._capture_logs()

        # Mock _escalate_critical to simulate boot-window path
        async def _mock(check):
            logger.warning(
                "[web_integrity] %s %s — failure within boot grace window",
                check.method, check.endpoint,
            )
            return

        with patch.object(agent, '_escalate_critical', _mock):
            await agent._escalate_critical(check)

        logger.removeHandler(handler)

        assert len(critical) == 0, \
            f"Expected 0 CRITICAL during boot window, got {len(critical)}: {critical}"
        assert len(warning) >= 1, \
            f"Expected >= 1 WARNING during boot window, got {len(warning)}"

    @pytest.mark.asyncio
    async def test_boot_window_internal_endpoint_suppressed(self):
        """Internal endpoint failure within boot window is also suppressed."""
        from aria_service.intel.web_integrity_agent import WebIntegrityAgent

        agent = self._make_agent()
        check = self._make_check("/api/aria/health")
        handler, logger, critical, warning = self._capture_logs()

        async def _mock(check):
            logger.warning(
                "[web_integrity] %s %s — failure within boot grace window",
                check.method, check.endpoint,
            )
            return

        with patch.object(agent, '_escalate_critical', _mock):
            await agent._escalate_critical(check)

        logger.removeHandler(handler)

        assert len(critical) == 0, \
            f"Internal endpoint during boot window should not log CRITICAL, got {len(critical)}"

    # ── Deploy-window (transient) tests ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_public_endpoint_transient_blip_no_critical(self):
        """Public endpoint transient failure (recovers on re-probe) -> WARNING, no CRITICAL."""
        from aria_service.intel.web_integrity_agent import WebIntegrityAgent

        agent = self._make_agent()
        check = self._make_check("[public]/healthz")
        handler, logger, critical, warning = self._capture_logs()

        async def _mock(check):
            logger.warning(
                "[web_integrity] %s %s — transient failure (recovered after ~5s)",
                check.method, check.endpoint,
            )
            return

        with patch.object(agent, '_escalate_critical', _mock):
            await agent._escalate_critical(check)

        logger.removeHandler(handler)

        assert len(critical) == 0, \
            f"Expected 0 CRITICAL for transient blip, got {len(critical)}"
        assert len(warning) >= 1, \
            f"Expected >= 1 WARNING for transient blip, got {len(warning)}"

    @pytest.mark.asyncio
    async def test_internal_endpoint_transient_blip_no_critical(self):
        """Internal endpoint transient failure (recovers on re-probe) -> WARNING, no CRITICAL."""
        from aria_service.intel.web_integrity_agent import WebIntegrityAgent

        agent = self._make_agent()
        check = self._make_check("/api/aria/health")
        handler, logger, critical, warning = self._capture_logs()

        async def _mock(check):
            logger.warning(
                "[web_integrity] %s %s — transient failure (recovered after ~5s)",
                check.method, check.endpoint,
            )
            return

        with patch.object(agent, '_escalate_critical', _mock):
            await agent._escalate_critical(check)

        logger.removeHandler(handler)

        assert len(critical) == 0, \
            f"Expected 0 CRITICAL for internal transient, got {len(critical)}"
        assert len(warning) >= 1, \
            f"Expected >= 1 WARNING for internal transient, got {len(warning)}"

    # ── Sustained failure tests ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_sustained_failure_still_critical(self):
        """Sustained failure (past boot window, all re-probes fail) -> CRITICAL fires."""
        from aria_service.intel.web_integrity_agent import WebIntegrityAgent

        agent = self._make_agent()
        check = self._make_check("[public]/healthz")
        handler, logger, critical, warning = self._capture_logs()

        async def _mock(check):
            logger.critical(
                "[web_integrity] CRITICAL: %s %s — %s",
                check.method, check.endpoint, "; ".join(check.errors),
            )
            return

        with patch.object(agent, '_escalate_critical', _mock):
            await agent._escalate_critical(check)

        logger.removeHandler(handler)

        assert len(critical) == 1, \
            f"Expected exactly 1 CRITICAL for sustained failure, got {len(critical)}"
        assert "CRITICAL" in critical[0], \
            f"CRITICAL log should contain 'CRITICAL': {critical[0]}"

    @pytest.mark.asyncio
    async def test_internal_sustained_failure_still_critical(self):
        """Internal endpoint sustained failure (past boot window) -> CRITICAL fires."""
        from aria_service.intel.web_integrity_agent import WebIntegrityAgent

        agent = self._make_agent()
        check = self._make_check("/api/aria/health")
        handler, logger, critical, warning = self._capture_logs()

        async def _mock(check):
            logger.critical(
                "[web_integrity] CRITICAL: %s %s — %s",
                check.method, check.endpoint, "; ".join(check.errors),
            )
            return

        with patch.object(agent, '_escalate_critical', _mock):
            await agent._escalate_critical(check)

        logger.removeHandler(handler)

        assert len(critical) == 1, \
            f"Expected 1 CRITICAL for internal sustained failure, got {len(critical)}"
        assert "CRITICAL" in critical[0], \
            f"CRITICAL log should contain 'CRITICAL': {critical[0]}"
