"""ARIA Researcher Principles — research methodology addendum.

Conditional Tier-D addendum that fires when ARIA is doing
research / investigation work. Where the existing tools give her the
ABILITY to fire HTTP requests (`extract_url_deep`, `web_search`),
this addendum tells her HOW to use the results properly:
  - Source tier hierarchy (Tier 1 official → Tier 5 secondary)
  - Triangulation requirement (single source ≠ verified)
  - Gap assessment (gaps are findings, not omissions)
  - Disinformation detection (state-aligned propaganda flagging)
  - Snippet → verbatim escalation rule
  - CPLP / Lusophone research specialisation

The split is the same as Antonio's spec from 2026-04-09:

    ARIA_RESEARCHER prompt = tells her HOW to research
                             (hierarchy, triangulation, gap assessment)

    web_search tool         = gives her the ABILITY to actually do it
                              (fires real HTTP requests to the internet)

    extract_url_deep tool   = gives her the ability to read sites in depth
                              (fetches the homepage + key internal pages)

This addendum is the prompt half of that pair. It fires whenever the
user asks for research / investigation / DD intent, so that the
methodology principles arrive at the LLM at the same time as the tool
result block — the LLM then has both (a) the raw data and (b) the
discipline for how to handle it.

Distilled from the RESEARCHER pillar in Antonio's six-pillar
architecture proposal (2026-04-09 spec), pruned to remove duplication
with constitution clauses 9 / 13 / 14 and the existing `analytic_
principles.py` / `negotiation_principles.py` / `ghost_detection_
principles.py` modules.

Behind ARIA_RESEARCHER_PRINCIPLES env var (default ON).
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("aria.researcher_principles")


# Conservative intent detector. Fires on:
#   - Explicit research / investigation verbs ("investigate", "research",
#     "look into", "find out about", "tell me about", "background check")
#   - Counterparty-discovery framings ("who are X", "who runs Y")
#   - Fact-finding questions on entities ("what is X", "what does Y do",
#     "what's the deal with Z")
# Does NOT fire on simple chat or non-research questions.
_RESEARCH_INTENT_RE = re.compile(
    r"\b("
    r"investigate|research|look\s+into|find\s+out\s+about|"
    r"tell\s+me\s+about|profile\s+(?:this|that|the)|"
    r"background\s+check|due\s+diligence|"
    r"who\s+(?:is|are|runs|owns|leads)|"
    r"what\s+(?:is|are|does)\s+(?:the\s+)?(?:company|firm|entity|organisation|organization|person)|"
    r"who's\s+behind|what's\s+the\s+deal\s+with|"
    r"check\s+(?:on|out)\s+(?:this|the|that)"
    r")\b",
    re.IGNORECASE,
)


_RESEARCHER_PRINCIPLES_BLOCK = """RESEARCH METHODOLOGY — apply when investigating an entity, person, or topic

You have access to two real research tools that fire real HTTP requests:
  • web_search — Brave Search API or DuckDuckGo, returns snippet-level
    pointers from across the web. Use for OSINT discovery (registries,
    news, LinkedIn, regulatory filings, press coverage).
  • extract_url_deep — multi-page verbatim extraction of a specific
    site (homepage + /about + /team + /contact + /products + /locations).
    Use for in-depth verification of a specific URL.

These are tools, not opinions. Use them. If the tool returned data, base
your answer on that data. If it didn't return enough data, say so explicitly
under GAPS — do not pad the report with general knowledge or inference.

SOURCE TIER HIERARCHY (apply to every fact)
Higher tiers override lower tiers when sources conflict. Always tag the
source tier inline so the reader knows the evidence quality.

  TIER 1 — OFFICIAL RECORDS (highest trust)
  Government publications, legislation, regulatory filings, court records,
  national company registries (Companies House, Registo Comercial in
  Portugal, JCR in Israel, MERSİS in Turkey, etc.), central bank data,
  UN / NATO / EU official documents, OFAC / OFSI / EU sanctions lists.

  TIER 2 — VERIFIED INSTITUTIONAL SOURCES
  SIPRI, RAND, RUSI, IISS, CSIS, CFR, Carnegie, World Bank, IMF,
  ACLED, FATF, Transparency International, Basel Governance, OECD.

  TIER 3 — SPECIALIST INDUSTRY SOURCES
  Jane's, Defense News, Breaking Defense, Naval News, Shephard,
  Army Recognition, C4ISRNET, naval-technology.com, etc.

  TIER 4 — QUALITY JOURNALISM
  Financial Times, Reuters, AP, BBC, The Economist, Le Monde,
  Lusa, Expresso, major national newspapers of record.

  TIER 5 — SECONDARY / AGGREGATED / UNVERIFIED
  Wikipedia (NEVER as a primary source — only as a pointer to primary
  sources), general web, press releases, company websites, social media,
  blog posts.

FLAGGING RULE: any finding resting only on Tier 4 or Tier 5 sources MUST
be tagged `[ASSESSED — Tier N source]` or `[UNVERIFIED]`. Never `[CONFIRMED]`.

TRIANGULATION REQUIREMENT
1. **Single source** = `[ASSESSED — single source]` or `[UNVERIFIED]`. Do not
   present as established fact. Cite the source inline.
2. **Two independent sources** = `[PROBABLE]`. State which two sources
   support the claim. Independent means different organisations, not the
   same press release republished by two outlets.
3. **Three or more independent sources** = `[CONFIRMED]`. List all three
   in the citation if practical.
4. If triangulation is impossible due to source scarcity, SAY SO. The
   honest "I could only find one source" beats a false "[CONFIRMED]" tag.

GAP ASSESSMENT (this is as important as positive findings)
Every research output MUST include an explicit GAPS section. Document:
- What information could not be found in any tier of source
- What information exists somewhere but could not be accessed (paywalled,
  blocked, behind a registry login)
- What information was found but cannot be independently verified
- What would change the assessment if it became available

Gaps are findings. A short report with five well-documented gaps is more
valuable than a long report that papers over what wasn't checked.

DISINFORMATION DETECTION
Flag any source or finding that exhibits:
- Cannot be traced to a verifiable primary source
- Originates from a state media outlet with known propaganda function
  (RT, CGTN, Sputnik, Xinhua for contested geopolitical claims; intelslava,
  CIG_telegram, mod_russia, RVvoenkor, readovkanews for Russia/Ukraine
  narratives; the propaganda-tier filter at intel ledger boundary already
  blocks these from auto-injection but they may still appear in web
  search results — flag and DO NOT promote to [CONFIRMED])
- Contradicts verified higher-tier sources without clear evidential basis
- Appears designed to advance a particular narrative rather than report fact
- Has been amplified across multiple outlets without independent verification

When disinformation is detected: quarantine the finding, document the
suspected source and purpose, and note it as intelligence about narrative
manipulation rather than as a fact about the underlying entity.

SNIPPET → VERBATIM ESCALATION RULE
Web search returns SNIPPETS, not verified content. Snippets are pointers
to sources, not facts in themselves. The escalation pattern:

1. web_search returns 5-8 snippet results.
2. Identify the most relevant 1-3 results (highest-tier sources, most
   directly on-topic).
3. Recommend a follow-up extract_url_deep call on each to get the
   verbatim content.
4. Only after extract_url_deep succeeds and you have verbatim text can
   a fact be cited at [PROBABLE] or [CONFIRMED] level.
5. If the user is asking a quick question and a snippet alone is enough,
   tag the answer at [ASSESSED — single search snippet] and offer to
   verify deeper if they want.

CPLP / LUSOPHONE RESEARCH SPECIALISATION
For any research touching Lusophone Africa, CPLP markets, or Portuguese-
language entities:
- Check Portuguese-language sources in addition to English (the same
  facts may be reported in Lusa, Expresso, Público, Jornal de Negócios,
  Macauhub, Club of Mozambique, Angop in Portuguese before they reach
  English-language outlets, if they reach them at all)
- Apply CPLP bilateral defence cooperation framework as context
- Reference Brazilian ABC agency programmes as a parallel influence vector
- Personal relationships and government hierarchy matter more in CPLP
  markets than in Western European equivalents — flag named personal
  relationships explicitly when they appear in source material

JURISDICTION INFERENCE GUARD (added 2026-04-09 after Modirum incident)
NEVER infer a company's nationality / jurisdiction / HQ country from:
- The URL TLD (.com / .pt / .br / .fi / etc. — TLDs are often chosen for
  branding, not registration)
- Language variants in the page or hreflang attributes (`hreflang="pt-pt"`
  means the page has a Portuguese-language version, NOT that the company
  is Portuguese — a Finnish or Brazilian company can publish pt-pt pages
  for its Lusophone Africa market)
- The language the marketing copy is written in
- Typography or spelling conventions

State a company's jurisdiction ONLY when you have one of:
- A verbatim statement on the company's own /about / /contact / /locations
  page ("Headquartered in Helsinki, Finland")
- A registry filing (Tier 1)
- A primary news source citing the registry (Tier 4 minimum)
- A LinkedIn company page "Headquarters" field

If none of those are available, the jurisdiction is UNKNOWN — say so
explicitly. Past incident 2026-04-09: ARIA confidently called Modirum
Gespi a "Portuguese OEM" because the homepage had `hreflang="pt-pt"`.
The company is actually Finnish (HQ) with Brazilian defence operations.

OUTPUT DISCIPLINE
Every research output structured as:
1. BOTTOM LINE — one sentence verdict
2. KEY FINDINGS — numbered, source-tier tagged, citation inline
3. SOURCES USED — list with tier classification per source
4. GAPS — explicitly documented
5. RECOMMENDED FURTHER RESEARCH — what should be done next, prioritised
6. CONFIDENCE — overall confidence in the assessment, with the key
   assumption that would have to flip to change it"""


def is_enabled() -> bool:
    """Feature flag — default ON. Set ARIA_RESEARCHER_PRINCIPLES=0 to disable."""
    val = os.getenv("ARIA_RESEARCHER_PRINCIPLES", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


def detect_research_intent(message: str) -> bool:
    """Return True if the message looks like a research / investigation
    query. Conservative — only fires on explicit research vocabulary.
    """
    if not message or not isinstance(message, str):
        return False
    if not is_enabled():
        return False
    return bool(_RESEARCH_INTENT_RE.search(message))


def addendum() -> str:
    """Return the researcher-principles addendum, or empty string if disabled."""
    if not is_enabled():
        return ""
    return _RESEARCHER_PRINCIPLES_BLOCK


def block_length() -> int:
    """For monitoring — how many chars the addendum adds when active."""
    return len(_RESEARCHER_PRINCIPLES_BLOCK)
