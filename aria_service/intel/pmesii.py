"""ARIA PMESII country-assessment template.

Adds a structured-analysis scaffold to the system prompt when ARIA is asked
to assess a country, region, or actor. PMESII is the doctrinal framework used
by NATO and US joint planners to ensure country reports cover the same six
dimensions in the same order, so they're directly comparable across countries
and across time.

P  Political      — government stability, leadership, alliances, recent transitions
M  Military       — capabilities, doctrine, procurement patterns, recent acquisitions
E  Economic       — GDP, defence budget % of GDP, debt, FDI, sanctions exposure
S  Social         — demographics, ethnic / sectarian tensions, public opinion drivers
I  Information    — media freedom, disinformation environment, cyber posture
I  Infrastructure — transport, ports, energy, logistics chokepoints

Why this exists
═══════════════
Without a template, ARIA's country assessments vary in shape from query to
query. One reply emphasises politics, the next emphasises economics. That
makes them hard to compare and gives clients a "chatbot" feel rather than a
"professional intelligence" feel. PMESII forces a consistent skeleton.

The intent detector is conservative — it only fires on clearly country-
focused questions ("assess Angola", "country risk Mozambique", "PMESII on
Senegal"). General questions that happen to mention a country do not
trigger it.

Behind ARIA_PMESII_TEMPLATE_ENABLED env var (default on). If misfiring,
flip to 0 and the chat path returns to its prior behaviour with no other
changes — this module only contributes a system-prompt addendum, it does
not modify any other code path.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import os
import re

logger = logging.getLogger("aria.pmesii")

# Countries ARIA covers — keep this list aligned with the system prompt's
# domain expertise. We don't fire PMESII for arbitrary country mentions
# (e.g. "the meeting is in Switzerland") because the framework is designed
# for assessment, not for casual references.
_COVERED_COUNTRIES = [
    # Lusophone Africa (incumbent)
    "angola", "mozambique", "cape verde", "cabo verde", "guinea-bissau",
    "guinea bissau", "são tomé", "sao tome", "são tomé and príncipe",
    "sao tome and principe", "equatorial guinea", "timor-leste", "timor leste",
    # West / East Africa (established + developing)
    "south africa", "kenya", "nigeria", "senegal", "ghana", "ethiopia",
    "rwanda", "uganda", "cameroon", "tanzania", "zambia", "zimbabwe",
    "botswana", "namibia", "democratic republic of the congo", "drc",
    "côte d'ivoire", "cote d'ivoire", "ivory coast", "mali", "burkina faso",
    "niger", "chad", "sudan", "south sudan", "somalia", "djibouti", "eritrea",
    # Southeast Asia (cold entry)
    "indonesia", "philippines", "vietnam", "thailand", "malaysia",
    "singapore", "myanmar", "cambodia", "laos",
    # MENA (cold entry)
    "uae", "united arab emirates", "saudi arabia", "qatar", "oman", "kuwait",
    "bahrain", "jordan", "egypt", "morocco", "algeria", "tunisia", "libya",
    "iraq", "lebanon", "yemen",
    # Eastern Europe (cold entry)
    "poland", "ukraine", "romania", "bulgaria", "serbia", "moldova",
    "hungary", "slovakia", "czech republic", "czechia", "estonia", "latvia",
    "lithuania",
    # Latin America (cold entry)
    "brazil", "colombia", "peru", "chile", "argentina", "mexico", "ecuador",
    "venezuela", "bolivia", "paraguay", "uruguay",
]

# Verbs / phrases that indicate the user wants a *structured assessment*
# rather than a casual mention. Conservative on purpose.
_ASSESSMENT_VERBS = [
    r"assess",
    r"assessment\s+(?:of|on|for)",
    r"profile\s+(?:of|on)?",
    r"country\s+(?:risk|profile|brief|report|assessment|overview)",
    r"brief(?:ing)?\s+(?:on|for|about)",
    r"overview\s+(?:of|on)",
    r"deep\s+dive\s+(?:on|into)",
    r"situation\s+(?:in|of)",
    r"pmesii",
    r"analysis\s+of",
    r"market\s+entry\s+(?:into|to|for)",
    r"opportunity\s+(?:in|for)",
]

# Compiled patterns
_COUNTRY_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in _COVERED_COUNTRIES) + r")\b",
    re.IGNORECASE,
)
_VERB_RE = re.compile(
    r"\b(" + "|".join(_ASSESSMENT_VERBS) + r")\b",
    re.IGNORECASE,
)
# Explicit "/pmesii X" form is a hard trigger regardless of verbs.
_PMESII_EXPLICIT_RE = re.compile(r"\bpmesii\b", re.IGNORECASE)


def is_enabled() -> bool:
    """Feature flag — default ON. Set ARIA_PMESII_TEMPLATE_ENABLED=0 to disable."""
    val = os.getenv("ARIA_PMESII_TEMPLATE_ENABLED", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


def detect_country_assessment(message: str) -> str | None:
    """If the message is a country-assessment intent, return the country name.
    Otherwise return None.

    Conservative detector — requires BOTH a covered country AND an assessment
    verb, OR an explicit /pmesii or "pmesii on X" form. General questions that
    happen to mention a country in passing do not trigger.
    """
    if not message or not isinstance(message, str):
        return None
    if not is_enabled():
        return None

    msg = message.strip()
    if not msg:
        return None

    country_match = _COUNTRY_RE.search(msg)
    if not country_match:
        return None
    country = country_match.group(1)

    # Hard trigger: explicit pmesii keyword
    if _PMESII_EXPLICIT_RE.search(msg):
        return country

    # Soft trigger: assessment verb + country
    if _VERB_RE.search(msg):
        return country

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="pmesii",
        summary="Detect Country Assessment",
        source_id="pmesii:R-F996",
    )

    return None


def addendum_for(country: str) -> str:
    """Return a system-prompt addendum that instructs ARIA to structure her
    reply as a PMESII country assessment for the given country.

    The output is appended to ARIA_SYSTEM_PROMPT (after calibration and
    contradictions addenda) by _build_calibrated_system_prompt in aria_engine.
    """
    country_display = country.strip().title()
    return f"""STRUCTURED ANALYSIS REQUEST — PMESII COUNTRY ASSESSMENT

The user is asking for a country-level assessment of {country_display}. Structure your reply
using the PMESII framework. Do NOT skip sections — if you have no data for a section, say so
explicitly with [UNCERTAIN] and explain what additional information would change the picture.
Each section should be 2-5 sentences. End with a Bottom Line and Indicators to Monitor.

SECTIONS (in order, with these exact headings):

**P — POLITICAL**
Government stability, leadership trajectory, recent elections / transitions, key alliances,
relationship with major powers (Russia, China, US, France, EU). Note any pending political risks.

**M — MILITARY**
Force structure, doctrine, recent procurement (last 5 years), key suppliers, training relationships,
operational deployments. Cite SIPRI / Jane's-style data with confidence tags.

**E — ECONOMIC**
GDP trajectory, defence budget as % of GDP, debt position, FX reserves, sanctions exposure,
key trade partners, FDI flows. Note any IMF programmes or fiscal stress.

**S — SOCIAL**
Demographics, ethnic / religious composition, internal tensions, public opinion drivers, urbanisation,
education / human capital indicators relevant to defence-industrial capacity.

**I — INFORMATION**
Media freedom, disinformation environment, cyber posture, telecoms infrastructure, key narratives
shaping decision-making.

**I — INFRASTRUCTURE**
Transport (road, rail, air, ports), energy security, logistics chokepoints, key strategic
infrastructure. Note anything that constrains military mobility or supply chains.

**BOTTOM LINE**
2-3 sentences. Net assessment of the country as a defence-procurement opportunity / counterparty risk
for Arkmurus, with explicit relationship-tier note (incumbent / established / developing / cold entry).

**INDICATORS TO MONITOR**
3-5 specific events or data points that, if observed, would materially change this assessment.

CONFIDENCE DISCIPLINE
Every material claim must carry one of: [CONFIRMED] [PROBABLE] [ASSESSED] [UNCERTAIN] [SPECULATIVE].
A section with no available data should say so plainly — do NOT invent numbers."""

# R-F2119 §21a — wire failure handler for pmesii
try:
    wire_failure(module="pmesii", detail="module shutdown",
                gap_type="engine_failure", source="pmesii:shutdown")
except Exception:
    pass
