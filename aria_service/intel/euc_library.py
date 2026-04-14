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

TEMPLATES: dict[str, str] = {
    "US_DSP83": _TEMPLATE_CORE + _TEMPLATE_DSP83_TAIL,
    "UK_GENERAL": _TEMPLATE_CORE,
    "EU_DUAL_USE": _TEMPLATE_CORE + _TEMPLATE_EU_TAIL,
    "WASSENAAR_GENERIC": _TEMPLATE_CORE,
    "GCC_GENERIC": _TEMPLATE_CORE,
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

    # ── Brain hook: fire-and-forget if an event loop is running.
    # gap_check is sync (called from sync route handler logic in some paths),
    # so we schedule the absorb only when a loop is actually available.
    try:
        import asyncio
        from . import brain_hook
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
    except RuntimeError:
        pass  # no running loop (sync test context) — skip silently
    except Exception as e:
        logger.debug("brain_hook absorb failed (non-fatal): %s", e)

    return result
