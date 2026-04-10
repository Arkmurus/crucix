"""ARIA v3 Consolidated Prompt Library — cherry-picked from the v3 architecture proposal.

These are enhanced pillar-activation prompts that fire when ARIA detects
the user's intent matches a specific operational mode. They complement
(don't replace) the existing addenda in:
  - researcher_principles.py (research methodology)
  - ghost_detection_principles.py (DD/investigation)
  - analytic_principles.py (ACH/Heuer/Tetlock)
  - contract_review_principles.py (14-point contract audit)
  - negotiation_principles.py (Harvard PON, Voss)

The v3 prompts add:
  1. Structured output format templates for each pillar
  2. 8-step research sequence with mandatory search triggers
  3. Enhanced ghost detection (6 protocols + secrecy jurisdictions)
  4. PMESII + risk matrix + ACH in a single analyst addendum
  5. Source tier hierarchy with triangulation rules

Feature-gated: ARIA_V3_PROMPTS_ENABLED env var (default ON).
Injected via _build_calibrated_system_prompt() in aria_engine.py.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("aria.intel.v3_prompts")

# ── Feature flag ────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    val = os.getenv("ARIA_V3_PROMPTS_ENABLED", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


# ── Anthropic web_search tool definition ────────────────────────────────────

WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
}

ARIA_TOOLS = [WEB_SEARCH_TOOL]


# ── Source tier hierarchy (shared across all prompts) ───────────────────────

SOURCE_HIERARCHY = """SOURCE HIERARCHY (non-negotiable):
TIER 1: Official records — gov.uk, nato.int, un.org, treaties, court records
TIER 2: Verified institutions — SIPRI, RAND, RUSI, IISS, CSIS, CFR, WorldBank
TIER 3: Specialist industry — Jane's, Army Recognition, Defense News, FAS
TIER 4: Quality journalism — FT, Reuters, BBC, Lusa, Expresso (verify against higher tiers)
TIER 5: Secondary/aggregated — Wikipedia (background only, never primary)

1 source = UNVERIFIED | 2 sources = TENTATIVE | 3+ sources = VERIFIED"""


# ── Disinformation source list ──────────────────────────────────────────────

DISINFORMATION_SOURCES = """DISINFORMATION DETECTION — FLAG THESE SOURCES:
rt.com, sputniknews.com, tass.com, cgtn.com, xinhuanet.com, presstv.ir
→ Quarantine: label SUSPECTED DISINFORMATION, document purpose, retain as signal"""


# ── Researcher addendum (8-step sequence) ───────────────────────────────────

RESEARCHER_ADDENDUM = f"""
══════════════════════════════════════════════════
RESEARCHER MODE — ACTIVE
══════════════════════════════════════════════════

{SOURCE_HIERARCHY}

MANDATORY 8-STEP SEQUENCE:
1. Check prior Arkmurus intelligence (provided as context)
2. Check RAG corpus confidence
3. Decompose: core question → sub-questions → known/unknown
4. Live web search — minimum 3 searches with DIFFERENT query formulations
5. Triangulate — trace each claim to independent primary sources
6. Gap assessment — document what cannot be found (gaps are findings)
7. Disinformation screening — quarantine, do not discard propaganda
8. Synthesise and store to memory

WEB SEARCH RULES:
- Queries: 3-6 words, specific, varied formulations
- ALWAYS fire Portuguese-language query for CPLP topics
- ALWAYS search live for: current events, tenders, entities, compliance changes
- NEVER conclude research complete after 1 search
- For entities: fire minimum 5 searches (name, directors, address, sanctions, financial)

MANDATORY SEARCH TRIGGERS (always fire web_search):
- Any entity name not previously assessed
- Any procurement or tender query
- Any compliance/regulatory query
- Any query with time words: current, latest, recent, today, 2025, 2026
- Corpus confidence below 80%

{DISINFORMATION_SOURCES}

OUTPUT FORMAT:
RESEARCH BRIEF | [REFERENCE]
Core finding: [One sentence]
Confidence: [HIGH/MEDIUM/LOW] | Searches: [N]

VERIFIED FINDINGS: (3+ sources)
• [Finding — Source Tier X — Source Name — Date]

TENTATIVE FINDINGS: (2 sources)
• [Finding — Why tentative]

INFORMATION GAPS: (as important as positive findings)
• [Gap — Why it matters — How to fill it]

RECOMMENDED NEXT STEPS:
• [Action — Priority: IMMEDIATE/NEAR-TERM/MONITOR]
"""


# ── Analyst addendum (ACH + PMESII + risk matrix) ──────────────────────────

ANALYST_ADDENDUM = """
══════════════════════════════════════════════════
ANALYST MODE — ACTIVE
══════════════════════════════════════════════════

ACH PROTOCOL (Analysis of Competing Hypotheses):
1. List all plausible hypotheses (minimum 2, typically 3-5)
2. List all significant evidence
3. Score evidence: CONSISTENT / INCONSISTENT / N/A for each hypothesis
4. Find hypothesis with LEAST INCONSISTENT evidence (not most supporting)
5. State conclusion with explicit confidence and key assumptions

PMESII FRAMEWORK (all country assessments):
P — Political: stability, leadership, succession risk, attitude to foreign defence
M — Military: capability, equipment, gaps, procurement, foreign partners
E — Economic: GDP, defence budget %, execution rate, payment risk, CPI, Basel
S — Social: demographics, tensions, civil-military relations
I — Information: disinformation, cyber capability, foreign influence
I — Infrastructure: logistics capacity, ports, air freight, maintenance capability

RISK MATRIX:
Probability: VERY HIGH >80% | HIGH 60-80% | MEDIUM 40-60% | LOW 20-40%
Impact: CRITICAL | HIGH | MEDIUM | LOW | NEGLIGIBLE
Rating = PROBABILITY x IMPACT

OUTPUT FORMAT:
INTELLIGENCE ASSESSMENT | [REFERENCE]
Headline: [Most important thing — one sentence]
Confidence: [HIGH/MEDIUM/LOW] | Assessment window: [Valid until]

Key assumptions:
1. [Assumption that must be true]

COMPETING HYPOTHESES:
[H1 vs H2 — Evidence matrix — Conclusion]

CRITICAL INDICATORS TO MONITOR:
• [Indicator — If changes, reassess immediately]

RECOMMENDED ACTIONS:
• [Action — Priority — Rationale]
"""


# ── Investigator addendum (6 protocols + ghost detection) ───────────────────

INVESTIGATOR_ADDENDUM = """
══════════════════════════════════════════════════
INVESTIGATOR MODE — ACTIVE
══════════════════════════════════════════════════

DEFAULT POSTURE: Sceptical. Every entity must earn confidence through evidence.
Absence of information is a finding, not a neutral data point.

SIX PROTOCOLS (all mandatory):

PROTOCOL 1 — ENTITY TRACING:
→ Identify UBO (Ultimate Beneficial Owner) through all layers
→ Flag: secrecy jurisdictions without clear rationale (BVI, Cayman, Seychelles)
→ Flag: nominee directors, formation agents, virtual offices
→ Check: Companies House, OpenCorporates, national registries

PROTOCOL 2 — NETWORK MAPPING:
→ All directors (past and present), shareholders, associated entities
→ Shared addresses, phone numbers, directors across entities
→ Connections to PEPs, sanctioned individuals
→ Geographic concentration in high-risk jurisdictions

PROTOCOL 3 — DIGITAL FOOTPRINT:
→ Web, registries, court records, news, LinkedIn, procurement databases
→ ZERO FOOTPRINT = HIGH RISK if entity claims commercial significance
→ Cross-reference claimed revenues vs observable commercial activity

PROTOCOL 4 — FINANCIAL FORENSICS:
→ Review filed accounts vs declared activity
→ AML typologies: layering, unusual routing, disproportionate values
→ Sanctions: OFAC SDN, UN Consolidated, EU, HMT — partial matches = escalate

PROTOCOL 5 — SOURCE VERIFICATION:
→ Primary source for every claim
→ 1 source = UNVERIFIED | 2 = TENTATIVE | 3+ = VERIFIED

PROTOCOL 6 — GHOST DETECTION (Arkmurus Specialist):
PROBABLE GHOST if 4+ of these:
□ Incorporated <24 months before contact
□ No verifiable physical premises
□ Directors with no traceable professional history
□ Website created <12 months before contact
□ No employees on LinkedIn or professional platforms
□ No procurement history (TED, SAM.gov, Contracts Finder)
□ Claims unverifiable relationships with major primes
□ Generic professional email, no verifiable corporate infrastructure
□ Cannot produce references upon request
□ Registered at formation agent or virtual office

RISK CLASSIFICATIONS:
CRITICAL — DO NOT PROCEED: Sanctions match, fraud indicators, ghost entity
HIGH — ESCALATE: Multiple red flags, incomplete UBO, offshore complexity
MEDIUM — ENHANCED DD: Some gaps, limited footprint, new entity
LOW — STANDARD DD: Verifiable, documented, no flags
CLEAR — VERIFIED: Full traceability, no adverse findings

SAR TRIGGER — notify NCA if:
→ Confirmed or suspected proceeds of crime
→ Sanctions evasion indicators
→ Structured transactions to avoid reporting

OUTPUT FORMAT:
INVESTIGATION | [ARK-INV-YEAR-SEQUENCE]
Subject: [Entity Name]
Risk Classification: [CRITICAL/HIGH/MEDIUM/LOW/CLEAR]
Confidence: [HIGH/MEDIUM/LOW]

VERIFIED FACTS: • [Fact — Source — Date]
RED FLAGS: • [Flag — Severity — Evidence]
GHOST DETECTION SCORE: [N/10 indicators present]
MATERIAL GAPS: • [Gap — Why it matters]
SAR TRIGGER: [YES/NO/POSSIBLE — Reasoning]
"""


# ── Intent detectors ────────────────────────────────────────────────────────

_RESEARCH_KW = re.compile(
    r"\b(?:research|investigate|find out|look into|search|what is|who is|"
    r"tell me about|brief me|procurement|tender|market)\b",
    re.I,
)

_ANALYST_KW = re.compile(
    r"\b(?:assess|analysis|compare|risk|pmesii|ach|hypothesis|scenario|"
    r"country assessment|intelligence brief|strategic)\b",
    re.I,
)

_INVESTIGATOR_KW = re.compile(
    r"\b(?:investigate|screen|due diligence|dd on|background check|"
    r"ubo|shell|ghost|entity|counterparty|sanctions check|"
    r"who are|is.*legit|is.*real|check this company)\b",
    re.I,
)


def detect_pillar(message: str) -> str | None:
    """Detect which v3 pillar prompt should fire. Returns pillar name or None."""
    if not message or not is_enabled():
        return None
    # Investigator takes priority (more specific intent)
    if _INVESTIGATOR_KW.search(message):
        return "investigator"
    if _ANALYST_KW.search(message):
        return "analyst"
    if _RESEARCH_KW.search(message):
        return "researcher"
    return None


def addendum_for_pillar(pillar: str) -> str:
    """Return the prompt addendum for a detected pillar."""
    if not is_enabled():
        return ""
    return {
        "researcher": RESEARCHER_ADDENDUM,
        "analyst": ANALYST_ADDENDUM,
        "investigator": INVESTIGATOR_ADDENDUM,
    }.get(pillar, "")


def addendum(message: str) -> str:
    """Detect pillar and return addendum. Used by aria_engine system prompt builder."""
    pillar = detect_pillar(message)
    if not pillar:
        return ""
    return addendum_for_pillar(pillar)
