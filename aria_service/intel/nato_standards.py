# =============================================================================
# ARIA v3.1 — NATO Standards Knowledge Module
# aria_service/intel/nato_standards.py
#
# Comprehensive taxonomy and lookup for NATO standardisation agreements,
# allied publications, quality assurance publications, and environmental
# testing standards as they apply to defence procurement, broking, and
# export compliance.
#
# SCOPE: Global. All NATO standardisation instruments relevant to
#        defence procurement advisory, equipment interoperability
#        assessment, contract compliance, and export licensing.
#
# ARIA APPLICATION: This knowledge activates automatically when:
#   - Hardware comparison involves NATO-interoperable equipment
#   - Contract review references STANAGs, AQAPs, or allied pubs
#   - Procurement advisory requires interoperability standards
#   - BD advice involves NATO member or partner nation acquisition
#   - Export licence assessment needs NATO classification context
#   - Due diligence on a supplier requires quality standard checks
#
# ALL SOURCES: Open-source, verifiable, citable.
# Primary: NATO Standardization Office (NSO), NSPA, NATO HQ
# Secondary: National defence standards bodies (DSTAN, DIN, MIL-STD)
# =============================================================================

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("aria.nato_standards")


# =============================================================================
# NATO REFERENCE URLS
# =============================================================================

NATO_URLS = {
    "nso_public_standards": "https://nso.nato.int/nso/",
    "nso_stanag_search": "https://nso.nato.int/nso/nsdd/listpromulg.html",
    "nato_publications": "https://www.nato.int/cps/en/natohq/publications.htm",
    "nspa_portal": "https://www.nspa.nato.int/",
    "nato_codification": "https://www.nato.int/cps/en/natohq/topics_56898.htm",
    "uk_dstan": "https://www.gov.uk/government/collections/defence-standards",
    "us_assist": "https://assist.dla.mil/",
    "everyspec": "https://everyspec.com/",
}


# =============================================================================
# SECTION 1 — STANAG (STANDARDIZATION AGREEMENTS)
# =============================================================================
# STANAGs are binding on ratifying nations. They define interoperability
# requirements across Alliance forces. Each STANAG is promulgated by the
# NATO Standardization Office (NSO) and ratified individually by member
# nations — ratification status varies.
# =============================================================================

STANAG_CATEGORIES: dict[str, dict] = {
    "ammunition_weapons": {
        "label": "Ammunition & Weapons",
        "description": (
            "Standards governing ammunition design, safety, testing, "
            "interoperability, and weapons system interfaces across "
            "NATO forces."
        ),
        "standards": [
            {
                "stanag": "STANAG 4170",
                "title": "Principles of Ammunition Safety",
                "desc": "Master safety framework for all NATO ammunition — covers design, manufacture, storage, transport, and disposal safety principles.",
            },
            {
                "stanag": "STANAG 4187",
                "title": "Fuzing Systems — Safety Design Requirements",
                "desc": "Safety and arming criteria for fuzing systems on munitions, including ESAD (Environmental, Safety and Arming Device) requirements.",
            },
            {
                "stanag": "STANAG 4439",
                "title": "Policy for Introduction and Assessment of NSPA-Managed Munitions",
                "desc": "Governs assessment and qualification of munitions entering NSPA logistics management.",
            },
            {
                "stanag": "STANAG 4157",
                "title": "Fuze Interface for High-Explosive Artillery Projectiles",
                "desc": "Physical and functional interface requirements ensuring fuze interchangeability across NATO artillery systems.",
            },
            {
                "stanag": "STANAG 4172",
                "title": "5.56mm NATO Ball Ammunition",
                "desc": "Defines the 5.56x45mm NATO cartridge specification — the standard small-arms round across Alliance infantry forces.",
            },
            {
                "stanag": "STANAG 4179",
                "title": "Explosive Ordnance Disposal (EOD) Procedures",
                "desc": "Standardises EOD render-safe procedures and reporting across NATO forces.",
            },
            {
                "stanag": "STANAG 2310",
                "title": "NATO Ammunition Marking",
                "desc": "Colour-coding and marking conventions for ammunition and explosives identification across NATO stockpiles.",
            },
            {
                "stanag": "STANAG 4110",
                "title": "Assessments of the Noise Threat to Hearing",
                "desc": "Assessment methodology for weapons noise exposure and hearing conservation requirements.",
            },
        ],
    },
    "uas_unmanned": {
        "label": "UAS / Unmanned Systems",
        "description": (
            "Standards for unmanned aerial systems (UAS), unmanned ground "
            "vehicles (UGV), and autonomous systems interoperability."
        ),
        "standards": [
            {
                "stanag": "STANAG 4586",
                "title": "Standard Interfaces of UAV Control System for NATO UAV Interoperability",
                "desc": "Core UAS interoperability standard — defines data link and control interfaces allowing multi-nation UAV tasking and data sharing.",
            },
            {
                "stanag": "STANAG 4670",
                "title": "Guidance for the Training of Unmanned Aircraft Systems Operators",
                "desc": "Training and qualification requirements for UAS operators across NATO forces.",
            },
            {
                "stanag": "STANAG 4671",
                "title": "UAV Systems Airworthiness Requirements (USAR)",
                "desc": "Airworthiness certification framework for fixed-wing UAVs — NATO equivalent of civil airworthiness standards.",
            },
            {
                "stanag": "STANAG 4703",
                "title": "Light Unmanned Aircraft Systems Airworthiness Requirements (LUAR)",
                "desc": "Airworthiness requirements for light UAS (under 150kg), complementing STANAG 4671 for smaller platforms.",
            },
            {
                "stanag": "STANAG 7023",
                "title": "NATO Primary Image Format Standard",
                "desc": "Image and sensor data format standard for ISR products — ensures UAS imagery interoperability across NATO C4ISR systems.",
            },
        ],
    },
    "communications_datalinks": {
        "label": "Communications & Data Links",
        "description": (
            "Standards for secure communications, tactical data links, "
            "information exchange, and C4ISR interoperability."
        ),
        "standards": [
            {
                "stanag": "STANAG 5516",
                "title": "Tactical Data Exchange — Link 16",
                "desc": "Defines Link 16 TADIL-J — the primary tactical data link for NATO air, maritime, and ground forces real-time data exchange.",
            },
            {
                "stanag": "STANAG 4559",
                "title": "NATO Standard ISR Library Interface (NSILI)",
                "desc": "Interface standard for ISR data libraries, enabling cross-nation querying and retrieval of intelligence products.",
            },
            {
                "stanag": "STANAG 4607",
                "title": "NATO Ground Moving Target Indicator (GMTI) Format",
                "desc": "Data format for ground moving target indicator radar data — enables multi-sensor GMTI fusion across NATO.",
            },
            {
                "stanag": "STANAG 4609",
                "title": "NATO Digital Motion Imagery Standard",
                "desc": "Full-motion video (FMV) metadata and format standard for motion imagery across NATO ISR platforms.",
            },
            {
                "stanag": "STANAG 5066",
                "title": "Profile for HF Radio Data Communications",
                "desc": "HF radio data communication profiles for beyond-line-of-sight NATO communications.",
            },
            {
                "stanag": "STANAG 4406",
                "title": "Military Message Handling System (MMHS)",
                "desc": "NATO military messaging standard — defines secure email-equivalent message handling across classified networks.",
            },
            {
                "stanag": "STANAG 4677",
                "title": "UAS Sense and Avoid System (UASSAS)",
                "desc": "Sense-and-avoid requirements for UAS operating in shared airspace with manned aircraft.",
            },
        ],
    },
    "cbrn_protection": {
        "label": "NBC / CBRN Protection",
        "description": (
            "Standards for Chemical, Biological, Radiological, and Nuclear "
            "defence — detection, protection, decontamination, and warning."
        ),
        "standards": [
            {
                "stanag": "STANAG 4360",
                "title": "NBC Defence Equipment Test and Evaluation Methodology",
                "desc": "Test and evaluation framework for all CBRN protective equipment, detection systems, and decontamination capabilities.",
            },
            {
                "stanag": "STANAG 2352",
                "title": "Nuclear, Biological and Chemical Defence Equipment Logistic Support",
                "desc": "Logistics, maintenance, and support requirements for CBRN defence equipment interoperability.",
            },
            {
                "stanag": "STANAG 4578",
                "title": "Contamination Survivability Factors in the Design of Equipment",
                "desc": "Design requirements for military equipment to survive and operate in CBRN-contaminated environments.",
            },
            {
                "stanag": "STANAG 2150",
                "title": "NATO Standards for Reporting Nuclear Detonations",
                "desc": "Standardised nuclear detonation detection, reporting format, and fallout prediction procedures.",
            },
            {
                "stanag": "STANAG 2103",
                "title": "Reporting Nuclear Detonations, Biological and Chemical Attacks",
                "desc": "CBRN incident reporting procedures and message formats across NATO forces.",
            },
        ],
    },
    "ballistic_protection": {
        "label": "Ballistic Protection",
        "description": (
            "Standards for ballistic protection levels — personal armour, "
            "vehicle armour, and protective materials testing."
        ),
        "standards": [
            {
                "stanag": "STANAG 4569",
                "title": "Protection Levels for Occupants of Armoured Vehicles",
                "desc": "Defines ballistic and mine/IED protection levels (1-6) for logistic and light armoured vehicles — the universal reference for APC/IFV procurement.",
            },
            {
                "stanag": "STANAG 2920",
                "title": "Ballistic Test Method for Personal Armour Materials and Combat Clothing",
                "desc": "V50 ballistic testing methodology for personal armour — the NATO standard for body armour performance verification.",
            },
            {
                "stanag": "STANAG 4190",
                "title": "Test Procedures for Measuring Behind-Armour Effects of Anti-Armour Ammunition",
                "desc": "Behind-armour debris and effects assessment methodology for evaluating armour defeat mechanisms.",
            },
            {
                "stanag": "STANAG 2310",
                "title": "NATO Ammunition Marking",
                "desc": "Marking conventions relevant to armour-piercing vs ball ammunition identification.",
            },
            {
                "stanag": "STANAG 4164",
                "title": "Test Procedures for Small Arms Ammunition",
                "desc": "Proof and acceptance testing for small arms ammunition including ballistic performance verification.",
            },
        ],
    },
    "vehicle_standards": {
        "label": "Vehicle Standards",
        "description": (
            "Standards for military vehicles — design, testing, "
            "interoperability, and cross-servicing compatibility."
        ),
        "standards": [
            {
                "stanag": "STANAG 4569",
                "title": "Protection Levels for Occupants of Armoured Vehicles",
                "desc": "Ballistic and mine/IED protection levels for logistic and light armoured vehicles — primary procurement reference.",
            },
            {
                "stanag": "STANAG 2805",
                "title": "Road Movement Bidding and Offering Procedures",
                "desc": "Procedures for coordinating military road movements and convoy support across NATO nations.",
            },
            {
                "stanag": "STANAG 2154",
                "title": "Regulations for Military Road Movements",
                "desc": "Road movement regulations, signage, and procedures for cross-border military transit.",
            },
            {
                "stanag": "STANAG 4280",
                "title": "Safety Design Requirements for Weapon Systems",
                "desc": "System-level safety design requirements for vehicle-mounted and turreted weapon systems.",
            },
            {
                "stanag": "STANAG 4370",
                "title": "Environmental Testing (AECTP)",
                "desc": "Master standard for all environmental testing of materiel — references the AECTP series for detailed test methods.",
            },
        ],
    },
    "logistics_support": {
        "label": "Logistics & Support",
        "description": (
            "Standards for logistics, supply chain, codification, "
            "and cross-servicing across NATO forces."
        ),
        "standards": [
            {
                "stanag": "STANAG 3150",
                "title": "NATO Uniform System of Supply Classification",
                "desc": "Supply classification system (NATO Stock Number structure) enabling cross-nation logistics interoperability.",
            },
            {
                "stanag": "STANAG 3151",
                "title": "NATO Codification System (NCS)",
                "desc": "Item identification and codification — assigns NSN (NATO Stock Numbers) for cross-reference across nations.",
            },
            {
                "stanag": "STANAG 2034",
                "title": "Standard Procedures for Mutual Support of NATO Forces",
                "desc": "Cross-servicing and mutual logistics support procedures between NATO forces, including acquisition and cross-servicing agreements.",
            },
            {
                "stanag": "STANAG 2961",
                "title": "Classes of Supply",
                "desc": "Defines NATO classes of supply (I–V) for logistics planning and resupply coordination.",
            },
            {
                "stanag": "STANAG 4107",
                "title": "Mutual Acceptance of Government Quality Assurance",
                "desc": "Framework for mutual recognition of quality assurance oversight between NATO nations — underpins AQAP cross-border acceptance.",
            },
        ],
    },
}


# =============================================================================
# SECTION 2 — ALLIED PUBLICATIONS (AP / ATP / AJP / AEP)
# =============================================================================
# Allied Publications contain doctrine, procedures, and engineering
# standards. They are referenced by STANAGs but stand as separate
# document series.
# =============================================================================

ALLIED_PUBLICATIONS: dict[str, dict] = {
    "atp": {
        "label": "ATP — Allied Tactical Publications",
        "description": "Tactical doctrine and procedures for NATO forces.",
        "standards": [
            {
                "id": "ATP-3.2.1",
                "title": "Allied Land Tactics",
                "desc": "Core land tactics doctrine covering offensive, defensive, and stability operations for NATO land forces.",
            },
            {
                "id": "ATP-3.3.4",
                "title": "Tactics, Techniques and Procedures for Air-to-Surface Operations",
                "desc": "Air-ground integration doctrine — critical for CAS, air interdiction, and joint fires.",
            },
            {
                "id": "ATP-45",
                "title": "Warning and Reporting and Hazard Prediction of CBRN Incidents",
                "desc": "CBRN warning, reporting, and hazard area prediction procedures across NATO.",
            },
            {
                "id": "ATP-3.3.1",
                "title": "Air-Maritime Coordination (AMC) Procedures",
                "desc": "Procedures for coordinating air and maritime operations, including surface/subsurface track management.",
            },
            {
                "id": "ATP-69",
                "title": "NATO Submarine Rescue",
                "desc": "Submarine escape and rescue doctrine — defines interoperability requirements for submarine rescue systems.",
            },
        ],
    },
    "ajp": {
        "label": "AJP — Allied Joint Publications",
        "description": "Joint doctrine governing multi-domain and combined operations.",
        "standards": [
            {
                "id": "AJP-01",
                "title": "Allied Joint Doctrine",
                "desc": "Capstone joint doctrine — the foundational framework for all NATO joint operations.",
            },
            {
                "id": "AJP-3",
                "title": "Allied Joint Doctrine for the Conduct of Operations",
                "desc": "Operational-level doctrine for planning and conducting joint and combined operations.",
            },
            {
                "id": "AJP-3.4.9",
                "title": "Allied Joint Doctrine for Civil-Military Cooperation (CIMIC)",
                "desc": "CIMIC doctrine — coordination between military forces and civilian actors in operations.",
            },
            {
                "id": "AJP-4",
                "title": "Allied Joint Doctrine for Logistics",
                "desc": "Joint logistics doctrine covering sustainment, supply chain, and host-nation support across NATO.",
            },
            {
                "id": "AJP-3.5",
                "title": "Allied Joint Doctrine for Special Operations",
                "desc": "SOF doctrine — interoperability framework for NATO special operations forces.",
            },
        ],
    },
    "aep": {
        "label": "AEP — Allied Engineering Publications",
        "description": "Engineering standards and specifications for materiel and equipment.",
        "standards": [
            {
                "id": "AEP-55",
                "title": "Procedures for Evaluating the Protection Level of Armoured Vehicles",
                "desc": "Detailed testing procedures implementing STANAG 4569 protection levels — the engineering companion to the policy STANAG.",
            },
            {
                "id": "AEP-2920",
                "title": "Ballistic Test Method for Personal Armour — Detailed Procedures",
                "desc": "Engineering test procedures implementing STANAG 2920 V50 methodology for body armour.",
            },
            {
                "id": "AEP-84",
                "title": "Water-Reactive and CBRN Detection Equipment Standards",
                "desc": "Performance and testing standards for chemical agent detection equipment.",
            },
            {
                "id": "AEP-31",
                "title": "Airworthiness Design Criteria for UAS",
                "desc": "Engineering design criteria supporting STANAG 4671 UAS airworthiness — structural, propulsion, and systems requirements.",
            },
            {
                "id": "AEP-4518",
                "title": "Ammunition Safety Group Information (MSIAC/NIAG)",
                "desc": "Ammunition insensitive munitions (IM) assessment criteria and reporting procedures.",
            },
        ],
    },
}


# =============================================================================
# SECTION 3 — AQAP (ALLIED QUALITY ASSURANCE PUBLICATIONS)
# =============================================================================
# AQAPs define quality management requirements for NATO defence
# procurement. They are the NATO-specific quality layer on top of
# ISO 9001 and sector-specific standards.
# =============================================================================

AQAP_STANDARDS: list[dict] = [
    {
        "id": "AQAP-2110",
        "title": "NATO Quality Assurance Requirements for Design, Development and Production",
        "desc": "The primary AQAP for defence procurement — specifies ISO 9001-based QMS plus additional NATO requirements for design/development/production contracts.",
        "iso_basis": "ISO 9001",
    },
    {
        "id": "AQAP-2120",
        "title": "NATO Quality Assurance Requirements for Production",
        "desc": "Production-only contracts where design responsibility is not with the supplier — ISO 9001 subset plus NATO production requirements.",
        "iso_basis": "ISO 9001",
    },
    {
        "id": "AQAP-2130",
        "title": "NATO Quality Assurance Requirements for Inspection and Test",
        "desc": "Inspection and test-only requirements — the lightest AQAP tier, for simple supply contracts with mature products.",
        "iso_basis": "ISO 9001",
    },
    {
        "id": "AQAP-2131",
        "title": "NATO Quality Assurance Requirements for Final Inspection and Test",
        "desc": "Final inspection/acceptance only — used for COTS (commercial off-the-shelf) military procurement.",
        "iso_basis": "N/A",
    },
    {
        "id": "AQAP-2210",
        "title": "NATO Supplementary Software Quality Assurance Requirements",
        "desc": "Software quality requirements supplementing AQAP-2110 — covers software lifecycle, configuration management, and verification/validation.",
        "iso_basis": "ISO 9001 + ISO/IEC 12207",
    },
    {
        "id": "AQAP-2310",
        "title": "NATO Quality Assurance Requirements for Aviation, Space and Defence Suppliers",
        "desc": "AS9100-based quality requirements for aerospace and defence supply chains — critical for aircraft components and systems.",
        "iso_basis": "AS9100 / EN 9100",
    },
    {
        "id": "AQAP-160",
        "title": "NATO Integrated Quality Requirements for Software Throughout the Life Cycle",
        "desc": "Full lifecycle software quality management from requirements through retirement — command and control, weapons software.",
        "iso_basis": "ISO/IEC 12207",
    },
    {
        "id": "AQAP-2070",
        "title": "NATO Mutual Government Quality Assurance (GQA) Process",
        "desc": "Procedures for government quality assurance when procurement crosses NATO borders — defines requesting/performing nation GQA roles.",
        "iso_basis": "N/A",
    },
    {
        "id": "AQAP-4107",
        "title": "Mutual Acceptance of Government Quality Assurance and Usage of AQAP",
        "desc": "Framework enabling mutual recognition of GQA between NATO nations — reduces duplication of quality oversight.",
        "iso_basis": "N/A",
    },
]


# =============================================================================
# SECTION 4 — AECTP (ALLIED ENVIRONMENTAL CONDITIONS AND TEST PUBLICATIONS)
# =============================================================================
# AECTPs define environmental conditions, test procedures, and
# evaluation methods for military materiel. Referenced by STANAG 4370.
# =============================================================================

AECTP_STANDARDS: list[dict] = [
    {
        "id": "AECTP-100",
        "title": "Environmental Guidelines for Defence Materiel",
        "desc": "Environmental engineering guidelines — how to define and apply environmental requirements in materiel specifications.",
    },
    {
        "id": "AECTP-200",
        "title": "Environmental Conditions",
        "desc": "Characterisation of environmental conditions (climate, shock, vibration, pressure) that military materiel will encounter in service.",
    },
    {
        "id": "AECTP-230",
        "title": "Climatic Conditions — Temperature, Humidity, Solar Radiation",
        "desc": "Detailed climatic environmental characterisation for materiel deployed across NATO climate zones (A1–C4).",
    },
    {
        "id": "AECTP-300",
        "title": "Climatic Environmental Tests",
        "desc": "Test methods for climatic conditions: temperature cycling, humidity, solar radiation, rain, sand/dust, icing, salt fog.",
    },
    {
        "id": "AECTP-400",
        "title": "Mechanical Environmental Tests",
        "desc": "Test methods for mechanical environments: vibration, shock, drop, acceleration, acoustic noise — the core survivability test suite.",
    },
    {
        "id": "AECTP-500",
        "title": "Electrical Environmental Tests",
        "desc": "Electromagnetic compatibility (EMC), EMP, lightning, ESD, and power supply variation testing for military electronics.",
    },
    {
        "id": "AECTP-600",
        "title": "Materiel Assessment Using Climatic/Dynamic Environmental Data",
        "desc": "Integration of environmental data with materiel performance — tailoring test severity to actual deployment conditions.",
    },
]


# =============================================================================
# PRODUCT CATEGORY → STANDARDS MAPPING
# =============================================================================
# Maps product categories (as used in procurement, BD, and contract
# review) to the set of NATO standards most likely to be relevant.
# =============================================================================

_PRODUCT_STANDARDS_MAP: dict[str, list[str]] = {
    "armoured vehicle": [
        "STANAG 4569", "STANAG 4370", "STANAG 4280",
        "AEP-55", "AQAP-2110", "AECTP-300", "AECTP-400",
    ],
    "apc": [
        "STANAG 4569", "STANAG 4370", "STANAG 4280",
        "AEP-55", "AQAP-2110", "AECTP-300", "AECTP-400",
    ],
    "ifv": [
        "STANAG 4569", "STANAG 4370", "STANAG 4280",
        "AEP-55", "AQAP-2110", "AECTP-300", "AECTP-400",
    ],
    "body armour": [
        "STANAG 2920", "AEP-2920", "AQAP-2110", "AQAP-2130",
    ],
    "personal armour": [
        "STANAG 2920", "AEP-2920", "AQAP-2110", "AQAP-2130",
    ],
    "helmet": [
        "STANAG 2920", "AEP-2920", "AQAP-2110",
    ],
    "ammunition": [
        "STANAG 4170", "STANAG 4187", "STANAG 4439",
        "STANAG 2310", "STANAG 4110", "STANAG 4164",
        "AQAP-2110", "AECTP-300", "AECTP-400",
    ],
    "small arms ammunition": [
        "STANAG 4172", "STANAG 4164", "STANAG 4170",
        "STANAG 2310", "AQAP-2110",
    ],
    "fuze": [
        "STANAG 4187", "STANAG 4157", "STANAG 4170",
        "AQAP-2110", "AECTP-500",
    ],
    "uas": [
        "STANAG 4586", "STANAG 4671", "STANAG 4703",
        "STANAG 7023", "STANAG 4609", "STANAG 4677",
        "AEP-31", "AQAP-2110", "AQAP-2210",
    ],
    "uav": [
        "STANAG 4586", "STANAG 4671", "STANAG 4703",
        "STANAG 7023", "STANAG 4609", "STANAG 4677",
        "AEP-31", "AQAP-2110", "AQAP-2210",
    ],
    "drone": [
        "STANAG 4586", "STANAG 4671", "STANAG 4703",
        "STANAG 7023", "AQAP-2110",
    ],
    "radio": [
        "STANAG 5066", "STANAG 4406", "STANAG 5516",
        "AQAP-2110", "AECTP-500",
    ],
    "communications": [
        "STANAG 5066", "STANAG 4406", "STANAG 5516",
        "AQAP-2110", "AECTP-500",
    ],
    "data link": [
        "STANAG 5516", "STANAG 4559", "STANAG 4607",
        "STANAG 4609", "AQAP-2110",
    ],
    "cbrn": [
        "STANAG 4360", "STANAG 2352", "STANAG 4578",
        "STANAG 2150", "STANAG 2103", "ATP-45",
        "AEP-84", "AQAP-2110",
    ],
    "nbc": [
        "STANAG 4360", "STANAG 2352", "STANAG 4578",
        "STANAG 2150", "STANAG 2103", "ATP-45",
        "AEP-84", "AQAP-2110",
    ],
    "aircraft": [
        "AQAP-2310", "AQAP-2110", "AQAP-2210",
        "AECTP-300", "AECTP-400", "AECTP-500",
    ],
    "helicopter": [
        "AQAP-2310", "AQAP-2110", "AQAP-2210",
        "AECTP-300", "AECTP-400", "AECTP-500",
    ],
    "electronics": [
        "AECTP-500", "AECTP-300", "AECTP-400",
        "AQAP-2110", "AQAP-2210",
    ],
    "software": [
        "AQAP-2210", "AQAP-160", "AQAP-2110",
    ],
    "c4isr": [
        "STANAG 5516", "STANAG 4559", "STANAG 4607",
        "STANAG 4609", "STANAG 4406", "STANAG 7023",
        "AQAP-2110", "AQAP-2210",
    ],
    "logistics": [
        "STANAG 3150", "STANAG 3151", "STANAG 2034",
        "STANAG 2961", "STANAG 4107", "AJP-4",
    ],
    "eod": [
        "STANAG 4179", "STANAG 4170", "AQAP-2110",
    ],
    "weapon system": [
        "STANAG 4280", "STANAG 4170", "STANAG 4370",
        "AQAP-2110", "AECTP-300", "AECTP-400", "AECTP-500",
    ],
}


# =============================================================================
# FULL-TEXT SEARCH INDEX — all standards flattened for keyword matching
# =============================================================================

def _build_search_index() -> list[dict]:
    """Build a flat list of all standards for keyword search."""
    index: list[dict] = []

    # STANAGs
    for cat_key, cat_data in STANAG_CATEGORIES.items():
        for std in cat_data["standards"]:
            index.append({
                "id": std["stanag"],
                "title": std["title"],
                "desc": std["desc"],
                "category": cat_data["label"],
                "type": "STANAG",
                "search_text": f"{std['stanag']} {std['title']} {std['desc']} {cat_data['label']}".lower(),
            })

    # Allied publications
    for pub_type, pub_data in ALLIED_PUBLICATIONS.items():
        for std in pub_data["standards"]:
            index.append({
                "id": std["id"],
                "title": std["title"],
                "desc": std["desc"],
                "category": pub_data["label"],
                "type": pub_type.upper(),
                "search_text": f"{std['id']} {std['title']} {std['desc']} {pub_data['label']}".lower(),
            })

    # AQAPs
    for std in AQAP_STANDARDS:
        index.append({
            "id": std["id"],
            "title": std["title"],
            "desc": std["desc"],
            "category": "Quality Assurance",
            "type": "AQAP",
            "search_text": f"{std['id']} {std['title']} {std['desc']} quality assurance".lower(),
        })

    # AECTPs
    for std in AECTP_STANDARDS:
        index.append({
            "id": std["id"],
            "title": std["title"],
            "desc": std["desc"],
            "category": "Environmental Testing",
            "type": "AECTP",
            "search_text": f"{std['id']} {std['title']} {std['desc']} environmental testing".lower(),
        })

    return index


_SEARCH_INDEX: list[dict] = _build_search_index()


# =============================================================================
# PUBLIC API — get_nato_context
# =============================================================================

def get_nato_context(query: str) -> str:
    """Return relevant NATO standards context for a given query.

    Performs keyword matching against the full taxonomy and specific
    standards. Returns a formatted string suitable for inclusion in
    ARIA prompts and knowledge context.

    Args:
        query: Free-text query (e.g., "armoured vehicle protection",
               "ammunition safety", "STANAG 4569").

    Returns:
        Formatted multi-line string with matching standards and context.
        Empty string if no matches found.
    """
    if not query or not query.strip():
        return ""

    query_lower = query.lower().strip()
    tokens = query_lower.split()

    scored: list[tuple[int, dict]] = []
    for entry in _SEARCH_INDEX:
        score = 0
        # Exact ID match gets highest priority
        if query_lower in entry["id"].lower():
            score += 100
        # Token matching
        for token in tokens:
            if len(token) < 2:
                continue
            if token in entry["search_text"]:
                score += 10
            if token in entry["id"].lower():
                score += 20
        if score > 0:
            scored.append((score, entry))

    if not scored:
        return ""

    # Sort by score descending, take top 10
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:10]

    lines = [
        "NATO STANDARDS — Relevant to query: " + query,
        "=" * 60,
    ]
    for score, entry in top:
        lines.append(
            f"  {entry['id']:15s} | {entry['title']}"
        )
        lines.append(
            f"  {'':15s} | Type: {entry['type']} | Category: {entry['category']}"
        )
        lines.append(
            f"  {'':15s} | {entry['desc']}"
        )
        lines.append("")

    lines.append(
        f"Reference: NATO Standardization Office (NSO) — {NATO_URLS['nso_public_standards']}"
    )
    return "\n".join(lines)


# =============================================================================
# PUBLIC API — get_procurement_standards
# =============================================================================

def get_procurement_standards(product_category: str) -> list[dict]:
    """Return which NATO standards apply to a given product category.

    Looks up the product category in the standards mapping. Performs
    fuzzy matching if no exact match is found — tries substring and
    normalised forms.

    Args:
        product_category: Product type, e.g., 'armoured vehicle',
                          'ammunition', 'uas', 'body armour'.

    Returns:
        List of dicts with keys: id, title, desc, type, category.
        Empty list if no standards found for the category.
    """
    if not product_category or not product_category.strip():
        return []

    cat_lower = product_category.lower().strip()

    # Direct match
    std_ids = _PRODUCT_STANDARDS_MAP.get(cat_lower)

    # Substring match fallback
    if not std_ids:
        for key in _PRODUCT_STANDARDS_MAP:
            if key in cat_lower or cat_lower in key:
                std_ids = _PRODUCT_STANDARDS_MAP[key]
                break

    if not std_ids:
        return []

    # Resolve each standard ID to its full entry
    id_to_entry: dict[str, dict] = {e["id"]: e for e in _SEARCH_INDEX}
    results: list[dict] = []
    seen: set[str] = set()
    for std_id in std_ids:
        if std_id in seen:
            continue
        seen.add(std_id)
        entry = id_to_entry.get(std_id)
        if entry:
            results.append({
                "id": entry["id"],
                "title": entry["title"],
                "desc": entry["desc"],
                "type": entry["type"],
                "category": entry["category"],
            })
        else:
            # Standard referenced in map but not in index — include stub
            results.append({
                "id": std_id,
                "title": std_id,
                "desc": f"Referenced standard for {product_category}",
                "type": "UNKNOWN",
                "category": "Unknown",
            })

    return results


# =============================================================================
# KNOWLEDGE INGESTION — stores taxonomy into knowledge.py + rag_store
# =============================================================================

# Assemble full-text content blocks for RAG ingestion

_STANAG_TEXT = """
NATO STANDARDIZATION AGREEMENTS (STANAGs)
==========================================
STANAGs are binding standardisation agreements ratified by NATO member
nations. They ensure interoperability of equipment, doctrine, and
procedures across the Alliance. Ratification is voluntary per nation,
but once ratified a STANAG becomes binding on that nation's forces.

"""

for _cat_key, _cat_data in STANAG_CATEGORIES.items():
    _STANAG_TEXT += f"\n--- {_cat_data['label']} ---\n{_cat_data['description']}\n\n"
    for _std in _cat_data["standards"]:
        _STANAG_TEXT += f"  {_std['stanag']}: {_std['title']}\n    {_std['desc']}\n\n"

_AP_TEXT = """
NATO ALLIED PUBLICATIONS (AP / ATP / AJP / AEP)
=================================================
Allied Publications contain doctrine, procedures, and engineering
standards published under NATO authority. Sub-types:
- ATP: Allied Tactical Publications (tactical doctrine)
- AJP: Allied Joint Publications (joint/combined doctrine)
- AEP: Allied Engineering Publications (engineering standards)

"""

for _pub_key, _pub_data in ALLIED_PUBLICATIONS.items():
    _AP_TEXT += f"\n--- {_pub_data['label']} ---\n{_pub_data['description']}\n\n"
    for _std in _pub_data["standards"]:
        _AP_TEXT += f"  {_std['id']}: {_std['title']}\n    {_std['desc']}\n\n"

_AQAP_TEXT = """
ALLIED QUALITY ASSURANCE PUBLICATIONS (AQAP)
==============================================
AQAPs define quality management requirements for NATO defence
procurement contracts. They build on ISO 9001 (and sector standards
like AS9100) with additional NATO-specific requirements. Every NATO
defence contract references an AQAP tier.

"""

for _std in AQAP_STANDARDS:
    _iso = f" (basis: {_std['iso_basis']})" if _std.get("iso_basis") else ""
    _AQAP_TEXT += f"  {_std['id']}: {_std['title']}{_iso}\n    {_std['desc']}\n\n"

_AECTP_TEXT = """
ALLIED ENVIRONMENTAL CONDITIONS AND TEST PUBLICATIONS (AECTP)
===============================================================
AECTPs define environmental conditions and test methods for military
materiel. Referenced by STANAG 4370, they cover climatic, mechanical,
and electrical environmental testing. Any equipment sold to NATO
forces must demonstrate compliance with relevant AECTP test methods.

"""

for _std in AECTP_STANDARDS:
    _AECTP_TEXT += f"  {_std['id']}: {_std['title']}\n    {_std['desc']}\n\n"

ALL_SECTIONS: dict[str, dict] = {
    "stanag": {
        "content": _STANAG_TEXT,
        "tags": [
            "STANAG", "standardization-agreement", "NATO-interoperability",
            "ammunition", "UAS", "communications", "CBRN", "ballistic",
            "vehicle", "logistics", "weapons",
        ],
        "domain": "nato_standards",
    },
    "allied_publications": {
        "content": _AP_TEXT,
        "tags": [
            "ATP", "AJP", "AEP", "allied-publication", "doctrine",
            "tactics", "joint-operations", "engineering",
        ],
        "domain": "nato_standards",
    },
    "aqap": {
        "content": _AQAP_TEXT,
        "tags": [
            "AQAP", "quality-assurance", "ISO-9001", "AS9100",
            "defence-procurement", "QMS", "GQA", "software-quality",
        ],
        "domain": "nato_standards",
    },
    "aectp": {
        "content": _AECTP_TEXT,
        "tags": [
            "AECTP", "environmental-testing", "climatic", "vibration",
            "shock", "EMC", "temperature", "humidity", "STANAG-4370",
        ],
        "domain": "nato_standards",
    },
}


async def ingest_to_knowledge() -> dict:
    """Store the NATO standards taxonomy into the knowledge + RAG stores.

    Ingests all sections (STANAGs, Allied Publications, AQAPs, AECTPs)
    as permanent CONFIRMED facts via knowledge.store_fact, and as RAG
    documents via rag_store.ingest_document for semantic retrieval.

    Similar pattern to dd_case_library.ingest_all_cases().

    Called from main.py _seed_knowledge_bg at startup or via manual
    /api/aria/knowledge/reseed endpoint.
    """
    from . import knowledge, rag_store

    results: dict = {}
    total_chunks = 0

    for section_name, data in ALL_SECTIONS.items():
        try:
            # Store as permanent knowledge fact
            await knowledge.store_fact(
                topic=f"nato_standards_{section_name}",
                content=data["content"],
                source=f"nato_standards:{section_name}",
                confidence="CONFIRMED",
            )

            # Ingest into RAG store for semantic search
            result = await rag_store.ingest_document(
                text=data["content"],
                source=f"nato_standards:{section_name}",
                source_type="nato_standards",
                title=f"NATO Standards — {section_name.replace('_', ' ').title()}",
                url=f"internal://aria/nato_standards/{section_name}",
                extra_metadata={
                    "domain": data["domain"],
                    "tags": ",".join(data["tags"]),
                    "module": "nato_standards",
                    "module_version": "1.0",
                },
            )
            chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
            total_chunks += chunks
            results[section_name] = {"status": "OK", "chunks": chunks}
            logger.info("NATO standards section ingested: %s (%d chunks)",
                        section_name, chunks)
        except Exception as e:
            results[section_name] = {"status": "ERROR", "error": str(e)}
            logger.error("NATO standards ingestion failed [%s]: %s",
                         section_name, e)

    success = sum(1 for v in results.values() if v.get("status") == "OK")
    logger.info("NATO standards ingestion: %d/%d sections, %d total chunks",
                success, len(ALL_SECTIONS), total_chunks)

    # R-F996 — wire to brain
    from .engine_wiring import wire_success  # R-F1349: was missing → NameError
    wire_success(
        module="nato_standards",
        summary="NATO standards",
        source_id="nato_standards:R-F996",
    )
    return {
        "sections_ingested": success,
        "total_sections": len(ALL_SECTIONS),
        "total_chunks": total_chunks,
        "detail": results,
    }
