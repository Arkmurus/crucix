"""Procurement-portal adapters — SEACE / SSB / SIPCE / PCE.

These four portals are defence-adjacent procurement sites that have NO
official API alternative and are badly-built (poor SEO, JS-heavy tables,
no sitemap). They're the legitimate use case for a heavier browser
fallback.

Portals covered
───────────────
  SEACE Peru          — https://prodapp2.seace.gob.pe/seacebus-uiwd-pub/
  SSB Turkey          — https://www.ihale.gov.tr/
  SIPCE Angola        — https://www.sipce.gov.ao/concursos
  PCE Mozambique      — https://www.pce.gov.mz/procurement/search

What we do
──────────
  - Navigate to the search page
  - Optional: submit a search query (entity name / product category)
  - Extract the results table using known CSS selectors
  - Return structured ProcurementNotice records for RAG ingest

What we do NOT do
─────────────────
  - No stealth fingerprint evasion
  - No CAPTCHA bypass (if the portal protects with CAPTCHA, we report
    `blocked_by_bot_detection` and skip — not scrape-through)
  - No paywalled content (the listed portals are all free-public)

DOM selector knowledge comes from ARIA_Playwright_Package.zip which
the operator supplied on 2026-04-18. We've stripped the stealth layer
but kept the legitimate selector maps — the author clearly knows
these portals well.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from ..engine_wiring import wired  # R-F3557 (§21a)

logger = logging.getLogger("aria.scraper.procurement_adapters")


@dataclass
class ProcurementNotice:
    source: str                 # portal name
    country: str
    reference: str              # portal's internal ID
    title: str
    contracting_authority: str = ""
    estimated_value: str = ""
    currency: str = ""
    deadline: str = ""
    published: str = ""
    category: str = ""
    url: str = ""
    description: str = ""
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Portal definitions ────────────────────────────────────────────────────
# Each entry: base URL, default search URL, selectors, notes.
# Selectors are the DOM hooks we use to extract rows and fields.

_PORTALS = {
    "seace_peru": {
        "name": "SEACE Peru (Sistema Electrónico de Contrataciones del Estado)",
        "country": "Peru",
        "base_url": "https://prodapp2.seace.gob.pe",
        "search_url": "https://prodapp2.seace.gob.pe/seacebus-uiwd-pub/buscadorPublico/buscadorPublico.xhtml",
        "wait_for": "networkidle",
        "selectors": {
            "results_row": ".ui-datatable tbody tr",
            "reference": "td:nth-child(2)",
            "title": "td:nth-child(3)",
            "authority": "td:nth-child(4)",
            "category": "td:nth-child(5)",
            "value": "td:nth-child(6)",
            "deadline": "td:nth-child(7)",
        },
        "notes": "JSF/PrimeFaces portal. Public search, no login. Moderate"
                 " rate-limiting. Results table paginates.",
    },
    "ssb_turkey": {
        "name": "Türkiye İhale Platformu (SSB procurement)",
        "country": "Turkey",
        "base_url": "https://www.ihale.gov.tr",
        "search_url": "https://www.ihale.gov.tr/EKAP/Ihale/Arama.aspx",
        "wait_for": "networkidle",
        "selectors": {
            "results_row": "table.ihale-results tr.data",
            "reference": "td.ihale-no",
            "title": "td.ihale-konu",
            "authority": "td.idare",
            "value": "td.yaklasik-bedel",
            "deadline": "td.son-basvuru",
        },
        "notes": "ASP.NET portal. Public access. Turkish defence tenders"
                 " including SSB-administered programmes.",
    },
    "sipce_angola": {
        "name": "SIPCE Angola (Sistema Integrado de Compras Públicas)",
        "country": "Angola",
        "base_url": "https://www.sipce.gov.ao",
        "search_url": "https://www.sipce.gov.ao/concursos",
        "wait_for": "load",
        "selectors": {
            "results_row": ".concurso-item",
            "reference": ".concurso-ref",
            "title": ".concurso-titulo",
            "authority": ".entidade",
            "value": ".valor-estimado",
            "deadline": ".data-limite",
        },
        "notes": "Public tender listings. SIPCE handles MINDEF (defence"
                 " ministry) tenders via this portal. No login needed.",
    },
    "pce_mozambique": {
        "name": "PCE Mozambique (Portal de Compras do Estado)",
        "country": "Mozambique",
        "base_url": "https://www.pce.gov.mz",
        "search_url": "https://www.pce.gov.mz/procurement/search",
        "wait_for": "load",
        "selectors": {
            "results_row": ".procurement-item",
            "reference": ".item-ref",
            "title": ".item-title",
            "authority": ".item-authority",
            "value": ".item-value",
            "deadline": ".item-deadline",
        },
        "notes": "Lightweight portal, usually responsive. FADM defence"
                 " tenders appear here when non-classified.",
    },
}


def list_portals() -> list[dict]:
    """Read-only view of registered procurement portals (for capability
    card + dashboard)."""
    return [
        {
            "id": pid,
            "name": cfg["name"],
            "country": cfg["country"],
            "base_url": cfg["base_url"],
            "notes": cfg["notes"],
        }
        for pid, cfg in _PORTALS.items()
    ]


# ── Row extraction via Playwright ──────────────────────────────────────────

async def _extract_rows(
    page,
    selectors: dict[str, str],
    limit: int = 50,
) -> list[dict[str, str]]:
    """Walk the results table, pull each row's field values."""
    rows_data: list[dict[str, str]] = []
    try:
        rows = await page.query_selector_all(selectors["results_row"])
    except Exception as e:
        logger.debug("[procurement] query_selector_all failed: %s", e)
        return []

    for row in rows[:limit]:
        row_data: dict[str, str] = {}
        for field_name, selector in selectors.items():
            if field_name == "results_row":
                continue
            try:
                cell = await row.query_selector(selector)
                if cell is None:
                    row_data[field_name] = ""
                    continue
                text = (await cell.inner_text() or "").strip()
                # Collapse whitespace — portal cells often have weird nbsp noise
                row_data[field_name] = re.sub(r"\s+", " ", text)
            except Exception:
                row_data[field_name] = ""
        if any(row_data.values()):
            rows_data.append(row_data)
    return rows_data


# ── Public API ─────────────────────────────────────────────────────────────

@wired(module="procurement_adapters", summary="procurement portal fetch completed", gap_type="source_failure")
async def fetch_portal(
    portal_id: str,
    *,
    query: str = "",
    max_results: int = 25,
    timeout: float = 45.0,
) -> dict:
    """Fetch a known procurement portal.

    Args:
        portal_id: one of the keys in _PORTALS.
        query: optional search query; if empty, returns the landing-page
            results (typically "latest N tenders").
        max_results: cap on returned notices.
        timeout: max wait for page navigation.

    Returns:
        {
            "ok": bool,
            "portal": str,
            "country": str,
            "url": str,
            "notices": list[ProcurementNotice_dict],
            "blocked": bool,
            "block_reason": str,
            "error": str | None,
            "scraped_at": str,
        }
    """
    cfg = _PORTALS.get(portal_id)
    if not cfg:
        return {
            "ok": False, "portal": portal_id, "country": "",
            "notices": [], "error": f"unknown portal: {portal_id}",
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

    # Import here so the module doesn't hard-fail if Playwright isn't
    # installed (self_diagnostic tests the import separately)
    from . import playwright_engine as _pe

    # Light usage — just fetch the search URL and extract rows.
    # Stateful search-with-query requires per-portal click flows and is
    # deferred to a follow-up commit.
    url = cfg["search_url"]
    if query:
        url = f"{url}?q={query}"  # naïve — portals differ; refine per-portal

    result = await _pe.fetch(
        url, wait_for=cfg.get("wait_for", "load"), timeout=timeout,
    )

    out = {
        "ok": False,
        "portal": portal_id,
        "portal_name": cfg["name"],
        "country": cfg["country"],
        "url": url,
        "notices": [],
        "blocked": result.blocked,
        "block_reason": result.block_reason,
        "error": result.error,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }

    if not result.ok:
        return out

    # Re-open a page in a new browser context so we can query DOM.
    # (We could refactor playwright_engine to return the page, but
    # keeping it simple — the overhead is acceptable for procurement
    # polling since this runs on the autonomous cadence, not per-request.)
    from playwright.async_api import async_playwright
    notices: list[ProcurementNotice] = []

    playwright = None
    browser = None
    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent="ARIA-Research-Crawler/1.0 (+research@arkmurus.com)",
            viewport={"width": 1280, "height": 800},
            locale="en-GB",
        )
        page = await context.new_page()
        page.set_default_timeout(int(timeout * 1000))
        await page.goto(url, wait_until=cfg.get("wait_for", "load"),
                        timeout=int(timeout * 1000))
        rows_raw = await _extract_rows(page, cfg["selectors"], limit=max_results)
        for r in rows_raw:
            notices.append(ProcurementNotice(
                source=portal_id,
                country=cfg["country"],
                reference=r.get("reference", ""),
                title=r.get("title", ""),
                contracting_authority=r.get("authority", ""),
                estimated_value=r.get("value", ""),
                deadline=r.get("deadline", ""),
                published=r.get("published", ""),
                category=r.get("category", ""),
                url=url,
                description=r.get("description", ""),
            ))
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        try:
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()
        except Exception:
            pass

    out["ok"] = bool(notices)
    out["notices"] = [n.__dict__ for n in notices]
    return out


async def is_available() -> bool:
    """True iff Playwright is available. Individual portals may be
    unreachable but the adapter layer itself just needs Playwright."""
    try:
        from . import playwright_engine as _pe
        return await _pe.is_available()
    except Exception:
        return False
