"""regulated_commodity_pack — oil, LNG, crude (and adjacent regulated-
commodity) knowledge extension for ARIA.

Why this exists
═══════════════
ARIA's positioning is global defence broking + compliance. Its sanctions /
UBO / counterparty-deception / DD-orchestrator infrastructure is domain-
agnostic — it works on any commercial entity. But its *knowledge depth*
is defence-shaped: 188 defence sources, NATO STANAGs, ECCN classification,
ITAR/EAR specifics, calibre/platform/OEM mappings.

When the operator was asked to run a DD on `lngtradinginternationalpanamasa.com`
on 2026-05-10, ARIA's pipeline ran cleanly and returned AMBER-LIGHT — but
the *reasons* were generic (registry incomplete, directors unknown), not
commodity-specific (no vessel history, no Russia-origin check, no LNG-
broker fraud-pattern match). A defence buyer would have got STANAG-aware
analysis; an LNG buyer got compliance-aware analysis without LNG specifics.

This module closes the depth gap — narrowly. It is ADJACENT to defence,
NOT a positioning pivot. ARIA's home page, BD pitch, and brand stay
defence-broking; this pack widens the buyer pool to "regulated-commodity
brokers who need defence-grade compliance rigour" without diluting the
specialist signal.

What it provides
════════════════
1. **Sanctions knowledge** — Russia G7+ oil price cap, Iran SDGT shipping
   designations, Venezuela GL framework, Maritime sanctions indicators
2. **Counterparty fraud patterns** — oil/LNG-broker-specific red flags
   (parallel structure to Clause 16's defence patterns)
3. **Commodity contract structures** — Incoterms 2020, FOB/CIF/DAP,
   demurrage, NOR/SOF, B/L verification, draft survey, LOI vs LC,
   long-term offtake, FSRU
4. **Energy regulators** — FERC, OFGEM, BAFA energy, EU REMIT, ANP (BR),
   ARERA (IT)
5. **Domain-name red-flag heuristics** — compound names + tax-haven
   domains that are the textbook LNG-broker fraud pattern
6. **Capability-extension entry points** — `is_commodity_dd_target()`,
   `enrich_dd_report()`, `commodity_compliance_bullets()`

How it integrates with the existing DD pipeline
══════════════════════════════════════════════
By default, this module is OPT-IN. The dd_orchestrator does NOT
automatically invoke commodity enrichment — the operator (or a future
intent-classifier patch) decides when an entity is in scope. Two
integration points the operator may opt into:

  - In dd_orchestrator._run_compliance: call enrich_dd_report() when
    is_commodity_dd_target() returns True. Adds commodity_compliance
    section to the report.

  - In aria_chat (chat path): when the user message mentions
    oil/LNG/crude/commodity context, prepend commodity_compliance_bullets()
    to the system prompt so ARIA's response covers the pack.

Neither is wired automatically — that's the operator's call. The
ecosystem audit (R-F149) discipline of "implemented but not yet
integrated" applies until explicit wiring.

Authoring discipline
════════════════════
Sanctions citations point to the actual regulation (OFAC Sanctions
Programs page, EU Council Regulation number, UK OFSI page). Where a
reading is operator-confirmable, this module flags it; where the regime
is too complex for a code-grounded summary (e.g. precise Russia price-
cap attestation forms), the module returns a "consult primary source"
pointer rather than a fabricated answer. Clause 14 (no fabricated
verifiable facts) discipline applies — when in doubt, refuse to assert.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import re
from typing import Any

logger = logging.getLogger("aria.regulated_commodity_pack")


# ══════════════════════════════════════════════════════════════════════
# 1. SANCTIONS KNOWLEDGE — oil/crude/LNG specific
# ══════════════════════════════════════════════════════════════════════

RUSSIA_OIL_PRICE_CAP = {
    "regime": "G7 + EU + Australia coalition oil price cap",
    "in_force_since": "2022-12-05 (crude) / 2023-02-05 (refined products)",
    "price_thresholds_usd_per_barrel": {
        "crude_oil": 60.0,
        "refined_premium": 100.0,    # diesel, gasoline (premium product class)
        "refined_discount": 45.0,    # fuel oil, naphtha (discount product class)
    },
    "scope": (
        "Applies to seaborne Russian-origin crude oil and petroleum products. "
        "Pipeline oil is NOT covered. Price-cap-conforming trade allows "
        "G7+ jurisdictions' service providers (shipping, insurance, finance, "
        "brokering) to facilitate the transaction. Trade above the cap voids "
        "service-provider safe harbour — secondary-sanctions exposure follows."
    ),
    "service_providers_covered": [
        "shipping (vessel charter, ship management)",
        "insurance + reinsurance (P&I clubs, hull, cargo)",
        "trade finance (LC, payment processing in G7+ currencies)",
        "brokering (where the broker is incorporated in a coalition jurisdiction)",
        "flagging (vessel registration in coalition flag states)",
    ],
    "attestation_requirements": (
        "G7+ service providers must obtain attestations through the trade "
        "chain. Tier 1 (charterers / cargo owners): attest price-paid is at "
        "or below the cap. Tier 2 (commodity traders / storage providers): "
        "attest reliance on Tier 1 attestation. Tier 3 (banks / insurers / "
        "shipowners): attest reliance on Tier 1 or 2. Form templates exist "
        "but jurisdiction-specific (US OFAC, UK OFSI, EU Council)."
    ),
    "primary_sources": [
        "https://ofac.treasury.gov/sanctions-programs-and-country-information/russian-harmful-foreign-activities-sanctions",
        "https://www.gov.uk/government/publications/notice-to-exporters-202331-uk-oil-price-cap",
        "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32022R2367",  # EU Reg 2022/2367
    ],
    "common_compliance_failures": [
        "Accepting a flat 'price-cap compliant' assertion without attestation chain documentation",
        "Failing to track whether the cargo was loaded BEFORE the cap effective date (grandfathering)",
        "Treating product-class boundaries (premium vs discount) as obvious — they are not; ICE pricing classifications govern",
        "Assuming pipeline oil exemption applies to ship-to-pipeline transhipments (it does not)",
    ],
    "operator_caveat": (
        "Russia oil price cap is a fast-moving regime — read the live OFAC "
        "Russian Harmful Foreign Activities FAQ before any specific transaction. "
        "Do NOT rely on this module for live transaction structuring; use it as "
        "an awareness-and-flagging surface only."
    ),
}


IRAN_SHIPPING_SDGT = {
    "regime": "OFAC Specially Designated Global Terrorists — Iran shipping",
    "scope": (
        "Distinct from Iran financial sanctions (CISADA, IRGC designations). "
        "Targets specific tankers (vessel-level), shipping companies, "
        "captains, port operators, and port-state shadow infrastructure. "
        "EO 13224 (SDGT) is the typical authority; EO 13902 (Iran shipping) "
        "is its sectoral counterpart."
    ),
    "indicators_of_listed_vessels": [
        "AIS turned off near Iranian waters (gone-dark patterns)",
        "Frequent flag-of-convenience changes (Comoros, Gabon, Liberia rotations)",
        "Ship-to-ship transfer in Persian Gulf / Strait of Hormuz",
        "STS partner is an OFAC-listed vessel",
        "Ownership traced to listed Iranian entities (NIOC, NITC, IRISL affiliates)",
        "Insurance with non-IG (International Group of P&I Clubs) clubs only",
    ],
    "verification_chain": [
        "1. Verify vessel name against OFAC SDN List + UK OFSI + EU Annex listings",
        "2. Cross-check IMO number (immutable across name changes) — NOT just name",
        "3. Trace registered owner + operator + manager — three-tier ownership",
        "4. Check flag history via Equasis (free) for flag-hopping patterns",
        "5. AIS history via MarineTraffic or VesselFinder (paid for full history)",
    ],
    "primary_sources": [
        "https://ofac.treasury.gov/recent-actions",
        "https://www.equasis.org/",
        "https://www.unitedagainstnucleariran.com/iran-tankers-tracker",
    ],
}


VENEZUELA_GL_FRAMEWORK = {
    "regime": "OFAC Venezuela Sanctions + General Licences",
    "current_state": (
        "Venezuela sanctions framework structured around blocking PdVSA + "
        "Maduro government officials, with NARROW General Licences (GL 41/43/44) "
        "authorising specific oil-sector activity. Each GL has technical "
        "preconditions; misreading a GL scope is the #1 compliance failure."
    ),
    "key_general_licences_to_check": [
        "GL 41 (Chevron + PdVSA limited operations) — specific to a named operator",
        "GL 5 series (PdVSA bondholders) — bond-payment-specific carve-outs",
        "GL 8 series (Iran/Venezuela humanitarian) — narrow medical/food only",
        "Any active OFAC GL — list at treasury.gov/policy-issues/financial-sanctions/sanctions-programs-and-country-information/venezuela-related-sanctions",
    ],
    "common_compliance_failures": [
        "Citing a withdrawn or expired GL (always verify current status)",
        "Extending one GL's scope to cover activity outside its specific authorisation",
        "Assuming third-party intermediary structure shields the principal",
        "Underestimating the secondary-sanctions reach for non-US persons",
    ],
}


MARITIME_DARK_FLEET_INDICATORS = {
    "definition": (
        "'Dark fleet' or 'shadow fleet' — vessels operating outside the "
        "transparent shipping ecosystem to evade sanctions or facilitate "
        "sanctioned-cargo transport. Heavily used for Russian oil, Iranian "
        "oil, Venezuelan oil, North Korean coal."
    ),
    "indicators": [
        "Vessel age >15 years (older vessels + cheap acquisitions = typical dark-fleet profile)",
        "Frequent ownership changes via shell companies (single-vessel-owning LLCs)",
        "Flag changes within 12 months",
        "Insurance from non-IG clubs or 'self-insurance' claims",
        "Class certificate from Tier-3 societies (not IACS members)",
        "AIS gaps near sanctioned-jurisdiction waters (loitering at sanction boundaries)",
        "STS transfer activity in known dark-fleet zones (Persian Gulf, Eastern Mediterranean, off Singapore)",
        "Port-state inspection deficiencies / detention history",
    ],
    "due_diligence_resources": [
        "Equasis (free vessel data, including ownership chain)",
        "IMO Global Integrated Shipping Information System (GISIS, free)",
        "Lloyd's List Intelligence (paid, ~£5-15k/yr — the Janes-equivalent for shipping)",
        "Kpler (paid commodity-flow analytics)",
        "TankerTrackers.com (subscription analytics on dark-fleet patterns)",
    ],
}


# ══════════════════════════════════════════════════════════════════════
# 2. COUNTERPARTY FRAUD PATTERNS — oil/LNG-broker specific
# ══════════════════════════════════════════════════════════════════════
# Parallel structure to Clause 16's defence patterns. Operator should
# review these against actual fraud cases Arkmurus has seen — the patterns
# below are well-documented in the trade-finance + maritime-fraud literature
# (FBI, INTERPOL, ICC IMB advisories) but real-case calibration is the
# operator's domain.

OIL_LNG_FRAUD_PATTERNS = [
    {
        "id": "advance_fee_scam_oil",
        "name": "Advance-fee scam (oil-broker variant)",
        "indicators": [
            "Demand for upfront 'mandate fee', 'commitment fee', or 'allocation fee' before any LOI / SCO documentation",
            "Use of NCNDA (Non-Circumvention Non-Disclosure Agreement) as the FIRST document rather than the LAST",
            "Promise of allocation from a named refinery / NOC without verifiable mandate",
            "Pressure to use a specific 'paymaster' or 'escrow agent' the seller insists on",
        ],
        "real_world_volume": "ICC IMB reports advance-fee oil fraud as the single largest source of commodity-broker complaints",
        "action": "REFUSE to pay any fee before SCO + POP + verifiable end-loader confirmation",
    },
    {
        "id": "fake_pop_documents",
        "name": "Fake Proof-of-Product documents",
        "indicators": [
            "POP documents (SGS, Saybolt, Intertek inspection certs) provided as PDFs without verifiable inspection-house URL or QR code",
            "Inspection-house letterhead inconsistent with the actual house's published format",
            "Cargo specifications (API gravity, sulphur content) suspiciously round numbers",
            "Inspection date implausible for the loading port + season",
        ],
        "verification_steps": [
            "Contact SGS / Saybolt / Intertek directly via the inspection-house's official channel — NEVER via the contact provided by the seller",
            "Verify the inspection-house has actually issued the certificate (most major houses have a verification hotline)",
            "Cross-check vessel + load port + load date against AIS history (Equasis free, MarineTraffic paid)",
        ],
        "action": "DO NOT proceed with payment until POP independently verified",
    },
    {
        "id": "non_existent_cargo",
        "name": "Phantom cargo / over-pledged cargo",
        "indicators": [
            "Same cargo offered to multiple buyers in parallel (impossible to verify until you commit)",
            "Vessel allegedly carrying cargo is NOT in AIS at the claimed position",
            "Storage receipt / warehouse warrant from a tank farm that won't confirm by phone",
            "Cargo supposedly in 'strategic reserves' or 'private stockpile' rather than tradeable origin",
        ],
        "action": "Verify cargo location via independent channel (port authority, AIS, terminal operator) BEFORE binding contract",
    },
    {
        "id": "false_seller_mandate",
        "name": "Seller claims 'mandate' from refinery / NOC without proof",
        "indicators": [
            "Verbal claim of being 'mandated by Aramco/Rosneft/PdVSA/etc.' without verifiable mandate letter",
            "Mandate letter that cannot be verified against the named refinery/NOC's public contact list",
            "Refusal to put buyer in direct touch with the refinery/NOC for verification",
            "'Confidentiality' invoked to block verification",
        ],
        "action": "ALWAYS verify mandate via direct independent contact with the named refinery/NOC. Refusal = fraud signal",
    },
    {
        "id": "lng_paper_broker",
        "name": "LNG paper broker (no actual cargo capacity)",
        "indicators": [
            "Counterparty has no demonstrated history of LNG cargo movement (Kpler / Lloyd's List shows no historical activity)",
            "Claims 'spot cargo' availability without explaining sourcing chain",
            "Shell-company structure incorporated in tax haven (BVI, Panama, Marshall Islands) with no operational substance",
            "Domain name pattern: compound name + tax-haven country (e.g. 'lngtradinginternational[panama|seychelles|bvi]sa.com')",
            "Website registered <12 months ago, no real LinkedIn presence for claimed staff",
        ],
        "action": "Verify counterparty's historical cargo activity via Kpler / Lloyd's List BEFORE engaging on commercial terms",
    },
    {
        "id": "russia_oil_pricecap_evasion",
        "name": "Russia G7 oil price-cap evasion structure",
        "indicators": [
            "Cargo origin disclosed as 'Kazakhstan blend' or 'Latvia origin' for crude that should be Russian Urals/Sokol/ESPO",
            "Cargo allegedly transhipped at a port (Sikka, Ras Tanura, Sohar) AFTER price-cap effective date with no clear non-Russian origin documentation",
            "Insurance arranged with non-IG clubs only (typically Russian / Indian / Chinese P&I)",
            "Vessel registered in coalition flag state (Greek, Maltese, Cypriot) but operated by non-coalition manager",
        ],
        "action": "Treat any Russian-origin or potentially-Russian-origin crude as full price-cap-attestation-required transaction",
    },
    {
        "id": "vessel_dark_fleet_match",
        "name": "Vessel matches dark-fleet profile",
        "indicators": MARITIME_DARK_FLEET_INDICATORS["indicators"],
        "action": "Run vessel through Equasis + IMO GISIS + Lloyd's List (if available) before agreeing to STS or charter",
    },
]


# ══════════════════════════════════════════════════════════════════════
# 3. COMMODITY CONTRACT STRUCTURES — Incoterms + payment + verification
# ══════════════════════════════════════════════════════════════════════

INCOTERMS_2020_KEY_TERMS = {
    "FOB": {
        "name": "Free On Board",
        "transfer_point": "ship's rail at port of loading",
        "buyer_responsibility_starts": "as cargo crosses ship's rail at load port",
        "common_use": "bulk crude trades, FOB Med crude, FOB Suez Canal",
        "risk_to_buyer": "cargo loss / contamination during voyage is buyer's risk",
    },
    "CIF": {
        "name": "Cost, Insurance, Freight",
        "transfer_point": "ship's rail at load port (risk transfers there) but seller pays for ocean transit + insurance to discharge port",
        "buyer_responsibility_starts": "at load port for risk; at discharge for delivery",
        "common_use": "delivered crude / refined products to importer port",
        "risk_to_buyer": "even though seller pays freight + insurance, RISK transfers at load port",
    },
    "CFR": {
        "name": "Cost and Freight",
        "transfer_point": "ship's rail at load port (risk) but seller pays for ocean transit only — no insurance",
        "buyer_responsibility_starts": "at load port; buyer arranges own cargo insurance",
        "common_use": "uncommon in oil; seen in refined products to specific destinations",
    },
    "DAP": {
        "name": "Delivered At Place",
        "transfer_point": "named delivery point (typically discharge terminal)",
        "buyer_responsibility_starts": "at the named delivery point",
        "common_use": "growing in oil/LNG — seller bears risk all the way to terminal",
    },
    "DDP": {
        "name": "Delivered Duty Paid",
        "transfer_point": "delivery point with import duties paid by seller",
        "buyer_responsibility_starts": "at named delivery point — seller has cleared customs",
        "common_use": "rare in oil; occasional in refined products to specific markets",
    },
}


COMMODITY_PAYMENT_INSTRUMENTS = {
    "LC_letter_of_credit": {
        "what": "Bank-guaranteed payment instrument; seller paid against compliant documents",
        "common_in": "first-time relationships, high-value cargoes, jurisdictions with credit risk",
        "compliance_check": "LC issuing bank must NOT be on sanctions list; LC-confirming bank in seller's jurisdiction",
        "common_failure": "Seller accepts a non-confirmed LC from a sub-investment-grade bank → can't draw if buyer / bank defaults",
    },
    "LOI_letter_of_intent": {
        "what": "Non-binding statement of intent to purchase; NOT a payment instrument",
        "warning": "An LOI is NOT proof of funds. Sellers asking for LC on the basis of an LOI are misreading the instrument",
        "fraud_signal": "Buyer-side fraud sometimes uses fake LOIs to extract POP without committing capital",
    },
    "tt_telegraphic_transfer": {
        "what": "Wire transfer payment, typically against documents presented",
        "common_in": "established relationships, lower-value cargoes",
        "compliance_check": "Sanctions screening on receiving bank + intermediate correspondents (USD clearing brings OFAC into ANY USD trade)",
        "common_failure": "Wire instructions intercepted via email-compromise — payment goes to fraudster's account",
    },
    "escrow": {
        "what": "Third-party holds funds pending document compliance",
        "compliance_check": "Escrow agent identity + jurisdiction (specifically NOT a fraudster shell company nominated by seller)",
        "fraud_signal": "Seller insisting on a SPECIFIC escrow agent they introduce is a major red flag (often the agent is the fraudster's accomplice)",
    },
}


COMMODITY_DOC_VERIFICATION = [
    {
        "doc": "Bill of Lading (B/L)",
        "verify": [
            "B/L number unique + cross-checked with carrier's online tracking system",
            "Vessel name + IMO matches AIS-confirmed loading at named port",
            "Cargo quantity + type matches shore-tank measurements + inspection cert",
            "Cleanly issued (no notations of cargo-quality concerns)",
        ],
        "fraud_signals": [
            "B/L from non-existent carrier or unverifiable carrier",
            "Same B/L number cited in multiple parallel transactions",
            "Issuing date + voyage timeline implausible vs vessel AIS",
        ],
    },
    {
        "doc": "Certificate of Origin",
        "verify": [
            "Issued by named chamber of commerce or official authority of origin country",
            "Cross-check with origin country's commerce ministry verification portal",
            "Origin claim consistent with vessel's load port",
        ],
        "fraud_signals": [
            "Ad-hoc 'private' origin certificates (not from chamber of commerce)",
            "Origin claim that contradicts vessel AIS history (vessel never been in claimed origin port)",
        ],
    },
    {
        "doc": "Inspection Certificate (SGS / Saybolt / Intertek / others)",
        "verify": "ALWAYS verify directly with the inspection house — major houses have inspection-cert verification hotlines + portals",
        "fraud_signals": [
            "PDF without QR code or inspection-house URL for verification",
            "Letterhead inconsistent with house's published format",
            "Inspector signature + license number can't be verified",
        ],
    },
    {
        "doc": "Notice of Readiness (NOR) / Statement of Facts (SOF)",
        "verify": "Vessel actually at the agreed berth + port operations records (independent confirmation)",
        "fraud_signals": [
            "NOR claimed but vessel AIS shows different position",
            "SOF showing demurrage hours that don't match port records",
        ],
    },
    {
        "doc": "Charter Party",
        "verify": [
            "Owner + charterer entity verification (not shell with no track record)",
            "Vessel is the one actually under contract (IMO check)",
            "Charter terms (laytime, demurrage, deviation clauses) read by counsel",
        ],
    },
]


# ══════════════════════════════════════════════════════════════════════
# 4. ENERGY REGULATORS (additions to ARIA's 188-source catalogue)
# ══════════════════════════════════════════════════════════════════════

ENERGY_REGULATORS = [
    {"name": "FERC", "country": "US", "scope": "Federal Energy Regulatory Commission — electricity, natural gas, oil pipelines, hydropower, LNG terminals",
     "url": "https://www.ferc.gov/", "tier": "tier_1a"},
    {"name": "OFGEM", "country": "UK", "scope": "Office of Gas and Electricity Markets — UK energy regulator",
     "url": "https://www.ofgem.gov.uk/", "tier": "tier_1a"},
    {"name": "BAFA Energy", "country": "DE", "scope": "Bundesamt für Wirtschaft und Ausfuhrkontrolle — energy section (oil + gas market)",
     "url": "https://www.bafa.de/EN/Energy/energy_node.html", "tier": "tier_1a"},
    {"name": "EU REMIT", "country": "EU", "scope": "Regulation on Wholesale Energy Market Integrity and Transparency — published by ACER",
     "url": "https://www.acer.europa.eu/", "tier": "tier_1a"},
    {"name": "ANP Brazil", "country": "BR", "scope": "Agência Nacional do Petróleo — oil/gas/biofuels regulator (Lusophone moat overlap)",
     "url": "https://www.gov.br/anp/pt-br", "tier": "tier_1a"},
    {"name": "ARERA Italy", "country": "IT", "scope": "Italian energy regulator + EU LNG terminal access framework",
     "url": "https://www.arera.it/", "tier": "tier_1a"},
    {"name": "EIA US", "country": "US", "scope": "Energy Information Administration — oil/gas market data, weekly inventories, STEO forecasts",
     "url": "https://www.eia.gov/", "tier": "tier_1b",
     "note": "Already in apis/sources/eia.mjs — verify wiring includes oil-market briefings, not just energy prices"},
    {"name": "OPEC", "country": "international", "scope": "Monthly Oil Market Report (MOMR), production decisions, member-country data",
     "url": "https://www.opec.org/", "tier": "tier_1b"},
    {"name": "IEA", "country": "international", "scope": "International Energy Agency — Oil Market Report, World Energy Outlook",
     "url": "https://www.iea.org/", "tier": "tier_1b"},
    {"name": "Lloyd's List Intelligence", "country": "global", "scope": "Maritime + commodity intelligence — vessel ownership, port calls, sanctions compliance",
     "url": "https://www.lloydslistintelligence.com/", "tier": "tier_1b",
     "note": "PAID (~£5-15k/yr) — Janes-equivalent for shipping. Phase E premium addition per ecosystem audit"},
    {"name": "Equasis", "country": "global", "scope": "Free vessel data, ownership chain, port-state-control inspection history",
     "url": "https://www.equasis.org/", "tier": "tier_1a",
     "note": "FREE alternative for vessel basic data — should be added immediately"},
    {"name": "IMO GISIS", "country": "global", "scope": "IMO Global Integrated Shipping Information System — vessel registry, casualties, port-state-control",
     "url": "https://gisis.imo.org/", "tier": "tier_1a",
     "note": "FREE — required reading for any maritime-related DD"},
]


# ══════════════════════════════════════════════════════════════════════
# 5. DOMAIN-NAME RED-FLAG HEURISTICS — oil/LNG fraud patterns
# ══════════════════════════════════════════════════════════════════════

# These are NOT verdicts — they are RISK INDICATORS that justify Enhanced
# Due Diligence. Per Clause 16 discipline, a domain-name match warrants a
# closer look at corporate substance, NOT an automatic rejection.

_HIGH_RISK_DOMAIN_KEYWORDS = (
    # Compound commodity-trading names typical of paper-broker fraud
    "lngtrading", "oiltrading", "crudetrading", "petroleumtrading",
    "energytrading", "commoditytrading", "fuelstrading",
    "lngint", "oilint", "crudeint", "petroleuminternational",
    "globaloil", "globallng", "globalcrude", "globalpetroleum",
    # Country/jurisdiction tags often on tax-haven shells
    "panamasa", "panamaltd", "bvioil", "seychellesoil", "marshalloil",
    "mauritiusoil", "uaecrude",
)

# Tax-haven TLDs that combine with commodity-trading names = elevated risk
_TAX_HAVEN_TLD_HINTS = (
    ".pa",     # Panama
    ".vg",     # British Virgin Islands
    ".mh",     # Marshall Islands
    ".ky",     # Cayman
    ".bs",     # Bahamas
    ".sc",     # Seychelles
)


def domain_red_flags(url: str) -> dict[str, Any]:
    """Score a URL for oil/LNG-broker fraud risk indicators.

    Returns:
      {
        "risk_score": float (0.0-1.0),
        "matched_indicators": [str],
        "recommendation": str,
      }

    NOT a verdict. Caller should treat high score as Enhanced DD trigger.
    """
    if not url:
        return {"risk_score": 0.0, "matched_indicators": [], "recommendation": "no URL provided"}

    indicators: list[str] = []
    score = 0.0

    url_l = url.lower()

    # Compound commodity name
    for kw in _HIGH_RISK_DOMAIN_KEYWORDS:
        if kw in url_l:
            indicators.append(f"compound commodity-trading domain keyword: {kw}")
            score += 0.30
            break  # one match is enough for this indicator

    # Tax-haven TLD
    for tld in _TAX_HAVEN_TLD_HINTS:
        if url_l.rstrip("/").endswith(tld) or f"{tld}/" in url_l:
            indicators.append(f"tax-haven TLD: {tld}")
            score += 0.20
            break

    # Compound + tax-haven jurisdiction in domain string (e.g. "panamasa")
    haven_strings = ("panamasa", "bvi", "seychelles", "marshall", "mauritius", "cayman", "bahamas")
    for hs in haven_strings:
        if hs in url_l:
            indicators.append(f"tax-haven jurisdiction substring: {hs}")
            score += 0.25
            break

    # Very long compound name (typical paper-broker pattern)
    domain_part = re.sub(r"^https?://", "", url_l).split("/")[0].split(".")[0]
    if len(domain_part) > 25:
        indicators.append(f"unusually long compound domain ({len(domain_part)} chars)")
        score += 0.15

    score = min(1.0, score)

    if score >= 0.6:
        rec = "HIGH RISK — Enhanced DD required: verify operational substance (real office, real staff, real LinkedIn presence, vessel-history confirmation) before any commercial commitment"
    elif score >= 0.3:
        rec = "ELEVATED — verify counterparty has demonstrated commodity-trading history (Kpler / Lloyd's List / Equasis); do not skip substance check"
    else:
        rec = "domain-name pattern not flagged — proceed to standard DD layers"

    return {
        "risk_score": round(score, 2),
        "matched_indicators": indicators,
        "recommendation": rec,
    }


# ══════════════════════════════════════════════════════════════════════
# 6. PUBLIC API — capability-extension entry points
# ══════════════════════════════════════════════════════════════════════

_COMMODITY_DOMAIN_KEYWORDS = (
    "oil", "lng", "crude", "petrol", "petroleum", "gas",
    "energy", "refining", "refinery", "hydrocarbon",
    "barrel", "bbl", "naphtha", "diesel", "fuel oil",
    "feedstock", "tanker", "fleet", "bunker",
)


def is_commodity_dd_target(
    entity_name: str = "",
    url: str = "",
    sector_hint: str = "",
    free_text_context: str = "",
) -> tuple[bool, str]:
    """Detect whether a DD target is in the regulated-commodity scope.

    Returns (is_target, classification) where classification is one of:
      'lng', 'crude_oil', 'refined_products', 'natural_gas',
      'energy_commercial', 'unlikely'.

    Conservative — returns False for borderline cases. Operator can
    always opt-in manually via explicit sector_hint='oil' / 'lng' / etc.
    """
    haystack = " ".join([
        entity_name or "", url or "", sector_hint or "", free_text_context or "",
    ]).lower()

    if not haystack.strip():
        return (False, "unlikely")

    if sector_hint.lower() in ("oil", "lng", "crude", "petroleum", "energy"):
        return (True, sector_hint.lower())

    # Specific high-confidence signals
    if "lng" in haystack:
        return (True, "lng")
    if any(k in haystack for k in ("crude oil", "crude trading", "petroleum trading")):
        return (True, "crude_oil")
    if any(k in haystack for k in ("refinery", "refining", "refined products", "naphtha")):
        return (True, "refined_products")
    if any(k in haystack for k in ("natural gas", "lpg", "fsru")):
        return (True, "natural_gas")

    # Lower-confidence — multiple commodity keywords
    matches = sum(1 for k in _COMMODITY_DOMAIN_KEYWORDS if k in haystack)
    if matches >= 2:
        return (True, "energy_commercial")

    return (False, "unlikely")


def commodity_compliance_bullets(classification: str = "crude_oil") -> list[str]:
    """Return a list of compliance bullets to inject into DD or chat
    context when the target is in regulated-commodity scope.

    Used by:
      - dd_orchestrator (when wired) to enrich the compliance section
      - aria_chat (when wired) to prepend to system prompt
      - direct API surface for operator inspection

    Caller chooses where to render these — module is data-only.
    """
    bullets: list[str] = []

    bullets.append(
        "REGULATED-COMMODITY DD MODE — apply oil/LNG/crude-specific compliance "
        "in addition to standard ARIA defence-broking compliance."
    )

    bullets.append(
        f"Russia G7 oil price cap: applies to ALL seaborne Russian-origin crude "
        f"({RUSSIA_OIL_PRICE_CAP['price_thresholds_usd_per_barrel']['crude_oil']} USD/bbl) "
        f"and refined products. If Russian-origin involved, "
        f"FULL ATTESTATION CHAIN required (Tier 1/2/3). "
        f"Do NOT accept flat 'price-cap compliant' assertion."
    )

    bullets.append(
        "Iran SDGT shipping designations: vessel-level (not entity-level) "
        "sanctions. Verify IMO number against OFAC SDN + UK OFSI + EU Annex. "
        "AIS-dark patterns near Iranian waters are dark-fleet indicators."
    )

    bullets.append(
        "Maritime DD: any cargo movement requires vessel ownership chain "
        "verification (3 tiers: registered owner, operator, manager) + flag "
        "history + AIS history. Equasis FREE; Lloyd's List Intelligence PAID."
    )

    if classification == "lng":
        bullets.append(
            "LNG-broker fraud watch: paper brokers without cargo capacity are "
            "endemic. Verify counterparty has DEMONSTRATED LNG cargo movement "
            "history via Kpler / Lloyd's List BEFORE engaging on commercial terms. "
            "Compound-name + tax-haven domain (e.g. 'lngtradinginternationalpanamasa.com') "
            "is the textbook fraud signature."
        )
    elif classification == "crude_oil":
        bullets.append(
            "Crude-oil DD watch: advance-fee fraud is the #1 source of broker "
            "complaints (ICC IMB). REFUSE any upfront 'mandate fee' / 'commitment "
            "fee' before SCO + POP + verifiable end-loader. Verify POP documents "
            "directly with inspection house (NEVER via seller-provided contact)."
        )
    elif classification == "refined_products":
        bullets.append(
            "Refined-products DD: product-class boundaries (premium vs discount "
            "under Russia price cap) are non-obvious — ICE pricing classifications "
            "govern. Demurrage + B/L verification critical for refined cargoes "
            "with multiple discharge-port options."
        )

    bullets.append(
        "Documentation discipline: B/L cross-checked with vessel AIS; "
        "Inspection Cert verified with issuing house; NOR/SOF cross-checked "
        "with port operations records. NEVER accept parties' own attestations "
        "as primary verification."
    )

    return bullets


def enrich_dd_report(report_dict: dict) -> dict:
    """Add a regulated_commodity section to a DD report dict.

    Operator-callable — NOT auto-wired into dd_orchestrator. Returns the
    report dict with a new `regulated_commodity` key containing:
      - classification
      - domain_red_flags (if URL present)
      - compliance_bullets
      - applicable_fraud_patterns
      - recommended_sources
    """
    target = report_dict.get("target", {}) or {}
    entity_name = target.get("name", "") or ""
    url = target.get("website") or target.get("url", "") or ""
    sector_hint = target.get("sector", "") or ""

    is_target, classification = is_commodity_dd_target(
        entity_name=entity_name, url=url, sector_hint=sector_hint,
    )

    if not is_target:
        report_dict["regulated_commodity"] = {
            "applicable": False,
            "reason": "target does not match commodity-DD heuristics; opt-in via sector hint if applicable",
        }
        return report_dict

    flags = domain_red_flags(url) if url else None
    bullets = commodity_compliance_bullets(classification)

    # Match fraud patterns to the classification
    relevant_patterns = []
    if classification == "lng":
        relevant_patterns = [p for p in OIL_LNG_FRAUD_PATTERNS if p["id"] in
                             ("lng_paper_broker", "advance_fee_scam_oil", "fake_pop_documents",
                              "non_existent_cargo", "false_seller_mandate")]
    elif classification == "crude_oil":
        relevant_patterns = [p for p in OIL_LNG_FRAUD_PATTERNS if p["id"] in
                             ("advance_fee_scam_oil", "fake_pop_documents", "non_existent_cargo",
                              "false_seller_mandate", "russia_oil_pricecap_evasion",
                              "vessel_dark_fleet_match")]
    else:
        relevant_patterns = OIL_LNG_FRAUD_PATTERNS  # all

    report_dict["regulated_commodity"] = {
        "applicable": True,
        "classification": classification,
        "domain_red_flags": flags,
        "compliance_bullets": bullets,
        "applicable_fraud_patterns": [
            {"id": p["id"], "name": p["name"], "indicators": p["indicators"], "action": p["action"]}
            for p in relevant_patterns
        ],
        "recommended_sources": [
            r for r in ENERGY_REGULATORS
            if classification == "lng" and "LNG" in (r.get("scope", "") or "") or
               classification in ("crude_oil", "refined_products") and any(
                   k in (r.get("scope", "") or "").lower() for k in ("oil", "petroleum", "energy"))
        ][:8],
        "operator_note": (
            "This enrichment was generated by regulated_commodity_pack.py — a "
            "capability extension, not a positioning change. ARIA remains a "
            "defence-broking + compliance platform. The pack lets ARIA serve "
            "regulated-commodity broker requests using the same DD discipline "
            "without rebranding."
        ),
    }

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="regulated_commodity_pack",
        summary="Enrich Dd Report",
        source_id="regulated_commodity_pack:R-F996",
    )

    return report_dict


# ══════════════════════════════════════════════════════════════════════
# 7. INDUSTRY 360 — OPEC, OPEC+, decision mechanics
# ══════════════════════════════════════════════════════════════════════

OPEC_STRUCTURE = {
    "founded": "1960 (Baghdad — Iran, Iraq, Kuwait, Saudi Arabia, Venezuela)",
    "current_membership_2026": [
        "Algeria", "Republic of the Congo", "Equatorial Guinea", "Gabon",
        "Iran", "Iraq", "Kuwait", "Libya", "Nigeria", "Saudi Arabia",
        "United Arab Emirates", "Venezuela",
    ],
    "departures_recent": {
        "Angola": "Jan 2024 — disagreement over production quota; reduces OPEC African voice",
        "Ecuador": "2020 — fiscal pressure, asserting full crude sovereignty",
        "Indonesia": "2016 — net importer status incompatible with member-state economics",
        "Qatar": "2019 — pivoted strategic focus to LNG (now world's largest LNG exporter)",
    },
    "secretariat": "Vienna, Austria",
    "decision_bodies": {
        "Conference": "Highest authority; ministerial-level; meets at least twice yearly (March + November ordinary, plus extraordinary)",
        "Board of Governors": "Member-nominated, oversees Secretariat day-to-day operations",
        "Economic Commission Board": "Technical analysis underpinning policy decisions",
        "Secretary General": "Currently (2026) Haitham Al Ghais (Kuwait, took office 2022)",
    },
    "voting": (
        "Decisions require unanimity in principle. In practice, Saudi Arabia "
        "+ allies (UAE, Kuwait, Iraq) form the dominant bloc; Iran/Venezuela "
        "have been periodically constrained by sanctions; Nigeria/Libya by "
        "domestic instability. African members typically follow Saudi lead."
    ),
}

OPEC_PLUS = {
    "name_origin": "OPEC+ formed 2016 (Algiers Accord) to coordinate production with non-OPEC producers",
    "non_opec_partners_2026": [
        "Russia", "Mexico", "Kazakhstan", "Azerbaijan", "Oman", "Bahrain",
        "Brunei", "Malaysia", "South Sudan", "Sudan",
    ],
    "decision_dynamics": (
        "OPEC+ governed by JMMC (Joint Ministerial Monitoring Committee) "
        "between full ministerial meetings. Russia + Saudi Arabia are de facto "
        "co-leaders — Saudi sets long-term direction; Russia's leverage is "
        "production volume + energy-export weight. Voluntary cuts (above the "
        "headline OPEC+ quota) increasingly common — eg Saudi 2023-2024 "
        "1m bpd voluntary, UAE 2024 0.4m bpd, Russia 2024 0.5m bpd."
    ),
    "compliance_monitoring": (
        "OPEC+ uses secondary sources (Platts, Argus, S&P Global, IEA, EIA, "
        "Wood Mackenzie, Energy Intelligence Group) for compliance verification — "
        "members do NOT self-report. IEA Oil Market Report + OPEC's own MOMR "
        "are the standard external trackers."
    ),
    "production_quota_lifecycle": (
        "Quotas allocated by reference baseline (typically previous-year output). "
        "Members exceeding quota = 'over-compliance failure'; chronic over-producers "
        "(Iraq, Kazakhstan, UAE pre-2024 readjustment) trigger inter-member tension."
    ),
}

OPEC_DECISIONS_HISTORICAL_PATTERNS = [
    "2014 — Saudi-led decision NOT to cut production despite price collapse → 2016 price crash to ~$26/bbl",
    "2016 — Algiers Accord formalises OPEC+, first joint cut since 2008",
    "2020 — Russia-Saudi price war (March-April), OPEC+ disagreement collapsed coordination → April 20 WTI went negative",
    "2022 — OPEC+ resists Western pressure to increase production despite Russia-Ukraine; preserves coalition",
    "2023-2024 — Saudi extends voluntary cuts repeatedly; UAE pushed for higher baseline (granted 2024)",
    "2025-2026 — gradual unwind of voluntary cuts as global demand recovers",
]


# ══════════════════════════════════════════════════════════════════════
# 8. MAJOR PLAYERS — NOCs (National Oil Companies) + IOCs (International)
# ══════════════════════════════════════════════════════════════════════

NATIONAL_OIL_COMPANIES = [
    {"name": "Saudi Aramco", "country": "Saudi Arabia",
     "scale": "world's largest oil company by revenue + production (~10-12 mbpd capacity)",
     "key_grades": ["Arab Light", "Arab Heavy", "Arab Medium", "Arab Extra Light", "Arab Super Light"],
     "structure": "publicly listed since 2019 (Saudi Tadawul) — ~98% Saudi government ownership",
     "compliance_note": "Aramco crude exports require official allocation — NO LEGITIMATE 'mandate' from third-party brokers without Aramco endorsement"},
    {"name": "ADNOC", "country": "UAE",
     "scale": "~3 mbpd capacity; aggressive downstream + LNG expansion",
     "key_grades": ["Murban", "Upper Zakum", "Das"],
     "compliance_note": "Murban traded on ICE Futures Abu Dhabi (IFAD) since 2021 — first Middle East crude with futures contract"},
    {"name": "Rosneft", "country": "Russia",
     "scale": "~4-5 mbpd; world's largest publicly-traded crude producer pre-sanctions",
     "key_grades": ["Urals", "ESPO", "Sokol", "Vityaz"],
     "compliance_note": "OFAC + UK + EU sanctioned including under price-cap regime; significant secondary-sanctions exposure for Western counterparties"},
    {"name": "Gazprom Neft", "country": "Russia", "scale": "~1.5 mbpd",
     "compliance_note": "Subsidiary of Gazprom; same sanctions regime as Rosneft"},
    {"name": "PetroChina / CNPC", "country": "China",
     "scale": "~4 mbpd domestic + significant international",
     "compliance_note": "China's largest E&P; significant Iran/Venezuela trade through Chinese intermediary structures"},
    {"name": "Sinopec", "country": "China",
     "scale": "world's largest refiner by capacity (~5-6 mbpd)",
     "compliance_note": "Refining-led; major buyer of Russian / Iranian discounted crude"},
    {"name": "CNOOC", "country": "China", "scale": "offshore-focused E&P; ~1.5 mbpd",
     "compliance_note": "On US Entity List since 2020 — restrictions on US-tech access"},
    {"name": "PdVSA", "country": "Venezuela",
     "scale": "~1 mbpd current (was 3.5 mbpd pre-2010 collapse)",
     "key_grades": ["Merey", "Boscan", "Santa Barbara"],
     "compliance_note": "OFAC SDN-listed; transactions require specific OFAC General Licence (currently GL 41 etc — VERIFY CURRENT STATUS)"},
    {"name": "NIOC + NICO + NITC", "country": "Iran",
     "scale": "~3 mbpd capacity (~1.5-2 mbpd actual exports under sanctions)",
     "structure": "NIOC = National Iranian Oil Company; NICO = trading subsidiary; NITC = National Iranian Tanker Company",
     "compliance_note": "All three OFAC SDN-listed; transactions involving US persons / USD prohibited; secondary-sanctions exposure for non-US"},
    {"name": "Pemex", "country": "Mexico", "scale": "~1.5 mbpd",
     "key_grades": ["Maya (heavy sour)", "Olmeca (light sweet)", "Istmo (light)"],
     "compliance_note": "Mexico is non-OPEC but OPEC+ partner; Maya is reference heavy-sour grade for US Gulf Coast refineries"},
    {"name": "Petrobras", "country": "Brazil",
     "scale": "~3 mbpd; Brazil's pre-salt expansion makes it growing non-OPEC supplier",
     "key_grades": ["Lula", "Búzios", "Iracema"],
     "compliance_note": "ANP regulator; concessions + production-sharing under recent rounds"},
    {"name": "Sonangol", "country": "Angola", "scale": "~1.1 mbpd",
     "key_grades": ["Cabinda", "Dalia", "Girassol", "Pazflor", "Plutonio"],
     "compliance_note": "Angola left OPEC Jan 2024; Lusophone-Africa moat overlap for Arkmurus"},
    {"name": "NNPC", "country": "Nigeria", "scale": "~1.5 mbpd",
     "key_grades": ["Bonny Light", "Forcados", "Qua Iboe"],
     "compliance_note": "Petroleum Industry Act 2021 restructured NNPC into commercial entity; oil theft endemic — verify cargo origin chain"},
    {"name": "Sonatrach", "country": "Algeria", "scale": "~1 mbpd",
     "key_grades": ["Saharan Blend"],
     "compliance_note": "OPEC member; gas export via pipeline (Maghreb-Europe, Medgaz) more material than crude"},
    {"name": "QatarEnergy", "country": "Qatar",
     "scale": "LNG-led — world's largest LNG exporter (~77 mtpa, expanding to ~126 mtpa by 2027)",
     "compliance_note": "Qatar left OPEC 2019 to focus on LNG; long-term SPA discipline is industry standard"},
    {"name": "Equinor", "country": "Norway", "scale": "~2 mbpd equivalent (oil + gas)",
     "key_grades": ["Johan Sverdrup", "Troll"],
     "compliance_note": "Major North Sea producer; gas pipeline supplier to UK / EU"},
]

INTERNATIONAL_OIL_COMPANIES = [
    {"name": "ExxonMobil", "country": "US", "scale": "~3.7 mbpd equivalent",
     "key_assets": "Permian Basin (US shale), Guyana (Stabroek block — Exxon operator)",
     "compliance_note": "US-domiciled; subject to OFAC + ITAR if controlled tech"},
    {"name": "Shell", "country": "Netherlands/UK", "scale": "~3 mbpd equivalent",
     "key_assets": "Pearl GTL Qatar, Brazil pre-salt, Permian"},
    {"name": "BP", "country": "UK", "scale": "~2.3 mbpd equivalent",
     "key_assets": "UK North Sea, Caspian (Azerbaijan ACG), GoM, Indonesia Tangguh LNG",
     "compliance_note": "Russian Rosneft 19.75% stake exited 2022 post-invasion"},
    {"name": "Chevron", "country": "US", "scale": "~3 mbpd equivalent",
     "key_assets": "Tengiz (Kazakhstan), Permian, GoM, Australia (Wheatstone/Gorgon LNG)",
     "compliance_note": "Currently (2026) holds OFAC GL 41 for limited Venezuela operations"},
    {"name": "TotalEnergies", "country": "France",
     "scale": "~2.8 mbpd equivalent; global LNG leader (~50 mtpa)",
     "key_assets": "Mozambique LNG (force majeure since 2021), Iraq, Angola, US LNG"},
    {"name": "Eni", "country": "Italy", "scale": "~1.7 mbpd equivalent",
     "key_assets": "Mozambique Coral Sul FLNG, Egypt Zohr, Libya, Norway Johan Castberg",
     "compliance_note": "Strong Africa footprint — Lusophone overlap for Arkmurus"},
    {"name": "ConocoPhillips", "country": "US",
     "scale": "~1.8 mbpd; pure E&P (no downstream)",
     "key_assets": "US Lower 48 (Permian / Eagle Ford), Alaska, Norway"},
]

INDEPENDENT_TRADERS_FIVE_PLUS = [
    {"name": "Vitol", "scale": "world's largest independent oil trader (~7 mbpd traded)",
     "incorporated": "Netherlands (registered) / Switzerland (operations)",
     "structure": "private partnership; opaque ownership",
     "compliance_note": "OFAC settlement Dec 2020 ($163M Iran sanctions case)"},
    {"name": "Glencore", "scale": "~3-4 mbpd traded + significant mining",
     "incorporated": "Switzerland (Baar) / publicly listed LSE+JSE+HKE",
     "compliance_note": "Multiple bribery/sanctions settlements 2022 ($1.5B+ across UK SFO, US DOJ, Brazil); ongoing compliance monitor"},
    {"name": "Trafigura", "scale": "~6 mbpd traded; second-largest indep oil trader",
     "incorporated": "Singapore (registered) / Geneva (operations)",
     "compliance_note": "Subject to SFO investigation 2024 (Brazil bribery)"},
    {"name": "Mercuria", "scale": "~3 mbpd traded",
     "incorporated": "Switzerland (Geneva)",
     "structure": "private; founded by Marco Dunand + Daniel Jaeggi (ex-Phibro)"},
    {"name": "Gunvor", "scale": "~2-3 mbpd traded",
     "incorporated": "Cyprus / Geneva operations",
     "compliance_note": "Significant historical Russia exposure; Timchenko 2014 sanctions trigger; reduced Russia activity post-2022"},
    {"name": "Hartree Partners", "scale": "smaller-scale physical + financial trader; significant US power + gas"},
    {"name": "Petraco", "scale": "smaller; specialised crude trader; Switzerland"},
    {"name": "CNPC International / Unipec / PetroChina International",
     "note": "Trading arms of Chinese NOCs — increasingly significant in crude flows; opaque to Western counterparties"},
    {"name": "Indian Oil Corporation, BPCL, HPCL",
     "note": "Indian state refiners with significant trading operations; major buyers of Russian discounted crude post-2022"},
]


# ══════════════════════════════════════════════════════════════════════
# 9. CRUDE BENCHMARKS + GRADES
# ══════════════════════════════════════════════════════════════════════

CRUDE_BENCHMARKS = {
    "Brent": {
        "type": "light sweet (~38 API, 0.4% sulphur)",
        "physical_basket": "BFOET — Brent, Forties, Oseberg, Ekofisk, Troll (added 2018; Midland WTI added 2023)",
        "futures_market": "ICE Brent (London) — global benchmark for ~70% of internationally-traded crude",
        "delivery": "Sullom Voe terminal (Shetland)",
    },
    "WTI": {
        "type": "light sweet (~40 API, 0.3% sulphur)",
        "physical_basket": "WTI Cushing — pipeline-deliverable at Cushing OK",
        "futures_market": "NYMEX WTI (CME)",
        "delivery": "Cushing, Oklahoma (US Gulf Coast pipeline hub)",
    },
    "Dubai/Oman": {
        "type": "medium sour (~31 API, 2.0% sulphur)",
        "physical_basket": "Dubai + Oman + Upper Zakum + Murban (added 2018)",
        "pricing_authority": "Platts MoC (Market on Close) — Asia benchmark",
        "use": "reference for Middle East crude exports to Asia (Saudi OSP linked to Dubai)",
    },
    "Urals": {
        "type": "medium sour (~31 API, 1.5% sulphur)",
        "origin": "Russian export blend (Black Sea Novorossiysk + Baltic Primorsk)",
        "pricing": "discount to Brent; widened to $30+/bbl post-2022 sanctions",
        "compliance_note": "Subject to Russia G7 oil price cap; pricing monitored for cap-conforming trade",
    },
    "ESPO": {
        "type": "light sweet (~35 API, 0.6% sulphur)",
        "origin": "Russia East Siberia (Eastern Siberia-Pacific Ocean pipeline → Kozmino)",
        "use": "primary Russian crude flow to Asia (China especially)",
    },
    "Bonny Light": {"type": "light sweet (~32 API, 0.16% sulphur)", "origin": "Nigeria",
                    "compliance_note": "Oil theft + bunkering concerns affect cargo verification"},
    "Murban": {"type": "light sour (~40 API, 0.78% sulphur)", "origin": "UAE ADNOC",
               "futures": "ICE Futures Abu Dhabi (IFAD) since March 2021"},
    "Maya": {"type": "heavy sour (~22 API, 3.3% sulphur)", "origin": "Mexico Pemex",
             "use": "reference heavy-sour for US Gulf Coast refining"},
    "Mars": {"type": "medium sour (~28 API)", "origin": "US GoM", "use": "USGC benchmark for sour crude"},
}


# ══════════════════════════════════════════════════════════════════════
# 10. PRICE REPORTING AGENCIES (PRAs) — how prices are FORMED
# ══════════════════════════════════════════════════════════════════════

PRICE_REPORTING_AGENCIES = [
    {"name": "Platts (S&P Global Commodity Insights)",
     "role": "dominant PRA for oil + gas + LNG + petrochemicals",
     "methodology": "MoC (Market-on-Close) — concentrated trading window with reportable bids/offers/trades",
     "key_assessments": ["Dated Brent (BFOET)", "Dubai (sour)", "JKM (Japan/Korea Marker)",
                         "Singapore Mogas / Gasoil", "RBOB / Heating Oil"]},
    {"name": "Argus Media",
     "role": "second-largest PRA; growing share, especially in physical markets",
     "key_assessments": ["Argus Sour Crude Index (ASCI) — used for Mexican Maya pricing",
                         "Argus Russian crudes", "Argus LPG"]},
    {"name": "OPIS (Oil Price Information Service)",
     "owner": "S&P Global (acquired 2022)",
     "key_assessments": ["US wholesale gasoline/diesel/jet fuel"]},
    {"name": "ICIS",
     "key_assessments": ["LNG East-Asia / NW Europe spot", "petrochemicals"]},
    {"name": "Energy Intelligence Group (EIG)",
     "role": "research + pricing publisher (Petroleum Intelligence Weekly, World Gas Intelligence)"},
]


# ══════════════════════════════════════════════════════════════════════
# 11. SHIPPING — vessel classes, routes, freight indices
# ══════════════════════════════════════════════════════════════════════

VESSEL_CLASSES = {
    "VLCC": {"name": "Very Large Crude Carrier", "capacity": "200,000-320,000 DWT", "typical_cargo": "2 million bbl crude"},
    "ULCC": {"name": "Ultra Large Crude Carrier", "capacity": "320,000+ DWT"},
    "Suezmax": {"name": "Suezmax", "capacity": "120,000-200,000 DWT", "typical_cargo": "1 million bbl crude"},
    "Aframax": {"name": "Aframax", "capacity": "80,000-120,000 DWT"},
    "Panamax": {"name": "Panamax", "capacity": "55,000-80,000 DWT"},
    "MR": {"name": "Medium Range product tanker", "capacity": "25,000-55,000 DWT"},
    "LR1": {"name": "Long Range 1", "capacity": "55,000-80,000 DWT"},
    "LR2": {"name": "Long Range 2", "capacity": "80,000-120,000 DWT"},
    "Q-Max LNG": {"name": "Q-Max LNG carrier", "capacity": "266,000 m³ LNG"},
    "Q-Flex LNG": {"name": "Q-Flex LNG carrier", "capacity": "210,000 m³ LNG"},
    "Standard LNG": {"capacity": "150,000-180,000 m³ LNG"},
    "FSRU": {"name": "Floating Storage Regasification Unit",
             "use": "Imports LNG + regasifies in situ → grid; faster to deploy than land terminal"},
}

CHOKEPOINTS = [
    {"name": "Strait of Hormuz", "throughput": "~17 mbpd crude + significant LNG",
     "risk": "Iran proximity; closure threat in escalation scenarios"},
    {"name": "Bab-el-Mandeb / Red Sea / Suez", "throughput": "~9 mbpd",
     "risk": "Yemen Houthi attacks 2023-2024 forced major rerouting around Cape of Good Hope"},
    {"name": "Strait of Malacca", "throughput": "~16 mbpd",
     "risk": "Singapore + Indonesian coast; piracy historical; choke risk concerns China"},
    {"name": "Turkish Straits (Bosphorus + Dardanelles)", "throughput": "~3 mbpd",
     "risk": "Russia + Caspian crude transit; Ukraine war raised regulatory + insurance scrutiny"},
    {"name": "Panama Canal", "throughput": "smaller crude; significant for US Gulf to Asia LNG",
     "risk": "Drought-induced low water levels reduced transit slots 2023-2024"},
    {"name": "Cape of Good Hope", "throughput": "secondary route; rising due to Bab-el-Mandeb diversion",
     "risk": "Longer voyage → higher freight rates + insurance"},
]

FREIGHT_INDICES = {
    "Worldscale": "Industry-standard freight rate flat-rate index; quoted as % of flat",
    "Baltic Dirty Tanker Index (BDTI)": "Crude tanker freight index — Baltic Exchange",
    "Baltic Clean Tanker Index (BCTI)": "Refined-products tanker freight index",
    "BLNG (Baltic LNG)": "LNG carrier rate index",
}


# ══════════════════════════════════════════════════════════════════════
# 12. REFINING — economics, hubs, complexity
# ══════════════════════════════════════════════════════════════════════

REFINING_ECONOMICS = {
    "crack_spread": {
        "definition": "Difference between refined-products price and crude input price",
        "common_spreads": [
            "3:2:1 — 3 bbl crude → 2 bbl gasoline + 1 bbl distillate (US benchmark)",
            "5:3:2 — 5 bbl crude → 3 bbl gasoline + 2 bbl distillate (alt US benchmark)",
            "Singapore complex margin — Asia benchmark (gasoil-heavy)",
        ],
    },
    "nelson_complexity_index": {
        "definition": "Weighted measure of refinery upgrading capacity (FCC, hydrocracker, coker)",
        "ranges": "Simple (3-5) → Medium (5-10) → Complex (10+)",
        "key_complex_refineries": [
            "Reliance Jamnagar (India) — world's largest single-site refinery, ~1.4 mbpd, Nelson ~14",
            "ExxonMobil Baytown TX (US Gulf Coast)",
            "Shell Pulau Bukom (Singapore)",
            "SK Energy Ulsan (South Korea)",
        ],
    },
}

REFINING_HUBS = [
    {"name": "US Gulf Coast (USGC)", "capacity": "~9 mbpd",
     "note": "World's largest refining cluster; consumes Mexican Maya + Canadian heavy + LTO blend"},
    {"name": "Singapore + Malaysia", "capacity": "~3 mbpd",
     "note": "Asia trading hub + bunkering centre"},
    {"name": "ARA (Amsterdam-Rotterdam-Antwerp)", "capacity": "~3 mbpd",
     "note": "Northwest Europe hub; gasoline export-focused"},
    {"name": "South Korea (Yeosu, Daesan, Ulsan)", "capacity": "~3 mbpd"},
    {"name": "India (Jamnagar, Vadinar, Mangalore)", "capacity": "~5 mbpd",
     "note": "Major buyer of Russian discounted crude + significant export refinery"},
    {"name": "China (Sinopec Zhenhai, CNPC Dalian)", "capacity": "~17 mbpd"},
    {"name": "Middle East (Aramco Yanbu/Ras Tanura, ADNOC Ruwais)", "capacity": "~5 mbpd"},
]


# ══════════════════════════════════════════════════════════════════════
# 13. LNG MARKET STRUCTURE
# ══════════════════════════════════════════════════════════════════════

LNG_MARKET = {
    "global_trade_2026": "~430 mtpa (million tonnes per annum) — growing rapidly post-2022 EU dash for gas",
    "top_exporters": [
        {"country": "United States", "capacity_mtpa_2026": "~120", "growth": "Cheniere Sabine/Corpus, Freeport, Cameron, Plaquemines"},
        {"country": "Qatar", "capacity_mtpa_2026": "~77 (expanding to ~126 by 2027)", "key_project": "North Field Expansion"},
        {"country": "Australia", "capacity_mtpa_2026": "~88", "key_facilities": "Gorgon, Wheatstone, Ichthys, Prelude FLNG"},
        {"country": "Russia", "capacity_mtpa_2026": "~30 (sanctions limited expansion)", "key_facility": "Yamal LNG, Sakhalin-2"},
        {"country": "Malaysia", "capacity_mtpa_2026": "~30"},
        {"country": "Algeria", "capacity_mtpa_2026": "~25"},
        {"country": "Nigeria (NLNG)", "capacity_mtpa_2026": "~22"},
        {"country": "Indonesia", "capacity_mtpa_2026": "~16"},
        {"country": "Mozambique (Coral Sul FLNG + Rovuma onshore)", "capacity_mtpa_2026": "~3.4 + 13 (TotalEnergies onshore force majeure)",
         "note": "Lusophone-Africa moat overlap for Arkmurus"},
    ],
    "top_importers": ["Japan", "China", "South Korea", "India", "EU (DE+NL+FR+ES+IT)", "UK", "Taiwan", "Thailand", "Singapore"],
    "pricing_mechanisms": {
        "JCC (Japan Crude Cocktail)": "Historical Asia LNG pricing — averaged crude basket; declining share",
        "JKM (Japan/Korea Marker)": "Spot LNG benchmark — Platts-assessed; growing reference for new contracts",
        "TTF (Title Transfer Facility)": "Dutch gas hub — primary Europe LNG benchmark",
        "Henry Hub": "US natural gas spot price + 115% (typical formula) → US LNG export pricing",
        "Brent-linked": "Used in some legacy long-term SPAs (e.g. Qatar SPAs to Asia)",
    },
    "contract_structures": {
        "long_term_SPA": {
            "duration": "15-25 years (declining; new contracts often 10-15 years)",
            "volume": "Annual Contract Quantity (ACQ) with delivery flexibility (Annual Delivery Programme)",
            "pricing": "Brent-linked (~12-15% slope) OR Henry-Hub-linked (~115%) OR JKM-linked (newer)",
            "destination_clauses": "Historical destination restrictions largely deprecated post-2018 EU competition rulings",
        },
        "spot_cargo": {
            "duration": "Single cargo / multi-cargo strip",
            "pricing": "JKM / TTF / regional spot benchmark at delivery",
            "growing_share": "From ~5% pre-2010 to ~30%+ today",
        },
    },
    "broker_role_in_LNG": (
        "LNG broking is dominated by large traders (Vitol, Trafigura, Gunvor, "
        "Glencore, Mercuria) + utility-affiliated traders (RWE Supply & Trading, "
        "EnBW, Naturgy). Independent brokers face high credibility barriers — "
        "verifying counterparty cargo capacity is the FIRST DD step. 'Paper-only' "
        "LNG brokers without demonstrated cargo movement are a documented fraud "
        "pattern (see lng_paper_broker)."
    ),
}


# ══════════════════════════════════════════════════════════════════════
# 14. TRADE LIFECYCLE END-TO-END
# ══════════════════════════════════════════════════════════════════════

TRADE_LIFECYCLE = [
    {"stage": "Origination", "description": "Identifying buyer or seller need; matching counterparty profile to transaction"},
    {"stage": "Soft probe", "description": "Initial counterparty contact; verifying mandate / authority via independent channel"},
    {"stage": "NCNDA", "description": "Non-Circumvention Non-Disclosure Agreement",
     "warning": "NCNDA-first behaviour from counterparty (especially before SCO) can be a fraud signal"},
    {"stage": "FCO / SCO", "description": "Full Corporate Offer / Soft Corporate Offer — seller's offer with cargo specs, price, terms"},
    {"stage": "POP", "description": "Proof of Product — inspection certificate, ATB, or similar",
     "warning": "POP forgery is endemic; verify with inspection house DIRECTLY"},
    {"stage": "POF", "description": "Proof of Funds — buyer demonstrates capacity (RWA, MT760, etc.)",
     "warning": "POF documents can be fraudulent; verify with issuing bank directly"},
    {"stage": "Pre-advice / Notice of Readiness", "description": "Seller notifies buyer; vessel arrives at load port"},
    {"stage": "Loading + Inspection", "description": "Independent inspection (SGS/Saybolt/Intertek) verifies quality + quantity"},
    {"stage": "B/L issued", "description": "Bill of Lading issued by carrier; transfers title (depending on B/L type)"},
    {"stage": "Documentation transit", "description": "B/L + invoice + COA + COQ + EUR1 (if applicable) couriered or e-doc'd"},
    {"stage": "Discharge + Inspection", "description": "Vessel arrives; independent inspection at discharge"},
    {"stage": "Settlement", "description": "Payment per agreed instrument (LC, escrow, TT) against compliant documents"},
    {"stage": "Demurrage + claims", "description": "Reconcile NOR/SOF; settle demurrage; settle quality claims if any"},
]


# ══════════════════════════════════════════════════════════════════════
# 15. MARKET EVENTS + INVENTORY REPORTS — what moves the market
# ══════════════════════════════════════════════════════════════════════

MARKET_EVENTS = {
    "scheduled_recurring": [
        {"event": "OPEC + OPEC+ ministerial meetings", "frequency": "min 2x/year + extraordinary"},
        {"event": "JMMC monthly review", "frequency": "monthly"},
        {"event": "EIA Weekly Petroleum Status Report", "frequency": "Wednesday 10:30 ET", "impact": "biggest weekly market mover"},
        {"event": "API Weekly Statistical Bulletin", "frequency": "Tuesday afternoon ET (preview of EIA)"},
        {"event": "EIA Monthly STEO", "frequency": "monthly"},
        {"event": "OPEC MOMR (Monthly Oil Market Report)", "frequency": "monthly mid-month"},
        {"event": "IEA Oil Market Report", "frequency": "monthly mid-month"},
    ],
    "unscheduled_high_impact": [
        "Geopolitical incidents (Strait of Hormuz interdiction, Bab-el-Mandeb attacks)",
        "Hurricane / weather (US Gulf Coast refinery + production disruption — annual June-Nov)",
        "Pipeline outages (Druzhba Russia-EU, Nord Stream gas)",
        "Sanctions changes (OFAC additions / removals; EU package adoption)",
        "Major refinery fires / outages",
        "Strikes (TotalEnergies France, Argentina YPF, French refinery actions)",
        "Black-swan supply shocks (Aramco Abqaiq Sep 2019 attack — temporarily halved Saudi output)",
    ],
}


# ══════════════════════════════════════════════════════════════════════
# 16. ENERGY TRANSITION + CARBON REGULATIONS
# ══════════════════════════════════════════════════════════════════════

ENERGY_TRANSITION_REGIMES = {
    "CBAM": {"name": "EU Carbon Border Adjustment Mechanism",
             "scope": "Initial sectors: cement, iron+steel, aluminium, fertilisers, electricity, hydrogen",
             "fuel_relevance": "Refined-products imports may eventually be in-scope (post-2026 review)"},
    "EEXI_CII": {"name": "IMO Energy Efficiency Existing Ship Index + Carbon Intensity Indicator",
                 "in_force_since": "January 2023",
                 "impact": "Older, less-efficient vessels face progressive restrictions"},
    "Methane_Regulations": {
        "EU Methane Regulation 2024": "Methane emission reporting + reduction obligations",
        "US Methane Rule": "EPA rule on upstream methane (subject to political reversal)",
    },
    "Carbon_pricing_evolution": {
        "EU ETS": "Allowance prices €65-100/tCO2 typical 2024-2026 range",
        "UK ETS": "Mirrors EU but separate market post-Brexit",
    },
}


# ══════════════════════════════════════════════════════════════════════
# 17. RUSSIA POST-2022 — sanctions, dark fleet, payment, alternate flows
# ══════════════════════════════════════════════════════════════════════

RUSSIA_POST_2022_DYNAMICS = {
    "key_changes": [
        "EU + G7 stopped buying ~3 mbpd Russian crude (was 4-5 mbpd export)",
        "India + China absorbed ~2.5 mbpd of redirected Russian crude (Reliance, IOC, BPCL, HPCL, Sinopec, CNPC)",
        "G7 oil price cap effective Dec 2022 (crude) / Feb 2023 (products)",
        "Insurance: Russia + India formed alternative P&I clubs to bypass IG of P&I",
        "Payment: Rouble + Yuan + Rupee + UAE Dirham trade settlement increased; USD-denominated trade declined",
        "Shipping: Russia acquired/chartered ~600+ tanker dark-fleet vessels via opaque ownership chains",
        "Discount: Urals discount to Brent reached -$30/bbl peak 2022; narrowed to -$10 to -$15 by 2024-2025",
        "Refinery damage: Ukrainian strikes 2024-2025 reduced Russian refining capacity ~600k bpd at peak",
    ],
    "compliance_implications_for_western_brokers": [
        "Any USD-cleared transaction touching Russian-origin crude requires price-cap attestation",
        "Even non-USD transactions with Russian-origin crude carry secondary-sanctions exposure for Western counterparts",
        "'Latvia origin' / 'Kazakhstan blend' / 'India re-export' claims for Russian-actual crude are documented evasion patterns",
        "Insurance via non-IG clubs is the operational compliance flag — no Western insurer = no Western broker / charterer touch",
    ],
}


# ══════════════════════════════════════════════════════════════════════
# 18. INDUSTRY KNOWLEDGE SUMMARY (callable for ARIA self-knowledge)
# ══════════════════════════════════════════════════════════════════════

def industry_knowledge_summary() -> dict[str, Any]:
    """Structured summary of what this pack KNOWS about the oil/LNG/crude
    industry. Use when ARIA is asked 'what do you know about X' or for
    capability disclosure.
    """
    return {
        "scope": "regulated commodity adjacency to ARIA's defence-broking + compliance core",
        "depth": "industry-360 reference — sufficient for credible DD + commercial conversation; NOT for live transaction structuring without operator + legal review",
        "knowledge_sections": {
            "sanctions": {
                "covered": ["Russia G7 oil price cap", "Iran SDGT shipping", "Venezuela GL framework", "Maritime dark fleet"],
                "depth": "rule-aware + verification-chain pointers; primary-source URLs included",
            },
            "fraud_patterns": {"count": len(OIL_LNG_FRAUD_PATTERNS), "covered": [p["id"] for p in OIL_LNG_FRAUD_PATTERNS]},
            "incoterms_2020": {"count": len(INCOTERMS_2020_KEY_TERMS), "covered": list(INCOTERMS_2020_KEY_TERMS.keys())},
            "payment_instruments": {"count": len(COMMODITY_PAYMENT_INSTRUMENTS), "covered": list(COMMODITY_PAYMENT_INSTRUMENTS.keys())},
            "doc_verification": {"count": len(COMMODITY_DOC_VERIFICATION),
                                 "covered": [d["doc"] for d in COMMODITY_DOC_VERIFICATION]},
            "energy_regulators": {"count": len(ENERGY_REGULATORS), "covered": [r["name"] for r in ENERGY_REGULATORS]},
            "OPEC_OPEC+": {"covered": "membership, structure, decision dynamics, JMMC monitoring, historical patterns"},
            "NOCs": {"count": len(NATIONAL_OIL_COMPANIES), "covered": [n["name"] for n in NATIONAL_OIL_COMPANIES]},
            "IOCs": {"count": len(INTERNATIONAL_OIL_COMPANIES), "covered": [n["name"] for n in INTERNATIONAL_OIL_COMPANIES if n.get("name")]},
            "independent_traders": {"count": len(INDEPENDENT_TRADERS_FIVE_PLUS),
                                    "covered": [n["name"] for n in INDEPENDENT_TRADERS_FIVE_PLUS]},
            "crude_benchmarks": {"count": len(CRUDE_BENCHMARKS), "covered": list(CRUDE_BENCHMARKS.keys())},
            "PRAs": {"count": len(PRICE_REPORTING_AGENCIES), "covered": [p["name"].split(" ")[0] for p in PRICE_REPORTING_AGENCIES]},
            "vessel_classes": {"count": len(VESSEL_CLASSES), "covered": list(VESSEL_CLASSES.keys())},
            "chokepoints": {"count": len(CHOKEPOINTS), "covered": [c["name"] for c in CHOKEPOINTS]},
            "refining_hubs": {"count": len(REFINING_HUBS), "covered": [r["name"] for r in REFINING_HUBS]},
            "LNG_market": {"covered": "exporters (top 9), importers, pricing (JCC/JKM/TTF/HH/Brent), contract structures, broker role"},
            "trade_lifecycle": {"stages": len(TRADE_LIFECYCLE), "covered": [s["stage"] for s in TRADE_LIFECYCLE]},
            "market_events": {"scheduled": len(MARKET_EVENTS["scheduled_recurring"]),
                              "unscheduled": len(MARKET_EVENTS["unscheduled_high_impact"])},
            "energy_transition": {"covered": ["CBAM", "EEXI/CII", "Methane", "Carbon pricing"]},
            "Russia_post_2022": {"covered": ["price cap impact", "redirected flows", "dark fleet", "alternate insurance/payment"]},
        },
        "deliberate_gaps": [
            "Live OPEC+ quota math (use OPEC MOMR + IEA — these change monthly)",
            "Real-time crude benchmark prices (use Platts / Argus / EIA — module is structure, not pricing)",
            "Specific real-case Arkmurus fraud experiences (operator's domain)",
            "Live OFAC General Licence text (verify treasury.gov directly before transaction)",
            "Detailed refining yields per crude grade (use refinery LP models or Wood Mackenzie analytics)",
            "Vessel-specific sanctions designations (use OFAC SDN search by IMO + Equasis)",
            "Trader-internal ownership structure (Vitol/Trafigura/Mercuria are PRIVATE — ownership not fully public)",
        ],
        "credibility_caveat": (
            "This pack provides INDUSTRY AWARENESS sufficient for DD-level "
            "conversation. It does NOT replace: (a) live primary-source verification "
            "for transactions, (b) operator domain expertise on Arkmurus's specific "
            "deal patterns + counterparty network, (c) qualified legal / compliance "
            "review for sanctions structuring. ARIA in commodity-DD mode should "
            "always cite primary sources (treasury.gov, eur-lex, gov.uk, OPEC, IEA, "
            "EIA) rather than rely on this module's data alone. Per Clauses 14 + 17, "
            "verifiable facts require primary-source attribution at the point of claim."
        ),
        "integration_status": "implemented but NOT yet integrated — opt-in via dd_orchestrator wiring or chat-context injection (operator decision)",
    }

# R-F2119 §21a — wire failure handler for regulated_commodity_pack
try:
    wire_failure(module="regulated_commodity_pack", detail="module shutdown",
                gap_type="engine_failure", source="regulated_commodity_pack:shutdown")
except Exception:
    pass
