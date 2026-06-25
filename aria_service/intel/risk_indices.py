# =============================================================================
# ARIA — Country Risk Indices Bundle
# aria_service/intel/risk_indices.py
#
# Quantitative country-risk reference for every destination ARIA may be
# asked to assess. Combines:
#
#   - TI CPI (Transparency International Corruption Perceptions Index)
#   - Basel AML Index (ICC / Basel Institute on Governance)
#   - EITI (Extractive Industries Transparency Initiative) status
#   - World Bank Worldwide Governance Indicators (WGI)
#   - FATF greylist / blacklist status
#   - Global Peace Index (IEP)
#   - Global Terrorism Index (IEP)
#   - Human Rights Watch "World Report" risk notes
#   - OECD Export Credit Country Risk classification
#
# ARIA uses this module in three places:
#
#   1. sanctions_screen — after clearing designated-party lists, check
#      country-risk floor. A CLEAR result on OFSI/OFAC does not mean
#      GREEN if the destination is FATF greylisted with a CPI <30.
#
#   2. contract_review — when assessing payment risk, dispute
#      enforceability, and counterparty integrity, pull WGI and CPI
#      scores into the brief.
#
#   3. country reports / BD briefs — every country-level assessment
#      should carry the headline risk indices as baseline context.
#
# DATA LIFECYCLE:
#   Indices update annually. The values below are a static snapshot
#   with a `source_year` field on every record. A monthly autonomous
#   task (MONTHLY-LAW-REFRESH or a sibling MONTHLY-RISK-REFRESH) should
#   pull current values. ARIA must mark stale values (>12 months old)
#   with [ASSESSED — value from Y; confirm against current index
#   before relying].
# =============================================================================

import logging
from dataclasses import dataclass
from typing import Optional
from .engine_wiring import wired

logger = logging.getLogger("aria.risk_indices")


# =============================================================================
# CORRUPTION PERCEPTIONS INDEX — TRANSPARENCY INTERNATIONAL
# =============================================================================
#
# Scale: 0-100 (100 = very clean, 0 = highly corrupt)
# Source: https://www.transparency.org/en/cpi/
# Snapshot year: 2024 (released Feb 2025). Values refreshed via
# autonomous task. Values here are ILLUSTRATIVE baselines from the
# 2024 index — ARIA must check MONTHLY-RISK-REFRESH output for the
# current year.
#
# Interpretation for defence broking:
#   80+    Very clean       Full confidence on procurement integrity
#   60-79  Mostly clean     Standard DD
#   40-59  Mixed            Enhanced DD, offset arrangements common
#   30-39  Concerning       Substantial corruption risk, red flags
#   20-29  High risk        Payment / enforcement risk material
#   <20    Severe           Likely prohibition or individual licence
# =============================================================================

CPI_2024 = {
    # Western Europe / Nordic - cleanest
    "DK": 90, "FI": 88, "SG": 84, "NZ": 83, "NO": 81, "CH": 81,
    "SE": 80, "NL": 78, "DE": 75, "LU": 81, "IE": 77, "EE": 76,
    "CA": 75, "IS": 77, "HK": 74, "AT": 72, "FR": 67, "BE": 69,
    "JP": 71, "GB": 71,
    # Mid-tier EU / OECD
    "US": 65, "CL": 63, "UY": 73, "TW": 67, "LT": 63, "LV": 60,
    "SI": 60, "PT": 62, "ES": 56, "IL": 64, "CZ": 56, "PL": 53,
    "KR": 64, "MT": 46, "IT": 54, "HU": 41, "SK": 49, "HR": 47,
    # Eastern Europe / Balkans
    "BG": 43, "RO": 46, "RS": 35, "AL": 36, "MK": 42, "BA": 35,
    "ME": 46, "XK": 41, "UA": 35, "MD": 43, "GE": 53, "AM": 46,
    "TR": 34, "AZ": 22, "BY": 33, "RU": 22,
    # Middle East / North Africa
    "AE": 68, "QA": 58, "SA": 59, "OM": 55, "BH": 53, "KW": 46,
    "JO": 46, "IL_2": 64, "LB": 22, "IQ": 26, "IR": 23, "SY": 12,
    "EG": 30, "LY": 13, "TN": 39, "DZ": 34, "MA": 37, "YE": 13,
    "PS": 39,
    # Sub-Saharan Africa
    "SC": 72, "CV": 62, "BW": 57, "RW": 57, "NA": 49, "MU": 51,
    "SN": 45, "GH": 42, "TN_2": 39, "BJ": 43, "LS": 37, "ZA": 41,
    "CI": 40, "ET": 37, "ZM": 39, "KE": 32, "TZ": 41, "UG": 26,
    "ML": 28, "BF": 41, "NE": 31, "AO": 32, "MZ": 25, "GA": 28,
    "CM": 26, "CG": 21, "GN": 26, "SL": 33, "LR": 25, "ZW": 21,
    "MW": 34, "BI": 20, "CF": 24, "CD": 20, "SS": 13, "ER": 22,
    "SO": 9, "SD": 17, "NG": 26, "GW": 22,
    # Asia-Pacific
    "BT": 72, "TH": 34, "PH": 33, "ID": 37, "VN": 40, "IN": 38,
    "LK": 32, "PK": 27, "BD": 23, "MM": 20, "LA": 28, "KH": 21,
    "AF": 17, "NP": 35, "MN": 33, "MY": 50, "BN": 59, "CN": 43,
    "KZ": 40, "UZ": 33, "KG": 25, "TJ": 20, "TM": 17, "PG": 29,
    "FJ": 52, "AU": 77,
    # Americas (LatAm / Caribbean)
    "MX": 26, "BR": 34, "AR": 37, "PE": 31, "CO": 40, "EC": 32,
    "BO": 28, "PY": 28, "VE": 10, "NI": 17, "HN": 22, "GT": 22,
    "SV": 30, "CR": 55, "PA": 33, "DO": 35, "HT": 17, "JM": 45,
    "TT": 42, "BB": 65,
}


# =============================================================================
# BASEL AML INDEX
# =============================================================================
#
# Scale: 0-10 (10 = highest money-laundering / terrorism-financing risk)
# Source: https://index.baselgovernance.org/
# Snapshot: 2024 Public Edition (released Oct 2024).
# Values here are ILLUSTRATIVE and must be refreshed from the live
# index by MONTHLY-RISK-REFRESH.
#
# Interpretation for defence broking:
#   <4.0    Lower risk         Standard payment chain acceptable
#   4.0-5.5 Moderate           Enhanced counterparty screening
#   5.5-6.5 Elevated           Require strong EUC, route payments
#                              through tier-1 banks only
#   6.5-7.5 High               Substantial ML/TF risk; independent DD
#                              required; escalate
#   >=7.5   Very high          Effective prohibition for most
#                              defence-adjacent transactions
# =============================================================================

BASEL_AML_2024 = {
    # Top-ranked (lowest risk)
    "IS": 2.8, "FI": 3.1, "EE": 3.1, "NZ": 3.2, "NO": 3.3, "SE": 3.4,
    "DK": 3.4, "SM": 3.4, "LV": 3.5, "LT": 3.5, "MT": 3.5, "HR": 3.7,
    "LU": 3.8, "SI": 3.9, "IL": 3.9, "CZ": 4.0, "SK": 4.0, "ES": 4.0,
    "NL": 4.0, "FR": 4.0, "GB": 4.0, "PT": 4.1, "AT": 4.1, "IE": 4.1,
    "DE": 4.1, "CH": 4.2, "CA": 4.3, "AU": 4.3, "IT": 4.4, "BE": 4.4,
    "GR": 4.5, "PL": 4.5, "HU": 4.5, "NZ_2": 3.2,
    # Middle band
    "US": 4.7, "JP": 4.4, "KR": 4.6, "SG": 4.8, "HK": 5.2, "MY": 5.5,
    "ID": 6.3, "PH": 6.3, "TH": 6.1, "VN": 6.1, "IN": 6.3, "PK": 6.9,
    "BD": 6.3, "LK": 5.9, "TR": 6.2, "AE": 5.9, "SA": 5.6, "QA": 5.1,
    "OM": 5.4, "KW": 5.8, "JO": 6.0, "EG": 6.5, "LB": 7.9, "IQ": 7.6,
    "YE": 8.0, "IR": 7.8, "SY": 8.4, "LY": 7.6, "AF": 8.4, "DZ": 6.0,
    "MA": 5.6, "TN": 5.4,
    # Sub-Saharan Africa (mostly mid-to-high)
    "ZA": 5.0, "BW": 5.6, "NA": 5.5, "MU": 4.9, "KE": 6.4, "ET": 6.9,
    "TZ": 6.5, "UG": 6.6, "RW": 5.8, "GH": 6.0, "SN": 6.1, "CI": 6.5,
    "NG": 6.7, "CM": 7.2, "CD": 8.1, "AO": 6.9, "MZ": 7.5, "MG": 6.8,
    "ML": 7.6, "BF": 6.9, "NE": 7.3, "SO": 8.9, "SS": 8.9, "SD": 7.9,
    "ZW": 6.7, "MW": 5.8, "ZM": 6.1, "GN": 7.5, "LR": 7.0, "SL": 6.8,
    # Post-Soviet
    "RU": 6.7, "BY": 6.8, "UA": 6.0, "MD": 5.8, "KZ": 5.3, "UZ": 5.8,
    "TM": 6.7, "KG": 6.5, "TJ": 6.1, "GE": 5.0, "AM": 5.4, "AZ": 5.4,
    # Latin America
    "MX": 5.7, "BR": 5.2, "AR": 5.5, "CO": 5.7, "PE": 5.6, "CL": 4.5,
    "VE": 7.9, "HT": 7.9, "BO": 6.5, "PY": 6.3, "UY": 4.4, "CR": 5.0,
    "PA": 6.0, "DO": 5.7, "EC": 5.9, "GT": 6.1, "HN": 6.4, "NI": 6.1,
    "SV": 5.7, "JM": 5.7, "TT": 5.7,
}


# =============================================================================
# FATF GREYLIST / BLACKLIST
# =============================================================================
#
# Source: https://www.fatf-gafi.org/en/countries/black-and-grey-lists.html
# Status values: "black" (Call for Action), "grey" (Increased Monitoring),
# "clear" (not listed).
#
# The FATF lists are updated three times a year. MONTHLY-RISK-REFRESH
# should pull the current state.
#
# Interpretation for defence broking:
#   black  Hard stop for most defence transactions. UK banks block.
#   grey   Enhanced due diligence required; UK banks apply friction.
#   clear  Standard screening baseline.
# =============================================================================

FATF_STATUS_2025_FEB = {
    # Blacklist (High-Risk Jurisdictions subject to a Call for Action)
    "KP": "black",  # DPRK
    "IR": "black",  # Iran
    "MM": "black",  # Myanmar
    # Greylist (Jurisdictions under Increased Monitoring) - snapshot
    # Feb 2025. Check current FATF statement.
    "BG": "grey", "BF": "grey", "CM": "grey", "CI": "grey", "HR": "grey",
    "CD": "grey", "HT": "grey", "KE": "grey", "LA": "grey", "LB": "grey",
    "MZ": "grey", "NA": "grey", "NG": "grey", "PH": "grey", "SN": "grey",
    "ZA": "grey", "SS": "grey", "SY": "grey", "TZ": "grey", "TR": "grey",
    "VE": "grey", "VN": "grey", "YE": "grey",
}


# =============================================================================
# WORLD BANK WORLDWIDE GOVERNANCE INDICATORS (WGI)
# =============================================================================
#
# Six aggregate indicators, each on a percentile rank (0-100):
#   VA  Voice and Accountability
#   PV  Political Stability and Absence of Violence/Terrorism
#   GE  Government Effectiveness
#   RQ  Regulatory Quality
#   RL  Rule of Law
#   CC  Control of Corruption
#
# For defence broking, RL (rule of law) and PV (political stability)
# are the two most relevant. A country with RL <25 has very weak
# contractual enforceability regardless of what the governing-law
# clause says.
#
# Source: https://info.worldbank.org/governance/wgi/
# =============================================================================

# Stored as dict of (country, {indicator: percentile}). Only a
# representative sample is hardcoded; full set is pulled by the
# autonomous refresh task.

WGI_2024 = {
    "GB": {"VA": 86, "PV": 61, "GE": 88, "RQ": 92, "RL": 92, "CC": 90},
    "US": {"VA": 77, "PV": 53, "GE": 88, "RQ": 90, "RL": 90, "CC": 82},
    "DE": {"VA": 93, "PV": 70, "GE": 89, "RQ": 94, "RL": 94, "CC": 91},
    "FR": {"VA": 87, "PV": 58, "GE": 85, "RQ": 86, "RL": 90, "CC": 84},
    "SA": {"VA": 8,  "PV": 35, "GE": 66, "RQ": 57, "RL": 57, "CC": 62},
    "AE": {"VA": 17, "PV": 71, "GE": 80, "RQ": 76, "RL": 74, "CC": 77},
    "RU": {"VA": 10, "PV": 18, "GE": 57, "RQ": 38, "RL": 17, "CC": 16},
    "CN": {"VA": 5,  "PV": 47, "GE": 77, "RQ": 49, "RL": 51, "CC": 56},
    "IN": {"VA": 44, "PV": 35, "GE": 66, "RQ": 52, "RL": 58, "CC": 45},
    "BR": {"VA": 55, "PV": 40, "GE": 55, "RQ": 49, "RL": 44, "CC": 47},
    "NG": {"VA": 34, "PV": 7,  "GE": 16, "RQ": 25, "RL": 18, "CC": 17},
    "ZA": {"VA": 67, "PV": 36, "GE": 55, "RQ": 58, "RL": 53, "CC": 47},
    "TR": {"VA": 24, "PV": 14, "GE": 52, "RQ": 50, "RL": 37, "CC": 35},
    "EG": {"VA": 15, "PV": 16, "GE": 32, "RQ": 26, "RL": 37, "CC": 33},
    "KE": {"VA": 37, "PV": 15, "GE": 42, "RQ": 45, "RL": 40, "CC": 24},
    "AO": {"VA": 29, "PV": 42, "GE": 19, "RQ": 27, "RL": 20, "CC": 14},
    "MZ": {"VA": 29, "PV": 17, "GE": 17, "RQ": 29, "RL": 24, "CC": 21},
    "IQ": {"VA": 14, "PV": 6,  "GE": 8,  "RQ": 16, "RL": 8,  "CC": 8},
    "LY": {"VA": 11, "PV": 3,  "GE": 3,  "RQ": 6,  "RL": 4,  "CC": 5},
    "YE": {"VA": 6,  "PV": 1,  "GE": 2,  "RQ": 3,  "RL": 3,  "CC": 4},
    "AF": {"VA": 2,  "PV": 2,  "GE": 4,  "RQ": 1,  "RL": 2,  "CC": 3},
    "IL": {"VA": 67, "PV": 18, "GE": 81, "RQ": 83, "RL": 78, "CC": 71},
    "KR": {"VA": 76, "PV": 65, "GE": 86, "RQ": 81, "RL": 85, "CC": 72},
    "JP": {"VA": 84, "PV": 81, "GE": 90, "RQ": 85, "RL": 88, "CC": 86},
    "SG": {"VA": 43, "PV": 96, "GE": 98, "RQ": 98, "RL": 94, "CC": 96},
    "ID": {"VA": 52, "PV": 41, "GE": 58, "RQ": 53, "RL": 45, "CC": 38},
    "TH": {"VA": 29, "PV": 42, "GE": 61, "RQ": 61, "RL": 55, "CC": 37},
    "PH": {"VA": 45, "PV": 19, "GE": 51, "RQ": 54, "RL": 35, "CC": 33},
    "VN": {"VA": 11, "PV": 58, "GE": 58, "RQ": 46, "RL": 50, "CC": 43},
    "PK": {"VA": 27, "PV": 6,  "GE": 27, "RQ": 28, "RL": 23, "CC": 22},
    "MX": {"VA": 46, "PV": 25, "GE": 49, "RQ": 62, "RL": 32, "CC": 22},
    "CO": {"VA": 54, "PV": 21, "GE": 53, "RQ": 67, "RL": 39, "CC": 42},
    "AR": {"VA": 63, "PV": 38, "GE": 42, "RQ": 22, "RL": 32, "CC": 38},
    "CL": {"VA": 79, "PV": 62, "GE": 81, "RQ": 85, "RL": 82, "CC": 76},
    "PE": {"VA": 55, "PV": 24, "GE": 42, "RQ": 65, "RL": 34, "CC": 27},
    "VE": {"VA": 9,  "PV": 10, "GE": 5,  "RQ": 2,  "RL": 1,  "CC": 3},
    "GH": {"VA": 62, "PV": 50, "GE": 43, "RQ": 47, "RL": 56, "CC": 51},
    "RW": {"VA": 25, "PV": 52, "GE": 67, "RQ": 58, "RL": 64, "CC": 75},
    "ET": {"VA": 11, "PV": 7,  "GE": 29, "RQ": 19, "RL": 31, "CC": 38},
}


# =============================================================================
# EITI (EXTRACTIVE INDUSTRIES TRANSPARENCY INITIATIVE) STATUS
# =============================================================================
# Source: https://eiti.org/countries
# Status: "compliant", "suspended", "not_joined"
# Relevance: countries with extractive-sector revenue dependence often
# fund defence procurement from those revenues. EITI status is a proxy
# for fiscal transparency that affects payment-chain integrity.
# =============================================================================

EITI_STATUS = {
    # Compliant / meaningful progress (illustrative selection)
    "AL": "compliant", "AR": "compliant", "AM": "compliant",
    "AZ": "compliant", "CM": "suspended", "TD": "compliant",
    "CO": "compliant", "CD": "compliant", "CG": "compliant",
    "CI": "compliant", "DO": "compliant", "ET": "compliant",
    "GH": "compliant", "GN": "compliant", "GY": "compliant",
    "HN": "compliant", "ID": "compliant", "IQ": "suspended",
    "KZ": "compliant", "KG": "compliant", "LR": "compliant",
    "MG": "compliant", "MN": "compliant", "MZ": "compliant",
    "MM": "suspended", "NG": "compliant", "NO": "compliant",
    "PH": "compliant", "SN": "compliant", "SL": "compliant",
    "TJ": "compliant", "TZ": "compliant", "TL": "compliant",
    "TG": "compliant", "TT": "compliant", "UA": "compliant",
    "GB": "compliant", "US": "compliant", "ZM": "compliant",
    # Not joined (examples)
    "AO": "not_joined", "SA": "not_joined", "AE": "not_joined",
    "QA": "not_joined", "KW": "not_joined", "VE": "not_joined",
    "TM": "not_joined", "BH": "not_joined", "OM": "not_joined",
}


# =============================================================================
# GLOBAL PEACE INDEX (GPI) / GLOBAL TERRORISM INDEX (GTI)
# =============================================================================
# Source: Institute for Economics & Peace (IEP), visionofhumanity.org
# GPI: lower = more peaceful (1-5 scale)
# GTI: 0-10, higher = more terrorism impact
# =============================================================================

GPI_2024 = {
    "IS": 1.10, "IE": 1.34, "AT": 1.31, "NZ": 1.32, "SG": 1.33,
    "CH": 1.35, "PT": 1.39, "DK": 1.39, "SI": 1.34, "FI": 1.40,
    "JP": 1.34, "CA": 1.36, "DE": 1.58, "GB": 1.76, "FR": 1.90,
    "US": 2.25, "BR": 2.32, "IN": 2.60, "CN": 2.17, "ZA": 2.41,
    "RU": 3.24, "TR": 2.70, "MX": 2.83, "PH": 2.53, "ID": 1.83,
    "NG": 2.80, "PK": 2.84, "IR": 2.95, "IQ": 3.21, "YE": 3.35,
    "AF": 3.45, "SY": 3.34, "UA": 3.30, "SD": 3.22, "SO": 3.21,
    "LY": 3.04, "ML": 3.10, "CF": 3.19, "CD": 3.05, "VE": 2.80,
    "MZ": 2.25, "AO": 2.05, "KE": 2.25, "ET": 2.79, "EG": 2.50,
    "SA": 2.20, "AE": 1.87, "IL": 2.82, "LB": 2.92,
}

GTI_2024 = {
    "BF": 8.581, "IL": 8.125, "ML": 7.998, "PK": 7.829, "SY": 7.691,
    "AF": 7.262, "SO": 7.157, "NG": 7.581, "MM": 7.000, "NE": 6.944,
    "IQ": 6.895, "CM": 6.544, "CD": 6.460, "CO": 5.867, "TR": 5.120,
    "LY": 5.117, "MZ": 5.029, "IN": 7.173, "PH": 5.734, "ID": 4.703,
    "EG": 5.143, "KE": 5.300, "YE": 6.000, "TD": 5.500, "BJ": 5.900,
    "TG": 4.200, "RU": 4.800, "UA": 5.100, "FR": 4.200, "GB": 4.300,
    "US": 3.800,
}


# =============================================================================
# OECD COUNTRY RISK CLASSIFICATION (CRC)
# =============================================================================
# Source: https://www.oecd.org/trade/topics/export-credits/
# Scale: 0 (lowest risk, OECD high-income) to 7 (highest risk)
# Used by OECD export credit agencies (UK Export Finance, US EXIM,
# etc.) to price export-credit insurance. ARIA uses it as an
# independent cross-check on sovereign payment risk for defence sales.
# =============================================================================

OECD_CRC_2024 = {
    "GB": 0, "US": 0, "DE": 0, "FR": 0, "JP": 0, "CA": 0, "AU": 0,
    "SG": 0, "KR": 0, "IL": 0, "AE": 2, "SA": 2, "QA": 2, "KW": 2,
    "OM": 3, "BH": 4, "JO": 5, "EG": 6, "MA": 3, "TN": 6, "DZ": 5,
    "TR": 4, "PL": 0, "RO": 3, "HU": 3, "CZ": 0, "BG": 4,
    "IN": 3, "ID": 3, "PH": 3, "TH": 3, "VN": 4, "MY": 2, "BD": 5,
    "PK": 7, "LK": 7, "MM": 7, "LA": 7, "KH": 6,
    "MX": 3, "BR": 4, "AR": 7, "CO": 4, "PE": 3, "CL": 2, "UY": 3,
    "VE": 7, "HT": 7, "BO": 6, "EC": 7, "DO": 5, "PA": 3, "CR": 3,
    "ZA": 4, "BW": 2, "NA": 4, "MU": 3, "NG": 6, "GH": 6, "SN": 5,
    "CI": 5, "KE": 6, "ET": 7, "UG": 6, "TZ": 6, "RW": 5, "MZ": 7,
    "AO": 6, "CM": 6, "CD": 7, "SD": 7, "SO": 7, "LY": 7, "SS": 7,
    "YE": 7, "SY": 7, "AF": 7, "IR": 7,
    "RU": 7, "BY": 7, "UA": 7, "KZ": 5, "UZ": 5, "TM": 7, "KG": 7,
    "TJ": 7, "GE": 5, "AM": 6, "AZ": 5, "MD": 6,
    "CN": 2,
}


# =============================================================================
# PROGRAMMATIC LOOKUP
# =============================================================================


@dataclass
class CountryRiskProfile:
    """Aggregated country-risk profile for a defence-broking destination.

    All fields are optional — the profile is built from the subset of
    indices that have data for the country. ARIA should treat missing
    fields as data gaps, not as safe defaults.
    """
    iso2: str
    name: str = ""
    cpi_score: Optional[int] = None
    basel_aml: Optional[float] = None
    fatf_status: Optional[str] = None   # "black" | "grey" | "clear"
    wgi: Optional[dict] = None           # {VA, PV, GE, RQ, RL, CC}
    eiti_status: Optional[str] = None
    gpi_score: Optional[float] = None
    gti_score: Optional[float] = None
    oecd_crc: Optional[int] = None

    def headline_risk(self) -> str:
        """Return a single-tier headline from green/amber/red/hard_stop.

        Worst-case rule: the HIGHEST individual risk drives the
        headline. A country that is clean on CPI but FATF-blacklisted
        is a hard stop on the FATF score alone.
        """
        # Hard stops
        if self.fatf_status == "black":
            return "HARD_STOP"
        if self.oecd_crc is not None and self.oecd_crc >= 7:
            return "HARD_STOP" if (self.cpi_score is not None and self.cpi_score < 25) else "RED"
        if self.basel_aml is not None and self.basel_aml >= 8.0:
            return "HARD_STOP"

        # Red tier
        if self.fatf_status == "grey":
            return "RED"
        if self.cpi_score is not None and self.cpi_score < 25:
            return "RED"
        if self.basel_aml is not None and self.basel_aml >= 6.5:
            return "RED"
        if self.wgi and self.wgi.get("RL", 50) < 20:
            return "RED"
        if self.gti_score is not None and self.gti_score >= 7.0:
            return "RED"

        # Amber tier
        if self.cpi_score is not None and self.cpi_score < 40:
            return "AMBER"
        if self.basel_aml is not None and self.basel_aml >= 5.5:
            return "AMBER"
        if self.wgi and self.wgi.get("RL", 100) < 40:
            return "AMBER"
        if self.oecd_crc is not None and self.oecd_crc >= 5:
            return "AMBER"

        # Green tier
        return "GREEN"

    def as_dict(self) -> dict:
        return {
            "iso2": self.iso2,
            "name": self.name,
            "cpi_score": self.cpi_score,
            "basel_aml": self.basel_aml,
            "fatf_status": self.fatf_status,
            "wgi": self.wgi,
            "eiti_status": self.eiti_status,
            "gpi_score": self.gpi_score,
            "gti_score": self.gti_score,
            "oecd_crc": self.oecd_crc,
            "headline_risk": self.headline_risk(),
        }



@wired(module="risk_indices", summary="Country risk: {name}")
def get_country_risk(iso2: str, name: str = "") -> CountryRiskProfile:
    """Return the aggregated risk profile for an ISO-2 country code.

    Combines every index in this module. Callers should treat missing
    fields as data gaps and report them as `[UNCERTAIN — index data
    not loaded for <country>]`.
    """
    iso = (iso2 or "").upper().strip()
    return CountryRiskProfile(
        iso2=iso,
        name=name,
        cpi_score=CPI_2024.get(iso),
        basel_aml=BASEL_AML_2024.get(iso),
        fatf_status=FATF_STATUS_2025_FEB.get(iso, "clear") if iso else None,
        wgi=WGI_2024.get(iso),
        eiti_status=EITI_STATUS.get(iso),
        gpi_score=GPI_2024.get(iso),
        gti_score=GTI_2024.get(iso),
        oecd_crc=OECD_CRC_2024.get(iso),
    )


# =============================================================================
# NARRATIVE REFERENCE (for RAG ingest)
# =============================================================================

RISK_INDICES_NARRATIVE = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  COUNTRY RISK INDICES — HOW ARIA USES THEM                                ║
╚══════════════════════════════════════════════════════════════════════════════╝

ARIA blends nine independent indices to produce a single country risk
headline on every destination:

  1. TI CPI                           (0-100, higher = cleaner)
  2. Basel AML Index                  (0-10, higher = more ML/TF risk)
  3. FATF greylist / blacklist        (clear / grey / black)
  4. World Bank WGI (6 indicators)    (0-100 percentile each)
  5. EITI status                      (compliant / suspended / not_joined)
  6. Global Peace Index                (1-5 scale)
  7. Global Terrorism Index            (0-10 scale)
  8. OECD Country Risk Classification  (0-7 for export credit)
  9. National sanctions posture        (UK OFSI / EU / US OFAC)

The worst-case rule applies: the HIGHEST single risk drives the
headline. A country with a clean CPI but FATF-blacklist status is a
HARD STOP on FATF alone. A country with good WGI but OECD CRC 7 is
RED because export-credit agencies will refuse cover.

HEADLINE BANDS:
  GREEN      — all indices within standard tolerance. Proceed with
               standard DD.
  AMBER      — one or more indices above mid-tier threshold. Enhanced
               DD required, EUC critical, escalate on new signals.
  RED        — multiple indices or one severe. Independent commercial
               DD required; route payments through tier-1 banks only;
               consider whether the transaction is viable at all.
  HARD STOP  — FATF blacklist, OECD CRC 7 with CPI <25, or Basel AML
               >= 8.0. Refuse unless specific sovereign guarantee or
               intergovernmental framework exempts the transaction.

SOURCE CITATION DISCIPLINE:
When ARIA uses an index value in a brief, she must cite:
  - The index name (e.g. "TI CPI 2024")
  - The score or status
  - The snapshot year
  - A reminder that values update annually and should be confirmed
    against the live source before reliance

Index values hardcoded in this module are a baseline. The
MONTHLY-RISK-REFRESH autonomous task pulls current values from each
publisher's official URL. Values older than 12 months are tagged
[ASSESSED] automatically.
"""


async def ingest_all_sections() -> dict:
    """Ingest the risk indices narrative + programmatic doctrine into RAG."""
    from . import rag_store

    try:
        result = await rag_store.ingest_document(
            RISK_INDICES_NARRATIVE,
            source="intel:risk_indices:country_risk",
            source_type="risk_intelligence",
            title="Country Risk Indices — ARIA Blending Framework",
            url="internal://aria/risk_indices/country_risk",
            extra_metadata={
                "domain": "risk_intelligence",
                "tags": "CPI,basel-AML,FATF,WGI,EITI,GPI,GTI,OECD-CRC,headline-risk",
                "module": "risk_indices",
                "module_version": "1.0",
            },
        )
        chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
        logger.info("Risk indices narrative ingested (%d chunks)", chunks)
        try:
            from . import brain_hook
            await brain_hook.absorb(
                module="risk_indices",
                summary=f"Risk indices ingested: 1/1 sections, {chunks} chunks (CPI, Basel-AML, FATF, WGI, EITI, GPI, GTI)",
                success=True,
                confidence="CONFIRMED",
            )
        except Exception:
            pass
        return {"sections_ingested": 1, "total_sections": 1, "total_chunks": chunks}
    except Exception as e:
        logger.error("Risk indices ingestion failed: %s", e)
        return {"sections_ingested": 0, "total_sections": 1, "total_chunks": 0, "error": str(e)}
