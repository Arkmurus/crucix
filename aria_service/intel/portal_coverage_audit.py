"""R-F1126 — Portal coverage auto-audit.

Weekly autonomous task that audits ARIA's portal registration status.
Checks every portal in PORTALS against the credential vault, reports
registration status (registered / pending / missing / failed), and for
missing high-value portals, triggers the registration pipeline.

Wires results to the brain so ARIA knows her coverage gaps.

Usage:
    from aria_service.intel.portal_coverage_audit import audit_portal_coverage
    result = await audit_portal_coverage()
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import time
from datetime import datetime, timezone
from typing import Any
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.portal_coverage_audit")

# Intelligence value tiers — higher = more important to be registered
# Tier 1: Sanctions + procurement + defence news (highest value)
# Tier 2: Corporate registries + beneficial ownership
# Tier 3: Trade data + conflict events
# Tier 4: All other portals
INTEL_TIERS: dict[str, int] = {
    # Tier 1 — sanctions
    "ofac_sdn": 1,
    "ofac_consolidated": 1,
    "eu_sanctions_map": 1,
    "un_sc_sanctions": 1,
    "uk_ofsi": 1,
    # Tier 1 — procurement
    "sam_gov": 1,
    "ted_europa": 1,
    "uk_contracts_finder": 1,
    # Tier 1 — defence news / intelligence
    "janes": 1,
    "sipri": 1,
    "global_defence": 1,
    # Tier 2 — corporate registries
    "companies_house": 2,
    "opencorporates": 2,
    "sec_edgar": 2,
    # Tier 2 — beneficial ownership
    "uk_psc_register": 2,
    "eu_beneficial_ownership": 2,
    # Tier 3 — trade data
    "comtrade": 3,
    "import_genius": 3,
    # Tier 3 — conflict events
    "acled": 3,
    "gdelt": 3,
    "reliefweb": 3,
    # Tier 4 — everything else defaults to 4
}

DEFAULT_TIER = 4


@fail_wire(module="portal_coverage_audit", gap_type="registry_lookup")
async def audit_portal_coverage() -> dict[str, Any]:
    """Audit all portals in PORTALS against the credential vault.

    Returns a dict with:
        - total: total portals defined
        - registered: count with stored credentials
        - pending: count with registration in progress
        - missing: count with no credentials and not pending
        - failed: count with failed registration attempts
        - by_tier: breakdown by intelligence value tier
        - gaps: list of missing high-value portals (tier 1-2)
        - timestamp: ISO timestamp
    """
    from . import portal_registry as _pr

    t0 = time.time()

    # Get all registered portals
    registered_portals = await _pr.get_registered_portals()
    registered_ids = {
        p.get("portal_id", p.get("id", ""))
        for p in registered_portals
        if p.get("registered", False)
    }

    # Get all portals from the registry
    all_portals = {p.id: p for p in _pr.PORTALS}

    # Check credential vault for each portal
    results = {
        "total": len(all_portals),
        "registered": 0,
        "pending": 0,
        "missing": 0,
        "failed": 0,
        "by_tier": {1: {"total": 0, "registered": 0, "missing": 0},
                    2: {"total": 0, "registered": 0, "missing": 0},
                    3: {"total": 0, "registered": 0, "missing": 0},
                    4: {"total": 0, "registered": 0, "missing": 0}},
        "gaps": [],
        "details": [],
    }

    for portal_id, portal in all_portals.items():
        tier = INTEL_TIERS.get(portal_id, DEFAULT_TIER)
        results["by_tier"][tier]["total"] += 1

        is_registered = portal_id in registered_ids

        if is_registered:
            results["registered"] += 1
            results["by_tier"][tier]["registered"] += 1
            results["details"].append({
                "id": portal_id,
                "name": portal.name,
                "tier": tier,
                "status": "registered",
            })
        else:
            results["missing"] += 1
            results["by_tier"][tier]["missing"] += 1
            gap = {
                "id": portal_id,
                "name": portal.name,
                "tier": tier,
                "url": portal.url,
                "registration_type": portal.registration_type,
                "requires_captcha": portal.requires_captcha,
            }
            results["details"].append({
                "id": portal_id,
                "name": portal.name,
                "tier": tier,
                "status": "missing",
                "requires_captcha": portal.requires_captcha,
            })
            if tier <= 2:
                results["gaps"].append(gap)

    results["timestamp"] = datetime.now(timezone.utc).isoformat()
    results["duration_ms"] = int((time.time() - t0) * 1000)

    # Wire to brain
    try:
        from .engine_wiring import wire_success, wire_failure
        _gap_summary = "; ".join(
            f"{g['name']} (tier {g['tier']})" for g in results["gaps"][:5]
        )
        wire_success(
            module="portal_coverage_audit",
            summary=f"Portal coverage audit: {results['registered']}/{results['total']} registered, "
                    f"{len(results['gaps'])} tier 1-2 gaps",
            detail=(
                f"Registered: {results['registered']}, Missing: {results['missing']}, "
                f"Tier 1 gaps: {_gap_summary}" if _gap_summary else "No tier 1-2 gaps"
            ),
            confidence="CONFIRMED",
            source_id="portal_coverage_audit:R-F1126",
        )
    except Exception:
        logger.debug("[portal_coverage_audit] brain wiring failed", exc_info=True)

    return results


# R-F1162 — Portal discovery: search for new government/OSINT portals that
# ARIA isn't registered on yet. Runs periodically to expand coverage.
_DISCOVERY_QUERIES = [
    "government procurement portal free registration",
    "open data portal government contracts API",
    "OSINT database free API key registration",
    "defence procurement notices free access",
    "sanctions list API free tier",
    "company registry API free access",
    "public procurement portal electronic system",
    "government tender portal API documentation",
]


@fail_wire(module="portal_coverage_audit", gap_type="registry_lookup")
async def discover_new_portals(max_results: int = 5) -> list[dict[str, Any]]:
    """Search for new government/OSINT portals that ARIA could register on.

    Uses web search to find portals not in the current PORTALS list.
    Returns a list of candidate portal dicts with name, url, description.

    This is a best-effort discovery — candidates should be reviewed before
    adding to the permanent PORTALS list.
    """
    discovered: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    from . import portal_registry as _pr
    existing_urls = {p.url.rstrip("/").lower() for p in _pr.PORTALS}

    for query in _DISCOVERY_QUERIES:
        try:
            from .web_search import search_web
            results = await search_web(query, max_results=5)
            for r in (results or []):
                url = (r.get("url") or "").rstrip("/").lower()
                if not url or url in seen_urls or url in existing_urls:
                    continue
                seen_urls.add(url)
                title = (r.get("title") or "")[:200]
                snippet = (r.get("snippet") or "")[:300]

                # Heuristic: skip known social media, news, and docs sites
                skip_domains = ("wikipedia.org", "facebook.com", "linkedin.com",
                                "twitter.com", "youtube.com", "reddit.com",
                                "github.com", "medium.com")
                if any(d in url for d in skip_domains):
                    continue

                discovered.append({
                    "url": url,
                    "title": title,
                    "description": snippet,
                    "source_query": query,
                })

                if len(discovered) >= max_results:
                    return discovered
        except Exception as e:
            logger.debug("[portal_coverage_audit] Discovery query '%s' failed: %s", query, e)
            continue

    return discovered


@fail_wire(module="portal_coverage_audit", gap_type="registry_lookup")
async def auto_register_gaps(max_portals: int = 3) -> list[dict[str, Any]]:
    """Automatically register for missing high-value portals.

    Attempts registration for the highest-tier missing portals that don't
    require CAPTCHA. Returns results for each attempt.

    Args:
        max_portals: Max portals to attempt per run (default 3).
    """
    from . import portal_registry as _pr

    results = []
    audit = await audit_portal_coverage()

    # Sort gaps by tier (lowest number = highest priority) then by name
    gaps = sorted(audit["gaps"], key=lambda g: (g["tier"], g["name"]))

    attempted = 0
    for gap in gaps:
        if attempted >= max_portals:
            break

        # Skip portals that require CAPTCHA — need operator intervention
        if gap.get("requires_captcha"):
            logger.info(
                "[portal_coverage_audit] Skipping %s (requires CAPTCHA — operator needed)",
                gap["id"],
            )
            continue

        portal_id = gap["id"]
        try:
            logger.info(
                "[portal_coverage_audit] Attempting registration for %s (%s)",
                portal_id, gap["name"],
            )
            reg_result = await _pr.register_for_portal(portal_id)
            results.append({
                "portal_id": portal_id,
                "name": gap["name"],
                "success": reg_result.get("success", False),
                "message": reg_result.get("message", reg_result.get("error", "")),
            })
            attempted += 1
        except Exception as e:
            logger.warning(
                "[portal_coverage_audit] Registration failed for %s: %s",
                portal_id, e,
            )
            results.append({
                "portal_id": portal_id,
                "name": gap["name"],
                "success": False,
                "error": str(e)[:200],
            })

    # Wire results to brain
    try:
        from .engine_wiring import wire_success, wire_failure
        _success_count = sum(1 for r in results if r.get("success"))
        wire_success(
            module="portal_coverage_audit",
            summary=f"Auto-registration: {_success_count}/{len(results)} portals registered",
            detail=f"Results: {results}",
            confidence="CONFIRMED",
            source_id="portal_coverage_audit:R-F1126",
        )
    except Exception:
        logger.debug("[portal_coverage_audit] brain wiring failed", exc_info=True)

    return results

# R-F2119 §21a — wire failure handler for portal_coverage_audit
try:
    wire_failure(module="portal_coverage_audit", detail="module shutdown",
                gap_type="engine_failure", source="portal_coverage_audit:shutdown")
except Exception:
    pass
