"""Vendor / data-supplier registry — what ARIA is signed up to, what it costs.

Why this module exists
──────────────────────
Past incident 2026-04-18: user asked what DD sources we have and the
answer involved 20+ minutes of grep / agent exploration because nothing
enumerated vendors in one place. Env vars, sign-up URLs, monthly costs,
trial dates — all scattered across 52 adapters.

This registry is the single source of truth for:
  - Which data suppliers are live (API key present + pings)
  - Which are scaffolded (code exists, no credentials)
  - What each costs (so monthly spend never drifts invisibly)
  - Where to sign up (so non-engineers can onboard new sources)
  - The strategic "next buy" ladder for commercial upgrades

It is YAML-backed so the operator can edit without a code change. The
YAML lives next to this module at `vendor_registry.yaml`.

Public API
──────────
  load_vendors() -> list[Vendor]
  get(vendor_id: str) -> Vendor | None
  live_vendors() -> list[Vendor]    # credentials present
  pending_vendors() -> list[Vendor] # trialing / evaluating
  total_monthly_usd() -> float
  next_buy_recommendations(budget_usd=None) -> list[dict]
  async availability_ping() -> dict  # hit each source's is_available
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.vendor_registry")

_YAML_PATH = Path(__file__).parent / "vendor_registry.yaml"

# R-F705 (2026-05-18) — shared TTL cache for availability_ping().
# Three concurrent call sites (capability_card.get_vendor_availability,
# /api/aria/vendors, /api/aria/diagnostic/details health-status) each
# fire every vendor's is_available(), which means a single dashboard
# refresh hits OFAC/UN/HMT/SEC 3x within ~500ms (visible as duplicate
# httpx GETs to sdn.xml + sec.gov + ofsi within milliseconds in fly
# logs). Cache the full result for 60s so the second and third
# concurrent callers reuse the first one's work. Direct §15 fix
# (pay-once-remember-forever for the dashboard window).
_PING_CACHE: dict[str, Any] = {"result": None, "fetched_at": 0.0}
_PING_LOCK: asyncio.Lock | None = None  # lazily created on first call
_PING_CACHE_TTL_S = 60.0

# Tiers govern the recommended budget approach:
TIERS = {
    "free",              # no cost ever (SEC EDGAR, UN, OFAC)
    "self_signup_free",  # free tier after registration (ACLED, OpenCorporates free)
    "paid_low",          # < $500/mo — "just buy it" range
    "paid_mid",          # $500-5000/mo — requires trial + measurement
    "enterprise",        # > $5000/mo — sales call, legal review, board sign-off
}

# Status is the lifecycle of our relationship with the vendor:
STATUSES = {
    "unused",      # we know about it, not integrated
    "evaluating",  # signed up for trial, not yet wired
    "live",        # wired + credentials set + last known to work
    "suspended",   # previously live, currently disabled
    "cancelled",   # tried, rejected
}

# Coverage tags let us answer "which vendor covers X?"
COVERAGE_TAGS = {
    "sanctions",          # OFAC, OFSI, UN, EU, PEP lists
    "registry",           # company registries (CH, HMRC-adjacent, state SoS)
    "financials",         # P&L, revenue, credit, solvency
    "ubo",                # beneficial ownership graphs
    "tenders",            # procurement portals
    "conflict",           # ACLED, GDELT, ICG
    "adverse_media",      # news, negative press, enforcement
    "trade",              # import/export, customs, Comtrade
    "patents",            # IP filings
    "insider_trades",     # SEC 13D/13G/4, EDGAR
    "defence_specific",   # Jane's, SIPRI, defence-trade registers
}


@dataclass
class Vendor:
    id: str
    name: str
    tier: str
    status: str
    coverage: list[str] = field(default_factory=list)
    monthly_cost_usd: float = 0.0
    signup_url: str = ""
    docs_url: str = ""
    api_key_env_var: str = ""
    aux_env_vars: list[str] = field(default_factory=list)
    source_module: str = ""       # dotted path to aria_service.intel.sources.X
    notes: str = ""
    signed_up_on: str = ""
    trial_ends_on: str = ""
    priority_to_buy: int = 0      # 1 = buy next, 10 = nice-to-have

    # Runtime-derived fields (not persisted)
    credentials_present: bool = False
    available: bool | None = None  # None = not pinged yet

    def has_credentials(self) -> bool:
        """True if the API key env var (if any) is set. Free sources
        that don't need a key return True."""
        if not self.api_key_env_var:
            return True  # free source, no key needed
        val = (os.getenv(self.api_key_env_var) or "").strip()
        if not val:
            return False
        # Check aux vars too (ACLED needs email + password, not just one key)
        for v in self.aux_env_vars:
            if not (os.getenv(v) or "").strip():
                return False
        return True


def _default_registry() -> list[dict]:
    """Seed list that ships with the codebase. YAML overrides if present.

    Sorted by priority_to_buy. Entries marked enterprise/paid_mid are the
    strategic-buy ladder — the notes field explains WHY each one is on
    the list so the priority ordering is defensible.
    """
    return [
        # ── Free, always live ─────────────────────────────────────────
        {
            "id": "sec_edgar", "name": "SEC EDGAR",
            "tier": "free", "status": "live",
            "coverage": ["registry", "financials", "insider_trades"],
            "monthly_cost_usd": 0.0,
            "signup_url": "https://www.sec.gov/edgar/filer-information",
            "docs_url": "https://www.sec.gov/edgar/sec-api-documentation",
            "source_module": "aria_service.intel.sources.sec_edgar",
            "notes": "Free, unauthenticated. Rate limit: 10 req/s per IP. "
                     "Covers US public companies only — but 10-K/10-Q filings "
                     "are gold-standard for financial DD on US defence primes.",
            "priority_to_buy": 0,
        },
        {
            "id": "ofac_sdn", "name": "US Treasury OFAC SDN",
            "tier": "free", "status": "live",
            "coverage": ["sanctions"],
            "monthly_cost_usd": 0.0,
            "signup_url": "https://sanctionslist.ofac.treas.gov/",
            "docs_url": "https://ofac.treasury.gov/specially-designated-nationals-and-blocked-persons-list-sdn-human-readable-lists",
            "source_module": "aria_service.intel.sources.ofac_sdn",
            "notes": "Primary-source US sanctions screen. Legal requirement "
                     "if any US person/USD-clearing is in the deal chain. "
                     "Cached 3h; refreshed continuously at source.",
            "priority_to_buy": 0,
        },
        {
            "id": "uk_ofsi", "name": "UK FCDO / OFSI consolidated list",
            "tier": "free", "status": "live",
            "coverage": ["sanctions"],
            "monthly_cost_usd": 0.0,
            "signup_url": "https://www.gov.uk/government/publications/financial-sanctions-consolidated-list-of-targets",
            "source_module": "aria_service.intel.sources.fcdo_sanctions",
            "notes": "UK sanctions regime (SAMLA 2018). Legally distinct from "
                     "EU/US post-Brexit. Required for any UK-nexus deal.",
            "priority_to_buy": 0,
        },
        {
            "id": "un_sc", "name": "UN Security Council consolidated list",
            "tier": "free", "status": "live",
            "coverage": ["sanctions"],
            "monthly_cost_usd": 0.0,
            "signup_url": "https://www.un.org/securitycouncil/sanctions/information",
            "source_module": "aria_service.intel.sources.un_sc_sanctions",
            "notes": "All 193 UN members legally obligated to implement. "
                     "Hard-stop for any designation. No exceptions.",
            "priority_to_buy": 0,
        },
        {
            "id": "worldbank_debarred", "name": "World Bank debarred firms",
            # R-F279 (2026-05-11) — tier changed: 'enterprise_only' (was
            # 'self_signup_free'), reflecting verified state per R-F155:
            # apigwext.worldbank.org returns 403, no public developer
            # portal exists. WB Firm360 access is enterprise-direct
            # only (data@worldbank.org / IVP), 1-3wk SLA, low probability.
            "tier": "enterprise_only", "status": "covered_via_opensanctions",
            "coverage": ["sanctions", "adverse_media"],
            "monthly_cost_usd": 0.0,
            "api_key_env_var": "WORLDBANK_SUBSCRIPTION_KEY",
            "signup_url": "https://projects.worldbank.org/en/projects-operations/procurement/debarred-firms",
            "docs_url": "https://projects.worldbank.org/en/projects-operations/procurement/debarred-firms",
            "source_module": "aria_service.intel.sources.worldbank_debarred",
            "notes": "Cross-recognised by AfDB/AsDB/EBRD/IDB under MCEA 2010. "
                     "Critical for MDB-financed defence-adjacent deals. R-F155 "
                     "(2026-05-10) verified that the WB developer portal does "
                     "NOT exist for public registration (apigwext.worldbank.org "
                     "returns 403). The only known access route is enterprise-"
                     "direct via data@worldbank.org / Integrity VP (1-3wk SLA). "
                     "OpenSanctions dataset 'wb_debarred' aggregates the same "
                     "coverage and is the practical default — no operator action "
                     "required.",
            "priority_to_buy": 2,
        },
        {
            "id": "worldbank_documents", "name": "World Bank Documents & Reports API",
            "tier": "free", "status": "live",
            "coverage": ["registry", "adverse_media", "procurement"],
            "monthly_cost_usd": 0.0,
            "signup_url": "https://documents.worldbank.org/en/publication/documents-reports/api",
            "docs_url": "https://documents.worldbank.org/en/publication/documents-reports/api",
            "source_module": "aria_service.intel.sources.worldbank_documents",
            "notes": "Free, no auth. 200k+ WB project reports, procurement plans, "
                     "country assessments, INT investigations. Research-shaped — "
                     "different from worldbank_debarred (compliance screen). Use "
                     "for DD context when target operates in WB-funded markets "
                     "(Angola, Kenya, Nigeria, Bangladesh, etc.).",
            "priority_to_buy": 0,
        },
        {
            "id": "opensanctions_free", "name": "OpenSanctions (free tier)",
            "tier": "free", "status": "live",
            "coverage": ["sanctions", "adverse_media"],
            "monthly_cost_usd": 0.0,
            "api_key_env_var": "OPENSANCTIONS_API_KEY",
            "signup_url": "https://www.opensanctions.org/api/",
            "source_module": "aria_service.intel.sanctions",
            "notes": "Already wired. Free tier: 1 req/s, 1k/month. Aggregates "
                     "OFAC/EU/UN/OFSI/PEP — useful as fallback when primary "
                     "sources are rate-limiting but NOT a replacement.",
            "priority_to_buy": 0,
        },
        {
            "id": "companies_house_free", "name": "UK Companies House",
            "tier": "free", "status": "live",
            "coverage": ["registry", "financials", "ubo"],
            "monthly_cost_usd": 0.0,
            "api_key_env_var": "COMPANIES_HOUSE_API_KEY",
            "signup_url": "https://developer.company-information.service.gov.uk/",
            "source_module": "aria_service.intel.companies_house",
            "notes": "Free tier: 600 req/5min per key. Paid tier 10x rate "
                     "limit ~£100/mo — recommend upgrading once DD volume "
                     "crosses ~200 runs/day.",
            "priority_to_buy": 0,
        },

        # ── Self-signup free (needs operator action to activate) ──────
        {
            "id": "acled", "name": "ACLED (Armed Conflict Location & Event Data)",
            "tier": "self_signup_free", "status": "evaluating",
            "coverage": ["conflict"],
            "monthly_cost_usd": 0.0,
            "api_key_env_var": "ACLED_EMAIL",
            "aux_env_vars": ["ACLED_PASSWORD"],
            "signup_url": "https://acleddata.com/register/",
            "source_module": "aria_service.intel.sources.acled",
            "notes": "Free for non-commercial use. Register at acleddata.com "
                     "with a professional email; approval usually < 24h. "
                     "Set ACLED_EMAIL + ACLED_PASSWORD on fly.io after "
                     "approval. Unlocks 200+ country operational-risk signal.",
            "priority_to_buy": 1,  # one-form signup, unlocks material DD signal
        },
        {
            "id": "opencorporates_free", "name": "OpenCorporates (free tier)",
            "tier": "self_signup_free", "status": "unused",
            "coverage": ["registry", "ubo"],
            "monthly_cost_usd": 0.0,
            "api_key_env_var": "OPENCORPORATES_API_KEY",
            "signup_url": "https://api.opencorporates.com/",
            "notes": "Free tier: 500 req/mo. Adds ~140 jurisdictions ARIA "
                     "currently can't query directly. Low ceiling but good "
                     "for validation + coverage spot-checks.",
            "priority_to_buy": 2,
        },
        {
            "id": "gleif", "name": "GLEIF (Legal Entity Identifier)",
            "tier": "free", "status": "unused",
            "coverage": ["registry"],
            "monthly_cost_usd": 0.0,
            "signup_url": "https://www.gleif.org/en/lei-data/gleif-api",
            "notes": "Free, unauthenticated. Provides cross-jurisdiction LEI "
                     "identifiers so the same entity resolves consistently "
                     "across SEC / Companies House / OpenCorporates / ORBIS. "
                     "Low engineering cost, high identity-resolution payoff.",
            "priority_to_buy": 3,
        },

        # ── Paid-low: strategic buy, small budget ─────────────────────
        {
            "id": "opencorporates_pro", "name": "OpenCorporates Pro",
            "tier": "paid_low", "status": "unused",
            "coverage": ["registry", "ubo", "adverse_media"],
            "monthly_cost_usd": 250.0,
            "signup_url": "https://opencorporates.com/info/our-data",
            "notes": "~$1-5k/yr. Adds full global company data + basic UBO. "
                     "Fills ~70% of the missing-jurisdiction gap in one buy. "
                     "Best first paid buy once free tier hits ceiling.",
            "priority_to_buy": 4,
        },
        {
            "id": "companies_house_paid", "name": "Companies House (paid tier)",
            "tier": "paid_low", "status": "unused",
            "coverage": ["registry", "ubo"],
            "monthly_cost_usd": 125.0,
            "signup_url": "https://developer.company-information.service.gov.uk/",
            "notes": "~£100/mo. 10x rate limit. Only worth buying once UK DD "
                     "volume crosses ~200 runs/day or hits free-tier throttling.",
            "priority_to_buy": 5,
        },

        # ── Paid-mid: trial + measure before committing ───────────────
        {
            "id": "sayari", "name": "Sayari",
            "tier": "paid_mid", "status": "unused",
            "coverage": ["ubo", "adverse_media", "sanctions", "trade"],
            "monthly_cost_usd": 5000.0,
            "signup_url": "https://sayari.com/solutions/due-diligence/",
            "notes": "~$50-200k/yr. Best-in-class UBO graph for emerging "
                     "markets (LatAm, Africa, Gulf). Primary competitor to "
                     "Refinitiv in the KYC/AML vertical. Trial before buying.",
            "priority_to_buy": 6,
        },
        {
            "id": "refinitiv_worldcheck", "name": "Refinitiv World-Check",
            "tier": "paid_mid", "status": "unused",
            "coverage": ["sanctions", "adverse_media", "ubo"],
            "monthly_cost_usd": 5000.0,
            "signup_url": "https://www.refinitiv.com/en/products/world-check-kyc-screening",
            "notes": "~$30-100k/yr. Supersedes OpenSanctions for enterprise "
                     "KYC — real-time PEP + adverse-media. Adjacent to Sayari; "
                     "pick one based on trial results.",
            "priority_to_buy": 7,
        },
        {
            "id": "dnb", "name": "Dun & Bradstreet API",
            "tier": "paid_mid", "status": "unused",
            "coverage": ["financials", "registry", "trade"],
            "monthly_cost_usd": 1000.0,
            "signup_url": "https://www.dnb.com/products/business-data-api.html",
            "notes": "~$5-20k/yr starting tier. Adds credit metrics, payment "
                     "behaviour, supply-chain data. Essential if clients ask "
                     "for financial-health grading on counterparties.",
            "priority_to_buy": 8,
        },

        # ── Enterprise: board-sign-off territory ──────────────────────
        {
            "id": "janes", "name": "Jane's Intelligence",
            "tier": "enterprise", "status": "unused",
            "coverage": ["defence_specific", "adverse_media"],
            "monthly_cost_usd": 8500.0,
            "signup_url": "https://www.janes.com/",
            "notes": "~$100k+/yr. Defence-sector-exclusive intelligence "
                     "(procurement, sanctions enforcement, equipment specs). "
                     "Justified only if clients demand Jane's citations.",
            "priority_to_buy": 9,
        },
        {
            "id": "orbis_bvd", "name": "Orbis (Bureau van Dijk)",
            "tier": "enterprise", "status": "unused",
            "coverage": ["financials", "ubo", "registry"],
            "monthly_cost_usd": 8500.0,
            "signup_url": "https://www.bvdinfo.com/en-gb/our-products/data/international/orbis",
            "notes": "~$50-150k/yr. 200M+ entities, deep financials + UBO. "
                     "Heavy overlap with D&B + OpenCorporates Pro — trial "
                     "both first, then decide which tier to keep.",
            "priority_to_buy": 10,
        },
        {
            "id": "bloomberg", "name": "Bloomberg Terminal",
            "tier": "enterprise", "status": "unused",
            "coverage": ["financials", "adverse_media"],
            "monthly_cost_usd": 2000.0,  # per user
            "signup_url": "https://www.bloomberg.com/professional/",
            "notes": "~$24k/user/year + API licence negotiated separately. "
                     "Not primarily a DD tool; only buy if markets desk is "
                     "also using it.",
            "priority_to_buy": 12,
        },
    ]


def _load_yaml_overrides() -> list[dict] | None:
    """Load vendor_registry.yaml if present, else return None."""
    if not _YAML_PATH.exists():
        return None
    try:
        import yaml
        with open(_YAML_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return None
        vendors = data.get("vendors")
        if isinstance(vendors, list):
            return vendors
    except Exception as e:
        logger.warning("[vendor_registry] YAML load failed: %s", e)
    return None


def load_vendors() -> list[Vendor]:
    """Return the full vendor list. YAML overrides the default seed if
    present; otherwise the in-code seed is authoritative.

    Each Vendor has its `credentials_present` field populated from the
    current environment, so "live" status is always an accurate read of
    whether the API key is actually set.
    """
    raw = _load_yaml_overrides() or _default_registry()
    out: list[Vendor] = []
    for entry in raw:
        try:
            v = Vendor(
                id=str(entry["id"]),
                name=str(entry.get("name", entry["id"])),
                tier=str(entry.get("tier", "free")),
                status=str(entry.get("status", "unused")),
                coverage=list(entry.get("coverage") or []),
                monthly_cost_usd=float(entry.get("monthly_cost_usd", 0.0)),
                signup_url=str(entry.get("signup_url", "")),
                docs_url=str(entry.get("docs_url", "")),
                api_key_env_var=str(entry.get("api_key_env_var", "")),
                aux_env_vars=list(entry.get("aux_env_vars") or []),
                source_module=str(entry.get("source_module", "")),
                notes=str(entry.get("notes", "")),
                signed_up_on=str(entry.get("signed_up_on", "")),
                trial_ends_on=str(entry.get("trial_ends_on", "")),
                priority_to_buy=int(entry.get("priority_to_buy", 0)),
            )
            v.credentials_present = v.has_credentials()
            out.append(v)
        except Exception as e:
            logger.warning("[vendor_registry] skipping malformed entry %r: %s", entry, e)
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="vendor_registry",
        summary="Load Vendors",
        source_id="vendor_registry:R-F1001",
    )

    return out


def get(vendor_id: str) -> Vendor | None:
    for v in load_vendors():
        if v.id == vendor_id:
            return v
    return None


def live_vendors() -> list[Vendor]:
    """Vendors that are marked live AND have credentials present."""
    return [v for v in load_vendors() if v.status == "live" and v.credentials_present]


def pending_vendors() -> list[Vendor]:
    """Vendors in evaluation / signed up but not yet credentialed."""
    return [v for v in load_vendors() if v.status in ("evaluating", "unused") and v.priority_to_buy > 0]


def total_monthly_usd() -> float:
    """Sum of monthly cost across vendors that are live. Does not
    include evaluating/unused — those are aspirational spend.
    """
    return round(sum(v.monthly_cost_usd for v in live_vendors()), 2)


def next_buy_recommendations(budget_usd: float | None = None, limit: int = 5) -> list[dict]:
    """Return the ordered buy ladder, optionally filtered by budget.

    Each recommendation includes reason (from notes), monthly cost, and
    signup URL so the operator can action without further research.
    """
    vendors = sorted(
        [v for v in load_vendors()
         if v.status in ("unused", "evaluating") and v.priority_to_buy > 0],
        key=lambda v: v.priority_to_buy,
    )
    if budget_usd is not None:
        vendors = [v for v in vendors if v.monthly_cost_usd <= budget_usd]
    return [
        {
            "rank": i + 1,
            "id": v.id,
            "name": v.name,
            "tier": v.tier,
            "monthly_cost_usd": v.monthly_cost_usd,
            "coverage": v.coverage,
            "signup_url": v.signup_url,
            "rationale": v.notes,
            "env_var_to_set": v.api_key_env_var or "(none — free source)",
            "aux_env_vars": v.aux_env_vars,
        }
        for i, v in enumerate(vendors[:limit])
    ]


async def availability_ping() -> dict:
    """Ping each live source's `is_available()` to get real status (not
    just credential-presence). Used by the cross-server health endpoint
    and the daily briefing parity panel.

    R-F705 (2026-05-18) — result is TTL-cached for 60s across concurrent
    callers. Pre-R-F705 each dashboard refresh fired this function from
    three places (vendors / capability_card / diagnostic-details) and
    each call re-fetched every OFAC/UN/HMT/SEC feed (~12 upstream GETs
    each, ~36 per refresh). Now first call does the work, next two reuse.
    """
    global _PING_LOCK
    if _PING_LOCK is None:
        _PING_LOCK = asyncio.Lock()

    # Cheap path — return cached without grabbing the lock.
    now = time.monotonic()
    cached = _PING_CACHE.get("result")
    if cached is not None and (now - _PING_CACHE["fetched_at"]) < _PING_CACHE_TTL_S:
        return cached

    async with _PING_LOCK:
        # Re-check under lock — another waiter may have filled it.
        now = time.monotonic()
        cached = _PING_CACHE.get("result")
        if cached is not None and (now - _PING_CACHE["fetched_at"]) < _PING_CACHE_TTL_S:
            return cached
        return await _availability_ping_uncached()


async def _availability_ping_uncached() -> dict:
    """The uncached implementation. Always called under _PING_LOCK; the
    public availability_ping() wraps it with the TTL cache."""
    import importlib
    results: list[dict] = []
    for v in load_vendors():
        entry = {
            "id": v.id,
            "name": v.name,
            "status": v.status,
            "tier": v.tier,
            "credentials_present": v.credentials_present,
            "monthly_cost_usd": v.monthly_cost_usd,
        }
        if not v.source_module:
            entry["available"] = None
            entry["reason"] = "no source module wired"
            results.append(entry)
            continue
        try:
            mod = importlib.import_module(v.source_module)
            checker = getattr(mod, "is_available", None)
            if checker is None:
                entry["available"] = None
                entry["reason"] = "module has no is_available()"
            else:
                entry["available"] = bool(await checker())
                entry["reason"] = None
        except Exception as e:
            entry["available"] = False
            entry["reason"] = f"{type(e).__name__}: {e}"
        results.append(entry)

    live_count = sum(1 for r in results if r.get("available") is True)
    summary = {
        "total_vendors": len(results),
        "live_count": live_count,
        "credentials_set_count": sum(1 for r in results if r.get("credentials_present")),
        "monthly_spend_usd": total_monthly_usd(),
        "vendors": results,
    }

    # Brain signal — vendor health is a compliance/integrity check.
    # Sub-50% live count = capability gap; daily briefing should
    # surface which sources are dark.
    try:
        from . import brain_hook as _bh
        live_pct = (live_count / len(results)) if results else 0.0
        dark = [r["id"] for r in results if r.get("available") is False][:8]
        await _bh.absorb(
            module="vendor_registry",
            summary=(
                f"Vendor health: {live_count}/{len(results)} live "
                f"({live_pct:.0%}), monthly spend ${summary['monthly_spend_usd']:.2f}"
            ),
            detail=f"dark vendors: {dark}" if dark else "all vendors green",
            success=live_pct >= 0.5,
            gap_type=("vendor_outage" if live_pct < 0.5 else None),
            gap_detail=(f"Only {live_count}/{len(results)} vendors live — dark: {dark}"
                        if live_pct < 0.5 else None),
            extra_topics=["compliance"],
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    # R-F705 — populate the TTL cache used by the public wrapper.
    _PING_CACHE["result"] = summary
    _PING_CACHE["fetched_at"] = time.monotonic()
    return summary

# R-F2119 §21a — wire failure handler for vendor_registry
try:
    wire_failure(module="vendor_registry", detail="module shutdown",
                gap_type="engine_failure", source="vendor_registry:shutdown")
except Exception:
    pass
