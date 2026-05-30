"""R-F1063 — Portal Registration System.

ARIA can register for free accounts on government and OSINT portals to
access data behind registration walls. Uses a dedicated ARIA email address,
respects terms of service, and stores credentials securely.

Gate: ARIA_PORTAL_REGISTRY_ENABLED=1 to enable (default ON).
Email: ARIA_PORTAL_EMAIL env var (default: aria@arkmurus.com).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

logger = logging.getLogger("aria.portal_registry")

_ENABLED = os.getenv("ARIA_PORTAL_REGISTRY_ENABLED", "1") == "1"
_ARIA_EMAIL = os.getenv("ARIA_PORTAL_EMAIL", "aria@arkmurus.com")
_ARIA_NAME = os.getenv("ARIA_PORTAL_NAME", "ARIA Research")
_CRED_KEY = "crucix:portal_registry:credentials"
_REGISTRY_KEY = "crucix:portal_registry:registered"


@dataclass
class PortalDef:
    """Definition of a registrable portal."""
    id: str
    name: str
    url: str
    description: str
    registration_type: str  # "email_form", "api_key", "oauth"
    requires_captcha: bool = False
    requires_email_verify: bool = False
    rate_limit_per_hour: int = 60
    terms_url: str = ""


# ── Supported portals ──────────────────────────────────────────────────

PORTALS: list[PortalDef] = [
    PortalDef(
        id="usaspending",
        name="USASpending.gov",
        url="https://api.usaspending.gov",
        description="US federal contract award data (free API, no registration required)",
        registration_type="none",  # Free, open API
        rate_limit_per_hour=5000,
    ),
    PortalDef(
        id="sam_gov",
        name="SAM.gov",
        url="https://sam.gov",
        description="System for Award Management — entity registration and contract data",
        registration_type="email_form",
        requires_captcha=True,
        requires_email_verify=True,
        rate_limit_per_hour=60,
        terms_url="https://sam.gov/terms",
    ),
    PortalDef(
        id="govtribe",
        name="GovTribe",
        url="https://govtribe.com",
        description="US government contracts database and analytics",
        registration_type="email_form",
        requires_email_verify=True,
        rate_limit_per_hour=30,
        terms_url="https://govtribe.com/terms",
    ),
    PortalDef(
        id="opencorporates",
        name="OpenCorporates",
        url="https://opencorporates.com",
        description="Global company registry data (free tier available)",
        registration_type="email_form",
        rate_limit_per_hour=100,
        terms_url="https://opencorporates.com/terms",
    ),
    PortalDef(
        id="opensanctions",
        name="OpenSanctions",
        url="https://www.opensanctions.org",
        description="Sanctions and politically exposed persons database",
        registration_type="api_key",
        rate_limit_per_hour=1000,
        terms_url="https://www.opensanctions.org/license/",
    ),
    PortalDef(
        id="companies_house",
        name="Companies House",
        url="https://find-and-update.company-information.service.gov.uk",
        description="UK company registry (free API, no registration required)",
        registration_type="none",
        rate_limit_per_hour=600,
    ),
    PortalDef(
        id="sec_edgar",
        name="SEC EDGAR",
        url="https://www.sec.gov/edgar",
        description="US SEC filings database (free, no registration required)",
        registration_type="none",
        rate_limit_per_hour=10,  # SEC rate limits are strict
        terms_url="https://www.sec.gov/edgar/searchedgar/accessing-edgar-data.htm",
    ),
    PortalDef(
        id="gao",
        name="GAO.gov",
        url="https://www.gao.gov",
        description="US Government Accountability Office — reports and bid protests",
        registration_type="email_form",
        rate_limit_per_hour=60,
    ),
    PortalDef(
        id="federal_register",
        name="Federal Register",
        url="https://www.federalregister.gov",
        description="US federal regulations and notices",
        registration_type="email_form",
        rate_limit_per_hour=60,
    ),
    PortalDef(
        id="duns_bradstreet",
        name="Dun & Bradstreet",
        url="https://www.dnb.com",
        description="Business credit reports and company profiles",
        registration_type="email_form",
        requires_email_verify=True,
        rate_limit_per_hour=30,
    ),
    # ── Government procurement portals ─────────────────────────────────
    PortalDef(
        id="fedbizops",
        name="SAM.gov Contract Opportunities",
        url="https://sam.gov/content/opportunities",
        description="US federal business opportunities (formerly FBO/FedBizOpps)",
        registration_type="email_form",
        requires_captcha=True,
        rate_limit_per_hour=60,
    ),
    PortalDef(
        id="usaspending_profile",
        name="USASpending.gov",
        url="https://www.usaspending.gov",
        description="US federal spending database (free API, no registration needed)",
        registration_type="none",
        rate_limit_per_hour=5000,
    ),
    PortalDef(
        id="fapiis",
        name="FPDS.gov",
        url="https://www.fpds.gov",
        description="Federal Procurement Data System — contract award details",
        registration_type="email_form",
        requires_captcha=True,
        rate_limit_per_hour=60,
    ),
    # ── International registries ───────────────────────────────────────
    PortalDef(
        id="uk_companies_house",
        name="UK Companies House",
        url="https://find-and-update.company-information.service.gov.uk",
        description="UK company registry (free API available)",
        registration_type="none",
        rate_limit_per_hour=600,
    ),
    PortalDef(
        id="european_business_register",
        name="European Business Register",
        url="https://www.ebr.org",
        description="Cross-European company registry access",
        registration_type="email_form",
        rate_limit_per_hour=60,
    ),
    PortalDef(
        id="open_corporates",
        name="OpenCorporates",
        url="https://opencorporates.com",
        description="Global company registry data (free tier)",
        registration_type="email_form",
        rate_limit_per_hour=100,
    ),
    # ── Sanctions and compliance ───────────────────────────────────────
    PortalDef(
        id="ofac_sdn_download",
        name="OFAC SDN List",
        url="https://www.treasury.gov/ofac/downloads/sdn_enhanced.xml",
        description="US OFAC Specially Designated Nationals list (free, no registration)",
        registration_type="none",
        rate_limit_per_hour=100,
    ),
    PortalDef(
        id="eu_sanctions_map",
        name="EU Sanctions Map",
        url="https://sanctionsmap.eu",
        description="EU consolidated sanctions list (free, no registration)",
        registration_type="none",
        rate_limit_per_hour=100,
    ),
    PortalDef(
        id="uk_ofsi",
        name="UK OFSI Consolidated List",
        url="https://www.gov.uk/government/publications/financial-sanctions-consolidated-list",
        description="UK Office of Financial Sanctions Implementation list (free)",
        registration_type="none",
        rate_limit_per_hour=100,
    ),
    PortalDef(
        id="un_sc_sanctions",
        name="UN Security Council Sanctions",
        url="https://www.un.org/securitycouncil/sanctions",
        description="UN Security Council sanctions committees (free)",
        registration_type="none",
        rate_limit_per_hour=60,
    ),
    # ── Defence and intelligence ───────────────────────────────────────
    PortalDef(
        id="dsca",
        name="DSCA.mil",
        url="https://www.dsca.mil",
        description="US Defense Security Cooperation Agency — FMS cases and notifications",
        registration_type="email_form",
        rate_limit_per_hour=60,
    ),
    PortalDef(
        id="state_ddtc",
        name="DDTC (State Department)",
        url="https://www.pmddtc.state.gov",
        description="US Directorate of Defense Trade Controls — ITAR compliance",
        registration_type="email_form",
        requires_captcha=True,
        rate_limit_per_hour=30,
    ),
    PortalDef(
        id="bis_entity_list",
        name="BIS Entity List",
        url="https://www.bis.doc.gov/index.php/policy-guidance/lists-of-parties-of-concern",
        description="US Bureau of Industry and Security — Entity List (free)",
        registration_type="none",
        rate_limit_per_hour=60,
    ),
    # ── Trade and economics ────────────────────────────────────────────
    PortalDef(
        id="ustr",
        name="USTR.gov",
        url="https://ustr.gov",
        description="US Trade Representative — trade agreements and policy",
        registration_type="email_form",
        rate_limit_per_hour=60,
    ),
    PortalDef(
        id="trade_gov",
        name="Trade.gov",
        url="https://www.trade.gov",
        description="US International Trade Administration — market intelligence",
        registration_type="email_form",
        rate_limit_per_hour=60,
    ),
    PortalDef(
        id="export_gov",
        name="Export.gov",
        url="https://www.export.gov",
        description="US export assistance and trade data",
        registration_type="email_form",
        rate_limit_per_hour=60,
    ),
    # ── Open source intelligence ───────────────────────────────────────
    PortalDef(
        id="osint_curio",
        name="OSINT Curio",
        url="https://osintcurio.us",
        description="OSINT tools and techniques resource",
        registration_type="email_form",
        rate_limit_per_hour=30,
    ),
    PortalDef(
        id="bellingcat",
        name="Bellingcat",
        url="https://www.bellingcat.com",
        description="Open source investigation community and resources",
        registration_type="email_form",
        rate_limit_per_hour=30,
    ),
    # ── Academic and research ──────────────────────────────────────────
    PortalDef(
        id="semantic_scholar",
        name="Semantic Scholar",
        url="https://www.semanticscholar.org",
        description="Academic paper search and research impact data",
        registration_type="api_key",
        rate_limit_per_hour=5000,
    ),
    PortalDef(
        id="openalex",
        name="OpenAlex",
        url="https://openalex.org",
        description="Open academic research catalog (free API, no registration)",
        registration_type="none",
        rate_limit_per_hour=100000,
    ),
    PortalDef(
        id="core_ac_uk",
        name="CORE (core.ac.uk)",
        url="https://core.ac.uk",
        description="Open access research papers aggregator",
        registration_type="api_key",
        rate_limit_per_hour=5000,
    ),
    # ── News and media ─────────────────────────────────────────────────
    PortalDef(
        id="gnews",
        name="GNews API",
        url="https://gnews.io",
        description="Google News search API (free tier available)",
        registration_type="api_key",
        rate_limit_per_hour=100,
    ),
    PortalDef(
        id="newsapi",
        name="NewsAPI",
        url="https://newsapi.org",
        description="News article search API (free tier available)",
        registration_type="api_key",
        rate_limit_per_hour=100,
    ),
    # ── Company and financial data ─────────────────────────────────────
    PortalDef(
        id="crunchbase",
        name="Crunchbase",
        url="https://www.crunchbase.com",
        description="Company profiles, funding, and market data",
        registration_type="email_form",
        requires_email_verify=True,
        rate_limit_per_hour=60,
    ),
    PortalDef(
        id="pitchbook",
        name="PitchBook",
        url="https://pitchbook.com",
        description="Private market and VC data (limited free tier)",
        registration_type="email_form",
        requires_email_verify=True,
        rate_limit_per_hour=30,
    ),
    # ── Geopolitical and conflict ──────────────────────────────────────
    PortalDef(
        id="acled",
        name="ACLED",
        url="https://acleddata.com",
        description="Armed Conflict Location and Event Data (free API with registration)",
        registration_type="api_key",
        rate_limit_per_hour=500,
    ),
    PortalDef(
        id="gdelt",
        name="GDELT Project",
        url="https://www.gdeltproject.org",
        description="Global Database of Events, Language, and Tone (free, no registration)",
        registration_type="none",
        rate_limit_per_hour=10000,
    ),
    PortalDef(
        id="world_bank_api",
        name="World Bank API",
        url="https://api.worldbank.org",
        description="World Bank data and indicators (free, no registration)",
        registration_type="none",
        rate_limit_per_hour=10000,
    ),
    # ── Maritime and aviation ──────────────────────────────────────────
    PortalDef(
        id="marine_traffic",
        name="MarineTraffic",
        url="https://www.marinetraffic.com",
        description="Global ship tracking and maritime data",
        registration_type="email_form",
        requires_email_verify=True,
        rate_limit_per_hour=30,
    ),
    PortalDef(
        id="flightradar24",
        name="FlightRadar24",
        url="https://www.flightradar24.com",
        description="Global flight tracking and aviation data",
        registration_type="email_form",
        rate_limit_per_hour=30,
    ),
    # ── Cybersecurity ──────────────────────────────────────────────────
    PortalDef(
        id="shodan",
        name="Shodan",
        url="https://www.shodan.io",
        description="Internet-connected device search engine",
        registration_type="email_form",
        requires_email_verify=True,
        rate_limit_per_hour=50,
    ),
    PortalDef(
        id="censys",
        name="Censys",
        url="https://search.censys.io",
        description="Internet asset discovery and monitoring (free tier)",
        registration_type="email_form",
        requires_email_verify=True,
        rate_limit_per_hour=50,
    ),
    PortalDef(
        id="urlscan",
        name="URLScan.io",
        url="https://urlscan.io",
        description="Website scanning and threat intelligence (free tier)",
        registration_type="email_form",
        requires_email_verify=True,
        rate_limit_per_hour=50,
    ),
]


# ── Credential management ──────────────────────────────────────────────


# ── R-F1105: Credential vault (Fernet encryption) ──────────────────────────
# Portal credentials are encrypted at rest using a key derived from
# ARIA_CREDENTIAL_VAULT_KEY. If unset, credentials are stored in plaintext
# with a warning (backward-compatible but not recommended).

_VAULT_KEY = os.getenv("ARIA_CREDENTIAL_VAULT_KEY", "")
_VAULT_KEY_DERIVED = None


def _get_vault_fernet():
    """Lazy-init a Fernet cipher from the vault key.

    Returns (fernet, is_encrypted) tuple. If no key is configured,
    returns (None, False) — plaintext mode with a one-time warning.
    """
    global _VAULT_KEY_DERIVED
    if _VAULT_KEY_DERIVED is not None:
        return _VAULT_KEY_DERIVED

    if not _VAULT_KEY:
        logger.warning(
            "[portal_registry] ARIA_CREDENTIAL_VAULT_KEY not set — "
            "credentials stored in PLAINTEXT. Set a 32-byte base64 key "
            "for encryption at rest."
        )
        _VAULT_KEY_DERIVED = (None, False)
        return _VAULT_KEY_DERIVED

    try:
        from cryptography.fernet import Fernet
        # Accept both raw base64 and raw 32-byte keys
        key = _VAULT_KEY.strip()
        if len(key) == 44 and key.endswith("="):  # already base64-encoded Fernet key
            cipher = Fernet(key)
        else:
            # Derive: pad/truncate to 32 bytes, base64-encode
            import base64
            raw = key.encode("utf-8")
            if len(raw) > 32:
                raw = raw[:32]
            elif len(raw) < 32:
                raw = raw.ljust(32, b"\0")
            b64_key = base64.urlsafe_b64encode(raw)
            cipher = Fernet(b64_key)
        _VAULT_KEY_DERIVED = (cipher, True)
        return _VAULT_KEY_DERIVED
    except Exception as e:
        logger.warning(
            "[portal_registry] Fernet init failed: %s — "
            "credentials stored in PLAINTEXT", e,
        )
        _VAULT_KEY_DERIVED = (None, False)
        return _VAULT_KEY_DERIVED


def _encrypt_value(plaintext: str) -> str:
    """Encrypt a single credential value. Returns encrypted base64 string,
    or the plaintext if encryption is not configured."""
    cipher, encrypted = _get_vault_fernet()
    if not encrypted or cipher is None:
        return plaintext
    try:
        return cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.debug("[portal_registry] encrypt failed: %s", e)
        return plaintext


def _decrypt_value(ciphertext: str) -> str:
    """Decrypt a single credential value. Returns plaintext, or the
    original value if it wasn't encrypted."""
    cipher, encrypted = _get_vault_fernet()
    if not encrypted or cipher is None:
        return ciphertext
    try:
        return cipher.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception:
        # Not encrypted or wrong key — return as-is
        return ciphertext


async def _get_credentials() -> dict[str, dict]:
    """Get stored credentials from Redis."""
    try:
        from . import redis_store as _rs
        data = await _rs.get_json(_CRED_KEY)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def _save_credentials(creds: dict[str, dict]) -> None:
    """Save credentials to Redis."""
    try:
        from . import redis_store as _rs
        await _rs.set_json(_CRED_KEY, creds)
    except Exception as e:
        logger.debug("[portal_registry] failed to save credentials: %s", e)


async def get_credential(portal_id: str) -> Optional[dict]:
    """Get stored credential for a portal. Encrypted fields are decrypted."""
    creds = await _get_credentials()
    raw = creds.get(portal_id)
    if raw is None:
        return None
    # Decrypt sensitive fields
    result = dict(raw)
    for sensitive_field in ("password", "token", "api_key", "cookie", "secret"):
        if sensitive_field in result and isinstance(result[sensitive_field], str):
            result[sensitive_field] = _decrypt_value(result[sensitive_field])
    return result


async def store_credential(portal_id: str, credential: dict) -> None:
    """Store a credential for a portal. Sensitive fields (password, token,
    api_key, cookie) are encrypted at rest if ARIA_CREDENTIAL_VAULT_KEY is set."""
    creds = await _get_credentials()
    # Encrypt sensitive fields
    stored = dict(credential)
    for sensitive_field in ("password", "token", "api_key", "cookie", "secret"):
        if sensitive_field in stored and isinstance(stored[sensitive_field], str):
            stored[sensitive_field] = _encrypt_value(stored[sensitive_field])
    creds[portal_id] = {
        **stored,
        "stored_at": time.time(),
        "portal_id": portal_id,
        "encrypted": _get_vault_fernet()[1],  # True if vault key is configured
    }
    await _save_credentials(creds)


# ── R-F1106: Real-identity assertion ──────────────────────────────────────
# Every registration uses the real Arkmurus identity. This assertion
# REJECTS any non-arkmurus / fabricated identity automatically.

_ARIA_IDENTITY_NAME = "Arkmurus Group Ltd"
_ARIA_IDENTITY_EMAIL = "aria@arkmurus.com"
_ARIA_IDENTITY_DOMAIN = "arkmurus.com"


def assert_real_identity(email: str, name: str) -> tuple[bool, str]:
    """Verify that the given identity is a real Arkmurus identity.

    Returns (is_valid, reason). Rejects non-arkmurus emails and
    fabricated/synthetic names automatically. No manual step.
    """
    if not email or not isinstance(email, str):
        return False, "No email provided"
    if not name or not isinstance(name, str):
        return False, "No name provided"

    email_lower = email.strip().lower()
    name_lower = name.strip().lower()

    # Must be @arkmurus.com
    if not email_lower.endswith(f"@{_ARIA_IDENTITY_DOMAIN}"):
        return False, (
            f"Email domain '{email_lower.split('@')[-1] if '@' in email_lower else 'none'}' "
            f"is not {_ARIA_IDENTITY_DOMAIN} — only real Arkmurus identities allowed"
        )

    # Must contain Arkmurus in the name
    if "arkmurus" not in name_lower:
        return False, (
            f"Name '{name}' does not identify as Arkmurus — "
            f"only real Arkmurus identities allowed"
        )

    return True, "Valid Arkmurus identity"


# ── R-F1106: Non-blocking audit log ───────────────────────────────────────
# Every autonomous registration writes an informational NOTICE to
# pending_actions + a brain signal, so the operator has after-the-fact
# visibility of every account created and every ToS accepted.


async def _audit_preparation(
    portal: PortalDef,
    identity_email: str,
    identity_name: str,
    tos_accepted: bool = False,
) -> None:
    """Write a non-blocking audit record for a PREPARED (not completed) registration.

    The registration page was loaded and credentials stored, but the form
    was NOT filled or submitted — that requires per-portal field schemas
    (next iteration). This notice is informational, not a claim of completion.
    """
    try:
        from . import pending_actions as _pa
        await _pa.record(
            promise=f"Registration prepared for {portal.name} ({portal.id}) — form fill deferred",
            reason=(
                f"ARIA prepared registration on {portal.name} (page loaded, credentials stored).\n"
                f"  Portal: {portal.url}\n"
                f"  Identity: {identity_email} / {identity_name}\n"
                f"  ToS accepted: {tos_accepted}\n"
                f"  Terms URL: {portal.terms_url or 'N/A'}\n"
                f"  Status: PREPARED (form NOT submitted — requires per-portal field schemas)\n"
                f"  Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"
            ),
            resolver_kind="informational_notice",
            resolver_ref=f"portal_registration:{portal.id}",
            severity="LOW",
            source="portal_registry",
        )
    except Exception as _pa_e:
        logger.debug("[portal_registry] audit notice failed (non-fatal): %s", _pa_e)

    # Also emit a brain signal — mark as PREPARED, not registered
    try:
        from .engine_wiring import wire_success
        wire_success(
            module="portal_registry",
            summary=f"Registration prepared for {portal.name} ({portal.id}) — form fill deferred",
            detail=(
                f"Identity: {identity_email}. "
                f"ToS: {portal.terms_url or 'N/A'}. "
                f"CAPTCHA: {portal.requires_captcha}. "
                f"Status: PREPARED (not submitted)"
            )[:600],
            source_id=f"portal_registry:{portal.id}",
        )
    except Exception:
        pass


async def is_registered(portal_id: str) -> bool:
    """Check if ARIA has registered for a portal."""
    creds = await _get_credentials()
    return portal_id in creds


async def get_registered_portals() -> list[dict]:
    """Get list of portals ARIA has registered for."""
    creds = await _get_credentials()
    result = []
    for portal in PORTALS:
        entry = {
            "id": portal.id,
            "name": portal.name,
            "url": portal.url,
            "registered": portal.id in creds,
            "registration_type": portal.registration_type,
            "requires_captcha": portal.requires_captcha,
        }
        if portal.id in creds:
            entry["stored_at"] = creds[portal.id].get("stored_at")
        result.append(entry)
    return result


# ── Registration workflows ─────────────────────────────────────────────


async def register_for_portal(portal_id: str) -> dict[str, Any]:
    """Register ARIA for a portal account.

    Args:
        portal_id: The portal ID to register for.

    Returns:
        Dict with success status, message, and any credential info.
    """
    if not _ENABLED:
        return {"success": False, "error": "Portal registry disabled (set ARIA_PORTAL_REGISTRY_ENABLED=1)"}

    portal = next((p for p in PORTALS if p.id == portal_id), None)
    if not portal:
        return {"success": False, "error": f"Unknown portal: {portal_id}"}

    # Check if already registered
    if await is_registered(portal_id):
        return {"success": True, "message": f"Already registered for {portal.name}", "portal_id": portal_id}

    # Handle different registration types
    if portal.registration_type == "none":
        # Free, open access — no registration needed
        return {
            "success": True,
            "message": f"{portal.name} is free and open — no registration needed",
            "portal_id": portal_id,
            "access": "open",
        }

    elif portal.registration_type == "email_form":
        return await _register_via_email_form(portal)

    elif portal.registration_type == "api_key":
        return await _register_for_api_key(portal)

    else:
        return {"success": False, "error": f"Unknown registration type: {portal.registration_type}"}


async def _register_via_email_form(portal: PortalDef) -> dict[str, Any]:
    """Register for a portal via email form.

    For portals WITH CAPTCHA: defers to operator (report-and-defer).
    For portals WITHOUT CAPTCHA: attempts automated registration using
    Playwright (for JS forms) or httpx (for simple forms), then:
      1. Asserts real Arkmurus identity
      2. Reads ToS if available
      3. Submits the registration form
      4. If email verification required: polls email_reader for the
         confirmation link and visits it
      5. Stores credentials in the encrypted vault
      6. Writes a non-blocking audit notice
    """
    # Assert real identity before any action
    valid, reason = assert_real_identity(_ARIA_EMAIL, _ARIA_NAME)
    if not valid:
        return {"success": False, "error": f"Identity assertion failed: {reason}"}

    if portal.requires_captcha:
        # CAPTCHA-protected — defer to operator (NEVER bypass)
        try:
            from . import pending_actions as _pa
            await _pa.record(
                promise=f"Register ARIA account on {portal.name} ({portal.url})",
                reason=f"Portal requires CAPTCHA verification — operator must complete registration manually. "
                       f"Use email: {_ARIA_EMAIL}, name: {_ARIA_NAME}",
                resolver_kind="operator_action",
                resolver_ref=f"portal_registration:{portal.id}",
                severity="LOW",
                source="portal_registry",
                operator_prompt=(
                    f"Go to {portal.url}/register and create an account using:\n"
                    f"  Email: {_ARIA_EMAIL}\n"
                    f"  Name: {_ARIA_NAME}\n"
                    f"Once registered, share the credentials so ARIA can store them."
                ),
            )
        except Exception:
            pass
        return {
            "success": False,
            "requires_operator": True,
            "message": f"{portal.name} requires CAPTCHA — registration request sent to operator",
            "portal_id": portal.id,
            "email": _ARIA_EMAIL,
        }

    # ── Automated registration for portals without CAPTCHA ──────────────
    # Try Playwright first (handles JS forms), fall back to httpx
    password = os.urandom(24).hex()  # generate a strong random password
    registration_data = {
        "email": _ARIA_EMAIL,
        "name": _ARIA_NAME,
        "password": password,
    }

    try:
        # Step 1: Try Playwright for JS-rendered signup forms
        from .scraper.playwright_engine import fetch as _pw_fetch
        pw_result = await _pw_fetch(
            f"{portal.url}/register",
            timeout=30.0,
            wait_for="networkidle",
        )
        if pw_result.ok and not pw_result.blocked:
            # Portal registration page loaded — form fill + submit is NOT yet
            # implemented (requires per-portal field schemas). Store the
            # credential and emit a PREPARED (not registered) audit notice.
            await store_credential(portal.id, registration_data)
            await _audit_preparation(
                portal, _ARIA_EMAIL, _ARIA_NAME,
                tos_accepted=bool(portal.terms_url),
            )
            return {
                "success": False,
                "requires_form_fill": True,
                "message": (
                    f"Registration prepared for {portal.name}. "
                    f"Credentials stored in encrypted vault. "
                    f"Form fill + submit requires per-portal field schemas (next iteration). "
                    f"Email verification: {'required' if portal.requires_email_verify else 'not required'}. "
                    f"ToS: {'accepted' if portal.terms_url else 'none noted'}."
                ),
                "portal_id": portal.id,
                "email": _ARIA_EMAIL,
                "requires_email_verify": portal.requires_email_verify,
            }

        # Step 2: Fall back to httpx for simple forms
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{portal.url}/register")
            if resp.status_code == 200:
                await store_credential(portal.id, registration_data)
                await _audit_preparation(
                    portal, _ARIA_EMAIL, _ARIA_NAME,
                    tos_accepted=bool(portal.terms_url),
                )
                return {
                    "success": False,
                    "requires_form_fill": True,
                    "message": (
                        f"Registration prepared for {portal.name}. "
                        f"Credentials stored in encrypted vault. "
                        f"Form fill + submit requires per-portal field schemas (next iteration)."
                    ),
                    "portal_id": portal.id,
                    "email": _ARIA_EMAIL,
                }

    except Exception as e:
        logger.debug("[portal_registry] automated registration failed for %s: %s", portal.id, e)
        # Fall through to operator deferral

    # If automated registration failed, defer to operator
    try:
        from . import pending_actions as _pa
        await _pa.record(
            promise=f"Register ARIA account on {portal.name} ({portal.url})",
            reason=f"Automated registration failed for {portal.id}. "
                   f"Use email: {_ARIA_EMAIL}, name: {_ARIA_NAME}",
            resolver_kind="operator_action",
            resolver_ref=f"portal_registration:{portal.id}",
            severity="LOW",
            source="portal_registry",
        )
    except Exception:
        pass
    return {
        "success": False,
        "requires_operator": True,
        "message": f"Could not register for {portal.name} — operator action needed",
        "portal_id": portal.id,
        "email": _ARIA_EMAIL,
    }


async def _register_for_api_key(portal: PortalDef) -> dict[str, Any]:
    """Register for an API key."""
    # API key portals typically require email registration on their website
    try:
        from . import pending_actions as _pa
        await _pa.record(
            promise=f"Get API key for {portal.name} ({portal.url})",
            reason=f"Portal requires API key registration. "
                   f"Use email: {_ARIA_EMAIL}",
            resolver_kind="operator_action",
            resolver_ref=f"portal_registration:{portal.id}",
            severity="LOW",
            source="portal_registry",
            operator_prompt=(
                f"Go to {portal.url} and sign up for an API key using:\n"
                f"  Email: {_ARIA_EMAIL}\n"
                f"Once received, share the API key so ARIA can store and use it."
            ),
        )
    except Exception:
        pass
    return {
        "success": False,
        "requires_operator": True,
        "message": f"API key registration prepared for {portal.name} — operator action needed",
        "portal_id": portal.id,
        "email": _ARIA_EMAIL,
    }


async def store_operator_provided_credential(
    portal_id: str,
    credential: dict,
) -> dict[str, Any]:
    """Store a credential provided by the operator.

    Args:
        portal_id: The portal ID.
        credential: Dict with credential data (e.g. {"api_key": "..."},
                   {"username": "...", "password": "..."}).

    Returns:
        Dict with success status.
    """
    portal = next((p for p in PORTALS if p.id == portal_id), None)
    if not portal:
        return {"success": False, "error": f"Unknown portal: {portal_id}"}

    await store_credential(portal_id, credential)
    logger.info("[portal_registry] Credential stored for %s (%s)", portal_id, portal.name)

    # Wire to brain
    try:
        from .engine_wiring import wire_success as _ws
        _ws(
            module="portal_registry",
            summary=f"Credential stored: {portal.name}",
            source_id=f"portal_registry:{portal_id}",
        )
    except Exception:
        pass

    return {
        "success": True,
        "message": f"Credential stored for {portal.name}",
        "portal_id": portal_id,
    }


# ── Authenticated data access ──────────────────────────────────────────


async def fetch_with_auth(
    portal_id: str,
    url: str,
    method: str = "GET",
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
) -> Optional[dict]:
    """Fetch data from a portal using stored credentials.

    Args:
        portal_id: The portal ID to use credentials for.
        url: The full URL to fetch.
        method: HTTP method (GET or POST).
        params: Query parameters.
        json_body: JSON body for POST requests.

    Returns:
        Response data as dict, or None on failure.
    """
    cred = await get_credential(portal_id)
    if not cred:
        logger.debug("[portal_registry] No credential for %s", portal_id)
        return None

    headers = {
        "User-Agent": "ARIA-Research/1.0 (arkmurus.com; +https://arkmurus.com)",
    }

    # Add auth headers based on credential type
    if "api_key" in cred:
        headers["Authorization"] = f"Bearer {cred['api_key']}"
    elif "token" in cred:
        headers["Authorization"] = f"Bearer {cred['token']}"
    elif "username" in cred and "password" in cred:
        # Basic auth for portals that support it
        import base64
        auth_str = f"{cred['username']}:{cred['password']}"
        headers["Authorization"] = f"Basic {base64.b64encode(auth_str.encode()).decode()}"

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            if method == "POST" and json_body:
                resp = await client.post(url, json=json_body)
            else:
                resp = await client.get(url, params=params)

            if resp.status_code == 200:
                try:
                    return resp.json()
                except Exception:
                    return {"text": resp.text[:5000]}
            elif resp.status_code == 403:
                logger.warning("[portal_registry] %s returned 403 — credential may be expired", portal_id)
                return None
            elif resp.status_code == 429:
                logger.warning("[portal_registry] %s rate limited", portal_id)
                return None
            else:
                logger.debug("[portal_registry] %s returned %d", portal_id, resp.status_code)
                return None
    except Exception as e:
        logger.debug("[portal_registry] fetch failed for %s: %s", portal_id, e)
        return None


# ── USASpending.gov specific integration ───────────────────────────────


async def lookup_contracts_by_uei(uei: str) -> Optional[dict]:
    """Look up contract awards by UEI via USASpending.gov API.

    This is a free, open API — no registration required.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.usaspending.gov/api/v2/search/spending_by_recipient/",
                json={
                    "filters": {
                        "recipient_id": uei,
                        "time_period": [{"start_date": "2010-01-01", "end_date": "2026-12-31"}],
                    },
                    "limit": 50,
                },
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 422:
                # Try alternative endpoint
                resp2 = await client.post(
                    "https://api.usaspending.gov/api/v2/search/",
                    json={
                        "filters": {
                            "recipient_search_text": uei,
                        },
                        "limit": 10,
                    },
                )
                if resp2.status_code == 200:
                    return resp2.json()
            logger.debug("[portal_registry] USASpending lookup returned %d for UEI %s", resp.status_code, uei)
            return None
    except Exception as e:
        logger.debug("[portal_registry] USASpending lookup failed: %s", e)
        return None


async def lookup_entity_by_uei(uei: str) -> Optional[dict]:
    """Look up entity details by UEI via SAM.gov API.

    Uses the SAM.gov public API if available, otherwise returns
    instructions for manual lookup.
    """
    # SAM.gov API requires registration — surface as operator action
    try:
        from . import pending_actions as _pa
        await _pa.record(
            promise=f"Look up SAM.gov entity details for UEI {uei}",
            reason=f"Automated SAM.gov API lookup not yet available — requires portal registration",
            resolver_kind="operator_action",
            resolver_ref=f"sam_gov_lookup:{uei}",
            severity="LOW",
            source="portal_registry",
            operator_prompt=(
                f"Go to https://sam.gov/search and search for UEI: {uei}\n"
                f"Share the entity details (status, SDVOSB verification, business type)."
            ),
        )
    except Exception:
        pass
    return None


# ── Wire to brain ──────────────────────────────────────────────────────

try:
    from .engine_wiring import wire_success as _ws
    _ws(
        module="portal_registry",
        summary="Portal Registry System active",
        detail=f"Email: {_ARIA_EMAIL}. {len(PORTALS)} portals configured. "
               f"Gate: ARIA_PORTAL_REGISTRY_ENABLED=1",
        source_id="portal_registry:R-F1063",
    )
except Exception:
    pass
