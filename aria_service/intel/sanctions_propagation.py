"""Product-origin → sanctions-list → payment-rail propagation.

Extracted from the NAK / SERBAN / F3 learnings 2026-04-17: the
FK-3000 counter-drone deal involves NORINCO (on OFAC NS-CMIC) and
potential USD routing via a Florida LLC, creating a secondary-sanctions
chain that ARIA's chain_correlator did not model (that correlator only
covers geo → procurement → relationships, not manufacturer → sanctions
→ payment).

This module answers: "Given a product or OEM I'm dealing with, which
sanctions regimes does it trigger, and through which payment rails does
that risk propagate back to me as the exporter/broker?"

Design:
  - Seed list of OEMs + parent-group mappings → authoritative sanctions
    list references. Every entry cites the list, the date of the current
    designation (or "ongoing" where the list is live), and the effect.
  - Payment-rail propagation: if a sanctioning jurisdiction is involved
    AND the payment currency maps to that jurisdiction's clearing
    infrastructure, the broker carries secondary-sanctions exposure
    even if the broker is not itself in the sanctioning jurisdiction.
  - No live HTTP calls. This module is a reasoning/lookup layer — the
    actual sanctions-screen (OpenSanctions) is a different pipeline.
    We do NOT duplicate that; we wrap and interpret its output.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("aria.intel.sanctions_propagation")


# ═══════════════════════════════════════════════════════════════════════
# Sanctions regime references (for operator citations)
# ═══════════════════════════════════════════════════════════════════════

_REGIMES = {
    "OFAC_SDN":       {"auth": "US Treasury OFAC",         "scope": "US persons + USD clearing + US-nexus",  "currency": "USD"},
    "OFAC_NSCMIC":    {"auth": "US Treasury OFAC",         "scope": "US investor prohibition (NS-CMIC list)", "currency": "USD"},
    "BIS_ENTITY":     {"auth": "US Commerce / BIS",        "scope": "Export of US-origin / 25%+ US content items requires licence", "currency": "USD"},
    "UK_OFSI":        {"auth": "UK HM Treasury OFSI",      "scope": "UK persons + GBP clearing + UK-nexus",  "currency": "GBP"},
    "EU_CONSOLIDATED":{"auth": "EU Council",               "scope": "EU persons + EUR clearing + EU-nexus",  "currency": "EUR"},
    "UN_CONSOLIDATED":{"auth": "UN Security Council",      "scope": "All UN member states (hard universal)", "currency": "*"},
    "CAATSA_231":     {"auth": "US CAATSA §231",           "scope": "Secondary sanctions for significant transactions with Russian defence sector", "currency": "USD"},
}


# ═══════════════════════════════════════════════════════════════════════
# Seed: OEM / product → applicable sanctions regimes
# ═══════════════════════════════════════════════════════════════════════
#
# This is an opinionated seed. Each entry reflects Arkmurus's public
# understanding as of 2026-04-17; operators should verify live status on
# the OFAC/BIS/OFSI/EU sanctions list at the time of any deal. Dates
# omitted where the listing is live and subject to change.

@dataclass(frozen=True)
class OEMSanctionsEntry:
    oem:        str
    aliases:    tuple[str, ...]
    country:    str
    regimes:    tuple[str, ...]
    products:   tuple[str, ...]
    effect:     str
    notes:      str = ""


_OEM_SANCTIONS: tuple[OEMSanctionsEntry, ...] = (
    # ── China: NS-CMIC / SDN / BIS Entity List ──
    OEMSanctionsEntry(
        oem="NORINCO",
        aliases=("China North Industries Corporation", "CNIC", "北方工业"),
        country="CN",
        regimes=("OFAC_NSCMIC", "BIS_ENTITY", "OFAC_SDN"),
        products=("FK-3000", "FK-2000", "FK-1000", "SR-5 MLRS", "VT-4 MBT", "CS/SA2 SPAAG", "tank", "artillery", "mlrs"),
        effect="US persons prohibited from investment (NS-CMIC). Export of US-origin goods/tech to NORINCO requires BIS licence (presumption of denial). Secondary-sanctions exposure for non-US persons transacting in USD.",
        notes="Parent group; most Chinese land-systems exports touch NORINCO.",
    ),
    OEMSanctionsEntry(
        oem="CETC",
        aliases=("China Electronics Technology Group", "中国电子科技集团"),
        country="CN",
        regimes=("OFAC_NSCMIC", "BIS_ENTITY"),
        products=("radar", "C4ISR", "electronic warfare", "ecm", "elint"),
        effect="NS-CMIC-listed parent group. BIS Entity List coverage across most CETC subsidiaries.",
    ),
    OEMSanctionsEntry(
        oem="AVIC",
        aliases=("Aviation Industry Corporation of China", "中国航空工业集团"),
        country="CN",
        regimes=("OFAC_NSCMIC", "BIS_ENTITY"),
        products=("j-10", "j-20", "fc-31", "y-20", "z-10", "z-20", "wing loong", "ch-5", "ch-4"),
        effect="NS-CMIC-listed parent group. Civil-military fusion — any AVIC subsidiary (civilian or military) carries the designation.",
    ),
    OEMSanctionsEntry(
        oem="CASC",
        aliases=("China Aerospace Science and Technology Corporation",),
        country="CN",
        regimes=("OFAC_NSCMIC", "BIS_ENTITY"),
        products=("df-17", "df-21", "df-26", "dong feng", "space launch"),
        effect="NS-CMIC + BIS Entity. Ballistic missile / hypersonic systems.",
    ),
    OEMSanctionsEntry(
        oem="Poly Technologies",
        aliases=("China Poly Group",),
        country="CN",
        regimes=("OFAC_SDN", "BIS_ENTITY"),
        products=("small arms", "man-pads", "fn-6", "qw-1", "qw-2"),
        effect="OFAC-designated on multiple occasions for proliferation to Iran. BIS Entity List.",
    ),
    # ── Russia: SDN + EU + UK + CAATSA ──
    OEMSanctionsEntry(
        oem="Rosoboronexport",
        aliases=("ROE", "Рособоронэкспорт"),
        country="RU",
        regimes=("OFAC_SDN", "UK_OFSI", "EU_CONSOLIDATED", "CAATSA_231"),
        products=("*",),
        effect="Blocked under all Western regimes. Any significant transaction triggers CAATSA §231 secondary sanctions — including for non-US/UK/EU brokers if USD/GBP/EUR rails are used.",
        notes="Umbrella Russian state arms exporter. Hard NO-GO.",
    ),
    OEMSanctionsEntry(
        oem="Rostec",
        aliases=("Ростех",),
        country="RU",
        regimes=("OFAC_SDN", "UK_OFSI", "EU_CONSOLIDATED"),
        products=("kalashnikov", "ak", "almaz-antey", "s-300", "s-400", "pantsir", "sukhoi", "mig"),
        effect="Parent group — blocked. Subsidiaries (Kalashnikov Concern, Almaz-Antey, UAC, United Engine Corp) carry through.",
    ),
    OEMSanctionsEntry(
        oem="Almaz-Antey",
        aliases=("Алмаз-Антей",),
        country="RU",
        regimes=("OFAC_SDN", "UK_OFSI", "EU_CONSOLIDATED"),
        products=("s-300", "s-400", "s-500", "buk", "tor", "pantsir"),
        effect="SDN. All S-family / Pantsir / Tor sales are blocked across US/UK/EU.",
    ),
    # ── Iran ──
    OEMSanctionsEntry(
        oem="Shahed Aviation Industries",
        aliases=("HESA", "HAMESA", "Shahed"),
        country="IR",
        regimes=("OFAC_SDN", "UK_OFSI", "EU_CONSOLIDATED"),
        products=("shahed 129", "shahed 131", "shahed 136", "mohajer-6"),
        effect="SDN + EU. Shahed-136 design transfers to Russia triggered additional US/UK/EU action in 2022-2024.",
    ),
    OEMSanctionsEntry(
        oem="IRGC Aerospace Force",
        aliases=("IRGC-ASF", "Aerospace Force"),
        country="IR",
        regimes=("OFAC_SDN", "UK_OFSI", "EU_CONSOLIDATED", "UN_CONSOLIDATED"),
        products=("ballistic missile", "cruise missile", "mrbm"),
        effect="Comprehensive designations. Any engagement is a hard stop.",
    ),
    # ── DPRK ──
    OEMSanctionsEntry(
        oem="KOMID",
        aliases=("Korea Mining Development Trading Corporation",),
        country="KP",
        regimes=("UN_CONSOLIDATED", "OFAC_SDN", "UK_OFSI", "EU_CONSOLIDATED"),
        products=("*",),
        effect="UN 1718 — universal hard stop. Primary DPRK arms exporter.",
    ),
    # ── Turkey (note: Turkey is a supplier not a sanctioned party, but
    # CAATSA §231 applies to certain Russian-tied Turkish transactions) ──
    # No designation on TR primes (Baykar, ASELSAN, Roketsan, TAI, STM).
)


# Flatten name → entry for fast lookup
_OEM_INDEX: dict[str, OEMSanctionsEntry] = {}
for _e in _OEM_SANCTIONS:
    _OEM_INDEX[_e.oem.lower()] = _e
    for _a in _e.aliases:
        _OEM_INDEX[_a.lower()] = _e


# ═══════════════════════════════════════════════════════════════════════
# Payment-rail → sanctioning-jurisdiction nexus mapping
# ═══════════════════════════════════════════════════════════════════════

_CURRENCY_NEXUS = {
    "USD": ("OFAC_SDN", "OFAC_NSCMIC", "BIS_ENTITY", "CAATSA_231"),
    "GBP": ("UK_OFSI",),
    "EUR": ("EU_CONSOLIDATED",),
    "CHF": ("EU_CONSOLIDATED",),  # CH often mirrors EU; conservative
    "JPY": (),  # Japan has its own lighter regime
    "AED": (),
    "*":   (),
}


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def lookup_oem(name_or_product: str) -> OEMSanctionsEntry | None:
    """Fuzzy-lookup: exact OEM name, alias, or product keyword."""
    if not name_or_product:
        return None
    needle = name_or_product.lower().strip()
    if needle in _OEM_INDEX:
        return _OEM_INDEX[needle]
    # Product keyword scan (case-insensitive)
    for entry in _OEM_SANCTIONS:
        for p in entry.products:
            if p == "*":
                continue
            if p.lower() in needle:
                return entry
    # Substring scan over OEM names
    for entry in _OEM_SANCTIONS:
        if entry.oem.lower() in needle:
            return entry
        for alias in entry.aliases:
            if alias.lower() in needle:
                return entry
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="sanctions_propagation",
        summary="Lookup Oem",
        source_id="sanctions_propagation:R-F996",
    )

    return None


def propagate(
    oem_or_product: str,
    payment_currency: str | None = None,
    broker_jurisdiction: str | None = None,
) -> dict[str, Any]:
    """Given an OEM (or product name) and optional payment currency +
    broker jurisdiction, compute the full sanctions-exposure chain.

    Returns:
        {
          "hit":             bool,
          "oem":             str,              # matched canonical name
          "country":         str,
          "regimes":         [ {code, auth, scope, effect}, ... ],
          "payment_risk":    "*" | "USD exposure" | "EUR exposure" | ...,
          "broker_exposure": "direct" | "secondary" | "none",
          "verdict":         "HARD_STOP" | "HIGH" | "MEDIUM" | "NONE",
          "human_summary":   str,
        }
    """
    out: dict[str, Any] = {
        "hit": False,
        "oem": "",
        "country": "",
        "regimes": [],
        "payment_risk": "none",
        "broker_exposure": "none",
        "verdict": "NONE",
        "human_summary": "",
    }

    entry = lookup_oem(oem_or_product)
    if not entry:
        out["human_summary"] = (
            f"No sanctions-list match on the OEM / product keyword "
            f"'{oem_or_product}'. This is a negative result — verify against "
            f"the live OpenSanctions feed before relying on it."
        )
        return out

    out["hit"] = True
    out["oem"] = entry.oem
    out["country"] = entry.country
    out["regimes"] = [
        {
            "code": r,
            "auth": _REGIMES.get(r, {}).get("auth", ""),
            "scope": _REGIMES.get(r, {}).get("scope", ""),
        }
        for r in entry.regimes
    ]

    # Payment-rail nexus
    cur = (payment_currency or "").upper().strip() if payment_currency else None
    if cur and cur in _CURRENCY_NEXUS:
        nexus_regimes = set(_CURRENCY_NEXUS[cur])
        entry_regimes = set(entry.regimes)
        overlap = nexus_regimes & entry_regimes
        if overlap:
            out["payment_risk"] = f"{cur} rail triggers: {', '.join(sorted(overlap))}"
        else:
            out["payment_risk"] = f"{cur} rail does not overlap with designation — still verify"

    # Broker exposure
    #   direct    = broker is in the sanctioning jurisdiction itself
    #   secondary = broker is NOT in the sanctioning jurisdiction but the
    #               payment currency creates a clearing-bank nexus
    #   none      = no currency or jurisdiction match
    broker = (broker_jurisdiction or "").upper().strip() if broker_jurisdiction else None
    broker_direct_map = {
        "US": {"OFAC_SDN", "OFAC_NSCMIC", "BIS_ENTITY", "CAATSA_231"},
        "GB": {"UK_OFSI"},
        "UK": {"UK_OFSI"},
    }
    eu_countries = {"DE", "FR", "IT", "ES", "PT", "NL", "BE", "LU", "AT",
                    "PL", "SK", "CZ", "HU", "RO", "BG", "SE", "FI", "DK",
                    "IE", "GR", "EE", "LV", "LT", "SI", "HR", "MT", "CY"}
    if broker and broker in eu_countries:
        broker_direct_map[broker] = {"EU_CONSOLIDATED"}

    if broker and broker in broker_direct_map:
        if set(entry.regimes) & broker_direct_map[broker]:
            out["broker_exposure"] = "direct"

    if out["broker_exposure"] == "none" and cur:
        if cur in _CURRENCY_NEXUS and set(_CURRENCY_NEXUS[cur]) & set(entry.regimes):
            out["broker_exposure"] = "secondary"

    # Verdict
    if "UN_CONSOLIDATED" in entry.regimes:
        out["verdict"] = "HARD_STOP"
    elif out["broker_exposure"] == "direct":
        out["verdict"] = "HARD_STOP"
    elif out["broker_exposure"] == "secondary":
        out["verdict"] = "HIGH"
    else:
        out["verdict"] = "MEDIUM"

    # Human summary
    regimes_str = ", ".join(r["code"] for r in out["regimes"])
    parts = [
        f"{entry.oem} ({entry.country}) is designated under: {regimes_str}.",
        entry.effect,
    ]
    if cur:
        parts.append(f"Payment in {cur} → {out['payment_risk']}.")
    if broker:
        parts.append(
            f"Broker jurisdiction {broker} → {out['broker_exposure']} exposure "
            f"({out['verdict']} verdict)."
        )
    if entry.notes:
        parts.append(entry.notes)
    out["human_summary"] = " ".join(parts)

    # Brain-hook: a sanctions-propagation hit is high-value learning signal
    try:
        import asyncio as _asyncio
        from . import brain_hook as _bh
        async def _emit():
            try:
                await _bh.absorb(
                    module="sanctions_propagation",
                    summary=f"{entry.oem} → {regimes_str} | verdict={out['verdict']}",
                    entity_name=entry.oem,
                    success=True,
                    confidence="CONFIRMED",
                    gap_type="sanctions_hit" if out["verdict"] in ("HARD_STOP", "HIGH") else None,
                    gap_detail=out["verdict"] if out["verdict"] in ("HARD_STOP", "HIGH") else None,
                )
            except Exception as e:
                logger.debug("sanctions_propagation brain_hook failed: %s", e)
        try:
            _asyncio.get_running_loop().create_task(_emit())
        except RuntimeError:
            pass  # Sync caller — skip async emit
    except Exception as exc:
        logger.debug("sanctions_propagation brain dispatch failed: %s", exc)

    return out


def summary() -> dict[str, Any]:
    """Capability manifest summary."""
    by_country: dict[str, int] = {}
    for e in _OEM_SANCTIONS:
        by_country[e.country] = by_country.get(e.country, 0) + 1
    return {
        "oems_covered": len(_OEM_SANCTIONS),
        "by_country": by_country,
        "regimes_tracked": list(_REGIMES.keys()),
        "currencies_mapped": [c for c in _CURRENCY_NEXUS if c != "*"],
    }
