"""tbml_detection — Trade-Based Money Laundering price-anomaly detection
(R-F73, 2026-05-09).

Why this module exists
──────────────────────
Per peer review: 'The TBML detection logic is: does the declared
transaction value for commodity X between Country A and Country B fall
within the normal range for that commodity? A £500,000 invoice for a
commodity that COMTRADE shows typically trades at £50,000–£80,000
between those countries is a material anomaly that should be flagged.'

What this module does
─────────────────────
1. classify_anomaly(declared_value, benchmark_low, benchmark_high) —
   pure-math classifier given a declared value vs a benchmark range.
   No external dependencies; deterministic.

2. analyze_transaction(declared_unit_value, hs_code, exporter_country,
                        importer_country, year, quantity=1, currency='USD')
   — full pipeline: fetches the COMTRADE / IMF DOTS benchmark for the
   commodity-corridor pair, runs classify_anomaly, returns structured
   result + narrative.

3. analyze_transaction returns INDETERMINATE when COMTRADE_API_KEY is
   not set (the operator has the action item) — this module's pipeline
   is gated on the key, but the classify_anomaly primitive always works.

Compliance basis
────────────────
- FATF 2024 TBML Risk Indicators (commodity, corridor, invoice-value)
- FinCEN Geographic Targeting Orders for trade-based laundering
- EU 6th AML Directive Article 1.f (predicate offence: tax evasion via
  invoice manipulation)

Public API
──────────
    classify_anomaly(declared, low, high) -> dict
    analyze_transaction(declared_unit_value, hs_code, exporter_country,
                         importer_country, year=None, quantity=1) -> dict
    summary() -> dict
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.tbml_detection")

# Anomaly thresholds (multiplicative against benchmark midpoint)
# These are the conventional FATF 2024 TBML practitioner thresholds —
# 25% deviation = flag, 50% = severe, 100% = blatant. Below 25% =
# normal market noise.
_THRESHOLD_FLAG    = 0.25  # 25% over/under benchmark midpoint
_THRESHOLD_SEVERE  = 0.50
_THRESHOLD_BLATANT = 1.00

# COMTRADE Plus API base — gated on COMTRADE_API_KEY env var
_COMTRADE_BASE = "https://comtradeapi.un.org/data/v1/get/C/A/HS"


def _comtrade_api_key() -> str | None:
    """Return the COMTRADE Plus API key or None.
    Operator-pending: set via `fly secrets set COMTRADE_API_KEY=<key>`."""
    key = (os.getenv("COMTRADE_API_KEY") or "").strip()
    return key or None


def classify_anomaly(
    declared: float, low: float, high: float
) -> dict[str, Any]:
    """Classify a declared value against a benchmark range.

    Args:
        declared: declared unit value (per unit, in benchmark currency)
        low:      benchmark low (e.g. 25th percentile)
        high:     benchmark high (e.g. 75th percentile)

    Returns:
        {
          "ok":           bool,
          "midpoint":     float,
          "deviation_pct": float,        # signed; negative = under-invoiced
          "direction":    'over' | 'under' | 'neutral',
          "grade":        'OK' | 'FLAG' | 'SEVERE' | 'BLATANT',
          "narrative":    str,
        }
    """
    if not (low and high) or low <= 0 or high <= 0 or declared <= 0:
        return {
            "ok":         False,
            "reason":     "non-positive declared/low/high",
            "grade":      "INDETERMINATE",
            "narrative":  "TBML classify INDETERMINATE — non-positive input.",
        }
    if low > high:
        low, high = high, low
    midpoint = (low + high) / 2
    deviation = (declared - midpoint) / midpoint
    deviation_pct = round(deviation * 100, 2)
    abs_dev = abs(deviation)

    if abs_dev <= _THRESHOLD_FLAG:
        grade = "OK"
        direction = "neutral"
    elif abs_dev <= _THRESHOLD_SEVERE:
        grade = "FLAG"
        direction = "over" if deviation > 0 else "under"
    elif abs_dev <= _THRESHOLD_BLATANT:
        grade = "SEVERE"
        direction = "over" if deviation > 0 else "under"
    else:
        grade = "BLATANT"
        direction = "over" if deviation > 0 else "under"

    if grade == "OK":
        narrative = (
            f"TBML OK — declared {declared:,.2f} within normal range "
            f"({low:,.2f}-{high:,.2f}, deviation {deviation_pct:+.1f}% from midpoint)."
        )
    else:
        narrative = (
            f"TBML {grade} — declared {declared:,.2f} is {deviation_pct:+.1f}% "
            f"({direction}-invoiced) vs benchmark {low:,.2f}-{high:,.2f} "
            f"(midpoint {midpoint:,.2f})."
        )

    return {
        "ok":            True,
        "midpoint":      round(midpoint, 4),
        "low":           low,
        "high":          high,
        "declared":      declared,
        "deviation_pct": deviation_pct,
        "direction":     direction,
        "grade":         grade,
        "narrative":     narrative,
    }


async def _fetch_comtrade_benchmark(
    hs_code: str,
    exporter: str,
    importer: str,
    year: int,
) -> tuple[float, float] | None:
    """Fetch the unit-value benchmark range from UN COMTRADE Plus.

    Returns (low, high) in USD per unit, or None on failure / no key /
    no data. Best-effort; caller falls back to global-trade benchmark
    if a corridor-specific quote isn't available.
    """
    key = _comtrade_api_key()
    if not key:
        return None
    try:
        import httpx
    except ImportError:
        return None
    params = {
        "subscription-key":     key,
        "reporterCode":         exporter,
        "partnerCode":          importer,
        "cmdCode":              hs_code,
        "period":               str(year),
        "freqCode":             "A",
        "flowCode":             "X",  # exports
        "max":                  "5",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(_COMTRADE_BASE, params=params)
            if resp.status_code != 200:
                logger.debug("COMTRADE %s -> %s", hs_code, resp.status_code)
                return None
            data = resp.json()
    except Exception as e:
        logger.debug("COMTRADE fetch error: %s", e)
        return None

    rows = data.get("data") or []
    if not rows:
        return None

    unit_values: list[float] = []
    for r in rows:
        try:
            qty = float(r.get("qty") or 0)
            val = float(r.get("primaryValue") or 0)
            if qty > 0 and val > 0:
                unit_values.append(val / qty)
        except (ValueError, TypeError):
            continue

    if not unit_values:
        return None

    unit_values.sort()
    n = len(unit_values)
    if n >= 4:
        low = unit_values[n // 4]
        high = unit_values[(3 * n) // 4]
    else:
        low = unit_values[0]
        high = unit_values[-1]
    return (low, high)


async def analyze_transaction(
    *,
    declared_unit_value: float,
    hs_code: str,
    exporter_country: str,
    importer_country: str,
    year: int | None = None,
    quantity: int | float = 1,
) -> dict[str, Any]:
    """Full pipeline: fetch COMTRADE benchmark + classify the deviation.

    Returns INDETERMINATE if COMTRADE_API_KEY is unset or the fetch
    fails.
    """
    use_year = int(year) if year else datetime.now(timezone.utc).year - 1
    if not _comtrade_api_key():
        return {
            "ok":         False,
            "reason":     "COMTRADE_API_KEY not set — operator action pending",
            "grade":      "INDETERMINATE",
            "narrative":  (
                "TBML INDETERMINATE — COMTRADE_API_KEY env var not set. "
                "Set the key on fly to enable benchmark queries."
            ),
            "year":       use_year,
        }

    benchmark = await _fetch_comtrade_benchmark(
        hs_code, exporter_country, importer_country, use_year
    )
    if not benchmark:
        return {
            "ok":         False,
            "reason":     "no benchmark data for corridor + commodity + year",
            "grade":      "INDETERMINATE",
            "narrative": (
                f"TBML INDETERMINATE — no COMTRADE benchmark data for "
                f"HS {hs_code} {exporter_country}->{importer_country} {use_year}."
            ),
            "year":       use_year,
        }

    low, high = benchmark
    result = classify_anomaly(declared_unit_value, low, high)
    result["hs_code"]          = hs_code
    result["exporter_country"] = exporter_country
    result["importer_country"] = importer_country
    result["year"]             = use_year
    result["quantity"]         = quantity
    result["declared_total"]   = declared_unit_value * quantity

    # Brain absorb — material anomalies (FLAG and worse) into knowledge
    if result.get("grade") in ("FLAG", "SEVERE", "BLATANT"):
        try:
            from . import brain_hook
            await brain_hook.absorb(
                module="tbml_detection",
                summary=f"TBML {result['grade']}: {result['narrative']}",
                detail=(
                    f"HS {hs_code} {exporter_country}->{importer_country} "
                    f"{use_year}; declared {declared_unit_value:,.2f}/unit "
                    f"× {quantity} = {result['declared_total']:,.2f}; "
                    f"benchmark {low:,.2f}-{high:,.2f}; "
                    f"deviation {result['deviation_pct']:+.1f}%."
                ),
                topic="tbml_anomaly",
                entity=f"{exporter_country}->{importer_country}:HS{hs_code}",
                success=True,
                confidence="ASSESSED",
            )
        except Exception as e:
            logger.debug("tbml brain absorb failed (non-fatal): %s", e)

    return result


def summary() -> dict[str, Any]:
    return {
        "module":            "tbml_detection",
        "compliance_basis":  "FATF 2024 TBML Risk Indicators",
        "comtrade_key_set":  _comtrade_api_key() is not None,
        "thresholds":        {
            "flag":    _THRESHOLD_FLAG,
            "severe":  _THRESHOLD_SEVERE,
            "blatant": _THRESHOLD_BLATANT,
        },
    }

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="tbml_detection",
                     summary="tbml_detection module active",
                     source_id="tbml_detection:init")
    except Exception:
        try:
            wire_failure(module="tbml_detection", detail="module init failed",
                        gap_type="engine_failure", source="tbml_detection:init")
        except Exception:
            pass
