# =============================================================================
# ARIA — Layer 5c: Commercial & Legal Coherence Assessment
# aria_service/intel/commercial_coherence.py
#
# Runs between Layer 5 (digital) and Layer 5b (deception scoring). Answers
# three questions that no other DD layer asks:
#
#   1. Corporate coherence        — does the corporate structure fit the
#                                    claimed business activity and deal size?
#   2. Commercial terms coherence — are payment ratios / bonds / liability
#                                    caps consistent with jurisdiction + sector
#                                    norms?
#   3. Legal framework coherence  — does the counterparty acknowledge the
#                                    full licence chain their deal requires?
#
# DATA-DRIVEN — no LLM calls. The constants below encode published norms
# (UK DSPCRs, NATO NSPA, SIMPORTEX, SAMI/EDGE, SSB, SEACE) and legal rules
# (BiH two-stage export chain, ITAR re-transfer, EU dual-use, CAGE/UEI).
#
# SLICE 1 of the Layer 5c build — the broader corpus ingestion (Phase 2)
# and per-domain writer modules (Phase 3) are captured in memory and will
# extend what's encoded here without changing the public function.
# =============================================================================

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from .dd_schema import (
    ARKDDReport,
    CommercialCoherenceSection,
    Finding,
    LayerStatus,
    SectionMeta,
)

logger = logging.getLogger("ARIA.CommercialCoherence")


# =============================================================================
# PAYMENT STRUCTURE NORMS — defence supply contracts by market
# Source: aria_global_legal_framework.html §5 + NSPA public T&Cs +
#         SIMPORTEX practitioner guidance + SSB procurement manual.
# =============================================================================

# iso2 → norms. `advance_norm_max` is the upper bound of *typical* advance
# payment; `advance_red_flag_above` is the threshold beyond which the
# advance is structurally abnormal for legitimate counterparties.
#
# Each `extra_red_flags` entry is {label, keywords} — the assessor checks
# whether ANY keyword (lowercased substring) appears in the combined
# counterparty text. `label` is the human-readable phrase surfaced in the
# report.
PAYMENT_NORMS: dict[str, dict] = {
    "GB": {
        "market": "UK MoD / DSPCRs 2011",
        "advance_norm_min": 0, "advance_norm_max": 10,
        "advance_red_flag_above": 20,
        "perf_bond_norm_pct": 10,
        "perf_bond_mandatory": False,
        "final_on_acceptance_pct_range": (20, 30),
        "extra_red_flags": [
            {"label": "no milestone schedule", "keywords": ["no milestone"]},
        ],
    },
    "NATO": {  # NSPA / alliance procurement — not a jurisdiction but a regime
        "market": "NATO / NSPA",
        "advance_norm_min": 0, "advance_norm_max": 15,
        "advance_red_flag_above": 20,
        "perf_bond_norm_pct": 10,
        "perf_bond_mandatory": True,
        "final_on_acceptance_pct_range": (10, 20),
        "extra_red_flags": [
            {"label": "request to waive performance bond",
             "keywords": ["waive performance bond", "waive the bond"]},
        ],
    },
    "AO": {  # Angola — SIMPORTEX
        "market": "Angola (SIMPORTEX)",
        "advance_norm_min": 30, "advance_norm_max": 40,
        "advance_red_flag_above": 60,
        "perf_bond_norm_pct": 8,
        "perf_bond_mandatory": False,
        "final_on_acceptance_pct_range": (20, 30),
        "extra_red_flags": [
            {"label": "USD cash payment request",
             "keywords": ["usd cash", "cash payment", "cash in usd"]},
            {"label": "non-invoiced side payment",
             "keywords": ["side payment", "non-invoiced", "undocumented payment"]},
        ],
    },
    "AE": {  # UAE / Gulf
        "market": "UAE / Gulf",
        "advance_norm_min": 20, "advance_norm_max": 30,
        "advance_red_flag_above": 50,
        "perf_bond_norm_pct": 8,
        "perf_bond_mandatory": False,
        "final_on_acceptance_pct_range": (10, 20),
        "extra_red_flags": [
            {"label": "request for non-SWIFT transfer",
             "keywords": ["non-swift", "non swift", "not via swift",
                          "alternative to swift"]},
            {"label": "payment via third-country bank with no deal nexus",
             "keywords": ["third-country bank", "third country bank"]},
        ],
    },
    "SA": {  # Saudi — SAMI
        "market": "Saudi Arabia (SAMI)",
        "advance_norm_min": 20, "advance_norm_max": 30,
        "advance_red_flag_above": 50,
        "perf_bond_norm_pct": 10,
        "perf_bond_mandatory": True,
        "final_on_acceptance_pct_range": (10, 20),
        "extra_red_flags": [
            {"label": "request to bypass SAMI localisation",
             "keywords": ["bypass sami", "avoid sami"]},
        ],
    },
    "TR": {  # Turkey — SSB
        "market": "Turkey (SSB)",
        "advance_norm_min": 15, "advance_norm_max": 25,
        "advance_red_flag_above": 40,
        "perf_bond_norm_pct": 10,
        "perf_bond_mandatory": True,  # kesin teminat mandatory
        "final_on_acceptance_pct_range": (5, 15),
        "extra_red_flags": [
            {"label": "no kesin teminat (final guarantee) letter",
             "keywords": ["no kesin teminat", "without kesin teminat"]},
        ],
    },
    "NG": {  # Nigeria
        "market": "Nigeria (MoD)",
        "advance_norm_min": 15, "advance_norm_max": 30,
        "advance_red_flag_above": 50,
        "perf_bond_norm_pct": 10,
        "perf_bond_mandatory": False,
        "final_on_acceptance_pct_range": (10, 25),
        "extra_red_flags": [
            {"label": "request for offshore payment account",
             "keywords": ["offshore payment", "offshore account",
                          "offshore bank account"]},
            {"label": "direct payment to individual",
             "keywords": ["payment to individual", "personal account"]},
        ],
    },
    "PE": {  # Peru — SEACE
        "market": "Peru (SEACE)",
        "advance_norm_min": 0, "advance_norm_max": 30,  # statutory max
        "advance_red_flag_above": 30,
        "perf_bond_norm_pct": 10,
        "perf_bond_mandatory": True,  # 10% statutory minimum
        "final_on_acceptance_pct_range": (10, 30),
        "extra_red_flags": [
            {"label": "request to bypass SEACE platform",
             "keywords": ["bypass seace", "outside seace", "without seace"]},
        ],
    },
    "BR": {  # Brazil — reference baseline
        "market": "Brazil (COMAER / COMDABRA)",
        "advance_norm_min": 10, "advance_norm_max": 30,
        "advance_red_flag_above": 45,
        "perf_bond_norm_pct": 5,
        "perf_bond_mandatory": False,
        "final_on_acceptance_pct_range": (15, 25),
        "extra_red_flags": [],
    },
}


# =============================================================================
# LICENCE CHAIN REQUIREMENTS — by deal shape
# Source: aria_global_legal_framework.html §5 (Licence Chain Completeness).
# Each entry is a deal shape; the orchestrator attempts to match the target
# against one of these and then checks that the counterparty's documented
# text acknowledges every mandatory step.
# =============================================================================

LICENCE_CHAIN_REQUIREMENTS: dict[str, dict] = {
    "uk_broker_foreign_supply_foreign_delivery": {
        "shape": "UK broker, non-UK supply chain, non-UK delivery",
        "steps": [
            {"step": "UK SITCL brokering licence", "authority": "UK ECJU",
             "keywords": ["sitcl", "brokering licence", "ecju", "uk brokering"],
             "timeline_weeks": (6, 12)},
            {"step": "Origin-country export licence", "authority": "origin state",
             "keywords": ["export licence", "export license", "origin country"],
             "timeline_weeks": (4, 12)},
            {"step": "End-user certificate (EUC)", "authority": "recipient state",
             "keywords": ["euc", "end-user certificate", "end user certificate"],
             "timeline_weeks": (2, 6)},
        ],
    },
    "uk_supply_bih_intermediary_third_country": {
        "shape": "UK supply, BiH intermediary, third-country delivery",
        "steps": [
            {"step": "UK export licence (SIEL/OIEL)", "authority": "UK ECJU",
             "keywords": ["siel", "oiel", "uk export licence"],
             "timeline_weeks": (4, 12)},
            {"step": "BiH MoFTER re-export authorisation (TWO-STAGE)", "authority": "BiH Ministry of Foreign Trade",
             "keywords": ["mofter", "bih re-export", "bosnia re-export",
                          "re-export authorisation", "two-stage"],
             "timeline_weeks": (8, 16)},
            {"step": "Third-country EUC", "authority": "recipient state",
             "keywords": ["euc", "end-user certificate", "third-country euc"],
             "timeline_weeks": (2, 6)},
        ],
    },
    "us_origin_non_us_buyer": {
        "shape": "US-origin equipment, non-US buyer",
        "steps": [
            {"step": "DSP-5 permanent export licence or TAA", "authority": "US DDTC",
             "keywords": ["dsp-5", "dsp5", "taa", "technical assistance agreement",
                          "ddtc", "itar export licence"],
             "timeline_weeks": (12, 26)},
            {"step": "ITAR EUC + re-transfer prohibition", "authority": "US DDTC",
             "keywords": ["itar", "re-transfer prohibition", "re-transfer"],
             "timeline_weeks": (4, 8)},
            {"step": "US government re-transfer approval (if downstream)", "authority": "US DDTC",
             "keywords": ["re-transfer approval", "us government approval"],
             "timeline_weeks": (8, 16)},
        ],
    },
    "eu_dual_use_third_country": {
        "shape": "EU dual-use, third-country delivery",
        "steps": [
            {"step": "EU dual-use export licence", "authority": "EU member state licensing authority",
             "keywords": ["dual-use licence", "dual use licence",
                          "eu dual-use", "regulation 821", "regulation 2021/821"],
             "timeline_weeks": (4, 8)},
            {"step": "End-use declaration", "authority": "recipient entity",
             "keywords": ["end-use declaration", "end use declaration"],
             "timeline_weeks": (1, 4)},
            {"step": "No-re-export assurance", "authority": "recipient state",
             "keywords": ["no re-export", "no-re-export", "no reexport"],
             "timeline_weeks": (2, 4)},
        ],
    },
    "us_sam_gov_registration": {
        "shape": "SAM.gov registration (US procurement)",
        "steps": [
            {"step": "NATO CAGE code", "authority": "national CAGE bureau",
             "keywords": ["cage code", "ncage"],
             "timeline_weeks": (2, 4)},
            {"step": "UEI registration via SAM.gov", "authority": "US GSA",
             "keywords": ["uei", "unique entity id", "unique entity identifier"],
             "timeline_weeks": (2, 4)},
            {"step": "US banking relationship for ACH payments", "authority": "US bank",
             "keywords": ["ach", "us banking relationship", "us bank account"],
             "timeline_weeks": (2, 4)},
        ],
    },
}


# =============================================================================
# CORPORATE RED FLAGS — universal
# Source: aria_global_legal_framework.html §5 Corporate Structure Flags
# =============================================================================

# Number of days between incorporation and the deal attempt; below this we
# treat the entity as a just-stood-up vehicle, which is a structural flag.
NEW_ENTITY_DAYS_THRESHOLD = 90

# If claimed turnover / transaction value exceeds this multiple of nominal
# share capital, flag as structurally incoherent.
CAPITAL_TO_TXN_RED_MULTIPLE = 10_000  # £1 capital vs £10k+ deal → suspicious

# Address pattern → red flag for industrial / manufacturing claims.
_FORMATION_AGENT_KEYWORDS = [
    "formation agent", "virtual office", "mailbox", "mail-drop",
    "serviced office", "regus", "wework", "business centre",
    "c/o", "care of", "po box", "p.o. box",
]

# SIC/NACE/ISIC codes that are incongruent with defence/security activity.
_NON_DEFENCE_ACTIVITY_KEYWORDS = [
    "cleaning", "catering", "hairdress", "florist", "retail clothing",
    "taxi", "restaurant", "café", "cafe", "beauty salon",
]

# Keywords indicating defence/security activity (for sector-match check).
_DEFENCE_ACTIVITY_KEYWORDS = [
    "defence", "defense", "military", "weapon", "ammunition", "firearm",
    "armament", "security equipment", "ballistic", "armour", "armor",
    "aerospace", "tactical", "law enforcement",
]


# =============================================================================
# JURISDICTION-SPECIFIC CORPORATE RULES
# =============================================================================

def _check_bearer_shares_uk_eu(iso2: str, claim_text: str) -> Optional[dict]:
    """UK (2015) and EU member states have abolished bearer shares. Any
    counterparty claiming a bearer share structure for a UK/EU entity is
    either misinformed or fraudulent."""
    _eu_uk = {
        "GB", "IE", "FR", "DE", "IT", "ES", "PT", "NL", "BE", "LU", "AT",
        "DK", "SE", "FI", "PL", "CZ", "SK", "HU", "RO", "BG", "GR", "HR",
        "SI", "LT", "LV", "EE", "MT", "CY",
    }
    if iso2.upper() not in _eu_uk:
        return None
    if re.search(r"\bbearer\s+share", claim_text, re.IGNORECASE):
        return {
            "kind": "bearer_shares_in_abolished_jurisdiction",
            "severity": "red",
            "detail": (
                f"Counterparty text references bearer shares in {iso2} — "
                f"bearer shares have been abolished across the UK (2015) "
                f"and all EU member states. Either legally incorrect or "
                f"pre-reform instruments that should have been converted."
            ),
        }
    return None


def _check_uae_free_zone_onshore_claim(iso2: str, claim_text: str) -> Optional[dict]:
    """UAE free zone entities cannot legally operate onshore without a
    mainland licence. A free-zone entity claiming UAE-wide trading rights
    is misrepresenting its legal capacity."""
    if iso2.upper() != "AE":
        return None
    lc = claim_text.lower()
    if "free zone" in lc and (
        "uae-wide" in lc or "nationwide" in lc
        or "mainland" in lc and "licence" not in lc and "license" not in lc
    ):
        return {
            "kind": "uae_free_zone_claiming_onshore",
            "severity": "red",
            "detail": (
                "Free zone entity claiming UAE-wide / mainland trading "
                "rights without an explicit mainland licence. Free zone "
                "companies cannot legally trade onshore UAE without one."
            ),
        }
    return None


def _check_angola_sarl_shareholder_cap(iso2: str, claim_text: str,
                                       shareholder_count: int = 0) -> Optional[dict]:
    """Angola SARL (Sociedade por Quotas) cannot have more than 30
    shareholders under the Lei das Sociedades Comerciais. An SARL claiming
    50+ investors is structurally incoherent — either the form is wrong
    or the disclosure is false."""
    if iso2.upper() != "AO":
        return None
    lc = claim_text.lower()
    if ("sarl" in lc or "sociedade por quotas" in lc) and shareholder_count > 30:
        return {
            "kind": "angola_sarl_exceeds_shareholder_cap",
            "severity": "red",
            "detail": (
                f"Angolan SARL claims {shareholder_count} shareholders — "
                f"SARL (Sociedade por Quotas) is capped at 30 shareholders "
                f"under the Lei das Sociedades Comerciais. Structure is "
                f"incompatible with the declared form."
            ),
        }
    return None


def _check_bih_entity_level(iso2: str, claim_text: str, declared_address: str) -> Optional[dict]:
    """Bosnia has two separate entity-level jurisdictions (Republika Srpska
    and Federation of BiH) with distinct commercial registries. A BiH
    entity that does not specify which is structurally incomplete."""
    if iso2.upper() != "BA":
        return None
    combined = f"{claim_text} {declared_address}".lower()
    _rs_markers = ["republika srpska", " rs ", "banja luka", "bijeljina", "bratunac"]
    _fbih_markers = ["federation of bih", "federacija", "sarajevo", "tuzla", "mostar", "zenica"]
    has_rs = any(m in combined for m in _rs_markers)
    has_fbih = any(m in combined for m in _fbih_markers)
    if not has_rs and not has_fbih:
        return {
            "kind": "bih_entity_level_unspecified",
            "severity": "amber",
            "detail": (
                "BiH entity does not identify its entity-level jurisdiction "
                "(Republika Srpska vs Federation of BiH). These are "
                "separate commercial registries with distinct legal "
                "frameworks — operator should confirm which applies."
            ),
        }
    return None


# =============================================================================
# PUBLIC API
# =============================================================================

def _extract_advance_payment_pct(text: str) -> Optional[float]:
    """Find the largest plausible advance-payment percentage in the text.
    Looks for patterns like '70% advance', 'advance payment of 60%',
    'down payment 50%', etc."""
    if not text:
        return None
    patterns = [
        r"(\d{1,3})\s*%\s*(?:advance|down[-\s]?payment|upfront|up-front|deposit)",
        r"(?:advance|down[-\s]?payment|upfront|up-front|deposit)[^0-9\n]{0,40}?(\d{1,3})\s*%",
    ]
    best: Optional[float] = None
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            try:
                pct = float(m.group(1))
                if 0 <= pct <= 100 and (best is None or pct > best):
                    best = pct
            except (ValueError, IndexError):
                continue
    return best


def _counterparty_text(target: dict) -> str:
    """Pull every narrative field off the target into a single combined
    string so keyword/percentage matchers have everything to work with."""
    parts = []
    for key in (
        "counterparty_text", "message_text", "communication", "claim",
        "capability_statement", "narrative", "description", "proposal",
        "proposal_text", "rfq_text", "sow_text",
    ):
        v = target.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    return "\n".join(parts)


def _infer_payment_market(target: dict, identity_iso2: str) -> str:
    """Which payment-norms row applies? Prefer explicit delivery country;
    otherwise fall back to the identity jurisdiction. 'NATO' if the target
    explicitly flags an alliance procurement."""
    delivery = (target.get("delivery_country") or target.get("end_user_country") or "").upper()
    if target.get("nato_procurement") is True or "nspa" in (_counterparty_text(target).lower()):
        return "NATO"
    if delivery and delivery in PAYMENT_NORMS:
        return delivery
    if identity_iso2 and identity_iso2.upper() in PAYMENT_NORMS:
        return identity_iso2.upper()
    return ""


def _match_licence_chain_shape(target: dict, identity_iso2: str) -> Optional[str]:
    """Match the deal to one of the LICENCE_CHAIN_REQUIREMENTS shapes.
    Caller can override via `target['licence_chain_shape']`."""
    override = target.get("licence_chain_shape")
    if override and override in LICENCE_CHAIN_REQUIREMENTS:
        return override

    product_desc = (target.get("product_description") or "").lower()
    delivery = (target.get("delivery_country") or "").upper()
    origin = (target.get("origin_country") or "").upper()
    iso2 = (identity_iso2 or "").upper()
    text = _counterparty_text(target).lower()

    # US-origin, non-US buyer
    if origin == "US" and delivery and delivery != "US":
        return "us_origin_non_us_buyer"
    if ("itar" in text or "dsp-5" in text or "taa" in text) and delivery and delivery != "US":
        return "us_origin_non_us_buyer"

    # BiH intermediary — the common Arkmurus pattern
    if iso2 == "BA" or "mofter" in text or "republika srpska" in text:
        return "uk_supply_bih_intermediary_third_country"

    # UK broker — foreign supply, foreign delivery
    if iso2 == "GB" and origin and origin != "GB" and delivery and delivery != "GB":
        return "uk_broker_foreign_supply_foreign_delivery"

    # SAM.gov — explicit mention
    if "sam.gov" in text or "uei" in text or "cage code" in text:
        return "us_sam_gov_registration"

    # EU dual-use
    _eu = {"FR", "DE", "IT", "ES", "NL", "BE", "PL", "SE", "PT", "IE"}
    if (origin in _eu or iso2 in _eu) and ("dual-use" in text or "dual use" in text):
        return "eu_dual_use_third_country"

    return None


def _assess_payment_structure(target: dict, market: str) -> tuple[list[dict], float]:
    """Compare any payment terms in the target text against the norms for
    the inferred market. Returns (anomalies, score_deduction)."""
    if not market or market not in PAYMENT_NORMS:
        return [], 0.0
    norms = PAYMENT_NORMS[market]
    text = _counterparty_text(target)
    anomalies: list[dict] = []
    deduction = 0.0

    advance_pct = _extract_advance_payment_pct(text)
    if advance_pct is not None:
        if advance_pct > norms["advance_red_flag_above"]:
            anomalies.append({
                "market": norms["market"],
                "kind": "advance_payment_above_red_flag_threshold",
                "claimed": f"{advance_pct:.0f}%",
                "norm_range": f"{norms['advance_norm_min']}-{norms['advance_norm_max']}%",
                "severity": "red",
            })
            deduction += 0.25
        elif advance_pct > norms["advance_norm_max"]:
            anomalies.append({
                "market": norms["market"],
                "kind": "advance_payment_above_market_norm",
                "claimed": f"{advance_pct:.0f}%",
                "norm_range": f"{norms['advance_norm_min']}-{norms['advance_norm_max']}%",
                "severity": "amber",
            })
            deduction += 0.10

    if norms.get("perf_bond_mandatory"):
        lc = text.lower()
        mentions_bond = any(k in lc for k in (
            "performance bond", "perf bond", "kesin teminat",
            "bid bond", "guarantee letter", "bank guarantee",
        ))
        requests_waiver = any(k in lc for k in (
            "waive performance bond", "waive the bond", "no performance bond",
            "without performance bond", "bond is not required",
        ))
        if requests_waiver:
            anomalies.append({
                "market": norms["market"],
                "kind": "performance_bond_waiver_request",
                "claimed": "waiver",
                "norm_range": f"{norms['perf_bond_norm_pct']}% (mandatory)",
                "severity": "red",
            })
            deduction += 0.20
        elif len(text) > 300 and not mentions_bond:
            # Only flag absence when we have enough text to reasonably
            # expect the counterparty to have addressed bonding.
            anomalies.append({
                "market": norms["market"],
                "kind": "performance_bond_absent_but_mandatory",
                "claimed": "not mentioned",
                "norm_range": f"{norms['perf_bond_norm_pct']}% (mandatory)",
                "severity": "amber",
            })
            deduction += 0.05

    lc_text = text.lower()
    for extra in norms.get("extra_red_flags", []):
        # Support both legacy string entries and the new {label, keywords} shape.
        if isinstance(extra, dict):
            label = extra.get("label", "")
            keywords = [k.lower() for k in extra.get("keywords", []) if k]
        else:
            label = str(extra)
            keywords = [label.lower()]
        if keywords and any(k in lc_text for k in keywords):
            anomalies.append({
                "market": norms["market"],
                "kind": "jurisdiction_specific_red_flag",
                "claimed": label,
                "norm_range": "not accepted",
                "severity": "red",
            })
            deduction += 0.15

    return anomalies, deduction


def _assess_corporate_structure(identity, target: dict) -> tuple[list[dict], list[str], float]:
    """Universal + jurisdiction-specific corporate flags.

    Returns (corporate_anomalies, jurisdiction_flags, score_deduction).
    """
    anomalies: list[dict] = []
    jflags: list[str] = []
    deduction = 0.0

    iso2 = (identity.jurisdiction_iso2 or "").upper()
    claim_text = _counterparty_text(target)

    # 1. Incorporation-timing flag (<90 days before deal attempt)
    inc_str = identity.incorporation_date
    if inc_str:
        try:
            inc_date = datetime.fromisoformat(str(inc_str)[:10].replace("Z", ""))
            age_days = (datetime.now(timezone.utc).replace(tzinfo=None) - inc_date).days
            if 0 <= age_days < NEW_ENTITY_DAYS_THRESHOLD:
                anomalies.append({
                    "kind": "freshly_incorporated_entity",
                    "severity": "red",
                    "detail": (
                        f"Entity incorporated {age_days} days before this deal "
                        f"attempt (threshold {NEW_ENTITY_DAYS_THRESHOLD}d). "
                        f"Legitimate defence counterparties have track records."
                    ),
                })
                deduction += 0.20
        except (ValueError, TypeError):
            pass

    # 2. Nominal capital vs transaction-value mismatch
    txn_value = target.get("transaction_value_usd") or target.get("contract_value_usd") or 0
    try:
        txn_value = float(txn_value)
    except (ValueError, TypeError):
        txn_value = 0
    share_cap = 0
    for sh in (identity.shareholders or []):
        if isinstance(sh, dict):
            try:
                share_cap += float(sh.get("capital") or sh.get("value") or 0)
            except (ValueError, TypeError):
                pass
    if txn_value > 100_000 and 0 < share_cap < txn_value / CAPITAL_TO_TXN_RED_MULTIPLE:
        anomalies.append({
            "kind": "nominal_capital_mismatch",
            "severity": "amber",
            "detail": (
                f"Declared share capital {share_cap:.2f} vs proposed "
                f"transaction {txn_value:.0f} USD — capital structure does "
                f"not match deal scale (ratio 1:{CAPITAL_TO_TXN_RED_MULTIPLE:,})."
            ),
        })
        deduction += 0.10

    # 3. Director concentration — single person is director AND sole shareholder
    directors = identity.directors or []
    shareholders = identity.shareholders or []
    if len(directors) == 1 and len(shareholders) == 1:
        d_name = (directors[0].get("name") if isinstance(directors[0], dict) else "") or ""
        s_name = (shareholders[0].get("name") if isinstance(shareholders[0], dict) else "") or ""
        if d_name and s_name and d_name.strip().lower() == s_name.strip().lower() and txn_value > 500_000:
            anomalies.append({
                "kind": "director_concentration",
                "severity": "amber",
                "detail": (
                    f"Single individual ({d_name}) is sole director and "
                    f"sole shareholder on a deal > USD 500k. Legitimate "
                    f"businesses at this scale have distributed governance."
                ),
            })
            deduction += 0.10

    # 4. Address anomaly — industrial claim vs formation-agent address
    declared_activity = (identity.declared_activity or "").lower()
    reg_address = (identity.registered_address or "").lower()
    is_defence_claim = any(k in declared_activity for k in _DEFENCE_ACTIVITY_KEYWORDS)
    is_industrial_claim = any(k in declared_activity for k in (
        "manufactur", "industrial", "production", "assembly", "warehouse",
    )) or is_defence_claim
    is_formation_agent = any(k in reg_address for k in _FORMATION_AGENT_KEYWORDS)
    if is_industrial_claim and is_formation_agent and reg_address:
        anomalies.append({
            "kind": "industrial_claim_with_formation_agent_address",
            "severity": "amber",
            "detail": (
                f"Declared activity ({identity.declared_activity[:60]}) "
                f"implies industrial/manufacturing operations but registered "
                f"address is a formation agent / virtual office. "
                f"Operators should verify physical premises independently."
            ),
        })
        deduction += 0.08

    # 5. Sector mismatch — non-defence SIC claiming defence deal
    product_desc = (target.get("product_description") or "").lower()
    is_defence_deal = any(k in product_desc for k in _DEFENCE_ACTIVITY_KEYWORDS)
    activity_is_non_defence = any(k in declared_activity for k in _NON_DEFENCE_ACTIVITY_KEYWORDS)
    if is_defence_deal and activity_is_non_defence:
        anomalies.append({
            "kind": "sector_mismatch",
            "severity": "red",
            "detail": (
                f"Entity's declared activity ({identity.declared_activity[:60]}) "
                f"is inconsistent with the defence/security deal scope. "
                f"Classic fronting pattern."
            ),
        })
        deduction += 0.20

    # 6. Jurisdiction-specific rules
    shareholder_count = len(shareholders)
    for checker, kwargs in [
        (_check_bearer_shares_uk_eu,       {"iso2": iso2, "claim_text": claim_text}),
        (_check_uae_free_zone_onshore_claim, {"iso2": iso2, "claim_text": claim_text}),
        (_check_angola_sarl_shareholder_cap, {"iso2": iso2, "claim_text": claim_text,
                                              "shareholder_count": shareholder_count}),
        (_check_bih_entity_level,          {"iso2": iso2, "claim_text": claim_text,
                                            "declared_address": identity.registered_address or ""}),
    ]:
        hit = checker(**kwargs)
        if hit:
            anomalies.append(hit)
            jflags.append(hit["detail"])
            deduction += 0.15 if hit["severity"] == "red" else 0.05

    return anomalies, jflags, deduction


def _assess_licence_chain(target: dict, identity_iso2: str) -> tuple[list[dict], float]:
    """Which licence chain does this deal shape require, and does the
    counterparty acknowledge every mandatory step?"""
    shape_key = _match_licence_chain_shape(target, identity_iso2)
    if not shape_key:
        return [], 0.0

    shape_def = LICENCE_CHAIN_REQUIREMENTS[shape_key]
    text = _counterparty_text(target).lower()
    gaps: list[dict] = []
    deduction = 0.0

    # If there's no meaningful counterparty text at all, don't penalise.
    if len(text) < 80:
        return [], 0.0

    for step in shape_def["steps"]:
        acknowledged = any(kw in text for kw in step["keywords"])
        if not acknowledged:
            gaps.append({
                "deal_shape": shape_def["shape"],
                "missing_step": step["step"],
                "authority": step["authority"],
                "typical_timeline_weeks": f"{step['timeline_weeks'][0]}-{step['timeline_weeks'][1]}",
            })
            deduction += 0.12

    return gaps, deduction


def _assess_offset_claims(target: dict, identity_iso2: str) -> tuple[list[dict], float]:
    """Check counterparty's offset claims against the actual regime.

    Re-uses OFFSET_REGIMES from the procurement writer so Phase 1 of the
    spec (re-route existing knowledge into DD) is achieved in-situ.
    """
    try:
        from ..writers.procurement_paper_writer import OFFSET_REGIMES
    except Exception:
        return [], 0.0

    delivery = (target.get("delivery_country") or identity_iso2 or "").lower()
    # Map iso2 → offset key
    iso2_to_key = {
        "AO": "angola", "SA": "saudi_arabia", "AE": "uae",
        "IN": "india", "TR": "turkey",
    }
    key = iso2_to_key.get(delivery.upper(), delivery)
    regime = OFFSET_REGIMES.get(key)
    if not regime:
        return [], 0.0

    txn_value = target.get("transaction_value_usd") or target.get("contract_value_usd") or 0
    try:
        txn_value = float(txn_value)
    except (ValueError, TypeError):
        txn_value = 0

    text = _counterparty_text(target).lower()
    issues: list[dict] = []
    deduction = 0.0

    # Claim 1: counterparty says offset does not apply when threshold is exceeded.
    threshold = regime.get("threshold_usd", 0)
    minimum_pct = regime.get("minimum_percentage", 0)
    exempt_claims = [
        "offset does not apply", "no offset required", "offset exemption",
        "exempt from offset", "below offset threshold",
    ]
    if txn_value >= threshold and any(c in text for c in exempt_claims):
        issues.append({
            "regime": regime.get("name", key),
            "issue": "offset_exemption_claimed_despite_threshold_met",
            "detail": (
                f"Counterparty claims offset exemption but deal value "
                f"(USD {txn_value:,.0f}) meets the regime threshold "
                f"(USD {threshold:,.0f}). {regime.get('authority','')}."
            ),
        })
        deduction += 0.20

    # Claim 2: counterparty claims offset compliance via activities not in
    # the qualifying list. We flag generic claims with no specifics.
    if txn_value >= threshold:
        mentions_offset = "offset" in text or "localisation" in text or "localization" in text
        claims_compliance = any(c in text for c in (
            "offset compliant", "offset compliance", "offset fulfilled",
            "offset obligation met", "100% offset", "compliant with offset",
        ))
        has_specific_activity = any(
            kw in text for kw in (
                "technology transfer", "local manufacturing", "joint venture",
                "local employment", "r&d", "mro", "local procurement",
                "indigenous content",
            )
        )
        if claims_compliance and not has_specific_activity:
            issues.append({
                "regime": regime.get("name", key),
                "issue": "offset_compliance_claimed_without_qualifying_activity",
                "detail": (
                    f"Counterparty asserts offset compliance for a deal "
                    f"under {regime.get('name','')} ({minimum_pct}% minimum) "
                    f"but cites no qualifying activity. Qualifying list: "
                    f"{', '.join(regime.get('qualifying_activities', [])[:3])}..."
                ),
            })
            deduction += 0.10

    return issues, deduction


def _anomaly_title(a: dict, fallback: str) -> str:
    """Short, human-readable title for a flat anomaly dict.

    Payment and offset anomalies don't carry a stand-alone sentence, so
    build one from their structured fields. Corporate anomalies already
    ship a ``detail`` sentence — use its first clause as the title.
    """
    kind = a.get("kind") or a.get("issue") or ""
    market = a.get("market", "")
    regime = a.get("regime", "")
    if kind == "advance_payment_above_red_flag_threshold":
        return (
            f"Advance payment {a.get('claimed','?')} exceeds "
            f"{market} red-flag threshold (norm {a.get('norm_range','?')})"
        )
    if kind == "advance_payment_above_market_norm":
        return (
            f"Advance payment {a.get('claimed','?')} above {market} market "
            f"norm ({a.get('norm_range','?')})"
        )
    if kind == "jurisdiction_specific_red_flag":
        return f"{market} red flag — {a.get('claimed','')}"
    if kind == "performance_bond_waiver_request":
        return f"{market} — request to waive mandatory performance bond"
    if kind == "performance_bond_absent_but_mandatory":
        return f"{market} — mandatory performance bond not mentioned"
    if kind == "licence_chain_gap":
        return a.get("detail", "Licence-chain step not acknowledged")
    if a.get("issue"):
        # Offset issues
        return f"{regime}: {a['issue'].replace('_', ' ')}"
    if a.get("detail"):
        # Corporate anomalies carry a full sentence — take first clause
        det = a["detail"]
        first = det.split(".")[0]
        return first[:160] + ("…" if len(first) > 160 else "")
    return fallback


def _anomaly_detail(a: dict) -> str:
    """Detail string — prefer the anomaly's own ``detail`` sentence; else
    synthesise one from structured fields (never dump the raw dict)."""
    if a.get("detail"):
        return str(a["detail"])
    kind = a.get("kind") or a.get("issue") or "anomaly"
    parts = [kind.replace("_", " ")]
    if a.get("market"):
        parts.append(f"market={a['market']}")
    if a.get("regime"):
        parts.append(f"regime={a['regime']}")
    if a.get("claimed") is not None:
        parts.append(f"claimed={a['claimed']}")
    if a.get("norm_range"):
        parts.append(f"norm={a['norm_range']}")
    return " · ".join(parts)


def _classify_tier(score: float) -> str:
    if score >= 0.80:
        return "GREEN"
    if score >= 0.55:
        return "ELEVATED"
    return "HIGH"


def _build_summary(
    section: CommercialCoherenceSection, entity_name: str, txn_value: float,
) -> str:
    n_total = (
        len(section.anomalies) + len(section.licence_chain_gaps)
        + len(section.payment_anomalies) + len(section.corporate_anomalies)
        + len(section.offset_issues) + len(section.jurisdiction_flags)
    )
    if n_total == 0:
        return (
            f"Commercial coherence CLEAN — no structural anomalies, "
            f"licence-chain gaps, payment deviations, or offset issues "
            f"detected on {entity_name or 'subject'}."
        )
    parts = []
    if section.corporate_anomalies:
        parts.append(f"{len(section.corporate_anomalies)} corporate anomaly(ies)")
    if section.payment_anomalies:
        parts.append(f"{len(section.payment_anomalies)} payment deviation(s)")
    if section.licence_chain_gaps:
        parts.append(f"{len(section.licence_chain_gaps)} licence-chain gap(s)")
    if section.offset_issues:
        parts.append(f"{len(section.offset_issues)} offset issue(s)")
    if section.jurisdiction_flags:
        parts.append(f"{len(section.jurisdiction_flags)} jurisdiction flag(s)")
    return (
        f"Commercial coherence {section.tier} (score {section.coherence_score:.2f}) "
        f"— {', '.join(parts)} on {entity_name or 'subject'}."
    )


async def assess_commercial_coherence(
    target: dict,
    report: ARKDDReport,
) -> CommercialCoherenceSection:
    """Run Layer 5c. Always populates report.commercial_coherence; never
    raises — it's an observer, not a gate. Compose with report.digital +
    report.identity from the prior layers."""
    t0 = time.time()
    section = CommercialCoherenceSection()
    section.meta.started_at = datetime.now(timezone.utc).isoformat()

    try:
        identity = report.identity
        identity_iso2 = (identity.jurisdiction_iso2 or "").upper()

        # 1. Corporate structure (always — cheapest check)
        corp_anomalies, jflags, corp_deduction = _assess_corporate_structure(identity, target)
        section.corporate_anomalies = corp_anomalies
        section.jurisdiction_flags = jflags

        # 2. Payment structure
        market = _infer_payment_market(target, identity_iso2)
        pay_anomalies, pay_deduction = _assess_payment_structure(target, market)
        section.payment_anomalies = pay_anomalies

        # 3. Licence chain
        licence_gaps, lic_deduction = _assess_licence_chain(target, identity_iso2)
        section.licence_chain_gaps = licence_gaps

        # 4. Offset claims
        offset_issues, off_deduction = _assess_offset_claims(target, identity_iso2)
        section.offset_issues = offset_issues

        # 5. Roll up into a single coherence score (clamped [0, 1])
        total_deduction = corp_deduction + pay_deduction + lic_deduction + off_deduction
        section.coherence_score = max(0.0, min(1.0, 1.0 - total_deduction))
        section.tier = _classify_tier(section.coherence_score)

        # 6. Umbrella anomaly list (flat — used by deception feed + BLUF)
        section.anomalies = (
            corp_anomalies + pay_anomalies + offset_issues
            + [{"kind": "licence_chain_gap", "severity": "amber",
                "detail": f"Missing: {g['missing_step']} ({g['authority']})"}
               for g in licence_gaps]
        )

        # 7. Emit Findings so synthesis + BLUF rendering can weight them.
        severity_title = {
            "red": "Layer 5c RED — commercial coherence anomaly",
            "amber": "Layer 5c AMBER — commercial coherence deviation",
        }
        for a in section.anomalies:
            sev = a.get("severity", "amber")
            section.findings.append(Finding(
                severity=sev,
                title=_anomaly_title(a, severity_title.get(sev, "Layer 5c observation")),
                detail=_anomaly_detail(a),
                source="commercial_coherence",
                confidence="ASSESSED",
            ))

        txn_value = target.get("transaction_value_usd") or target.get("contract_value_usd") or 0
        try:
            txn_value = float(txn_value)
        except (ValueError, TypeError):
            txn_value = 0
        section.assessment_summary = _build_summary(
            section, identity.entity_name, txn_value
        )
        section.meta.status = LayerStatus.OK.value

    except Exception as e:
        section.meta.status = LayerStatus.ERROR.value
        section.meta.error = str(e)[:200]
        logger.warning("commercial_coherence assessment failed (non-fatal): %s", e)

    section.meta.duration_ms = int((time.time() - t0) * 1000)
    report.commercial_coherence = section

    # Brain hook — fire-and-forget learning signal. Coherence issues feed
    # the predictor so future DD runs on similar entities surface warnings.
    try:
        from . import brain_hook as _bh
        if section.anomalies or section.licence_chain_gaps:
            _top = "; ".join(
                (a.get("kind") or "anomaly") for a in section.anomalies[:3]
            ) or "clean"
            await _bh.absorb(
                module="commercial_coherence",
                summary=(
                    f"Coherence {section.tier} (score {section.coherence_score:.2f}) "
                    f"on {report.identity.entity_name or '?'}: {_top}"
                ),
                detail=section.assessment_summary,
                entity_name=report.identity.entity_name or "",
                success=True,
                gap_type=("commercial_coherence_elevated"
                          if section.tier != "GREEN" else None),
                gap_detail=(section.assessment_summary
                            if section.tier != "GREEN" else None),
                confidence="ASSESSED",
            )
    except Exception:
        pass

    return section


def anomaly_text_for_deception(section: CommercialCoherenceSection) -> str:
    """Render the Layer 5c findings as a short narrative so deception
    scoring (Layer 5b) can analyse the combined picture rather than
    picking up coherence gaps separately."""
    if not section or not (
        section.anomalies or section.licence_chain_gaps or section.jurisdiction_flags
    ):
        return ""
    parts = [
        f"Commercial coherence assessment — score {section.coherence_score:.2f} "
        f"({section.tier}).",
    ]
    for a in section.anomalies[:6]:
        parts.append(f"- {a.get('kind','anomaly')}: {a.get('detail','')}"[:300])
    for g in section.licence_chain_gaps[:4]:
        parts.append(
            f"- licence-chain gap: {g.get('missing_step','')} "
            f"({g.get('authority','')})"
        )
    for j in section.jurisdiction_flags[:3]:
        parts.append(f"- jurisdiction: {j[:280]}")
    return "\n".join(parts)
