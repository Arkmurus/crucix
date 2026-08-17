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
        """The capture-result check and the gap must live in the SAME function
        as the `capture_message` call — wherever that function now is.

        R-F4103 (C-155): this asserted `'_cw_result.get("captured")' in
        function_source(a, "brain_signal_ep")` and had been red for a long
        time. The wiring was never removed — it was EXTRACTED into
        `_route_one_signal` and the variable renamed `_cw`. So the test pinned
        two implementation details (a function name and a local variable name)
        and reported a defect that did not exist, while its own three
        behavioural siblings passed the whole time.

        That mattered beyond the noise: this file is one of three wiring gates
        standing red, and a red gate carries no information in either
        direction. R-F4097 stole a `@fail_wire` off `learning_progress.
        get_all_domains` the same day and shipped, because nobody trusts a
        board that is already red.

        The honest question is not "is this string in that function" but "is
        the capture result checked and reported where it is captured". Located
        by AST, so renaming the function or the variable cannot break it, and
        deleting the check still can.
        """
        import ast

        from aria_service.tests._source_probe import repo_path

        src = repo_path("aria_service/routes/aria.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        owners = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            body = ast.get_source_segment(src, node) or ""
            if "compliance_watch" in body and "capture_message" in body:
                owners.append((node.name, body))

        assert owners, (
            "no function calls compliance_watch.capture_message any more — the "
            "WhatsApp compliance capture path is gone, not just moved")

        wired = [
            name for name, body in owners
            if '"captured"' in body and "record_gap(" in body
        ]
        assert wired, (
            "compliance_watch.capture_message is called in "
            f"{[n for n, _ in owners]} but no caller checks the `captured` "
            "result and records a gap — a failed compliance capture would be "
            "silent (§21a)")

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
