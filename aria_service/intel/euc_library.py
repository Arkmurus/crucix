"""ARIA EUC Library — End-User Certificate templates + clause gap detection.

A defence broker handles EUCs constantly:
  - Drafting one for a new export → needs a template for the right jurisdiction
  - Receiving one from a customer → needs to validate it has every required clause
  - Filing one → needs it linked to the deal in the pipeline

This module covers the first two. Linking is via deal_pipeline.update_lead with
an `euc_status` tag — done by the calling route, not here.

Templates and required-clause sets are derived from:
  - US DSP-83 (ITAR § 123.10 Non-Transfer and Use Certificate)
  - UK SIEL standard (DBT/ECJU guidance)
  - EU Regulation 2021/821 model EUC
  - Wassenaar Arrangement End User Statement template
  - GCC common-pattern (UAE/Saudi/Qatar/Oman observed practice)

NOT a substitute for legal review. The gap detector flags missing clauses
the broker must address; the controlling authority decides licence outcome.
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("aria.euc_library")


# ── Clause definitions ──────────────────────────────────────────────────────
# Each clause: id, label, severity (critical|important|optional), and a list
# of regex patterns. A clause is "present" if any pattern matches the EUC text.
# Patterns are deliberately permissive (case-insensitive substring with light
# variation) — false negatives are worse than false positives here, since a
# missed clause produces a noisy "gap" the broker will dismiss.

_UNIVERSAL_CLAUSES: list[dict] = [
    {
        "id": "end_user_identity",
        "label": "End-user identity (legal name + address)",
        "severity": "critical",
        "patterns": [
            r"end[\s\-]?user\s+(?:is|name|identity)",
            r"the\s+(?:undersigned|end[\s\-]?user|consignee)",
            r"(?:purchaser|recipient|importer)\s+(?:is|name|name\s+and\s+address)",
        ],
    },
    {
        "id": "end_use_statement",
        "label": "Specific end-use statement",
        "severity": "critical",
        "patterns": [
            r"end[\s\-]?use\b",
            r"(?:intended|final)\s+(?:use|purpose)",
            r"(?:will|shall)\s+be\s+used\s+(?:for|by|in)",
            r"purpose\s+of\s+(?:the\s+)?(?:import|acquisition|purchase)",
        ],
    },
    {
        "id": "items_description",
        "label": "Items description + quantity",
        "severity": "critical",
        "patterns": [
            r"(?:goods?|items?|equipment|articles?|commodit(?:y|ies))\s+(?:described|listed|specified|covered)",
            r"quantity\s+(?:of|:)",
            r"(?:item|article|product)\s+description",
        ],
    },
    {
        "id": "destination",
        "label": "Country of final destination",
        "severity": "critical",
        "patterns": [
            r"(?:country|destination)\s+of\s+(?:final\s+)?(?:destination|delivery|use)",
            r"final\s+destination",
            r"(?:imported|delivered)\s+(?:into|to)\s+\w+",
        ],
    },
    {
        "id": "no_reexport",
        "label": "Non-re-export commitment",
        "severity": "critical",
        "patterns": [
            # Tolerate up to 50 chars between "not be" and "re-exported" to
            # match enumerated lists like "will not be: (a) re-exported..."
            r"(?:not|will\s+not|shall\s+not)\s+(?:be\b.{0,50}?)?re[\s\-]?export(?:ed)?",
            r"no\s+(?:re[\s\-]?export|re[\s\-]?transfer|diversion)",
            r"(?:without|prior)\s+(?:the\s+)?(?:prior\s+)?(?:written\s+)?(?:consent|approval|authori[sz]ation)\s+of",
        ],
    },
    {
        "id": "no_retransfer",
        "label": "Non-retransfer to third party",
        "severity": "critical",
        "patterns": [
            r"(?:not|will\s+not|shall\s+not)\s+(?:be\b.{0,50}?)?(?:transfer(?:red)?|retransfer(?:red)?|sold|loaned|disposed)",
            r"(?:no|without)\s+(?:transfer|sale|loan|disposal)",
            r"third\s+(?:party|country|state)",
        ],
    },
    {
        "id": "signatory",
        "label": "Authorised signatory + position",
        "severity": "critical",
        "patterns": [
            r"(?:signed|signature|authori[sz]ed\s+(?:by|signatory))",
            r"(?:position|title|rank|capacity)\s*[:\-]",
            r"(?:minister|director|general|brigadier|colonel|secretary|head)\s+of",
        ],
    },
    {
        "id": "issuing_authority",
        "label": "Issuing government authority + seal",
        "severity": "critical",
        "patterns": [
            r"(?:government|ministry|department)\s+of",
            r"(?:official|government)\s+(?:seal|stamp)",
            r"(?:issued|certified)\s+by",
        ],
    },
    {
        "id": "date_place",
        "label": "Date and place of issue",
        "severity": "important",
        "patterns": [
            r"(?:date|dated)\s*[:\-]",
            r"(?:place|location)\s+of\s+issue",
            r"\b(?:19|20)\d{2}\b",  # year present
        ],
    },
    {
        "id": "delivery_verification",
        "label": "Delivery verification commitment (allows on-site inspection)",
        "severity": "important",
        "patterns": [
            r"(?:delivery|on[\s\-]?site|post[\s\-]?shipment)\s+(?:verification|inspection|check)",
            r"(?:right|permission)\s+to\s+(?:inspect|verify)",
            r"verify\s+(?:end[\s\-]?use|delivery|installation)",
        ],
    },
]

# US DSP-83 specific (ITAR § 123.10) — adds explicit non-transfer-and-use language
_DSP83_CLAUSES: list[dict] = [
    {
        "id": "dsp83_nonretransfer_warranty",
        "label": "DSP-83: explicit non-retransfer warranty (ITAR §123.10(a))",
        "severity": "critical",
        "patterns": [
            r"will\s+not\s+(?:re[\s\-]?export|retransfer|sell|transfer)\s+(?:the\s+)?(?:articles?|defen[cs]e\s+articles?|technical\s+data)",
            r"itar",
            r"(?:non[\s\-]?transfer|non[\s\-]?retransfer)\s+(?:and|&)\s+use",
        ],
    },
    {
        "id": "dsp83_us_government_consent",
        "label": "DSP-83: prior written consent of US Government",
        "severity": "critical",
        "patterns": [
            r"prior\s+written\s+(?:consent|approval|authori[sz]ation)\s+of\s+the\s+(?:united\s+states|u\.?s\.?)\s+government",
            r"department\s+of\s+state",
            r"directorate\s+of\s+defen[cs]e\s+trade",
        ],
    },
]

# UK / EU dual-use additional checks
_EU_DUAL_USE_CLAUSES: list[dict] = [
    {
        "id": "eu_dual_use_reference",
        "label": "EU Reg 2021/821 reference or equivalent",
        "severity": "important",
        "patterns": [
            r"(?:regulation|reg(?:\.|ulation))\s*(?:eu|\(eu\))?\s*2021/821",
            r"dual[\s\-]?use\s+(?:regulation|item|control)",
            r"council\s+regulation",
        ],
    },
]

# R-F53 (2026-05-09): jurisdiction-specific clause add-ons. Each list is
# small — three to five extra checks that distinguish a market's EUC
# practice from the universal set. Patterns kept fuzzy on purpose so a
# slightly-paraphrased official template still matches.

_ISRAEL_CLAUSES: list[dict] = [
    {
        "id": "il_sibat_approval",
        "label": "SIBAT / DECA approval reference",
        "severity": "critical",
        "patterns": [
            r"sibat", r"deca", r"defen[cs]e\s+export\s+control(?:s)?\s+agency",
            r"israel(?:i)?\s+ministry\s+of\s+defen[cs]e",
        ],
    },
    {
        "id": "il_wa_mtcr_compliance",
        "label": "Wassenaar / MTCR adherence statement",
        "severity": "important",
        "patterns": [
            r"wassenaar", r"mtcr", r"missile\s+technology\s+control\s+regime",
        ],
    },
]

_TURKEY_CLAUSES: list[dict] = [
    {
        "id": "tr_ssb_approval",
        "label": "SSB / Presidency of Defence Industries approval",
        "severity": "critical",
        "patterns": [
            r"ssb", r"savunma\s+sanayi(?:i)?\s+ba[sş]kanl[ıi][gğ][ıi]",
            r"presidency\s+of\s+defen[cs]e\s+industries",
        ],
    },
    {
        "id": "tr_decision_2014_7",
        "label": "Decision-Note 2014/7 retransfer reference",
        "severity": "important",
        "patterns": [r"2014/7", r"decision\s+note\s+2014"],
    },
]

_INDIA_CLAUSES: list[dict] = [
    {
        "id": "in_dgft_dpp_approval",
        "label": "DGFT / DDP approval reference",
        "severity": "critical",
        "patterns": [
            r"dgft", r"directorate\s+general\s+of\s+foreign\s+trade",
            r"ddp", r"department\s+of\s+defen[cs]e\s+production",
        ],
    },
    {
        "id": "in_scomet_classification",
        "label": "SCOMET classification reference",
        "severity": "important",
        "patterns": [
            r"scomet", r"special\s+chemicals,?\s+organisms,?\s+materials",
        ],
    },
]

_BRAZIL_CLAUSES: list[dict] = [
    {
        "id": "br_md_coadi_approval",
        "label": "MD / COADI approval reference",
        "severity": "critical",
        "patterns": [
            r"minist[éee]rio\s+da\s+defesa", r"\bmd\b", r"coadi",
            r"comiss[ãa]o\s+de\s+coordena[çc][ãa]o",
        ],
    },
    {
        "id": "br_pne_decree_9607",
        "label": "PNE / Decree 9607 reference",
        "severity": "important",
        "patterns": [
            r"pne\b", r"pol[íi]tica\s+nacional\s+de\s+exporta[çc][ãa]o",
            r"decreto\s+9\.?607", r"decree\s+9\.?607",
        ],
    },
]

_SAUDI_CLAUSES: list[dict] = [
    {
        "id": "sa_gami_approval",
        "label": "GAMI / General Authority for Military Industries approval",
        "severity": "critical",
        "patterns": [
            r"gami", r"general\s+authority\s+for\s+military\s+industries",
            r"الهيئة\s+العامة\s+للصناعات\s+العسكرية",
        ],
    },
    {
        "id": "sa_no_transfer_yemen_iran",
        "label": "No-transfer to designated states clause",
        "severity": "important",
        "patterns": [
            r"shall\s+not\s+(?:be\s+)?transfer(?:red)?\s+to\s+(?:iran|yemen|hou)",
            r"non[\s\-]?aligned\s+state\s+exclusion",
        ],
    },
]

_UAE_CLAUSES: list[dict] = [
    {
        "id": "ae_secc_approval",
        "label": "SECC / Strategic Goods Export Committee approval",
        "severity": "critical",
        "patterns": [
            r"secc", r"strategic\s+goods\s+export\s+committee",
            r"executive\s+office\s+for\s+control\s+&?\s*non[\s\-]?proliferation",
            r"اللجنة\s+الوطنية\s+لمراقبة\s+الصادرات",
        ],
    },
    {
        "id": "ae_federal_law_13_2007",
        "label": "Federal Law 13/2007 reference",
        "severity": "important",
        "patterns": [r"federal\s+law\s+(?:no\.?\s*)?13[\s/-]?2007", r"13\s+of\s+2007"],
    },
]

_SOUTH_AFRICA_CLAUSES: list[dict] = [
    {
        "id": "za_ncacc_approval",
        "label": "NCACC approval reference",
        "severity": "critical",
        "patterns": [
            r"ncacc", r"national\s+conventional\s+arms\s+control\s+committee",
            r"act\s+(?:no\.?\s*)?41\s+of\s+2002",
        ],
    },
    {
        "id": "za_dceltc_classification",
        "label": "DCEC / DCAC classification reference",
        "severity": "important",
        "patterns": [
            r"dcec", r"dcac", r"directorate\s+conventional\s+arms\s+control",
        ],
    },
]


# ── Profiles ────────────────────────────────────────────────────────────────
# A profile bundles the clause set required for a transaction type.

PROFILES: dict[str, dict] = {
    "US_DSP83": {
        "label": "US DSP-83 (ITAR Non-Transfer and Use Certificate)",
        "regime": "ITAR § 123.10",
        "clauses": _UNIVERSAL_CLAUSES + _DSP83_CLAUSES,
    },
    "UK_GENERAL": {
        "label": "UK SIEL End-User Undertaking",
        "regime": "UK Strategic Export Control",
        "clauses": _UNIVERSAL_CLAUSES,
    },
    "EU_DUAL_USE": {
        "label": "EU Dual-Use End-User Statement",
        "regime": "EU Reg 2021/821",
        "clauses": _UNIVERSAL_CLAUSES + _EU_DUAL_USE_CLAUSES,
    },
    "WASSENAAR_GENERIC": {
        "label": "Wassenaar Arrangement End User Statement",
        "regime": "Wassenaar Arrangement",
        "clauses": _UNIVERSAL_CLAUSES,
    },
    "GCC_GENERIC": {
        "label": "GCC End-User Certificate (UAE/Saudi/Qatar/Oman common pattern)",
        "regime": "Per-state strategic goods regime",
        "clauses": _UNIVERSAL_CLAUSES,
    },
    # R-F53: jurisdiction-specific profiles (10-market target).
    "ISRAEL_SIBAT": {
        "label": "Israel — SIBAT/DECA EUC",
        "regime": "Israel Defense Export Control Law 5767-2007",
        "clauses": _UNIVERSAL_CLAUSES + _ISRAEL_CLAUSES,
    },
    "TURKEY_SSB": {
        "label": "Turkey — SSB EUC",
        "regime": "Decision-Note 2014/7 + Law 7406",
        "clauses": _UNIVERSAL_CLAUSES + _TURKEY_CLAUSES,
    },
    "INDIA_DGFT_DDP": {
        "label": "India — DGFT/DDP EUC",
        "regime": "FT(DR) Act 1992 + SCOMET",
        "clauses": _UNIVERSAL_CLAUSES + _INDIA_CLAUSES,
    },
    "BRAZIL_MD_COADI": {
        "label": "Brazil — MD/COADI EUC",
        "regime": "PNE / Decree 9607",
        "clauses": _UNIVERSAL_CLAUSES + _BRAZIL_CLAUSES,
    },
    "SAUDI_GAMI": {
        "label": "Saudi Arabia — GAMI EUC",
        "regime": "GAMI Regulations",
        "clauses": _UNIVERSAL_CLAUSES + _SAUDI_CLAUSES,
    },
    "UAE_SECC": {
        "label": "UAE — SECC EUC",
        "regime": "Federal Law 13/2007",
        "clauses": _UNIVERSAL_CLAUSES + _UAE_CLAUSES,
    },
    "SOUTH_AFRICA_NCACC": {
        "label": "South Africa — NCACC EUC",
        "regime": "Act 41 of 2002",
        "clauses": _UNIVERSAL_CLAUSES + _SOUTH_AFRICA_CLAUSES,
    },
}


# ── Templates ───────────────────────────────────────────────────────────────
# Skeleton text templates. Real EUCs are issued by destination governments
# on letterhead; these are drafting aids for the broker to send to the
# customer for completion. Keep deliberately minimal — legal team will adapt.

_TEMPLATE_CORE = """\
END-USER CERTIFICATE

[Government letterhead — issuing ministry]

We, the undersigned, [MINISTRY/DEPARTMENT NAME], [COUNTRY], hereby certify:

1. END USER
   Name: [LEGAL NAME OF END USER]
   Address: [FULL ADDRESS]
   Country: [COUNTRY]

2. ITEMS COVERED
   Description: [ITEMS / EQUIPMENT / TECHNOLOGY]
   Quantity: [QUANTITY]
   Manufacturer / supplier: [SUPPLIER NAME, COUNTRY]

3. END USE
   The above-described items will be used exclusively for: [SPECIFIC END USE]
   Place of installation / deployment: [LOCATION]

4. FINAL DESTINATION
   The country of final destination is: [COUNTRY]

5. UNDERTAKINGS
   The end user undertakes that the items will not be:
     (a) re-exported or retransferred to any third party, country, or
         destination without the prior written consent of the
         [SUPPLIER GOVERNMENT / EXPORT CONTROL AUTHORITY];
     (b) used for any purpose other than the end use stated above;
     (c) used in connection with the development, production or use of
         chemical, biological, or nuclear weapons or missile delivery
         systems.

6. DELIVERY VERIFICATION
   The end user agrees to permit on-site verification of delivery and
   end-use by representatives of the supplier government upon reasonable
   request.

7. SIGNATORY
   Name: [FULL NAME]
   Position: [TITLE — e.g. Director of Procurement, Ministry of Defence]
   Date: [DATE]
   Place: [CITY, COUNTRY]
   Signature: ____________________
   Official seal: [GOVERNMENT SEAL]
"""

_TEMPLATE_DSP83_TAIL = """\

8. ITAR § 123.10 NON-TRANSFER AND USE CERTIFICATE
   The end user further certifies that the defence articles and/or
   technical data covered above are intended for the end-use stated and
   that the end user will not re-export, resell, or otherwise dispose of
   the articles or data outside the country of ultimate destination
   without the prior written approval of the United States Government,
   acting through the Directorate of Defense Trade Controls, Department
   of State.
"""

_TEMPLATE_EU_TAIL = """\

8. EU REG 2021/821 STATEMENT
   This certificate is issued in connection with an export subject to
   Council Regulation (EU) 2021/821 setting up a Union regime for the
   control of exports, brokering, technical assistance, transit and
   transfer of dual-use items.
"""

_TEMPLATE_ISRAEL_TAIL = """\

8. SIBAT / DECA AUTHORISATION
   This export is authorised by [LICENCE NO.] issued by the Defense
   Export Controls Agency (DECA) of the State of Israel under the
   Defense Export Control Law 5767-2007. The end user undertakes to
   comply with all conditions imposed by DECA / SIBAT.

9. WASSENAAR / MTCR ADHERENCE
   The end user confirms compliance with the Wassenaar Arrangement
   and (where applicable) the Missile Technology Control Regime.
"""

_TEMPLATE_TURKEY_TAIL = """\

8. SSB AUTHORISATION
   This export is authorised by [İHRAÇ İZİN BELGESİ NO.] issued by
   the Republic of Türkiye Presidency of Defence Industries (Savunma
   Sanayi Başkanlığı / SSB) under Decision-Note 2014/7 and Law 7406.

9. RETRANSFER RESTRICTION
   The end user shall not retransfer the items to any third party
   without prior written consent of SSB and the supplier government.
"""

_TEMPLATE_INDIA_TAIL = """\

8. DGFT / DDP AUTHORISATION
   This import is authorised by [LICENCE NO.] issued by the
   Directorate General of Foreign Trade (DGFT) and / or the
   Department of Defence Production (DDP), Ministry of Defence,
   Government of India.

9. SCOMET CLASSIFICATION
   The items are classified under SCOMET category [X] / sub-class
   [Y] of the Foreign Trade (Development & Regulation) Act, 1992
   and Schedule 2 of the SCOMET list.
"""

_TEMPLATE_BRAZIL_TAIL = """\

8. AUTORIZAÇÃO MD / COADI
   Esta importação está autorizada por [Nº DA AUTORIZAÇÃO] emitida
   pelo Ministério da Defesa (MD) através da Comissão de Coordenação
   da Indústria de Material de Emprego Militar (COADI), em
   conformidade com o Decreto 9.607 e a Política Nacional de
   Exportação (PNE).

9. NÃO RETRANSFERÊNCIA
   O usuário final compromete-se a não retransferir os itens a
   terceiros sem o consentimento prévio por escrito do MD/COADI.
"""

_TEMPLATE_SAUDI_TAIL = """\

8. GAMI AUTHORISATION
   This import is authorised by [LICENCE NO.] issued by the General
   Authority for Military Industries (GAMI) of the Kingdom of Saudi
   Arabia. The end user undertakes to comply with all GAMI
   conditions and the Kingdom's strategic-goods regulations.

9. NON-DIVERSION COMMITMENT
   The end user confirms the items shall not be transferred to or
   used in connection with any state or non-state actor designated
   by GAMI as a restricted destination, including (without
   limitation) any actor under UN Security Council sanctions.
"""

_TEMPLATE_UAE_TAIL = """\

8. SECC AUTHORISATION
   This import is authorised by [LICENCE NO.] issued by the
   Strategic Goods Export Committee (SECC) of the United Arab
   Emirates under Federal Law No. 13 of 2007 on Goods, Materials
   and Equipment Subject to Import and Export Controls, and the
   Executive Office for Control & Non-Proliferation (EOCN).

9. NON-DIVERSION COMMITMENT
   The end user shall not transfer the items to any third party
   without prior written consent of the SECC.
"""

_TEMPLATE_SOUTH_AFRICA_TAIL = """\

8. NCACC AUTHORISATION
   This import is authorised by [PERMIT NO.] issued by the
   National Conventional Arms Control Committee (NCACC) of the
   Republic of South Africa under the National Conventional
   Arms Control Act, 41 of 2002, and administered by the
   Directorate Conventional Arms Control (DCAC).

9. END-USE / NON-RETRANSFER UNDERTAKING
   The end user undertakes to comply with all conditions imposed
   by the NCACC, including the prohibition on retransfer without
   prior NCACC approval and the obligation to permit on-site
   end-use verification by representatives of the NCACC or the
   supplier government.
"""

TEMPLATES: dict[str, str] = {
    "US_DSP83": _TEMPLATE_CORE + _TEMPLATE_DSP83_TAIL,
    "UK_GENERAL": _TEMPLATE_CORE,
    "EU_DUAL_USE": _TEMPLATE_CORE + _TEMPLATE_EU_TAIL,
    "WASSENAAR_GENERIC": _TEMPLATE_CORE,
    "GCC_GENERIC": _TEMPLATE_CORE,
    "ISRAEL_SIBAT": _TEMPLATE_CORE + _TEMPLATE_ISRAEL_TAIL,
    "TURKEY_SSB": _TEMPLATE_CORE + _TEMPLATE_TURKEY_TAIL,
    "INDIA_DGFT_DDP": _TEMPLATE_CORE + _TEMPLATE_INDIA_TAIL,
    "BRAZIL_MD_COADI": _TEMPLATE_CORE + _TEMPLATE_BRAZIL_TAIL,
    "SAUDI_GAMI": _TEMPLATE_CORE + _TEMPLATE_SAUDI_TAIL,
    "UAE_SECC": _TEMPLATE_CORE + _TEMPLATE_UAE_TAIL,
    "SOUTH_AFRICA_NCACC": _TEMPLATE_CORE + _TEMPLATE_SOUTH_AFRICA_TAIL,
}


# ── Public API ──────────────────────────────────────────────────────────────

def list_profiles() -> list[dict]:
    """Return all available EUC profiles for the UI / API."""
    return [
        {
            "id": pid,
            "label": p["label"],
            "regime": p["regime"],
            "clause_count": len(p["clauses"]),
            "critical_clauses": sum(1 for c in p["clauses"] if c["severity"] == "critical"),
        }
        for pid, p in PROFILES.items()
    ]


def get_template(profile_id: str) -> Optional[str]:
    """Return the template text for a profile."""
    return TEMPLATES.get(profile_id)


def _clause_present(text_lower: str, clause: dict) -> tuple[bool, str]:
    """Return (present, matched_pattern) for a single clause."""
    for pat in clause["patterns"]:
        m = re.search(pat, text_lower, re.IGNORECASE)
        if m:
            return True, m.group(0)[:80]
    return False, ""


def gap_check(euc_text: str, profile_id: str = "UK_GENERAL") -> dict:
    """Validate a submitted EUC against a profile's required clauses.

    Returns clauses present + missing, an overall status, and next actions.
    """
    profile = PROFILES.get(profile_id)
    if not profile:
        raise ValueError(f"unknown profile: {profile_id}. Available: {list(PROFILES)}")
    if not euc_text or len(euc_text.strip()) < 20:
        raise ValueError("euc_text too short — provide the full submitted EUC body")

    text_lower = euc_text.lower()
    present: list[dict] = []
    missing: list[dict] = []

    for clause in profile["clauses"]:
        ok, matched = _clause_present(text_lower, clause)
        record = {
            "id": clause["id"],
            "label": clause["label"],
            "severity": clause["severity"],
        }
        if ok:
            record["matched_phrase"] = matched
            present.append(record)
        else:
            missing.append(record)

    critical_missing = [m for m in missing if m["severity"] == "critical"]
    important_missing = [m for m in missing if m["severity"] == "important"]

    if critical_missing:
        status = "REJECT"
        headline = (
            f"{len(critical_missing)} critical clause(s) missing — "
            f"do not accept this EUC. Request a corrected version from the issuing authority."
        )
    elif important_missing:
        status = "GAPS"
        headline = (
            f"All critical clauses present, but {len(important_missing)} important clause(s) missing. "
            f"Either request an addendum or document the gap before licence application."
        )
    else:
        status = "VALID"
        headline = "All required clauses identified. Retain on file and link to the deal."

    next_actions: list[str] = []
    if status == "REJECT":
        next_actions.append("Reply to issuing authority listing the missing critical clauses verbatim.")
        next_actions.append(f"Send corrected template ({profile_id}) for re-issue.")
        next_actions.append("Do NOT proceed to licence application until critical gaps closed.")
    elif status == "GAPS":
        next_actions.append("Decide: request addendum from issuing authority OR file with the licence application as-is and document the gap.")
        next_actions.append("If filing as-is, prepare a covering note explaining why each missing clause is acceptable in this case.")
    else:
        next_actions.append("Tag the deal as EUC_VALID and store the document reference.")
        next_actions.append("Diary a re-validation 12 months from issue date or before each shipment.")

    result = {
        "profile": profile_id,
        "profile_label": profile["label"],
        "regime": profile["regime"],
        "status": status,
        "headline": headline,
        "clauses_present": present,
        "clauses_missing": missing,
        "critical_missing_count": len(critical_missing),
        "important_missing_count": len(important_missing),
        "total_clauses": len(profile["clauses"]),
        "next_actions": next_actions,
        "disclaimer": (
            "Pattern-based clause detection. False positives possible if the EUC uses "
            "uncommon legal phrasing. Final acceptance is a legal-review decision."
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # ── Brain hook + audit log: fire-and-forget if an event loop is running.
    # gap_check is sync; the absorb + audit calls are scheduled only when a
    # loop is actually available (production routes always have one; some
    # sync test contexts won't).
    try:
        import asyncio
        from . import brain_hook, audit_log
        loop = asyncio.get_running_loop()
        loop.create_task(brain_hook.absorb(
            module="euc_library",
            summary=(
                f"EUC gap check ({profile_id}): {status} — "
                f"{len(present)}/{len(profile['clauses'])} clauses present, "
                f"{len(critical_missing)} critical missing"
            ),
            success=(status == "VALID"),
            confidence="ASSESSED",
            gap_type="euc_critical_clauses_missing" if status == "REJECT" else None,
            gap_detail=f"Missing critical clauses: {', '.join(c['id'] for c in critical_missing)}" if critical_missing else None,
        ))
        loop.create_task(audit_log.record(
            action="euc_check",
            actor="euc_library.gap_check",
            entity_name=profile_id,
            inputs={"profile": profile_id, "text_length": len(euc_text)},
            outputs={
                "status": status,
                "clauses_present": [c["id"] for c in present],
                "clauses_missing": [c["id"] for c in missing],
                "critical_missing_count": len(critical_missing),
            },
            decision=status,
            confidence="ASSESSED",
            notes=f"Profile: {profile['label']}; regime: {profile['regime']}",
        ))
    except RuntimeError:
        pass  # no running loop (sync test context) — skip silently
    except Exception as e:
        logger.debug("brain_hook/audit absorb failed (non-fatal): %s", e)

    return result

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="euc_library",
                     summary="euc_library module active",
                     source_id="euc_library:init")
    except Exception:
        try:
            wire_failure(module="euc_library", detail="module init failed",
                        gap_type="engine_failure", source="euc_library:init")
        except Exception:
            pass
