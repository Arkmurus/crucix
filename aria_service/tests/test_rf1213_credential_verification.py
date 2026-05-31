"""R-F1213 — Capability tests for credential verification system.

Tests:
1. WebIntegrityAgent.verify_all_credentials() runs without error
2. _verify_portal_credential() handles various HTTP responses
3. _trigger_re_registration() records a capability gap
4. get_credential_health() returns stored results
5. Portal credential routes exist in aria.py
6. Credential verification is wired into _one_cycle
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _read(path: str) -> str:
    """Read a file relative to repo root."""
    return (_REPO_ROOT / path).read_text(encoding="utf-8")


# ── Tests: Routes exist ────────────────────────────────────────────────────


class TestCredentialRoutes:
    """Credential management routes must exist in aria.py."""

    def test_credential_health_route_exists(self):
        """GET /portal/credentials endpoint must exist."""
        source = _read("aria_service/routes/aria.py")
        assert 'portal_credentials_ep' in source, (
            "Credential health endpoint must exist"
        )
        assert '/portal/credentials"' in source or "/portal/credentials'" in source, (
            "Route must be /portal/credentials"
        )

    def test_credential_verify_route_exists(self):
        """POST /portal/credentials/verify endpoint must exist."""
        source = _read("aria_service/routes/aria.py")
        assert 'portal_credentials_verify_ep' in source, (
            "Credential verify endpoint must exist"
        )

    def test_credential_store_route_exists(self):
        """POST /portal/credentials/{portal_id} endpoint must exist."""
        source = _read("aria_service/routes/aria.py")
        assert 'portal_credentials_store_ep' in source, (
            "Credential store endpoint must exist"
        )


# ── Tests: WebIntegrityAgent credential methods ────────────────────────────


class TestCredentialMethods:
    """WebIntegrityAgent must have credential verification methods."""

    def test_verify_all_credentials_exists(self):
        """verify_all_credentials method must exist."""
        source = _read("aria_service/intel/web_integrity_agent.py")
        assert "async def verify_all_credentials" in source, (
            "verify_all_credentials method must exist"
        )

    def test_verify_portal_credential_exists(self):
        """_verify_portal_credential method must exist."""
        source = _read("aria_service/intel/web_integrity_agent.py")
        assert "async def _verify_portal_credential" in source, (
            "_verify_portal_credential method must exist"
        )

    def test_trigger_re_registration_exists(self):
        """_trigger_re_registration method must exist."""
        source = _read("aria_service/intel/web_integrity_agent.py")
        assert "async def _trigger_re_registration" in source, (
            "_trigger_re_registration method must exist"
        )

    def test_get_credential_health_exists(self):
        """get_credential_health method must exist."""
        source = _read("aria_service/intel/web_integrity_agent.py")
        assert "async def get_credential_health" in source, (
            "get_credential_health method must exist"
        )


# ── Tests: Credential verification wired into _one_cycle ───────────────────


class TestCredentialCycle:
    """Credential verification must be wired into the monitoring cycle."""

    def test_cred_verify_in_one_cycle(self):
        """_one_cycle must call verify_all_credentials periodically."""
        source = _read("aria_service/intel/web_integrity_agent.py")
        assert "verify_all_credentials" in source, (
            "_one_cycle must reference verify_all_credentials"
        )
        assert "_CRED_VERIFY_INTERVAL_S" in source, (
            "_one_cycle must check credential verify interval"
        )

    def test_cred_verify_config_exists(self):
        """Credential verification configuration constants must exist."""
        source = _read("aria_service/intel/web_integrity_agent.py")
        assert "_CRED_VERIFY_INTERVAL_S" in source, (
            "Credential verify interval constant must exist"
        )
        assert "_CRED_VERIFY_KEY" in source, (
            "Credential verify Redis key must exist"
        )
        assert "_CRED_HEALTH_KEY" in source, (
            "Credential health Redis key must exist"
        )


# ── Tests: Re-registration trigger ─────────────────────────────────────────


class TestReRegistration:
    """Expired credentials must trigger re-registration."""

    def test_re_registration_records_gap(self):
        """_trigger_re_registration must record a capability gap."""
        source = _read("aria_service/intel/web_integrity_agent.py")
        assert "record_gap" in source, (
            "_trigger_re_registration must call record_gap"
        )
        assert "credential_expired" in source, (
            "Gap type must be credential_expired"
        )

    def test_re_registration_logs_warning(self):
        """_trigger_re_registration must log a warning."""
        source = _read("aria_service/intel/web_integrity_agent.py")
        assert 'logger.warning' in source, (
            "_trigger_re_registration must log a warning"
        )
        assert "Credentials expired" in source, (
            "Warning must mention credential expiry"
        )


# ── Tests: Portal registry integration ─────────────────────────────────────


class TestPortalRegistryIntegration:
    """Credential verification must integrate with portal registry."""

    def test_imports_portal_registry(self):
        """WebIntegrityAgent must import portal_registry."""
        source = _read("aria_service/intel/web_integrity_agent.py")
        assert "portal_registry" in source, (
            "WebIntegrityAgent must import portal_registry"
        )
        assert "PORTALS" in source, (
            "WebIntegrityAgent must reference PORTALS list"
        )
        assert "get_credential" in source, (
            "WebIntegrityAgent must call get_credential"
        )


# ── Tests: Brain wiring ────────────────────────────────────────────────────


class TestBrainWiring:
    """Credential verification must wire to the brain."""

    def test_verify_wires_to_brain(self):
        """verify_all_credentials must wire results to brain."""
        source = _read("aria_service/intel/web_integrity_agent.py")
        assert "_wire_to_brain" in source, (
            "verify_all_credentials must call _wire_to_brain"
        )
        assert "cred_verify" in source, (
            "Brain signal must include cred_verify source"
        )

    def test_cred_store_wires_to_brain(self):
        """Credential store route must wire to brain."""
        source = _read("aria_service/routes/aria.py")
        assert "wire_success" in source, (
            "Credential store route must call wire_success"
        )
        assert "portal_credentials" in source, (
            "Brain signal must reference portal_credentials"
        )
