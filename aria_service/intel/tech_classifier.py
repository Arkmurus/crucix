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
from .engine_wiring import wire_failure

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

    # ── R-F54 (2026-05-09) curated coverage extension ──────────────────────
    # Naval platforms (ML9 / 8A/E)
    "Type 26":      {"type": "frigate",   "country": "UK", "oem": "BAE Systems",    "ml": "ML9", "eccn": "8A609", "controlled": True},
    "Type 31":      {"type": "frigate",   "country": "UK", "oem": "Babcock",        "ml": "ML9", "eccn": "8A609", "controlled": True},
    "FREMM":        {"type": "frigate",   "country": "FR/IT", "oem": "Naval Group/Fincantieri", "ml": "ML9", "eccn": "8A609", "controlled": True},
    "Constellation":{"type": "frigate",   "country": "US", "oem": "Fincantieri Marinette", "ml": "ML9", "eccn": "8A609", "controlled": True},
    "Type 212":     {"type": "submarine", "country": "DE/IT", "oem": "TKMS",        "ml": "ML9", "eccn": "8A609", "controlled": True},
    "Type 214":     {"type": "submarine", "country": "DE", "oem": "TKMS",           "ml": "ML9", "eccn": "8A609", "controlled": True},
    "Scorpene":     {"type": "submarine", "country": "FR", "oem": "Naval Group",    "ml": "ML9", "eccn": "8A609", "controlled": True},
    "Astute":       {"type": "submarine", "country": "UK", "oem": "BAE Systems",    "ml": "ML9", "eccn": "8A609", "controlled": True},
    "Virginia-class":{"type":"submarine", "country": "US", "oem": "GD Electric Boat/Newport News", "ml": "ML9", "eccn": "8A609", "controlled": True},

    # Combat & utility helicopters (ML10)
    "AH-64 Apache": {"type": "attack_helo", "country": "US", "oem": "Boeing",       "ml": "ML10", "eccn": "9A610", "controlled": True},
    "AH-1Z Viper":  {"type": "attack_helo", "country": "US", "oem": "Bell",         "ml": "ML10", "eccn": "9A610", "controlled": True},
    "T129 ATAK":    {"type": "attack_helo", "country": "TR/IT", "oem": "TAI/Leonardo", "ml": "ML10", "eccn": "9A610", "controlled": True},
    "Mi-28":        {"type": "attack_helo", "country": "RU", "oem": "Mil",          "ml": "ML10", "eccn": "EAR99", "controlled": True, "embargo_risk": "RU sanctions"},
    "Tiger":        {"type": "attack_helo", "country": "EU", "oem": "Airbus Helicopters", "ml": "ML10", "eccn": "9A610", "controlled": True},
    "NH90":         {"type": "utility_helo","country": "EU", "oem": "Airbus/Leonardo/Fokker", "ml": "ML10", "eccn": "9A610", "controlled": True},
    "CH-47 Chinook":{"type": "transport_helo","country":"US","oem": "Boeing",       "ml": "ML10", "eccn": "9A610", "controlled": True},
    "UH-60 Blackhawk":{"type":"utility_helo","country":"US", "oem": "Sikorsky",     "ml": "ML10", "eccn": "9A610", "controlled": True},

    # Precision air-to-ground munitions (ML4)
    "Hellfire":     {"type": "agm",      "country": "US", "oem": "Lockheed Martin", "ml": "ML4", "eccn": "9A610", "controlled": True},
    "Brimstone":    {"type": "agm",      "country": "UK", "oem": "MBDA",            "ml": "ML4", "eccn": "9A610", "controlled": True},
    "Maverick":     {"type": "agm",      "country": "US", "oem": "Raytheon",        "ml": "ML4", "eccn": "9A610", "controlled": True},
    "HARM":         {"type": "anti_radar","country":"US",  "oem": "Northrop Grumman","ml":"ML4", "eccn": "9A610", "controlled": True},
    "GBU-12":       {"type": "lgb",       "country":"US",  "oem": "Raytheon",       "ml": "ML4", "eccn": "9A610", "controlled": True},
    "GBU-31 JDAM":  {"type": "guided_bomb","country":"US",  "oem": "Boeing",        "ml": "ML4", "eccn": "9A610", "controlled": True},
    "Paveway":      {"type": "lgb",       "country":"US/UK","oem": "Raytheon",      "ml": "ML4", "eccn": "9A610", "controlled": True},
    "SDB":          {"type": "guided_bomb","country":"US",  "oem": "Boeing",        "ml": "ML4", "eccn": "9A610", "controlled": True},
    "Spice":        {"type": "guided_bomb","country":"IL",  "oem": "Rafael",        "ml": "ML4", "eccn": "9A610", "controlled": True},

    # Air-to-air missiles (ML4)
    "AIM-9X Sidewinder":{"type":"aam",   "country":"US", "oem": "Raytheon",         "ml": "ML4", "eccn": "9A610", "controlled": True},
    "AIM-120 AMRAAM":   {"type":"aam",   "country":"US", "oem": "Raytheon",         "ml": "ML4", "eccn": "9A610", "controlled": True},
    "Meteor":           {"type":"aam",   "country":"EU", "oem": "MBDA",             "ml": "ML4", "eccn": "9A610", "controlled": True},
    "MICA":             {"type":"aam",   "country":"FR", "oem": "MBDA",             "ml": "ML4", "eccn": "9A610", "controlled": True},
    "R-77":             {"type":"aam",   "country":"RU", "oem": "Vympel",           "ml": "ML4", "eccn": "EAR99", "controlled": True, "embargo_risk": "RU sanctions"},
    "PL-15":            {"type":"aam",   "country":"CN", "oem": "AVIC",             "ml": "ML4", "eccn": "EAR99", "controlled": True},

    # Stand-off / land-attack (MTCR-relevant)
    "JASSM":            {"type":"alcm",  "country":"US", "oem": "Lockheed Martin",  "ml": "ML4", "eccn": "9A610", "controlled": True, "mtcr": "Cat I"},
    "Tomahawk":         {"type":"cruise","country":"US", "oem": "Raytheon",         "ml": "ML4", "eccn": "9A610", "controlled": True, "mtcr": "Cat I"},
    "Taurus KEPD-350":  {"type":"alcm",  "country":"DE/SE","oem": "Taurus Systems", "ml": "ML4", "eccn": "9A610", "controlled": True, "mtcr": "Cat I"},
    "Kalibr":           {"type":"cruise","country":"RU", "oem": "Novator",          "ml": "ML4", "eccn": "EAR99", "controlled": True, "embargo_risk": "RU sanctions", "mtcr": "Cat I"},
    "Kinzhal":          {"type":"hypersonic","country":"RU","oem":"various",        "ml": "ML4", "eccn": "EAR99", "controlled": True, "embargo_risk": "RU sanctions", "mtcr": "Cat I"},

    # Radars + EW + ISR (ML11 / ML15)
    "AN/MPQ-65":        {"type":"radar","country":"US", "oem": "Raytheon",          "ml": "ML15", "eccn": "9A610", "controlled": True},
    "GhostEye":         {"type":"radar","country":"US", "oem": "Raytheon",          "ml": "ML15", "eccn": "9A610", "controlled": True},
    "ELM-2080 Green Pine":{"type":"radar","country":"IL","oem": "IAI ELTA",         "ml": "ML15", "eccn": "9A610", "controlled": True},
    "Master 400":       {"type":"radar","country":"FR", "oem": "Thales",            "ml": "ML15", "eccn": "9A610", "controlled": True},
    "AN/ALQ-249 NGJ":   {"type":"ew_pod","country":"US","oem": "Raytheon",          "ml": "ML11", "eccn": "9A610", "controlled": True},
    "Krasukha-4":       {"type":"ew_system","country":"RU","oem":"KRET",            "ml": "ML11", "eccn": "EAR99", "controlled": True, "embargo_risk": "RU sanctions"},
    "Litening":         {"type":"isr_pod","country":"IL/US","oem":"Northrop Grumman/Rafael","ml": "ML15", "eccn": "9A610", "controlled": True},
    "Sniper XR":        {"type":"isr_pod","country":"US", "oem": "Lockheed Martin", "ml": "ML15", "eccn": "9A610", "controlled": True},

    # Loitering munitions / one-way attack drones (ML4 + ML10 hybrid)
    "Switchblade":      {"type":"loiter","country":"US","oem":"AeroVironment",      "ml": "ML4", "eccn": "9A610", "controlled": True},
    "Hero":             {"type":"loiter","country":"IL","oem":"UVision",            "ml": "ML4", "eccn": "9A610", "controlled": True},
    "Lancet":           {"type":"loiter","country":"RU","oem":"Zala Aero",          "ml": "ML4", "eccn": "EAR99", "controlled": True, "embargo_risk": "RU sanctions"},
    "Shahed-136":       {"type":"loiter","country":"IR","oem":"HESA",               "ml": "ML4", "eccn": "EAR99", "controlled": True, "embargo_risk": "IR sanctions"},
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


# ── US Munitions List (USML) keyword index — P5 expansion ───────────────────
# Maps USML Categories I-XXI (22 CFR 121.1) to descriptive keywords.
# Used when ARIA needs to reason about a product's US jurisdictional
# position (ITAR vs EAR vs EAR99). Note: since the 2013 Export Control
# Reform, many commercial-derived items moved from USML to CCL 600-series.
# This keyword index captures the PRE-ECR USML definitions so ARIA can
# still reason about legacy systems AND flag items likely to still be
# ITAR-controlled.

_USML_KEYWORDS = {
    "I":     ["firearm", "rifle", "pistol", "revolver", "shotgun", "carbine",
              "submachine gun", "assault rifle", "sniper rifle"],
    "II":    ["gun", "howitzer", "cannon", "mortar", "recoilless", "artillery",
              "self-propelled gun", "flamethrower"],
    "III":   ["ammunition", "cartridge", "round", "fuze", "shell", "projectile",
              "propellant charge", "primer"],
    "IV":    ["launch vehicle", "missile", "rocket", "torpedo", "bomb", "mine",
              "atgm", "manpads", "cruise missile", "ballistic missile",
              "depth charge", "warhead"],
    "V":     ["explosive", "propellant", "rdx", "hmx", "tnt", "c4",
              "composition b", "pbx", "incendiary", "pyrotechnic",
              "detonator", "blasting cap"],
    "VI":    ["warship", "frigate", "corvette", "destroyer", "submarine",
              "patrol boat", "minesweeper", "naval vessel", "auxiliary vessel"],
    "VII":   ["ground vehicle", "tank", "armoured fighting vehicle", "afv",
              "apc", "ifv", "mrap", "self-propelled artillery"],
    "VIII":  ["military aircraft", "fighter", "bomber", "attack aircraft",
              "military helicopter", "military transport", "gunship",
              "aew&c", "awacs", "maritime patrol aircraft", "uav",
              "drone", "unmanned aircraft"],
    "IX":    ["military training equipment", "flight simulator", "combat simulator",
              "training aid", "targetry"],
    "X":     ["body armour", "ballistic vest", "helmet", "ballistic plate",
              "personal protective equipment", "ppe"],
    "XI":    ["military electronics", "c4isr", "ground station", "jammer",
              "electronic warfare", "ew system", "signals intelligence",
              "sigint", "elint", "comint", "military communication",
              "military crypto"],
    "XII":   ["fire control", "targeting system", "laser designator",
              "rangefinder", "night vision", "thermal imager",
              "electro-optical", "lidar", "guidance system", "imaging sensor",
              "image intensifier"],
    "XIII":  ["military material", "armour plate", "ceramic armour",
              "composite armour", "reactive armour", "specialty metal",
              "tungsten heavy alloy"],
    "XIV":   ["chemical agent", "biological agent", "toxin", "nerve agent",
              "mustard gas", "riot control agent", "incapacitating agent",
              "pepper spray military"],
    "XV":    ["spacecraft", "satellite", "launch vehicle", "reentry vehicle",
              "space-qualified", "star tracker", "on-orbit", "payload"],
    "XVI":   ["nuclear weapon", "nuclear-related", "weapon of mass destruction"],
    "XVII":  ["classified article", "classified data", "classified defense service"],
    "XVIII": ["directed-energy weapon", "laser weapon", "high-energy laser",
              "microwave weapon", "particle beam"],
    "XIX":   ["gas turbine engine", "military jet engine", "afterburner",
              "fadec military"],
    "XX":    ["submersible", "submarine vehicle", "underwater vehicle",
              "submersible weapon system"],
    "XXI":   ["catch-all defense article", "defense service", "technical data"],
}


# ── EAR Commerce Control List (CCL) — dual-use categories 0-9 ───────────────
# Each EAR category is 0-9, product group A-E (systems/test/materials/
# software/technology). This keyword index maps descriptions to the likely
# ECCN category. Used when ARIA needs to reason about dual-use items.

_EAR_CATEGORY_KEYWORDS = {
    "0": ["nuclear", "uranium", "plutonium", "heavy water", "zirconium",
          "radiation detector", "isotope separation", "reactor"],
    "1": ["special material", "composite material", "ceramic material",
          "toxin", "pathogen", "chemical precursor", "polymer",
          "high-strength alloy", "metal powder"],
    "2": ["machine tool", "five-axis", "cnc machine", "isostatic press",
          "electron beam welding", "laser cutting", "ceramic manufacturing",
          "numerically controlled", "precision grinding"],
    "3": ["semiconductor", "integrated circuit", "microprocessor",
          "asic", "fpga", "high-speed adc", "dac", "rf transceiver",
          "power mosfet", "gallium nitride", "gan", "silicon carbide"],
    "4": ["high-performance computer", "supercomputer", "ai accelerator",
          "gpu cluster", "teraflop", "petaflop", "neural processor",
          "tpu"],
    "5": ["telecommunications equipment", "microwave radio", "satellite terminal",
          "satcom", "encryption", "cipher", "cryptographic module",
          "hsm", "information security", "vpn gateway", "intrusion detection"],
    "6": ["sensor", "infrared detector", "cooled detector", "laser",
          "night vision tube", "image intensifier", "focal plane array",
          "hyperspectral", "lidar", "acoustic sensor", "magnetometer"],
    "7": ["inertial measurement", "imu", "gyroscope", "accelerometer",
          "gnss", "gps receiver", "ring laser gyro", "fiber optic gyro",
          "star tracker", "autopilot", "flight control computer"],
    "8": ["underwater", "submarine technology", "acoustic array",
          "marine propulsion", "uuv", "rov", "sonar"],
    "9": ["aerospace", "jet engine", "afterburner", "hot section",
          "titanium alloy aero", "rocket motor", "solid propellant",
          "liquid propellant", "reentry", "hypersonic"],
}


# ── EU Common Military List (CML) — 22 categories ─────────────────────────
# The EU CML mirrors Wassenaar ML and largely parallels UK ML1-22 and
# USML I-XXI. The numbering is IDENTICAL to Wassenaar ML/UK ML (ML1-22)
# so the _ML_KEYWORDS index above is the authoritative mapping. We note
# here that the same keyword hit implies coverage under:
#   - Wassenaar Munitions List
#   - UK SECL UKML (ML1-22)
#   - EU CML (ML1-22)
# and a typical US USML equivalent (see _ML_TO_USML_MAP below).

_ML_TO_USML_MAP = {
    "ML1":  ["I"],        # Small arms → USML I
    "ML2":  ["II"],       # Artillery → USML II
    "ML3":  ["III"],      # Ammunition → USML III
    "ML4":  ["IV"],       # Missiles/rockets → USML IV
    "ML5":  ["XII"],      # Fire control → USML XII
    "ML6":  ["VII"],      # Ground vehicles → USML VII
    "ML7":  ["XIV"],      # CBRN agents → USML XIV
    "ML8":  ["V"],        # Energetics → USML V
    "ML9":  ["VI", "XX"], # Naval vessels + submersibles
    "ML10": ["VIII"],     # Aircraft/UAVs → USML VIII
    "ML11": ["XI"],       # Military electronics → USML XI
    "ML12": ["IV"],       # Kinetic energy → USML IV
    "ML13": ["X", "XIII"],# Body armour + material
    "ML14": ["IX"],       # Training equipment → USML IX
    "ML15": ["XII"],      # Imaging/countermeasures → USML XII
    "ML17": ["XIII"],     # Misc material → USML XIII
    "ML19": ["XVIII"],    # Directed energy → USML XVIII
    "ML21": ["XVII"],     # Software → USML XVII
    "ML22": ["XVII"],     # Technology → USML XVII
}


# ── Multilateral regime hooks ─────────────────────────────────────────────
# Maps item category patterns to applicable multilateral regimes so
# ARIA can cite them automatically.

_MULTILATERAL_HOOKS = {
    "missile_mtcr_cat_1": {
        "triggers": ["cruise missile >=300km", "ballistic missile >=300km",
                     "rocket >=300km range >=500kg payload", "mq-9", "mq-1",
                     "predator", "reaper", "tomahawk", "storm shadow",
                     "scalp", "brahmos"],
        "regime": "MTCR Category I",
        "note": "Strong presumption of denial under MTCR Cat I. 300km range "
                "/ 500kg payload threshold.",
    },
    "missile_mtcr_cat_2": {
        "triggers": ["short range missile", "air-to-air missile",
                     "short range rocket"],
        "regime": "MTCR Category II",
        "note": "Case-by-case review under MTCR Cat II.",
    },
    "nuclear_nsg": {
        "triggers": ["reactor", "uranium enrichment", "plutonium", "heavy water",
                     "fuel fabrication", "reprocessing", "hexafluoride"],
        "regime": "NSG Trigger List",
        "note": "NSG export-control regime applies. IAEA safeguards required "
                "for non-NWS.",
    },
    "chemical_cwc": {
        "triggers": ["nerve agent", "sarin", "vx", "mustard gas",
                     "schedule 1 chemical", "schedule 2 chemical",
                     "phosgene", "thiodiglycol"],
        "regime": "CWC Schedule 1/2/3",
        "note": "Chemical Weapons Convention schedules apply. OPCW oversight.",
    },
    "biological_ag": {
        "triggers": ["anthrax", "botulinum", "pathogen", "toxin",
                     "biological agent", "dual-use biology"],
        "regime": "Australia Group + BTWC",
        "note": "Australia Group export controls + Biological and Toxin "
                "Weapons Convention obligations.",
    },
    "dual_use_wassenaar": {
        "triggers": ["high-performance computer", "encryption module",
                     "inertial measurement unit", "fiber optic gyro",
                     "cryptographic"],
        "regime": "Wassenaar Dual-Use",
        "note": "Wassenaar Arrangement dual-use controls apply across all "
                "participating states.",
    },
}


def _match_multilateral_hooks(text_lower: str) -> list[dict]:
    """Return the list of multilateral regimes implicated by the text."""
    hits: list[dict] = []
    for hook_id, spec in _MULTILATERAL_HOOKS.items():
        for trig in spec["triggers"]:
            if trig in text_lower:
                hits.append({
                    "hook": hook_id,
                    "regime": spec["regime"],
                    "note": spec["note"],
                    "trigger": trig,
                })
                break
    return hits


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

    # ── 3. ML categories from keywords (Wassenaar ML / UK ML / EU CML) ──
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

    # ── 3a. USML categories (US ITAR) — direct keyword match + ML mapping ──
    usml_categories: set[str] = set()
    for cat, kws in _USML_KEYWORDS.items():
        if any(kw in sample_lower for kw in kws):
            usml_categories.add(cat)
    # Also project from ML matches via the mapping table.
    for ml in ml_categories:
        for us in _ML_TO_USML_MAP.get(ml, []):
            usml_categories.add(us)

    # ── 3b. EAR CCL dual-use categories (0-9) from keywords ──
    ear_categories: set[str] = set()
    for cat, kws in _EAR_CATEGORY_KEYWORDS.items():
        if any(kw in sample_lower for kw in kws):
            ear_categories.add(cat)

    # ── 3c. Multilateral regime hooks (MTCR, NSG, AG, CWC, Wassenaar DU) ──
    multilateral = _match_multilateral_hooks(sample_lower)

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

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="tech_classifier",
        summary="Classify Text",
        source_id="tech_classifier:R-F996",
    )

    return {
        "calibres": list(found_calibres.values()),
        "systems": found_systems,
        "ml_categories": sorted(ml_categories),
        "usml_categories": sorted(usml_categories),
        "ear_categories": sorted(ear_categories),
        "multilateral_regimes": multilateral,
        "quantities": quantities,
        "controlled_items_count": controlled_count,
        "embargo_risks": embargo_risks,
        "raw_summary": raw_summary,
    }


def classify_export_control(text: str) -> dict:
    """High-level export-control classification for a free-text item description.

    Returns the full regime stack ARIA would need to cite in a brief:
      {
        "wassenaar_ml":      ["ML4", "ML10", ...]   (UK ML / EU CML / Wassenaar)
        "usml":              ["IV", "VIII", ...]    (US ITAR categories)
        "ear_ccl":           ["3", "9", ...]        (EAR dual-use categories)
        "multilateral":      [{hook, regime, note, trigger}, ...]
        "systems":           [<system-db-matches>]
        "recommendation":    "ITAR primary" | "EAR primary" | "civilian" | "mixed"
        "confidence":        0.0-1.0
        "notes":             [...]
      }

    This is the P5 replacement for the audit's "stub" tech_classifier
    — it's a real multi-regime classifier that lets ARIA answer "what
    regime does this fall under" in structured form, from any free
    text description.
    """
    inner = classify_text(text)
    wass_ml = inner.get("ml_categories") or []
    usml = inner.get("usml_categories") or []
    ear = inner.get("ear_categories") or []
    multi = inner.get("multilateral_regimes") or []

    # Recommendation heuristic:
    # - USML hit → ITAR primary (the strictest regime)
    # - ML hit but no USML → likely EAR 600-series (post-ECR)
    # - EAR category hit only → EAR primary (dual-use)
    # - No hits → civilian or un-classified
    has_usml = bool(usml)
    has_ml   = bool(wass_ml)
    has_ear  = bool(ear)

    if has_usml:
        recommendation = "ITAR primary"
        confidence = 0.85 if len(usml) <= 3 else 0.70
    elif has_ml and not has_ear:
        recommendation = "EAR 600-series / ITAR legacy"
        confidence = 0.75
    elif has_ml and has_ear:
        recommendation = "mixed — confirm ITAR vs EAR jurisdiction"
        confidence = 0.60
    elif has_ear:
        recommendation = "EAR dual-use primary"
        confidence = 0.75
    else:
        recommendation = "civilian or unclassified"
        confidence = 0.40

    notes = []
    if multi:
        notes.append(f"{len(multi)} multilateral regime(s) implicated")
    if inner.get("embargo_risks"):
        notes.append(f"{len(inner['embargo_risks'])} embargo-risk flag(s)")
    if inner.get("systems"):
        notes.append(f"{len(inner['systems'])} known system(s) identified")
    if not (has_usml or has_ml or has_ear):
        notes.append("No export-control hits — verify classification with specific product datasheet")

    return {
        "wassenaar_ml": wass_ml,
        "usml": usml,
        "ear_ccl": ear,
        "multilateral": multi,
        "systems": inner.get("systems", []),
        "embargo_risks": inner.get("embargo_risks", []),
        "quantities": inner.get("quantities", []),
        "recommendation": recommendation,
        "confidence": confidence,
        "notes": notes,
        "raw_summary": inner.get("raw_summary", ""),
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

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
