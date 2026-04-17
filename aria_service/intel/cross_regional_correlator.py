"""Cross-regional deal correlator.

Extends chain_correlator (which models within-region geo → procurement
→ relationship) with CROSS-region propagation rules: a shift in region A
predicts a procurement window or relationship activation in region B.

These are opinionated rules derived from the Arkmurus commercial thesis:
  - Gulf crisis/escalation → Lusophone + African demand spike for
    Gulf-sourced equipment routed via trusted brokers (Vision 2030
    supply-chain spillover)
  - Sahel coup wave (AES Alliance) → ECOWAS response procurement in
    Nigeria + Ghana + Senegal + Côte d'Ivoire
  - Russia-Ukraine escalation → Central Asia + Caucasus Turkish
    supplier preference + European arms aid reshuffle
  - China-Philippines / SCS escalation → Indonesia + Vietnam +
    Malaysia defence diversification away from Chinese supply
  - Iran-Israel escalation → Gulf + Jordan + Egypt re-arm signals
  - DRC / Great Lakes escalation → Kenya + Rwanda + Uganda regional
    procurement + peacekeeping-mission supply

Unlike chain_correlator (which is time-delayed, 12-18mo), cross-regional
correlations tend to be FASTER (weeks to a few months) because they
represent reactive procurement in response to a crisis.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("aria.intel.cross_regional_correlator")


@dataclass(frozen=True)
class CrossRegionRule:
    code:               str
    trigger_region:     str       # ARIA heat-map region name
    trigger_signals:    tuple[str, ...]  # keywords / event types
    downstream_regions: tuple[str, ...]
    downstream_effect:  str        # short description
    lag_weeks:          int        # typical lag from trigger to downstream procurement
    confidence:         str        # LOW | MEDIUM | HIGH
    rationale:          str


_RULES: tuple[CrossRegionRule, ...] = (
    CrossRegionRule(
        code="GULF_ESCALATION_TO_LUSOPHONE",
        trigger_region="gulf",
        trigger_signals=("yemen escalation", "strait of hormuz", "houthi attack",
                         "idf gulf", "iran-israel", "red sea shipping"),
        downstream_regions=("lusophone", "west_africa"),
        downstream_effect="African MoD urgency for Gulf-routed munitions + UAS. Arkmurus broker role opens.",
        lag_weeks=8,
        confidence="MEDIUM",
        rationale="Gulf crisis elevates Arab / GCC procurement then spills supply-chain capacity to African buyers who are next in the Vision 2030 / EDGE export queue.",
    ),
    CrossRegionRule(
        code="SAHEL_COUP_TO_ECOWAS",
        trigger_region="west_africa",
        trigger_signals=("coup in niger", "coup in mali", "coup in burkina",
                         "junta", "aes alliance", "ecowas sanctions"),
        downstream_regions=("west_africa",),
        downstream_effect="Nigeria + Ghana + Senegal + Côte d'Ivoire procure counter-insurgency + border-security capability.",
        lag_weeks=6,
        confidence="HIGH",
        rationale="ECOWAS stable states respond to AES Alliance instability with immediate border-security procurement. Window typically 4-12 weeks.",
    ),
    CrossRegionRule(
        code="UKRAINE_ESCALATION_TO_CAUCASUS",
        trigger_region="europe",
        trigger_signals=("ukraine offensive", "russia escalation", "kursk",
                         "ukraine aid", "patriot to ukraine"),
        downstream_regions=("central_africa", "balkans", "turkey"),
        downstream_effect="Turkish UAS (TB2/Akıncı) reallocation + European legacy stock transfer to secondary buyers.",
        lag_weeks=12,
        confidence="MEDIUM",
        rationale="Ukraine prioritisation shifts Turkish + European export slots. Secondary markets absorb redirected capacity.",
    ),
    CrossRegionRule(
        code="SCS_ESCALATION_TO_ASEAN",
        trigger_region="southeast_asia",
        trigger_signals=("china philippines", "scarborough shoal", "second thomas shoal",
                         "south china sea", "scs escalation"),
        downstream_regions=("southeast_asia",),
        downstream_effect="Philippines + Vietnam + Indonesia + Malaysia accelerate non-Chinese supply diversification.",
        lag_weeks=16,
        confidence="HIGH",
        rationale="AUKUS-signalled diversification is already a policy; escalation events collapse procurement timelines by 40-60%.",
    ),
    CrossRegionRule(
        code="IRAN_ESCALATION_TO_GULF_PLUS",
        trigger_region="mena",
        trigger_signals=("iran strike", "iran nuclear", "israel iran", "idf iran",
                         "hezbollah iran"),
        downstream_regions=("gulf", "north_africa"),
        downstream_effect="Gulf + Jordan + Egypt rapid re-arm procurement: air-defence + counter-UAS.",
        lag_weeks=4,
        confidence="HIGH",
        rationale="GCC + Jordan respond within weeks to direct Iran-Israel exchanges. Counter-UAS + Patriot-family demand spikes.",
    ),
    CrossRegionRule(
        code="DRC_ESCALATION_TO_EAC",
        trigger_region="central_africa",
        trigger_signals=("m23 offensive", "goma", "rwanda drc", "monusco",
                         "samidrc", "samidrc withdrawal", "eac force"),
        downstream_regions=("central_africa",),
        downstream_effect="Kenya + Rwanda + Uganda + Tanzania reassess border posture + peacekeeping-mission supply.",
        lag_weeks=6,
        confidence="HIGH",
        rationale="Active conflict in eastern DRC triggers regional capability review in EAC states within 4-8 weeks.",
    ),
    CrossRegionRule(
        code="RED_SEA_SHIPPING_TO_MARITIME",
        trigger_region="mena",
        trigger_signals=("red sea", "bab el mandeb", "suez",
                         "houthi maritime", "shipping route attack"),
        downstream_regions=("lusophone", "west_africa", "north_africa"),
        downstream_effect="Coastal-state naval patrol capability demand — OPVs, USVs, maritime radar.",
        lag_weeks=12,
        confidence="MEDIUM",
        rationale="Red Sea disruption elevates African Atlantic + Mediterranean coastal-surveillance procurement.",
    ),
    CrossRegionRule(
        code="TURKEY_EUC_DENIAL_TO_ALTERNATIVES",
        trigger_region="turkey",
        trigger_signals=("ssb denial", "euc refusal", "nihai kullan refused",
                         "turkish export restricted"),
        downstream_regions=("balkans", "gulf"),
        downstream_effect="Buyer pivots to Serbian (Yugoimport) or Gulf (EDGE) alternatives.",
        lag_weeks=8,
        confidence="MEDIUM",
        rationale="SSB EUC denials are rare but when they happen, Arkmurus's Serbia + Gulf lanes become the fallback route.",
    ),
)


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def correlate(trigger_text: str) -> list[dict[str, Any]]:
    """Given free text describing a geopolitical trigger, return a list
    of cross-regional rules that fire, each with predicted downstream
    regions + effect + lag."""
    if not trigger_text or not trigger_text.strip():
        return []
    needle = trigger_text.lower()
    hits: list[dict[str, Any]] = []
    for rule in _RULES:
        for sig in rule.trigger_signals:
            if sig in needle:
                hits.append(_to_dict(rule, matched_signal=sig))
                break
    return hits


def rules_for_trigger_region(region: str) -> list[dict[str, Any]]:
    region = (region or "").lower().strip()
    return [_to_dict(r, matched_signal="") for r in _RULES if r.trigger_region == region]


def get_cross_regional_context(query: str) -> str:
    """Chat-context injection — compact summary of triggered rules."""
    hits = correlate(query)
    if not hits:
        return ""
    lines = ["[CROSS-REGIONAL CORRELATION]"]
    for h in hits[:3]:
        downstream = " + ".join(h["downstream_regions"])
        lines.append(
            f"• {h['code']}: {h['matched_signal']} → {downstream} "
            f"({h['lag_weeks']}w lag, {h['confidence']} confidence). "
            f"{h['downstream_effect']}"
        )
    return "\n".join(lines)


def summary() -> dict[str, Any]:
    by_trigger: dict[str, int] = {}
    for r in _RULES:
        by_trigger[r.trigger_region] = by_trigger.get(r.trigger_region, 0) + 1
    return {
        "rules_total": len(_RULES),
        "by_trigger_region": by_trigger,
        "codes": [r.code for r in _RULES],
    }


def _to_dict(r: CrossRegionRule, matched_signal: str = "") -> dict[str, Any]:
    return {
        "code": r.code,
        "trigger_region": r.trigger_region,
        "matched_signal": matched_signal,
        "downstream_regions": list(r.downstream_regions),
        "downstream_effect": r.downstream_effect,
        "lag_weeks": r.lag_weeks,
        "confidence": r.confidence,
        "rationale": r.rationale,
    }
