"""
ARIA Technical Classifier — extract structured defence/security item data from text.

When ARIA reads "Mozambique procuring 200x Panhard CRAB armoured vehicles with
M2 Browning .50 cal machine guns and 5,000 rounds of 12.7×99mm ammunition",
the previous pipeline stored that as plain text. This module pulls out:

  - Calibres (5.56×45mm NATO, 7.62×39mm, .50 BMG, 155mm)
  - Weapon system designations (Panhard CRAB, M2 Browning, TB2, Caesar, K9)
  - Quantities and units (200x, 5,000 rounds)
  - HS codes (9301-9307 — arms and ammunition, 9013 — optical instruments)
  - UK Military List categories (ML1-ML22)
  - US ECCN ratings (heuristic — only for items with strong keyword match)
  - End-use indicators (training, operational, combat, ceremonial)

Each extracted item is a structured dict that downstream modules (compliance
screen, knowledge base, neural memory) can index, query, and reason about.

This is what gives ARIA "technical depth" — without it she's a glorified RSS reader.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("aria.tech")

# ── Calibre patterns ────────────────────────────────────────────────────────
# Three families:
#   1. Metric × case length: 5.56×45mm, 7.62×39mm, 9×19mm
#   2. Metric only: 155mm, 105mm, 120mm
#   3. Imperial: .50 cal, .50 BMG, .308, .223, .357

_CALIBRE_PATTERNS = [
    # Metric with case length
    (re.compile(r"\b(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+)\s*mm\b", re.IGNORECASE),
     lambda m: f"{m.group(1)}×{m.group(2)}mm"),
    # Metric only (artillery, mortars)
    (re.compile(r"\b(\d{2,3})\s*mm\b(?!\s*[xX×])"),
     lambda m: f"{m.group(1)}mm"),
    # Imperial decimal
    (re.compile(r"\b(\.\d{2,3})(?:\s*(?:cal|BMG|Win|ACP|Magnum|Mag))?\b"),
     lambda m: m.group(0).strip()),
    # Specific NATO names
    (re.compile(r"\b(\.50\s*BMG|\.50\s*cal|12\.7×99\s*mm|9×19\s*mm parabellum|7\.62\s*NATO|5\.56\s*NATO)\b", re.IGNORECASE),
     lambda m: m.group(0).strip()),
]

# Calibre → likely category and HS-code prefix
_CALIBRE_DB = {
    "5.56×45mm": {"category": "small_arms_ammo", "hs": "9306.30", "ml": "ML3", "use": "rifle ammunition (NATO standard)"},
    "5.56mm":    {"category": "small_arms_ammo", "hs": "9306.30", "ml": "ML3", "use": "rifle ammunition"},
    "7.62×39mm": {"category": "small_arms_ammo", "hs": "9306.30", "ml": "ML3", "use": "AK platform ammunition (Warsaw Pact)"},
    "7.62×51mm": {"category": "machine_gun_ammo", "hs": "9306.30", "ml": "ML3", "use": "GPMG / DMR ammunition (NATO)"},
    "7.62mm":    {"category": "small_arms_ammo", "hs": "9306.30", "ml": "ML3", "use": "rifle/machine-gun ammunition"},
    "9×19mm":    {"category": "pistol_ammo",     "hs": "9306.30", "ml": "ML3", "use": "9mm pistol/SMG ammunition"},
    "12.7×99mm": {"category": "hmg_ammo",        "hs": "9306.30", "ml": "ML3", "use": "heavy machine gun (.50 BMG NATO)"},
    "12.7mm":    {"category": "hmg_ammo",        "hs": "9306.30", "ml": "ML3", "use": "heavy machine gun ammunition"},
    "14.5mm":    {"category": "hmg_ammo",        "hs": "9306.30", "ml": "ML3", "use": "anti-material rifle / KPV"},
    "20mm":      {"category": "cannon_ammo",     "hs": "9306.21", "ml": "ML3", "use": "autocannon / aircraft gun"},
    "25mm":      {"category": "cannon_ammo",     "hs": "9306.21", "ml": "ML3", "use": "autocannon (Bushmaster)"},
    "30mm":      {"category": "cannon_ammo",     "hs": "9306.21", "ml": "ML3", "use": "autocannon (M230, GAU-8)"},
    "35mm":      {"category": "cannon_ammo",     "hs": "9306.21", "ml": "ML3", "use": "Oerlikon air defence"},
    "40mm":      {"category": "grenade_ammo",    "hs": "9306.21", "ml": "ML3", "use": "grenade launcher / Bofors"},
    "57mm":      {"category": "naval_gun_ammo",  "hs": "9306.21", "ml": "ML3", "use": "Bofors naval gun"},
    "76mm":      {"category": "naval_gun_ammo",  "hs": "9306.21", "ml": "ML3", "use": "Oto Melara naval gun"},
    "105mm":     {"category": "artillery",       "hs": "9306.21", "ml": "ML2", "use": "light artillery / tank (older)"},
    "120mm":     {"category": "tank/mortar",     "hs": "9306.21", "ml": "ML2", "use": "main tank gun / heavy mortar"},
    "152mm":     {"category": "artillery",       "hs": "9306.21", "ml": "ML2", "use": "Soviet heavy artillery"},
    "155mm":     {"category": "artillery",       "hs": "9306.21", "ml": "ML2", "use": "NATO heavy artillery (M777, K9, Caesar)"},
    ".50 BMG":   {"category": "hmg_ammo",        "hs": "9306.30", "ml": "ML3", "use": "12.7×99mm — sniper/HMG"},
    ".50 cal":   {"category": "hmg_ammo",        "hs": "9306.30", "ml": "ML3", "use": "12.7mm equivalent"},
    ".308":      {"category": "rifle_ammo",      "hs": "9306.30", "ml": "ML3", "use": "7.62×51mm civilian/sniper"},
    ".223":      {"category": "rifle_ammo",      "hs": "9306.30", "ml": "ML3", "use": "5.56×45mm civilian"},
    ".338":      {"category": "sniper_ammo",     "hs": "9306.30", "ml": "ML3", "use": "long-range sniper"},
}


# ── Weapon-system designations ──────────────────────────────────────────────
# Maps designation → {category, country, oem, ml_category, eccn_hint}

_WEAPON_SYSTEMS: dict[str, dict] = {
    # Combat aircraft
    "F-35":     {"type": "fighter",   "country": "US", "oem": "Lockheed Martin", "ml": "ML10", "eccn": "9A610", "controlled": True},
    "F-16":     {"type": "fighter",   "country": "US", "oem": "Lockheed Martin", "ml": "ML10", "eccn": "9A610", "controlled": True},
    "F/A-18":   {"type": "fighter",   "country": "US", "oem": "Boeing",          "ml": "ML10", "eccn": "9A610", "controlled": True},
    "Rafale":   {"type": "fighter",   "country": "FR", "oem": "Dassault",        "ml": "ML10", "eccn": "9A610", "controlled": True},
    "Eurofighter Typhoon": {"type": "fighter", "country": "EU", "oem": "Airbus/BAE/Leonardo", "ml": "ML10", "eccn": "9A610", "controlled": True},
    "Gripen":   {"type": "fighter",   "country": "SE", "oem": "Saab",            "ml": "ML10", "eccn": "9A610", "controlled": True},
    "Su-35":    {"type": "fighter",   "country": "RU", "oem": "Sukhoi",          "ml": "ML10", "eccn": "EAR99", "controlled": True, "embargo_risk": "RU sanctions"},
    "Su-30":    {"type": "fighter",   "country": "RU", "oem": "Sukhoi",          "ml": "ML10", "eccn": "EAR99", "controlled": True, "embargo_risk": "RU sanctions"},
    "MiG-29":   {"type": "fighter",   "country": "RU", "oem": "MiG",             "ml": "ML10", "eccn": "EAR99", "controlled": True, "embargo_risk": "RU sanctions"},
    "J-10":     {"type": "fighter",   "country": "CN", "oem": "Chengdu",         "ml": "ML10", "eccn": "EAR99", "controlled": True},
    "JF-17":    {"type": "fighter",   "country": "PK/CN", "oem": "PAC/Chengdu",  "ml": "ML10", "eccn": "EAR99", "controlled": True},
    "FA-50":    {"type": "fighter",   "country": "KR", "oem": "KAI",             "ml": "ML10", "eccn": "9A610", "controlled": True},

    # UAVs
    "Bayraktar TB2": {"type": "uav", "country": "TR", "oem": "Baykar",         "ml": "ML10", "eccn": "9A012", "controlled": True},
    "Bayraktar TB3": {"type": "uav", "country": "TR", "oem": "Baykar",         "ml": "ML10", "eccn": "9A012", "controlled": True},
    "Anka":          {"type": "uav", "country": "TR", "oem": "TAI",            "ml": "ML10", "eccn": "9A012", "controlled": True},
    "Wing Loong":    {"type": "uav", "country": "CN", "oem": "Chengdu",        "ml": "ML10", "eccn": "EAR99", "controlled": True},
    "MQ-9 Reaper":   {"type": "uav", "country": "US", "oem": "General Atomics","ml": "ML10", "eccn": "9A012", "controlled": True, "mtcr": "Cat I"},
    "MQ-1 Predator": {"type": "uav", "country": "US", "oem": "General Atomics","ml": "ML10", "eccn": "9A012", "controlled": True, "mtcr": "Cat I"},
    "Heron":         {"type": "uav", "country": "IL", "oem": "IAI",            "ml": "ML10", "eccn": "9A012", "controlled": True},
    "Hermes 900":    {"type": "uav", "country": "IL", "oem": "Elbit",          "ml": "ML10", "eccn": "9A012", "controlled": True},

    # Tanks & AFVs
    "Leopard 2":  {"type": "tank", "country": "DE", "oem": "KMW",             "ml": "ML6", "eccn": "0A606", "controlled": True},
    "Abrams":     {"type": "tank", "country": "US", "oem": "General Dynamics","ml": "ML6", "eccn": "0A606", "controlled": True},
    "Challenger": {"type": "tank", "country": "UK", "oem": "BAE Systems",     "ml": "ML6", "eccn": "0A606", "controlled": True},
    "K2 Black Panther": {"type": "tank", "country": "KR", "oem": "Hyundai Rotem", "ml": "ML6", "eccn": "0A606", "controlled": True},
    "Altay":      {"type": "tank", "country": "TR", "oem": "BMC",             "ml": "ML6", "eccn": "0A606", "controlled": True},
    "T-90":       {"type": "tank", "country": "RU", "oem": "Uralvagonzavod",  "ml": "ML6", "eccn": "EAR99", "controlled": True, "embargo_risk": "RU sanctions"},

    # Artillery
    "K9 Thunder": {"type": "spg",     "country": "KR", "oem": "Hanwha",       "ml": "ML2", "eccn": "0A606", "controlled": True},
    "Caesar":     {"type": "spg",     "country": "FR", "oem": "Nexter",       "ml": "ML2", "eccn": "0A606", "controlled": True},
    "PzH 2000":   {"type": "spg",     "country": "DE", "oem": "KMW",          "ml": "ML2", "eccn": "0A606", "controlled": True},
    "M777":       {"type": "howitzer","country": "US/UK", "oem": "BAE Systems","ml": "ML2", "eccn": "0A606", "controlled": True},
    "HIMARS":     {"type": "mlrs",    "country": "US", "oem": "Lockheed Martin","ml": "ML4", "eccn": "9A610", "controlled": True},
    "Archer":     {"type": "spg",     "country": "SE", "oem": "BAE Bofors",   "ml": "ML2", "eccn": "0A606", "controlled": True},

    # Missiles & air defence
    "Patriot":     {"type": "sam",  "country": "US", "oem": "Raytheon",       "ml": "ML4", "eccn": "9A610", "controlled": True, "mtcr": "Cat I"},
    "S-400":       {"type": "sam",  "country": "RU", "oem": "Almaz-Antey",    "ml": "ML4", "eccn": "EAR99", "controlled": True, "embargo_risk": "RU sanctions"},
    "S-300":       {"type": "sam",  "country": "RU", "oem": "Almaz-Antey",    "ml": "ML4", "eccn": "EAR99", "controlled": True, "embargo_risk": "RU sanctions"},
    "Iron Dome":   {"type": "sam",  "country": "IL", "oem": "Rafael",         "ml": "ML4", "eccn": "9A610", "controlled": True},
    "NASAMS":      {"type": "sam",  "country": "NO/US", "oem": "Kongsberg/Raytheon", "ml": "ML4", "eccn": "9A610", "controlled": True},
    "IRIS-T":      {"type": "sam",  "country": "DE", "oem": "Diehl",          "ml": "ML4", "eccn": "9A610", "controlled": True},
    "THAAD":       {"type": "sam",  "country": "US", "oem": "Lockheed Martin","ml": "ML4", "eccn": "9A610", "controlled": True, "mtcr": "Cat I"},
    "Javelin":     {"type": "atgm", "country": "US", "oem": "Raytheon/Lockheed", "ml": "ML4", "eccn": "9A610", "controlled": True},
    "Spike":       {"type": "atgm", "country": "IL", "oem": "Rafael",         "ml": "ML4", "eccn": "9A610", "controlled": True},
    "NLAW":        {"type": "atgm", "country": "SE/UK", "oem": "Saab",        "ml": "ML4", "eccn": "9A610", "controlled": True},
    "Stinger":     {"type": "manpads", "country": "US", "oem": "Raytheon",    "ml": "ML4", "eccn": "9A610", "controlled": True, "mtcr": "Cat I"},
    "BrahMos":     {"type": "cruise", "country": "IN/RU", "oem": "BrahMos Aerospace", "ml": "ML4", "eccn": "EAR99", "controlled": True},
    "Storm Shadow":{"type": "cruise", "country": "UK/FR", "oem": "MBDA",      "ml": "ML4", "eccn": "9A610", "controlled": True, "mtcr": "Cat I"},
    "SCALP":       {"type": "cruise", "country": "FR/UK", "oem": "MBDA",      "ml": "ML4", "eccn": "9A610", "controlled": True},
    "Harpoon":     {"type": "ascm", "country": "US", "oem": "Boeing",         "ml": "ML4", "eccn": "9A610", "controlled": True},
    "Exocet":      {"type": "ascm", "country": "FR", "oem": "MBDA",           "ml": "ML4", "eccn": "9A610", "controlled": True},
    "NSM":         {"type": "ascm", "country": "NO", "oem": "Kongsberg",      "ml": "ML4", "eccn": "9A610", "controlled": True},

    # Small arms (just the key ones)
    "M2 Browning": {"type": "hmg",   "country": "US", "oem": "various", "ml": "ML1", "eccn": "0A501", "controlled": True},
    "M16":         {"type": "rifle", "country": "US", "oem": "various", "ml": "ML1", "eccn": "0A501", "controlled": True},
    "M4 Carbine":  {"type": "rifle", "country": "US", "oem": "various", "ml": "ML1", "eccn": "0A501", "controlled": True},
    "AK-47":       {"type": "rifle", "country": "RU", "oem": "various", "ml": "ML1", "eccn": "0A501", "controlled": True},
    "AK-74":       {"type": "rifle", "country": "RU", "oem": "various", "ml": "ML1", "eccn": "0A501", "controlled": True},
    "Galil":       {"type": "rifle", "country": "IL", "oem": "IWI",     "ml": "ML1", "eccn": "0A501", "controlled": True},
    "G36":         {"type": "rifle", "country": "DE", "oem": "Heckler & Koch", "ml": "ML1", "eccn": "0A501", "controlled": True},
}


# ── ML category keyword index (for items not in the explicit DB) ────────────

_ML_KEYWORDS = {
    "ML1":  ["small arms", "rifle", "pistol", "carbine", "smg", "submachine gun", "shotgun"],
    "ML2":  ["howitzer", "artillery", "mortar", "cannon", "field gun", "self-propelled gun", "spg"],
    "ML3":  ["ammunition", "ammo", "round", "cartridge", "fuze", "shell", "warhead"],
    "ML4":  ["missile", "rocket", "torpedo", "atgm", "manpads", "cruise missile", "ballistic"],
    "ML5":  ["fire control", "targeting system", "rangefinder", "laser designator"],
    "ML6":  ["tank", "afv", "ifv", "apc", "mrap", "armoured vehicle", "armored vehicle"],
    "ML7":  ["chemical agent", "biological agent", "riot control", "tear gas"],
    "ML8":  ["explosive", "rdx", "tnt", "c4", "detonator", "propellant"],
    "ML9":  ["warship", "frigate", "corvette", "patrol boat", "submarine", "destroyer", "naval"],
    "ML10": ["fighter aircraft", "helicopter", "uav", "drone", "transport aircraft", "trainer aircraft"],
    "ML11": ["military communication", "crypto equipment", "c4isr", "jammer", "ew system"],
    "ML13": ["body armour", "ballistic vest", "helmet", "ballistic plate"],
    "ML15": ["radar", "thermal imager", "night vision", "electro-optical", "lidar", "sigint"],
    "ML22": ["military training", "advisory services", "simulator"],
}


# ── Quantity & unit extraction ──────────────────────────────────────────────

_QUANTITY_RE = re.compile(
    r"\b(\d{1,3}(?:[,.]?\d{3})*|\d+)\s*[xX×]?\s*"
    r"(units?|rounds?|missiles?|aircraft|tanks?|vehicles?|systems?|guns?|launchers?|drones?|uavs?)?\b",
    re.IGNORECASE,
)


def _extract_quantities(text: str) -> list[dict]:
    """Pull explicit quantities like '200 vehicles' or '5,000 rounds'."""
    out = []
    for m in _QUANTITY_RE.finditer(text):
        qty_str = m.group(1).replace(",", "").replace(".", "")
        unit = (m.group(2) or "").lower().rstrip("s")
        if not unit:
            continue
        try:
            qty = int(qty_str)
        except ValueError:
            continue
        if qty == 0 or qty > 10_000_000:
            continue
        out.append({"quantity": qty, "unit": unit, "raw": m.group(0)})
    # Dedup by (qty, unit) keeping first occurrence
    seen = set()
    deduped = []
    for q in out:
        key = (q["quantity"], q["unit"])
        if key in seen: continue
        seen.add(key)
        deduped.append(q)
    return deduped[:20]


# ── Public extraction API ───────────────────────────────────────────────────

def classify_text(text: str) -> dict:
    """Extract structured defence/security item data from free text.

    Returns:
        {
            calibres:    [{calibre, category, hs, ml, use}],
            systems:     [{designation, type, country, oem, ml, eccn, controlled}],
            ml_categories: ["ML1", "ML4", ...],
            quantities:  [{quantity, unit}],
            controlled_items_count: int,
            embargo_risks: [...],
            raw_summary: str,  # one-line human-readable
        }
    """
    if not text or len(text) < 5:
        return {"calibres": [], "systems": [], "ml_categories": [],
                "quantities": [], "controlled_items_count": 0, "embargo_risks": []}

    sample = text[:8000]
    sample_lower = sample.lower()

    # ── 1. Calibres ──
    found_calibres: dict[str, dict] = {}
    for pat, normaliser in _CALIBRE_PATTERNS:
        for m in pat.finditer(sample):
            cal = normaliser(m).strip()
            db_match = _CALIBRE_DB.get(cal) or _CALIBRE_DB.get(cal.replace("×", "x"))
            if db_match:
                found_calibres[cal] = {"calibre": cal, **db_match}
            else:
                found_calibres[cal] = {"calibre": cal, "category": "ammunition_unknown",
                                        "hs": "9306.xx", "ml": "ML3", "use": "unspecified"}

    # ── 2. Weapon systems ──
    found_systems: list[dict] = []
    for designation, meta in _WEAPON_SYSTEMS.items():
        if designation.lower() in sample_lower:
            found_systems.append({"designation": designation, **meta})

    # ── 3. ML categories from keywords ──
    ml_categories: set[str] = set()
    for code, kws in _ML_KEYWORDS.items():
        if any(kw in sample_lower for kw in kws):
            ml_categories.add(code)
    for sys in found_systems:
        if sys.get("ml"):
            ml_categories.add(sys["ml"])
    for cal in found_calibres.values():
        if cal.get("ml"):
            ml_categories.add(cal["ml"])

    # ── 4. Quantities ──
    quantities = _extract_quantities(sample)

    # ── 5. Embargo risks ──
    embargo_risks = []
    for sys in found_systems:
        if sys.get("embargo_risk"):
            embargo_risks.append(f"{sys['designation']}: {sys['embargo_risk']}")

    # ── 6. Summary ──
    summary_parts = []
    if found_systems:
        summary_parts.append(f"{len(found_systems)} system(s): " + ", ".join(s["designation"] for s in found_systems[:5]))
    if found_calibres:
        summary_parts.append(f"{len(found_calibres)} calibre(s): " + ", ".join(list(found_calibres.keys())[:5]))
    if quantities:
        summary_parts.append(f"{len(quantities)} quantity ref(s)")
    if ml_categories:
        summary_parts.append(f"ML categories: {', '.join(sorted(ml_categories))}")
    raw_summary = " | ".join(summary_parts) if summary_parts else "no military items detected"

    controlled_count = sum(1 for s in found_systems if s.get("controlled"))

    return {
        "calibres": list(found_calibres.values()),
        "systems": found_systems,
        "ml_categories": sorted(ml_categories),
        "quantities": quantities,
        "controlled_items_count": controlled_count,
        "embargo_risks": embargo_risks,
        "raw_summary": raw_summary,
    }


def explain_item(designation: str) -> dict:
    """Look up a single weapon system designation and return its full profile.

    Useful for /api/aria/tech/explain endpoint or chat handler when ARIA
    is asked "what is the K9 Thunder?"
    """
    key = designation.strip()
    # Try exact match
    if key in _WEAPON_SYSTEMS:
        return {"designation": key, "found": True, **_WEAPON_SYSTEMS[key]}
    # Try case-insensitive
    for k, v in _WEAPON_SYSTEMS.items():
        if k.lower() == key.lower():
            return {"designation": k, "found": True, **v}
    # Try partial match
    matches = [k for k in _WEAPON_SYSTEMS if key.lower() in k.lower()]
    if matches:
        return {
            "designation": key,
            "found": False,
            "partial_matches": matches[:5],
            "note": "Exact match not found — closest known systems shown.",
        }
    return {"designation": key, "found": False, "note": "Not in technical database."}
