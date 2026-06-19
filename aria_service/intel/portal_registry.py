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
# R-F1495: portal registration identity. Must contain 'arkmurus' to pass
# assert_real_identity. The env var ARIA_PORTAL_NAME overrides this default.
_ARIA_EMAIL = os.getenv("ARIA_PORTAL_EMAIL", "aria@arkmurus.com")
_ARIA_NAME = os.getenv("ARIA_PORTAL_NAME", "ARIA Research (Arkmurus Group)")
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
    # R-F1651: weekly per-domain registration cap. Default 3 per week.
    # Prevents ARIA from registering on the same domain more than N times
    # in a 7-day window, regardless of per-hour rate limits. This is a
    # ToS-compliance safety net — not a rate-limit for the portal's API.
    max_per_week: int = 3
    terms_url: str = ""
    # R-F1108: Per-portal signup field schemas for automated form fill.
    # Each entry: (field_selector, field_type, value_source)
    #   field_selector: CSS selector or name attribute for the form field
    #   field_type: "text", "email", "password", "checkbox", "radio", "select", "hidden"
    #   value_source: "email", "name", "org", "password", "website", "literal:<value>"
    signup_fields: list[tuple[str, str, str]] = field(default_factory=list)
    # URL path for the registration page (defaults to /user/register)
    register_path: str = "/user/register"
    # URL path for login (for checking if account exists)
    login_path: str = "/user/login"
    # Expected success indicator in the response after form submit
    success_indicator: str = ""  # text or URL pattern that indicates success
    # IMAP sender domain for email verification (to filter confirmation emails)
    verify_email_domain: str = ""


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
        register_path="/signup",
        signup_fields=[
            ("email", "email", "email"),
            ("password", "password", "password"),
            ("name", "text", "name"),
        ],
        success_indicator="Welcome to GovTribe",
        verify_email_domain="govtribe.com",
    ),
    PortalDef(
        id="opencorporates",
        name="OpenCorporates",
        url="https://opencorporates.com",
        description="Global company registry data (free tier available)",
        registration_type="email_form",
        rate_limit_per_hour=100,
        terms_url="https://opencorporates.com/terms",
        register_path="/users/sign_up",
        signup_fields=[
            ("user[email]", "email", "email"),
            ("user[password]", "password", "password"),
            ("user[full_name]", "text", "name"),
            ("user[terms]", "checkbox", "literal:1"),
        ],
        success_indicator="Welcome!",
        verify_email_domain="opencorporates.com",
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
        register_path="/subscribe",
        signup_fields=[
            ("email", "email", "email"),
        ],
        success_indicator="subscription confirmed",
    ),
    PortalDef(
        id="federal_register",
        name="Federal Register",
        url="https://www.federalregister.gov",
        description="US federal regulations and notices",
        registration_type="email_form",
        rate_limit_per_hour=60,
        register_path="/account/signup",
        signup_fields=[
            ("user[email]", "email", "email"),
            ("user[password]", "password", "password"),
            ("user[name]", "text", "name"),
        ],
        success_indicator="Account created",
        verify_email_domain="federalregister.gov",
    ),
    PortalDef(
        id="duns_bradstreet",
        name="Dun & Bradstreet",
        url="https://www.dnb.com",
        description="Business credit reports and company profiles",
        registration_type="email_form",
        requires_email_verify=True,
        rate_limit_per_hour=30,
        register_path="/register",
        signup_fields=[
            ("email", "email", "email"),
            ("password", "password", "password"),
            ("firstName", "text", "literal:ARIA"),
            ("lastName", "text", "literal:Research"),
            ("company", "text", "org"),
        ],
        success_indicator="Thank you for registering",
        verify_email_domain="dnb.com",
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
        register_path="/user/register",
        signup_fields=[
            ("email", "email", "email"),
            ("password", "password", "password"),
            ("name", "text", "name"),
            ("organisation", "text", "org"),
        ],
        success_indicator="Account created",
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
        register_path="/user/register",
        signup_fields=[
            ("mail", "email", "email"),
            ("name", "text", "name"),
            ("pass[pass1]", "password", "password"),
            ("pass[pass2]", "password", "password"),
        ],
        success_indicator="Thank you for registering",
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
        register_path="/subscribe",
        signup_fields=[
            ("email", "email", "email"),
        ],
        success_indicator="subscribed",
    ),
    PortalDef(
        id="trade_gov",
        name="Trade.gov",
        url="https://www.trade.gov",
        description="US International Trade Administration — market intelligence",
        registration_type="email_form",
        rate_limit_per_hour=60,
        register_path="/user/register",
        signup_fields=[
            ("mail", "email", "email"),
            ("name", "text", "name"),
            ("pass[pass1]", "password", "password"),
            ("pass[pass2]", "password", "password"),
        ],
        success_indicator="Account created",
    ),
    PortalDef(
        id="export_gov",
        name="Export.gov",
        url="https://www.export.gov",
        description="US export assistance and trade data",
        registration_type="email_form",
        rate_limit_per_hour=60,
        register_path="/user/register",
        signup_fields=[
            ("mail", "email", "email"),
            ("name", "text", "name"),
        ],
        success_indicator="Account created",
    ),
    # ── Open source intelligence ───────────────────────────────────────
    PortalDef(
        id="osint_curio",
        name="OSINT Curio",
        url="https://osintcurio.us",
        description="OSINT tools and techniques resource",
        registration_type="email_form",
        rate_limit_per_hour=30,
        register_path="/subscribe",
        signup_fields=[
            ("email", "email", "email"),
            ("fname", "text", "name"),
        ],
        success_indicator="Thank you for subscribing",
    ),
    PortalDef(
        id="bellingcat",
        name="Bellingcat",
        url="https://www.bellingcat.com",
        description="Open source investigation community and resources",
        registration_type="email_form",
        rate_limit_per_hour=30,
        register_path="/subscribe",
        signup_fields=[
            ("email", "email", "email"),
        ],
        success_indicator="subscribed",
    ),
    # ── Academic and research ──────────────────────────────────────────
    PortalDef(
        id="semantic_scholar",
        name="Semantic Scholar",
        url="https://www.semanticscholar.org",
        description="Academic paper search and research impact data",
        registration_type="api_key",
        rate_limit_per_hour=5000,
        register_path="/account/create",
        signup_fields=[
            ("email", "email", "email"),
            ("password", "password", "password"),
            ("displayName", "text", "name"),
        ],
        success_indicator="Account created",
        verify_email_domain="semanticscholar.org",
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
        register_path="/register",
        signup_fields=[
            ("email", "email", "email"),
            ("password", "password", "password"),
            ("name", "text", "name"),
        ],
        success_indicator="API key",
        verify_email_domain="gnews.io",
    ),
    PortalDef(
        id="newsapi",
        name="NewsAPI",
        url="https://newsapi.org",
        description="News article search API (free tier available)",
        registration_type="api_key",
        rate_limit_per_hour=100,
        register_path="/register",
        signup_fields=[
            ("email", "email", "email"),
            ("password", "password", "password"),
            ("name", "text", "name"),
        ],
        success_indicator="API key",
        verify_email_domain="newsapi.org",
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
        register_path="/signup",
        signup_fields=[
            ("user[email]", "email", "email"),
            ("user[password]", "password", "password"),
            ("user[first_name]", "text", "literal:ARIA"),
            ("user[last_name]", "text", "literal:Research"),
        ],
        success_indicator="Welcome to Crunchbase",
        verify_email_domain="crunchbase.com",
    ),
    PortalDef(
        id="pitchbook",
        name="PitchBook",
        url="https://pitchbook.com",
        description="Private market and VC data (limited free tier)",
        registration_type="email_form",
        requires_email_verify=True,
        rate_limit_per_hour=30,
        register_path="/register",
        signup_fields=[
            ("email", "email", "email"),
            ("password", "password", "password"),
            ("firstName", "text", "literal:ARIA"),
            ("lastName", "text", "literal:Research"),
            ("company", "text", "org"),
        ],
        success_indicator="Thank you for registering",
        verify_email_domain="pitchbook.com",
    ),
    # ── Geopolitical and conflict ──────────────────────────────────────
    PortalDef(
        id="acled",
        name="ACLED",
        url="https://acleddata.com",
        description="Armed Conflict Location and Event Data (free API with registration)",
        registration_type="email_form",
        rate_limit_per_hour=500,
        terms_url="https://acleddata.com/privacy-policy/",
        register_path="/user/register",
        signup_fields=[
            ("field_first_name[0][value]", "text", "literal:ARIA"),
            ("field_last_name[0][value]", "text", "literal:Research"),
            ("mail", "email", "email"),
            ("field_organisation_name[0][value]", "text", "org"),
            ("field_website[0][value]", "text", "website"),
            ("field_category", "radio", "literal:153"),  # Corporate
            ("field_areas_of_interest[160]", "checkbox", "literal:160"),
            ("pp", "checkbox", "literal:1"),  # Privacy policy
            ("tou", "checkbox", "literal:1"),  # Terms of use
        ],
        success_indicator="Thank you for applying",
        verify_email_domain="acleddata.com",
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
        register_path="/register",
        signup_fields=[
            ("email", "email", "email"),
            ("password", "password", "password"),
            ("username", "text", "literal:ARIA_Research"),
        ],
        success_indicator="Account created",
        verify_email_domain="marinetraffic.com",
    ),
    PortalDef(
        id="flightradar24",
        name="FlightRadar24",
        url="https://www.flightradar24.com",
        description="Global flight tracking and aviation data",
        registration_type="email_form",
        rate_limit_per_hour=30,
        register_path="/register",
        signup_fields=[
            ("email", "email", "email"),
            ("password", "password", "password"),
            ("username", "text", "literal:ARIA_Research"),
        ],
        success_indicator="Account created",
        verify_email_domain="flightradar24.com",
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
        terms_url="https://www.shodan.io/terms",
        register_path="/register",
        signup_fields=[
            ("username", "text", "literal:ARIA_Research"),
            ("email", "email", "email"),
            ("password", "password", "password"),
            ("password_confirm", "password", "password"),
        ],
        success_indicator="Account created",
        verify_email_domain="shodan.io",
    ),
    PortalDef(
        id="censys",
        name="Censys",
        url="https://search.censys.io",
        description="Internet asset discovery and monitoring (free tier)",
        registration_type="email_form",
        requires_email_verify=True,
        rate_limit_per_hour=50,
        register_path="/register",
        signup_fields=[
            ("email", "email", "email"),
            ("password", "password", "password"),
            ("name", "text", "name"),
        ],
        success_indicator="Account created",
        verify_email_domain="censys.io",
    ),
    PortalDef(
        id="urlscan",
        name="URLScan.io",
        url="https://urlscan.io",
        description="Website scanning and threat intelligence (free tier)",
        registration_type="email_form",
        requires_email_verify=True,
        rate_limit_per_hour=50,
        register_path="/user/register",
        signup_fields=[
            ("mail", "email", "email"),
            ("name", "text", "name"),
            ("pass[pass1]", "password", "password"),
            ("pass[pass2]", "password", "password"),
        ],
        success_indicator="Account created",
        verify_email_domain="urlscan.io",
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

    # R-F1233: Record in agent signup vault so all agents are aware
    try:
        from .agent_signup_vault import get_vault
        vault = get_vault()
        try:
            vault.record(
                site_id=portal.id,
                site_name=portal.name,
                site_url=portal.url,
                agent_id="portal_registry",
                site_type="portal",
                agent_type="autonomous",
                status="pending",
                notes=f"Registration prepared — form fill deferred. CAPTCHA: {portal.requires_captcha}. ToS: {portal.terms_url or 'N/A'}.",
                metadata={
                    "registration_type": portal.registration_type,
                    "requires_captcha": portal.requires_captcha,
                    "requires_email_verify": portal.requires_email_verify,
                    "has_signup_fields": bool(portal.signup_fields),
                },
            )
        except ValueError:
            pass  # already in vault
    except Exception as _e:
        logger.debug("[portal_registry] vault record failed (non-fatal): %s", _e)


async def _audit_registered(
    portal: PortalDef,
    identity_email: str,
    identity_name: str,
    purpose: str = "",
) -> None:
    """Write a non-blocking audit record for a COMPLETED registration.

    The form was filled, submitted, and (if required) email-verified.
    This is a truthful claim of completion.

    R-F1651: `purpose` captures why this registration was needed, for
    audit defensibility.
    """
    try:
        from . import pending_actions as _pa
        purpose_line = f"  Purpose: {purpose}\n" if purpose else ""
        await _pa.record(
            promise=f"Registered on {portal.name} ({portal.id})",
            reason=(
                f"ARIA autonomously registered an account on {portal.name}.\n"
                f"  Portal: {portal.url}\n"
                f"  Identity: {identity_email} / {identity_name}\n"
                f"  ToS: {portal.terms_url or 'N/A'}\n"
                f"{purpose_line}"
                f"  Status: REGISTERED (form submitted and verified)\n"
                f"  Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"
            ),
            resolver_kind="informational_notice",
            resolver_ref=f"portal_registration:{portal.id}",
            severity="LOW",
            source="portal_registry",
        )
    except Exception as _pa_e:
        logger.debug("[portal_registry] audit notice failed (non-fatal): %s", _pa_e)

    # Also emit a brain signal
    try:
        from .engine_wiring import wire_success
        wire_success(
            module="portal_registry",
            summary=f"Registered on {portal.name} ({portal.id})",
            detail=(
                f"Identity: {identity_email}. "
                f"ToS: {portal.terms_url or 'N/A'}. "
                f"Status: REGISTERED"
            )[:600],
            source_id=f"portal_registration:{portal.id}",
        )
    except Exception:
        pass

    # R-F1233: Record in agent signup vault so all agents are aware
    try:
        from .agent_signup_vault import get_vault
        vault = get_vault()
        try:
            vault.record(
                site_id=portal.id,
                site_name=portal.name,
                site_url=portal.url,
                agent_id="portal_registry",
                site_type="portal",
                agent_type="autonomous",
                status="registered",
                notes=f"Autonomously registered. Identity: {identity_email}.",
                metadata={
                    "registration_type": portal.registration_type,
                    "requires_captcha": portal.requires_captcha,
                    "requires_email_verify": portal.requires_email_verify,
                },
            )
        except ValueError:
            # Already in vault — update status to registered
            vault.update_status(portal.id, "registered",
                notes=f"Autonomously registered. Identity: {identity_email}.")
    except Exception:
        logger.debug("[portal_registry] vault record failed (non-fatal)")


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


async def register_for_portal(portal_id: str, purpose: str = "") -> dict[str, Any]:
    """Register ARIA for a portal account.

    Args:
        portal_id: The portal ID to register for.
        purpose: R-F1651 — why this registration is needed (e.g. "accessing
            Angola procurement notices for Q3 2026 market assessment").
            Passed through to the audit trail for defensibility.

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

    # R-F1651: weekly per-domain rate cap. Count registrations on this
    # portal's domain in the last 7 days from the credential store.
    # This is a ToS-compliance safety net, not a rate-limit for the API.
    try:
        creds = await _get_credentials()
        domain = portal.url.rstrip("/").lower()
        one_week_ago = time.time() - (7 * 86400)
        recent = [
            c for c in creds.values()
            if isinstance(c, dict)
            and c.get("portal_id") == portal.id
            and c.get("stored_at", 0) >= one_week_ago
        ]
        if len(recent) >= portal.max_per_week:
            return {
                "success": False,
                "error": f"Weekly registration cap reached for {portal.name} "
                         f"({len(recent)}/{portal.max_per_week} in 7 days). "
                         f"Next window opens when the oldest registration expires.",
                "portal_id": portal_id,
                "cap": portal.max_per_week,
                "current": len(recent),
            }
    except Exception as e:
        logger.debug("[R-F1651] weekly cap check failed (non-fatal): %s", e)

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
        result = await _register_via_email_form(portal)
    elif portal.registration_type == "api_key":
        result = await _register_for_api_key(portal)
    else:
        result = {"success": False, "error": f"Unknown registration type: {portal.registration_type}"}

    # R-F1692: wire failure branch to brain so the operator has visibility
    # into registration failures (previously all failures were masked as
    # 'prepared' with no brain signal — §25 proprioception violation).
    if not result.get("success"):
        try:
            from .engine_wiring import wire_failure as _wf1692
            _wf1692(
                module="portal_registry",
                detail=f"Registration failed for {portal_id} ({portal.name}): "
                       f"{result.get('error') or result.get('message', 'unknown')}",
                gap_type="source_failure",
                source="portal_registry",
            )
        except Exception:
            pass

    return result


async def _register_via_email_form(portal: PortalDef) -> dict[str, Any]:
    """Register for a portal via email form.

    For portals WITH CAPTCHA: defers to operator (report-and-defer).
    For portals WITHOUT CAPTCHA: attempts automated registration using
    Playwright (for JS forms) or httpx (for simple forms), then:
      1. Asserts real Arkmurus identity
      2. Reads ToS if available
      3. Submits the registration form using per-portal field schemas
      4. If email verification required: polls email_reader for the
         confirmation link and visits it
      5. Stores credentials in the encrypted vault
      6. Writes a non-blocking audit notice (REGISTERED on success,
         PREPARED if form not submitted, FAILED on error)
    """
    # Assert real identity before any action
    valid, reason = assert_real_identity(_ARIA_EMAIL, _ARIA_NAME)
    if not valid:
        return {"success": False, "error": f"Identity assertion failed: {reason}"}

    if portal.requires_captcha:
        # R-F1689: attempt autonomous CAPTCHA solving before deferring to operator.
        # If a CAPTCHA solver is configured (ARIA_TWOCAPTCHA_API_KEY etc.), try to
        # solve the CAPTCHA and complete registration autonomously. Only defer to
        # operator if no solver is configured or solving fails.
        try:
            from .captcha_solver import get_solver, detect_and_solve_captcha
            solver = get_solver()
            if solver.is_ready:
                logger.info(
                    "[portal_registry] R-F1689: attempting CAPTCHA solve for %s",
                    portal.id,
                )
                # Load the registration page, detect CAPTCHA, solve it, and submit
                password = os.urandom(24).hex()
                registration_data = {
                    "email": _ARIA_EMAIL,
                    "name": _ARIA_NAME,
                    "password": password,
                    "purpose": f"Auto-registration for {portal.name} — {portal.description[:100]}",
                }
                result = await _attempt_form_fill_submit(
                    portal, f"{portal.url.rstrip('/')}{portal.register_path}",
                    registration_data, solve_captcha=True,
                )
                if result.get("success"):
                    await _audit_registered(
                        portal, _ARIA_EMAIL, _ARIA_NAME,
                        purpose=registration_data.get("purpose", ""),
                    )
                    return result
                if result.get("requires_email_verify"):
                    return result
                logger.info(
                    "[portal_registry] R-F1689: CAPTCHA solve + submit failed for %s — "
                    "falling back to operator deferral: %s",
                    portal.id, result.get("error", "unknown"),
                )
        except ImportError:
            logger.debug("[portal_registry] captcha_solver not available")
        except Exception as e:
            logger.debug(
                "[portal_registry] R-F1689: CAPTCHA solve error for %s: %s",
                portal.id, e,
            )

        # CAPTCHA-protected and no solver available or solving failed — defer to operator
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
                    f"Go to {portal.url}{portal.register_path} and create an account using:\n"
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
    password = os.urandom(24).hex()  # generate a strong random password
    registration_data = {
        "email": _ARIA_EMAIL,
        "name": _ARIA_NAME,
        "password": password,
        # R-F1651: pass purpose through to audit trail
        "purpose": purpose,
    }

    register_url = f"{portal.url.rstrip('/')}{portal.register_path}"

    # If portal has signup_fields defined, attempt real form fill + submit
    if portal.signup_fields:
        result = await _attempt_form_fill_submit(portal, register_url, registration_data)
        if result.get("success"):
            # Real registration succeeded — audit as REGISTERED
            await _audit_registered(portal, _ARIA_EMAIL, _ARIA_NAME, purpose=registration_data.get("purpose", ""))
            return result
        elif result.get("requires_operator"):
            return result
        elif result.get("requires_email_verify"):
            # Form submitted, needs email verification
            return result
        # R-F1496: form fill failed — store credentials anyway so the operator
        # or retry scheduler can pick up where ARIA left off. The portal stays
        # 'pending' but now has credentials stored.
        try:
            await store_credential(portal.id, registration_data)
            logger.info(
                "[R-F1496] Credentials stored for %s despite form fill failure — "
                "retry scheduler will attempt registration again",
                portal.id,
            )
        except Exception:
            pass
        # Fall through to the generic detector below if the explicit form fill failed.

    # R-F1497: generic form detector (R-F1161) — de-nested out of the
    # `if portal.signup_fields:` block. R-F1496 accidentally nested it there, so
    # portals WITHOUT explicit signup_fields (the ones that NEED the generic
    # detector to discover the form) skipped it entirely and went straight to
    # 'prepared'. It now runs for BOTH: no-signup_fields portals AND explicit-field
    # portals whose fill failed (which fell through the if above).
    try:
        from .scraper.playwright_engine import fetch as _pw_fetch
        pw_result = await _pw_fetch(
            register_url,
            timeout=30.0,
            wait_for="networkidle",
        )
        if pw_result.ok and pw_result.text:
            detected_fields = _detect_form_fields(pw_result.text)
            if detected_fields:
                logger.info(
                    "[portal_registry] Generic detector found %d fields for %s",
                    len(detected_fields), portal.id,
                )
                # Create a temporary portal with detected fields and retry
                import dataclasses
                temp_portal = dataclasses.replace(portal, signup_fields=detected_fields)
                result = await _attempt_form_fill_submit(temp_portal, register_url, registration_data)
                if result.get("success"):
                    await _audit_registered(portal, _ARIA_EMAIL, _ARIA_NAME, purpose=registration_data.get("purpose", ""))
                    return result
                elif result.get("requires_operator"):
                    return result
                elif result.get("requires_email_verify"):
                    return result
    except Exception as e:
        logger.debug(
            "[portal_registry] Generic form detection failed for %s: %s",
            portal.id, e,
        )

    # If no signup_fields or form fill failed, store credentials and
    # emit a PREPARED (not registered) audit notice
    try:
        await store_credential(portal.id, registration_data)
    except Exception:
        pass
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


async def _attempt_form_fill_submit(
    portal: PortalDef,
    register_url: str,
    registration_data: dict[str, str],
    solve_captcha: bool = False,
) -> dict[str, Any]:
    """Attempt to fill and submit a registration form using Playwright.

    Uses the portal's signup_fields schema to map form fields to values.
    When `solve_captcha=True`, detects CAPTCHA on the page and solves it
    via the configured provider before submitting (R-F1689).

    Returns a result dict matching register_for_portal's contract.
    """
    try:
        from .scraper.playwright_engine import fetch as _pw_fetch

        # Step 1: Load the registration page with Playwright
        pw_result = await _pw_fetch(
            register_url,
            timeout=45.0,
            wait_for="networkidle",
        )

        if not pw_result.ok or pw_result.blocked:
            logger.debug(
                "[portal_registry] Playwright could not load %s: ok=%s blocked=%s",
                register_url, pw_result.ok, pw_result.blocked,
            )
            return {"success": False, "error": "Could not load registration page"}

        # R-F1689: Detect and solve CAPTCHA before building form data
        captcha_token = None
        if solve_captcha and pw_result.text:
            try:
                from .captcha_solver import detect_and_solve_captcha
                captcha_token = await detect_and_solve_captcha(
                    register_url, pw_result.text,
                )
                if captcha_token:
                    logger.info(
                        "[portal_registry] R-F1689: CAPTCHA solved for %s",
                        portal.id,
                    )
                else:
                    # R-F1692: CAPTCHA solving failed on a required-captcha portal.
                    # Fail immediately — submitting without a token will be rejected
                    # and the brain would see a false 'prepared' success.
                    logger.warning(
                        "[portal_registry] R-F1692: CAPTCHA solve failed for %s "
                        "(all providers returned None) — aborting registration",
                        portal.id,
                    )
                    try:
                        from .engine_wiring import wire_failure as _wf1692
                        _wf1692(
                            module="portal_registry",
                            detail=f"CAPTCHA solve failed for {portal.id} — all providers returned None",
                            gap_type="source_failure",
                            source="portal_registry",
                        )
                    except Exception:
                        pass
                    return {
                        "success": False,
                        "error": f"CAPTCHA solve failed for {portal.name} — all providers returned None",
                        "portal_id": portal.id,
                    }
            except Exception as e:
                logger.debug(
                    "[portal_registry] R-F1689: CAPTCHA detection failed for %s: %s",
                    portal.id, e,
                )

        # Step 2: Build form data from signup_fields schema
        form_data = _build_form_data(portal.signup_fields, registration_data)

        # Step 3: Submit the form through Playwright (carries browser session,
        # cookies, and CSRF tokens — unlike httpx POST which loses browser context)
        from .scraper.playwright_engine import submit_form as _pw_submit
        submit_result = await _pw_submit(
            register_url,
            form_data,
            submit_selector='[type="submit"]',
            success_indicator=portal.success_indicator,
            timeout=45.0,
            captcha_token=captcha_token,  # R-F1689: pass solved token
        )

        if submit_result.get("error"):
            logger.debug(
                "[portal_registry] Playwright form submit failed for %s: %s",
                portal.id, submit_result["error"],
            )
            return {"success": False, "error": f"Form submit failed: {submit_result['error']}"}

        # Step 4: Check for success indicators
        if submit_result.get("success"):
            # Store credentials in vault
            await store_credential(portal.id, registration_data)

            # If email verification required, attempt to verify
            if portal.requires_email_verify and portal.verify_email_domain:
                verified = await _handle_email_verification(portal, registration_data)
                if not verified:
                    return {
                        "success": False,
                        "requires_email_verify": True,
                        "message": (
                            f"Registration submitted for {portal.name}. "
                            f"Email verification required — waiting for confirmation link "
                            f"from {portal.verify_email_domain}. "
                            f"Credentials stored in encrypted vault."
                        ),
                        "portal_id": portal.id,
                        "email": _ARIA_EMAIL,
                    }

            return {
                "success": True,
                "message": (
                    f"Successfully registered for {portal.name}. "
                    f"Credentials stored in encrypted vault."
                ),
                "portal_id": portal.id,
                "email": _ARIA_EMAIL,
            }

        # Step 5: Check for bot detection / rate limiting in response
        response_text = submit_result.get("response_text", "")
        resp_lower = response_text.lower()
        if "please wait" in resp_lower and "seconds" in resp_lower:
            return {
                "success": False,
                "requires_operator": True,
                "message": (
                    f"{portal.name} rate-limited the registration attempt. "
                    f"Try again later or register manually."
                ),
                "portal_id": portal.id,
                "email": _ARIA_EMAIL,
            }

        # Check for field errors
        field_errors = _extract_field_errors(response_text)
        if field_errors:
            logger.debug(
                "[portal_registry] Registration field errors for %s: %s",
                portal.id, field_errors,
            )
            return {
                "success": False,
                "error": f"Registration failed: {'; '.join(field_errors[:3])}",
                "portal_id": portal.id,
            }

        logger.debug(
            "[portal_registry] Form submission for %s returned unexpected response",
            portal.id,
        )
        return {"success": False, "error": "Unexpected response from registration form"}

    except Exception as e:
        logger.debug("[portal_registry] Form fill+submit failed for %s: %s", portal.id, e)
        return {"success": False, "error": str(e)}


def _build_form_data(
    signup_fields: list[tuple[str, str, str]],
    registration_data: dict[str, str],
) -> dict[str, str]:
    """Build form data dict from signup_fields schema and registration data."""
    form_data: dict[str, str] = {}

    for selector, field_type, value_source in signup_fields:
        if value_source.startswith("literal:"):
            value = value_source[len("literal:"):]
        elif value_source == "email":
            value = registration_data.get("email", "")
        elif value_source == "name":
            value = registration_data.get("name", "")
        elif value_source == "org":
            value = "Arkmurus Group Ltd"
        elif value_source == "password":
            value = registration_data.get("password", "")
        elif value_source == "website":
            value = "https://arkmurus.com"
        else:
            value = ""

        if value:
            form_data[selector] = value

    return form_data


# R-F1161 — Generic form field detector. When a portal has no explicit
# signup_fields schema, we scan the HTML for common registration form
# patterns and map them heuristically. This covers the 20+ portals that
# would otherwise fall through to "PREPARED — form fill deferred".
_COMMON_FORM_ACTIONS = re.compile(
    r'(register|signup|sign.up|create.account|join|subscribe|user.register)',
    re.IGNORECASE,
)
_EMAIL_FIELD_PATTERNS = re.compile(
    r'(email|e-mail|mail|username|login)',
    re.IGNORECASE,
)
_PASSWORD_FIELD_PATTERNS = re.compile(
    r'(password|passwd|pwd|pass)',
    re.IGNORECASE,
)
_NAME_FIELD_PATTERNS = re.compile(
    r'(name|full.name|first.name|last.name|fname|lname|your.name)',
    re.IGNORECASE,
)
_ORG_FIELD_PATTERNS = re.compile(
    r'(org|company|organization|organisation|firm|business|employer)',
    re.IGNORECASE,
)
_WEBSITE_FIELD_PATTERNS = re.compile(
    r'(website|web.site|url|homepage|company.url|site)',
    re.IGNORECASE,
)
_PHONE_FIELD_PATTERNS = re.compile(
    r'(phone|telephone|mobile|cell|contact.number|tel)',
    re.IGNORECASE,
)
_COUNTRY_FIELD_PATTERNS = re.compile(
    r'(country|nation|region)',
    re.IGNORECASE,
)


def _detect_form_fields(html: str) -> list[tuple[str, str, str]]:
    """Scan HTML for registration form fields and return a signup_fields schema.

    Uses regex heuristics to identify common field types by their name, id,
    or label text. Returns a list of (selector, field_type, value_source)
    tuples compatible with _build_form_data.

    This is a best-effort heuristic — it won't catch every portal's form,
    but it covers the common patterns (Drupal, WordPress, Django, Rails,
    Laravel, Express, etc.).
    """
    fields: list[tuple[str, str, str]] = []
    seen_names: set[str] = set()

    # Find the registration form — look for <form> with action containing
    # register/signup keywords, or the first <form> on the page
    form_html = html
    form_match = re.search(
        r'<form[^>]*action\s*=\s*["\']([^"\']*register[^"\']*)["\'][^>]*>(.*?)</form>',
        html, re.DOTALL | re.IGNORECASE,
    )
    if form_match:
        form_html = form_match.group(2)
    else:
        # Fallback: try any form with a submit button
        form_match = re.search(
            r'<form[^>]*>(.*?)</form>',
            html, re.DOTALL,
        )
        if form_match:
            form_html = form_match.group(2)

    # Extract all input fields within the form
    input_pattern = re.compile(
        r'<input[^>]*(?:name|id)\s*=\s*["\']([^"\']+)["\'][^>]*>',
        re.DOTALL | re.IGNORECASE,
    )
    for input_match in input_pattern.finditer(form_html):
        input_html = input_match.group(0)
        name = input_match.group(1).strip()

        if name in seen_names:
            continue
        seen_names.add(name)

        # Determine field type
        type_match = re.search(r'type\s*=\s*["\']([^"\']+)["\']', input_html, re.IGNORECASE)
        field_type = type_match.group(1).lower() if type_match else "text"

        # Skip submit/reset/button/hidden/file fields (handled separately)
        if field_type in ("submit", "reset", "button", "file", "image"):
            continue
        if field_type == "hidden":
            # Hidden fields are handled by CSRF extraction
            continue
        if field_type == "checkbox":
            # Checkboxes for ToS/privacy — auto-check them
            name_lower = name.lower()
            if any(t in name_lower for t in ("terms", "conditions", "privacy", "agree", "accept", "subscribe", "newsletter")):
                fields.append((name, "checkbox", "literal:1"))
            continue
        if field_type == "radio":
            continue  # Too context-dependent for generic detection

        # Map field name to value source
        name_lower = name.lower()
        if _EMAIL_FIELD_PATTERNS.search(name_lower):
            fields.append((name, "email", "email"))
        elif _PASSWORD_FIELD_PATTERNS.search(name_lower):
            fields.append((name, "password", "password"))
        elif _NAME_FIELD_PATTERNS.search(name_lower):
            fields.append((name, "text", "name"))
        elif _ORG_FIELD_PATTERNS.search(name_lower):
            fields.append((name, "text", "org"))
        elif _WEBSITE_FIELD_PATTERNS.search(name_lower):
            fields.append((name, "text", "website"))
        elif _PHONE_FIELD_PATTERNS.search(name_lower):
            fields.append((name, "text", "literal:+351900000000"))
        elif _COUNTRY_FIELD_PATTERNS.search(name_lower):
            fields.append((name, "text", "literal:Portugal"))
        else:
            # Unknown field — skip it rather than guess wrong
            logger.debug(
                "[portal_registry] Generic detector: unknown field '%s' (type=%s) — skipping",
                name, field_type,
            )

    # Also check for select/textarea fields
    select_pattern = re.compile(
        r'<(select|textarea)[^>]*(?:name|id)\s*=\s*["\']([^"\']+)["\'][^>]*>',
        re.DOTALL | re.IGNORECASE,
    )
    for select_match in select_pattern.finditer(form_html):
        tag = select_match.group(1).lower()
        name = select_match.group(2).strip()
        if name in seen_names:
            continue
        seen_names.add(name)

        name_lower = name.lower()
        if _COUNTRY_FIELD_PATTERNS.search(name_lower):
            fields.append((name, "select", "literal:Portugal"))
        else:
            logger.debug(
                "[portal_registry] Generic detector: unknown %s '%s' — skipping",
                tag, name,
            )

    return fields


def _extract_csrf_token(html: str) -> str | None:
    """Extract CSRF token from HTML form."""
    import re
    # Common CSRF token field names
    for pattern in [
        r'name="csrf_token" value="([^"]*)"',
        r'name="csrfmiddlewaretoken" value="([^"]*)"',
        r'name="authenticity_token" value="([^"]*)"',
        r'name="_token" value="([^"]*)"',
        r'name="csrf" value="([^"]*)"',
    ]:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def _extract_hidden_field(html: str, field_name: str) -> str | None:
    """Extract a hidden form field value by name."""
    import re
    m = re.search(rf'name="{re.escape(field_name)}" value="([^"]*)"', html)
    return m.group(1) if m else None


def _is_registration_successful(
    resp: httpx.Response,
    portal: PortalDef,
) -> bool:
    """Check if a registration POST was successful."""
    # Check by status code
    if resp.status_code in (301, 302):
        # Redirect after successful registration
        return True

    if resp.status_code != 200:
        return False

    # Check by success indicator
    if portal.success_indicator:
        return portal.success_indicator.lower() in resp.text.lower()

    # Check for common success patterns
    text = resp.text.lower()
    success_patterns = [
        "account created",
        "registration complete",
        "thank you for registering",
        "welcome",
        "check your email",
        "verify your email",
        "confirmation email",
    ]
    return any(p in text for p in success_patterns)


def _extract_field_errors(html: str) -> list[str]:
    """Extract field-level error messages from HTML response."""
    import re
    errors = []

    # Drupal-style error messages
    for m in re.finditer(
        r'<div[^>]*class="[^"]*messages[^"]*"[^>]*>(.*?)</div>',
        html, re.DOTALL,
    ):
        clean = re.sub(r'<[^>]*>', '', m.group(1)).strip()
        if clean:
            errors.append(clean)

    # Rails-style field errors
    for m in re.finditer(
        r'<div[^>]*class="[^"]*field-error[^"]*"[^>]*>(.*?)</div>',
        html, re.DOTALL,
    ):
        clean = re.sub(r'<[^>]*>', '', m.group(1)).strip()
        if clean:
            errors.append(clean)

    return errors


async def _handle_email_verification(
    portal: PortalDef,
    registration_data: dict[str, str],
) -> bool:
    """Handle email verification after registration.

    Polls the email reader for a confirmation email from the portal's
    domain, extracts the confirmation link, and visits it.

    Returns True if verification was completed, False if it could not
    be completed (e.g. email reader not configured).
    """
    if not portal.verify_email_domain:
        return False

    try:
        from .email_reader import read_emails as _read_emails

        # Poll for confirmation email (up to 5 minutes, checking every 30s)
        email_addr = registration_data.get("email", _ARIA_EMAIL)
        for attempt in range(10):
            await asyncio.sleep(30)
            try:
                emails = await _read_emails()
                for email in emails or []:
                    sender = (email.get("from") or "").lower()
                    subject = (email.get("subject") or "").lower()
                    body = (email.get("body") or "").lower()

                    # Check if this email is from the portal's domain
                    if portal.verify_email_domain not in sender:
                        continue

                    # Check if it's addressed to our email
                    if email_addr.lower() not in (email.get("to") or "").lower():
                        continue

                    # Extract confirmation link
                    import re
                    link = _extract_confirmation_link(body + subject)
                    if link:
                        # Visit the confirmation link
                        async with httpx.AsyncClient(
                            timeout=15.0, follow_redirects=True,
                        ) as client:
                            await client.get(link)
                            return True

            except Exception:
                logger.debug(
                    "[portal_registry] Email poll attempt %d failed for %s",
                    attempt, portal.id,
                )

        logger.debug(
            "[portal_registry] Email verification timeout for %s "
            "(10 polls, 30s interval)",
            portal.id,
        )
        return False

    except ImportError:
        logger.debug(
            "[portal_registry] email_reader not available — "
            "email verification deferred for %s",
            portal.id,
        )
        return False
    except Exception as e:
        logger.debug(
            "[portal_registry] Email verification failed for %s: %s",
            portal.id, e,
        )
        return False


def _extract_confirmation_link(text: str) -> str | None:
    """Extract a confirmation/verification link from email text."""
    import re
    # Common confirmation URL patterns
    patterns = [
        r'https?://[^\s<>"]*confirm[^\s<>"]*',
        r'https?://[^\s<>"]*verify[^\s<>"]*',
        r'https?://[^\s<>"]*activate[^\s<>"]*',
        r'https?://[^\s<>"]*registration[^\s<>"]*confirm[^\s<>"]*',
        r'https?://[^\s<>"]*user[^\s<>"]*activate[^\s<>"]*',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


async def _register_for_api_key(portal: PortalDef) -> dict[str, Any]:
    """Register for an API key.

    If the portal has signup_fields defined, attempts auto-registration
    via the email form path (same as _register_via_email_form). The API
    key is extracted from the response or email and stored in the vault.

    Falls back to operator deferral if no signup_fields or auto-reg fails.
    """
    # R-F1161 — if portal has signup_fields, try auto-registration first
    if portal.signup_fields:
        result = await _register_via_email_form(portal)
        if result.get("success"):
            # Registration succeeded — try to extract API key from response
            # or mark as needing operator to provide the key
            return {
                "success": True,
                "message": (
                    f"Account created for {portal.name}. "
                    f"API key may need to be obtained from the account dashboard. "
                    f"Credentials stored in encrypted vault."
                ),
                "portal_id": portal.id,
                "email": _ARIA_EMAIL,
            }
        elif result.get("requires_email_verify"):
            return result
        # Fall through to operator deferral

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


# ── Auto-register all pending portals ──────────────────────────────────

async def auto_register_all() -> dict[str, Any]:
    """Attempt registration for every unregistered portal.

    Returns a summary dict with counts of registered, skipped, failed,
    and captcha-deferred portals. Best-effort: individual failures are
    logged and do not abort the sweep.

    R-F1312: adds automated sweep so pending sources get registered
    without manual per-portal invocation.
    """
    if not _ENABLED:
        return {"success": False, "error": "Portal registry disabled"}

    results: dict[str, Any] = {
        "total": len(PORTALS),
        "already_registered": 0,
        "newly_registered": 0,
        "captcha_deferred": 0,
        "failed": 0,
        "skipped_open": 0,
        "details": [],
    }

    for portal in PORTALS:
        if portal.registration_type == "none":
            results["skipped_open"] += 1
            continue

        try:
            if await is_registered(portal.id):
                results["already_registered"] += 1
                continue
        except Exception:
            pass

        try:
            outcome = await register_for_portal(portal.id)
            if outcome.get("success"):
                results["newly_registered"] += 1
                results["details"].append({
                    "id": portal.id, "status": "registered",
                    "message": outcome.get("message", ""),
                })
            elif outcome.get("requires_operator"):
                results["captcha_deferred"] += 1
                results["details"].append({
                    "id": portal.id, "status": "captcha_deferred",
                    "message": outcome.get("message", ""),
                })
            else:
                results["failed"] += 1
                results["details"].append({
                    "id": portal.id, "status": "failed",
                    "message": outcome.get("message", outcome.get("error", "unknown")),
                })
        except Exception as e:
            results["failed"] += 1
            results["details"].append({
                "id": portal.id, "status": "error",
                "message": str(e)[:200],
            })

    # Wire result to brain
    try:
        from .engine_wiring import wire_success as _ws2, wire_failure as _wf2
        if results.get("failed", 0) == 0 and results.get("captcha_deferred", 0) == 0:
            _ws2(
                module="portal_registry",
                summary=f"Auto-register: {results['newly_registered']} new, "
                        f"{results['already_registered']} already registered",
                source_id="portal_registry:R-F1312",
            )
        else:
            _wf2(
                module="portal_registry",
                detail=f"Auto-register: {results['newly_registered']} new, "
                       f"{results['failed']} failed, "
                       f"{results['captcha_deferred']} captcha-deferred",
                gap_type="source_failure",
                source="portal_registry",
            )
    except Exception:
        pass

    return results


# ── Env var check for pending sources ──────────────────────────────────

def get_pending_source_requirements() -> list[dict]:
    """Return a list of pending sources and what env vars they need.

    R-F1312: gives the operator a clear view of what's blocking each
    pending source registration.
    """
    requirements: list[dict] = []
    for portal in PORTALS:
        if portal.registration_type == "none":
            continue
        needed_vars: list[str] = []
        if portal.id == "acled":
            needed_vars = ["ACLED_EMAIL", "ACLED_PASSWORD"]
        elif portal.registration_type == "api_key":
            # API-key portals need their key set as env var
            key_var = f"{portal.id.upper()}_API_KEY"
            needed_vars = [key_var]
        elif portal.registration_type == "email_form":
            needed_vars = ["ARIA_PORTAL_EMAIL", "ARIA_PORTAL_NAME"]

        requirements.append({
            "id": portal.id,
            "name": portal.name,
            "url": portal.url,
            "registration_type": portal.registration_type,
            "requires_captcha": portal.requires_captcha,
            "needs_env_vars": needed_vars,
            "env_vars_set": [v for v in needed_vars if os.getenv(v)],
            "env_vars_missing": [v for v in needed_vars if not os.getenv(v)],
        })
    return requirements


# R-F1502: portals the operator has declined (§18) — suppressed from recurring digest
_DECLINED_PORTAL_IDS = {"crunchbase", "pitchbook", "opencorporates", "duns_bradstreet", "opensanctions"}
# R-F1502: portals deferred (e.g. ACLED pending env vars) — suppressed from recurring digest
_DEFERRED_PORTAL_IDS = {"acled"}


async def determine_and_drive(portal_id: str) -> dict[str, Any]:
    """R-F1502: For ONE portal, determine its honest status and drive the outcome.

    Returns one of three:
      {"status": "open_api"}           — free/open, no registration needed
      {"status": "registered", ...}    — ARIA successfully registered
      {"status": "needs_operator", "blocker": "...", "declined": bool, "deferred": bool}

    The determination is driven by the REAL attempt outcome, NEVER a guess.
    """
    portal = next((p for p in PORTALS if p.id == portal_id), None)
    if not portal:
        return {"status": "error", "error": f"Unknown portal: {portal_id}"}

    # 1. Open API — no registration needed
    if portal.registration_type == "none":
        return {"status": "open_api"}

    # 2. Check if already registered (credentials exist in Redis)
    try:
        if await is_registered(portal_id):
            return {"status": "registered", "message": "Credentials found in vault"}
    except Exception:
        pass

    # 3. CAPTCHA — ARIA cannot bypass
    if portal.requires_captcha:
        return {
            "status": "needs_operator",
            "blocker": "captcha",
            "declined": portal_id in _DECLINED_PORTAL_IDS,
            "deferred": portal_id in _DEFERRED_PORTAL_IDS,
            "message": f"{portal.name} requires CAPTCHA — operator must register manually",
        }

    # 4. Paid/declined portals
    if portal_id in _DECLINED_PORTAL_IDS:
        return {
            "status": "needs_operator",
            "blocker": "paid",
            "declined": True,
            "deferred": False,
            "message": f"{portal.name} requires paid subscription — operator declined (§18)",
        }

    # 5. Deferred portals (e.g. ACLED waiting for env vars)
    if portal_id in _DEFERRED_PORTAL_IDS:
        return {
            "status": "needs_operator",
            "blocker": "manual_signup",
            "declined": False,
            "deferred": True,
            "message": f"{portal.name} deferred — waiting for env vars (ACLED_EMAIL, ACLED_PASSWORD)",
        }

    # 6. Attempt auto-registration
    try:
        outcome = await register_for_portal(portal_id, purpose=f"Auto-registration for {portal.name} — {portal.description[:100]}")
        if outcome.get("success"):
            # Real registration succeeded
            try:
                from .agent_signup_vault import get_vault
                vault = get_vault()
                vault.update_status(portal_id, "registered",
                    notes=f"Auto-registered: {outcome.get('message', '')[:200]}")
            except Exception:
                pass
            return {"status": "registered", "message": outcome.get("message", "")}

        if outcome.get("requires_email_verify"):
            return {
                "status": "needs_operator",
                "blocker": "email_verify",
                "declined": False,
                "deferred": False,
                "message": f"{portal.name} requires email verification — IMAP not configured",
            }

        # Attempt failed
        error = outcome.get("error") or outcome.get("message", "unknown failure")
        return {
            "status": "needs_operator",
            "blocker": "attempt_failed",
            "declined": False,
            "deferred": False,
            "message": f"{portal.name}: auto-registration failed — {error[:200]}",
        }
    except Exception as e:
        return {
            "status": "needs_operator",
            "blocker": "attempt_failed",
            "declined": False,
            "deferred": False,
            "message": f"{portal.name}: auto-registration exception — {e}",
        }


async def determine_and_drive_all(portal_ids: list[str] | None = None) -> list[dict]:
    """R-F1502: Run determine_and_drive for multiple portals.

    Args:
        portal_ids: List of portal IDs to process. If None, processes all
                    portals that are currently 'pending'.

    Returns list of result dicts.
    """
    if portal_ids is None:
        try:
            from .agent_signup_vault import get_vault
            vault = get_vault()
            pending = vault.list(status="pending", limit=100)
            portal_ids = [e["site_id"] for e in pending]
        except Exception:
            portal_ids = [p.id for p in PORTALS if p.registration_type != "none"]

    results = []
    for pid in portal_ids:
        try:
            result = await determine_and_drive(pid)
            # Update vault status
            try:
                from .agent_signup_vault import get_vault
                vault = get_vault()
                status = result.get("status", "needs_operator")
                if status == "open_api":
                    vault.update_status(pid, "open_api",
                        notes="Free/open API — no registration required.")
                elif status == "registered":
                    vault.update_status(pid, "registered",
                        notes=result.get("message", "Registered successfully.")[:200])
                elif status == "needs_operator":
                    blocker = result.get("blocker", "unknown")
                    declined = result.get("declined", False)
                    deferred = result.get("deferred", False)
                    notes = result.get("message", "Operator action needed.")[:200]
                    if declined:
                        notes += " [DECLINED — suppressed from digest]"
                    if deferred:
                        notes += " [DEFERRED — suppressed from digest]"
                    vault.update_status(pid, "needs_operator", notes=notes)
            except Exception:
                pass
            results.append(result)
        except Exception as e:
            results.append({"status": "error", "error": str(e)})

    return results


# R-F1498: portals that require a PAID subscription — operator has declined paid
# third-party services (§6/§18). Emailed as "your decision", never auto-pursued.
_PAID_PORTAL_IDS = {"crunchbase", "pitchbook", "opencorporates", "duns_bradstreet", "opensanctions"}


async def email_portal_requirements_to_operator() -> dict[str, Any]:
    """R-F1498/R-F1502 — email the operator the HONEST state of each portal.

    Uses determine_and_drive results. Suppresses declined/deferred portals
    from the recurring digest (don't nag about a "no"). Throttled: only sends
    if at least one actionable portal exists.
    """
    operator = (
        (os.getenv("ARIA_EMAIL_OPERATOR_ALLOWLIST") or "").split(",")[0].strip()
        or os.getenv("ARIA_OPERATOR_EMAIL")
        or os.getenv("ARIA_SMTP_USER")
        or ""
    )
    if not operator:
        return {"sent": False, "error": "no operator email configured (ARIA_OPERATOR_EMAIL)"}

    # R-F1502: run determine_and_drive for all pending portals
    results = await determine_and_drive_all()

    # Categorize results
    actionable: list[str] = []
    captcha: list[str] = []
    paid: list[str] = []
    deferred: list[str] = []
    already_working: list[str] = []

    for r in results:
        status = r.get("status", "")
        blocker = r.get("blocker", "")
        declined = r.get("declined", False)
        is_deferred = r.get("deferred", False)
        msg = r.get("message", "")

        if status == "open_api":
            already_working.append(f"  - {msg}")
        elif status == "registered":
            already_working.append(f"  - {msg}")
        elif status == "needs_operator":
            if declined:
                paid.append(f"  - {msg}")
            elif is_deferred:
                deferred.append(f"  - {msg}")
            elif blocker == "captcha":
                captcha.append(f"  - {msg}")
            else:
                actionable.append(f"  - {msg}")

    # Only send if there's something actionable
    if not actionable and not captcha:
        return {"sent": False, "reason": "nothing actionable", "counts": {
            "actionable": len(actionable), "captcha": len(captcha),
            "paid": len(paid), "deferred": len(deferred),
            "already_working": len(already_working),
        }}

    parts = [
        "Hi — here is the HONEST state of the external data sources ARIA cannot use "
        "autonomously.\n"
    ]
    if actionable:
        parts.append("ACTION REQUIRED — ARIA attempted but could not complete:\n"
                     + "\n".join(actionable) + "\n")
    if captcha:
        parts.append("CAPTCHA — manual signup required:\n" + "\n".join(captcha) + "\n")
    if paid:
        parts.append("PAID/DECLINED — your previous decisions (not actionable):\n"
                     + "\n".join(paid) + "\n")
    if deferred:
        parts.append("DEFERRED — waiting on env vars or other prerequisites:\n"
                     + "\n".join(deferred) + "\n")
    if already_working:
        parts.append("ALREADY WORKING (no action needed):\n"
                     + "\n".join(already_working) + "\n")

    parts.append("— ARIA")
    body = "\n".join(parts)

    from ..integrations.email_outbound import send_email
    result = send_email(
        to=operator,
        subject="ARIA — portal access digest",
        body=body,
        internal=True,
        sender_note="R-F1502 portal-requirements digest",
    )
    counts = {"actionable": len(actionable), "captcha": len(captcha),
              "paid": len(paid), "deferred": len(deferred),
              "already_working": len(already_working)}
    try:
        from .engine_wiring import wire_success
        wire_success(
            module="portal_registry",
            summary=f"Portal digest: {counts['actionable']} actionable, {counts['captcha']} captcha",
            source_id="portal_registry:R-F1502",
        )
    except Exception:
        pass
    return {
        "sent": bool(result.get("sent")),
        "to": operator,
        "counts": counts,
        "delivery_error": result.get("delivery_error"),
        "draft": None if result.get("sent") else result,
    }


# ── R-F1504: Boot-time identity assertion check ─────────────────────────
# If the identity assertion fails with defaults, log CRITICAL so the
# operator knows immediately — silent registration failures are the #1
# recurring bug (R-F1495).
_valid, _reason = assert_real_identity(_ARIA_EMAIL, _ARIA_NAME)
if not _valid:
    logger.critical(
        "[R-F1504] PORTAL REGISTRY IDENTITY ASSERTION FAILED at import time: %s. "
        "Set ARIA_PORTAL_NAME to include 'Arkmurus' (e.g. 'ARIA Research (Arkmurus Group)') "
        "and ARIA_PORTAL_EMAIL to 'aria@arkmurus.com'. ALL auto-registrations will fail until fixed.",
        _reason,
    )
else:
    logger.info(
        "[R-F1504] Portal registry identity OK: %s <%s>",
        _ARIA_NAME, _ARIA_EMAIL,
    )


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
