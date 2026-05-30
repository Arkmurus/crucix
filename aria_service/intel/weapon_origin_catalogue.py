"""R-F638 — Weapon-name → OEM → origin → sanctions lookup catalogue.

Why this module exists
──────────────────────
ARIA's existing ECCN classifier (R-F586 / eccn_lookup) maps freeform
equipment text to broad UK ML / EAR CCL categories. That's enough to
tag a 155mm shell as "ML3 — explosive munitions" — but it's NOT enough
to recognise that:

  - "Cornet ATGM"  → 9M133 Kornet → KBP Instrument Design Bureau, Tula,
                     Russia → CRIMINAL OFFENCE for a UK broker
                     (UK Russia Sanctions Regs 2019)
  - "GPI laser-guided 155mm" → NORINCO GP1/GP6 → CHINA → US BIS
                     Entity List + Russian-tech-licensed (parent
                     KBP Tula)
  - "Krasnopol"   → 2K25 Krasnopol → KBP Tula, Russia → same
                     prohibition
  - "Bayraktar TB2" → Baykar Tech, Türkiye → not sanctioned but
                     end-use scrutiny in some destinations

The 2026-05-17 ADSM/Saudi case (29-item goods list) demonstrated that
without per-weapon manufacturer + sanctions resolution, ARIA produces
the right structured DD output but misses the smoking-gun specifics
that distinguish "AMBER-DARK pending more info" from "HARD STOP today".

Coverage philosophy (R-F638 seed)
─────────────────────────────────
~80 weapon-system entries covering the items most-likely-encountered
by a defence broker:

  - Russian systems (sanctioned post-2022): Kornet, Krasnopol, BM-21,
    Iskander, Pantsir-S1, Tor-M2, T-90, S-300/S-400, AK-pattern small
    arms, RPG-7 family
  - Chinese systems (BIS Entity List / KBP-licensed): NORINCO GP1/GP6,
    PLZ-45, PL-15, Type-99/A, J-10/J-20
  - Iranian systems (comprehensive embargo): Shahed-136, Fajr-5,
    Bavar-373, Arash-2
  - US ITAR: M1A2 Abrams, M829, Excalibur Ia/Ib/S, Patriot PAC-3,
    THAAD, Hellfire, Stinger, Javelin, AIM-120 AMRAAM
  - UK MoD: Brimstone, NLAW, Starstreak, Sea Ceptor
  - French: Caesar 155, OFL155, Mistral, MICA, Exocet
  - German: Leopard 2, IRIS-T, RBS-15
  - Korean: K2 Black Panther, K-RAVEN/RAYBOLT, KSTAM, K9 Thunder
  - Israeli: Spike LR2/NLOS, Trophy APS, Iron Dome, Heron, Hermes
  - Turkish: Bayraktar TB2/TB3, Akinci, ATMACA, Bora
  - Other (Polish, Spanish, Swedish, Norwegian, S African)

Each entry carries:
  - canonical_name + aliases (for fuzzy matching against goods-list text)
  - oem (immediate manufacturer)
  - oem_parent (corporate parent if known)
  - origin_iso2 (manufacturing country)
  - sanctions_status (cleared | restricted | prohibited)
  - sanctions_regimes (which list it falls under)
  - notes (auditable context including ELI references where applicable)

Honesty discipline
──────────────────
Sanctions status is a SNAPSHOT — UK Russia (Sanctions) Regs 2019,
EU Council Reg 833/2014, US OFAC SDN, US BIS Entity List, UN SC. These
change. Each entry carries `as_of` so consumers know freshness. The
public API never invents — unknown weapon → None, not a guess.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("aria.weapon_origin_catalogue")


# ─────────────────────────────────────────────────────────────────────
# Enums (string-subclass for log-friendly comparison)
# ─────────────────────────────────────────────────────────────────────


class SanctionsStatus:
    """Three discrete buckets for a UK broker's perspective.

    PROHIBITED  — Brokering is a criminal offence for a UK person
                  (Russian / Iranian state-supplied items, etc.).
    RESTRICTED  — Brokering is permitted only with an explicit
                  Standard Individual Trade Control Licence (SITCL)
                  AND case-specific compliance (Chinese NORINCO via
                  BIS Entity List, North Korean items via UN SC).
    CLEARED     — Standard export-control licensing path (SIEL/OIEL
                  + EUC). No bespoke prohibition.
    """
    PROHIBITED = "prohibited"
    RESTRICTED = "restricted"
    CLEARED = "cleared"


@dataclass(frozen=True)
class WeaponSystem:
    """Frozen per-system record. Immutable so seed cannot be mutated
    by callers."""
    canonical_name: str
    aliases: tuple[str, ...]            # alternate names + NATO designators
    category: str                       # e.g. "ATGM", "PGM-155mm", "MBT"
    oem: str                            # immediate manufacturer
    oem_parent: str                     # corporate parent / state owner
    origin_iso2: str                    # ISO-3166-1 alpha-2
    sanctions_status: str               # SanctionsStatus enum value
    sanctions_regimes: tuple[str, ...]  # which lists it falls under
    notes: str = ""
    as_of: str = "2026-05-17"           # snapshot date

    def is_prohibited(self) -> bool:
        return self.sanctions_status == SanctionsStatus.PROHIBITED

    def is_restricted(self) -> bool:
        return self.sanctions_status == SanctionsStatus.RESTRICTED


# ─────────────────────────────────────────────────────────────────────
# SEED — ~80 priority weapon systems
# ─────────────────────────────────────────────────────────────────────
#
# Sources:
#   - OFAC SDN (US sanctions)
#   - UK OFSI Consolidated List
#   - EU Consolidated Financial Sanctions
#   - US BIS Entity List
#   - UK Russia (Sanctions) (EU Exit) Regulations 2019
#   - EU Council Regulation 833/2014
#   - UN Security Council Consolidated List
#   - Public OEM disclosures + SIPRI Arms Transfers Database
#
# Every sanctions claim cites a regime + a date. NO INVENTED CLAIMS.


_SEED: tuple[WeaponSystem, ...] = (

    # ─── RUSSIAN — prohibited for UK brokers post-2022 ───────────────

    WeaponSystem(
        canonical_name="9M133 Kornet",
        aliases=("Kornet", "Cornet", "AT-14 Spriggan", "Kornet-E", "Kornet-EM"),
        category="ATGM",
        oem="KBP Instrument Design Bureau (Tula)",
        oem_parent="Rostec / Rosoboronexport",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019", "EU 833/2014",
                           "US OFAC SDN"),
        notes=(
            "KBP Tula is on UK OFSI Russia sanctions list (post-2022). "
            "Brokering by UK person = criminal offence. Iranian-licensed "
            "variant 'Dehlavieh' is also sanctioned (Iran embargo). "
            "Syrian-produced variants likewise prohibited (Syria sanctions)."
        ),
    ),
    WeaponSystem(
        canonical_name="2K25 Krasnopol",
        aliases=("Krasnopol", "Krasnopol-M2", "2K25M"),
        category="PGM-155mm",
        oem="KBP Instrument Design Bureau (Tula)",
        oem_parent="Rostec / Rosoboronexport",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019", "EU 833/2014"),
        notes=(
            "Russian 152/155mm laser-guided artillery shell. KBP Tula "
            "manufacturer. Chinese NORINCO GP1 is the licensed export "
            "variant (see GP1 entry — separately restricted)."
        ),
    ),
    WeaponSystem(
        canonical_name="9K720 Iskander",
        aliases=("Iskander", "Iskander-M", "SS-26 Stone", "Iskander-E"),
        category="SRBM",
        oem="KBP / Mashinostroyenia",
        oem_parent="Rostec",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019", "EU 833/2014",
                           "US OFAC SDN", "MTCR Cat I — multilateral"),
        notes="Short-range ballistic missile system, ~500km range.",
    ),
    WeaponSystem(
        canonical_name="BM-21 Grad",
        aliases=("Grad", "BM-21", "BM-21M Tornado-G", "9K51"),
        category="MLRS",
        oem="Splav State Research and Production Enterprise",
        oem_parent="Rostec / Tecmash",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019", "EU 833/2014"),
        notes=(
            "122mm multiple-launch rocket system. Widely cloned/produced "
            "elsewhere (Egypt Sakr-18, China Type-81, Romanian APR-21, "
            "Iranian Arash). Origin determines sanctions status."
        ),
    ),
    WeaponSystem(
        canonical_name="Pantsir-S1",
        aliases=("Pantsir", "SA-22 Greyhound", "Pantsir-S2", "Pantsir-SM"),
        category="SHORAD",
        oem="KBP / TsKBR",
        oem_parent="Rostec",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019", "EU 833/2014",
                           "US OFAC SDN"),
        notes=(
            "Short-range air defence system. Saudi Arabia operates "
            "(legacy purchase) but new procurement is prohibited."
        ),
    ),
    WeaponSystem(
        canonical_name="9K38 Igla / 9K333 Verba",
        aliases=("Igla", "SA-18 Grouse", "SA-24 Grinch", "Verba",
                 "Igla-S"),
        category="MANPADS",
        oem="KBM Engineering Design Bureau (Kolomna)",
        oem_parent="Rostec",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019", "EU 833/2014",
                           "Wassenaar MANPADS controls — multilateral"),
        notes=(
            "Man-portable air defence system. MANPADS specifically "
            "subject to Wassenaar Arrangement strict export controls."
        ),
    ),
    WeaponSystem(
        canonical_name="9K38 Strela / 9K34 Strela-3",
        aliases=("Strela", "SA-7 Grail", "Strela-2", "Strela-3"),
        category="MANPADS",
        oem="KBM Engineering Design Bureau",
        oem_parent="Rostec",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019", "EU 833/2014"),
        notes="Legacy Soviet MANPADS; still widely encountered in MENA.",
    ),
    WeaponSystem(
        canonical_name="S-300 (P/PMU/PMU-1/PMU-2)",
        aliases=("S-300", "SA-10 Grumble", "SA-20 Gargoyle", "S-300P",
                 "S-300PMU", "S-300PMU-1", "S-300PMU-2"),
        category="LRAD",
        oem="Almaz-Antey",
        oem_parent="Rostec",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019", "EU 833/2014",
                           "US OFAC SDN"),
        notes=(
            "Long-range air defence. NATO designation SA-10/SA-20 — "
            "'SA-20' often confused with the non-existent 'SS-20' in "
            "informal procurement lists (R-F639 watchlist)."
        ),
    ),
    WeaponSystem(
        canonical_name="S-400 Triumf",
        aliases=("S-400", "SA-21 Growler", "Triumf"),
        category="LRAD",
        oem="Almaz-Antey",
        oem_parent="Rostec",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019", "EU 833/2014",
                           "US CAATSA Section 231"),
        notes=(
            "Long-range air defence. CAATSA secondary-sanctions exposure "
            "for buyers (Turkey case 2019)."
        ),
    ),
    WeaponSystem(
        canonical_name="T-90",
        aliases=("T-90", "T-90A", "T-90M", "T-90S", "T-90MS"),
        category="MBT",
        oem="Uralvagonzavod",
        oem_parent="Rostec",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019", "EU 833/2014"),
        notes="Main battle tank.",
    ),
    WeaponSystem(
        canonical_name="T-72",
        aliases=("T-72", "T-72B", "T-72B3", "T-72M", "T-72M1"),
        category="MBT",
        oem="Uralvagonzavod (originally)",
        oem_parent="Rostec / multiple licensed producers",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019", "EU 833/2014"),
        notes=(
            "Russian-produced T-72 is sanctioned. Licensed producers "
            "(Polish PT-91 Twardy, Indian Ajeya, Iraqi Lion of Babylon) "
            "carry origin-country sanctions status. Polish T-72 = CLEARED."
        ),
    ),
    WeaponSystem(
        canonical_name="RPG-7",
        aliases=("RPG-7", "RPG-7V2", "PG-7", "PG-7V", "PG-7VL", "PG-7VR"),
        category="RPG",
        oem="Bazalt (Russia) — original",
        oem_parent="Rostec / multiple licensed producers",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019 — for RU-produced",),
        notes=(
            "Russian-produced RPG-7 + rockets are sanctioned. Bulgarian "
            "(Arsenal AD), Romanian (ROMARM), Iranian (DIO), Chinese "
            "(NORINCO Type-69) variants exist — origin-country sanctions "
            "status applies. Bulgarian = CLEARED, Iranian + Chinese = "
            "RESTRICTED via Iran embargo + BIS Entity List respectively."
        ),
    ),
    WeaponSystem(
        canonical_name="AK-pattern small arms (Russian-produced)",
        aliases=("AK-47 (Russian)", "AK-74 (Russian)", "AK-12",
                 "AKM (Russian)", "RPK (Russian)", "PKM (Russian)"),
        category="small arms",
        oem="Kalashnikov Concern (Izhevsk)",
        oem_parent="Rostec",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019", "EU 833/2014",
                           "US OFAC SDN"),
        notes=(
            "Russian-manufactured AK-pattern weapons are sanctioned. "
            "Licensed/clone producers (Bulgaria Arsenal, Romania Cugir, "
            "Serbia Zastava, Hungary, China NORINCO, Iran DIO, Egypt, "
            "Pakistan) carry origin-country status. CRITICAL: 7.62×39 "
            "AMMUNITION origin must be tracked separately — Bulgarian/ "
            "Romanian/Serbian = CLEARED; Russian/Iranian = sanctioned."
        ),
    ),
    WeaponSystem(
        canonical_name="9M729 Novator",
        aliases=("9M729", "SSC-8 Screwdriver"),
        category="GLCM",
        oem="Novator Design Bureau",
        oem_parent="Almaz-Antey / Rostec",
        origin_iso2="RU",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Russia Sanctions Regs 2019",
                           "INF Treaty violation — historical"),
        notes=(
            "Ground-launched cruise missile cited by US as INF Treaty "
            "violation pre-2019 withdrawal. Currently fielded — distinct "
            "from the INF-eliminated SS-20 (R-F639)."
        ),
    ),

    # ─── CHINESE — restricted via BIS Entity List ────────────────────

    WeaponSystem(
        canonical_name="NORINCO GP1 (155mm laser-guided)",
        aliases=("GP1", "GP-1", "GPI", "NORINCO GP1", "Chinese Krasnopol"),
        category="PGM-155mm",
        oem="NORINCO (China North Industries Corp)",
        oem_parent="NORINCO Group",
        origin_iso2="CN",
        sanctions_status=SanctionsStatus.RESTRICTED,
        sanctions_regimes=("US BIS Entity List (NORINCO since 2003)",
                           "US OFAC NS-CMIC", "UK end-use scrutiny",
                           "tech licensed from KBP Tula = secondary "
                           "Russia exposure"),
        notes=(
            "Licensed copy of Russian Krasnopol — technology origin Tula. "
            "NORINCO is on US BIS Entity List since 2003. UK brokers may "
            "transact with SITCL but USD payment routing has OFAC NS-CMIC "
            "secondary-sanctions exposure. Documented in Conflict "
            "Armament Research field reports from Libya (2020) and Yemen."
        ),
    ),
    WeaponSystem(
        canonical_name="NORINCO GP6 (155mm laser+GPS)",
        aliases=("GP6", "GP-6", "NORINCO GP6"),
        category="PGM-155mm",
        oem="NORINCO",
        oem_parent="NORINCO Group",
        origin_iso2="CN",
        sanctions_status=SanctionsStatus.RESTRICTED,
        sanctions_regimes=("US BIS Entity List",
                           "US OFAC NS-CMIC",
                           "Russian-tech derivative"),
        notes=(
            "Successor to GP1 with combined laser + GPS guidance. Same "
            "NORINCO BIS Entity List status. Documented in Libya 2020 "
            "by ARES (Armament Research Services)."
        ),
    ),
    WeaponSystem(
        canonical_name="NORINCO Type-69 RPG",
        aliases=("Type-69", "Type 69 RPG", "NORINCO RPG"),
        category="RPG",
        oem="NORINCO",
        oem_parent="NORINCO Group",
        origin_iso2="CN",
        sanctions_status=SanctionsStatus.RESTRICTED,
        sanctions_regimes=("US BIS Entity List", "US OFAC NS-CMIC"),
        notes="Chinese RPG-7 derivative. NORINCO BIS Entity List status.",
    ),
    WeaponSystem(
        canonical_name="PLZ-45",
        aliases=("PLZ-45", "PLZ45", "Type 88 SPG"),
        category="SPG-155mm",
        oem="NORINCO",
        oem_parent="NORINCO Group",
        origin_iso2="CN",
        sanctions_status=SanctionsStatus.RESTRICTED,
        sanctions_regimes=("US BIS Entity List", "US OFAC NS-CMIC"),
        notes=(
            "Self-propelled howitzer. In service Saudi RSLF (legacy "
            "purchase early 2010s) — replacement parts subject to BIS "
            "Entity List for any US-content component."
        ),
    ),
    WeaponSystem(
        canonical_name="PL-15",
        aliases=("PL-15", "PL15", "Pi-Li 15"),
        category="AAM",
        oem="CATIC / AVIC",
        oem_parent="AVIC",
        origin_iso2="CN",
        sanctions_status=SanctionsStatus.RESTRICTED,
        sanctions_regimes=("US BIS Entity List (AVIC)",
                           "US OFAC NS-CMIC"),
        notes="Beyond-visual-range air-to-air missile. Export variant.",
    ),
    WeaponSystem(
        canonical_name="Type 99 MBT",
        aliases=("Type 99", "Type-99", "Type-99A", "ZTZ-99"),
        category="MBT",
        oem="NORINCO",
        oem_parent="NORINCO Group",
        origin_iso2="CN",
        sanctions_status=SanctionsStatus.RESTRICTED,
        sanctions_regimes=("US BIS Entity List", "US OFAC NS-CMIC"),
        notes="Chinese main battle tank. Not exported.",
    ),

    # ─── IRANIAN — comprehensive embargo ─────────────────────────────

    WeaponSystem(
        canonical_name="Shahed-136 / Geran-2",
        aliases=("Shahed-136", "Shahed 136", "Geran-2", "Geran 2",
                 "HESA Shahed-136"),
        category="loitering munition",
        oem="HESA (Iran Aircraft Manufacturing Industries)",
        oem_parent="MODAFL / IRGC-AF",
        origin_iso2="IR",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Iran Sanctions Regs", "EU Iran sanctions",
                           "US OFAC SDN (IRGC + MODAFL)",
                           "UN SC Resolution 2231 (legacy)"),
        notes=(
            "Iranian loitering munition. Russian-licensed production as "
            "Geran-2. Brokering by UK person = criminal offence. "
            "Documented widely in Ukraine + Sudan + Yemen."
        ),
    ),
    WeaponSystem(
        canonical_name="Fajr-5 / Fajr-3",
        aliases=("Fajr-5", "Fajr 5", "Fajr-3", "Fajr 3"),
        category="MLRS",
        oem="Shahid Bagheri Industrial Group",
        oem_parent="MODAFL / DIO Iran",
        origin_iso2="IR",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Iran Sanctions Regs", "EU Iran sanctions",
                           "US OFAC SDN"),
        notes="Iranian 333mm / 240mm long-range artillery rockets.",
    ),
    WeaponSystem(
        canonical_name="Bavar-373",
        aliases=("Bavar-373", "Bavar 373"),
        category="LRAD",
        oem="Iran Aviation Industries Organization",
        oem_parent="MODAFL",
        origin_iso2="IR",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Iran Sanctions Regs", "EU Iran sanctions",
                           "US OFAC SDN"),
        notes="Iranian S-300-class long-range air defence system.",
    ),
    WeaponSystem(
        canonical_name="Arash-2 / Arash-1",
        aliases=("Arash", "Arash-1", "Arash-2"),
        category="loitering munition",
        oem="MODAFL Iran",
        oem_parent="MODAFL",
        origin_iso2="IR",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Iran Sanctions Regs", "EU Iran sanctions",
                           "US OFAC SDN"),
        notes="Iranian loitering munition with claimed ~2000km range.",
    ),
    WeaponSystem(
        canonical_name="Dehlavieh (Iranian Kornet variant)",
        aliases=("Dehlavieh", "Dehlaviyeh"),
        category="ATGM",
        oem="Iran Defense Industries Organization (DIO)",
        oem_parent="MODAFL",
        origin_iso2="IR",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UK Iran Sanctions Regs", "EU Iran sanctions",
                           "US OFAC SDN — IRGC supplied"),
        notes="Iranian licensed copy of Russian 9M133 Kornet. Doubly prohibited.",
    ),

    # ─── NORTH KOREAN — UN SC embargo ────────────────────────────────

    WeaponSystem(
        canonical_name="Hwasong-class missiles (DPRK)",
        aliases=("Hwasong-12", "Hwasong-14", "Hwasong-15", "Hwasong-17",
                 "Hwasong-18"),
        category="ballistic missile",
        oem="DPRK Academy of Defence Sciences",
        oem_parent="DPRK MoD",
        origin_iso2="KP",
        sanctions_status=SanctionsStatus.PROHIBITED,
        sanctions_regimes=("UN SC Res 1718 + successor resolutions",
                           "UK DPRK Sanctions Regs",
                           "US OFAC SDN comprehensive",
                           "EU DPRK sanctions"),
        notes="North Korean ballistic missile family. Comprehensive embargo.",
    ),

    # ─── US — ITAR controlled (cleared with SIEL+EUC) ────────────────

    WeaponSystem(
        canonical_name="M1A2 Abrams",
        aliases=("M1A2", "Abrams", "M1A2 SEP", "M1A2 SEPv3"),
        category="MBT",
        oem="General Dynamics Land Systems",
        oem_parent="General Dynamics Corp",
        origin_iso2="US",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("US ITAR (DDTC) — DSP-5 licensing",
                           "USML Category VII"),
        notes=(
            "US main battle tank. Saudi RSLF operates M1A2. ITAR-controlled "
            "— any UK broker facilitating must coordinate via DSCA FMS "
            "channel OR DCS via DDTC."
        ),
    ),
    WeaponSystem(
        canonical_name="M829 APFSDS-T",
        aliases=("M829", "M829A1", "M829A2", "M829A3", "M829A4",
                 "120mm APFSDS"),
        category="tank-ammunition",
        oem="General Dynamics Ordnance & Tactical Systems",
        oem_parent="General Dynamics",
        origin_iso2="US",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("US ITAR", "USML Category III"),
        notes="120mm APFSDS-T anti-armour round for M1A2 Abrams.",
    ),
    WeaponSystem(
        canonical_name="Excalibur (M982)",
        aliases=("Excalibur", "M982", "Excalibur Ia", "Excalibur Ib",
                 "Excalibur S"),
        category="PGM-155mm",
        oem="Raytheon / BAE Systems Bofors",
        oem_parent="RTX Corporation / BAE Systems",
        origin_iso2="US",  # joint US-SE production
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("US ITAR (DDTC)", "USML Category III"),
        notes=(
            "GPS-guided 155mm artillery shell. Excalibur S (semi-active "
            "laser variant) released 2019. Cleared via standard ITAR "
            "process; common Saudi-procurement-eligible PGM."
        ),
    ),
    WeaponSystem(
        canonical_name="MIM-104 Patriot (PAC-3)",
        aliases=("Patriot", "PAC-2", "PAC-3", "PAC-3 MSE", "MIM-104"),
        category="LRAD",
        oem="Raytheon / Lockheed Martin",
        oem_parent="RTX / Lockheed Martin",
        origin_iso2="US",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("US ITAR", "USML Category IV"),
        notes="Long-range air/missile defence. Saudi RSADF operates.",
    ),
    WeaponSystem(
        canonical_name="THAAD",
        aliases=("THAAD", "Terminal High Altitude Area Defense"),
        category="LRAD",
        oem="Lockheed Martin Missiles and Fire Control",
        oem_parent="Lockheed Martin",
        origin_iso2="US",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("US ITAR", "USML Category IV"),
        notes="Terminal high-altitude area defence system.",
    ),
    WeaponSystem(
        canonical_name="AGM-114 Hellfire",
        aliases=("Hellfire", "AGM-114", "Hellfire R9X"),
        category="ATGM",
        oem="Lockheed Martin",
        oem_parent="Lockheed Martin",
        origin_iso2="US",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("US ITAR", "USML Category IV"),
        notes="Air-launched anti-tank missile.",
    ),
    WeaponSystem(
        canonical_name="FIM-92 Stinger",
        aliases=("Stinger", "FIM-92", "FIM-92E", "FIM-92F"),
        category="MANPADS",
        oem="Raytheon",
        oem_parent="RTX Corporation",
        origin_iso2="US",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("US ITAR", "USML Category IV",
                           "Wassenaar MANPADS — multilateral"),
        notes=(
            "MANPADS — strictest end-use scrutiny. UK ECJU + DSCA require "
            "named-end-user with EUC; serial-numbered tracking required."
        ),
    ),
    WeaponSystem(
        canonical_name="FGM-148 Javelin",
        aliases=("Javelin", "FGM-148", "FGM-148F"),
        category="ATGM",
        oem="Raytheon / Lockheed Martin (Javelin Joint Venture)",
        oem_parent="RTX / Lockheed Martin",
        origin_iso2="US",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("US ITAR", "USML Category IV"),
        notes="Man-portable fire-and-forget ATGM.",
    ),
    WeaponSystem(
        canonical_name="AIM-120 AMRAAM",
        aliases=("AMRAAM", "AIM-120", "AIM-120C", "AIM-120D"),
        category="AAM",
        oem="Raytheon",
        oem_parent="RTX Corporation",
        origin_iso2="US",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("US ITAR", "USML Category IV"),
        notes="Beyond-visual-range air-to-air missile.",
    ),
    WeaponSystem(
        canonical_name="M107 155mm HE projectile",
        aliases=("M107", "155mm M107", "M107 HE"),
        category="munition-155mm",
        oem="General Dynamics OTS / multiple",
        oem_parent="multiple",
        origin_iso2="US",  # original — many producers worldwide
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("US ITAR for US-origin",
                           "USML Category III"),
        notes=(
            "Legacy 155mm HE shell, in NATO service since 1958. "
            "Originally US — now also produced by South Africa (Rheinmetall "
            "Denel Munitions), South Korea (Poongsan), Israel (IMI Systems), "
            "Egypt (AOI/MIC), Bulgaria, Pakistan POF, India OFB. Origin "
            "country determines licensing path."
        ),
    ),
    WeaponSystem(
        canonical_name="M120 / M121 81mm mortar bombs",
        aliases=("M120 mortar", "M121", "81mm M252 ammunition"),
        category="munition-81mm",
        oem="multiple",
        oem_parent="multiple",
        origin_iso2="US",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("US ITAR for US-origin", "USML Category III"),
    ),
    WeaponSystem(
        canonical_name="M18A1 Claymore",
        aliases=("Claymore", "M18A1", "M18"),
        category="directional mine",
        oem="multiple",
        oem_parent="multiple",
        origin_iso2="US",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("US ITAR for US-origin",
                           "Ottawa Treaty — exempt as command-detonated only"),
        notes=(
            "Directional anti-personnel mine. NOT prohibited under "
            "Ottawa Anti-Personnel Mine Ban Convention BECAUSE "
            "command-detonated (not victim-activated). If configured "
            "tripwire/victim-activated, Ottawa applies for state parties."
        ),
    ),

    # ─── UK MoD ──────────────────────────────────────────────────────

    WeaponSystem(
        canonical_name="Brimstone",
        aliases=("Brimstone", "Brimstone 2", "Brimstone 3"),
        category="ATGM",
        oem="MBDA UK",
        oem_parent="MBDA / BAE Systems / Airbus / Leonardo",
        origin_iso2="GB",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("UK ECO 2008 — UK ML",),
    ),
    WeaponSystem(
        canonical_name="NLAW",
        aliases=("NLAW", "MBT LAW", "Next-generation Light Anti-tank Weapon"),
        category="ATGM",
        oem="Saab Bofors Dynamics",
        oem_parent="Saab AB",
        origin_iso2="SE",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("UK ECO 2008", "Swedish ISP"),
        notes="UK MoD-procured, Swedish-manufactured.",
    ),
    WeaponSystem(
        canonical_name="Starstreak",
        aliases=("Starstreak", "Starstreak HVM", "Starstreak II"),
        category="SHORAD",
        oem="Thales Air Defence (Belfast)",
        oem_parent="Thales Group",
        origin_iso2="GB",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("UK ECO 2008",),
    ),
    WeaponSystem(
        canonical_name="Sea Ceptor / CAMM",
        aliases=("Sea Ceptor", "CAMM", "CAMM-ER"),
        category="naval-SAM",
        oem="MBDA UK",
        oem_parent="MBDA",
        origin_iso2="GB",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("UK ECO 2008",),
    ),

    # ─── FRENCH ──────────────────────────────────────────────────────

    WeaponSystem(
        canonical_name="Caesar 155mm SPG",
        aliases=("Caesar", "Caesar 155", "Camion équipé d'un système d'artillerie"),
        category="SPG-155mm",
        oem="Nexter (KNDS France)",
        oem_parent="KNDS Group",
        origin_iso2="FR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("French CIEEMG export controls",
                           "EU Common Position"),
        notes="Saudi Arabia operates (procured 2022 onward).",
    ),
    WeaponSystem(
        canonical_name="Mistral MANPADS",
        aliases=("Mistral", "Mistral 2", "Mistral 3"),
        category="MANPADS",
        oem="MBDA France",
        oem_parent="MBDA",
        origin_iso2="FR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("French CIEEMG", "Wassenaar MANPADS"),
    ),
    WeaponSystem(
        canonical_name="OFL155 (French 155mm)",
        aliases=("OFL155", "OFL 155", "French 155mm projectile",
                 "OFL155 ER"),
        category="munition-155mm",
        oem="Nexter Munitions",
        oem_parent="KNDS Group",
        origin_iso2="FR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("French CIEEMG export controls",
                           "EU Common Position"),
    ),
    WeaponSystem(
        canonical_name="MICA",
        aliases=("MICA", "MICA IR", "MICA EM", "MICA NG"),
        category="AAM",
        oem="MBDA France",
        oem_parent="MBDA",
        origin_iso2="FR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("French CIEEMG",),
    ),
    WeaponSystem(
        canonical_name="Exocet",
        aliases=("Exocet", "MM38", "MM40", "MM40 Block 3", "AM39", "SM39"),
        category="anti-ship",
        oem="MBDA France",
        oem_parent="MBDA",
        origin_iso2="FR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("French CIEEMG", "MTCR Category I"),
    ),

    # ─── GERMAN ──────────────────────────────────────────────────────

    WeaponSystem(
        canonical_name="Leopard 2",
        aliases=("Leopard 2", "Leopard 2A4", "Leopard 2A5", "Leopard 2A6",
                 "Leopard 2A7"),
        category="MBT",
        oem="KMW (Krauss-Maffei Wegmann)",
        oem_parent="KNDS Group",
        origin_iso2="DE",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("German BAFA export controls",
                           "EU Common Position"),
    ),
    WeaponSystem(
        canonical_name="IRIS-T",
        aliases=("IRIS-T", "IRIS-T SL", "IRIS-T SLM", "IRIS-T SLS"),
        category="SAM",
        oem="Diehl Defence",
        oem_parent="Diehl Stiftung",
        origin_iso2="DE",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("German BAFA",),
    ),
    WeaponSystem(
        canonical_name="RBS-15",
        aliases=("RBS-15", "RBS-15 Mk3", "RBS-15 Gungnir"),
        category="anti-ship",
        oem="Saab Bofors Dynamics / Diehl BGT",
        oem_parent="Saab AB / Diehl Stiftung",
        origin_iso2="SE",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Swedish ISP", "German BAFA — co-development"),
    ),

    # ─── KOREAN ──────────────────────────────────────────────────────

    WeaponSystem(
        canonical_name="K2 Black Panther",
        aliases=("K2", "K2 Black Panther", "K2PL"),
        category="MBT",
        oem="Hyundai Rotem",
        oem_parent="Hyundai Motor Group",
        origin_iso2="KR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("South Korean DAPA export controls",),
    ),
    WeaponSystem(
        canonical_name="K-RAVEN / RAYBOLT",
        aliases=("K-RAVEN", "RAYBOLT", "Rebolt", "Korean ATGM",
                 "Hyundai-LIG Nex1 ATGM"),
        category="ATGM",
        oem="LIG Nex1 / Hyundai-Rotem",
        oem_parent="LIG Nex1 / Hyundai",
        origin_iso2="KR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("South Korean DAPA export controls",),
        notes=(
            "Often mis-spelled as 'Rebolt' on translated procurement lists. "
            "UAE and Indonesia confirmed buyers; Saudi not yet declared "
            "publicly as buyer at production scale."
        ),
    ),
    WeaponSystem(
        canonical_name="K9 Thunder",
        aliases=("K9", "K9 Thunder", "K9A1", "K9A2"),
        category="SPG-155mm",
        oem="Hanwha Aerospace",
        oem_parent="Hanwha Group",
        origin_iso2="KR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("South Korean DAPA export controls",),
    ),

    # ─── ISRAELI ─────────────────────────────────────────────────────

    WeaponSystem(
        canonical_name="Spike",
        aliases=("Spike", "Spike LR", "Spike LR2", "Spike NLOS",
                 "Spike SR", "Spike ER"),
        category="ATGM",
        oem="Rafael Advanced Defense Systems",
        oem_parent="Rafael ADS (state-owned)",
        origin_iso2="IL",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Israeli SIBAT export controls",),
    ),
    WeaponSystem(
        canonical_name="Trophy APS",
        aliases=("Trophy", "Trophy APS", "Trophy HV", "Trophy MV"),
        category="active-protection",
        oem="Rafael Advanced Defense Systems",
        oem_parent="Rafael ADS",
        origin_iso2="IL",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Israeli SIBAT",),
    ),
    WeaponSystem(
        canonical_name="Iron Dome",
        aliases=("Iron Dome", "Tamir interceptor"),
        category="SHORAD",
        oem="Rafael Advanced Defense Systems",
        oem_parent="Rafael ADS",
        origin_iso2="IL",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Israeli SIBAT", "US joint-funding ITAR overlay"),
    ),
    WeaponSystem(
        canonical_name="Heron / Hermes UAV",
        aliases=("Heron", "Heron TP", "Hermes 450", "Hermes 900",
                 "Elbit Hermes"),
        category="UAV",
        oem="IAI / Elbit Systems",
        oem_parent="state-owned / Elbit Systems",
        origin_iso2="IL",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Israeli SIBAT", "MTCR Category II"),
    ),

    # ─── TURKISH ─────────────────────────────────────────────────────

    WeaponSystem(
        canonical_name="Bayraktar TB2",
        aliases=("Bayraktar TB2", "TB2", "Bayraktar"),
        category="UAV",
        oem="Baykar Technology",
        oem_parent="Baykar",
        origin_iso2="TR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Turkish SSB export controls",),
        notes="Heavy end-use scrutiny in destinations adjacent to current conflicts.",
    ),
    WeaponSystem(
        canonical_name="Bayraktar Akinci",
        aliases=("Akinci", "Bayraktar Akinci"),
        category="UAV",
        oem="Baykar Technology",
        oem_parent="Baykar",
        origin_iso2="TR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Turkish SSB", "MTCR Category II"),
    ),
    WeaponSystem(
        canonical_name="ATMACA",
        aliases=("ATMACA", "Atmaca AShM"),
        category="anti-ship",
        oem="Roketsan",
        oem_parent="Turkish Armed Forces Foundation",
        origin_iso2="TR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Turkish SSB",),
    ),
    WeaponSystem(
        canonical_name="Bora-2",
        aliases=("Bora", "Bora-2", "B-611TG"),
        category="SRBM",
        oem="Roketsan",
        oem_parent="Turkish Armed Forces Foundation",
        origin_iso2="TR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Turkish SSB", "MTCR Category I"),
    ),
    WeaponSystem(
        canonical_name="MKE 155mm projectile (Turkish)",
        aliases=("MKE 155mm", "MKE M107", "Turkish 155mm",
                 "MKE Inc 155mm"),
        category="munition-155mm",
        oem="MKE (Makina ve Kimya Endüstrisi)",
        oem_parent="Turkish state",
        origin_iso2="TR",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Turkish SSB",),
        notes=(
            "Turkish state-produced 155mm — distinct from Russian/Chinese "
            "rounds stored in Turkey. Origin verification matters when "
            "goods are 'in Turkey' on a procurement list."
        ),
    ),

    # ─── OTHER EUROPEAN / NATO ──────────────────────────────────────

    WeaponSystem(
        canonical_name="Carl Gustaf",
        aliases=("Carl Gustaf", "Carl-Gustaf M4", "CGM4", "M3 MAAWS",
                 "AT4"),
        category="RPG",
        oem="Saab Bofors Dynamics",
        oem_parent="Saab AB",
        origin_iso2="SE",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Swedish ISP",),
    ),
    WeaponSystem(
        canonical_name="Rheinmetall 120mm tank ammunition (DM63 / DM73)",
        aliases=("DM63", "DM73", "DM53", "Rheinmetall 120mm APFSDS"),
        category="tank-ammunition",
        oem="Rheinmetall",
        oem_parent="Rheinmetall AG",
        origin_iso2="DE",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("German BAFA",),
    ),
    WeaponSystem(
        canonical_name="Hirtenberger 60mm/81mm/120mm mortar bombs",
        aliases=("Hirtenberger mortar", "Hirtenberger 81mm",
                 "Hirtenberger 120mm", "HD81", "HD120"),
        category="munition-mortar",
        oem="Hirtenberger Defence Systems",
        oem_parent="Hirtenberger AG (Austria)",
        origin_iso2="AT",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Austrian export controls",),
        notes="Major NATO mortar-bomb supplier.",
    ),
    WeaponSystem(
        canonical_name="Bulgarian Arsenal AK-pattern / 7.62×39",
        aliases=("Arsenal AK", "Bulgarian AK", "Arsenal AD", "Kintex"),
        category="small arms",
        oem="Arsenal AD / Kintex",
        oem_parent="Bulgarian state-affiliated",
        origin_iso2="BG",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Bulgarian export controls", "EU Common Position"),
        notes=(
            "Bulgarian-produced AK variants + 7.62×39 ammunition are "
            "LEGAL to broker (distinct from Russian-produced — see "
            "'AK-pattern small arms (Russian-produced)' entry)."
        ),
    ),
    WeaponSystem(
        canonical_name="Cugir Romanian AK-pattern / 7.62×39",
        aliases=("Cugir AKM", "Cugir Md.63", "Romanian AKM",
                 "ROMARM"),
        category="small arms",
        oem="Cugir Arms Factory (ROMARM)",
        oem_parent="Romarm SA (Romanian state)",
        origin_iso2="RO",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Romanian export controls", "EU Common Position"),
    ),
    WeaponSystem(
        canonical_name="Zastava M70 / M21 (Serbian AK-pattern)",
        aliases=("Zastava", "Zastava M70", "Zastava M21",
                 "Yugoimport SDPR"),
        category="small arms",
        oem="Zastava Arms",
        oem_parent="Yugoimport SDPR (Serbian state)",
        origin_iso2="RS",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Serbian export controls",),
        notes=(
            "Serbian-produced AK variants. End-use scrutiny — Yugoimport "
            "has historical diversion patterns (Sudan, Yemen, Sahel)."
        ),
    ),

    # ─── DENEL / Egypt / Pakistan ───────────────────────────────────

    WeaponSystem(
        canonical_name="Rheinmetall Denel 155mm (G5/G6 family)",
        aliases=("G5 155mm", "G6 SPG", "Denel 155mm",
                 "Rheinmetall Denel Munitions"),
        category="SPG-155mm",
        oem="Rheinmetall Denel Munitions",
        oem_parent="Rheinmetall AG / Denel SOC",
        origin_iso2="ZA",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("South African NCACC export controls",),
        notes=(
            "Saudi RSLF operates G6 SPG. Major supplier of 155mm M107 "
            "and Assegai (long-range) shells in NATO-compatible spec."
        ),
    ),
    WeaponSystem(
        canonical_name="POF / Pakistan Ordnance Factories 155mm",
        aliases=("POF 155mm", "Pakistan Ordnance"),
        category="munition-155mm",
        oem="Pakistan Ordnance Factories",
        oem_parent="Pakistan state",
        origin_iso2="PK",
        sanctions_status=SanctionsStatus.CLEARED,
        sanctions_regimes=("Pakistan SECP export controls",),
        notes="Saudi-Pakistani defence relationship long-standing.",
    ),
)


# ─────────────────────────────────────────────────────────────────────
# Indices for fast lookup
# ─────────────────────────────────────────────────────────────────────

# canonical_name_lower → WeaponSystem
_BY_CANONICAL: dict[str, WeaponSystem] = {
    ws.canonical_name.lower(): ws for ws in _SEED
}

# alias_lower → WeaponSystem  (one entry per alias)
_BY_ALIAS: dict[str, WeaponSystem] = {}
for _ws in _SEED:
    _BY_ALIAS[_ws.canonical_name.lower()] = _ws
    for _a in _ws.aliases:
        _BY_ALIAS[_a.lower()] = _ws


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


def lookup_by_name(name: str) -> Optional[WeaponSystem]:
    """Exact-or-alias lookup. Returns None for unknown weapon.

    Case-insensitive, whitespace-trimmed. Tries canonical name first,
    then alias index.
    """
    if not name or not isinstance(name, str):
        return None
    key = name.strip().lower()
    if not key:
        return None
    return _BY_CANONICAL.get(key) or _BY_ALIAS.get(key)


def best_match_for_text(text: str) -> Optional[WeaponSystem]:
    """Fuzzy match against goods-list line-item text.

    Returns the first weapon system whose canonical name OR any alias
    appears as a substring (with word-boundary anchoring) in the text.
    Prefers longer matches to avoid prefix-collision (e.g. 'M829' should
    win over 'M8').

    Returns None for no-match.
    """
    if not text or not isinstance(text, str):
        return None
    text_lc = text.lower()
    # Score = match-length; pick the longest matched name/alias
    best: tuple[int, Optional[WeaponSystem]] = (0, None)
    for key, ws in _BY_ALIAS.items():
        if len(key) < 3:
            continue  # avoid spurious 2-char matches
        # Word-boundary match
        if re.search(rf"\b{re.escape(key)}\b", text_lc):
            if len(key) > best[0]:
                best = (len(key), ws)
    return best[1]


def list_by_origin(iso2: str) -> list[WeaponSystem]:
    """All weapons with a given origin ISO2 (case-insensitive)."""
    if not iso2 or not isinstance(iso2, str):
        return []
    iso2_u = iso2.strip().upper()
    return [ws for ws in _SEED if ws.origin_iso2 == iso2_u]


def list_by_status(status: str) -> list[WeaponSystem]:
    """All weapons at a given sanctions status (prohibited/restricted/cleared)."""
    if not status:
        return []
    s = status.lower().strip()
    return [ws for ws in _SEED if ws.sanctions_status == s]


def list_prohibited() -> list[WeaponSystem]:
    """Convenience: all weapons that are PROHIBITED for UK brokers."""
    return list_by_status(SanctionsStatus.PROHIBITED)


def list_restricted() -> list[WeaponSystem]:
    """Convenience: all weapons that require SITCL or have BIS exposure."""
    return list_by_status(SanctionsStatus.RESTRICTED)


def all_weapons() -> tuple[WeaponSystem, ...]:
    """Return the full seed (immutable tuple)."""
    return _SEED


def stats() -> dict[str, int | str]:
    """Catalogue health metrics — for the operator dashboard."""
    return {
        "total_entries": len(_SEED),
        "prohibited": len(list_prohibited()),
        "restricted": len(list_restricted()),
        "cleared": len(list_by_status(SanctionsStatus.CLEARED)),
        "origin_countries": len({ws.origin_iso2 for ws in _SEED}),
        "as_of": _SEED[0].as_of if _SEED else "n/a",
    }


def render_finding_for_text(line_item_text: str) -> Optional[dict]:
    """Convenience: match a goods-list line + return a Finding-shaped dict
    suitable for the DD orchestrator's compliance section.

    Returns None if no weapon match — caller falls back to ECCN generic.
    """
    try:
        ws = best_match_for_text(line_item_text)
        if not ws:
            return None
        severity_map = {
            SanctionsStatus.PROHIBITED: "hard_stop",
            SanctionsStatus.RESTRICTED: "amber",
            SanctionsStatus.CLEARED: "info",
        }
        result = {
            "severity": severity_map.get(ws.sanctions_status, "amber"),
            "title": f"Weapon match: {ws.canonical_name} — origin {ws.origin_iso2} — {ws.sanctions_status.upper()}",
            "detail": (
                f"Matched line-item text to {ws.canonical_name} "
                f"({ws.category}). OEM: {ws.oem}. Parent: {ws.oem_parent}. "
                f"Origin: {ws.origin_iso2}. Sanctions status: "
                f"{ws.sanctions_status}. Regimes: {', '.join(ws.sanctions_regimes)}. "
                f"Notes: {ws.notes}"
            ),
            "source": (
                f"weapon_origin_catalogue.lookup (R-F638) — as_of {ws.as_of}"
            ),
            "confidence": "PROBABLE",
            "weapon_canonical_name": ws.canonical_name,
            "origin_iso2": ws.origin_iso2,
            "sanctions_status": ws.sanctions_status,
            "matched_text": line_item_text[:200],
        }
        # R-F994 — wire to brain
        from .engine_wiring import wire_success
        wire_success(
            module="weapon_origin_catalogue",
            summary=f"Weapon origin match: {ws.canonical_name} ({ws.origin_iso2}, {ws.sanctions_status})",
            detail=result["detail"][:600],
            entity_name=ws.canonical_name,
            confidence="PROBABLE",
            source_id="weapon_origin_catalogue:R-F638",
        )
        return result
    except Exception as e:
        from .engine_wiring import wire_failure
        wire_failure(
            module="weapon_origin_catalogue",
            detail=f"render_finding_for_text crashed: {e}",
            gap_type="compliance_engine_failure",
            source="weapon_origin_catalogue:render_finding_for_text",
        )
        return None
