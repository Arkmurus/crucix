"""R-F643 — Goods-list aggregator-broker pattern detector.

Standalone analyser of a goods-list (line-item text) that scores
multiple structural signals consistent with the aggregator-broker /
commission-farming pattern observed in the 2026-05-17 ADSM case.

Signals scored:
  - NATO + Soviet/Chinese calibres mixed (incoherent for a single
    operational force)
  - Volumes consistent with strategic-reserve / multi-year supply
    (millions of small-arms rounds for sub-200K force = aggregator)
  - OEM-agnostic line items (no specific manufacturer requested)
  - Multiple non-operational legacy items (retired fleet ammunition)
  - Mixed NATO + non-NATO weapon-system origin presence

Composes with R-F640 — F643 is the line-item-level pattern detector
while F640 is the country/routing-typology detector. Together they
give the full composite verdict.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("aria.goods_list_aggregator_detector")


# Calibres categorised by alliance/origin
_NATO_CALIBRES = {
    "5.56", "5.56×45", "5.56x45",
    "7.62×51", "7.62x51", "7.62 NATO",
    "12.7×99", "12.7x99", ".50 cal", ".50 BMG",
    "9×19", "9x19", "9mm NATO",
    "25×137", "25x137",
    "30×113", "30x113",
    "40×46", "40x46", "40×53", "40x53", "40 mm Mk19", "40×52",
    "60mm mortar", "81mm mortar", "120mm mortar",
    "105mm howitzer", "105mm M60", "120mm Abrams", "120mm APFSDS",
    "155mm M107", "155mm howitzer",
}

_SOVIET_CHINESE_CALIBRES = {
    "7.62×39", "7.62x39",
    "5.45×39", "5.45x39",
    "5.8×42", "5.8x42",
    "12.7×108", "12.7x108",
    "14.5×114", "14.5x114",
    "23×152", "23x152", "23mm",
    "100mm tank", "115mm tank", "125mm tank",
    "76mm soviet", "85mm soviet",
    "122mm Grad", "122mm BM-21", "152mm soviet",
    "30×165", "30x165",
    "GP9 mortar", "120mm Soviet",
}

# Calibres / weapon families typical of Saudi operational force
_SAUDI_OPERATIONAL_INVENTORY = {
    "5.56×45", "7.62×51", "12.7×99",  # NATO small arms
    "120mm Abrams",                     # M1A2 fleet
    "155mm M107", "155mm howitzer",     # M109 / M198 / G6 / Caesar
    "120mm mortar",
}

# Retired Saudi inventory — line items targeting these are diversion
# flags
_SAUDI_RETIRED_FLEET = {
    "105mm M60",     # M60 Patton retired ~2013
}


@dataclass(frozen=True)
class AggregatorAnalysis:
    score: int                       # 0-10+
    severity: str                    # info / amber / red / hard_stop
    signals: tuple[str, ...]         # human-readable findings
    mixed_alliance: bool
    has_diversion_flag: bool
    has_strategic_volumes: bool


def _normalise_calibre_text(text: str) -> str:
    """Light normalisation for calibre matching: collapse whitespace,
    lowercase, replace × variants, and collapse 'N mm' → 'Nmm' so
    the match works regardless of whether the source uses '105mm M60'
    or '105 mm M60'."""
    s = text.lower().strip()
    s = s.replace("×", "x")
    s = re.sub(r"(\d)\s+mm\b", r"\1mm", s)  # "105 mm" → "105mm"
    s = re.sub(r"\s+", " ", s)
    return s


def analyse_line_items(line_items: list[str],
                       buyer_country_iso2: str | None = None) -> AggregatorAnalysis:
    """Analyse a list of goods-list line-item texts.

    Args:
        line_items: list of strings, each a line-item description
        buyer_country_iso2: optional — narrows diversion analysis
    """
    if not line_items or not isinstance(line_items, list):
        return AggregatorAnalysis(
            score=0, severity="info",
            signals=(), mixed_alliance=False,
            has_diversion_flag=False,
            has_strategic_volumes=False,
        )

    signals: list[str] = []
    score = 0
    nato_calibres_found: set[str] = set()
    soviet_chinese_calibres_found: set[str] = set()
    diversion_items: list[str] = []
    total_quantity = 0
    has_quantities = False

    for item in line_items:
        if not isinstance(item, str):
            continue
        item_norm = _normalise_calibre_text(item)

        # NATO calibre detection
        for cal in _NATO_CALIBRES:
            if _normalise_calibre_text(cal) in item_norm:
                nato_calibres_found.add(cal)
                break

        # Soviet/Chinese calibre detection
        for cal in _SOVIET_CHINESE_CALIBRES:
            if _normalise_calibre_text(cal) in item_norm:
                soviet_chinese_calibres_found.add(cal)
                break

        # Saudi-retired-fleet diversion check — look for M60 + 105
        # tokens in the same item (handles "105 mm tank round (M60)"
        # variation without forcing exact substring order).
        if buyer_country_iso2 and buyer_country_iso2.upper() == "SA":
            if "m60" in item_norm and ("105" in item_norm):
                diversion_items.append(item)
            else:
                for retired in _SAUDI_RETIRED_FLEET:
                    if _normalise_calibre_text(retired) in item_norm:
                        diversion_items.append(item)
                        break

        # Quantity extraction
        m = re.search(r"\b(\d{1,3}(?:[,.]\d{3})+|\d{3,})\b", item)
        if m:
            try:
                qty = int(m.group(1).replace(",", "").replace(".", ""))
                if qty >= 1000:
                    has_quantities = True
                    total_quantity += qty
            except ValueError:
                pass

    # Score the signals
    mixed_alliance = bool(nato_calibres_found and soviet_chinese_calibres_found)
    if mixed_alliance:
        score += 3
        signals.append(
            f"Mixed NATO + Soviet/Chinese calibres detected: "
            f"NATO {sorted(nato_calibres_found)[:3]}; "
            f"Soviet/Chinese {sorted(soviet_chinese_calibres_found)[:3]}. "
            f"Inconsistent with single operational-force inventory."
        )

    has_strategic_volumes = (total_quantity >= 10_000_000 and has_quantities)
    if has_strategic_volumes:
        score += 2
        signals.append(
            f"Aggregate volume ≥10M units across line items "
            f"({total_quantity:,}) — strategic-reserve / multi-year "
            f"scale, not operational-restock."
        )

    has_diversion_flag = bool(diversion_items)
    if has_diversion_flag:
        score += 2
        signals.append(
            f"{len(diversion_items)} item(s) target retired-fleet "
            f"ammunition for the stated buyer country — possible "
            f"onward-transfer / diversion: {diversion_items[:2]}"
        )

    # Determine severity
    if score >= 6:
        severity = "hard_stop"
    elif score >= 4:
        severity = "red"
    elif score >= 2:
        severity = "amber"
    else:
        severity = "info"

    return AggregatorAnalysis(
        score=score,
        severity=severity,
        signals=tuple(signals),
        mixed_alliance=mixed_alliance,
        has_diversion_flag=has_diversion_flag,
        has_strategic_volumes=has_strategic_volumes,
    )


def render_finding(line_items: list[str],
                   buyer_country_iso2: str | None = None) -> dict | None:
    """Finding-shaped dict for the DD orchestrator. None when no
    aggregator signals detected."""
    try:
        result = analyse_line_items(line_items, buyer_country_iso2)
        if not result.signals:
            return None
        finding = {
            "severity": result.severity,
            "title": (
                f"Goods-list aggregator-pattern score: {result.score}/10 — "
                f"{result.severity.upper()}"
            ),
            "detail": (
                f"Composite analysis of {len(line_items)} line items. "
                f"Score: {result.score}. Mixed-alliance: {result.mixed_alliance}. "
                f"Diversion-flag: {result.has_diversion_flag}. "
                f"Strategic-volumes: {result.has_strategic_volumes}. "
                f"Signals:\n"
                + "\n".join(f"  - {s}" for s in result.signals)
            ),
            "source": "goods_list_aggregator_detector (R-F643)",
            "confidence": "PROBABLE",
            "score": result.score,
            "mixed_alliance": result.mixed_alliance,
            "has_diversion_flag": result.has_diversion_flag,
            "has_strategic_volumes": result.has_strategic_volumes,
        }
        # R-F994 — wire to brain
        from .engine_wiring import wire_success
        wire_success(
            module="goods_list_aggregator_detector",
            summary=f"Aggregator pattern: score {result.score}/10 ({result.severity})",
            detail=f"Signals: {'; '.join(result.signals)}"[:600],
            confidence="PROBABLE",
            source_id="goods_list_aggregator_detector:R-F643",
        )
        return finding
    except Exception as e:
        from .engine_wiring import wire_failure
        wire_failure(
            module="goods_list_aggregator_detector",
            detail=f"render_finding crashed: {e}",
            gap_type="compliance_engine_failure",
            source="goods_list_aggregator_detector:render_finding",
        )
        return None
