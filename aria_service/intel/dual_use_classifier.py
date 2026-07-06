"""ARIA Dual-Use Decision Engine.

Wraps the existing tech_classifier (which returns ML/USML/EAR codes) and adds
the jurisdictional layer a broker actually asks for:

  "I have product X going from country A to country B for end-use Y.
   Is a licence required? Which authority issues it? How long does it take?
   Are there embargo conflicts I should know about?"

Returns a structured decision, not just a code list. Reuses tech_classifier
for classification — does NOT duplicate the regime data in
global_export_control.py.
"""
from __future__ import annotations
from .engine_wiring import wire_success

import logging
from datetime import datetime, timezone
from typing import Optional
from .engine_wiring import wired, wire_failure

logger = logging.getLogger("aria.dual_use_classifier")

# ── Authority + lead-time table ──────────────────────────────────────────────
# Origin country (ISO2 or common name lower) → controlling authority profile.
# Lead times are typical working-day ranges; "*" indicates wide variance.
_AUTHORITY: dict[str, dict] = {
    "uk": {
        "authority": "ECJU (Export Control Joint Unit, DBT)",
        "regime": "UK Strategic Export Control",
        "licences": {
            "military": "SIEL (Standard Individual Export Licence) — military items",
            "dual_use": "SIEL or OGEL — dual-use items",
            "trade": "SITCL / OGTCL — brokering / trade controls (extraterritorial)",
        },
        "lead_time_days": "20-45",
        "portal": "https://www.spire.trade.gov.uk",
    },
    "us": {
        "authority": "DDTC (State, ITAR) and/or BIS (Commerce, EAR)",
        "regime": "ITAR / EAR",
        "licences": {
            "military": "DSP-5 (permanent export) / DSP-83 (NLR + EUC) — ITAR / USML",
            "dual_use": "BIS Form 748P — EAR / CCL",
            "trade": "ITAR brokering registration (DDTC) required separately",
        },
        "lead_time_days": "30-90",
        "portal": "https://www.pmddtc.state.gov / https://www.bis.doc.gov",
    },
    "germany": {
        "authority": "BAFA (Bundesamt für Wirtschaft und Ausfuhrkontrolle)",
        "regime": "AWG / AWV + EU Reg 2021/821",
        "licences": {
            "military": "Einzelausfuhrgenehmigung (single licence) — Kriegswaffenliste / AL Part I",
            "dual_use": "Single / global / general licence — AL Part I.B",
        },
        "lead_time_days": "30-90",
        "portal": "https://www.bafa.de",
    },
    "france": {
        "authority": "SBDU / Douanes (Service des Biens à Double Usage)",
        "regime": "EU Reg 2021/821 + national",
        "licences": {
            "military": "Licence d'exportation de matériel de guerre (LEMG)",
            "dual_use": "Licence individuelle / globale (EGADD portal)",
        },
        "lead_time_days": "30-60",
        "portal": "https://www.entreprises.gouv.fr/sbdu",
    },
    "eu": {
        "authority": "Member-state competent authority (varies)",
        "regime": "EU Reg 2021/821 (dual-use) + Common Military List (military)",
        "licences": {
            "military": "Member-state military export licence",
            "dual_use": "EU general / global / individual export authorisation",
        },
        "lead_time_days": "30-90",
        "portal": "Per member state",
    },
    "uae": {
        "authority": "EDCC (Executive Office for Control & Non-Proliferation)",
        "regime": "Federal Law 13/2007 (Strategic Goods)",
        "licences": {
            "military": "EDCC strategic-goods export licence",
            "dual_use": "EDCC strategic-goods export licence",
        },
        "lead_time_days": "30-60",
        "portal": "https://edcc.gov.ae",
    },
}

# Aliases → canonical key
_ORIGIN_ALIASES: dict[str, str] = {
    "united kingdom": "uk", "great britain": "uk", "gb": "uk", "england": "uk",
    "united states": "us", "usa": "us", "america": "us",
    "deutschland": "germany", "de": "germany",
    "fr": "france",
    "united arab emirates": "uae", "u.a.e.": "uae", "ae": "uae",
    "european union": "eu",
}

# ── Embargo / hard-stop destinations (illustrative — broker must verify
# against live OFAC/OFSI/EU lists; this is a fast-fail check only) ───────────
_EMBARGO_HARD_STOP: set[str] = {
    "north korea", "dprk", "kp",
    "iran", "ir",
    "syria", "sy",
    "russia", "ru",
    "belarus", "by",
    "myanmar", "burma", "mm",
    "cuba", "cu",
    "crimea", "donetsk", "luhansk",
}

# Heightened-scrutiny destinations (not blocked, but expect denial / extra DD)
_ELEVATED_RISK: set[str] = {
    "venezuela", "ve",
    "zimbabwe", "zw",
    "south sudan", "ss",
    "central african republic", "car", "cf",
    "libya", "ly",
    "yemen", "ye",
    "sudan", "sd",
    "afghanistan", "af",
    "lebanon", "lb",
}


def _normalise_country(c: str) -> str:
    return (c or "").strip().lower()


def _origin_profile(origin: str) -> dict:
    key = _normalise_country(origin)
    key = _ORIGIN_ALIASES.get(key, key)
    return _AUTHORITY.get(key) or _AUTHORITY["eu"]


def _embargo_check(destination: str) -> dict:
    d = _normalise_country(destination)
    if d in _EMBARGO_HARD_STOP:
        return {
            "status": "HARD_STOP",
            "detail": f"{destination} is subject to comprehensive embargo or near-total restrictions. "
                      f"Verify current OFAC / OFSI / EU sanctions lists. Most exports prohibited; "
                      f"general licences rarely available.",
        }
    if d in _ELEVATED_RISK:
        return {
            "status": "ELEVATED",
            "detail": f"{destination} is subject to partial restrictions, arms embargo, or "
                      f"heightened scrutiny. Expect extended review and likely denial for "
                      f"military / dual-use items without strong justification.",
        }
    return {"status": "STANDARD", "detail": "No comprehensive embargo identified — verify against live lists."}


def _decide_licence(
    classification: dict,
    origin_profile: dict,
    embargo: dict,
) -> dict:
    """Decide whether a licence is required and which one."""
    has_military = bool(classification.get("wassenaar_ml") or classification.get("usml"))
    has_dual_use = bool(classification.get("ear_ccl"))
    multilateral = classification.get("multilateral") or []

    if embargo["status"] == "HARD_STOP":
        return {
            "licence_required": "PROHIBITED",
            "licence_type": "—",
            "reasoning": "Destination is under comprehensive embargo. Export prohibited regardless of classification.",
        }

    if has_military:
        return {
            "licence_required": "YES",
            "licence_type": origin_profile["licences"].get("military", "Military export licence"),
            "reasoning": (
                "Item matches military list categories — licence mandatory. "
                "Brokering / trade controls may also apply if you are facilitating "
                "between two third countries (extraterritorial reach)."
            ),
        }
    if has_dual_use:
        return {
            "licence_required": "YES",
            "licence_type": origin_profile["licences"].get("dual_use", "Dual-use export licence"),
            "reasoning": (
                "Item matches dual-use control list categories — licence required. "
                "Check whether a general / global licence covers the destination + end-user "
                "before applying for an individual licence."
            ),
        }
    if multilateral:
        return {
            "licence_required": "LIKELY",
            "licence_type": "Specialised regime licence (MTCR / NSG / AG / CWC)",
            "reasoning": (
                "Item triggers a multilateral regime even if not on the standard control list. "
                "Specialist authority engagement required."
            ),
        }
    if embargo["status"] == "ELEVATED":
        return {
            "licence_required": "DEPENDS",
            "licence_type": "Catch-all / end-use control likely",
            "reasoning": (
                "Item appears uncontrolled by classification, but destination is "
                "high-risk — catch-all controls (military end-use, WMD end-use, "
                "human-rights end-use) may still require a licence."
            ),
        }
    return {
        "licence_required": "PROBABLY_NO",
        "licence_type": "—",
        "reasoning": (
            "No control-list match identified. Verify with manufacturer's classification "
            "(ECCN / ML self-classification) and confirm no catch-all triggers apply."
        ),
    }


@wired(module="dual_use_classifier", summary="Dual-use classification")

async def assess(
    item_description: str,
    origin: str,
    destination: str,
    end_user: Optional[str] = None,
    end_use: Optional[str] = None,
) -> dict:
    """Run a full dual-use jurisdictional assessment.

    Returns a structured decision a broker can act on, not just classification codes.
    """
    from . import tech_classifier

    classification = tech_classifier.classify_export_control(item_description or "")
    origin_profile = _origin_profile(origin)
    embargo = _embargo_check(destination)
    decision = _decide_licence(classification, origin_profile, embargo)

    next_actions: list[str] = []
    if decision["licence_required"] == "PROHIBITED":
        next_actions.append("Do not proceed. Document refusal and the embargo basis.")
        next_actions.append("If the deal involves a UK person, brokering offences may apply even without UK transit.")
    elif decision["licence_required"] in ("YES", "LIKELY"):
        next_actions.append(f"Engage {origin_profile['authority']} via {origin_profile['portal']}.")
        next_actions.append("Obtain End-User Certificate (EUC) signed by destination government.")
        next_actions.append("Run sanctions + UBO screen on consignee, end-user, freight forwarder.")
        if end_use:
            next_actions.append(f"Document declared end-use ('{end_use}') and verify against open-source intelligence.")
    elif decision["licence_required"] == "DEPENDS":
        next_actions.append("Request control classification (ECCN / ML number) from the manufacturer.")
        next_actions.append("Apply catch-all assessment — military end-use, WMD end-use, human-rights end-use.")
        next_actions.append(f"If in doubt, file a control-list classification request with {origin_profile['authority']}.")
    else:
        next_actions.append("Retain manufacturer's classification statement on file.")
        next_actions.append("Re-screen at shipment if destination, end-user, or end-use changes.")

    result = {
        "input": {
            "item": item_description,
            "origin": origin,
            "destination": destination,
            "end_user": end_user,
            "end_use": end_use,
        },
        "classification": {
            "wassenaar_ml": classification.get("wassenaar_ml", []),
            "usml": classification.get("usml", []),
            "ear_ccl": classification.get("ear_ccl", []),
            "multilateral": classification.get("multilateral", []),
            "embargo_risks": classification.get("embargo_risks", []),
            "confidence": classification.get("confidence", 0.0),
            "summary": classification.get("raw_summary", ""),
        },
        "jurisdiction": {
            "controlling_authority": origin_profile["authority"],
            "regime": origin_profile["regime"],
            "portal": origin_profile["portal"],
            "lead_time_days": origin_profile["lead_time_days"],
        },
        "embargo_check": embargo,
        "decision": decision,
        "next_actions": next_actions,
        "disclaimer": (
            "Indicative assessment based on open-source regime data. Formal classification "
            "and licence determination must be obtained from the controlling authority. "
            "Embargo / sanctions lists change daily — verify against live OFAC / OFSI / EU "
            "lists before any commitment."
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # ── Brain hook: feed decision into learning tiers ──
    try:
        from . import brain_hook
        success = decision["licence_required"] not in ("PROHIBITED",)
        gap_type = None
        gap_detail = None
        if classification.get("confidence", 0) < 0.4:
            gap_type = "low_confidence_classification"
            gap_detail = f"Item description '{item_description[:80]}' produced no high-confidence regime match."
        await brain_hook.absorb(
            module="dual_use_classifier",
            summary=(
                f"Dual-use assessment: {item_description[:60]} ({origin} → {destination}) "
                f"→ {decision['licence_required']} ({decision['licence_type'][:60]})"
            ),
            entity_name=item_description[:80],
            success=success,
            confidence="ASSESSED",
            gap_type=gap_type,
            gap_detail=gap_detail,
        )
    except Exception as e:
        logger.debug("brain_hook absorb failed (non-fatal): %s", e)

    # ── Audit log: this is a compliance-grade decision; record it with
    # full inputs/outputs so it can be traced back later by a regulator. ──
    try:
        from . import audit_log
        audit_entry = await audit_log.record(
            action="dual_use_assessment",
            actor="dual_use_classifier.assess",
            entity_name=item_description[:80],
            inputs={
                "item": item_description[:200],
                "origin": origin,
                "destination": destination,
                "end_user": end_user,
                "end_use": end_use,
            },
            outputs={
                "wassenaar_ml": classification.get("wassenaar_ml", []),
                "usml": classification.get("usml", []),
                "ear_ccl": classification.get("ear_ccl", []),
                "controlling_authority": origin_profile["authority"],
                "embargo_status": embargo["status"],
            },
            decision=f"{decision['licence_required']}: {decision['licence_type'][:80]}",
            confidence="ASSESSED" if classification.get("confidence", 0) >= 0.5 else "LOW",
            sources=[origin_profile.get("portal", "")],
            notes=decision["reasoning"][:300],
        )
        result["audit_entry_hash"] = audit_entry.get("entry_hash")
    except Exception as e:
        logger.debug("audit log write failed (non-fatal): %s", e)

    return result

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="dual_use_classifier",
                     summary="dual_use_classifier module active",
                     source_id="dual_use_classifier:init")
    except Exception:
        try:
            wire_failure(module="dual_use_classifier", detail="operation failed", gap_type="engine_failure", source="dual_use_classifier")
        except Exception:
            pass
        pass
