"""Scraper orchestrator — routes URLs to the right Playwright adapter.

Routing precedence
──────────────────
  1. Known procurement portal (SEACE/SSB/SIPCE/PCE) → procurement adapter
  2. Everything else → generic catch-all

We do NOT route to paid/paywalled adapters here (no Jane's 360, no
Africa Intelligence, no Handelsregister paid-tier). Those remain
legitimate-access-only — if the operator buys the licence, we wire the
proper API; until then we skip them honestly.

This is called by deep_researcher.crawl_website as the LAST fallback
after static httpx + Lightpanda + crawl_enhancements (PDF/Wayback).
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger("aria.scraper.orchestrator")

# Domain → portal_id mapping
_PORTAL_DOMAINS: dict[str, str] = {
    "prodapp2.seace.gob.pe": "seace_peru",
    "seace.gob.pe": "seace_peru",
    "ihale.gov.tr": "ssb_turkey",
    "sipce.gov.ao": "sipce_angola",
    "pce.gov.mz": "pce_mozambique",
}


def detect_portal(url: str) -> str | None:
    """Return a portal_id if the URL matches a known procurement portal."""
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return None
    if not host:
        return None
    # Exact match first
    if host in _PORTAL_DOMAINS:
        return _PORTAL_DOMAINS[host]
    # Suffix match for subdomain variants
    for known, pid in _PORTAL_DOMAINS.items():
        if host.endswith(known):
            return pid
    return None


async def fetch(url: str, *, timeout: float = 45.0) -> dict:
    """Route + fetch. Returns a unified dict shape for the caller."""
    portal_id = detect_portal(url)
    if portal_id:
        logger.info("[scraper.orchestrator] %s → procurement adapter %s",
                    url[:80], portal_id)
        from . import procurement_adapters as _pa
        result = await _pa.fetch_portal(portal_id, timeout=timeout)
        # Re-shape into the generic adapter shape so callers get the same
        # interface regardless of which adapter served the request.
        return {
            "ok": result.get("ok", False),
            "url": url,
            "adapter": f"procurement:{portal_id}",
            "portal_notices": result.get("notices", []),
            "blocked": result.get("blocked", False),
            "block_reason": result.get("block_reason", ""),
            "error": result.get("error"),
            # Legacy shape for downstream extractors
            "html": "",
            "text": "\n\n".join(
                f"[{n.get('reference','')}] {n.get('title','')} — "
                f"{n.get('contracting_authority','')} — "
                f"value={n.get('estimated_value','')} "
                f"deadline={n.get('deadline','')}"
                for n in result.get("notices", [])
            ),
        }

    # Fall through to generic catch-all
    logger.info("[scraper.orchestrator] %s → generic catch-all", url[:80])
    from . import generic_adapter as _ga
    result = await _ga.fetch(url, timeout=timeout)
    result["adapter"] = "generic"
    return result
