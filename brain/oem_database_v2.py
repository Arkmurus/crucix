"""
CRUCIX — Global OEM Intelligence Database v2.0
═══════════════════════════════════════════════════════════════════════════
100+ defence manufacturers across 22 countries — verified April 2026.

DATA VERIFICATION POLICY:
  Every entry in this database includes:
    data_verified_date  — when the entry was last checked against open sources
    data_sources        — verifiable URLs or named publications
    data_confidence     — HIGH (primary source) / MEDIUM (secondary) / LOW (inference)

  ARIA monitors the sources listed in OEM_MONITORING_SOURCES below and
  flags entries where data is >90 days old for re-verification.

PRIMARY SOURCES USED (all publicly verifiable):
  - Company annual reports / investor releases
  - DefenceWeb (defenceweb.co.za) — Southern Africa specialist
  - Defense News (defensenews.com) — US/NATO specialist
  - SIPRI Arms Transfers Database (sipri.org)
  - NCACC Annual Reports (South Africa)
  - C4Defence (c4defence.com) — Turkey specialist
  - Daily Sabah / Anadolu Agency — Turkey
  - Nordic Defence Review (nordicdefencereview.com)
  - Janes Defence (janes.com) — global
  - Flight Global (flightglobal.com)
  - Company press releases on PR Newswire / Business Wire

STRUCTURE:
  Section 1 — UK Manufacturers
  Section 2 — France
  Section 3 — Germany
  Section 4 — Nordic (Sweden, Norway, Finland)
  Section 5 — Other Western/NATO European
  Section 6 — Israel
  Section 7 — Turkey
  Section 8 — South Africa
  Section 9 — Brazil
  Section 10 — India
  Section 11 — UAE / Gulf
  Section 12 — South Korea
  Section 13 — USA (ITAR-controlled — tracked, not primary route)
  Section 14 — Competitors (China, Russia — track only, never pitch)

Last full review: April 2026
═══════════════════════════════════════════════════════════════════════════
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
import json
import logging
from datetime import datetime, date

logger = logging.getLogger("crucix.oem_database")

# ── Compliance Constants ───────────────────────────────────────────────────────
ITAR    = "ITAR_CONTROLLED"      # US Munitions List — DSP-5 required, min 60 days
EAR99   = "EAR99"               # No US export licence for most destinations
EAR_CCL = "EAR_CCL"             # EAR Commerce Control List — needs BIS licence
EU_DU   = "EU_DUAL_USE"         # EU Regulation 2021/821 (updated Nov 2025)
UK_ML   = "UK_MILITARY_LIST"    # UK Strategic Export Control Lists
OGEL    = "UK_OGEL_ELIGIBLE"    # Open General Export Licence
SIEL    = "UK_SIEL_REQUIRED"    # Standard Individual Export Licence (20-60 days)
MTCR    = "MTCR_CONTROLLED"     # Missile Technology Control Regime
UNRESTR = "UNRESTRICTED"        # No export licence required from OEM's country
NCACC   = "NCACC_APPROVED"      # South Africa NCACC export controlled
SSBD    = "TURKISH_SSB"         # Turkish Presidency of Defence Industries approval required

# ── Relationship Constants ─────────────────────────────────────────────────────
REL_NONE    = "none"
REL_AWARE   = "aware"
REL_CONTACT = "contacted"
REL_MOU     = "mou"
REL_ACTIVE  = "active_partner"


@dataclass
class OEMEntry:
    id:                      str
    name:                    str
    country_of_origin:       str
    region:                  str
    capabilities:            List[str]
    product_lines:           List[Dict]
    export_regime:           List[str]
    itar_controlled:         bool = False
    mtcr_controlled:         bool = False
    eu_arms_embargo_applies: bool = False
    proven_in:               List[str] = field(default_factory=list)
    attempted_in:            List[str] = field(default_factory=list)
    blocked_in:              List[str] = field(default_factory=list)
    known_agent_agreements:  List[Dict] = field(default_factory=list)
    contact_route:           str  = "direct"
    arkmurus_relationship:   str  = REL_NONE
    arkmurus_contact:        Optional[str] = None
    competitor_only:         bool = False
    lusophone_experience:    bool = False
    cplp_certifications:     List[str] = field(default_factory=list)
    # Verification metadata
    data_verified_date:      str  = "2026-04"
    data_confidence:         str  = "HIGH"
    data_sources:            List[str] = field(default_factory=list)
    # Live intelligence fields (updated by ARIA monitoring)
    revenue_latest:          Optional[str] = None
    order_backlog:           Optional[str] = None
    recent_notable_contracts: List[str] = field(default_factory=list)
    export_policy_notes:     str = ""
    notes:                   str = ""


class OEMDatabase:
    """
    Global OEM intelligence database — 100+ manufacturers verified April 2026.
    
    ARIA calls this database via:
      /api/brain/oem/search?capability=counter-IED&destination=Mozambique
      /api/brain/oem/unrestricted
      /api/brain/oem/lusophone
      /api/brain/oem/by-country/South Africa
      /api/brain/oem/stats
    
    Self-monitoring: ARIA flags entries >90 days old for re-verification
    and pulls from OEM_MONITORING_SOURCES on each weekly sweep.
    """

    def __init__(self):
        self._oems: Dict[str, OEMEntry] = {}
        self._build()
        logger.info(f"OEM Database loaded: {self.count()} manufacturers across "
                    f"{len(set(o.country_of_origin for o in self._oems.values()))} countries")

    def _add(self, oem: OEMEntry):
        self._oems[oem.id] = oem

    def _build(self):

        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 1 — UNITED KINGDOM
        # Primary regime: UK ECJU (SIEL/OGEL). Post-Brexit autonomous controls.
        # Key update: Export Control (Amendment)(No.2) Regulations 2025 (Dec 2025)
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="BAE_SYSTEMS", name="BAE Systems plc",
            country_of_origin="UK", region="Western/NATO",
            capabilities=["naval","submarines","armoured_vehicles","electronic_warfare",
                          "munitions","fighter_aircraft","C4ISR","cyber","space"],
            product_lines=[
                {"name":"Type 26 Frigate","export_class":SIEL},
                {"name":"CV90 IFV (via joint venture with Rheinmetall)","export_class":SIEL},
                {"name":"Challenger 3 MBT","export_class":SIEL},
                {"name":"Typhoon Eurofighter (consortium)","export_class":SIEL},
                {"name":"APATS munitions","export_class":OGEL},
                {"name":"Brimstone missile (with MBDA)","export_class":SIEL},
            ],
            export_regime=[SIEL, UK_ML],
            itar_controlled=False,
            proven_in=["Saudi Arabia","Australia","Oman","Qatar","Kuwait","USA","Norway",
                       "Canada","India","South Africa"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            revenue_latest="£24.2bn (2025)",
            order_backlog="£75bn (H2 2025)",
            recent_notable_contracts=[
                "Norway Type 26 frigate £10bn (2025)",
                "US laser-guidance kits $1.7bn (2025)",
                "Dreadnought submarine programme (ongoing)",
                "AUKUS submarine programme — prime contractor UK/Australia",
            ],
            export_policy_notes="UK SIEL required for all military exports. BAE has extensive "
                                 "export compliance infrastructure — fast EUC coordination. "
                                 "No ITAR on UK-origin BAE products unless US components.",
            data_sources=["BAE Systems Annual Report 2025","Defense News Dec 2025",
                          "MatrixBCG analysis March 2026"],
            notes="Europe's largest defence contractor. 45% of revenue from US DoD. "
                  "Strong in UK/Australia/Saudi. Limited Africa track record. "
                  "GCAP (next-gen fighter) partner with Leonardo and Mitsubishi.",
        ))

        self._add(OEMEntry(
            id="CHEMRING", name="Chemring Group plc",
            country_of_origin="UK", region="Western/NATO",
            capabilities=["counter-IED","countermeasures","pyrotechnics","munitions",
                          "sensors","energetic_materials"],
            product_lines=[
                {"name":"HaVy IED defeat","export_class":SIEL},
                {"name":"Mortar illumination rounds","export_class":OGEL},
                {"name":"Decoy flares/chaff","export_class":SIEL},
            ],
            export_regime=[SIEL, OGEL, UK_ML],
            itar_controlled=False,
            proven_in=["USA","Australia","Saudi Arabia","Norway","Germany"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["Chemring Annual Report 2025"],
            notes="Counter-IED and survivability specialist. Strong US and NATO market. "
                  "Potential for African markets needing C-IED.",
        ))

        self._add(OEMEntry(
            id="MBDA_UK", name="MBDA (UK arm)",
            country_of_origin="UK", region="Western/NATO",
            capabilities=["missiles","air_defence","anti_ship","precision_strike",
                          "loitering_munitions","C4ISR"],
            product_lines=[
                {"name":"Meteor BVRAAM","export_class":SIEL},
                {"name":"Brimstone air-to-surface","export_class":SIEL},
                {"name":"ASRAAM","export_class":SIEL},
                {"name":"Sea Venom","export_class":SIEL},
                {"name":"SPEAR 3 (in development)","export_class":SIEL},
            ],
            export_regime=[SIEL, UK_ML, EU_DU],
            itar_controlled=False,
            proven_in=["UK","France","Germany","Italy","Saudi Arabia","India","Qatar",
                       "Kuwait","Oman"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="MBDA is a joint venture (BAE 37.5%, Airbus 37.5%, Leonardo 25%). "
                                 "Export requires approval from all three partner nation governments "
                                 "(UK, France, Italy) simultaneously — adds complexity and timeline.",
            data_sources=["MBDA company website","Janes Missiles & Rockets 2025"],
            notes="Europe's leading missile house. Four-nation approval process means timeline "
                  "longer than single-nation OEMs. Strong air defence and precision strike.",
        ))

        self._add(OEMEntry(
            id="QINETIQ", name="QinetiQ Group plc",
            country_of_origin="UK", region="Western/NATO",
            capabilities=["C4ISR","cyber","EW","UAS","surveillance","training","testing"],
            product_lines=[
                {"name":"TITAN UAV","export_class":SIEL},
                {"name":"Optus EW system","export_class":SIEL},
            ],
            export_regime=[SIEL, UK_ML],
            itar_controlled=False,
            proven_in=["USA","Australia","Germany","UK"],
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["QinetiQ Annual Report 2025"],
            notes="Defence science and technology. Growing UAS portfolio. Testing and evaluation services.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 2 — FRANCE
        # Primary regime: French DGA (Direction Générale de l'Armement)
        # France has extensive Africa track record — generally faster approvals for Africa
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="THALES_FR", name="Thales Group",
            country_of_origin="France", region="Western/NATO",
            capabilities=["C4ISR","radar","air_defence","communications","cyber",
                          "surveillance","avionics","naval_systems","satellites"],
            product_lines=[
                {"name":"Ground Master radar family","export_class":"French DGA"},
                {"name":"CONTACT tactical communications","export_class":"French DGA"},
                {"name":"Bushmaster APC (via partnership)","export_class":"French DGA"},
                {"name":"Crotale NG air defence","export_class":"French DGA"},
            ],
            export_regime=[EU_DU, UK_ML],
            itar_controlled=False,
            proven_in=["Saudi Arabia","UAE","Egypt","Morocco","Australia","UK",
                       "France","Germany","Qatar","Angola","Nigeria","Senegal"],
            lusophone_experience=True,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            revenue_latest="€22bn (2025)",
            order_backlog="Record (2025)",
            recent_notable_contracts=[
                "UK MoD: 5,000 Starstreak air defence missiles",
                "Germany MoD: portable land radars (unnamed third party, likely Ukraine)",
                "Unnamed European country: 70mm ammunition",
                "Airbus/Leonardo space activities merger planned",
            ],
            export_policy_notes="French DGA export licence required. France has strong Africa "
                                 "relationships and faster processing for Francophone/Lusophone "
                                 "Africa. French political will generally supports Africa sales.",
            data_sources=["Thales investor update March 2026","Irish News March 2026"],
            notes="52% defence revenue. France historically supportive of Africa sales. "
                  "Strong radar, C4ISR, and naval electronics. Good route for Lusophone Africa C4ISR.",
        ))

        self._add(OEMEntry(
            id="KNDS", name="KNDS (Nexter + KMW)",
            country_of_origin="France", region="Western/NATO",
            capabilities=["armoured_vehicles","artillery","MBT","IFV","APC","ammunition"],
            product_lines=[
                {"name":"Leclerc MBT","export_class":"French DGA + German BAFA"},
                {"name":"VBCI IFV","export_class":"French DGA"},
                {"name":"Caesar wheeled howitzer","export_class":"French DGA"},
                {"name":"Leopard 2A8 (KMW)","export_class":"German BAFA"},
                {"name":"PzH 2000 SP howitzer (KMW)","export_class":"German BAFA"},
                {"name":"RCH 155 (joint KNDS-Rheinmetall)","export_class":"French DGA + German BAFA"},
            ],
            export_regime=[EU_DU, UK_ML],
            itar_controlled=False,
            proven_in=["France","Germany","Saudi Arabia","Morocco","Indonesia",
                       "Denmark","Belgium","Czech Republic","Hungary","Netherlands"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            recent_notable_contracts=[
                "Leopard 2A8: multiple billion-euro NATO orders 2025",
                "Caesar: €200m Morocco deal 2025 (Elbit comparable deal Feb 2025)",
                "KNDS-Rheinmetall: €3.4bn Germany+Netherlands 200+ IFV Boxer contract Oct 2025",
            ],
            export_policy_notes="KNDS requires BOTH French DGA (Nexter side) AND German BAFA "
                                 "(KMW side) approval. Germany's Africa policy more cautious. "
                                 "Caesar is France-only — single DGA approval, faster.",
            data_sources=["Defense News Dec 2025","DefenceWeb 2025"],
            notes="Franco-German land systems giant. Caesar howitzer is France's primary "
                  "Africa artillery offering — good track record. Leopard requires Germany approval.",
        ))

        self._add(OEMEntry(
            id="NAVAL_GROUP", name="Naval Group (Chantiers de l'Atlantique)",
            country_of_origin="France", region="Western/NATO",
            capabilities=["naval","frigates","submarines","OPV","patrol_vessels","naval_systems"],
            product_lines=[
                {"name":"FDI frigate (Frégate de Défense et d'Intervention)","export_class":"French DGA"},
                {"name":"Barracuda submarine","export_class":"French DGA"},
                {"name":"Scorpène submarine","export_class":"French DGA"},
                {"name":"OPV (offshore patrol vessels)","export_class":"French DGA"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["Greece","Egypt","Morocco","Portugal","Brazil","India","Chile"],
            lusophone_experience=True,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            recent_notable_contracts=[
                "Challenging year 2025 — lost Canada submarine bid (Hanwha won)",
                "Lost Poland A26 submarine bid (Saab won)",
                "Continuing FDI frigate deliveries to France and Greece",
            ],
            export_policy_notes="French DGA approval. Naval Group has strong Lusophone Africa "
                                 "relationships (Portugal, Brazil partnership). "
                                 "Difficult year 2025 losing major export competitions.",
            data_sources=["Defense News Dec 2025 review","Naval Group press releases"],
            notes="France's main naval shipbuilder. Lost several major 2025 export competitions. "
                  "Portugal partnership is key Lusophone entry point.",
        ))

        self._add(OEMEntry(
            id="DASSAULT", name="Dassault Aviation",
            country_of_origin="France", region="Western/NATO",
            capabilities=["fighter_aircraft","UCAV","ISR","maritime_patrol"],
            product_lines=[
                {"name":"Rafale multirole fighter","export_class":"French DGA"},
                {"name":"nEUROn UCAV demonstrator","export_class":"French DGA (R&D)"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["France","India","Egypt","UAE","Qatar","Greece","Croatia",
                       "Indonesia","Serbia"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            recent_notable_contracts=[
                "Indonesia: additional Rafale contract 2025",
                "FCAS next-gen fighter: stalled due to Dassault-Airbus disputes",
            ],
            export_policy_notes="French DGA approval. Rafale sales driven by French government "
                                 "diplomatic support at head-of-state level. "
                                 "FCAS (6th-gen fighter with Airbus/Indra) facing development disputes.",
            data_sources=["Defense News Dec 2025","Fortune March 2025"],
            notes="Rafale is France's flagship export product. Strong diplomatic push by Élysée. "
                  "Not relevant for Arkmurus Africa focus — too large/expensive for Lusophone Africa.",
        ))

        self._add(OEMEntry(
            id="SAFRAN", name="Safran SA",
            country_of_origin="France", region="Western/NATO",
            capabilities=["aircraft_engines","avionics","optics","EO_systems","navigation",
                          "helicopter_systems","ammunition_fuzes"],
            product_lines=[
                {"name":"M88 Rafale engine","export_class":"French DGA"},
                {"name":"Strix sight system (helicopter)","export_class":"French DGA"},
                {"name":"PASEO optronics","export_class":"French DGA"},
                {"name":"Katana helicopter upgrade","export_class":"French DGA"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["France","India","Brazil","Singapore","Saudi Arabia","UAE","Egypt"],
            lusophone_experience=True,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["Safran Annual Report 2024","Gain report Feb 2026"],
            notes="French aerospace and defence. Key helicopter optics and avionics provider. "
                  "Relevant for helicopter upgrade programmes in Africa.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 3 — GERMANY
        # Primary regime: German BAFA (Bundesamt für Wirtschaft und Ausfuhrkontrolle)
        # Germany applies Rüstungsexportrichtlinien — cautious on Sub-Saharan Africa
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="RHEINMETALL", name="Rheinmetall AG",
            country_of_origin="Germany", region="Western/NATO",
            capabilities=["armoured_vehicles","ammunition","air_defence","artillery",
                          "naval_systems","digital_battlefield","C4ISR"],
            product_lines=[
                {"name":"Lynx IFV","export_class":"German BAFA"},
                {"name":"Skyranger CUAS","export_class":"German BAFA"},
                {"name":"155mm artillery ammunition","export_class":"German BAFA"},
                {"name":"Boxer wheeled platform","export_class":"German BAFA"},
                {"name":"HX truck family","export_class":"German BAFA"},
            ],
            export_regime=[EU_DU, UK_ML],
            itar_controlled=False,
            proven_in=["Germany","Australia","Netherlands","Hungary","Estonia",
                       "Ukraine","UK","Italy","USA"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            revenue_latest="€64bn order book (2025)",
            recent_notable_contracts=[
                "Skyranger anti-drone: Netherlands",
                "Lynx IFV + Boxer: KNDS JV €3.4bn Germany+Netherlands Oct 2025",
                "Germany Armed Forces satellite intelligence (with ICEYE)",
                "Sold civilian auto business — now 100% defence focused",
            ],
            export_policy_notes="German BAFA approval required. Germany applies strict "
                                 "'Rüstungsexportrichtlinien' (arms export guidelines). "
                                 "Sub-Saharan Africa faces higher scrutiny. "
                                 "RDM (SA subsidiary) is better route for Africa ammunition.",
            data_sources=["Defense News Dec 2025","Rheinmetall Q1 2025","Irish News 2026"],
            notes="Germany's largest defence contractor. Sold auto division — now all-defence. "
                  "BAFA approval cautious for Africa. Use RDM (South African subsidiary) for "
                  "Africa ammunition — no German BAFA needed, NCACC applies instead.",
        ))

        self._add(OEMEntry(
            id="HENSOLDT", name="HENSOLDT AG",
            country_of_origin="Germany", region="Western/NATO",
            capabilities=["radar","EW","sensors","optronics","surveillance","C4ISR"],
            product_lines=[
                {"name":"Twinvis passive radar","export_class":"German BAFA"},
                {"name":"SPEXER border surveillance radar","export_class":"German BAFA"},
                {"name":"Kalaetron EW suite","export_class":"German BAFA"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["Germany","Saudi Arabia","UAE","Australia","Spain","South Africa"],
            lusophone_experience=True,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["HENSOLDT AG company website","Hensoldt Optronics South Africa"],
            notes="Hensoldt Optronics (formerly Zeiss) has SA subsidiary — "
                  "provides submarine periscopes and optronics to SANDF. "
                  "Germany BAFA required for export but SA link is useful for Lusophone Africa.",
        ))

        self._add(OEMEntry(
            id="DIEHL", name="Diehl Defence GmbH",
            country_of_origin="Germany", region="Western/NATO",
            capabilities=["air_defence","missiles","ammunition","HVM_systems"],
            product_lines=[
                {"name":"IRIS-T SL air defence","export_class":"German BAFA"},
                {"name":"IRIS-T SLM (medium range)","export_class":"German BAFA"},
                {"name":"IRIS-T SLS (short range)","export_class":"German BAFA"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["Germany","Sweden","Norway","Canada","Egypt","UAE"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["Diehl Defence website","Janes 2025"],
            notes="IRIS-T air defence family is highly capable and in high demand. "
                  "German BAFA cautious on Africa. Not an Arkmurus priority OEM.",
        ))

        self._add(OEMEntry(
            id="AIRBUS_DS", name="Airbus Defence & Space",
            country_of_origin="Germany", region="Western/NATO",
            capabilities=["military_aircraft","satellites","C4ISR","airlift","ISR",
                          "helicopter_systems"],
            product_lines=[
                {"name":"A400M military transport","export_class":"Multi-nation (OCCAR)"},
                {"name":"Eurofighter Typhoon (consortium)","export_class":"Multi-nation"},
                {"name":"C295 maritime patrol","export_class":"French/German/Spanish"},
                {"name":"H125/H145 helicopters","export_class":"French DGAC"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["UK","Germany","France","Spain","Saudi Arabia","Turkey","Kuwait",
                       "Qatar","Oman","Malaysia","India","Senegal","Morocco"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["Airbus Annual Results 2025","Defense News"],
            notes="A400M in service with many NATO allies. C295 strong African track record. "
                  "Multi-nation approval required — slow. Not primary Arkmurus OEM.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 4 — NORDIC (Sweden, Norway, Finland)
        # All NATO members as of 2024. Record defence exports 2025.
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="SAAB", name="Saab AB",
            country_of_origin="Sweden", region="Western/NATO",
            capabilities=["fighter_aircraft","surveillance","CUAS","naval","submarines",
                          "ground_vehicles","EW","C4ISR","UAV"],
            product_lines=[
                {"name":"Gripen fighter (E/F)","export_class":"Swedish ISP"},
                {"name":"A26 Blekinge submarine","export_class":"Swedish ISP"},
                {"name":"GlobalEye AEW&C","export_class":"Swedish ISP"},
                {"name":"Carl-Gustaf man-portable (and ammunition)","export_class":"Swedish ISP"},
                {"name":"ARTHUR counter-battery radar","export_class":"Swedish ISP"},
                {"name":"GIRAFFE radar family","export_class":"Swedish ISP"},
                {"name":"CAMM-ER (with MBDA)","export_class":"Multi-nation"},
            ],
            export_regime=[EU_DU, UK_ML],
            itar_controlled=False,
            proven_in=["Sweden","Brazil","Czech Republic","Hungary","South Africa",
                       "Thailand","Colombia","Switzerland"],
            lusophone_experience=True,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            revenue_latest="SEK 274.5bn order backlog (2025, record)",
            recent_notable_contracts=[
                "France: 2× GlobalEye AEW&C aircraft (2025)",
                "Poland: A26 submarine (Nov 2025, largest submarine export)",
                "Poland: Carl-Gustaf ammo + simulators €1.1bn (2025)",
                "Brazil: Gripen E/F ongoing deliveries",
                "Colombia: Gripen E contract",
            ],
            export_policy_notes="Swedish ISP (Inspectorate for Strategic Products) approval. "
                                 "Sweden joined NATO 2024 — policy now aligned with NATO allies. "
                                 "Historically cautious on Africa but improving. "
                                 "Brazil partnership gives Lusophone Africa credibility.",
            data_sources=["Nordic Defence Review March 2026","Defense News Dec 2025",
                          "Saab Annual Report 2025"],
            notes="Gripen is world's most cost-effective fighter. Brazil partnership key for "
                  "Portuguese-language market. Carl-Gustaf widely used in Africa. "
                  "Strong 2025 performance winning Poland submarine — largest-ever export.",
        ))

        self._add(OEMEntry(
            id="KONGSBERG", name="Kongsberg Defence & Aerospace",
            country_of_origin="Norway", region="Western/NATO",
            capabilities=["naval_strike_missiles","air_defence","NASAMS","CUAS",
                          "C4ISR","maritime_patrol","remote_weapon_stations"],
            product_lines=[
                {"name":"Naval Strike Missile (NSM)","export_class":"Norwegian DND"},
                {"name":"Joint Strike Missile (JSM)","export_class":"Norwegian DND"},
                {"name":"NASAMS air defence system","export_class":"Norwegian DND"},
                {"name":"Protector RWS","export_class":"Norwegian DND"},
                {"name":"San counter-UAS system (with PGZ)","export_class":"Norwegian+Polish"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["Norway","USA","Australia","Netherlands","Qatar","Morocco",
                       "Saudi Arabia","South Korea"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            revenue_latest="NOK 157.4bn order backlog 2025 (~€14bn)",
            recent_notable_contracts=[
                "Poland counter-UAS (with PGZ): NOK 16bn / €1.5bn (Jan 2026)",
                "US Navy: NSM anti-ship missiles",
                "Dutch Army: integrated air defence",
                "Acquires Zone 5 Technologies (USA) Dec 2025 for missile production",
                "Kongsberg Maritime spinning off as independent company April 2026",
            ],
            export_policy_notes="Norwegian DND (Department of National Defence) approval. "
                                 "Norway is NATO member — generally aligned export policy. "
                                 "NASAMS widely used by NATO allies including USA.",
            data_sources=["Nordic Defence Review March 2026","Defense News Feb 2026"],
            notes="Owners of 49.9% of Patria and 50% of Nammo — Nordic defence axis. "
                  "NASAMS is top-tier air defence in significant demand. "
                  "NSM selected by US Navy — strong US relationship. Not Africa-focused.",
        ))

        self._add(OEMEntry(
            id="PATRIA", name="Patria Oyj",
            country_of_origin="Finland", region="Western/NATO",
            capabilities=["armoured_vehicles","APC","mortar_systems","aviation_maintenance",
                          "UAV","life_cycle_support","ELINT"],
            product_lines=[
                {"name":"Patria 6×6 armoured vehicle (CAVS)","export_class":"Finnish Tukes"},
                {"name":"AMV XP (Armored Modular Vehicle)","export_class":"Finnish Tukes"},
                {"name":"NEMO 120mm turreted mortar","export_class":"Finnish Tukes"},
                {"name":"ARIS ELINT system","export_class":"Finnish Tukes"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["Finland","Estonia","Latvia","Sweden","Norway","Germany",
                       "Denmark","UK","Netherlands"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            revenue_latest="€1,086.7m net sales 2025 (+31.6%)",
            order_backlog="€3,526m (2025, record)",
            recent_notable_contracts=[
                "Germany CAVS: serial production €2bn+ Dec 2025",
                "Denmark, UK, Norway: joined CAVS 2025",
                "7-country CAVS programme: ~2,000 vehicles ordered or optioned",
                "F-35 industrial participation: landing gear doors production",
            ],
            export_policy_notes="Finnish Tukes (Safety Investigations Authority) approval. "
                                 "Finland joined NATO March 2023. Growing export base. "
                                 "Owned 50.1% by Finnish government, 49.9% by Kongsberg.",
            data_sources=["Nordic Defence Review March 2026","Patria Annual Report 2025",
                          "Patria website"],
            notes="Core CAVS armoured vehicle programme is Finland's biggest export success. "
                  "Owns 50% of Nammo. AMV XP used by Morocco — Africa track record beginning.",
        ))

        self._add(OEMEntry(
            id="NAMMO", name="Nammo AS",
            country_of_origin="Norway", region="Western/NATO",
            capabilities=["ammunition","rockets","propellants","155mm_artillery",
                          "small_calibre","MANPADS_propulsion","space_propulsion"],
            product_lines=[
                {"name":"155mm Extended Range Ammunition","export_class":"Norwegian DND"},
                {"name":"Multipurpose Ammunition","export_class":"Norwegian DND"},
                {"name":"M72 LAW","export_class":"Norwegian DND"},
                {"name":"Shoulder-launched weapons","export_class":"Norwegian DND"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["Norway","Sweden","Finland","USA","Germany","UK","Australia"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="Norwegian-Finnish owned (45% Norwegian govt, 27.5% Patria, "
                                 "27.5% Saab). Premium NATO-standard ammunition. "
                                 "Direct competitor to RDM in 155mm market.",
            data_sources=["Nammo annual report 2024","Nordic Defence Review 2026"],
            notes="Nammo's 155mm rounds compete directly with RDM in NATO markets. "
                  "Not an Arkmurus primary OEM but important competitor awareness.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 5 — OTHER WESTERN/NATO EUROPEAN
        # Italy (Leonardo), Austria (Frequentis), Spain (Indra), Czech Republic
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="LEONARDO", name="Leonardo SpA",
            country_of_origin="Italy", region="Western/NATO",
            capabilities=["helicopters","naval_systems","radar","EW","C4ISR","avionics",
                          "UAV","cyber","space","electronics"],
            product_lines=[
                {"name":"AW139/AW149/AW189 helicopters","export_class":"Italian UAMA"},
                {"name":"Falco EVO MALE UAV","export_class":"Italian UAMA"},
                {"name":"LynxBMS (battle management)","export_class":"Italian UAMA"},
                {"name":"FREMM frigate systems","export_class":"Italian UAMA"},
                {"name":"M-346 Master jet trainer","export_class":"Italian UAMA"},
            ],
            export_regime=[EU_DU, UK_ML],
            itar_controlled=False,
            proven_in=["Italy","UK","Angola","Mozambique","Nigeria","Kenya","Morocco",
                       "Saudi Arabia","UAE","Qatar","India","Israel","USA"],
            lusophone_experience=True,
            cplp_certifications=["Angola","Mozambique","Cape Verde"],
            contact_route="direct",
            arkmurus_relationship=REL_AWARE,
            revenue_latest="€20.9bn (2025)",
            order_backlog="€44bn (2025)",
            recent_notable_contracts=[
                "GCAP (6th-gen fighter) partner with BAE and Mitsubishi",
                "LBA Systems JV with Baykar for EU UAV market",
                "Italy Lynx IFV: first order from Italy (with Rheinmetall)",
                "Thales space merger planned (Thales Alenia Space)",
            ],
            export_policy_notes="Italian UAMA (Unità per le Autorizzazioni dei Materiali "
                                 "d'Armamento) approval required. Italy has strong Africa track "
                                 "record — particularly helicopter sales. "
                                 "30% Italian government owned — political reliability high.",
            data_sources=["Leonardo investor day 2025","Fortune March 2025","Irish News 2026"],
            notes="CRITICAL: Proven Lusophone Africa presence with helicopters. "
                  "AW139/AW149 in service ANGOLA (FAA), MOZAMBIQUE (FADM). "
                  "Excellent Arkmurus OEM — established relationships, proven supply chain. "
                  "GCAP and Baykar (LBA JV) partnerships enhance drone capability.",
        ))

        self._add(OEMEntry(
            id="FREQUENTIS", name="Frequentis AG",
            country_of_origin="Austria", region="Western/NATO",
            capabilities=["air_traffic_management","communications","C2_systems",
                          "coastal_surveillance","maritime_safety"],
            product_lines=[
                {"name":"VCS voice communication systems","export_class":"Austrian BMEIA"},
                {"name":"Coast Guard communication suite","export_class":"Austrian BMEIA"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["Austria","Germany","UK","Australia","USA","UAE"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["Frequentis website"],
            notes="Civil/military ATC and communications. Limited direct defence relevance "
                  "for Lusophone Africa military procurement.",
        ))

        self._add(OEMEntry(
            id="INDRA", name="Indra Sistemas SA",
            country_of_origin="Spain", region="Western/NATO",
            capabilities=["radar","C4ISR","EW","air_traffic","communications",
                          "cyber","simulation"],
            product_lines=[
                {"name":"LANZA 3D radar","export_class":"Spanish SDSPNSD"},
                {"name":"INDRAnet C4ISR suite","export_class":"Spanish SDSPNSD"},
                {"name":"SIROCC border surveillance","export_class":"Spanish SDSPNSD"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["Spain","Angola","Nigeria","Morocco","Saudi Arabia","UAE",
                       "Chile","Colombia"],
            lusophone_experience=True,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="Spanish SDSPNSD approval. Spain has growing Africa engagement. "
                                 "Iberian Peninsula relationship with Lusophone markets is an asset.",
            data_sources=["Indra Annual Report 2024","DefenceWeb 2025"],
            notes="INDRA proven in Angola — SIROCC border radar in service. "
                  "Good C4ISR and radar option for Lusophone Africa. "
                  "Spain-Africa diplomatic relations supporting export.",
        ))

        self._add(OEMEntry(
            id="EXCALIBUR_ARMY", name="Excalibur Army sro",
            country_of_origin="Czech Republic", region="Western/NATO",
            capabilities=["armoured_vehicles","APC","artillery","vehicle_upgrades",
                          "munitions","military_trucks"],
            product_lines=[
                {"name":"Pandur II wheeled APC","export_class":"Czech ÚOVZK"},
                {"name":"SpGH DANA M2 wheeled howitzer","export_class":"Czech ÚOVZK"},
                {"name":"T-72 upgrades","export_class":"Czech ÚOVZK"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["Czech Republic","Slovakia","Saudi Arabia","Libya","Georgia"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="Czech Republic ÚOVZK approval. Eastern European OEM — "
                                 "generally less restrictive than Western European on Africa. "
                                 "Post-Soviet design heritage familiar to African militaries.",
            data_sources=["DefenseNews 2025","Jane's 2025"],
            notes="Czech Eastern European OEM with competitive pricing. "
                  "Good option for African markets wanting wheeled systems at lower cost. "
                  "Less scrutinised by human rights concerns than German/French OEMs.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 6 — ISRAEL
        # Export regime: Israeli DECA (Defence Export Control Agency)
        # KEY: Most Israeli systems contain US components → ITAR DSP-5 required
        # EXCEPTION: Products using Israeli-developed components only may be ITAR-free
        # Recent development: Gaza conflict creating international export scrutiny
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="ELBIT", name="Elbit Systems Ltd",
            country_of_origin="Israel", region="Israeli",
            capabilities=["C4ISR","EW","UAV","avionics","EO_systems","armoured_vehicles",
                          "artillery","night_vision","DIRCM","ISTAR","SIGINT"],
            product_lines=[
                {"name":"Hermes 900 MALE UAV","export_class":"Israeli DECA"},
                {"name":"Hermes 450 tactical UAV","export_class":"Israeli DECA"},
                {"name":"J-MUSIC DIRCM","export_class":"Israeli DECA"},
                {"name":"LYNX APC upgrade","export_class":"Israeli DECA"},
                {"name":"ATMOS 155mm self-propelled howitzer","export_class":"Israeli DECA"},
                {"name":"SPEAR EW suite","export_class":"Israeli DECA"},
                {"name":"TORCH-X battle management","export_class":"Israeli DECA"},
            ],
            export_regime=[SIEL, EU_DU],
            itar_controlled=True,
            proven_in=["USA","Serbia","Germany","Philippines","Azerbaijan","Rwanda",
                       "Morocco","Kenya","Nigeria","Togo","Uganda","Cameroon"],
            lusophone_experience=True,
            contact_route="direct",
            arkmurus_relationship=REL_AWARE,
            revenue_latest="$7,938.6m (FY2025)",
            order_backlog="$28.1bn (Dec 2025)",
            recent_notable_contracts=[
                "$2.3bn UAE contract (8yr, company's largest ever) Nov 2025",
                "$1.635bn Serbia contract: precision artillery, UAVs, ISTAR (2025)",
                "$435m international land systems contract Feb 2026",
                "$275m Asia-Pacific DIRCM helicopter protection Jan 2026",
                "$150m additional European DIRCM contracts",
                "$210m Israel MoD Merkava tank upgrades",
            ],
            export_policy_notes="Israeli DECA approval required. Significant US component content "
                                 "in most Elbit systems — ITAR DSP-5 adds 60-120 days. "
                                 "Gaza conflict 2023-ongoing creating scrutiny in some markets — "
                                 "verify buyer position on Israel relations before approach. "
                                 "Elbit Africa track record strong: Kenya, Nigeria, Rwanda, Uganda.",
            data_sources=["Elbit investor releases 2025-2026","Global Defense Corp Dec 2025",
                          "PRNewswire Jan-Feb 2026"],
            notes="Record revenue and backlog 2025. J-MUSIC DIRCM in high demand for aircraft "
                  "protection. Strong Africa track record — good Arkmurus OEM if buyer accepts "
                  "Israeli-origin and ITAR timeline is manageable. "
                  "IMI Systems now a fully-owned Elbit subsidiary (2018 acquisition).",
        ))

        self._add(OEMEntry(
            id="RAFAEL", name="Rafael Advanced Defense Systems",
            country_of_origin="Israel", region="Israeli",
            capabilities=["air_defence","missiles","precision_munitions","EO","ISTAR",
                          "naval_systems","C-RAM","active_protection"],
            product_lines=[
                {"name":"Iron Dome (with Raytheon US)","export_class":"Israeli DECA + ITAR"},
                {"name":"Barak 8 naval air defence (with IAI)","export_class":"Israeli DECA + ITAR"},
                {"name":"Spike ATGM family","export_class":"Israeli DECA"},
                {"name":"Trophy APS","export_class":"Israeli DECA"},
                {"name":"SPICE precision guidance kit","export_class":"Israeli DECA"},
                {"name":"C-DOME naval Iron Dome","export_class":"Israeli DECA + ITAR"},
            ],
            export_regime=[SIEL, EU_DU],
            itar_controlled=True,
            proven_in=["India","Germany","Romania","Singapore","Poland","Azerbaijan",
                       "Morocco","Nigeria"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            recent_notable_contracts=[
                "Romania: Barak MX / short-range air defence €2.2bn (June 2025)",
            ],
            data_sources=["Global Defense Corp Dec 2025","Rafael company website"],
            notes="State-owned Israeli OEM. Spike ATGM widely proliferated. "
                  "Iron Dome co-production with Raytheon makes it doubly ITAR-controlled. "
                  "Trophy APS widely adopted for vehicle protection.",
        ))

        self._add(OEMEntry(
            id="IAI", name="Israel Aerospace Industries (IAI)",
            country_of_origin="Israel", region="Israeli",
            capabilities=["UAV","missiles","air_defence","naval","satellites",
                          "radar","EO","SIGINT","cyber"],
            product_lines=[
                {"name":"Heron TP MALE UAV","export_class":"Israeli DECA"},
                {"name":"Heron 1 tactical UAV","export_class":"Israeli DECA"},
                {"name":"Harop loitering munition","export_class":"Israeli DECA"},
                {"name":"Barak 8 / LRSAM (with Rafael)","export_class":"Israeli DECA + ITAR"},
                {"name":"Arrow 3 BMD (with Boeing)","export_class":"Israeli DECA + ITAR"},
                {"name":"ELM-2084 radar","export_class":"Israeli DECA"},
            ],
            export_regime=[SIEL, EU_DU],
            itar_controlled=True,
            proven_in=["India","Germany","Azerbaijan","Morocco","France","Singapore",
                       "Nigeria","Kenya","Ethiopia"],
            lusophone_experience=True,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            recent_notable_contracts=[
                "Germany: Arrow 3 BMD $4.6bn (2023, largest Israeli export ever at time)",
            ],
            data_sources=["Global Defense Corp","IAI company website","Jane's 2025"],
            notes="State-owned. Arrow 3 sale to Germany was landmark. "
                  "Harop loitering munition used by Azerbaijan in Nagorno-Karabakh. "
                  "Africa track record in Nigeria, Kenya, Ethiopia — relevant for Arkmurus.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 7 — TURKEY
        # Export regime: Turkish Presidency of Defence Industries (SSB)
        # KEY ADVANTAGE: No ITAR content — Turkey uses domestic components aggressively.
        # Turkey NATO member but operates independently of Western export restrictions.
        # 2025 exports: $10.054bn (record). Baykar: $2.2bn (88% export revenue).
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="BAYKAR", name="Baykar Makina",
            country_of_origin="Turkey", region="Turkish",
            capabilities=["UAV","UCAV","armed_drones","ISR","loitering_munitions"],
            product_lines=[
                {"name":"Bayraktar TB2 (tactical MALE UAV)","export_class":SSBD},
                {"name":"Bayraktar TB3 (naval variant)","export_class":SSBD},
                {"name":"Bayraktar Akinci UCAV (MALE armed)","export_class":SSBD},
                {"name":"Bayraktar Kizilelma (unmanned fighter — 2026)","export_class":SSBD},
            ],
            export_regime=[SSBD],
            itar_controlled=False,
            mtcr_controlled=True,
            proven_in=["Ukraine","Poland","Morocco","Ethiopia","Libya","Somalia","Togo",
                       "Djibouti","Nigeria","Niger","Azerbaijan","Saudi Arabia","UAE",
                       "Pakistan","Kazakhstan","Kyrgyzstan","Rwanda","Albania","Kosovo"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_AWARE,
            revenue_latest="$2.5bn total, $2.2bn exports (2025, record)",
            recent_notable_contracts=[
                "Export agreements: 37 countries (TB2: 36, Akinci: 16)",
                "Italy: Acquired Piaggio Aerospace (2025)",
                "LBA Systems JV with Leonardo for European UAV market",
                "Sudan armed forces: TB2 in active service",
            ],
            export_policy_notes="Turkish SSB approval required. No ITAR content — significant "
                                 "advantage over US/Western UAVs. Erdoğan personally promotes "
                                 "Baykar in foreign visits — political support is built in. "
                                 "MTCR applies to Akinci but Turkey interprets permissively. "
                                 "TB2 proved in Africa: Ethiopia, Somalia, Libya, Togo, Niger. "
                                 "CONCERN: TB2 used in conflict zones — some buyers face scrutiny.",
            data_sources=["Turkiye Today Feb 2026","C4Defence March 2026",
                          "Daily Sabah Dec 2025","Turkish Minute Dec 2025"],
            notes="World's largest armed drone exporter (3 consecutive years). "
                  "TB2 at $5m vs Predator at $30m — price point Africa can afford. "
                  "CRITICAL for Arkmurus: TB2 in active service across Africa. "
                  "Kizilelma entering inventory 2026 — next generation.",
        ))

        self._add(OEMEntry(
            id="ASELSAN", name="Aselsan AŞ",
            country_of_origin="Turkey", region="Turkish",
            capabilities=["EW","communications","radar","C4ISR","avionics","EO",
                          "naval_systems","land_systems","anti_drone"],
            product_lines=[
                {"name":"ASELFLIR-500 EO/IR targeting pod","export_class":SSBD},
                {"name":"EJDERHA anti-drone system (2025)","export_class":SSBD},
                {"name":"SMASH 30mm naval gun","export_class":SSBD},
                {"name":"MAR-D naval 3D radar","export_class":SSBD},
                {"name":"VURAL EW system","export_class":SSBD},
            ],
            export_regime=[SSBD],
            itar_controlled=False,
            proven_in=["Turkey","Poland","Malaysia","Qatar","UAE","Saudi Arabia",
                       "Azerbaijan","Kazakhstan"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            revenue_latest="$3.47bn revenue (SIPRI 2024) — world rank #47",
            recent_notable_contracts=[
                "ASELFLIR-500 exported to 16 countries (2024)",
                "Poland: electronic warfare and radar systems contracts",
                "Malaysia: SMASH 30mm gun (Littoral Mission Ships)",
                "Total exports 2024: $508m direct + indirect",
            ],
            export_policy_notes="Turkish SSB approval. No ITAR content — major advantage. "
                                 "ASELSAN revenues exceed entire defence budgets of some buyers "
                                 "(Nigeria $3.16bn, Peru $3.42bn). "
                                 "Strong NATO electronic standards compliance.",
            data_sources=["SIPRI Top 100 2024","C4Defence March 2026","SSB report 2025"],
            notes="Turkey's largest defence electronics company. ASELFLIR targeting pod "
                  "widely integrated on Turkish and export platforms. "
                  "Ejderha anti-drone system 2025 launch — CUAS is a growing Africa need.",
        ))

        self._add(OEMEntry(
            id="ROKETSAN", name="Roketsan AŞ",
            country_of_origin="Turkey", region="Turkish",
            capabilities=["missiles","rockets","artillery_rockets","MANPADS","cruise_missiles",
                          "loitering_munitions","ballistic_missiles"],
            product_lines=[
                {"name":"CIRIT 70mm laser-guided rocket","export_class":SSBD},
                {"name":"KHAN ballistic missile system","export_class":SSBD},
                {"name":"Sungur MANPADS","export_class":SSBD},
                {"name":"Tayfun Block-4 hypersonic missile","export_class":SSBD},
                {"name":"ÇAKIR cruise missile (150km)","export_class":SSBD},
                {"name":"EREN loitering munition","export_class":SSBD},
            ],
            export_regime=[SSBD],
            itar_controlled=False,
            mtcr_controlled=True,
            proven_in=["Turkey","Indonesia","Malaysia","Qatar","Azerbaijan"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            revenue_latest=">$750m exports (2025), +50% year-on-year",
            recent_notable_contracts=[
                "Kale Jet turbojet engine exported to Brazil (2025) — first Turkish engine export",
                "Indonesia: Khan ballistic missile system (2025)",
                "Malaysia: signed; Philippines discussions",
            ],
            data_sources=["C4Defence March 2026","Daily Sabah Dec 2025","Turkish Minute Dec 2025"],
            notes="Roketsan +50% export growth 2025. First Turkish engine export (Brazil). "
                  "Tayfun hypersonic missile unveiled 2025. "
                  "MTCR controlled for longer-range systems. Good for short-range rockets/MANPADS.",
        ))

        self._add(OEMEntry(
            id="FNSS", name="FNSS Defence Systems",
            country_of_origin="Turkey", region="Turkish",
            capabilities=["armoured_vehicles","IFV","APC","tracked_vehicles"],
            product_lines=[
                {"name":"ACV-15 IFV","export_class":SSBD},
                {"name":"Kaplan MT tracked IFV","export_class":SSBD},
                {"name":"Pars wheeled APC","export_class":SSBD},
            ],
            export_regime=[SSBD],
            itar_controlled=False,
            proven_in=["Turkey","Malaysia","Indonesia","Saudi Arabia","Bahrain"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["FNSS website","Janes Armour 2025"],
            notes="Kaplan MT (with Indonesia) — strong SE Asia track record. "
                  "Good option for buyers wanting tracked vehicles without ITAR complexity.",
        ))

        self._add(OEMEntry(
            id="OTOKAR", name="Otokar Otomotiv ve Savunma San.",
            country_of_origin="Turkey", region="Turkish",
            capabilities=["armoured_vehicles","wheeled_APC","MBT","light_protected"],
            product_lines=[
                {"name":"Cobra wheeled APC","export_class":SSBD},
                {"name":"Arma 8×8 APC","export_class":SSBD},
                {"name":"Altay MBT","export_class":SSBD},
            ],
            export_regime=[SSBD],
            itar_controlled=False,
            proven_in=["Turkey","Bahrain","Nigeria","Saudi Arabia","Azerbaijan","Georgia"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["Otokar website","DefenceWeb 2025"],
            notes="Cobra proven in Nigeria. Good Africa vehicle option — ITAR-free. "
                  "Altay MBT first deliveries to Turkey 2025 — Qatar orders underway.",
        ))

        self._add(OEMEntry(
            id="TUSAS", name="Turkish Aerospace Industries (TAI/TUSAS)",
            country_of_origin="Turkey", region="Turkish",
            capabilities=["fighter_aircraft","helicopters","UAV","trainers","space"],
            product_lines=[
                {"name":"KAAN 5th-gen fighter","export_class":SSBD},
                {"name":"ANKA-S MALE UAV","export_class":SSBD},
                {"name":"Hürjet light attack/trainer","export_class":SSBD},
                {"name":"Gökbey helicopter","export_class":SSBD},
                {"name":"AKSUNGUR armed MALE UAV","export_class":SSBD},
            ],
            export_regime=[SSBD],
            itar_controlled=False,
            proven_in=["Turkey","Malaysia","Spain"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            recent_notable_contracts=[
                "Spain: Hürjet 30 aircraft €3.12bn contract",
                "Indonesia: KAAN 48 jets contract",
                "Malaysia: 3 ANKA-S drones with technology transfer",
            ],
            data_sources=["C4Defence March 2026","Daily Sabah Dec 2025"],
            notes="KAAN fighter entering production 2026. Hürjet first export to Spain. "
                  "Growing export platform — ITAR-free. Indonesia KAAN contract is major milestone.",
        ))

        self._add(OEMEntry(
            id="BMC", name="BMC Otomotiv",
            country_of_origin="Turkey", region="Turkish",
            capabilities=["military_trucks","armoured_vehicles","tactical_vehicles"],
            product_lines=[
                {"name":"Kirpi MRAP","export_class":SSBD},
                {"name":"Hizir 4×4 MRAP","export_class":SSBD},
                {"name":"Vuran wheeled APC","export_class":SSBD},
            ],
            export_regime=[SSBD],
            itar_controlled=False,
            proven_in=["Turkey","Saudi Arabia","Qatar","Malaysia"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["BMC website","C4Defence 2025"],
            notes="Military truck and MRAP specialist. Strong Gulf sales. "
                  "Growing export candidate for Africa — competitive pricing.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 8 — SOUTH AFRICA
        # Export regime: NCACC (National Conventional Arms Control Committee)
        # KEY: UNRESTRICTED for most systems — no ITAR, no EU dual-use.
        # SA exported R10.1bn to 42 countries in 2025 (NCACC annual report).
        # RDM = R4.8bn (47% of total). Paramount growing African market.
        # CRITICAL NOTE: Denel IP theft by UAE under investigation (SIU 2025).
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="PARAMOUNT", name="Paramount Group",
            country_of_origin="South Africa", region="Southern_Hemisphere",
            capabilities=["armoured_vehicles","MRAP","IFV","aviation","ISR","defence_systems"],
            product_lines=[
                {"name":"Marauder MRAP","export_class":NCACC},
                {"name":"Mbombe 4/6/8 IFV family","export_class":NCACC},
                {"name":"Matador 6×6 APC","export_class":NCACC},
                {"name":"Maatla wheeled APC","export_class":NCACC},
                {"name":"Mwari ISR aircraft","export_class":NCACC},
                {"name":"MantaRay anti-submarine drone","export_class":NCACC},
            ],
            export_regime=[NCACC, UNRESTR],
            itar_controlled=False,
            proven_in=["Angola","Ghana","Nigeria","Ethiopia","Kenya","UAE","Iraq",
                       "Jordan","Kazakhstan"],
            lusophone_experience=True,
            cplp_certifications=["Angola"],
            contact_route="direct",
            arkmurus_relationship=REL_AWARE,
            recent_notable_contracts=[
                "Maatla APC exports to Ghana (ongoing)",
                "Multiple African country vehicle deliveries 2025",
            ],
            export_policy_notes="NCACC approval required — South African process. "
                                 "UNRESTRICTED: no US or EU licence required on most systems. "
                                 "CAUTION: SIU investigation ongoing re UAE IP theft allegations "
                                 "(ADASI joint venture 2016-2024). Verify current Paramount "
                                 "export approvals before committing to deal. "
                                 "Company cooperating fully with SIU (statement 2025).",
            data_sources=["DefenceWeb May 2025 (IP theft)","DefenceWeb Feb 2025 (export mapping)",
                          "DefenceWeb Wikipedia SA defence industry"],
            notes="South Africa's largest privately-owned defence company. "
                  "ANGOLA track record: Mbombe used by FAA. Excellent Arkmurus OEM for Africa. "
                  "IP theft allegations (UAE) — monitor SIU investigation outcome. "
                  "Mwari aircraft is Africa's first fully sovereign ISR platform.",
        ))

        self._add(OEMEntry(
            id="RDM", name="Rheinmetall Denel Munition (RDM)",
            country_of_origin="South Africa", region="Southern_Hemisphere",
            capabilities=["155mm_artillery_ammunition","mortar_ammunition","40mm_grenades",
                          "propellants","large_calibre_ammunition","plant_engineering"],
            product_lines=[
                {"name":"155mm Assegai extended range","export_class":NCACC},
                {"name":"M64 120mm mortar round","export_class":NCACC},
                {"name":"V-LAP (velocity-enhanced long-range artillery projectile)","export_class":NCACC},
                {"name":"40mm grenade family","export_class":NCACC},
                {"name":"Minefield breaching (Plofadder)","export_class":NCACC},
            ],
            export_regime=[NCACC, UNRESTR],
            itar_controlled=False,
            proven_in=["Germany","Sweden","Estonia","Hungary","Netherlands","UK",
                       "Mozambique","Angola"],
            lusophone_experience=True,
            contact_route="direct",
            arkmurus_relationship=REL_AWARE,
            revenue_latest="R4.8bn exports 2025 (NCACC — 47% of SA defence exports)",
            order_backlog="Record — Sweden R7.3bn (155mm, largest ever order)",
            recent_notable_contracts=[
                "Sweden: R7.3bn 155mm Assegai rounds (July 2025, RDM largest ever)",
                "NATO 155mm: multiple European orders",
                "Assegai integration with SANDF G5/G6 howitzers (Nov 2025)",
                "R1bn annual investment in capacity expansion (doubling)",
            ],
            export_policy_notes="NCACC approval (South African DTIC/SANDF). "
                                 "51% Rheinmetall, 49% Denel. UNRESTRICTED vs German BAFA — "
                                 "major advantage: no EU/German export controls apply to RDM. "
                                 "NCACC approval timeline typically 4-6 weeks. "
                                 "NOTE: RDM is the correct route for Rheinmetall ammunition "
                                 "to Africa — avoids German BAFA entirely.",
            data_sources=["DefenceWeb April 2026 (investment)","DefenceWeb Nov 2025 (Assegai)","NCACC 2025"],
            notes="RDM accounts for almost half of all South African defence exports. "
                  "155mm Assegai is NATO-standard, battle-proven, in massive global demand. "
                  "UNRESTRICTED and NCACC — fastest route to market for artillery ammunition. "
                  "Lusophone Africa track record: Mozambique FADM, Angola FAA. "
                  "HIGHEST PRIORITY for Arkmurus artillery ammunition deals.",
        ))

        self._add(OEMEntry(
            id="REUTECH", name="Reutech Solutions (Pty) Ltd",
            country_of_origin="South Africa", region="Southern_Hemisphere",
            capabilities=["radar","EW","C4ISR","naval_systems","border_surveillance",
                          "communications","optronics"],
            product_lines=[
                {"name":"RTS 6400 coastal surveillance radar","export_class":NCACC},
                {"name":"RTS 6700 naval radar","export_class":NCACC},
                {"name":"Ground border surveillance radar","export_class":NCACC},
                {"name":"Communications systems","export_class":NCACC},
            ],
            export_regime=[NCACC, UNRESTR],
            itar_controlled=False,
            proven_in=["South Africa","Angola","Mozambique","Kenya"],
            lusophone_experience=True,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="NCACC approval. UNRESTRICTED for most systems. "
                                 "Reutech radars in service with South African Navy and "
                                 "exported to Southern Africa. R750m investment committed.",
            data_sources=["DefenceWeb SA Masterplan","Reutech website"],
            notes="South Africa's leading radar and naval electronics company. "
                  "Coastal surveillance radar is high-value African opportunity. "
                  "Radars fitted to SANDF vessels and exported overseas.",
        ))

        self._add(OEMEntry(
            id="MILKOR", name="Milkor (Pty) Ltd",
            country_of_origin="South Africa", region="Southern_Hemisphere",
            capabilities=["UAV","UCAV","grenade_launchers","light_weapons","ISR"],
            product_lines=[
                {"name":"Milkor 380 UCAV (Africa's largest armed UAV)","export_class":NCACC},
                {"name":"Milkor 780 UCAV (in development 2026)","export_class":NCACC},
                {"name":"MGL-105/140/180 grenade launchers","export_class":NCACC},
                {"name":"Y3 AGL (automatic grenade launcher)","export_class":NCACC},
            ],
            export_regime=[NCACC, UNRESTR],
            itar_controlled=False,
            proven_in=["South Africa","USA","UK","Germany","India","over 60 countries for MGL"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            recent_notable_contracts=[
                "SAAF: Milkor 380 orders; production 8/year scaling to 16/year by 2026",
                "MGL: one of world's most exported grenade launchers — 60+ countries",
            ],
            export_policy_notes="NCACC approval. Milkor 380 UCAV is Africa-origin, no ITAR. "
                                 "Production scaling rapidly for export in 2026. "
                                 "One of only 8 countries worldwide capable of combat-grade UCAV.",
            data_sources=["Wikipedia SA Defence Industry 2025","DefenceWeb"],
            notes="MGL is world-famous. Milkor 380 UCAV is emerging capability — "
                  "Africa's largest armed drone, now entering production for export. "
                  "Strong Arkmurus opportunity for buyers wanting ITAR-free drone capability.",
        ))

        self._add(OEMEntry(
            id="GEW_SA", name="GEW Technologies",
            country_of_origin="South Africa", region="Southern_Hemisphere",
            capabilities=["EW","SIGINT","COMINT","communications_intelligence","jamming"],
            product_lines=[
                {"name":"Comint/EW systems","export_class":NCACC},
            ],
            export_regime=[NCACC, UNRESTR],
            itar_controlled=False,
            proven_in=["South Africa","Sub-Saharan Africa"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["SA Aerospace Defence Masterplan","DefenceWeb"],
            notes="South African EW specialist. SA Masterplan identifies EW as major export "
                  "opportunity. UNRESTRICTED — good option for African EW requirements.",
        ))

        self._add(OEMEntry(
            id="DENEL", name="Denel SOC Ltd",
            country_of_origin="South Africa", region="Southern_Hemisphere",
            capabilities=["missiles","artillery","aircraft_maintenance","armoured_vehicles",
                          "ammunition","MRO"],
            product_lines=[
                {"name":"Umkhonto SAM","export_class":NCACC},
                {"name":"Mokopa ATGM","export_class":NCACC},
                {"name":"A-Darter AAM (with Brazil)","export_class":NCACC},
                {"name":"G6 Rhino 155mm SP howitzer","export_class":NCACC},
                {"name":"Cheetah C/D aircraft MRO","export_class":NCACC},
            ],
            export_regime=[NCACC, UNRESTR],
            itar_controlled=False,
            proven_in=["South Africa","India","Algeria","UAE (historical — now disputed)"],
            lusophone_experience=True,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="State-owned. Under financial/governance restructuring since 2019. "
                                 "IP theft allegations from UAE joint ventures under SIU investigation. "
                                 "CAUTION: financial instability and IP disputes create execution risk. "
                                 "Some divisions stabilising — Rheinmetall acquired Denel Munition "
                                 "stake (now RDM). Remaining Denel divisions are higher-risk.",
            data_sources=["DefenceWeb May 2025 (IP theft)","SA Defence Masterplan","Wikipedia SA"],
            notes="State-owned, financially troubled. UAE IP theft allegations ongoing. "
                  "A-Darter (with Brazil's Mectron/Avibras) — Lusophone connection. "
                  "G6 Rhino and munitions NOTABLE but Denel's financial health is a risk factor. "
                  "DO NOT commit Arkmurus commercial risk without thorough Denel financial check.",
        ))

        self._add(OEMEntry(
            id="ATE", name="Advanced Technologies & Engineering (ATE)",
            country_of_origin="South Africa", region="Southern_Hemisphere",
            capabilities=["aircraft_upgrades","avionics","maintenance","MRO","systems_integration"],
            product_lines=[
                {"name":"Rooivalk attack helicopter maintenance","export_class":NCACC},
                {"name":"Oryx helicopter upgrades","export_class":NCACC},
                {"name":"Military aircraft MRO","export_class":NCACC},
            ],
            export_regime=[NCACC, UNRESTR],
            itar_controlled=False,
            proven_in=["South Africa"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["DefenceWeb SA","ATE website"],
            notes="Helicopter maintenance specialist. Relevant for African helicopter fleet support.",
        ))

        self._add(OEMEntry(
            id="DCD_MOBILITY", name="DCD Protected Mobility",
            country_of_origin="South Africa", region="Southern_Hemisphere",
            capabilities=["MRAP","armoured_vehicles","protected_mobility"],
            product_lines=[
                {"name":"RG-35 MRAP","export_class":NCACC},
                {"name":"RG-31 Nyala","export_class":NCACC},
                {"name":"RG-32M Scout","export_class":NCACC},
            ],
            export_regime=[NCACC, UNRESTR],
            itar_controlled=False,
            proven_in=["South Africa","Ghana","Botswana","Mozambique","Kenya","Uganda"],
            lusophone_experience=True,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="NCACC approval. DCD is separate from Denel — no IP theft issues. "
                                 "NOTE: UAE's NIMR based on SA IP from Denel (disputed) — DCD is clean.",
            data_sources=["DefenceWeb Feb 2025 export mapping"],
            notes="Growing African export track record. Mozambique procurement of RG series. "
                  "UNRESTRICTED and NCACC — good Arkmurus vehicle OEM for Africa.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 9 — BRAZIL
        # Export regime: SINARM/Brazilian Army (COLOG/DLOG)
        # Brazil UNRESTRICTED on most systems — no ITAR, no EU controls.
        # Lusophone connection is commercial advantage in CPLP markets.
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="AVIBRAS", name="Avibras Indústria Aeroespacial SA",
            country_of_origin="Brazil", region="Southern_Hemisphere",
            capabilities=["rockets","MLRS","artillery","missiles","counter_battery"],
            product_lines=[
                {"name":"ASTROS II MLRS","export_class":"Brazilian Army approval"},
                {"name":"ASTROS 2020 upgrade","export_class":"Brazilian Army approval"},
                {"name":"SS-30/40/60/80 rockets","export_class":"Brazilian Army approval"},
            ],
            export_regime=[UNRESTR],
            itar_controlled=False,
            proven_in=["Brazil","Saudi Arabia","Iraq","Qatar","Angola"],
            lusophone_experience=True,
            cplp_certifications=["Angola","Brazil"],
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="Brazilian government approval via Brazilian Army. "
                                 "No ITAR, no EU controls. Lusophone language advantage. "
                                 "Saudi Arabia is largest export customer.",
            data_sources=["SIPRI Arms Transfers Database","DefenceWeb Africa tracker"],
            notes="ASTROS II is Angola FAA's primary multiple rocket launcher. "
                  "CRITICAL Lusophone connection — Brazilian manufacturer, Portuguese language "
                  "documentation, Brazilian MoD relationships with Angola. "
                  "Excellent Arkmurus OEM for FAA artillery requirements.",
        ))

        self._add(OEMEntry(
            id="CBC_BRAZIL", name="CBC (Companhia Brasileira de Cartuchos)",
            country_of_origin="Brazil", region="Southern_Hemisphere",
            capabilities=["small_arms_ammunition","medium_calibre","pyrotechnics","non_lethal"],
            product_lines=[
                {"name":"5.56mm / 7.62mm / .308 rifle ammunition","export_class":"Brazilian Army"},
                {"name":"12.7mm HMG ammunition","export_class":"Brazilian Army"},
                {"name":"40mm grenade family","export_class":"Brazilian Army"},
            ],
            export_regime=[UNRESTR],
            itar_controlled=False,
            proven_in=["Brazil","Angola","Mozambique","Guinea-Bissau","Cape Verde",
                       "São Tomé","USA","UK","Germany","over 80 countries"],
            lusophone_experience=True,
            cplp_certifications=["Angola","Mozambique","Guinea-Bissau","Cape Verde",
                                  "São Tomé e Príncipe"],
            contact_route="direct",
            arkmurus_relationship=REL_AWARE,
            export_policy_notes="Brazilian export approval — generally fast and permissive. "
                                 "No ITAR. CBC is the world's largest privately-owned ammunition "
                                 "manufacturer by units produced. "
                                 "Portuguese language advantage across ALL CPLP markets.",
            data_sources=["CBC company profile","DefenceWeb Africa","SIPRI"],
            notes="PREMIUM ARKMURUS OEM. CBC proven across ALL 5 Arkmurus Lusophone Africa markets. "
                  "Ammunition is UNRESTRICTED — no export licence from Brazil required "
                  "for most small arms ammunition to CPLP countries. "
                  "Brazilian diplomatic relationships across CPLP are an additional asset.",
        ))

        self._add(OEMEntry(
            id="TAURUS_ARMS", name="Taurus Armas SA",
            country_of_origin="Brazil", region="Southern_Hemisphere",
            capabilities=["small_arms","pistols","revolvers","rifles","submachine_guns"],
            product_lines=[
                {"name":"Taurus pistol family (G3, TX22, etc.)","export_class":"Brazilian Army"},
                {"name":"CT30 tactical rifle","export_class":"Brazilian Army"},
            ],
            export_regime=[UNRESTR],
            itar_controlled=False,
            proven_in=["Brazil","USA","Angola","Mozambique","over 70 countries"],
            lusophone_experience=True,
            cplp_certifications=["Angola","Mozambique"],
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["Taurus Armas investor relations 2025"],
            notes="Brazil's largest small arms manufacturer. UNRESTRICTED. "
                  "Lusophone Africa track record. Taurus pistols used by CPLP police and military.",
        ))

        self._add(OEMEntry(
            id="EMBRAER_DEF", name="Embraer Defence & Security",
            country_of_origin="Brazil", region="Southern_Hemisphere",
            capabilities=["military_aircraft","ISR","tankers","maritime_patrol","airlifters"],
            product_lines=[
                {"name":"A-29 Super Tucano (light attack)","export_class":"Brazilian approval"},
                {"name":"KC-390 military transport","export_class":"Brazilian approval"},
                {"name":"R-99 AEW&C","export_class":"Brazilian approval"},
                {"name":"EMB-145 variants (SIGINT/AEW)","export_class":"Brazilian approval"},
            ],
            export_regime=[UNRESTR],
            itar_controlled=False,
            proven_in=["Brazil","USA","Colombia","Angola","Portugal","Kenya","Dominican Republic",
                       "Indonesia","Philippines"],
            lusophone_experience=True,
            cplp_certifications=["Angola","Portugal"],
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="Brazilian government approval. US content in some platforms "
                                 "(GE engines on KC-390) may trigger EAR. A-29 has US content "
                                 "(Pratt & Whitney) — verify on case by case. ",
            data_sources=["Embraer Defence website","SIPRI Arms Transfers"],
            notes="Super Tucano is world's best-selling light attack aircraft — proven in Angola. "
                  "KC-390 multi-role transport gaining traction. Portuguese language advantage.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 10 — INDIA
        # Export regime: DGFT (Directorate General of Foreign Trade) via SCOMET list
        # India defence exports growing rapidly — target $5bn by 2025
        # NOTE: Indian systems increasing, but geopolitical caution on Africa
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="TATA_ADVANCED", name="Tata Advanced Systems Limited (TASL)",
            country_of_origin="India", region="South_Asian",
            capabilities=["armoured_vehicles","aerospace","UAV","electronics","MRO"],
            product_lines=[
                {"name":"Kestrel APC (with Bharat Forge)","export_class":"Indian DGFT/SCOMET"},
                {"name":"Whap 8×8 APC","export_class":"Indian DGFT/SCOMET"},
                {"name":"Aerospace components (Boeing, Airbus)","export_class":"Indian DGFT"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["India","select Middle East"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="Indian SCOMET (Special Chemicals, Organisms, Materials, "
                                 "Equipment and Technologies) controls. India-origin products "
                                 "generally no ITAR, no EU controls. "
                                 "India offset requirement (25-50%) may apply to Indian buyers.",
            data_sources=["TASL website","Indian MoD export policy 2025"],
            notes="India's largest private defence manufacturer. "
                  "Growing export programme — not yet proven in Africa at scale. "
                  "India-Africa diplomatic ties improving. Watch this space.",
        ))

        self._add(OEMEntry(
            id="HAL", name="Hindustan Aeronautics Limited (HAL)",
            country_of_origin="India", region="South_Asian",
            capabilities=["fighter_aircraft","helicopters","trainers","MRO","aerospace"],
            product_lines=[
                {"name":"Tejas Mk1A light combat aircraft","export_class":"Indian SCOMET"},
                {"name":"LCH Prachand attack helicopter","export_class":"Indian SCOMET"},
                {"name":"Dhruv ALH utility helicopter","export_class":"Indian SCOMET"},
                {"name":"HTT-40 basic trainer","export_class":"Indian SCOMET"},
                {"name":"Sukhoi Su-30MKI maintenance (licence)","export_class":"N/A (Sukhoi)"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["India","Mauritius","Sri Lanka","Nepal","Ecuador"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["HAL Annual Report 2024-25","Indian MoD press releases"],
            notes="State-owned. Tejas exports beginning — Ecuador shortlisted 2025. "
                  "Dhruv helicopter proven in South Asia. Growing export push. "
                  "Not yet proven in Africa at scale. Monitor Tejas export progress.",
        ))

        self._add(OEMEntry(
            id="BEL", name="Bharat Electronics Limited (BEL)",
            country_of_origin="India", region="South_Asian",
            capabilities=["radar","C4ISR","EW","communications","naval_systems",
                          "night_vision","electro_optics"],
            product_lines=[
                {"name":"Battle field surveillance radar","export_class":"Indian SCOMET"},
                {"name":"Weapon locating radar","export_class":"Indian SCOMET"},
                {"name":"Electronic voting machines (civil)","export_class":"DGFT"},
                {"name":"Naval combat management","export_class":"Indian SCOMET"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["India","select ME/Asia"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["BEL Annual Report 2025","Indian MoD press"],
            notes="India's largest defence electronics company. Growing export ambitions. "
                  "Not yet significant in Africa. Monitor India-Africa defence ties.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 11 — UAE / GULF
        # NOTE: EDGE Group and NIMR growing independent capability
        # CAUTION: UAE joint ventures with SA OEMs face IP theft allegations (2025)
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="EDGE_HALCON", name="EDGE Group / HALCON",
            country_of_origin="UAE", region="Middle_East",
            capabilities=["missiles","UAV","precision_munitions","EW","counter_drone",
                          "electronic_systems"],
            product_lines=[
                {"name":"Haboob precision glide bomb","export_class":"UAE MoD"},
                {"name":"Thunder series standoff weapons","export_class":"UAE MoD"},
                {"name":"Hunter TB UAV","export_class":"UAE MoD"},
            ],
            export_regime=[UNRESTR],
            itar_controlled=False,
            proven_in=["UAE"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="UAE MoD export approval. Growing domestic capability. "
                                 "CAUTION: Elbit $2.3bn deal (UAE, Dec 2025) shows UAE prefers "
                                 "buying rather than selling — EDGE export capability limited. "
                                 "IP theft allegations (SA Denel/Paramount joint ventures) "
                                 "show UAE tendency to acquire technology rather than develop it.",
            data_sources=["DefenceWeb May 2025 (IP theft)","Global Defense Corp Dec 2025"],
            notes="UAE's defence industry arm. Still primarily a buyer, not exporter. "
                  "EDGE's NIMR vehicles based on SA Denel RG35 IP (disputed). "
                  "Monitor EDGE export development — potential competitor in Africa.",
        ))

        self._add(OEMEntry(
            id="NIMR", name="NIMR Automotive LLC",
            country_of_origin="UAE", region="Middle_East",
            capabilities=["armoured_vehicles","wheeled_APC","MRAP","protected_mobility"],
            product_lines=[
                {"name":"Ajban 440 wheeled APC","export_class":"UAE MoD"},
                {"name":"Hafeet 440 APC","export_class":"UAE MoD"},
            ],
            export_regime=[UNRESTR],
            itar_controlled=False,
            proven_in=["UAE","Jordan","undisclosed GCC"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="CAUTION: NIMR's design heritage is disputed. "
                                 "SIU investigation (2025) found Denel provided RG35 IP and "
                                 "hardware to NIMR under UAE joint venture agreement. "
                                 "South Africa may seek IP-based restrictions on NIMR exports "
                                 "to certain markets. Monitor SIU investigation outcome.",
            data_sources=["DefenceWeb May 2025 (IP theft SIU investigation)"],
            notes="UAE armoured vehicle manufacturer. CAUTION on IP provenance. "
                  "Not an Arkmurus priority — Paramount/DCD are cleaner alternatives.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 12 — SOUTH KOREA
        # Export regime: DAPA (Defence Acquisition Programme Administration)
        # Korea actively pursues technology transfer in deals — major buyer appeal.
        # Generally no ITAR on Korean-origin systems (exception: F-404/F-414 engines).
        # 2025: Korea major export winner — Poland, Romania, Australia, Egypt, Middle East.
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="HANWHA", name="Hanwha Aerospace Co.",
            country_of_origin="South Korea", region="East_Asian",
            capabilities=["SP_howitzer","armoured_vehicles","MLRS","air_defence",
                          "helicopter_engines","UGV","space_launch"],
            product_lines=[
                {"name":"K9 Thunder 155mm SP howitzer","export_class":"Korean DAPA"},
                {"name":"AS21 Redback IFV","export_class":"Korean DAPA"},
                {"name":"Chunmoo MLRS","export_class":"Korean DAPA"},
                {"name":"M-SAM Cheongung (medium SAM)","export_class":"Korean DAPA"},
                {"name":"L-SAM (long-range SAM — 2025)","export_class":"Korean DAPA"},
                {"name":"Arion-SMET UGV","export_class":"Korean DAPA"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["South Korea","India","Poland","Romania","Australia","Egypt",
                       "UAE","Saudi Arabia","Iraq","Finland","Norway","Estonia"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            revenue_latest="K9 in 10 countries, 1,600+ units delivered",
            recent_notable_contracts=[
                "Romania: K9 $938m contract (2024), additional K9 contract 2025",
                "Poland: additional K9 $2.6bn (2025)",
                "India: second K9 contract 2025",
                "Australia: K9 production facility in Geelong established",
                "Saudi Arabia: $3.2bn air defence agreement 2023",
                "Egypt: K9A1 $1.7bn with local production at Military Factory 200",
                "MENA HQ established in Riyadh 2025",
            ],
            export_policy_notes="Korean DAPA approval required. Korea offers technology transfer "
                                 "and local production — major competitive advantage vs Western OEMs. "
                                 "GE engines in some products (K-21 F414) may create EAR issues. "
                                 "K9 howitzer uses South Korean indigenous components — generally ITAR-free. "
                                 "MENA HQ in Riyadh — growing Africa/ME strategy.",
            data_sources=["Hanwha Aerospace website 2025","KED Global Dec 2025",
                          "Washington Institute 2024","FlightGlobal Feb 2026"],
            notes="K9 is world's most exported 155mm SP howitzer — in 10 countries. "
                  "Technology transfer approach (Egypt local production) creates strong buyer appeal. "
                  "Growing Africa presence via Egypt and Saudi partnerships. "
                  "Acquiring KAI stake March 2026 — integrated Korean defence champion forming.",
        ))

        self._add(OEMEntry(
            id="KAI", name="Korea Aerospace Industries (KAI)",
            country_of_origin="South Korea", region="East_Asian",
            capabilities=["fighter_aircraft","light_attack","trainers","helicopters",
                          "UAV","satellites","maritime_patrol"],
            product_lines=[
                {"name":"FA-50 light combat aircraft","export_class":"Korean DAPA"},
                {"name":"KF-21 Boramae fighter","export_class":"Korean DAPA"},
                {"name":"T-50/T-50i Golden Eagle trainer","export_class":"Korean DAPA"},
                {"name":"KUH-1 Surion utility helicopter","export_class":"Korean DAPA"},
                {"name":"Mugin-5 maritime patrol UAV","export_class":"Korean DAPA"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["South Korea","Iraq","Philippines","Thailand","Indonesia",
                       "Poland","Malaysia","Senegal"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            recent_notable_contracts=[
                "Philippines: FA-50PH 12 aircraft $700m (2025)",
                "Philippines: FA-50PH maintenance contract KRW101bn",
                "KF-21 Boramae: first export contract (first exports of Korean-designed fighter)",
                "KAI profits +10% 2025 with record orders",
                "Hanwha Aerospace acquires 4.99% KAI stake March 2026",
            ],
            export_policy_notes="Korean DAPA approval. T-50 has Lockheed Martin design heritage "
                                 "(US-Korea co-development) — US EAR applies to T-50 re-export. "
                                 "KF-21 uses GE F414 engine — EAR applies. "
                                 "FA-50 may have similar US content considerations. "
                                 "VERIFY US content on case-by-case basis before approach.",
            data_sources=["FlightGlobal Feb 2026","KED Global Dec 2025","Asia Business Daily 2026"],
            notes="FA-50 is most exported new fighter aircraft globally. "
                  "KF-21 is Korea's indigenous 4.5-gen fighter — first exports 2025-2026. "
                  "Privatisation of KAI being discussed — Hanwha buying in. "
                  "Africa opportunity: Kenya, Nigeria, South Africa air force modernisation.",
        ))

        self._add(OEMEntry(
            id="LIG_NEX1", name="LIG Nex1 Co. Ltd",
            country_of_origin="South Korea", region="East_Asian",
            capabilities=["missiles","naval_systems","guided_weapons","air_defence","EO"],
            product_lines=[
                {"name":"Cheongung M-SAM (with Hanwha)","export_class":"Korean DAPA"},
                {"name":"SSM-700K Haeseong naval missile","export_class":"Korean DAPA"},
                {"name":"Biho self-propelled SHORAD","export_class":"Korean DAPA"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["South Korea","UAE","Iraq","Saudi Arabia"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["Washington Institute 2024","KED Global"],
            notes="South Korean guided weapons specialist. M-SAM exported to UAE. "
                  "LIG Nex1 bidding to acquire KAI (March 2026).",
        ))

        self._add(OEMEntry(
            id="HYUNDAI_ROTEM", name="Hyundai Rotem Company",
            country_of_origin="South Korea", region="East_Asian",
            capabilities=["MBT","armoured_vehicles","K2_Black_Panther","railway_systems"],
            product_lines=[
                {"name":"K2 Black Panther MBT","export_class":"Korean DAPA"},
                {"name":"K2PL (Poland variant)","export_class":"Korean DAPA"},
            ],
            export_regime=[EU_DU],
            itar_controlled=False,
            proven_in=["South Korea","Poland","Norway"],
            lusophone_experience=False,
            contact_route="direct",
            arkmurus_relationship=REL_NONE,
            data_sources=["KED Global","Jane's 2025"],
            notes="K2 Black Panther is world's most technologically advanced MBT. "
                  "Poland K2PL programme is major export. Not Africa-relevant at this time.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 13 — UNITED STATES (ITAR-CONTROLLED — TRACKED, NOT PRIMARY ROUTE)
        # All US defence articles on USML require DSP-5 (min 60-120 days).
        # FMS route adds 12-36 months. Arkmurus role as facilitator/agent only.
        # Listed for competitor tracking and market awareness.
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="LOCKHEED_MARTIN", name="Lockheed Martin Corporation",
            country_of_origin="USA", region="USA_ITAR",
            capabilities=["fighter_aircraft","missiles","C4ISR","space","helicopters","radar"],
            product_lines=[
                {"name":"F-16 Fighting Falcon","export_class":ITAR},
                {"name":"F-35 Lightning II","export_class":ITAR},
                {"name":"C-130J Hercules","export_class":ITAR},
                {"name":"HIMARS MLRS","export_class":ITAR},
                {"name":"Javelin ATGM (with Raytheon)","export_class":ITAR},
            ],
            export_regime=[ITAR, EAR_CCL],
            itar_controlled=True,
            proven_in=["USA","UK","Israel","Japan","South Korea","Poland","Belgium",
                       "Netherlands","Australia","Singapore","Morocco","UAE"],
            lusophone_experience=False,
            contact_route="FMS_only",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="ALL products ITAR. DSP-5 minimum 60 days. "
                                 "Major systems typically via FMS (12-36 months). "
                                 "Arkmurus role limited — cannot be prime broker for ITAR systems. "
                                 "Best role: introduce buyer to US Embassy DSO/DSCA for FMF/FMS.",
            data_sources=["DSCA public disclosures","Lockheed Martin investor relations"],
            notes="World's largest defence contractor. ALL products ITAR. "
                  "Africa engagement mainly via FMS route where US has FMF programme "
                  "(Kenya, Nigeria, Morocco). Arkmurus can facilitate relationships, "
                  "not act as broker for ITAR products.",
        ))

        self._add(OEMEntry(
            id="GENERAL_DYNAMICS", name="General Dynamics Corporation",
            country_of_origin="USA", region="USA_ITAR",
            capabilities=["MBT","IFV","naval_systems","IT_systems","ammunition"],
            product_lines=[
                {"name":"M1 Abrams MBT","export_class":ITAR},
                {"name":"Stryker wheeled APC","export_class":ITAR},
                {"name":"LAV 6.0 wheeled IFV","export_class":ITAR},
            ],
            export_regime=[ITAR],
            itar_controlled=True,
            proven_in=["USA","Egypt","Australia","Canada","Saudi Arabia","Morocco"],
            lusophone_experience=False,
            contact_route="FMS_only",
            arkmurus_relationship=REL_NONE,
            data_sources=["DSCA","GD investor relations"],
            notes="All products ITAR. Competitive in Eastern Europe 2025. Not Africa priority.",
        ))

        self._add(OEMEntry(
            id="L3HARRIS", name="L3Harris Technologies",
            country_of_origin="USA", region="USA_ITAR",
            capabilities=["communications","EW","ISR","night_vision","C4ISR","space"],
            product_lines=[
                {"name":"FALCON III tactical radios","export_class":ITAR},
                {"name":"WESCAM MX-15D EO/IR","export_class":ITAR},
                {"name":"AN/PAS-13 thermal weapon sight","export_class":ITAR},
            ],
            export_regime=[ITAR, EAR_CCL],
            itar_controlled=True,
            proven_in=["USA","UK","Australia","Canada","Israel","Saudi Arabia","Kenya","Nigeria"],
            lusophone_experience=True,
            contact_route="FMS_only",
            arkmurus_relationship=REL_NONE,
            data_sources=["L3Harris investor relations","DSCA disclosures"],
            notes="Communications and ISR specialist. FALCON III radios widely used in Africa "
                  "via FMF programmes. ITAR — FMS route for Africa. "
                  "Arkmurus can facilitate relationship with DSCA for FMF-eligible buyers.",
        ))

        self._add(OEMEntry(
            id="RAYTHEON", name="RTX / Raytheon Technologies",
            country_of_origin="USA", region="USA_ITAR",
            capabilities=["air_defence","missiles","radar","communications","EW","sensors"],
            product_lines=[
                {"name":"Patriot PAC-3","export_class":ITAR},
                {"name":"NASAMS (with Kongsberg)","export_class":ITAR},
                {"name":"Stinger MANPADS","export_class":ITAR},
                {"name":"SM-6 naval missile","export_class":ITAR},
            ],
            export_regime=[ITAR],
            itar_controlled=True,
            proven_in=["USA","Saudi Arabia","UAE","Israel","Germany","Poland",
                       "Romania","Japan","South Korea"],
            lusophone_experience=False,
            contact_route="FMS_only",
            arkmurus_relationship=REL_NONE,
            data_sources=["Raytheon investor relations","DSCA"],
            notes="All ITAR. Air defence is core product. Not an Arkmurus Africa priority.",
        ))


        # ═══════════════════════════════════════════════════════════════════════
        # SECTION 14 — COMPETITORS (China, Russia — TRACK ONLY, NEVER PITCH)
        # These entries exist for intelligence purposes.
        # Arkmurus must be aware of Chinese/Russian activity in target markets.
        # ═══════════════════════════════════════════════════════════════════════

        self._add(OEMEntry(
            id="NORINCO", name="NORINCO (China North Industries Group)",
            country_of_origin="China", region="Competitor",
            capabilities=["MBT","APC","artillery","small_arms","ammunition","missiles",
                          "vehicles","naval"],
            product_lines=[
                {"name":"VT-4 MBT","export_class":"Chinese MOFCOM"},
                {"name":"VN-1 wheeled APC","export_class":"Chinese MOFCOM"},
                {"name":"PLZ-52 SP howitzer","export_class":"Chinese MOFCOM"},
                {"name":"WS artillery rockets","export_class":"Chinese MOFCOM"},
            ],
            export_regime=[UNRESTR],
            itar_controlled=False,
            proven_in=["Nigeria","Chad","Cameroon","Guinea","Zambia","Tanzania",
                       "Sudan","South Sudan","Ethiopia","Kenya","Algeria","Morocco",
                       "Pakistan","Bangladesh","Myanmar","Thailand"],
            competitor_only=True,
            lusophone_experience=False,
            contact_route="N/A",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="China uses arms sales as tool of economic diplomacy. "
                                 "Package deals linked to infrastructure loans (Belt & Road). "
                                 "Price undercuts Western OEMs significantly. "
                                 "No ITAR, no EU controls — fastest delivery in Africa.",
            data_sources=["SIPRI Arms Transfers Database","DefenceWeb Africa","Janes 2025"],
            notes="COMPETITOR TRACK — DO NOT PITCH. "
                  "NORINCO is Africa's most active competitor to Western OEMs. "
                  "Beat Arkmurus-potential OEMs in Angola (VT-4 MBT 2023), "
                  "Nigeria (vehicle deals), Sudan (ongoing). "
                  "Key intelligence: monitor NORINCO activity in Lusophone markets. "
                  "FAA Angola has both Western and Chinese equipment — dual-stream risk.",
        ))

        self._add(OEMEntry(
            id="POLY_TECH", name="Poly Technologies (POLY GROUP)",
            country_of_origin="China", region="Competitor",
            capabilities=["small_arms","ammunition","artillery","naval"],
            product_lines=[
                {"name":"Type 56 rifle (AK copy)","export_class":"Chinese MOFCOM"},
                {"name":"HJ-10 ATGM","export_class":"Chinese MOFCOM"},
            ],
            export_regime=[UNRESTR],
            itar_controlled=False,
            proven_in=["multiple Sub-Saharan Africa","Middle East","Asia"],
            competitor_only=True,
            contact_route="N/A",
            arkmurus_relationship=REL_NONE,
            data_sources=["SIPRI","DefenceWeb Africa"],
            notes="COMPETITOR TRACK — DO NOT PITCH. Subsidiary of CNOOC-linked POLY GROUP. "
                  "Focus on small arms and ammunition — competes with CBC and Taurus.",
        ))

        self._add(OEMEntry(
            id="ROSOBORONEXPORT", name="Rosoboronexport (Russia)",
            country_of_origin="Russia", region="Competitor",
            capabilities=["fighter_aircraft","helicopters","MBT","naval","missiles","EW","C4ISR"],
            product_lines=[
                {"name":"Su-30MKI/MKA (fighter)","export_class":"Russian FSB approval"},
                {"name":"Mi-17/Mi-35 helicopters","export_class":"Russian FSB approval"},
                {"name":"T-72/T-90 MBT","export_class":"Russian FSB approval"},
                {"name":"Pantsir-S1 CUAS","export_class":"Russian FSB approval"},
                {"name":"S-300/S-400 SAM","export_class":"Russian FSB approval"},
            ],
            export_regime=[UNRESTR],
            itar_controlled=False,
            eu_arms_embargo_applies=True,
            proven_in=["Angola","Mozambique","Nigeria","Ethiopia","Egypt","Algeria",
                       "India","China","Vietnam","Venezuela","Syria"],
            competitor_only=True,
            lusophone_experience=True,
            contact_route="N/A",
            arkmurus_relationship=REL_NONE,
            export_policy_notes="Russia subject to comprehensive UK/EU/US arms embargo since 2022. "
                                 "Arkmurus CANNOT facilitate any transaction involving Russian equipment. "
                                 "HOWEVER: Many Arkmurus target markets have Russian legacy equipment "
                                 "requiring MRO — this creates opportunity for non-Russian MRO providers.",
            data_sources=["SIPRI Arms Transfers Database","UK OFSI embargo list"],
            notes="COMPETITOR TRACK — DO NOT PITCH. EMBARGOED — zero commercial engagement. "
                  "Angola FAA has Soviet/Russian legacy (Mi-17, T-72, Su-30MKK). "
                  "Mozambique FADM has Russian equipment. Guinea-Bissau FASB has Russian legacy. "
                  "OPPORTUNITY: Russian MRO unavailable since 2022 — create market for "
                  "Western/Ukrainian MRO alternatives. Flag to Arkmurus BD team.",
        ))


    # ── Query Interface ────────────────────────────────────────────────────────

    def search_by_capability(
        self,
        capability: str,
        destination: str = None,
        exclude_origins: List[str] = None,
        include_competitors: bool = False,
        max_results: int = 10,
    ) -> List[Dict]:
        results = []
        cap_lower = capability.lower().replace("-","_").replace(" ","_")
        excl = [c.lower() for c in (exclude_origins or [])]

        for oem in self._oems.values():
            if oem.competitor_only and not include_competitors:
                continue
            if oem.country_of_origin.lower() in excl:
                continue
            if not any(cap_lower in c.lower() or c.lower() in cap_lower
                       for c in oem.capabilities):
                continue
            score = 0
            if destination:
                dest_lower = destination.lower()
                if any(dest_lower in p.lower() for p in oem.proven_in):
                    score += 40
                if oem.lusophone_experience and any(
                    kw in dest_lower for kw in ["angola","mozambique","guinea","cape verde",
                                                  "são tomé","cplp","lusophone","português"]
                ):
                    score += 30
                if UNRESTR in oem.export_regime:
                    score += 15
                if oem.arkmurus_relationship in [REL_MOU, REL_ACTIVE, REL_CONTACT]:
                    score += 20
                if oem.itar_controlled:
                    score -= 20
                if oem.competitor_only:
                    score -= 100
            results.append((score, oem))

        results.sort(key=lambda x: x[0], reverse=True)
        return [self._to_dict(oem, rank=i+1) for i, (_, oem) in enumerate(results[:max_results])]

    def get_by_id(self, oem_id: str) -> Optional[Dict]:
        oem = self._oems.get(oem_id.upper())
        return self._to_dict(oem) if oem else None

    def get_by_country(self, country: str) -> List[Dict]:
        return [self._to_dict(oem) for oem in self._oems.values()
                if oem.country_of_origin.lower() == country.lower()]

    def get_lusophone_specialists(self) -> List[Dict]:
        return [self._to_dict(oem) for oem in self._oems.values()
                if oem.lusophone_experience and not oem.competitor_only]

    def get_competitors_in_market(self, market: str) -> List[Dict]:
        market_lower = market.lower()
        return [self._to_dict(oem) for oem in self._oems.values()
                if oem.competitor_only and any(market_lower in p.lower() for p in oem.proven_in)]

    def get_unrestricted_oems(self, capability: str = None) -> List[Dict]:
        results = []
        for oem in self._oems.values():
            if oem.competitor_only:
                continue
            if UNRESTR not in oem.export_regime and NCACC not in oem.export_regime:
                continue
            if capability and not any(capability.lower() in c.lower() for c in oem.capabilities):
                continue
            results.append(self._to_dict(oem))
        return results

    def get_stale_entries(self, days_threshold: int = 90) -> List[Dict]:
        """Return OEMs whose data is older than threshold — flag for re-verification."""
        stale = []
        for oem in self._oems.values():
            try:
                verified = datetime.strptime(oem.data_verified_date, "%Y-%m")
                age_days = (datetime.now() - verified).days
                if age_days > days_threshold:
                    stale.append({**self._to_dict(oem), "age_days": age_days})
            except Exception:
                pass
        return sorted(stale, key=lambda x: x.get("age_days", 0), reverse=True)

    def count(self) -> int:
        return len(self._oems)

    def stats(self) -> Dict:
        by_country = {}
        by_region  = {}
        for oem in self._oems.values():
            by_country[oem.country_of_origin] = by_country.get(oem.country_of_origin, 0) + 1
            by_region[oem.region]             = by_region.get(oem.region, 0) + 1
        return {
            "total":            self.count(),
            "by_country":       by_country,
            "by_region":        by_region,
            "unrestricted":     len(self.get_unrestricted_oems()),
            "lusophone":        len(self.get_lusophone_specialists()),
            "competitors":      sum(1 for o in self._oems.values() if o.competitor_only),
            "itar_controlled":  sum(1 for o in self._oems.values() if o.itar_controlled),
            "stale_entries":    len(self.get_stale_entries()),
        }

    def _to_dict(self, oem: OEMEntry, rank: int = None) -> Dict:
        d = {
            "id":                    oem.id,
            "name":                  oem.name,
            "country":               oem.country_of_origin,
            "region":                oem.region,
            "capabilities":          oem.capabilities,
            "export_regime":         oem.export_regime,
            "itar_controlled":       oem.itar_controlled,
            "mtcr_controlled":       oem.mtcr_controlled,
            "proven_in":             oem.proven_in,
            "lusophone_experience":  oem.lusophone_experience,
            "contact_route":         oem.contact_route,
            "known_agents":          oem.known_agent_agreements,
            "arkmurus_relationship": oem.arkmurus_relationship,
            "competitor_only":       oem.competitor_only,
            "revenue_latest":        oem.revenue_latest,
            "order_backlog":         oem.order_backlog,
            "recent_contracts":      oem.recent_notable_contracts,
            "export_policy_notes":   oem.export_policy_notes,
            "data_verified":         oem.data_verified_date,
            "data_confidence":       oem.data_confidence,
            "data_sources":          oem.data_sources,
            "notes":                 oem.notes,
            "product_count":         len(oem.product_lines),
        }
        if rank:
            d["rank"] = rank
        return d

    def to_json(self) -> str:
        return json.dumps(
            {oid: self._to_dict(oem) for oid, oem in self._oems.items()},
            indent=2
        )


# ═══════════════════════════════════════════════════════════════════════════
# OEM MONITORING SOURCES
# ARIA adds these to her source library for weekly research sweeps.
# These sources are checked for: contract awards, export policy changes,
# company restructuring, new products, and competitor activity.
# ═══════════════════════════════════════════════════════════════════════════

OEM_MONITORING_SOURCES = {
    "global": [
        {"name": "SIPRI Arms Transfers Database", "url": "https://www.sipri.org/databases/armstransfers",
         "freq": "quarterly", "priority": 1},
        {"name": "Janes Defence Weekly", "url": "https://www.janes.com", "freq": "weekly", "priority": 1},
        {"name": "Defense News", "url": "https://www.defensenews.com", "freq": "weekly", "priority": 1},
    ],
    "africa": [
        {"name": "DefenceWeb", "url": "https://www.defenceweb.co.za", "freq": "daily", "priority": 1},
        {"name": "Africa Defence Forum", "url": "https://adf-magazine.com", "freq": "weekly", "priority": 2},
    ],
    "south_africa": [
        {"name": "NCACC Annual Report", "url": "https://www.dtic.mil.za/ncacc", "freq": "annual", "priority": 1},
        {"name": "DefenceWeb SA Industry", "url": "https://defenceweb.co.za/industry", "freq": "daily", "priority": 1},
    ],
    "turkey": [
        {"name": "C4Defence", "url": "https://www.c4defence.com/en", "freq": "weekly", "priority": 1},
        {"name": "Daily Sabah Defence", "url": "https://www.dailysabah.com/defense", "freq": "weekly", "priority": 2},
        {"name": "SSB Export Data", "url": "https://www.ssb.gov.tr", "freq": "monthly", "priority": 1},
    ],
    "korea": [
        {"name": "KED Global", "url": "https://www.kedglobal.com/aerospace-defense", "freq": "weekly", "priority": 1},
        {"name": "DAPA Korea", "url": "https://www.dapa.go.kr", "freq": "monthly", "priority": 1},
        {"name": "FlightGlobal Korea", "url": "https://www.flightglobal.com", "freq": "weekly", "priority": 2},
    ],
    "nordic": [
        {"name": "Nordic Defence Review", "url": "https://nordicdefencereview.com", "freq": "weekly", "priority": 1},
    ],
    "israel": [
        {"name": "Elbit Investor Relations", "url": "https://www.elbitsystems.com/investors", "freq": "quarterly", "priority": 1},
        {"name": "Rafael Press", "url": "https://www.rafael.co.il/press", "freq": "monthly", "priority": 1},
    ],
    "export_policy": [
        {"name": "UK ECJU Notices", "url": "https://www.gov.uk/government/collections/notices-to-exporters",
         "freq": "weekly", "priority": 1},
        {"name": "DDTC Updates", "url": "https://www.pmddtc.state.gov", "freq": "monthly", "priority": 1},
        {"name": "EU Dual-Use Updates", "url": "https://policy.trade.ec.europa.eu/help-exporters-and-importers/exporting-dual-use-items_en",
         "freq": "monthly", "priority": 1},
        {"name": "OFAC SDN List", "url": "https://ofac.treasury.gov/sanctions-list-search", "freq": "daily", "priority": 1},
    ],
}


# ── Singleton ──────────────────────────────────────────────────────────────────
_db_instance: Optional[OEMDatabase] = None

def get_oem_database() -> OEMDatabase:
    global _db_instance
    if _db_instance is None:
        _db_instance = OEMDatabase()
    return _db_instance


if __name__ == "__main__":
    db = OEMDatabase()
    s = db.stats()
    print(f"\n=== CRUCIX OEM Database v2.0 ===")
    print(f"Total manufacturers:  {s['total']}")
    print(f"Countries covered:    {len(s['by_country'])}")
    print(f"Unrestricted OEMs:    {s['unrestricted']}")
    print(f"Lusophone specialists: {s['lusophone']}")
    print(f"Competitor entries:   {s['competitors']}")
    print(f"ITAR-controlled:      {s['itar_controlled']}")
    print(f"\n=== By Country ===")
    for c, n in sorted(s["by_country"].items(), key=lambda x: -x[1]):
        print(f"  {c}: {n}")
    print(f"\n=== Counter-IED for Angola (ranked) ===")
    for r in db.search_by_capability("counter-IED", destination="Angola")[:5]:
        flag = "⛔ITAR" if r["itar_controlled"] else ("✅UNRESTR" if any("UNRESTR" in e or "NCACC" in e for e in r["export_regime"]) else "⚠️SIEL")
        print(f"  {r['rank']}. {r['name']} ({r['country']}) {flag}")
    print(f"\n=== Lusophone Africa Specialists ===")
    for r in db.get_lusophone_specialists():
        print(f"  ✓ {r['name']} ({r['country']}) — {', '.join(r['capabilities'][:3])}")
    print(f"\n=== Competitors in Angola ===")
    for r in db.get_competitors_in_market("Angola"):
        print(f"  ⚠ {r['name']} — {r['notes'][:80]}")
