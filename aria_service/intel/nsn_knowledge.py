"""
ARIA NSN (NATO Stock Number) Knowledge Module — v2.1 Schema Integration

PURPOSE:
  Parse, decode, and explain NATO Stock Numbers. NSN is the universal
  identification system for every defence logistics item across NATO nations.
  Format: XXXX-XX-XXX-XXXX (13 digits)
    Group 1 (4 digits): NATO Supply Classification (NSC/FSC)
    Group 2 (2 digits): NATO Country Code (NCC) — codifying nation
    Group 3 (7 digits): NIIN (NATO Item Identification Number) — unique item

  Example: 5820-01-234-5678
    5820 = Radio/telecom equipment (FSC Group 58, Class 20)
    01 = United States
    234-5678 = Unique item number

COVERAGE:
  - NSN structure decoding (FSC group → category → class)
  - NATO Country Code (NCC) lookup
  - NCAGE (NATO Commercial and Government Entity) code explanation
  - CPV (Common Procurement Vocabulary) cross-reference
  - Item Name Code (INC) interpretation
  - Demilitarization code meaning
  - NIIN status code interpretation

SOURCE: nsnSchema-2.1 (NATO NMCRL XML Schema, April 2025)
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("aria.intel.nsn")


# ── FSC (Federal Supply Classification) Groups ──────────────────────────────
# First 2 digits of NSN. 78 groups covering all military supply categories.

FSC_GROUPS = {
    "10": "Weapons",
    "11": "Nuclear Ordnance",
    "12": "Fire Control Equipment",
    "13": "Ammunition & Explosives",
    "14": "Guided Missiles",
    "15": "Aircraft & Airframe Structural Components",
    "16": "Aircraft Components & Accessories",
    "17": "Aircraft Launch/Landing/Ground Handling Equipment",
    "18": "Space Vehicles",
    "19": "Ships, Small Craft, Pontoons & Floating Docks",
    "20": "Ship & Marine Equipment",
    "22": "Railway Equipment",
    "23": "Ground Effect Vehicles, Motor Vehicles, Trailers & Cycles",
    "24": "Tractors",
    "25": "Vehicular Equipment Components",
    "26": "Tyres & Tubes",
    "28": "Engines, Turbines & Components",
    "29": "Engine Accessories",
    "30": "Mechanical Power Transmission Equipment",
    "31": "Bearings",
    "32": "Woodworking Machinery & Equipment",
    "34": "Metalworking Machinery",
    "35": "Service & Trade Equipment",
    "36": "Special Industry Machinery",
    "37": "Agricultural Machinery & Equipment",
    "38": "Construction, Mining, Excavating & Highway Equipment",
    "39": "Materials Handling Equipment",
    "40": "Rope, Cable, Chain & Fittings",
    "41": "Refrigeration, Air Conditioning & Heating Equipment",
    "42": "Fire Fighting/Rescue/Safety Equipment",
    "43": "Pumps & Compressors",
    "44": "Furnace, Steam Plant & Drying Equipment",
    "45": "Plumbing, Heating & Sanitary Equipment",
    "46": "Water Purification & Sewage Treatment Equipment",
    "47": "Pipe, Tubing, Hose & Fittings",
    "48": "Valves",
    "49": "Maintenance & Repair Shop Equipment",
    "51": "Hand Tools",
    "52": "Measuring Tools",
    "53": "Hardware & Abrasives",
    "54": "Prefabricated Structures & Scaffolding",
    "55": "Lumber, Millwork, Plywood & Veneer",
    "56": "Construction & Building Materials",
    "58": "Communication, Detection & Coherent Radiation Equipment",
    "59": "Electrical & Electronic Equipment Components",
    "60": "Fibre Optics",
    "61": "Electric Wire, Power & Distribution Equipment",
    "62": "Lighting Fixtures & Lamps",
    "63": "Alarm, Signal & Security Detection Systems",
    "65": "Medical, Dental & Veterinary Equipment",
    "66": "Instruments & Laboratory Equipment",
    "67": "Photographic Equipment",
    "68": "Chemicals & Chemical Products",
    "69": "Training Aids & Devices",
    "70": "General Purpose ADP Equipment, Software & Supplies",
    "71": "Furniture",
    "72": "Household & Commercial Furnishings",
    "73": "Food Preparation & Serving Equipment",
    "74": "Office Machines & Data Processing Equipment",
    "75": "Office Supplies & Devices",
    "76": "Books, Maps & Publications",
    "77": "Musical Instruments, Phonographs & Home Radios",
    "78": "Recreational & Athletic Equipment",
    "79": "Cleaning Equipment & Supplies",
    "80": "Brushes, Paints, Sealers & Adhesives",
    "81": "Containers, Packaging & Packing Supplies",
    "83": "Textiles, Leather & Furs",
    "84": "Clothing, Individual Equipment & Insignia",
    "85": "Toiletries",
    "87": "Agricultural Supplies",
    "88": "Live Animals",
    "89": "Subsistence (Food)",
    "91": "Fuels, Lubricants, Oils & Waxes",
    "93": "Nonmetallic Fabricated Materials",
    "94": "Nonmetallic Crude Materials",
    "95": "Metal Bars, Sheets & Shapes",
    "96": "Ores, Minerals & Primary Products",
    "99": "Miscellaneous",
}

# ── NATO Country Codes (NCC) — 2-digit codifying nation ─────────────────────

NATO_COUNTRY_CODES = {
    "00": "United States (Army)",
    "01": "United States",
    "10": "Canada (obsolete)",
    "11": "NATO Maintenance & Supply Agency (NAMSA/NSPA)",
    "12": "Germany",
    "13": "Belgium",
    "14": "France",
    "15": "Italy",
    "16": "Netherlands",
    "17": "Norway",
    "18": "Portugal",
    "19": "Spain (obsolete)",
    "20": "Canada",
    "21": "United Kingdom",
    "22": "Denmark",
    "23": "Greece",
    "24": "Iceland (obsolete)",
    "25": "Turkey",
    "26": "Spain",
    "27": "Luxembourg",
    "28": "Czech Republic",
    "29": "Hungary",
    "30": "Poland",
    "31": "Estonia",
    "32": "Latvia",
    "33": "Lithuania",
    "34": "Slovenia",
    "35": "Slovakia",
    "36": "Albania",
    "37": "Croatia",
    "38": "Montenegro",
    "39": "North Macedonia",
    "40": "Australia",
    "41": "New Zealand",
    "42": "South Korea",
    "43": "Japan",
    "44": "Austria",
    "45": "Finland",
    "46": "Sweden",
    "47": "Romania",
    "48": "Bulgaria",
    "66": "Israel",
    "98": "NATO (organisation)",
    "99": "Non-NATO / Other",
}

# ── NIIN Status Codes ────────────────────────────────────────────────────────

NIIN_STATUS = {
    "0": "Active — item in current use",
    "1": "Provisionally active — awaiting full cataloguing",
    "2": "Cancelled — replaced by another NSN",
    "3": "Cancelled — no replacement",
    "4": "Inactive — retained for reference",
    "5": "Under review",
    "6": "Suspended",
}

# ── Demilitarization Codes ───────────────────────────────────────────────────

DEMIL_CODES = {
    "A": "Non-MLI — no demilitarization required",
    "B": "Demilitarization by mutilation — requires physical destruction",
    "C": "Controlled item — requires demilitarization and export restrictions",
    "D": "DoD-controlled — demilitarization required, special instructions",
    "E": "ITAR-controlled — International Traffic in Arms Regulations apply",
    "F": "Classified item — security destruction required",
    "G": "Munitions List Item — full demilitarization mandatory",
    "P": "Precautionary measures — special handling in disposal",
    "Q": "Questionable — review before release",
}

# ── NSN regex pattern ────────────────────────────────────────────────────────

NSN_PATTERN = re.compile(
    r"(?P<fsc>\d{4})[\s\-]?(?P<ncc>\d{2})[\s\-]?(?P<niin_a>\d{3})[\s\-]?(?P<niin_b>\d{4})"
)

NIIN_PATTERN = re.compile(r"(?P<ncc>\d{2})[\s\-]?(?P<niin_a>\d{3})[\s\-]?(?P<niin_b>\d{4})")


# ── Public API ───────────────────────────────────────────────────────────────

def decode_nsn(nsn_string: str) -> dict:
    """Decode a NATO Stock Number into its components.

    Accepts formats: 5820-01-234-5678, 5820012345678, 5820 01 234 5678

    Returns:
        {
            "nsn": "5820-01-234-5678",
            "valid": True,
            "fsc_group": "58",
            "fsc_class": "5820",
            "fsc_description": "Communication, Detection & Coherent Radiation Equipment",
            "ncc": "01",
            "codifying_nation": "United States",
            "niin": "012345678",
            "niin_formatted": "01-234-5678",
            "explanation": "Radio/telecom equipment codified by United States"
        }
    """
    if not nsn_string:
        return {"valid": False, "error": "Empty input"}

    # Strip whitespace and common prefixes
    clean = nsn_string.strip().upper().replace("NSN", "").replace(":", "").strip()

    m = NSN_PATTERN.search(clean)
    if not m:
        # Try as bare NIIN (9 digits)
        niin_m = NIIN_PATTERN.search(clean)
        if niin_m:
            ncc = niin_m.group("ncc")
            niin = ncc + niin_m.group("niin_a") + niin_m.group("niin_b")
            return {
                "valid": True,
                "type": "NIIN_only",
                "niin": niin,
                "niin_formatted": f"{ncc}-{niin_m.group('niin_a')}-{niin_m.group('niin_b')}",
                "ncc": ncc,
                "codifying_nation": NATO_COUNTRY_CODES.get(ncc, f"Unknown (NCC {ncc})"),
                "note": "NIIN only — no FSC/NSC classification available. Provide full 13-digit NSN for complete decode.",
            }
        return {"valid": False, "error": f"Cannot parse NSN from '{nsn_string}'. Expected format: XXXX-XX-XXX-XXXX (13 digits)."}

    fsc = m.group("fsc")
    ncc = m.group("ncc")
    niin_a = m.group("niin_a")
    niin_b = m.group("niin_b")

    fsc_group = fsc[:2]
    niin = ncc + niin_a + niin_b

    group_desc = FSC_GROUPS.get(fsc_group, f"Unknown FSC Group {fsc_group}")
    nation = NATO_COUNTRY_CODES.get(ncc, f"Unknown (NCC {ncc})")

    return {
        "valid": True,
        "nsn": f"{fsc}-{ncc}-{niin_a}-{niin_b}",
        "fsc_group": fsc_group,
        "fsc_class": fsc,
        "fsc_description": group_desc,
        "ncc": ncc,
        "codifying_nation": nation,
        "niin": niin,
        "niin_formatted": f"{ncc}-{niin_a}-{niin_b}",
        "explanation": f"{group_desc} codified by {nation}",
    }


def explain_nsn_structure() -> str:
    """Return a human-readable explanation of the NSN system."""
    return """NATO STOCK NUMBER (NSN) — Structure Guide

An NSN is a 13-digit code that uniquely identifies every item in the NATO
logistics system. Format: XXXX-XX-XXX-XXXX

  ┌─────┐ ┌──┐ ┌───────┐
  │ FSC │ │NC│ │ NIIN  │
  │4730 │-│01│-│234-5678│
  └─────┘ └──┘ └───────┘
     │      │      │
     │      │      └─ NATO Item ID Number (unique to this item)
     │      └──────── NATO Country Code (codifying nation)
     └─────────────── Federal Supply Classification (item category)

FSC (first 4 digits):
  First 2 = Supply Group (e.g., 58 = Communications)
  Last 2 = Supply Class (e.g., 5820 = Radio equipment)

NCC (digits 5-6):
  01 = USA, 12 = Germany, 14 = France, 21 = UK, 25 = Turkey, etc.

NIIN (last 9 digits):
  Unique identifier assigned by the codifying nation.
  Same NIIN = same item worldwide across all NATO nations.

Key cross-references:
  NCAGE = NATO supplier/manufacturer code (5 characters)
  CPV = EU Common Procurement Vocabulary code
  HS = Harmonized System (customs classification)
  DEMIL = Demilitarization requirement code

NMCRL = NATO Master Catalogue of References for Logistics
  The central database of all NSNs. Managed by NSPA (NATO Support Agency).
  Schema version: 2.1 (April 2025)
"""


def get_nsn_context(query: str) -> str:
    """Return NSN-relevant context for ARIA prompts.

    If the query contains an NSN or mentions cataloguing/NSN/NIIN/NCAGE,
    inject relevant knowledge into the prompt.
    """
    if not query:
        return ""

    lower = query.lower()

    # Check for NSN in query
    m = NSN_PATTERN.search(query)
    if m:
        decoded = decode_nsn(query)
        if decoded.get("valid"):
            parts = [
                "[NSN DECODED]",
                f"NSN: {decoded.get('nsn', '')}",
                f"Category: {decoded.get('fsc_description', '')} (FSC {decoded.get('fsc_class', '')})",
                f"Codified by: {decoded.get('codifying_nation', '')} (NCC {decoded.get('ncc', '')})",
                f"NIIN: {decoded.get('niin_formatted', '')}",
            ]
            return "\n".join(parts) + "\n"

    # Check for NSN-related keywords
    nsn_kw = ["nsn", "nato stock", "niin", "ncage", "nmcrl", "fsc code",
              "supply classification", "cataloguing", "nato catalogue",
              "demilitarization code", "demil code", "item name code"]
    if any(kw in lower for kw in nsn_kw):
        return explain_nsn_structure()

    return ""


def lookup_fsc(code: str) -> Optional[str]:
    """Look up an FSC group or class code."""
    code = code.strip()
    if len(code) == 2:
        return FSC_GROUPS.get(code)
    if len(code) == 4:
        return FSC_GROUPS.get(code[:2])
    return None


def lookup_country(ncc: str) -> Optional[str]:
    """Look up a NATO Country Code."""
    return NATO_COUNTRY_CODES.get(ncc.strip())


def lookup_demil(code: str) -> Optional[str]:
    """Look up a demilitarization code."""
    return DEMIL_CODES.get(code.strip().upper())


def lookup_niin_status(code: str) -> Optional[str]:
    """Look up a NIIN status code."""
    return NIIN_STATUS.get(code.strip())
