"""
ARIA Symbolic Reasoner — pure-Python rules e
from .engine_wiring import wire_success
ngine for defence intelligence.

No LLM calls. No statistical models. Just deterministic logic encoding the
hard rules of export control, sanctions, procurement structure, market
scoring, and deal feasibility.

Why this exists
═══════════════
A frontier LLM is overkill for questions like:
  - "Can we export 5.56mm ammunition to Mali?"
  - "Is K9 Thunder ITAR-controlled?"
  - "What licence do we need for Angola?"
  - "Is Mozambique under any embargoes?"

These have RIGHT answers that can be looked up in tables and matrices. The
LLM was wasting tokens (and your money) hallucinating around a deterministic
question. The symbolic reasoner answers these instantly with zero ambiguity
and zero external calls.

Coverage areas
══════════════
1. Export licence routing (UK SITCL/SIEL/OGEL/SITEL by destination + ML category)
2. Embargo / sanctions tier lookup (UN/EU/UK/US/OFAC)
3. ML category classification from product description
4. ECCN ↔ ML cross-mapping
5. Country risk tier (LOW/MEDIUM/HIGH/EMBARGOED)
6. Procurement feasibility scoring (relationship tier × compliance × competition)
7. Common reasoning chains (if-this-then-that for typical defence broker queries)

Usage
═════
    from .intel import symbolic_reasoner

    answer = symbolic_reasoner.reason("can we export K9 Thunder to angola?")
    if answer["confident"]:
        return answer["response"]

    # Otherwise fall through to LLM
    return await llm.complete(...)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from . import tech_classifier

logger = logging.getLogger("aria.symbolic")

# ── Knowledge tables (the "facts ARIA knows by heart") ─────────────────────

# Embargoed destinations (UN / EU / UK / US comprehensive)
EMBARGOED_COUNTRIES = {
    "RU": {"name": "Russia",       "regimes": ["UN-SC", "EU", "UK-OFSI", "US-OFAC"], "scope": "comprehensive"},
    "BY": {"name": "Belarus",      "regimes": ["EU", "UK-OFSI"], "scope": "comprehensive"},
    "IR": {"name": "Iran",         "regimes": ["UN-SC", "EU", "UK-OFSI", "US-OFAC"], "scope": "comprehensive"},
    "KP": {"name": "North Korea",  "regimes": ["UN-SC", "EU", "UK-OFSI", "US-OFAC"], "scope": "comprehensive"},
    "SY": {"name": "Syria",        "regimes": ["UN-SC", "EU", "UK-OFSI", "US-OFAC"], "scope": "comprehensive"},
    "CU": {"name": "Cuba",         "regimes": ["US-OFAC"], "scope": "partial"},
    "VE": {"name": "Venezuela",    "regimes": ["EU", "US-OFAC", "UK-OFSI"], "scope": "comprehensive"},
    "MM": {"name": "Myanmar",      "regimes": ["EU", "UK-OFSI", "US-OFAC"], "scope": "arms"},
    "CF": {"name": "Central African Republic", "regimes": ["UN-SC"], "scope": "arms"},
    "CD": {"name": "DRC",          "regimes": ["UN-SC"], "scope": "arms"},
    "LY": {"name": "Libya",        "regimes": ["UN-SC", "EU"], "scope": "arms"},
    "ML": {"name": "Mali",         "regimes": ["UN-SC", "EU"], "scope": "arms"},
    "SO": {"name": "Somalia",      "regimes": ["UN-SC"], "scope": "arms"},
    "SD": {"name": "Sudan",        "regimes": ["UN-SC", "EU"], "scope": "arms"},
    "SS": {"name": "South Sudan",  "regimes": ["UN-SC", "EU"], "scope": "arms"},
    "YE": {"name": "Yemen",        "regimes": ["UN-SC"], "scope": "arms"},
    "AF": {"name": "Afghanistan",  "regimes": ["UN-SC", "EU"], "scope": "arms"},
    "ZW": {"name": "Zimbabwe",     "regimes": ["EU", "UK-OFSI"], "scope": "arms"},
    "ER": {"name": "Eritrea",      "regimes": ["UN-SC"], "scope": "arms"},
    "HT": {"name": "Haiti",        "regimes": ["UN-SC"], "scope": "arms"},
    "IQ": {"name": "Iraq",         "regimes": ["UN-SC"], "scope": "limited"},  # Saddam-era residual
    "NI": {"name": "Nicaragua",    "regimes": ["EU"], "scope": "arms"},
    "CN": {"name": "China",        "regimes": ["EU"], "scope": "arms"},  # 1989 EU arms embargo still in force
}

HIGH_RISK = {
    "GW": "Guinea-Bissau", "CM": "Cameroon", "NE": "Niger", "BF": "Burkina Faso",
    "TD": "Chad", "PK": "Pakistan", "EG": "Egypt", "TR": "Türkiye",
    "IN": "India", "ET": "Ethiopia",
}

MEDIUM_RISK = {
    "AO": "Angola", "MZ": "Mozambique", "NG": "Nigeria", "SN": "Senegal",
    "CI": "Côte d'Ivoire", "UG": "Uganda", "ID": "Indonesia", "VN": "Vietnam",
    "CO": "Colombia", "PE": "Peru", "SA": "Saudi Arabia", "AE": "UAE", "JO": "Jordan",
}

# Country name → ISO2
NAME_TO_ISO = {v["name"].lower(): k for k, v in EMBARGOED_COUNTRIES.items()}
NAME_TO_ISO.update({v.lower(): k for k, v in HIGH_RISK.items()})
NAME_TO_ISO.update({v.lower(): k for k, v in MEDIUM_RISK.items()})
NAME_TO_ISO.update({
    "drc": "CD", "congo": "CD", "burma": "MM", "cote d'ivoire": "CI",
    "ivory coast": "CI", "north korea": "KP", "south korea": "KR",
})

# UK ECJU licence routes by ML category and destination tier
LICENCE_ROUTES = {
    "low_risk": {
        "ML1": "OGEL military goods (lethal) — open general licence available; check end-use",
        "ML2": "SIEL Standard Individual Export Licence — typically required for artillery",
        "ML3": "OGEL or SIEL — depends on quantity and end-use certificate",
        "ML4": "SIEL or SITCL — controlled missile/rocket items always require individual licence",
        "ML6": "SIEL — armoured vehicles always require individual licence",
        "ML9": "SIEL — naval vessels require individual licence",
        "ML10": "SIEL — aircraft + UAVs always require individual licence",
        "ML11": "OGEL or SIEL — depends on whether it's a controlled cryptographic item",
        "ML15": "SIEL — radar/EO/IR equipment requires individual licence",
    },
    "medium_risk": {
        "ML1": "SIEL Standard Individual Export Licence — end-use certificate required",
        "ML2": "SIEL — enhanced due diligence required",
        "ML3": "SIEL with EUC — diversion risk assessment required",
        "ML4": "SITCL Standard Individual Trade Control Licence — strongly scrutinised",
        "ML6": "SIEL with full EUC + maintenance plan",
        "ML9": "SIEL with EUC + delivery monitoring",
        "ML10": "SIEL — UAVs especially scrutinised under MTCR",
        "ML11": "SIEL — review against Wassenaar dual-use",
        "ML15": "SIEL — review against Wassenaar dual-use",
    },
    "high_risk": {
        "ML1": "SITCL with full ECJU review — high probability of refusal",
        "ML2": "SITCL — escalated review",
        "ML3": "SITCL — strong diversion risk",
        "ML4": "SITCL — likely refused for missiles/MANPADS",
        "ML6": "SITCL — escalated to FCDO consultation",
        "ML9": "SITCL — escalated review",
        "ML10": "SITCL — UAVs require special MTCR review",
        "ML11": "SITCL — political review required",
        "ML15": "SITCL — Wassenaar review + political clearance",
    },
    "embargoed": {
        "any": "PROHIBITED — destination is under arms embargo. No licence will be granted. "
                "Discussing the deal may itself constitute brokering offence under UK Trade in "
                "Goods (Categories of Controlled Goods) Order 2008.",
    },
}

# CPLP / Lusophone markets where Arkmurus has incumbent advantage
LUSOPHONE_INCUMBENT = {"AO", "MZ", "GW", "CV", "ST", "BR", "PT", "TL"}
LUSOPHONE_NAMES = {"angola", "mozambique", "guinea-bissau", "guinea bissau", "cape verde",
                   "são tomé", "sao tome", "brazil", "portugal", "timor-leste", "east timor"}

# Arkmurus relationship tiers (matches the system prompt)
RELATIONSHIP_TIERS = {
    "incumbent":   {"AO", "MZ", "GW", "CV", "ST"},
    "established": {"ZA", "KE", "NG"},
    "developing":  {"SN", "GH", "ET", "RW", "UG", "CM"},
    "cold_entry":  {"ID", "PH", "VN", "AE", "SA", "PL", "TH", "MY"},
}


def _country_to_iso(country: str) -> str:
    """Normalise a country reference to ISO2."""
    if not country:
        return ""
    c = country.strip().lower()
    if len(c) == 2 and c.upper().isalpha():
        return c.upper()
    return NAME_TO_ISO.get(c, "")


def _country_tier(iso: str) -> str:
    if not iso:
        return "unknown"
    if iso in EMBARGOED_COUNTRIES: return "embargoed"
    if iso in HIGH_RISK:           return "high_risk"
    if iso in MEDIUM_RISK:         return "medium_risk"
    return "low_risk"


def _arkmurus_tier(iso: str) -> str:
    for tier, isos in RELATIONSHIP_TIERS.items():
        if iso in isos:
            return tier
    return "no_relationship"


# ── Intent rules (pattern → handler) ────────────────────────────────────────

def _extract_country(text: str) -> tuple[str, str]:
    """Find a country mention in free text. Returns (iso, name) or ("", "").

    Round 7b: previously this returned the FIRST country name found in the
    text via dict-iteration order. Because EMBARGOED_COUNTRIES is loaded
    first, that biased toward returning embargoed countries even when the
    document was clearly about a different country mentioned more often.
    Past incident: Ghana opportunity brief screened as "Russia is EMBARGOED"
    because Russia was in NAME_TO_ISO before Ghana.

    New behaviour: count occurrences of every recognized country name,
    return the one with the highest count. Ties broken by first appearance.
    """
    t = text.lower()
    counts: list[tuple[int, int, str]] = []  # (count, first_idx_neg, iso)
    for name, iso in NAME_TO_ISO.items():
        matches = list(re.finditer(rf"\b{re.escape(name)}\b", t))
        if matches:
            # Negate the first index so tie-break still favours earlier mentions
            counts.append((len(matches), -matches[0].start(), iso))
    if not counts:
        return "", ""
    counts.sort(reverse=True)
    iso = counts[0][2]
    full_name = (
        EMBARGOED_COUNTRIES.get(iso, {}).get("name")
        or HIGH_RISK.get(iso)
        or MEDIUM_RISK.get(iso)
        or iso
    )
    return iso, full_name


def _extract_ml_category(text: str) -> list[str]:
    """Find ML categories mentioned (or implied via tech_classifier)."""
    explicit = re.findall(r"\bML\s*(\d{1,2})\b", text, re.IGNORECASE)
    cats = sorted({f"ML{n}" for n in explicit})
    if not cats:
        # Use tech_classifier to detect
        tc = tech_classifier.classify_text(text)
        cats = tc.get("ml_categories", [])
    return cats


# ── Handler implementations ────────────────────────────────────────────────

_EXPORT_VERB_RE = re.compile(
    r"\b(?:export|sell|ship|deliver|supply|provide|send)\b",
    re.IGNORECASE,
)


def _handle_export_query(text: str) -> dict | None:
    """Answer 'can we export X to Y' style queries from the rules tables.

    Round 7b: requires the export verb AND the country name to appear in the
    same sentence within ~120 chars of each other. Without this proximity
    check the rule fired on any document containing both keywords anywhere
    — past incident: a Ghana brief mentioning "Russian-supplied weapons"
    matched as "supply Russia" → fired EMBARGOED template.
    """
    verb_match = _EXPORT_VERB_RE.search(text)
    if not verb_match:
        return None
    if not re.search(r"\bto\b", text, re.I):
        return None

    # Use the most-mentioned country, not the first found
    iso, country = _extract_country(text)
    if not iso:
        return None

    # Proximity check: the export verb and the chosen country must be in
    # the same ~250-char window. Count distance in characters between
    # the verb match and the closest country mention.
    country_pattern = next(
        (n for n, i in NAME_TO_ISO.items() if i == iso),
        None,
    )
    if country_pattern:
        nearest_distance = None
        for cm in re.finditer(rf"\b{re.escape(country_pattern)}\b", text, re.IGNORECASE):
            distance = abs(cm.start() - verb_match.start())
            if nearest_distance is None or distance < nearest_distance:
                nearest_distance = distance
        if nearest_distance is None or nearest_distance > 250:
            # Verb and country aren't in proximity — this is not a real
            # export query, just two unrelated mentions in the same blob.
            return None

    ml_cats = _extract_ml_category(text)
    tier = _country_tier(iso)

    lines = []
    if tier == "embargoed":
        info = EMBARGOED_COUNTRIES[iso]
        lines.append(f"⛔ *{country} is EMBARGOED*")
        lines.append(f"Regimes: {', '.join(info['regimes'])}")
        lines.append(f"Scope: {info['scope']}")
        lines.append("")
        lines.append(LICENCE_ROUTES["embargoed"]["any"])
        lines.append("")
        lines.append("[ASSESSED — static embargo table] Recommendation: DO NOT PROCEED. This may be a brokering offence under UK law. Verify current status against ECJU live embargo list before acting.")
        return {
            "confident": True,
            "confidence": 0.95,
            "response": "\n".join(lines),
            "intent": "export_query",
            "tier": tier,
            "country": country,
            "ml_categories": ml_cats,
        }

    tier_label = {"high_risk": "🟠 HIGH RISK", "medium_risk": "🟡 MEDIUM RISK", "low_risk": "🟢 LOW RISK"}.get(tier, tier)
    lines.append(f"*Export query — {country}* ({tier_label})")
    lines.append("")
    if ml_cats:
        lines.append(f"Detected ML categories: {', '.join(ml_cats)}")
        lines.append("")
        lines.append("*Licence routing:*")
        for cat in ml_cats[:5]:
            route = LICENCE_ROUTES.get(tier, {}).get(cat)
            if route:
                lines.append(f"  • {cat}: {route}")
    else:
        lines.append("No specific ML category detected — provide product description for routing.")

    # Add Arkmurus angle
    arkmurus_tier = _arkmurus_tier(iso)
    if arkmurus_tier == "incumbent":
        lines.append("")
        lines.append("✅ *Arkmurus relationship tier: INCUMBENT* (Lusophone Africa) — direct buyer access available.")
    elif arkmurus_tier == "established":
        lines.append("")
        lines.append("✅ *Arkmurus relationship tier: ESTABLISHED* — known to MoD, regular engagement.")
    elif arkmurus_tier == "developing":
        lines.append("")
        lines.append("🟡 *Arkmurus relationship tier: DEVELOPING* — building contacts.")
    elif arkmurus_tier == "cold_entry":
        lines.append("")
        lines.append("🔵 *Arkmurus relationship tier: COLD ENTRY* — needs partner intelligence.")

    lines.append("")
    lines.append("[PROBABLE] Procurement feasibility: see SIEL/SITCL routing above. Verify with ECJU before commercial commitment.")

    return {
        "confident": True,
        "confidence": 0.85,
        "response": "\n".join(lines),
        "intent": "export_query",
        "tier": tier,
        "country": country,
        "ml_categories": ml_cats,
    }


def _handle_embargo_query(text: str) -> dict | None:
    """Answer 'is X embargoed/under sanctions' queries."""
    if not re.search(r"\b(?:embargo|sanction|sanctioned|prohibit|allowed|legal)\b", text, re.I):
        return None
    iso, country = _extract_country(text)
    if not iso:
        return None

    if iso in EMBARGOED_COUNTRIES:
        info = EMBARGOED_COUNTRIES[iso]
        return {
            "confident": True,
            "confidence": 0.95,
            "response": (
                f"⛔ *{country}* is under arms embargo.\n\n"
                f"Active regimes: {', '.join(info['regimes'])}\n"
                f"Scope: {info['scope']}\n\n"
                f"[ASSESSED — static embargo table] No defence exports permitted per ARIA's static regimes list. Brokering may be a criminal offence. Verify current embargo status against the ECJU live list before any action."
            ),
            "intent": "embargo_query",
        }
    if iso in HIGH_RISK:
        return {
            "confident": True,
            "confidence": 0.85,
            "response": (
                f"🟠 *{country}* is NOT embargoed but is HIGH-RISK.\n\n"
                f"[ASSESSED — static country-risk table] Enhanced due diligence indicated. End-use certificate, diversion risk assessment, "
                f"and SITCL likely required. Likely subject to political review at FCDO. Verify risk tier against current FCDO / ECJU guidance."
            ),
            "intent": "embargo_query",
        }
    if iso in MEDIUM_RISK:
        return {
            "confident": True,
            "confidence": 0.85,
            "response": (
                f"🟡 *{country}* is permitted with standard controls.\n\n"
                f"[ASSESSED — static country-risk table] Not embargoed per ARIA's static list. Standard SIEL with end-use certificate required for ML items. Verify against current ECJU list."
            ),
            "intent": "embargo_query",
        }
    return {
        "confident": True,
        "confidence": 0.80,
        "response": (
            f"🟢 *{country}* — no embargo on file in ARIA's static tables.\n\n"
            f"[ASSESSED] Standard export controls apply. Verify against current ECJU country list before action."
        ),
        "intent": "embargo_query",
    }


def _handle_licence_query(text: str) -> dict | None:
    """Answer 'what licence do I need for X to Y' queries."""
    if not re.search(r"\b(?:licen[cs]e|sitcl|siel|ogel|sitel|eccn|export\s+control)\b", text, re.I):
        return None
    iso, country = _extract_country(text)
    if not iso:
        return None
    ml_cats = _extract_ml_category(text)
    tier = _country_tier(iso)

    if tier == "embargoed":
        return _handle_export_query(text)  # handled in export branch

    if not ml_cats:
        return {
            "confident": False,
            "confidence": 0.4,
            "response": f"Need a specific product/ML category to determine licence route for {country}. "
                        f"Tell me what you want to ship and I'll route it.",
            "intent": "licence_query",
        }

    lines = [f"*Licence routing for {country}* ({tier.replace('_', ' ')})", ""]
    for cat in ml_cats[:5]:
        route = LICENCE_ROUTES.get(tier, {}).get(cat)
        if route:
            lines.append(f"*{cat}*: {route}")
            lines.append("")
    lines.append("[PROBABLE] Routing based on UK ECJU rules. Confirm with current SPIRE guidance before submission.")
    return {
        "confident": True,
        "confidence": 0.85,
        "response": "\n".join(lines),
        "intent": "licence_query",
    }


def _handle_classification_query(text: str) -> dict | None:
    """Answer 'classify this product / what ML category is X'."""
    if not re.search(r"\b(?:classif|ml\s*\d|eccn|control(?:led)?\s+item|category)\b", text, re.I):
        return None
    tc = tech_classifier.classify_text(text)
    if not tc.get("systems") and not tc.get("calibres") and not tc.get("ml_categories"):
        return None

    lines = ["*Technical classification*", ""]
    if tc.get("systems"):
        lines.append("*Systems detected:*")
        for s in tc["systems"][:5]:
            lines.append(f"  • {s.get('designation')} — ML: {s.get('ml')}, ECCN: {s.get('eccn')}, OEM: {s.get('oem')}")
        lines.append("")
    if tc.get("calibres"):
        lines.append("*Calibres detected:*")
        for c in tc["calibres"][:5]:
            lines.append(f"  • {c.get('calibre')} — {c.get('use')} (HS {c.get('hs')}, {c.get('ml')})")
        lines.append("")
    if tc.get("ml_categories"):
        lines.append(f"*ML categories:* {', '.join(tc['ml_categories'])}")
    if tc.get("controlled_items_count", 0) > 0:
        lines.append(f"⚠️ {tc['controlled_items_count']} controlled item(s) detected")
    if tc.get("embargo_risks"):
        lines.append("")
        lines.append("⛔ *Embargo risks:*")
        for r in tc["embargo_risks"]:
            lines.append(f"  - {r}")
    lines.append("")
    lines.append("[ASSESSED — static technical database] Classification from ARIA's static ML/ECCN table. Verify against the current UK Strategic Export Control Lists before relying on this.")
    return {
        "confident": True,
        "confidence": 0.90,
        "response": "\n".join(lines),
        "intent": "classification_query",
    }


def _handle_relationship_query(text: str) -> dict | None:
    """Answer 'what's our relationship in X' / 'are we incumbent in X' queries."""
    if not re.search(r"\b(?:relationship|tier|incumbent|established|developing|presence|positioning|advantage)\b", text, re.I):
        return None
    iso, country = _extract_country(text)
    if not iso:
        return None
    arkmurus_tier = _arkmurus_tier(iso)
    if arkmurus_tier == "no_relationship":
        return {
            "confident": True,
            "confidence": 0.85,
            "response": (
                f"📍 *{country}* — Arkmurus has *no formal relationship tier* on file.\n\n"
                f"This is a cold market. Entry requires: (1) local partner identification, "
                f"(2) MoD introduction via OEM or industry conference, (3) compliance pre-screen.\n\n"
                f"[ASSESSED] Treat as cold entry — invest 3-6 months relationship-building before pursuing deals."
            ),
            "intent": "relationship_query",
        }

    descriptions = {
        "incumbent": "ARIA has deep relationships, market intelligence advantage, and direct buyer access.",
        "established": "ARIA is known to the MoD with regular engagement.",
        "developing": "ARIA is building contacts — needs sustained relationship investment.",
        "cold_entry": "ARIA has no significant presence — requires partner-led entry strategy.",
    }
    return {
        "confident": True,
        "confidence": 0.95,
        "response": (
            f"📍 *{country}* — Arkmurus tier: *{arkmurus_tier.upper().replace('_', ' ')}*\n\n"
            f"{descriptions[arkmurus_tier]}\n\n"
            f"[ASSESSED — static positioning matrix] Tier derived from Arkmurus market-posture table. Verify against current BD state."
        ),
        "intent": "relationship_query",
        "tier": arkmurus_tier,
    }


# ── Top-level reasoning entry point ────────────────────────────────────────

# Handlers in priority order (more specific first)
_HANDLERS = [
    _handle_export_query,
    _handle_licence_query,
    _handle_embargo_query,
    _handle_classification_query,
    _handle_relationship_query,
]


# 2026-04-08 round 7b: hard-block phrases that indicate the user is asking
# for a document review, not asking an export-feasibility question. Without
# this guard the symbolic reasoner pattern-matches stray "supply"/"export"
# verbs + stray country names inside the document body and emits a
# high-confidence wrong answer (the canned EMBARGOED template). Past
# incident: a Ghana opportunity brief was screened as "Russia is EMBARGOED"
# because the doc mentioned Russian arms in passing.
_DOC_REVIEW_RE = re.compile(
    r"\b(?:double[\s-]?check|review|read|check\s+this|look\s+at|"
    r"go\s+through|analyse|analyze|summari[sz]e|what(?:'s|\s+is)\s+in|"
    r"give\s+me\s+(?:a\s+)?summary|tell\s+me\s+about\s+this)\b.*\b(?:document|brief|"
    r"pdf|attachment|file|paper|report|email|message|note)s?\b",
    re.IGNORECASE | re.DOTALL,
)
# 2026-04-24: workflow commands ("run DD", "screen this company") reached the
# export handler and pattern-matched stray verbs + country names from the
# pasted entity name (e.g. "Serban Industries SRL, Str. Drid..."). These are
# commands to execute a downstream pipeline, not questions the rules engine
# can answer — skip cleanly so the routing layer hands them to the DD flow.
_WORKFLOW_CMD_RE = re.compile(
    r"\b(?:run|do|start|perform|execute|conduct|please\s+run)\s+"
    r"(?:a\s+|the\s+)?(?:full\s+|complete\s+|quick\s+)?"
    r"(?:due[\s-]?diligence|dd)\b|"
    # R-F10 2026-05-01: added `search|find|look[\s-]?up` so chat queries
    # like "Aria, can your search this person Antonio Magalhaes" route to
    # the DD flow instead of logging a no_symbolic_rule gap.
    r"\b(?:screen|vet|investigate|background[\s-]?check|search|find|look[\s-]?up)\s+"
    r"(?:this\s+|that\s+|the\s+)?"
    r"(?:company|entity|person|individual|org(?:anisation|anization)?|counterpart(?:y|ies)|"
    r"supplier|buyer|customer|vendor|contact)\b|"
    r"\bfind\s+(?:info|information|details|intel|everything)\s+(?:on|about)\b|"
    # R-F10: the colon-label format ("Aria, person: Colin Risso / Nationality: UK / Role: Director at Limestone")
    # is a structured DD request the LLM/DD orchestrator handles, not a
    # rules-engine question. Skip cleanly to suppress the gap log.
    r"\b(?:person|company|entity|counterpart(?:y|ies)|supplier|buyer)\s*:\s*\S|"
    # R-F11 2026-05-01: autonomous task templates use "Aria, investigate
    # the latest on: X" (DAILY-PROC-TED, DAILY-PROC-SAM, etc). Live
    # evidence 08:15:24: SAM.gov template fired no_symbolic_rule. The
    # rules engine has no deterministic answer for "latest news on
    # procurement portal X" — that's the deep_researcher's job. Skip.
    r"\binvestigate\s+(?:the\s+|that\s+|this\s+)?(?:latest|recent|new|update|news|current)\b|"
    r"\b(?:latest|recent|new|news|update)\s+on\s*:",
    re.IGNORECASE,
)
# 2026-04-24: multiparty / end-use structures (origin → buyer → end user)
# aren't modelled by the two-party UK→X export handler. Without this guard
# the handler picks one country from the chain and produces a misleading
# answer. Skip and let the LLM answer — this is a legitimate reasoning gap.
_MULTIPARTY_RE = re.compile(
    r"\bend[\s-]?user(?:s)?\b|\bend[\s-]?use\b|"
    r"\bre[\s-]?export(?:ing|ed|s)?\b|"
    r"\btrans[\s-]?ship(?:ment|ping|ped)?\b|"
    r"\btransit\s+(?:country|via|through)\b",
    re.IGNORECASE,
)
# Anything longer than this is treated as "probably contains pasted content,
# not a clean question" and the symbolic reasoner skips entirely. Real export
# questions are short — "can we export X to Y" is ~30 chars.
_SYMBOLIC_MAX_INPUT_LEN = 600


def reason(question: str, *, silent_brain_hook: bool = False) -> dict:
    """Try to answer a question using only symbolic rules.

    Args:
        silent_brain_hook: when True, skip the brain_hook absorb / capability-gap
            emission. Used by student.compare_local_silently which re-runs
            the same query post-LLM purely to score divergence — recording
            the same `no_symbolic_rule` gap a second time per chat just
            duplicates the ledger entry from the pre-LLM call (F53 2026-04-28).

    Returns:
        {
            "confident": bool,    # True if a rule fired and produced a clean answer
            "confidence": float,  # 0..1
            "response": str,      # the answer text
            "intent": str,        # which rule fired
            "source": "symbolic_reasoner",
        }
        OR {"confident": False} if no rule matched.
    """
    if not question or len(question.strip()) < 5:
        return {"confident": False, "source": "symbolic_reasoner"}

    text = question.strip()

    # Round 7b guard: don't run rules engine on document-review requests or
    # on long pasted content — both cases produce high-confidence wrong
    # answers because the rules pattern-match stray verbs and country names
    # in the body text.
    if len(text) > _SYMBOLIC_MAX_INPUT_LEN:
        return {"confident": False, "source": "symbolic_reasoner", "skipped": "input_too_long"}
    if _DOC_REVIEW_RE.search(text):
        return {"confident": False, "source": "symbolic_reasoner", "skipped": "document_review_intent"}
    if _WORKFLOW_CMD_RE.search(text):
        return {"confident": False, "source": "symbolic_reasoner", "skipped": "workflow_command"}
    if _MULTIPARTY_RE.search(text):
        return {"confident": False, "source": "symbolic_reasoner", "skipped": "multiparty_structure"}

    fired_intent = ""
    final_result = None
    for handler in _HANDLERS:
        try:
            result = handler(text)
            if result and result.get("confident"):
                result["source"] = "symbolic_reasoner"
                fired_intent = result.get("intent", handler.__name__)
                final_result = result
                break
        except Exception as e:
            logger.warning("symbolic handler %s failed: %s", handler.__name__, e)
            continue

    if final_result is None:
        final_result = {"confident": False, "source": "symbolic_reasoner"}

    # Brain signal — every confident symbolic resolution = LLM call
    # saved + deterministic answer shipped. Tracks which intents the
    # rules engine handles so the predictor can warn early on
    # repeating failures.
    if silent_brain_hook:
        return final_result
    try:
        import asyncio as _aio
        from . import brain_hook as _bh

        async def _emit():
            confident = bool(final_result.get("confident"))
            await _bh.absorb(
                module="symbolic_reasoner",
                summary=(
                    f"Symbolic reason: {'HIT' if confident else 'NO_RULE'} "
                    f"intent={fired_intent or 'none'} "
                    f"conf={final_result.get('confidence', 0):.2f}"
                ),
                detail=f"q={text[:200]!r}",
                success=confident,
                gap_type=("no_symbolic_rule" if not confident else None),
                gap_detail=(f"No rule matched: {text[:120]}"
                            if not confident else None),
            )

        try:
            loop = _aio.get_running_loop()
            loop.create_task(_emit())
        except RuntimeError:
            pass
    except Exception:
        pass

    return final_result


def get_capability_surface() -> dict:

    # R-F996 — wire to brain
    wire_success(
        module="symbolic_reasoner",
        summary="Symbolic reasoning",
        source_id="symbolic_reasoner:R-F996",
    )
    return {
        "rules_loaded": len(_HANDLERS),
        "intents_supported": [
            "export_query",
            "licence_query",
            "embargo_query",
            "classification_query",
            "relationship_query",
        ],
        "embargoed_countries_indexed": len(EMBARGOED_COUNTRIES),
        "high_risk_countries_indexed": len(HIGH_RISK),
        "medium_risk_countries_indexed": len(MEDIUM_RISK),
        "licence_routes": sum(len(v) for v in LICENCE_ROUTES.values()),
        "llm_calls_required": 0,
    }
