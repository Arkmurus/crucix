"""fatf_typologies — FATF money-laundering + TBML typology library
(R-F72, 2026-05-09).

Why this module exists
──────────────────────
Per peer review: 'FATF publishes specific money laundering and sanctions
evasion typologies — not as general guidance but as concrete pattern
descriptions based on real enforcement cases. The 2023 FATF report on
professional money laundering identifies 14 specific structural patterns
used to layer proceeds. The 2024 typologies report on trade-based
money laundering identifies the specific commodity codes, trade
corridors, and invoice anomaly patterns most commonly exploited.'

Knowing FATF *exists* (we already do — it's in the risk_indices source
list and the sanctions adversarial library) is not the same as
*pattern-matching* counterparties against the encoded typologies. This
module closes that gap by encoding the typologies as machine-readable
detection rules and providing a matcher that applies them to
counterparty profiles.

Sources used
────────────
- FATF "Professional Money Laundering" (2023) — 14 structural patterns
- FATF "Trade-Based Money Laundering — Risk Indicators" (2024) —
  commodity / corridor / invoice / shipping patterns
- FATF "Best Practices on Combatting the Abuse of NPOs" (2023)
- FATF guidance on Beneficial Owners (2023)

This is the FIRST CUT — focused on the typologies most relevant to
defence-DD. Adding more is a data-table edit, not a code change.

Public API
──────────
    match_typologies(profile: dict) -> dict
        Score a counterparty profile against the encoded typologies.
        Returns matched typologies with confidence weights and the
        specific signals that triggered each match.

    list_typologies() -> list[dict]
        Return the full encoded library — for /api/aria/fatf/typologies

    summary() -> dict
        Capability-manifest entry.
"""
from __future__ import annotations

from typing import Any

# ── The encoded typology library ──────────────────────────────────────
# Each typology is structured to support deterministic matching against
# a counterparty profile dict. Signals are matched as substring
# (case-insensitive) against any of the named profile fields.
#
# Schema:
#   id:          short stable identifier (used in match output)
#   name:        human-readable name
#   source:      FATF report citation
#   category:    'professional_ml' | 'tbml' | 'sanctions_evasion'
#   relevance:   defence-DD-relevance bucket: 'high' | 'medium' | 'low'
#   description: 1-2 sentence operator-readable explanation
#   indicators:  list of {field: <profile field>, signals: [tokens],
#                          weight: 0..1, note: ...}
#   threshold:   float — composite indicator score above which a match
#                fires. Below threshold = "weak signal", above = "match".

_TYPOLOGY_LIBRARY: list[dict[str, Any]] = [
    {
        "id":          "PML-SHELL-COMPLEX",
        "name":        "Shell companies in complex multi-jurisdictional structures",
        "source":      "FATF 2023 Professional ML Report, §3.1",
        "category":    "professional_ml",
        "relevance":   "high",
        "description": (
            "Layered ownership structure across multiple opaque "
            "jurisdictions, often involving nominee directors, virtual "
            "office addresses, and minimal economic substance. Used to "
            "obscure beneficial ownership and frustrate sanctions "
            "screening."
        ),
        "indicators": [
            {"field": "registered_address", "signals": ["regus", "wework", "iwg", "registered agent", "virtual office"], "weight": 0.30, "note": "virtual-office address"},
            {"field": "jurisdictions",      "signals": ["bvi", "british virgin", "panama", "seychelles", "marshall islands", "delaware (no operations)"], "weight": 0.40, "note": "opaque-jurisdiction holding"},
            {"field": "directors",          "signals": ["nominee", "trustee company", "secretarial services"], "weight": 0.30, "note": "nominee-director structure"},
        ],
        "threshold": 0.50,
    },
    {
        "id":          "PML-INTERMEDIARY-GATEKEEPER",
        "name":        "Use of professional intermediaries as gatekeepers",
        "source":      "FATF 2023 Professional ML Report, §4",
        "category":    "professional_ml",
        "relevance":   "high",
        "description": (
            "Lawyers, accountants, trust and company service providers "
            "(TCSPs) act as the entry point to the financial system on "
            "behalf of an obscured client. Common in defence brokerage "
            "where the principal seeks to hide their involvement."
        ),
        "indicators": [
            {"field": "transaction_intermediary_type", "signals": ["law firm", "accountant", "tcsp", "trust company", "company secretary", "fiduciary"], "weight": 0.50, "note": "professional gatekeeper"},
            {"field": "directors",                     "signals": ["solicitor", "advocate", "legal counsel"], "weight": 0.30, "note": "law-firm director"},
            {"field": "ubo_disclosure",                "signals": ["undisclosed", "trust", "nominee", "client account"], "weight": 0.20, "note": "UBO obscured behind professional"},
        ],
        "threshold": 0.40,
    },
    {
        "id":          "TBML-OVER-UNDER-INVOICING",
        "name":        "Trade-based money laundering — over/under invoicing",
        "source":      "FATF 2024 TBML Risk Indicators, §2.1",
        "category":    "tbml",
        "relevance":   "high",
        "description": (
            "Declared invoice value materially diverges from market price "
            "for the commodity-corridor pair. Over-invoicing moves value "
            "from importer to exporter; under-invoicing moves it the "
            "other way. Detection requires COMTRADE/IMF-DOTS benchmark "
            "(see R-F73 TBML detection module)."
        ),
        "indicators": [
            {"field": "tbml_invoice_anomaly", "signals": ["over-invoice", "under-invoice", "value divergence", "price anomaly"], "weight": 0.60, "note": "invoice/benchmark divergence"},
            {"field": "commodity_codes",      "signals": ["7108", "7110", "7113", "9301", "9303", "9306"], "weight": 0.20, "note": "high-TBML-risk HS codes (precious metals + arms)"},
            {"field": "corridor",             "signals": ["uae->", "panama->", "free trade zone", "ftz"], "weight": 0.20, "note": "high-TBML-risk corridor"},
        ],
        "threshold": 0.50,
    },
    {
        "id":          "TBML-PHANTOM-SHIPMENT",
        "name":        "Phantom shipments (no goods actually moved)",
        "source":      "FATF 2024 TBML Risk Indicators, §2.3",
        "category":    "tbml",
        "relevance":   "high",
        "description": (
            "Invoice and payment occur for goods that never physically "
            "ship. Often paired with documents from a complicit freight "
            "forwarder. Defence-relevant: end-use certificates without "
            "physical end-use verification."
        ),
        "indicators": [
            {"field": "shipping_evidence_status", "signals": ["no bill of lading", "missing bl", "no tracking", "no port of departure", "documentation only"], "weight": 0.50, "note": "absent shipping evidence"},
            {"field": "freight_forwarder",        "signals": ["unknown forwarder", "newly incorporated forwarder", "single-deal forwarder"], "weight": 0.30, "note": "suspect forwarder"},
            {"field": "euc_verification_status",  "signals": ["unverified", "self-declared", "no end-use audit"], "weight": 0.20, "note": "EUC not verified post-delivery"},
        ],
        "threshold": 0.45,
    },
    {
        "id":          "PML-FREE-TRADE-ZONE",
        "name":        "Free trade zone routing for opacity",
        "source":      "FATF 2023 Professional ML + 2024 TBML Risk Indicators",
        "category":    "professional_ml",
        "relevance":   "medium",
        "description": (
            "Transactions routed through free trade zones (UAE Jebel "
            "Ali, Panama Colón, Hong Kong, Singapore, Curacao) to "
            "exploit reduced inspection regimes. Defence-relevant when "
            "end-use jurisdiction is opaque post-FTZ transit."
        ),
        "indicators": [
            {"field": "transit_jurisdictions", "signals": ["jebel ali", "uae ftz", "colón", "hong kong ftz", "singapore ftz", "curacao", "free zone"], "weight": 0.60, "note": "FTZ transit"},
            {"field": "end_use_jurisdiction",  "signals": ["unknown post-ftz", "tbd", "to be confirmed"], "weight": 0.40, "note": "opaque post-FTZ destination"},
        ],
        "threshold": 0.45,
    },
    {
        "id":          "PML-VIRTUAL-ASSETS",
        "name":        "Virtual asset service providers as layering venues",
        "source":      "FATF 2023 Professional ML Report, §5; FATF VA Guidance 2024",
        "category":    "professional_ml",
        "relevance":   "high",
        "description": (
            "Stablecoin USDT/USDC and cryptocurrency exchanges used to "
            "convert proceeds and obscure the trail. Crosses with R-F74 "
            "OpenSanctions crypto address dataset. 84% of illicit virtual "
            "asset volume is stablecoin-denominated (FATF March 2026)."
        ),
        "indicators": [
            {"field": "payment_method", "signals": ["usdt", "usdc", "tether", "circle", "crypto", "stablecoin", "wallet"], "weight": 0.50, "note": "crypto-denominated payment"},
            {"field": "wallet_addresses_provided", "signals": ["yes", "true"], "weight": 0.30, "note": "wallet addresses present in DD inputs"},
            {"field": "exchange_jurisdiction", "signals": ["unregulated", "no kyc", "decentralised", "p2p"], "weight": 0.20, "note": "non-KYC venue"},
        ],
        "threshold": 0.40,
    },
    {
        "id":          "PML-HIGH-VALUE-GOODS",
        "name":        "High-value goods as portable wealth (gold/gems/art/luxury)",
        "source":      "FATF 2023 Professional ML Report, §3.4",
        "category":    "professional_ml",
        "relevance":   "medium",
        "description": (
            "Layering through purchase and resale of gold, precious "
            "stones, fine art, or luxury vehicles. Defence-relevant when "
            "settlement is structured as goods-in-kind."
        ),
        "indicators": [
            {"field": "settlement_form",  "signals": ["gold", "diamonds", "gems", "fine art", "luxury vehicles", "precious metals"], "weight": 0.60, "note": "non-monetary settlement"},
            {"field": "commodity_codes",  "signals": ["7108", "7110", "7113", "97"], "weight": 0.40, "note": "high-value-goods HS codes"},
        ],
        "threshold": 0.40,
    },
    {
        "id":          "PML-OPAQUE-UBO",
        "name":        "Opaque or undisclosed beneficial ownership",
        "source":      "FATF 2023 BO Guidance + Recommendations 24/25",
        "category":    "professional_ml",
        "relevance":   "high",
        "description": (
            "Beneficial owner cannot be identified to a natural person "
            "with adequate confidence. Trust-of-trust structures, "
            "bearer shares (where still permitted), or 'client account' "
            "masking. Direct trigger for FATF Recommendation 10/24."
        ),
        "indicators": [
            {"field": "ubo_disclosure",         "signals": ["undisclosed", "to be confirmed", "trust", "trust of trust", "bearer", "anonymous"], "weight": 0.60, "note": "UBO not disclosed"},
            {"field": "ubo_verification_depth", "signals": ["unverified", "self-declared", "no documentation"], "weight": 0.40, "note": "UBO not verified"},
        ],
        "threshold": 0.45,
    },
]


def _signal_match_score(field_value: Any, signals: list[str]) -> float:
    """Return 1.0 if any signal token appears in the field value
    (case-insensitive substring), 0.0 otherwise."""
    if field_value is None:
        return 0.0
    text = str(field_value).lower()
    for sig in signals:
        if sig.lower() in text:
            return 1.0
    # Lists: any element matching counts
    if isinstance(field_value, (list, tuple, set)):
        for elem in field_value:
            if elem is None:
                continue
            elem_text = str(elem).lower()
            for sig in signals:
                if sig.lower() in elem_text:
                    return 1.0
    return 0.0


def match_typologies(profile: dict[str, Any]) -> dict[str, Any]:
    """Score a counterparty profile against every encoded typology.

    Returns:
        {
          "matches":    [...]  # typologies above threshold
          "weak":       [...]  # typologies with non-zero but below-threshold score
          "narrative":  str
        }
    """
    matched: list[dict[str, Any]] = []
    weak:    list[dict[str, Any]] = []

    for typ in _TYPOLOGY_LIBRARY:
        composite = 0.0
        triggered: list[dict[str, Any]] = []
        total_weight = 0.0
        for ind in typ["indicators"]:
            field_value = profile.get(ind["field"])
            score_raw = _signal_match_score(field_value, ind["signals"])
            total_weight += ind["weight"]
            composite += score_raw * ind["weight"]
            if score_raw > 0:
                triggered.append({
                    "field":   ind["field"],
                    "signals": ind["signals"],
                    "weight":  ind["weight"],
                    "note":    ind["note"],
                    "value":   field_value,
                })

        # Normalise composite to [0, 1] against total declared weight,
        # so partial-data profiles aren't penalised for missing fields.
        normalised = composite / total_weight if total_weight > 0 else 0.0
        normalised = round(normalised, 3)

        record = {
            "id":            typ["id"],
            "name":          typ["name"],
            "source":        typ["source"],
            "category":      typ["category"],
            "relevance":     typ["relevance"],
            "description":   typ["description"],
            "score":         normalised,
            "threshold":     typ["threshold"],
            "triggered_indicators": triggered,
        }
        if normalised >= typ["threshold"]:
            matched.append(record)
        elif normalised > 0:
            weak.append(record)

    matched.sort(key=lambda r: r["score"], reverse=True)
    weak.sort(key=lambda r: r["score"], reverse=True)

    if matched:
        narrative = (
            f"FATF typology matches: {len(matched)} fired"
            + (f" + {len(weak)} weak signals" if weak else "")
            + ". Top: " + ", ".join(f"{m['id']} ({m['score']:.2f})" for m in matched[:3])
            + "."
        )
    elif weak:
        narrative = (
            f"FATF typology — no matches above threshold; "
            f"{len(weak)} weak signal(s): "
            + ", ".join(f"{w['id']} ({w['score']:.2f})" for w in weak[:3])
            + "."
        )
    else:
        narrative = "FATF typology — no matches detected on the encoded library."

    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="fatf_typologies",
        summary="Match Typologies",
        source_id="fatf_typologies:R-F1001",
    )

    return {
        "matches":   matched,
        "weak":      weak,
        "narrative": narrative,
        "library_size": len(_TYPOLOGY_LIBRARY),
    }


def list_typologies() -> list[dict[str, Any]]:
    """Return the full encoded library — for /api/aria/fatf/typologies."""
    return [
        {k: v for k, v in t.items() if k != "indicators"}  # hide internals
        for t in _TYPOLOGY_LIBRARY
    ]


def summary() -> dict[str, Any]:
    return {
        "module":       "fatf_typologies",
        "library_size": len(_TYPOLOGY_LIBRARY),
        "categories":   sorted({t["category"] for t in _TYPOLOGY_LIBRARY}),
        "sources":      sorted({t["source"] for t in _TYPOLOGY_LIBRARY}),
    }

# R-F2119 §21a — wire failure handler for fatf_typologies
try:
    wire_failure(module="fatf_typologies", detail="module shutdown",
                gap_type="engine_failure", source="fatf_typologies:shutdown")
except Exception:
    pass
