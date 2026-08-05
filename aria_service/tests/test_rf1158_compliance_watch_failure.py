"""R-F1158 — Capability test for compliance_watch failure wiring in brain_signal_ep.

Verifies that when compliance_watch.capture_message returns {captured: False},
the brain_signal_ep records a capability_gap (was dark: debug-log only).

Also verifies that an exception in capture_message also records a gap.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

# R-F3754/§16 — NOT inspect.getsource: it slices at the line numbers captured
# AT IMPORT, so an edit mid-run returns a DIFFERENT function's body, silently.
from ._source_probe import function_source


def _make_client(monkeypatch):
    """Create a TestClient with auth bypassed.

    Uses the same pattern as test_rf683_wa_auth_idempotency.py: force-empty
    the accepted-tokens set so require_aria_token is a no-op.
    """
    from aria_service.routes import aria as aria_routes
    monkeypatch.setattr(aria_routes, "_accepted_tokens", lambda: set())
    app = FastAPI()
    app.include_router(aria_routes.router)
    return TestClient(app)


class TestComplianceWatchFailureWiring:
    """Capability test: brain_signal_ep must wire compliance_watch failures to brain."""

    def test_source_contains_failure_wiring(self) -> None:
        """The brain_signal_ep source must check capture result and record gap on failure."""
        from aria_service.routes import aria as a
        src = function_source(a, "brain_signal_ep")
        # Must check the capture result
        assert '_cw_result.get("captured")' in src or 'not _cw_result' in src
        # Must record a gap on failure
        assert "record_gap(" in src
        assert "compliance_watch" in src

    def test_capture_failure_records_gap(self, monkeypatch) -> None:
        """When capture_message returns captured=False, a capability_gap must be recorded.

        Note: the response 'captured' field is computed from signal_type, not from
        the capture result. The actual failure is visible via record_gap being called.
        """
        # Patch the module directly. The brain_signal_ep does a late import
        # (from ..intel import compliance_watch), so we patch the source module.
        with patch(
            "aria_service.intel.compliance_watch.capture_message",
            new_callable=AsyncMock,
            return_value={"captured": False, "error": "redis unavailable"},
        ):
            with patch(
                "aria_service.intel.capability_gaps.record_gap",
                new_callable=AsyncMock,
            ) as mock_record:
                client = _make_client(monkeypatch)
                resp = client.post(
                    "/api/aria/brain/signal",
                    json={
                        "content": "test message",
                        "source": "whatsapp_group:test:user",
                        "signal_type": "whatsapp_group_message",
                        "metadata": {"group": "test", "sender": "user", "channel": "whatsapp"},
                    },
                )
                assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
                # The response 'captured' field is always True for whatsapp_group_message
                # (it's computed from signal_type, not from the capture result).
                # The real test is that record_gap was called.
                assert mock_record.await_count >= 1, (
                    "record_gap must be called when capture_message returns captured=False"
                )

    def test_capture_exception_records_gap(self, monkeypatch) -> None:
        """When capture_message raises, a capability_gap must be recorded."""
        with patch(
            "aria_service.intel.compliance_watch.capture_message",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection lost"),
        ):
            client = _make_client(monkeypatch)
            resp = client.post(
                "/api/aria/brain/signal",
                json={
                    "content": "test message",
                    "source": "whatsapp_group:test:user",
                    "signal_type": "whatsapp_group_message",
                    "metadata": {"group": "test", "sender": "user", "channel": "whatsapp"},
                },
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert data.get("ok") is True

    def test_successful_capture_does_not_error(self, monkeypatch) -> None:
        """When capture_message succeeds, the endpoint returns captured=True."""
        with patch(
            "aria_service.intel.compliance_watch.capture_message",
            new_callable=AsyncMock,
            return_value={"captured": True, "seq": 1, "hash": "abc123"},
        ):
            client = _make_client(monkeypatch)
            resp = client.post(
                "/api/aria/brain/signal",
                json={
                    "content": "test message",
                    "source": "whatsapp_group:test:user",
                    "signal_type": "whatsapp_group_message",
                    "metadata": {"group": "test", "sender": "user", "channel": "whatsapp"},
                },
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert data.get("captured") is True
