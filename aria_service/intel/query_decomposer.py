"""Semantic query decomposer — intent + domain-aware query expansion.

Why this exists
───────────────
Past incident 2026-04-18: ARIA's deep_research tool fires an LLM call
per invocation just to generate 3-15 search queries from a free-text
topic. The LLM returns generic queries ("defence research methods")
that don't use domain vocabulary and miss the specific terms a senior
defence analyst would search for (STANAG numbers, SIEL codes, country-
specific procurement portals, OEM acronyms).

This module replaces that LLM call with a pure-regex classifier +
domain-aware query template expansion for the 60-80% of cases where
intent is clear. Ambiguous queries still fall through to the LLM.

Net effect: save ~$0.02 per deep_research call, produce better queries
that reach defence-specific sources the LLM wouldn't pick.

Public API
──────────
  classify(query: str) -> QueryIntent
      Returns structured intent + entities. Pure regex.

  decompose(query: str, intent: QueryIntent, max_queries: int = 5) -> list[str]
      Generate domain-specific search queries.

  async should_fallback_to_llm(intent: QueryIntent) -> bool
      If confidence below threshold, caller should use the LLM path instead.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("aria.query_decomposer")


class Intent(str, Enum):
    DD = "DD"                           # due diligence on an entity
    COMPANY_RESEARCH = "COMPANY_RESEARCH"
    PERSON_RESEARCH = "PERSON_RESEARCH"
    TENDER_SEARCH = "TENDER_SEARCH"     # procurement / RFP discovery
    TECHNICAL = "TECHNICAL"             # equipment specs, STANAGs, standards
    COMPLIANCE = "COMPLIANCE"           # sanctions, export control, legal
    GEOPOLITICAL = "GEOPOLITICAL"       # country risk, conflict, elections
    OSINT = "OSINT"                     # general intelligence gathering
    ACADEMIC = "ACADEMIC"               # papers, studies, reports
    UNKNOWN = "UNKNOWN"


@dataclass
class QueryIntent:
    """Structured view of a user question."""
    raw_query: str
    intent: Intent
    confidence: float                   # 0.0-1.0, how sure we are about intent
    entities: list[str] = field(default_factory=list)      # org / person / product names
    countries: list[str] = field(default_factory=list)      # ISO-2 or full names
    years: list[int] = field(default_factory=list)
    amounts: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)


# ── Intent keyword patterns ────────────────────────────────────────────────
# Each pattern assigns a confidence contribution. Highest total wins.
# Patterns are intentionally narrow so ambiguous queries score low +
# fall back to the LLM.

_INTENT_PATTERNS: list[tuple[Intent, re.Pattern, float]] = [
    # DUE DILIGENCE — explicit DD asks
    (Intent.DD, re.compile(
        r"\b(due\s+diligence|dd\s+(?:on|for|of)|ark-?dd|run\s+dd|screen\s+(?:this|that|the)|"
        r"background\s+check|integrity\s+check|risk\s+screen|kyc\s+(?:on|check))\b",
        re.IGNORECASE,
    ), 0.9),

    # COMPANY RESEARCH — "tell me about", "what do you know about", company-shaped entity
    (Intent.COMPANY_RESEARCH, re.compile(
        r"\b(tell\s+me\s+about|what\s+do\s+you\s+know\s+about|investigate|research|"
        r"profile|deep\s*dive|look\s+into|find\s+out\s+about|learn\s+about)\b"
        r".*?"
        r"\b(company|corporation|firm|business|ltd|limited|inc|gmbh|sa|sarl|plc|"
        r"group|industries|technologies|systems|aerospace|defense|defence|manufacturer|oem)\b",
        re.IGNORECASE | re.DOTALL,
    ), 0.8),

    # PERSON RESEARCH — "who is X", named person context
    (Intent.PERSON_RESEARCH, re.compile(
        r"\b(who\s+is|who\s+was|find\s+out\s+who|background\s+of|resume\s+of|"
        r"career\s+of|history\s+of)\b\s+"
        r"(?:the\s+|a\s+)?(?:current\s+|former\s+|new\s+)?"
        r"(?:minister|general|ceo|director|chairman|president|founder|"
        r"secretary|officer|ambassador|attaché|admiral|colonel|lieutenant|"
        r"commander|chief|deputy|[a-z]+\s+of\s+defence|[a-z]+\s+of\s+defense)",
        re.IGNORECASE,
    ), 0.85),

    # TENDER / PROCUREMENT
    (Intent.TENDER_SEARCH, re.compile(
        r"\b(tender|rfp|rfq|rfi|procurement|bid|solicitation|contract\s+award|"
        r"contract\s+notice|opportunity|call\s+for\s+bids|expression\s+of\s+interest)\b",
        re.IGNORECASE,
    ), 0.85),

    # TECHNICAL — equipment specs, standards
    (Intent.TECHNICAL, re.compile(
        r"\b(specifications?|spec\s+sheet|technical\s+data|stanag|"
        r"155mm|122mm|152mm|calib(?:er|re)|ammunition|m\s*109|m\s*107|pzh|"
        r"caesar|atmos|trg-?\d+|kh\s*-?\d+|tb-?\d+|akin[cç]i|baykar|"
        r"radar|missile|sam|manpads|mbt|ifv|apc|uas|uav|fpv|loitering\s+munition|"
        r"c4isr|electronic\s+warfare|ew\s+system)\b",
        re.IGNORECASE,
    ), 0.8),

    # COMPLIANCE — sanctions, export control, legal
    (Intent.COMPLIANCE, re.compile(
        r"\b(sanctions?|ofac|sdn|ofsi|un\s+1267|uk\s+ofsi|eu\s+sanctions|"
        r"export\s+control|export\s+licen[cs]e|siel|ogel|ecju|"
        r"itar|ear99|ear\s+ccl|eu\s+dual\s*[-\s]?use|mtcr|wassenaar|"
        r"compliance|bribery|fcpa|aml|money\s+laundering|pep|beneficial\s+owner|ubo|"
        r"debarred|ineligible|blacklist)\b",
        re.IGNORECASE,
    ), 0.9),

    # GEOPOLITICAL
    (Intent.GEOPOLITICAL, re.compile(
        r"\b(conflict|war|escalation|sanctions\s+regime|coup|election|"
        r"political\s+risk|country\s+risk|security\s+situation|insurgency|"
        r"what'?s?\s+happening\s+in|situation\s+in)\b",
        re.IGNORECASE,
    ), 0.75),

    # ACADEMIC — papers, studies
    (Intent.ACADEMIC, re.compile(
        r"\b(paper|study|research\s+paper|publication|journal|arxiv|doi|"
        r"peer[-\s]?reviewed|citation|bibliography|literature\s+review|"
        r"meta[-\s]?analysis|systematic\s+review)\b",
        re.IGNORECASE,
    ), 0.7),

    # OSINT — catch-all for explicit OSINT phrasing
    (Intent.OSINT, re.compile(
        r"\b(osint|open\s+source\s+intelligence|social\s+media\s+intel|"
        r"linkedin\s+check|press\s+search|news\s+search|web\s+search)\b",
        re.IGNORECASE,
    ), 0.65),
]


# ── Entity extraction ──────────────────────────────────────────────────────

# Known OEMs / brands — prefix with optional articles. Narrow list
# for now; extend via oem_registry import later.
_OEM_ENTITY = re.compile(
    r"\b("
    # Turkey
    r"baykar|roketsan|aselsan|stm|otokar|fnss|tai\b|havelsan|mkek|"
    # Israel
    r"elbit|iai|rafael|plasan|israel\s+aerospace|israel\s+weapons|iwi|"
    # Korea
    r"hanwha|hyundai\s+rotem|lig\s+nex1|kai\b|korea\s+aerospace|"
    # NATO
    r"rheinmetall|knds|nexter|kmw|bae\s+systems|leonardo|thales|mbda|"
    r"nammo|saab|kongsberg|patria|"
    # Eastern EU / Balkans
    r"csg|czechoslovak\s+group|pgz|polska\s+grupa\s+zbrojeniowa|romarm|"
    # Others
    r"denel|paramount\s+group|embraer|avibras|bharat\s+electronics|edge\s+group|"
    # US (ITAR — tracked, not primary route)
    r"lockheed|raytheon|boeing|general\s+dynamics|northrop|textron|l3harris"
    r")\b",
    re.IGNORECASE,
)

# Country names (ISO / common) — narrow to Arkmurus-relevant markets
_COUNTRY_ENTITY = re.compile(
    r"\b(angola|mozambique|guinea[-\s]?bissau|cape\s+verde|são\s+tomé|são\s+tome|"
    r"nigeria|ghana|kenya|uganda|rwanda|tanzania|south\s+africa|zambia|"
    r"zimbabwe|botswana|namibia|senegal|ivory\s+coast|c[oô]te\s+d'ivoire|"
    r"cameroon|gabon|drc|democratic\s+republic\s+of\s+congo|"
    r"saudi\s+arabia|uae|united\s+arab\s+emirates|qatar|bahrain|oman|kuwait|"
    r"egypt|jordan|lebanon|morocco|tunisia|algeria|libya|"
    r"turkey|türkiye|israel|lebanon|"
    r"south\s+korea|japan|taiwan|vietnam|thailand|philippines|malaysia|"
    r"indonesia|india|pakistan|bangladesh|"
    r"brazil|mexico|colombia|argentina|peru|chile|ecuador|panama|"
    r"uk|united\s+kingdom|britain|"
    r"france|germany|italy|spain|poland|romania|czech(?:\s+republic)?|czechia|"
    r"slovakia|hungary|greece|bulgaria|croatia|"
    r"usa|united\s+states|america|"
    r"russia|ukraine|belarus|kazakhstan|"
    r"china|mongolia|"
    r"australia|new\s+zealand|canada|norway|sweden|finland|denmark)\b",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r"\b(20\d{2}|19\d{2})\b")
_AMOUNT_RE = re.compile(
    r"\b(?:\$|£|€|usd|eur|gbp)\s*\d[\d,]*\.?\d*\s*(?:k|m|b|bn|million|billion)?\b",
    re.IGNORECASE,
)

# Capitalised phrases as candidate entities (multi-word proper nouns)
_CAPS_ENTITY = re.compile(
    r"\b([A-Z][a-zA-Z0-9&-]+(?:\s+[A-Z][a-zA-Z0-9&-]+){1,3})\b"
)


def _extract_entities(query: str) -> tuple[list[str], list[str], list[int], list[str]]:
    """Return (named_entities, countries, years, amounts)."""
    named: list[str] = []
    seen: set[str] = set()

    # OEM matches first (authoritative)
    for m in _OEM_ENTITY.finditer(query):
        name = m.group(1).strip().title()
        if name.lower() not in seen:
            seen.add(name.lower())
            named.append(name)

    # Capitalised multi-word phrases (company-shape)
    for m in _CAPS_ENTITY.finditer(query):
        name = m.group(1).strip()
        if len(name) < 5:
            continue
        # Skip country-shaped matches (picked up below)
        if _COUNTRY_ENTITY.fullmatch(name):
            continue
        # Skip sentence starters
        if name in ("How", "What", "Who", "When", "Where", "Why", "Aria"):
            continue
        if name.lower() not in seen:
            seen.add(name.lower())
            named.append(name)

    countries: list[str] = []
    for m in _COUNTRY_ENTITY.finditer(query):
        c = m.group(1).strip().title()
        if c not in countries:
            countries.append(c)

    years: list[int] = []
    for m in _YEAR_RE.finditer(query):
        y = int(m.group(1))
        if y not in years:
            years.append(y)

    amounts: list[str] = []
    for m in _AMOUNT_RE.finditer(query):
        a = m.group(0).strip()
        if a not in amounts:
            amounts.append(a)

    return named[:10], countries[:5], years[:3], amounts[:5]


# ── Classification ─────────────────────────────────────────────────────────

def classify(query: str) -> QueryIntent:
    """Pure-regex intent classification. No LLM call."""
    if not query or len(query.strip()) < 3:
        return QueryIntent(
            raw_query=query,
            intent=Intent.UNKNOWN,
            confidence=0.0,
        )

    # Score each intent by pattern match
    scores: dict[Intent, float] = {}
    matched: list[str] = []
    for intent, pattern, weight in _INTENT_PATTERNS:
        m = pattern.search(query)
        if m:
            scores[intent] = max(scores.get(intent, 0.0), weight)
            matched.append(m.group(0).strip())

    # Pick highest-scoring intent
    if not scores:
        chosen = Intent.UNKNOWN
        confidence = 0.0
    else:
        chosen = max(scores, key=scores.get)
        confidence = scores[chosen]

    # Extract entities
    entities, countries, years, amounts = _extract_entities(query)

    # Disambiguate UNKNOWN — if there's a named entity + "investigate"-like
    # verb, bump to COMPANY_RESEARCH at low confidence
    if chosen == Intent.UNKNOWN and entities:
        chosen = Intent.COMPANY_RESEARCH
        confidence = 0.5

    # R-F309 (2026-05-11): when the operator pastes just a hostname
    # (`modirumgespi.com`) the decomposer used to land on UNKNOWN, which
    # routed deep_research to threat-intel framing ("malware phishing
    # domain history cybercrime takedown"). For a chat target with a
    # URL the right default is COMPANY_RESEARCH (commercial DD framing),
    # not a cyber-threat investigation. The decomposer now treats any
    # bare-hostname query that didn't match a pattern as company
    # research at low confidence.
    if chosen == Intent.UNKNOWN:
        import re as _re_intent
        # Hostname pattern: token with a dot + 2-6-char TLD, no spaces
        if _re_intent.search(r"\b[a-z0-9][a-z0-9\-]{1,60}\.[a-z]{2,6}\b", query.lower()):
            chosen = Intent.COMPANY_RESEARCH
            confidence = 0.5

    # Bump confidence if multiple supporting signals
    if chosen == Intent.DD and entities:
        confidence = min(1.0, confidence + 0.05)
    if chosen == Intent.COMPANY_RESEARCH and entities:
        confidence = min(1.0, confidence + 0.05)
    if chosen == Intent.TENDER_SEARCH and countries:
        confidence = min(1.0, confidence + 0.05)
    if chosen == Intent.COMPLIANCE and (entities or countries):
        confidence = min(1.0, confidence + 0.05)
    if chosen == Intent.GEOPOLITICAL and countries:
        confidence = min(1.0, confidence + 0.05)

    result = QueryIntent(
        raw_query=query,
        intent=chosen,
        confidence=round(confidence, 3),
        entities=entities,
        countries=countries,
        years=years,
        amounts=amounts,
        matched_keywords=matched[:5],
    )

    # Brain signal — every classification teaches the predictor which
    # intents the LLM saves work on (high-confidence regex matches) vs
    # which fall through to the LLM (UNKNOWN / low confidence). Low-
    # confidence classifications feed query_intent_unclear gap so we
    # can grow the pattern library over time. Fire-and-forget.
    try:
        import asyncio as _aio
        from . import brain_hook as _bh

        async def _emit():
            await _bh.absorb(
                module="query_decomposer",
                summary=(
                    f"Intent={chosen.value} conf={confidence:.2f} "
                    f"entities={len(entities)} countries={len(countries)}"
                ),
                detail=f"query={query[:200]!r} matched={matched[:3]}",
                success=confidence >= 0.5,
                gap_type=("query_intent_unclear" if chosen == Intent.UNKNOWN else None),
                gap_detail=(f"No intent pattern matched: {query[:120]}"
                            if chosen == Intent.UNKNOWN else None),
            )

        try:
            loop = _aio.get_running_loop()
            loop.create_task(_emit())
        except RuntimeError:
            pass
    except Exception:
        from .engine_wiring import wire_failure as _wf
        _wf(
            module="query_decomposer",
            detail="brain_hook.absorb failed in classify",
            gap_type="engine_failure",
            source="query_decomposer:classify",
        )
        pass

    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="query_decomposer",
        summary="Classify",
        source_id="query_decomposer:R-F1001",
    )

    return result


# ── Domain-aware query expansion ───────────────────────────────────────────
# For each intent, we expand the raw query into 3-5 targeted search
# strings using defence vocabulary. Templates are narrow so results
# land on specialist sources (ECJU, OFAC, SIPRI, Janes, country MoD
# sites) instead of generic web chatter.

_DOMAIN_EXPANSIONS: dict[Intent, list[str]] = {
    Intent.DD: [
        "{entity} beneficial ownership",
        "{entity} sanctions list",
        "{entity} company registration {country}",
        "{entity} ultimate parent",
        "{entity} adverse media enforcement",
    ],
    Intent.COMPANY_RESEARCH: [
        "{entity} defence portfolio products",
        "{entity} recent contracts {year}",
        "{entity} export licence {country}",
        "{entity} leadership team directors",
        "{entity} financial performance revenue",
    ],
    Intent.PERSON_RESEARCH: [
        "{entity} biography career history",
        "{entity} current position {country}",
        "{entity} defence procurement decisions",
        "{entity} public statements {year}",
        "{entity} LinkedIn background",
    ],
    Intent.TENDER_SEARCH: [
        "{country} defence procurement tenders {year}",
        "{country} ministry of defence RFP",
        "{country} {entity} contract award",
        "site:ted.europa.eu {entity} {country}",
        "site:sam.gov {entity} defence",
    ],
    Intent.TECHNICAL: [
        "{entity} specifications datasheet",
        "{entity} technical manual",
        "{entity} STANAG compatibility",
        "{entity} operational range payload",
        "{entity} compared to alternatives",
    ],
    Intent.COMPLIANCE: [
        "{entity} OFAC SDN screening",
        "{entity} UK OFSI sanctions",
        "{entity} EU consolidated sanctions",
        "{entity} export control licence {country}",
        "{entity} end user certificate EUC",
    ],
    Intent.GEOPOLITICAL: [
        "{country} defence budget {year}",
        "{country} security situation",
        "{country} arms imports SIPRI",
        "{country} political risk assessment",
        "{country} ministry of defence leadership",
    ],
    Intent.ACADEMIC: [
        "site:arxiv.org {entity}",
        "site:scholar.google.com {entity}",
        "{entity} peer reviewed study",
        "{entity} meta-analysis {year}",
        "{entity} systematic review",
    ],
    Intent.OSINT: [
        "{entity} {country} news",
        "{entity} press release",
        "{entity} LinkedIn profile",
        "{entity} recent activity {year}",
        "{entity} corporate announcements",
    ],
    Intent.UNKNOWN: [
        # No domain expansion — caller should fall back to LLM
    ],
}


def decompose(intent: QueryIntent, max_queries: int = 5) -> list[str]:
    """Turn a QueryIntent into domain-specific search queries.

    Returns empty list for UNKNOWN or low-confidence intents — caller
    should fall back to LLM-generated queries. Otherwise returns up to
    `max_queries` concrete search strings with entity/country/year
    substitution.
    """
    if intent.intent == Intent.UNKNOWN or intent.confidence < 0.5:
        return []

    templates = _DOMAIN_EXPANSIONS.get(intent.intent, [])
    if not templates:
        return []

    entity = intent.entities[0] if intent.entities else ""
    country = intent.countries[0] if intent.countries else ""
    year = str(intent.years[0]) if intent.years else ""

    # If no entity present, drop templates that require {entity}
    results: list[str] = []
    for tpl in templates:
        needs_entity = "{entity}" in tpl
        needs_country = "{country}" in tpl
        if needs_entity and not entity:
            continue
        if needs_country and not country:
            # try with empty country string — still usable
            pass
        q = (
            tpl.replace("{entity}", entity)
               .replace("{country}", country)
               .replace("{year}", year)
        )
        # Clean up double spaces from empty substitutions
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in results:
            results.append(q)
        if len(results) >= max_queries:
            break

    # Always include the raw query as the last fallback — sometimes the
    # user's phrasing is the right search string and our templates
    # would lose nuance.
    raw = intent.raw_query.strip()
    if raw and raw not in results:
        results.append(raw[:200])

    return results[:max_queries]


def should_fallback_to_llm(intent: QueryIntent) -> bool:
    """Caller uses this to decide whether to call the LLM for query
    generation. True when confidence is low enough that our templates
    would produce worse queries than the LLM would."""
    if intent.intent == Intent.UNKNOWN:
        return True
    if intent.confidence < 0.65:
        return True
    # If we have no entity for intents that require one, fall back
    if intent.intent in (
        Intent.DD, Intent.COMPANY_RESEARCH, Intent.PERSON_RESEARCH,
    ) and not intent.entities:
        return True
    return False

# R-F2119 §21a — wire failure handler for query_decomposer
try:
    wire_failure(module="query_decomposer", detail="module shutdown",
                gap_type="engine_failure", source="query_decomposer:shutdown")
except Exception:
    pass
