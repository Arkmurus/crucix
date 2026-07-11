"""ARIA Country Taxonomy — single source of truth for country normalisation.

Every signal stream in ARIA has, historically, used a different country format:
  tender_monitor           ISO2            "AO"
  conflict_tracker         ISO3            "AGO"
  intel_ledger             free-text       "Angola"
  contact_intelligence     free-text       "angola"
  regional_navigation      region aliases  "sub_saharan_africa_beyond_cplp"

That divergence silently limits correlation: a geopolitical shift logged as
"Angola" in the ledger never joins a tender logged as "AO". This module
exists so every new correlation feature can normalise once and join cleanly.

Public API
──────────
  to_iso2(value: str) -> str | None
      "Angola" | "AGO" | "AO" | "angola"  →  "AO"

  region_members(region: str) -> set[str]
      "ECOWAS"  →  {"NG", "GH", "SN", "CI", ...}

  iso2_to_region(iso2: str) -> str | None
      "AO"  →  "SADC"

Seed sources:
  - tender_monitor.TARGET_MARKETS / _COUNTRY_TO_ISO2 (already curated)
  - ISO3 → ISO2 table below (hand-built for ARIA's target set, not a full
    pycountry dep — keeps the module import-light and offline-safe)
  - Regional blocs curated per aria_global_positioning.md global-parity rule:
    NATO, EU, ECOWAS, SADC, AU, GCC, ASEAN, MERCOSUR, CIS, OSCE, EAC.

Adding a country: add to _ISO3_TO_ISO2 and to any relevant region in REGIONS.
Adding a region: extend REGIONS. Do not add per-module shadow maps.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
from typing import Optional

logger = logging.getLogger("aria.country_taxonomy")

# ── ISO3 → ISO2 (ARIA's covered countries) ────────────────────────────────
# Built to cover every country referenced by tender_monitor.TARGET_MARKETS,
# conflict_tracker.ISO3_MAP, and the regional bloc lists below. If a country
# code appears in exactly one place, add it here AND to the relevant bloc.

_ISO3_TO_ISO2: dict[str, str] = {
    # Africa
    "AGO": "AO", "MOZ": "MZ", "GNB": "GW", "CPV": "CV", "STP": "ST",
    "NGA": "NG", "GHA": "GH", "SEN": "SN", "CIV": "CI", "MLI": "ML",
    "BFA": "BF", "NER": "NE", "TCD": "TD", "CMR": "CM", "GIN": "GN",
    "SLE": "SL", "LBR": "LR", "BEN": "BJ", "TGO": "TG", "GMB": "GM",
    "MRT": "MR",
    "KEN": "KE", "ETH": "ET", "SOM": "SO", "UGA": "UG", "TZA": "TZ",
    "RWA": "RW", "SSD": "SS", "SDN": "SD", "DJI": "DJ", "ERI": "ER",
    "BDI": "BI",
    "ZAF": "ZA", "ZWE": "ZW", "BWA": "BW", "NAM": "NA", "ZMB": "ZM",
    "MWI": "MW", "COD": "CD", "COG": "CG", "GAB": "GA", "SWZ": "SZ",
    "LSO": "LS", "MDG": "MG",
    "EGY": "EG", "LBY": "LY", "TUN": "TN", "DZA": "DZ", "MAR": "MA",
    # MENA
    "IRQ": "IQ", "SYR": "SY", "YEM": "YE", "LBN": "LB", "JOR": "JO",
    "SAU": "SA", "ARE": "AE", "OMN": "OM", "IRN": "IR", "ISR": "IL",
    "QAT": "QA", "BHR": "BH", "KWT": "KW", "PSE": "PS",
    # Asia
    "AFG": "AF", "PAK": "PK", "IND": "IN", "BGD": "BD", "LKA": "LK",
    "MMR": "MM", "IDN": "ID", "PHL": "PH", "VNM": "VN", "THA": "TH",
    "KHM": "KH", "LAO": "LA", "MYS": "MY", "SGP": "SG", "BRN": "BN",
    "TLS": "TL", "NPL": "NP", "BTN": "BT",
    "CHN": "CN", "JPN": "JP", "KOR": "KR", "PRK": "KP", "MNG": "MN",
    "TWN": "TW", "HKG": "HK", "MAC": "MO",
    "KAZ": "KZ", "UZB": "UZ", "TJK": "TJ", "KGZ": "KG", "TKM": "TM",
    # Europe
    "GBR": "GB", "DEU": "DE", "FRA": "FR", "ITA": "IT", "ESP": "ES",
    "PRT": "PT", "NLD": "NL", "BEL": "BE", "LUX": "LU", "IRL": "IE",
    "DNK": "DK", "NOR": "NO", "SWE": "SE", "FIN": "FI", "ISL": "IS",
    "POL": "PL", "CZE": "CZ", "SVK": "SK", "HUN": "HU", "ROU": "RO",
    "BGR": "BG", "SVN": "SI", "HRV": "HR", "SRB": "RS", "BIH": "BA",
    "MKD": "MK", "MNE": "ME", "ALB": "AL", "KOS": "XK",
    "EST": "EE", "LVA": "LV", "LTU": "LT",
    "GRC": "GR", "CYP": "CY", "MLT": "MT", "TUR": "TR",
    "UKR": "UA", "BLR": "BY", "MDA": "MD", "RUS": "RU", "GEO": "GE",
    "ARM": "AM", "AZE": "AZ",
    "CHE": "CH", "AUT": "AT",
    # Americas
    "USA": "US", "CAN": "CA", "MEX": "MX",
    "BRA": "BR", "ARG": "AR", "CHL": "CL", "PER": "PE", "COL": "CO",
    "VEN": "VE", "URY": "UY", "PRY": "PY", "BOL": "BO", "ECU": "EC",
    "GUY": "GY", "SUR": "SR",
    "HTI": "HT", "DOM": "DO", "CUB": "CU", "JAM": "JM", "TTO": "TT",
    "PAN": "PA", "CRI": "CR", "NIC": "NI", "HND": "HN", "SLV": "SV",
    "GTM": "GT", "BLZ": "BZ",
    # Oceania
    "AUS": "AU", "NZL": "NZ", "PNG": "PG", "FJI": "FJ", "SLB": "SB",
    "VUT": "VU",
}

# Reverse (not strictly needed but useful for debugging + future callers)
_ISO2_TO_ISO3: dict[str, str] = {v: k for k, v in _ISO3_TO_ISO2.items()}


# ── Regional blocs ────────────────────────────────────────────────────────
# Curated per aria_global_positioning.md — ARIA must treat every region
# with parity. If a new region becomes relevant, add it here rather than
# introducing a parallel map in another module.

REGIONS: dict[str, set[str]] = {
    # NATO — 32 members (as of 2024 accession of Sweden)
    "NATO": {
        "US", "CA", "GB", "FR", "DE", "IT", "ES", "PT", "NL", "BE", "LU",
        "DK", "NO", "IS", "TR", "GR", "PL", "CZ", "SK", "HU", "RO", "BG",
        "SI", "HR", "EE", "LV", "LT", "AL", "ME", "MK", "FI", "SE",
    },
    # EU — 27 members
    "EU": {
        "DE", "FR", "IT", "ES", "PT", "NL", "BE", "LU", "IE", "DK", "SE",
        "FI", "PL", "CZ", "SK", "HU", "RO", "BG", "SI", "HR", "EE", "LV",
        "LT", "GR", "CY", "MT", "AT",
    },
    # ECOWAS — 15 West African states (incl. suspended/withdrawing)
    "ECOWAS": {
        "NG", "GH", "SN", "CI", "ML", "BF", "NE", "TD", "BJ", "TG",
        "GM", "GN", "GW", "SL", "LR", "CV",
    },
    # SADC — 16 Southern African states
    "SADC": {
        "ZA", "AO", "MZ", "ZW", "BW", "NA", "ZM", "MW", "CD", "CG",
        "SZ", "LS", "MG", "TZ", "MU", "SC",
    },
    # AU — African Union, 55 members (superset; used for continental parity)
    "AU": {
        "DZ", "AO", "BJ", "BW", "BF", "BI", "CM", "CV", "CF", "TD",
        "KM", "CG", "CD", "CI", "DJ", "EG", "GQ", "ER", "SZ", "ET",
        "GA", "GM", "GH", "GN", "GW", "KE", "LS", "LR", "LY", "MG",
        "MW", "ML", "MR", "MU", "MA", "MZ", "NA", "NE", "NG", "RW",
        "ST", "SN", "SC", "SL", "SO", "ZA", "SS", "SD", "TZ", "TG",
        "TN", "UG", "ZM", "ZW",
    },
    # GCC — 6 Arabian Gulf states
    "GCC": {"SA", "AE", "QA", "KW", "BH", "OM"},
    # ASEAN — 10 Southeast Asian states
    "ASEAN": {"ID", "MY", "TH", "PH", "VN", "SG", "MM", "KH", "LA", "BN"},
    # MERCOSUR — core South American bloc (+ Bolivia)
    "MERCOSUR": {"BR", "AR", "UY", "PY", "BO"},
    # CIS — Commonwealth of Independent States (former-Soviet, excl. Baltics)
    "CIS": {"RU", "BY", "KZ", "UZ", "KG", "TJ", "AM", "AZ", "MD"},
    # OSCE — 57 members; for ARIA we use the non-NATO/EU subset that matters
    # operationally (the NATO+EU overlap is already covered).
    "OSCE": {
        "RU", "BY", "UA", "MD", "GE", "AM", "AZ", "RS", "BA", "MK",
        "ME", "AL", "XK", "KZ", "UZ", "TJ", "KG", "TM", "CH", "MN",
    },
    # EAC — East African Community (7 members)
    "EAC": {"KE", "UG", "TZ", "RW", "BI", "SS", "CD"},
}

# Primary region for a given ISO2. Ties broken in this order so that
# "PL" → "NATO" rather than "EU" (defence framing dominates), and
# "AO" → "SADC" rather than "AU" (sub-region is more actionable).
_PRIMARY_PRIORITY = (
    "NATO", "GCC", "ECOWAS", "SADC", "EAC", "ASEAN", "MERCOSUR",
    "EU", "CIS", "OSCE", "AU",
)


# ── Name → ISO2 (lazy-loaded from tender_monitor) ─────────────────────────
# Built on first call so this module loads even if tender_monitor has
# an import-time side effect that would break a cold test.

_NAME_CACHE: Optional[dict[str, str]] = None


def _name_to_iso2_table() -> dict[str, str]:
    global _NAME_CACHE
    if _NAME_CACHE is not None:
        return _NAME_CACHE
    table: dict[str, str] = {}
    try:
        from . import tender_monitor as tm
        for iso2, name in tm.TARGET_MARKETS.items():
            table[name.lower()] = iso2
        # tender_monitor's _COUNTRY_TO_ISO2 already contains alt spellings
        table.update(tm._COUNTRY_TO_ISO2)
    except Exception as e:
        logger.debug("tender_monitor seed unavailable: %s", e)
    # Additional ARIA-specific aliases not covered by tender_monitor
    table.update({
        "democratic republic of the congo": "CD", "dr congo": "CD",
        "republic of the congo": "CG",
        "cabo verde": "CV", "cape verde": "CV",
        "são tomé": "ST", "sao tome": "ST", "são tomé and príncipe": "ST",
        "myanmar": "MM", "burma": "MM",
        "eswatini": "SZ", "swaziland": "SZ",
        "timor-leste": "TL", "east timor": "TL",
        "russian federation": "RU", "russia": "RU",
        "iran": "IR", "islamic republic of iran": "IR",
        "syria": "SY", "syrian arab republic": "SY",
        "bolivia": "BO", "plurinational state of bolivia": "BO",
        "venezuela": "VE", "bolivarian republic of venezuela": "VE",
        "north korea": "KP", "dprk": "KP",
        "south korea": "KR", "republic of korea": "KR",
        "palestine": "PS", "state of palestine": "PS",
        "north macedonia": "MK", "macedonia": "MK",
        "kosovo": "XK",
    })
    _NAME_CACHE = table
    return table


# ── Public API ─────────────────────────────────────────────────────────────

def to_iso2(value: str) -> Optional[str]:
    """Normalise any country representation to an ISO2 code.

    Accepts:
      - ISO2 (case-insensitive):  "AO", "ao"
      - ISO3 (case-insensitive):  "AGO", "ago"
      - English name:             "Angola", "angola"
      - Common alt spellings:     "türkiye", "uk", "usa", "drc"

    Returns None if the input can't be resolved. Never raises.
    """
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    # Name / alias table first — this is what turns "UK"→"GB", "USA"→"US",
    # "drc"→"CD" etc. before the raw length-based checks.
    from_name = _name_to_iso2_table().get(v.lower())
    if from_name:
        return from_name
    upper = v.upper()
    if len(upper) == 2 and upper.isalpha():
        if upper in all_known_iso2():
            return upper
        return None
    if len(upper) == 3 and upper.isalpha():
        return _ISO3_TO_ISO2.get(upper)
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="country_taxonomy",
        summary="To Iso2",
        source_id="country_taxonomy:R-F1001",
    )

    return None


def region_members(region: str) -> set[str]:
    """Return the set of ISO2 codes belonging to a region.

    Returns an empty set for unknown regions. Case-insensitive.
    """
    if not region:
        return set()
    return set(REGIONS.get(region.upper(), set()))


def iso2_to_region(iso2: str) -> Optional[str]:
    """Return the primary region label for an ISO2 code.

    Tie-breaking follows _PRIMARY_PRIORITY so that defence-relevant blocs
    win over broader ones (NATO > EU, SADC > AU, etc.).
    """
    if not iso2:
        return None
    code = iso2.upper()
    for region in _PRIMARY_PRIORITY:
        if code in REGIONS.get(region, set()):
            return region
    return None


def all_known_iso2() -> set[str]:
    """Every ISO2 code this module knows about — useful for parity tests."""
    known: set[str] = set(_ISO2_TO_ISO3.keys())
    for members in REGIONS.values():
        known.update(members)
    return known

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
