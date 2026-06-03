"""World Bank Indicators v2 + Data360 — free macro-data API for country-risk overlay.

Why this exists
═══════════════
ARIA's jurisdiction_country_risk discipline (per dd_disciplines.py R-F152)
asks for quantitative macro overlay alongside qualitative FATF / FCDO
country-risk data. World Bank publishes ~1,500 indicators × 217 countries
× 60 years for free, no auth, on TWO complementary endpoints:

  - Indicators v2 (api.worldbank.org/v2) — older, battle-tested, deep
    historical coverage; Country / Indicator / Topic queries
  - Data360 (data360api.worldbank.org) — newer, modern search interface,
    same WDI underlying data + better search

This module wraps both. Free, no key needed. CC-BY 4.0 licensed (just cite WB).

Why NOT to confuse this with worldbank_debarred.py
══════════════════════════════════════════════════
Different APIs, different scope:
  - worldbank_debarred → Firm360 (Adobe Experience Manager backend, gated,
    no public registration; OpenSanctions wb_debarred is the practical answer
    per R-F155).
  - worldbank_indicators (THIS) → free macro indicators / WDI (no auth, open).

The two share a brand but operate on completely different infrastructure.

Use cases for DD
════════════════
  - jurisdiction_country_risk: pull GDP, debt/GDP, fiscal balance, FX
    reserves, military spend %, governance indicators for the counterparty's
    home jurisdiction. Adds quantitative depth to FATF qualitative.
  - financial_soundness: country-context for sovereign-counterparty
    transactions (debt sustainability, current account balance).
  - environmental_esg: WB carbon-intensity indicators per country, energy
    consumption per capita, methane intensity.
  - market_intel: trade flow data (imports/exports), commodity dependence,
    growth trajectories — useful for commodity-broker DD.

Examples of high-value indicators for ARIA's domain
═══════════════════════════════════════════════════
  Macro-stability:
    NY.GDP.MKTP.CD          GDP (current US$)
    GC.DOD.TOTL.GD.ZS       Central government debt (% GDP)
    NE.RSB.GNFS.CD          External balance on goods + services
    FI.RES.TOTL.CD          Total reserves
    FP.CPI.TOTL.ZG          Inflation, consumer prices

  Defence / security:
    MS.MIL.XPND.GD.ZS       Military expenditure (% GDP)
    MS.MIL.XPND.CD          Military expenditure (current US$)
    MS.MIL.MPRT.KD          Arms imports (SIPRI TIV)
    MS.MIL.XPRT.KD          Arms exports (SIPRI TIV)

  Governance (WGI):
    CC.EST                  Control of Corruption: Estimate
    GE.EST                  Government Effectiveness: Estimate
    PV.EST                  Political Stability + Absence of Violence
    RL.EST                  Rule of Law: Estimate
    RQ.EST                  Regulatory Quality: Estimate
    VA.EST                  Voice + Accountability: Estimate

  Energy / commodity:
    EG.USE.PCAP.KG.OE       Energy use per capita (kg oil equivalent)
    EG.IMP.CONS.ZS          Energy imports, net (% energy use)
    EN.GHG.CO2.PC.CE.AR5    CO2 emissions per capita (AR5)
    EG.ELC.RNEW.ZS          Renewable electricity output (% total)

(See full WDI catalogue at api.worldbank.org/v2/indicator?format=json)

Authoring discipline
════════════════════
Per Clauses 14 + 17, every figure returned carries source attribution
(WB indicator code + retrieval date) so downstream synthesis can cite
properly. Module degrades gracefully if either endpoint is unreachable.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from . import _common
from ..engine_wiring import wired

logger = logging.getLogger("aria.sources.worldbank_indicators")

_SOURCE = "worldbank_indicators"
_AUTH = "anonymous"

_INDICATORS_V2_BASE = "https://api.worldbank.org/v2"
_DATA360_BASE = "https://data360api.worldbank.org/data360"
_HUMAN_URL = "https://data.worldbank.org/"

_REQUEST_TIMEOUT = 15.0
_UA = "ARIA-DD-Orchestrator research@arkmurus.com"

# Default indicator basket for country-risk overlay — covers macro stability,
# defence/security, governance. Operator can override per call.
_DEFAULT_COUNTRY_RISK_INDICATORS = [
    "NY.GDP.MKTP.CD",       # GDP (current US$)
    "GC.DOD.TOTL.GD.ZS",    # Central government debt (% GDP)
    "FI.RES.TOTL.CD",       # Total reserves
    "FP.CPI.TOTL.ZG",       # Inflation, consumer prices
    "MS.MIL.XPND.GD.ZS",    # Military expenditure (% GDP)
    "CC.EST",               # Control of Corruption (WGI)
    "PV.EST",               # Political Stability (WGI)
    "RL.EST",               # Rule of Law (WGI)
    "GE.EST",               # Government Effectiveness (WGI)
]


def is_available() -> bool:
    """No env vars required — endpoints are free + open. Always available."""
    return True


# ── Indicators v2 — Country indicator fetch ────────────────────────────────

@wired(module="sources.worldbank_indicators", summary="WB indicators for {iso2_or_iso3}")
async def fetch_country_indicators(
    iso2_or_iso3: str,
    indicators: list[str] | None = None,
    *,
    most_recent_only: bool = True,
    years: int = 5,
) -> dict[str, Any]:
    """Fetch a basket of indicators for a single country via Indicators v2.

    Args:
      iso2_or_iso3: ISO-3166 alpha-2 or alpha-3 code (e.g. "AO" or "AGO")
      indicators: WDI indicator codes; defaults to country-risk basket
      most_recent_only: if True, return only the latest available value
        per indicator; if False, return last `years` years
      years: when most_recent_only=False, how many years back

    Returns:
      {
        ok: bool,
        country_code: str,
        indicators: { <code>: [{year, value, indicator_name}] },
        retrieved_at: ISO timestamp,
        source_url: API URL used,
        error: str (only on failure),
      }
    """
    if not iso2_or_iso3:
        return {"ok": False, "error": "country code required"}
    indicators = list(indicators) if indicators else list(_DEFAULT_COUNTRY_RISK_INDICATORS)
    code = iso2_or_iso3.strip().upper()

    # Indicators v2 uses lower-case in URL
    url = f"{_INDICATORS_V2_BASE}/country/{code.lower()}/indicator/{';'.join(indicators)}"
    params: dict[str, Any] = {"format": "json", "per_page": 500}
    if most_recent_only:
        params["mrnev"] = 1  # Most Recent Non-Empty Value per indicator
    else:
        # Date range — last N years
        from datetime import datetime, timezone
        _now_year = datetime.now(timezone.utc).year
        params["date"] = f"{_now_year - years}:{_now_year}"

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers={"User-Agent": _UA, "Accept": "application/json"})
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "country_code": code, "source_url": str(resp.request.url)}
        payload = resp.json()
        # Indicators v2 returns [meta, [data...]]
        if not isinstance(payload, list) or len(payload) < 2:
            return {"ok": False, "error": "unexpected payload shape", "country_code": code}
        meta = payload[0] or {}
        data = payload[1] or []
        # Group by indicator code
        by_code: dict[str, list[dict[str, Any]]] = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            ind = row.get("indicator", {}) or {}
            ind_code = ind.get("id", "")
            if not ind_code:
                continue
            by_code.setdefault(ind_code, []).append({
                "year": row.get("date"),
                "value": row.get("value"),
                "indicator_name": ind.get("value", ""),
            })
        return {
            "ok": True,
            "country_code": code,
            "indicators": by_code,
            "retrieved_at": _common._iso_now() if hasattr(_common, "_iso_now") else "",
            "source_url": str(resp.request.url),
            "meta": {"total_records": meta.get("total")},
        }
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"http_error: {e}", "country_code": code, "source_url": url}
    except Exception as e:
        logger.warning("worldbank_indicators.fetch_country_indicators failed: %s", e)
        return {"ok": False, "error": str(e)[:200], "country_code": code, "source_url": url}


# ── Indicators v2 — Topic / catalogue search ──────────────────────────────

async def search_indicators(query: str, *, max_results: int = 25) -> dict[str, Any]:
    """Search WDI catalogue for indicators matching a free-text query.

    Args:
      query: free-text query (e.g. "trade flows", "energy consumption")
      max_results: cap

    Returns:
      { ok, query, results: [{indicator_code, name, source, topic}] }
    """
    if not query:
        return {"ok": False, "error": "query required"}
    # Indicators v2 doesn't have a direct full-text search endpoint, but
    # we can pull the indicator catalogue + filter client-side. This is
    # a small payload (indicator metadata, not data).
    url = f"{_INDICATORS_V2_BASE}/indicator"
    params = {"format": "json", "per_page": 1000}
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers={"User-Agent": _UA, "Accept": "application/json"})
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "source_url": str(resp.request.url)}
        payload = resp.json()
        if not isinstance(payload, list) or len(payload) < 2:
            return {"ok": False, "error": "unexpected payload shape"}
        all_indicators = payload[1] or []
        ql = query.lower()
        matches: list[dict[str, Any]] = []
        for row in all_indicators:
            if not isinstance(row, dict):
                continue
            name = (row.get("name", "") or "").lower()
            note = (row.get("sourceNote", "") or "").lower()
            if ql in name or ql in note:
                matches.append({
                    "indicator_code": row.get("id", ""),
                    "name": row.get("name", ""),
                    "source": (row.get("source") or {}).get("value", ""),
                    "topic": ", ".join((t.get("value", "") for t in row.get("topics", []) or [])),
                })
                if len(matches) >= max_results:
                    break
        return {"ok": True, "query": query, "results": matches}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"http_error: {e}", "source_url": url}
    except Exception as e:
        logger.warning("worldbank_indicators.search_indicators failed: %s", e)
        return {"ok": False, "error": str(e)[:200], "source_url": url}


# ── Data360 — modern search endpoint ───────────────────────────────────────

async def data360_search(query: str, *, max_results: int = 15) -> dict[str, Any]:
    """Use Data360's searchv2 endpoint — modern interface to WDI + adjacent
    datasets. Complements Indicators v2 search with broader coverage.

    Args:
      query: free-text or structured query
      max_results: cap

    Returns:
      { ok, query, results: [...], source_url }
    """
    if not query:
        return {"ok": False, "error": "query required"}
    url = f"{_DATA360_BASE}/searchv2"
    params = {"q": query, "limit": max_results}
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers={"User-Agent": _UA, "Accept": "application/json"})
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "source_url": str(resp.request.url)}
        payload = resp.json()
        # Data360 schema: results array
        results = payload.get("results") or payload.get("data") or []
        return {
            "ok": True,
            "query": query,
            "results": results[:max_results] if isinstance(results, list) else [],
            "source_url": str(resp.request.url),
        }
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"http_error: {e}", "source_url": url}
    except Exception as e:
        logger.warning("worldbank_indicators.data360_search failed: %s", e)
        return {"ok": False, "error": str(e)[:200], "source_url": url}


# ── DD-shaped facade — country-risk overlay for orchestrator integration ──

async def country_risk_overlay(country_iso2_or_iso3: str) -> dict[str, Any]:
    """Convenience facade for jurisdiction_country_risk discipline.

    Returns a structured country-risk overlay using the default indicator
    basket. Suitable for direct inclusion in DD report compliance section.

    Returns:
      {
        ok: bool,
        country_code: str,
        macro: { gdp_usd, debt_to_gdp_pct, reserves_usd, inflation_pct },
        defence: { military_spend_pct_gdp },
        governance: { control_of_corruption, political_stability,
                      rule_of_law, government_effectiveness },
        retrieved_at: ISO,
        source_attribution: 'World Bank Indicators v2 (api.worldbank.org/v2)',
        primary_source_url: str,
      }
    """
    raw = await fetch_country_indicators(country_iso2_or_iso3, most_recent_only=True)
    if not raw.get("ok"):
        return raw
    inds = raw.get("indicators", {})

    def _val(code: str) -> Any:
        rows = inds.get(code) or []
        return rows[0]["value"] if rows else None

    return {
        "ok": True,
        "country_code": raw["country_code"],
        "macro": {
            "gdp_usd": _val("NY.GDP.MKTP.CD"),
            "debt_to_gdp_pct": _val("GC.DOD.TOTL.GD.ZS"),
            "reserves_usd": _val("FI.RES.TOTL.CD"),
            "inflation_pct": _val("FP.CPI.TOTL.ZG"),
        },
        "defence": {
            "military_spend_pct_gdp": _val("MS.MIL.XPND.GD.ZS"),
        },
        "governance_wgi": {
            "control_of_corruption": _val("CC.EST"),
            "political_stability": _val("PV.EST"),
            "rule_of_law": _val("RL.EST"),
            "government_effectiveness": _val("GE.EST"),
        },
        "retrieved_at": raw.get("retrieved_at", ""),
        "source_attribution": "World Bank Indicators v2 (api.worldbank.org/v2). Free under CC-BY 4.0.",
        "primary_source_url": raw.get("source_url", ""),
        "wgi_interpretation_note": (
            "Worldwide Governance Indicators range -2.5 to +2.5. Higher = better. "
            "Negative values indicate below-average global governance on that dimension. "
            "Interpret as percentile — e.g. CC.EST = -1.0 ≈ 16th percentile globally."
        ),
    }


# ── Self-diagnostic surface ────────────────────────────────────────────────

async def smoke_test() -> dict[str, Any]:
    """Quick health check — fetches one indicator for one country.
    Used by self_diagnostic to verify the module is reachable."""
    result = await fetch_country_indicators("US", indicators=["NY.GDP.MKTP.CD"], most_recent_only=True)
    return {
        "ok": result.get("ok", False),
        "tested_endpoint": _INDICATORS_V2_BASE,
        "error": result.get("error"),
        "latency_indicator": "fast" if result.get("ok") else "failed",
    }
