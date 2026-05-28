"""Baykar UAS export pipeline tracker.

Seeded snapshot of publicly-disclosed Baykar export contracts, MoUs
and operational deliveries for TB2 (Bayraktar), Akıncı and Kızılelma.
Data comes from open sources (SIPRI TIV, company press releases,
defence journalism) as of early 2026 — operators should verify
specific status + quantity before citing in a mandate.

Export position matters for Arkmurus: Baykar is the largest Turkish
UAS exporter and Arkmurus's primary Lusophone / West-African / Gulf
buyer markets either already operate TB2 or are actively evaluating.
Knowing who has, who's ordered, and who's in negotiation is
structural intelligence for positioning.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("aria.intel.baykar_export_pipeline")


@dataclass(frozen=True)
class BaykarExport:
    customer_iso2:  str
    customer_name:  str
    platform:       str            # TB2 / Akıncı / Kızılelma / Mini
    status:         str            # "delivered" | "contracted" | "mou" | "evaluating" | "reported_interest"
    quantity:       int             # 0 = undisclosed
    first_reported: str             # ISO date (approximate)
    notes:          str = ""


# ═══════════════════════════════════════════════════════════════════════
# Seed (public-source snapshot, 2026-04)
# ═══════════════════════════════════════════════════════════════════════

_EXPORTS: tuple[BaykarExport, ...] = (
    # ── Post-Soviet / Caucasus ──────────────────────────────────────
    BaykarExport("AZ", "Azerbaijan",  "TB2",    "delivered",  0,  "2019-06", "First foreign TB2 operator. Combat-proven 2020 Second Karabakh War."),
    BaykarExport("AZ", "Azerbaijan",  "Akıncı", "contracted", 0,  "2023-04"),
    BaykarExport("UA", "Ukraine",      "TB2",    "delivered",  50, "2019-01", "SIPRI + open-source estimate, mix of pre-2022 contract + wartime transfers."),
    BaykarExport("UA", "Ukraine",      "Akıncı", "evaluating", 0,  "2024-02"),
    BaykarExport("KG", "Kyrgyzstan",   "TB2",    "delivered",  0,  "2022-10"),
    BaykarExport("KZ", "Kazakhstan",   "Akıncı", "contracted", 3,  "2023-05", "3× Akıncı reported in 2023 defence press."),
    BaykarExport("TM", "Turkmenistan", "TB2",    "delivered",  0,  "2022-11"),

    # ── NATO / Europe ────────────────────────────────────────────────
    BaykarExport("PL", "Poland",        "TB2",    "delivered",  24, "2021-05", "First NATO state to procure TB2. 24 airframes + training."),
    BaykarExport("AL", "Albania",       "TB2",    "delivered",  3,  "2023-10"),
    BaykarExport("XK", "Kosovo",        "TB2",    "contracted", 5,  "2023-12"),
    BaykarExport("HR", "Croatia",       "TB2",    "reported_interest", 0, "2023-09"),
    BaykarExport("RO", "Romania",       "TB2",    "contracted", 18, "2023-05", "Via domestic sales channel + local assembly discussion."),

    # ── Gulf / MENA ──────────────────────────────────────────────────
    BaykarExport("AE", "UAE",           "TB2",    "reported_interest", 0, "2023-04"),
    BaykarExport("QA", "Qatar",         "TB2",    "delivered",  0,  "2018-01", "Export precedes current pipeline rhythm."),
    BaykarExport("MA", "Morocco",       "TB2",    "delivered",  19, "2021-06", "19 airframes reported. Post-delivery operational since 2022."),
    BaykarExport("TN", "Tunisia",       "TB2",    "evaluating", 0,  "2024-06"),
    BaykarExport("LY", "Libya (GNU)",   "TB2",    "delivered",  0,  "2019-06", "Used in GNA/LNA conflict 2020. UN Panel of Experts cites transfer."),

    # ── Sub-Saharan Africa ───────────────────────────────────────────
    BaykarExport("NG", "Nigeria",       "TB2",    "delivered",  6,  "2024-10", "Procurement announced late 2024. Post-delivery training 2025."),
    BaykarExport("ET", "Ethiopia",      "TB2",    "delivered",  0,  "2021-09", "UN Panel of Experts reporting on Tigray-related transfers."),
    BaykarExport("SO", "Somalia",       "TB2",    "delivered",  0,  "2022-05"),
    BaykarExport("NE", "Niger",         "TB2",    "delivered",  6,  "2022-08", "Pre-AES Alliance era. Current status under junta uncertain."),
    BaykarExport("TG", "Togo",          "TB2",    "delivered",  0,  "2022-03"),
    BaykarExport("BF", "Burkina Faso",  "TB2",    "delivered",  0,  "2023-08"),
    BaykarExport("ML", "Mali",          "TB2",    "delivered",  0,  "2022-10"),
    BaykarExport("RW", "Rwanda",        "TB2",    "reported_interest", 0, "2023-11"),
    BaykarExport("AO", "Angola",        "TB2",    "evaluating", 0,  "2024-09", "Strategic — aligns with Arkmurus Lusophone focus."),
    BaykarExport("CI", "Côte d'Ivoire", "TB2",    "evaluating", 0,  "2024-12"),

    # ── South / Southeast Asia ───────────────────────────────────────
    BaykarExport("ID", "Indonesia",     "Akıncı", "contracted", 12, "2024-02"),
    BaykarExport("MY", "Malaysia",      "Akıncı", "contracted", 3,  "2024-02"),
    BaykarExport("PK", "Pakistan",      "Akıncı", "reported_interest", 0, "2023-11"),

    # ── LatAm ────────────────────────────────────────────────────────
    BaykarExport("VE", "Venezuela",     "TB2",    "reported_interest", 0, "2024-07", "Compliance red flag — CAATSA + EU sanctions exposure."),
)


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def exports_for_country(iso2: str) -> list[dict[str, Any]]:
    iso2 = (iso2 or "").upper().strip()
    return [_to_dict(e) for e in _EXPORTS if e.customer_iso2 == iso2]


def exports_by_status(status: str) -> list[dict[str, Any]]:
    status = (status or "").lower().strip()
    return [_to_dict(e) for e in _EXPORTS if e.status == status]


def exports_by_platform(platform: str) -> list[dict[str, Any]]:
    p = (platform or "").strip()
    return [_to_dict(e) for e in _EXPORTS if e.platform.lower() == p.lower()]


def get_baykar_context(query: str, max_hits: int = 5) -> str:
    """Fires when query mentions Baykar / TB2 / Akıncı / Kızılelma / Bayraktar."""
    if not query or not query.strip():
        return ""
    q = query.lower()
    triggers = ("baykar", "tb2", "bayraktar", "akinci", "akıncı", "akcini",
                "kizilelma", "kızılelma", "turkish drone", "turkish uas")
    if not any(t in q for t in triggers):
        return ""
    lines = ["[BAYKAR EXPORT PIPELINE]"]
    # If a specific country is in the query, surface that first
    countries = {
        "nigeria": "NG", "angola": "AO", "morocco": "MA", "ukraine": "UA",
        "poland": "PL", "azerbaijan": "AZ", "romania": "RO", "indonesia": "ID",
        "rwanda": "RW", "ethiopia": "ET", "togo": "TG", "niger": "NE",
        "libya": "LY", "mali": "ML", "burkina": "BF",
    }
    specific: list[BaykarExport] = []
    for kw, iso2 in countries.items():
        if kw in q:
            specific.extend([e for e in _EXPORTS if e.customer_iso2 == iso2])
    source = specific if specific else list(_EXPORTS[:max_hits])
    for e in source[:max_hits]:
        qty = f" ×{e.quantity}" if e.quantity else ""
        lines.append(
            f"• {e.customer_name} ({e.customer_iso2}) — {e.platform}{qty} "
            f"[{e.status}, first reported {e.first_reported}]"
        )
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="baykar_export_pipeline",
        summary="Get Baykar Context",
        source_id="baykar_export_pipeline:R-F996",
    )

    return "\n".join(lines)


def summary() -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_platform: dict[str, int] = {}
    by_region: dict[str, int] = {}
    _region_map = {
        "AZ": "caucasus", "UA": "eastern_europe", "KG": "central_asia",
        "KZ": "central_asia", "TM": "central_asia",
        "PL": "nato", "AL": "balkans", "XK": "balkans", "HR": "nato", "RO": "nato",
        "AE": "gulf", "QA": "gulf", "MA": "north_africa", "TN": "north_africa",
        "LY": "north_africa",
        "NG": "west_africa", "ET": "east_africa", "SO": "east_africa",
        "NE": "west_africa", "TG": "west_africa", "BF": "west_africa",
        "ML": "west_africa", "RW": "central_africa",
        "AO": "lusophone", "CI": "west_africa",
        "ID": "southeast_asia", "MY": "southeast_asia", "PK": "south_asia",
        "VE": "latam_non_lusophone",
    }
    for e in _EXPORTS:
        by_status[e.status] = by_status.get(e.status, 0) + 1
        by_platform[e.platform] = by_platform.get(e.platform, 0) + 1
        reg = _region_map.get(e.customer_iso2, "other")
        by_region[reg] = by_region.get(reg, 0) + 1
    return {
        "total_entries": len(_EXPORTS),
        "by_status": by_status,
        "by_platform": by_platform,
        "by_region": by_region,
    }


def _to_dict(e: BaykarExport) -> dict[str, Any]:
    return {
        "customer_iso2": e.customer_iso2,
        "customer_name": e.customer_name,
        "platform": e.platform,
        "status": e.status,
        "quantity": e.quantity,
        "first_reported": e.first_reported,
        "notes": e.notes,
    }
